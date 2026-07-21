# Agent behavior args (stubs)

> App secrets and runtime config stay in `.env`, `data/settings.json`, and `memory_stack.env`.

## Example files (add as needed)

- `research-depth.yaml` — default legs for research-swarm (last30, firecrawl, perplexity)
- `brief-length.yaml` — executive brief word targets
- `session-recall.yaml` — how many days of session catalog to surface

These files shape **agent workflow behavior**, not FastAPI boot.
