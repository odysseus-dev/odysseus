# Odysseus

Self-hosted AI workspace for chat, agents, deep research, documents, email,
notes, tasks and calendar, packaged for Home Assistant OS.

## Installation

1. In Home Assistant open **Settings → Add-ons → Add-on Store**.
2. Open the three-dot menu (top right) and choose **Repositories**.
3. Paste this repository URL and click **Add**:

   ```
   https://github.com/odysseus-dev/odysseus
   ```
4. Close the dialog. **Odysseus** now appears in the store — select it and click
   **Install**.
5. Open the **Configuration** tab, set at least your model provider or LLM host,
   then click **Start**.
6. Open the web UI with the **OPEN WEB UI** button, or at
   `http://<home-assistant-host>:7000`.

The first build clones and installs the Odysseus application, so installation
takes a few minutes. Later updates reuse the cached dependency layer.

## Access

| Way in | Address | Authentication |
| --- | --- | --- |
| Direct port | `http://<home-assistant-host>:7000` | Odysseus login |

Set the host port to `null` in the add-on **Network** settings to disable direct
access.

Serve it over your own reverse proxy or VPN for remote access. Plain HTTP on a
non-loopback host does not get browser "secure context" features (clipboard,
camera); use HTTPS if you need those.

## Configuration

```yaml
auth_enabled: true          # Require login
admin_user: ""              # Optional initial local admin username
admin_password: ""          # Optional initial local admin password
llm_provider: none          # openai | anthropic | openrouter | groq | deepseek | google | mistral | none
llm_api_key: ""             # API key for the selected provider (stored encrypted)
llm_model: ""               # Optional model ID to pin (else pick from the list in the app)
db_type: sqlite             # sqlite | postgres
db_url: ""                  # Optional full connection string (overrides the fields below)
db_host: ""                 # PostgreSQL host
db_port: ""                 # PostgreSQL port (default 5432)
db_name: ""                 # PostgreSQL database (default odysseus)
db_user: ""                 # PostgreSQL user
db_password: ""             # PostgreSQL password
search_provider: none       # searxng | brave | tavily | serper | google_pse | duckduckgo | disabled | none
search_url: ""              # SearXNG base URL, e.g. http://searxng:8080
search_api_key: ""          # API key for brave/tavily/serper/google_pse
llm_host: localhost         # Default LLM server host
ollama_base_url: ""         # e.g. http://host.docker.internal:11434
allowed_origins: []         # Extra CORS origins
bind_address: ""            # Empty = dual-stack "::" when IPv6 is available
extra_env: []               # Free-form KEY=VALUE entries
```

### Model provider

Pick your provider in the **Model provider** dropdown and paste the key into
**Provider API key**. On start the add-on creates a shared model endpoint for
that provider (`OpenAI (add-on)`, `OpenRouter (add-on)`, …) with the correct
base URL and the key stored encrypted in `/data`. The model list is discovered
automatically; optionally pin one with **Default model**:

| Provider | Base URL |
| --- | --- |
| OpenAI | `https://api.openai.com/v1` |
| Anthropic | `https://api.anthropic.com/v1` |
| OpenRouter | `https://openrouter.ai/api/v1` |
| Groq | `https://api.groq.com/openai/v1` |
| DeepSeek | `https://api.deepseek.com/v1` |
| Google Gemini | `https://generativelanguage.googleapis.com/v1beta/openai` |
| Mistral | `https://api.mistral.ai/v1` |

Local servers (Ollama, LM Studio) are configured with `ollama_base_url` or in
the app itself. The add-on option is authoritative and rewrites the endpoint key
on every start.

### Database

By default everything lives in SQLite on the `/data` volume — no configuration
needed. To use an external **PostgreSQL** server:

```yaml
db_type: postgres
db_host: 192.168.1.20
db_port: "5432"
db_name: odysseus
db_user: odysseus
db_password: "your password"
```

The user and password are percent-encoded, so special characters are safe. For
anything more exotic set `db_url` to a full SQLAlchemy string
(`postgresql+psycopg2://user:pass@host:5432/odysseus`); it overrides the fields.

Switching databases does **not** migrate existing data — point the add-on at an
empty database or import your SQLite data yourself. The schema is created on
first start.

### Web search

Pick the backend in **Search provider**:

| Provider | `search_provider` | Needs |
| --- | --- | --- |
| SearXNG | `searxng` | `search_url` (or a bundled/host instance) |
| Brave Search API | `brave` | `search_api_key` |
| Tavily | `tavily` | `search_api_key` |
| Serper.dev | `serper` | `search_api_key` |
| Google Programmable Search | `google_pse` | `search_api_key` |
| DuckDuckGo (no key) | `duckduckgo` | – |
| Disabled | `disabled` | – |

The provider is written into the app settings on start (add-on option is
authoritative). API keys are passed as environment variables, so a key set
inside the app takes precedence over the add-on option.

Anything Odysseus supports through environment variables can be passed with
`extra_env`, for example:

```yaml
extra_env:
  - TAVILY_API_KEY=...
  - ODYSSEUS_INPROCESS_TASKS=1
```

## Data and backups

All state is stored on the add-on's `/data` volume (Odysseus `data/`, SQLite
database, uploads, sessions, encryption key). Home Assistant backups include it
automatically. `/share` is mounted read-write at `/share` for exchanging files
with other add-ons.

## Local models

To reach an Ollama instance on the Home Assistant host, set:

```yaml
ollama_base_url: http://host.docker.internal:11434
```

The add-on runs inside the Supervisor network, so host services are reachable
via `host.docker.internal` or by IP address.

## Notes and limitations

- The add-on clones the Odysseus source at build time. Build your own fork or a
  pinned release by changing `ODYSSEUS_REPO` / `ODYSSEUS_REF` in
  `hass-addon/Dockerfile`.
- The Cookbook "install Real-ESRGAN" path is not pre-staged in this image
  (upstream's image builds patched wheels for it). Everything else is unchanged.
- Cookbook model downloads and some agent features are disk- and memory-heavy;
  make sure the host has enough resources.