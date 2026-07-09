import re
import shutil
import subprocess
from pathlib import Path

import pytest

_HAS_NODE = shutil.which("node") is not None


@pytest.mark.skipif(not _HAS_NODE, reason="node binary not on PATH")
def test_opencode_setup_provider_aliases_resolve():
    source = Path("static/js/slashCommands.js").read_text()
    match = re.search(
        r"const SETUP_PROVIDER_URLS = \{[\s\S]*?\nfunction _normalizeSetupBaseUrl",
        source,
    )
    assert match, "setup provider helper block not found"
    helper_source = match.group(0).removesuffix("\nfunction _normalizeSetupBaseUrl")
    script = helper_source + r"""
function assert(condition, message) {
  if (!condition) throw new Error(message);
}
const zenFromCommand = _setupProviderFromInput('opencode zen');
assert(zenFromCommand && zenFromCommand.url === 'https://opencode.ai/zen/v1', 'opencode zen command alias failed');
const goFromCommand = _setupProviderFromInput('opencode-go');
assert(goFromCommand && goFromCommand.url === 'https://opencode.ai/zen/go/v1', 'opencode-go command alias failed');
const zenCredential = _extractSetupProviderCredential('opencode-zen sk-test');
assert(zenCredential && zenCredential.provider.name === 'OpenCode Zen', 'opencode-zen credential provider failed');
assert(zenCredential.credential === 'sk-test', 'opencode-zen credential extraction failed');
const goCredential = _extractSetupProviderCredential('opencode go sk-test');
assert(goCredential && goCredential.provider.name === 'OpenCode Go', 'opencode go credential provider failed');
assert(goCredential.credential === 'sk-test', 'opencode go credential extraction failed');
"""
    subprocess.run(["node", "-e", script], check=True)
