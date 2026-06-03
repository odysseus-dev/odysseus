# Odysseus Project Context

Odysseus is a self-hosted AI workspace designed to provide a local-first, privacy-focused alternative to ChatGPT and Claude. It features chat, agents, model discovery (Cookbook), deep research, side-by-side model comparison, a multi-tab document editor, and integrations for email, calendar, and notes.

## Project Structure

The repository is divided into two main parts:

- **`odysseus/`**: The core application, including the backend API and the web frontend.
- **`odysseus_desktop/`**: A Flutter-based desktop application (likely a native wrapper or client for the core app).

### `odysseus/` (Core App)

- **Architecture**: FastAPI backend serving a modular vanilla JavaScript frontend.
- **Backend (`odysseus/src/`, `odysseus/core/`, `odysseus/routes/`)**:
  - `app.py`: The main entry point and slim orchestrator.
  - `core/`: Fundamental logic for authentication, database (SQLAlchemy), middleware, and constants.
  - `src/`: Core AI logic including the `agent_loop`, `llm_core` for model interactions, `tool_implementations` for agent capabilities, and specialized handlers for research, RAG, and more.
  - `routes/`: API endpoints organized by feature (chat, models, email, etc.).
  - `services/`: Higher-level service layers for documents, memory, and search.
- **Frontend (`odysseus/static/js/`)**:
  - A modular vanilla JS implementation.
  - `app.js`: Main application entry point and coordinator.
  - `chat.js`: Handles message flow and streaming responses.
  - `ui.js`: General UI utilities and toast notifications.
  - `markdown.js`: Processes and renders Markdown with syntax highlighting.
  - Other modules handle sessions, memory, file uploads, models, RAG, and search.
- **Data & Storage (`odysseus/data/`)**:
  - Local SQLite database (`app.db`), ChromaDB for vector memory, and various JSON files for presets and settings.

### `odysseus_desktop/` (Desktop App)

- **Technology**: Flutter / Dart.
- **Status**: Appears to be a supplementary client or wrapper.

## Building and Running

### Backend & Web UI

1.  **Environment Setup**:
    ```bash
    cd odysseus
    python3 -m venv venv
    source venv/bin/activate  # On Windows: venv\Scripts\activate
    pip install -r requirements.txt
    python setup.py
    ```
2.  **Running the Server**:
    ```bash
    python -m uvicorn app:app --host 127.0.0.1 --port 7000
    ```
3.  **Docker (Recommended)**:
    ```bash
    docker compose up -d --build
    ```

### Testing

The project uses `pytest` for backend testing.

- **Run all tests**:
  ```bash
  cd odysseus
  pytest
  ```
- **Tests Location**: `odysseus/tests/`

### Desktop App

1.  **Dependencies**:
    ```bash
    cd odysseus_desktop
    flutter pub get
    ```
2.  **Running**:
    ```bash
    flutter run
    ```

## Development Conventions

- **Platform Compatibility**: The codebase specifically addresses Windows quirks (e.g., MIME types for JS modules, symlink handling for HuggingFace).
- **Modularity**:
  - **Backend**: Logic is decoupled into `core`, `src`, `routes`, and `services`.
  - **Frontend**: Functionality is split into discrete JS modules with a clear load order (documented in `odysseus/static/js/MODULE_SUMMARY.md`).
- **Security**: Auth-enabled by default (`AUTH_ENABLED=true`). Uses admin privileges for sensitive operations like shell access or model serving.
- **Model Interaction**: Supports both local runtimes (vLLM, llama.cpp, Ollama) and external APIs (OpenAI, Anthropic, OpenRouter) via a unified `llm_core` interface.
- **Agent Tools**: Agents use a structured tool system defined in `src/tool_schemas.py` and implemented in `src/tool_implementations.py`.

## Key Files to Watch

- `odysseus/app.py`: Entry point and server configuration.
- `odysseus/src/agent_loop.py`: The heart of the agent's decision-making process.
- `odysseus/src/llm_core.py`: Manages communication with various AI models and providers.
- `odysseus/static/js/chat.js`: Primary frontend logic for the chat interface.
- `odysseus/docker-compose.yml`: Defines the full environment (Odysseus, ChromaDB, SearXNG, ntfy).
