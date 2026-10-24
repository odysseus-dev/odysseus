/**
 * odysseus-mcp-gateway — edge round-robin proxy for MCP API gateways.
 *
 * Mirrors the failure semantics of the Python pool in `src/mcp_gateway.py` so a
 * request that fails over here and one that fails over there behave the same:
 *
 *   success      -> clear cooldown + strikes, un-mark dead, track latency
 *   5xx / 429    -> exponential backoff (base * 2^(strikes-1)), capped
 *   >= 502       -> mark dead, removed from rotation until revived
 *   other 4xx    -> passthrough with NO health penalty (caller's fault)
 *
 * State is per-isolate and in-memory. That is a deliberate trade: a cooldown
 * that outlives isolate eviction would need a KV write on every failure, and
 * this proxy only needs *some* rotation and local failure memory. A revived
 * isolate re-learns within a few requests. See "Known gaps" in the spec.
 */

const DEFAULTS = {
  timeoutMs: 30000,
  cooldownBaseMs: 60000,
  cooldownMaxMs: 3600000,
};

/** Per-isolate routing state, keyed by upstream base URL. */
const state = {
  /** @type {Map<string, {strikes:number, cooldownUntil:number, dead:boolean, ok:number, fail:number, lastError:string}>} */
  endpoints: new Map(),
  cursor: 0,
};

function config(env) {
  const num = (v, d) => {
    const n = Number.parseInt(v ?? "", 10);
    return Number.isFinite(n) && n > 0 ? n : d;
  };
  return {
    pool: (env.GATEWAY_POOL || "https://htp.sh/ai")
      .split(",")
      .map((s) => s.trim().replace(/\/+$/, ""))
      .filter(Boolean),
    timeoutMs: num(env.UPSTREAM_TIMEOUT_MS, DEFAULTS.timeoutMs),
    cooldownBaseMs: num(env.COOLDOWN_BASE_MS, DEFAULTS.cooldownBaseMs),
    cooldownMaxMs: num(env.COOLDOWN_MAX_MS, DEFAULTS.cooldownMaxMs),
  };
}

export function recordFor(url) {
  let rec = state.endpoints.get(url);
  if (!rec) {
    rec = { strikes: 0, cooldownUntil: 0, dead: false, ok: 0, fail: 0, lastError: "" };
    state.endpoints.set(url, rec);
  }
  return rec;
}

export function recordOk(rec) {
  rec.ok += 1;
  rec.strikes = 0;
  rec.cooldownUntil = 0;
  rec.dead = false;
  rec.lastError = "";
}

export function recordFail(rec, cfg, error, terminal = false) {
  rec.fail += 1;
  rec.lastError = String(error).slice(0, 400);
  if (terminal) {
    rec.dead = true;
    return;
  }
  rec.strikes += 1;
  const backoff = Math.min(
    cfg.cooldownBaseMs * 2 ** (rec.strikes - 1),
    cfg.cooldownMaxMs
  );
  rec.cooldownUntil = Date.now() + backoff;
}

export function isAvailable(rec, now = Date.now()) {
  return !rec.dead && rec.cooldownUntil <= now;
}

/** Next available upstream, round-robin over the routable set. */
export function acquire(pool, now = Date.now()) {
  const routable = pool.filter((u) => isAvailable(recordFor(u), now));
  if (!routable.length) return null;
  routable.sort();
  const idx = state.cursor % routable.length;
  state.cursor = (idx + 1) % routable.length;
  return routable[idx];
}

export function resetState() {
  state.endpoints.clear();
  state.cursor = 0;
}

/**
 * Peer workers shown on the dashboard. Excludes this worker itself, so the
 * dashboard reports the others rather than probing its own isolate.
 */
const PEER_WORKERS = Object.freeze([
  {
    name: "odysseus-edge-optimizer",
    url: "https://odysseus-edge-optimizer.doxbin.workers.dev",
  },
]);


/** CORS for the read-only dashboard endpoints, which the page calls directly. */
function corsJson(body, status = 200) {
  return new Response(JSON.stringify(body, null, 2), {
    status,
    headers: {
      "Content-Type": "application/json",
      "Access-Control-Allow-Origin": "*",
      "Cache-Control": "no-store",
    },
  });
}

