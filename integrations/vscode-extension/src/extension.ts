import * as vscode from "vscode";

import { API_TOKEN_SECRET_KEY } from "./api/client";
import { OdysseusSidebarProvider } from "./chat/provider";

// View container in the secondary side bar requires VS Code >= 1.106. On older
// builds we fall back to the activity bar via this context key (mirrors how the
// Codex / Claude Code extensions gate their secondary-sidebar containers).
const SECONDARY_VIEW_ID = "odysseus.chat.secondary";

export async function activate(context: vscode.ExtensionContext): Promise<void> {
  const [major, minor] = vscode.version.split(".").map((part) => Number(part) || 0);
  const supportsSecondarySidebar = major > 1 || (major === 1 && minor >= 106);
  if (!supportsSecondarySidebar) {
    await vscode.commands.executeCommand(
      "setContext",
      "odysseus:doesNotSupportSecondarySidebar",
      true,
    );
  }

  const outputChannel = vscode.window.createOutputChannel("Odysseus");
  const provider = new OdysseusSidebarProvider(context.extensionUri, context, outputChannel);
  const statusBar = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Left, 10);
  statusBar.command = "odysseus.configure";
  statusBar.show();

  const syncStatusBar = async (): Promise<void> => {
    const state = await provider.getSidebarState();
    statusBar.text = `${statusIcon(state.connectionStatus)} Odysseus ${state.connectionLabel}`;
    statusBar.tooltip = [
      `Server: ${state.serverUrl}`,
      `Status: ${state.connectionLabel}`,
      state.serverVersion ? `Version: ${state.serverVersion}` : "Version: unavailable",
      state.connectionDetail ?? "Configure Odysseus server URL and API token.",
    ].join("\n");
  };

  context.subscriptions.push(
    outputChannel,
    statusBar,
    provider.onDidChangeState(() => {
      void syncStatusBar();
    }),
    vscode.window.registerWebviewViewProvider(OdysseusSidebarProvider.viewId, provider, {
      webviewOptions: { retainContextWhenHidden: true },
    }),
    vscode.window.registerWebviewViewProvider(SECONDARY_VIEW_ID, provider, {
      webviewOptions: { retainContextWhenHidden: true },
    }),
    vscode.commands.registerCommand("odysseus.configure", async () => {
      await provider.configure();
    }),
    vscode.commands.registerCommand("odysseus.newChat", async () => {
      await provider.newChat();
      await revealSidebar();
    }),
    vscode.commands.registerCommand("odysseus.pickModel", async () => {
      await provider.chooseModel();
      await revealSidebar();
    }),
    vscode.commands.registerCommand("odysseus.pickSession", async () => {
      await provider.chooseSession();
      await revealSidebar();
    }),
    vscode.commands.registerCommand("odysseus.attachFile", async () => {
      await provider.attachFiles();
      await revealSidebar();
    }),
    vscode.commands.registerCommand("odysseus.attachImage", async () => {
      await provider.attachImages();
      await revealSidebar();
    }),
    vscode.commands.registerCommand("odysseus.attachFolder", async () => {
      await provider.attachFolderContext();
      await revealSidebar();
    }),
    vscode.commands.registerCommand("odysseus.askSelection", async () => {
      await provider.stageSelectionPrompt();
      await revealSidebar();
    }),
    vscode.commands.registerCommand("odysseus.addCurrentFile", async () => {
      await provider.stageCurrentFileContext();
      await revealSidebar();
    }),
    vscode.commands.registerCommand("odysseus.stop", async () => {
      await provider.stopFromCommand();
      await revealSidebar();
    }),
    vscode.commands.registerCommand("odysseus.saveToBrain", async () => {
      await provider.saveToBrain();
    }),
    vscode.commands.registerCommand("odysseus.openComputerView", async () => {
      // Phase C: open the sandbox's live noVNC desktop in the browser so you can
      // watch (and take over) the agent's computer-use session.
      const url = vscode.workspace
        .getConfiguration("odysseus")
        .get<string>("computerUseViewUrl", "http://127.0.0.1:8391/vnc.html");
      await vscode.env.openExternal(vscode.Uri.parse(url));
    }),
    vscode.workspace.onDidChangeConfiguration((event) => {
      if (event.affectsConfiguration("odysseus.serverUrl")) {
        void provider.refresh({ validateConnection: true });
      } else if (event.affectsConfiguration("odysseus.defaultMode")) {
        void provider.refresh({ validateConnection: false });
      }
    }),
    vscode.window.onDidChangeActiveTextEditor(() => {
      void provider.refresh({ validateConnection: false });
    }),
    context.secrets.onDidChange((event) => {
      if (event.key === API_TOKEN_SECRET_KEY) {
        void provider.refresh({ validateConnection: true });
      }
    }),
  );

  await provider.refresh({ validateConnection: true });
  await syncStatusBar();
}

export function deactivate(): void {}

async function revealSidebar(): Promise<void> {
  // Whichever container is active (secondary side bar or activity bar), focus it.
  for (const viewId of [SECONDARY_VIEW_ID, OdysseusSidebarProvider.viewId]) {
    try {
      await vscode.commands.executeCommand(`${viewId}.focus`);
      return;
    } catch {
      // This view's focus command only exists when its container is shown.
    }
  }
}

function statusIcon(status: string): string {
  switch (status) {
    case "checking":
      return "$(sync~spin)";
    case "connected":
      return "$(pass)";
    case "offline":
      return "$(cloud-offline)";
    case "auth_failed":
      return "$(key)";
    case "server_error":
      return "$(warning)";
    default:
      return "$(plug)";
  }
}
