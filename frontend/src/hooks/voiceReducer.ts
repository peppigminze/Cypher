/**
 * UI state of the voice session, as a pure reducer: every server message
 * and every local event (mic on/off, connection changes) is an action, so
 * the whole state machine is testable without a browser
 * (see __tests__/voiceReducer.test.ts).
 *
 * The server (app/voice/session.py) is the source of truth for the
 * conversation state (idle / wake_listening / listening / thinking /
 * speaking / confirming); the client only adds what it alone knows:
 * connection, microphone, live subtitles.
 */
import type { ConnectionStatus, ServerMessage } from "../services/voiceClient";
import { followupNext, micStreams, type FollowupPhase } from "./voiceMachine";

export type ServerVoiceState = "idle" | "wake_listening" | "listening" | "thinking" | "speaking" | "confirming";
export type VoiceMode = "wake" | "auto" | "ptt";

export interface ApprovalCard {
  approvalId: string;
  toolName: string;
  summary: string;
  risk: string;
  arguments: Record<string, unknown>;
  expiresAt: number;
}

export interface VoiceError {
  code?: string;
  message: string;
  hint?: string;
}

export interface ToolActivity {
  name: string;
  summary: string;
  running: boolean;
  success?: boolean;
}

/** The follow-up window as the server announced it (times in client ms). */
export interface Followup {
  phase: FollowupPhase;
  until: number;
  total: number;
}

const NO_FOLLOWUP: Followup = { phase: "off", until: 0, total: 0 };

export interface VoiceUiState {
  connection: ConnectionStatus;
  serverState: ServerVoiceState;
  mode: VoiceMode;
  wakeAvailable: boolean;
  wakePhrase: string;
  micEnabled: boolean;
  micError: string | null;
  userSpeaking: boolean;
  transcript: string;
  reply: string;
  tool: ToolActivity | null;
  approval: ApprovalCard | null;
  error: VoiceError | null;
  notice: string | null;
  turnId: number;
  // Text/tool events of turns up to this id are ignored: they were
  // interrupted, but a few deltas may still be in flight.
  ignoredTurn: number;
  lastMetrics: Record<string, number> | null;
  followup: Followup;
}

export const initialVoiceState: VoiceUiState = {
  connection: "connecting",
  serverState: "idle",
  mode: "auto",
  wakeAvailable: false,
  wakePhrase: "Hey Cypher",
  micEnabled: false,
  micError: null,
  userSpeaking: false,
  transcript: "",
  reply: "",
  tool: null,
  approval: null,
  error: null,
  notice: null,
  turnId: 0,
  ignoredTurn: -1,
  lastMetrics: null,
  followup: NO_FOLLOWUP,
};

export type VoiceAction =
  | { type: "connection"; status: ConnectionStatus }
  | { type: "server"; message: ServerMessage; now: number }
  | { type: "mic"; enabled: boolean; error?: string | null }
  | { type: "local_interrupt" }
  | { type: "dismiss_error" };

const STATES: ServerVoiceState[] = ["idle", "wake_listening", "listening", "thinking", "speaking", "confirming"];
const MODES: VoiceMode[] = ["wake", "auto", "ptt"];

function str(value: unknown, fallback = ""): string {
  return typeof value === "string" ? value : fallback;
}

function num(value: unknown, fallback = 0): number {
  return typeof value === "number" && Number.isFinite(value) ? value : fallback;
}

const TURN_CONTENT = new Set(["reply_delta", "reply", "tool", "approval"]);