const HOP_BY_HOP = new Set([
  "connection", "keep-alive", "transfer-encoding", "upgrade",
  "proxy-authenticate", "proxy-authorization", "te", "trailer",
]);

// Headers that describe the *inbound* connection and must not be replayed to
// the upstream. Forwarding Host breaks virtual-host routing (the upstream sees
// the proxy's Host, not its own) and Content-Length/Accept-Encoding mismatch
// the rewritten target, which hangs until timeout.
const REQUEST_STRIP = new Set(["host", "content-length", "accept-encoding"]);

function upstreamHeaders(request) {
  const headers = new Headers();
  for (const [k, v] of request.headers) {
    const lower = k.toLowerCase();
    if (HOP_BY_HOP.has(lower) || REQUEST_STRIP.has(lower)) continue;
    headers.set(k, v);
  }
  headers.set("X-Odysseus-Gateway", "odysseus-mcp-gateway");
  return headers;
}

/**
 * Join an upstream base URL with the incoming path.
 *
 * Built from scratch rather than by mutating `new URL(request.url)`: mutating
 * leaks the proxy's own port into the upstream host (so the Worker dials
 * `htp.sh:8787` and hangs), and naively concatenating repeats a shared prefix
 * (`/ai` + `/ai/tools` -> `/ai/ai/tools`).
 *
 * When the incoming path already starts with the base path, the base is
 * treated as an origin prefix rather than concatenated.
 */
export function buildTarget(upstream, requestUrl) {
  const base = new URL(upstream);
  const incoming = new URL(requestUrl);
  const basePath = base.pathname.replace(/\/+$/, "");
  let path = incoming.pathname;
  if (basePath && (path === basePath || path.startsWith(`${basePath}/`))) {
    path = path.slice(basePath.length) || "/";
  }
  const joined = `${basePath}${path}`.replace(/\/{2,}/g, "/") || "/";
  return `${base.origin}${joined}${incoming.search}`;
}

/** Forward a request to the selected upstream, failing over on failure. */
async function forward(request, upstream, cfg, signal) {
  const target = buildTarget(upstream, request.url);
  const headers = upstreamHeaders(request);

  const res = await fetch(target, {
    method: request.method,
    headers,
    body: request.method === "GET" || request.method === "HEAD" ? undefined : request.body,
    redirect: "manual",
    signal,
  });
  return res;
}

/** Rebuild a response for the client, dropping hop-by-hop headers. */
function relay(res, upstream) {
  const out = new Headers();
  for (const [k, v] of res.headers) {
    if (!HOP_BY_HOP.has(k.toLowerCase())) out.set(k, v);
  }
  out.set("X-Gateway-Upstream", upstream);
  return new Response(res.body, { status: res.status, statusText: res.statusText, headers: out });
}

function json(body, status = 200) {
  return new Response(JSON.stringify(body, null, 2), {
    status,
    headers: { "content-type": "application/json; charset=utf-8" },
  });
}

