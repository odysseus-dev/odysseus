export interface Session {
  id: string; name: string; model: string; endpoint_url?: string;
  archived?: boolean; is_important?: boolean; folder?: string | null;
  updated_at?: string | null; last_message_at?: string | null;
  message_count?: number; mode?: string | null;
}
export interface Endpoint {
  url: string; endpoint_id: string; endpoint_name: string;
  category?: string; endpoint_kind?: string;
  models: string[]; models_display?: string[];
  models_extra?: string[]; models_extra_display?: string[];
}
export interface ModelsResponse { items: Endpoint[]; hosts?: unknown[] }
export interface DefaultChat { model: string; endpoint_url?: string; endpoint_id?: string; fallbacks?: string[] }
export interface HistoryMsg { role: string; content: unknown; model?: string; attachments?: unknown[]; metadata?: Record<string, unknown> }
export interface Source { url?: string; title?: string; snippet?: string }
export interface ToolEvent { name: string; input?: unknown; output?: string; progress?: string }
export interface ChatMessage {
  role: "user" | "assistant"; content: string; reasoning?: string; model?: string;
  tools?: ToolEvent[]; sources?: Source[]; streaming?: boolean;
}
export interface Memory {
  id: string; text: string; category?: string; categories?: string[];
  source?: string; timestamp?: number; session_id?: string;
}
export interface Note {
  id: string; title?: string; content?: string; items?: unknown;
  note_type?: string; color?: string; label?: string;
  pinned?: boolean; archived?: boolean; due_date?: string; updated_at?: string;
}
export interface Task {
  id: string; name?: string; title?: string; status?: string;
  task_type?: string; action?: string; prompt?: string;
  schedule?: string; cron?: string; model?: string;
  next_run_at?: string; last_run_at?: string; enabled?: boolean;
}
export interface Skill { id?: string; name: string; description?: string; published?: boolean }
export interface BuiltinSkill { name: string; description?: string; is_overridden?: boolean }
export interface GalleryImage { id: string; filename: string; url: string; prompt?: string; tags?: string; favorite?: boolean; width?: number; height?: number }
export interface EmailMsg {
  uid: string; subject?: string; from?: string; from_addr?: string; sender?: string;
  date?: string; snippet?: string; preview?: string; unread?: boolean; seen?: boolean;
}
export interface DocItem { id: string; title?: string; name?: string; language?: string; updated_at?: string; session_name?: string }
