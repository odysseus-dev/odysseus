"""Host-routed loopback proxy: hermes.localhost:<port> -> an independent Hermes backend.

Requests whose Host is exactly hermes.localhost:<listener port> are forwarded to the Hermes backend; every other
request reaches Odysseus unchanged. Two backends are supported, chosen by ODYSSEUS_HERMES_PROXY_MODE (default
``webui``, so nothing changes until the launcher opts in):

``webui``    the standalone Hermes WebUI. Host, Origin, bodies and the caller's own Cookie header are forwarded
             unchanged, so the WebUI keeps its own origin/CSRF/CSP; WebSockets are refused (1008) as before.
``gateway``  the official Hermes Gateway/Webapp. The gateway accepts only its own loopback authority as Host and
             Origin (DNS-rebinding defence, ``hermes_cli.web_server_boundary``) and documents a proxy that rewrites
             Host to the backend's as the supported arrangement. So this proxy first applies its OWN strict Host/Origin
             policy to what the browser sent (the browser only ever talks to the proxy) and only then presents the
             gateway's bound authority upstream. The gateway's checks stay fully active and its session-token
             authentication is untouched: this module never adds, stores or replays a credential.
             WebSockets are relayed frame for frame.

Both modes: the peer must be loopback and no Forwarded/X-Forwarded-*/X-Real-* header may be present.
Additionally in ``gateway`` mode (a refusal never reaches Hermes):
  * exactly one Host header, equal to an allowed ``<name>.localhost:<port>`` (checked before routing);
  * at most one Origin header; when present it must be exactly an allowed origin (``Origin: null`` never is);
  * Sec-Fetch-Site, when present: WebSockets must be ``same-origin``; state-changing HTTP methods must be
    ``same-origin`` or ``none``;
  * WebSocket upgrades must carry an allowed Origin (browsers always send one) unless explicitly relaxed;
  * Hermes' CORS response headers are dropped: this proxy grants no cross-origin access.

Access grants (gateway mode, on by default; ODYSSEUS_HERMES_PROXY_GRANTS=0 disables them explicitly): every request and WebSocket upgrade on the Hermes
origin must also carry a live server-side grant (src/hermes_grants.py) bound to the Odysseus browser session that launched Projects. The grant is an
ADDITIONAL gate in front of the gateway's own authentication, never a substitute for it. Logging out of Odysseus (or any end of that session) revokes the
grant on the next request and closes this proxy's open WebSockets and streams. The grant cookie is stripped from what is forwarded, and an upstream
response cannot set or clear it. It does not and cannot revoke copied upstream Hermes cookies or protect direct access to the gateway's own port.

The proxy keeps no cookies: its shared HTTP client would otherwise store a Hermes login and attach it to later
requests from any local process. This is not a security boundary beyond the checks above; Hermes keeps its own auth.
"""
import asyncio
import hashlib
import base64
from dataclasses import dataclass
import logging
import re
import socket
import time
from urllib.parse import urlsplit

import httpx

from src.hermes_grants import ENTER_PATH, ENTERED_COOKIE, GRANT_COOKIE, GrantDenied, GrantError
from src.hermes_local_api import LOCAL_PREFIX

PROXY_HOST = 'hermes.localhost'
MODES = ('webui', 'gateway')
DEFAULT_WS_MAX_MB = 400            # the gateway accepts one 384 MiB frame for a large attachment (web_server_boundary)
_LOOPBACK = {'127.0.0.1', '::1'}
_HOP_BY_HOP = {b'connection', b'keep-alive', b'proxy-authenticate', b'proxy-authorization',
               b'proxy-connection', b'te', b'trailer', b'transfer-encoding', b'upgrade'}
_FORWARDED = {b'forwarded', b'x-forwarded-for', b'x-forwarded-host', b'x-forwarded-proto',
              b'x-forwarded-port', b'x-real-ip', b'x-real-host'}
_WS_HANDSHAKE = {b'host', b'origin', b'upgrade', b'connection', b'content-length', b'sec-websocket-key',
                 b'sec-websocket-version', b'sec-websocket-extensions', b'sec-websocket-protocol', b'sec-websocket-accept'}
_ERROR_HEADERS = [(b'content-type', b'text/plain; charset=utf-8'), (b'cache-control', b'no-store'),
                  (b'x-content-type-options', b'nosniff'), (b'x-frame-options', b'DENY'),
                  (b'content-security-policy', b"default-src 'none'; frame-ancestors 'none'")]
_SAFE_METHODS = {'GET', 'HEAD', 'OPTIONS'}

