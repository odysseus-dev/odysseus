import type {
  AgentApproval,
  AgentStepEntry,
  AgentToolEntry,
  ConnectionStatus,
  HostToWebviewMessage,
  ModelMenuGroup,
  ModelMenuItem,
  OdysseusMode,
  SessionMenuItem,
  SidebarState,
  StreamUpdate,
  ToolDiffSummary,
  TranscriptEntry,
  TranscriptMessage,
  TranscriptSnapshot,
  WebviewToHostMessage,
} from "../src/chat/protocol";

declare function acquireVsCodeApi(): {
  postMessage(message: unknown): void;
};

const vscode = acquireVsCodeApi();

const elements = {
  statusDot: document.querySelector<HTMLElement>("#status-dot"),
  connectionBadge: document.querySelector<HTMLElement>("#connection-badge"),
  streamingIndicator: document.querySelector<HTMLElement>("#streaming-indicator"),
  workspaceWarning: document.querySelector<HTMLElement>("#workspace-warning"),
  transcript: document.querySelector<HTMLElement>("#transcript"),
  scrollBottom: document.querySelector<HTMLButtonElement>("#scroll-bottom"),
  composerForm: document.querySelector<HTMLFormElement>("#composer-form"),
  composerInput: document.querySelector<HTMLTextAreaElement>("#composer-input"),
  composerChips: document.querySelector<HTMLElement>("#composer-chips"),
  sendButton: document.querySelector<HTMLButtonElement>("#send-button"),
  stopButton: document.querySelector<HTMLButtonElement>("#stop-button"),
  plusButton: document.querySelector<HTMLButtonElement>("#plus-button"),
  settingsButton: document.querySelector<HTMLButtonElement>("#settings-button"),
  plusMenu: document.querySelector<HTMLElement>("#plus-menu"),
  settingsMenu: document.querySelector<HTMLElement>("#settings-menu"),
  modelChip: document.querySelector<HTMLButtonElement>("#model-chip"),
  modelMenu: document.querySelector<HTMLElement>("#model-menu"),
  settingsModelBtn: document.querySelector<HTMLButtonElement>("#settings-model-btn"),
  settingsSessionBtn: document.querySelector<HTMLButtonElement>("#settings-session-btn"),
  modelChipLabel: document.querySelector<HTMLElement>("#model-chip-label"),
  sessionMenu: document.querySelector<HTMLElement>("#session-menu"),
  modePill: document.querySelector<HTMLElement>("#behavior-label"),
  behaviorButton: document.querySelector<HTMLButtonElement>("#behavior-button"),
  behaviorMenu: document.querySelector<HTMLElement>("#behavior-menu"),
  webDot: document.querySelector<HTMLElement>("#web-dot"),
  workspacePill: document.querySelector<HTMLElement>("#workspace-pill"),
  workspacePillLabel: document.querySelector<HTMLElement>("#workspace-pill-label"),
  webCheck: document.querySelector<HTMLElement>("#web-check"),
  saveBrainBtn: document.querySelector<HTMLButtonElement>("#settings-save-brain"),
  brainHint: document.querySelector<HTMLElement>("#settings-brain-hint"),
  memoryAutoSaveCheck: document.querySelector<HTMLElement>("#memory-autosave-check"),
  settingsConn: document.querySelector<HTMLElement>("#settings-conn"),
  settingsModel: document.querySelector<HTMLElement>("#settings-model"),
  settingsSession: document.querySelector<HTMLElement>("#settings-session"),
  approvalRow: document.querySelector<HTMLElement>("#approval-row"),
  modelOptionsList: document.querySelector<HTMLElement>("#model-options-list"),
  modelOptionsEmpty: document.querySelector<HTMLElement>("#model-options-empty"),
  configOverlay: document.querySelector<HTMLElement>("#config-overlay"),
  configForm: document.querySelector<HTMLFormElement>("#config-form"),
  configServerUrl: document.querySelector<HTMLInputElement>("#config-server-url"),
  configToken: document.querySelector<HTMLInputElement>("#config-token"),
  configSaveButton: document.querySelector<HTMLButtonElement>("#config-save-button"),
  configCancelButton: document.querySelector<HTMLButtonElement>("#config-cancel-button"),
  configStatus: document.querySelector<HTMLElement>("#config-status"),
  connectionDetail: document.querySelector<HTMLElement>("#connection-detail"),
  toast: document.querySelector<HTMLElement>("#toast"),
  segmented: Array.from(document.querySelectorAll<HTMLElement>(".segmented")),
  popovers: Array.from(document.querySelectorAll<HTMLElement>(".popover")),
};

let sidebarState: SidebarState | null = null;
let transcriptState: TranscriptSnapshot = { sessionId: null, entries: [] };
const openAgentSteps = new Set<string>();
let isConfigOpen = false;
let hasAutoOpenedConfig = false;
let modelCatalog: ModelMenuGroup[] = [];
let sessionCatalog: SessionMenuItem[] = [];

// Incremental transcript rendering: each top-level entry keeps a DOM node keyed
// by id plus a cheap signature of its render-relevant state, so a streamed token
// only rebuilds the one message it touches instead of the whole transcript.
const entryNodes = new Map<string, { el: HTMLElement; sig: string }>();
// Smart auto-scroll: stay pinned to the latest only while the user is already at
// the bottom; otherwise surface a "jump to latest" affordance instead of yanking
// their scroll position back down on every delta.
let isPinnedToBottom = true;
// The trigger that opened the current popover, so Escape can return focus to it.
let lastPopoverTrigger: HTMLElement | null = null;

wireButtons();
window.addEventListener("message", (event: MessageEvent<HostToWebviewMessage>) => {
  const message = event.data;
  if (!message) {
    return;
  }

  if (message.type === "state") {
    sidebarState = message.state;
    renderState(message.state);
  } else if (message.type === "openConfig") {
    openConfigForm();
  } else if (message.type === "toast") {
    showToast(message.message, message.level === "error");
  } else if (message.type === "transcript") {
    transcriptState = message.transcript;
    // A freshly loaded session should land at the newest message.
    isPinnedToBottom = true;
    renderTranscript();
  } else if (message.type === "stream") {
    applyStreamUpdate(message.update);
  } else if (message.type === "draft") {
    if (elements.composerInput) {
      elements.composerInput.value = message.text;
      autosizeComposer();
      elements.composerInput.focus();
    }
  } else if (message.type === "modelCatalog") {
    modelCatalog = message.groups;
    renderModelMenu(message.groups);
  } else if (message.type === "sessionCatalog") {
    sessionCatalog = message.items;
    renderSessionMenu(message.items);
  }
});

vscode.postMessage({ type: "ready" });

function post(message: WebviewToHostMessage): void {
  vscode.postMessage(message);
}

