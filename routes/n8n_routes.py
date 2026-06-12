from typing import Any

from fastapi import APIRouter, HTTPException, Request, Query

from src.n8n_client import N8nClient
from src.event_store import EventStore

N8N_READ_SCOPES = {'n8n:read'}
N8N_EVENTS_SCOPES = {'n8n:events'}

def _has_scope(request: Request, allowed: set[str]) -> bool:
    if not getattr(request.state, 'api_token', False):
        return True
    scopes = set(getattr(request.state, 'api_token_scopes', []) or [])
    return bool(scopes.intersection(allowed))

def _scope_owner(request: Request, allowed: set[str]) -> str:
    if getattr(request.state, 'api_token', False):
        scopes = set(getattr(request.state, 'api_token_scopes', []) or [])
        if not scopes.intersection(allowed):
            required = ' or '.join(sorted(allowed))
            raise HTTPException(403, f'API token missing required scope: {required}')
        owner = getattr(request.state, 'api_token_owner', None)
        if not owner:
            raise HTTPException(403, 'API token has no owner')
        return owner
    from src.auth_helpers import require_user
    return require_user(request)

def setup_n8n_routes() -> APIRouter:
    router = APIRouter(prefix='/api/n8n', tags=['n8n'])

    @router.get('/health')
    async def n8n_health(request: Request):
        _scope_owner(request, N8N_READ_SCOPES)
        client = N8nClient()
        return await client.health()

    @router.get('/workflows')
    async def list_workflows(request: Request):
        _scope_owner(request, N8N_READ_SCOPES)
        client = N8nClient()
        if not client.configured:
            return {'status': 'ok', 'workflows': [], 'message': 'n8n not configured'}
        workflows = await client.list_workflows()
        return {'status': 'ok', 'workflows': workflows}

    @router.get('/executions')
    async def list_executions(request: Request, status: str | None = Query('error'), limit: int = Query(10, ge=1, le=100)):
        _scope_owner(request, N8N_READ_SCOPES)
        client = N8nClient()
        if not client.configured:
            return {'status': 'ok', 'executions': [], 'message': 'n8n not configured'}
        executions = await client.list_executions(status=status, limit=limit)
        return {'status': 'ok', 'executions': executions}

    @router.get('/executions/summary')
    async def executions_summary(request: Request):
        _scope_owner(request, N8N_READ_SCOPES)
        client = N8nClient()
        return await client.get_failed_executions_summary()

    @router.post('/executions/record-events')
    async def record_events(request: Request):
        owner = _scope_owner(request, N8N_EVENTS_SCOPES)
        client = N8nClient()
        
        if not client.configured:
            return {'status': 'ok', 'message': 'n8n not configured', 'recorded': 0}
            
        executions = await client.list_executions(status='error', limit=10)
        
        store = EventStore()
        recorded_events = []
        
        for execution in executions:
            workflow_id = execution.get('workflowId')
            exec_id = execution.get('id')
            if not workflow_id or not exec_id:
                continue
                
            dedupe_key = f"n8n:{workflow_id}:failed"
            
            try:
                # Basic error message extraction from n8n response if available
                # n8n execution data sometimes has an 'error' field inside 'data' or similar, 
                # but we'll try top level first
                # (n8n API format can vary slightly by version)
                err_msg = execution.get('error', 'Workflow execution failed')
                if isinstance(err_msg, dict):
                    err_msg = err_msg.get('message', 'Workflow execution failed')
                    
                workflow_name = execution.get('workflowData', {}).get('name', f"Workflow {workflow_id}")
                
                event = store.record_event(
                    source='n8n',
                    service='n8n',
                    severity='warning',
                    title='n8n workflow failed',
                    summary=f"{workflow_name} (Execution ID: {exec_id}) - {err_msg}",
                    dedupe_key=dedupe_key,
                    owner=owner,
                    metadata={
                        'workflow_id': workflow_id,
                        'execution_id': exec_id,
                        'status': execution.get('status'),
                        'started_at': execution.get('startedAt'),
                        'stopped_at': execution.get('stoppedAt'),
                    },
                    suggested_actions=['view_workflow', 'view_execution', 'ack', 'investigate']
                )
                recorded_events.append(event)
            except IOError as e:
                raise HTTPException(500, f'Failed to record event: {e}')
                
        return {
            'status': 'ok',
            'recorded': len(recorded_events),
            'events': recorded_events
        }

    return router
