# Dev Containers

## What is a Devcontainer?

A devcontainer runs Odysseus and its project files inside Docker so your editor, terminal, and app all live in the same Linux environment. You can use it from any IDE with Dev Containers support — VS Code, Cursor, and similar editors. It will deploy Odysseus plus ChromaDB, SearXNG, and ntfy — with code reload while you edit. Pick **Ubuntu** or **Fedora** when you open the folder in a container.

## Setup

1. Copy `.devcontainer/.env.example` to `.devcontainer/.env`.
2. Copy the profile env too: `ubuntu/.env.example` → `ubuntu/.env` (or `fedora/` for Fedora).
3. In VS Code, Cursor, or another supported IDE: **Dev Containers: Open Folder in Container** — choose Ubuntu or Fedora.
4. Open `http://localhost:7000`. First boot prints an admin password in the logs.
