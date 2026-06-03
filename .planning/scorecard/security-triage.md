# Security Triage Notes

Triage rationale for every finding in the scorecard's `security_findings` list
(referenced by the `triaged` flags in `baseline.json`). Populate a row as each
finding arises; high/critical findings must be resolved (bump the pin) or carry a
written rationale here (D-11: `security_high_critical` max is 0).

## bandit audit pass (Plan 05, Task 1)

**Tool:** bandit 1.9.4, run in `python:3.12-slim` against
`app.py src routes core mcp_servers companion services` with `-c pyproject.toml`.
**Result:** 319 findings — **0 HIGH**, 29 MEDIUM, 290 LOW. No genuine high-severity
issue exists, so nothing required a code fix; every finding is either an intentional,
auth-gated admin feature (THREAT_MODEL.md) or a verified false positive. The codes below
are encoded as documented per-code `[tool.bandit] skips` in `pyproject.toml` (NOT a blanket
disable — a future HIGH-severity code such as B602 still fires). After populating the skips,
`bandit -c pyproject.toml -r ...` exits 0.

### Per-code classification

| Test ID | Name | Count | Severity | Disposition | Rationale |
|---------|------|-------|----------|-------------|-----------|
| B404 | import subprocess | 9 | LOW | skip (intentional) | Required by the shell tool / cookbook model-runner / builtin actions — deliberately-provided admin capabilities (THREAT_MODEL.md). |
| B603 | subprocess_without_shell_equals_true | 12 | LOW | skip (intentional) | Admin shell/runner invocations; auth-gated admin feature by design. |
| B604 | shell=True | 4 | MEDIUM (LOW conf) | skip (intentional) | `src/builtin_actions.py` admin shell tool — `shell=True` is the feature, not a bug. |
| B607 | start_process_with_partial_path | 3 | LOW | skip (intentional) | Admin runner tools rely on PATH resolution; acceptable for the admin shell surface. |
| B110 | try_except_pass | 224 | LOW | skip (idiom) | Degrade-gracefully convention (CLAUDE.md) — optional subsystems swallow to stay alive. |
| B112 | try_except_continue | 33 | LOW | skip (idiom) | Same degrade-gracefully idiom inside loops. |
| B104 | hardcoded_bind_all_interfaces | 8 | MEDIUM | skip (false positive) | `"0.0.0.0"` appears as LLM host/URL **string defaults** in model-host config, not real socket binds; app binds loopback by default ("loopback-bound by default for safety"). |
| B105 | hardcoded_password_string | 7 | LOW | skip (false positive) | OAuth token URL, numeric setting defaults (`"6000"`/`"200000"`/`"0.95"`), and a shell snippet string — none are credentials. |
| B108 | hardcoded_tmp_directory | 3 | MEDIUM | skip (intentional) | `src/tool_execution.py` sandbox scratch paths, intentional. |
| B311 | random | 2 | LOW | skip (non-crypto) | Non-cryptographic pseudo-random for gallery/compare display IDs, not security tokens. |
| B103 | set_bad_file_permissions | 2 | MEDIUM | skip (intentional) | `chmod 0o755` makes generated cookbook runner shims executable — executability is required. |
| B608 | hardcoded_sql_expressions | 12 | MEDIUM | skip (false positive) | Verified parameterized: user values use bound `?` params; f-string interpolation is only computed `?,?` placeholder counts or internal allow-listed table names (never untrusted input). |

**Total findings:** 319. **Skipped (documented above):** 319. **Fixed:** 0. **Accepted-without-skip:** 0.
**High/critical unaddressed:** 0 → satisfies the scorecard `security_high_critical` max=0 threshold.

## pip-audit CVEs

| Vulnerability ID | Package | Severity | Fix version | Disposition | Rationale |
|------------------|---------|----------|-------------|-------------|-----------|
| _none at baseline_ | | | | | |

> The pip-audit CI job runs `pip-audit -r requirements.lock` against the hashed lock on
> every PR + push:main. Add an `--ignore-vuln GHSA-...` to the workflow and a row here only
> for a documented-triaged CVE; high/critical CVEs must otherwise be resolved by bumping the pin.
