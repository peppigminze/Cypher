import { useEffect, useRef } from "react";
import type { BubbleState } from "../hooks/voiceReducer";

interface Props {
  state: BubbleState;
  /** Fills the bins with the live spectrum and returns the loudness (0..1). */
  getSpectrum: (bins: Uint8Array<ArrayBuffer>) => number;
  onClick: () => void;
  label: string;
  disabled?: boolean;
}

interface Palette {
  core: string;
  glow: string;
  ring: string;
  motion: number; // ring rotation speed
}

const PALETTE: Record<BubbleState, Palette> = {
  offline: { core: "#3a4466", glow: "rgba(87,96,137,0.18)", ring: "#576089", motion: 0 },
  idle: { core: "#1d6f86", glow: "rgba(56,224,255,0.16)", ring: "#38e0ff", motion: 0.05 },
  wake: { core: "#1c8a7a", glow: "rgba(46,230,197,0.2)", ring: "#2ee6c5", motion: 0.07 },
  listening: { core: "#38e0ff", glow: "rgba(56,224,255,0.38)", ring: "#7cf0ff", motion: 0.12 },
  thinking: { core: "#6a4dff", glow: "rgba(124,92,255,0.34)", ring: "#a58bff", motion: 0.45 },
  speaking: { core: "#5ff2ff", glow: "rgba(111,243,255,0.42)", ring: "#c8fbff", motion: 0.1 },
  confirming: { core: "#ffb454", glow: "rgba(255,180,84,0.32)", ring: "#ffd08a", motion: 0.08 },
};

const TICKS = 96;
const BINS = 64;

/**
 * The reactive core of the voice view - drawn on a canvas at the display's
 * refresh rate (60 fps and more). One draw call per frame, no React state
 * per frame, so the rest of the page doesn't re-render.
 *
 *   outer HUD ring   96 ticks; their length follows the live spectrum
 *                    (mic while listening, Cypher's voice while speaking)
 *   orbit arcs       spin while Cypher is thinking
 *   core             breathes with the loudness, colour = state
 *
 * prefers-reduced-motion: one still frame per state change, no animation.
 * The canvas is decoration (aria-hidden); the button carries the label.
 */
