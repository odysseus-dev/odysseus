"""hello-odysseus plugin entry point.

This is the reference extension shipped with OXS. It exercises the minimum
viable surface so contributors can use it as a starting point.

Capabilities used (all "safe", all auto-granted):
  - ``settings_read``     — read extension settings under ``oxs.extensions.<id>``
  - ``static``            — serve ``ui/`` at ``/ext-static/hello-odysseus/``
  - ``events_fire``       — fire ``hello.greet`` when an admin enables us
  - ``events_sub``        — log every ``oxs.rescan`` event

Lifecycle:
  - ``on_install``:  write default settings
  - ``on_enable``:   register static dir, subscribe events
  - ``on_disable``:  nothing (host tears registrations down automatically)
  - ``on_uninstall``: nothing (host drops our tables on uninstall)
"""

from __future__ import annotations

import logging

# The ``host`` argument is supplied by OXS at enable time. PR 2 wires this
# up; until then, ``register`` is a placeholder that proves the AST shape.


def register(host):
    """OXS entry point. Must be a top-level ``def register(host):``.

    The single positional argument is the ``OXSHost`` instance for this
    extension. All host methods are capability-gated; calling one we did
    not declare raises ``OXSCapabilityError`` and writes an audit entry.
    """

    logger = host.get_logger()

    @host.on_install
    def _install():
        logger.info("hello-odysseus installed")

    @host.on_enable
    def _enable():
        host.add_static(
            url_prefix="/ext-static/hello-odysseus",
            directory="ui",
        )
        host.subscribe_event("oxs.rescan", _on_rescan)
        host.fire_event(
            "hello.greet",
            {"message": "hello from hello-odysseus"},
            owner=None,
        )
        logger.info("hello-odysseus enabled")

    @host.on_disable
    def _disable():
        logger.info("hello-odysseus disabled")

    @host.on_uninstall
    def _uninstall():
        logger.info("hello-odysseus uninstalled")

    def _on_rescan(payload):
        logger.info("oxs.rescan observed: %s", payload)