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

## Approval gate — Voice UI wiring: ✅ done (2026-09-11)

`/api/tool-chat` pauses instead of executing a CONFIRM_REQUIRED tool
(currently just `write_file`) and returns `pending_confirmation`
(`approval_id`, `tool_name`, `arguments`). `POST /api/tool-chat/confirm`
with `{approval_id, approved}` resolves it - see
`backend/app/tools/pending_approvals.py` and
`backend/app/agent/native_tool_agent.py` for the backend mechanics
(unchanged from the stabilization pass).

The Voice UI now handles this end to end: a new `"confirming"` state in
`useCypherVoiceState.ts`, entered whenever a `toolChat()`/
`confirmToolCall()` response carries `pending_confirmation`. Cypher
speaks the prompt (fire-and-forget, doesn't block the UI) while two ways
to answer are live at once - visible Ja/Nein buttons
(`CypherVoiceView.tsx`'s confirm panel) and tapping the bubble again to
give a spoken "ja"/"nein" (keyword-matched via `matchYesNo`, not NLU).
Either path calls `/api/tool-chat/confirm` and resumes the normal
`thinking → speaking → idle` flow from its result. Rejecting, an
unrecognized voice answer, or a client-side mirror of the backend's
5-minute TTL (`CONFIRMATION_TTL_MS`) all resolve cleanly - no stuck
"confirming" state.

Known remaining rough edges here, all deliberately out of scope for
that pass:
- No visual/audio distinction between "answering the confirmation by
  voice" (reuses the plain "listening" waveform) and a normal new
  request - functionally fine, just not visually called out.
- The spoken confirmation prompt itself is whatever
  `native_tool_agent.py` generates (e.g. `Ich möchte "write_file"
  ausführen. Bestätigst du das?`) and deliberately does not include
  argument values (so Cypher never reads e.g. a file's content aloud) -
  the visible confirm panel shows a short argument summary instead.
  Changing the spoken text itself would mean touching the approval-gate
  backend, which is out of scope here.

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
