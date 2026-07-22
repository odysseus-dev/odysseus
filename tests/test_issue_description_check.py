"""Regression coverage for issue-description label lifecycle events."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest


_REPO = Path(__file__).resolve().parent.parent
_CHECKER = _REPO / ".github" / "scripts" / "check-issue-description.js"
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


def test_workflow_handles_issue_closures():
    workflow = _WORKFLOW.read_text()
    assert "types: [opened, edited, reopened, closed]" in workflow


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
