from routes.session_routes import _context_info_tool_inventory


def test_context_info_reports_delegated_admin_as_restricted(monkeypatch):
    import asyncio
    from types import SimpleNamespace
    import routes.session_routes as routes
    monkeypatch.setattr(routes, '_verify_session_owner', lambda *args: None)
    monkeypatch.setattr('src.tool_security.blocked_tools_for_owner', lambda owner: set())
    from fastapi import APIRouter
    monkeypatch.setattr(routes, 'router', APIRouter(prefix='/api'))
    session = SimpleNamespace(endpoint_url='', model='', cwd='')
    router = routes.setup_session_routes(SimpleNamespace(get_session=lambda sid: session), {})
    endpoint = next(route.endpoint for route in router.routes if route.path == '/api/session/{session_id}/context_info')
    async def invoke(delegated):
        request = SimpleNamespace(state=SimpleNamespace(api_token=delegated, api_token_owner='admin',
                                                       api_token_scopes=['chat'], current_user='admin'))
        return await endpoint(request, 'session-1', cwd=None)
    interactive = asyncio.run(invoke(False))
    delegated = asyncio.run(invoke(True))
    assert interactive['tool_policy']['computer_tools'] == 'full'
    assert delegated['tool_policy']['computer_tools'] == 'restricted'
    assert delegated['tool_policy']['reason'] == 'API-token caller'


def test_context_info_tool_inventory_is_compact_backend_metadata():
    tools = _context_info_tool_inventory()
    names = {tool["name"] for tool in tools}

    assert "manage_skills" in names
    assert "ui_control" in names

    ui_control = next(tool for tool in tools if tool["name"] == "ui_control")
    assert ui_control["source"] == "backend"
    assert "description" in ui_control
    assert "schema" not in ui_control
    assert "parameters" not in ui_control


def test_context_info_tool_inventory_applies_limit():
    tools = _context_info_tool_inventory(limit=2)

    assert len(tools) == 2