# What a browser sees when the hand-over from Odysseus is refused. Every refusal returns this same page (no oracle for which check failed) and it recovers by
# itself, in at most three steps within 30 seconds: reload once (a refusal before the ticket was read leaves it unspent, which is what a manual hard refresh
# relies on), then go back to Odysseus once for a fresh ticket (the ticket was spent or expired), then stop and show the message. It changes no check.
#
# It also removes any service worker registered on this origin, and the cache it left, before it retries. A browser that used the old Hermes WebUI at
# hermes.localhost keeps that app's worker (scope /) for good; the worker answers every navigation by re-fetching it as fetch(new Request(request)), which turns
# the navigation (Sec-Fetch-Mode navigate, Dest document) into a plain fetch (same-origin, empty). The enter route refuses that, correctly, on every click
# until a hard refresh (which bypasses workers). The official Webapp registers no worker of its own, so nothing legitimate is removed.
_ENTER_SCRIPT = ("(function(){var K='odysseus.enterRetry',now=Date.now(),s={stage:0,t:0},d=document.body.dataset,"
                 "give=function(){document.getElementById('w').hidden=true;document.getElementById('m').hidden=false};"
                 "try{var r=JSON.parse(sessionStorage.getItem(K)||'null');if(r&&now-r.t<30000)s=r}catch(e){}"
                 "if(s.stage>=2)return give();"
                 "try{var v=JSON.stringify({stage:s.stage+1,t:s.stage?s.t:now});sessionStorage.setItem(K,v);if(sessionStorage.getItem(K)!==v)throw 0}catch(e){return give()}"
                 "var done=false,go=function(){if(done)return;done=true;if(s.stage===0)location.reload();else location.replace(d.back+'?open=projects')},jobs=[];"
                 "try{if(navigator.serviceWorker)jobs.push(navigator.serviceWorker.getRegistrations().then(function(a){return Promise.all(a.map(function(x){return x.unregister()}))}))}catch(e){}"
                 "try{if(window.caches)jobs.push(caches.keys().then(function(a){return Promise.all(a.filter(function(k){return k.indexOf('hermes-shell-')===0}).map(function(k){return caches.delete(k)}))}))}catch(e){}"
                 "Promise.all(jobs).then(go,go);setTimeout(go,1500)})()")
_ENTER_SCRIPT_HASH = base64.b64encode(hashlib.sha256(_ENTER_SCRIPT.encode()).digest()).decode()
_ENTER_MESSAGE = 'Could not open Hermes from Odysseus. Return to Odysseus and choose Projects again.'


def _enter_page(port):
    back = f'http://localhost:{int(port)}/'
    html = ('<!doctype html><html lang="en"><meta charset="utf-8"><meta name="color-scheme" content="light dark"><title>Opening Hermes</title>'
            f'<body data-back="{back}"><p id="w">Opening Hermes…</p><p id="m" hidden>Could not open Hermes from Odysseus. '
            f'<a href="{back}">Return to Odysseus</a> and choose Projects again.</p>'
            f'<noscript><p>Could not open Hermes from Odysseus. <a href="{back}">Return to Odysseus</a> and choose Projects again.</p></noscript>'
            f'<script>{_ENTER_SCRIPT}</script>')
    return html.encode('utf-8')


_ENTER_HEADERS = [(b'content-type', b'text/html; charset=utf-8'), (b'cache-control', b'no-store'), (b'pragma', b'no-cache'),
                  (b'x-content-type-options', b'nosniff'), (b'x-frame-options', b'DENY'), (b'referrer-policy', b'no-referrer'),
                  (b'content-security-policy', f"default-src 'none'; script-src 'sha256-{_ENTER_SCRIPT_HASH}'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'".encode())]
_LABEL = r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?'
_NAME = re.compile(rf'^(?:{_LABEL}\.)*localhost$')
_ORIGIN = re.compile(rf'^https?://(?:{_LABEL}\.)*{_LABEL}(?::[0-9]{{1,5}})?$')
_RESERVED_CLOSE = {1005, 1006, 1015}
_SECRET_QUERY = re.compile(r'([?&](?:token|ticket|access_token|session_token|code|launch)=)[^&\s"\']+', re.I)
log = logging.getLogger('odysseus.hermes_proxy')


class RedactSecrets(logging.Filter):
    """Blank credential-looking query parameters in log lines. httpx and uvicorn log full request URLs at INFO, and a
    one-use ticket or session token must not outlive its use in a log file."""

    def filter(self, record):
        try:
            if isinstance(record.msg, str):
                record.msg = _SECRET_QUERY.sub(r'\1[redacted]', record.msg)
            if isinstance(record.args, tuple):
                record.args = tuple(_SECRET_QUERY.sub(r'\1[redacted]', arg) if isinstance(arg, str) else arg for arg in record.args)
        except Exception:                      # a logging helper must never break the request being logged
            pass
        return True


def redact_request_logs():
    for name in ('httpx', 'uvicorn.access'):
        target = logging.getLogger(name)
        if not any(isinstance(item, RedactSecrets) for item in target.filters):
            target.addFilter(RedactSecrets())


class ProxyConfigError(ValueError):
    """The proxy configuration is invalid; the proxy is not installed (fail closed)."""


