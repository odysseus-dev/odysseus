# Native macOS agent and PDF changes

This fork contains work in progress for local-model tool calling and PDF creation in Odysseus. It keeps the native macOS installation, GPU acceleration, existing models, credentials, and configured permissions. No Docker deployment is required.

## What changed

- Automatically detect tool capability advertised by a local Ollama model when the model's tool mode is Auto. Models without native tool templates can use a local schema-constrained proposal adapter. Explicit model modes and endpoint restrictions remain authoritative.
- Add a bounded text-to-PDF option to `write_file`. It uses PyMuPDF, writes within the authorized workspace, refuses to overwrite an existing PDF, and registers an owner-scoped download link for chat.
- Preserve correct artifact evidence for binary PDF output and stop a completed standalone PDF request from being repeated after approval.
- Recover Mistral's explicit JSON tool-call envelopes, including an unmarked whole-response array, only for tools offered in that request. Recovered calls pass the existing schema, policy, workspace and approval checks. Identical calls in one batch are executed once; intermediate tool markup is buffered.

## Changed files

| File | Purpose |
| --- | --- |
| `routes/chat_routes.py` | Includes the authorized PDF writer in the interactive agent's routing contract, including when another classifier reports an unknown capability. |
| `src/agent_loop.py` | Selects compact tool schemas automatically for supported local Ollama models and ends an approved standalone PDF request after its successful write. |
| `src/agent_runtime/authority.py` | Grants the explicitly requested PDF operation without granting general file-write authority. |
| `src/agent_runtime/completion.py` | Avoids an irrelevant executable-test warning when no such verification was requested. |
| `src/agent_runtime/effect_adapters.py` | Records a PDF existence postcondition instead of comparing the PDF's binary bytes to the source text digest. |
| `src/agent_tools/filesystem_tools.py` | Adds the explicit PDF write path and produces an owner-scoped chat download link. |
| `src/agent_tools/pdf_writer.py` | Recognizes PDF requests, renders bounded text PDFs, prevents overwrites, and registers downloads with the existing upload handler. |
| `src/clean_agent_preview.py` | Routes the PDF writer in interactive chat, retains approval behavior and download links, and recovers and deduplicates supported Mistral tool-call formats. |
| `src/ollama_tool_detection.py` | Reads and caches native tool capability metadata from the local Ollama endpoint only. |
| `src/ollama_structured_tools.py` | Builds a local grammar-constrained tool proposal and validates it against the offered schemas; execution remains with the normal agent checks. |
| `src/tool_execution.py` | Allows explicit local PDF rendering through the existing writer while retaining binary-file protections and bridge restrictions. |
| `src/tool_schemas.py` | Describes the PDF format to models and preserves structured PDF arguments during dispatch. |
| `tests/test_local_pdf_writer.py` | Tests rendering, authorization, approvals, completion, downloads, routing, and Mistral call recovery. |
| `tests/test_ollama_tool_detection.py` | Tests local capability detection, caching, conservative failures, and explicit overrides. |
| `tests/test_ollama_structured_tools.py` | Tests constrained proposals, rejection of unavailable or invalid tools, and dispatch through the existing PDF writer. |
| `docs/native-macos-pdf-work-in-progress.md` | Documents this fork's changes, verification, and remaining limitations. |

## Verification and limitations

In the native macOS app, Llama 3.1 8B, Llama 3.2, local GPT-OSS 20B, Qwen 3.5 4B, and Mistral 7B each created a PDF with a chat download link. A Llama download was clicked in Safari and matched its workspace file byte for byte. Qwen 3.5 4B was observed using 100% GPU. Mistral needed recovery of tool-call text and duplicate calls; the latest live test showed a successful write and link, although the UI repeated the words “PDF ready.” These are individual checks, not a guarantee for every request or model.

Samantha Mistral advertises completion only through Ollama; Ollama rejects native tool schemas for that installed model. The new local adapter uses Ollama constrained output to propose calls, then applies existing agent validation and permissions. Focused tests and a local model proposal passed; it has not yet been verified in the running app.

General macOS Bash/Python execution remains blocked because the current process-group fallback does not satisfy the required filesystem and process-tree isolation. No containment check has been disabled. Earlier broad tests encountered macOS subprocess crashes; focused tests excluding that subprocess pattern were used for the changes above.

## Cookbook and Ollama

On the same Mac, Cookbook's **Download → Direct Download** accepts an Ollama model name with a tag, such as `qwen3.5:4b`; the local backend runs `ollama pull`, so the model appears in the Ollama app connected to that same server. Refresh the model picker if needed. Hugging Face, llama.cpp, and MLX downloads can live in different caches and do not automatically appear in Ollama. Only compatible model formats and architectures can be imported; not every Cookbook download is an Ollama model.
