"""Odysseus Chat Gateway.

Lets a user chat with the Odysseus agent itself (full tool/RAG/memory agent)
from messaging platforms. Thin per-platform adapters (transport) + one shared
GatewayRunner (agent), launched as background tasks at app startup.

Entrypoints used by app.py:
    from src.chat_gateway import start_chat_gateway, stop_chat_gateway
"""

from .service import start_chat_gateway, stop_chat_gateway

__all__ = ["start_chat_gateway", "stop_chat_gateway"]
