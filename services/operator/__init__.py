"""Operator service — unified perception/action/research broker for the agent loop.

See openspec/changes/add-agentic-operator for the governing spec.
"""

from services.operator.core import (  # noqa: F401
    CAP_BROWSER_ACTION,
    CAP_DESKTOP_ACTION,
    CAP_PIXEL_RETRIEVAL,
    CAP_RESEARCH,
    CAP_SCREEN_PERCEPTION,
    CAP_SPEC_TRACER,
    degraded_envelope,
    envelope,
    get_operator_status,
    record_audit,
    require_capability,
)
