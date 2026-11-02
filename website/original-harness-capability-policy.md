# Original Harness Capability Policy

Odysseus Original is the canonical product and the only agent orchestrator.
Specialized runners are evidence sources, not merge targets.

## Architecture

The supported shape is:

1. One Original agent loop owns prompting, tool selection, policy, evidence,
   recovery, compaction, and completion.
2. Capability contracts describe what the current environment can do.
3. Execution bridges or MCP servers perform work in the owning environment.
4. Personal tools remain available in interactive sessions but are excluded
   from external terminal contracts unless explicitly provided.

## Capability Decisions

| Capability | Decision | Canonical form |
| --- | --- | --- |
| Workspace execution | Keep | Request-scoped `AgentExecutionBridge`, moving toward a Workspace MCP boundary |
| File mutation | Keep | `write_file`, `edit_file`, and `apply_patch` with evidence recording |
| Process recovery | Keep | Bounded polling, timeout, exit status, and stale-process recovery behind the execution contract |
| Repeated-action recovery | Keep | Detect identical calls, unchanged successful results, and failed batches separately |
| Completion | Keep | Required artifacts and executable verifier evidence; no evaluator-specific shortcuts |
| Context control | Keep | Deterministic compaction with retained user evidence and bounded tool output |
| Search | Keep | Existing private SearXNG path |
| Browser interaction | Keep | Existing private browser for rendered pages, sessions, clicks, and screenshots |
| Personal tools | Keep, isolated | Separate personal capability surface; never substitute editor documents for workspace files |
| Tool discovery | Keep | Existing tool RAG and capability-aware selection |
| Media ingress | Keep | Bounded image, audio, video-frame, and document ingestion with hashes and trace metadata |
| Visual verification | Candidate | Generic source grounding and rendered-artifact checks, gated by available media capabilities |
| Document access | Candidate | Structured PDF and Office extraction through `read_file` or a Workspace MCP implementation |
| Skills | Keep, generic only | Procedures for artifact completion, recovery, verification, development, media evidence, and research |

## Rejected Merges

Do not merge:

- another agent loop, submit controller, or conversation state machine;
- category names, task identifiers, fixed workspace paths, expected answers,
  grader behavior, scoring rules, or evaluator prompts;
- per-category turn thresholds or phase transitions;
- terminal multiplexer control when the environment already exposes direct
  process execution;
- repeated prompt guards that do not add new executable evidence;
- browser or search replacements for capabilities Original already owns.

## Merge Gate

A mechanism may enter Original only when all of these are true:

1. It is useful outside the evaluation that revealed it.
2. It is selected by an environment capability, not a task name or path.
3. It fits inside the Original loop or behind an execution boundary.
4. Its success or failure produces traceable evidence.
5. It has focused regressions and a representative held-out run.
6. It removes or contains complexity instead of adding an overlapping mode.

## Current Priority

The next capability work is reliable structured document access. Failed
binary-file inspections must remain failures; they must not be interpreted as
unchanged evidence or trigger early artifact synthesis. Once grounding is
reliable, add generic rendered-artifact verification without importing any
specialized task phases.
