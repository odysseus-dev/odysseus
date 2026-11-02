# Historical Odysseus QA Queue

- Source sessions: 1294
- Source user turns / teacher seeds: 3258
- Unique conversation flows: 596
- Historical labels are conservative; `replay_first` must be replayed before assigning ownership.

## Workstreams

- `harness`: 1
- `model_sft`: 1
- `backend`: 1
- `replay_first`: 593

## Families

- `calendar`: 421
- `cookbook_admin`: 203
- `documents`: 173
- `email`: 359
- `general`: 303
- `memory`: 179
- `notes`: 362
- `research`: 14
- `search_browser`: 459
- `shell_files`: 104
- `skills`: 226
- `switching`: 130
- `tasks`: 197
- `ui`: 128

## Workflow

1. Cook one fresh conversation from every seed using the complete tool catalog.
2. Replay safe cooked cases on the current 7011 Agent runtime.
3. Judge, classify ownership, and patch recurring behavior classes.
4. Retain duplicate source runs as stability evidence; account for quarantined cases explicitly.
