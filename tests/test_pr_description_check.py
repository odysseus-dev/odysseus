"""Regression coverage for PR description and validation-state checks."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest


_REPO = Path(__file__).resolve().parent.parent
_CHECKER = _REPO / ".github" / "scripts" / "check-pr-description.js"
_WORKFLOW = _REPO / ".github" / "workflows" / "pr-description-check.yml"
pytestmark = pytest.mark.skipif(not shutil.which("node"), reason="node not on PATH")


def _body(*, app_ran=False, app_not_run=False, screenshot=False, media=""):
    return f"""## Summary

This focused change has enough concrete summary detail for the checker.

## Linked Issue

Fixes #5934

## Type of Change

- [x] CI / tooling / configuration

## Checklist

- [x] I searched open issues and open PRs.
- [{'x' if app_ran else ' '}] I actually ran the app and verified the change works end-to-end.
- [{'x' if app_not_run else ' '}] I did not run the app/runtime validation and stated that gap in How to Test.

## How to Test

Run the focused checker regression tests and inspect their exact assertions.

## Visual / UI changes

- [{'x' if screenshot else ' '}] **Screenshot or short clip** of the running change.

### Screenshots / clips

{media}
"""


def _run_checker(files, body, *, missing_labels=(), draft=False):
    harness = r"""
const checkPrDescription = require(process.argv[1]);
const input = JSON.parse(process.argv[2]);
const calls = [];
const listFiles = async () => {};
const listComments = async () => {};

const github = {
  paginate: async (method) => {
    if (method === listFiles) return input.files.map(filename => ({ filename }));
    if (method === listComments) return [];
    throw new Error('unexpected paginated method');
  },
  rest: {
    pulls: { listFiles },
    issues: {
      listComments,
      getLabel: async ({ name }) => {
        if (input.missingLabels.includes(name)) {
          const error = new Error(`missing label: ${name}`);
          error.status = 404;
          throw error;
        }
        return { data: { name } };
      },
      addLabels: async (params) => calls.push({ method: 'addLabels', params }),
      removeLabel: async (params) => calls.push({ method: 'removeLabel', params }),
      createComment: async (params) => calls.push({ method: 'createComment', params }),
      updateComment: async (params) => calls.push({ method: 'updateComment', params }),
      deleteComment: async (params) => calls.push({ method: 'deleteComment', params }),
    },
  },
};
const context = {
  payload: {
    pull_request: {
      number: 42,
      body: input.body,
      draft: input.draft,
    },
  },
  repo: { owner: 'odysseus-dev', repo: 'odysseus' },
};
const core = {
  warning: (message) => calls.push({ method: 'warning', message }),
  setFailed: (message) => calls.push({ method: 'setFailed', message }),
};

checkPrDescription({ github, context, core })
  .then(() => process.stdout.write(JSON.stringify(calls)))
  .catch((error) => {
    console.error(error);
    process.exitCode = 1;
  });
