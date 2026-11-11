# Native macOS PDF and tool-calling repair (work in progress)

Local Ollama models advertising tool support are detected automatically; explicit model settings remain authoritative. The PDF writer uses PyMuPDF to create a bounded text PDF in the agent workspace, uses the existing authorization and approval checks, avoids overwriting files, and registers an owner-scoped download for the chat. General shell and Python execution still require a macOS containment implementation; these changes do not disable isolation or Metal.

A prior live Odysseus test created a valid PDF after approval with Llama 3.1 8B and Auto tool mode. Focused download registration and approved-continuation tests passed. The download link changes require live verification. A subsequent live test of a fresh chat refused an ordinary PDF request, and an explicit tool request triggered web search instead: routing reliability is still unresolved. Do not treat this patch as complete.

A broad test run also encountered macOS subprocess crashes in approval tests; targeted tests excluding those subprocess checks were used instead. No credentials or local settings belong in this commit.

A fresh live test showed that an unknown capability classification removed all offered tools. The routing change retains the specifically authorized PDF writer when it is available; the exact request and authorization checks pass targeted tests. It still needs end-to-end verification after restart.

A live test confirmed the PDF was created and registered, but the completion gate removed the sentence containing the download link. The writer now returns a link without an unverified mutation claim; a focused approval-continuation test checks that the link survives. Live download verification remains pending.
