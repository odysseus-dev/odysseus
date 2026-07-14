# browser-action — Spec Delta

## ADDED Requirements

### Requirement: CDP connection to the user's Chrome
The system SHALL provide a browser harness service that connects to the user's already-running Chrome via the DevTools protocol (default `http://localhost:9222`), listing open tabs and attaching to a chosen tab. It MUST NOT launch a separate headless browser by default.

#### Scenario: Attach to an open tab
- **WHEN** the agent calls `browser_act` with `action="tabs"`
- **THEN** the tool returns the open tabs (title, URL, target id) from the CDP endpoint

### Requirement: Core browser actions
The `browser_act` agent tool SHALL support: `navigate` (URL), `snapshot` (accessibility/DOM outline of the active tab), `click` and `type` (by selector or snapshot node reference), and `evaluate` (JavaScript expression returning JSON-serializable data).

#### Scenario: Navigate and read a page
- **WHEN** the agent calls `browser_act` with `action="navigate"` to a URL, then `action="snapshot"`
- **THEN** the tab loads the URL and the snapshot returns the page's interactive elements with stable node references

### Requirement: Browser actions are consent-gated and audited
Browser actions that mutate state (`navigate`, `click`, `type`, `evaluate`) SHALL be covered by the same per-session consent gate and audit log as desktop actions. Read-only actions (`tabs`, `snapshot`) SHALL NOT require consent.

#### Scenario: Snapshot without consent
- **WHEN** the agent calls `browser_act` with `action="snapshot"` before consent is granted
- **THEN** the snapshot succeeds because it is read-only

#### Scenario: Click requires consent
- **WHEN** the agent calls `browser_act` with `action="click"` before consent is granted
- **THEN** the action is held pending user approval

### Requirement: CDP unavailable degrades cleanly
When no Chrome debugging endpoint is reachable, `browser_act` SHALL return a degraded result explaining how to start Chrome with `--remote-debugging-port=9222`, and MUST NOT raise into the agent loop.

#### Scenario: Chrome not in debug mode
- **WHEN** `browser_act` is called and the CDP endpoint refuses connection
- **THEN** the tool returns `ok: false, reason: cdp_unreachable` with the launch flag hint
