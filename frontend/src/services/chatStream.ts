/**
 * Text chat over /api/chat (backend: app/api/chat.py) - the answer arrives
 * as Server-Sent Events while the model is still writing.
 *
 * EventSource can't send a POST body or an Authorization header, so this
 * reads the SSE format from a normal fetch() stream: lines "data: {...}",
 * events separated by a blank line.
 */
import { API_BASE_URL, AUTH_REQUIRED_EVENT, getStoredToken } from "./auth";

export interface ChatEvent {
  type: "conversation" | "reply_delta" | "tool" | "approval" | "reply" | "error";
  [key: string]: unknown;
}

export class ChatHttpError extends Error {
  status: number;
  constructor(message: string, status: number) {
    super(message);
    this.status = status;
  }
}

function headers(): HeadersInit {
  const token = getStoredToken();
  return { "Content-Type": "application/json", ...(token ? { Authorization: `Bearer ${token}` } : {}) };
}

/** Splits an SSE byte stream into parsed events (exported for tests). */
export async function* parseSse(body: ReadableStream<Uint8Array>): AsyncGenerator<ChatEvent> {
  const reader = body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  try {
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      let boundary = buffer.indexOf("\n\n");
      while (boundary >= 0) {
        const block = buffer.slice(0, boundary);
        buffer = buffer.slice(boundary + 2);
        const data = block
          .split("\n")
          .filter((line) => line.startsWith("data: "))
          .map((line) => line.slice(6))
          .join("\n");
        if (data) {
          try {
            yield JSON.parse(data) as ChatEvent;
          } catch {
            /* ignore a malformed event, keep streaming */
          }
        }
        boundary = buffer.indexOf("\n\n");
      }
    }
  } finally {
    reader.releaseLock();
  }
}

async function postStream(path: string, body: unknown, signal?: AbortSignal): Promise<AsyncGenerator<ChatEvent>> {
  const resp = await fetch(`${API_BASE_URL}${path}`, { method: "POST", headers: headers(), body: JSON.stringify(body), signal });
  if (resp.status === 401) window.dispatchEvent(new Event(AUTH_REQUIRED_EVENT));
  if (!resp.ok || !resp.body) {
    let detail = "Anfrage fehlgeschlagen";
    try {
      const json = (await resp.json()) as { detail?: string; error?: { message?: string } };
      detail = json.error?.message ?? (typeof json.detail === "string" ? json.detail : detail);
    } catch {
      /* not JSON */
    }
    throw new ChatHttpError(detail, resp.status);
  }
  return parseSse(resp.body);
}

export function streamChat(message: string, conversationId: string | null, signal?: AbortSignal) {
  return postStream("/api/chat", { message, conversation_id: conversationId }, signal);
}

export function streamApproval(approvalId: string, approved: boolean, signal?: AbortSignal) {
  return postStream("/api/chat/approval", { approval_id: approvalId, approved }, signal);
}

export interface HistoryTurn {
  role: "user" | "assistant";
  text: string;
}

export async function fetchChatHistory(conversationId: string): Promise<HistoryTurn[]> {
  const resp = await fetch(`${API_BASE_URL}/api/chat/history?conversation_id=${encodeURIComponent(conversationId)}`, { headers: headers() });
  if (!resp.ok) return [];
  const json = (await resp.json()) as { turns?: HistoryTurn[] };
  return json.turns ?? [];
}
