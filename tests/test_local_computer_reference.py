"""`on <word>` / `from <word>` must only mean a named machine when the word can be one."""
import pytest

from src.agent_loop import _looks_like_local_computer_request


@pytest.mark.parametrize("text", [
    "One line on anything tomorrow that needs prep today.",
    "Summarize the article from yesterday",
    "What do I need to do on friday",
    "Recall open items I'm waiting on from everyone",
    "Skip anything from nobody",
])
def test_ordinary_prose_is_not_a_machine_reference(text):
    assert not _looks_like_local_computer_request(text)


@pytest.mark.parametrize("text", [
    "list files on gpu-box",
    "stats from node1",
    "check the model on my laptop",
    "download it on nodes",
    "run it on workstation",
    "ssh to workstation",
    "show anything-goes on node1",
])
def test_named_machines_still_match(text):
    assert _looks_like_local_computer_request(text)
