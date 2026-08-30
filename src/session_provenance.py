"""Small shared seam for recording session endpoint provenance."""


def persist_session_endpoint_provenance(
    session_manager,
    session_id: str,
    session,
    *,
    model_endpoint_id=None,
    endpoint_provenance: str,
) -> None:
    """Record trusted endpoint provenance on a session and its durable row.

    The production ``SessionManager`` owns the database write. Lightweight
    route test doubles may not implement that method, so they still receive
    the same in-memory fields without changing the production contract.
    """
    provenance = str(endpoint_provenance or "").strip().lower()
    endpoint_id = str(model_endpoint_id or "").strip() or None
    if provenance == "registered" and not endpoint_id:
        raise ValueError("registered session provenance requires an endpoint id")
    if provenance == "direct":
        endpoint_id = None
    if provenance not in {"registered", "direct"}:
        raise ValueError("unsupported session endpoint provenance")

    setter = getattr(session_manager, "set_session_endpoint_provenance", None)
    if callable(setter):
        setter(
            session_id,
            model_endpoint_id=endpoint_id,
            endpoint_provenance=provenance,
        )
    if session is None:
        session = getattr(session_manager, "sessions", {}).get(session_id)
    if session is not None:
        setattr(session, "model_endpoint_id", endpoint_id)
        setattr(session, "endpoint_provenance", provenance)
