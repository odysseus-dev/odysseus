/**
 * god.htp.sh — custom router.
 *
 * Replaces the bare upstream 404 with an actual application surface. Serves
 * the built dashboards under stable paths, proxies the gateway and OSINT
 * routes upstream, and answers everything else with a real 404 page that
 * lists what IS available — instead of relaying a blank 404 that gives the
 * caller no way to discover the surface.
 *
 * Deployed with a route on god.htp.sh only. It does not touch htp.sh root,
 * /ai or /osint, which are served by still-firefly-7b67ebk.
 */

const ORIGIN = "https://htp.sh";
const UPSTREAM_ORIGIN = ORIGIN;

/** Canonical path of the God's Eye viewer on the upstream. */
const VIEWER_PATH = "/osint/gods-eye";

/** Upstream paths this router forwards rather than serving itself. */
const PROXY_PREFIXES = ["/ai", "/osint", "/chat/api"];

/**
 * Aliases under /god/... that reach the God's Eye viewer. These are served on
 * god.htp.sh and, through the zone routes htp.sh/god/osint/* and
 * htp.sh/god/os/int/gods-eye-viewer, on the htp.sh apex as well.
 */
const GOD_VIEWER_ALIASES = ["/god/osint/gods-eye-viewer", "/god/os/int/gods-eye-viewer"];
const GOD_VIEWER_PATH = "/god/osint/gods-eye";

/**
 * Odysseus app, reached through a Cloudflare Tunnel origin hostname. The
 * /app prefix is stripped before forwarding. Target comes from env.APP_UPSTREAM.
 */
const APP_PREFIX = "/app";
const APP_UPSTREAM_DEFAULT = "https://odysseus-origin.htp.sh";

const PAGES = {
  "/": {
    title: "GOD.htp.sh",
    desc: "Routing surface for the htp.sh gateway and intelligence tooling.",
    body: `
      <div class="grid">
        <a class="card" href="/dashboard"><h3>Gateway Control</h3>
          <p>Workers, upstream pool, routing state, agent tool surface.</p></a>
        <a class="card" href="/lattice"><h3>E8 Root Lattice</h3>
          <p>240 roots, 120 nodes, 3360 edges. Live animated projection.</p></a>
        <a class="card" href="/ai"><h3>MCP Gateway</h3>
          <p>Route map, capability discovery, health. Proxied upstream.</p></a>
        <a class="card" href="/osint/gods-eye"><h3>God's Eye Viewer</h3>
          <p>Photorealistic 3D globe and GEOINT console. Canonical app.</p></a>
        <a class="card" href="/osint"><h3>OSINT</h3>
          <p>Visual GEOINT geolocation. Proxied upstream.</p></a>
        <a class="card" href="/website"><h3>Website</h3>
          <p>Project site mirror. Served from static assets.</p></a>
      </div>`,
  },
  "/dashboard": { file: "dashboard.html", title: "Gateway Control" },
  "/lattice": { file: "e8-lattice.html", title: "E8 · Live Root Lattice" },
};

const NOT_FOUND = (path) => `<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>404 · god.htp.sh</title>
<style>
 body{margin:0;background:#05080c;color:#d6e4f0;
      font:13px/1.7 ui-monospace,SFMono-Regular,Menlo,monospace;
      display:grid;place-items:center;min-height:100vh}
 .b{max-width:640px;padding:28px}
 h1{color:#ff6b8a;font-size:15px;letter-spacing:.2em;margin:0 0 6px}
 code{color:#38e0c8}
 p{color:#5f7788}
 ul{color:#5f7788;padding-left:20px}
 a{color:#38e0c8}
</style></head><body><div class="b">
<h1>404 · NOT FOUND</h1>
<p>No route matches <code>${escapeHtml(path)}</code> on god.htp.sh.</p>
<p>Available:</p>
<ul>
  <li><a href="/">/</a> — router index</li>
  <li><a href="/dashboard">/dashboard</a> — gateway control</li>
  <li><a href="/lattice">/lattice</a> — E8 root lattice</li>
  <li><a href="/osint/gods-eye">/osint/gods-eye</a> — God's Eye viewer (redirects to canonical)</li>
  <li><code>/ai</code> — MCP gateway (proxied upstream)</li>
  <li><code>/osint</code> — OSINT (proxied upstream)</li>
  <li><a href="/gods-eye">/gods-eye</a> — God's Eye 3D globe (short redirect)</li>
  <li><a href="/website">/website</a> — project site mirror</li>
</ul>
<p>API callers should fetch <code>/ai</code> for the machine-readable route map.</p>
</div></body></html>`;

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

