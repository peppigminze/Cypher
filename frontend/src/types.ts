export type MessageStatus = "pending" | "processing" | "completed" | "failed" | "cancelled";
export type TaskStatus = "pending" | "completed" | "cancelled";
export type TaskPriority = "low" | "medium" | "high";

export type ProcessedBy = "local" | "cloud" | null;

export interface ToolObservation {
  tool: string;
  arguments: Record<string, unknown>;
  result?: unknown;
  error?: string;
}

export interface ChatMessage {
  id: number;
  client_id: string | null;
  content: string;
  status: MessageStatus;
  response: string | null;
  error: string | null;
  retry_count: number;
  processed_by: ProcessedBy;
  observations: ToolObservation[];
  created_at: string;
  processed_at: string | null;
}

export type Recurrence = "none" | "daily" | "weekly" | "monthly";

export interface Task {
  id: number;
  title: string;
  description: string | null;
  notes: string | null;
  tags: string[];
  status: TaskStatus;
  priority: TaskPriority;
  due_at: string | null;
  reminder_enabled: boolean;
  recurrence: Recurrence;
  last_notified_at: string | null;
  created_at: string;
  completed_at: string | null;
}

export type MemoryType = "fact" | "preference" | "project";

export interface MemoryEntry {
  id: number;
  content: string;
  category: string | null;
  memory_type: MemoryType;
  created_at: string;
  updated_at: string;
}

export interface Project {
  id: number;
  name: string;
  path: string | null;
  description: string | null;
  technologies: string[];
  repository: string | null;
  notes: string | null;
  created_at: string;
  updated_at: string;
}

export interface WakeOnLanInfo {
  configured: boolean;
  mac_address: string | null;
  broadcast: string;
  port: number;
  command: string | null;
}

export interface Settings {
  llm_provider: string;
  local_llm_model: string;
  local_llm_base_url: string;
  cloud_llm_enabled: boolean;
  cloud_llm_model: string | null;
  allowed_directories: string[];
  message_max_retries: number;
  wake_on_lan: WakeOnLanInfo;
}

export interface SystemStatus {
  pc_online: boolean;
  last_seen: string | null;
  pending_messages: number;
  llm_provider: string;
  llm_available: boolean | null;
}

export type ActionStatus = "awaiting_confirmation" | "confirmed" | "rejected" | "executing" | "completed" | "failed";

export interface PendingAction {
  id: number;
  message_id: number | null;
  tool_name: string;
  arguments: Record<string, unknown>;
  observations: unknown[];
  status: ActionStatus;
  result: unknown;
  error: string | null;
  created_at: string;
  resolved_at: string | null;
}

// ---------------------------------------------------------------- Voice (native tool-calling + local STT/TTS)

export interface ToolCallResult {
  name: string;
  arguments: Record<string, unknown>;
  success: boolean;
  data?: unknown;
  error: string | null;
}

export interface ToolChatResult {
  reply: string;
  tool_calls: ToolCallResult[];
  steps_used: number;
}

export interface TranscribeResult {
  text: string;
  language: string | null;
  audio_duration_seconds: number;
  transcribe_time_seconds: number;
}
