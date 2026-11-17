from src.execution_capabilities import ExecutionCapabilities, verifier_prompt


def test_probe_contract_deduplicates_and_rejects_parent_paths():
    capabilities = ExecutionCapabilities.from_probe({
        "cwd": "/workspace",
        "commands": ["python3", "git", "python3"],
        "verifiers": [
            {
                "command": "python3 -m pytest -q",
                "kind": "pytest",
                "source_path": "pyproject.toml",
            },
            {
                "command": "python3 -m pytest -q",
                "kind": "pytest",
                "source_path": "tests",
            },
            {
                "command": "sh ../verifier/test.sh",
                "kind": "script",
                "source_path": "../verifier/test.sh",
            },
        ],
    })

    assert capabilities.commands == ("git", "python3")
    assert capabilities.verifier_commands == ("python3 -m pytest -q",)
    assert "../verifier" not in str(capabilities.to_dict())


def test_verifier_prompt_contains_only_discovered_commands():
    capabilities = ExecutionCapabilities.from_probe({
        "cwd": "/workspace",
        "commands": ["npm"],
        "verifiers": [{
            "command": "npm test",
            "kind": "npm",
            "source_path": "package.json",
        }],
    })

    prompt = verifier_prompt(capabilities)
    assert "`npm test`" in prompt
    assert "address any failure" in prompt
