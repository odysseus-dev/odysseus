# Codex Model Provider Docker Validation

This validates the experimental status probe in the real Docker deployment
without exposing Codex in the model picker and without adding a `codex exec`
chat adapter.

Run on the Proxmox/Docker host:

```bash
cd /opt/odysseus
git fetch fork codex-model-provider-draft
git switch codex-model-provider-draft
git pull --ff-only fork codex-model-provider-draft

ODYSSEUS_ADMIN_USER=admin \
ODYSSEUS_ADMIN_PASSWORD='your-admin-password' \
scripts/validate_codex_model_provider_docker.sh
```

If the admin account uses TOTP, add:

```bash
ODYSSEUS_ADMIN_TOTP=123456
```

The script validates:

- unauthenticated access to `/api/codex-model-provider/status` is rejected
- feature flag disabled returns `status=disabled`
- feature flag enabled plus logged-out Codex returns `status=sign_in_required`
- feature flag enabled plus logged-in Codex returns `status=available`
- the synthetic model id is `codex-cli/chatgpt-experimental`
- `POST /api/codex-model-provider/test-chat` returns a completed assistant message
- status reports chat support only when the adapter passes safety preflight
- streaming, session resume, and tool execution support remain false
- response bodies do not include token-like fields such as access or refresh tokens
- logout returns the provider status to `sign_in_required`

The script temporarily edits the deployment `.env` file to toggle
`ODYSSEUS_CODEX_MODEL_PROVIDER_ENABLED`, recreates only the `odysseus` service,
restores `.env` before exit, and removes its temporary `.env` backup. It does
not modify Dockerfile or Compose files.

It intentionally logs Codex out during validation. That is part of the requested
state coverage and cannot be undone without signing in again.
