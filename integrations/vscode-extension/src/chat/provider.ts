import * as fs from "node:fs";
import * as path from "node:path";

import * as vscode from "vscode";

import {
  DEFAULT_SERVER_URL,
  OdysseusApiClient,
  OdysseusRequestError,
  type ConnectionProbeResult,
  type OdysseusHistoryResponse,
  readServerConfig,
  storeServerConfig,
} from "../api/client";
import { consumeSseBuffer } from "../api/sse";
import {
  getCurrentFileContext,
  getCurrentFileLabel,
  getSelectionContext,
  insertCodeAtCursor,
  previewCodeDiff,
} from "../context/editorContext";
import {
  clearSelectedModel,
  fetchModelMenuGroups,
  getSelectedModel,
  pickModel,
  reconcileSelectedModelScope,
  setSelectedModel,
} from "../model/modelPicker";
import {
  clearSelectedSession,
  fetchSessionMenuItems,
  getSelectedSession,
  pickSession,
  reconcileSelectedSessionScope,
  setSelectedSession,
} from "../session/sessionPicker";
import type {
  ActiveModelOption,
  AgentApproval,
  AgentStepEntry,
  AgentToolEntry,
  ConnectionStatus,
  EffortLevel,
  HostToWebviewMessage,
  ModelOptionDefinition,
  OdysseusMode,
  PendingAttachment,
  SavedModelSelection,
  SidebarState,
  StagedContext,
  StreamUpdate,
  ThinkingMode,
  ToastLevel,
  ToolDiffSummary,
  TranscriptEntry,
  TranscriptMessage,
  TranscriptSnapshot,
  WebviewToHostMessage,
} from "./protocol";

const MODE_STATE_KEY = "odysseus.mode";
const MODEL_OPTIONS_STATE_KEY = "odysseus.modelOptionsByModel";
const EFFORT_STATE_KEY = "odysseus.effort";
const THINKING_STATE_KEY = "odysseus.thinkingMode";
const APPROVAL_STATE_KEY = "odysseus.agentApproval";
const WEB_STATE_KEY = "odysseus.webEnabled";

const DEFAULT_APPROVAL: AgentApproval = "ask";

// Categories the Brain understands (mirrors MEMORY_CATEGORIES in the web UI).
const MEMORY_CATEGORIES = ["fact", "identity", "preference", "contact", "project", "goal", "task"];

// Read whether the paired API token may write memories. The Codex capabilities
// payload reports per-tool scope availability; we gate the "Save to Brain"
// affordances on it so a read-only token fails fast with a clear message
// instead of a 403 mid-save.
function capabilityMemoryWritable(result: ConnectionProbeResult): boolean {
  const tools = result.capabilities.tools as Record<
    string,
    { write?: boolean; available?: boolean } | undefined
  >;
  const mem = tools?.memory;
  return Boolean(mem && mem.write && (mem.available ?? true));
}

// The token-usage dashboard is an admin-scoped modal inside the Odysseus web
// app (its /api/usage/* endpoints require_admin, so the chat-scoped extension
// token can't render it in-process). We open the web app in the user's browser
// — where their admin session lives — with a #usage fragment so the app can
// auto-open the modal once it grows a matching hashchange handler.
const USAGE_PATH = "/#usage";

interface ConnectionSnapshot {
  status: ConnectionStatus;
  label: string;
  detail: string | null;
  version: string | null;
}

interface ActiveStream {
  abortController: AbortController;
  assistantMessageId: string;
  sessionId: string;
}

interface PendingContext extends StagedContext {
  content: string;
}

interface PendingAttachmentState extends PendingAttachment {
  uri: vscode.Uri;
}

interface LiveAgentState {
  currentRound: number | null;
  currentStepId: string | null;
  currentToolId: string | null;
}

const EMPTY_TRANSCRIPT: TranscriptSnapshot = {
  sessionId: null,
  entries: [],
};

export class OdysseusSidebarProvider implements vscode.WebviewViewProvider {
  public static readonly viewId = "odysseus.chat";

  private readonly stateDidChangeEmitter = new vscode.EventEmitter<void>();
  private readonly outputChannel: vscode.OutputChannel;
  private view: vscode.WebviewView | undefined;
  private connection: ConnectionSnapshot = {
    status: "not_configured",
    label: "Needs token",
    detail: "Configure an Odysseus server URL and API token.",
    version: null,
  };
  private validationPromise: Promise<ConnectionSnapshot> | null = null;
  private memoryWritable = false; // does the paired token carry the memory:write scope?
  private readonly memoryPromptDismissed = new Set<string>(); // sessions where the user said "Not now"
  private transcript: TranscriptSnapshot = { ...EMPTY_TRANSCRIPT };
  private activeStream: ActiveStream | null = null;
  private autoContinueCount = 0; // capped rounds_exhausted auto-continues for the current user turn
  private pendingContexts: PendingContext[] = [];
  private pendingAttachments: PendingAttachmentState[] = [];

  public readonly onDidChangeState = this.stateDidChangeEmitter.event;

  public constructor(
    private readonly extensionUri: vscode.Uri,
    private readonly context: vscode.ExtensionContext,
    outputChannel?: vscode.OutputChannel,
  ) {
    this.outputChannel = outputChannel ?? vscode.window.createOutputChannel("Odysseus");
  }

  public async resolveWebviewView(view: vscode.WebviewView): Promise<void> {
    this.view = view;
    view.webview.options = {
      enableScripts: true,
      localResourceRoots: [
        vscode.Uri.joinPath(this.extensionUri, "webview"),
        vscode.Uri.joinPath(this.extensionUri, "media"),
        vscode.Uri.joinPath(this.extensionUri, "dist"),
      ],
    };
    view.webview.html = this.renderWebview(view.webview);

    view.webview.onDidReceiveMessage(async (message: WebviewToHostMessage) => {
      try {
        await this.handleMessage(message);
      } catch (error) {
        const detail = error instanceof Error ? error.message : String(error);
        await this.postMessage({ type: "toast", level: "error", message: detail });
      }
    });

    view.onDidDispose(() => {
      if (this.view === view) {
        this.view = undefined;
      }
    });

    await this.reconcileScopedSelections();
    await this.refresh({ validateConnection: false });
    await this.postTranscript();
    await this.loadSelectedSessionHistory();
  }

  public async configure(): Promise<void> {
    await this.postMessage({ type: "openConfig" });
  }

  public async newChat(): Promise<void> {
    if (this.activeStream) {
      await this.stopStreaming();
    }
    await clearSelectedSession(this.context);
    this.transcript = { ...EMPTY_TRANSCRIPT };
    this.pendingAttachments = [];
    await this.refresh({ validateConnection: false });
    await this.postTranscript();
    await this.notify(
      "info",
      "Started a fresh local draft. The session will be created on first send.",
    );
  }

  public async chooseModel(): Promise<void> {
    const connection = await this.validateConnection({ force: false });
    if (connection.status !== "connected") {
      await this.notify("error", connection.detail ?? "Odysseus is not ready yet.");
      return;
    }
    const selection = await pickModel(this.context);
    if (selection) {
      await this.refresh({ validateConnection: false });
      await this.notify("info", `Selected model: ${selection.providerLabel} • ${selection.label}`);
    }
  }

  // In-panel model dropdown: fetch the catalog and hand it to the webview so the
  // list renders inside the Odysseus panel instead of a native VS Code quick pick.
  public async openModelMenu(): Promise<void> {
    const connection = await this.validateConnection({ force: false });
    if (connection.status !== "connected") {
      await this.postMessage({ type: "modelCatalog", groups: [] });
      await this.notify("error", connection.detail ?? "Odysseus is not ready yet.");
      return;
    }
    try {
      const groups = await fetchModelMenuGroups(this.context);
      await this.postMessage({ type: "modelCatalog", groups });
    } catch (error) {
      await this.postMessage({ type: "modelCatalog", groups: [] });
      const detail = error instanceof Error ? error.message : String(error);
      await this.notify("error", `Could not load models: ${detail}`);
    }
  }

  public async selectModelFromMenu(selection: SavedModelSelection): Promise<void> {
    await setSelectedModel(this.context, selection);
    await this.refresh({ validateConnection: false });
    await this.notify("info", `Selected model: ${selection.providerLabel} • ${selection.label}`);
  }

  public async chooseSession(): Promise<void> {
    const connection = await this.validateConnection({ force: false });
    if (connection.status !== "connected") {
      await this.notify("error", connection.detail ?? "Odysseus is not ready yet.");
      return;
    }
    const selection = await pickSession(this.context);
    if (selection) {
      await this.loadSelectedSessionHistory(selection.sessionId);
      await this.refresh({ validateConnection: false });
      await this.notify("info", `Selected session: ${selection.label}`);
    }
  }

  public async openSessionMenu(): Promise<void> {
    const connection = await this.validateConnection({ force: false });
    if (connection.status !== "connected") {
      await this.postMessage({ type: "sessionCatalog", items: [] });
      await this.notify("error", connection.detail ?? "Odysseus is not ready yet.");
      return;
    }
    try {
      const items = await fetchSessionMenuItems(this.context);
      await this.postMessage({ type: "sessionCatalog", items });
    } catch (error) {
      await this.postMessage({ type: "sessionCatalog", items: [] });
      const detail = error instanceof Error ? error.message : String(error);
      await this.notify("error", `Could not load sessions: ${detail}`);
    }
  }

