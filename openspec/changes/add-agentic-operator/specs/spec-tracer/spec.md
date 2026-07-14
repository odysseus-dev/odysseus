# spec-tracer — Spec Delta

> Capture is performed by the existing SpecTracer Chrome extension
> (`C:\Users\tylar\code\Spec_Tracer`, published on the Chrome Web Store).
> This capability covers the Odysseus side (ingest + agent access) and the
> extension's new export target. It does not rebuild the picker.

## ADDED Requirements

### Requirement: SpecTracer export target for Odysseus
The SpecTracer extension SHALL gain a "Send to Odysseus" export action alongside its existing clipboard copy, posting the captured context bundle (element name, label, page/hierarchy path, position, classes, selector, and available console/event tail) to the Odysseus ingest endpoint, authenticated with an Odysseus API token configured in the extension settings.

#### Scenario: Developer sends a capture to Odysseus
- **WHEN** the developer inspects an element in SpecTracer and chooses "Send to Odysseus"
- **THEN** the extension POSTs the context bundle to the configured Odysseus URL with the API token, and shows success/failure feedback in the extension UI

#### Scenario: Odysseus unreachable falls back to clipboard
- **WHEN** the export POST fails (offline, bad token)
- **THEN** the extension still copies the bundle to the clipboard and surfaces the error, so no capture is lost

### Requirement: Context bundle ingest endpoint
The system SHALL expose `POST /api/operator/spec-trace` which authenticates the request, validates the bundle against a size cap (default 256 KB), stores it with a generated trace id, and makes it retrievable by the agent.

#### Scenario: Bundle posted and stored
- **WHEN** the extension posts a valid bundle with a valid API token
- **THEN** the API responds 200 with a `trace_id` and the bundle is retrievable via the `spec_trace` agent tool

#### Scenario: Oversized bundle rejected
- **WHEN** a bundle exceeds the size cap
- **THEN** the API responds 413 and the extension falls back to clipboard copy

### Requirement: Agent access to recent traces
The system SHALL provide a `spec_trace` agent tool that lists recent trace bundles (id, page URL, element summary, age) and fetches a specific bundle by id for use as development context.

#### Scenario: Agent pulls the latest trace
- **WHEN** the user says "look at the element I just grabbed" and the agent calls `spec_trace` with `action="latest"`
- **THEN** the tool returns the most recent bundle's element context (hierarchy, selector, classes, position) and console/event tail

### Requirement: Traces are ephemeral by default
Trace bundles SHALL be retained for a configurable window (default 24 hours or last 50 traces, whichever is smaller) and purged automatically; they are development scratch context, not durable documents.

#### Scenario: Old traces purged
- **WHEN** the retention job runs and a trace is older than the window
- **THEN** the trace is deleted and no longer listed by the `spec_trace` tool
