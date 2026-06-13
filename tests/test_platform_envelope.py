# tests/test_platform_envelope.py
"""Envelope v1: schema, canonical bytes, sign/verify, tamper rejection."""
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from services.business_platform.envelope import (
    Envelope, EnvelopeStatus, GATED_CLASSES, classify_intent,
    canonical_bytes, sign_envelope, verify_envelope, keypair_pem,
)


def _envelope(**over):
    base = dict(
        message_id="m-1", conversation_id="c-1", causation_id=None,
        idempotency_key="ik-1",
        from_subject="agent:travel-1/booker", from_company="travel-1",
        to_subject=None, to_company="bigboss",
        issued_at="2026-06-13T10:00:00Z", expires_at="2026-06-13T11:00:00Z",
        schema_version="1.0", intent="booking.confirm",
        status="proposed", requires_human_approval=True,
        capabilities_requested=[], capability_token_id=None,
        trust_level="untrusted", payload={"booking_id": "B42"},
    )
    base.update(over)
    return Envelope(**base)


def test_status_enum_members():
    assert {s.value for s in EnvelopeStatus} == {
        "finished", "blocked", "needs_input", "partial",
        "proposed", "approved", "denied", "error",
    }


def test_canonical_bytes_stable_and_signature_roundtrip():
    priv_pem, pub_pem = keypair_pem()
    env = _envelope()
    sig = sign_envelope(env, priv_pem)
    assert verify_envelope(env, sig, pub_pem) is True
    # canonical bytes must not depend on dict insertion order
    env2 = _envelope(payload={"booking_id": "B42"})
    assert canonical_bytes(env) == canonical_bytes(env2)


def test_tampered_envelope_rejected():
    priv_pem, pub_pem = keypair_pem()
    env = _envelope()
    sig = sign_envelope(env, priv_pem)
    tampered = _envelope(payload={"booking_id": "B43"})
    assert verify_envelope(tampered, sig, pub_pem) is False


def test_classify_intent_gated_classes():
    assert classify_intent("booking.confirm") == "booking"
    assert classify_intent("booking.cancel") == "booking"
    assert classify_intent("payment.refund") == "payment_refund"
    assert classify_intent("comms.email.send") == "outbound_comms"
    assert classify_intent("quote.create") == "quote"
    assert classify_intent("status.report") is None
    assert GATED_CLASSES == {"payment_refund", "booking", "outbound_comms", "quote"}