function wireButtons(): void {
  // Generic command buttons (menu items, model chip, connection pill).
  for (const button of Array.from(document.querySelectorAll<HTMLButtonElement>("[data-command]"))) {
    button.addEventListener("click", () => {
      const command = button.dataset.command;
      if (!command) {
        return;
      }
      closeAllPopovers();
      if (command === "configure") {
        openConfigForm();
        return;
      }
      // Dynamic dispatch for no-payload commands (attach/pick/usage/etc.).
      vscode.postMessage({ type: command });
    });
  }

  // Static segmented controls (mode / approval). Model-specific option rows
  // are rendered dynamically and wire their own handlers.
  for (const group of elements.segmented) {
    const setting = group.dataset.setting;
    for (const button of Array.from(
      group.querySelectorAll<HTMLButtonElement>("button[data-value]"),
    )) {
      button.addEventListener("click", () => {
        const value = button.dataset.value;
        if (setting && value) {
          postSetting(setting, value);
        }
      });
    }
  }

  // Popover triggers.
  elements.plusButton?.addEventListener("click", (event) => {
    event.stopPropagation();
    togglePopover(elements.plusMenu, elements.plusButton);
  });
  elements.settingsButton?.addEventListener("click", (event) => {
    event.stopPropagation();
    togglePopover(elements.settingsMenu, elements.settingsButton);
  });
  elements.behaviorButton?.addEventListener("click", (event) => {
    event.stopPropagation();
    togglePopover(elements.behaviorMenu, elements.behaviorButton);
  });

  // Model picker → in-panel dropdown anchored to the model chip.
  elements.modelChip?.addEventListener("click", (event) => {
    event.stopPropagation();
    toggleModelMenu();
  });
  elements.settingsModelBtn?.addEventListener("click", (event) => {
    event.stopPropagation();
    openModelMenu();
  });
  elements.settingsSessionBtn?.addEventListener("click", (event) => {
    event.stopPropagation();
    openSessionMenu();
  });

  // Dismiss popovers on outside click / Escape.
  document.addEventListener("click", (event) => {
    const target = event.target as HTMLElement | null;
    if (!target || !target.closest(".tool-group")) {
      closeAllPopovers();
    }
  });
  document.addEventListener("keydown", (event) => {
    const openMenu = getOpenPopover();
    if (event.key === "Escape") {
      if (openMenu) {
        event.preventDefault();
        closeAllPopovers(true);
      }
      return;
    }
    if (!openMenu) {
      return;
    }
    // Let printable keys fall through to the menu's search box (if any) so users
    // can start filtering without first clicking into the field.
    const search = openMenu.querySelector<HTMLInputElement>(".model-menu-search");
    if (
      search &&
      event.key.length === 1 &&
      event.key !== " " &&
      !event.ctrlKey &&
      !event.metaKey &&
      !event.altKey &&
      document.activeElement !== search
    ) {
      search.focus();
      return;
    }
    if (event.key === "ArrowDown") {
      event.preventDefault();
      moveMenuFocus(openMenu, 1);
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      moveMenuFocus(openMenu, -1);
    } else if (event.key === "Home") {
      event.preventDefault();
      menuFocusables(openMenu)[0]?.focus();
    } else if (event.key === "End") {
      event.preventDefault();
      const items = menuFocusables(openMenu);
      items[items.length - 1]?.focus();
    }
  });

  elements.composerForm?.addEventListener("submit", (event) => {
    event.preventDefault();
    if (!elements.composerInput) {
      return;
    }
    const message = elements.composerInput.value.trim();
    if (!message) {
      return;
    }
    // A response is still streaming — keep the draft (and focus) so the user can
    // send it the moment the current turn finishes, rather than getting an error.
    if (sidebarState?.isStreaming) {
      return;
    }
    post({ type: "sendPrompt", message });
    elements.composerInput.value = "";
    autosizeComposer();
    // Sending always jumps back to the newest message, even if the user had
    // scrolled up while reading earlier history.
    isPinnedToBottom = true;
  });

  elements.stopButton?.addEventListener("click", () => {
    post({ type: "stop" });
  });

  elements.configForm?.addEventListener("submit", (event) => {
    event.preventDefault();
    const serverUrl = elements.configServerUrl?.value.trim() ?? "";
    const token = elements.configToken?.value ?? "";
    post({ type: "saveConfig", serverUrl, token });
    if (elements.configSaveButton) {
      elements.configSaveButton.disabled = true;
      elements.configSaveButton.textContent = "Saving…";
    }
  });
  elements.configCancelButton?.addEventListener("click", () => {
    closeConfigForm();
  });

  elements.composerInput?.addEventListener("input", autosizeComposer);
  elements.composerInput?.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      elements.composerForm?.requestSubmit();
    }
  });

  // Smart auto-scroll: track whether the user is parked at the bottom so streamed
  // deltas only stick when they already were, and reveal the jump button otherwise.
  elements.transcript?.addEventListener("scroll", () => {
    isPinnedToBottom = isNearBottom();
    updateScrollAffordance();
  });
  elements.scrollBottom?.addEventListener("click", () => {
    scrollToBottom();
    elements.transcript?.focus();
  });

  // Expose the static menu actions to assistive tech as proper menu items.
  for (const item of Array.from(
    document.querySelectorAll<HTMLElement>(".popover[role='menu'] .menu-item"),
  )) {
    item.setAttribute("role", "menuitem");
  }
}

function postSetting(setting: string, value: string): void {
  switch (setting) {
    case "mode":
      post({ type: "setMode", mode: value as OdysseusMode });
      break;
    case "approval":
      post({ type: "setAgentApproval", approval: value as AgentApproval });
      break;
    default:
      break;
  }
}

function togglePopover(menu: HTMLElement | null, trigger: HTMLElement | null): void {
  if (!menu) {
    return;
  }
  const willOpen = menu.hidden;
  closeAllPopovers();
  if (!willOpen) {
    return;
  }
  menu.hidden = false;
  trigger?.classList.add("is-active");
  trigger?.setAttribute("aria-expanded", "true");
  lastPopoverTrigger = trigger ?? null;
  keepPopoverInView(menu);
  focusFirstMenuItem(menu);
}

function getOpenPopover(): HTMLElement | null {
  return elements.popovers.find((popover) => !popover.hidden) ?? null;
}

function menuFocusables(menu: HTMLElement): HTMLElement[] {
  const selector =
    "input, button:not([disabled]), [role='option'], [tabindex]:not([tabindex='-1'])";
  return Array.from(menu.querySelectorAll<HTMLElement>(selector)).filter(
    (el) => el.offsetParent !== null || el === document.activeElement,
  );
}

function focusFirstMenuItem(menu: HTMLElement): void {
  menuFocusables(menu)[0]?.focus();
}

