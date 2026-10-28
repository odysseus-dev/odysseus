"""Shared MIME text decoding for the web email helpers and MCP server."""


def decode_text_part(part) -> str:
    payload = part.get_payload(decode=True)
    if not payload:
        return ""
    charset = part.get_content_charset() or "utf-8"
    try:
        return payload.decode(charset, errors="replace")
    except (LookupError, ValueError):
        # Broken senders can label valid UTF-8 with an unsupported codec.
        return payload.decode("utf-8", errors="replace")
