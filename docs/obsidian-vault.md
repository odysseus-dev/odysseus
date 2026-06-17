# Using an Obsidian vault with Odysseus

Odysseus can read, search, and write Markdown notes in an
[Obsidian](https://obsidian.md) vault, so the assistant works against the same
knowledge base you edit by hand. This is a recipe, not a built-in feature —
it wires existing pieces together: a mounted folder, the workspace-confined
file tools, and (optionally) a tiny host bridge for opening notes in the
desktop app.

The design goal is **least privilege**: give the assistant access to *one
folder* (your vault), not your whole machine.

## 1. Mount the vault into the container

Add your vault as a volume in `docker-compose.yml` (or your override file) so it
appears inside the workspace the assistant is confined to. Use your own paths:

```yaml
services:
  odysseus:
    volumes:
      # <host path to your vault> : <path inside the container workspace>
      - /path/to/your/ObsidianVault:/mnt/vault
```

Notes:

- The container-side path must live under the workspace root the file tools are
  confined to (`/mnt/...` by default). A path outside it is rejected by design —
  that confinement is the safety boundary, don't disable it.
- On Windows/WSL the host path looks like `C:/Users/you/Documents/MyVault` or
  `/mnt/c/Users/you/Documents/MyVault`. On macOS/Linux it's a normal absolute
  path.
- Mount read-only (`:ro` suffix) if you only want the assistant to *read* the
  vault and never modify it.

Restart so the mount takes effect:

```bash
docker compose up -d odysseus
```

## 2. Point the assistant at the vault

Tell the assistant where the vault is. The simplest, durable way is a memory:

> Remember: my Obsidian vault is mounted at `/mnt/vault`. Read and write notes
> there as Markdown.

From then on, requests like *"add a note about X to my vault"*, *"what do my
notes say about Y"*, or *"summarize this week's journal entries"* operate on the
mounted folder.

The relevant tools (all workspace-confined):

- **`download_file`** — fetch a URL straight into the vault (e.g. save a
  reference page as a Markdown attachment).
- **`delete_file`** — remove a note (confined to the workspace, so it can't
  touch anything outside the vault mount).
- Document / notes tools — create and edit Markdown content.

Keep the vault under the workspace and let these confined tools do the work.
Do **not** route this through unrestricted shell access just to touch files —
that throws away the confinement boundary for no benefit.

## 3. (Optional) Open notes in the Obsidian desktop app

The container can read/write files, but it can't launch your desktop Obsidian.
If you want *"open that note in Obsidian"* to actually pop the app, run a small
HTTP bridge on the host that the container calls to fire `obsidian://` URIs.

Minimal pattern (adapt to your stack — Flask shown for brevity):

```python
# obsidian_bridge.py  — runs on the HOST, not in the container
import os, subprocess
from flask import Flask, request, abort

app = Flask(__name__)
TOKEN = os.environ["OBSIDIAN_BRIDGE_TOKEN"]  # set your own; never commit it

@app.post("/open")
def open_uri():
    if request.headers.get("Authorization") != f"Bearer {TOKEN}":
        abort(401)
    uri = request.json.get("uri", "")
    if not uri.startswith("obsidian://"):
        abort(400)
    # Windows: os.startfile(uri) | macOS: ["open", uri] | Linux: ["xdg-open", uri]
    os.startfile(uri)  # noqa: example for Windows
    return {"ok": True}

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=9731)
```

From inside the container, reach the host bridge (Docker Desktop exposes the
host as `host.docker.internal`):

```bash
curl -s -X POST http://host.docker.internal:9731/open \
  -H "Authorization: Bearer $OBSIDIAN_BRIDGE_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"uri": "obsidian://open?vault=MyVault&file=Some%20Note"}'
```

Security notes for the bridge:

- **Pick your own token** via the `OBSIDIAN_BRIDGE_TOKEN` env var and keep it out
  of git. The example above reads it from the environment for exactly this
  reason.
- Bind to `127.0.0.1` only; never expose the bridge on a public interface.
- Restrict it to `obsidian://` URIs (as shown) so it can't be turned into a
  general "run anything on my host" endpoint.
- URL-encode spaces in vault/file names as `%20`.

## Why not just give the assistant full filesystem/shell access?

Because a notes vault is exactly the kind of thing that benefits from a tight
blast radius. Untrusted content can end up in your notes (pasted text, clipped
web pages, email). Confining writes to a single mounted folder, and keeping the
host bridge limited to `obsidian://`, means a bad instruction can mangle a note
at worst — not reach the rest of your machine. See `THREAT_MODEL.md` for the
project's broader stance on confinement.