  public async selectSessionFromMenu(sessionId: string, label: string): Promise<void> {
    await setSelectedSession(this.context, { sessionId, label });
    await this.loadSelectedSessionHistory(sessionId);
    await this.refresh({ validateConnection: false });
    await this.notify("info", `Selected session: ${label}`);
  }

  public async setMode(mode: OdysseusMode): Promise<void> {
    await this.context.workspaceState.update(MODE_STATE_KEY, mode);
    await this.refresh({ validateConnection: false });
  }

  public async setModelOption(key: string, value: string): Promise<void> {
    const model = await this.getSelectedModelSelection();
    if (!model) {
      return;
    }
    const definition = (model.modelOptions ?? []).find((option) => option.key === key);
    if (!definition || !definition.options.some((option) => option.value === value)) {
      return;
    }
    const allOptions = this.getModelOptionsStore();
    const modelKey = getModelStorageKey(model);
    allOptions[modelKey] = {
      ...(allOptions[modelKey] ?? {}),
      [key]: value,
    };
    await this.context.workspaceState.update(MODEL_OPTIONS_STATE_KEY, allOptions);
    await this.refresh({ validateConnection: false });
  }

  public async setAgentApproval(approval: AgentApproval): Promise<void> {
    await this.context.workspaceState.update(APPROVAL_STATE_KEY, approval);
    await this.refresh({ validateConnection: false });
  }

  public async toggleWeb(): Promise<void> {
    const next = !this.getWebEnabled();
    await this.context.workspaceState.update(WEB_STATE_KEY, next);
    await this.refresh({ validateConnection: false });
    await this.notify(
      "info",
      next ? "Web browsing enabled for the next prompt." : "Web browsing disabled.",
    );
  }

  public async openUsage(): Promise<void> {
    const config = await readServerConfig(this.context);
    const base = config.serverUrl || DEFAULT_SERVER_URL;
    const target = vscode.Uri.parse(base.replace(/\/$/, "") + USAGE_PATH);
    await vscode.env.openExternal(target);
  }

  private async reconcileScopedSelections(): Promise<void> {
    const [clearedModel, clearedSession] = await Promise.all([
      reconcileSelectedModelScope(this.context),
      reconcileSelectedSessionScope(this.context),
    ]);
    if (clearedModel) {
      this.outputChannel.appendLine("Cleared stale model selection after Odysseus server change.");
    }
    if (clearedSession) {
      this.transcript = { ...EMPTY_TRANSCRIPT };
      await this.postTranscript();
      this.outputChannel.appendLine(
        "Cleared stale session selection after Odysseus server change.",
      );
    }
  }

  private getMode(): OdysseusMode {
    return (
      (this.context.workspaceState.get<OdysseusMode>(MODE_STATE_KEY) ??
        vscode.workspace.getConfiguration("odysseus").get<OdysseusMode>("defaultMode", "chat")) ||
      "chat"
    );
  }

  private getAgentApproval(): AgentApproval {
    return this.context.workspaceState.get<AgentApproval>(APPROVAL_STATE_KEY) ?? DEFAULT_APPROVAL;
  }

  private getWebEnabled(): boolean {
    return this.context.workspaceState.get<boolean>(WEB_STATE_KEY) ?? false;
  }

  private getModelOptionsStore(): Record<string, Record<string, string>> {
    return (
      this.context.workspaceState.get<Record<string, Record<string, string>>>(
        MODEL_OPTIONS_STATE_KEY,
      ) ?? {}
    );
  }

  private getResolvedModelOptions(model: SavedModelSelection | null): ActiveModelOption[] {
    if (!model) {
      return [];
    }
    const definitions = model.modelOptions ?? [];
    const stored = this.getModelOptionsStore()[getModelStorageKey(model)] ?? {};
    return definitions
      .map((definition) => {
        const value = resolveModelOptionValue(definition, stored, this.context);
        return value ? { ...definition, value } : null;
      })
      .filter((option): option is ActiveModelOption => Boolean(option));
  }

  private async getSelectedModelSelection(): Promise<SavedModelSelection | null> {
    const selected = getSelectedModel(this.context);
    if (!selected) {
      return null;
    }
    if (selected.modelOptionsHydrated) {
      return selected;
    }
    return this.hydrateSelectedModelSelection(selected);
  }

  private async hydrateSelectedModelSelection(
    selection: SavedModelSelection,
  ): Promise<SavedModelSelection> {
    const config = await readServerConfig(this.context);
    if (!config.token) {
      const fallback = { ...selection, modelOptions: [], modelOptionsHydrated: true };
      await setSelectedModel(this.context, fallback);
      return fallback;
    }
    try {
      const client = new OdysseusApiClient(config);
      const catalog = await client.getModels();
      for (const endpoint of catalog.items) {
        const modelIds = [...endpoint.models, ...(endpoint.models_extra ?? [])];
        if (
          (endpoint.endpoint_id ?? "") !== selection.endpointId ||
          endpoint.url !== selection.endpointUrl ||
          !modelIds.includes(selection.modelId)
        ) {
          continue;
        }
        const hydrated = {
          ...selection,
          modelOptions: endpoint.model_options?.[selection.modelId] ?? [],
          modelOptionsHydrated: true,
        };
        await setSelectedModel(this.context, hydrated);
        return hydrated;
      }
    } catch (error) {
      this.outputChannel.appendLine(`Could not hydrate model options: ${String(error)}`);
    }
    const fallback = { ...selection, modelOptions: [], modelOptionsHydrated: true };
    await setSelectedModel(this.context, fallback);
    return fallback;
  }

  public async addContext(): Promise<void> {
    const choice = await vscode.window.showQuickPick(
      [
        {
          label: "$(file) Current file",
          value: "file" as const,
          detail: "Stage the active editor file as context.",
        },
        {
          label: "$(list-selection) Selection",
          value: "selection" as const,
          detail: "Stage the highlighted code with a prompt template.",
        },
        {
          label: "$(folder) Folder snapshot",
          value: "folder" as const,
          detail: "Stage a folder tree and file previews.",
        },
      ],
      { title: "Add context to Odysseus", ignoreFocusOut: true },
    );
    if (!choice) {
      return;
    }
    if (choice.value === "file") {
      await this.stageCurrentFileContext();
    } else if (choice.value === "selection") {
      await this.stageSelectionPrompt();
    } else {
      await this.attachFolderContext();
    }
  }

  public async saveConfig(serverUrl: string, token: string): Promise<void> {
    const trimmedUrl = serverUrl.trim() || DEFAULT_SERVER_URL;
    try {
      new URL(trimmedUrl);
    } catch {
      throw new Error("Enter a valid absolute server URL.");
    }

    const config = await readServerConfig(this.context);
    const nextToken = token.trim() || config.token || "";
    if (!nextToken) {
      throw new Error("An Odysseus API token is required.");
    }

    await storeServerConfig(this.context, trimmedUrl, nextToken);
    await this.reconcileScopedSelections();
    const connection = await this.validateConnection({ force: true });
    await this.refresh({ validateConnection: false });
    if (connection.status === "connected") {
      await this.loadSelectedSessionHistory();
      await this.notify("info", "Odysseus connection validated.");
      return;
    }
    await this.notify(
      "error",
      connection.detail ?? `Settings saved, but connection is ${connection.label.toLowerCase()}.`,
    );
  }

  public async resetModelSelection(): Promise<void> {
    await clearSelectedModel(this.context);
    await this.refresh({ validateConnection: false });
  }

  public async stopFromCommand(): Promise<void> {
    await this.stopStreaming();
  }

  public async attachFiles(): Promise<void> {
    const uris = await vscode.window.showOpenDialog({
      title: "Attach Files to Odysseus",
      canSelectFiles: true,
      canSelectFolders: false,
      canSelectMany: true,
      openLabel: "Attach",
    });
    if (!uris?.length) {
      return;
    }
    await this.addAttachmentUris(uris, "file");
    await this.notify(
      "info",
      `${uris.length} file${uris.length === 1 ? "" : "s"} staged for the next prompt.`,
    );
  }

  public async attachImages(): Promise<void> {
    const uris = await vscode.window.showOpenDialog({
      title: "Attach Photos to Odysseus",
      canSelectFiles: true,
      canSelectFolders: false,
      canSelectMany: true,
      openLabel: "Attach",
      filters: {
        Images: ["png", "jpg", "jpeg", "webp", "gif"],
      },
    });
    if (!uris?.length) {
      return;
    }
    await this.addAttachmentUris(uris, "image");
    await this.notify(
      "info",
      `${uris.length} image${uris.length === 1 ? "" : "s"} staged for the next prompt.`,
    );
  }

  public async attachFolderContext(): Promise<void> {
    const uris = await vscode.window.showOpenDialog({
      title: "Attach Folder Snapshot",
      canSelectFiles: false,
      canSelectFolders: true,
      canSelectMany: true,
      openLabel: "Attach Folder",
    });
    if (!uris?.length) {
      return;
    }
    for (const uri of uris) {
      const snapshot = await buildFolderContext(uri);
      this.upsertPendingContext(snapshot);
    }
    await this.refresh({ validateConnection: false });
    await this.notify(
      "info",
      `${uris.length} folder snapshot${uris.length === 1 ? "" : "s"} staged for the next prompt.`,
    );
  }

