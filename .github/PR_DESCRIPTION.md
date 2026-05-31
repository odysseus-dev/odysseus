## Summary

This PR adds basic maintainer infrastructure to help handle the current (and future) volume of issues and PRs with less manual triage.

### What’s included

- **Issue templates**:
  - `bug_report.yml` — for actual bugs
  - `install_support.yml` — specifically for the flood of fresh install / Docker / platform issues (this is currently the majority of incoming reports)
  - `feature_request.yml`
- **PR template** with a lightweight checklist (focus on testing, security impact, and cross-platform considerations)
- **Suggested labels** (`labels.yml`) including platform-specific ones (`windows`, `macos`, `docker`, `support`, `needs-triage`, etc.)
- **CONTRIBUTING.md** — realistic guidance that sets expectations (important for a project in this state)

### Why this matters right now

With 50+ issues and 40+ PRs arriving within hours of launch, the lack of templates creates a lot of noise and extra work for the maintainer. These changes should make new reports higher signal by default.

### Notes

- This is intentionally minimal and opinionated toward reducing maintainer load rather than being overly corporate.
- The templates are designed around the reality of the project today (heavy fresh install friction, especially on Windows/macOS, ambitious agent capabilities, solo maintainer situation).
- Happy to iterate on the templates based on feedback.

This is purely infrastructure to make helping easier, not code changes.
