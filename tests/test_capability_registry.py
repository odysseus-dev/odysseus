import pytest

from src.capabilities.models import ToolContext, ToolDefinition
from src.capabilities.providers.builtin import BuiltinToolProvider
from src.capabilities.providers.mcp import McpToolProvider, get_mcp_function_schemas
from src.capabilities.registry import CapabilityRegistry
from src.tool_schemas import FUNCTION_TOOL_SCHEMAS, get_function_schemas


pytestmark = pytest.mark.area_unit


def _schema(name: str) -> dict:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": f"{name} schema",
            "parameters": {"type": "object", "properties": {}},
        },
    }


class _StaticProvider:
    def __init__(self, provider_id: str, definitions):
        self.provider_id = provider_id
        self._definitions = list(definitions)

    def list_tools(self, context=None):
        return list(self._definitions)


class _FakeMcpManager:
    def __init__(self):
        self.received_disabled_map = None
        self._schemas = {
            "fetch_doc": _schema("mcp__srv1__fetch_doc"),
            "admin_write": _schema("mcp__srv1__admin_write"),
        }

    def get_all_openai_schemas(self, disabled_map=None):
        self.received_disabled_map = disabled_map or {}
        disabled = self.received_disabled_map.get("srv1", set())
        return [
            schema
            for name, schema in self._schemas.items()
            if name not in disabled
        ]


def _names(schemas):
    return [schema["function"]["name"] for schema in schemas]


def test_builtin_schema_wrapper_preserves_existing_order_and_objects():
    schemas = get_function_schemas()

    assert _names(schemas) == _names(FUNCTION_TOOL_SCHEMAS)
    assert [id(schema) for schema in schemas] == [
        id(schema) for schema in FUNCTION_TOOL_SCHEMAS
    ]
    assert all(schema["type"] == "function" for schema in schemas)
    assert all(schema["function"]["name"] for schema in schemas)


def test_registry_rejects_duplicate_tool_names():
    registry = CapabilityRegistry()
    registry.register_provider(
        _StaticProvider(
            "first",
            [ToolDefinition("bash", _schema("bash"), "first")],
        )
    )
    registry.register_provider(
        _StaticProvider(
            "second",
            [ToolDefinition("bash", _schema("bash"), "second")],
        )
    )

    with pytest.raises(ValueError, match="Tool name conflict: bash"):
        registry.list_tools()


def test_registry_unknown_provider_fails_closed():
    registry = CapabilityRegistry()

    with pytest.raises(KeyError, match="Unknown capability provider"):
        registry.require_provider("missing")


def test_builtin_provider_rejects_schema_without_name():
    provider = BuiltinToolProvider([{"type": "function", "function": {}}])

    with pytest.raises(ValueError, match="missing function.name"):
        list(provider.list_tools())


def test_mcp_provider_preserves_namespacing_disabled_map_and_admin_metadata():
    mcp = _FakeMcpManager()
    context = ToolContext(mcp_disabled_map={"srv1": frozenset({"admin_write"})})
    definitions = list(McpToolProvider(mcp).list_tools(context))

    assert [definition.name for definition in definitions] == ["mcp__srv1__fetch_doc"]
    assert all(definition.provider_id == "mcp" for definition in definitions)
    assert all(definition.admin_only for definition in definitions)
    assert mcp.received_disabled_map == {"srv1": {"admin_write"}}


def test_mcp_function_schema_wrapper_returns_registry_schemas():
    mcp = _FakeMcpManager()
    schemas = get_mcp_function_schemas(
        mcp,
        disabled_map={"srv1": {"admin_write"}},
    )

    assert _names(schemas) == ["mcp__srv1__fetch_doc"]
