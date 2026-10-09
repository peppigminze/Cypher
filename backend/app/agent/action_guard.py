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

Second mode, "lookup": questions about the current state that only a tool
can know ("Wie laut ist es gerade?", "Brauche ich heute einen Schirm?").
Answered without a tool, the answer is invented ("50 Prozent") - the engine
retries once with a hint; the second answer is passed on as it is.
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
    r"stell\w*|dreh\w*|brich\w*|abbrechen|"
    # found by the M5 suite: "Gib mir in 90 Sekunden Bescheid", "Ruf die
    # Seite ... ab", "Mach mir den Editor auf"
    r"bescheid|ruf\w*\s.*\sab|mach\w*\s+(mir\s+)?(den|die|das)\s+\w+\s+(auf|an|zu)"
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
    r"gefunden|nachgesehen|nachgeschaut|geprüft|überprüft|gelöscht|entfernt|erledigt|gemacht|ausgeführt|mach\s+ich"
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

# Requests whose tool is unambiguous. If the model claims success without a
# tool call, the retry names it: measured in M8 with "Merk dir, dass meine
# Lieblingsfarbe Petrol ist." - with the generic hint qwen3.5:9b answered
# "Ich habe mir gemerkt ..." twice in 2 of 4 runs.
_OBVIOUS_TOOL = (
    (re.compile(r"\bmerk\w*\s+dir\b|\bvergiss\s+nicht\b", re.IGNORECASE), "remember"),
    (re.compile(r"\bvergiss\b", re.IGNORECASE), "forget"),
    (re.compile(r"\bnotiz\w*|\bnotier\w*|\bschreib\w*\s+(dir|mir)?\s*\w*\s*auf\b", re.IGNORECASE), "save_note"),
    (re.compile(r"\btimer\b.*\b(ab|abbrechen|stopp\w*|lösch\w*)\b|\b(brich|stopp\w*|lösch\w*)\b.*\btimer\b", re.IGNORECASE), "cancel_timer"),
    (re.compile(r"\bzu\s+(laut|leise)\b", re.IGNORECASE), "set_volume"),
)


def obvious_tool(user_text: str) -> str | None:
    """The one tool an explicit request clearly asks for, if any."""
    for pattern, tool in _OBVIOUS_TOOL:
        if pattern.search(user_text):
            return tool
    return None


def retry_hint(tool: str | None) -> str:
    if tool is None:
        return RETRY_HINT
    return f"[Hinweis: Du hast eben kein Tool aufgerufen, also ist nichts passiert. Ruf jetzt das Tool {tool} auf.]"
NOT_DONE_REPLY = "Das habe ich nicht ausgeführt, da ist etwas schiefgelaufen. Sag es bitte nochmal etwas genauer."


LOOKUP_HINT = (
    "[Hinweis: Diese Frage braucht aktuelle Daten vom PC oder aus dem Internet (Lautstärke, Wiedergabe, Timer, "
    "Zwischenablage, Wetter, Auslastung ...). Rate nicht - ruf das passende Tool auf. Wenn kein Tool passt, sag das ehrlich.]"
)

# Questions about the CURRENT state of something only a tool can see
# ("Wie laut ist es gerade?", "Was läuft gerade?", "Wird es morgen regnen?").
# Needs a topic AND a "now/mine" marker, so "Was ist ein Timer?" stays a
# normal knowledge question.
_LOOKUP_TOPIC = re.compile(
    r"\b(lautstärke|wie\s+laut|läuft|spielt|lied|song|titel|timer|wecker|erinnerung\w*|zwischenablage|"
    r"wetter|regn\w*|regen|schirm|schnee|temperatur|grad|auslastung|ausgelastet|arbeitsspeicher|ram|cpu|"
    r"notiz\w*|notiert|bildschirm)\b",
    re.IGNORECASE,
)
_LOOKUP_NOW = re.compile(r"\b(gerade|aktuell|jetzt|momentan|heute|morgen|noch|mein\w*|eingestellt|draussen|draußen)\b", re.IGNORECASE)


def looks_like_lookup_request(text: str) -> bool:
    text = (text or "").strip()
    return text.endswith("?") and bool(_LOOKUP_TOPIC.search(text)) and bool(_LOOKUP_NOW.search(text))


def answered_without_lookup(reply: str) -> bool:
    """A lookup question answered without a tool - unless the model asked
    back ("Für welchen Ort?"), which is fine."""
    reply = (reply or "").strip()
    return bool(reply) and not reply.endswith("?")


def guard_mode(text: str) -> "str | None":
    """"action", "lookup" or None - which guard (if any) watches this turn."""
    if looks_like_action_request(text):
        return "action"
    if looks_like_lookup_request(text):
        return "lookup"
    return None


def looks_like_action_request(text: str) -> bool:
    text = (text or "").strip()
    if not _ACTION_REQUEST.search(text):
        return False
    return not text.endswith("?") or bool(_POLITE_REQUEST.match(text))


def claims_action(reply: str) -> bool:
    reply = (reply or "").strip()
    return 0 < len(reply) <= _MAX_CLAIM_CHARS and bool(_CLAIM.search(reply))


# A refusal or "nothing there" is an honest answer, not a fake confirmation.
_REFUSAL = re.compile(r"\b(nicht|nichts|kein\w*|leider|unmöglich|geht\s+nicht)\b", re.IGNORECASE)


def unverified_action_reply(reply: str) -> bool:
    """The model answered an ACTION request without calling a tool. Suspicious
    unless it asks back or refuses: a word list of success phrases is never
    complete ("Das habe ich getan." slipped through one), so any short
    statement counts - plus every explicit claim, even next to a refusal
    ("Ich habe keine Timer gefunden" claims a check that never happened)."""
    reply = (reply or "").strip()
    if not reply or "?" in reply:
        return False
    if claims_action(reply):
        return True
    return len(reply) <= _MAX_CLAIM_CHARS and not _REFUSAL.search(reply)
