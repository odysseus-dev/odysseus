"""Pure helpers shared across transports."""

# Document tools whose streaming arguments are surfaced to the frontend as
# incremental `tool_call_delta` events (so the document UI can render live).
# Both the OpenAI and Anthropic stream decoders special-case these names.
DOC_TOOLS = ("create_document", "update_document", "edit_document")