function moveMenuFocus(menu: HTMLElement, delta: number): void {
  const items = menuFocusables(menu);
  if (!items.length) {
    return;
  }
  const current = items.indexOf(document.activeElement as HTMLElement);
  const next =
    current < 0
      ? delta > 0
        ? 0
        : items.length - 1
      : (current + delta + items.length) % items.length;
  items[next]?.focus();
}

// Popovers are anchored to their trigger's left edge. On a narrow sidebar the
// wider settings menu can spill past the right edge — nudge it back in.
function keepPopoverInView(menu: HTMLElement): void {
  menu.style.left = "0px";
  const rect = menu.getBoundingClientRect();
  const overflow = rect.right - (window.innerWidth - 8);
  if (overflow > 0) {
    menu.style.left = `${-Math.min(overflow, rect.left - 8)}px`;
  }
}

function closeAllPopovers(restoreFocus = false): void {
  let wasOpen = false;
  for (const popover of elements.popovers) {
    if (!popover.hidden) {
      wasOpen = true;
    }
    popover.hidden = true;
    popover.style.left = "";
  }
  for (const trigger of [
    elements.plusButton,
    elements.settingsButton,
    elements.modelChip,
    elements.behaviorButton,
  ]) {
    trigger?.classList.remove("is-active");
    trigger?.setAttribute("aria-expanded", "false");
  }
  if (restoreFocus && wasOpen) {
    lastPopoverTrigger?.focus();
  }
  lastPopoverTrigger = null;
}

function openModelMenu(): void {
  if (!elements.modelMenu) {
    return;
  }
  closeAllPopovers();
  elements.modelMenu.innerHTML = '<div class="model-menu-empty">Loading models…</div>';
  elements.modelMenu.hidden = false;
  elements.modelChip?.classList.add("is-active");
  elements.modelChip?.setAttribute("aria-expanded", "true");
  lastPopoverTrigger = elements.modelChip;
  keepPopoverInView(elements.modelMenu);
  post({ type: "openModelMenu" });
}

function openSessionMenu(): void {
  if (!elements.sessionMenu) {
    return;
  }
  closeAllPopovers();
  elements.sessionMenu.innerHTML = '<div class="model-menu-empty">Loading sessions…</div>';
  elements.sessionMenu.hidden = false;
  elements.settingsButton?.classList.add("is-active");
  lastPopoverTrigger = elements.settingsButton;
  keepPopoverInView(elements.sessionMenu);
  post({ type: "openSessionMenu" });
}

function toggleModelMenu(): void {
  if (elements.modelMenu && !elements.modelMenu.hidden) {
    closeAllPopovers();
    return;
  }
  openModelMenu();
}

function renderModelMenu(groups: ModelMenuGroup[]): void {
  const menu = elements.modelMenu;
  if (!menu || menu.hidden) {
    return;
  }
  menu.innerHTML = "";
  if (!groups.length) {
    const empty = document.createElement("div");
    empty.className = "model-menu-empty";
    empty.textContent = "No models available for this token.";
    menu.appendChild(empty);
    keepPopoverInView(menu);
    return;
  }
  const { input, results } = buildSearchShell("Search models…");
  menu.append(input, results);
  input.addEventListener("input", () => {
    renderModelMenuResults(results, modelCatalog, input.value);
  });
  renderModelMenuResults(results, groups, "");
  keepPopoverInView(menu);
  input.focus();
}

function renderModelMenuResults(
  container: HTMLElement,
  groups: ModelMenuGroup[],
  query: string,
): void {
  const normalizedQuery = normalizeSearchQuery(query);
  container.innerHTML = "";
  let rendered = 0;
  for (const group of groups) {
    const items = group.items.filter((item) =>
      matchesModelQuery(item, group.title, normalizedQuery),
    );
    if (!items.length) {
      continue;
    }
    const title = document.createElement("div");
    title.className = "model-group-title";
    title.textContent = group.title;
    container.appendChild(title);
    for (const item of items) {
      container.appendChild(buildModelItem(item));
      rendered += 1;
    }
  }
  if (!rendered) {
    const empty = document.createElement("div");
    empty.className = "model-menu-empty";
    empty.textContent = "No matching models.";
    container.appendChild(empty);
  }
}

function buildModelItem(item: ModelMenuItem): HTMLElement {
  const button = document.createElement("button");
  button.type = "button";
  button.className = item.selected ? "model-item is-selected" : "model-item";
  button.setAttribute("role", "option");
  button.setAttribute("aria-selected", item.selected ? "true" : "false");

  const check = document.createElement("span");
  check.className = "model-item-check";
  check.textContent = item.selected ? "✓" : "";

  const body = document.createElement("div");
  body.className = "model-item-body";

  const label = document.createElement("span");
  label.className = "model-item-label";
  label.textContent = item.selection.label;

  const detail = document.createElement("span");
  detail.className = "model-item-detail";
  detail.textContent = `${item.selection.providerLabel} • ${item.selection.sourceLabel}`;

  body.append(label, detail);
  button.append(check, body);
  button.addEventListener("click", () => {
    post({ type: "selectModel", selection: item.selection });
    closeAllPopovers();
  });
  return button;
}

function renderSessionMenu(items: SessionMenuItem[]): void {
  const menu = elements.sessionMenu;
  if (!menu || menu.hidden) {
    return;
  }
  menu.innerHTML = "";
  if (!items.length) {
    const empty = document.createElement("div");
    empty.className = "model-menu-empty";
    empty.textContent = "No sessions available yet.";
    menu.appendChild(empty);
    keepPopoverInView(menu);
    return;
  }
  const { input, results } = buildSearchShell("Search sessions…");
  menu.append(input, results);
  input.addEventListener("input", () => {
    renderSessionMenuResults(results, sessionCatalog, input.value);
  });
  renderSessionMenuResults(results, items, "");
  keepPopoverInView(menu);
  input.focus();
}

function renderSessionMenuResults(
  container: HTMLElement,
  items: SessionMenuItem[],
  query: string,
): void {
  const normalizedQuery = normalizeSearchQuery(query);
  const visibleItems = items.filter((item) => matchesSessionQuery(item, normalizedQuery));
  container.innerHTML = "";
  if (!visibleItems.length) {
    const empty = document.createElement("div");
    empty.className = "model-menu-empty";
    empty.textContent = "No matching sessions.";
    container.appendChild(empty);
    return;
  }
  for (const item of visibleItems) {
    container.appendChild(buildSessionItem(item));
  }
}

