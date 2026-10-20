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
  wake_on_lan: WakeOnLanInfo;
}

export type LlmProviderName = "local" | "cloud";

// ---------------------------------------------------------------- Provider toggle (Phase 7)

export interface ActiveProviderInfo {
  provider: LlmProviderName;
  cloud_configured: boolean;
  /** "ok" | "disabled" (CLOUD_LLM_ENABLED=false) | "no_key" */
  cloud_status?: "ok" | "disabled" | "no_key";
  cloud_model?: string;
}

export interface UsageTotals {
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
  request_count: number;
  /** USD - cloud only, local is free */
  cost_usd?: number;
}

export interface UsagePeriod {
  total: UsageTotals;
  by_provider: Record<string, UsageTotals>;
}

export interface UsageSummary {
  today: UsagePeriod;
  month: UsagePeriod;
  cloud_monthly_budget_usd?: number;
  cloud_model?: string;
}
