"""Tests for password length and byte limit validation."""

import pytest
from fastapi import HTTPException

from src.auth_helpers import validate_password_length
from src.constants import PASSWORD_MAX_LENGTH, PASSWORD_MIN_LENGTH


def test_password_length_seven_chars_rejected():
    with pytest.raises(HTTPException) as exc:
        validate_password_length("a" * 7)
    assert exc.value.status_code == 400
    assert exc.value.detail == f"Password must be at least {PASSWORD_MIN_LENGTH} characters"


def test_password_length_eight_chars_accepted():
    # 8 chars -> accepted
    validate_password_length("a" * 8)


def test_password_length_72_ascii_accepted():
    # 72 ASCII chars -> accepted
    validate_password_length("a" * 72)


def test_password_length_73_ascii_rejected():
    with pytest.raises(HTTPException) as exc:
        validate_password_length("a" * 73)
    assert exc.value.status_code == 400
    assert exc.value.detail == f"Password must be {PASSWORD_MAX_LENGTH} bytes or fewer"


def test_password_length_24_euro_accepted():
    # 24 x "€" (3 bytes each = exactly 72 bytes) -> accepted
    password = "€" * 24
    assert len(password) == 24
    assert len(password.encode("utf-8")) == 72
    validate_password_length(password)


def test_password_length_25_euro_rejected():
    # 25 x "€" (75 bytes but only 25 chars) -> rejected
    password = "€" * 25
    assert len(password) == 25
    assert len(password.encode("utf-8")) == 75
    with pytest.raises(HTTPException) as exc:
        validate_password_length(password)
    assert exc.value.status_code == 400
    assert exc.value.detail == f"Password must be {PASSWORD_MAX_LENGTH} bytes or fewer"
