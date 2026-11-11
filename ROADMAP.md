# Roadmap / Help Wanted

Odysseus is an evolving self-hosted AI workspace. This roadmap highlights the areas where improvements and contributions are most useful.

It describes current direction, not a fixed release schedule or a complete issue backlog. Specific implementations should be coordinated through existing issues and pull requests.

Contributions target `dev`. The `main` branch is curated for releases.

## Stabilization and reliability

Reliable operation across installations, platforms, providers, and supported hardware remains a priority.

- **CI and validation:** Improve the reliability of automated checks, exact-revision validation, and release confidence. See #5932 and the confirmed docs-only CI regression, #7109.
- **Test quality:** Continue improving deterministic tests, isolation, focused execution, and maintainable test structure. See #2523 and the [testing standard](tests/TESTING_STANDARD.md).
- **Fresh installations:** Expand smoke-test coverage for native Linux, macOS, Windows, WSL, and Docker. Verify clean startup, health reporting, and graceful handling of unavailable optional services. See #3968.
- **Cookbook reliability:** Improve model installation, dependency management, hardware detection, and serving across supported platforms, GPUs, drivers, and execution backends.
- **Email integration:** Resolve remaining Google OAuth regressions, preserve account isolation, and improve cross-provider reliability. See #5637.
- **Storage integrity:** Investigate upload payloads, database growth, indexing, and file lifecycle without introducing unsafe or destructive cleanup. See #4889.
- **Security:** Continue hardening authorization, agent tool execution, prompt-injection boundaries, filesystem access, and handling of untrusted content.
- **Integration diagnostics:** Verify provider and optional-service behavior, improve failure reporting, and ensure unavailable dependencies degrade predictably.

Current stabilization work is coordinated through the [Stabilization & Modularization milestone](https://github.com/odysseus-dev/odysseus/milestone/1).

## Modularization and maintainability

The goal is to make the codebase easier to navigate, test, review, and extend while preserving existing behavior and compatibility.

The initial CSS extraction and substantial tool-domain decomposition have established a foundation, but responsibility boundaries still need improvement.

Current areas of interest:

- **Frontend:** Continue focused decomposition of large document, gallery, settings, calendar, and other UI modules.
- **CSS:** The former `static/style.css` has been split into ordered files under `static/css/`. Improve maintainability without inadvertently changing cascade order, theme behavior, or responsive layouts.
- **Tools:** Continue clarifying ownership within `src/agent_tools/` and related implementations while preserving existing public contracts and compatibility.
- **Backend:** Identify safe boundaries in large Cookbook routes, persistence modules, and feature-specific route handlers.
- **Tests:** Keep test helpers, fixtures, and focused test ownership aligned with the implementation modules they exercise.
- **UI infrastructure:** Reduce duplicated tour scaffolding, fragile modal/window positioning, stale feature flags, and cross-feature coupling.

[#4071](https://github.com/odysseus-dev/odysseus/issues/4071) is the canonical coordination tracker for this work.

Larger PRs can be appropriate for a single coherent decomposition when splitting the work into many smaller PRs would create unnecessary integration complexity. Coordinate the intended boundary before implementation and provide meaningful regression and runtime validation.

### Agent runtime and Harness V2

Harness V2 is under active development.

The agent runtime, including `src/agent_loop.py` and closely related execution, authorization, orchestration, and context contracts, is temporarily frozen for unrelated changes while that work is developed and validated.

Agent-runtime refactors must be coordinated with maintainers. Urgent bug and security reports remain welcome.

This restriction does not apply to unrelated application modules.

## AI, local models, and research

The following improvements remain areas of product interest. Their exact implementations and priority relative to one another require normal issue triage and product decisions.

- **Smaller-model agent efficiency:** Reduce unnecessary prompt, tool-schema, skill, memory, and document context overhead. Improve tool selection and behavior with constrained context windows.
- **Cookbook SGLang support:** Improve setup and serving across relevant operating systems, hardware, and deployment methods.
- **Model discovery and ranking:** Recommend appropriate models using architecture, quantization, RAM/VRAM availability, backend support, multimodal requirements, and expected serving reliability.
- **Deep Research presets:** Explore hardware-appropriate model and parameter configurations for smaller and larger local systems.
- **Cookbook diagnostics:** Expose actionable installation, download, preflight, and serving failures with useful, copyable logs.
- **Speculative decoding:** Investigate supported draft-model configurations for compatible local backends. Evaluate end-to-end correctness and time to successful output, not throughput alone.
- **Provider setup and probing:** Improve integration quality for supported local and hosted providers, including endpoint discovery and capability handling.

## Frontend and user experience

- Improve document and file editing, window behavior, save/export flows, and image-editing reliability.
- Expand agent-assisted workflows for Notes and Todos, including reading, updating, summarizing, and turning information into actionable tasks.
- Improve the mobile gallery and editor experience.
- Strengthen keyboard navigation, accessibility, focus behavior, contrast, and reduced-motion support.
- Improve first-run setup, empty states, error messages, and onboarding guidance.
- Improve desktop/mobile CSS maintainability and responsive behavior.
- Continue reducing unnecessary external asset dependencies where practical for self-hosted and offline operation.

## Backend and operations

- Improve email performance through measured investigation of fetching, caching, attachment handling, searching, and sending.
- Expand regression coverage for provider setup, endpoint probing, and degraded service states.
- Improve task-scheduler defaults, reliability, and operational visibility.
- Improve backup and recovery guidance for persistent application data.
- Continue reviewing administrative capabilities, sensitive integrations, and their authorization boundaries.

## Not a current priority

- Adding more themes without a clear product need.
- Broad rewrites without demonstrated value.
- Framework migrations or architectural changes undertaken solely to reduce file counts.
- Uncoordinated changes to the temporarily frozen agent-runtime contracts.

## Contributing

Before starting work:

1. Read [CONTRIBUTING.md](CONTRIBUTING.md).
2. Search existing issues and PRs to avoid duplicating work.
3. Coordinate structural changes through #4071 and stabilization work through the milestone.
4. Open a focused issue where needed and follow the existing contribution templates.
5. Provide relevant tests, runtime evidence, and screenshots or recordings for visual changes.

For changes involving product direction or difficult-to-reverse contracts, follow the [feature proposal process](docs/maintainers/product-and-features.md).

Security vulnerabilities must be reported privately through [SECURITY.md](SECURITY.md).
