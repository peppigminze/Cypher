import type {
  ActiveProviderInfo,
  ChatMessage,
  LlmProviderName,
  MemoryEntry,
  MemoryType,
  PendingAction,
  Project,
  Settings,
  SystemStatus,
  Task,
  ToolChatResult,
  TranscribeResult,
  UsageSummary,
} from "../types";

import { API_BASE_URL, AUTH_REQUIRED_EVENT, getStoredToken } from "./auth";

const BASE_URL = API_BASE_URL;

const QUEUE_KEY = "jarvis.offline_queue.v1";

function bearer(): Record<string, string> {
  const token = getStoredToken();
  return token ? { Authorization: `Bearer ${token}` } : {};
}

function authHeaders(): HeadersInit {
  return { "Content-Type": "application/json", ...bearer() };
}

/** Prefer the backend's German error message ("detail") over raw status text. */
async function errorFromResponse(resp: Response, fallback: string): Promise<Error> {
  if (resp.status === 401) window.dispatchEvent(new Event(AUTH_REQUIRED_EVENT));
  const text = await resp.text().catch(() => "");
  try {
    const body = JSON.parse(text) as { detail?: string; error?: { message?: string; hint?: string } };
    const message = body.error?.message ?? (typeof body.detail === "string" ? body.detail : null);
    if (message) {
      const err = new Error(message) as Error & { hint?: string; status?: number };
      err.hint = body.error?.hint;
      err.status = resp.status;
      return err;
    }
  } catch {
    // not JSON
  }
  return new Error(`${fallback} (${resp.status}): ${text || resp.statusText}`);
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const resp = await fetch(`${BASE_URL}${path}`, {
    ...init,
    headers: { ...authHeaders(), ...(init?.headers || {}) },
  });
  if (!resp.ok) throw await errorFromResponse(resp, "Anfrage fehlgeschlagen");
  if (resp.status === 204) return undefined as T;
  return (await resp.json()) as T;
}

function newClientId(): string {
  if ("randomUUID" in crypto) return crypto.randomUUID();
  return `${Date.now()}-${Math.random().toString(36).slice(2)}`;
}

// ------------------------------------------------------------------ Offline queue
// If the browser has no network (or the backend can't be reached),
// outgoing chat messages are queued locally in localStorage with a
// stable client_id. Once we're back online we flush the queue - the
// backend's /api/messages endpoint is idempotent on client_id, so a
// retried send never creates a duplicate message (see project spec
// section 16).

interface QueuedMessage {
  client_id: string;
  content: string;
  queued_at: string;
}

function readQueue(): QueuedMessage[] {
  try {
    const raw = localStorage.getItem(QUEUE_KEY);
    return raw ? (JSON.parse(raw) as QueuedMessage[]) : [];
  } catch {
    return [];
  }
}

function writeQueue(queue: QueuedMessage[]): void {
  localStorage.setItem(QUEUE_KEY, JSON.stringify(queue));
}

function enqueue(msg: QueuedMessage): void {
  const queue = readQueue();
  queue.push(msg);
  writeQueue(queue);
}

function dequeue(clientId: string): void {
  writeQueue(readQueue().filter((m) => m.client_id !== clientId));
}

export async function flushOfflineQueue(): Promise<void> {
  const queue = readQueue();
  for (const item of queue) {
    try {
      await request<ChatMessage>("/api/messages", {
        method: "POST",
        body: JSON.stringify({ content: item.content, client_id: item.client_id }),
      });
      dequeue(item.client_id);
    } catch {
      // Still offline / backend unreachable - stop, try again later.
      break;
    }
  }
}

export function hasQueuedMessages(): boolean {
  return readQueue().length > 0;
}

// ------------------------------------------------------------------ Messages

export async function sendMessage(content: string): Promise<ChatMessage | { queued: true; client_id: string }> {
  const client_id = newClientId();
  try {
    return await request<ChatMessage>("/api/messages", {
      method: "POST",
      body: JSON.stringify({ content, client_id }),
    });
  } catch {
    enqueue({ client_id, content, queued_at: new Date().toISOString() });
    return { queued: true, client_id };
  }
}