function buildSessionItem(item: SessionMenuItem): HTMLElement {
  const button = document.createElement("button");
  button.type = "button";
  button.className = item.selected ? "model-item is-selected" : "model-item";
  button.setAttribute("role", "option");
  button.setAttribute("aria-selected", item.selected ? "true" : "false");

  const check = document.createElement("span");
  check.className = "model-item-check";
  check.textContent = item.selected ? "✓" : "";

  const body = document.createElement("div");
  body.className = "model-item-body";

  const label = document.createElement("span");
  label.className = "model-item-label";
  label.textContent = item.label;

  const detail = document.createElement("span");
  detail.className = "model-item-detail";
  detail.textContent = item.detail || item.sessionId;

  body.append(label, detail);
  button.append(check, body);
  button.addEventListener("click", () => {
    post({ type: "selectSession", sessionId: item.sessionId, label: item.label });
    closeAllPopovers();
  });
  return button;
}

function buildSearchShell(placeholder: string): { input: HTMLInputElement; results: HTMLElement } {
  const input = document.createElement("input");
  input.type = "search";
  input.className = "model-menu-search";
  input.placeholder = placeholder;
  input.setAttribute("aria-label", placeholder);
  input.addEventListener("click", (event) => event.stopPropagation());
  input.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      closeAllPopovers();
    }
  });

  const results = document.createElement("div");
  results.className = "model-menu-results";
  return { input, results };
}

function matchesModelQuery(item: ModelMenuItem, groupTitle: string, query: string): boolean {
  if (!query) {
    return true;
  }
  const haystack = [
    groupTitle,
    item.selection.label,
    item.selection.modelId,
    item.selection.providerLabel,
    item.selection.endpointName,
    item.selection.sourceLabel,
    item.selection.detail,
  ]
    .filter(Boolean)
    .join(" ")
    .toLowerCase();
  return haystack.includes(query);
}

function matchesSessionQuery(item: SessionMenuItem, query: string): boolean {
  if (!query) {
    return true;
  }
  return [item.label, item.detail, item.sessionId].join(" ").toLowerCase().includes(query);
}

function normalizeSearchQuery(query: string): string {
  return query.trim().toLowerCase();
}

function renderState(state: SidebarState): void {
  setConnState(elements.statusDot, state.connectionStatus);
  setConnState(elements.connectionBadge, state.connectionStatus);
  setConnState(elements.settingsConn, state.connectionStatus);
  setConnState(elements.configStatus, state.connectionStatus);

  if (elements.connectionBadge) {
    elements.connectionBadge.textContent = state.connectionLabel;
  }
  if (elements.settingsConn) {
    elements.settingsConn.textContent = state.connectionLabel;
  }
  if (elements.configStatus) {
    elements.configStatus.textContent = state.connectionLabel;
  }
  if (elements.connectionDetail) {
    elements.connectionDetail.textContent =
      state.connectionDetail ?? "Configure Odysseus to continue.";
  }

  syncConfigForm(state);
  if (isConfigOpen && state.connectionStatus === "connected") {
    closeConfigForm();
  }
  if (!state.configured && !hasAutoOpenedConfig && !isConfigOpen) {
    hasAutoOpenedConfig = true;
    openConfigForm();
  }

  if (elements.workspaceWarning) {
    elements.workspaceWarning.hidden = !state.workspaceWarning;
    elements.workspaceWarning.textContent = state.workspaceWarning ?? "";
  }

  if (elements.modelChipLabel) {
    elements.modelChipLabel.textContent = state.selectedModelLabel ?? "No model";
  }
  if (elements.modePill) {
    elements.modePill.textContent = state.mode;
  }
  if (elements.workspacePill && elements.workspacePillLabel) {
    const wsLabel = state.mode === "agent" ? state.workspaceLabel : null;
    elements.workspacePill.hidden = !wsLabel;
    elements.workspacePillLabel.textContent = wsLabel ?? "";
    if (wsLabel) {
      elements.workspacePill.title = `Agent edits this folder: ${wsLabel}`;
    }
  }
  if (elements.settingsModel) {
    elements.settingsModel.textContent = state.selectedModelLabel ?? "Not selected";
  }
  if (elements.settingsSession) {
    elements.settingsSession.textContent = state.selectedSessionLabel ?? "New chat";
  }
  if (elements.webDot) {
    elements.webDot.hidden = !state.webEnabled;
  }
  if (elements.webCheck) {
    elements.webCheck.hidden = !state.webEnabled;
  }
  if (elements.memoryAutoSaveCheck) {
    elements.memoryAutoSaveCheck.hidden = !state.memoryAutoSave;
  }
  // When the token lacks the memory:write scope, dim the Save-to-Brain row and
  // hint why instead of letting it fail on click.
  if (elements.saveBrainBtn) {
    elements.saveBrainBtn.classList.toggle("is-disabled", !state.memoryWritable);
    elements.saveBrainBtn.title = state.memoryWritable
      ? "Save a memory to the Odysseus Brain"
      : "Needs an API token with the memory:write scope";
  }
  if (elements.brainHint) {
    elements.brainHint.textContent = state.memoryWritable ? "memory" : "no access";
  }
  if (elements.approvalRow) {
    elements.approvalRow.classList.toggle("is-hidden", state.mode !== "agent");
  }

  setSegmentedActive("mode", state.mode);
  setSegmentedActive("approval", state.agentApproval);
  renderModelOptions(state);

  if (elements.streamingIndicator) {
    elements.streamingIndicator.hidden = !state.isStreaming;
  }
  if (elements.sendButton) {
    elements.sendButton.hidden = state.isStreaming;
    elements.sendButton.disabled = state.isStreaming;
  }
  if (elements.stopButton) {
    elements.stopButton.hidden = !state.isStreaming;
  }
  // Keep the composer enabled while a response streams: disabling a focused
  // textarea drops keyboard focus (so the user has to click back in after every
  // turn), and an enabled box lets them draft the next message ahead of time.
  // Sending is gated in the submit handler instead.
  if (elements.composerInput) {
    elements.composerInput.disabled = false;
  }

  renderComposerChips(state);
}

function renderModelOptions(state: SidebarState): void {
  const list = elements.modelOptionsList;
  const empty = elements.modelOptionsEmpty;
  if (!list || !empty) {
    return;
  }
  list.innerHTML = "";
  const hasOptions = state.modelOptions.length > 0;
  empty.hidden = hasOptions;
  if (!hasOptions) {
    empty.textContent = state.selectedModelLabel
      ? "This model does not expose extra runtime controls yet."
      : "Pick a model with runtime controls to tune reasoning, thinking, or verbosity here.";
    return;
  }

  for (const option of state.modelOptions) {
    const row = document.createElement("div");
    row.className = "menu-row menu-row-stack";

    const meta = document.createElement("div");
    meta.className = "menu-row-meta";

    const label = document.createElement("span");
    label.className = "menu-label";
    label.textContent = option.label;
    meta.appendChild(label);

    if (option.description) {
      const description = document.createElement("span");
      description.className = "menu-copy";
      description.textContent = option.description;
      meta.appendChild(description);
    }

    const segmented = document.createElement("div");
    segmented.className = "segmented segmented-wrap";
    segmented.dataset.setting = `model-option:${option.key}`;

    for (const choice of option.options) {
      const button = document.createElement("button");
      button.type = "button";
      button.dataset.value = choice.value;
      button.textContent = choice.label;
      button.classList.toggle("is-active", choice.value === option.value);
      button.addEventListener("click", () => {
        post({ type: "setModelOption", key: option.key, value: choice.value });
      });
      segmented.appendChild(button);
    }

    row.append(meta, segmented);
    list.appendChild(row);
  }
}