const INDEX_STYLE = `<style>
 body{margin:0;background:#05080c;color:#d6e4f0;
      font:13px/1.7 ui-monospace,SFMono-Regular,Menlo,monospace;
      min-height:100vh;display:flex;flex-direction:column}
 header{padding:22px 26px;border-bottom:1px solid #16222f}
 h1{margin:0;font-size:14px;letter-spacing:.24em;color:#38e0c8}
 header p{margin:6px 0 0;color:#5f7788}
 main{padding:26px;flex:1}
 .grid{display:grid;gap:14px;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));
       max-width:900px}
 .card{border:1px solid #16222f;background:#0a1018;padding:16px;text-decoration:none;
       color:inherit;transition:border-color .15s}
 .card:hover{border-color:#38e0c8}
 .card h3{margin:0 0 6px;font-size:12px;letter-spacing:.1em;color:#38e0c8}
 .card p{margin:0;color:#5f7788;font-size:12px}
 footer{padding:14px 26px;border-top:1px solid #16222f;color:#5f7788}
</style>`;

const INDEX = (title, desc, body) => `<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>${escapeHtml(title)}</title>
<meta name="description" content="${escapeHtml(desc)}">${INDEX_STYLE}</head>
<body>
<header><h1>${escapeHtml(title).toUpperCase()}</h1><p>${escapeHtml(desc)}</p></header>
<main>${body}</main>
<footer>Router for htp.sh · upstream <code>${ORIGIN}</code></footer>
</body></html>`;

/** Documents served from the Workers Static Assets binding. */
const DOCS = {
  "/dashboard": "dashboard.html",
  "/lattice": "e8-lattice.html",
};

/**
 * Subtrees served from Static Assets by URL prefix.
 *
 * Key is the prefix as the router normalises it (no trailing slash); value is
 * the directory under public/ that backs it. A request for the prefix itself
 * resolves to index.html, so typing /website lands on the site rather than a
 * directory listing or a 404.
 *
 * This mirrors the repository's website/ directory, which is also served from
 * hackdeplanet Pages. Two copies means one can drift; keep them in sync.
 */
const ASSET_TREES = {
  "/website": "website",
};

/**
 * Turn a successful asset response into the router's own response.
 *
 * Returns null on a miss so the caller can fall through to the 404 page rather
 * than relaying the asset server's bare miss.
 */
function serveAsset(assetRes, method) {
  if (!assetRes.ok) return null;
  const headers = new Headers(assetRes.headers);
  headers.set("Cache-Control", "public, max-age=60");
  headers.set("Access-Control-Allow-Origin", "*");
  headers.set("X-Router", "god.htp.sh");
  return new Response(method === "HEAD" ? null : assetRes.body, {
    status: 200,
    headers,
  });
}

const HOP_BY_HOP = new Set([
  "connection", "keep-alive", "transfer-encoding", "upgrade",
  "proxy-authenticate", "proxy-authorization", "te", "trailer",
]);

/**
 * Build a 302 for every redirect this router emits.
 *
 * Caching is the reason this is centralised. A 302 with no Cache-Control is
 * heuristically cacheable, so a browser can keep replaying a stale target
 * after the viewer path moves — the failure is invisible until someone
 * hard-reloads. The short max-age bounds that, and the CORS header keeps
 * fetch() callers from tripping on an opaque redirect response.
 */
function redirect(location) {
  return new Response(null, {
    status: 302,
    headers: {
      Location: location,
      "Access-Control-Allow-Origin": "*",
      "Cache-Control": "public, max-age=60",
      "X-Router": "god.htp.sh",
    },
  });
}

