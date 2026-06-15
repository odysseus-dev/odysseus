import pytest
from src.tool_execution import _turn_readonly, _active_workspace

@pytest.fixture
def protected_context():
    """Garante isolamento de contexto de segurança entre testes."""
    ro_token = _turn_readonly.set(False)
    ws_token = _active_workspace.set(None)
    yield
    _turn_readonly.reset(ro_token)
    _active_workspace.reset(ws_token)
