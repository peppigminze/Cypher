"""
Guard against "claimed but not done": the model answers "Notiz gespeichert."
without ever calling save_note.

Small local models do this now and then (see app/persona.py for how badly
a single prompt sentence can trigger it). The rule is simple: success may
only be reported after a tool result. The engine uses two checks:

  looks_like_action_request(user_text)
      cheap regex: does the user want something DONE (note, timer, volume,
      music, clipboard, app, memory)? Information questions ("Was ist ein
      Timer?") don't count; polite requests ("Kannst du die Musik
      anhalten?") do. Only then is the guard armed, so normal questions
      stream without any delay.
  claims_action(reply)
      does a SHORT answer say it did or is doing something ("gespeichert",
      "Timer läuft", "Ich stelle die Lautstärke runter")? Long answers are
      explanations, not confirmations.

If both are true and the step made no tool call, the engine discards the
reply (it was held back, never spoken), retries once with an explicit hint,
and if the model claims success again it says honestly that nothing happened.
"""
from __future__ import annotations

import re

_ACTION_REQUEST = re.compile(
    r"\b("
    r"notiz\w*|notier\w*|schreib\w*\s+(dir|mir|das|es)?\s*\w*\s*auf|aufschreiben|"
    r"timer|erinner\w*|wecker|wecke?\s+mich|"
    r"leiser|lauter|lautstärke|stumm\w*|ton\s+(aus|an)|zu\s+laut|zu\s+leise|"
    r"pausier\w*|pause|anhalten|musik|song|lied|titel|track|spiel\w*|überspring\w*|"
    r"zwischenablage|kopier\w*|"
    r"öffne\w*|starte\w*|mach\s+(mir\s+)?\w*\s*(auf|an)|"
    r"merk\s+dir|vergiss|such\w*|finde\w*|"
    r"stell\w*|dreh\w*|brich\w*|abbrechen"
    r")\b",
    re.IGNORECASE,
)

# "Kannst du ...?" is a request, "Was ist ...?" a question.
_POLITE_REQUEST = re.compile(r"^\s*(kannst|könntest|würdest|magst|willst)\s+du\b", re.IGNORECASE)

_CLAIM = re.compile(
    r"\b("
    r"gespeichert|notiert|aufgeschrieben|vermerkt|gemerkt|vergessen|"
    r"gestellt|eingestellt|gestartet|gesetzt|läuft|"
    r"pausiert|angehalten|gestoppt|fortgesetzt|übersprungen|abgebrochen|"
    r"leiser|lauter|stumm|reduziert|erhöht|gesenkt|\w*gedreht|"
    r"kopiert|eingefügt|geöffnet|geschlossen|"
    r"gefunden|nachgesehen|nachgeschaut|geprüft|überprüft|erledigt|gemacht|ausgeführt|mach\s+ich"
    r")\b"
    # announcing instead of doing: "Ich stelle die Lautstärke runter."
    r"|\bich\s+(stelle?|schalte?|mache?|setze?|starte?|öffne|kopiere?|speichere?|notiere?|pausiere?|drehe?|merke?|suche?|schaue?|prüfe?)\b",
    re.IGNORECASE,
)
_MAX_CLAIM_CHARS = 200

RETRY_HINT = (
    "[Hinweis: Du hast eben kein Tool aufgerufen, also ist nichts passiert. "
    "Wenn das ein Befehl ist, ruf jetzt das passende Tool auf. Sonst antworte ehrlich.]"
)
NOT_DONE_REPLY = "Das habe ich nicht ausgeführt, da ist etwas schiefgelaufen. Sag es bitte nochmal etwas genauer."


def looks_like_action_request(text: str) -> bool:
    text = (text or "").strip()
    if not _ACTION_REQUEST.search(text):
        return False
    return not text.endswith("?") or bool(_POLITE_REQUEST.match(text))


def claims_action(reply: str) -> bool:
    reply = (reply or "").strip()
    return 0 < len(reply) <= _MAX_CLAIM_CHARS and bool(_CLAIM.search(reply))
