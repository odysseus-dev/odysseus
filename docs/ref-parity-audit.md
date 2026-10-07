# Ref parity audit

`scripts/ref_parity_audit.py` reports which commits on one git ref left no trace
in another, and which files exist on one and not the other. It is read-only: it
runs `git log`, `git show`, `git diff`, `git grep`, `git ls-tree` and
`git merge-base`, writes nothing to the repository, touches no remote, and does
not import the application package.

## Why it exists

`lab` and the public `dev` line share only the repository's first commit as a
merge base, so `git log lab..dev` lists thousands of commits — nearly all of
which are in fact present on both sides, having arrived under different SHAs. A
plain log tells you nothing about what is actually missing.

The question that matters before `lab` becomes a release is narrower: is there a
fix on the public line that never reached `lab`? This script answers that by
sampling distinctive added lines from each commit and searching the other tree
for them.

## Running it

```bash
git remote add public https://github.com/odysseus-dev/odysseus.git   # once
git fetch public dev --no-tags

scripts/ref_parity_audit.py --source public/dev --target lab --since 2026-08-10
```

Roughly 30 seconds for a 100-commit window; it grows linearly, so bound a wide
audit with `--since`. Add `--format json` for a machine-readable report and
`--output PATH` to write it to a file.

| Flag | Effect |
|---|---|
| `--source REF` | The ref whose commits are audited. Required. |
| `--target REF` | The ref searched for traces of them. Required. |
| `--since` / `--until` | Bound the commit range. Both filter **committer** date, which is also the date the report prints. |
| `--traversal linear` | Default. Individual authored commits, merges dropped. Finds a fix that arrived on a side branch. |
| `--traversal first-parent` | One row per merge into the source branch, which reads as one row per merged pull request. |
| `--probes N` | Probe lines sampled per commit, default 4. |
| `--exclude GLOB` | Extra path glob whose lines are not used as probes. Repeatable. |
| `--no-default-excludes` | Drop the built-in vendored / lockfile / binary exclusions. |
| `--top N` | Rows shown per file list, default 50. |
| `--repo PATH` | Repository to run in. Defaults to this checkout. |

## How a verdict is reached

For each commit in `target..source`, the script takes the patch with no context
lines, collects the added lines, drops the ones from vendored code, committed
build output, lockfiles and binaries, and keeps those that are at least 24
characters long and name at least two distinct identifiers. It ranks what is
left by how many distinct identifiers each line carries (length breaks ties),
takes the top `--probes`, and searches the whole target tree for each one with
`git grep --fixed-strings`.

Probes are stripped of leading and trailing whitespace, so a change that was
re-indented on the target still counts as present. The whole target tree is
searched, not the same file, because a ported fix routinely moves.

| Verdict | Meaning |
|---|---|
| **absent** | No probe found anywhere in the target. Treat as a real gap and read the diff. |
| **partial** | Some probes found. **Inconclusive.** A line can be rewritten by a refactor on the target and still be the same change. |
| **present** | Every probe found. The change is almost certainly there in some form. |
| **no-probe** | Nothing to sample: a deletion-only commit, or one touching only excluded paths. No verdict. |

## What is exact and what is a heuristic

**Exact:** the two file-presence lists. They come from `git ls-tree` on both
refs, so a file in "on the source and not the target" is definitely not there.

**Heuristic:** every commit verdict. It samples at most four lines out of a
diff that may be hundreds, and a probe can be absent because the area was
refactored rather than because the change was never made.

The two complement each other in a specific and useful way. A commit that reads
**present** while one of the files it added shows up in the source-only list is
almost always a fix whose production change was reproduced on the target without
its test. The line sampling cannot see that; the presence diff can.

Read the diff before porting anything. The verdicts say where to look, not what
to do.

## Tests

`tests/test_ref_parity_audit.py`. The end-to-end cases build a throwaway
repository with two branches off one root, so the verdicts come from git's own
`grep` and `diff` rather than from a fake.
