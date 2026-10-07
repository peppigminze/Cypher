"""
Tool framework.

A tool is a class with
    name, description                  what the LLM sees
    Args (pydantic model) or parameters (JSON schema)   its arguments
    risk                               RiskTier, see below
    execute(db=None, ctx=None, **args) the implementation

Tools register themselves with `@register_tool` (app/tools/catalog.py
discovers every module in this package), so adding a tool means adding
one class - no list to maintain elsewhere. Execution ALWAYS goes through
app/tools/executor.py, which validates arguments, enforces the risk tier,
applies timeouts and writes the audit log.

Risk tiers (enforced by the executor, not by politeness of the LLM):
    read_only    no side effects                       -> runs automatically
    control      harmless, instantly reversible PC     -> runs automatically
                 control (volume, media keys, timer,
                 whitelisted app start, clipboard write)
    write        creates/changes data (note, file,     -> needs the user's approval
                 memory entry)
    destructive  overwrites/deletes, arbitrary         -> approval + warning
                 commands, git push
    blocked      never runs

`control` exists because "mach leiser" must act immediately - asking
"Bist du sicher?" before every volume change would make the assistant
unusable. It is still fully audited.

Legacy: the older `security` attribute (SAFE / CONFIRM_REQUIRED /
BLOCKED) is kept in sync with `risk` so the old pipeline keeps working
until it is removed (M5).
"""
from __future__ import annotations

import enum
import json
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, ClassVar, Dict, Literal, Optional, Tuple, Type

from pydantic import BaseModel, ConfigDict, Field, ValidationError, create_model

if TYPE_CHECKING:
    from app.tools.context import ToolContext


class ToolSecurity(str, enum.Enum):
    SAFE = "SAFE"
    CONFIRM_REQUIRED = "CONFIRM_REQUIRED"
    BLOCKED = "BLOCKED"


class RiskTier(str, enum.Enum):
    READ_ONLY = "read_only"
    CONTROL = "control"
    WRITE = "write"
    DESTRUCTIVE = "destructive"
    BLOCKED = "blocked"

    @property
    def needs_approval(self) -> bool:
        return self in (RiskTier.WRITE, RiskTier.DESTRUCTIVE)

    @property
    def label_de(self) -> str:
        return {
            RiskTier.READ_ONLY: "nur lesen",
            RiskTier.CONTROL: "steuern",
            RiskTier.WRITE: "schreiben",
            RiskTier.DESTRUCTIVE: "verändert/löscht Daten",
            RiskTier.BLOCKED: "gesperrt",
        }[self]


_RISK_TO_SECURITY = {
    RiskTier.READ_ONLY: ToolSecurity.SAFE,
    RiskTier.CONTROL: ToolSecurity.SAFE,
    RiskTier.WRITE: ToolSecurity.CONFIRM_REQUIRED,
    RiskTier.DESTRUCTIVE: ToolSecurity.CONFIRM_REQUIRED,
    RiskTier.BLOCKED: ToolSecurity.BLOCKED,
}
_SECURITY_TO_RISK = {
    ToolSecurity.SAFE: RiskTier.READ_ONLY,
    ToolSecurity.CONFIRM_REQUIRED: RiskTier.WRITE,
    ToolSecurity.BLOCKED: RiskTier.BLOCKED,
}


@dataclass
class ToolResult:
    success: bool
    data: Any = None
    error: str | None = None
    # Set by the executor for tools whose output comes from outside
    # (files, web pages, clipboard, screen): it is data, never instructions.
    untrusted: bool = False


class ToolArgumentError(ValueError):
    """Arguments failed validation. The message is fed back to the LLM so it can retry."""


# ------------------------------------------------------------------ JSON schema -> pydantic
_JSON_TYPES: Dict[str, Any] = {"string": str, "integer": int, "number": float, "boolean": bool, "object": dict}