export default {
  async fetch(request, env, ctx) {
    const cfg = config(env);
    const url = new URL(request.url);


    // ---- dashboard --------------------------------------------------------
    // Aggregated view of the deployed workers plus the upstream pool.
    // Read-only: it fans out to peer workers and the upstreams and returns
    // their status without proxying any response body.
    if (url.pathname === "/__gateway/dashboard") {
      const probeUpstream = async (base) => {
        const started = Date.now();
        try {
          const res = await fetch(`${base}/ai/health`, {
            signal: AbortSignal.timeout(8000),
          });
          return {
            base_url: base,
            ok: res.ok,
            status: res.status,
            latencyMs: Date.now() - started,
          };
        } catch (err) {
          return {
            base_url: base,
            ok: false,
            status: 0,
            error: String((err && err.name) || err),
          };
        }
      };

      return corsJson({
        gateway: {
          pool: cfg.pool,
          routable: cfg.pool.filter((u) => isAvailable(recordFor(u))),
          endpoints: cfg.pool.map((u) => ({ base_url: u, ...recordFor(u) })),
        },
        // Peers are intentionally absent: see PEER_WORKERS. The page probes them.
        peers: PEER_WORKERS,
        upstream: await Promise.all(cfg.pool.map(probeUpstream)),
        // Attach the origin: recordFor() keys on the URL but does not store
        // it, so mapping over pool without re-adding it loses the address.
        routing: cfg.pool.map((u) => ({ base_url: u, ...recordFor(u) })),
        colo: (request.cf && request.cf.colo) || null,
        country: (request.cf && request.cf.country) || null,
      });
    }

    /**
 * Gateway manifest.
 *
 * Served so this Worker can be an origin in its own pool, and so any gateway
 * consumer can discover routes without hardcoding them. `status` is "local"
 * unless every pooled upstream is routable AND responding — it reports the
 * pool's real health rather than asserting an upstream that is down.
 */
if (url.pathname === "/ai" || url.pathname === "/ai/") {
  const now = Date.now();
  const routable = cfg.pool.filter((u) => isAvailable(recordFor(u), now));
  return corsJson({
    status: routable.length === cfg.pool.length && cfg.pool.length ? "ok" : "local",
    gateway: new URL(request.url).host,
    message:
      routable.length === cfg.pool.length && cfg.pool.length
        ? "all pooled gateways routable"
        : "no pooled upstream is routable",
    endpoints: {
      models: "/ai/models",
      chat: "/chat/api/ask",
      tools: "/ai/tools",
      compose: "/ai/compose",
      route: "/ai/route",
      // Advertised so mcp_gateway_transport.build_tools() can synthesise the
      // osint_analyze tool. Without this key the transport never creates it and
      // OSINT is reachable only through the hardcoded ANALYZE_PATH fallback in
      // src/agent_tools/osint_tools.py. The route is served upstream by the
      // OSINT worker, not here — this manifest is a discovery map, and the
      // gateway is itself in the pool that resolves the path.
      osint_analyze: "/osint/api/analyze",
      capabilities: "/ai/capabilities",
      abilities: "/api/abilities",
      health: "/ai/health",
      metrics: "/ai/metrics",
      vitals: "/api/vitals",
      mcp_resources: "/ai/mcp/resources",
      mcp_read: "/ai/mcp/resources/read",
    },
    pool: cfg.pool,
    routable,
    colo: (request.cf && request.cf.colo) || null,
  });
}

if (url.pathname === "/__gateway/health") {
      const now = Date.now();
      return corsJson({
        worker: "odysseus-mcp-gateway",
        pool: cfg.pool,
        routable: cfg.pool.filter((u) => isAvailable(recordFor(u), now)),
        endpoints: cfg.pool.map((u) => {
          const r = recordFor(u);
          return {
            base_url: u,
            dead: r.dead,
            cooling: r.cooldownUntil > now,
            strikes: r.strikes,
            ok: r.ok,
            fail: r.fail,
            last_error: r.lastError,
          };
        }),
      });
    }

    if (request.method === "OPTIONS") {
      return new Response(null, { status: 204 });
    }

    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), cfg.timeoutMs);
    let lastError = null;

    try {
      // Two attempts: one endpoint, then the next. Matches the Python pool's
      // default so a single flaky upstream does not surface as a user error.
      for (let attempt = 0; attempt < 2; attempt++) {
        const upstream = acquire(cfg.pool);
        if (!upstream) {
          return json(
            {
              error: "gateway_exhausted",
              message: "no gateway endpoint available; all are cooling or dead",
              pool: cfg.pool,
            },
            503
          );
        }
        const rec = recordFor(upstream);

        try {
          const res = await forward(request, upstream, cfg, controller.signal);

          // 5xx/429 are the gateway's fault -> cool it down. Other 4xx are the
          // caller's -> surface them untouched so a bad request does not eject
          // a healthy upstream from the pool.
          if (res.status >= 500 || res.status === 429) {
            recordFail(rec, cfg, `HTTP ${res.status}`, res.status >= 502);
            lastError = new Error(`HTTP ${res.status}`);
            ctx.waitUntil(Promise.resolve());
            continue;
          }
          recordOk(rec);
          return relay(res, upstream);
        } catch (err) {
          recordFail(rec, cfg, err?.name === "AbortError" ? "timeout" : String(err));
          lastError = err;
        }
      }

      return json(
        {
          error: "gateway_exhausted",
          message: `all upstream attempts failed: ${lastError}`,
          pool: cfg.pool,
        },
        502
      );
    } finally {
      clearTimeout(timer);
    }
  },
};