"""
    payload = json.dumps(
        {
            "files": files,
            "body": body,
            "missingLabels": list(missing_labels),
            "draft": draft,
        }
    )
    proc = subprocess.run(
        ["node", "-e", harness, str(_CHECKER), payload],
        capture_output=True,
        text=True,
        cwd=str(_REPO),
        timeout=30,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


def _added_labels(calls):
    return {
        call["params"]["labels"][0]
        for call in calls
        if call["method"] == "addLabels"
    }


def _comment(calls):
    comments = [call for call in calls if call["method"] == "createComment"]
    return comments[0]["params"]["body"] if comments else ""


@pytest.mark.parametrize(
    ("files", "body"),
    [
        (["README.md"], _body()),
        (["routes/example.py"], _body(app_ran=True)),
        (
            ["static/js/example.js"],
            _body(
                app_ran=True,
                screenshot=True,
                media="https://github.com/user-attachments/assets/example",
            ),
        ),
    ],
)
def test_complete_expected_state_is_ready(files, body):
    calls = _run_checker(files, body)

    assert _added_labels(calls) == {"ready for review"}
    assert not _comment(calls)
    assert not any(call["method"] == "setFailed" for call in calls)


def test_ui_checkbox_without_media_still_needs_visual_evidence():
    calls = _run_checker(
        ["static/js/example.js"],
        _body(app_ran=True, screenshot=True),
    )

    assert _added_labels(calls) == {"needs visual evidence"}
    assert "does not contain an actual attachment or link" in _comment(calls)
    assert not any(call["method"] == "setFailed" for call in calls)


def test_ticked_screenshot_box_counts_without_the_template_bolding():
    body = _body(
        app_ran=True,
        screenshot=True,
        media="https://github.com/user-attachments/assets/example",
    ).replace("**Screenshot or short clip**", "Screenshot or short clip")

    calls = _run_checker(["static/js/example.js"], body)

    assert _added_labels(calls) == {"ready for review"}
    assert not _comment(calls)
    assert not any(call["method"] == "setFailed" for call in calls)


def test_explicit_not_run_is_honest_but_not_ready():
    calls = _run_checker(
        ["services/example.py"],
        _body(app_not_run=True),
    )

    assert _added_labels(calls) == {"needs runtime validation"}
    assert "explicitly reports that app/runtime validation was not performed" in _comment(calls)
    assert "does not prove that a local command ran" in _comment(calls)
    assert not any(call["method"] == "setFailed" for call in calls)


def test_missing_validation_label_fails_soft():
    calls = _run_checker(
        ["services/example.py"],
        _body(app_not_run=True),
        missing_labels=("needs runtime validation",),
    )

    assert "needs runtime validation" not in _added_labels(calls)
    assert any(
        call["method"] == "warning"
        and 'Label "needs runtime validation" does not exist' in call["message"]
        for call in calls
    )
    assert not any(call["method"] == "setFailed" for call in calls)


@pytest.mark.parametrize(
    "filename",
    ("requirements.txt", "requirements-optional.txt"),
)
def test_requirement_manifests_require_runtime_validation(filename):
    calls = _run_checker(
        [filename],
        _body(app_not_run=True),
    )

    assert _added_labels(calls) == {"needs runtime validation"}
    assert not any(
        call["method"] == "addLabels"
        and call["params"]["labels"] == ["ready for review"]
        for call in calls
    )
    assert any(
        call["method"] == "removeLabel"
        and call["params"]["name"] == "ready for review"
        for call in calls
    )
    assert "Changed-file classification: **backend/runtime**." in _comment(calls)


def test_old_template_runtime_pr_is_not_ready_without_attestation():
    calls = _run_checker(
        ["routes/example.py"],
        _body(),
    )

    assert _added_labels(calls) == {"needs runtime validation"}
    assert "App/runtime validation is not author-attested" in _comment(calls)
    assert not any(call["method"] == "setFailed" for call in calls)


def test_conflicting_runtime_attestations_are_not_ready():
    calls = _run_checker(
        ["services/example.py"],
        _body(app_ran=True, app_not_run=True),
    )

    assert _added_labels(calls) == {"needs runtime validation"}
    assert "both checked" in _comment(calls)
    assert not any(
        call["method"] == "addLabels"
        and call["params"]["labels"] == ["ready for review"]
        for call in calls
    )


def test_structurally_invalid_description_still_fails_hard_gate():
    calls = _run_checker(
        ["README.md"],
        "## Summary\nshort\n",
    )

    assert any(call["method"] == "setFailed" for call in calls)
    assert "PR description" in _comment(calls)
    assert "needs work" in _added_labels(calls)
    assert "ready for review" not in _added_labels(calls)


def test_github_workflow_only_change_does_not_require_app_runtime():
    calls = _run_checker(
        [".github/workflows/example.yml"],
        _body(),
    )

    assert _added_labels(calls) == {"ready for review"}
    assert not _comment(calls)
    assert not any(call["method"] == "setFailed" for call in calls)


def test_draft_pr_never_receives_ready_for_review():
    calls = _run_checker(
        ["README.md"],
        _body(),
        draft=True,
    )

    assert "ready for review" not in _added_labels(calls)
    assert any(
        call["method"] == "removeLabel"
        and call["params"]["name"] == "ready for review"
        for call in calls
    )
    assert not any(call["method"] == "setFailed" for call in calls)


def test_workflow_serializes_readiness_before_mergeability():
    workflow = _WORKFLOW.read_text()

    assert (
        "types: [opened, edited, synchronize, reopened, ready_for_review, "
        "converted_to_draft]"
    ) in workflow
    assert "group: pr-description-${{ github.event.pull_request.number }}" in workflow
    assert "cancel-in-progress: true" in workflow

    mergeable = workflow.split("  check-mergeable:", 1)[1]
    assert "needs: check-description" in mergeable
    assert (
        "if: ${{ !cancelled() && github.event.pull_request.user.type != 'Bot' }}"
        in mergeable
    )


def test_privileged_pr_workflow_executes_only_base_code():
    workflow = _WORKFLOW.read_text()

    assert "pull_request_target:" in workflow
    assert "ref: ${{ github.base_ref }}" in workflow
    assert "persist-credentials: false" in workflow
    assert "github.event.pull_request.head" not in workflow