export default {
  async fetch(request, env, ctx) {
    const url = new URL(request.url);
    const path = url.pathname.replace(/\/+$/, "") || "/";

    if (request.method === "OPTIONS") {
      return new Response(null, {
        status: 204,
        headers: {
          "Access-Control-Allow-Origin": "*",
          "Access-Control-Allow-Methods": "GET, HEAD, POST, PUT, PATCH, DELETE, OPTIONS",
          "Access-Control-Allow-Headers": request.headers.get("Access-Control-Request-Headers") || "Content-Type, Authorization",
          "Access-Control-Max-Age": "600",
        },
      });
    }

    // Explicit health, so a monitor does not have to scrape HTML.
    if (path === "/healthz") {
      return Response.json(
        { ok: true, router: "god.htp.sh", upstream: env.UPSTREAM || ORIGIN },
        {
          headers: {
            "Access-Control-Allow-Origin": "*",
            "Cache-Control": "no-store",
            // Every other route the router answers carries this marker. Without
            // it /healthz is indistinguishable from an asset-server or upstream
            // response, which makes the marker useless for telling where a
            // response came from.
            "X-Router": "god.htp.sh",
          },
        },
      );
    }

    const doc = DOCS[path];
    if (doc && (request.method === "GET" || request.method === "HEAD")) {
      const assetRes = await env.ASSETS.fetch(new Request(new URL(`/${doc}`, url)));
      const served = serveAsset(assetRes, request.method);
      if (served) return served;
    }

    if (request.method === "GET" || request.method === "HEAD") {
      for (const [prefix, dir] of Object.entries(ASSET_TREES)) {
        if (path !== prefix && !path.startsWith(`${prefix}/`)) continue;
        // url.pathname is already normalised by the URL parser, so `..` cannot
        // survive into `rel`; the explicit reject is belt-and-braces against a
        // future refactor that reassembles the path from raw input.
        const rel = path.slice(prefix.length).replace(/^\/+/, "") || "index.html";
        if (rel.includes("..")) break;
        const assetRes = await env.ASSETS.fetch(new Request(new URL(`/${dir}/${rel}`, url)));
        const served = serveAsset(assetRes, request.method);
        // A miss inside a known tree falls through to the 404 page, which
        // tells the caller what does exist instead of relaying a bare miss.
        if (served) return served;
        break;
      }
    }

    if (path === "/" && (request.method === "GET" || request.method === "HEAD")) {
      const page = PAGES["/"];
      const html = INDEX(page.title, page.desc, page.body);
      return new Response(request.method === "HEAD" ? null : html, {
        headers: {
          "Content-Type": "text/html; charset=utf-8",
          "Cache-Control": "public, max-age=60",
          "X-Router": "god.htp.sh",
        },
      });
    }

    // The God's Eye viewer is served by the htp-osint-maps worker, which 404s any
    // Worker-originated subrequest — verified by probing from this Worker:
    // /ai and /osint proxy fine, /osint/gods-eye returns a bare workerd 404
    // regardless of User-Agent or fetch headers. So it cannot be reverse
    // proxied; redirect to the canonical URL instead, which always works and
    // keeps the app in a real top-level browsing context (an iframe would hit
    // framing restrictions on a full-screen 3D app).
    //
    // The Location is deliberately ABSOLUTE and points at the upstream origin.
    // Rewriting it to be origin-relative would send the browser back to this
    // router, which cannot serve the app — an infinite 302 loop.
    // /god/... aliases for the viewer (also reached on the htp.sh apex).
    if (GOD_VIEWER_ALIASES.includes(path)) {
      return redirect(`${UPSTREAM_ORIGIN}${VIEWER_PATH}/${url.search}`);
    }
    if (path === GOD_VIEWER_PATH || path.startsWith(`${GOD_VIEWER_PATH}/`)) {
      const suffix = url.pathname.slice(GOD_VIEWER_PATH.length) + url.search;
      return redirect(
        `${UPSTREAM_ORIGIN}${VIEWER_PATH}/${suffix.replace(/^\/+/, "")}`,
      );
    }

    // Odysseus app via tunnel: all methods, streamed bodies, WebSockets.
    if (path === APP_PREFIX || path.startsWith(`${APP_PREFIX}/`)) {
      return proxyApp(request, url, env.APP_UPSTREAM || APP_UPSTREAM_DEFAULT);
    }

    if (path === VIEWER_PATH || path.startsWith(`${VIEWER_PATH}/`)) {
      const suffix = url.pathname.slice(VIEWER_PATH.length) + url.search;
      return redirect(
        `${UPSTREAM_ORIGIN}${VIEWER_PATH}/${suffix.replace(/^\/+/, "")}`,
      );
    }

    // "-viewer" is a common misremembering and does not exist upstream.
    if (path === "/osint/gods-eye-viewer") {
      return redirect(VIEWER_PATH);
    }

    // Short entry point for the same app. Points straight at the upstream
    // canonical URL rather than at VIEWER_PATH: going via the relative path
    // would cost a second 302 hop on the one route people type by hand.
    if (path === "/gods-eye") {
      return redirect(`${UPSTREAM_ORIGIN}${VIEWER_PATH}/`);
    }

    // Forward the gateway and OSINT surface upstream.
    if (PROXY_PREFIXES.some((p) => path === p || path.startsWith(`${p}/`))) {
      return proxyUpstream(request, url, env.UPSTREAM || ORIGIN);
    }

    // Anything else: a real 404 page, not a relayed blank one.
    return new Response(NOT_FOUND(path), {
      status: 404,
      headers: {
        "Content-Type": "text/html; charset=utf-8",
        "X-Router": "god.htp.sh",
        "Cache-Control": "no-store",
      },
    });
  },
};