def _csv(value):
    return [item.strip() for item in (value or '').split(',') if item.strip()]


@dataclass(frozen=True)
class ProxyPolicy:
    """Which Host and Origin values the gateway-mode proxy accepts. Exact matches only, no wildcards."""
    names: frozenset = frozenset({PROXY_HOST})
    extra_origins: frozenset = frozenset()
    allow_missing_origin_http: bool = True       # plain navigations and curl-style clients send none
    allow_missing_origin_ws: bool = False        # browsers always send one; a native client should dial the gateway itself
    ws_max_bytes: int = DEFAULT_WS_MAX_MB * 1024 * 1024
    pass_host: bool = False                      # False: present the gateway's own authority upstream; True: the browser's Host/Origin
    require_enter_referer: bool = True           # the grant exchange must be a navigation that came from the Odysseus origin

    def hosts(self, port):
        accepted = {f'{name}:{port}' for name in self.names}
        if port == 80:
            accepted |= set(self.names)
        return accepted

    def odysseus_origins(self, port):
        return {f'http://localhost:{port}', f'http://127.0.0.1:{port}'}

    def origins(self, port):
        return {f'http://{host}' for host in self.hosts(port)} | set(self.extra_origins)

    @classmethod
    def from_environment(cls, environment):
        """ODYSSEUS_HERMES_PROXY_HOSTNAMES        extra names, each ``localhost`` or ``*.localhost`` (browsers resolve them to loopback)
           ODYSSEUS_HERMES_PROXY_ORIGINS          extra exact origins, e.g. ``http://projects.localhost:7000``
           ODYSSEUS_HERMES_PROXY_WS_NO_ORIGIN=1   accept WebSocket upgrades that carry no Origin header
           ODYSSEUS_HERMES_PROXY_WS_MAX_MB        largest single WebSocket frame, 1-1024 (default 400)
           ODYSSEUS_HERMES_PROXY_UPSTREAM_HOST    ``bound`` (default): present the gateway's own 127.0.0.1:PORT as Host/Origin; use with
                                                  webapp.public_url unset. ``public``: pass the browser's Host/Origin/Referer through
                                                  unchanged; required when the gateway's sign-in gate is on, because it then insists on
                                                  the real origin (webapp.public_url must be http://hermes.localhost:<Odysseus port>)"""
        names = {PROXY_HOST}
        for name in _csv(environment.get('ODYSSEUS_HERMES_PROXY_HOSTNAMES')):
            if not _NAME.match(name) or len(name) > 253:
                raise ProxyConfigError(f'ODYSSEUS_HERMES_PROXY_HOSTNAMES: {name!r} is not localhost or a *.localhost name')
            names.add(name)
        origins = set()
        for origin in _csv(environment.get('ODYSSEUS_HERMES_PROXY_ORIGINS')):
            if not _ORIGIN.match(origin):
                raise ProxyConfigError(f'ODYSSEUS_HERMES_PROXY_ORIGINS: {origin!r} is not an exact http(s) origin')
            origins.add(origin)
        megabytes = environment.get('ODYSSEUS_HERMES_PROXY_WS_MAX_MB') or str(DEFAULT_WS_MAX_MB)
        if not megabytes.isdigit() or not 1 <= int(megabytes) <= 1024:
            raise ProxyConfigError('ODYSSEUS_HERMES_PROXY_WS_MAX_MB must be a whole number from 1 to 1024')
        upstream_host = environment.get('ODYSSEUS_HERMES_PROXY_UPSTREAM_HOST') or 'bound'
        if upstream_host not in ('bound', 'public'):
            raise ProxyConfigError("ODYSSEUS_HERMES_PROXY_UPSTREAM_HOST must be 'bound' or 'public'")
        return cls(names=frozenset(names), extra_origins=frozenset(origins),
                   allow_missing_origin_ws=environment.get('ODYSSEUS_HERMES_PROXY_WS_NO_ORIGIN') == '1',
                   ws_max_bytes=int(megabytes) * 1024 * 1024, pass_host=upstream_host == 'public',
                   require_enter_referer=environment.get('ODYSSEUS_HERMES_ENTER_REFERER') != '0')


UVICORN_DEFAULT_WS_MAX = 16 * 1024 * 1024


def uvicorn_ws_max_size(environment=None):
    """uvicorn ``ws_max_size`` for the Odysseus server: unchanged unless the gateway proxy mode is on, because a large
    attachment arrives as one browser frame and must fit through the relay."""
    import os
    source = os.environ if environment is None else environment
    if (source.get('ODYSSEUS_HERMES_PROXY_MODE') or 'webui') != 'gateway' or source.get('ODYSSEUS_HERMES_PROXY') == '0':
        return UVICORN_DEFAULT_WS_MAX
    try:
        return ProxyPolicy.from_environment(source).ws_max_bytes
    except ProxyConfigError:
        return UVICORN_DEFAULT_WS_MAX


