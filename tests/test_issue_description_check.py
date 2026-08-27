"""Regression coverage for issue-description label lifecycle events."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml


_REPO = Path(__file__).resolve().parent.parent
_CHECKER = _REPO / ".github" / "scripts" / "check-issue-description.js"
_BUG_TEMPLATE = _REPO / ".github" / "ISSUE_TEMPLATE" / "bug_report.yml"
_WORKFLOW = _REPO / ".github" / "workflows" / "issue-description-check.yml"
pytestmark = pytest.mark.skipif(not shutil.which("node"), reason="node not on PATH")


def _run_closed_issue(action):
    harness = r"""
const checkIssueDescription = require(process.argv[1]);
const action = process.argv[2];
const calls = [];
const unexpected = (name) => async () => {
  throw new Error(`${name} should not be called for a closed issue`);
};

const github = {
  rest: {
    issues: {
      removeLabel: async (params) => calls.push({ method: 'removeLabel', params }),
      getLabel: unexpected('getLabel'),
      addLabels: unexpected('addLabels'),
      listComments: unexpected('listComments'),
      createComment: unexpected('createComment'),
      updateComment: unexpected('updateComment'),
      deleteComment: unexpected('deleteComment'),
    },
  },
};
const context = {
  payload: {
    action,
    issue: { number: 42, state: 'closed', body: '', labels: [] },
  },
  repo: { owner: 'odysseus-dev', repo: 'odysseus' },
};
const core = {
  warning: unexpected('core.warning'),
  setFailed: unexpected('core.setFailed'),
};

checkIssueDescription({ github, context, core })
  .then(() => process.stdout.write(JSON.stringify(calls)))
  .catch((error) => {
    console.error(error);
    process.exitCode = 1;
  });
"""
    proc = subprocess.run(
        ["node", "-e", harness, str(_CHECKER), action],
        capture_output=True,
        text=True,
        cwd=str(_REPO),
        timeout=30,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


def _run_bug_issue(revision):
    body = f"""# Odysseus Revision
{revision}

# Install Method
Docker

# Operating System
Linux

# Steps to Reproduce
1. Start Odysseus.

# Expected Behaviour
The application starts normally.

# Actual Behaviour
The application exits unexpectedly.
"""
    harness = r"""
const checkIssueDescription = require(process.argv[1]);
const body = process.argv[2];
const calls = [];

const github = {
  rest: {
    issues: {
      removeLabel: async (params) => calls.push({ method: 'removeLabel', params }),
      getLabel: async () => ({}),
      addLabels: async (params) => calls.push({ method: 'addLabels', params }),
      listComments: async () => ({ data: [] }),
      createComment: async (params) => calls.push({ method: 'createComment', params }),
      updateComment: async (params) => calls.push({ method: 'updateComment', params }),
      deleteComment: async (params) => calls.push({ method: 'deleteComment', params }),
    },
  },
};
const context = {
  payload: {
    action: 'opened',
    issue: { number: 42, state: 'open', body, labels: [{ name: 'bug' }] },
  },
  repo: { owner: 'odysseus-dev', repo: 'odysseus' },
};
const core = {
  warning: (message) => calls.push({ method: 'warning', message }),
  setFailed: (message) => calls.push({ method: 'setFailed', message }),
};

checkIssueDescription({ github, context, core })
  .then(() => process.stdout.write(JSON.stringify(calls)))
  .catch((error) => {
    console.error(error);
    process.exitCode = 1;
  });
"""
    proc = subprocess.run(
        ["node", "-e", harness, str(_CHECKER), body],
        capture_output=True,
        text=True,
        cwd=str(_REPO),
        timeout=30,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


def test_workflow_handles_issue_closures():
    workflow = _WORKFLOW.read_text()
    assert "types: [opened, edited, reopened, closed]" in workflow


def test_bug_template_requires_exact_revision():
    template = yaml.safe_load(_BUG_TEMPLATE.read_text())
    revision = next(item for item in template["body"] if item.get("id") == "revision")
    assert revision["type"] == "input"
    assert revision["attributes"]["label"] == "Odysseus Revision"
    assert "git show -s --abbrev=12 --format='%h (%cs)' HEAD" in revision["attributes"]["description"]
    assert revision["attributes"]["placeholder"] == "1fef4929cf1d (2026-08-11)"
    assert revision["validations"]["required"] is True


def test_bug_checker_accepts_exact_revision():
    calls = _run_bug_issue("1fef4929cf1d (2026-08-11)")
    assert not any(call["method"] in {"createComment", "setFailed"} for call in calls)
    assert any(
        call["method"] == "addLabels" and call["params"]["labels"] == ["ready for review"]
        for call in calls
    )


@pytest.mark.parametrize(
    "revision",
    [
        "",
        "1fef492",
        "1fef4929cf1d",
        "1fef4929cf1d (11 August 2026)",
        "1fef4929cf1d (2026-08-11) extra",
    ],
)
def test_bug_checker_rejects_missing_or_malformed_revision(revision):
    calls = _run_bug_issue(revision)
    comment = next(call["params"]["body"] for call in calls if call["method"] == "createComment")
    assert "**Odysseus Revision**" in comment
    assert any(call["method"] == "setFailed" for call in calls)


@pytest.mark.parametrize("action", ["closed", "edited"])
def test_closed_issue_only_drops_ready_for_review(action):
    assert _run_closed_issue(action) == [
        {
            "method": "removeLabel",
            "params": {
                "owner": "odysseus-dev",
                "repo": "odysseus",
                "issue_number": 42,
                "name": "ready for review",
            },
        }
    ]
