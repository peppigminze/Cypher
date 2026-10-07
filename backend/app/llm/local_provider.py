"""
Backward-compatible name for the local provider.

`LocalLLMProvider` used to talk to Ollama's OpenAI-compatible endpoint;
it is now the native-API OllamaProvider (see app/llm/ollama.py for why).
Existing imports and constructor calls `LocalLLMProvider(base_url, model,
timeout_seconds)` keep working unchanged.
"""
from __future__ import annotations

from app.llm.ollama import OllamaProvider

LocalLLMProvider = OllamaProvider

__all__ = ["LocalLLMProvider"]
