"""Compatibility facade for the chunked :mod:`routes.chat` package.

New code should import ``routes.chat`` directly.  Attribute reads are forwarded
to the active package (and then its utility module) so integrations using the
legacy module path keep working without loading a second route implementation
or creating a second ``_active_streams`` registry.
"""

from routes import chat as _chat
from routes.chat import _utils

setup_chat_routes = _chat.setup_chat_routes

__all__ = ["setup_chat_routes"]


def __getattr__(name):
    if hasattr(_chat, name):
        return getattr(_chat, name)
    return getattr(_utils, name)


def __dir__():
    return sorted(set(globals()) | set(dir(_chat)) | set(dir(_utils)))
