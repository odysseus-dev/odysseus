from tests.helpers.cli_loader import load_script


def test_is_runnable_subcommand_requires_executable_file(tmp_path):
    cli = load_script("odysseus")
    sub = tmp_path / "odysseus-demo"
    sub.write_text("#!/bin/sh\n")
    sub.chmod(0o644)

    assert cli._is_runnable_subcommand(sub) is False

    sub.chmod(0o755)
    assert cli._is_runnable_subcommand(sub) is True
