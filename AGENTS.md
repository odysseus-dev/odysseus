# AGENTS — Roles & Permissions

Overview
- This document describes the purpose of agent files and the roles that operate on the repository.

Agent Roles
- Odysseus Enterprise Command Center: senior operations assistant — can create and update knowledge_base content, templates, and governance documents.
- Explore: read-only codebase exploration agent used for fast searches and context gathering.
- Automation Agents: CI bots that run tests, linters, and deployments (restricted to CI configs and infra files).

Permissions & Process
- Human reviewers must approve content changes to policy or financial documents via PR.  
- Agents may create draft files and templates; final publication requires a PR with an owner assigned.  
- Large binaries (PBIX, XLSX, STL) are stored with `_vX` suffixes and a manifest entry in the folder README.  

Edit Guidance
- All changes that affect governance, policy, or financial controls must include an owner, contacts, and review cadence in the README.  
- Use `manage_todo_list` to track multi-step changes and link the PR to the todo item.
