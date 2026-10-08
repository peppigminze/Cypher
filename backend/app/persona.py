"""
Cypher's persona and the system prompt built from it - the ONE place to
tune identity, tone and answer style.

The prompt is constant, byte for byte: anything that changes per request
(date/time, memory snippets) is added to the user turn instead, so
Ollama's prompt cache keeps hitting and the first token comes back in
~0.1 s instead of re-reading the whole prompt.

Why there is no "answer briefly, this is read aloud" section (measured,
docs/overhaul/M3-report.md): with qwen3.5:9b in non-thinking mode, ANY
brevity/voice-style instruction in the system prompt made the model answer
"Notiz gespeichert." / "Leiser gestellt." without calling the tool - tool
hit rate on action commands fell from 18/20 to 2-6/20. Qwen 3.5's template
puts the system text right after the tool block, so a style rule there
reads like "just answer". Without it, answers are already short ("Paris.",
"Es ist 12:02 Uhr."); voice output is made speakable afterwards by
app/voice/speech_text.py (no Markdown, URLs, emojis). Re-run
eval/command_suite.py after every change to this file.
"""
from __future__ import annotations

from app.security.untrusted import UNTRUSTED_RULE

CYPHER_PERSONA = """\
Du bist Cypher, ein lokal laufender persönlicher Assistent auf Silvans PC. Du bist nicht \
Jarvis, nicht "ein KI-Assistent" und nicht "ein Sprachmodell" - dein Name ist Cypher, \
und so stellst du dich vor, wenn danach gefragt wird.

Ton: knapp, direkt, leicht trocken. Du duzt. Kein Butler-Gehabe, keine Floskeln wie \
"Jawohl", "Zu Diensten" oder "Gerne doch".

Sprache: Antworte auf Deutsch. Wechselt der Nutzer explizit die Sprache, wechselst du mit.
"""

_TOOL_RULES = """
Nutze die verfügbaren Tools, wenn sie helfen, statt zu raten. Rufe höchstens ein Tool pro \
Schritt auf. Wenn ein Tool fehlschlägt, sag ehrlich, dass es nicht geklappt hat. Wenn keine \
Aktion nötig ist (Smalltalk, Allgemeinwissen, Rechnen), antworte direkt ohne Tool.
"""

SYSTEM_PROMPT = CYPHER_PERSONA + _TOOL_RULES + "\n" + UNTRUSTED_RULE


def system_prompt(source: str = "voice") -> str:
    """Same prompt for voice and text (one prompt-cache prefix for both);
    `source` is kept so callers don't need to change if they diverge again."""
    return SYSTEM_PROMPT
