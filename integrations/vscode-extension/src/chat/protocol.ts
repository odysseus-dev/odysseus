export type OdysseusMode = "chat" | "agent";
export type EffortLevel = "low" | "medium" | "high";
export type ThinkingMode = "off" | "auto" | "on";
export type AgentApproval = "ask" | "auto";
export type ToastLevel = "info" | "error";
export type ConnectionStatus =
  | "not_configured"
  | "checking"
  | "connected"
  | "offline"
  | "auth_failed"
  | "server_error";

export interface ModelOptionChoice {
  value: string;
  label: string;
  description?: string;
}

export interface ModelOptionDefinition {
  key: string;
  label: string;
  type: "enum";
  defaultValue: string;
  description?: string;
  options: ModelOptionChoice[];
}

export interface ActiveModelOption extends ModelOptionDefinition {
  value: string;
}

export interface SidebarState {
  configured: boolean;
  hasToken: boolean;
  serverUrl: string;
  connectionStatus: ConnectionStatus;
  connectionLabel: string;
  connectionDetail: string | null;
  workspaceWarning: string | null;
  workspaceLabel: string | null;
  serverVersion: string | null;
  mode: OdysseusMode;
  modelOptions: ActiveModelOption[];
  agentApproval: AgentApproval;
  webEnabled: boolean;
  memoryWritable: boolean;
  memoryAutoSave: boolean;
  selectedModelLabel: string | null;
  selectedModelDetail: string | null;
  selectedSessionLabel: string | null;
  selectedFileLabel: string | null;
  stagedContexts: StagedContext[];
  pendingAttachments: PendingAttachment[];
  isStreaming: boolean;
}

export interface StagedContext {
  id: string;
  label: string;
  detail: string;
}

export interface PendingAttachment {
  id: string;
  label: string;
  detail: string;
  kind: "file" | "image";
}

export interface TranscriptMessage {
  kind: "message";
  id: string;
  role: "user" | "assistant" | "tool" | "system";
  content: string;
  title?: string | null;
  detail?: string | null;
  status?: "streaming" | "done" | "error";
}

export interface ToolDiffSummary {
  file: string | null;
  text: string;
  added: number;
  removed: number;
  newFile: boolean;
}

export interface AgentToolEntry {
  id: string;
  toolName: string;
  label: string;
  command: string | null;
  output: string;
  tail: string | null;
  diff: ToolDiffSummary | null;
  screenshotUrl: string | null;
  status: "running" | "done" | "error";
  exitCode: number | null;
}

export interface AgentStepEntry {
  kind: "agentStep";
  id: string;
  round: number | null;
  title: string;
  detail: string | null;
  status: "running" | "done" | "error";
  tools: AgentToolEntry[];
}

export type TranscriptEntry = TranscriptMessage | AgentStepEntry;

export interface TranscriptSnapshot {
  sessionId: string | null;
  entries: TranscriptEntry[];
}

export type StreamUpdate =
  | { type: "append"; message: TranscriptMessage }
  | { type: "assistantStart"; message: TranscriptMessage }
  | { type: "assistantDelta"; messageId: string; delta: string }
  | { type: "assistantDone"; messageId: string; detail?: string | null }
  | { type: "assistantError"; messageId: string; error: string }
  | { type: "agentStepStart"; step: AgentStepEntry }
  | { type: "agentToolStart"; stepId: string; tool: AgentToolEntry }
  | { type: "agentToolProgress"; stepId: string; toolId: string; tail: string }
  | { type: "agentToolDone"; stepId: string; tool: AgentToolEntry }
  | { type: "agentStepStatus"; stepId: string; status: AgentStepEntry["status"] }
  | { type: "session"; sessionId: string; label: string }
  | { type: "replaceTranscript"; transcript: TranscriptSnapshot };

export type WebviewToHostMessage =
  | { type: "ready" }
  | { type: "configure" }
  | { type: "saveConfig"; serverUrl: string; token: string }
  | { type: "newChat" }
  | { type: "pickModel" }
  | { type: "pickSession" }
  | { type: "attachFile" }
  | { type: "attachImage" }
  | { type: "attachFolder" }
  | { type: "addContext" }
  | { type: "toggleWeb" }
  | { type: "saveToBrain" }
  | { type: "toggleMemoryAutoSave" }
  | { type: "openUsage" }
  | { type: "openModelMenu" }
  | { type: "selectModel"; selection: SavedModelSelection }
  | { type: "openSessionMenu" }
  | { type: "selectSession"; sessionId: string; label: string }
  | { type: "setMode"; mode: OdysseusMode }
  | { type: "setModelOption"; key: string; value: string }
  | { type: "setAgentApproval"; approval: AgentApproval }
  | { type: "sendPrompt"; message: string }
  | { type: "stop" }
  | { type: "clearContexts" }
  | { type: "clearAttachments" }
  | { type: "removeContext"; id: string }
  | { type: "removeAttachment"; id: string }
  | { type: "insertCode"; code: string }
  | { type: "previewDiff"; code: string; label?: string | null };

export type HostToWebviewMessage =
  | { type: "state"; state: SidebarState }
  | { type: "openConfig" }
  | { type: "toast"; level: ToastLevel; message: string }
  | { type: "transcript"; transcript: TranscriptSnapshot }
  | { type: "stream"; update: StreamUpdate }
  | { type: "draft"; text: string }
  | { type: "modelCatalog"; groups: ModelMenuGroup[] }
  | { type: "sessionCatalog"; items: SessionMenuItem[] };

export interface ModelMenuItem {
  selection: SavedModelSelection;
  selected: boolean;
}

export interface ModelMenuGroup {
  title: string;
  items: ModelMenuItem[];
}

export interface SessionMenuItem {
  sessionId: string;
  label: string;
  detail: string;
  selected: boolean;
}

export interface SavedModelSelection {
  serverUrl?: string;
  endpointId: string;
  endpointUrl: string;
  modelId: string;
  label: string;
  detail: string;
  endpointName: string | null;
  providerLabel: string;
  sourceLabel: string;
  modelOptions?: ModelOptionDefinition[];
  modelOptionsHydrated?: boolean;
}

export interface SavedSessionSelection {
  serverUrl?: string;
  sessionId: string;
  label: string;
}
