"""`pwd` is POSIX-only, so importing it at module scope breaks Windows startup.

`web_tools` is pulled in by `app.py` -> `routes.chat_routes` -> `src.agent_loop`
-> `src.agent_tools`, so a `ModuleNotFoundError: pwd` there takes down the whole
app at import time rather than degrading. On POSIX the missing module is
simulated, so this runs on the hosts CI actually uses.
"""

import importlib
import sys

from tests.helpers.import_state import clear_module, preserve_import_state

MODULE = "src.agent_tools.web_tools"


class _PwdBlocker:
    """Meta-path finder that makes `import pwd` fail, as it does on Windows."""

    def find_spec(self, fullname, path=None, target=None):
        if fullname == "pwd":
            raise ImportError("No module named 'pwd'")
        return None


def test_web_tools_imports_on_a_host_without_pwd():
    with preserve_import_state("pwd", MODULE):
        blocker = _PwdBlocker()
        sys.meta_path.insert(0, blocker)
        try:
            clear_module("pwd")
            clear_module(MODULE)
            module = importlib.import_module(MODULE)
        finally:
            sys.meta_path.remove(blocker)

        assert module.PrivateBrowserTool.__module__ == MODULE
