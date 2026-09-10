# Cypher — Known Gaps / TODO

Living list of known-open items in the **native tool-calling pipeline**
(`/api/tool-chat`, `backend/app/agent/native_tool_agent.py`) and the
**Voice UI** (`frontend/src/components/CypherVoiceView.tsx`,
`frontend/src/hooks/useCypherVoiceState.ts`) from Phase 5/6. This does
**not** cover the old `/api/messages` / `CypherAgent` pipeline
(`backend/app/agent/core.py`), which is a separate, untouched path.

Written during the pre-Phase-6 stabilization pass (2026-09-10) so these
don't get forgotten before more feature work lands on top.

## Voice UI (Phase 5, unchanged in the stabilization pass)

- **No interrupt handling.** Tapping the bubble while Cypher is
  `thinking` or `speaking` does nothing (see the comment in
  `useCypherVoiceState.ts`'s `toggleListening`). There's no way to
  cut off a long reply or cancel an in-flight request from the UI.
- **Push-to-talk only, no silence detection.** Recording starts/stops
  on tap; there's no voice-activity detection (VAD) to auto-stop when
  the user stops talking. Same interaction model as the
  `stt-test.html`/`tts-test.html` reference pages this was built from.
- **No dedicated tool-history/log view.** The Phase 5 request asked
  for "Tool-Historie" to live behind the menu icon eventually - it
  doesn't exist yet. `useCypherVoiceState`'s `lastTask` only tracks the
  single most recent tool call for the status bar, nothing persisted
  or browsable.

## Approval gate (new in the stabilization pass, backend-only)

`/api/tool-chat` now pauses instead of executing a CONFIRM_REQUIRED
tool (currently just `write_file`) and returns `pending_confirmation`
(`approval_id`, `tool_name`, `arguments`). Call
`POST /api/tool-chat/confirm` with `{approval_id, approved}` to
resolve it - see `backend/app/tools/pending_approvals.py` and
`backend/app/agent/native_tool_agent.py` for the mechanics.

**This is API-level only - nothing in the Voice UI surfaces or acts on
`pending_confirmation` yet.** Today, if a voice request triggers
`write_file`, Cypher will *say* "Ich möchte 'write_file' ausführen.
Bestätigst du das?" (that's `reply`, and it does get spoken via
`/speak` since `useCypherVoiceState` always sends `chatResult.reply`
to TTS) - but there is no way to answer by voice or tap yet. The
pipeline just returns to `idle` afterwards, and the tool never runs
unless something calls `/api/tool-chat/confirm` directly (e.g. via
curl, as done in the stabilization pass's test run - see the phase's
status report for details).

Needed before this is actually usable end-to-end via voice:
1. `useCypherVoiceState` needs to inspect `pending_confirmation` on
   the `/api/tool-chat` response and enter some kind of
   `awaiting-confirmation` sub-state instead of going straight to
   `speaking` → `idle`.
2. `CypherBubble`/`CypherVoiceView` need a way to capture the answer -
   simplest version: a second short recording ("ja"/"nein") transcribed
   and matched against a small yes/no vocabulary, or two visible
   buttons that appear during the confirmation prompt (the "hands are
   free" voice pitch takes a hit here either way - worth a deliberate
   design decision, not a rushed one).
3. On "yes", call `/api/tool-chat/confirm` and resume the normal
   `thinking → speaking` flow with its response; on "no" (or a timeout
   - `pending_approvals.py`'s `PENDING_TTL_SECONDS` is 5 minutes),
   drop back to `idle` without ever calling confirm.

## Risk tiers (future direction, not needed yet)

`ToolSecurity` (`backend/app/tools/base.py`) is a flat
`SAFE` / `CONFIRM_REQUIRED` / `BLOCKED` enum per tool - all-or-nothing.
The personalJarvis reference project's Tool protocol supports per-call
risk overrides (e.g. `risk_tier_for_args(args)` - a Gmail tool that's
`safe` to read but `ask` to send). If more CONFIRM_REQUIRED tools get
exposed to the native tool-calling allowlist later
(`DEFAULT_TOOL_NAMES` in `native_tool_agent.py`), consider that pattern
instead of extending the flat enum. Not worth building for write_file
alone.
