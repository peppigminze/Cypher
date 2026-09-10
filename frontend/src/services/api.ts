import type {
  ChatMessage,
  MemoryEntry,
  MemoryType,
  PendingAction,
  Project,
  Settings,
  SystemStatus,
  Task,
  ToolChatResult,
  TranscribeResult,
} from "../types";

const BASE_URL = import.meta.env.VITE_API_BASE_URL || "http://localhost:8000";
const USER_TOKEN = import.meta.env.VITE_USER_TOKEN || "";

const QUEUE_KEY = "jarvis.offline_queue.v1";

function authHeaders(): HeadersInit {
  return {
    "Content-Type": "application/json",
    Authorization: `Bearer ${USER_TOKEN}`,
  };
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const resp = await fetch(`${BASE_URL}${path}`, {
    ...init,
    headers: { ...authHeaders(), ...(init?.headers || {}) },
  });
  if (!resp.ok) {
    const text = await resp.text().catch(() => "");
    throw new Error(`Request failed (${resp.status}): ${text || resp.statusText}`);
  }
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
// Powers useCypherVoiceState.ts (frontend/src/components/CypherVoiceView.tsx).
// These three endpoints are separate from /api/messages (see backend
// app/api/tool_chat.py, transcribe.py, speak.py) - none of them touch the
// production chat pipeline above.

export async function transcribeAudio(audioBlob: Blob): Promise<TranscribeResult> {
  const formData = new FormData();
  formData.append("audio", audioBlob, "recording.webm");

  const resp = await fetch(`${BASE_URL}/transcribe`, {
    method: "POST",
    headers: { Authorization: `Bearer ${USER_TOKEN}` },
    body: formData,
  });
  if (!resp.ok) {
    const text = await resp.text().catch(() => "");
    throw new Error(`Transkription fehlgeschlagen (${resp.status}): ${text || resp.statusText}`);
  }
  return (await resp.json()) as TranscribeResult;
}

export async function toolChat(message: string): Promise<ToolChatResult> {
  return request<ToolChatResult>("/api/tool-chat", {
    method: "POST",
    body: JSON.stringify({ message }),
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
  if (!resp.ok) {
    const errText = await resp.text().catch(() => "");
    throw new Error(`Sprachausgabe fehlgeschlagen (${resp.status}): ${errText || resp.statusText}`);
  }
  return {
    audioBlob: await resp.blob(),
    audioDurationSeconds: Number(resp.headers.get("X-Audio-Duration-Seconds") ?? 0),
    generationTimeSeconds: Number(resp.headers.get("X-Generation-Time-Seconds") ?? 0),
  };
}
