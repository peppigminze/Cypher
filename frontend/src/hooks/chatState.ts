/**
 * State of the text chat as a pure reducer (testable without a browser):
 * the stream events of /api/chat are applied to the last assistant turn.
 */
import type { ChatEvent } from "../services/chatStream";
import type { ApprovalCard } from "./voiceReducer";

export interface ToolChip {
  name: string;
  summary: string;
  running: boolean;
  success?: boolean;
}

export interface ChatTurn {
  id: number;
  role: "user" | "assistant";
  text: string;
  tools: ToolChip[];
  approval: ApprovalCard | null;
  streaming: boolean;
  error: { message: string; hint?: string } | null;
}

export interface ChatState {
  conversationId: string | null;
  turns: ChatTurn[];
  busy: boolean;
  nextId: number;
}

export const initialChatState: ChatState = { conversationId: null, turns: [], busy: false, nextId: 1 };

export type ChatAction =
  | { type: "load"; conversationId: string; history: { role: "user" | "assistant"; text: string }[] }
  | { type: "send"; text: string }
  | { type: "resume" }
  | { type: "event"; event: ChatEvent; now: number }
  | { type: "failed"; message: string }
  | { type: "finished" }
  | { type: "reset" };

function turn(id: number, role: ChatTurn["role"], text = "", streaming = false): ChatTurn {
  return { id, role, text, tools: [], approval: null, streaming, error: null };
}

function updateLast(state: ChatState, fn: (t: ChatTurn) => ChatTurn): ChatState {
  const turns = [...state.turns];
  const i = turns.length - 1;
  if (i < 0 || turns[i].role !== "assistant") return state;
  turns[i] = fn(turns[i]);
  return { ...state, turns };
}

function str(v: unknown): string {
  return typeof v === "string" ? v : "";
}

export function chatReducer(state: ChatState, action: ChatAction): ChatState {
  switch (action.type) {
    case "load": {
      let id = state.nextId;
      const turns = action.history.map((h) => turn(id++, h.role, h.text));
      return { ...state, conversationId: action.conversationId, turns, nextId: id };
    }
    case "send":
      return {
        ...state,
        busy: true,
        turns: [...state.turns, turn(state.nextId, "user", action.text), turn(state.nextId + 1, "assistant", "", true)],
        nextId: state.nextId + 2,
      };
    case "resume":
      // An approval was answered: the same assistant turn continues.
      return updateLast({ ...state, busy: true }, (t) => ({ ...t, approval: null, streaming: true }));
    case "event": {
      const e = action.event;
      switch (e.type) {
        case "conversation":
          return str(e.conversation_id) ? { ...state, conversationId: str(e.conversation_id) } : state;
        case "reply_delta":
          return updateLast(state, (t) => ({ ...t, text: t.text + str(e.text) }));
        case "reply":
          return updateLast(state, (t) => ({ ...t, text: str(e.text) || t.text, streaming: false }));
        case "tool":
          return updateLast(state, (t) => {
            const chip: ToolChip = {
              name: str(e.name),
              summary: str(e.summary),
              running: e.phase === "start",
              success: e.phase === "end" ? e.success === true : undefined,
            };
            const tools =
              e.phase === "end" && t.tools.length && t.tools[t.tools.length - 1].running && t.tools[t.tools.length - 1].name === chip.name
                ? [...t.tools.slice(0, -1), chip]
                : [...t.tools, chip];
            return { ...t, tools };
          });
        case "approval": {
          const card = (e.card ?? {}) as Record<string, unknown>;
          return updateLast(state, (t) => ({
            ...t,
            streaming: false,
            approval: {
              approvalId: str(card.approval_id),
              toolName: str(card.tool_name),
              summary: str(card.summary),
              risk: str(card.risk) || "write",
              arguments: (card.arguments as Record<string, unknown>) ?? {},
              expiresAt: action.now + (typeof card.expires_in_seconds === "number" ? card.expires_in_seconds : 300) * 1000,
            },
          }));
        }
        case "error":
          return updateLast(state, (t) => ({
            ...t,
            streaming: false,
            error: { message: str(e.message) || "Etwas ist schiefgelaufen.", hint: str(e.hint) || undefined },
          }));
        default:
          return state;
      }
    }
    case "failed":
      return updateLast({ ...state, busy: false }, (t) => ({ ...t, streaming: false, error: { message: action.message } }));
    case "finished":
      return updateLast({ ...state, busy: false }, (t) => ({ ...t, streaming: false }));
    case "reset":
      return { ...initialChatState, nextId: state.nextId };
    default:
      return state;
  }
}
