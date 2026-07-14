# operator-research — Spec Delta

## ADDED Requirements

### Requirement: Perplexity as a first-class search provider
The system SHALL add `perplexity` to `PROVIDER_INFO` in `services/search/providers.py` (label "Perplexity", requires API key, no custom URL), configurable through the same admin settings surface as existing providers, using `PERPLEXITY_API_KEY` as the key setting.

#### Scenario: Perplexity selected as provider
- **WHEN** the admin sets the search provider to `perplexity` with a valid API key and the agent runs `web_search`
- **THEN** results are returned from the Perplexity API in the same normalized result shape as other providers

### Requirement: Parallel fan-out research mode
The system SHALL provide an `operator_research` agent tool that queries TinyFish, Perplexity, and Firecrawl in parallel (skipping any provider without a configured key), merges results, deduplicates by normalized URL, and ranks the merged list.

#### Scenario: Fan-out with all providers configured
- **WHEN** the agent calls `operator_research` with a query and all three providers have keys
- **THEN** all three are queried concurrently and the tool returns a single merged, deduplicated, ranked result list with per-provider attribution

#### Scenario: Fan-out with a missing key
- **WHEN** Firecrawl has no API key configured
- **THEN** the fan-out runs with TinyFish and Perplexity only, and the result notes which providers were skipped

### Requirement: Fan-out is failure-isolated
A provider error or timeout during fan-out SHALL NOT fail the whole call: the tool returns results from the surviving providers plus a per-provider error summary, and provider errors are recorded through the existing search analytics/error logger.

#### Scenario: One provider times out
- **WHEN** Perplexity times out but TinyFish and Firecrawl respond
- **THEN** the merged results contain the two successful providers' hits and the response notes the Perplexity timeout

### Requirement: Fan-out respects a latency budget
`operator_research` SHALL enforce an overall deadline (default 20 seconds, matching `REQUEST_TIMEOUT`) after which pending providers are cancelled and partial results are returned.

#### Scenario: Deadline reached with partial results
- **WHEN** the deadline expires while one provider is still pending
- **THEN** the tool returns the completed providers' merged results and marks the pending provider as timed out
