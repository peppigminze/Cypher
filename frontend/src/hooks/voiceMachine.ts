/**
 * The voice view as an explicit finite state machine (M6).
 *
 * The SERVER owns the conversation state (app/voice/session.py) and tells
 * the client about every change; this module writes down, in one place,
 *   1. which state changes the server is expected to make (SERVER_FLOW), and
 *   2. what each user input does in each state (INPUTS) - the bubble tap,
 *      Space, Escape and the J/N keys.
 * No XState dependency: a typed transition table does the same job here, and
 * tests check that every state answers every input (no "dead" button).
 *
 *            tap                 speech end             first audio
 *   idle ─────────▶ listening ─────────────▶ thinking ─────────────▶ speaking
 *    ▲  wake word      │ ▲                     │  approval needed       │
 *    │  (wake mode)    │ │ tap = interrupt     ▼                        │
 *   wake_listening ◀───┘ └──────────────── confirming ◀── ja/nein ─────┘
 *                         playback done / timeout → resting state
 */
import type { ServerVoiceState, VoiceMode } from "./voiceReducer";

export type UiInput = "TAP" | "ESCAPE" | "YES" | "NO";

/** What the client does in response - one command per (state, input). */
export type UiCommand =
  | "start" // ask the server to listen (enables the mic first if needed)
  | "stop" // "I'm done talking" / push-to-talk release
  | "interrupt" // barge-in: silence Cypher, cancel the turn
  | "approve"
  | "reject"
  | "none";

export const SERVER_FLOW: Record<ServerVoiceState, ServerVoiceState[]> = {
  idle: ["listening", "thinking", "wake_listening"],
  wake_listening: ["listening", "thinking", "idle", "speaking"],
  listening: ["thinking", "idle", "wake_listening", "listening"],
  thinking: ["speaking", "confirming", "idle", "wake_listening", "listening"],
  speaking: ["idle", "wake_listening", "listening", "confirming", "thinking"],
  confirming: ["thinking", "idle", "wake_listening", "listening", "speaking"],
};

const BUSY: Record<UiInput, UiCommand> = { TAP: "interrupt", ESCAPE: "interrupt", YES: "none", NO: "none" };

export const INPUTS: Record<ServerVoiceState, Record<UiInput, UiCommand>> = {
  idle: { TAP: "start", ESCAPE: "none", YES: "none", NO: "none" },
  wake_listening: { TAP: "start", ESCAPE: "none", YES: "none", NO: "none" },
  listening: { TAP: "stop", ESCAPE: "stop", YES: "none", NO: "none" },
  thinking: BUSY,
  speaking: BUSY,
  // Answer by voice ("ja"/"nein"), with the card's buttons or J/N - a tap
  // on the bubble does nothing so an accidental touch can't decide.
  confirming: { TAP: "none", ESCAPE: "reject", YES: "approve", NO: "reject" },
};

export function commandFor(state: ServerVoiceState, input: UiInput): UiCommand {
  return INPUTS[state][input];
}

/** Server transitions outside SERVER_FLOW point to a protocol bug; the
 * client still follows the server (it is the source of truth) but says so. */
export function isExpectedTransition(from: ServerVoiceState, to: ServerVoiceState): boolean {
  return from === to || SERVER_FLOW[from].includes(to);
}

/**
 * Follow-up window (FIX2): a SUB-state of "listening", not a new main state.
 * After an answer has finished playing, the server keeps listening for a few
 * seconds without the wake word. The timer runs on the server; the client
 * only mirrors it.
 *
 *          server opens (answer played)          user starts talking
 *   off ───────────────────────────────▶ open ───────────────────────▶ paused
 *    ▲                                    │ ▲                             │
 *    │   command / expiry / sleep /       │ └──── noise or echo, time ────┘
 *    └── tap / any other state / offline ─┘       left: server reopens
 */
export type FollowupPhase = "off" | "open" | "paused";
export type FollowupEvent = "open" | "speech" | "close";

export const FOLLOWUP_FLOW: Record<FollowupPhase, Record<FollowupEvent, FollowupPhase>> = {
  off: { open: "open", speech: "off", close: "off" },
  open: { open: "open", speech: "paused", close: "off" },
  paused: { open: "open", speech: "paused", close: "off" },
};

export function followupNext(phase: FollowupPhase, event: FollowupEvent): FollowupPhase {
  return FOLLOWUP_FLOW[phase][event];
}

/** Share of the window still left (1 = just opened, 0 = over). */
export function followupLeft(until: number, total: number, now: number): number {
  if (total <= 0) return 0;
  return Math.min(1, Math.max(0, (until - now) / total));
}

/** Audio leaves the browser only when the server needs it (privacy). */
export function micStreams(state: ServerVoiceState, mode: VoiceMode, pttHeld: boolean): boolean {
  if (mode === "wake") return true;
  if (state === "confirming") return true;
  if (mode === "ptt") return pttHeld;
  return state === "listening";
}