  private async addAttachmentUris(
    uris: readonly vscode.Uri[],
    kind: PendingAttachment["kind"],
  ): Promise<void> {
    const next = [...this.pendingAttachments];
    for (const uri of uris) {
      const stat = await vscode.workspace.fs.stat(uri);
      const id = uri.toString();
      const label = path.basename(uri.fsPath || uri.path);
      const detail = `${formatBytes(stat.size)} • ${uri.fsPath || uri.path}`;
      const attachment: PendingAttachmentState = { id, uri, label, detail, kind };
      const existingIndex = next.findIndex((item) => item.id === id);
      if (existingIndex >= 0) {
        next[existingIndex] = attachment;
        continue;
      }
      if (next.length >= 10) {
        throw new Error("Odysseus currently supports up to 10 staged attachments per prompt.");
      }
      next.push(attachment);
    }
    this.pendingAttachments = next;
    await this.refresh({ validateConnection: false });
  }

  private async uploadPendingAttachments(
    client: OdysseusApiClient,
    attachments: PendingAttachmentState[],
  ): Promise<Array<{ id: string; name: string }>> {
    if (!attachments.length) {
      return [];
    }
    const files = await Promise.all(
      attachments.map(async (attachment) => {
        const content = await vscode.workspace.fs.readFile(attachment.uri);
        return {
          name: attachment.label,
          content,
          mimeType: inferMimeType(attachment.label, attachment.kind),
        };
      }),
    );
    const uploaded = await client.uploadFiles(files);
    if (uploaded.length !== attachments.length) {
      throw new Error("Some attachments failed to upload. Please retry the staged files.");
    }
    return uploaded.map((item) => ({ id: item.id, name: item.name }));
  }

  public async stageSelectionPrompt(): Promise<void> {
    const selection = getSelectionContext();
    if (!selection) {
      throw new Error("Select some code first.");
    }

    const template = await vscode.window.showQuickPick(
      [
        { label: "Explain", value: "Explain what this code does and call out any risks." },
        { label: "Fix", value: "Find the bug in this selection and propose a fix." },
        { label: "Refactor", value: "Refactor this selection for clarity and maintainability." },
        { label: "Write Tests", value: "Write focused tests for this selection." },
      ],
      {
        title: "Ask Odysseus About Selection",
        ignoreFocusOut: true,
      },
    );

    if (!template) {
      return;
    }

    this.upsertPendingContext({
      id: "selection",
      label: "Selection",
      detail: `${selection.filePath} (${selection.languageId})`,
      content:
        `Selected code from ${selection.filePath} (${selection.languageId}):\n` +
        `\`\`\`${selection.languageId}\n${selection.selection}\n\`\`\``,
    });
    await this.refresh({ validateConnection: false });
    await this.postDraft(template.value);
    await this.notify("info", "Selection staged for the next prompt.");
  }

  public async stageCurrentFileContext(): Promise<void> {
    const file = getCurrentFileContext();
    if (!file) {
      throw new Error("Open a file first.");
    }

    this.upsertPendingContext({
      id: `file:${file.filePath}`,
      label: "Current file",
      detail: `${file.filePath} (${file.languageId})`,
      content:
        `Current file context from ${file.filePath} (${file.languageId}):\n` +
        `\`\`\`${file.languageId}\n${file.content}\n\`\`\``,
    });
    await this.refresh({ validateConnection: false });
    await this.notify("info", "Current file staged as prompt context.");
  }

  // ── Memory / Brain ────────────────────────────────────────────────────────
  /**
   * Save a memory to the Brain. Seeds from `seedText`, else the active editor
   * selection, then lets the user edit it and pick a category. Routed through
   * the scope-gated Codex memory endpoint, so it works for the extension's
   * bearer token (unlike the cookie-only `/api/memory/extract`).
   */
  public async saveToBrain(seedText?: string): Promise<void> {
    const config = await readServerConfig(this.context);
    if (!config.token) {
      await this.notify("error", "Configure an Odysseus API token first.");
      return;
    }
    if (!this.memoryWritable) {
      await this.notify(
        "error",
        "This API token can't write memories — re-pair the extension with the memory:write scope.",
      );
      return;
    }

    let seed = (seedText ?? "").trim();
    if (!seed) {
      const editor = vscode.window.activeTextEditor;
      if (editor && !editor.selection.isEmpty) {
        seed = editor.document.getText(editor.selection).trim();
      }
    }

    const entered = await vscode.window.showInputBox({
      title: "Save to Odysseus Brain",
      prompt: "What should Odysseus remember across chats?",
      value: seed.slice(0, 2000),
      ignoreFocusOut: true,
      validateInput: (value) =>
        value.trim().length === 0 ? "Memory text can't be empty." : undefined,
    });
    const text = (entered ?? "").trim();
    if (!text) {
      return;
    }

    const category = await this.pickMemoryCategory();
    if (!category) {
      return;
    }
    await this.persistMemory(text, category, { linkSession: false, announce: true });
  }

  /** Flip the opt-in `odysseus.memory.autoSave` setting from the panel menu. */
  public async toggleMemoryAutoSave(): Promise<void> {
    const cfg = vscode.workspace.getConfiguration("odysseus");
    const next = !cfg.get<boolean>("memory.autoSave", false);
    await cfg.update("memory.autoSave", next, vscode.ConfigurationTarget.Global);
    await this.notify("info", next ? "Auto-save to Brain is on." : "Auto-save to Brain is off.");
    await this.refresh({ validateConnection: false });
  }

  private async pickMemoryCategory(): Promise<string | undefined> {
    const pick = await vscode.window.showQuickPick(
      MEMORY_CATEGORIES.map((label) => ({ label })),
      { title: "Memory category", placeHolder: "Pick a category", ignoreFocusOut: true },
    );
    return pick?.label;
  }

  private async persistMemory(
    text: string,
    category: string,
    options: { linkSession: boolean; announce: boolean },
  ): Promise<boolean> {
    const config = await readServerConfig(this.context);
    if (!config.token || !this.memoryWritable) {
      return false;
    }
    const sessionId = options.linkSession
      ? (getSelectedSession(this.context)?.sessionId ?? null)
      : null;
    try {
      const client = new OdysseusApiClient(config);
      const result = await client.addMemory({
        text: text.slice(0, 2000),
        category,
        source: "vscode",
        sessionId,
      });
      if (options.announce) {
        const duplicate = typeof result.message === "string" && /exist/i.test(result.message);
        await this.notify(
          "info",
          duplicate ? "Already in your Brain." : "Saved to Odysseus Brain.",
        );
      }
      return true;
    } catch (error) {
      const detail = error instanceof OdysseusRequestError ? error.message : String(error);
      this.outputChannel.appendLine(`Save to Brain failed: ${detail}`);
      await this.notify("error", `Could not save to Brain: ${detail}`);
      return false;
    }
  }

  /**
   * When `odysseus.memory.autoSave` is on, offer (non-modally) to remember the
   * just-finished exchange. The user curates — we don't silently write — so the
   * Brain stays high-signal. "Not now" mutes the prompt for the rest of the
   * session so it never nags.
   */
  private async offerSaveAfterTurn(userText: string, sessionId: string): Promise<void> {
    if (!this.memoryWritable) {
      return;
    }
    const enabled = vscode.workspace
      .getConfiguration("odysseus")
      .get<boolean>("memory.autoSave", false);
    if (!enabled) {
      return;
    }
    const text = userText.trim();
    if (text.length < 20 || this.memoryPromptDismissed.has(sessionId)) {
      return; // skip trivial prompts and sessions the user has muted
    }
    const choice = await vscode.window.showInformationMessage(
      "Save this to Odysseus Brain?",
      "Save",
      "Edit…",
      "Not now",
    );
    if (choice === "Save") {
      await this.persistMemory(text.slice(0, 280), "project", {
        linkSession: true,
        announce: true,
      });
    } else if (choice === "Edit…") {
      await this.saveToBrain(text.slice(0, 280));
    } else if (choice === "Not now") {
      this.memoryPromptDismissed.add(sessionId);
    }
  }

  public async refresh(options: { validateConnection?: boolean } = {}): Promise<void> {
    await this.postMessage({ type: "state", state: await this.getSidebarState() });
    this.stateDidChangeEmitter.fire();
    if (options.validateConnection) {
      void this.validateConnection({ force: false });
    }
  }

