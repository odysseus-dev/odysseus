"""Behaviour of scripts/ref_parity_audit.py.

The audit answers one question: which commits on a source ref left no trace in a
target ref? The interesting cases are the ones a plain `git log A..B` gets wrong
-- a change that was reproduced on the target under a different SHA must read as
present, and a change whose lines were merely re-indented must not read as
missing. Those are exercised against a real throwaway repository rather than a
fake, because the verdicts come from git's own `grep` and `diff`.
"""
import json
import shutil
import subprocess

import pytest

from tests.helpers.cli_loader import load_script


@pytest.fixture(scope="module")
def audit():
    return load_script("ref_parity_audit.py")


# --------------------------------------------------------------------------- #
# probe selection
# --------------------------------------------------------------------------- #


def test_short_and_thin_lines_are_not_probes(audit):
    assert not audit.is_probe_candidate("pass")
    assert not audit.is_probe_candidate("})")
    # Long enough, but nothing named in it: a separator comment is in every file.
    assert not audit.is_probe_candidate("# " + "-" * 60)
    assert audit.is_probe_candidate("_hosts_cache_time = now  # remember the empty answer")


def test_probes_are_ranked_by_distinctiveness_and_capped(audit):
    lines = [
        ("src/a.py", "        x = 1"),
        ("src/a.py", "        parsed_args = json.loads(args) if args else []"),
        ("src/a.py", "        if not isinstance(parsed_args, list): raise HTTPException(400)"),
        ("src/a.py", "        return parsed_args"),
    ]

    probes = audit.pick_probes(lines, limit=2)

    assert probes == [
        "if not isinstance(parsed_args, list): raise HTTPException(400)",
        "parsed_args = json.loads(args) if args else []",
    ]


def test_probes_are_stripped_so_a_reindented_port_still_counts(audit):
    probes = audit.pick_probes([("src/a.py", "\t\tself.cache_time = now  # keep the empty answer")], limit=4)

    assert probes == ["self.cache_time = now  # keep the empty answer"]


def test_identical_added_lines_yield_one_probe(audit):
    lines = [
        ("src/a.py", "    raise HTTPException(400, 'args must be a JSON array')"),
        ("src/b.py", "    raise HTTPException(400, 'args must be a JSON array')"),
    ]

    assert len(audit.pick_probes(lines, limit=4)) == 1


# --------------------------------------------------------------------------- #
# path exclusion and diff parsing
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "path, excluded",
    [
        ("static/lib/marked.js", True),
        ("static/js/chat.min.js", True),
        ("package-lock.json", True),
        ("assets/logo.png", True),
        ("src/model_discovery.py", False),
        ("static/js/chat.js", False),
    ],
)
def test_default_excludes_cover_vendored_and_binary_paths(audit, path, excluded):
    assert audit.is_excluded(path, audit.DEFAULT_EXCLUDES) is excluded


def test_added_lines_skips_headers_deletions_and_excluded_paths(audit):
    patch = "\n".join(
        [
            "diff --git a/src/a.py b/src/a.py",
            "--- a/src/a.py",
            "+++ b/src/a.py",
            "@@ -1 +1 @@",
            "-old_line_that_is_long_enough()",
            "+new_line_that_is_long_enough()",
            "diff --git a/static/lib/vendor.js b/static/lib/vendor.js",
            "--- a/static/lib/vendor.js",
            "+++ b/static/lib/vendor.js",
            "@@ -1 +1 @@",
            "+vendored_line_that_is_long_enough()",
            "diff --git a/src/gone.py b/src/gone.py",
            "--- a/src/gone.py",
            "+++ /dev/null",
        ]
    )

    assert audit.added_lines(patch, audit.DEFAULT_EXCLUDES) == [
        ("src/a.py", "new_line_that_is_long_enough()")
    ]


# --------------------------------------------------------------------------- #
# end-to-end against a real repository
# --------------------------------------------------------------------------- #

MARKER_ABSENT = "def confine_agent_to_workspace(root_dir, session_store):"
MARKER_PORTED = "def cache_the_empty_tailscale_answer(now, hosts_cache_time):"
MARKER_KEPT = "def shared_helper_present_on_both_lines(value):"


