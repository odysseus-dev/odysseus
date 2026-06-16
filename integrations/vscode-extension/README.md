# Odysseus for VS Code

Chat, agent, and computer-use workflows for a **self-hosted [Odysseus](https://github.com/pewdiepie-archdaemon/odysseus)
server** — right inside your editor. The extension host does all HTTP and holds
your API token; the webview never sees it.

## Features

- **Sidebar chat** — streaming responses with a composer-centric UI, model and
  session pickers, and per-prompt context/attachments. Lives in the secondary
  side bar (or the activity bar on older VS Code builds).
- **Agent mode** — workspace file-editing, browser, and tool runs rendered as
  collapsible step cards with live progress, output, and diffs; an
  **"ask before edits"** approval flow; and a remote-server warning when the
  agent's filesystem may not match your local workspace.
- **Behavior menu** — Mode (Chat/Agent), agent approval, and per-model runtime
  options (effort, thinking, …) in one dropdown on the composer bar.
- **Save to Brain** — store a selection or note as a long-term memory, plus an
  opt-in _auto-save_ prompt after replies. Gated on the token's `memory:write`
  scope.
- **Computer-use view** — one click to open the sandboxed desktop's live noVNC
  session (Phase C).
- **Context staging** — current file, a highlighted selection, or a folder
  snapshot; file/photo attachments uploaded from the host.
- **Connection-aware** — status bar + sidebar states (connected / checking /
  offline / auth failed / server error), validated via `/api/codex/capabilities`.

## Requirements

- A running Odysseus server (default `http://127.0.0.1:7860`).
- An Odysseus **API token**. For chat use the `chat` scope; for _Save to Brain_
  add `memory:write` (create the token in Odysseus → Settings → API Tokens).

## Getting started

1. Run **`Odysseus: Configure`** (or click the status-bar item).
2. Enter the server URL and paste your API token (stored in VS Code
   `SecretStorage`).
3. Open the Odysseus view and start chatting.

## Settings

| Setting                       | Default                          | Description                                                      |
| ----------------------------- | -------------------------------- | ---------------------------------------------------------------- |
| `odysseus.serverUrl`          | `http://127.0.0.1:7860`          | Base URL of the Odysseus server.                                 |
| `odysseus.defaultMode`        | `chat`                           | Default mode (`chat` or `agent`).                                |
| `odysseus.memory.autoSave`    | `false`                          | Offer to save each exchange to the Brain (needs `memory:write`). |
| `odysseus.computerUseViewUrl` | `http://127.0.0.1:8391/vnc.html` | Live noVNC view for the computer-use sandbox.                    |

## Development

```bash
cd integrations/vscode-extension
npm install
npm run build        # bundle host + webview into dist/ (esbuild)
npm run watch        # rebuild on change
npm run typecheck    # tsc --noEmit
npm run lint         # eslint
npm run test         # vitest unit tests
npm run check        # typecheck + lint + test
```

Open this folder in VS Code and press `F5` for an Extension Development Host.

## Packaging

```bash
npx @vscode/vsce package
```

`.vscodeignore` keeps sources, maps, tests, and dev tooling out of the VSIX.

## Security

- HTTP runs in the extension host; the **webview never receives the API token**.
- Saved model/session selections are scoped to the configured server, so
  switching servers never silently reuses stale state.

## License

AGPL-3.0-or-later — see the [repository](https://github.com/pewdiepie-archdaemon/odysseus).
