"""M7: frontend served by the backend, clean shutdown, backup/restore,
Tailscale/pairing helpers and the doctor."""
from __future__ import annotations

import json
import sqlite3
import zipfile
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import frontend as frontend_api
from app.config import Settings
from app.ops import backup, tailscale
from app.security.origin import is_trusted_local_browser_request


# ------------------------------------------------------------------ frontend serving
@pytest.fixture()
def dist(tmp_path):
    root = tmp_path / "dist"
    (root / "assets").mkdir(parents=True)
    (root / "index.html").write_text("<!doctype html><title>Cypher</title>", encoding="utf-8")
    (root / "assets" / "app-abc123.js").write_text("console.log(1)", encoding="utf-8")
    (root / "sw.js").write_text("self.x=1", encoding="utf-8")
    (root / "manifest.webmanifest").write_text("{}", encoding="utf-8")
    (tmp_path / "secret.txt").write_text("nope", encoding="utf-8")
    return root


def _spa_client(dist_dir) -> TestClient:
    settings = Settings(_env_file=None, FRONTEND_DIST=str(dist_dir))
    app = FastAPI()

    @app.get("/api/ping")
    def ping():
        return {"ok": True}

    router = frontend_api.build_router(settings)
    assert router is not None
    app.include_router(router)
    return TestClient(app)


def test_spa_index_assets_and_fallback(dist):
    client = _spa_client(dist)
    index = client.get("/")
    assert index.status_code == 200 and "<title>Cypher</title>" in index.text
    assert "no-cache" in index.headers["cache-control"]
    asset = client.get("/assets/app-abc123.js")
    assert asset.status_code == 200 and "immutable" in asset.headers["cache-control"]
    assert "no-cache" in client.get("/sw.js").headers["cache-control"]
    # client-side route -> index.html
    deep = client.get("/irgendwas/tief")
    assert deep.status_code == 200 and "<title>Cypher</title>" in deep.text


def test_spa_never_shadows_api_or_leaks_files(dist):
    client = _spa_client(dist)
    assert client.get("/api/ping").json() == {"ok": True}
    assert client.get("/api/does-not-exist").status_code == 404  # no index.html for API typos
    assert client.get("/health").status_code == 404
    assert client.get("/assets/missing.js").status_code == 404  # a missing asset is not the SPA
    leaked = client.get("/..%2Fsecret.txt")
    assert "nope" not in leaked.text


def test_spa_disabled_or_unbuilt(tmp_path):
    assert frontend_api.build_router(Settings(_env_file=None, SERVE_FRONTEND=False)) is None
    assert frontend_api.build_router(Settings(_env_file=None, FRONTEND_DIST=str(tmp_path / "nothing"))) is None


# ------------------------------------------------------------------ shutdown
def test_shutdown_requires_token_and_server_handle(client, user_headers):
    from app.main import app

    assert client.post("/api/system/shutdown").status_code == 401
    app.state.uvicorn_server = None  # not started via run.py
    assert client.post("/api/system/shutdown", headers=user_headers).status_code == 409
    server = SimpleNamespace(should_exit=False)
    app.state.uvicorn_server = server
    try:
        assert client.post("/api/system/shutdown", headers=user_headers).status_code == 200
        assert server.should_exit is True
    finally:
        app.state.uvicorn_server = None


def test_shutdown_refused_from_other_devices(user_headers):
    from app.main import app

    server = SimpleNamespace(should_exit=False)
    app.state.uvicorn_server = server
    try:
        remote = TestClient(app, client=("100.64.1.5", 50000))
        assert remote.post("/api/system/shutdown", headers=user_headers).status_code == 403
        assert server.should_exit is False
    finally:
        app.state.uvicorn_server = None