export async function listMessages(): Promise<ChatMessage[]> {
  return request<ChatMessage[]>("/api/messages");
}

// ------------------------------------------------------------------ Tasks

export async function listTasks(): Promise<Task[]> {
  return request<Task[]>("/api/tasks");
}

export interface CreateTaskInput {
  title: string;
  priority?: Task["priority"];
  due_at?: string;
  reminder_enabled?: boolean;
  recurrence?: Task["recurrence"];
  tags?: string[];
  notes?: string;
}

export async function createTask(input: CreateTaskInput): Promise<Task> {
  return request<Task>("/api/tasks", { method: "POST", body: JSON.stringify(input) });
}

export async function completeTask(id: number): Promise<Task> {
  return request<Task>(`/api/tasks/${id}`, {
    method: "PATCH",
    body: JSON.stringify({ status: "completed" }),
  });
}

export async function deleteTask(id: number): Promise<void> {
  await request<void>(`/api/tasks/${id}`, { method: "DELETE" });
}

export async function getDueReminders(): Promise<Task[]> {
  return request<Task[]>("/api/tasks/due-reminders");
}

// ------------------------------------------------------------------ Memory

export async function listMemory(): Promise<MemoryEntry[]> {
  return request<MemoryEntry[]>("/api/memory");
}

export async function saveMemory(
  content: string,
  category?: string,
  memory_type: MemoryType = "fact"
): Promise<MemoryEntry> {
  return request<MemoryEntry>("/api/memory", {
    method: "POST",
    body: JSON.stringify({ content, category, memory_type }),
  });
}

export async function deleteMemory(id: number): Promise<void> {
  await request<void>(`/api/memory/${id}`, { method: "DELETE" });
}

// Both memory layers (M4): core facts (always in Cypher's prompt, limited)
// and the searchable archive.
export type MemoryLayer = "core" | "archive";

export interface MemoryItem {
  layer: MemoryLayer;
  id: string;
  text: string;
}

export interface MemoryOverview {
  core: MemoryItem[];
  archive: MemoryItem[];
  core_used_tokens: number;
  core_max_tokens: number;
}

export async function getMemoryOverview(): Promise<MemoryOverview> {
  return request<MemoryOverview>("/api/memory/overview");
}

export async function addMemoryItem(text: string, layer: MemoryLayer): Promise<MemoryItem> {
  return request<MemoryItem>("/api/memory/items", { method: "POST", body: JSON.stringify({ text, layer }) });
}

export async function updateMemoryItem(item: MemoryItem, change: { text?: string; move_to?: MemoryLayer }): Promise<MemoryItem> {
  return request<MemoryItem>(`/api/memory/items/${item.layer}/${encodeURIComponent(item.id)}`, { method: "PATCH", body: JSON.stringify(change) });
}

export async function deleteMemoryItem(item: MemoryItem): Promise<void> {
  await request<void>(`/api/memory/items/${item.layer}/${encodeURIComponent(item.id)}`, { method: "DELETE" });
}

export async function deleteConversationHistory(): Promise<{ deleted: number }> {
  return request<{ deleted: number }>("/api/conversations", { method: "DELETE" });
}

// ------------------------------------------------------------------ Pending actions (confirmation flow)

export async function listPendingActions(): Promise<PendingAction[]> {
  return request<PendingAction[]>("/api/actions?status=awaiting_confirmation");
}

export async function confirmAction(id: number): Promise<PendingAction> {
  return request<PendingAction>(`/api/actions/${id}/confirm`, { method: "POST" });
}

export async function rejectAction(id: number): Promise<PendingAction> {
  return request<PendingAction>(`/api/actions/${id}/reject`, { method: "POST" });
}

// ------------------------------------------------------------------ Status

