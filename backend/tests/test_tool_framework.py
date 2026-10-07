"""Tool framework: schemas, validation, risk tiers, catalog, executor, audit."""
from __future__ import annotations

import asyncio
from typing import Optional

import pytest
from pydantic import Field
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import Settings
from app.database.db import Base
from app.tools.audit import AuditLog, redact
from app.tools.base import ArgsModel, RiskTier, Tool, ToolArgumentError, ToolResult, ToolSecurity, schema_to_model
from app.tools.catalog import build_registry, exposed_tool_names
from app.tools.context import ToolContext
from app.tools.executor import ToolExecutor
from app.tools.registry import ToolRegistry


@pytest.fixture()
def audit():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(bind=engine)
    log = AuditLog(sessionmaker(bind=engine))
    yield log
    engine.dispose()


class EchoArgs(ArgsModel):
    text: str = Field(..., min_length=1)
    times: int = Field(1, ge=1, le=3)


class EchoTool(Tool):
    name = "echo"
    description = "echo"
    Args = EchoArgs
    risk = RiskTier.READ_ONLY

    async def execute(self, db=None, text: str = "", times: int = 1, **_) -> ToolResult:
        return ToolResult(success=True, data=text * times)


class WriteTool(Tool):
    name = "writer"
    description = "writes"
    Args = EchoArgs
    risk = RiskTier.WRITE
    runs = 0

    async def execute(self, db=None, **_) -> ToolResult:
        WriteTool.runs += 1
        return ToolResult(success=True)


class SlowTool(Tool):
    name = "slow"
    description = "slow"
    risk = RiskTier.READ_ONLY
    timeout_seconds = 0.05

    async def execute(self, db=None, **_) -> ToolResult:
        await asyncio.sleep(1)
        return ToolResult(success=True)


class SecretArgsTool(Tool):
    name = "login"
    description = "x"
    parameters = {"type": "object", "properties": {"user": {"type": "string"}, "password": {"type": "string"}}}
    risk = RiskTier.READ_ONLY

    async def execute(self, db=None, **_) -> ToolResult:
        return ToolResult(success=True, data="ok")


class NoTaintTool(Tool):
    name = "remember_x"
    description = "x"
    risk = RiskTier.CONTROL
    allow_when_tainted = False

    async def execute(self, db=None, **_) -> ToolResult:
        return ToolResult(success=True)


class ContextTool(Tool):
    name = "ctx_tool"
    description = "x"
    risk = RiskTier.READ_ONLY
    seen: Optional[ToolContext] = None

    async def execute(self, db=None, ctx: Optional[ToolContext] = None, **_) -> ToolResult:
        ContextTool.seen = ctx
        return ToolResult(success=True)


@pytest.fixture()
def executor(audit):
    reg = ToolRegistry()
    for cls in (EchoTool, WriteTool, SlowTool, SecretArgsTool, NoTaintTool, ContextTool):
        reg.register(cls())
    return ToolExecutor(reg, audit=audit)


def test_risk_tier_keeps_legacy_security_in_sync():
    assert EchoTool.security == ToolSecurity.SAFE
    assert WriteTool.security == ToolSecurity.CONFIRM_REQUIRED
    assert RiskTier.CONTROL.needs_approval is False and RiskTier.DESTRUCTIVE.needs_approval is True


def test_pydantic_schema_is_compact_for_small_models():
    schema = EchoTool.parameters
    assert schema["required"] == ["text"]
    assert "title" not in schema and "title" not in schema["properties"]["text"]


def test_schema_to_model_handles_legacy_json_schema():
    model = schema_to_model("t", {"type": "object", "properties": {"n": {"type": "integer"}, "mode": {"type": "string", "enum": ["a", "b"]}}, "required": ["mode"]})
    assert model.model_validate({"n": "5", "mode": "a"}).n == 5
    with pytest.raises(Exception):
        model.model_validate({"mode": "c"})


def test_validation_errors_are_readable_for_retry():
    with pytest.raises(ToolArgumentError, match="times"):
        EchoTool().validate_args({"text": "x", "times": 99})


def test_validation_drops_unknown_keys_and_unset_defaults():
    assert EchoTool().validate_args({"text": "x", "bogus": 1}) == {"text": "x"}


