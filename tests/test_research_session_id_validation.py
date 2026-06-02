"""Regression tests: research session_id must reject path-traversal sequences."""

import re
import unittest

_SESSION_ID_RE = re.compile(r"^[a-zA-Z0-9-]{1,128}$")


class TestResearchSessionIdValidation(unittest.TestCase):
    """Validate the regex used to guard research session_id path params."""

    def test_accepts_rp_prefixed_id(self):
        self.assertIsNotNone(_SESSION_ID_RE.fullmatch("rp-abc123def456"))

    def test_accepts_standard_uuid(self):
        self.assertIsNotNone(
            _SESSION_ID_RE.fullmatch("550e8400-e29b-41d4-a716-446655440000")
        )

    def test_accepts_custom_alphanumeric(self):
        self.assertIsNotNone(_SESSION_ID_RE.fullmatch("custom-id-123"))

    def test_rejects_double_dot(self):
        self.assertIsNone(_SESSION_ID_RE.fullmatch(".."))

    def test_rejects_single_dot(self):
        self.assertIsNone(_SESSION_ID_RE.fullmatch("."))

    def test_rejects_dot_slash_traversal(self):
        self.assertIsNone(_SESSION_ID_RE.fullmatch("../../data/auth"))

    def test_rejects_deep_traversal(self):
        self.assertIsNone(_SESSION_ID_RE.fullmatch("../../../etc/passwd"))

    def test_rejects_mixed_traversal(self):
        self.assertIsNone(_SESSION_ID_RE.fullmatch("normal/../../traversal"))

    def test_rejects_dot_prefix_traversal(self):
        self.assertIsNone(_SESSION_ID_RE.fullmatch("./../../secret"))

    def test_rejects_empty(self):
        self.assertIsNone(_SESSION_ID_RE.fullmatch(""))

    def test_rejects_whitespace(self):
        self.assertIsNone(_SESSION_ID_RE.fullmatch(" "))

    def test_rejects_slash(self):
        self.assertIsNone(_SESSION_ID_RE.fullmatch("a/b"))

    def test_rejects_null_byte(self):
        self.assertIsNone(_SESSION_ID_RE.fullmatch("rp-test\x00"))


if __name__ == "__main__":
    unittest.main()