  public async getSidebarState(): Promise<SidebarState> {
    const config = await readServerConfig(this.context);
    const connection = !config.token
      ? this.applyConnectionSnapshot({
          status: "not_configured",
          label: "Needs token",
          detail: "Configure an Odysseus server URL and API token.",
          version: null,
        })
      : this.connection;
    const mode = this.getMode();
    const model = await this.getSelectedModelSelection();
    const session = getSelectedSession(this.context);

    return {
      configured: Boolean(config.token),
      hasToken: Boolean(config.token),
      serverUrl: config.serverUrl || DEFAULT_SERVER_URL,
      connectionStatus: connection.status,
      connectionLabel: connection.label,
      connectionDetail: connection.detail,
      workspaceWarning: buildWorkspaceWarning(config.serverUrl || DEFAULT_SERVER_URL, mode),
      workspaceLabel:
        mode === "agent" ? agentWorkspaceLabel(config.serverUrl || DEFAULT_SERVER_URL) : null,
      serverVersion: connection.version,
      mode,
      modelOptions: this.getResolvedModelOptions(model),
      agentApproval: this.getAgentApproval(),
      webEnabled: this.getWebEnabled(),
      memoryWritable: this.memoryWritable,
      memoryAutoSave: vscode.workspace
        .getConfiguration("odysseus")
        .get<boolean>("memory.autoSave", false),
      selectedModelLabel: model?.label ?? null,
      selectedModelDetail: model ? `${model.providerLabel} • ${model.sourceLabel}` : null,
      selectedSessionLabel: session?.label ?? null,
      selectedFileLabel: getCurrentFileLabel(),
      stagedContexts: this.pendingContexts.map(({ id, label, detail }) => ({ id, label, detail })),
      pendingAttachments: this.pendingAttachments.map(({ id, label, detail, kind }) => ({
        id,
        label,
        detail,
        kind,
      })),
      isStreaming: Boolean(this.activeStream),
    };
  }

  public async validateConnection(options: { force: boolean }): Promise<ConnectionSnapshot> {
    if (!options.force && this.validationPromise) {
      return this.validationPromise;
    }

    this.validationPromise = this.runConnectionValidation();
    try {
      return await this.validationPromise;
    } finally {
      this.validationPromise = null;
    }
  }

  public async notify(level: ToastLevel, message: string): Promise<void> {
    await this.postMessage({ type: "toast", level, message });
  }

  private async handleMessage(message: WebviewToHostMessage): Promise<void> {
    switch (message.type) {
      case "ready":
        await this.refresh({ validateConnection: false });
        await this.postTranscript();
        break;
      case "configure":
        await this.configure();
        break;
      case "saveConfig":
        await this.saveConfig(message.serverUrl, message.token);
        break;
      case "newChat":
        await this.newChat();
        break;
      case "pickModel":
        await this.chooseModel();
        break;
      case "pickSession":
        await this.chooseSession();
        break;
      case "attachFile":
        await this.attachFiles();
        break;
      case "attachImage":
        await this.attachImages();
        break;
      case "attachFolder":
        await this.attachFolderContext();
        break;
      case "addContext":
        await this.addContext();
        break;
      case "toggleWeb":
        await this.toggleWeb();
        break;
      case "saveToBrain":
        await this.saveToBrain();
        break;
      case "toggleMemoryAutoSave":
        await this.toggleMemoryAutoSave();
        break;
      case "openUsage":
        await this.openUsage();
        break;
      case "openModelMenu":
        await this.openModelMenu();
        break;
      case "selectModel":
        await this.selectModelFromMenu(message.selection);
        break;
      case "openSessionMenu":
        await this.openSessionMenu();
        break;
      case "selectSession":
        await this.selectSessionFromMenu(message.sessionId, message.label);
        break;
      case "setMode":
        await this.setMode(message.mode);
        break;
      case "setModelOption":
        await this.setModelOption(message.key, message.value);
        break;
      case "setAgentApproval":
        await this.setAgentApproval(message.approval);
        break;
      case "sendPrompt":
        this.autoContinueCount = 0; // fresh user turn — re-arm auto-continue
        await this.sendPrompt(message.message);
        break;
      case "stop":
        await this.stopStreaming();
        break;
      case "clearContexts":
        this.pendingContexts = [];
        await this.refresh({ validateConnection: false });
        await this.notify("info", "Staged context cleared.");
        break;
      case "clearAttachments":
        this.pendingAttachments = [];
        await this.refresh({ validateConnection: false });
        await this.notify("info", "Pending attachments cleared.");
        break;
      case "removeContext":
        this.pendingContexts = this.pendingContexts.filter((context) => context.id !== message.id);
        await this.refresh({ validateConnection: false });
        break;
      case "removeAttachment":
        this.pendingAttachments = this.pendingAttachments.filter(
          (attachment) => attachment.id !== message.id,
        );
        await this.refresh({ validateConnection: false });
        break;
      case "insertCode":
        await insertCodeAtCursor(message.code);
        await this.notify("info", "Code inserted at the cursor.");
        break;
      case "previewDiff":
        await previewCodeDiff(message.code, message.label ?? "Odysseus Suggestion");
        await this.notify("info", "Diff preview opened.");
        break;
      default:
        break;
    }
  }