def _websockets():
    try:
        from websockets.asyncio.client import connect
        import websockets.exceptions as errors
    except ImportError:
        return None
    return connect, errors


def _close_code(code):
    if code is None or code == 1005:
        return 1000
    if code in _RESERVED_CLOSE or not 1000 <= code <= 4999:
        return 1011
    return code


def _close_reason(reason):
    return (reason or '').encode('utf-8')[:120].decode('utf-8', 'ignore')


class _NoCookies(httpx.Cookies):
    """An always-empty jar: nothing is stored from responses and nothing is added to requests."""

    def extract_cookies(self, response):
        return None

    def set_cookie_header(self, request):
        return None

    def set(self, *args, **kwargs):
        return None


class _StatelessClient(httpx.AsyncClient):
    @property
    def cookies(self):
        return _NoCookies()

    @cookies.setter
    def cookies(self, cookies):
        return None


class HermesHostProxy:
    def __init__(self, app, upstream, mode='webui', policy=None, grants=None, local_api=None):
        self.local_api = local_api
        parts = urlsplit(upstream)
        if parts.scheme != 'http' or parts.hostname != '127.0.0.1' or not parts.port:
            raise ValueError('The Hermes upstream must be an explicit http://127.0.0.1:PORT/ address')
        if mode not in MODES:
            raise ProxyConfigError(f'The Hermes proxy mode must be one of {MODES}')
        self.app = app
        self.upstream = f'http://127.0.0.1:{parts.port}'
        self.authority = f'127.0.0.1:{parts.port}'
        self.mode = mode
        self.policy = policy or ProxyPolicy()
        self.grants = grants if mode == 'gateway' else None
        self._client = None

    def _http(self):
        if self._client is None:
            self._client = _StatelessClient(
                base_url=self.upstream, follow_redirects=False, trust_env=False,
                timeout=httpx.Timeout(connect=5, read=None, write=120, pool=10),
                limits=httpx.Limits(max_connections=64, max_keepalive_connections=8))
        return self._client

    @staticmethod
    def _host(scope):
        for name, value in scope['headers']:
            if name == b'host':
                return value.decode('latin-1').lower()
        return ''

    @staticmethod
    def _port(scope):
        return (scope.get('server') or (None, None))[1]

    def _matches(self, scope):
        port = self._port(scope)
        if not port:
            return False
        if self.mode == 'gateway':
            return self._host(scope) in self.policy.hosts(port)
        return self._host(scope) in ({f'{PROXY_HOST}:{port}'} | ({PROXY_HOST} if port == 80 else set()))

    @staticmethod
    def _permitted(scope):
        client = (scope.get('client') or (None,))[0]
        return client in _LOOPBACK and not any(name in _FORWARDED for name, _ in scope['headers'])

    def _refuse(self, scope, *, websocket):
        """None when the request may be forwarded, else (status, message). Nothing here reaches Hermes."""
        if not self._permitted(scope):
            return 403, b'Hermes is available only from this computer.'
        if self.mode != 'gateway':
            return None
        headers = scope['headers']
        if sum(1 for name, _ in headers if name == b'host') != 1:
            return 400, b'Ambiguous Host header.'
        origins = [value for name, value in headers if name == b'origin']
        if len(origins) > 1:
            return 400, b'Ambiguous Origin header.'
        if origins:
            if origins[0].decode('latin-1') not in self.policy.origins(self._port(scope)):
                return 403, b'Origin not allowed.'
        elif websocket and not self.policy.allow_missing_origin_ws:
            return 403, b'Origin required.'
        elif not websocket and not self.policy.allow_missing_origin_http:
            return 403, b'Origin required.'
        site = next((value.decode('latin-1').lower() for name, value in headers if name == b'sec-fetch-site'), None)
        if site is not None:
            if websocket and site != 'same-origin':
                return 403, b'Cross-site WebSocket refused.'
            if not websocket and scope['method'] not in _SAFE_METHODS and site not in ('same-origin', 'none'):
                return 403, b'Cross-site request refused.'
        return None

    @staticmethod
    def _cookie_values(scope):
        values = []
        for name, raw in scope['headers']:
            if name == b'cookie':
                for part in raw.decode('latin-1').split(';'):
                    key, _, value = part.strip().partition('=')
                    if key == GRANT_COOKIE:
                        values.append(value)
        return values

    @staticmethod
    def _without_grant_cookie(headers):
        """The request headers with our grant cookie removed (the gateway and the agent behind it must never see it); other cookies are kept verbatim."""
        out = []
        for name, value in headers:
            if name == b'cookie':
                kept = [part.strip() for part in value.decode('latin-1').split(';') if part.strip().partition('=')[0] not in (GRANT_COOKIE, ENTERED_COOKIE)]
                if not kept:
                    continue
                value = '; '.join(kept).encode('latin-1')
            out.append((name, value))
        return out

    def _authorize(self, scope):
        """The live grant for this request, or None. Zero cookies, conflicting duplicates, a bad value, a revoked grant and any internal failure all deny."""
        values = self._cookie_values(scope)
        if len(set(values)) != 1:
            return None
        try:
            return self.grants.authorize(values[0])
        except Exception:
            log.error('grant validation failed; denying', exc_info=True)
            return None

    def _deny_no_grant(self, scope):
        """(status, message, headers) for a request without a live grant: navigations are sent back to Odysseus, everything else gets a plain 401."""
        extra = [(b'x-odysseus-grant', b'required')]
        headers = dict(scope['headers'])
        navigating = headers.get(b'sec-fetch-mode') == b'navigate' or (b'sec-fetch-mode' not in headers and b'text/html' in headers.get(b'accept', b''))
        if navigating and scope['type'] == 'http' and scope['method'] in ('GET', 'HEAD'):
            return 302, b'Open Hermes from Odysseus (Projects).', extra + [(b'location', f'http://localhost:{self._port(scope)}/'.encode())]
        return 401, b'Open Hermes from Odysseus (Projects).', extra

    async def _fail(self, send, status, message, extra=()):
        await send({'type': 'http.response.start', 'status': status,
                    'headers': _ERROR_HEADERS + list(extra) + [(b'content-length', str(len(message)).encode())]})
        await send({'type': 'http.response.body', 'body': message})

    async def _deny_enter(self, scope, send):
        body = _enter_page(self._port(scope))
        await send({'type': 'http.response.start', 'status': 403, 'headers': _ENTER_HEADERS + [(b'content-length', str(len(body)).encode())]})
        await send({'type': 'http.response.body', 'body': body})

    async def _enter(self, scope, receive, send):
        """GET /__odysseus/enter?ticket=...: redeem a one-use ticket, set this browser's grant cookie, redirect to the server-held target.

        Everything here is checked before the ticket is looked at, and every failure is the same generic 403 (no oracle)."""
        refusal = self._refuse(scope, websocket=False)
        if refusal:
            log.warning('enter refused before the ticket: %s', refusal[1].decode('latin-1'))
            return await self._fail(send, *refusal)
        headers = dict(scope['headers'])
        deny = None
        if scope['method'] != 'GET':
            return await self._fail(send, 405, b'Method not allowed.', [(b'allow', b'GET')])
        mode, dest = headers.get(b'sec-fetch-mode', b'navigate'), headers.get(b'sec-fetch-dest', b'document')
        if mode != b'navigate' or dest != b'document':
            log.warning('enter refused before the ticket: reason=fetch-metadata mode=%s dest=%s', mode.decode('latin-1')[:20], dest.decode('latin-1')[:20])
            return await self._deny_enter(scope, send)
        if self.policy.require_enter_referer:
            raw_referer = headers.get(b'referer', b'').decode('latin-1')
            referer = urlsplit(raw_referer)
            try:
                ok = (referer.scheme == 'http' and not referer.username and not referer.password
                      and f'http://{referer.hostname}:{referer.port}' in self.policy.odysseus_origins(self._port(scope)))
            except ValueError:
                ok = False
            if not ok:
                seen = 'absent' if not raw_referer else f'{referer.scheme}://{referer.hostname}:{referer.port}'[:60]
                log.warning('enter refused before the ticket: reason=referer seen=%s site=%s', seen, headers.get(b'sec-fetch-site', b'-').decode('latin-1')[:20])
                return await self._deny_enter(scope, send)
        query = scope.get('query_string', b'').decode('latin-1')
        pairs = [part.partition('=') for part in query.split('&') if part]
        tickets = [value for key, _, value in pairs if key == 'ticket']
        values = set(self._cookie_values(scope))
        try:
            if len(tickets) != 1 or len(pairs) != 1:
                raise GrantDenied('refused', 'query-shape')
            secret, target, max_age = self.grants.exchange(tickets[0], next(iter(values)) if len(values) == 1 else None)
        except GrantError as exc:
            log.warning('grant exchange refused: reason=%s', getattr(exc, 'reason', 'refused'))
            return await self._deny_enter(scope, send)
        except Exception:
            log.error('grant exchange failed; denying', exc_info=True)
            return await self._deny_enter(scope, send)
        out = [(b'location', target.encode('latin-1')), (b'cache-control', b'no-store'), (b'pragma', b'no-cache'), (b'referrer-policy', b'no-referrer'),
               (b'x-content-type-options', b'nosniff'), (b'content-length', b'0')]
        out.append((b'set-cookie', f'{ENTERED_COOKIE}=1; Path=/; SameSite=Lax; Max-Age=30'.encode('latin-1')))      # a hint, not a credential; the grant cookie follows it
        if secret:
            out.append((b'set-cookie', f'{GRANT_COOKIE}={secret}; Path=/; HttpOnly; SameSite=Lax; Max-Age={max_age}'.encode('latin-1')))
        await send({'type': 'http.response.start', 'status': 303, 'headers': out})
        await send({'type': 'http.response.body', 'body': b''})

    async def __call__(self, scope, receive, send):
        if scope['type'] not in ('http', 'websocket') or not self._matches(scope):
            return await self.app(scope, receive, send)
        if scope['type'] == 'websocket':
            if self.mode != 'gateway':
                await receive()
                return await send({'type': 'websocket.close', 'code': 1008})
            if self.grants is not None and scope['path'] == ENTER_PATH:
                await receive()
                return await send({'type': 'websocket.close', 'code': 1008})
            return await self._websocket(scope, receive, send)
        if self.grants is not None and scope['path'] == ENTER_PATH:
            return await self._enter(scope, receive, send)
        refusal = self._refuse(scope, websocket=False)
        if refusal:
            log.warning('refused %s %s: %s', scope['method'], scope['path'], refusal[1].decode())
            return await self._fail(send, *refusal)
        grant = None
        if self.grants is not None:
            grant = self._authorize(scope)
            if grant is None:
                return await self._fail(send, *self._deny_no_grant(scope))
        if self.local_api is not None and self.mode == 'gateway' and scope['path'].startswith(LOCAL_PREFIX):
            return await self.local_api(scope, receive, send, grant)           # answered here, never forwarded to Hermes
        await self._forward(scope, receive, send, grant)

    def _toward_upstream(self, scope, headers):
        """gateway mode: present the gateway's own bound authority (the browser's values were already validated).
        With pass_host the gateway sees the browser's own Host/Origin/Referer, so its own checks judge the real values."""
        if self.mode != 'gateway' or self.policy.pass_host:
            return headers
        own = ('http://' + self._host(scope)).encode()
        rewritten = []
        for name, value in headers:
            if name == b'host':
                value = self.authority.encode()
            elif name == b'origin':
                value = self.upstream.encode()
            elif name == b'referer' and value.startswith(own):
                value = self.upstream.encode() + value[len(own):]
            rewritten.append((name, value))
        return rewritten

    async def _forward(self, scope, receive, send, grant=None):
        headers = self._toward_upstream(scope, [(n, v) for n, v in scope['headers'] if n not in _HOP_BY_HOP])
        if self.grants is not None:
            headers = self._without_grant_cookie(headers)
        names = {n for n, _ in headers}
        has_body = b'transfer-encoding' in dict(scope['headers']) or (b'content-length' in names and dict(headers)[b'content-length'] != b'0')
        watch = self.grants.watch(grant.key, asyncio.get_running_loop()) if grant else None
        revoked = asyncio.ensure_future(watch.event.wait()) if watch else None

        async def body():
            while True:
                message = await receive()
                if message['type'] == 'http.disconnect':
                    raise ConnectionAbortedError('client disconnected')
                if watch and watch.event.is_set():
                    raise ConnectionAbortedError('grant revoked')
                if message.get('body'):
                    yield message['body']
                if not message.get('more_body'):
                    return

        target = scope['raw_path'].decode('latin-1') if scope.get('raw_path') else scope['path']
        if scope.get('query_string'):
            target += '?' + scope['query_string'].decode('latin-1')
        request = self._http().build_request(scope['method'], target, headers=headers, content=body() if has_body else None)
        watcher = None
        upstream = None
        try:
            pending = asyncio.ensure_future(self._http().send(request, stream=True))
            if revoked is not None:
                await asyncio.wait({pending, revoked}, return_when=asyncio.FIRST_COMPLETED)
                if revoked.done() and not pending.done():
                    pending.cancel()
                    return await self._fail(send, 401, b'Open Hermes from Odysseus (Projects).', [(b'x-odysseus-grant', b'required')])
            try:
                upstream = await pending
            except ConnectionAbortedError:
                return
            except (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout):
                return await self._fail(send, 502, b'Hermes is not running. Open Odysseus and select Hermes Mode to start it.')
            except httpx.HTTPError:
                return await self._fail(send, 502, b'Hermes returned an invalid response.')

            async def watch_disconnect():
                while (await receive())['type'] != 'http.disconnect':
                    pass

            watcher = asyncio.ensure_future(watch_disconnect())
            own_origin = 'http://' + self._host(scope)
            response_headers = []
            for name, value in upstream.headers.raw:
                lowered = name.lower()
                if lowered in _HOP_BY_HOP:
                    continue
                if self.mode == 'gateway' and lowered.startswith(b'access-control-'):
                    continue                         # same-origin only: this proxy grants no cross-origin access
                if self.grants is not None and lowered == b'set-cookie' and value.lstrip().startswith(GRANT_COOKIE.encode() + b'='):
                    continue                         # the gateway can neither set nor clear this proxy's grant cookie
                if lowered == b'location' and value.startswith(self.upstream.encode()):
                    value = own_origin.encode() + value[len(self.upstream):]
                response_headers.append((lowered, value))
            await send({'type': 'http.response.start', 'status': upstream.status_code, 'headers': response_headers})
            if scope['method'] == 'HEAD':
                return await send({'type': 'http.response.body', 'body': b''})
            iterator = upstream.aiter_raw().__aiter__()
            chunk_task = None
            while True:
                if chunk_task is None:
                    chunk_task = asyncio.ensure_future(iterator.__anext__())
                waiting = {chunk_task, watcher} | ({revoked} if revoked is not None else set())
                done, _ = await asyncio.wait(waiting, timeout=2.0 if grant else None, return_when=asyncio.FIRST_COMPLETED)
                if not done:                         # periodic re-validation: the Odysseus session may have ended without a callback
                    if grant and not self.grants.check(grant.key):
                        chunk_task.cancel()
                        return
                    continue
                if (revoked is not None and revoked.done()) or chunk_task not in done:
                    chunk_task.cancel()
                    return
                try:
                    chunk = chunk_task.result()
                except StopAsyncIteration:
                    break
                chunk_task = None
                await send({'type': 'http.response.body', 'body': chunk, 'more_body': True})
            await send({'type': 'http.response.body', 'body': b''})
        finally:
            if watcher is not None:
                watcher.cancel()
            if revoked is not None:
                revoked.cancel()
                self.grants.unwatch(grant.key, watch)
            if upstream is not None:
                await upstream.aclose()

    # ── WebSocket relay (gateway mode) ──────────────────────────────────────────────────────────
    @staticmethod
    async def _ws_refuse(scope, send, status, message):
        if 'websocket.http.response' in (scope.get('extensions') or {}):
            await send({'type': 'websocket.http.response.start', 'status': status,
                        'headers': _ERROR_HEADERS + [(b'content-length', str(len(message)).encode())]})
            return await send({'type': 'websocket.http.response.body', 'body': message})
        await send({'type': 'websocket.close', 'code': 1008})

    async def _websocket(self, scope, receive, send):
        first = await receive()
        if first['type'] != 'websocket.connect':
            return
        refusal = self._refuse(scope, websocket=True)
        if refusal:
            log.warning('refused WebSocket %s: %s', scope['path'], refusal[1].decode())
            return await self._ws_refuse(scope, send, *refusal)
        grant = None
        if self.grants is not None:
            grant = self._authorize(scope)
            if grant is None:
                log.warning('refused WebSocket %s: no live grant', scope['path'])
                return await self._ws_refuse(scope, send, 401, b'Open Hermes from Odysseus (Projects).')
        client = _websockets()
        if client is None:
            log.error('WebSocket support (the websockets package) is not installed; refusing %s', scope['path'])
            return await self._ws_refuse(scope, send, 501, b'WebSocket support is not installed.')
        connect, errors = client
        target = scope['raw_path'].decode('latin-1') if scope.get('raw_path') else scope['path']
        if scope.get('query_string'):
            target += '?' + scope['query_string'].decode('latin-1')
        forwarded = [(n.decode('latin-1'), v.decode('latin-1')) for n, v in self._without_grant_cookie(scope['headers'])
                     if n not in _WS_HANDSHAKE and n not in _HOP_BY_HOP]
        browser_origin = next((v.decode('latin-1') for n, v in scope['headers'] if n == b'origin'), None)
        if self.policy.pass_host:
            authority, origin, where = self._host(scope), browser_origin, {'host': '127.0.0.1', 'port': int(self.authority.rsplit(':', 1)[1])}
        else:
            authority, origin, where = self.authority, (self.upstream if browser_origin is not None else None), {}
        try:
            upstream = await connect(
                f'ws://{authority}{target}', additional_headers=forwarded, origin=origin,
                subprotocols=scope.get('subprotocols') or None, compression=None, max_size=self.policy.ws_max_bytes,
                ping_interval=None, open_timeout=10, close_timeout=5, user_agent_header=None, **where)
        except errors.InvalidStatus as exc:
            body = (exc.response.body or b'')[:2000] or b'Hermes refused the connection.'
            return await self._ws_refuse(scope, send, exc.response.status_code, body)
        except (OSError, asyncio.TimeoutError, errors.WebSocketException):
            return await self._ws_refuse(scope, send, 502, b'Hermes is not running. Open Odysseus and select Hermes Mode to start it.')
        watch = self.grants.watch(grant.key, asyncio.get_running_loop()) if grant else None
        await send({'type': 'websocket.accept', 'subprotocol': upstream.subprotocol})

        async def guard():
            """Ends the relay the moment the grant is revoked, and re-validates every 2 s for endings nobody announced (expiry, a deleted user)."""
            while True:
                try:
                    await asyncio.wait_for(watch.event.wait(), 2.0)
                    return 'revoked', 4401
                except asyncio.TimeoutError:
                    if not self.grants.check(grant.key):
                        return 'revoked', 4401

        async def to_upstream():
            while True:
                event = await receive()
                if event['type'] == 'websocket.disconnect':
                    return 'client', event.get('code', 1005)
                if event['type'] != 'websocket.receive':
                    continue
                if event.get('text') is not None:
                    await upstream.send(event['text'])
                elif event.get('bytes') is not None:
                    await upstream.send(event['bytes'])

        async def to_client():
            try:
                while True:
                    message = await upstream.recv()
                    if isinstance(message, str):
                        await send({'type': 'websocket.send', 'text': message})
                    else:
                        await send({'type': 'websocket.send', 'bytes': bytes(message)})
            except errors.ConnectionClosed:
                return 'upstream', None

        tasks = {asyncio.ensure_future(to_upstream()), asyncio.ensure_future(to_client())}
        if watch is not None:
            tasks.add(asyncio.ensure_future(guard()))
        side, code = 'error', 1011
        try:
            done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
            for task in done:
                if task.exception() is None:
                    side, code = task.result()
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            try:
                if side == 'client':
                    await upstream.close(_close_code(code))
                elif side == 'upstream':
                    reason = _close_reason(upstream.close_reason)
                    await send({'type': 'websocket.close', 'code': _close_code(upstream.close_code), 'reason': reason})
                elif side == 'revoked':
                    await send({'type': 'websocket.close', 'code': 4401, 'reason': 'Odysseus session ended'})
                    await upstream.close(1000, 'Odysseus session ended')
                else:
                    await upstream.close(1011)
                    await send({'type': 'websocket.close', 'code': 1011})
            except Exception:                      # the other side is already gone; nothing more to tell it
                log.debug('WebSocket close relay failed', exc_info=True)
            finally:
                await upstream.close()
                if watch is not None:
                    self.grants.unwatch(grant.key, watch)


