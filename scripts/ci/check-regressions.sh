#!/usr/bin/env bash
set -euo pipefail

require_file() {
  local file="$1"
  local message="$2"

  if [ ! -f "$file" ]; then
    echo "regression check failed: $message" >&2
    echo "missing file: $file" >&2
    exit 1
  fi
}

require_pattern() {
  local file="$1"
  local pattern="$2"
  local message="$3"

  if ! grep -Eq "$pattern" "$file"; then
    echo "regression check failed: $message" >&2
    echo "missing pattern '$pattern' in $file" >&2
    exit 1
  fi
}

require_absent() {
  local file="$1"
  local pattern="$2"
  local message="$3"

  if grep -Eq "$pattern" "$file"; then
    echo "regression check failed: $message" >&2
    echo "forbidden pattern '$pattern' found in $file" >&2
    exit 1
  fi
}

require_file ".github/workflows/ci.yml" "unit tests must run in GitHub Actions"
require_file ".github/workflows/package.yml" "package builds must run in GitHub Actions"
require_file "pyproject.toml" "Python package metadata must stay explicit"
require_file "MANIFEST.in" "source distributions must include runtime assets"
require_file "scripts/ci/build-package-artifacts.sh" "package build steps must be reproducible locally"
require_file "scripts/ci/finalize-artifacts.sh" "artifacts must have checksums and manifests"
require_file "scripts/ci/install-rpm-build-deps.sh" "RPM build dependencies must be installed through a retryable script"
require_file "packaging/README.md" "maintainer-owned publishing placeholders must be documented"

require_pattern ".github/workflows/ci.yml" "python -m pytest" "unit tests must execute pytest"
require_pattern ".github/workflows/ci.yml" "scripts/ci/check-regressions.sh" "regression guards must run in CI"
require_pattern ".github/workflows/package.yml" "python -m build --sdist --wheel|scripts/ci/build-package-artifacts.sh" "Python package artifacts must be built"
require_pattern ".github/workflows/package.yml" "docker/build-push-action" "container images must be built by the package pipeline"
require_pattern ".github/workflows/package.yml" "github.event_name == 'push'" "package publishing must be gated to pushes/tags"
require_pattern ".github/workflows/package.yml" "GitHub Release artifacts" "tagged builds must aggregate artifacts into a GitHub Release"
require_pattern ".github/workflows/package.yml" "actions/upload-artifact" "package outputs must be retained as workflow artifacts"
require_pattern ".github/workflows/package.yml" "actions/download-artifact" "release job must collect package artifacts"
require_pattern ".github/workflows/package.yml" "refs/tags/v" "releases must be driven by v* tags"
require_pattern ".github/workflows/package.yml" "install-rpm-build-deps\\.sh" "RPM distro installs must use the retryable install script"
require_pattern ".github/workflows/package.yml" "smoke-windows-package\\.ps1" "Windows packaged binary must be smoke-tested in CI"
require_pattern ".github/workflows/package.yml" "ExecutionPolicy Bypass" "Windows smoke tests must run under CI-friendly PowerShell execution policy"
require_pattern "scripts/ci/smoke-windows-package.ps1" "odysseus-windows-install-smoke" "Windows smoke test must launch from an isolated install directory"
require_pattern "scripts/ci/smoke-windows-package.ps1" "LOCALAPPDATA" "Windows smoke test must verify packaged writable data behavior"
require_pattern "scripts/ci/smoke-windows-package.ps1" "/static/app\\.js" "Windows smoke test must verify bundled static JavaScript"
require_pattern "scripts/ci/smoke-windows-package.ps1" "/static/style\\.css" "Windows smoke test must verify bundled static CSS"
require_pattern "scripts/ci/finalize-artifacts.sh" "SHA256SUMS" "artifact checksums must be present in package outputs"
require_pattern "scripts/ci/install-rpm-build-deps.sh" "retry 5 zypper" "openSUSE RPM build dependency installs must retry mirror timeouts"
require_pattern "pyproject.toml" "\\[project\\]" "PEP 621 project metadata must stay present"
require_pattern "pyproject.toml" "odysseus-ai-workspace" "distribution name must stay explicit"
require_pattern "pyproject.toml" "requirements.txt" "runtime dependencies must be sourced from requirements.txt"
require_pattern "MANIFEST.in" "recursive-include static" "sdist must include web assets"
require_pattern "MANIFEST.in" "recursive-include docs" "sdist must include Pages/demo assets"
require_pattern "Dockerfile" "ENTRYPOINT \\[\"/usr/local/bin/entrypoint.sh\"\\]" "Docker runtime entrypoint must stay explicit"
require_pattern "packaging/README.md" "AUR_SSH_PRIVATE_KEY" "AUR publishing secret must remain a maintainer placeholder"
require_pattern "packaging/README.md" "SNAPCRAFT_STORE_CREDENTIALS" "Snap publishing secret must remain a maintainer placeholder"
require_pattern "packaging/README.md" "No third-party store credentials" "PR package builds must not require personal publishing keys"
require_pattern "packaging/README.md" 'Push a matching `vX.Y.Z` tag' "tag-driven release instructions must be documented"
require_pattern "packaging/README.md" "website/GitHub Pages deployment is intentionally out of scope" "website deployment must stay out of this package pipeline"
require_pattern "patches/windows/pyinstaller-static.patch" "_configure_windows_frozen_database_url" "Windows packaged app database path must stay in the Windows packaging patch"
require_pattern "patches/windows/pyinstaller-static.patch" "_resolve_windows_static_dir" "Windows packaged app static asset resolution must stay in the Windows packaging patch"

if [ -f ".github/workflows/pages.yml" ]; then
  echo "regression check failed: GitHub Pages deployment is out of scope for this CI/CD package PR" >&2
  exit 1
fi

test_count="$(find scripts/tests -maxdepth 1 -type f \( -name 'test_*.py' -o -name '*_test.py' \) | wc -l | tr -d ' ')"
if [ "${test_count}" -lt 10 ]; then
  echo "regression check failed: expected at least 10 Python unit/regression test files, found ${test_count}" >&2
  exit 1
fi

target_count="$(
  grep -E "target: (debian12|debian13|ubuntu2404|ubuntu2604|fedora43|fedora44|el10|opensuse-tumbleweed|alpine320|alpine322|alpine323)" .github/workflows/package.yml | wc -l | tr -d ' '
)"
fixed_targets="$(
  grep -E "name: (Python wheel and sdist|Git source archive|Docker image|AUR source package|macOS standalone|Windows .exe installer)" .github/workflows/package.yml | wc -l | tr -d ' '
)"
total_targets=$((target_count + fixed_targets))
if [ "${total_targets}" -ne 17 ]; then
  echo "regression check failed: expected 17 package targets, found ${total_targets}" >&2
  exit 1
fi

echo "Regression checks passed."
