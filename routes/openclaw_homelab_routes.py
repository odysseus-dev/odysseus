"""OpenClaw homelab command-facing routes (Phase 3: read/incident-state only).

Provides a compact Slack-friendly JSON layer over the homelab health checks
and event lifecycle.  No restart actions, no shell execution.
"""

from __future__ import annotations

import logging
import json
import http.client
import os
import re
import socket
import subprocess
import urllib.parse
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException, Request

from routes.homelab_routes import (
    HOMELAB_READ_SCOPES,
    EVENTS_WRITE_SCOPES,
    _load_services,
    execute_health_checks,
    _has_scope,
    _scope_owner,
)
from src.event_store import EventStore

logger = logging.getLogger(__name__)

EVENTS_READ_SCOPES = {'events:read'}
EVENTS_ACK_SCOPES = {'events:ack'}
EVENTS_RESOLVE_SCOPES = {'events:resolve'}

# Actions that may appear in OpenClaw responses.
_ALLOWED_ACTIONS = {
    'ack', 'investigate', 'resolve', 'ignore', 'view_service',
    'view_workflow', 'view_execution', 'record_event'
}

BASE_URL = '/api/openclaw/homelab'
PING_TARGET = os.getenv('HEIMDAL_PING_TARGET', '100.110.136.4')
CADDY_CONTAINER = os.getenv('CADDY_CONTAINER', 'caddy')
DOCKER_SOCKET = os.getenv('HOMELAB_DOCKER_SOCKET', '/var/run/docker.sock')
TAILSCALE_SOCKET = os.getenv('HOMELAB_TAILSCALE_SOCKET', '/var/run/tailscale/tailscaled.sock')


def _safe_actions(actions: list[str]) -> list[str]:
    """Filter to only allowed actions before returning to OpenClaw."""
    return [a for a in actions if a in _ALLOWED_ACTIONS]

def _sanitize_dict(data: dict) -> dict:
    """Recursively redact sensitive keys from a dictionary."""
    redact_keys = {'token', 'secret', 'password', 'api_key', 'authorization', 'headers', 'auth'}
    clean = {}
    for k, v in data.items():
        # Normalize camelCase to snake_case before sensitive-key matching.
        k_norm = re.sub(r'([a-z0-9])([A-Z])', r'\1_\2', k).lower()
        if any(re.search(rf'(^|[-_]){re.escape(rk)}s?([-_]|$)', k_norm) for rk in redact_keys):
            clean[k] = '***REDACTED***'
        elif isinstance(v, dict):
            clean[k] = _sanitize_dict(v)
        elif isinstance(v, list):
            clean[k] = [_sanitize_dict(i) if isinstance(i, dict) else i for i in v]
        else:
            clean[k] = v
    return clean

def _sanitize_service(service: dict) -> dict:
    """Return a new dict with sensitive fields redacted."""
    return _sanitize_dict(service)


def _run_static_command(args: list[str], timeout: int = 8) -> dict[str, Any]:
    """Run a fixed argv command and return a redacted diagnostic envelope."""
    try:
        result = subprocess.run(
            args,
            capture_output=True,
            text=True,
            check=False,
            shell=False,
            timeout=timeout,
        )
    except FileNotFoundError:
        return {'status': 'degraded', 'error': 'command_not_available', 'command': args[0]}
    except subprocess.TimeoutExpired:
        return {'status': 'degraded', 'error': 'command_timeout', 'command': args[0]}
    except Exception as exc:
        return {'status': 'degraded', 'error': str(exc), 'command': args[0]}

    stdout = (result.stdout or '').strip()
    stderr = (result.stderr or '').strip()
    return {
        'status': 'ok' if result.returncode == 0 else 'degraded',
        'returncode': result.returncode,
        'stdout': stdout[:12000],
        'stderr': stderr[:4000],
    }


def _json_lines(text: str) -> list[dict[str, Any]]:
    rows = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            value = json.loads(line)
        except Exception:
            rows.append({'raw': line})
            continue
        if isinstance(value, dict):
            rows.append(_sanitize_dict(value))
    return rows


