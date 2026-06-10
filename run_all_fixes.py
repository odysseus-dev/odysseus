import re

with open('docs/ARCHITECTURE.md', 'r') as f:
    arch = f.read()

# 1. document_tools.py in tree
if 'document_tools.py' not in arch:
    arch = arch.replace(
        '    │   │   ├── subprocess_tools.py\n    │   │   └── web_tools.py',
        '    │   │   ├── document_tools.py\n    │   │   ├── subprocess_tools.py\n    │   │   └── web_tools.py'
    )

# 1. document_tools.py in descriptions
replacement_doc_tools = """- **Subprocess Tools ([`src/agent_tools/subprocess_tools.py`](../src/agent_tools/subprocess_tools.py))**: Allows the agent to run arbitrary shell commands. It manages timeout constraints, captures `stdout` and `stderr` safely, and ensures long-running processes do not hang the main agent loop.
- **Web Tools ([`src/agent_tools/web_tools.py`](../src/agent_tools/web_tools.py))**: Includes utilities for fetching webpage content, often interacting with local headless browsers or `BeautifulSoup` to strip away visual clutter and return clean markdown directly to the agent's context.
- **Document Tools ([`src/agent_tools/document_tools.py`](../src/agent_tools/document_tools.py))**: Tools for managing and querying workspace documents."""

arch = arch.replace(
    '- **Subprocess Tools ([`src/agent_tools/subprocess_tools.py`](../src/agent_tools/subprocess_tools.py))**: Allows the agent to run arbitrary shell commands. It manages timeout constraints, captures `stdout` and `stderr` safely, and ensures long-running processes do not hang the main agent loop.\n- **Web Tools ([`src/agent_tools/web_tools.py`](../src/agent_tools/web_tools.py))**: Includes utilities for fetching webpage content, often interacting with local headless browsers or `BeautifulSoup` to strip away visual clutter and return clean markdown directly to the agent\'s context.',
    replacement_doc_tools
)

# 2. Add fonts in tree
if 'static/fonts/' not in arch:
    arch = arch.replace(
        '    │   │   └── xlsx.full.min.js\n    │   ├── app.js',
        '    │   │   └── xlsx.full.min.js\n    │   ├── fonts/\n    │   │   ├── FiraCode-Light.woff2\n    │   │   ├── FiraCode-Regular.woff2\n    │   │   ├── FiraCode-SemiBold.woff2\n    │   │   ├── Inter-Medium.woff2\n    │   │   ├── Inter-Regular.woff2\n    │   │   ├── Inter-SemiBold.woff2\n    │   │   └── custom/\n    │   │       └── GohuFont.ttf\n    │   ├── app.js'
    )

# 2. Add fonts to description
if 'static/fonts/' not in arch:
    replacement_fonts = """- **[`static/fonts/`](../static/fonts/)**: Contains the locally hosted webfonts used in the interface:
  - [`FiraCode-Light.woff2`](../static/fonts/FiraCode-Light.woff2), [`FiraCode-Regular.woff2`](../static/fonts/FiraCode-Regular.woff2), [`FiraCode-SemiBold.woff2`](../static/fonts/FiraCode-SemiBold.woff2): Fira Code fonts for monospace elements like code blocks.
  - [`Inter-Medium.woff2`](../static/fonts/Inter-Medium.woff2), [`Inter-Regular.woff2`](../static/fonts/Inter-Regular.woff2), [`Inter-SemiBold.woff2`](../static/fonts/Inter-SemiBold.woff2): Inter fonts for general typography.
  - [`custom/GohuFont.ttf`](../static/fonts/custom/GohuFont.ttf): Custom font utilized by specific themes.
- **[`static/app.js`](../static/app.js) & [`static/js/init.js`](../static/js/init.js)**: The main orchestrator."""

    arch = arch.replace(
        '- **[`static/app.js`](../static/app.js) & [`static/js/init.js`](../static/js/init.js)**: The main orchestrator.',
        replacement_fonts
    )

