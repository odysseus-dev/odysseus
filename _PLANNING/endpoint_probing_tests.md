# LID Planning: Endpoint Probing and Provider Setup Tests

## Landscape
Odysseus supports a wide variety of LLM providers and local model servers (Anthropic, Gemini, Groq, xAI, OpenRouter, OpenAI, DeepSeek, vLLM, llama.cpp, Ollama). The process of setting up these providers and probing their endpoints to verify connectivity and authentication is a critical path for user onboarding. However, the current codebase lacks comprehensive test coverage for these flows. This can lead to silent failures, unclear error messages for users, and regressions when integrating new providers or updating existing ones. As highlighted in the `ROADMAP.md`, "More tests around endpoint probing and provider setup" is a key backend priority.

## Initiative
The goal is to build a robust test suite that covers the setup and endpoint probing logic for all supported providers. This initiative will ensure that:
1. **Connectivity Checks**: The system correctly identifies successful and failed connections.
2. **Error Handling**: Authentication errors, rate limits, network timeouts, and invalid API keys are caught and reported clearly to the user, rather than causing unhandled exceptions.
3. **Degraded States**: The system correctly reports degraded states when a provider is unreachable, as requested in the high-priority bugs section of the roadmap.
We will mock external network calls to ensure these tests run quickly, deterministically, and without requiring actual API keys in the CI/CD pipeline.

## Deliverable
- **Test Suite**: A new suite of unit and integration tests (e.g., in `tests/test_providers.py` or similar) covering probing logic for all supported providers.
- **Mocking Infrastructure**: Setup robust mocking for `httpx` or `requests` to simulate various API responses (200 OK, 401 Unauthorized, 429 Too Many Requests, 500 Internal Server Error, network timeouts).
- **Refactored Error Handling**: If tests reveal poorly handled edge cases in the current probing logic, those specific backend routes/services will be refactored to return consistent error structures.
- **Documentation**: Instructions in `CONTRIBUTING.md` on how to run and extend the provider test suite.
