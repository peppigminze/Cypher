export type VoiceOrbState = "idle" | "listening" | "speaking" | "unsupported";

interface Props {
  state: VoiceOrbState;
  onClick: () => void;
  size?: number;
  title?: string;
}

/**
 * The futuristic HUD centerpiece: a ringed, glowing orb that idles with
 * a slow pulse, spins up and brightens while listening (voice input),
 * and shifts to a second hue while Cypher speaks (voice output) - so
 * "who's talking" is readable at a glance, not just from a label.
 */
export function VoiceOrb({ state, onClick, size = 56, title }: Props) {
  const disabled = state === "unsupported";
  return (
    <button
      type="button"
      className={`voice-orb voice-orb--${state}`}
      style={{ width: size, height: size }}
      onClick={onClick}
      disabled={disabled}
      aria-pressed={state === "listening"}
      title={title}
    >
      <svg className="voice-orb__rings" viewBox="0 0 100 100" aria-hidden="true">
        <circle className="voice-orb__ring voice-orb__ring--outer" cx="50" cy="50" r="45" />
        <circle className="voice-orb__ring voice-orb__ring--mid" cx="50" cy="50" r="34" />
        <circle className="voice-orb__core" cx="50" cy="50" r="21" />
      </svg>
      <svg className="voice-orb__icon" viewBox="0 0 24 24" aria-hidden="true">
        <path
          d="M12 15a3 3 0 0 0 3-3V6a3 3 0 0 0-6 0v6a3 3 0 0 0 3 3Z"
          fill="currentColor"
        />
        <path
          d="M19 11a7 7 0 0 1-14 0M12 18v3"
          stroke="currentColor"
          strokeWidth="1.6"
          strokeLinecap="round"
          fill="none"
        />
      </svg>
    </button>
  );
}
