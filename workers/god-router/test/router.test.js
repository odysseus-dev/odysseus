import { beforeEach, describe, it, expect, vi } from "vitest";
import worker from "../src/index.js";

function assetResponse(body = "<html>doc</html>", status = 200) {
  return new Response(body, {
    status,
    headers: { "content-type": "text/html; charset=utf-8" },
  });
}

function req(path, init) {
  return new Request(`https://god.htp.sh${path}`, init);
}

// Fresh ENV per test: sharing one object leaked the ASSETS mock between
// tests and made results depend on execution order.
let ENV;

beforeEach(() => {
  ENV = {
    UPSTREAM: "https://htp.sh",
    ASSETS: { fetch: vi.fn(async () => assetResponse()) },
  };
});

function withAssets(impl) {
  ENV.ASSETS.fetch = impl ?? vi.fn(async () => assetResponse());
}

describe("router", () => {
  it("serves an index at /", async () => {
    const res = await worker.fetch(req("/"), ENV, {});
    expect(res.status).toBe(200);
    const body = await res.text();
    expect(body).toContain("GOD.HTP.SH");
    expect(body).toContain("/dashboard");
    expect(body).toContain("/lattice");
  });

  it("marks its own responses with X-Router", async () => {
    const res = await worker.fetch(req("/"), ENV, {});
    expect(res.headers.get("X-Router")).toBe("god.htp.sh");
  });

  it("serves the dashboard from static assets", async () => {
    withAssets(async () => assetResponse("<html>dashboard</html>"));
    const res = await worker.fetch(req("/dashboard"), ENV, {});
    expect(res.status).toBe(200);
    expect(await res.text()).toContain("dashboard");
  });

  it("serves the lattice from static assets", async () => {
    withAssets(async () => assetResponse("<html>lattice</html>"));
    const res = await worker.fetch(req("/lattice"), ENV, {});
    expect(res.status).toBe(200);
    expect(await res.text()).toContain("lattice");
  });

  it("HEAD returns no body but the headers", async () => {
    withAssets();
    const res = await worker.fetch(req("/dashboard", { method: "HEAD" }), ENV, {});
    expect(res.status).toBe(200);
    expect(res.headers.get("X-Router")).toBe("god.htp.sh");
  });

  it("answers /healthz without touching assets or upstream", async () => {
    const assets = ENV.ASSETS.fetch;
    const res = await worker.fetch(req("/healthz"), ENV, {});
    expect(res.status).toBe(200);
    expect((await res.json()).ok).toBe(true);
    expect(assets).not.toHaveBeenCalled();
    // Every route the router answers carries the marker, so a response source
    // can be identified from headers alone.
    expect(res.headers.get("X-Router")).toBe("god.htp.sh");
  });

  it("CORS preflights OPTIONS", async () => {
    const res = await worker.fetch(req("/ai", { method: "OPTIONS" }), ENV, {});
    expect(res.status).toBe(204);
    expect(res.headers.get("Access-Control-Allow-Origin")).toBe("*");
  });
});

describe("404 handling", () => {
  it("returns a real 404 page, not a relayed blank one", async () => {
    const res = await worker.fetch(req("/nothing-here"), ENV, {});
    expect(res.status).toBe(404);
    const body = await res.text();
    expect(body).toContain("NOT FOUND");
    expect(body).toContain("/dashboard");
    // The point of the page: a caller can discover the surface.
    expect(body).toContain("/lattice");
  });

  it("redirects the common -viewer misremembering to the real path", async () => {
    const res = await worker.fetch(req("/osint/gods-eye-viewer"), ENV, {});
    expect(res.status).toBe(302);
    expect(res.headers.get("Location")).toBe("/osint/gods-eye");
  });

  it("escapes the requested path in the 404 page", async () => {
    // Request normalises < and > to %3C/%3E, so the path arrives already
    // percent-encoded and must not be decoded back into markup.
    const res = await worker.fetch(req("/<script>alert(1)</script>"), ENV, {});
    const body = await res.text();
    expect(body).not.toContain("<script>alert(1)");
    expect(body).toContain("%3Cscript%3E");
  });

  it("marks the 404 as no-store so it is not cached", async () => {
    const res = await worker.fetch(req("/nope"), ENV, {});
    expect(res.headers.get("Cache-Control")).toBe("no-store");
  });

  it("treats a trailing slash as the same path", async () => {
    const res = await worker.fetch(req("/dashboard/"), ENV, {});
    expect(res.status).toBe(200);
  });

  it("falls through to 404 when an asset is missing", async () => {
    withAssets(async () => new Response("nope", { status: 404 }));
    const res = await worker.fetch(req("/lattice"), ENV, {});
    expect(res.status).toBe(404);
  });
});

