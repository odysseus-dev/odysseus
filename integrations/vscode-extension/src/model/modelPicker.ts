import * as vscode from "vscode";

import type { OdysseusModelCatalogItem } from "../api/client";
import { getConfiguredServerUrl, OdysseusApiClient, readServerConfig } from "../api/client";
import type { ModelMenuGroup, ModelMenuItem, SavedModelSelection } from "../chat/protocol";

const SELECTED_MODEL_KEY = "odysseus.selectedModel";

interface ModelQuickPickItem extends vscode.QuickPickItem {
  value: SavedModelSelection;
}

export async function pickModel(
  context: vscode.ExtensionContext,
): Promise<SavedModelSelection | null> {
  const config = await readServerConfig(context);
  if (!config.token) {
    void vscode.window.showWarningMessage("Configure an Odysseus API token first.");
    return null;
  }

  const client = new OdysseusApiClient(config);
  const catalog = await client.getModels();
  const endpoints = catalog.items.filter((endpoint) => !endpoint.offline);
  const items = endpoints.flatMap(buildEndpointQuickPickGroup);

  if (!items.length) {
    void vscode.window.showWarningMessage(
      "No Odysseus models are currently available for this token.",
    );
    return null;
  }

  const selected = await vscode.window.showQuickPick(items, {
    title: "Pick an Odysseus model",
    matchOnDescription: true,
    matchOnDetail: true,
  });

  if (!isModelQuickPickItem(selected)) {
    return null;
  }

  const scopedSelection = {
    ...selected.value,
    serverUrl: config.serverUrl,
  } satisfies SavedModelSelection;
  await context.workspaceState.update(SELECTED_MODEL_KEY, scopedSelection);
  return scopedSelection;
}

export function getSelectedModel(context: vscode.ExtensionContext): SavedModelSelection | null {
  const selection = context.workspaceState.get<SavedModelSelection>(SELECTED_MODEL_KEY) ?? null;
  if (!selection) {
    return null;
  }
  const serverUrl = getConfiguredServerUrl();
  if (selection.serverUrl && selection.serverUrl !== serverUrl) {
    return null;
  }
  return selection.serverUrl ? selection : { ...selection, serverUrl };
}

export async function clearSelectedModel(context: vscode.ExtensionContext): Promise<void> {
  await context.workspaceState.update(SELECTED_MODEL_KEY, undefined);
}

export async function setSelectedModel(
  context: vscode.ExtensionContext,
  selection: SavedModelSelection,
): Promise<void> {
  await context.workspaceState.update(SELECTED_MODEL_KEY, {
    ...selection,
    serverUrl: selection.serverUrl || getConfiguredServerUrl(),
  } satisfies SavedModelSelection);
}

export async function reconcileSelectedModelScope(
  context: vscode.ExtensionContext,
): Promise<boolean> {
  const selection = context.workspaceState.get<SavedModelSelection>(SELECTED_MODEL_KEY) ?? null;
  if (!selection) {
    return false;
  }
  const serverUrl = getConfiguredServerUrl();
  if (!selection.serverUrl) {
    await setSelectedModel(context, { ...selection, serverUrl });
    return false;
  }
  if (selection.serverUrl !== serverUrl) {
    await clearSelectedModel(context);
    return true;
  }
  return false;
}

// Same catalog as the native picker, shaped for the in-panel dropdown so the
// model list renders inside the Odysseus webview instead of a VS Code quick pick.
export async function fetchModelMenuGroups(
  context: vscode.ExtensionContext,
): Promise<ModelMenuGroup[]> {
  const config = await readServerConfig(context);
  if (!config.token) {
    return [];
  }
  const client = new OdysseusApiClient(config);
  const catalog = await client.getModels();
  const selected = getSelectedModel(context);
  return catalog.items
    .filter((endpoint) => !endpoint.offline)
    .map((endpoint) => buildModelMenuGroup(endpoint, selected))
    .filter((group) => group.items.length > 0);
}

function buildModelMenuGroup(
  endpoint: OdysseusModelCatalogItem,
  selected: SavedModelSelection | null,
): ModelMenuGroup {
  const providerLabel = formatProviderLabel(endpoint);
  const sourceLabel = formatSourceLabel(endpoint);
  const endpointName = endpoint.endpoint_name?.trim() || null;
  const title = endpointName ? `${providerLabel} • ${endpointName}` : providerLabel;
  const items = [
    ...buildModelMenuEntries(
      endpoint.models,
      endpoint.models_display,
      endpoint,
      providerLabel,
      sourceLabel,
      endpointName,
      selected,
    ),
    ...buildModelMenuEntries(
      endpoint.models_extra ?? [],
      endpoint.models_extra_display,
      endpoint,
      providerLabel,
      `${sourceLabel} • extra`,
      endpointName,
      selected,
    ),
  ];
  return { title, items };
}

