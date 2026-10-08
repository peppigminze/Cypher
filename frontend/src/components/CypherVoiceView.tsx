import { useEffect, useState, type FormEvent } from "react";
import { CypherBubble } from "./CypherBubble";
import { useVoiceSession } from "../hooks/useVoiceSession";
import { bubbleState, type ApprovalCard, type VoiceMode, type VoiceUiState } from "../hooks/voiceReducer";
import { getActiveProvider } from "../services/api";
import type { LlmProviderName, SystemStatus } from "../types";

interface Props {
  status: SystemStatus | null;
  onOpenMenu: () => void;
}

const RISK_LABEL: Record<string, string> = {
  control: "Steuert etwas am PC",
  write: "Speichert oder ändert Daten",
  destructive: "Nicht rückgängig zu machen",
};

const MODE_LABEL: Record<VoiceMode, string> = { wake: "Hey Cypher", auto: "Tippen", ptt: "Funk" };
const MODE_HINT: Record<VoiceMode, string> = {
  wake: "Hört dauerhaft auf das Aktivierungswort",
  auto: "Tippen, sprechen - Cypher merkt selbst, wann du fertig bist",
  ptt: "Tippen zum Starten, nochmal tippen zum Senden",
};

export function caption(state: VoiceUiState): string {
  if (state.connection === "unauthorized") return "Anmeldung abgelaufen - bitte neu koppeln.";
  if (state.connection === "forbidden") return "Diese Adresse ist im Backend nicht freigegeben (CORS_ORIGINS).";
  if (state.connection !== "open") return state.connection === "connecting" ? "Verbinde mit Cypher …" : "Verbindung getrennt - ich versuche es weiter …";
  if (state.micError) return state.micError;
  switch (state.serverState) {
    case "wake_listening":
      return state.micEnabled ? `Sag „${state.wakePhrase}“ …` : `Tippen, um „${state.wakePhrase}“ einzuschalten`;
    case "listening":
      if (state.mode === "ptt") return "Sprich - nochmal tippen zum Senden";
      return state.userSpeaking ? "Ich höre …" : "Ich höre zu …";
    case "thinking":
      return state.tool?.running ? state.tool.summary || "Einen Moment …" : "Denke nach …";
    case "speaking":
      return "Tippen zum Unterbrechen";
    case "confirming":
      return "Sag „ja“ oder „nein“ - oder tippe unten.";
    default:
      return "Tippen zum Sprechen";
  }
}

function bubbleLabel(state: VoiceUiState): string {
  switch (state.serverState) {
    case "thinking":
    case "speaking":
      return "Cypher unterbrechen";
    case "listening":
      return state.mode === "ptt" ? "Aufnahme senden" : "Fertig gesprochen";
    case "confirming":
      return "Antworte mit ja oder nein";
    default:
      return state.micEnabled ? "Sprechen" : "Mikrofon einschalten und sprechen";
  }
}

function ApprovalPanel({ card, onDecide }: { card: ApprovalCard; onDecide: (approved: boolean) => void }) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, []);
  const seconds = Math.max(0, Math.round((card.expiresAt - now) / 1000));
  return (
    <section className={`voice-view__confirm voice-view__confirm--${card.risk}`} role="alertdialog" aria-labelledby="approval-title" aria-describedby="approval-summary">
      <p id="approval-title" className="voice-view__confirm-risk">
        {RISK_LABEL[card.risk] ?? "Braucht deine Bestätigung"}
      </p>
      <p id="approval-summary" className="voice-view__confirm-summary">
        {card.summary || card.toolName}
      </p>
      <div className="voice-view__confirm-actions">
        <button type="button" autoFocus className="voice-view__confirm-btn voice-view__confirm-btn--yes" onClick={() => onDecide(true)}>
          Ja <kbd>J</kbd>
        </button>
        <button type="button" className="voice-view__confirm-btn voice-view__confirm-btn--no" onClick={() => onDecide(false)}>
          Nein <kbd>N</kbd>
        </button>
      </div>
      <p className="voice-view__confirm-expiry">{seconds > 0 ? `Läuft in ${seconds} s ab` : "Abgelaufen"}</p>
    </section>
  );
}

/** Subtitles are read, not rendered: drop Markdown markers the model may
 * use ("**fett**", "* Punkt", "## Titel"). Speech gets the same cleanup on
 * the server (app/voice/speech_text.py). */
