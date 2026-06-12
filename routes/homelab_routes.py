"""Homelab operations routes (Phase 1 & 2.1: Read-only + concurrent health checks)."""

import asyncio
import json
import logging
import os
import subprocess
from typing import Any, Dict, List, Optional

import httpx
from fastapi import APIRouter, HTTPException, Request

from src.auth_helpers import require_user

logger = logging.getLogger(__name__)

HOMELAB_READ_SCOPES = {'homelab:read'}


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
    return require_user(request)


def _load_services() -> List[Dict[str, Any]]:
    config_path = os.path.join('config', 'homelab_services.json')
    if not os.path.exists(config_path):
        return []
    try:
        with open(config_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
            return data.get('services', [])
    except Exception as e:
        logger.error(f'Failed to load homelab services config: {e}')
        return []


def _get_concurrency() -> int:
    """Read HOMELAB_HEALTH_CONCURRENCY from env; default 5, clamped 1–50."""
    raw = os.getenv('HOMELAB_HEALTH_CONCURRENCY', '').strip()
    try:
        value = int(raw) if raw else 5
    except ValueError:
        logger.warning('Invalid HOMELAB_HEALTH_CONCURRENCY %r — defaulting to 5', raw)
        value = 5
    return max(1, min(value, 50))


def _inspect_container_status(container: str) -> tuple[str, bool]:
    """Synchronous Docker inspect helper — safe to run in a thread.

    Returns (container_status_string, is_running).
    shell=False is enforced; container name comes from the registry config,
    not from any user-supplied input.
    """
    try:
        result = subprocess.run(
            ['docker', 'inspect', '-f', '{{.State.Status}}', container],
            capture_output=True,
            text=True,
            check=False,
            shell=False,
        )
        if result.returncode == 0:
            cstatus = result.stdout.strip()
            return cstatus, (cstatus == 'running')
        return 'not_found', False
    except Exception:
        return 'check_failed', False


async def _check_service_health(
    service: dict,
    *,
    client: Optional[httpx.AsyncClient] = None,
) -> dict:
    """Check health of a single service.

    Args:
        service: Service config dict from the registry.
        client: Optional shared AsyncClient. If None a temporary client is
                created for just this call (backwards-compat / standalone use).
    """
    status: Dict[str, Any] = {'name': service.get('name'), 'status': 'unknown'}

    # --- Docker container check (offloaded to thread pool) ---
    container = service.get('container')
    if container:
        cstatus, is_running = await asyncio.to_thread(_inspect_container_status, container)
        status['container_status'] = cstatus
        status['status'] = 'ok' if is_running else 'error'

    # --- HTTP health check ---
    health_url = service.get('health_url') or service.get('url')
    if health_url:
        try:
            if client is not None:
                resp = await client.get(health_url)
            else:
                async with httpx.AsyncClient(timeout=5) as _client:
                    resp = await _client.get(health_url)
            status['http_status'] = resp.status_code
            if resp.status_code < 400:
                # Degrade to "degraded" only if container already errored.
                status['status'] = 'ok' if status.get('status') != 'error' else 'degraded'
            else:
                status['status'] = 'error'
        except Exception:
            status['http_status'] = 'unreachable'
            status['status'] = 'error'

    # Service with no container and no URL is considered healthy.
    if status['status'] == 'unknown' and not container and not health_url:
        status['status'] = 'ok'

    return status


from src.event_store import EventStore  # noqa: E402

EVENTS_WRITE_SCOPES = {'events:write'}


def _has_scope(request: Request, allowed: set[str]) -> bool:
    if not getattr(request.state, 'api_token', False):
        return True
    scopes = set(getattr(request.state, 'api_token_scopes', []) or [])
    return bool(scopes.intersection(allowed))


async def execute_health_checks(
    services: list[dict],
    record_events: bool = False,
    owner: str | None = None,
    source_name: str = 'homelab_health',
) -> tuple[list[dict], list[dict], str]:
    """Execute health checks for all services concurrently.
    
    If record_events is True, serializes writes to the EventStore.
    Returns (health_results, recorded_events, overall_status).
    """
    concurrency = _get_concurrency()
    sem = asyncio.Semaphore(concurrency)

    async def _bounded_check(srv: dict, shared_client: httpx.AsyncClient) -> dict:
        async with sem:
            return await _check_service_health(srv, client=shared_client)

    async with httpx.AsyncClient(timeout=5) as shared_client:
        health_results = await asyncio.gather(
            *[_bounded_check(srv, shared_client) for srv in services]
        )

    recorded_events = []
    if record_events:
        event_store = EventStore()
        for srv, res in zip(services, health_results):
            if res.get('status') in ('error', 'degraded'):
                dedupe_key = f"homelab:{srv['name']}:health"
                severity = 'critical' if res.get('status') == 'error' else 'warning'
                title = f"{srv.get('display_name', srv['name'])} is {res['status']}"
                summary = (
                    f"Container: {res.get('container_status', 'N/A')}, "
                    f"HTTP: {res.get('http_status', 'N/A')}"
                )
                try:
                    event = event_store.record_event(
                        source=source_name,
                        service=srv['name'],
                        severity=severity,
                        title=title,
                        summary=summary,
                        dedupe_key=dedupe_key,
                        owner=owner,
                        metadata=res,
                    )
                    recorded_events.append(event)
                except IOError as e:
                    raise HTTPException(500, f'Failed to record event: {e}')
                except Exception as e:
                    raise HTTPException(500, f'Failed to record event: {e}')

    overall_status = 'ok'
    if any(r.get('status') == 'error' for r in health_results):
        overall_status = 'error'
    elif any(r.get('status') == 'degraded' for r in health_results):
        overall_status = 'degraded'

    return list(health_results), recorded_events, overall_status


def setup_homelab_routes() -> APIRouter:
    router = APIRouter(prefix='/api/homelab', tags=['homelab'])

    @router.get('/services')
    async def list_services(request: Request):
        _scope_owner(request, HOMELAB_READ_SCOPES)
        services = _load_services()
        return {'status': 'ok', 'services': services}

    @router.get('/services/{name}')
    async def get_service(request: Request, name: str):
        _scope_owner(request, HOMELAB_READ_SCOPES)
        services = _load_services()
        for srv in services:
            if srv.get('name') == name:
                return {'status': 'ok', 'service': srv}
        raise HTTPException(404, 'Service not found')

    @router.get('/health')
    async def homelab_health(request: Request, record_events: bool = False):
        owner = _scope_owner(request, HOMELAB_READ_SCOPES)

        if record_events and not _has_scope(request, EVENTS_WRITE_SCOPES):
            raise HTTPException(403, 'API token missing required scope: events:write')

        services = _load_services()
        health_results, _, overall_status = await execute_health_checks(
            services, record_events=record_events, owner=owner, source_name='homelab_health'
        )

        return {
            'status': overall_status,
            'services': health_results,
        }

    return router
