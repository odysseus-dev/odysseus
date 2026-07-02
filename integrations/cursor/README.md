# Cursor Bridge

This bridge exposes a minimal OpenAI-compatible API on the host and forwards
requests to Cursor's local SDK runtime.

Supported routes:

- `GET /v1/models`
- `POST /v1/chat/completions`

The bridge is meant to be run on the host while Odysseus runs in Podman.

## Install

Use the existing Odysseus virtualenv or any Python 3.11+ environment:

```bash
cd /var/home/joe/Downloads/odysseus
source venv/bin/activate
pip install -r requirements-optional.txt
```

## Environment

Required:

```bash
export CURSOR_API_KEY="cursor_..."
```

Recommended:

```bash
export CURSOR_BRIDGE_API_KEY="choose-a-shared-secret"
export CURSOR_BRIDGE_CWD="/var/home/joe/Downloads/odysseus"
export CURSOR_BRIDGE_DEFAULT_MODEL="composer-2.5"
```

Optional model control:

```bash
# Expose aliases or pin the visible model list.
export CURSOR_BRIDGE_MODEL_MAP="composer-2.5=composer-2.5"
```

## Run

```bash
cd /var/home/joe/Downloads/odysseus
source venv/bin/activate
uvicorn integrations.cursor.bridge:app --host 0.0.0.0 --port 8011
```

Health check:

```bash
curl http://127.0.0.1:8011/health
```

Model list:

```bash
curl -H "Authorization: Bearer $CURSOR_BRIDGE_API_KEY" \
  http://127.0.0.1:8011/v1/models
```

Chat test:

```bash
curl -H "Authorization: Bearer $CURSOR_BRIDGE_API_KEY" \
  -H "Content-Type: application/json" \
  http://127.0.0.1:8011/v1/chat/completions \
  -d '{
    "model": "composer-2.5",
    "messages": [{"role": "user", "content": "Say hello in one sentence."}]
  }'
```

## Odysseus Provider Settings

Because the Odysseus app container already resolves `host.docker.internal`, use:

```text
Base URL: http://host.docker.internal:8011/v1
API key:  <the same value as CURSOR_BRIDGE_API_KEY>
```

Then select one of the model IDs returned by `GET /v1/models`.

For Podman, binding the bridge to `127.0.0.1` is not enough for container
access. Use `--host 0.0.0.0` or another host-reachable interface when Odysseus
runs in a container.

## Notes

- This bridge uses Cursor's local SDK runtime, which still requires a real
  `CURSOR_API_KEY`.
- If Odysseus does not send a stable conversation key, the bridge falls back to
  stateless prompt flattening.
- If a client sends `user` or `X-Session-Id`, the bridge reuses a Cursor agent
  across turns for that key.