def _find_grafana_url() -> str | None:
    configured = (os.getenv('HOMELAB_GRAFANA_URL') or os.getenv('GRAFANA_URL') or '').strip()
    if configured:
        return configured
    for service in _load_services():
        if str(service.get('name') or '').lower() == 'grafana':
            return service.get('health_url') or service.get('url')
    return None


class _UnixSocketHTTPConnection(http.client.HTTPConnection):
    def __init__(self, socket_path: str, timeout: int = 8):
        super().__init__('localhost', timeout=timeout)
        self.socket_path = socket_path

    def connect(self) -> None:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(self.timeout)
        sock.connect(self.socket_path)
        self.sock = sock


def _docker_api_request(path: str, timeout: int = 8) -> dict[str, Any]:
    if not os.path.exists(DOCKER_SOCKET):
        return {'status': 'degraded', 'error': 'docker_socket_not_available'}
    conn = _UnixSocketHTTPConnection(DOCKER_SOCKET, timeout=timeout)
    try:
        conn.request('GET', path)
        resp = conn.getresponse()
        raw = resp.read()
        text = raw.decode('utf-8', errors='replace')
        return {
            'status': 'ok' if resp.status < 400 else 'degraded',
            'http_status': resp.status,
            'body': text,
        }
    except PermissionError:
        return {'status': 'degraded', 'error': 'docker_socket_permission_denied'}
    except Exception as exc:
        return {'status': 'degraded', 'error': str(exc)}
    finally:
        try:
            conn.close()
        except Exception:
            pass


def _docker_unhealthy_containers() -> dict[str, Any]:
    filters = urllib.parse.quote(json.dumps({'health': ['unhealthy']}))
    result = _docker_api_request(f'/containers/json?filters={filters}')
    containers = []
    if result.get('status') == 'ok':
        try:
            payload = json.loads(result.get('body') or '[]')
        except Exception:
            payload = []
        if isinstance(payload, list):
            for item in payload:
                if isinstance(item, dict):
                    containers.append(_sanitize_dict({
                        'ID': item.get('Id'),
                        'Names': ', '.join([str(name).lstrip('/') for name in item.get('Names', [])]),
                        'Image': item.get('Image'),
                        'Status': item.get('Status'),
                        'State': item.get('State'),
                    }))
    return {'containers': containers, 'check': result}


def _docker_container_logs(container: str, lines: int) -> dict[str, Any]:
    safe_container = urllib.parse.quote(container, safe='')
    result = _docker_api_request(
        f'/containers/{safe_container}/logs?stdout=1&stderr=1&tail={lines}',
        timeout=10,
    )
    logs = re.sub(r'[\x00-\x08\x0b-\x1f]', '', result.get('body') or '')
    check = {k: v for k, v in result.items() if k != 'body'}
    return {'logs': logs[-12000:], 'check': check}