function buildModelMenuEntries(
  modelIds: string[],
  modelDisplays: string[] | undefined,
  endpoint: OdysseusModelCatalogItem,
  providerLabel: string,
  sourceLabel: string,
  endpointName: string | null,
  selected: SavedModelSelection | null,
): ModelMenuItem[] {
  return modelIds.map((modelId, index) => {
    const display = modelDisplays?.[index]?.trim();
    const renderedModel = display && display !== modelId ? `${display} (${modelId})` : modelId;
    const endpointId = endpoint.endpoint_id ?? "";
    const detailParts = [
      sourceLabel,
      endpointId ? `endpoint ${endpointId}` : null,
      endpoint.url,
    ].filter((part): part is string => Boolean(part));
    const detail = detailParts.join(" • ");
    const selection: SavedModelSelection = {
      serverUrl: selected?.serverUrl || getConfiguredServerUrl(),
      endpointId,
      endpointUrl: endpoint.url,
      modelId,
      label: renderedModel,
      detail,
      endpointName,
      providerLabel,
      sourceLabel,
      modelOptions: endpoint.model_options?.[modelId] ?? [],
      modelOptionsHydrated: true,
    };
    const isSelected = Boolean(
      selected &&
      selected.endpointId === endpointId &&
      selected.endpointUrl === endpoint.url &&
      selected.modelId === modelId,
    );
    return { selection, selected: isSelected };
  });
}

function buildEndpointQuickPickGroup(
  endpoint: OdysseusModelCatalogItem,
): Array<vscode.QuickPickItem | ModelQuickPickItem> {
  const providerLabel = formatProviderLabel(endpoint);
  const sourceLabel = formatSourceLabel(endpoint);
  const endpointName = endpoint.endpoint_name?.trim() || null;
  const endpointTitle = endpointName ? `${providerLabel} • ${endpointName}` : providerLabel;

  const modelEntries = buildModelEntries(
    endpoint.models,
    endpoint.models_display,
    endpoint,
    providerLabel,
    sourceLabel,
    endpointName,
  );
  const extraEntries = buildModelEntries(
    endpoint.models_extra ?? [],
    endpoint.models_extra_display,
    endpoint,
    providerLabel,
    `${sourceLabel} • extra`,
    endpointName,
  );

  return [
    {
      label: endpointTitle,
      kind: vscode.QuickPickItemKind.Separator,
    },
    ...modelEntries,
    ...extraEntries,
  ];
}

function buildModelEntries(
  modelIds: string[],
  modelDisplays: string[] | undefined,
  endpoint: OdysseusModelCatalogItem,
  providerLabel: string,
  sourceLabel: string,
  endpointName: string | null,
): ModelQuickPickItem[] {
  return modelIds.map((modelId, index) => {
    const display = modelDisplays?.[index]?.trim();
    const renderedModel = display && display !== modelId ? `${display} (${modelId})` : modelId;
    const endpointId = endpoint.endpoint_id ?? "";
    const detailParts = [
      sourceLabel,
      endpointId ? `endpoint ${endpointId}` : null,
      endpoint.url,
    ].filter((part): part is string => Boolean(part));
    const detail = detailParts.join(" • ");

    return {
      label: renderedModel,
      description: endpointName ? `${providerLabel} • ${endpointName}` : providerLabel,
      detail,
      value: {
        serverUrl: getConfiguredServerUrl(),
        endpointId,
        endpointUrl: endpoint.url,
        modelId,
        label: renderedModel,
        detail,
        endpointName,
        providerLabel,
        sourceLabel,
        modelOptions: endpoint.model_options?.[modelId] ?? [],
        modelOptionsHydrated: true,
      } satisfies SavedModelSelection,
    };
  });
}

function formatProviderLabel(endpoint: OdysseusModelCatalogItem): string {
  const name = endpoint.endpoint_name?.trim();
  if (name) {
    const lower = name.toLowerCase();
    if (lower.includes("chatgpt")) {
      return "ChatGPT Subscription";
    }
    if (lower.includes("claude")) {
      return "Claude Subscription";
    }
    if (lower.includes("openrouter")) {
      return "OpenRouter";
    }
    if (lower.includes("deepseek")) {
      return "DeepSeek";
    }
    if (lower.includes("ollama")) {
      return "Ollama";
    }
  }

  if (endpoint.category === "local") {
    return "Local Model";
  }
  if (endpoint.category === "api") {
    return "API Model";
  }
  if (endpoint.endpoint_kind === "proxy") {
    return "Proxy Model";
  }
  return "Odysseus Model";
}

function formatSourceLabel(endpoint: OdysseusModelCatalogItem): string {
  if (endpoint.category === "local") {
    return "local";
  }
  if (endpoint.endpoint_kind === "proxy") {
    return "proxy";
  }
  if (endpoint.endpoint_kind === "api" || endpoint.category === "api") {
    return "api";
  }
  return endpoint.endpoint_kind ?? endpoint.category ?? "endpoint";
}

function isModelQuickPickItem(
  item: vscode.QuickPickItem | ModelQuickPickItem | undefined,
): item is ModelQuickPickItem {
  return Boolean(item && "value" in item);
}