function serverReducer(state: VoiceUiState, msg: ServerMessage, now: number): VoiceUiState {
  if (TURN_CONTENT.has(msg.type) && typeof msg.turn_id === "number" && msg.turn_id <= state.ignoredTurn) return state;
  switch (msg.type) {
    case "ready": {
      const mode = MODES.includes(msg.mode as VoiceMode) ? (msg.mode as VoiceMode) : state.mode;
      return {
        ...state,
        mode,
        wakeAvailable: msg.wake_word_enabled === true,
        wakePhrase: str(msg.wake_phrase, state.wakePhrase),
        error: null,
      };
    }
    case "state": {
      const next = STATES.includes(msg.state as ServerVoiceState) ? (msg.state as ServerVoiceState) : state.serverState;
      const listensNow = next === "listening" || next === "wake_listening" || next === "confirming";
      const seconds = typeof msg.followup_seconds === "number" ? msg.followup_seconds : null;
      const opens = next === "listening" && seconds !== null;
      const followup: Followup = opens
        ? { phase: followupNext(state.followup.phase, "open"), until: now + seconds * 1000, total: num(msg.followup_total, seconds) * 1000 }
        : { ...NO_FOLLOWUP, phase: followupNext(state.followup.phase, "close") };
      // Starting to listen for a new command clears the old subtitles - but in
      // the follow-up window the answer stays readable until you speak.
      const freshTurn = next === "listening" && state.serverState !== "listening" && !opens;
      return {
        ...state,
        serverState: next,
        followup,
        turnId: num(msg.turn_id, state.turnId),
        userSpeaking: listensNow ? state.userSpeaking : false,
        // The card survives "speaking" (Cypher reads the question aloud) and
        // only goes away when a new conversation phase starts.
        approval: next === "idle" || next === "wake_listening" || next === "listening" ? null : state.approval,
        ...(freshTurn ? { transcript: "", reply: "", tool: null, error: null } : {}),
      };
    }
    case "mode":
      return MODES.includes(msg.mode as VoiceMode) ? { ...state, mode: msg.mode as VoiceMode } : state;
    case "vad":
      return { ...state, userSpeaking: msg.speech === true };
    case "followup":
      // {"active": false}: you started talking - the window stops counting.
      return msg.active === false
        ? { ...state, followup: { ...state.followup, phase: followupNext(state.followup.phase, "speech") } }
        : state;
    case "wake":
      return { ...state, transcript: "", reply: "", tool: null, error: null };
    case "transcript":
      return { ...state, transcript: str(msg.text), reply: str(msg.text) ? "" : state.reply, tool: str(msg.text) ? null : state.tool };
    case "reply_delta":
      return { ...state, reply: state.reply + str(msg.text) };
    case "reply":
      return { ...state, reply: str(msg.text, state.reply) };
    case "tool":
      return {
        ...state,
        tool: {
          name: str(msg.name),
          summary: str(msg.summary),
          running: msg.phase === "start",
          success: msg.phase === "end" ? msg.success === true : undefined,
        },
      };
    case "approval": {
      const card = (msg.card ?? {}) as Record<string, unknown>;
      return {
        ...state,
        approval: {
          approvalId: str(card.approval_id),
          toolName: str(card.tool_name),
          summary: str(card.summary),
          risk: str(card.risk, "write"),
          arguments: (card.arguments as Record<string, unknown>) ?? {},
          expiresAt: now + num(card.expires_in_seconds, 300) * 1000,
        },
      };
    }
    case "conversation_reset":
      return { ...state, transcript: "", reply: "", tool: null, approval: null, error: null, notice: null };
    case "approval_resolved":
      return { ...state, approval: null };
    case "audio_stop":
      return { ...state, ignoredTurn: Math.max(state.ignoredTurn, num(msg.turn_id, state.turnId)) };
    case "audio_end":
      return { ...state, lastMetrics: (msg.metrics as Record<string, number>) ?? state.lastMetrics };
    case "notice":
      return { ...state, notice: str(msg.text) };
    case "error":
      return {
        ...state,
        error: {
          code: str(msg.code) || undefined,
          message: str(msg.message, "Etwas ist schiefgelaufen."),
          hint: str(msg.hint) || undefined,
        },
      };
    default:
      return state;
  }
}

export function voiceReducer(state: VoiceUiState, action: VoiceAction): VoiceUiState {
  switch (action.type) {
    case "connection":
      return {
        ...state,
        connection: action.status,
        // A dropped connection ends whatever the server was doing.
        ...(action.status !== "open" ? { serverState: "idle" as const, userSpeaking: false, approval: null, followup: NO_FOLLOWUP } : {}),
      };
    case "server":
      return serverReducer(state, action.message, action.now);
    case "mic":
      return { ...state, micEnabled: action.enabled, micError: action.error ?? null };
    case "local_interrupt":
      return {
        ...state,
        ignoredTurn: Math.max(state.ignoredTurn, state.turnId),
        serverState: state.serverState === "thinking" || state.serverState === "speaking" ? "listening" : state.serverState,
      };
    case "dismiss_error":
      return { ...state, error: null, notice: null };
    default:
      return state;
  }
}

/** Which bubble look/label the UI shows. */
export type BubbleState = "offline" | "idle" | "wake" | "listening" | "followup" | "thinking" | "speaking" | "confirming";

export function bubbleState(state: VoiceUiState): BubbleState {
  if (state.connection !== "open") return "offline";
  switch (state.serverState) {
    case "wake_listening":
      return state.micEnabled ? "wake" : "idle";
    case "listening":
      return state.followup.phase === "open" ? "followup" : "listening";
    case "thinking":
      return "thinking";
    case "speaking":
      return "speaking";
    case "confirming":
      return "confirming";
    default:
      return "idle";
  }
}

/** Should microphone audio be streamed to the server right now? Always in
 * wake mode (it listens for "Hey Cypher"), otherwise only while the server
 * is actually listening - nothing leaves the browser in between. */
export function shouldStreamAudio(state: VoiceUiState, pttHeld: boolean): boolean {
  if (!state.micEnabled || state.connection !== "open") return false;
  return micStreams(state.serverState, state.mode, pttHeld);
}
