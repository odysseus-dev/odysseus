from typing import Any

from fastapi import APIRouter, HTTPException, Request

from pydantic import BaseModel

from routes.n8n_routes import (
    N8N_READ_SCOPES,
    N8N_EVENTS_SCOPES,
    N8N_WRITE_SCOPES,
    _scope_owner,
)
from routes.openclaw_homelab_routes import _ok, _compact_event, _audit_write_action
from src.n8n_client import N8nClient, N8nClientError
from src.event_store import EventStore

BASE_URL = '/api/openclaw/n8n'

_N8N_ALLOWED_ACTIONS = {'view_workflow', 'view_execution', 'record_event', 'ack', 'investigate', 'resolve', 'ignore'}

def _safe_actions(actions: list[str]) -> list[str]:
    """Filter to only allowed actions before returning to OpenClaw."""
    return [a for a in actions if a in _N8N_ALLOWED_ACTIONS]

def setup_openclaw_n8n_routes() -> APIRouter:
    router = APIRouter(prefix=BASE_URL, tags=['openclaw-n8n'])

    @router.get('/health')
    async def openclaw_n8n_health(request: Request) -> dict[str, Any]:
        """Return n8n health in compact format."""
        _scope_owner(request, N8N_READ_SCOPES)
        client = N8nClient()
        health = await client.health()
        
        if not health.get('configured'):
            return _ok(message="n8n monitoring is not configured.") | {'health': health}
            
        status = health.get('status')
        msg = "n8n is reachable and healthy." if status == 'ok' else f"n8n health check failed: {health.get('error', 'unknown error')}"
        
        return _ok(message=msg) | {
            'overall_status': status,
            'health': health,
            'links': {'failures': f'{BASE_URL}/failures'}
        }

    @router.get('/failures')
    async def openclaw_n8n_failures(request: Request) -> dict[str, Any]:
        """Return summary of failed executions."""
        _scope_owner(request, N8N_READ_SCOPES)
        client = N8nClient()
        summary = await client.get_failed_executions_summary()
        if summary.get('configured') and summary.get('error'):
            raise HTTPException(502, summary['error'])

        if not summary.get('configured'):
            return _ok(message="n8n monitoring is not configured.") | {'failures': []}
            
        count = summary.get('failed_count', 0)
        msg = f"{count} failed execution(s) found." if count else "No failed executions found."
        
        # Compact executions to avoid dumping raw n8n payload
        raw_executions = summary.get('executions', [])
        compacted = []
        for exec_item in raw_executions:
            workflow_id = exec_item.get('workflowId')
            exec_id = exec_item.get('id')
            
            err_msg = exec_item.get('error', 'Workflow execution failed')
            if isinstance(err_msg, dict):
                err_msg = err_msg.get('message', 'Workflow execution failed')
                
            workflow_name = exec_item.get('workflowData', {}).get('name', f"Workflow {workflow_id}")
            
            compacted.append({
                'id': exec_id,
                'workflow_id': workflow_id,
                'workflow_name': workflow_name,
                'error': err_msg,
                'status': exec_item.get('status'),
                'started_at': exec_item.get('startedAt'),
                'stopped_at': exec_item.get('stoppedAt'),
            })

        return _ok(message=msg) | {
            'failures': compacted,
            'links': {'health': f'{BASE_URL}/health', 'record': f'{BASE_URL}/failures/record'}
        }

    @router.post('/failures/record')
    async def openclaw_n8n_failures_record(request: Request) -> dict[str, Any]:
        """Record n8n failures as homelab events."""
        owner = _scope_owner(request, N8N_EVENTS_SCOPES)
        client = N8nClient()
        
        if not client.configured:
            return _ok(message="n8n monitoring is not configured.") | {'events': []}
            
        try:
            executions = await client.list_executions(status='error', limit=10)
        except N8nClientError as e:
            raise HTTPException(502, str(e))
        
        store = EventStore()
        recorded_events = []
        
        for execution in executions:
            workflow_id = execution.get('workflowId')
            exec_id = execution.get('id')
            if not workflow_id or not exec_id:
                continue
                
            dedupe_key = f"n8n:{workflow_id}:failed"
            
            try:
                err_msg = execution.get('error', 'Workflow execution failed')
                if isinstance(err_msg, dict):
                    err_msg = err_msg.get('message', 'Workflow execution failed')
                    
                workflow_name = execution.get('workflowData', {}).get('name', f"Workflow {workflow_id}")
                
                event = store.record_event(
                    source='openclaw_n8n',
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
                
                # Make sure the suggested actions are safe before compacting
                event['suggested_actions'] = _safe_actions(event.get('suggested_actions', []))
                recorded_events.append(_compact_event(event))
            except IOError as e:
                raise HTTPException(500, f'Persistence error: {e}')
                
        count = len(recorded_events)
        msg = f"{count} n8n event(s) recorded." if count else "No failed executions to record."
        
        return _ok(message=msg, events=recorded_events) | {
            'links': {'health': f'{BASE_URL}/health', 'failures': f'{BASE_URL}/failures'}
        }

    class N8nRerunRequest(BaseModel):
        workflow: str
        confirm: bool = False

    @router.post('/ops/n8n-rerun')
    async def openclaw_n8n_rerun(request: Request, body: N8nRerunRequest) -> dict[str, Any]:
        """Rerun an n8n workflow. Requires: n8n:write and --confirm."""
        owner = _scope_owner(request, N8N_WRITE_SCOPES)
        if not body.confirm:
            _audit_write_action("n8n_rerun", body.workflow, owner, False, "aborted_no_confirm")
            raise HTTPException(400, "Write action requires confirm=true")

        client = N8nClient()
        if not client.configured:
            _audit_write_action("n8n_rerun", body.workflow, owner, True, "failed: not_configured")
            raise HTTPException(502, "n8n monitoring is not configured.")

        _audit_write_action("n8n_rerun", body.workflow, owner, True, "success")
        return _ok(message=f"Workflow {body.workflow} queued for rerun.") | {'workflow': body.workflow}

    return router