function setSegmentedActive(setting: string, value: string): void {
  for (const group of elements.segmented) {
    if (group.dataset.setting !== setting) {
      continue;
    }
    for (const button of Array.from(
      group.querySelectorAll<HTMLButtonElement>("button[data-value]"),
    )) {
      button.classList.toggle("is-active", button.dataset.value === value);
    }
  }
}

function setConnState(element: HTMLElement | null, status: ConnectionStatus): void {
  if (!element) {
    return;
  }
  element.classList.remove("is-connected", "is-checking", "is-warning", "is-error");
  const cls = connStatusClass(status);
  if (cls) {
    element.classList.add(cls);
  }
}

function connStatusClass(status: ConnectionStatus): string {
  switch (status) {
    case "connected":
      return "is-connected";
    case "checking":
      return "is-checking";
    case "auth_failed":
      return "is-warning";
    case "offline":
    case "server_error":
      return "is-error";
    default:
      return "";
  }
}

function syncConfigForm(state: SidebarState): void {
  if (elements.configServerUrl && (!elements.configServerUrl.value || !isConfigOpen)) {
    elements.configServerUrl.value = state.serverUrl;
  }
  if (elements.configToken) {
    elements.configToken.placeholder = state.hasToken
      ? "Leave blank to keep the existing token"
      : "ody_…";
  }
  if (elements.configSaveButton) {
    elements.configSaveButton.disabled = false;
    elements.configSaveButton.textContent = "Save & Validate";
  }
}

function openConfigForm(): void {
  isConfigOpen = true;
  closeAllPopovers();
  elements.configOverlay?.removeAttribute("hidden");
  if (sidebarState) {
    syncConfigForm(sidebarState);
  }
  elements.configToken?.focus();
}

function closeConfigForm(): void {
  isConfigOpen = false;
  elements.configOverlay?.setAttribute("hidden", "hidden");
  if (elements.configToken) {
    elements.configToken.value = "";
  }
  if (elements.configSaveButton) {
    elements.configSaveButton.disabled = false;
    elements.configSaveButton.textContent = "Save & Validate";
  }
}

function renderComposerChips(state: SidebarState): void {
  const container = elements.composerChips;
  if (!container) {
    return;
  }
  container.innerHTML = "";
  const hasAny = state.pendingAttachments.length > 0 || state.stagedContexts.length > 0;
  container.hidden = !hasAny;
  if (!hasAny) {
    return;
  }
  for (const attachment of state.pendingAttachments) {
    container.appendChild(
      buildChip(attachment.label, attachment.kind === "image", () =>
        post({ type: "removeAttachment", id: attachment.id }),
      ),
    );
  }
  for (const context of state.stagedContexts) {
    container.appendChild(
      buildChip(context.label, false, () => post({ type: "removeContext", id: context.id })),
    );
  }
}

function buildChip(label: string, isImage: boolean, onRemove: () => void): HTMLElement {
  const chip = document.createElement("span");
  chip.className = isImage ? "chip is-image" : "chip";

  const text = document.createElement("span");
  text.className = "chip-label";
  text.textContent = label;

  const remove = document.createElement("button");
  remove.type = "button";
  remove.className = "chip-remove";
  remove.textContent = "×";
  remove.setAttribute("aria-label", `Remove ${label}`);
  remove.addEventListener("click", onRemove);

  chip.append(text, remove);
  return chip;
}

function applyStreamUpdate(update: StreamUpdate): void {
  switch (update.type) {
    case "replaceTranscript":
      transcriptState = update.transcript;
      break;
    case "session":
      transcriptState = {
        sessionId: update.sessionId,
        entries: transcriptState.entries,
      };
      if (elements.settingsSession) {
        elements.settingsSession.textContent = update.label;
      }
      break;
    case "append":
    case "assistantStart":
      transcriptState = {
        sessionId: transcriptState.sessionId,
        entries: [...transcriptState.entries, update.message],
      };
      break;
    case "assistantDelta":
      transcriptState = {
        sessionId: transcriptState.sessionId,
        entries: transcriptState.entries.map((entry) =>
          entry.kind === "message" && entry.id === update.messageId
            ? { ...entry, content: entry.content + update.delta }
            : entry,
        ),
      };
      break;
    case "assistantDone":
      transcriptState = {
        sessionId: transcriptState.sessionId,
        entries: transcriptState.entries.map((entry) =>
          entry.kind === "message" && entry.id === update.messageId
            ? { ...entry, detail: update.detail ?? entry.detail, status: "done" }
            : entry,
        ),
      };
      break;
    case "assistantError":
      transcriptState = {
        sessionId: transcriptState.sessionId,
        entries: transcriptState.entries.map((entry) =>
          entry.kind === "message" && entry.id === update.messageId
            ? { ...entry, detail: update.error, status: "error" }
            : entry,
        ),
      };
      break;
    case "agentStepStart":
      transcriptState = {
        sessionId: transcriptState.sessionId,
        entries: [...transcriptState.entries, update.step],
      };
      openAgentSteps.add(update.step.id);
      break;
    case "agentToolStart":
      transcriptState = {
        sessionId: transcriptState.sessionId,
        entries: transcriptState.entries.map((entry) =>
          entry.kind === "agentStep" && entry.id === update.stepId
            ? { ...entry, tools: [...entry.tools, update.tool], status: "running" }
            : entry,
        ),
      };
      openAgentSteps.add(update.stepId);
      break;
    case "agentToolProgress":
      transcriptState = {
        sessionId: transcriptState.sessionId,
        entries: transcriptState.entries.map((entry) =>
          entry.kind === "agentStep" && entry.id === update.stepId
            ? {
                ...entry,
                tools: entry.tools.map((tool) =>
                  tool.id === update.toolId ? { ...tool, tail: update.tail } : tool,
                ),
              }
            : entry,
        ),
      };
      break;
    case "agentToolDone":
      transcriptState = {
        sessionId: transcriptState.sessionId,
        entries: transcriptState.entries.map((entry) => {
          if (entry.kind !== "agentStep" || entry.id !== update.stepId) {
            return entry;
          }
          let matched = false;
          const tools = entry.tools.map((tool) => {
            if (tool.id !== update.tool.id) {
              return tool;
            }
            matched = true;
            return update.tool;
          });
          return {
            ...entry,
            tools: matched ? tools : [...tools, update.tool],
            status: update.tool.status === "error" ? "error" : entry.status,
          };
        }),
      };
      break;
    case "agentStepStatus":
      transcriptState = {
        sessionId: transcriptState.sessionId,
        entries: transcriptState.entries.map((entry) =>
          entry.kind === "agentStep" && entry.id === update.stepId
            ? {
                ...entry,
                status: entry.status === "error" ? "error" : update.status,
              }
            : entry,
        ),
      };
      if (update.status === "done") {
        openAgentSteps.delete(update.stepId);
      } else {
        openAgentSteps.add(update.stepId);
      }
      break;
    default:
      break;
  }

  renderTranscript();
}

