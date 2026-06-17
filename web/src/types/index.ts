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
