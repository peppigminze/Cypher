"""
Where the LLM's context window (LOCAL_LLM_NUM_CTX) goes - shown in
/api/diagnostics and checked at startup.

    system prompt + tool definitions      measured (estimate)
    core memory                           <= MEMORY_CORE_MAX_TOKENS
    conversation history                  <= HISTORY_MAX_TOKENS
    recalled archive memories             <= MEMORY_RECALL_MAX_TOKENS
    one big tool result (web page)        <= FETCH_MAX_CHARS
    reserve for the answer                ANSWER_RESERVE_TOKENS

If the sum is larger than the window, Ollama silently drops the start of
the prompt (the system prompt!) - this check makes that visible instead.
"""
from __future__ import annotations

import json
from typing import Any, Dict

from app.config import Settings

ANSWER_RESERVE_TOKENS = 1024
CHARS_PER_TOKEN = 3.5


def _tokens(text: str) -> int:
    return int(len(text) / CHARS_PER_TOKEN) + 1


def context_budget(settings: Settings) -> Dict[str, Any]:
    from app.persona import SYSTEM_PROMPT  # noqa: PLC0415
    from app.tools.catalog import build_registry, exposed_tool_names  # noqa: PLC0415
    from app.tools.executor import to_openai_tools  # noqa: PLC0415

    registry = build_registry()
    tools = to_openai_tools(registry, exposed_tool_names(settings, registry))
    parts = {
        "system_prompt": _tokens(SYSTEM_PROMPT),
        "tool_definitions": _tokens(json.dumps(tools, ensure_ascii=False)),
        "core_memory_max": settings.MEMORY_CORE_MAX_TOKENS,
        "history_max": settings.HISTORY_MAX_TOKENS,
        "recall_max": settings.MEMORY_RECALL_MAX_TOKENS,
        "tool_result_max": _tokens("x" * settings.FETCH_MAX_CHARS),
        "answer_reserve": ANSWER_RESERVE_TOKENS,
    }
    total = sum(parts.values())
    return {"parts": parts, "total": total, "window": settings.LOCAL_LLM_NUM_CTX, "fits": total <= settings.LOCAL_LLM_NUM_CTX}
