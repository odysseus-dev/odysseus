# Changelog

All notable changes to the Odysseus VS Code extension are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/), and the
project aims to follow [Semantic Versioning](https://semver.org/).

## [0.1.0]

### Added

- **Save to Brain** — save a selection or note as a long-term memory from the
  command palette, the editor context menu, or the settings menu; plus an opt-in
  `odysseus.memory.autoSave` post-reply prompt. Gated on the token's
  `memory:write` scope.
- **Behavior menu** — Mode, agent approval, and per-model runtime options moved
  into a single dropdown on the composer bar (the gear menu stays focused on
  connection/model/session).
- **Computer-use view** — `Odysseus: Open Computer-Use View` opens the sandbox's
  live noVNC desktop (`odysseus.computerUseViewUrl`).
- Tooling: ESLint (flat config) + Prettier, Vitest unit tests (SSE parsing, URL
  normalization), and `lint` / `format` / `test` / `check` scripts.

### Changed

- The user's message bubble now paints before the attachment upload, so sending
  with files staged feels instant.

### Fixed

- Reasoning-model "thinking" deltas no longer leak into the answer bubble.

## [0.0.1]

- Initial editor integration (phases 1–7): sidebar chat with streaming, model
  and session pickers, connection validation, context staging and attachments,
  agent-step cards with an approval flow, and host-side SSE parsing so the API
  token never leaves the extension host.
