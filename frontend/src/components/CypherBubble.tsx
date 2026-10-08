import { useEffect, useRef } from "react";
import type { BubbleState } from "../hooks/voiceReducer";

interface Props {
  state: BubbleState;
  /** 0..1 loudness, polled every animation frame (mic or Cypher's voice). */
  getLevel: () => number;
  onClick: () => void;
  label: string;
  disabled?: boolean;
}

const BAR_COUNT = 28;
const BAR_INDICES = Array.from({ length: BAR_COUNT }, (_, i) => i);
const REACTIVE: BubbleState[] = ["listening", "wake", "speaking", "confirming"];

/**
 * The full-screen focal point of the voice UI. Audio-reactive: the level
 * comes from the voice session (mic while listening, Cypher's own voice
 * while speaking) and is applied by direct DOM writes in a
 * requestAnimationFrame loop - React state at 60 fps would re-render the
 * whole view every frame for nothing.
 *
 * With prefers-reduced-motion the loop doesn't run at all; states stay
 * distinguishable by colour and label.
 */
export function CypherBubble({ state, getLevel, onClick, label, disabled }: Props) {
  const rootRef = useRef<HTMLButtonElement | null>(null);
  const barRefs = useRef<(HTMLDivElement | null)[]>([]);
  const reactive = REACTIVE.includes(state);

  useEffect(() => {
    const root = rootRef.current;
    if (!reactive || !root) return;
    if (window.matchMedia?.("(prefers-reduced-motion: reduce)").matches) return;
    let smoothed = 0;
    let rafId = 0;
    let frame = 0;
    const tick = () => {
      frame += 1;
      smoothed = smoothed * 0.7 + getLevel() * 0.3;
      root.style.setProperty("--level", smoothed.toFixed(3));
      barRefs.current.forEach((bar, i) => {
        if (!bar) return;
        // A travelling ripple around the ring, scaled by the loudness.
        const wave = 0.55 + 0.45 * Math.sin(frame / 6 + i * 0.9);
        const value = smoothed * wave;
        bar.style.height = `${6 + value * 34}px`;
        bar.style.opacity = `${0.3 + value * 0.7}`;
      });
      rafId = requestAnimationFrame(tick);
    };
    rafId = requestAnimationFrame(tick);
    return () => {
      cancelAnimationFrame(rafId);
      root.style.removeProperty("--level");
    };
  }, [reactive, getLevel]);

  return (
    <button
      ref={rootRef}
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

      {reactive && (
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