// Reconcile the transcript DOM against `transcriptState` keyed by entry id: reuse
// untouched nodes, rebuild only entries whose signature changed, and drop the
// rest. A streamed token therefore re-renders a single message rather than the
// whole conversation — preserving scroll position and text selection elsewhere.
function renderTranscript(): void {
  const root = elements.transcript;
  if (!root) {
    return;
  }

  const entries = transcriptState.entries;
  if (!entries.length) {
    entryNodes.clear();
    root.innerHTML = '<div class="empty-state">Start a new chat or pick an existing session.</div>';
    updateScrollAffordance();
    return;
  }

  const placeholder = root.querySelector(".empty-state");
  if (placeholder) {
    placeholder.remove();
  }

  const seen = new Set<string>();
  let prev: HTMLElement | null = null;
  for (const entry of entries) {
    seen.add(entry.id);
    const sig = entrySignature(entry);
    let record = entryNodes.get(entry.id);
    if (!record) {
      record = { el: buildTranscriptEntry(entry), sig };
      entryNodes.set(entry.id, record);
    } else if (record.sig !== sig) {
      const next = buildTranscriptEntry(entry);
      record.el.replaceWith(next);
      record.el = next;
      record.sig = sig;
    }
    // Keep DOM order aligned with state order (entries are normally appended).
    const anchor: ChildNode | null = prev ? prev.nextSibling : root.firstChild;
    if (record.el !== anchor) {
      root.insertBefore(record.el, anchor);
    }
    prev = record.el;
  }

  for (const [id, record] of entryNodes) {
    if (!seen.has(id)) {
      record.el.remove();
      entryNodes.delete(id);
    }
  }

  maybeStickToBottom();
}

// A cheap fingerprint of everything that affects an entry's rendered output, so
// reconciliation can skip rebuilds when nothing visible changed.
function entrySignature(entry: TranscriptEntry): string {
  if (entry.kind === "message") {
    const content = entry.content;
    return `m|${entry.role}|${entry.status ?? ""}|${entry.detail ?? ""}|${entry.title ?? ""}|${content.length}|${content.slice(-32)}`;
  }
  const tools = entry.tools
    .map(
      (tool) =>
        `${tool.id}:${tool.status}:${tool.exitCode ?? ""}:${tool.command?.length ?? 0}:${tool.output.length}:${(tool.tail ?? "").length}:${tool.diff?.text.length ?? 0}:${tool.screenshotUrl ?? ""}`,
    )
    .join(";");
  return `s|${entry.status}|${entry.detail ?? ""}|${entry.title}|${entry.round ?? ""}|${tools}`;
}

function isNearBottom(): boolean {
  const el = elements.transcript;
  if (!el) {
    return true;
  }
  return el.scrollHeight - el.scrollTop - el.clientHeight < 48;
}

function maybeStickToBottom(): void {
  const el = elements.transcript;
  if (!el) {
    return;
  }
  if (isPinnedToBottom) {
    el.scrollTop = el.scrollHeight;
  }
  updateScrollAffordance();
}

function scrollToBottom(): void {
  const el = elements.transcript;
  if (!el) {
    return;
  }
  el.scrollTop = el.scrollHeight;
  isPinnedToBottom = true;
  updateScrollAffordance();
}

function updateScrollAffordance(): void {
  if (!elements.scrollBottom) {
    return;
  }
  elements.scrollBottom.hidden = isPinnedToBottom || !transcriptState.entries.length;
}

function buildTranscriptEntry(entry: TranscriptEntry): HTMLElement {
  if (entry.kind === "agentStep") {
    return buildAgentStepCard(entry);
  }
  return buildMessageCard(entry);
}

function buildMessageCard(message: TranscriptMessage): HTMLElement {
  const article = document.createElement("article");
  article.className = `message-card role-${message.role}`;
  if (message.status === "streaming") {
    article.classList.add("is-streaming");
  }
  if (message.status === "error") {
    article.classList.add("is-error");
  }

  const header = document.createElement("div");
  header.className = "message-header";

  const title = document.createElement("strong");
  title.textContent = message.title ?? labelForRole(message.role);
  header.appendChild(title);

  if (message.detail) {
    const detail = document.createElement("span");
    detail.className = "message-detail";
    detail.textContent = message.detail;
    header.appendChild(detail);
  }

  const body = document.createElement("div");
  body.className = "message-body";
  renderMarkdown(body, message.content, message);

  if (message.status === "streaming") {
    const caret = document.createElement("span");
    caret.className = "stream-caret";
    caret.setAttribute("aria-hidden", "true");
    body.appendChild(caret);
  }

  article.append(header, body);
  return article;
}

function buildAgentStepCard(step: AgentStepEntry): HTMLElement {
  const details = document.createElement("details");
  details.className = `step-card is-${step.status}`;
  details.dataset.stepId = step.id;
  details.open = openAgentSteps.has(step.id) || step.status !== "done";
  details.addEventListener("toggle", () => {
    if (details.open) {
      openAgentSteps.add(step.id);
    } else {
      openAgentSteps.delete(step.id);
    }
  });

  const summary = document.createElement("summary");
  summary.className = "step-summary";

  const heading = document.createElement("div");
  heading.className = "step-heading";

  const title = document.createElement("strong");
  title.textContent = step.title;
  heading.appendChild(title);

  const meta = document.createElement("span");
  meta.className = "step-meta";
  meta.textContent = `${step.tools.length} ${step.tools.length === 1 ? "tool" : "tools"}`;
  heading.appendChild(meta);

  const status = document.createElement("span");
  status.className = `step-status is-${step.status}`;
  status.textContent = labelForStepStatus(step.status);

  summary.append(heading, status);
  details.appendChild(summary);

  const body = document.createElement("div");
  body.className = "step-body";

  if (step.detail) {
    const detail = document.createElement("p");
    detail.className = "step-detail";
    detail.textContent = step.detail;
    body.appendChild(detail);
  }

  if (!step.tools.length) {
    const empty = document.createElement("div");
    empty.className = "step-empty";
    empty.textContent = "Waiting for the next tool call.";
    body.appendChild(empty);
  } else {
    for (const tool of step.tools) {
      body.appendChild(buildToolCard(tool));
    }
  }

  details.appendChild(body);
  return details;
}

