"""
Alias table for app words (FIX2 point 3, app/pc/app_aliases.py).

"Mach mir den Editor auf" opened VS Code with qwen3.5:4b: the model picked
the app name. Now the word is resolved deterministically - alias first,
then the exact app name, unknown -> a question. Nothing is really launched
here: the Start menu is a fixed list, os.startfile and the registry are
dummies.
"""
from __future__ import annotations

import os
import sys

import pytest

from app.agent.engine import ApprovalRequired, ConversationEngine, ConversationStore, Sentence, TurnDone
from app.agent.quick_commands import match_open_app, open_term
from app.config import get_settings
from app.pc import app_aliases, app_index
from app.pc.app_index import AppEntry
from app.tools import app_tools, pending_approvals
from app.tools.app_tools import OpenAppTool, resolve_target
from app.tools.base import RiskTier
from app.tools.registry import ToolRegistry

from tests.test_engine import ScriptedLLM, call

LOCAL = os.environ.get("LOCALAPPDATA", "C:\\Users\\x\\AppData\\Local")
VS_CODE = AppEntry("Visual Studio Code", "C:\\Start\\Code.lnk", os.path.join(LOCAL, "Programs", "Microsoft VS Code", "Code.exe"), "shortcut")
CHROME = "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe"


@pytest.fixture(autouse=True)
def _clean_approvals():
    pending_approvals.clear()
    yield
    pending_approvals.clear()


@pytest.fixture()
def start_menu(monkeypatch):
    app_index.set_entries_for_tests([
        VS_CODE,
        AppEntry("Notepad++", "C:\\Start\\Notepad++.lnk", "C:\\Program Files\\Notepad++\\notepad++.exe", "shortcut"),
        AppEntry("Discord", "C:\\Start\\Discord.lnk", os.path.join(LOCAL, "Discord", "Discord.exe"), "shortcut"),
        # an app that happens to be called like an alias word: the alias wins
        AppEntry("Browser", "C:\\Start\\Browser.lnk", "C:\\Tools\\Browser.exe", "shortcut"),
        AppEntry("Visual Studio Installer", "C:\\Start\\VSI.lnk", "C:\\Program Files (x86)\\Microsoft Visual Studio\\Installer\\setup.exe", "shortcut"),
    ])
    monkeypatch.setattr(app_aliases, "default_browser", lambda: CHROME)
    yield
    app_index.set_entries_for_tests([])


@pytest.fixture()
def launched(monkeypatch):
    calls = []
    monkeypatch.setattr(app_tools.os, "startfile", lambda target: calls.append(target), raising=False)
    return calls


@pytest.fixture()
def env_aliases(monkeypatch):
    def set_env(name, value):
        monkeypatch.setenv(name, value)
        get_settings.cache_clear()

    yield set_env
    get_settings.cache_clear()


# ------------------------------------------------------------------ resolution
@pytest.mark.parametrize(
    "word, launch",
    [
        ("Editor", "notepad.exe"), ("den Editor", "notepad.exe"), ("Notizblock", "notepad.exe"), ("Texteditor", "notepad.exe"),
        ("Rechner", "calc.exe"), ("Taschenrechner", "calc.exe"),
        ("Explorer", "explorer.exe"), ("Datei-Explorer", "explorer.exe"),
        ("Taskmanager", "taskmgr.exe"), ("Task-Manager", "taskmgr.exe"),
        ("Einstellungen", "ms-settings:"), ("Systemsteuerung", "control.exe"),
        ("Browser", CHROME), ("Webbrowser", CHROME),
    ],
)
def test_alias_words_resolve_deterministically(start_menu, word, launch):
    target = resolve_target(word)
    assert target.error == "" and target.launch == launch, target
    assert target.verdict.level == RiskTier.CONTROL


def test_default_browser_comes_from_the_registered_open_command(monkeypatch):
    assert app_aliases.exe_from_command(f'"{CHROME}" --single-argument %1') == CHROME
    assert app_aliases.exe_from_command('"C:\\Program Files\\Mozilla Firefox\\firefox.exe" -osint -url "%1"') == "C:\\Program Files\\Mozilla Firefox\\firefox.exe"
    assert app_aliases.exe_from_command("C:\\Browser\\b.exe %1") == "C:\\Browser\\b.exe"
    assert app_aliases.exe_from_command("rundll32 url.dll") == ""
    monkeypatch.setattr(app_aliases, "_browser_command", lambda: f'"{sys.executable}" %1')  # a file that exists
    assert app_aliases.default_browser() == sys.executable
    monkeypatch.setattr(app_aliases, "_browser_command", lambda: '"C:\\gibt-es-nicht\\browser.exe" %1')
    assert app_aliases.default_browser() is None
    monkeypatch.setattr(app_aliases, "_browser_command", lambda: None)
    assert app_aliases.default_browser() is None


def test_no_default_browser_is_reported(start_menu, monkeypatch):
    monkeypatch.setattr(app_aliases, "default_browser", lambda: None)
    missing = resolve_target("Browser")
    assert missing.launch == "" and "Standardbrowser" in missing.error


# ------------------------------------------------------------------ precedence
def test_alias_wins_over_the_start_menu_search(start_menu):
    assert resolve_target("Browser").launch == CHROME  # not C:\Start\Browser.lnk
    app_index.set_entries_for_tests([AppEntry("Editor", "C:\\Start\\Code.lnk", VS_CODE.target, "shortcut")])
    assert resolve_target("Editor").launch == "notepad.exe"


