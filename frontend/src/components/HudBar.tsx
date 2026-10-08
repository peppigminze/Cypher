import type { VoiceUiState } from "../hooks/voiceReducer";

interface Props {
  state: VoiceUiState;
  provider: "local" | "cloud" | null;
  onOpenMenu: () => void;
}

const MODE_TEXT = { wake: "HEY CYPHER", auto: "TIPPEN", ptt: "FUNK" } as const;

/** Top HUD: wordmark, live telemetry (provider, mode, mic, last latency) and the menu. */
export function HudBar({ state, provider, onOpenMenu }: Props) {
  const online = state.connection === "open";
  const latency = state.lastMetrics?.first_audible ?? state.lastMetrics?.first_audio_sent;
  return (
    <header className="hud">
      <div className="hud__brand">
        <span className={`hud__dot ${online ? "is-online" : "is-offline"}`} aria-hidden="true" />
        <span className="hud__wordmark">CYPHER</span>
      </div>
      <p className="hud__telemetry" aria-label="Status">
        <span
          className={provider === "cloud" ? "hud__item hud__item--cloud" : "hud__item"}
          title={provider === "cloud" ? "Antworten kommen aus der Cloud" : "Alles bleibt auf deinem PC"}
        >
          {provider === "cloud" ? "☁ CLOUD" : "● LOKAL"}
        </span>
        <span className="hud__item">{MODE_TEXT[state.mode]}</span>
        <span className="hud__item">{state.micEnabled ? "MIKRO AN" : "MIKRO AUS"}</span>
        {online && typeof latency === "number" && <span className="hud__item">{latency.toFixed(1).replace(".", ",")} S</span>}
        {!online && <span className="hud__item hud__item--warn">GETRENNT</span>}
      </p>
      <button type="button" className="hud__menu" onClick={onOpenMenu} aria-label="Menü öffnen (Taste M)" title="Menü (M)">
        <svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true">
          <path d="M4 7h16M4 12h16M4 17h10" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" />
        </svg>
      </button>
    </header>
  );
}
