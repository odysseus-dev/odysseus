# desktop-action — Spec Delta

## ADDED Requirements

### Requirement: Clicky action bridge
The system SHALL extend the Clicky bridge beyond launch/status with a `desktop_act` agent tool supporting pointer actions (`move`, `click`, `double_click`, `drag`) at screen coordinates or at a target resolved from a recent `screen_look` frame, executed through the Clicky worker.

#### Scenario: Click a visible control
- **WHEN** the agent calls `desktop_act` with `action="click"` and a target matching text found by `screen_look`
- **THEN** the Clicky worker resolves the on-screen coordinates from the OCR frame geometry and performs the click, returning the resolved coordinates in the result

### Requirement: Audio actions respect the mic lease
`desktop_act` SHALL support audio actions (`speak` via TTS, `listen` via STT) that acquire the shared mic lease before listening and release it after; if the lease is held by another session, the tool returns a busy result instead of grabbing the device.

#### Scenario: Listen while voice session active
- **WHEN** the agent calls `desktop_act` with `action="listen"` while an Odysseus realtime voice session holds the mic lease
- **THEN** the tool returns `ok: false, reason: mic_busy` without interrupting the voice session

### Requirement: Desktop actions are consent-gated per session
The system SHALL require explicit user consent before the first desktop action in a chat session (via the existing `ask_user` flow or a UI toggle). Consent SHALL expire with the session, and every executed action MUST be appended to an audit log (timestamp, action, target, initiating session).

#### Scenario: First action prompts for consent
- **WHEN** the agent attempts its first `desktop_act` in a session without prior consent
- **THEN** the action is held, the user is asked to approve desktop control, and the action executes only after approval

#### Scenario: Actions audited
- **WHEN** any desktop action executes
- **THEN** an audit entry is written with timestamp, action type, target, and session id

### Requirement: Clicky offline degrades cleanly
When the Clicky worker is not running, `desktop_act` SHALL return a degraded result with the hint `POST /api/clicky/start` (or `deploy/scripts/start-clicky.ps1`) rather than raising.

#### Scenario: Action with worker down
- **WHEN** `desktop_act` is called and the Clicky worker health check fails
- **THEN** the tool returns `ok: false, reason: clicky_offline` with the launch hint