# ------------------------------------------------------------------ backup
def _settings_with_data(tmp_path) -> Settings:
    data = tmp_path / "data"
    data.mkdir()
    db = tmp_path / "cypher.db"
    with sqlite3.connect(db) as conn:
        conn.execute("create table t (v text)")
        conn.execute("insert into t values ('vorher')")
    conn.close()
    (data / "core_memory.json").write_text('{"facts": ["Ich heisse Silvan"]}', encoding="utf-8")
    (data / "notes").mkdir()
    (data / "notes" / "einkauf.md").write_text("Milch", encoding="utf-8")
    (data / "cypher.pid").write_text("123", encoding="ascii")
    return Settings(_env_file=None, DATA_DIR=str(data), DATABASE_URL=f"sqlite:///{db.as_posix()}", BACKUP_DIR=str(tmp_path / "backups"))


def test_backup_contains_db_and_data_but_not_pid(tmp_path):
    settings = _settings_with_data(tmp_path)
    archive = backup.create_backup(settings)
    with zipfile.ZipFile(archive) as zf:
        names = set(zf.namelist())
        manifest = json.loads(zf.read("manifest.json"))
    assert {"cypher.db", "data/core_memory.json", "data/notes/einkauf.md", "manifest.json"} <= names
    assert "data/cypher.pid" not in names
    assert manifest["version"] == 1 and "cypher.db" in manifest["files"]
    assert not any(name.endswith(".env") for name in names)


def test_restore_roundtrip_with_safety_backup(tmp_path):
    settings = _settings_with_data(tmp_path)
    archive = backup.create_backup(settings)
    # change everything after the backup
    db = backup.database_path(settings)
    conn = sqlite3.connect(db)
    with conn:
        conn.execute("update t set v = 'nachher'")
    conn.close()
    (settings.data_dir_path / "core_memory.json").write_text('{"facts": []}', encoding="utf-8")

    safety = backup.restore_backup(archive, settings)

    conn = sqlite3.connect(db)
    assert conn.execute("select v from t").fetchone()[0] == "vorher"
    conn.close()
    assert "Silvan" in (settings.data_dir_path / "core_memory.json").read_text(encoding="utf-8")
    with zipfile.ZipFile(safety) as zf:  # the state before the restore is kept
        assert '"facts": []' in zf.read("data/core_memory.json").decode("utf-8")


@pytest.mark.parametrize("bad_name", ["../evil.txt", "data/../../evil.txt"])
def test_restore_rejects_unsafe_paths(tmp_path, bad_name):
    settings = _settings_with_data(tmp_path)
    archive = tmp_path / "evil.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("manifest.json", "{}")
        zf.writestr(bad_name, "x")
    with pytest.raises(ValueError):
        backup.restore_backup(archive, settings)
    assert not (tmp_path / "evil.txt").exists()


def test_restore_rejects_foreign_zip(tmp_path):
    settings = _settings_with_data(tmp_path)
    archive = tmp_path / "foreign.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("hello.txt", "x")
    with pytest.raises(ValueError, match="kein Cypher-Backup"):
        backup.restore_backup(archive, settings)


# ------------------------------------------------------------------ tailscale + pairing
def test_tailscale_status_parsing():
    serve = "https://pc.tail1234.ts.net (tailnet only)\n|-- / proxy http://127.0.0.1:8000\n"
    assert tailscale.serves_port(serve, 8000)
    assert not tailscale.serves_port(serve, 8001)
    assert not tailscale.serves_port("No serve config", 8000)
    status = json.dumps({"BackendState": "Running", "Self": {"DNSName": "pc.tail1234.ts.net."}})
    assert tailscale.https_url_from_status(status) == "https://pc.tail1234.ts.net"
    assert tailscale.https_url_from_status("{}") is None
    assert tailscale.https_url_from_status("kaputt") is None


def test_requests_through_tailscale_serve_are_not_local():
    s = Settings(_env_file=None)
    # tailscale serve connects from 127.0.0.1; it keeps the Host and adds proxy headers
    assert not is_trusted_local_browser_request("127.0.0.1", {"host": "pc.tail1234.ts.net"}, s)
    assert not is_trusted_local_browser_request("127.0.0.1", {"host": "127.0.0.1:8000", "x-forwarded-for": "100.101.102.103"}, s)
    assert not is_trusted_local_browser_request("127.0.0.1", {"host": "localhost:8000", "tailscale-user-login": "x@y"}, s)


