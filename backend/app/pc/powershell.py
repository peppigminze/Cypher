"""
Run PowerShell commands for run_command / command_status.

    start(command, cwd, background)   starts powershell.exe, returns a Job
    wait(job, timeout)                awaits a foreground job; if the awaiting
                                      task is cancelled (barge-in, tool timeout)
                                      the WHOLE process tree is killed
    get(job_id) / jobs()              background jobs for command_status

Why Popen + reader threads instead of asyncio subprocesses: they work with
every event loop (a Selector loop on Windows has no subprocess support).

The command goes in as -EncodedCommand (base64 UTF-16): no quoting problems,
nothing is re-interpreted by cmd.exe. A fixed prefix switches the output to
UTF-8 (umlauts) and hides progress bars; PowerShell reports errors as CLIXML
on stderr, which is turned back into plain text here.
"""
from __future__ import annotations

import asyncio
import base64
import html
import itertools
import logging
import os
import re
import subprocess
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional

import psutil

logger = logging.getLogger("cypher.pc.powershell")

PREFIX = "[Console]::OutputEncoding=[Text.Encoding]::UTF8;$OutputEncoding=[Text.Encoding]::UTF8;$ProgressPreference='SilentlyContinue'\n"
# A failing last statement / native program -> non-zero exit code.
SUFFIX = "\nif (-not $?) { exit 1 }; if ($LASTEXITCODE) { exit $LASTEXITCODE }"
CREATE_NO_WINDOW = 0x08000000
CREATE_NEW_PROCESS_GROUP = 0x00000200
MAX_CAPTURE_BYTES = 1_000_000  # per stream, kept in memory and in command_logs
MAX_RUNNING_BACKGROUND = 5
KEEP_FINISHED = 20

# Cypher's own secrets are not handed down to commands it runs.
_SECRET_ENV = {"USER_TOKEN", "AGENT_TOKEN", "CLOUD_LLM_API_KEY"}


@dataclass
class Job:
    id: str
    command: str
    cwd: str
    background: bool
    proc: subprocess.Popen
    started: float = field(default_factory=time.time)
    finished: Optional[float] = None
    exit_code: Optional[int] = None
    stopped: str = ""  # "timeout" | "cancelled" | "stopped" - why Cypher killed it
    truncated: bool = False
    _out: bytearray = field(default_factory=bytearray)
    _err: bytearray = field(default_factory=bytearray)
    _done: threading.Event = field(default_factory=threading.Event)

    @property
    def running(self) -> bool:
        return not self._done.is_set()

    @property
    def pid(self) -> int:
        return self.proc.pid

    def stdout(self) -> str:
        return bytes(self._out).decode("utf-8", errors="replace")

    def stderr(self) -> str:
        return clixml_to_text(bytes(self._err).decode("utf-8", errors="replace"))

    def duration(self) -> float:
        return (self.finished or time.time()) - self.started

    def kill_tree(self, reason: str) -> None:
        if self._done.is_set():
            return
        self.stopped = self.stopped or reason
        kill_process_tree(self.proc.pid)


_lock = threading.Lock()
_jobs: "OrderedDict[str, Job]" = OrderedDict()
_ids = itertools.count(1)


def clixml_to_text(text: str) -> str:
    """'#< CLIXML <Objs><S S="Error">Get-Item : ..._x000D__x000A_</S>' -> plain text."""
    if not text.lstrip().startswith("#< CLIXML"):
        return text
    parts = re.findall(r'<S S="(?:Error|Warning|Verbose|Debug|Information)">(.*?)</S>', text, re.DOTALL)
    joined = "".join(html.unescape(p) for p in parts)
    joined = re.sub(r"_x([0-9A-Fa-f]{4})_", lambda m: chr(int(m.group(1), 16)), joined)
    return joined.replace("\r\n", "\n").strip()


def kill_process_tree(pid: int) -> None:
    try:
        parent = psutil.Process(pid)
        procs = parent.children(recursive=True) + [parent]
    except psutil.Error:
        return
    for proc in procs:
        try:
            proc.kill()
        except psutil.Error:
            pass