  private async sendPrompt(message: string): Promise<void> {
    const trimmed = message.trim();
    if (!trimmed) {
      return;
    }
    if (this.activeStream) {
      await this.notify("error", "A response is already streaming.");
      return;
    }

    const connection = await this.validateConnection({ force: false });
    if (connection.status !== "connected") {
      await this.notify("error", connection.detail ?? "Odysseus is not ready yet.");
      return;
    }

    const config = await readServerConfig(this.context);
    const client = new OdysseusApiClient(config);
    const mode = this.getMode();
    let roundsExhausted = false;
    const pendingContexts = [...this.pendingContexts];
    const pendingAttachments = [...this.pendingAttachments];
    const session = await this.ensureSessionForPrompt(client);

    // Paint the user's bubble immediately — before the attachment upload network
    // call — so sending feels instant even with files staged. The detail line
    // summarises the *pending* attachments, which need no upload to display.
    const userMessage: TranscriptMessage = {
      kind: "message",
      id: makeId("user"),
      role: "user",
      content: trimmed,
      detail: summarizeOutgoingMessageDetails(pendingContexts, pendingAttachments),
      status: "done",
    };
    this.appendTranscriptMessage(userMessage);
    await this.postStreamUpdate({ type: "append", message: userMessage });

    const uploadedAttachments = await this.uploadPendingAttachments(client, pendingAttachments);

    const assistantMessage: TranscriptMessage = {
      kind: "message",
      id: makeId("assistant"),
      role: "assistant",
      content: "",
      detail: mode === "agent" ? "Agent mode" : "Chat mode",
      status: "streaming",
    };
    this.appendTranscriptMessage(assistantMessage);
    await this.postStreamUpdate({ type: "assistantStart", message: assistantMessage });

    const body = new FormData();
    body.set("message", buildPromptWithContexts(trimmed, pendingContexts));
    body.set("session", session.sessionId);
    body.set("mode", mode);
    // Web browsing is a real backend toggle (use_web / allow_web_search).
    if (this.getWebEnabled()) {
      body.set("use_web", "true");
      body.set("allow_web_search", "true");
    }
    const currentModel = await this.getSelectedModelSelection();
    const modelOptions = Object.fromEntries(
      this.getResolvedModelOptions(currentModel).map((option) => [option.key, option.value]),
    );
    if (Object.keys(modelOptions).length > 0) {
      body.set("model_options", JSON.stringify(modelOptions));
    }
    if (mode === "agent") {
      body.set("agent_approval", this.getAgentApproval());
      // Let the agent edit/run inside the user's actual VS Code project (local
      // server only — the path is resolved on the server's filesystem).
      const agentWorkspace = resolveAgentWorkspace(config.serverUrl || DEFAULT_SERVER_URL);
      if (agentWorkspace) {
        body.set("workspace", agentWorkspace);
      }
    }
    if (uploadedAttachments.length) {
      body.set(
        "attachments",
        JSON.stringify(uploadedAttachments.map((attachment) => attachment.id)),
      );
    }

    const abortController = new AbortController();
    this.activeStream = {
      abortController,
      assistantMessageId: assistantMessage.id,
      sessionId: session.sessionId,
    };
    await this.refresh({ validateConnection: false });

    try {
      const response = await client.requestRaw("/api/chat_stream", {
        method: "POST",
        body,
        signal: abortController.signal,
      });
      this.pendingContexts = [];
      this.pendingAttachments = [];
      await this.refresh({ validateConnection: false });
      if (!response.body) {
        throw new Error("Odysseus returned no response body.");
      }

      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      let assistantDetail = assistantMessage.detail ?? null;
      const liveAgentState: LiveAgentState = {
        currentRound: mode === "agent" ? 1 : null,
        currentStepId: null,
        currentToolId: null,
      };

      let streamDone = false;
      while (true) {
        const { value, done } = await reader.read();
        if (done) {
          break;
        }
        buffer += decoder.decode(value, { stream: true });
        const parsed = consumeSseBuffer(buffer);
        buffer = parsed.remainder;

        for (const event of parsed.events) {
          if (event.data === "[DONE]") {
            // Stop reading immediately — don't wait for the backend to close
            // the socket (an agent-mode stream can stay open after [DONE],
            // which would leave reader.read() blocked and the composer locked).
            // The post-loop finalizer below marks the message done.
            streamDone = true;
            break;
          }

          if (event.event === "error") {
            throw new Error(event.data);
          }

          const payload = safeJsonParse(event.data);
          if (!payload) {
            continue;
          }

          if (typeof payload.delta === "string") {
            // Reasoning models (DeepSeek etc.) stream their chain-of-thought as
            // deltas flagged `thinking:true`, separate from the real answer. The
            // web app folds these into a collapsible block; here we simply drop
            // them so the bubble shows only the answer (not the model's monologue).
            if (payload.thinking) {
              continue;
            }
            this.appendTranscriptDelta(assistantMessage.id, payload.delta);
            await this.postStreamUpdate({
              type: "assistantDelta",
              messageId: assistantMessage.id,
              delta: payload.delta,
            });
            continue;
          }

          const streamType = typeof payload.type === "string" ? payload.type : "";
          if (streamType === "model_info") {
            const parts = [payload.character_name || payload.model, payload.suffix].filter(Boolean);
            assistantDetail = parts.join(" • ") || assistantDetail;
            continue;
          }

          if (streamType === "agent_step") {
            this.finalizeAgentStep(liveAgentState, "done");
            const round =
              typeof payload.round === "number" ? payload.round : liveAgentState.currentRound;
            liveAgentState.currentRound = round;
            const step = this.createAgentStep(round);
            liveAgentState.currentStepId = step.id;
            this.appendTranscriptEntry(step);
            await this.postStreamUpdate({ type: "agentStepStart", step });
            continue;
          }

          if (streamType === "tool_start") {
            const { step, created } = this.ensureAgentStep(liveAgentState);
            if (created) {
              await this.postStreamUpdate({ type: "agentStepStart", step });
            }
            const tool = createAgentToolFromStart(payload);
            liveAgentState.currentToolId = tool.id;
            this.appendAgentTool(step.id, tool);
            await this.postStreamUpdate({ type: "agentToolStart", stepId: step.id, tool });
            continue;
          }

          if (streamType === "tool_progress") {
            const tail = typeof payload.tail === "string" ? payload.tail.trim() : "";
            if (tail && liveAgentState.currentStepId && liveAgentState.currentToolId) {
              this.updateAgentToolProgress(
                liveAgentState.currentStepId,
                liveAgentState.currentToolId,
                tail,
              );
              await this.postStreamUpdate({
                type: "agentToolProgress",
                stepId: liveAgentState.currentStepId,
                toolId: liveAgentState.currentToolId,
                tail,
              });
            }
            continue;
          }

          if (streamType === "tool_output") {
            const { step, created } = this.ensureAgentStep(liveAgentState);
            if (created) {
              await this.postStreamUpdate({ type: "agentStepStart", step });
            }
            const completedTool = this.completeAgentTool(
              step.id,
              liveAgentState.currentToolId,
              payload,
            );
            liveAgentState.currentToolId = null;
            if (completedTool) {
              await this.postStreamUpdate({
                type: "agentToolDone",
                stepId: step.id,
                tool: completedTool,
              });
            }
            // Open the file the agent just wrote/edited so the user watches it
            // change live in their editor (file-writing tools carry a diff.file).
            const editedDiff = readToolDiff(payload.diff);
            if (editedDiff?.file) {
              await this.revealAgentEditedFile(editedDiff.file);
            }
            continue;
          }

          if (streamType === "rounds_exhausted") {
            roundsExhausted = true;
            continue;
          }

          if (streamType === "approval_request") {
            // "Ask before edits" — the agent loop is blocked awaiting our
            // decision. Prompt natively and POST it back (fire-and-forget so
            // the read loop isn't held; the server sends nothing until resolved).
            void this.handleApprovalRequest(client, payload);
            continue;
          }

          if (streamType === "message_saved" || streamType === "metrics") {
            continue;
          }
        }
        if (streamDone) {
          break;
        }
      }

      this.finalizeAgentStep(liveAgentState, "done");
      this.markTranscriptMessageDone(assistantMessage.id, assistantDetail);
      await this.postStreamUpdate({
        type: "assistantDone",
        messageId: assistantMessage.id,
        detail: assistantDetail,
      });
      // Release the HTTP connection now that we've seen [DONE] (the backend may
      // not close an agent-mode stream on its own).
      await reader.cancel().catch(() => undefined);
      // Offer to remember this exchange (opt-in; no-op unless the user enabled
      // memory.autoSave). Skip while the agent will auto-continue — the final
      // continuation makes the offer once, not every intermediate round.
      if (!roundsExhausted) {
        void this.offerSaveAfterTurn(userMessage.content, session.sessionId);
      }
    } catch (error) {
      const isAbort = error instanceof Error && error.name === "AbortError";
      const detail = isAbort
        ? "Generation stopped."
        : error instanceof Error
          ? error.message
          : String(error);
      if (mode === "agent") {
        this.markLatestAgentStepError(detail);
      }
      this.markTranscriptMessageError(assistantMessage.id, detail);
      await this.postStreamUpdate({
        type: "assistantError",
        messageId: assistantMessage.id,
        error: detail,
      });
      if (!isAbort) {
        await this.notify("error", detail);
      }
    } finally {
      this.activeStream = null;
      await this.refresh({ validateConnection: false });
    }

    // The agent hit its per-turn step cap but isn't finished. Auto-continue a
    // few times so long / unattended tasks complete, then fall back to a manual
    // prompt so it can never loop forever.
    if (roundsExhausted && mode === "agent") {
      const cap = 3;
      if (this.autoContinueCount < cap) {
        this.autoContinueCount += 1;
        await this.notify(
          "info",
          `Agent hit the step limit — continuing (${this.autoContinueCount}/${cap})…`,
        );
        void this.sendPrompt("Continue.");
      } else {
        const choice = await vscode.window.showInformationMessage(
          "The agent paused at its step limit.",
          "Continue",
        );
        if (choice === "Continue") {
          this.autoContinueCount = 0;
          void this.sendPrompt("Continue.");
        }
      }
    }
  }

  private async handleApprovalRequest(
    client: OdysseusApiClient,
    payload: Record<string, unknown>,
  ): Promise<void> {
    const approvalId = String(payload.approval_id || "");
    if (!approvalId) {
      return;
    }
    const toolName = String(payload.tool || "action");
    const command = String(payload.command || "");
    // Modal so a high-stakes action (edit/write/bash/python/trade) can't slip
    // through unseen. Dismissing the dialog counts as Deny (no 30-min hang).
    const choice = await vscode.window.showWarningMessage(
      `Odysseus agent wants to run a "${toolName}" action. Approve?`,
      { modal: true, detail: command || undefined },
      "Approve",
      "Deny",
    );
    const decision: "approve" | "deny" = choice === "Approve" ? "approve" : "deny";
    try {
      await client.approveAgent(approvalId, decision);
    } catch (error) {
      const detail = error instanceof Error ? error.message : String(error);
      await this.notify("error", `Could not send the approval decision: ${detail}`);
    }
  }

  private async revealAgentEditedFile(filePath: string): Promise<void> {
    // Show the file the agent just touched. Only when the agent targets this
    // VS Code workspace (local server); resolve relative tool paths against it.
    const folder = vscode.workspace.workspaceFolders?.[0]?.uri.fsPath;
    if (!folder) {
      return;
    }
    const abs = path.isAbsolute(filePath) ? filePath : path.join(folder, filePath);
    try {
      const document = await vscode.workspace.openTextDocument(vscode.Uri.file(abs));
      await vscode.window.showTextDocument(document, { preview: true, preserveFocus: true });
    } catch {
      // File may not exist yet, or the path is outside the workspace — ignore.
    }
  }

  private async stopStreaming(): Promise<void> {
    if (!this.activeStream) {
      await this.notify("info", "No active Odysseus stream.");
      return;
    }

    const current = this.activeStream;
    this.activeStream = null;
    current.abortController.abort();

    try {
      const config = await readServerConfig(this.context);
      const client = new OdysseusApiClient(config);
      await client.stopChat(current.sessionId);
    } catch (error) {
      this.outputChannel.appendLine(
        `[${new Date().toISOString()}] Stop request warning: ${String(error)}`,
      );
    }

    await this.refresh({ validateConnection: false });
    await this.notify("info", "Stop signal sent to Odysseus.");
  }

  private async ensureSessionForPrompt(
    client: OdysseusApiClient,
  ): Promise<{ sessionId: string; label: string }> {
    const selectedSession = getSelectedSession(this.context);
    const selectedModel = getSelectedModel(this.context);

    if (!selectedSession) {
      if (!selectedModel) {
        throw new Error("Pick a model before starting a new chat.");
      }
      const created = await client.createSession({
        endpointId: selectedModel.endpointId,
        endpointUrl: selectedModel.endpointUrl,
        modelId: selectedModel.modelId,
      });
      const selection = {
        sessionId: created.id,
        label: created.name || "Untitled session",
      };
      await setSelectedSession(this.context, selection);
      this.transcript = { sessionId: created.id, entries: [] };
      await this.postStreamUpdate({
        type: "session",
        sessionId: created.id,
        label: selection.label,
      });
      return selection;
    }

    if (selectedModel) {
      try {
        await client.updateSessionModel({
          sessionId: selectedSession.sessionId,
          endpointId: selectedModel.endpointId,
          endpointUrl: selectedModel.endpointUrl,
          modelId: selectedModel.modelId,
        });
      } catch (error) {
        // The cached session no longer exists on the server (e.g. a server
        // restart dropped an unsaved session, or it was deleted elsewhere).
        // Drop the stale id and start a fresh chat instead of failing.
        if (error instanceof OdysseusRequestError && error.statusCode === 404) {
          await clearSelectedSession(this.context);
          this.transcript = { ...EMPTY_TRANSCRIPT };
          await this.notify("info", "That chat was no longer on the server — started a new one.");
          return this.ensureSessionForPrompt(client);
        }
        throw error;
      }
    }

    if (this.transcript.sessionId !== selectedSession.sessionId) {
      await this.loadSelectedSessionHistory(selectedSession.sessionId);
    }
    return selectedSession;
  }