/**
 * Headers that must not be replayed to the upstream.
 *
 * Host is the critical one: forwarding `god.htp.sh` upstream makes Cloudflare
 * match the zone's `*.htp.sh/*` wildcard, which routes to still-firefly-7b67ebk
 * instead of htp-osint-maps — so every proxied path 404s even though it works
 * when requested directly. Verified: the same URL returns 200 normally and 404
 * with a foreign Host header.
 */
const REQUEST_STRIP = new Set([
  "host", "content-length", "accept-encoding",
  "cf-connecting-ip", "cf-ray", "cf-ipcountry", "cf-visitor",
  "cf-ray", "x-forwarded-for", "x-forwarded-proto", "x-forwarded-host",
]);

async function proxyUpstream(request, url, upstream) {
  const target = new URL(url.toString());
  const base = new URL(upstream);
  target.protocol = base.protocol;
  target.host = base.host;

  const headers = new Headers();
  for (const [k, v] of request.headers) {
    if (REQUEST_STRIP.has(k.toLowerCase())) continue;
    headers.set(k, v);
  }
  headers.set("X-Router", "god.htp.sh");

  const res = await fetch(new Request(target.toString(), {
    method: request.method,
    headers,
    body: request.method === "GET" || request.method === "HEAD" ? undefined : request.body,
    redirect: "manual",
  }));

  const out = new Headers();
  for (const [k, v] of res.headers) {
    if (!HOP_BY_HOP.has(k.toLowerCase())) out.set(k, v);
  }
  out.set("X-Router", "god.htp.sh");
  out.set("Access-Control-Allow-Origin", "*");

  // A 404 from upstream is a bare "Not Found" that tells the caller nothing.
  // Substitute the router's own page, keeping the status, so a miss inside a
  // proxied prefix still tells the requester what does exist.
  if (res.status === 404) {
    return new Response(NOT_FOUND(url.pathname), {
      status: 404,
      headers: {
        "Content-Type": "text/html; charset=utf-8",
        "X-Router": "god.htp.sh",
        "Cache-Control": "no-store",
      },
    });
  }

  return new Response(res.body, {
    status: res.status,
    statusText: res.statusText,
    headers: out,
  });
}

/**
 * Proxy /app/* to the Odysseus tunnel origin with the prefix stripped.
 *
 * Bodies are streamed in both directions (no buffering), every method is
 * passed through, and WebSocket upgrades are handed to fetch() with the
 * Upgrade header intact so the runtime returns the 101 + webSocket pair.
 */
async function proxyApp(request, url, upstream) {
  const base = new URL(upstream);
  const target = new URL(url.toString());
  target.protocol = base.protocol;
  target.host = base.host;
  target.pathname = url.pathname.slice(APP_PREFIX.length) || "/";

  if ((request.headers.get("Upgrade") || "").toLowerCase() === "websocket") {
    return fetch(new Request(target.toString(), request));
  }

  const headers = new Headers();
  for (const [k, v] of request.headers) {
    if (REQUEST_STRIP.has(k.toLowerCase())) continue;
    headers.set(k, v);
  }
  headers.set("X-Forwarded-Host", url.host);
  headers.set("X-Forwarded-Prefix", APP_PREFIX);
  headers.set("X-Router", "god.htp.sh");

  const hasBody = !(request.method === "GET" || request.method === "HEAD");
  const res = await fetch(target.toString(), {
    method: request.method,
    headers,
    body: hasBody ? request.body : undefined,
    redirect: "manual",
  });

  const out = new Headers();
  for (const [k, v] of res.headers) {
    if (!HOP_BY_HOP.has(k.toLowerCase())) out.set(k, v);
  }
  // Keep app-relative redirects inside /app.
  const loc = out.get("Location");
  if (loc && loc.startsWith("/")) out.set("Location", APP_PREFIX + loc);
  out.set("X-Router", "god.htp.sh");
  return new Response(res.body, { status: res.status, statusText: res.statusText, headers: out });
}
