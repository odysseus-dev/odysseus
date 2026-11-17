# Contributing to Odysseus

Thanks for helping improve Odysseus. The best contributions are **focused, reviewable, and backed by meaningful validation**.

> [!IMPORTANT]
> **Public pull requests target `dev`, not `main`.** Search existing work before implementing, and follow the current [PR template](.github/pull_request_template.md). Runtime changes require relevant execution evidence, not just a green CI badge.

## At a glance

| Topic | What contributors should do |
| --- | --- |
| **Branch** | Target `dev`; `main` is the curated release branch |
| **Scope** | One coherent change per PR; coordinate larger modularization work through [#4071](https://github.com/odysseus-dev/odysseus/issues/4071) |
| **Validation** | Document commands, results, runtime checks, and anything not tested |
| **Visual changes** | Run the UI, match the existing visual language, and attach screenshots or clips |
| **Review** | Follow the [risk-based maintainer policy](docs/maintainers/changes-and-review.md) and applicable repository protections |
| **Security** | Report vulnerabilities privately using [SECURITY.md](SECURITY.md) |

**Jump to:** [Branches](#branch-model) · [Getting started](#before-you-start) · [Setup](#setup) · [Checks](#running-checks) · [Tests](#adding-and-organizing-tests) · [Pull requests](#pull-requests) · [Modularization](#structural-changes-and-modularization) · [Visual changes](#style-and-visual-changes) · [Code conventions](#code-conventions) · [Issue reports](#issue-reports)

---

## Branch model

| Branch | Purpose |
| --- | --- |
| **`dev`** | Default integration branch and target for every public PR. Work can be in flux. |
| **`main`** | Curated release branch. A [Release Maintainer](docs/maintainers/testing-and-releases.md#release-ownership) promotes a verified `dev` commit. |

If your PR accidentally targets `main`, use **Edit** on GitHub to change its base to `dev`. Changing the base does not itself require a rebase.

End users cloning the repository land on `dev` by default. To use the curated release branch, run `git checkout main` after cloning.

## Before You Start

1. Search [open issues](https://github.com/odysseus-dev/odysseus/issues) and [PRs](https://github.com/odysseus-dev/odysseus/pulls) to avoid duplicate or conflicting work.
2. Prefer one bug fix, feature, or coherent refactor per PR. Avoid broad rewrites, formatting-only churn, and unrelated file moves.
3. For a feature, open a **Feature Request** first. Big, new-direction, or difficult-to-reverse changes require the documented [product decision process](docs/maintainers/product-and-features.md#feature-proposal-workflow).
4. For structural work, coordinate the intended module boundary with maintainers and the [modularization tracker #4071](https://github.com/odysseus-dev/odysseus/issues/4071).

> [!NOTE]
> **Coding agents:** If you use an LLM coding agent, open an issue describing the problem before submitting its PR. Bulk agent-generated submissions that ignore the project's contribution format or visual style may be closed without review, even when the underlying fix is correct.

## Setup

<details open>
<summary><strong>Docker (recommended for normal testing)</strong></summary>

```bash
git clone https://github.com/odysseus-dev/odysseus.git
cd odysseus
cp .env.example .env
docker compose up -d --build
```

</details>

<details>
<summary><strong>Manual Python development (Python 3.11+)</strong></summary>

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
python -m uvicorn app:app --host 127.0.0.1 --port 7000
```

</details>

> [!NOTE]
> Native Windows is not actively tested. Docker on Linux or a Linux/macOS manual installation is the safer path for now. Document the environment actually used for testing.

## Running Checks

Run relevant checks for the affected area. Examples from the existing contributor workflow:

```bash
python -m pytest
python -m py_compile app.py routes/*.py src/*.py
node --check static/js/<file-you-changed>.js
```

For Docker-related changes:

```bash
docker compose config
docker compose up -d --build
docker compose logs --tail=120 odysseus
```

> [!IMPORTANT]
> **Report what actually ran.** Include commands, results, and relevant manual/runtime verification in the PR description. If a check was not run, mark it as **not run** and explain the limitation. Passing syntax checks and unit tests do not automatically prove changed behavior works in the running application.

See the [testing and releases handbook](docs/maintainers/testing-and-releases.md) for risk-based validation and isolation requirements.

## Adding and organizing tests

Read the [testing standard](tests/TESTING_STANDARD.md) before adding
or moving tests. The [test suite notes](tests/README.md) document
current runners, helpers, taxonomy, and CI behavior.

- **Placement:** Follow the existing taxonomy. Migration is
  incremental under [#2523](https://github.com/odysseus-dev/odysseus/issues/2523).
  Planned directories are not necessarily active yet.
- **Behavior:** Prefer deterministic behavioral tests covering
  meaningful outcomes, failures, and recovery.
- **Discovery:** Verify new tests execute through the intended pytest,
  Node, or CI runner. Node tests are not automatically pytest tests.
- **Organization:** Group tests by cohesive responsibility within
  approved categories. Coordinate new feature directories.
- **Migration:** Move older tests in small batches without changing
  assertions or fixtures in the same change.
- **Quality:** Avoid redundant coverage, unnecessary mocks, brittle
  source assertions, and premature shared helpers. Never weaken
  tests merely to obtain passing CI.

## Maintainer governance and review guidance

The [Odysseus maintainer handbook](docs/maintainers/README.md) is authoritative for repository roles, product decisions, risk-based approvals, issue triage, security review, CodeQL findings, and release handoffs.

> [!NOTE]
> The handbook complements this contributor guide. It does not override the branch model, live GitHub protection rules, or the PR template.

## Pull Requests

Keep PRs **cohesive**. Small, focused changes are the default. A larger, coordinated module decomposition may be appropriate under the criteria below, but unrelated cleanup, formatting, refactors, and behavior changes must not be mixed.

| Include in the PR | Why it matters |
| --- | --- |
| **Summary and scope** | Explain the problem, intended behavior, and areas changed |
| **Linked issue** | Connect the PR to the existing report or approved work, for example `Fixes #123` or `Part of #4071` |
| **Validation evidence** | Show manual test steps or automated results that exercise the actual behavior |
| **UI evidence, when applicable** | Attach screenshots or short recordings from the running application |
| **Limitations** | Clearly identify tests or environments that were not exercised |

For exact required sections and attestations, follow the current [PR template](.github/pull_request_template.md).

### Structural changes and modularization

**One coherent responsibility is the unit of review.** A larger PR can be appropriate when extracting that responsibility from an oversized module is safer or simpler than splitting the work into many conflicting or interdependent PRs.

Before implementation and review:

- **Coordinate:** Read existing issues/PRs and agree on the boundary with maintainers. Use [#4071](https://github.com/odysseus-dev/odysseus/issues/4071) for codebase modularization.
- **Constrain scope:** Keep one subsystem or responsibility per PR. Avoid unrelated features, formatting, and opportunistic cleanup.
- **Preserve contracts:** Maintain observable behavior, imports, public interfaces, persistence contracts, and runtime invariants unless an approved issue explicitly scopes a change.
- **Validate behavior:** Include targeted regression tests and runtime checks. Frontend work needs browser validation and screenshots or recordings when rendering may change.
- **Explain tradeoffs:** Describe extraction boundaries, compatibility, validation results, and known limitations in the PR.

> [!TIP]
> **Large does not mean broad.** There is no arbitrary line-count limit for a well-scoped extraction. Correctness, reviewability, and integration risk determine whether the scope is acceptable.

> [!CAUTION]
> **Temporary Harness V2 coordination boundary:** Unrelated changes to `src/agent_loop.py` and closely related execution, authorization, orchestration, and context contracts are temporarily frozen while Harness V2 is developed and validated. Coordinate such work with maintainers. Other modules remain open, and urgent bug or security reports are welcome.

These contributions remain subject to the [risk-based review policy](docs/maintainers/changes-and-review.md), current repository protections, and PR template.

## Style and visual changes

Odysseus has an intentional visual language. Changes that ignore it can be closed without merge even when their underlying code is correct.

For any change affecting buttons, icons, typography, colors, spacing, layout, CSS, HTML, SVG, or JavaScript that renders into the DOM:

1. **Run the application locally** and inspect the change in a browser. Type checks and unit tests alone are insufficient.
2. **Attach a screenshot or short clip** from the running app, plus a mobile screenshot when mobile behavior changes.
3. **Reuse the existing design system:**
   - Prefer existing CSS tokens (`--red`, `--fg`, `--bg`, `--card`, `--border`, etc.). Do not introduce parallel colors, type scales, or spacing systems.
   - Reuse existing button, input, card, and border classes rather than creating duplicate patterns.
   - Do **not** add Unicode emoji in UI or code. Use inline SVG consistent with `static/index.html` or plain text.
   - Preserve the existing primary monospaced font (`Fira Code`).
   - Keep dark mode as the default; route any light-mode work through the existing theme system.
4. **Extend existing components** instead of introducing a parallel component for the same purpose.

> [!IMPORTANT]
> If you are unsure whether a change is visual, assume that it is and provide a screenshot. Visual evidence does not replace behavioral validation.

## Code conventions

**Use existing constants and helpers.** Hardcoded duplicates drift, break non-default deployments, and can reintroduce previously fixed issues.

- **Filesystem paths:** Do not derive writable paths from `Path(__file__)...` into the source tree, hardcode `/app/...`, or use a relative `"data/..."` string. Persisted files and directories have named constants in `src/constants.py`, such as `AUTH_FILE`, `USER_PREFS_FILE`, `SETTINGS_FILE`, `TTS_CACHE_DIR`, and `CHROMA_DIR`. Import those names rather than deriving paths with `os.path.join(DATA_DIR, "x.json")` or `DATA_DIR / "x.json"`. `DATA_DIR` reads `ODYSSEUS_DATA_DIR` and is intended directly only for dynamic paths without fixed names, such as per-owner files. Add a constant when a fixed path does not yet have one. The source tree is read-only in Docker, and `/app/...` does not exist on native runs. Guard directory creation so unwritable paths degrade gracefully rather than crashing at import.
- **Internal API and loopback URLs:** Do not hardcode `http://localhost:7000`. Use `internal_api_base()` from `src.constants`, which honors `ODYSSEUS_INTERNAL_BASE` and `APP_PORT`.
- **Ports, limits, and model lists:** Reuse existing constants. When a value is shared by multiple locations and has no constant, add one in `src/constants.py` instead of duplicating a literal. `core/constants.py` is a compatibility re-export, not another source of truth.

**Commits:** Use [Conventional Commits](https://www.conventionalcommits.org) with `type(scope): summary`, for example `fix(search): ...`, `feat(notes): ...`, or `docs(contributing): ...`. Common types include `fix`, `feat`, `refactor`, `docs`, `test`, `chore`, and `ci`. Keep the subject short and imperative, with the motivation in the body when useful.

## Issue Reports

| Bug reports should include | Model-serving reports should additionally include |
| --- | --- |
| Installation method (Docker, Python, WSL, etc.) | Backend (Ollama, vLLM, SGLang, llama.cpp, LM Studio, etc.) |
| OS, browser, and device when relevant | Model name |
| Exact reproduction steps | GPU/CPU and operating system |
| Expected versus actual behavior | Cookbook task logs or backend/server logs |
| Relevant logs, screenshots, or terminal output | Relevant backend configuration |

Issues containing only "help", "does not work", or an unexplained screenshot may be closed as not actionable. Start with a reproducible problem or use [Discussions](https://github.com/odysseus-dev/odysseus/discussions) for setup questions.

## Security

> [!CAUTION]
> **Do not publish secrets.** Keep API keys, sensitive logs, personal documents, and public IP addresses out of public issues and PRs. Report vulnerabilities privately through [SECURITY.md](SECURITY.md).
