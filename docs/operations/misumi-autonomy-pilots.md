# Misumi Phase A autonomy pilots

All pilots are disabled by default in `config/misumi_autonomy.json`. They read the canonical household repository and may write structured output only under the external Odysseus data directory. They do not write household files, run Git mutation commands, send messages, or invoke shell tools.

## Manual evaluation

```powershell
python scripts/run_misumi_pilot.py morning-status --manual
python scripts/run_misumi_pilot.py skill-audit --manual
python scripts/run_misumi_pilot.py task-triage --manual
python scripts/run_misumi_pilot.py household-qa --manual --question "What is on the shopping list?"
python scripts/run_misumi_pilot.py memory-digest --manual
```

Each output includes `household_unchanged`. A false value is a hard failure.

`memory-digest` is local and manual-only. It reads the append-only Misumi memory stores and writes `DATA_DIR/misumi/memory/digests/<UTC date>-digest.md`. Before writing, it compares path, size, and modification-time snapshots of the household root. Any difference aborts the digest with `household_unchanged: false` and no digest file.

## Scheduling gate

Do not enable a Windows scheduled task until the corresponding manual output is useful. The host installer registers the three schedulable definitions disabled and copies the disabled versioned config to the external data directory:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\windows\misumi-pilots.ps1 -Action Install
powershell -ExecutionPolicy Bypass -File scripts\windows\misumi-pilots.ps1 -Action Status
```

To enable one pilot, set both the top-level `enabled` value and that pilot's `enabled` value to true in `%LOCALAPPDATA%\Odysseus\Misumi\misumi\autonomy.json`, then enable only its scheduled task. Keep the versioned defaults disabled.

Suggested order:

1. morning status;
2. task triage;
3. skill audit;
4. household question answering remains request-driven.
5. memory digest remains manual-only.

## Rollback

Disable the scheduled task and its host-local config entry, or run `misumi-pilots.ps1 -Action Uninstall`. Preserve logged output for diagnosis. No household rollback is required because the adapter has no write operation.