def _field_type(spec: Dict[str, Any]) -> Any:
    if "enum" in spec and spec["enum"]:
        return Literal[tuple(spec["enum"])]  # type: ignore[valid-type]
    json_type = spec.get("type", "string")
    if json_type == "array":
        item = spec.get("items", {}).get("type", "string")
        return list[_JSON_TYPES.get(item, Any)]  # type: ignore[misc]
    return _JSON_TYPES.get(json_type, Any)


def schema_to_model(name: str, schema: Dict[str, Any]) -> Type[BaseModel]:
    """Build a pydantic model from the simple JSON schemas older tools declare."""
    required = set(schema.get("required", []))
    fields: Dict[str, Tuple[Any, Any]] = {}
    for prop, spec in (schema.get("properties") or {}).items():
        typ = _field_type(spec)
        constraints = {k: spec[k] for k in ("minimum", "maximum") if k in spec}
        kwargs = {"ge": constraints.get("minimum"), "le": constraints.get("maximum"), "description": spec.get("description")}
        kwargs = {k: v for k, v in kwargs.items() if v is not None}
        if prop in required:
            fields[prop] = (typ, Field(..., **kwargs))
        else:
            fields[prop] = (Optional[typ], Field(None, **kwargs))
    return create_model(f"{name}_args", __config__=ConfigDict(extra="ignore"), **fields)  # type: ignore[call-overload]


class ArgsModel(BaseModel):
    """Base for tool argument models: unknown keys from a chatty model are ignored."""

    model_config = ConfigDict(extra="ignore")


def _clean_schema(schema: Dict[str, Any]) -> Dict[str, Any]:
    """pydantic's JSON schema is verbose (titles, anyOf-with-null). Small
    local models pick tools more reliably from a compact schema."""

    def strip(node: Any) -> Any:
        if isinstance(node, list):
            return [strip(x) for x in node]
        if not isinstance(node, dict):
            return node
        node = {k: strip(v) for k, v in node.items() if k != "title"}
        any_of = node.get("anyOf")
        if isinstance(any_of, list):
            non_null = [x for x in any_of if not (isinstance(x, dict) and x.get("type") == "null")]
            if len(non_null) == 1 and isinstance(non_null[0], dict):
                merged = {k: v for k, v in node.items() if k != "anyOf"}
                merged.update(non_null[0])
                node = merged
        if "default" in node and node["default"] is None:
            del node["default"]
        return node

    cleaned = strip(schema)
    cleaned.setdefault("type", "object")
    cleaned.setdefault("properties", {})
    cleaned.pop("additionalProperties", None)
    return cleaned


