import { useEffect, useState, type FormEvent } from "react";
import { ApprovalCard } from "./ApprovalCard";
import { CoreVisualizer } from "./CoreVisualizer";
import { HealthBanner } from "./HealthBanner";
import { HudBar } from "./HudBar";
import { useCriticalConfirm } from "../hooks/useCriticalConfirm";
import { useHealth } from "../hooks/useHealth";
import { useVoiceSession } from "../hooks/useVoiceSession";
import { bubbleState, type VoiceMode } from "../hooks/voiceReducer";
import { bubbleLabel, caption } from "../hooks/voiceText";
import { getActiveProvider } from "../services/api";
import type { LlmProviderName } from "../types";
import { plainText } from "../utils/plainText";

interface Props {
  onOpenMenu: () => void;
  /** The menu is open: keyboard shortcuts belong to it then. */
  menuOpen: boolean;
}

const MODE_LABEL: Record<VoiceMode, string> = { wake: "Hey Cypher", auto: "Tippen", ptt: "Funk" };
const MODE_HINT: Record<VoiceMode, string> = {
  wake: "Hört dauerhaft auf das Aktivierungswort",
  auto: "Tippen, sprechen - Cypher merkt selbst, wann du fertig bist",
  ptt: "Tippen zum Starten, nochmal tippen zum Senden",
};

function isTyping(target: EventTarget | null): boolean {
  return target instanceof HTMLElement && (target.isContentEditable || ["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName));
}

/**
 * The main screen: Cypher's core in the middle, live subtitles under it,
 * telemetry on top, modes and a text field at the bottom. The voice session
 * runs over a WebSocket (hooks/useVoiceSession.ts); what every input does
 * per state is the table in hooks/voiceMachine.ts.
 *
 * Keyboard: Space = core, Esc = interrupt / "nein", J / N = approval, M = menu.
 */
export function CypherVoiceView({ onOpenMenu, menuOpen }: Props) {
  const voice = useVoiceSession();
  const health = useHealth();
  const [safety] = useCriticalConfirm();
  const { state, input } = voice;
  const [provider, setProvider] = useState<LlmProviderName | null>(null);
  const [text, setText] = useState("");

  useEffect(() => {
    getActiveProvider()
      .then((info) => setProvider(info.provider))
      .catch(() => {});
  }, [menuOpen]);

  useEffect(() => {
    if (menuOpen) return;
    const onKey = (event: KeyboardEvent) => {
      if (isTyping(event.target) || event.repeat || event.ctrlKey || event.metaKey || event.altKey) return;
      const key = event.key.toLowerCase();
      if (key === "m") {
        event.preventDefault();
        onOpenMenu();
      } else if (key === "escape") {
        void input("ESCAPE");
      } else if (key === "j") {
        void input("YES");
      } else if (key === "n") {
        void input("NO");
      } else if (key === " " && !(event.target instanceof HTMLButtonElement)) {
        event.preventDefault();
        void input("TAP");
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [input, menuOpen, onOpenMenu]);

  const submit = (event: FormEvent) => {
    event.preventDefault();
    voice.sendText(text);
    setText("");
  };

  const look = bubbleState(state);
  const offline = state.connection !== "open";

  return (
    <div className={`voice-view voice-view--${look}`}>
      <HudBar state={state} provider={provider} onOpenMenu={onOpenMenu} safety={safety} />
      <HealthBanner health={health} />

      <main className="voice-view__stage">
        <CoreVisualizer
          state={look}
          getSpectrum={voice.getSpectrum}
          onClick={() => void voice.tap()}
          label={bubbleLabel(state)}
          disabled={offline}
          countdown={look === "followup" ? state.followup : null}
        />
        <p className={`voice-view__caption ${state.micError || offline ? "is-error" : ""}`} aria-live="polite">
          {caption(state)}
        </p>

        <div className="voice-view__subtitles" aria-live="polite">
          {state.transcript && <p className="voice-view__you">„{state.transcript}“</p>}
          {state.reply && <p className="voice-view__reply">{plainText(state.reply)}</p>}
          {state.tool?.running && <p className="voice-view__tool">{state.tool.summary || "Einen Moment …"}</p>}
          {state.tool && !state.tool.running && state.tool.success === false && (
            <p className="voice-view__tool is-error">{state.tool.summary} - hat nicht geklappt</p>
          )}
        </div>

        {state.approval && <ApprovalCard card={state.approval} onDecide={voice.approve} showKeys />}

        {(state.error || state.notice) && (
          <div className={`voice-view__alert ${state.error ? "is-error" : ""}`} role={state.error ? "alert" : "status"}>
            <p>{state.error ? state.error.message : state.notice}</p>
            {state.error?.hint && <p className="voice-view__alert-hint">{state.error.hint}</p>}
            <button type="button" onClick={voice.dismissError} aria-label="Hinweis schliessen">
              ×
            </button>
          </div>
        )}
      </main>

      <footer className="voice-view__controls">
        <div className="segmented" role="radiogroup" aria-label="Sprachmodus">
          {(["wake", "auto", "ptt"] as VoiceMode[]).map((mode) => (
            <button
              key={mode}
              type="button"
              role="radio"
              aria-checked={state.mode === mode}
              className={state.mode === mode ? "is-active" : ""}
              disabled={offline || (mode === "wake" && !state.wakeAvailable)}
              title={MODE_HINT[mode]}
              onClick={() => voice.setMode(mode)}
            >
              {MODE_LABEL[mode]}
            </button>
          ))}
        </div>
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
        <button
          type="button"
          className="voice-view__new"
          onClick={voice.newConversation}
          disabled={offline}
          title="Den bisherigen Gesprächsverlauf beiseitelegen - das Gedächtnis bleibt"
        >
          Neues Gespräch
        </button>
      </footer>
    </div>
  );
}