async def test_editor_sentence_never_reaches_the_model(start_menu, launched):
    """The reported bug: the model would have picked VS Code - it is not asked."""
    llm = ScriptedLLM([[call("open_app", {"name": "Visual Studio Code"})], ["VS Code ist offen."]])
    reg = ToolRegistry()
    reg.register(OpenAppTool())
    engine = ConversationEngine(llm, reg, store=ConversationStore(), tool_names=["open_app"])
    events = [e async for e in engine.run_turn("ed", "Mach mir den Editor auf.", source="voice")]
    assert llm.calls == [] and launched == ["notepad.exe"]
    assert [e.text for e in events if isinstance(e, Sentence)] == ["Editor wird geöffnet."]
    assert isinstance(events[-1], TurnDone) and events[-1].metrics["quick_command"] == 1.0


@pytest.mark.parametrize(
    "text, term",
    [
        ("Mach mir den Editor auf.", "editor"), ("Mach mir bitte mal den Editor auf!", "editor"), ("Hey Cypher, öffne den Rechner.", "rechner"),
        ("Starte den Taschenrechner.", "taschenrechner"), ("Kannst du mir den Browser öffnen?", "browser"), ("Editor öffnen", "editor"),
        ("Öffne VS Code", "vs code"), ("öffne Notepad++", "notepad++"),
    ],
)
def test_open_sentences_yield_the_users_word(text, term):
    assert open_term(text) == term


@pytest.mark.parametrize(
    "text",
    ["Öffne youtube.com", "Öffne C:\\Users\\x\\Bericht.pdf", "Starte die Musik", "Starte einen Timer für fünf Minuten",
     "Öffne die Datei, die ich gestern bearbeitet habe, und lies sie mir vor", "Was ist ein Editor?", "Pause."],
)
def test_other_sentences_are_not_open_commands(text):
    assert open_term(text) is None


# ------------------------------------------------------------------ exact names
def test_exact_app_names_still_work(start_menu):
    assert resolve_target("Visual Studio Code").launch == "C:\\Start\\Code.lnk"
    assert resolve_target("VS Code").launch == "C:\\Start\\Code.lnk"  # alias app:Visual Studio Code
    assert resolve_target("Notepad++").launch == "C:\\Start\\Notepad++.lnk"  # not the alias "notepad"
    quick = match_open_app("Öffne Visual Studio Code.")
    assert quick.arguments == {"name": "Visual Studio Code"} and quick.reply == "Visual Studio Code wird geöffnet."
    assert match_open_app("öffne Notepad++").arguments == {"name": "Notepad++"}


def test_alias_to_a_missing_app_says_so(start_menu):
    app_index.set_entries_for_tests([AppEntry("Discord", "C:\\Start\\Discord.lnk", "", "shortcut")])  # no VS Code
    target = resolve_target("VS Code")
    assert target.launch == "" and "Visual Studio Code" in target.error


# ------------------------------------------------------------------ unknown
async def test_unknown_word_gets_a_question_not_a_guess(start_menu, launched):
    assert match_open_app("Öffne Fotoshop") is None  # -> the model, which gets the question below
    result = await OpenAppTool().execute(name="Fotoshop")
    assert result.success is False and result.data == {"needs_clarification": True}
    assert result.error.startswith("Welche App meinst du mit 'Fotoshop'?")
    ambiguous = await OpenAppTool().execute(name="Visual Studio")
    assert ambiguous.data == {"needs_clarification": True} and "Visual Studio Code" in ambiguous.error and "Installer" in ambiguous.error
    assert launched == []


# ------------------------------------------------------------------ configurable
def test_aliases_are_configurable(start_menu, env_aliases):
    env_aliases("APP_ALIASES", "editor=app:Notepad++,chat=discord:")
    assert resolve_target("Editor").launch == "C:\\Start\\Notepad++.lnk"  # overrides the built-in entry
    assert resolve_target("Chat").launch == "discord:"
    assert match_open_app("Öffne den Chat").reply == "Chat wird geöffnet."
    assert resolve_target("Rechner").launch == "calc.exe"  # the rest of the table stays


def test_old_app_whitelist_is_still_read_and_app_aliases_wins(start_menu, env_aliases):
    env_aliases("APP_WHITELIST", "musikapp=spotify:,editor=wordpad.exe")
    env_aliases("APP_ALIASES", "editor=notepad.exe")
    assert resolve_target("musikapp").launch == "spotify:"
    assert resolve_target("Editor").launch == "notepad.exe"


async def test_alias_to_a_critical_program_still_asks(start_menu, env_aliases, launched):
    """A quick command is a shortcut for the decision, not for security."""
    env_aliases("APP_ALIASES", "konsole=powershell.exe")
    assert resolve_target("Konsole").verdict.level == RiskTier.CRITICAL
    llm = ScriptedLLM([[call("open_app", {"name": "konsole"})]])
    reg = ToolRegistry()
    reg.register(OpenAppTool())
    engine = ConversationEngine(llm, reg, store=ConversationStore(), tool_names=["open_app"])
    events = [e async for e in engine.run_turn("ko", "Öffne die Konsole.", source="voice")]
    approval = next(e for e in events if isinstance(e, ApprovalRequired))
    assert len(llm.calls) == 1 and approval.kind == "critical" and launched == []
