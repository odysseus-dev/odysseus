"""OXS — Odysseus Extension System.

Public facade for the extension host runtime. PR 1 ships the foundation
(manifest schema, security checks, audit log); PR 2 adds the host/loader/
registry/lifecycle; PR 3 adds storage/webhooks/MCP/events; PR 4 wires the
admin UI and inbound webhook exemptions.

Import surface (stable):

    from core.extensions import (
        # PR 1
        OXS_VERSION, validate_manifest, OXSManifestError,
        verify_file_hashes, verify_zip_safe, ast_parse_safe, OXSSecurityError,
        audit_log, audit_query, AuditEvent,
        # PR 2 (placeholder; raises until PR 2 lands)
        OXSCapabilityError,
    )
"""

from .manifest_schema import (
    validate_manifest,
    OXSManifestError,
    MANIFEST_FILENAME,
)
from .security import (
    verify_file_hashes,
    verify_zip_safe,
    ast_parse_safe,
    OXSSecurityError,
    sha256_of_bytes,
    sha256_of_file,
    compute_extension_hashes,
    find_register_callable,
)
from .audit import (
    audit_log,
    audit_query,
    audit_tail,
    AuditEvent,
    AuditSink,
)
from .host import (
    OXSHost,
    OXSCapabilityError,
    require_capability,
    require_privileged,
    SettingsNamespace,
    ToolRegistration,
    MCPToolRegistration,
    RouteRegistration,
    StaticRegistration,
    EventSubscription,
    WebhookRegistration,
    SidebarRegistration,
    PanelRegistration,
    SlashRegistration,
    SettingsTabRegistration,
    TaskActionRegistration,
)
from .lifecycle import (
    LifecycleHooks,
    on_install,
    on_enable,
    on_disable,
    on_uninstall,
)
from .loader import (
    DiscoveredExtension,
    discover_all,
    verify_discovered,
)
from .registry import (
    RunningExtension,
    ExtensionRegistry,
)


OXS_VERSION = "1.0.0"


__all__ = [
    # version
    "OXS_VERSION",
    # manifest
    "validate_manifest",
    "OXSManifestError",
    "MANIFEST_FILENAME",
    # security
    "verify_file_hashes",
    "verify_zip_safe",
    "ast_parse_safe",
    "OXSSecurityError",
    "sha256_of_bytes",
    "sha256_of_file",
    "compute_extension_hashes",
    "find_register_callable",
    # audit
    "audit_log",
    "audit_query",
    "audit_tail",
    "AuditEvent",
    "AuditSink",
    # capability (declared here so PR 2 callers can `from core.extensions
    # import OXSCapabilityError` without depending on host.py)
    "OXSCapabilityError",
    # PR 2: host + lifecycle + recorders
    "OXSHost",
    "require_capability",
    "require_privileged",
    "SettingsNamespace",
    "ToolRegistration",
    "MCPToolRegistration",
    "RouteRegistration",
    "StaticRegistration",
    "EventSubscription",
    "WebhookRegistration",
    "SidebarRegistration",
    "PanelRegistration",
    "SlashRegistration",
    "SettingsTabRegistration",
    "TaskActionRegistration",
    "LifecycleHooks",
    "on_install",
    "on_enable",
    "on_disable",
    "on_uninstall",
    # PR 2: loader
    "DiscoveredExtension",
    "discover_all",
    "verify_discovered",
    # PR 2: registry
    "RunningExtension",
    "ExtensionRegistry",
]