# ------------------------------------------------------------------ Tool base class
class Tool(ABC):
    name: ClassVar[str]
    description: ClassVar[str]
    # Either a pydantic Args model (preferred) or a JSON schema dict.
    Args: ClassVar[Optional[Type[BaseModel]]] = None
    parameters: ClassVar[Dict[str, Any]] = {"type": "object", "properties": {}}
    risk: ClassVar[RiskTier] = RiskTier.READ_ONLY
    security: ClassVar[ToolSecurity] = ToolSecurity.SAFE
    # Output comes from outside Cypher (files, web, clipboard, screen).
    untrusted_output: ClassVar[bool] = False
    # False: refuse to run once untrusted content entered the current turn
    # (prompt-injection defence for memory writes and similar).
    allow_when_tainted: ClassVar[bool] = True
    # Offered to the LLM by default (others need TOOLS_EXTRA in .env).
    exposed_by_default: ClassVar[bool] = False
    timeout_seconds: ClassVar[Optional[float]] = None
    # Short sentence spoken while a slow tool runs ("Ich schaue nach."), so
    # voice users don't sit in silence for seconds. None for fast tools.
    spoken_progress: ClassVar[Optional[str]] = None

    _args_model_cache: ClassVar[Optional[Type[BaseModel]]] = None

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        own = cls.__dict__
        if "risk" in own:
            cls.security = _RISK_TO_SECURITY[cls.risk]
        elif "security" in own:
            cls.risk = _SECURITY_TO_RISK[cls.security]
        if "Args" in own and cls.Args is not None:
            cls.parameters = _clean_schema(cls.Args.model_json_schema())
        cls._args_model_cache = None

    @classmethod
    def is_available(cls, settings: Any) -> bool:
        """Whether the tool can work with the current configuration/OS.
        Unavailable tools are not offered to the LLM (fewer, relevant tools
        = better tool choice and a shorter prompt)."""
        return True

    # ---------------------------------------------------------- arguments
    @classmethod
    def args_model(cls) -> Type[BaseModel]:
        if cls._args_model_cache is None:
            cls._args_model_cache = cls.Args or schema_to_model(cls.__name__, cls.parameters or {})
        return cls._args_model_cache

    def validate_args(self, raw: Dict[str, Any]) -> Dict[str, Any]:
        """Validated, coerced arguments with unknown keys dropped and
        unset optional keys omitted (so the tool's own defaults apply)."""
        # Test doubles (and dynamic tools) may set `parameters` per instance.
        model_cls = schema_to_model(self.name, self.__dict__["parameters"]) if "parameters" in self.__dict__ else self.args_model()
        try:
            model = model_cls.model_validate(raw or {})
        except ValidationError as exc:
            problems = "; ".join(f"{'.'.join(str(p) for p in e['loc']) or 'arguments'}: {e['msg']}" for e in exc.errors())
            raise ToolArgumentError(f"Invalid arguments for '{self.name}': {problems}") from exc
        return model.model_dump(exclude_unset=True)

    # ---------------------------------------------------------- risk + presentation
    def base_risk(self) -> RiskTier:
        """Class-level risk, honouring instance overrides (test doubles set
        `self.security`/`self.risk` in __init__)."""
        inst = self.__dict__
        if "risk" in inst:
            return inst["risk"]
        if "security" in inst:
            return _SECURITY_TO_RISK[inst["security"]]
        return type(self).risk

    def risk_for(self, args: Dict[str, Any], ctx: Optional["ToolContext"] = None) -> RiskTier:
        """Per-call risk. Override when the same tool is harmless for some
        arguments and dangerous for others (e.g. writing a new file vs.
        overwriting one)."""
        return self.base_risk()

    def describe(self, args: Dict[str, Any]) -> str:
        """One German line for the approval card / audit log."""
        shown = ", ".join(f"{k}={_short(v)}" for k, v in args.items())
        return f"{self.name}({shown})"

    def schema(self) -> Dict[str, Any]:
        return {"name": self.name, "description": self.description, "parameters": self.parameters, "security": self.security.value, "risk": self.risk.value}

    def openai_schema(self) -> Dict[str, Any]:
        return {"type": "function", "function": {"name": self.name, "description": self.description, "parameters": self.parameters}}

    @abstractmethod
    async def execute(self, db: Any = None, **kwargs: Any) -> ToolResult:
        """Implementation. Receives validated arguments as keyword args plus
        `db` (SQLAlchemy session or None) and `ctx` (ToolContext or None)."""
        raise NotImplementedError


def _short(value: Any, limit: int = 60) -> str:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
    return text if len(text) <= limit else text[: limit - 1] + "…"


# ------------------------------------------------------------------ registration
_REGISTERED: Dict[str, Type[Tool]] = {}


def register_tool(cls: Type[Tool]) -> Type[Tool]:
    """Class decorator: make the tool discoverable by app/tools/catalog.py."""
    existing = _REGISTERED.get(cls.name)
    if existing is not None and existing is not cls and existing.__qualname__ != cls.__qualname__:
        raise ValueError(f"Two tools registered under the name '{cls.name}': {existing} and {cls}")
    _REGISTERED[cls.name] = cls
    return cls


def registered_tools() -> Dict[str, Type[Tool]]:
    return dict(_REGISTERED)