export async function getStatus(): Promise<SystemStatus> {
  return request<SystemStatus>("/api/status");
}

export async function getSettingsOverview(): Promise<Settings> {
  return request<Settings>("/api/settings");
}

// ------------------------------------------------------------------ Provider toggle (Phase 7)
// Manual-only local/cloud switch for the native tool-calling pipeline
// (/api/tool-chat). Never automatic - see CypherVoiceView.tsx's
// provider badge and SettingsView.tsx's toggle, the only two places
// that ever call setActiveProvider.

export async function getActiveProvider(): Promise<ActiveProviderInfo> {
  return request<ActiveProviderInfo>("/api/settings/provider");
}

export async function setActiveProvider(provider: LlmProviderName): Promise<ActiveProviderInfo> {
  return request<ActiveProviderInfo>("/api/settings/provider", {
    method: "POST",
    body: JSON.stringify({ provider }),
  });
}

export async function getUsageSummary(): Promise<UsageSummary> {
  return request<UsageSummary>("/api/usage");
}

// ------------------------------------------------------------------ Projects

export interface CreateProjectInput {
  name: string;
  path?: string;
  description?: string;
  technologies?: string[];
  repository?: string;
  notes?: string;
}

export async function listProjects(): Promise<Project[]> {
  return request<Project[]>("/api/projects");
}

export async function createProject(input: CreateProjectInput): Promise<Project> {
  return request<Project>("/api/projects", { method: "POST", body: JSON.stringify(input) });
}

export async function deleteProject(id: number): Promise<void> {
  await request<void>(`/api/projects/${id}`, { method: "DELETE" });
}

// ------------------------------------------------------------------ Voice (local STT/TTS + native tool-calling)
// Used by the text chat (ChatView); the voice view streams over /api/voice/ws.
// These three endpoints are separate from /api/messages (see backend
// app/api/tool_chat.py, transcribe.py, speak.py) - none of them touch the
// production chat pipeline above.

export async function transcribeAudio(audioBlob: Blob): Promise<TranscribeResult> {
  const formData = new FormData();
  formData.append("audio", audioBlob, "recording.webm");

  const resp = await fetch(`${BASE_URL}/transcribe`, {
    method: "POST",
    headers: bearer(),
    body: formData,
  });
  if (!resp.ok) throw await errorFromResponse(resp, "Transkription fehlgeschlagen");
  return (await resp.json()) as TranscribeResult;
}

export async function toolChat(message: string, source: "voice" | "text" = "voice"): Promise<ToolChatResult> {
  return request<ToolChatResult>("/api/tool-chat", {
    method: "POST",
    body: JSON.stringify({ message, source }),
  });
}

// Resolves a pending_confirmation from toolChat() (see
// backend/app/tools/pending_approvals.py) - approving runs the paused
// tool and continues the reply, rejecting cancels it. Throws (a plain
// 404) if approval_id is unknown or its 5-minute TTL already expired -
// callers should treat that the same as an explicit rejection.
export async function confirmToolCall(approvalId: string, approved: boolean): Promise<ToolChatResult> {
  return request<ToolChatResult>("/api/tool-chat/confirm", {
    method: "POST",
    body: JSON.stringify({ approval_id: approvalId, approved }),
  });
}

export interface SpeakResult {
  audioBlob: Blob;
  audioDurationSeconds: number;
  generationTimeSeconds: number;
}

export async function speakText(text: string): Promise<SpeakResult> {
  const resp = await fetch(`${BASE_URL}/speak`, {
    method: "POST",
    headers: { ...authHeaders() },
    body: JSON.stringify({ text }),
  });
  if (!resp.ok) throw await errorFromResponse(resp, "Sprachausgabe fehlgeschlagen");
  return {
    audioBlob: await resp.blob(),
    audioDurationSeconds: Number(resp.headers.get("X-Audio-Duration-Seconds") ?? 0),
    generationTimeSeconds: Number(resp.headers.get("X-Generation-Time-Seconds") ?? 0),
  };
}
