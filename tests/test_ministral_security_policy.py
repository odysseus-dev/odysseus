
"""Synthetic regression for unfamiliar-device security alerts."""
import json
from unittest.mock import patch
import pytest
from src import ministral_email_classifier as classifier


@pytest.mark.parametrize("subject,body,expected", [
    ("Unfamiliar device",
     "We detected a login from an unfamiliar device.", 2),
    ("Unknown access",
     "Your account was accessed by an unknown device.", 2),
    ("Blocked login",
     "We blocked an unusual login attempt. Your account remains secure.", 2),
    ("Confirmed takeover",
     "An attacker changed your password and took control of your account.", 3),
    ("Confirmed compromise with unfamiliar device",
     "Your account was accessed by an unknown device. "
     "An attacker changed your password.", 3),
])
def test_security_policy(subject, body, expected):
    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self, size=-1):
            return json.dumps({
                "message": {
                    "content": json.dumps({
                        "urgency": (
                            "normal" if subject == "Blocked login"
                            else "critical"
                        ),
                        "reason": "Synthetic model verdict",
                    })
                }
            }).encode()

    with patch.object(
        classifier.urllib.request,
        "urlopen",
        return_value=FakeResponse(),
    ):
        result = classifier.classify_sync(subject, body)

    assert result["score"] == expected