describe("viewer route", () => {
  // The viewer worker 404s any Worker-originated subrequest, so it cannot be
  // reverse proxied; it redirects to the canonical URL instead.
  it("redirects the viewer to the canonical upstream URL", async () => {
    const orig = globalThis.fetch;
    globalThis.fetch = async () => { throw new Error("must not fetch upstream"); };
    try {
      const res = await worker.fetch(req("/osint/gods-eye"), ENV, {});
      expect(res.status).toBe(302);
      expect(res.headers.get("Location")).toBe("https://htp.sh/osint/gods-eye/");
    } finally { globalThis.fetch = orig; }
  });

  it("preserves the path suffix and query when redirecting", async () => {
    const res = await worker.fetch(req("/osint/gods-eye/sub/page?x=1"), ENV, {});
    expect(res.status).toBe(302);
    expect(res.headers.get("Location")).toBe("https://htp.sh/osint/gods-eye/sub/page?x=1");
  });

  it("chains the -viewer alias onto the canonical viewer", async () => {
    const res = await worker.fetch(req("/osint/gods-eye-viewer"), ENV, {});
    expect(res.headers.get("Location")).toBe("/osint/gods-eye");
  });
});

describe("upstream proxy", () => {
  it("forwards /ai upstream", async () => {
    const orig = globalThis.fetch;
    let seen;
    globalThis.fetch = async (r) => {
      seen = typeof r === "string" ? r : r.url;
      return new Response('{"ok":true}', { headers: { "content-type": "application/json" } });
    };
    try {
      const res = await worker.fetch(req("/ai"), ENV, {});
      expect(res.status).toBe(200);
      expect(seen).toBe("https://htp.sh/ai");
      expect(res.headers.get("X-Router")).toBe("god.htp.sh");
    } finally { globalThis.fetch = orig; }
  });

  it("forwards /osint sub-paths upstream", async () => {
    const orig = globalThis.fetch;
    let seen;
    globalThis.fetch = async (r) => { seen = r.url; return new Response("{}"); };
    try {
      await worker.fetch(req("/osint/api/analyze"), ENV, {});
      expect(seen).toBe("https://htp.sh/osint/api/analyze");
    } finally { globalThis.fetch = orig; }
  });

  it("substitutes its own 404 page for an upstream miss", async () => {
    // A bare relayed "Not Found" is what the caller complained about; inside
    // a proxied prefix the router must still explain what does exist.
    const orig = globalThis.fetch;
    globalThis.fetch = async () => new Response("Not Found", { status: 404 });
    try {
      // A proxied prefix that genuinely misses upstream (the -viewer spelling
      // is a redirect now, so it no longer reaches this path).
      const res = await worker.fetch(req("/osint/no-such-thing"), ENV, {});
      expect(res.status).toBe(404);
      const body = await res.text();
      expect(body).toContain("NOT FOUND");
      expect(body).toContain("/dashboard");
    } finally { globalThis.fetch = orig; }
  });

  it("relays non-404 upstream failures untouched", async () => {
    const orig = globalThis.fetch;
    globalThis.fetch = async () => new Response("boom", { status: 502 });
    try {
      const res = await worker.fetch(req("/ai"), ENV, {});
      expect(res.status).toBe(502);
      expect(res.headers.get("X-Router")).toBe("god.htp.sh");
    } finally { globalThis.fetch = orig; }
  });

  it("does not forward a body on GET", async () => {
    const orig = globalThis.fetch;
    let body;
    globalThis.fetch = async (r) => { body = r.body ?? null; return new Response("{}"); };
    try {
      await worker.fetch(req("/ai"), ENV, {});
      expect(body).toBeNull();
    } finally { globalThis.fetch = orig; }
  });

  it("strips Host so the upstream routes correctly", async () => {
    // Regression: forwarding Host: god.htp.sh made Cloudflare match the zone
    // wildcard (*.htp.sh/* -> still-firefly) instead of htp-osint-maps, and
    // every proxied path 404ed. Verified live: 200 normally, 404 with a
    // foreign Host header.
    //
    // Exercises /osint/api/analyze rather than /osint/gods-eye: the viewer is
    // redirected, not proxied, so it never reaches proxyUpstream.
    const orig = globalThis.fetch;
    let sent;
    globalThis.fetch = async (r) => {
      sent = r.headers;
      return new Response("{}");
    };
    try {
      await worker.fetch(req("/osint/api/analyze"), ENV, {});
      expect(sent.get("host")).toBeNull();
    } finally { globalThis.fetch = orig; }
  });

  it("strips content-length and accept-encoding", async () => {
    const orig = globalThis.fetch;
    let sent;
    globalThis.fetch = async (r) => { sent = r.headers; return new Response("{}"); };
    try {
      await worker.fetch(req("/osint/api/analyze", {
        headers: { "content-length": "99", "accept-encoding": "gzip" },
      }), ENV, {});
      expect(sent.get("content-length")).toBeNull();
      expect(sent.get("accept-encoding")).toBeNull();
    } finally { globalThis.fetch = orig; }
  });

  it("preserves ordinary request headers", async () => {
    const orig = globalThis.fetch;
    let sent;
    globalThis.fetch = async (r) => { sent = r.headers; return new Response("{}"); };
    try {
      await worker.fetch(req("/osint/api/analyze", {
        headers: { authorization: "Bearer x", "x-custom": "keep" },
      }), ENV, {});
      expect(sent.get("x-custom")).toBe("keep");
      expect(sent.get("authorization")).toBe("Bearer x");
      expect(sent.get("x-router")).toBe("god.htp.sh");
    } finally { globalThis.fetch = orig; }
  });
});