def test_pairing_offers_tailscale_link_with_qr(client, monkeypatch):
    monkeypatch.setattr(tailscale, "serve_url", lambda port: "https://pc.tail1234.ts.net")
    monkeypatch.setattr(tailscale, "cli_path", lambda: "tailscale.exe")
    body = client.get("/api/auth/pairing").json()
    first = body["links"][0]
    assert first["kind"] == "tailscale" and first["microphone"] is True
    assert first["url"] == f"https://pc.tail1234.ts.net/#pair={body['token']}"
    assert body["qr"].startswith("data:image/svg+xml") and "script" not in body["qr"].lower()
    assert body["tailscale"] == {"installed": True, "url": "https://pc.tail1234.ts.net"}


def test_pairing_without_tailscale_has_code_only(client, monkeypatch):
    monkeypatch.setattr(tailscale, "serve_url", lambda port: None)
    monkeypatch.setattr(tailscale, "cli_path", lambda: None)
    body = client.get("/api/auth/pairing").json()
    assert body["links"] == [] and body["qr"] is None and body["token"]
    assert body["tailscale"]["installed"] is False


def test_pairing_refused_through_proxy(client):
    resp = client.get("/api/auth/pairing", headers={"X-Forwarded-For": "100.101.102.103"})
    assert resp.status_code == 403


# ------------------------------------------------------------------ doctor
def test_doctor_runs_offline_without_crashing(monkeypatch):
    from app.ops import doctor

    # keep it fast and side-effect free: no subprocesses
    monkeypatch.setattr(doctor, "_run", lambda *a, **k: None)
    monkeypatch.setattr(tailscale, "cli_path", lambda: None)
    checks = doctor.run_all()
    assert checks
    assert all(c.status in {doctor.OK, doctor.WARN, doctor.ERROR, doctor.INFO} for c in checks)
    assert "Python" in {c.area for c in checks}


def test_shutdown_refused_through_local_proxy(client, user_headers):
    from app.main import app

    server = SimpleNamespace(should_exit=False)
    app.state.uvicorn_server = server
    try:
        resp = client.post("/api/system/shutdown", headers={**user_headers, "X-Forwarded-For": "100.101.102.103"})
        assert resp.status_code == 403
        assert server.should_exit is False
    finally:
        app.state.uvicorn_server = None


def test_backups_in_the_same_second_get_distinct_names(tmp_path):
    settings = _settings_with_data(tmp_path)
    first = backup.create_backup(settings)
    second = backup.create_backup(settings)
    assert first != second and first.exists() and second.exists()


def test_auth_lockout_behind_tailscale_serve_does_not_lock_out_the_pc(client, user_headers):
    from app.security.rate_limit import AUTH_FAILURE_LIMIT

    phone = {"X-Forwarded-For": "100.101.102.103"}
    for _ in range(AUTH_FAILURE_LIMIT):
        client.get("/api/auth/check", headers={**phone, "Authorization": "Bearer wrong"})
    assert client.get("/api/auth/check", headers={**phone, **user_headers}).status_code == 429
    assert client.get("/api/auth/check", headers=user_headers).status_code == 200  # the PC itself


@pytest.mark.parametrize(
    "cmd,expected",
    [
        (r'"C:\Python312\python.exe" Cypher/backend/run.py', True),
        (r'"C:\Cypher\backend\.venv\Scripts\python.exe" run.py', True),
        (r'python -m uvicorn app.main:app --host 127.0.0.1 --port 8000', True),
        (r'"C:\Python312\python.exe" C:\other\project\run.py', False),
        (r'"C:\Program Files\nodejs\node.exe" server.js', False),
    ],
)
def test_stop_only_ends_cypher_processes(cmd, expected):
    from app.ops.stop import is_cypher_backend

    assert is_cypher_backend(cmd) is expected