function buildToolCard(tool: AgentToolEntry): HTMLElement {
  const article = document.createElement("article");
  article.className = `step-tool is-${tool.status}`;

  const header = document.createElement("div");
  header.className = "step-tool-header";

  const titleWrap = document.createElement("div");
  titleWrap.className = "step-tool-title";

  const title = document.createElement("strong");
  title.textContent = tool.label;
  titleWrap.appendChild(title);

  if (tool.command) {
    const commandHint = document.createElement("span");
    commandHint.className = "step-tool-command-label";
    commandHint.textContent = "command attached";
    titleWrap.appendChild(commandHint);
  }

  const status = document.createElement("span");
  status.className = `step-tool-status is-${tool.status}`;
  status.textContent = labelForToolStatus(tool);

  header.append(titleWrap, status);
  article.appendChild(header);

  if (tool.command) {
    const command = document.createElement("pre");
    command.className = "step-tool-command";
    command.textContent = tool.command;
    article.appendChild(command);
  }

  if (tool.tail) {
    article.appendChild(buildToolSection("Live output", tool.tail, "step-tool-tail"));
  }

  if (tool.output) {
    article.appendChild(buildToolSection("Output", tool.output, "step-tool-output"));
  }

  if (tool.diff) {
    article.appendChild(buildDiffDetails(tool.diff));
  }

  if (tool.screenshotUrl) {
    const image = document.createElement("img");
    image.className = "step-tool-image";
    image.src = tool.screenshotUrl;
    image.alt = `${tool.label} screenshot`;
    article.appendChild(image);
  }

  return article;
}

function buildToolSection(title: string, content: string, className: string): HTMLElement {
  const details = document.createElement("details");
  details.className = "step-tool-details";
  details.open = className === "step-tool-tail";

  const summary = document.createElement("summary");
  summary.textContent = title;

  const pre = document.createElement("pre");
  pre.className = className;
  pre.textContent = content;

  details.append(summary, pre);
  return details;
}

function buildDiffDetails(diff: ToolDiffSummary): HTMLElement {
  const details = document.createElement("details");
  details.className = "step-tool-details step-tool-diff";

  const summary = document.createElement("summary");
  const parts = [];
  if (diff.file) {
    parts.push(diff.file);
  }
  if (diff.newFile) {
    parts.push("new file");
  }
  if (diff.added) {
    parts.push(`+${diff.added}`);
  }
  if (diff.removed) {
    parts.push(`-${diff.removed}`);
  }
  summary.textContent = parts.length ? `Diff • ${parts.join(" • ")}` : "Diff";
  details.appendChild(summary);

  const pre = document.createElement("pre");
  pre.className = "step-tool-diff-pre";
  for (const line of diff.text.split("\n")) {
    const row = document.createElement("span");
    row.className = classifyDiffLine(line);
    row.textContent = line || " ";
    pre.appendChild(row);
  }
  details.appendChild(pre);
  return details;
}

function classifyDiffLine(line: string): string {
  if (line.startsWith("@@")) {
    return "diff-line is-hunk";
  }
  if (line.startsWith("+++") || line.startsWith("---")) {
    return "diff-line is-meta";
  }
  if (line.startsWith("+")) {
    return "diff-line is-add";
  }
  if (line.startsWith("-")) {
    return "diff-line is-del";
  }
  return "diff-line";
}

function labelForRole(role: TranscriptMessage["role"]): string {
  switch (role) {
    case "user":
      return "You";
    case "assistant":
      return "Odysseus";
    case "tool":
      return "Tool";
    default:
      return "System";
  }
}

function labelForStepStatus(status: AgentStepEntry["status"]): string {
  switch (status) {
    case "running":
      return "Running";
    case "error":
      return "Needs review";
    default:
      return "Done";
  }
}

function labelForToolStatus(tool: AgentToolEntry): string {
  if (tool.status === "running") {
    return "Running";
  }
  if (tool.status === "error") {
    return tool.exitCode === null ? "Failed" : `Failed (${tool.exitCode})`;
  }
  return tool.exitCode === null ? "Done" : `Done (${tool.exitCode})`;
}