  private async loadSelectedSessionHistory(sessionId?: string): Promise<void> {
    const selected = sessionId
      ? { sessionId, label: getSelectedSession(this.context)?.label ?? "Session" }
      : getSelectedSession(this.context);
    if (!selected) {
      this.transcript = { ...EMPTY_TRANSCRIPT };
      await this.postTranscript();
      return;
    }

    const config = await readServerConfig(this.context);
    if (!config.token) {
      this.transcript = { ...EMPTY_TRANSCRIPT };
      await this.postTranscript();
      return;
    }

    try {
      const client = new OdysseusApiClient(config);
      const history = await client.getHistory(selected.sessionId);
      this.transcript = {
        sessionId: selected.sessionId,
        entries: mapHistoryToTranscript(history),
      };
      await this.postTranscript();
    } catch (error) {
      const detail = error instanceof Error ? error.message : String(error);
      this.outputChannel.appendLine(`[${new Date().toISOString()}] History load failed: ${detail}`);
      await this.notify("error", "Could not load session history.");
    }
  }

  private appendTranscriptEntry(entry: TranscriptEntry): void {
    this.transcript = {
      sessionId: this.transcript.sessionId,
      entries: [...this.transcript.entries, entry],
    };
  }

  private appendTranscriptMessage(message: TranscriptMessage): void {
    this.appendTranscriptEntry(message);
  }

  private appendTranscriptDelta(messageId: string, delta: string): void {
    this.transcript = {
      sessionId: this.transcript.sessionId,
      entries: this.transcript.entries.map((entry) =>
        entry.kind === "message" && entry.id === messageId
          ? { ...entry, content: entry.content + delta }
          : entry,
      ),
    };
  }

  private markTranscriptMessageDone(messageId: string, detail?: string | null): void {
    this.transcript = {
      sessionId: this.transcript.sessionId,
      entries: this.transcript.entries.map((entry) =>
        entry.kind === "message" && entry.id === messageId
          ? { ...entry, detail: detail ?? entry.detail ?? null, status: "done" }
          : entry,
      ),
    };
  }

  private markTranscriptMessageError(messageId: string, error: string): void {
    this.transcript = {
      sessionId: this.transcript.sessionId,
      entries: this.transcript.entries.map((entry) =>
        entry.kind === "message" && entry.id === messageId
          ? { ...entry, detail: error, status: "error" }
          : entry,
      ),
    };
  }

  private createAgentStep(round: number | null): AgentStepEntry {
    return {
      kind: "agentStep",
      id: makeId("step"),
      round,
      title: round ? `Agent Step ${round}` : "Agent Step",
      detail: "Planning and tool execution",
      status: "running",
      tools: [],
    };
  }

  private ensureAgentStep(state: LiveAgentState): { step: AgentStepEntry; created: boolean } {
    if (state.currentStepId) {
      const existing = this.findAgentStep(state.currentStepId);
      if (existing) {
        return { step: existing, created: false };
      }
    }

    const step = this.createAgentStep(state.currentRound);
    state.currentStepId = step.id;
    this.appendTranscriptEntry(step);
    return { step, created: true };
  }

  private findAgentStep(stepId: string): AgentStepEntry | null {
    for (const entry of this.transcript.entries) {
      if (entry.kind === "agentStep" && entry.id === stepId) {
        return entry;
      }
    }
    return null;
  }

  private appendAgentTool(stepId: string, tool: AgentToolEntry): void {
    this.transcript = {
      sessionId: this.transcript.sessionId,
      entries: this.transcript.entries.map((entry) =>
        entry.kind === "agentStep" && entry.id === stepId
          ? { ...entry, tools: [...entry.tools, tool], status: "running" }
          : entry,
      ),
    };
  }

  private updateAgentToolProgress(stepId: string, toolId: string, tail: string): void {
    this.transcript = {
      sessionId: this.transcript.sessionId,
      entries: this.transcript.entries.map((entry) =>
        entry.kind === "agentStep" && entry.id === stepId
          ? {
              ...entry,
              tools: entry.tools.map((tool) =>
                tool.id === toolId
                  ? {
                      ...tool,
                      tail,
                    }
                  : tool,
              ),
            }
          : entry,
      ),
    };
  }

  private completeAgentTool(
    stepId: string,
    toolId: string | null,
    payload: Record<string, unknown>,
  ): AgentToolEntry | null {
    const fallback = createAgentToolFromStart(payload);
    const diff = readToolDiff(payload.diff);
    const exitCode = typeof payload.exit_code === "number" ? payload.exit_code : null;
    const completed: AgentToolEntry = {
      ...fallback,
      id: toolId ?? fallback.id,
      output: typeof payload.output === "string" ? payload.output : "",
      tail: null,
      diff,
      screenshotUrl: typeof payload.screenshot === "string" ? payload.screenshot : null,
      status: exitCode === null || exitCode === 0 ? "done" : "error",
      exitCode,
    };

    let matched = false;
    this.transcript = {
      sessionId: this.transcript.sessionId,
      entries: this.transcript.entries.map((entry) => {
        if (entry.kind !== "agentStep" || entry.id !== stepId) {
          return entry;
        }
        const tools = entry.tools.map((tool) => {
          if (tool.id !== completed.id) {
            return tool;
          }
          matched = true;
          return {
            ...tool,
            command: completed.command,
            output: completed.output,
            tail: tool.tail,
            diff: completed.diff,
            screenshotUrl: completed.screenshotUrl,
            status: completed.status,
            exitCode: completed.exitCode,
          };
        });
        return {
          ...entry,
          tools: matched ? tools : [...tools, completed],
          status: completed.status === "error" ? "error" : entry.status,
        };
      }),
    };

    return completed;
  }

  private finalizeAgentStep(state: LiveAgentState, status: AgentStepEntry["status"]): void {
    if (!state.currentStepId) {
      return;
    }
    this.setAgentStepStatus(state.currentStepId, status);
    void this.postStreamUpdate({ type: "agentStepStatus", stepId: state.currentStepId, status });
    state.currentStepId = null;
    state.currentToolId = null;
  }

  private setAgentStepStatus(stepId: string, status: AgentStepEntry["status"]): void {
    this.transcript = {
      sessionId: this.transcript.sessionId,
      entries: this.transcript.entries.map((entry) =>
        entry.kind === "agentStep" && entry.id === stepId
          ? {
              ...entry,
              status: entry.status === "error" ? "error" : status,
            }
          : entry,
      ),
    };
  }

  private markLatestAgentStepError(detail: string): void {
    for (let index = this.transcript.entries.length - 1; index >= 0; index -= 1) {
      const entry = this.transcript.entries[index];
      if (entry.kind !== "agentStep") {
        continue;
      }
      this.transcript = {
        sessionId: this.transcript.sessionId,
        entries: this.transcript.entries.map((candidate) =>
          candidate.kind === "agentStep" && candidate.id === entry.id
            ? { ...candidate, status: "error", detail }
            : candidate,
        ),
      };
      void this.postStreamUpdate({ type: "agentStepStatus", stepId: entry.id, status: "error" });
      return;
    }
  }

  private async postTranscript(): Promise<void> {
    await this.postMessage({ type: "transcript", transcript: this.transcript });
  }

  private async postDraft(text: string): Promise<void> {
    await this.postMessage({ type: "draft", text });
  }

  private async postStreamUpdate(update: StreamUpdate): Promise<void> {
    await this.postMessage({ type: "stream", update });
  }

  private async postMessage(message: HostToWebviewMessage): Promise<void> {
    if (!this.view) {
      return;
    }
    await this.view.webview.postMessage(message);
  }

  private upsertPendingContext(context: PendingContext): void {
    this.pendingContexts = [
      ...this.pendingContexts.filter((item) => item.id !== context.id),
      context,
    ];
  }

  private async runConnectionValidation(): Promise<ConnectionSnapshot> {
    const config = await readServerConfig(this.context);
    if (!config.token) {
      this.memoryWritable = false;
      const snapshot = this.applyConnectionSnapshot({
        status: "not_configured",
        label: "Needs token",
        detail: "Configure an Odysseus server URL and API token.",
        version: null,
      });
      await this.refresh({ validateConnection: false });
      return snapshot;
    }

    this.applyConnectionSnapshot({
      status: "checking",
      label: "Checking",
      detail: "Validating token and capabilities with Odysseus.",
      version: this.connection.version,
    });
    await this.refresh({ validateConnection: false });

    try {
      const result = await new OdysseusApiClient(config).probeConnection();
      this.memoryWritable = capabilityMemoryWritable(result);
      const snapshot = this.applyConnectionSnapshot({
        status: "connected",
        label: "Connected",
        detail: this.describeCapabilities(result),
        version: result.version,
      });
      this.logSuccessfulConnection(config.serverUrl, result);
      await this.refresh({ validateConnection: false });
      return snapshot;
    } catch (error) {
      this.memoryWritable = false;
      const snapshot = this.applyConnectionSnapshot(this.mapConnectionError(error));
      this.logFailedConnection(config.serverUrl, error);
      await this.refresh({ validateConnection: false });
      return snapshot;
    }
  }

