# Direct Agent Interaction Guide

This guide explains how to interact with the Odysseus agent programmatically for testing, debugging, and feature development.

## Prerequisites
- Running Odysseus container on `localhost:7000`
- Auth cookie from login
- Session ID (create via `/api/session`)
- Admin privileges (for email tools and other restricted features)

## Step 1: Login
```bash
curl -s -X POST http://localhost:7000/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"<user>","password":"<pass>"}' \
  -c /tmp/odysseus-cookies.txt
```

## Step 2: Create a Session
```bash
curl -s -X POST http://localhost:7000/api/session \
  -H "Content-Type: application/json" \
  -d '{"name":"Test Session","mode":"agent"}' \
  -b /tmp/odysseus-cookies.txt
```
Response contains `"id"` — this is the `session_id`.

## Step 3: Send a Message
```bash
curl -s -X POST http://localhost:7000/api/chat_stream \
  -F "message=<your prompt>" \
  -F "session=<session_id>" \
  -F "mode=agent" \
  -b /tmp/odysseus-cookies.txt
```
Use `--data-binary @-` with `stream=true` for SSE streaming.

## Step 4: Inspect Results
- **Database:** `data/app.db` → `chat_messages` table filtered by `session_id`
- **Container logs:** `docker logs odysseus-odysseus-1 --tail 200`
- **Debug logs:** Look for `[tool-debug]`, `[agent-intent]`, `[agent-round]`, `Tool executed`

## Quick Test Script (Python)
```python
import requests, json

base = "http://localhost:7000"
cookies = {"odysseus_session": "<cookie_value>"}
session_id = "<session_id>"

resp = requests.post(
    f"{base}/api/chat_stream",
    data={
        "message": "your prompt here",
        "session": session_id,
        "mode": "agent"
    },
    cookies=cookies,
    timeout=300,
    stream=True
)

for line in resp.iter_lines(decode_unicode=True):
    if not line or not line.startswith("data:"):
        continue
    payload = line[5:].strip()
    if payload == "[DONE]":
        break
    try:
        data = json.loads(payload)
        delta = data.get("delta", "")
        if delta:
            print(delta, end="")
    except json.JSONDecodeError:
        pass
```

## Important Notes
- **Admin required:** Email and other sensitive tools are blocked for non-admin users. Set `is_admin: true` in `data/auth.json` for the test user.
- **Container must be rebuilt** after code changes: `docker compose build odysseus && docker compose up -d odysseus`
- **OCR requires optional build:** `docker compose build --build-arg INSTALL_OPTIONAL=true odysseus`
- **Debug logs:** Always check container logs first when behavior doesn't match expectations. The `[tool-debug]` lines show exactly which tools were selected, filtered, and sent to the model.
