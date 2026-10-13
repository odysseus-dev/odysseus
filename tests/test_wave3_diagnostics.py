"""Unexpected programming defects must not look like successful policy denial."""
import pytest
from src import tool_execution
from src.agent_runtime.authority import ExactOperation, OperationGrant, RequestAuthority
from src.agent_runtime.resources import ResourceIdentityError
from src.tool_capabilities import ToolRunSecurityContext
from src.tool_types import ToolBlock


@pytest.mark.parametrize('seam,tool,content', [
    ('bind_backend_for_operation', 'bash', 'printf probe'),
    ('resolve_process_operation', 'bash', 'printf probe'),
    ('admit_owned_operation', 'edit_document', '{"document_id":"doc","content":"changed"}'),
])
@pytest.mark.parametrize('error_type', [AttributeError, ResourceIdentityError, ValueError, TypeError])
async def test_binding_errors_keep_diagnostic_identity(tmp_path, monkeypatch, seam, tool, content, error_type):
    authority = RequestAuthority('request', 'alice', 'thread', str(tmp_path), (OperationGrant(tool),))
    monkeypatch.setattr(tool_execution, '_owner_is_admin', lambda owner: True)
    def broken(*args, **kwargs):
        raise error_type('injected defect')
    monkeypatch.setattr(tool_execution, seam, broken)
    args = dict(owner='alice', session_id='thread', workspace=str(tmp_path), request_authority=authority,
                security_context=ToolRunSecurityContext())
    if error_type is AttributeError:
        with pytest.raises(AttributeError, match='injected defect'):
            await tool_execution.execute_tool_block(ToolBlock(tool, content), **args)
    else:
        _, result = await tool_execution.execute_tool_block(ToolBlock(tool, content), **args)
        assert result['failure_kind'] == 'resource_identity_denied' and result['blocked']


async def test_argument_normalization_defect_propagates(tmp_path, monkeypatch):
    authority = RequestAuthority('request', 'alice', 'thread', str(tmp_path), (OperationGrant('bash'),))
    def broken(*args, **kwargs): raise AttributeError('internal-only diagnostic')
    monkeypatch.setattr(ExactOperation, 'normalize', broken)
    with pytest.raises(AttributeError):
        await tool_execution.execute_tool_block(ToolBlock('bash', 'printf probe'), owner='alice',
            session_id='thread', workspace=str(tmp_path), request_authority=authority,
            security_context=ToolRunSecurityContext())


@pytest.mark.parametrize('content', ['{invalid', None])
async def test_expected_bad_input_still_has_authority_denial(tmp_path, content):
    authority = RequestAuthority('request', 'alice', 'thread', str(tmp_path), (OperationGrant('api_call'),))
    _, result = await tool_execution.execute_tool_block(ToolBlock('api_call', content), owner='alice',
        session_id='thread', workspace=str(tmp_path), request_authority=authority,
        security_context=ToolRunSecurityContext())
    assert result['failure_kind'] == 'request_authority_denied'