describe("asset trees", () => {
  it("serves the tree index at the prefix itself", async () => {
    // Typing /website must land on the site, not a listing or a 404.
    let seenUrl;
    withAssets(async (r) => {
      seenUrl = r.url;
      return assetResponse("<html>site</html>");
    });
    const res = await worker.fetch(req("/website"), ENV, {});
    expect(res.status).toBe(200);
    expect(await res.text()).toContain("site");
    expect(seenUrl).toBe("https://god.htp.sh/website/index.html");
  });

  it("serves a file inside the tree", async () => {
    let seenUrl;
    withAssets(async (r) => {
      seenUrl = r.url;
      return assetResponse("video-bytes");
    });
    const res = await worker.fetch(req("/website/bg.webm"), ENV, {});
    expect(res.status).toBe(200);
    expect(seenUrl).toBe("https://god.htp.sh/website/bg.webm");
  });

  it("normalises a trailing slash on the prefix", async () => {
    let seenUrl;
    withAssets(async (r) => {
      seenUrl = r.url;
      return assetResponse("<html>site</html>");
    });
    const res = await worker.fetch(req("/website/"), ENV, {});
    expect(res.status).toBe(200);
    expect(seenUrl).toBe("https://god.htp.sh/website/index.html");
  });

  it("falls through to the 404 page on a miss inside the tree", async () => {
    withAssets(async () => new Response("nope", { status: 404 }));
    const res = await worker.fetch(req("/website/not-here.html"), ENV, {});
    expect(res.status).toBe(404);
    expect(await res.text()).toContain("NOT FOUND");
  });

  it("refuses to traverse out of the tree", async () => {
    const orig = globalThis.fetch;
    globalThis.fetch = vi.fn(async () => new Response("{}"));
    try {
      // Encoded traversal must not resolve to a file outside public/website.
      const res = await worker.fetch(req("/website/..%2F..%2Fwrangler.jsonc"), ENV, {});
      expect(res.status).toBe(404);
      expect(globalThis.fetch).not.toHaveBeenCalled();
    } finally { globalThis.fetch = orig; }
  });

  it("does not treat a prefix-similar path as a tree hit", async () => {
    withAssets(async () => new Response("{}"));
    const res = await worker.fetch(req("/websites"), ENV, {});
    expect(res.status).toBe(404);
  });
});

