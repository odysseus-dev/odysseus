# Changelog

## 1.1.0

- Track the current Odysseus `dev` line (588 upstream commits since 1.0.0),
  including the photo editor, tool-approval mode, local MCP tool declarations
  and the new search/agent fixes.
- Image now inherits the upstream runtime hardening: refreshed system and Python
  CA bundles with the eMudhra distrust checks, `NODE_OPTIONS=--use-openssl-ca`,
  `GRPC_DEFAULT_SSL_ROOTS_FILE_PATH`, and the Chromium policy override.
- Chromium gains `fonts-noto-cjk` + `fontconfig` so CJK pages render glyphs, and
  the image ships the network inspection tools (`iproute2`, `iputils-ping`,
  `net-tools`, `dnsutils`, `nmap`) used by agent tasks.
- Added `agent-browser` so the native `private_browser` tool drives the bundled
  Chromium without per-call `npx` installs.
- New dependencies from upstream: `pypdfium2`, `pdfplumber`, `faster-whisper`,
  `yt-dlp`, `yt-dlp`-backed media tools, `Pillow>=13`, `certifi>=2026.10.23`,
  `grpcio>=1.84.1`, `fastembed>=0.9.1`.
- New options: `tool_approval_gate` (default on) and `response_compression`
  (default on). Extra environment variables documented in `DOCS.md`.
- Base image pinned to `python:3.14.9-slim`; `/data/.local/bin` is on `PATH` so
  `pip --user` installs from the Cookbook resolve.

## 1.0.0

- Initial Home Assistant OS add-on package.
- Direct web UI / API access on port `7000`.
- Model provider dropdown + API key (seeds a ready-to-use model endpoint).
- Database options (SQLite on `/data`, or an external PostgreSQL server).
- Web search provider options (SearXNG, Brave, Tavily, Serper, Google PSE, …).