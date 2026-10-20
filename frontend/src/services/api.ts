import type { ActiveProviderInfo, LlmProviderName, Project, Settings, Task, UsageSummary } from "../types";

import { API_BASE_URL, AUTH_REQUIRED_EVENT, getStoredToken } from "./auth";

const BASE_URL = API_BASE_URL;

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
  return request<MemoryItem>(`/api/memory/items/${item.layer}/${encodeURIComponent(item.id)}`, {
    method: "PATCH",
    body: JSON.stringify(change),
  });
}

export async function deleteMemoryItem(item: MemoryItem): Promise<void> {
  await request<void>(`/api/memory/items/${item.layer}/${encodeURIComponent(item.id)}`, { method: "DELETE" });
}

export async function deleteConversationHistory(): Promise<{ deleted: number }> {
  return request<{ deleted: number }>("/api/conversations", { method: "DELETE" });
}

export async function getSettingsOverview(): Promise<Settings> {
  return request<Settings>("/api/settings");
}

// ------------------------------------------------------------------ Provider toggle (Phase 7)
// Manual-only local/cloud switch for every turn (voice and chat). Never
// automatic - SettingsView.tsx's toggle is the only place that calls
// setActiveProvider.

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

// ------------------------------------------------------------------ System (M6: diagnostics, audit, logs)

export interface ComponentHealth {
  status: "ok" | "degraded" | "error" | string;
  message: string;
  hint: string;
}

export interface HealthReport {
  status: "ok" | "degraded" | "error" | string;
  version: string;
  uptime_seconds: number;
  components: Record<string, ComponentHealth>;
}

export interface LatencyStat {
  count: number;
  last_ms: number;
  p50_ms: number;
  p90_ms: number;
}

export interface Diagnostics extends HealthReport {
  latency: Record<string, LatencyStat>;
  config: Record<string, unknown>;
  system: Record<string, unknown>;
  context_budget: { parts?: Record<string, number>; total?: number; window?: number; fits?: boolean; error?: string };
}

export interface AuditEntry {
  id: number;
  ts: string;
  conversation_id: string | null;
  source: string | null;
  provider: string | null;
  tool: string;
  risk: string | null;
  decision: string;
  summary: string | null;
  arguments: Record<string, unknown>;
  success: boolean | null;
  error: string | null;
  duration_ms: number | null;
}

export interface LogEntry {
  ts: string;
  level: string;
  logger: string;
  msg: string;
  request_id?: string;
}

/** Public, no token needed - the UI must be able to say "Ollama läuft nicht" before pairing. */
export async function getHealth(signal?: AbortSignal): Promise<HealthReport> {
  const resp = await fetch(`${BASE_URL}/api/health`, { signal });
  if (!resp.ok && resp.status !== 503) throw new Error(`HTTP ${resp.status}`);
  return (await resp.json()) as HealthReport;
}

export async function getDiagnostics(): Promise<Diagnostics> {
  return request<Diagnostics>("/api/diagnostics");
}

export async function getAudit(limit = 100): Promise<AuditEntry[]> {
  const data = await request<{ entries: AuditEntry[] }>(`/api/audit?limit=${limit}`);
  return data.entries;
}

export async function getLogs(level = "INFO", limit = 300): Promise<LogEntry[]> {
  const data = await request<{ entries: LogEntry[] }>(`/api/logs?limit=${limit}&level=${encodeURIComponent(level)}`);
  return data.entries;
}

// ------------------------------------------------------------------ Pairing (phone)

export interface PairingLink {
  url: string;
  /** "tailscale" = https via `tailscale serve`, "direct" = http to API_HOST */
  kind: "tailscale" | "direct";
  /** Browsers only allow the microphone over HTTPS. */
  microphone: boolean;
}

export interface PairingInfo {
  token: string;
  links: PairingLink[];
  /** QR code of the first link as an SVG data URI (null without a link). */
  qr: string | null;
  tailscale: { installed: boolean; url: string | null };
  /** e.g. "tailscale serve --bg 8000" */
  serve_command: string;
  note: string;
}

/** Only answers on the PC itself (403 elsewhere, e.g. on the phone). */
export async function getPairing(): Promise<PairingInfo> {
  return request<PairingInfo>("/api/auth/pairing");
}

// ------------------------------------------------------------------ Voice preferences

export interface VoicePrefs {
  /** Wake mode: keep listening this long after an answer has finished playing (0 = off). */
  followup_window_seconds: number;
}

export async function getVoicePrefs(): Promise<VoicePrefs> {
  return request<VoicePrefs>("/api/settings/voice");
}

export async function setVoicePrefs(prefs: VoicePrefs): Promise<VoicePrefs> {
  return request<VoicePrefs>("/api/settings/voice", { method: "PUT", body: JSON.stringify(prefs) });
}

// ------------------------------------------------------------------ Safety prompt (FIX2)

export interface CriticalConfirmState {
  /** true = critical actions ask first (default). */
  enabled: boolean;
  mode: "on" | "off_session" | "off_permanent";
  /** Categories that ALWAYS ask, even when the prompt is off ("rote Linie"). */
  red_line: string[];
  red_line_labels: string[];
}

export const CRITICAL_CONFIRM_EVENT = "cypher:critical-confirm";

export async function getCriticalConfirm(): Promise<CriticalConfirmState> {
  return request<CriticalConfirmState>("/api/security/critical-confirm");
}

/** Only works in the browser on the PC itself (the backend refuses the phone, scripts and Cypher's own tools). */
export async function setCriticalConfirm(enabled: boolean, scope: "session" | "permanent"): Promise<CriticalConfirmState> {
  const state = await request<CriticalConfirmState>("/api/security/critical-confirm", {
    method: "PUT",
    body: JSON.stringify({ enabled, scope }),
  });
  window.dispatchEvent(new CustomEvent(CRITICAL_CONFIRM_EVENT, { detail: state }));
  return state;
}
