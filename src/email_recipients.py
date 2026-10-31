"""Address parsing shared by immediate, scheduled and MCP email delivery."""

from email.utils import getaddresses


def envelope_recipients(*fields) -> list[str]:
    """Return bare SMTP addresses from header strings or lists of strings.

    Display-name commas belong to the address grammar, not to the envelope's
    recipient separator. Keep To/Cc/Bcc ordering and ignore empty entries.
    """
    headers = []
    for field in fields:
        if isinstance(field, str):
            headers.append(field)
        elif field:
            headers.extend(field)
    return [addr.strip() for _, addr in getaddresses(headers) if addr.strip()]
