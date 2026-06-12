# Security CI guide

This project runs a set of automated security checks on every pull request and
on every push to `main`. This page explains what each one does, whether it can
block a merge, and the few one-time settings you should turn on to get the full
benefit.

## What runs, and why

Each check lives in its own file under `.github/workflows/`. They run
automatically; you do not start them.

| Check | What it protects against | Blocks a merge? |
|---|---|---|
| **Secret scan** (gitleaks) | An API key, token, or password being committed by mistake or on purpose | Yes |
| **Workflow security** (actionlint + zizmor) | A broken or insecure automation file that could leak the repo's access token | Yes |
| **Dependency review** | A pull request that adds a software library with a known security hole | Yes |
| **pip-audit** | Known security holes in the Python libraries already used | No (advisory) |
| **Container scan: hadolint** | Mistakes and insecure patterns in the `Dockerfile` | Yes |
| **Container scan: Trivy** | Known security holes in the Docker image | No (advisory) |
| **CodeQL** | Real bugs in the app's own code: injection, auth mistakes, path traversal | No (advisory) |

"Blocks a merge" means a red X appears on the pull request and, once you enable
the setting below, the **Merge** button is disabled until it is fixed.

"Advisory" means it reports problems into the repository's **Security** tab so
you can review them on your own schedule, but it never stops a merge. These are
advisory on purpose: they often flag long-standing issues in other people's
libraries, not something a given pull request introduced.

## Where results appear

- **Checks tab of a pull request**: the pass/fail of each check. A green tick is
  good; a red X needs attention.
- **Security tab of the repository**: detailed findings from the advisory
  scanners (Trivy and CodeQL). This is your dashboard.

## If a check fails

- **Secret scan failed**: a real credential may have been committed. Treat it as
  leaked: rotate (regenerate) that key or token immediately, then remove it from
  the file. Do not just delete the commit; assume it was seen.
- **Dependency review failed**: the pull request adds a library with a known
  vulnerability. Ask the contributor to use a patched version, or decline the
  change.
- **hadolint / workflow security failed**: the contributor changed the
  `Dockerfile` or an automation file in a way the linter rejects. Ask them to
  address the message shown in the failed check.

## One-time settings to turn on

These two settings unlock the full value. You only do them once.

### 1. Require the blocking checks before merging

This makes the **Merge** button refuse to work until the gating checks pass.

1. Go to the repository on GitHub.
2. Click **Settings** (top right of the repo).
3. In the left sidebar, click **Branches**.
4. Under **Branch protection rules**, click **Add branch ruleset** (or **Add
   rule**), and set the branch name pattern to `dev` (this is the branch all
   pull requests target; `main` is fast-forwarded at releases).
5. Enable **Require status checks to pass before merging**.
6. In the search box that appears, add these checks by name:
   - `Python syntax (compileall)`
   - `JS syntax (node --check)`
   - `gitleaks`
   - `actionlint`
   - `zizmor (Actions SAST)`
   - `hadolint (Dockerfile lint)`
   - `dependency-review (PR gate)`

   The first two come from the correctness CI (`ci.yml`); the rest are this
   security suite. Leave pytest, pip-audit, Trivy, and CodeQL unchecked so they
   stay advisory.
7. Also enable **Require a pull request before merging** and **Require review
   from Code Owners** (this uses the `.github/CODEOWNERS` file so every change
   needs your sign-off).
8. Click **Create** / **Save changes**.

Note: a check name only appears in the list after it has run at least once, so
let the workflows run on one pull request first, then add them here.

### 2. Turn on the Security tab features

1. **Settings -> Code security** (or **Code security and analysis**).
2. Turn on **Dependency graph** (usually on by default for public repos) -- this
   powers Dependency review and Dependabot.
3. Turn on **Dependabot alerts** and **Dependabot security updates**.
4. Under **Code scanning**, you have two ways to scan the app code with CodeQL:
   - The included `codeql.yml` workflow already scans `main` and runs weekly.
   - To also scan **pull requests** (recommended, since most contributions come
     from forks), click **Set up -> Default** under Code scanning. GitHub then
     runs CodeQL on pull requests for you, with no token limitations.

## Keeping it current

`.github/dependabot.yml` opens small weekly pull requests to update Python and
npm packages, the Docker base image, and the pinned automation actions
themselves. Review and merge those like any other pull request; they keep the
project patched without manual tracking.
