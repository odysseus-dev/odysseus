"""Collection must not replace the agent-tools package with a test double."""

import src.agent_tools
from src.agent_tools.web_tools import PrivateBrowserTool


def test_agent_tools_remains_a_real_package_after_test_collection():
    assert src.agent_tools.__path__
    assert PrivateBrowserTool.__module__ == "src.agent_tools.web_tools"
