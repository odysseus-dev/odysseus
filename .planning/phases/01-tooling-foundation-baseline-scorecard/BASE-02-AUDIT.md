# BASE-02 — Revert Dead-Code Audit (SC#4)

**Requirement:** BASE-02 / ROADMAP SC#4 — the two reverts left no orphaned imports,
config, or stub code; `ruff` reports no `F401`/`F811` attributable to removed symbols.

**Conclusion: SATISFIED — both reverts are clean.** This is a *verification* task
(research already verified both reverts during planning); no removal was required. The six
audit commands below were re-run on the Phase-1 branch as recorded evidence. All returned
the expected empty / clean result.

## Reverts under audit

| Commit | Subject |
|--------|---------|
| `67b63e9` | Revert "fix(ui): allow manual prompt bar resize (#1201)" |
| `1f6c5ac` | Revert "Codex Agent integration: HTTP surface + plugin bundle + Settings UI" |

## Audit commands and outputs

### 1. `git show 67b63e9 --stat` — confirm reverted file set
```
Revert "fix(ui): allow manual prompt bar resize (#1201)"
 static/js/ui.js                        | 22 +++-------------------
 static/style.css                       |  4 ++--
 tests/test_prompt_bar_manual_resize.py | 16 ----------------
 3 files changed, 5 insertions(+), 37 deletions(-)
```
The reverted test file is gone; `ui.js` retains only the unrelated auto-resize-textarea /
calendar-pane `resize` code; the `style.css` change is a comment on the model-compare selector.

### 2. `git show 1f6c5ac --stat` — confirm reverted file set
```
Revert "Codex Agent integration: HTTP surface + plugin bundle + Settings UI"
 app.py                                       |  11 +-
 integrations/codex/.codex-plugin/plugin.json |  22 ---
 integrations/codex/README.md                 |  51 ------
 integrations/codex/scripts/odysseus_api.py   | 122 -------------
 integrations/codex/skills/odysseus/SKILL.md  |  64 -------
 routes/api_token_routes.py                   | 103 +----------
 routes/codex_routes.py                       | 169 ------------------
 static/js/settings.js                        | 253 +--------------------------
 8 files changed, 5 insertions(+), 790 deletions(-)
```
`integrations/codex/` and `routes/codex_routes.py` were deleted; `app.py`,
`api_token_routes.py`, and `settings.js` had their codex hooks removed.

### 3. `git grep -n -i 'codex' -- app.py routes/ static/js/ | grep -vi 'model_routes'`
```
(empty)
```
**CLEAN.** The only `codex` hits in the codebase are in `routes/model_routes.py:425-426`
(`"-codex"`, `"codex-"`) — unrelated OpenAI model-name filter strings that predate and are
independent of the reverted integration. They are correctly excluded by the `model_routes` filter.

### 4. `git grep -n 'promptBarResize\|prompt-bar-resize\|manual.*prompt.*resize' -- static/`
```
(empty)
```
**CLEAN.** No orphaned prompt-bar manual-resize handler, CSS class, or markup remains.

### 5. `ls integrations/codex routes/codex_routes.py 2>/dev/null`
```
ls: cannot access 'integrations/codex': No such file or directory
ls: cannot access 'routes/codex_routes.py': No such file or directory
```
**CLEAN.** Neither the codex integration directory nor its route module exists.

### 6. `ruff check . --select F401,F811` (ruff 0.15.15, python:3.12-slim)
```
All checks passed!
EXIT=0
```
**CLEAN.** Zero unused-import (`F401`) or redefinition (`F811`) findings — no orphaned imports
left by either revert. (Auto-satisfied by Plan 02's zero-finding clean baseline.)

## Result

All six audit commands return the expected empty / clean output. No orphaned imports,
configuration, stub code, or dead handlers remain from either revert. **BASE-02 / SC#4 is
satisfied** with no code removal needed this plan.
