# Roadmap / Help Wanted

Odysseus is an evolving, self-hosted AI workspace. This roadmap highlights where improvements and contributions are most useful, from reliability and maintainability to product capabilities.

> [!NOTE]
> **Direction, not a release schedule.** This page is not a complete issue backlog or a promise to deliver every proposal. Implementation scope and priority are coordinated through existing issues, pull requests, and the project's [feature proposal process](docs/maintainers/product-and-features.md).

## At a glance

| Area | Direction | Coordination |
| --- | --- | --- |
| **Stabilization** | Reliability, CI, fresh installs, security, and integrations | [Stabilization & Modularization milestone](https://github.com/odysseus-dev/odysseus/milestone/1) |
| **Modularization** | Clearer module ownership, safer changes, and contributor onboarding | [Canonical tracker #4071](https://github.com/odysseus-dev/odysseus/issues/4071) |
| **Agent runtime** | Active Harness V2 development, with a narrow temporary change freeze | [Coordination in #4071](https://github.com/odysseus-dev/odysseus/issues/4071) |
| **Product improvements** | Local models, research, frontend experience, and operations | [Issues](https://github.com/odysseus-dev/odysseus/issues) and [feature proposals](docs/maintainers/product-and-features.md) |

**Jump to:** [Stabilization](#stabilization-and-reliability) · [Modularization](#modularization-and-maintainability) · [AI and local models](#ai-local-models-and-research) · [Frontend](#frontend-and-user-experience) · [Backend](#backend-and-operations) · [Contributing](#contributing)

---

## Stabilization and reliability

**Priority:** Make Odysseus dependable across installations, platforms, providers, and supported hardware.

| Workstream | What needs improvement | Tracking |
| --- | --- | --- |
| **CI and validation** | Exact-revision validation, meaningful regression coverage, and release confidence | [#5932](https://github.com/odysseus-dev/odysseus/issues/5932), [#7109](https://github.com/odysseus-dev/odysseus/issues/7109) |
| **Test quality** | Deterministic tests, isolation, focused execution, and maintainable test structure | [#2523](https://github.com/odysseus-dev/odysseus/issues/2523), [testing standard](tests/TESTING_STANDARD.md) |
| **Fresh installations** | Smoke tests for Linux, macOS, Windows, WSL, and Docker; baseline startup and graceful degradation | [#3968](https://github.com/odysseus-dev/odysseus/issues/3968) |
| **Email integration** | Remaining Google OAuth regressions, account isolation, and cross-provider reliability | [#5637](https://github.com/odysseus-dev/odysseus/issues/5637) |
| **Storage integrity** | Upload payload growth, indexing, and attachment/file lifecycle | [#4889](https://github.com/odysseus-dev/odysseus/issues/4889) |

Additional reliability priorities:

- **Cookbook:** Improve model installation, dependency management, hardware detection, and model serving across operating systems, GPUs, drivers, shells, Python environments, and supported backends.
- **Security:** Keep strengthening authorization, administrative tools, agent tool execution, prompt-injection boundaries, filesystem access, and untrusted-content handling.
- **Integrations and degraded states:** Audit supported providers and optional services, including ChromaDB, SearXNG, email, and ntfy. Make failures observable and nonessential outages predictable.

> [!TIP]
> Start with an existing, reproducible issue. The [Stabilization & Modularization milestone](https://github.com/odysseus-dev/odysseus/milestone/1) is a curated work queue, not an invitation to implement every item in this roadmap without coordination.

## Modularization and maintainability

The goal is **clearer responsibility boundaries and safer development**, not smaller files for their own sake. Changes should preserve behavior, compatibility, and relevant runtime contracts.

| Area | Current foundation | Remaining opportunities |
| --- | --- | --- |
| **CSS** | The former `static/style.css` has been split into ordered files under `static/css/` | Improve ownership while preserving the cascade, themes, and responsive layouts |
| **Tools** | Substantial tool-domain extraction has landed | Clarify remaining ownership and preserve compatibility through the existing tool interfaces |
| **Frontend** | Existing feature modules remain in use | Decompose large document, gallery, settings, calendar, and other UI modules around coherent behavior |
| **Backend** | Domain-specific routes and services already exist | Identify safe boundaries in Cookbook routes, persistence, and other tightly coupled modules |
| **Tests** | Taxonomy, focused execution, and structural improvements exist | Align fixtures, helpers, and regression coverage with implementation boundaries |
| **UI infrastructure** | Targeted cleanup is underway | Consolidate tour scaffolding, modal/window positioning, responsive overrides, and obsolete paths |

**Canonical coordination:** [Codebase modularization and contributor onboarding, #4071](https://github.com/odysseus-dev/odysseus/issues/4071).

> [!IMPORTANT]
> **Cohesive work is more important than a small line count.** A larger PR may be appropriate for one well-defined decomposition when several smaller PRs would create avoidable conflicts or integration overhead. Coordinate the boundary, avoid unrelated work, and supply regression and runtime evidence. See [CONTRIBUTING.md](CONTRIBUTING.md#structural-changes-and-modularization).

### Agent runtime and Harness V2

Harness V2 is under active development.

> [!CAUTION]
> **Temporary agent-runtime freeze.** Unrelated changes to `src/agent_loop.py` and closely related execution, orchestration, authorization, and context contracts must be coordinated with maintainers while Harness V2 is developed and validated.
>
> This does **not** freeze unrelated application modules. Urgent bug reports and security issues remain welcome. No release date is implied.

## AI, local models, and research

These are **areas of product interest**, not commitments to a specific implementation or delivery order. Follow normal issue triage and product-decision requirements.

| Direction | Opportunity |
| --- | --- |
| **Smaller-model agents** | Reduce unnecessary prompt, tool-schema, skill, memory, and document context overhead; improve tool selection for constrained context windows |
| **Cookbook and SGLang** | Improve installation and serving across relevant hardware, operating systems, and deployment methods |
| **Model discovery** | Rank models by architecture, quantization, RAM/VRAM fit, backend support, multimodal requirements, and expected reliability |
| **Deep Research presets** | Explore suitable model and parameter configurations for small, medium, and large local systems |
| **Actionable diagnostics** | Expose command output, logs, and next steps for failed downloads, dependencies, preflights, and serving jobs |
| **Speculative decoding** | Investigate compatible draft-model configurations and evaluate end-to-end correctness and time to success |
| **Provider setup** | Improve endpoint discovery, capability checks, and setup for supported local and hosted providers |

## Frontend and user experience

- **Editors and files:** Improve document/file editing, window behavior, save/export flows, and image-editing reliability.
- **Notes and Todos:** Expand agent-assisted reading, updates, summarization, and turning information into tasks.
- **Mobile:** Improve gallery/editor usability, model setup, and responsive behavior.
- **Accessibility:** Strengthen keyboard navigation, focus states, contrast, and reduced-motion support.
- **First run:** Improve setup, hints, empty states, error messages, and onboarding tours without duplicate guidance.
- **Self-hosting:** Reduce unnecessary external asset dependencies where practical for offline operation.

## Backend and operations

- **Email performance:** Measure fetching, searching, body and attachment loading, caching, sending, and frontend refresh behavior before optimizing.
- **Provider reliability:** Expand regression coverage for setup, endpoint probing, and degraded-service behavior.
- **Task scheduling:** Improve defaults, reliability, and operational visibility.
- **Data recovery:** Improve backup and restore guidance and workflows for persistent data.
- **Administrative security:** Review privileged capabilities and sensitive integrations against their authorization boundaries.

## Not a current priority

- Additional themes without a demonstrated product need.
- Broad rewrites or framework migrations without clear, measurable value.
- Changes undertaken solely to reduce file counts.
- Uncoordinated changes to temporarily frozen agent-runtime contracts.

---

## Contributing

1. Read [CONTRIBUTING.md](CONTRIBUTING.md) and search [existing issues](https://github.com/odysseus-dev/odysseus/issues) and [PRs](https://github.com/odysseus-dev/odysseus/pulls).
2. Coordinate modularization through [#4071](https://github.com/odysseus-dev/odysseus/issues/4071) and active stabilization work through the [milestone](https://github.com/odysseus-dev/odysseus/milestone/1).
3. Use the repository's issue and PR templates and target **`dev`**.
4. Include relevant tests, runtime evidence, and screenshots or recordings for visual changes.

> [!NOTE]
> Product-direction or difficult-to-reverse changes follow the [feature proposal process](docs/maintainers/product-and-features.md). Report security vulnerabilities privately through [SECURITY.md](SECURITY.md), never in a public issue.
