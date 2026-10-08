"""Quick commands (LLM-free media/volume) and the "claimed but not done" guard."""
from __future__ import annotations

import pytest

from app.agent.action_guard import claims_action, looks_like_action_request
from app.agent.quick_commands import match_quick_command, normalize


@pytest.mark.parametrize(
    "text, tool, args",
    [
        ("Pause.", "media_control", {"action": "pause"}),
        ("Pause bitte!", "media_control", {"action": "pause"}),
        ("Hey Cypher, Pause", "media_control", {"action": "pause"}),
        ("Weiter", "media_control", {"action": "play"}),
        ("Nächster Song.", "media_control", {"action": "next"}),
        ("Mach die Musik leiser.", "set_volume", {"direction": "down"}),
        ("Lauter, bitte.", "set_volume", {"direction": "up"}),
        ("Ton aus.", "set_volume", {"mute": True}),
    ],
)
def test_quick_command_matches_exact_short_commands(text, tool, args):
    command = match_quick_command(text)
    assert command is not None and command.tool == tool and command.arguments == args


@pytest.mark.parametrize(
    "text",
    ["Mach eine Pause und erklär mir das.", "Wie laut ist es?", "Pausier die Musik in fünf Minuten.", "Was ist eine Pause?", "weiter so, gut gemacht" * 5],
)
def test_quick_command_ignores_everything_else(text):
    assert match_quick_command(text) is None


def test_normalize_strips_fillers_and_punctuation():
    assert normalize("Hey Cypher, mach mal bitte LEISER!") == "mach leiser"


@pytest.mark.parametrize(
    "text",
    ["Schreib eine Notiz: Brot kaufen.", "Notiere: Brot", "Stell einen Timer auf zehn Minuten.", "Erinnere mich in einer Stunde an die Wäsche.",
     "Das ist viel zu laut gerade.", "Kannst du die Musik kurz anhalten?", "Kopier das in die Zwischenablage.", "Öffne Spotify.", "Merk dir, dass ich Tee mag."],
)
def test_action_requests_arm_the_guard(text):
    assert looks_like_action_request(text)


@pytest.mark.parametrize(
    "text",
    ["Was ist der Unterschied zwischen einem Timer und einem Wecker?", "Wie wird das Wetter morgen?", "Wer hat die Relativitätstheorie entwickelt?", "Danke dir!", "Erzähl mir einen Witz."],
)
def test_questions_and_chat_do_not_arm_the_guard(text):
    assert not looks_like_action_request(text)


@pytest.mark.parametrize(
    "reply",
    ["Notiz gespeichert: Brot kaufen.", "Timer läuft, zehn Minuten.", "Leiser gestellt.", "Erledigt.", "Text kopiert.", "Ich stell die Lautstärke runter.",
     "Ich habe keine laufenden Timer gefunden."],
)
def test_short_success_claims_are_detected(reply):
    assert claims_action(reply)


@pytest.mark.parametrize(
    "reply",
    ["Wofür soll der Timer sein?", "Für wie lange?", "Ein Timer zählt eine Dauer herunter und meldet sich am Ende. " * 4, ""],
)
def test_questions_and_explanations_are_not_claims(reply):
    assert not claims_action(reply)
