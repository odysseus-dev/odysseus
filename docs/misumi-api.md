# Misumi compatibility API

Odysseus exposes a Misumi-facing compatibility surface while retaining its generic UI and APIs. Personas select context; Odysseus enforces permissions.

Only `GET /misumi/health` is unauthenticated. Every other route uses normal Odysseus cookie or bearer-token auth. Interface API tokens should carry `misumi:read` and `misumi:execute`; the latter currently permits planning only because Phase A exposes no household write or task-execution path.

## Health

```http
GET /misumi/health
```

Reports liveness, Phase A, and household-root reachability. Use authenticated `GET /api/ready` and `GET /misumi/status` for dependency state.

## Respond

```http
POST /misumi/respond
Authorization: Bearer ody_...
Content-Type: application/json

{
  "prompt": "What is on the shopping list?",
  "persona": "sanji",
  "mood": "focused"
}
```

Response:

```json
{
  "text": "From household/food/shopping-list.md line 3: - [ ] miso",
  "state": "speaking",
  "mood": "focused",
  "source": "odysseus",
  "persona": "sanji",
  "who": "Sanji",
  "audio_url": null,
  "voice": null,
  "tts_provider": null,
  "sources": [
    {"path": "household/food/shopping-list.md", "line": 3, "snippet": "- [ ] miso", "score": 1}
  ]
}
```

Repository-backed answers cite the canonical path and line. Missing data or model state degrades explicitly; no action is claimed.

## Task planning

```http
POST /misumi/task
Authorization: Bearer ody_...
Content-Type: application/json

{
  "prompt": "autonomously complete agentic routed tasks",
  "persona": "aoteru",
  "mode": "task",
  "approval": "none"
}
```

Task mode scans the documented file queues, ranks the critical path, and returns `planned` or `blocked` with candidates, files read, blockers, policy, and a handoff prompt. It never squeezes task state into chat text and never mutates the household repository.

Valid approval values are `none`, `plan_only`, `approved_read_only`, and `approved_execute`. Approval cannot enable Phase B household writes.

## Personas and skills

```http
GET /misumi/personas
GET /misumi/personas/kurisu/skills
POST /misumi/personas/kurisu/skills/audit
```

Skill lists are filtered by the versioned persona policy. Audits are admin-only and do not publish external skills.

## External skill intake

```http
POST /misumi/skills/import-draft
GET /misumi/skills/security-review/{skill}
```

Imports accept supported GitHub/skills.sh URLs through the existing constrained fetcher. Every external skill is forced to draft, scripts are stored only as text, no bundle code runs, and static security flags are returned. Publication requires a separate human/admin review path.

## Status

```http
GET /misumi/status
```

Reports readiness, household reachability and Git dirtiness, task counts, persona skill counts, recent event-log count, Phase A, and `writes_allowed: false`.

## Interface-box configuration

After side-by-side validation, set the box-local `agentUrl` to:

```text
http://DESKTOP-IN7O23D:420/misumi
```

The interface-box server appends `/respond`. Store `ODYSSEUS_API_TOKEN` only in the box process environment; its proxy adds the Authorization header server-side. Keep port 4500 as the fallback until end-to-end evals pass.