  private applyConnectionSnapshot(snapshot: ConnectionSnapshot): ConnectionSnapshot {
    this.connection = snapshot;
    return snapshot;
  }

  private describeCapabilities(result: ConnectionProbeResult): string {
    const scopes = result.capabilities.token_scopes.length
      ? result.capabilities.token_scopes.join(", ")
      : "none";
    return `Token scopes: ${scopes}`;
  }

  private mapConnectionError(error: unknown): ConnectionSnapshot {
    if (error instanceof OdysseusRequestError) {
      switch (error.kind) {
        case "missing_token":
          return {
            status: "not_configured",
            label: "Needs token",
            detail: "Configure an Odysseus API token to continue.",
            version: null,
          };
        case "auth":
          return {
            status: "auth_failed",
            label: "Auth failed",
            detail: "Odysseus rejected the token. Reconfigure or mint a new one.",
            version: this.connection.version,
          };
        case "network":
          return {
            status: "offline",
            label: "Offline",
            detail: error.message,
            version: null,
          };
        case "server":
          return {
            status: "server_error",
            label: "Backend error",
            detail: "Odysseus responded with a server error. Check the backend logs.",
            version: this.connection.version,
          };
        default:
          return {
            status: "server_error",
            label: "Request failed",
            detail: error.message,
            version: this.connection.version,
          };
      }
    }

    const detail = error instanceof Error ? error.message : String(error);
    return {
      status: "server_error",
      label: "Request failed",
      detail,
      version: this.connection.version,
    };
  }

  private logSuccessfulConnection(serverUrl: string, result: ConnectionProbeResult): void {
    this.outputChannel.appendLine(`[${new Date().toISOString()}] Connected to ${serverUrl}`);
    this.outputChannel.appendLine(`Version: ${result.version ?? "unavailable"}`);
    this.outputChannel.appendLine(`Capabilities: ${JSON.stringify(result.capabilities, null, 2)}`);
  }

  private logFailedConnection(serverUrl: string, error: unknown): void {
    const detail = error instanceof Error ? error.message : String(error);
    this.outputChannel.appendLine(
      `[${new Date().toISOString()}] Connection failed for ${serverUrl}`,
    );
    this.outputChannel.appendLine(detail);
  }

  private renderWebview(webview: vscode.Webview): string {
    const template = fs.readFileSync(
      path.join(this.extensionUri.fsPath, "webview", "index.html"),
      "utf8",
    );
    const stylesUri = webview.asWebviewUri(
      vscode.Uri.joinPath(this.extensionUri, "webview", "styles.css"),
    );
    const scriptUri = webview.asWebviewUri(
      vscode.Uri.joinPath(this.extensionUri, "dist", "webview.js"),
    );
    const nonce = createNonce();

    return template
      .replaceAll("{{cspSource}}", webview.cspSource)
      .replaceAll("{{stylesUri}}", stylesUri.toString())
      .replaceAll("{{scriptUri}}", scriptUri.toString())
      .replaceAll("{{nonce}}", nonce);
  }
}

function mapHistoryToTranscript(history: OdysseusHistoryResponse): TranscriptEntry[] {
  const entries: TranscriptEntry[] = [];

  for (const entry of history.history) {
    const metadata = isRecord(entry.metadata) ? entry.metadata : {};
    const toolEvents = Array.isArray(metadata.tool_events) ? metadata.tool_events : [];
    const groupedToolEvents = groupToolEventsByRound(toolEvents);

    for (const roundGroup of groupedToolEvents) {
      const tools = roundGroup.events.map((toolEvent) => createAgentToolFromHistory(toolEvent));
      const hasError = tools.some((tool) => tool.status === "error");
      entries.push({
        kind: "agentStep",
        id: makeId("step"),
        round: roundGroup.round,
        title: roundGroup.round ? `Agent Step ${roundGroup.round}` : "Agent Tools",
        detail: "Loaded from saved session history",
        status: hasError ? "error" : "done",
        tools,
      });
    }

    if (!entry.content && !toolEvents.length) {
      continue;
    }

    entries.push({
      kind: "message",
      id: makeId(entry.role),
      role: normalizeRole(entry.role),
      content: entry.content || "",
      detail: summarizeHistoryMetadataDetail(metadata, entry.role),
      status: "done",
    });
  }

  return entries;
}

