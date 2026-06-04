# GitHub Hooks for Odysseus

This directory contains agent guidance hook definitions and validation scripts.

Files
- `agent-guidance-hook.json`: hook metadata and events for agent customization.
- `agent-guidance.js`: runtime pre-tool-use validation script.
- `agent-guidance-validate.js`: CI validation script for the hook files.

Workflow
- `.github/workflows/hook-validation.yml` runs on changes to `.github/hooks/**` and verifies the hook JSON and script.

Usage
- Keep hook definitions in `.github/hooks/`.
- Use the workflow to guard hook changes in PRs.
