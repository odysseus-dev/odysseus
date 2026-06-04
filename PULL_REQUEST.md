## Summary

Refactored the prompt compaction logic (`_make_compact_section` and settings profile resolution) to base dynamic compaction strictly on the model's context window size instead of system RAM. Additionally, redesigned the compaction algorithm to preserve 100% of the functional tool guidelines and safety constraints (such as sandbox limitations and background command execution instructions) while stripping out only heavy example code blocks and verbose endpoint routing tables. This prevents tool calling degradation in smaller/local models, resolving feedback from @elic664.

## Linked Issue

<!-- Every PR should be linked to an issue.
     Use one of:  Fixes #NNN  |  Part of #NNN  |  Closes #NNN  -->

Fixes #664

## Type of Change

- [ ] Bug fix (non-breaking — fixes a confirmed issue)
- [ ] New feature (non-breaking — adds new behaviour)
- [ ] Breaking change (changes or removes existing behaviour)
- [x] Refactor / cleanup (behaviour unchanged)
- [ ] Documentation only
- [ ] CI / tooling / configuration

## Checklist

- [x] I searched open issues and open PRs — this is not a duplicate.
- [x] This PR targets `main`
- [x] My changes are limited to the scope described above — no unrelated refactors or whitespace changes mixed in.
- [x] I actually ran the app (`docker compose up` or `uvicorn app:app`) and verified the change works end-to-end. Type-checks and unit tests are not enough.

## How to Test

1. Set `agent_prompt_profile` to `auto` or `compact` in settings.
2. Run the agent loop with a model having a context window <= 16384 (or configure profile to `compact`).
3. Verify that tool schemas are present and description guidelines (such as `#!bg` instructions for bash, or sandbox limits) are kept, while example code blocks (such as the `pip install` lines in bash) are pruned.
4. Run the unit tests via `pytest tests/test_agent_compact_prompt.py` to verify that all compaction assertions pass.

## Visual / UI changes — REQUIRED if you touched anything that renders

*No visual changes included (backend refactor).*