async def test_executor_runs_read_only_and_audits(executor, audit):
    result = await executor.execute("echo", {"text": "ab", "times": 2})
    assert result.data == "abab"
    entry = audit.recent(1)[0]
    assert entry["tool"] == "echo" and entry["decision"] == "auto" and entry["success"] is True


async def test_executor_refuses_write_without_approval(executor, audit):
    WriteTool.runs = 0
    result = await executor.execute("writer", {"text": "x"})
    assert result.success is False and "approval" in result.error
    assert WriteTool.runs == 0
    assert audit.recent(1)[0]["decision"] == "needs_approval"
    ok = await executor.execute("writer", {"text": "x"}, approval_id="abc")
    assert ok.success and WriteTool.runs == 1
    assert audit.recent(1)[0]["decision"] == "approved" and audit.recent(1)[0]["approval_id"] == "abc"


async def test_invalid_arguments_are_rejected_and_audited(executor, audit):
    result = await executor.execute("echo", {"times": 2})
    assert result.success is False and "text" in result.error
    assert audit.recent(1)[0]["decision"] == "invalid"


async def test_unknown_tool(executor, audit):
    result = await executor.execute("nope", {})
    assert not result.success and audit.recent(1)[0]["decision"] == "unknown_tool"


async def test_tool_timeout(executor):
    result = await executor.execute("slow", {})
    assert result.success is False and "timed out" in result.error


async def test_taint_blocks_sensitive_tools(executor, audit):
    result = await executor.execute("remember_x", {}, ctx=ToolContext(tainted=True))
    assert result.success is False and "untrusted" in result.error
    assert audit.recent(1)[0]["decision"] == "tainted_blocked"
    assert (await executor.execute("remember_x", {}, ctx=ToolContext(tainted=False))).success


async def test_secrets_are_redacted_in_audit(executor, audit):
    await executor.execute("login", {"user": "silvan", "password": "hunter2"})
    entry = audit.recent(1)[0]
    assert entry["arguments"]["password"] == "***" and entry["arguments"]["user"] == "silvan"
    assert redact({"api_key": "x", "Token": "y", "name": "z"}) == {"api_key": "***", "Token": "***", "name": "z"}


async def test_context_only_passed_to_tools_that_declare_it(executor):
    ctx = ToolContext(source="voice")
    await executor.execute("ctx_tool", {}, ctx=ctx)
    assert ContextTool.seen is ctx


def test_catalog_discovers_all_tools_and_exposes_curated_set():
    reg = build_registry()
    names = {t.name for t in reg.list_tools()}
    for expected in ("set_volume", "media_control", "set_timer", "save_note", "web_search", "fetch_url", "open_app", "get_weather", "read_clipboard", "run_command"):
        assert expected in names
    exposed = exposed_tool_names(Settings(_env_file=None, ALLOWED_DIRECTORIES=""), reg)
    assert "set_volume" in exposed and "run_command" not in exposed
    assert "read_file" not in exposed  # no allowed directory configured


def test_catalog_extra_and_disabled(tmp_path):
    reg = build_registry()
    s = Settings(_env_file=None, TOOLS_EXTRA="list_tasks", TOOLS_DISABLED="web_search", ALLOWED_DIRECTORIES=str(tmp_path))
    exposed = exposed_tool_names(s, reg)
    assert "list_tasks" in exposed and "web_search" not in exposed and "read_file" in exposed


def test_every_registered_tool_has_valid_metadata():
    for tool in build_registry().list_tools():
        assert tool.name and tool.description, tool
        assert isinstance(tool.base_risk(), RiskTier)
        schema = tool.openai_schema()
        assert schema["function"]["parameters"]["type"] == "object"
        tool.args_model()  # must build


def test_registered_risk_tiers_for_dangerous_tools():
    reg = build_registry()
    for name in ("run_command", "delete_file", "move_file", "git_push", "open_application", "close_application", "delete_task"):
        assert reg.get(name).base_risk() == RiskTier.DESTRUCTIVE, name
    for name in ("save_note", "write_file", "git_commit"):
        assert reg.get(name).base_risk() == RiskTier.WRITE, name
    for name in ("set_volume", "media_control", "set_timer", "open_app", "write_clipboard"):
        assert reg.get(name).base_risk() == RiskTier.CONTROL, name
