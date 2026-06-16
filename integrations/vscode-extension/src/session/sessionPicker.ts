import * as vscode from "vscode";

import { getConfiguredServerUrl, OdysseusApiClient, readServerConfig } from "../api/client";
import type { SavedSessionSelection, SessionMenuItem } from "../chat/protocol";

const SELECTED_SESSION_KEY = "odysseus.selectedSession";

export async function pickSession(
  context: vscode.ExtensionContext,
): Promise<SavedSessionSelection | null> {
  const config = await readServerConfig(context);
  if (!config.token) {
    void vscode.window.showWarningMessage("Configure an Odysseus API token first.");
    return null;
  }

  const client = new OdysseusApiClient(config);
  const sessions = await client.getSessions();

  if (!sessions.length) {
    void vscode.window.showInformationMessage("No Odysseus sessions are available yet.");
    return null;
  }

  const selected = await vscode.window.showQuickPick(
    sessions.map((session) => ({
      label: session.name || "Untitled session",
      description: session.model || "",
      detail: session.last_message_at ?? session.updated_at ?? session.id,
      value: {
        serverUrl: config.serverUrl,
        sessionId: session.id,
        label: session.name || "Untitled session",
      } satisfies SavedSessionSelection,
    })),
    {
      title: "Pick an Odysseus session",
      matchOnDescription: true,
      matchOnDetail: true,
    },
  );

  if (!selected) {
    return null;
  }

  await context.workspaceState.update(SELECTED_SESSION_KEY, selected.value);
  return selected.value;
}

export function getSelectedSession(context: vscode.ExtensionContext): SavedSessionSelection | null {
  const selection = context.workspaceState.get<SavedSessionSelection>(SELECTED_SESSION_KEY) ?? null;
  if (!selection) {
    return null;
  }
  const serverUrl = getConfiguredServerUrl();
  if (selection.serverUrl && selection.serverUrl !== serverUrl) {
    return null;
  }
  return selection.serverUrl ? selection : { ...selection, serverUrl };
}

export async function setSelectedSession(
  context: vscode.ExtensionContext,
  selection: SavedSessionSelection,
): Promise<void> {
  await context.workspaceState.update(SELECTED_SESSION_KEY, {
    ...selection,
    serverUrl: selection.serverUrl || getConfiguredServerUrl(),
  } satisfies SavedSessionSelection);
}

export async function fetchSessionMenuItems(
  context: vscode.ExtensionContext,
): Promise<SessionMenuItem[]> {
  const config = await readServerConfig(context);
  if (!config.token) {
    return [];
  }

  const client = new OdysseusApiClient(config);
  const sessions = await client.getSessions();
  const selected = getSelectedSession(context);
  return sessions.map((session) => {
    const label = session.name || "Untitled session";
    const detail = [session.model, session.last_message_at ?? session.updated_at ?? session.id]
      .filter((part): part is string => Boolean(part))
      .join(" • ");
    return {
      sessionId: session.id,
      label,
      detail,
      selected: selected?.sessionId === session.id,
    };
  });
}

export async function reconcileSelectedSessionScope(
  context: vscode.ExtensionContext,
): Promise<boolean> {
  const selection = context.workspaceState.get<SavedSessionSelection>(SELECTED_SESSION_KEY) ?? null;
  if (!selection) {
    return false;
  }
  const serverUrl = getConfiguredServerUrl();
  if (!selection.serverUrl) {
    await setSelectedSession(context, { ...selection, serverUrl });
    return false;
  }
  if (selection.serverUrl !== serverUrl) {
    await clearSelectedSession(context);
    return true;
  }
  return false;
}

export async function clearSelectedSession(context: vscode.ExtensionContext): Promise<void> {
  await context.workspaceState.update(SELECTED_SESSION_KEY, undefined);
}