export function plainText(text: string): string {
  return text
    .replace(/\*\*|__/g, "")
    .replace(/(^|\s)[*•]\s+/g, "$1")
    .replace(/^\s*-\s+/gm, "")
    .replace(/(^|\s)#{1,6}\s+/g, "$1")
    .replace(/`/g, "");
}

function isTyping(target: EventTarget | null): boolean {
  return target instanceof HTMLElement && (target.isContentEditable || ["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName));
}

/**
 * Full-screen, bubble-centric main view. The voice session runs over a
 * WebSocket (see hooks/useVoiceSession.ts); this view only renders its
 * state: the bubble, live subtitles, the approval card and a mode switch.
 *
 * Keyboard: Space = bubble, Esc = interrupt, J / N = answer an approval.
 */
export function CypherVoiceView({ status, onOpenMenu }: Props) {
  const voice = useVoiceSession();
  const { state } = voice;
  const [provider, setProvider] = useState<LlmProviderName | null>(null);
  const [text, setText] = useState("");

  useEffect(() => {
    getActiveProvider()
      .then((info) => setProvider(info.provider))
      .catch(() => {});
  }, []);

  const { interrupt, approve, tap } = voice;
  const busy = state.serverState === "thinking" || state.serverState === "speaking";
  const hasApproval = state.approval !== null;
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (isTyping(event.target) || event.repeat) return;
      if (event.key === "Escape" && busy) {
        interrupt();
      } else if (hasApproval && (event.key === "j" || event.key === "n")) {
        approve(event.key === "j");
      } else if (event.key === " " && !(event.target instanceof HTMLButtonElement)) {
        event.preventDefault();
        void tap();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [busy, hasApproval, interrupt, approve, tap]);

  const submit = (event: FormEvent) => {
    event.preventDefault();
    voice.sendText(text);
    setText("");
  };

  const look = bubbleState(state);
  const offline = state.connection !== "open";
  const firstAudible = state.lastMetrics?.first_audible ?? state.lastMetrics?.first_audio_sent;

  return (
    <div className="voice-view">
      {provider && (
        <span
          className={`voice-view__provider-badge voice-view__provider-badge--${provider}`}
          title={provider === "cloud" ? "Cloud-Anbindung aktiv" : "Lokales Modell aktiv"}
        >
          {provider === "cloud" ? "☁ Cloud" : "🖥 Lokal"}
        </span>
      )}

      <button type="button" className="voice-view__menu-btn" onClick={onOpenMenu} title="Menü öffnen" aria-label="Menü öffnen">
        <svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true">
          <path d="M4 6h16M4 12h16M4 18h16" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" />
        </svg>
      </button>

      <div className="voice-view__stage">
        <CypherBubble state={look} getLevel={voice.getLevel} onClick={() => void voice.tap()} label={bubbleLabel(state)} disabled={offline} />
        <p className={`voice-view__caption ${state.micError || offline ? "is-error" : ""}`} aria-live="polite">
          {caption(state)}
        </p>

        <div className="voice-view__subtitles" aria-live="polite">
          {state.transcript && <p className="voice-view__you">„{state.transcript}“</p>}
          {state.reply && <p className="voice-view__reply">{plainText(state.reply)}</p>}
          {state.tool && !state.tool.running && state.tool.success === false && (
            <p className="voice-view__tool is-error">{state.tool.summary} - hat nicht geklappt</p>
          )}
        </div>

        {state.approval && <ApprovalPanel card={state.approval} onDecide={voice.approve} />}

        {(state.error || state.notice) && (
          <div className={`voice-view__alert ${state.error ? "is-error" : ""}`} role={state.error ? "alert" : "status"}>
            <p>{state.error ? state.error.message : state.notice}</p>
            {state.error?.hint && <p className="voice-view__alert-hint">{state.error.hint}</p>}
            <button type="button" onClick={voice.dismissError} aria-label="Hinweis schliessen">
              ×
            </button>
          </div>
        )}
      </div>

      <div className="voice-view__controls">
        <div className="voice-view__modes" role="radiogroup" aria-label="Sprachmodus">
          {(["wake", "auto", "ptt"] as VoiceMode[]).map((mode) => (
            <button
              key={mode}
              type="button"
              role="radio"
              aria-checked={state.mode === mode}
              className={`voice-view__mode ${state.mode === mode ? "is-active" : ""}`}
              disabled={offline || (mode === "wake" && !state.wakeAvailable)}
              title={MODE_HINT[mode]}
              onClick={() => voice.setMode(mode)}
            >
              {MODE_LABEL[mode]}
            </button>
          ))}
        </div>
        <button
          type="button"
          className="voice-view__mode"
          onClick={voice.newConversation}
          disabled={offline}
          title="Den bisherigen Gesprächsverlauf beiseitelegen - das Gedächtnis bleibt"
        >
          Neues Gespräch
        </button>
        <form className="voice-view__type" onSubmit={submit}>
          <input
            type="text"
            value={text}
            onChange={(e) => setText(e.target.value)}
            placeholder="… oder tippen"
            aria-label="Befehl tippen"
            maxLength={4000}
            disabled={offline}
          />
        </form>
      </div>

      <footer className="voice-view__statusbar">
        <span className={`voice-view__status-dot ${!offline && status?.pc_online !== false ? "is-online" : "is-offline"}`} aria-hidden="true" />
        <span className="voice-view__status-text">{offline ? "Getrennt" : state.micEnabled ? "Verbunden · Mikrofon an" : "Verbunden"}</span>
        {typeof firstAudible === "number" && (
          <span className="voice-view__last-task">· Antwort nach {firstAudible.toFixed(1).replace(".", ",")} s</span>
        )}
      </footer>
    </div>
  );
}
