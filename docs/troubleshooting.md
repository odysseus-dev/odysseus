<sub>[← Back to the README](../README.md) · [GPU and Cookbook](gpu-and-cookbook.md)</sub>

# Troubleshooting

## `chromadb-client` conflicts with embedded ChromaDB
If `chromadb-client` (the lightweight HTTP-only package) is installed alongside
the full `chromadb` package, Odysseus starts but ChromaDB silently falls back to
HTTP-only mode and fails.

**Fix:** uninstall `chromadb-client` and force-reinstall the full package:
```bash
./venv/bin/pip uninstall chromadb-client -y
./venv/bin/pip install --force-reinstall chromadb
```

### Manual installs: vector memory degraded without a ChromaDB service
`requirements.txt` pins `chromadb-client`, the HTTP-only client. If you set up
with the manual `pip install -r requirements.txt` path (Native Linux / macOS),
you get the client rather than the embedded engine — so vector memory, RAG, and
the tool index look for a ChromaDB service on `localhost:8100` and log
`ChromaDB is not reachable` / `MemoryVectorStore DEGRADED` when none is running.
The core app still works; only vector features are affected.

Two ways to fix it:

- **Embedded (no separate service)** — swap to the full package:
  ```bash
  ./venv/bin/pip uninstall chromadb-client -y
  ./venv/bin/pip install chromadb
  ```
  (The Docker setup and `start-macos.sh` already do this for you.)
- **Service** — run a ChromaDB service and point `CHROMADB_HOST` / `CHROMADB_PORT`
  at it. Docker Compose starts one automatically.

## HTTPS + LAN / Tailscale exposure
To expose Odysseus on a local network or Tailscale with HTTPS:
1. Change the bind address to `0.0.0.0` in `.env` (`APP_BIND=0.0.0.0` or `ODYSSEUS_HOST=0.0.0.0`).
2. Generate a locally-trusted cert for your LAN/Tailscale IPs using [mkcert](https://github.com/FiloSottile/mkcert):
   ```bash
   mkcert -install
   mkcert -cert-file cert.pem -key-file key.pem 192.168.1.100 tailscale-ip
   ```
3. Run `uvicorn` with the generated certs:
   ```bash
   python -m uvicorn app:app --host 0.0.0.0 --port 7000 --ssl-certfile=cert.pem --ssl-keyfile=key.pem
   ```
4. Install the `mkcert` CA on any other device you want to access Odysseus from
   (e.g., for iOS, email the `rootCA.pem` to yourself, install the profile, and
   trust it in Certificate Trust Settings).

Keep `AUTH_ENABLED=true` and do not expose the app port directly to the public
internet. See [Security](../README.md#security).

## Optional dependencies
`requirements-optional.txt` contains packages that unlock extra features. It is
not installed by default.

| Package | Feature unlocked |
|---------|-----------------|
| `faster-whisper` | Local speech-to-text (microphone -> text) via the "local" STT provider. |
| `duckduckgo-search` | DuckDuckGo as a search provider option. |
| `PyMuPDF` | PDF page rendering in the side viewer panel and form-filling. (Note: AGPL-3.0) |
| `markitdown` | Office/EPUB document text extraction (converts .docx/.xlsx/.pptx/.xls/.epub to Markdown). |

---
<sub>[← Back to the README](../README.md) · [GPU and Cookbook](gpu-and-cookbook.md)</sub>