def _git(repo, *args):
    subprocess.run(
        [
            "git",
            "-C",
            str(repo),
            "-c",
            "user.name=Audit Fixture",
            "-c",
            "user.email=fixture@example.invalid",
            "-c",
            "commit.gpgsign=false",
            *args,
        ],
        check=True,
        capture_output=True,
        text=True,
    )


@pytest.fixture
def two_line_repo(tmp_path):
    """A repository shaped like the real problem: two branches off one root.

    `source` carries three commits. `target` independently reproduces one of
    them (re-indented, so only a stripped probe finds it), never sees another,
    and carries a file of its own plus a vendored file that must stay out of
    the presence diff.
    """
    if not shutil.which("git"):
        pytest.skip("git is not available")

    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "root")
    (repo / "base.py").write_text(MARKER_KEPT + "\n    return value\n", encoding="utf-8")
    _git(repo, "add", "base.py")
    _git(repo, "commit", "-q", "-m", "chore: base")

    _git(repo, "checkout", "-q", "-b", "source")
    (repo / "confinement.py").write_text(MARKER_ABSENT + "\n    return root_dir\n", encoding="utf-8")
    _git(repo, "add", "confinement.py")
    _git(repo, "commit", "-q", "-m", "fix: confine the agent to its workspace")

    (repo / "discovery.py").write_text(MARKER_PORTED + "\n    return now\n", encoding="utf-8")
    _git(repo, "add", "discovery.py")
    _git(repo, "commit", "-q", "-m", "fix: cache an empty lookup")

    (repo / "base.py").write_text(MARKER_KEPT + "\n", encoding="utf-8")
    _git(repo, "add", "base.py")
    _git(repo, "commit", "-q", "-m", "refactor: drop the base body")

    _git(repo, "checkout", "-q", "-b", "target", "root")
    # The same fix, written by hand at a different indentation and in another file.
    (repo / "net.py").write_text("class Net:\n    " + MARKER_PORTED + "\n        return now\n", encoding="utf-8")
    (repo / "target_only.py").write_text("# only on the target line\n", encoding="utf-8")
    (repo / "static").mkdir()
    (repo / "static" / "lib").mkdir()
    (repo / "static" / "lib" / "vendor.min.js").write_text("var a=1;\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "feat: reproduce the lookup cache by hand")

    return repo


def _verdicts(report):
    return {item.commit.subject: item.verdict for item in report.verdicts}


def test_absent_ported_and_deletion_only_commits_are_told_apart(audit, two_line_repo):
    report = audit.audit(source="source", target="target", repo=two_line_repo)

    assert _verdicts(report) == {
        "fix: confine the agent to its workspace": audit.VERDICT_ABSENT,
        "fix: cache an empty lookup": audit.VERDICT_PRESENT,
        "refactor: drop the base body": audit.VERDICT_NO_PROBE,
    }


def test_the_absent_commit_reports_the_probes_it_looked_for(audit, two_line_repo):
    report = audit.audit(source="source", target="target", repo=two_line_repo)
    absent = report.by_verdict(audit.VERDICT_ABSENT)

    assert [item.commit.subject for item in absent] == ["fix: confine the agent to its workspace"]
    assert absent[0].probes == (MARKER_ABSENT,)
    assert absent[0].found == ()
    assert absent[0].paths == ("confinement.py",)


def test_file_presence_diff_is_exact_and_skips_vendored_paths(audit, two_line_repo):
    report = audit.audit(source="source", target="target", repo=two_line_repo)

    assert report.source_only_files == ("confinement.py", "discovery.py")
    assert report.target_only_files == ("net.py", "target_only.py")


def test_probes_are_only_drawn_from_included_paths(audit, two_line_repo):
    """Excluding discovery.py leaves its commit with nothing to probe."""
    report = audit.audit(
        source="source",
        target="target",
        repo=two_line_repo,
        excludes=list(audit.DEFAULT_EXCLUDES) + ["discovery.py"],
    )

    assert _verdicts(report)["fix: cache an empty lookup"] == audit.VERDICT_NO_PROBE


def test_a_probe_found_only_partly_reads_as_partial(audit, two_line_repo):
    # base.py's marker is on both branches; confinement.py's is not. A commit
    # adding both is the partial case.
    _git(two_line_repo, "checkout", "-q", "source")
    (two_line_repo / "mixed.py").write_text(
        MARKER_KEPT + "\n" + MARKER_ABSENT + "\n", encoding="utf-8"
    )
    _git(two_line_repo, "add", "mixed.py")
    _git(two_line_repo, "commit", "-q", "-m", "fix: a change that half landed")

    report = audit.audit(source="source", target="target", repo=two_line_repo, probe_limit=4)

    assert _verdicts(report)["fix: a change that half landed"] == audit.VERDICT_PARTIAL


def test_first_parent_traversal_diffs_a_merge_against_its_first_parent(audit, two_line_repo):
    """A squash-free merge carries its content in the merge commit itself."""
    _git(two_line_repo, "checkout", "-q", "-b", "side", "root")
    (two_line_repo / "from_side.py").write_text(MARKER_ABSENT + "\n", encoding="utf-8")
    _git(two_line_repo, "add", "from_side.py")
    _git(two_line_repo, "commit", "-q", "-m", "fix: arrived on a side branch")
    _git(two_line_repo, "checkout", "-q", "source")
    _git(two_line_repo, "merge", "-q", "--no-ff", "-m", "Merge commit from fork", "side")

    report = audit.audit(
        source="source", target="target", repo=two_line_repo, traversal="first-parent"
    )
    merge = [v for v in report.verdicts if v.commit.subject == "Merge commit from fork"]

    assert len(merge) == 1
    assert merge[0].commit.parent_count == 2
    assert merge[0].verdict == audit.VERDICT_ABSENT
    assert MARKER_ABSENT in merge[0].probes


def test_linear_traversal_reports_the_side_branch_commit_not_the_merge(audit, two_line_repo):
    _git(two_line_repo, "checkout", "-q", "-b", "side", "root")
    (two_line_repo / "from_side.py").write_text(MARKER_ABSENT + "\n", encoding="utf-8")
    _git(two_line_repo, "add", "from_side.py")
    _git(two_line_repo, "commit", "-q", "-m", "fix: arrived on a side branch")
    _git(two_line_repo, "checkout", "-q", "source")
    _git(two_line_repo, "merge", "-q", "--no-ff", "-m", "Merge commit from fork", "side")

    subjects = [
        v.commit.subject
        for v in audit.audit(source="source", target="target", repo=two_line_repo).verdicts
    ]

    assert "fix: arrived on a side branch" in subjects
    assert "Merge commit from fork" not in subjects


def test_date_window_bounds_the_commit_range(audit, two_line_repo):
    empty = audit.audit(source="source", target="target", repo=two_line_repo, until="2000-01-01")
    full = audit.audit(source="source", target="target", repo=two_line_repo, since="2000-01-01")

    assert empty.verdicts == []
    assert len(full.verdicts) == 3
    # The presence diff is not date-filtered: it compares the two trees.
    assert empty.source_only_files == full.source_only_files


# --------------------------------------------------------------------------- #
# rendering and cli
# --------------------------------------------------------------------------- #


def test_markdown_report_leads_with_the_absent_commits(audit, two_line_repo):
    text = audit.render_markdown(audit.audit(source="source", target="target", repo=two_line_repo))

    assert "No trace in the target: **1**" in text
    assert "Fully present: **1**" in text
    assert "fix: confine the agent to its workspace" in text
    assert "`confinement.py`" in text


def test_json_output_is_written_to_the_requested_path(audit, two_line_repo, tmp_path, capsys):
    out = tmp_path / "report.json"

    code = audit.main(
        [
            "--source",
            "source",
            "--target",
            "target",
            "--repo",
            str(two_line_repo),
            "--format",
            "json",
            "--output",
            str(out),
            "--quiet",
        ]
    )

    assert code == 0
    assert capsys.readouterr().out == ""
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["totals"] == {"absent": 1, "no-probe": 1, "partial": 0, "present": 1}
    assert payload["source_only_files"] == ["confinement.py", "discovery.py"]


def test_an_unknown_ref_exits_two_with_a_message(audit, two_line_repo, capsys):
    code = audit.main(
        [
            "--source",
            "no-such-ref",
            "--target",
            "target",
            "--repo",
            str(two_line_repo),
            "--quiet",
        ]
    )

    assert code == 2
    assert "error:" in capsys.readouterr().err


def test_probe_count_must_be_positive(audit):
    with pytest.raises(SystemExit):
        audit.build_parser().parse_args(
            ["--source", "a", "--target", "b", "--probes", "0"]
        )