describe("viewer redirects", () => {
  it("sends /gods-eye straight to the upstream canonical URL", async () => {
    // One hop, not two: going via /osint/gods-eye would cost a second 302 on
    // the route people type by hand.
    const res = await worker.fetch(req("/gods-eye"), ENV, {});
    expect(res.status).toBe(302);
    expect(res.headers.get("Location")).toBe("https://htp.sh/osint/gods-eye/");
  });

  it("redirects the canonical viewer path to the upstream origin", async () => {
    const res = await worker.fetch(req("/osint/gods-eye"), ENV, {});
    expect(res.status).toBe(302);
    expect(res.headers.get("Location")).toBe("https://htp.sh/osint/gods-eye/");
  });

  it("keeps the sub-path when redirecting a viewer asset", async () => {
    // The app pulls /osint/gods-eye/cesium/Cesium.js and hashed bundles off
    // this path. Dropping the suffix would land every asset on the app shell.
    const res = await worker.fetch(req("/osint/gods-eye/cesium/Cesium.js"), ENV, {});
    expect(res.status).toBe(302);
    expect(res.headers.get("Location")).toBe(
      "https://htp.sh/osint/gods-eye/cesium/Cesium.js",
    );
  });

  it("preserves the query string across the viewer redirect", async () => {
    const res = await worker.fetch(req("/osint/gods-eye?lat=51.5&lon=-0.12"), ENV, {});
    expect(res.headers.get("Location")).toBe(
      "https://htp.sh/osint/gods-eye/?lat=51.5&lon=-0.12",
    );
  });

  it("caches redirects briefly and allows CORS on every redirect", async () => {
    // A 302 with no Cache-Control is heuristically cacheable, which pins a
    // stale target in the browser after the viewer path moves.
    for (const path of ["/gods-eye", "/osint/gods-eye", "/osint/gods-eye-viewer"]) {
      const res = await worker.fetch(req(path), ENV, {});
      expect(res.status, path).toBe(302);
      expect(res.headers.get("Cache-Control"), path).toBe("public, max-age=60");
      expect(res.headers.get("Access-Control-Allow-Origin"), path).toBe("*");
      expect(res.headers.get("X-Router"), path).toBe("god.htp.sh");
    }
  });

  it("does not proxy the viewer — it never reaches the upstream fetch", async () => {
    // The viewer cannot be reverse proxied: the upstream worker 404s any
    // Worker-originated subrequest. If this ever starts fetching, that
    // constraint regressed and the redirect is no longer load-bearing.
    const orig = globalThis.fetch;
    globalThis.fetch = vi.fn(async () => new Response("{}"));
    try {
      await worker.fetch(req("/osint/gods-eye"), ENV, {});
      expect(globalThis.fetch).not.toHaveBeenCalled();
    } finally { globalThis.fetch = orig; }
  });
});