# 3. Fix GitHub Workflows section to use the proper list formatting and accordions
github_replacement = """
### GitHub Workflows ([`.github/`](../.github/))

<details>
<summary>View GitHub Workflows</summary>

- **[`ci.yml`](../.github/workflows/ci.yml)**: The primary Continuous Integration pipeline. It runs the Pytest suite, Node.js invariant tests, enforces typing with `mypy`, and checks formatting.
- **[`docker-publish.yml`](../.github/workflows/docker-publish.yml)**: Automatically builds and pushes multi-architecture (AMD64, ARM64) Docker images to the registry on new releases.
- **Issue & PR Validations**: Workflows like **[`issue-description-check.yml`](../.github/workflows/issue-description-check.yml)** and **[`pr-description-check.yml`](../.github/workflows/pr-description-check.yml)** execute scripts (e.g., [`check-pr-description.js`](../.github/scripts/check-pr-description.js)) to enforce minimum character limits and template adherence, reducing triage overhead.
- **`.github/scripts/`**: Automation scripts like [`check-issue-description.js`](../.github/scripts/check-issue-description.js) and [`check-pr-description.js`](../.github/scripts/check-pr-description.js) to enforce structural requirements on community submissions.

</details>
"""
arch = re.sub(r'### GitHub Workflows \(\[`.github/`\]\(\.\./\.github/\)\)\n- \*\*`ci\.yml`\*\*:.*?\n- \*\*`docker-publish\.yml`\*\*:.*?\n- \*\*Issue & PR Validations\*\*:.*?\n- \*\*\`\.github/scripts/`\*\*:.*?\n', github_replacement.strip() + '\n\n', arch, flags=re.DOTALL)

# 4. Add licenses
replacement_licenses = """### Components
- **[`config/searxng/settings.yml`](../config/searxng/settings.yml)**: A pre-configured settings file for the SearXNG search aggregator. Odysseus mounts this into the SearXNG container to enforce specific output formats (JSON/HTML) and inject a secret key securely without requiring user intervention.
- **[`licenses/`](../licenses/)**: The directory tracking open-source licenses for embedded components. Odysseus uses modified or integrated parts of tools like `DeepResearch` or `llmfit`, and this directory ensures proper MIT/Apache 2.0 attribution without bloating the root project directory, including:
  - [`licenses/DeepResearch-Apache-2.0.txt`](../licenses/DeepResearch-Apache-2.0.txt)
  - [`licenses/llmfit-MIT-LICENSE.txt`](../licenses/llmfit-MIT-LICENSE.txt)
  - [`licenses/opencode-MIT-LICENSE.txt`](../licenses/opencode-MIT-LICENSE.txt)"""

arch = arch.replace(
    '### Components\n- **[`config/searxng/settings.yml`](../config/searxng/settings.yml)**: A pre-configured settings file for the SearXNG search aggregator. Odysseus mounts this into the SearXNG container to enforce specific output formats (JSON/HTML) and inject a secret key securely without requiring user intervention.\n- **[`licenses/`](../licenses/)**: The directory tracking open-source licenses for embedded components. Odysseus uses modified or integrated parts of tools like `DeepResearch` or `llmfit`, and this directory ensures proper MIT/Apache 2.0 attribution without bloating the root project directory.',
    replacement_licenses
)

# Replace data/ links with actual texts because data/ directory is generated at runtime
arch = arch.replace('[`user_prefs.json`](../data/user_prefs.json)', '`data/user_prefs.json`')
arch = arch.replace('the [`data/`](../data/) directory', 'the `data/` directory')
arch = arch.replace('([`data/app.db`](../data/app.db))', '(`data/app.db`)')
arch = arch.replace('[`.app_key`](../data/.app_key)', '`.app_key`')
arch = arch.replace('[`data/vault.json`](../data/vault.json)', '`data/vault.json`')
arch = arch.replace('[`data/presets.json`](../data/presets.json)', '`data/presets.json`')
arch = arch.replace('the [`app.db`](../data/app.db)', 'the `data/app.db`')


# Quote Nodes in Mermaid Diagrams
def quote_nodes(match):
    prefix = match.group(1)
    content = match.group(2)
    # Only quote if there's a space or special char and it's not already quoted
    if not content.startswith('"') and not content.endswith('"'):
        if any(c in content for c in [' ', '/', '.', '-']):
            return f'{prefix}["{content}"]'
    return match.group(0)

def quote_cylinders(match):
    prefix = match.group(1)
    content = match.group(2)
    if not content.startswith('"') and not content.endswith('"'):
        if any(c in content for c in [' ', '/', '.', '-']):
            return f'{prefix}[("{content}")]'
    return match.group(0)

diagrams = re.findall(r'```mermaid(.*?)```', arch, flags=re.DOTALL)

for diagram in diagrams:
    new_diagram = diagram
    new_diagram = re.sub(r'(\w+)\[(.*?)\]', quote_nodes, new_diagram)
    new_diagram = re.sub(r'(\w+)\((.*?)\)', quote_cylinders, new_diagram)
    # Fix |gosu PUID:PGID| specifically since it's an edge label
    new_diagram = new_diagram.replace('|gosu PUID:PGID|', '|"gosu PUID:PGID"|')
    arch = arch.replace(diagram, new_diagram)


with open('docs/ARCHITECTURE.md', 'w') as f:
    f.write(arch)