function normalizeRole(value: string): "user" | "assistant" | "tool" | "system" {
  if (value === "user" || value === "assistant" || value === "tool") {
    return value;
  }
  return "system";
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

function safeJsonParse(value: string): Record<string, unknown> | null {
  try {
    const parsed = JSON.parse(value) as unknown;
    return isRecord(parsed) ? parsed : null;
  } catch {
    return null;
  }
}

function makeId(prefix: string): string {
  return `${prefix}-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`;
}

function buildPromptWithContexts(message: string, contexts: PendingContext[]): string {
  if (!contexts.length) {
    return message;
  }
  const contextBlock = contexts
    .map((context, index) => `Context ${index + 1} - ${context.label}\n${context.content}`)
    .join("\n\n");
  return `${contextBlock}\n\nUser request:\n${message}`;
}

function summarizePendingContexts(contexts: PendingContext[]): string {
  return contexts.map((context) => context.label).join(", ");
}

function summarizeOutgoingMessageDetails(
  contexts: PendingContext[],
  attachments: PendingAttachmentState[],
): string | null {
  const parts: string[] = [];
  if (contexts.length) {
    parts.push(`Contexts: ${summarizePendingContexts(contexts)}`);
  }
  if (attachments.length) {
    parts.push(
      `Attachments: ${summarizeAttachmentLabels(attachments.map((attachment) => attachment.label))}`,
    );
  }
  return parts.length ? parts.join(" • ") : null;
}

function summarizeHistoryMetadataDetail(
  metadata: Record<string, unknown>,
  role: string,
): string | null {
  const parts: string[] = [];
  if (typeof metadata.model === "string" && metadata.model.trim()) {
    parts.push(metadata.model.trim());
  }
  if (role === "user" && Array.isArray(metadata.attachments) && metadata.attachments.length) {
    const labels = metadata.attachments
      .filter(isRecord)
      .map((attachment) => String(attachment.name ?? attachment.id ?? "attachment"))
      .filter(Boolean);
    if (labels.length) {
      parts.push(`Attachments: ${summarizeAttachmentLabels(labels)}`);
    }
  }
  return parts.length ? parts.join(" • ") : null;
}

function summarizeAttachmentLabels(labels: string[]): string {
  if (labels.length <= 2) {
    return labels.join(", ");
  }
  return `${labels.slice(0, 2).join(", ")} +${labels.length - 2} more`;
}

function createAgentToolFromStart(payload: Record<string, unknown>): AgentToolEntry {
  const toolName = typeof payload.tool === "string" ? payload.tool : "tool";
  return {
    id: makeId("tool"),
    toolName,
    label: formatToolLabel(toolName),
    command: typeof payload.command === "string" ? payload.command : null,
    output: "",
    tail: null,
    diff: null,
    screenshotUrl: null,
    status: "running",
    exitCode: null,
  };
}

function createAgentToolFromHistory(payload: Record<string, unknown>): AgentToolEntry {
  const tool = createAgentToolFromStart(payload);
  const exitCode = typeof payload.exit_code === "number" ? payload.exit_code : null;
  return {
    ...tool,
    output: typeof payload.output === "string" ? payload.output : "",
    diff: readToolDiff(payload.diff),
    status: exitCode === null || exitCode === 0 ? "done" : "error",
    exitCode,
  };
}

function groupToolEventsByRound(
  toolEvents: unknown[],
): Array<{ round: number | null; events: Record<string, unknown>[] }> {
  const rounds = new Map<number | null, Record<string, unknown>[]>();
  for (const toolEvent of toolEvents) {
    if (!isRecord(toolEvent)) {
      continue;
    }
    const round = typeof toolEvent.round === "number" ? toolEvent.round : null;
    const bucket = rounds.get(round) ?? [];
    bucket.push(toolEvent);
    rounds.set(round, bucket);
  }

  return Array.from(rounds.entries())
    .sort((left, right) => (left[0] ?? 0) - (right[0] ?? 0))
    .map(([round, events]) => ({ round, events }));
}

function formatToolLabel(toolName: string): string {
  const normalized = toolName.replaceAll(/[_-]+/g, " ").trim();
  return normalized ? normalized.replace(/\b\w/g, (match) => match.toUpperCase()) : "Tool";
}

function readToolDiff(value: unknown): ToolDiffSummary | null {
  if (!isRecord(value) || typeof value.text !== "string") {
    return null;
  }
  return {
    file: typeof value.file === "string" ? value.file : null,
    text: value.text,
    added: typeof value.added === "number" ? value.added : 0,
    removed: typeof value.removed === "number" ? value.removed : 0,
    newFile: Boolean(value.new_file),
  };
}

function buildWorkspaceWarning(serverUrl: string, mode: OdysseusMode): string | null {
  if (mode !== "agent") {
    return null;
  }
  let local: boolean;
  try {
    local = isLocalHostname(new URL(serverUrl).hostname.toLowerCase());
  } catch {
    return null;
  }
  if (!local) {
    return "Agent tools run on the Odysseus server. If that server is remote, its filesystem may not match this VS Code workspace.";
  }
  if (!vscode.workspace.workspaceFolders?.[0]) {
    return "Open a folder in VS Code so the agent edits your project. Without one, it falls back to the server's default workspace.";
  }
  return null;
}

function isLocalHostname(host: string): boolean {
  return (
    host === "localhost" ||
    host === "127.0.0.1" ||
    host === "::1" ||
    host === "0.0.0.0" ||
    host === "host.docker.internal"
  );
}

/**
 * The agent's `workspace` path is resolved on the SERVER's filesystem. Only
 * send the open VS Code folder when the server is local (same machine, incl.
 * VS Code Remote where both live on the remote host) — otherwise the path
 * would not exist there. Multi-root workspaces use the first folder.
 */
function resolveAgentWorkspace(serverUrl: string): string | null {
  try {
    if (!isLocalHostname(new URL(serverUrl).hostname.toLowerCase())) {
      return null;
    }
  } catch {
    return null;
  }
  return vscode.workspace.workspaceFolders?.[0]?.uri.fsPath ?? null;
}

/** Short display name (folder basename) for the active agent workspace, or null. */
function agentWorkspaceLabel(serverUrl: string): string | null {
  const ws = resolveAgentWorkspace(serverUrl);
  return ws ? path.basename(ws) || ws : null;
}

async function buildFolderContext(uri: vscode.Uri): Promise<PendingContext> {
  const snapshot = await collectFolderSnapshot(uri);
  const name = path.basename(uri.fsPath || uri.path) || "Folder";
  return {
    id: `folder:${uri.toString()}`,
    label: `Folder: ${name}`,
    detail: uri.fsPath || uri.path,
    content: snapshot,
  };
}

async function collectFolderSnapshot(root: vscode.Uri): Promise<string> {
  const treeLines: string[] = [];
  const previewFiles: Array<{ uri: vscode.Uri; relativePath: string }> = [];
  const queue: Array<{ uri: vscode.Uri; relativePath: string; depth: number }> = [
    { uri: root, relativePath: "", depth: 0 },
  ];
  let visitedEntries = 0;

  while (queue.length && visitedEntries < 80) {
    const current = queue.shift();
    if (!current) {
      break;
    }
    const children = await vscode.workspace.fs.readDirectory(current.uri);
    children.sort((left, right) => {
      if (left[1] !== right[1]) {
        return left[1] === vscode.FileType.Directory ? -1 : 1;
      }
      return left[0].localeCompare(right[0]);
    });

    for (const [name, fileType] of children) {
      if (visitedEntries >= 80) {
        break;
      }
      if (shouldSkipFolderEntry(name)) {
        continue;
      }
      visitedEntries += 1;
      const relativePath = current.relativePath ? `${current.relativePath}/${name}` : name;
      const indent = "  ".repeat(Math.min(current.depth + 1, 4));
      if (fileType === vscode.FileType.Directory) {
        treeLines.push(`${indent}${name}/`);
        if (current.depth < 2) {
          queue.push({
            uri: vscode.Uri.joinPath(current.uri, name),
            relativePath,
            depth: current.depth + 1,
          });
        }
        continue;
      }

      treeLines.push(`${indent}${name}`);
      if (previewFiles.length < 6 && isLikelyTextFile(name)) {
        previewFiles.push({
          uri: vscode.Uri.joinPath(current.uri, name),
          relativePath,
        });
      }
    }
  }

  const previewSections: string[] = [];
  let remainingCharacters = 12000;
  for (const file of previewFiles) {
    if (remainingCharacters <= 0) {
      break;
    }
    const section = await readFolderPreviewFile(file.uri, file.relativePath, remainingCharacters);
    if (!section) {
      continue;
    }
    previewSections.push(section.text);
    remainingCharacters -= section.usedCharacters;
  }

  const rootLabel = root.fsPath || root.path;
  const parts = [
    `Folder snapshot from ${rootLabel}:`,
    "Tree:",
    `\`\`\`text\n${treeLines.length ? treeLines.join("\n") : "[empty folder]"}\n\`\`\``,
  ];
  if (visitedEntries >= 80) {
    parts.push("Tree output truncated after 80 entries.");
  }
  if (previewSections.length) {
    parts.push("Sample file previews:");
    parts.push(previewSections.join("\n\n"));
  }
  return parts.join("\n\n");
}

function shouldSkipFolderEntry(name: string): boolean {
  return (
    name === ".git" ||
    name === "node_modules" ||
    name === ".next" ||
    name === ".turbo" ||
    name === "dist"
  );
}

function isLikelyTextFile(name: string): boolean {
  return /\.(txt|md|py|ts|tsx|js|jsx|json|html|css|scss|yml|yaml|sh|bash|csv|xml|sql|go|rs|java|c|cpp|h|hpp|rb|php|nix)$/i.test(
    name,
  );
}

async function readFolderPreviewFile(
  uri: vscode.Uri,
  relativePath: string,
  remainingCharacters: number,
): Promise<{ text: string; usedCharacters: number } | null> {
  const stat = await vscode.workspace.fs.stat(uri);
  if (stat.size > 64 * 1024) {
    const text = `File preview skipped for ${relativePath} (larger than 64 KB).`;
    return { text, usedCharacters: text.length };
  }
  const raw = await vscode.workspace.fs.readFile(uri);
  if (looksBinary(raw)) {
    return null;
  }
  const decoded = new TextDecoder("utf-8").decode(raw).replace(/\r\n/g, "\n").trim();
  if (!decoded) {
    return null;
  }
  const budget = Math.max(400, Math.min(remainingCharacters, 2500));
  const excerpt = decoded.length > budget ? `${decoded.slice(0, budget)}\n[Truncated]` : decoded;
  const ext = path.extname(relativePath).replace(/^\./, "") || "text";
  const text = `File: ${relativePath}\n\`\`\`${ext}\n${excerpt}\n\`\`\``;
  return { text, usedCharacters: text.length };
}

function looksBinary(content: Uint8Array): boolean {
  const sample = content.subarray(0, Math.min(content.length, 512));
  let suspicious = 0;
  for (const byte of sample) {
    if (byte === 0) {
      return true;
    }
    if (byte < 9 || (byte > 13 && byte < 32)) {
      suspicious += 1;
    }
  }
  return sample.length > 0 && suspicious / sample.length > 0.2;
}

function inferMimeType(filename: string, kind: PendingAttachment["kind"]): string {
  const normalized = filename.toLowerCase();
  if (kind === "image") {
    if (normalized.endsWith(".png")) {
      return "image/png";
    }
    if (normalized.endsWith(".webp")) {
      return "image/webp";
    }
    if (normalized.endsWith(".gif")) {
      return "image/gif";
    }
    return "image/jpeg";
  }
  if (normalized.endsWith(".md")) {
    return "text/markdown";
  }
  if (normalized.endsWith(".json")) {
    return "application/json";
  }
  if (normalized.endsWith(".csv")) {
    return "text/csv";
  }
  if (normalized.endsWith(".pdf")) {
    return "application/pdf";
  }
  if (normalized.endsWith(".txt")) {
    return "text/plain";
  }
  return "application/octet-stream";
}

function formatBytes(bytes: number): string {
  if (bytes < 1024) {
    return `${bytes} B`;
  }
  if (bytes < 1024 * 1024) {
    return `${(bytes / 1024).toFixed(1)} KB`;
  }
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function getModelStorageKey(model: SavedModelSelection): string {
  const endpoint = model.endpointId || model.endpointUrl;
  return `${endpoint}::${model.modelId}`;
}

function resolveModelOptionValue(
  definition: ModelOptionDefinition,
  storedValues: Record<string, string>,
  context: vscode.ExtensionContext,
): string | null {
  const allowed = new Set(definition.options.map((option) => option.value));
  const storedValue = storedValues[definition.key];
  if (storedValue && allowed.has(storedValue)) {
    return storedValue;
  }
  const legacy = resolveLegacyModelOptionValue(definition, context);
  if (legacy && allowed.has(legacy)) {
    return legacy;
  }
  return allowed.has(definition.defaultValue)
    ? definition.defaultValue
    : (definition.options[0]?.value ?? null);
}

function resolveLegacyModelOptionValue(
  definition: ModelOptionDefinition,
  context: vscode.ExtensionContext,
): string | null {
  if (definition.key === "reasoning_effort") {
    const effort = context.workspaceState.get<EffortLevel>(EFFORT_STATE_KEY);
    return effort ?? null;
  }
  if (definition.key === "thinking_mode") {
    const thinking = context.workspaceState.get<ThinkingMode>(THINKING_STATE_KEY);
    if (thinking === "on" || thinking === "off") {
      return thinking;
    }
  }
  return null;
}

function createNonce(): string {
  const alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789";
  let result = "";
  for (let index = 0; index < 32; index += 1) {
    result += alphabet[Math.floor(Math.random() * alphabet.length)];
  }
  return result;
}
