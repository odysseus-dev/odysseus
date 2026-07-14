# screen-perception — Spec Delta

## ADDED Requirements

### Requirement: Live screen snapshot via Screenpipe OCR
The system SHALL provide a `screen_look` agent tool that queries the Screenpipe API (default `http://localhost:3030`) for the most recent OCR frames and returns the visible text with window titles, app names, and capture timestamps.

#### Scenario: Agent reads the current screen
- **WHEN** the agent calls `screen_look` with no arguments
- **THEN** the tool returns OCR text from frames captured within the last 60 seconds, grouped by window, newest first

### Requirement: Recent-history OCR query
The `screen_look` tool SHALL accept an optional `query` string and `minutes` lookback (default 5, max 120) and return only OCR frames whose text matches the query within that window.

#### Scenario: Find recent on-screen text
- **WHEN** the agent calls `screen_look` with `query="TypeError"` and `minutes=30`
- **THEN** the tool returns matching frames with timestamp, window title, and the matched text excerpt, or an empty result set if nothing matched

### Requirement: Screen perception results are size-bounded
Screen perception results returned to the agent loop SHALL be truncated to a configurable character budget (default 8,000 characters) with an explicit truncation sentinel, so OCR dumps cannot flood the model context.

#### Scenario: Large OCR result truncated
- **WHEN** matching OCR frames exceed the character budget
- **THEN** the result is cut at a frame boundary and ends with a sentinel noting how many frames were omitted

### Requirement: Mic ownership is never taken by screen perception
Screen perception SHALL operate on Screenpipe's screen-only capture and MUST NOT enable Screenpipe audio capture, preserving mic availability for Clicky and Odysseus voice (per the existing `--disable-audio` default).

#### Scenario: Perception with voice active
- **WHEN** an Odysseus voice session holds the mic lease and the agent calls `screen_look`
- **THEN** the call succeeds using screen frames only and the mic lease is untouched
