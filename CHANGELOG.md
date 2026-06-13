# Changelog

All notable changes to this fork of Odysseus are documented here.

## [0.1.0] — 2026-06-13

### Bug Fixes

- **ci:** Skip PR description check on fork (pull_request_target bootstrap)
- **ci:** Pin action SHAs and skip PR description check on fork
- **ci:** Skip dependency-review on fork and fix ruff import order
- **ci:** Use --exit-zero for advisory ruff check
- **local-models:** Handle tool schema rejection, timestamp cast, and phi4 thinking

### CI/CD

- Add ruff lint workflow and make pytest a required gate

### Chores

- Add semantic versioning (git-cliff, VERSION file)

### Features

- RAG manifest + filesystem MCP IaC
- **settings:** Set gemma3:12b as default model fallback
- **agent:** Tool-first rules and Odysseus framework context


