# START HERE - Juniperus / Gnexus Operations Console

_Juniperus = the local console application.  
_Gnexus Operations Console = the branded frontstage experience inside Juniperus, reachable at `/gnexus`._

---

## 1. Launch

PowerShell 5.1 (Windows):

```powershell
powershell -ExecutionPolicy Bypass -File .\launch-windows.ps1 -Port 7010 -BindHost 127.0.0.1
```

Open http://127.0.0.1:7010 — the main shell is `Juniperus - Gnexus Operations Console`.

---

## 2. Open the Gnexus Operations Console

From the main shell, go to:

- **Cockpit** — `http://127.0.0.1:7010/gnexus`
- Direct room links: `http://127.0.0.1:7010/gnexus/<room>`
- **Local Ollama Models** — `http://127.0.0.1:7010/gnexus/ollama-models`

All rooms are reachable. The cockpit page renders without waiting on API hydration. No endless "Loading..." state.

---

## 3. Import Local Ollama Models (one-time, idempotent)

```powershell
.\venv\Scripts\python.exe .\scripts\gnexus\Import-LocalOllamaModels.py
```

What it does:
- Detects Ollama at `http://127.0.0.1:11434/api/tags`, then `localhost:11434/api/tags`, then `ollama list` CLI.
- Registers ONE endpoint named `Local Ollama (All Models)` with base `http://127.0.0.1:11434/v1`.
- Caches **all discovered model names** on that single endpoint.
- Reports: online/offline, model count, capability classification, fallback model, smoke test result.
- Writes an import receipt to `data/gnexus/receipts/ollama-import-receipt.json`.

Safe to re-run. It updates the existing endpoint instead of creating duplicates.

---

## 4. Use the Operator Loop

The operator loop is the governed end-to-end workflow across all rooms:

1. **Governance** — boundary rules and approval boundaries.
2. **App Dock** — discovered apps / project cards.
3. **Approval Desk** — human decision surface.
4. **Interceptor** — shell and file operation governance.
5. **Diff Gate** — proposed edits become reviewable diffs.
6. **Patch Apply** — apply approved patches, after rollback snapshot.
7. **Verifier Loop** — post-apply verification and repair queue.
8. **Operator Loop** — `/gnexus/operator-loop`routes through all stages.
9. **Memory Routing** — context and model routing.
10. **Live Control** — activation gates and write posture.
11. **Ollama Models** — `/gnexus/ollama-models` for local model readiness.

Create an operation plan via the API:

```bash
curl -X POST -H "Content-Type: application/json" \
  -d '{"intent":"my operation","appId":"my-app"}' \
  http://127.0.0.1:7010/api/gnexus/operator-loop/plan
```

The status page (`/gnexus/operator-loop`) shows queue and ledger counts.

---

## 5. How Approvals Work

- No mutation runs without an explicit human approval.
- The approval desk queues objects with `approved: true` / `confirm: true`.
- The diff gate proposes the change as a unified diff.
- Patch apply checks the approval status before writing anything.
- Rollback snapshots are written before any apply step.

To run the proof:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\gnexus\Run-FullGovernedOperatorSmoke.ps1
```

With `-NoApprove` it stops at the approval gate (correct behavior). Without it, it uses `--approve` to prove the full apply → verify → rollback → verify cycle.

---

## 6. How Rollback Works

Before any patch is applied, the current target file content is saved to a `.snapshot` file. If verification fails or rollback is requested, the target is restored from the snapshot. The verifier loop checks content after rollback to confirm restoration.

---

## 7. Locked vs Live

| Dimension | Default |
|---|---|
| Production mutation | Locked unless explicitly approved |
| External writes | Disabled |
| Connector calls | Disabled / approval-gated |
| Secret storage | Disabled |
| Shell / file writes | Approval required (diff-first) |
| Model endpoint changes | Admin only |
| Vault / credential reads | Blocked |

"Live" means an admin has authorized a specific activation through the live control room. Unauthorized mutation stays locked.

---

## 8. Compatibility

Legacy env-var keys like `ODYSSEUS_INTERNAL_TOKEN`, `ODYSSEUS_FALLBACK_OWNER`, `ODYSSEUS_INTERNAL_TOKEN`, etc. are still honored. New Juniperus aliases (`JUNIPERUS_INTERNAL_TOKEN`) are preferred where present. Internal loopback headers: both `X-Juniperus-Internal-Token` and legacy `X-Odysseus-Internal-Token` are accepted. `juniperus-theme` and `odysseus-theme` localStorage keys are both read.

---

## 9. Troubleshooting

| Symptom | Fix |
|---|---|
| `/gnexus` returns 404 | Static page missing; server-side fallback renders a usable cockpit shell. Regenerate or restore `static/gnexus/index.html`. |
| Room stuck on "Loading..." | Every room has a server-side fallback shell. If you see loading, the inline JS fetch to `/api/gnexus/frontstage/state` is blocked by auth. Try re-logging in. |
| Ollama shows 0 models | Start Ollama (`ollama serve`), pull a model (`ollama pull llama3.2`), then re-run `Import-LocalOllamaModels.py`. |
| Ollama smoke test fails | Check Ollama is running, port 11434 is not blocked, and you have at least one model. Each call is local-only. |
| "login/restart required" card | The local API returned a 403. Restart the server or clear old session cookies. |
| Endpoint not visible in model picker | Re-run the import script. If it still shows `no (run import)`, check the DB at `data/app.db` for a `model_endpoints` row with `base_url = http://127.0.0.1:11434/v1`. |

---

*Workspace root: `C:\Users\iamcy\CymaticsDev`*  
*Juniperus repo: `C:\Users\iamcy\CymaticsDev\00_SYSTEMS\Juniperus`*
