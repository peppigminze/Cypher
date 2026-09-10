import { CypherBubble } from "./CypherBubble";
import { useCypherVoiceState } from "../hooks/useCypherVoiceState";
import type { SystemStatus } from "../types";

interface Props {
  status: SystemStatus | null;
  onOpenMenu: () => void;
}

/**
 * Full-screen, orb-centric main view - the new landing screen (see
 * App.tsx: view defaults to "voice" now, Chat/Tasks/Memory/Projects/
 * Settings are unchanged and reachable via the menu button here).
 *
 * Deliberately minimal: the bubble is the only real content. Everything
 * else (settings, logs, tool history - i.e. the rest of the app) lives
 * behind onOpenMenu, and the footer is a single status line, not a
 * dashboard.
 *
 * KNOWN GAPS - see /TODO.md for the full list and why they're deferred:
 * no interrupt handling while thinking/speaking, push-to-talk only (no
 * silence detection), and no UI yet for the backend's write_file
 * approval gate (`pending_confirmation` from /api/tool-chat is not
 * handled here - see useCypherVoiceState.ts).
 */
export function CypherVoiceView({ status, onOpenMenu }: Props) {
  const voice = useCypherVoiceState();

  const caption = !voice.isSupported
    ? "Mikrofon in diesem Browser/Kontext nicht verfügbar."
    : voice.state === "listening"
      ? "Ich höre zu ..."
      : voice.state === "thinking"
        ? voice.transcript || "Verarbeite ..."
        : voice.state === "speaking"
          ? voice.reply
          : voice.error || "Tippen zum Sprechen";

  return (
    <div className="voice-view">
      <button
        type="button"
        className="voice-view__menu-btn"
        onClick={onOpenMenu}
        title="Menü öffnen"
        aria-label="Menü öffnen"
      >
        <svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true">
          <path d="M4 6h16M4 12h16M4 18h16" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" />
        </svg>
      </button>

      <div className="voice-view__stage">
        <CypherBubble
          state={voice.state}
          micStream={voice.micStream}
          disabled={!voice.isSupported}
          onClick={voice.toggleListening}
        />
        <p className={`voice-view__caption ${voice.error ? "is-error" : ""}`}>{caption}</p>
      </div>

      <footer className="voice-view__statusbar">
        <span className={`voice-view__status-dot ${status?.pc_online ? "is-online" : "is-offline"}`} aria-hidden="true" />
        <span className="voice-view__status-text">
          {status ? (status.pc_online ? "Verbunden" : "Getrennt") : "Verbinde ..."}
        </span>
        {voice.lastTask && (
          <span className="voice-view__last-task">
            · Letzte Aktion: {voice.lastTask.tool}
            {!voice.lastTask.success && " (fehlgeschlagen)"}
          </span>
        )}
      </footer>
    </div>
  );
}
