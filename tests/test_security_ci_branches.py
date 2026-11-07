"""Pin security-workflow trigger and token boundaries.

These source-contract checks are deliberate: GitHub owns event dispatch and token
issuance, so exercising them would require remote workflow runs. Keep local
validation credential-free and require separate exact-commit Actions evidence.
"""

import re
import unittest
from pathlib import Path


_WORKFLOWS = Path(__file__).resolve().parent.parent / ".github" / "workflows"
_SECURITY_WORKFLOWS = (
    "secret-scan.yml",
    "workflow-security.yml",
    "container-scan.yml",
    "container-trivy.yml",
    "dependency-review.yml",
)


def _block(text, heading, indent):
    lines = text.splitlines(keepends=True)
    start = next(
        (i for i, line in enumerate(lines)
         if line.rstrip() == " " * indent + heading + ":"),
        None,
    )
    assert start is not None, f"missing {heading!r} block"
    end = start + 1
    while end < len(lines):
        line = lines[end]
        if line.strip() and len(line) - len(line.lstrip()) <= indent:
            break
        end += 1
    return "".join(lines[start:end])


class SecurityWorkflowBranchTests(unittest.TestCase):
    def test_push_checks_cover_dev_and_preserve_main(self):
        for name in _SECURITY_WORKFLOWS:
            with self.subTest(workflow=name):
                push = _block((_WORKFLOWS / name).read_text(), "push", 2)
                branches = re.search(r"(?m)^    branches:\s*\[([^\]]+)\]\s*$", push)
                self.assertIsNotNone(branches)
                self.assertEqual(
                    {branch.strip() for branch in branches.group(1).split(",")},
                    {"main", "dev"},
                )

    def test_pull_request_and_manual_entry_points_remain(self):
        for name in _SECURITY_WORKFLOWS:
            with self.subTest(workflow=name):
                text = (_WORKFLOWS / name).read_text()
                self.assertRegex(text, r"(?m)^  pull_request:\s*$")
                self.assertRegex(text, r"(?m)^  workflow_dispatch:\s*$")

    def test_security_workflows_keep_default_deny_tokens(self):
        for name in _SECURITY_WORKFLOWS:
            with self.subTest(workflow=name):
                text = (_WORKFLOWS / name).read_text()
                self.assertRegex(text, r"(?m)^permissions: \{\}\s*$")

    def test_dev_trivy_scan_has_read_only_permissions(self):
        text = (_WORKFLOWS / "container-trivy.yml").read_text()
        scan = _block(text, "scan", 2)
        self.assertIn(
            "    if: github.event_name != 'push' || github.ref == 'refs/heads/dev'\n",
            scan,
        )
        permissions = _block(scan, "permissions", 4)
        self.assertEqual(permissions.strip(), "permissions:\n      contents: read")
        self.assertNotIn("upload-sarif", scan)

    def test_trivy_publication_is_only_for_main_pushes(self):
        text = (_WORKFLOWS / "container-trivy.yml").read_text()
        publish = _block(text, "publish", 2)
        self.assertIn(
            "    if: github.event_name == 'push' && github.ref == 'refs/heads/main'\n",
            publish,
        )
        self.assertIn("security-events: write", publish)
        self.assertIn("upload-sarif", publish)

    def test_dependency_review_gate_stays_canonical_and_pr_only(self):
        text = (_WORKFLOWS / "dependency-review.yml").read_text()
        gate = _block(text, "dependency-review", 2)
        self.assertIn(
            "    if: github.event_name == 'pull_request' && "
            "github.repository == 'odysseus-dev/odysseus'\n",
            gate,
        )


if __name__ == "__main__":
    unittest.main()