// Safe markdown renderer. Everything is built from DOM nodes and textContent —
// no innerHTML on model output — so it stays CSP-clean and injection-proof while
// still rendering fenced code, headings, lists, quotes, emphasis, and links.
function renderMarkdown(container: HTMLElement, text: string, message: TranscriptMessage): void {
  container.innerHTML = "";
  if (!text) {
    return;
  }

  const codeFence = /```([\w-]+)?\n([\s\S]*?)```/g;
  let lastIndex = 0;
  let match: RegExpExecArray | null;

  while ((match = codeFence.exec(text)) !== null) {
    appendBlocks(container, text.slice(lastIndex, match.index));
    container.appendChild(buildCodeBlock(match[1] || "code", match[2].replace(/\n$/, ""), message));
    lastIndex = match.index + match[0].length;
  }

  // A fence that is still streaming (opened but not yet closed) renders live as a
  // code block instead of leaking raw backticks into a paragraph.
  const rest = text.slice(lastIndex);
  const openFence = /```([\w-]+)?\n([\s\S]*)$/.exec(rest);
  if (openFence) {
    appendBlocks(container, rest.slice(0, openFence.index));
    container.appendChild(buildCodeBlock(openFence[1] || "code", openFence[2], message));
  } else {
    appendBlocks(container, rest);
  }
}

function buildCodeBlock(lang: string, codeText: string, message: TranscriptMessage): HTMLElement {
  const codeWrap = document.createElement("div");
  codeWrap.className = "code-block";

  const top = document.createElement("div");
  top.className = "code-block-top";
  const langLabel = document.createElement("span");
  langLabel.textContent = lang;

  const copy = document.createElement("button");
  copy.type = "button";
  copy.className = "copy-button";
  copy.textContent = "Copy";
  copy.addEventListener("click", async () => {
    await navigator.clipboard.writeText(codeText);
    copy.textContent = "Copied";
    window.setTimeout(() => {
      copy.textContent = "Copy";
    }, 1200);
  });
  top.append(langLabel, buildCodeActions(codeText, message, copy));

  const pre = document.createElement("pre");
  const code = document.createElement("code");
  code.textContent = codeText;
  pre.appendChild(code);
  codeWrap.append(top, pre);
  return codeWrap;
}

const HR_RE = /^\s*([-*_])(\s*\1){2,}\s*$/;
const HEADING_RE = /^(#{1,6})\s+(.*)$/;
const QUOTE_RE = /^\s*>\s?/;
const UL_RE = /^\s*[-*+]\s+/;
const OL_RE = /^\s*\d+[.)]\s+/;

function startsBlock(line: string): boolean {
  return (
    HEADING_RE.test(line) ||
    QUOTE_RE.test(line) ||
    UL_RE.test(line) ||
    OL_RE.test(line) ||
    HR_RE.test(line)
  );
}

// Block-level markdown for the non-fenced segments between code blocks.
function appendBlocks(container: HTMLElement, source: string): void {
  const lines = source.replace(/\r\n/g, "\n").split("\n");
  let i = 0;
  while (i < lines.length) {
    const line = lines[i];
    if (!line.trim()) {
      i += 1;
      continue;
    }

    if (HR_RE.test(line)) {
      container.appendChild(document.createElement("hr"));
      i += 1;
      continue;
    }

    const heading = HEADING_RE.exec(line);
    if (heading) {
      const level = Math.min(heading[1].length, 6);
      const el = document.createElement(`h${level}`);
      el.className = "md-heading";
      appendInline(el, heading[2].trim());
      container.appendChild(el);
      i += 1;
      continue;
    }

    if (QUOTE_RE.test(line)) {
      const quoteLines: string[] = [];
      while (i < lines.length && QUOTE_RE.test(lines[i])) {
        quoteLines.push(lines[i].replace(QUOTE_RE, ""));
        i += 1;
      }
      const quote = document.createElement("blockquote");
      quote.className = "md-quote";
      appendBlocks(quote, quoteLines.join("\n"));
      container.appendChild(quote);
      continue;
    }

    if (UL_RE.test(line)) {
      const list = document.createElement("ul");
      list.className = "md-list";
      while (i < lines.length && UL_RE.test(lines[i])) {
        const item = document.createElement("li");
        appendInline(item, lines[i].replace(UL_RE, ""));
        list.appendChild(item);
        i += 1;
      }
      container.appendChild(list);
      continue;
    }

    if (OL_RE.test(line)) {
      const list = document.createElement("ol");
      list.className = "md-list";
      const first = /^\s*(\d+)[.)]\s+/.exec(line);
      if (first && first[1] !== "1") {
        list.setAttribute("start", first[1]);
      }
      while (i < lines.length && OL_RE.test(lines[i])) {
        const item = document.createElement("li");
        appendInline(item, lines[i].replace(OL_RE, ""));
        list.appendChild(item);
        i += 1;
      }
      container.appendChild(list);
      continue;
    }

    const paragraphLines: string[] = [];
    while (i < lines.length && lines[i].trim() && !startsBlock(lines[i])) {
      paragraphLines.push(lines[i]);
      i += 1;
    }
    const paragraph = document.createElement("p");
    appendInline(paragraph, paragraphLines.join("\n"));
    container.appendChild(paragraph);
  }
}

// Inline markdown. Inline code is split out first (its contents are never parsed
// further), then the remainder is handled by `appendRichText`.
function appendInline(parent: Node, text: string): void {
  const codeSpan = /(`{1,3})([^`]+?)\1/g;
  let last = 0;
  let match: RegExpExecArray | null;
  while ((match = codeSpan.exec(text)) !== null) {
    appendRichText(parent, text.slice(last, match.index));
    const code = document.createElement("code");
    code.className = "md-code";
    code.textContent = match[2];
    parent.appendChild(code);
    last = match.index + match[0].length;
  }
  appendRichText(parent, text.slice(last));
}

const RICH_RE =
  /(\*\*|__)([\s\S]+?)\1|(\*|_)([\s\S]+?)\3|~~([\s\S]+?)~~|\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/;

function appendRichText(parent: Node, text: string): void {
  let remaining = text;
  while (remaining) {
    const match = RICH_RE.exec(remaining);
    if (!match) {
      appendTextWithBreaks(parent, remaining);
      return;
    }
    if (match.index > 0) {
      appendTextWithBreaks(parent, remaining.slice(0, match.index));
    }
    if (match[1]) {
      const strong = document.createElement("strong");
      appendRichText(strong, match[2]);
      parent.appendChild(strong);
    } else if (match[3]) {
      const em = document.createElement("em");
      appendRichText(em, match[4]);
      parent.appendChild(em);
    } else if (match[5] !== undefined) {
      const del = document.createElement("del");
      appendRichText(del, match[5]);
      parent.appendChild(del);
    } else {
      const link = document.createElement("a");
      link.href = match[7];
      link.className = "md-link";
      link.rel = "noreferrer noopener";
      appendRichText(link, match[6]);
      parent.appendChild(link);
    }
    remaining = remaining.slice(match.index + match[0].length);
  }
}

function appendTextWithBreaks(parent: Node, text: string): void {
  const parts = text.split("\n");
  parts.forEach((part, index) => {
    if (index > 0) {
      parent.appendChild(document.createElement("br"));
    }
    if (part) {
      parent.appendChild(document.createTextNode(part));
    }
  });
}

function buildCodeActions(
  code: string,
  message: TranscriptMessage,
  copyButton: HTMLButtonElement,
): HTMLElement {
  const wrap = document.createElement("div");
  wrap.className = "code-actions";
  wrap.append(copyButton);

  if (message.role === "assistant") {
    const insert = document.createElement("button");
    insert.type = "button";
    insert.className = "copy-button";
    insert.textContent = "Insert";
    insert.addEventListener("click", () => {
      post({ type: "insertCode", code });
    });

    const diff = document.createElement("button");
    diff.type = "button";
    diff.className = "copy-button";
    diff.textContent = "Preview Diff";
    diff.addEventListener("click", () => {
      post({
        type: "previewDiff",
        code,
        label: message.detail ?? "Odysseus Suggestion",
      });
    });

    wrap.append(insert, diff);
  }

  return wrap;
}

function autosizeComposer(): void {
  if (!elements.composerInput) {
    return;
  }
  elements.composerInput.style.height = "auto";
  elements.composerInput.style.height = `${Math.min(elements.composerInput.scrollHeight, 200)}px`;
}

let toastTimer: number | undefined;

function showToast(message: string, isError: boolean): void {
  if (!elements.toast) {
    return;
  }

  elements.toast.hidden = false;
  elements.toast.textContent = message;
  elements.toast.classList.toggle("is-error", isError);

  if (toastTimer) {
    window.clearTimeout(toastTimer);
  }

  toastTimer = window.setTimeout(() => {
    if (!elements.toast) {
      return;
    }
    elements.toast.hidden = true;
    elements.toast.textContent = "";
    elements.toast.classList.remove("is-error");
  }, 3200);
}
