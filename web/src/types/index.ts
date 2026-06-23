export interface Session {
  id: string; name: string; model: string; endpoint_url?: string;
  endpoint_id?: string; created_at?: string | null;
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
export interface ToolDiff { text?: string; file?: string; added?: number; removed?: number; new_file?: boolean }
export interface ToolEvent {
  name: string; input?: unknown; command?: string; output?: string; progress?: string;
  exitCode?: number; round?: number; running?: boolean;
  // Rich artifacts a tool can produce — persisted in tool_events and/or streamed
  // on tool_output. The original frontend re-renders these in the tool row; V2
  // dropped them before this field existed.
  imageUrl?: string; imagePrompt?: string; screenshot?: string; diff?: ToolDiff;
  docId?: string; docTitle?: string;
}
// One agent round = the model's text for that step plus the tools it then ran.
// Drives the interleaved text → tools → text → tools layout (parity with the
// legacy chatRenderer multi-bubble reconstruction). Populated live AND on reload.
export interface AgentRound { text: string; tools: ToolEvent[] }
export interface Artifact { title: string; language?: string; content: string; closed?: boolean }
export interface ChatAttachment {
  id?: string; name: string; mime?: string; size?: number; width?: number; height?: number; previewUrl?: string;
  visionText?: string;
}
export interface AskUserOption { label: string; description?: string }
export interface AskUserPrompt { question?: string; options: AskUserOption[]; multi?: boolean }
export interface AgentNotice {
  kind: "info" | "warning" | "error" | "stopped";
  text: string;
  continuePrompt?: string;
}
export interface ChatMessage {
  role: "user" | "assistant"; content: string; reasoning?: string; model?: string;
  groupName?: string; groupParticipantId?: string; groupRunId?: string;
  tools?: ToolEvent[]; rounds?: AgentRound[]; sources?: Source[]; streaming?: boolean;
  research?: { phase: string; detail?: string };
  modelActual?: string; artifact?: Artifact;
  attachments?: ChatAttachment[]; messageId?: string;
  askUser?: AskUserPrompt; notice?: AgentNotice;
  plan?: string; // live agent plan checklist (markdown), from plan_update events
  edited?: boolean;
  metrics?: {
    tokens_in?: number; tokens_out?: number; tokens_total?: number; context_tokens?: number;
    cost?: number; tok_per_sec?: number; prep_seconds?: number; model_wait_seconds?: number;
    response_seconds?: number;
  };
}
export interface Memory {
  id: string; text: string; category?: string; categories?: string[];
  source?: string; timestamp?: number; session_id?: string; pinned?: boolean;
  uses?: number;
}
export interface Note {
  id: string; title?: string; content?: string; items?: NoteItem[] | null;
  note_type?: string; color?: string; label?: string;
  pinned?: boolean; archived?: boolean; due_date?: string; updated_at?: string; created_at?: string;
  image_url?: string; repeat?: string; source?: string; session_id?: string; sort_order?: number;
  agent_session_id?: string; ai_classification?: unknown;
}
export interface NoteItem { text?: string; done?: boolean; checked?: boolean; indent?: number; id?: string }
export interface Task {
  id: string; name?: string; title?: string; status?: string;
  task_type?: string; action?: string; prompt?: string;
  schedule?: string; cron?: string; model?: string;
  scheduled_time?: string; scheduled_day?: number; scheduled_date?: string;
  cron_expression?: string; trigger_type?: string; trigger_event?: string; trigger_count?: number; trigger_counter?: number;
  next_run?: string; next_run_at?: string; last_run?: string; last_run_at?: string; enabled?: boolean;
  output_target?: string; session_id?: string; run_count?: number; webhook_token?: string;
  then_task_id?: string | null; endpoint_url?: string | null; character_id?: string | null; crew_member_id?: string | null;
  created_at?: string; updated_at?: string;
  is_builtin?: boolean; is_modified?: boolean; notifications_enabled?: boolean;
  last_run_status?: string; last_run_result?: string;
}
export interface TaskRun {
  id: string; task_id?: string; task_name?: string; task_type?: string; action?: string;
  started_at?: string | null; finished_at?: string | null; status?: string;
  result?: string; error?: string; tokens_used?: number; model?: string; endpoint_url?: string;
  session_id?: string; research_id?: string; output_target?: string;
}
export interface Skill { id?: string; name: string; description?: string; published?: boolean; status?: string; confidence?: number; audit_verdict?: string }
export interface BuiltinSkill { name: string; description?: string; is_overridden?: boolean }
export interface GalleryImage {
  id: string; filename: string; url: string; prompt?: string; tags?: string; ai_tags?: string;
  model?: string; size?: string; quality?: string; album_id?: string | null; session_id?: string | null; session_name?: string | null;
  favorite?: boolean; width?: number; height?: number; file_size?: number; created_at?: string; updated_at?: string;
}
export interface GalleryAlbum {
  id: string; name: string; description?: string; cover_url?: string | null; count?: number; created_at?: string;
}
export interface EmailMsg {
  uid: string; subject?: string; from?: string; from_addr?: string; sender?: string;
  from_name?: string; from_address?: string; is_read?: boolean;
  date?: string; snippet?: string; preview?: string; unread?: boolean; seen?: boolean;
}
export interface DocItem {
  id: string; title?: string; name?: string; language?: string; updated_at?: string; created_at?: string;
  session_id?: string | null; session_name?: string | null; preview?: string; version_count?: number;
}
