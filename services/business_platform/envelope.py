"""Envelope v1 — signed, schema-versioned, A2A-compatible message unit.

Spec: docs/superpowers/specs/2026-06-13-business-platform-slice1-design.md §3.
Canonical form: JSON with sorted keys, compact separators, over every field
except the signature itself. Signature: Ed25519 over the canonical bytes.
"""
import json
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey, Ed25519PublicKey,
)
from cryptography.exceptions import InvalidSignature


class EnvelopeStatus(str, Enum):
    finished = "finished"
    blocked = "blocked"
    needs_input = "needs_input"
    partial = "partial"
    proposed = "proposed"
    approved = "approved"
    denied = "denied"
    error = "error"


# Gated action classes (spec §3: ALL four require manager approval).
GATED_CLASSES = {"payment_refund", "booking", "outbound_comms", "quote"}

_INTENT_PREFIX_TO_CLASS = {
    "payment.": "payment_refund",
    "booking.": "booking",
    "comms.": "outbound_comms",
    "quote.": "quote",
}


def classify_intent(intent: str) -> Optional[str]:
    """Map an intent string to its gated class, or None if ungated."""
    for prefix, klass in _INTENT_PREFIX_TO_CLASS.items():
        if intent.startswith(prefix):
            return klass
    return None


class Envelope(BaseModel):
    message_id: str
    conversation_id: str
    causation_id: Optional[str] = None
    idempotency_key: str
    from_subject: Optional[str] = None
    from_company: str
    to_subject: Optional[str] = None
    to_company: str
    issued_at: str                      # ISO-8601 UTC
    expires_at: Optional[str] = None    # ISO-8601 UTC
    schema_version: str = "1.0"
    intent: str
    status: EnvelopeStatus
    requires_human_approval: bool = False
    capabilities_requested: list[str] = Field(default_factory=list)
    capability_token_id: Optional[str] = None
    trust_level: str = "untrusted"      # inbound cross-company is ALWAYS untrusted data
    payload: dict[str, Any] = Field(default_factory=dict)


def canonical_bytes(env: Envelope) -> bytes:
    """Deterministic bytes for signing: sorted keys, compact JSON."""
    data = env.model_dump(mode="json")
    return json.dumps(data, sort_keys=True, separators=(",", ":")).encode("utf-8")


def keypair_pem() -> tuple[str, str]:
    """Generate an Ed25519 keypair, return (private_pem, public_pem)."""
    priv = Ed25519PrivateKey.generate()
    priv_pem = priv.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    pub_pem = priv.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode()
    return priv_pem, pub_pem


def sign_envelope(env: Envelope, private_pem: str) -> str:
    priv = serialization.load_pem_private_key(private_pem.encode(), password=None)
    return priv.sign(canonical_bytes(env)).hex()


def verify_envelope(env: Envelope, signature_hex: str, public_pem: str) -> bool:
    pub = serialization.load_pem_public_key(public_pem.encode())
    try:
        pub.verify(bytes.fromhex(signature_hex), canonical_bytes(env))
        return True
    except (InvalidSignature, ValueError):
        return False
