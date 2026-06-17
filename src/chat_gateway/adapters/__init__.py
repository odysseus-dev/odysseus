"""Platform adapter registry.

Add a new platform by writing one adapter module and registering its class
here. The runner and the rest of the gateway never change.
"""

from __future__ import annotations

from typing import Dict, Type

from ..base import PlatformAdapter
from .irc import IrcAdapter
from .matrix import MatrixAdapter
from .mattermost import MattermostAdapter
from .simplex import SimplexAdapter

# platform key -> adapter class.
# Self-hosted / local-first adapters live here. To add a platform: copy
# _skeleton.py, implement it, add one line below. See ADDING_A_PLATFORM.md.
ADAPTERS: Dict[str, Type[PlatformAdapter]] = {
    MattermostAdapter.platform: MattermostAdapter,   # verified end-to-end
    MatrixAdapter.platform: MatrixAdapter,           # verified end-to-end
    IrcAdapter.platform: IrcAdapter,                 # verified end-to-end
    SimplexAdapter.platform: SimplexAdapter,         # written; needs a simplex-chat CLI to verify
    # third-party/cloud (Telegram/Discord/Slack/...) → copy _skeleton.py
}


def get_adapter_class(platform: str) -> Type[PlatformAdapter] | None:
    return ADAPTERS.get(platform)