_resolution = {'ok': None, 'checked': 0.0}


def hostname_resolves():
    """Whether this OS resolver maps hermes.localhost to loopback; cached briefly."""
    now = time.monotonic()
    if _resolution['ok'] is None or (not _resolution['ok'] and now - _resolution['checked'] > 30):
        try:
            _resolution['ok'] = any(info[4][0] in _LOOPBACK for info in socket.getaddrinfo(PROXY_HOST, None))
        except OSError:
            _resolution['ok'] = False
        _resolution['checked'] = now
    return _resolution['ok']


def build_grant_store(app, environment):
    """The grant store for gateway mode, or None when it cannot be built (the caller then does not install the proxy: Projects stays closed)."""
    import os
    from src.hermes_grants import DEFAULT_GRANT_TTL, GrantStore, GrantStoreError
    auth = getattr(app.state, 'auth_manager', None)
    hours = environment.get('ODYSSEUS_HERMES_GRANT_TTL_HOURS')
    if auth is None or not getattr(auth, 'auth_path', None) or not hasattr(auth, 'lookup_session_digest'):
        log.error('Hermes proxy not installed: there is no Odysseus session manager to bind access grants to')
        return None
    if hours is not None and not (hours.isdigit() and 1 <= int(hours) <= 168):
        log.error('Hermes proxy not installed: ODYSSEUS_HERMES_GRANT_TTL_HOURS must be a whole number from 1 to 168')
        return None
    try:
        store = GrantStore(os.path.join(os.path.dirname(auth.auth_path), 'hermes-grants.db'), auth.lookup_session_digest,
                           grant_ttl=int(hours) * 3600 if hours else DEFAULT_GRANT_TTL)
    except GrantStoreError as exc:
        log.error('Hermes proxy not installed: %s', exc)
        return None
    auth.add_revoke_listener(store.sessions_revoked)
    return store


