import { useEffect, useRef } from "react";
import type { CypherVoiceState } from "../hooks/useCypherVoiceState";

interface Props {
  state: CypherVoiceState;
  micStream: MediaStream | null;
  onClick: () => void;
  disabled?: boolean;
}

const BAR_COUNT = 28;
const BAR_INDICES = Array.from({ length: BAR_COUNT }, (_, i) => i);

/**
 * The large, full-screen focal point of the voice UI (see
 * CypherVoiceView.tsx). Deliberately a new component rather than an
 * extension of VoiceOrb.tsx (the small mic button in ChatView's
 * composer) - different size, an extra "thinking" state, and a live
 * microphone waveform this component owns end to end. Keeping them
 * separate means this can't affect the existing chat composer's voice
 * button.
 *
 * The listening-state waveform bars are updated via direct DOM
 * mutation in a requestAnimationFrame loop (not React state) - at
 * ~60fps that would otherwise mean 28 state updates/re-renders per
 * frame for no visual benefit.
 */
export function CypherBubble({ state, micStream, onClick, disabled }: Props) {
  const barRefs = useRef<(HTMLDivElement | null)[]>([]);

  useEffect(() => {
    if (state !== "listening" || !micStream) return;

    const AudioCtxCtor = window.AudioContext || (window as unknown as { webkitAudioContext: typeof AudioContext }).webkitAudioContext;
    const audioCtx = new AudioCtxCtor();
    const source = audioCtx.createMediaStreamSource(micStream);
    const analyser = audioCtx.createAnalyser();
    analyser.fftSize = 64;
    analyser.smoothingTimeConstant = 0.7;
    source.connect(analyser);
    const data = new Uint8Array(analyser.frequencyBinCount);

    let rafId: number;
    const tick = () => {
      analyser.getByteFrequencyData(data);
      barRefs.current.forEach((bar, i) => {
        if (!bar) return;
        const value = data[i % data.length] / 255;
        bar.style.height = `${8 + value * 32}px`;
        bar.style.opacity = `${0.35 + value * 0.65}`;
      });
      rafId = requestAnimationFrame(tick);
    };
    tick();

    return () => {
      cancelAnimationFrame(rafId);
      source.disconnect();
      analyser.disconnect();
      void audioCtx.close();
    };
  }, [state, micStream]);

  const label =
    state === "listening"
      ? "Aufnahme stoppen"
      : state === "idle"
        ? "Sprachaufnahme starten"
        : "Cypher ist beschäftigt";

  return (
    <button
      type="button"
      className={`cypher-bubble cypher-bubble--${state}`}
      onClick={onClick}
      disabled={disabled}
      aria-pressed={state === "listening"}
      aria-label={label}
      title={label}
    >
      <div className="cypher-bubble__glow" aria-hidden="true" />

      <svg className="cypher-bubble__rings" viewBox="0 0 100 100" aria-hidden="true">
        <circle className="cypher-bubble__ring cypher-bubble__ring--outer" cx="50" cy="50" r="46" />
        <circle className="cypher-bubble__ring cypher-bubble__ring--mid" cx="50" cy="50" r="36" />
        <circle className="cypher-bubble__core" cx="50" cy="50" r="25" />
      </svg>

      {state === "listening" && (
        <div className="cypher-bubble__waveform" aria-hidden="true">
          {BAR_INDICES.map((i) => {
            const angle = (360 / BAR_COUNT) * i;
            return (
              <div
                key={i}
                ref={(el) => {
                  barRefs.current[i] = el;
                }}
                className="cypher-bubble__bar"
                style={{
                  transform: `translate(-50%, -50%) rotate(${angle}deg) translateY(calc(var(--size) / -2 - 2px))`,
                }}
              />
            );
          })}
        </div>
      )}
    </button>
  );
}
