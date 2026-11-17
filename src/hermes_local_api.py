"""Read-only Projects data on the Hermes origin, for the Projects page that runs inside HermesGUI.

The Hermes Webapp talks to ``hermes.localhost:<port>`` and the Projects dashboard API lives on ``localhost:<port>`` (Odysseus). Rather than opening cross-origin
access or giving Odysseus the gateway token, the Odysseus proxy that already fronts the Hermes origin answers these few GET routes itself, from the same
Dashboard the standalone page uses. Nothing here is forwarded to Hermes, nothing is written, and no Hermes credential is read, stored or sent.

Access: only after the proxy has applied its own host/origin/loopback checks AND found a live access grant (the grant is minted only for a signed-in Odysseus
browser administrator and ends with that Odysseus session). Same-origin browser requests only; every reply is private and non-cacheable.
"""
import asyncio
import json
import re
import time
from urllib.parse import parse_qs

LOCAL_PREFIX = '/__odysseus/api/'
PROJECT_ID = re.compile(r'[0-9a-f]{10}')
_ROUTE = re.compile(r'^/__odysseus/api/(?:projects(?:/([^/]+)(?:/(related))?)?|(sessions)|fs/(drives|check)|(usage)/costs)$')
_HEADERS = [(b'content-type', b'application/json; charset=utf-8'), (b'cache-control', b'no-store'), (b'pragma', b'no-cache'), (b'x-content-type-options', b'nosniff'),
            (b'referrer-policy', b'no-referrer')]
TIMEOUT_S = 25
COSTS_CACHE_S = 30.0
RESULT_TTL_S = 3.0
RECENT_MAX = 64


class LocalProjectsApi:
    def __init__(self, get_dashboard, concurrency=4):
        self.get_dashboard = get_dashboard
        self._slots = None
        self._concurrency = concurrency
        self._costs = (0.0, None)
        self._flights = {}          # key -> the one running read; identical concurrent requests wait for it instead of repeating the work
        self._recent = {}           # key -> (monotonic time, result): a few seconds of reuse, so a page that fires several reads at once pays once

    async def _reply(self, send, status, body, extra=()):
        data = json.dumps(body, default=str).encode()
        await send({'type': 'http.response.start', 'status': status, 'headers': _HEADERS + list(extra) + [(b'content-length', str(len(data)).encode())]})
        await send({'type': 'http.response.body', 'body': data})

    @staticmethod
    def _port(scope):
        for name, value in scope['headers']:
            if name == b'host':
                tail = value.decode('latin-1').rsplit(':', 1)
                return int(tail[1]) if len(tail) == 2 and tail[1].isdigit() else 80
        return 80

    async def _shared(self, key, work, ttl):
        """Run ``work`` in a thread once per key at a time. No global lock: different keys run in parallel (bounded by the concurrency slots, taken
        only while a read actually runs), identical ones are deduplicated, and a finished result is reused for ``ttl`` seconds. A waiter that times
        out or disconnects abandons only its own wait; the shared read carries on for the others."""
        cached = self._recent.get(key)
        if ttl and cached and time.monotonic() - cached[0] < ttl:
            return cached[1]
        flight = self._flights.get(key)
        if flight is None:
            async def run():
                async with self._slots:
                    value = await asyncio.to_thread(work)
                if ttl and value is not None:
                    if len(self._recent) >= RECENT_MAX:
                        self._recent.clear()
                    self._recent[key] = (time.monotonic(), value)
                return value
            flight = asyncio.ensure_future(run())
            self._flights[key] = flight

            def done(task):
                self._flights.pop(key, None)
                if not task.cancelled():
                    task.exception()                       # retrieved, so an error nobody is waiting for is not reported as unhandled
            flight.add_done_callback(done)
        return await asyncio.shield(flight)

    def costs(self, dash):
        """Usage costs from Hermes' own stored accounting (read-only SQLite), cached briefly so a page reload never rereads the store."""
        stamp, value = self._costs
        if value is not None and time.monotonic() - stamp < COSTS_CACHE_S:
            return value
        from src import usage_costs
        home = dash.hermes_home
        value = usage_costs.overview(home / 'state.db' if home else None, home / 'projects.db' if home else None)
        self._costs = (time.monotonic(), value)
        return value

    async def __call__(self, scope, receive, send, grant):
        if grant is None:                                                   # never serve without a live grant, even if the proxy is wired without them
            return await self._reply(send, 401, {'detail': 'Open Hermes from Odysseus (Projects).'}, [(b'x-odysseus-grant', b'required')])
        if scope['method'] not in ('GET', 'HEAD'):
            return await self._reply(send, 405, {'detail': 'Read-only'}, [(b'allow', b'GET, HEAD')])
        headers = dict(scope['headers'])
        site = headers.get(b'sec-fetch-site')
        if site is not None and site != b'same-origin':
            return await self._reply(send, 403, {'detail': 'Same-origin browser request required'})
        match = _ROUTE.match(scope['path'])
        if not match:
            return await self._reply(send, 404, {'detail': 'Unknown route'})
        project_id, part, sessions, fs, usage = match.group(1), match.group(2), match.group(3), match.group(4), match.group(5)
        if project_id is not None and not PROJECT_ID.fullmatch(project_id):
            return await self._reply(send, 404, {'detail': 'Unknown project'})
        port = self._port(scope)
        dash = self.get_dashboard()
        if self._slots is None:
            self._slots = asyncio.Semaphore(self._concurrency)

        def work():
            if usage:
                return self.costs(dash)
            if fs == 'drives':
                return {'drives': dash.drives()}
            if fs == 'check':
                return dash.check_path(parse_qs((scope.get('query_string') or b'').decode('utf-8', 'replace')).get('path', [''])[0])
            if sessions:
                return {'sessions': dash.sessions()}
            if project_id is None:
                return dash.overview()
            if part == 'related':
                return dash.related_detail(project_id, port=port)
            return dash.detail(project_id, port=port, related=False)

        key = (scope['path'], scope.get('query_string') or b'')
        try:
            result = await asyncio.wait_for(self._shared(key, work, 0 if fs else RESULT_TTL_S), TIMEOUT_S)
        except asyncio.TimeoutError:
            return await self._reply(send, 504, {'detail': 'The project data took too long to read; try again.'})
        except Exception:
            return await self._reply(send, 500, {'detail': 'The project data could not be read.'})
        if result is None:
            return await self._reply(send, 404, {'detail': 'Unknown project'})
        if part == 'related':
            result = {'related_sessions': result}
        await self._reply(send, 200, result)
