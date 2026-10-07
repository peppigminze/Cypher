import { CypherBubble } from "./CypherBubble";
import { useCypherVoiceState, type PendingConfirmation } from "../hooks/useCypherVoiceState";
import type { SystemStatus } from "../types";

interface Props {
  status: SystemStatus | null;
  onOpenMenu: () => void;
}

/** Short, non-technical summary of a pending tool call's arguments for
 * the visible confirmation panel - not spoken (the backend's own
 * `reply` text is what gets sent to /speak, deliberately without raw
 * argument values so Cypher never reads e.g. a file's content aloud -
 * see app/agent/native_tool_agent.py). Truncates long values (a file's
 * content, say) so the panel stays a glance, not a wall of text. */
function summarizeArguments(args: Record<string, unknown>): string {
  const entries = Object.entries(args);
  if (entries.length === 0) return "";
  return entries
    .map(([key, value]) => {
      const text = typeof value === "string" ? value : JSON.stringify(value);
      return `${key}: ${text.length > 60 ? `${text.slice(0, 60)}…` : text}`;
    })
    .join(" · ");
}

function ConfirmPanel({ pending, onConfirm }: { pending: PendingConfirmation; onConfirm: (approved: boolean) => void }) {
  const summary = summarizeArguments(pending.arguments);
  return (
    <div className="voice-view__confirm">
      <p className="voice-view__confirm-summary">
        <strong>{pending.toolName}</strong>
        {summary && <> — {summary}</>}
      </p>
      <div className="voice-view__confirm-actions">
        <button type="button" className="voice-view__confirm-btn voice-view__confirm-btn--yes" onClick={() => onConfirm(true)}>
          Ja
        </button>
        <button type="button" className="voice-view__confirm-btn voice-view__confirm-btn--no" onClick={() => onConfirm(false)}>
          Nein
        </button>
      </div>
    </div>
  );
}

/**
 * Full-screen, orb-centric main view - the new landing screen (see
 * App.tsx: view defaults to "voice" now, Chat/Tasks/Memory/Projects/
 * Settings are unchanged and reachable via the menu button here).
 *
 * Deliberately minimal: the bubble is the only real content. Everything
 * else (settings, logs, tool history - i.e. the rest of the app) lives
 * behind onOpenMenu, and the footer is a single status line, not a
 * dashboard. The one exception is the confirm panel below, which only
 * renders while a tool call is actually awaiting approval.
 *
 * KNOWN GAPS - see /TODO.md for the full list and why they're deferred:
 * no interrupt handling while thinking/speaking, push-to-talk only (no
 * silence detection).
 */
export function CypherVoiceView({ status, onOpenMenu }: Props) {
  const voice = useCypherVoiceState();

  const caption = !voice.isSupported
    ? "Mikrofon in diesem Browser/Kontext nicht verfügbar."
    : voice.state === "listening"
      ? "Ich höre zu ..."
      : voice.state === "thinking"
        ? voice.transcript || "Verarbeite ..."
        : voice.state === "confirming"
          ? voice.error || voice.reply || "Bestätigung erforderlich."
          : voice.state === "speaking"
            ? voice.reply
            : voice.error || "Tippen zum Sprechen";

  return (
    <div className="voice-view">
      {/* Sicherheitsaspekt (Phase 7): welcher Provider gerade aktiv ist,
          muss jederzeit sichtbar sein - kein unbemerkter Cloud-Modus.
          Nur eine Anzeige; die Wahl selbst passiert in den Settings. */}
      {voice.provider && (
        <span
          className={`voice-view__provider-badge voice-view__provider-badge--${voice.provider}`}
          title={voice.provider === "cloud" ? "Cloud-Anbindung aktiv" : "Lokales Modell aktiv"}
        >
          {voice.provider === "cloud" ? "☁ Cloud" : "🖥 Lokal"}
        </span>
      )}

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

        {voice.state === "confirming" && voice.pendingConfirmation && (
          <ConfirmPanel pending={voice.pendingConfirmation} onConfirm={voice.confirmPending} />
        )}
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
