"""Legacy browser compatibility cannot bypass runtime resource and approval gates."""
from unittest.mock import AsyncMock

import pytest

from src.agent_runtime.authority import OperationGrant, RequestAuthority
from src.tool_capabilities import ToolRunSecurityContext
from src.tool_types import ToolBlock
from src import tool_execution


def test_empty_legacy_browser_flag_does_not_enable_compatibility(monkeypatch):
    monkeypatch.setenv('ODYSSEUS_PRIVATE_BROWSER_LEGACY', '')
    assert not tool_execution._legacy_private_browser_enabled()


@pytest.mark.asyncio
@pytest.mark.parametrize('untrusted', [False, True])
async def test_legacy_browser_never_bypasses_untrusted_context_gate(monkeypatch, untrusted):
    monkeypatch.setenv('ODYSSEUS_PRIVATE_BROWSER_LEGACY', '1')
    legacy = AsyncMock(return_value=('executed', {'exit_code': 0}))
    monkeypatch.setattr(tool_execution, '_legacy_private_browser', legacy)
    authority = RequestAuthority('request', 'alice', 'thread', '', (OperationGrant('private_browser'),))
    _, result = await tool_execution.execute_tool_block(
        ToolBlock('private_browser', '{"action":"click","selector":"#submit"}'),
        owner='alice', session_id='thread', request_authority=authority,
        security_context=ToolRunSecurityContext(external_untrusted_context_seen=untrusted))
    assert result.get('exit_code') != 0
    legacy.assert_not_awaited()