def install_hermes_proxy(app, service, environment=None, local_api=None):
    """Install as the outermost middleware so Odysseus auth, CSP and compression never touch Hermes traffic."""
    import os
    source = os.environ if environment is None else environment
    if service.config is None or source.get('ODYSSEUS_HERMES_PROXY') == '0':
        return False
    mode = source.get('ODYSSEUS_HERMES_PROXY_MODE') or ('gateway' if service.config.backend == 'gateway' else 'webui')
    try:
        if mode not in MODES:
            raise ProxyConfigError(f'ODYSSEUS_HERMES_PROXY_MODE must be one of {MODES}')
        policy = ProxyPolicy.from_environment(source)
    except ProxyConfigError as exc:
        log.error('Hermes proxy not installed: %s', exc)
        return False
    grants = None
    if mode == 'gateway':
        if source.get('ODYSSEUS_HERMES_PROXY_GRANTS') == '0':
            log.warning('Projects access grants are DISABLED: Hermes requests are not tied to an Odysseus session and Odysseus logout does not end them')
        else:
            grants = build_grant_store(app, source)
            if grants is None:
                return False
    app.add_middleware(HermesHostProxy, upstream=service.config.url, mode=mode, policy=policy, grants=grants, local_api=local_api)
    if mode == 'gateway':
        redact_request_logs()
    service.grants = grants
    service.proxy = True
    return True
