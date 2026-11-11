# Native macOS PDF and tool-calling repair (work in progress)

Local Ollama models advertising tool support are detected automatically; explicit model settings remain authoritative. The PDF writer uses PyMuPDF to create a bounded text PDF in the agent workspace, uses the existing authorization and approval checks, avoids overwriting files, and registers an owner-scoped download for the chat. General shell and Python execution still require a macOS containment implementation; these changes do not disable isolation or Metal.

A prior live Odysseus test created a valid PDF after approval with Llama 3.1 8B and Auto tool mode. Focused download registration and approved-continuation tests passed. The download link changes require live verification. A subsequent live test of a fresh chat refused an ordinary PDF request, and an explicit tool request triggered web search instead: routing reliability is still unresolved. Do not treat this patch as complete.

A broad test run also encountered macOS subprocess crashes in approval tests; targeted tests excluding those subprocess checks were used instead. No credentials or local settings belong in this commit.

A fresh live test showed that an unknown capability classification removed all offered tools. The routing change retains the specifically authorized PDF writer when it is available; the exact request and authorization checks pass targeted tests. It still needs end-to-end verification after restart.

A live test confirmed the PDF was created and registered, but the completion gate removed the sentence containing the download link. The writer now returns a link without an unverified mutation claim; a focused approval-continuation test checks that the link survives. A subsequent live test displayed the download link in Odysseus; clicking it downloaded a PDF matching the workspace file byte for byte.

Additional live checks using Auto-mode Llama 3.2 and local GPT-OSS 20B each produced a valid one-page PDF containing the requested text and displayed a chat download link. These tests do not verify arbitrary shell or Python execution, which remains blocked by missing native macOS containment.

Qwen 3.5 4B produced a PDF with an in-chat link in Odysseus using GPU acceleration. Standard Mistral 7B emitted a [TOOL_CALLS] envelope as text; a narrowly scoped recovery accepts only well-formed calls for tools offered on that request and passes them through normal checks. Focused tests pass; live verification after restart remains pending. Samantha advertises no native tools, and unconstrained textual calls were unreliable; no unsafe fallback has been installed.

A live Mistral test after the recovery created a PDF and displayed a download link; the raw tool envelope also appeared in chat. The follow-up replaces the streamed draft with the final download message. Focused tests pass; the display fix needs live verification after restart.

A later live Mistral response used the same tool-call JSON array without its marker. The recovery now accepts either exact format, with the same offered-tool and authorization checks. Focused dispatch tests cover both; a fresh live check is pending.

A subsequent live test created the PDF but repeated the write in the same batch; the duplicate was rejected and shown as a failure. The latest patch deduplicates identical Mistral calls and buffers its tool-round text until the final answer. Focused tests pass; the change requires live verification.