export function CoreVisualizer({ state, getSpectrum, onClick, label, disabled }: Props) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);

  useEffect(() => {
    const canvas = canvasRef.current;
    const ctx = canvas?.getContext("2d");
    if (!canvas || !ctx) return;
    const palette = PALETTE[state];
    const reduced = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches ?? false;
    const bins = new Uint8Array(BINS);
    const smooth = new Float32Array(TICKS);
    let level = 0;
    let rafId = 0;
    const started = performance.now();

    const resize = () => {
      const dpr = Math.min(window.devicePixelRatio || 1, 2);
      const { width, height } = canvas.getBoundingClientRect();
      canvas.width = Math.max(1, Math.round(width * dpr));
      canvas.height = Math.max(1, Math.round(height * dpr));
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    };
    resize();
    const observer = typeof ResizeObserver !== "undefined" ? new ResizeObserver(resize) : null;
    observer?.observe(canvas);

    const draw = (now: number) => {
      const t = (now - started) / 1000;
      const { width, height } = canvas.getBoundingClientRect();
      const cx = width / 2;
      const cy = height / 2;
      const r = Math.min(width, height) * 0.27;
      const live = state === "listening" || state === "speaking" || state === "wake" || state === "confirming";
      const loudness = live && !reduced ? getSpectrum(bins) : 0;
      level = level * 0.8 + loudness * 0.2;
      ctx.clearRect(0, 0, width, height);

      // Glow behind everything.
      const glow = ctx.createRadialGradient(cx, cy, r * 0.2, cx, cy, r * 1.9);
      glow.addColorStop(0, palette.glow);
      glow.addColorStop(1, "rgba(0,0,0,0)");
      ctx.fillStyle = glow;
      ctx.beginPath();
      ctx.arc(cx, cy, r * 1.9, 0, Math.PI * 2);
      ctx.fill();

      // Outer HUD ring: ticks driven by the spectrum (mirrored around the circle).
      const spin = reduced ? 0 : t * palette.motion;
      ctx.strokeStyle = palette.ring;
      ctx.lineCap = "round";
      for (let i = 0; i < TICKS; i++) {
        const bin = bins[Math.floor(((i < TICKS / 2 ? i : TICKS - 1 - i) / (TICKS / 2)) * (BINS * 0.7))] / 255;
        smooth[i] = smooth[i] * 0.7 + bin * 0.3;
        const angle = (i / TICKS) * Math.PI * 2 + spin;
        const inner = r * 1.34;
        const length = 3 + smooth[i] * r * 0.42 + (i % 8 === 0 ? 4 : 0);
        ctx.globalAlpha = state === "offline" ? 0.25 : 0.35 + smooth[i] * 0.65;
        ctx.lineWidth = i % 8 === 0 ? 2 : 1.2;
        ctx.beginPath();
        ctx.moveTo(cx + Math.cos(angle) * inner, cy + Math.sin(angle) * inner);
        ctx.lineTo(cx + Math.cos(angle) * (inner + length), cy + Math.sin(angle) * (inner + length));
        ctx.stroke();
      }

      // Thin rings, counter-rotating dashes.
      ctx.globalAlpha = state === "offline" ? 0.2 : 0.45;
      ctx.lineWidth = 1;
      ctx.setLineDash([2, 7]);
      ctx.beginPath();
      ctx.arc(cx, cy, r * 1.22, -spin * 1.6, Math.PI * 2 - spin * 1.6);
      ctx.stroke();
      ctx.setLineDash([]);

      // Thinking: orbiting arcs.
      if (state === "thinking") {
        ctx.lineWidth = 2.5;
        for (let k = 0; k < 3; k++) {
          const a = (reduced ? 0 : t * (2.2 + k * 0.6)) + (k * Math.PI * 2) / 3;
          ctx.globalAlpha = 0.9 - k * 0.2;
          ctx.beginPath();
          ctx.arc(cx, cy, r * (1.08 + k * 0.05), a, a + 0.9);
          ctx.stroke();
        }
      }

      // Confirming: slow amber pulse ring.
      if (state === "confirming") {
        const pulse = reduced ? 0.5 : (Math.sin(t * 2.4) + 1) / 2;
        ctx.globalAlpha = 0.25 + pulse * 0.5;
        ctx.lineWidth = 2;
        ctx.beginPath();
        ctx.arc(cx, cy, r * (1.1 + pulse * 0.06), 0, Math.PI * 2);
        ctx.stroke();
      }

      // The core - an energy cell: dark shell, glowing centre that swells
      // with the voice, a segmented inner ring turning against the outer one.
      const breathe = reduced || state === "offline" ? 0 : Math.sin(t * 1.3) * 0.025;
      const radius = r * (0.82 + breathe + level * 0.22);
      ctx.globalAlpha = state === "offline" ? 0.6 : 1;
      const shell = ctx.createRadialGradient(cx, cy, radius * 0.2, cx, cy, radius);
      shell.addColorStop(0, "rgba(10,18,36,0.4)");
      shell.addColorStop(1, "rgba(4,8,18,0.95)");
      ctx.fillStyle = shell;
      ctx.beginPath();
      ctx.arc(cx, cy, radius, 0, Math.PI * 2);
      ctx.fill();
      ctx.strokeStyle = palette.ring;
      ctx.lineWidth = 1.5;
      ctx.globalAlpha *= 0.7;
      ctx.stroke();

      const heart = radius * (0.34 + level * 0.3);
      const light = ctx.createRadialGradient(cx, cy, 0, cx, cy, heart * 1.9);
      light.addColorStop(0, state === "offline" ? "rgba(160,170,200,0.5)" : "rgba(255,255,255,0.95)");
      light.addColorStop(0.28, palette.core);
      light.addColorStop(1, "rgba(0,0,0,0)");
      ctx.globalAlpha = state === "offline" ? 0.45 : 0.95;
      ctx.fillStyle = light;
      ctx.beginPath();
      ctx.arc(cx, cy, heart * 1.9, 0, Math.PI * 2);
      ctx.fill();

      const segments = 12;
      const ringR = radius * 0.66;
      ctx.lineWidth = Math.max(2, radius * 0.06);
      ctx.lineCap = "butt";
      for (let k = 0; k < segments; k++) {
        const a = (k / segments) * Math.PI * 2 - spin * 2.2;
        ctx.globalAlpha = (state === "offline" ? 0.25 : 0.55) + (k % 3 === 0 ? 0.25 : 0);
        ctx.beginPath();
        ctx.arc(cx, cy, ringR, a, a + (Math.PI * 2) / segments - 0.12);
        ctx.stroke();
      }
      ctx.lineCap = "round";
      ctx.globalAlpha = 1;

      if (!reduced && state !== "offline") rafId = requestAnimationFrame(draw);
    };
    rafId = requestAnimationFrame(draw);
    return () => {
      cancelAnimationFrame(rafId);
      observer?.disconnect();
    };
  }, [state, getSpectrum]);

  return (
    <button type="button" className={`core core--${state}`} onClick={onClick} disabled={disabled} aria-label={label} title={label}>
      <canvas ref={canvasRef} className="core__canvas" aria-hidden="true" />
    </button>
  );
}