def _child_env() -> Dict[str, str]:
    return {k: v for k, v in os.environ.items() if k.upper() not in _SECRET_ENV}


def _reader(stream, buffer: bytearray, job: Job) -> None:
    try:
        while True:
            chunk = stream.read(4096)
            if not chunk:
                break
            room = MAX_CAPTURE_BYTES - len(buffer)
            if room > 0:
                buffer.extend(chunk[:room])
            if len(chunk) > room:
                job.truncated = True
    except (OSError, ValueError):
        pass


def _watch(job: Job, readers: List[threading.Thread]) -> None:
    job.proc.wait()
    for thread in readers:
        thread.join(timeout=5)
    job.exit_code = job.proc.returncode
    job.finished = time.time()
    job._done.set()
    _log_command(job)


def _log_command(job: Job) -> None:
    """Every command Cypher ran ends up in command_logs (never auto-deleted)."""
    try:
        from app.database.db import session_scope  # noqa: PLC0415
        from app.database.models import CommandLog  # noqa: PLC0415

        with session_scope() as db:
            db.add(CommandLog(command=job.command, working_dir=job.cwd, exit_code=job.exit_code, stdout=job.stdout(), stderr=job.stderr(),
                              timed_out=job.stopped == "timeout", started_at=datetime.fromtimestamp(job.started, timezone.utc),
                              finished_at=datetime.fromtimestamp(job.finished or time.time(), timezone.utc)))
    except Exception:  # noqa: BLE001 - logging must never break a command
        logger.exception("Could not write command_logs entry")


def powershell_args(command: str) -> List[str]:
    encoded = base64.b64encode((PREFIX + command + SUFFIX).encode("utf-16-le")).decode("ascii")
    return ["powershell.exe", "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded]


def running_background() -> int:
    with _lock:
        return sum(1 for j in _jobs.values() if j.background and j.running)


def start(command: str, cwd: str, *, background: bool = False) -> Job:
    proc = subprocess.Popen(
        powershell_args(command),
        cwd=cwd,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=_child_env(),
        creationflags=CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP,
    )
    job = Job(id=f"j{next(_ids)}", command=command, cwd=cwd, background=background, proc=proc)
    readers = [threading.Thread(target=_reader, args=(proc.stdout, job._out, job), daemon=True),
               threading.Thread(target=_reader, args=(proc.stderr, job._err, job), daemon=True)]
    for thread in readers:
        thread.start()
    threading.Thread(target=_watch, args=(job, readers), daemon=True, name=f"cmd-{job.id}").start()
    with _lock:
        _jobs[job.id] = job
        finished = [k for k, j in _jobs.items() if not j.running]
        for key in finished[: max(0, len(_jobs) - KEEP_FINISHED)]:
            _jobs.pop(key, None)
    logger.info("Command %s started (pid %s, background=%s): %s", job.id, proc.pid, background, command[:200])
    return job


async def wait(job: Job, timeout: float) -> Job:
    """Wait for a foreground job. Cancelling this coroutine kills the tree."""
    deadline = time.monotonic() + timeout
    try:
        while job.running:
            if time.monotonic() >= deadline:
                job.kill_tree("timeout")
                break
            await asyncio.sleep(0.05)
    except asyncio.CancelledError:
        job.kill_tree("cancelled")
        logger.info("Command %s cancelled - process tree killed", job.id)
        raise
    await asyncio.to_thread(job._done.wait, 10)
    return job


def get(job_id: str) -> Optional[Job]:
    with _lock:
        return _jobs.get(job_id.strip().lower())


def jobs() -> List[Job]:
    with _lock:
        return list(_jobs.values())


def stop_all() -> None:
    """Shutdown: background commands do not outlive Cypher."""
    for job in jobs():
        job.kill_tree("stopped")


def clip(text: str, limit: int) -> tuple[str, bool]:
    """Keep head and tail of long output (the end often holds the error)."""
    if len(text) <= limit:
        return text, False
    head = int(limit * 0.6)
    tail = limit - head
    return f"{text[:head]}\n[... {len(text) - limit} Zeichen ausgelassen ...]\n{text[-tail:]}", True
