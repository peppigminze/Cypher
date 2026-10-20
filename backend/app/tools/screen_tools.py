"""
"Was ist auf meinem Bildschirm?" - screenshot + the LOCAL vision model.

Only offered when VISION_ENABLED and the configured local model actually
has the "vision" capability (qwen3.5 has; checked via Ollama /api/show).
No extra VRAM: it is the same model that is already loaded for chat.

Privacy: the screenshot itself never leaves the PC - it always goes to the
local model, even when the cloud provider is active. The resulting text
description would reach the cloud model, so in cloud mode the call needs
approval. Screen text is untrusted (a web page could display an injection).
"""
from __future__ import annotations

import asyncio
import base64
import io
import logging
import sys
import time
from typing import Any, Dict, Optional

import httpx

from app.config import Settings, get_settings
from app.llm.base import LLMUnavailableError, collect
from app.llm.factory import build_local_provider
from app.tools.base import RiskTier, Tool, ToolResult, register_tool

logger = logging.getLogger("cypher.tools.screen")

MAX_WIDTH = 1280
_PROMPT = (
    "Beschreibe knapp auf Deutsch, was auf diesem Bildschirmfoto zu sehen ist: welche Programme/Fenster offen sind "
    "und worum es inhaltlich geht. Höchstens 5 Sätze. Gib keine Passwörter, Zugangsdaten oder vollständigen "
    "persönlichen Daten wieder."
)

_capability_cache: Dict[str, tuple[float, bool]] = {}


async def model_has_vision(settings: Settings) -> bool:
    key = f"{settings.LOCAL_LLM_BASE_URL}|{settings.LOCAL_LLM_MODEL}"
    cached = _capability_cache.get(key)
    if cached and time.monotonic() - cached[0] < 300:
        return cached[1]
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.post(f"{settings.LOCAL_LLM_BASE_URL}/api/show", json={"model": settings.LOCAL_LLM_MODEL})
            has = resp.status_code == 200 and "vision" in (resp.json().get("capabilities") or [])
    except httpx.HTTPError:
        has = False
    _capability_cache[key] = (time.monotonic(), has)
    return has


def capture_screenshot_jpeg_b64() -> str:
    from PIL import ImageGrab  # noqa: PLC0415 - optional heavy import

    image = ImageGrab.grab()
    if image.width > MAX_WIDTH:
        image = image.resize((MAX_WIDTH, int(image.height * MAX_WIDTH / image.width)))
    buffer = io.BytesIO()
    image.convert("RGB").save(buffer, format="JPEG", quality=80)
    return base64.b64encode(buffer.getvalue()).decode("ascii")


@register_tool
class DescribeScreenTool(Tool):
    name = "describe_screen"
    description = "Ein Bildschirmfoto machen und beschreiben, was gerade auf dem Bildschirm zu sehen ist."
    risk = RiskTier.READ_ONLY
    untrusted_output = True
    exposed_by_default = True
    timeout_seconds = 90.0
    spoken_progress = "Ich schaue mir den Bildschirm an."

    @classmethod
    def is_available(cls, settings: Any) -> bool:
        return sys.platform == "win32" and bool(getattr(settings, "VISION_ENABLED", False))

    def risk_for(self, args: Dict[str, Any], ctx=None) -> RiskTier:
        if ctx is not None and ctx.provider == "cloud":
            return RiskTier.WRITE
        return self.base_risk()

    def describe(self, args: Dict[str, Any]) -> str:
        return "Bildschirm fotografieren und beschreiben"

    async def execute(self, db=None, **_) -> ToolResult:
        settings = get_settings()
        if not await model_has_vision(settings):
            return ToolResult(success=False, error=f"Das Modell {settings.LOCAL_LLM_MODEL} kann keine Bilder verarbeiten.")
        try:
            image_b64 = await asyncio.to_thread(capture_screenshot_jpeg_b64)
        except (OSError, ImportError) as exc:
            return ToolResult(success=False, error=f"Bildschirmfoto fehlgeschlagen: {exc}")
        provider = build_local_provider(settings)
        try:
            response = await collect(provider.stream_chat([{"role": "user", "content": _PROMPT, "images": [image_b64]}], max_tokens=300))
        except LLMUnavailableError as exc:
            return ToolResult(success=False, error=f"Bildbeschreibung fehlgeschlagen: {exc}")
        return ToolResult(success=True, data={"description": response.content.strip()})