def _compact_tailscale_status(payload: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(payload, dict):
        return None
    self_node = payload.get('Self') if isinstance(payload.get('Self'), dict) else {}
    peers = []
    for peer in (payload.get('Peer') or {}).values():
        if not isinstance(peer, dict):
            continue
        peers.append({
            'host_name': peer.get('HostName'),
            'dns_name': peer.get('DNSName'),
            'os': peer.get('OS'),
            'tailscale_ips': peer.get('TailscaleIPs') or [],
            'online': bool(peer.get('Online')),
            'active': bool(peer.get('Active')),
            'last_seen': peer.get('LastSeen'),
        })
    peers.sort(key=lambda item: (not item.get('online'), str(item.get('host_name') or item.get('dns_name') or '')))
    return {
        'version': payload.get('Version'),
        'backend_state': payload.get('BackendState'),
        'tailscale_ips': payload.get('TailscaleIPs') or [],
        'self': {
            'host_name': self_node.get('HostName'),
            'dns_name': self_node.get('DNSName'),
            'os': self_node.get('OS'),
            'tailscale_ips': self_node.get('TailscaleIPs') or [],
            'online': bool(self_node.get('Online')),
        },
        'health': payload.get('Health') or [],
        'peer_count': len(peers),
        'peers': peers,
    }


def _tailscale_status() -> dict[str, Any]:
    if os.path.exists(TAILSCALE_SOCKET):
        conn = _UnixSocketHTTPConnection(TAILSCALE_SOCKET, timeout=8)
        try:
            conn.request('GET', '/localapi/v0/status', headers={'Host': 'local-tailscaled.sock'})
            resp = conn.getresponse()
            text = resp.read().decode('utf-8', errors='replace')
            payload = json.loads(text) if resp.status < 400 else None
            compact = _compact_tailscale_status(payload)
            return {
                'status': 'ok' if resp.status < 400 else 'degraded',
                'http_status': resp.status,
                'tailscale': compact,
                'check': {'status': 'ok' if resp.status < 400 else 'degraded', 'http_status': resp.status},
            }
        except Exception as exc:
            return {'status': 'degraded', 'error': str(exc), 'tailscale': None, 'check': {'status': 'degraded', 'error': str(exc)}}
        finally:
            try:
                conn.close()
            except Exception:
                pass

    result = _run_static_command(['tailscale', 'status', '--json'])
    status_json = None
    if result.get('status') == 'ok' and result.get('stdout'):
        try:
            status_json = _compact_tailscale_status(json.loads(result['stdout']))
        except Exception:
            status_json = None
    return {'status': result.get('status'), 'tailscale': status_json, 'check': result}


def _event_links(event_id: str) -> dict[str, str]:
    """Return canonical self/collection links for an event."""
    return {
        'self': f'{BASE_URL}/events/{event_id}',
        'events': f'{BASE_URL}/events',
        'ack': f'{BASE_URL}/events/{event_id}/ack',
        'investigate': f'{BASE_URL}/events/{event_id}/investigate',
        'resolve': f'{BASE_URL}/events/{event_id}/resolve',
        'ignore': f'{BASE_URL}/events/{event_id}/ignore',
    }


def _compact_event(event: dict[str, Any]) -> dict[str, Any]:
    """Return a Slack-friendly subset of an event dict."""
    return {
        'id': event['id'],
        'service': event.get('service'),
        'severity': event.get('severity'),
        'status': event.get('status'),
        'title': event.get('title'),
        'summary': event.get('summary'),
        'count': event.get('count', 1),
        'first_seen': event.get('first_seen'),
        'last_seen': event.get('last_seen'),
        'owner': event.get('owner'),
        'suggested_actions': _safe_actions(event.get('suggested_actions', [])),
        'links': _event_links(event['id']),
    }


def _ok(*, message: str, event: dict | None = None, events: list | None = None,
        requires_approval: bool = False) -> dict[str, Any]:
    """Build a successful OpenClaw response envelope."""
    payload: dict[str, Any] = {
        'status': 'ok',
        'message': message,
        'requires_approval': requires_approval,
    }
    if event is not None:
        payload['event'] = event
    if events is not None:
        payload['events'] = events
    return payload


def _ops_result(kind: str, message: str, detail: dict[str, Any]) -> dict[str, Any]:
    return _ok(message=message) | {
        'ops': {
            'kind': kind,
            **detail,
        },
        'links': {'health': f'{BASE_URL}/health'},
    }


def setup_openclaw_homelab_routes() -> APIRouter:
    """Create and return the /api/openclaw/homelab router."""
    router = APIRouter(prefix=BASE_URL, tags=['openclaw-homelab'])

    # ------------------------------------------------------------------
    # Health routes
    # ------------------------------------------------------------------

    @router.get('/health')
    async def openclaw_homelab_health(request: Request) -> dict[str, Any]:
        """Return homelab health in compact Slack-friendly format.

        Requires: homelab:read
        """
        owner = _scope_owner(request, HOMELAB_READ_SCOPES)
        services = _load_services()
        results, _, overall = await execute_health_checks(
            services, record_events=False, owner=owner, source_name='openclaw_health'
        )

        unhealthy = [r['name'] for r in results if r.get('status') != 'ok']
        if unhealthy:
            msg = f"{len(unhealthy)} service(s) unhealthy: {', '.join(unhealthy)}"
        else:
            msg = f"All {len(results)} service(s) healthy."

        return _ok(
            message=msg,
            events=None,
            event=None,
        ) | {
            'overall_status': overall,
            'services': results,
            'links': {'health': f'{BASE_URL}/health', 'events': f'{BASE_URL}/events'},
        }

    @router.post('/health/record')
    async def openclaw_homelab_health_record(request: Request) -> dict[str, Any]:
        """Run health checks and record failures as durable events.

        Requires: homelab:read + events:write
        """
        owner = _scope_owner(request, HOMELAB_READ_SCOPES)
        if not _has_scope(request, EVENTS_WRITE_SCOPES):
            raise HTTPException(403, 'API token missing required scope: events:write')

        services = _load_services()
        results, raw_events, overall = await execute_health_checks(
            services, record_events=True, owner=owner, source_name='openclaw_health'
        )
        recorded = [_compact_event(e) for e in raw_events]

        msg = (
            f"{len(recorded)} event(s) recorded from {len(results)} service(s)."
            if recorded else
            f"All {len(results)} service(s) healthy — no events recorded."
        )

        return _ok(message=msg, events=recorded) | {
            'overall_status': overall,
            'services': results,
            'links': {'health': f'{BASE_URL}/health', 'events': f'{BASE_URL}/events'},
        }

    # ------------------------------------------------------------------
    # Service read routes
    # ------------------------------------------------------------------

    @router.get('/services')
    async def openclaw_list_services(request: Request) -> dict[str, Any]:
        """List homelab services in compact format.

        Requires: homelab:read
        """
        _scope_owner(request, HOMELAB_READ_SCOPES)
        services = [_sanitize_service(srv) for srv in _load_services()]
        msg = f"{len(services)} service(s) returned." if services else 'No services found.'
        return _ok(message=msg, events=None) | {
            'services': services,
            'links': {'services': f'{BASE_URL}/services', 'health': f'{BASE_URL}/health'},
        }

    @router.get('/services/{name}')
    async def openclaw_get_service(request: Request, name: str) -> dict[str, Any]:
        """Get a specific homelab service by name.

        Requires: homelab:read
        """
        _scope_owner(request, HOMELAB_READ_SCOPES)
        services = _load_services()
        for srv in services:
            if srv.get('name') == name:
                return _ok(message=f"Service {name}.", event=None) | {
                    'service': _sanitize_service(srv),
                    'links': {'services': f'{BASE_URL}/services', 'health': f'{BASE_URL}/health'},
                }
        raise HTTPException(404, 'Service not found')

    # ------------------------------------------------------------------
    # Safe read-only ops commands
    # ------------------------------------------------------------------

    @router.get('/ops/docker-unhealthy')
    async def openclaw_docker_unhealthy(request: Request) -> dict[str, Any]:
        """Return unhealthy Docker containers only. Requires: homelab:read."""
        _scope_owner(request, HOMELAB_READ_SCOPES)
        data = _docker_unhealthy_containers()
        result = data['check']
        containers = data['containers']
        message = (
            f"{len(containers)} unhealthy Docker container(s)."
            if result.get('status') == 'ok' else
            f"Docker unhealthy check degraded: {result.get('error') or result.get('body') or 'unknown error'}"
        )
        return _ops_result('docker_unhealthy', message, {'containers': containers, 'check': result})

    @router.get('/ops/tailscale-status')
    async def openclaw_tailscale_status(request: Request) -> dict[str, Any]:
        """Return Tailscale status. Requires: homelab:read."""
        _scope_owner(request, HOMELAB_READ_SCOPES)
        data = _tailscale_status()
        result = data['check']
        status_json = data.get('tailscale')
        peer_count = int(status_json.get('peer_count') or 0) if isinstance(status_json, dict) else 0
        message = (
            f"Tailscale status returned {peer_count} peer(s)."
            if data.get('status') == 'ok' else
            f"Tailscale status degraded: {result.get('error') or result.get('stderr') or 'unknown error'}"
        )
        return _ops_result('tailscale_status', message, {'tailscale': status_json, 'check': result})

    @router.get('/ops/ping-heimdal')
    async def openclaw_ping_heimdal(request: Request) -> dict[str, Any]:
        """Ping the configured Heimdal target. Requires: homelab:read."""
        _scope_owner(request, HOMELAB_READ_SCOPES)
        result = _run_static_command(['ping', '-c', '3', PING_TARGET], timeout=10)
        message = (
            f"Heimdal ping OK: {PING_TARGET}."
            if result.get('status') == 'ok' else
            f"Heimdal ping degraded: {PING_TARGET}."
        )
        return _ops_result('ping_heimdal', message, {'target': PING_TARGET, 'check': result})

    @router.get('/ops/grafana')
    async def openclaw_check_grafana(request: Request) -> dict[str, Any]:
        """Check configured Grafana health URL. Requires: homelab:read."""
        _scope_owner(request, HOMELAB_READ_SCOPES)
        url = _find_grafana_url()
        if not url:
            return _ops_result('grafana', 'Grafana health URL is not configured.', {'status': 'degraded'})
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                resp = await client.get(url)
            status = 'ok' if resp.status_code < 400 else 'degraded'
            message = f"Grafana returned HTTP {resp.status_code}."
            return _ops_result('grafana', message, {
                'status': status,
                'url': url,
                'http_status': resp.status_code,
                'body': resp.text[:1000],
            })
        except Exception as exc:
            return _ops_result('grafana', f"Grafana check degraded: {exc}", {
                'status': 'degraded',
                'url': url,
                'error': str(exc),
            })

    @router.get('/ops/caddy-logs')
    async def openclaw_tail_caddy_logs(request: Request, lines: int = 80) -> dict[str, Any]:
        """Tail recent Caddy container logs. Requires: homelab:read."""
        _scope_owner(request, HOMELAB_READ_SCOPES)
        if not (1 <= lines <= 200):
            raise HTTPException(400, 'lines must be between 1 and 200')
        data = _docker_container_logs(CADDY_CONTAINER, lines)
        result = data['check']
        message = (
            f"Caddy logs returned last {lines} line(s)."
            if result.get('status') == 'ok' else
            f"Caddy log check degraded: {result.get('error') or result.get('body') or 'unknown error'}"
        )
        return _ops_result('caddy_logs', message, {
            'container': CADDY_CONTAINER,
            'lines': lines,
            'logs': data['logs'],
            'check': result,
        })

    @router.get('/ops/disk-usage')
    async def openclaw_disk_usage(request: Request) -> dict[str, Any]:
        """Return filesystem usage. Requires: homelab:read."""
        _scope_owner(request, HOMELAB_READ_SCOPES)
        result = _run_static_command(['df', '-h'], timeout=8)
        message = (
            "Disk usage returned."
            if result.get('status') == 'ok' else
            f"Disk usage check degraded: {result.get('error') or result.get('stderr') or 'unknown error'}"
        )
        return _ops_result('disk_usage', message, {'table': result.get('stdout') or '', 'check': result})

    @router.get('/ops/memory-usage')
    async def openclaw_memory_usage(request: Request) -> dict[str, Any]:
        """Return memory usage. Requires: homelab:read."""
        _scope_owner(request, HOMELAB_READ_SCOPES)
        result = _run_static_command(['free', '-h'], timeout=8)
        message = (
            "Memory usage returned."
            if result.get('status') == 'ok' else
            f"Memory usage check degraded: {result.get('error') or result.get('stderr') or 'unknown error'}"
        )
        return _ops_result('memory_usage', message, {'table': result.get('stdout') or '', 'check': result})

    @router.get('/ops/dns-check')
    async def openclaw_dns_check(request: Request, domain: str) -> dict[str, Any]:
        """Check DNS resolution for a domain. Requires: homelab:read."""
        _scope_owner(request, HOMELAB_READ_SCOPES)
        try:
            hostname, aliases, ips = socket.gethostbyname_ex(domain)
            result = {'status': 'ok', 'hostname': hostname, 'aliases': aliases, 'ips': ips}
            message = f"DNS check for {domain} resolved to {len(ips)} IP(s)."
        except Exception as exc:
            result = {'status': 'degraded', 'error': str(exc)}
            message = f"DNS check for {domain} degraded: {exc}"
        return _ops_result('dns_check', message, {'domain': domain, 'check': result})

    @router.get('/ops/caddy-routes')
    async def openclaw_caddy_routes(request: Request) -> dict[str, Any]:
        """Check Caddy configured routes. Requires: homelab:read."""
        _scope_owner(request, HOMELAB_READ_SCOPES)
        caddy_url = os.getenv('HOMELAB_CADDY_CONFIG_URL', f'http://{CADDY_CONTAINER}:2019/config/')
        try:
            async with httpx.AsyncClient(timeout=8) as client:
                resp = await client.get(caddy_url)
                if resp.status_code < 400:
                    try:
                        payload = resp.json()
                        payload = _sanitize_dict(payload)
                    except Exception:
                        payload = {'raw': resp.text[:2000]}
                    message = "Caddy routes retrieved successfully."
                    result = {'status': 'ok', 'config': payload}
                else:
                    message = f"Caddy routes degraded: HTTP {resp.status_code}"
                    result = {'status': 'degraded', 'http_status': resp.status_code, 'body': resp.text[:1000]}
        except Exception as exc:
            message = f"Caddy routes degraded: {exc}"
            result = {'status': 'degraded', 'error': str(exc)}
        return _ops_result('caddy_routes', message, {'check': result})

    @router.get('/ops/netbox-sync-status')
    async def openclaw_netbox_sync_status(request: Request) -> dict[str, Any]:
        """Return Netbox sync status. Requires: homelab:read."""
        _scope_owner(request, HOMELAB_READ_SCOPES)
        result = _run_static_command(['netbox-sync', '--status'], timeout=8)
        message = (
            "Netbox sync status returned."
            if result.get('status') == 'ok' else
            f"Netbox sync status degraded: {result.get('error') or result.get('stderr') or 'unknown error'}"
        )
        return _ops_result('netbox_sync_status', message, {'output': result.get('stdout') or '', 'check': result})

    @router.get('/ops/redmine-status')
    async def openclaw_redmine_status(request: Request) -> dict[str, Any]:
        """Check Redmine via Converge. Requires: homelab:read."""
        _scope_owner(request, HOMELAB_READ_SCOPES)
        from routes.openclaw_bridge_routes import _converge_config
        try:
            base_url, api_key = _converge_config()
            async with httpx.AsyncClient(timeout=8) as client:
                resp = await client.get(f"{base_url}/health", headers={"Authorization": f"Bearer {api_key}"})
                if resp.status_code < 400:
                    try:
                        payload = resp.json()
                        payload = _sanitize_dict(payload)
                    except Exception:
                        payload = {'raw': resp.text[:2000]}
                    message = "Redmine (Converge) is healthy."
                    result = {'status': 'ok', 'health': payload}
                else:
                    message = f"Redmine (Converge) degraded: HTTP {resp.status_code}"
                    result = {'status': 'degraded', 'http_status': resp.status_code, 'body': resp.text[:1000]}
        except Exception as exc:
            message = f"Redmine (Converge) check failed: {exc}"
            result = {'status': 'degraded', 'error': str(exc)}
        return _ops_result('redmine_status', message, {'check': result})

    @router.get('/ops/github-failed')
    async def openclaw_github_failed(request: Request) -> dict[str, Any]:
        """List failed GitHub actions. Requires: homelab:read."""
        _scope_owner(request, HOMELAB_READ_SCOPES)
        result = _run_static_command(['gh', 'run', 'list', '--status', 'failure', '--limit', '5'], timeout=10)
        message = (
            "Failed GitHub runs retrieved."
            if result.get('status') == 'ok' else
            f"GitHub check degraded: {result.get('error') or result.get('stderr') or 'unknown error'}"
        )
        return _ops_result('github_failed', message, {'output': result.get('stdout') or '', 'check': result})

    @router.get('/ops/ollama-models')
    async def openclaw_ollama_models(request: Request) -> dict[str, Any]:
        """List Ollama models. Requires: homelab:read."""
        _scope_owner(request, HOMELAB_READ_SCOPES)
        result = _run_static_command(['ollama', 'list'], timeout=8)
        message = (
            "Ollama models retrieved."
            if result.get('status') == 'ok' else
            f"Ollama check degraded: {result.get('error') or result.get('stderr') or 'unknown error'}"
        )
        return _ops_result('ollama_models', message, {'table': result.get('stdout') or '', 'check': result})

    # ------------------------------------------------------------------
    # Event read routes
    # ------------------------------------------------------------------

    @router.get('/events')
    async def openclaw_list_events(
        request: Request,
        status: str | None = None,
        limit: int | None = None,
    ) -> dict[str, Any]:
        """List homelab events in compact Slack-friendly format.

        Requires: events:read
        """
        _scope_owner(request, EVENTS_READ_SCOPES)
        if limit is not None and not (1 <= limit <= 100):
            raise HTTPException(400, 'limit must be between 1 and 100')

        store = EventStore()
        try:
            events = store.get_events(status=status, limit=limit)
        except Exception as e:
            raise HTTPException(500, f'Persistence error: {e}')

        compact = [_compact_event(e) for e in events]
        count = len(compact)
        msg = f"{count} event(s) returned." if count else 'No events found.'
        return _ok(message=msg, events=compact) | {
            'links': {'events': f'{BASE_URL}/events', 'health': f'{BASE_URL}/health'},
        }

    @router.get('/events/{event_id}')
    async def openclaw_get_event(request: Request, event_id: str) -> dict[str, Any]:
        """Get a single event by ID.

        Requires: events:read
        """
        _scope_owner(request, EVENTS_READ_SCOPES)
        store = EventStore()
        event = store.get_event(event_id)
        if not event:
            raise HTTPException(404, 'Event not found')
        return _ok(
            message=f"Event {event_id}.",
            event=_compact_event(event),
        )

    # ------------------------------------------------------------------
    # Event lifecycle mutation routes
    # ------------------------------------------------------------------

    @router.post('/events/{event_id}/ack')
    async def openclaw_ack_event(request: Request, event_id: str) -> dict[str, Any]:
        """Acknowledge an open event.

        Requires: events:ack
        """
        owner = _scope_owner(request, EVENTS_ACK_SCOPES)
        store = EventStore()
        try:
            event = store.update_status(event_id, 'acknowledged', owner)
            if not event:
                raise HTTPException(404, 'Event not found')
            return _ok(
                message=f"Event {event_id} acknowledged by {owner}.",
                event=_compact_event(event),
            )
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(500, f'Persistence error: {e}')

    @router.post('/events/{event_id}/investigate')
    async def openclaw_investigate_event(request: Request, event_id: str) -> dict[str, Any]:
        """Mark an event as being investigated.

        Requires: events:ack
        """
        owner = _scope_owner(request, EVENTS_ACK_SCOPES)
        store = EventStore()
        try:
            event = store.update_status(event_id, 'investigating', owner)
            if not event:
                raise HTTPException(404, 'Event not found')
            return _ok(
                message=f"Event {event_id} marked as investigating by {owner}.",
                event=_compact_event(event),
            )
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(500, f'Persistence error: {e}')

    @router.post('/events/{event_id}/resolve')
    async def openclaw_resolve_event(request: Request, event_id: str) -> dict[str, Any]:
        """Resolve an event.

        Requires: events:resolve
        """
        owner = _scope_owner(request, EVENTS_RESOLVE_SCOPES)
        store = EventStore()
        try:
            event = store.update_status(event_id, 'resolved', owner)
            if not event:
                raise HTTPException(404, 'Event not found')
            return _ok(
                message=f"Event {event_id} resolved by {owner}.",
                event=_compact_event(event),
            )
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(500, f'Persistence error: {e}')

    @router.post('/events/{event_id}/ignore')
    async def openclaw_ignore_event(request: Request, event_id: str) -> dict[str, Any]:
        """Ignore an event.

        Requires: events:resolve
        """
        owner = _scope_owner(request, EVENTS_RESOLVE_SCOPES)
        store = EventStore()
        try:
            event = store.update_status(event_id, 'ignored', owner)
            if not event:
                raise HTTPException(404, 'Event not found')
            return _ok(
                message=f"Event {event_id} ignored by {owner}.",
                event=_compact_event(event),
            )
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(500, f'Persistence error: {e}')

    return router
