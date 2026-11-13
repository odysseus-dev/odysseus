"""Validate the UIDVALIDITY value returned by an IMAP connection."""


def read_uidvalidity(connection):
    status, values = connection.response("UIDVALIDITY")

    if status != "UIDVALIDITY" or not values:
        raise ValueError("UIDVALIDITY unavailable")

    raw = values[0]
    if isinstance(raw, bytes):
        raw = raw.decode("ascii")

    if not isinstance(raw, str) or not raw.isdecimal():
        raise ValueError("Invalid UIDVALIDITY")

    value = int(raw)
    if not 1 <= value <= 4294967295:
        raise ValueError("UIDVALIDITY out of range")

    return value
