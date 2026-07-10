# Misumi Phase A autonomy pilots

All pilots are disabled by default in `config/misumi_autonomy.json`. They read the canonical household repository and may write structured output only under the external Odysseus data directory. They do not write household files, run Git mutation commands, send messages, or invoke shell tools.

## Manual evaluation

```powershell
python scripts/run_misumi_pilot.py morning-status --manual
python scripts/run_misumi_pilot.py skill-audit --manual
python scripts/run_misumi_pilot.py task-triage --manual
python scripts/run_misumi_pilot.py household-qa --manual --question "What is on the shopping list?"
```

Each output includes `household_unchanged`. A false value is a hard failure.

## Scheduling gate

Do not register a Windows scheduled task until the corresponding manual output is useful. To enable one pilot, set both the top-level `enabled` value and that pilot's `enabled` value to true in host-local reviewed configuration. Keep the versioned defaults disabled.

Suggested order:

1. morning status;
2. task triage;
3. skill audit;
4. household question answering remains request-driven.

## Rollback

Disable the pilot config or remove its scheduled-task registration. Preserve logged output for diagnosis. No household rollback is required because the adapter has no write operation.
