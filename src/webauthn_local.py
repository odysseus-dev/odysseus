# src/webauthn_local.py
"""Local WebAuthn helpers for passkey login.

This module implements the small part of WebAuthn Odysseus needs:

* ES256 registrations with attestation ``none`` or self-attested ``packed``.
* ES256 assertions with user presence and user verification.

It stores only public keys. No face image, face template, or private key reaches
the server. No hosted service is used.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import secrets
import threading
import time
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec

from core.database import PasskeyCredential, SessionLocal

logger = logging.getLogger(__name__)

CHALLENGE_TTL_SECONDS = 300
MAX_PENDING_CHALLENGES = 200

_FLAG_UP = 0x01
_FLAG_UV = 0x04
_FLAG_AT = 0x40

_pending: Dict[str, Dict[str, Any]] = {}
_pending_lock = threading.Lock()


class WebAuthnError(ValueError):
    """Raised when a WebAuthn response fails validation."""


def b64url_encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def b64url_decode(value: str) -> bytes:
    if not isinstance(value, str) or not value:
        raise WebAuthnError("Missing base64url value")
    try:
        return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
    except Exception as exc:
        raise WebAuthnError("Invalid base64url value") from exc


def user_id_for(username: str) -> str:
    """Stable opaque user handle. The username itself is not exposed."""
    digest = hashlib.sha256(username.strip().lower().encode("utf-8")).digest()[:32]
    return b64url_encode(digest)


def issue_challenge(kind: str, username: Optional[str] = None) -> str:
    """Create and remember a short-lived challenge."""
    challenge = secrets.token_bytes(32)
    encoded = b64url_encode(challenge)
    now = time.time()
    with _pending_lock:
        expired = [k for k, v in _pending.items() if now - v["created"] > CHALLENGE_TTL_SECONDS]
        for key in expired:
            _pending.pop(key, None)
        while len(_pending) >= MAX_PENDING_CHALLENGES:
            oldest = min(_pending, key=lambda k: _pending[k]["created"])
            _pending.pop(oldest, None)
        _pending[encoded] = {
            "kind": kind,
            "username": username,
            "created": now,
        }
    return encoded


def consume_challenge(challenge: str, kind: str) -> Optional[str]:
    """Consume a challenge once. Return the bound username, if any."""
    with _pending_lock:
        entry = _pending.pop(challenge, None)
    if not entry:
        return None
    if entry["kind"] != kind:
        return None
    if time.time() - entry["created"] > CHALLENGE_TTL_SECONDS:
        return None
    return entry.get("username")


class _CBORDecoder:
    """Minimal CBOR decoder for WebAuthn attestation and COSE keys."""

    def __init__(self, data: bytes):
        self._data = data
        self._pos = 0

    @property
    def pos(self) -> int:
        return self._pos

    def _take(self, count: int) -> bytes:
        end = self._pos + count
        if end > len(self._data):
            raise WebAuthnError("Truncated CBOR value")
        chunk = self._data[self._pos:end]
        self._pos = end
        return chunk

    def _byte(self) -> int:
        return self._take(1)[0]

    def read(self) -> Any:
        initial = self._byte()
        major = initial >> 5
        additional = initial & 0x1F

        if additional < 24:
            value: Any = additional
        elif additional == 24:
            value = self._byte()
        elif additional == 25:
            value = int.from_bytes(self._take(2), "big")
        elif additional == 26:
            value = int.from_bytes(self._take(4), "big")
        elif additional == 27:
            value = int.from_bytes(self._take(8), "big")
        else:
            raise WebAuthnError("Unsupported CBOR length")

        if major == 0:
            return value
        if major == 1:
            return -1 - value
        if major == 2:
            return self._take(value)
        if major == 3:
            try:
                return self._take(value).decode("utf-8")
            except UnicodeDecodeError as exc:
                raise WebAuthnError("Invalid CBOR text") from exc
        if major == 4:
            if value > 1024:
                raise WebAuthnError("CBOR array too large")
            return [self.read() for _ in range(value)]
        if major == 5:
            if value > 1024:
                raise WebAuthnError("CBOR map too large")
            result = {}
            for _ in range(value):
                key = self.read()
                result[key] = self.read()
            return result
        if major == 6:
            return self.read()
        if major == 7:
            if value == 20:
                return False
            if value == 21:
                return True
            if value == 22:
                return None
            return value
        raise WebAuthnError("Unsupported CBOR major type")


def _decode_client_data(client_data_b64: str) -> Tuple[Dict[str, Any], bytes]:
    raw = b64url_decode(client_data_b64)
    try:
        return json.loads(raw.decode("utf-8")), raw
    except Exception as exc:
        raise WebAuthnError("Invalid client data JSON") from exc


def _public_key_from_cose(cose: Dict[str, Any]):
    if cose.get(1) != 2 or cose.get(3) != -7 or cose.get(-1) != 1:
        raise WebAuthnError("Only ES256 P-256 credentials are supported")
    x = cose.get(-2)
    y = cose.get(-3)
    if not isinstance(x, bytes) or not isinstance(y, bytes) or len(x) != 32 or len(y) != 32:
        raise WebAuthnError("Invalid ES256 public key")
    try:
        numbers = ec.EllipticCurvePublicNumbers(
            int.from_bytes(x, "big"), int.from_bytes(y, "big"), ec.SECP256R1()
        )
        return numbers.public_key()
    except Exception as exc:
        raise WebAuthnError("Invalid ES256 point") from exc


def _public_key_from_der(value: str):
    try:
        return serialization.load_der_public_key(b64url_decode(value))
    except Exception as exc:
        raise WebAuthnError("Stored public key is invalid") from exc


def _public_key_to_der(public_key) -> str:
    der = public_key.public_bytes(
        serialization.Encoding.DER,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return b64url_encode(der)


def verify_registration(
    *,
    expected_origin: str,
    rp_id: str,
    challenge: str,
    credential_id_b64: str,
    client_data_b64: str,
    attestation_object_b64: str,
) -> Tuple[str, str]:
    """Validate a registration response. Return credential id and public key."""
    client_data, client_raw = _decode_client_data(client_data_b64)
    if client_data.get("type") != "webauthn.create":
        raise WebAuthnError("Unexpected registration type")
    if client_data.get("challenge") != challenge:
        raise WebAuthnError("Registration challenge mismatch")
    if client_data.get("origin") != expected_origin:
        raise WebAuthnError("Registration origin mismatch")

    try:
        attestation = _CBORDecoder(b64url_decode(attestation_object_b64)).read()
    except WebAuthnError:
        raise
    except Exception as exc:
        raise WebAuthnError("Invalid attestation object") from exc

    fmt = attestation.get("fmt")
    auth_data = attestation.get("authData")
    if not isinstance(auth_data, bytes) or len(auth_data) < 37:
        raise WebAuthnError("Invalid authenticator data")
    if fmt not in ("none", "packed"):
        raise WebAuthnError("Unsupported attestation format")

    expected_rp_hash = hashlib.sha256(rp_id.encode("utf-8")).digest()
    if not secrets.compare_digest(auth_data[:32], expected_rp_hash):
        raise WebAuthnError("RP ID hash mismatch")

    flags = auth_data[32]
    if not flags & _FLAG_UP or not flags & _FLAG_UV:
        raise WebAuthnError("User presence and verification are required")
    if not flags & _FLAG_AT:
        raise WebAuthnError("Registration is missing credential data")

    offset = 37 + 16
    if offset + 2 > len(auth_data):
        raise WebAuthnError("Truncated credential data")
    credential_length = int.from_bytes(auth_data[offset:offset + 2], "big")
    offset += 2
    if credential_length < 1 or offset + credential_length > len(auth_data):
        raise WebAuthnError("Invalid credential ID length")
    credential_id = auth_data[offset:offset + credential_length]
    offset += credential_length

    if b64url_decode(credential_id_b64) != credential_id:
        raise WebAuthnError("Credential ID mismatch")

    try:
        cose = _CBORDecoder(auth_data[offset:]).read()
        public_key = _public_key_from_cose(cose)
    except WebAuthnError:
        raise
    except Exception as exc:
        raise WebAuthnError("Invalid COSE public key") from exc

    if fmt == "packed":
        statement = attestation.get("attStmt") or {}
        signature = statement.get("sig")
        if isinstance(signature, bytes):
            signed_data = auth_data + hashlib.sha256(client_raw).digest()
            try:
                public_key.verify(signature, signed_data, ec.ECDSA(hashes.SHA256()))
            except InvalidSignature as exc:
                raise WebAuthnError("Self attestation signature failed") from exc

    return b64url_encode(credential_id), _public_key_to_der(public_key)


def verify_assertion(
    *,
    expected_origin: str,
    rp_id: str,
    challenge: str,
    credential_id_b64: str,
    client_data_b64: str,
    authenticator_data_b64: str,
    signature_b64: str,
) -> Tuple[str, int]:
    """Validate an assertion. Return owner and new signature counter."""
    client_data, client_raw = _decode_client_data(client_data_b64)
    if client_data.get("type") != "webauthn.get":
        raise WebAuthnError("Unexpected assertion type")
    if client_data.get("challenge") != challenge:
        raise WebAuthnError("Login challenge mismatch")
    if client_data.get("origin") != expected_origin:
        raise WebAuthnError("Login origin mismatch")

    auth_data = b64url_decode(authenticator_data_b64)
    if len(auth_data) < 37:
        raise WebAuthnError("Invalid authenticator data")
    expected_rp_hash = hashlib.sha256(rp_id.encode("utf-8")).digest()
    if not secrets.compare_digest(auth_data[:32], expected_rp_hash):
        raise WebAuthnError("RP ID hash mismatch")
    flags = auth_data[32]
    if not flags & _FLAG_UP or not flags & _FLAG_UV:
        raise WebAuthnError("User presence and verification are required")

    record = find_credential(credential_id_b64)
    if not record:
        raise WebAuthnError("Unknown passkey")
    public_key = _public_key_from_der(record["public_key"])

    signature = b64url_decode(signature_b64)
    signed_data = auth_data + hashlib.sha256(client_raw).digest()
    try:
        public_key.verify(signature, signed_data, ec.ECDSA(hashes.SHA256()))
    except InvalidSignature as exc:
        raise WebAuthnError("Passkey signature failed") from exc

    new_count = int.from_bytes(auth_data[33:37], "big")
    old_count = int(record.get("sign_count") or 0)
    if old_count and new_count and new_count <= old_count:
        raise WebAuthnError("Passkey signature counter did not advance")

    return record["owner"], max(old_count, new_count)


def find_credential(credential_id_b64: str) -> Optional[Dict[str, Any]]:
    db = SessionLocal()
    try:
        row = (
            db.query(PasskeyCredential)
            .filter(PasskeyCredential.credential_id == credential_id_b64)
            .first()
        )
        if not row:
            return None
        return {
            "credential_id": row.credential_id,
            "owner": row.owner,
            "public_key": row.public_key,
            "sign_count": row.sign_count,
        }
    finally:
        db.close()


def store_credential(owner: str, credential_id_b64: str, public_key_b64: str, label: Optional[str] = None) -> bool:
    owner = owner.strip().lower()
    db = SessionLocal()
    try:
        existing = (
            db.query(PasskeyCredential)
            .filter(PasskeyCredential.credential_id == credential_id_b64)
            .first()
        )
        if existing:
            return False
        row = PasskeyCredential(
            credential_id=credential_id_b64,
            owner=owner,
            public_key=public_key_b64,
            sign_count=0,
            label=(label or "Windows Hello passkey")[:120],
        )
        db.add(row)
        db.commit()
        return True
    finally:
        db.close()


def list_credentials(owner: str):
    db = SessionLocal()
    try:
        rows = (
            db.query(PasskeyCredential)
            .filter(PasskeyCredential.owner == owner.strip().lower())
            .order_by(PasskeyCredential.created_at.asc())
            .all()
        )
        return [
            {
                "credential_id": row.credential_id,
                "label": row.label,
                "created_at": row.created_at.isoformat() if row.created_at else None,
                "last_used_at": row.last_used_at.isoformat() if row.last_used_at else None,
            }
            for row in rows
        ]
    finally:
        db.close()


def delete_credential(owner: str, credential_id_b64: str) -> bool:
    db = SessionLocal()
    try:
        row = (
            db.query(PasskeyCredential)
            .filter(
                PasskeyCredential.credential_id == credential_id_b64,
                PasskeyCredential.owner == owner.strip().lower(),
            )
            .first()
        )
        if not row:
            return False
        db.delete(row)
        db.commit()
        return True
    finally:
        db.close()


def delete_credentials_for_owner(owner: str) -> int:
    db = SessionLocal()
    try:
        count = (
            db.query(PasskeyCredential)
            .filter(PasskeyCredential.owner == owner.strip().lower())
            .delete(synchronize_session=False)
        )
        db.commit()
        return int(count or 0)
    finally:
        db.close()


def touch_credential(credential_id_b64: str, sign_count: int) -> None:
    db = SessionLocal()
    try:
        row = (
            db.query(PasskeyCredential)
            .filter(PasskeyCredential.credential_id == credential_id_b64)
            .first()
        )
        if row:
            row.sign_count = int(sign_count or 0)
            row.last_used_at = datetime.now(timezone.utc).replace(tzinfo=None)
            db.commit()
    finally:
        db.close()
