# Historical Odysseus QA Queue

- Source sessions: 626
- Unique conversation flows: 54
- Historical labels are conservative; `replay_first` must be replayed before assigning ownership.

## Workstreams

- `harness`: 1
- `model_sft`: 0
- `backend`: 0
- `replay_first`: 53

## Families

- `calendar`: 4
- `cookbook_admin`: 3
- `documents`: 3
- `email`: 4
- `memory`: 3
- `notes`: 5
- `search_browser`: 16
- `shell_files`: 3
- `skills`: 3
- `switching`: 7
- `tasks`: 3

## Workflow

1. Replay `replay_first` cases on the current 7011 Agent runtime.
2. Judge with the complete Odysseus tool catalog.
3. Move reproducible failures to `harness`, `model_sft`, or `backend`.
4. Fix recurring behavior classes and replay every member of that class.
