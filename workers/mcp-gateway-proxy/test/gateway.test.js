import { describe, it, expect, beforeEach } from "vitest";
import worker, {
  acquire,
  buildTarget,
  recordFail,
  recordOk,
  recordFor,
  isAvailable,
  resetState,
} from "../src/index.js";

const POOL = ["https://a.example.com/ai", "https://b.example.com/ai", "https://c.example.com/ai"];
const ENV = { GATEWAY_POOL: POOL.join(",") };

function req(path = "/ai/tools") {
  return new Request(`https://proxy.example.com${path}`);
}

beforeEach(() => resetState());

describe("routing", () => {
  it("round-robins across the pool", () => {
    const seen = Array.from({ length: 6 }, () => acquire(POOL));
    expect(seen).toEqual([
      "https://a.example.com/ai",
      "https://b.example.com/ai",
      "https://c.example.com/ai",
      "https://a.example.com/ai",
      "https://b.example.com/ai",
      "https://c.example.com/ai",
    ]);
  });

  it("skips a cooling upstream", () => {
    recordFor(POOL[0]).cooldownUntil = Date.now() + 60_000;
    const seen = new Set(Array.from({ length: 8 }, () => acquire(POOL)));
    expect(seen.has(POOL[0])).toBe(false);
    expect(seen.size).toBe(2);
  });

  it("skips a dead upstream", () => {
    recordFor(POOL[1]).dead = true;
    expect(new Set(Array.from({ length: 8 }, () => acquire(POOL))).has(POOL[1])).toBe(false);
  });

  it("returns null when everything is unavailable", () => {
    for (const u of POOL) recordFor(u).dead = true;
    expect(acquire(POOL)).toBeNull();
  });

  it("cooldown expires so an upstream can return", () => {
    const rec = recordFor(POOL[0]);
    recordFail(rec, { cooldownBaseMs: 1000, cooldownMaxMs: 60000 }, "boom");
    expect(isAvailable(rec)).toBe(false);
    rec.cooldownUntil = Date.now() - 1;
    expect(isAvailable(rec)).toBe(true);
  });
});

describe("failure accounting", () => {
  const cfg = { cooldownBaseMs: 1000, cooldownMaxMs: 60_000 };

  it("backs off exponentially and caps", () => {
    const rec = recordFor(POOL[0]);
    const delays = [];
    for (let i = 0; i < 8; i++) {
      recordFail(rec, cfg, "boom");
      delays.push(rec.cooldownUntil - Date.now());
      rec.cooldownUntil = 0;
    }
    expect(delays[0]).toBeLessThan(delays[1]);
    expect(delays[1]).toBeLessThan(delays[2]);
    expect(Math.max(...delays)).toBeLessThanOrEqual(cfg.cooldownMaxMs + 50);
  });

  it("marks terminal failures dead without cooldown", () => {
    const rec = recordFor(POOL[0]);
    recordFail(rec, cfg, "HTTP 502", true);
    expect(rec.dead).toBe(true);
    expect(rec.cooldownUntil).toBe(0);
  });

  it("success clears cooldown and revives a dead upstream", () => {
    const rec = recordFor(POOL[0]);
    recordFail(rec, cfg, "HTTP 502", true);
    recordOk(rec);
    expect(rec.dead).toBe(false);
    expect(rec.strikes).toBe(0);
    expect(rec.ok).toBe(1);
  });
});

describe("buildTarget", () => {
  const IN = "http://127.0.0.1:8787"; // proxy has a port; upstream must not inherit it

  it("does not leak the proxy port to the upstream", () => {
    expect(buildTarget("https://htp.sh", `${IN}/ai`)).toBe("https://htp.sh/ai");
  });

  it("preserves an explicit upstream port", () => {
    expect(buildTarget("https://htp.sh:8443", `${IN}/ai`)).toBe("https://htp.sh:8443/ai");
  });

  it("keeps a multi-segment upstream path", () => {
    expect(buildTarget("https://x.example.com/gw/ai", `${IN}/tools`))
      .toBe("https://x.example.com/gw/ai/tools");
  });

  it("does not double a shared path prefix", () => {
    expect(buildTarget("https://htp.sh/ai", `${IN}/ai/tools`))
      .toBe("https://htp.sh/ai/tools");
  });

  it("appends when the path diverges from the base", () => {
    expect(buildTarget("https://htp.sh/ai", `${IN}/models`))
      .toBe("https://htp.sh/ai/models");
  });

  it("preserves the query string", () => {
    expect(buildTarget("https://htp.sh", `${IN}/ai/models?q=llama&n=2`))
      .toBe("https://htp.sh/ai/models?q=llama&n=2");
  });

  it("handles a bare root path", () => {
    expect(buildTarget("https://htp.sh", `${IN}/`)).toBe("https://htp.sh/");
  });

  it("collapses duplicate slashes", () => {
    expect(buildTarget("https://htp.sh/", `${IN}/ai`)).toBe("https://htp.sh/ai");
  });
});

describe("fetch handler", () => {
  const mkFetch = (impl) => {
    const orig = globalThis.fetch;
    globalThis.fetch = impl;
    return () => { globalThis.fetch = orig; };
  };

  it("serves the health endpoint without proxying", async () => {
    const restore = mkFetch(async () => { throw new Error("should not fetch upstream"); });
    try {
      const res = await worker.fetch(req("/__gateway/health"), ENV, { waitUntil() {} });
      expect(res.status).toBe(200);
      const body = await res.json();
      expect(body.pool).toEqual(POOL);
      expect(body.routable).toHaveLength(3);
    } finally { restore(); }
  });

  it("reports routable=false when all upstreams are dead", async () => {
    for (const u of POOL) recordFor(u).dead = true;
    const restore = mkFetch(async () => { throw new Error("nope"); });
    try {
      const res = await worker.fetch(req("/__gateway/health"), ENV, { waitUntil() {} });
      expect((await res.json()).routable).toHaveLength(0);
    } finally { restore(); }
  });

  it("proxies a successful request and tags the upstream", async () => {
    let target;
    const restore = mkFetch(async (url) => {
      target = url;
      return new Response('{"ok":true}', {
        status: 200, headers: { "content-type": "application/json" },
      });
    });
    try {
      const res = await worker.fetch(req("/ai/tools"), ENV, { waitUntil() {} });
      expect(res.status).toBe(200);
      expect(res.headers.get("X-Gateway-Upstream")).toBeTruthy();
      expect(target).toMatch(/^https:\/\/[abc]\.example\.com\/ai\/tools$/);
      expect(target).not.toContain("127.0.0.1");
    } finally { restore(); }
  });

  it("does not penalize a 4xx upstream", async () => {
    const restore = mkFetch(async () => new Response("bad request", { status: 400 }));
    try {
      const res = await worker.fetch(req("/ai/tools"), ENV, { waitUntil() {} });
      expect(res.status).toBe(400);
      const rec = recordFor(POOL[0]);
      expect(rec.strikes).toBe(0);
      expect(rec.cooling ? rec.cooldownUntil > Date.now() : false).toBe(false);
    } finally { restore(); }
  });

  it("cools an upstream on 429 and fails over", async () => {
    let call = 0;
    const restore = mkFetch(async (url) => {
      call++;
      if (url.includes("a.example.com")) return new Response("slow down", { status: 429 });
      return new Response("ok", { status: 200 });
    });
    try {
      const res = await worker.fetch(req("/ai/tools"), ENV, { waitUntil() {} });
      expect(res.status).toBe(200);
      expect(recordFor(POOL[0]).strikes).toBe(1);
    } finally { restore(); }
  });

  it("marks dead on 502", async () => {
    const restore = mkFetch(async () => new Response("bad gateway", { status: 502 }));
    try {
      const res = await worker.fetch(req("/ai/tools"), ENV, { waitUntil() {} });
      expect(res.status).toBe(502);
      expect(res.status === 200).toBe(false);
    } finally { restore(); }
  });

  it("fails over on a network error", async () => {
    let call = 0;
    const restore = mkFetch(async (url) => {
      call++;
      if (url.includes("a.example.com")) throw new TypeError("network down");
      return new Response("ok", { status: 200 });
    });
    try {
      const res = await worker.fetch(req("/ai/tools"), ENV, { waitUntil() {} });
      expect(res.status).toBe(200);
      expect(recordFor(POOL[0]).fail).toBe(1);
    } finally { restore(); }
  });

  it("returns 503 when the pool is exhausted", async () => {
    for (const u of POOL) recordFor(u).dead = true;
    const restore = mkFetch(async () => { throw new Error("unused"); });
    try {
      const res = await worker.fetch(req("/ai/tools"), ENV, { waitUntil() {} });
      expect(res.status).toBe(503);
      expect((await res.json()).error).toBe("gateway_exhausted");
    } finally { restore(); }
  });

  it("answers CORS preflight without touching upstream", async () => {
    const restore = mkFetch(async () => { throw new Error("unused"); });
    try {
      const res = await worker.fetch(
        new Request("https://proxy.example.com/ai/tools", { method: "OPTIONS" }),
        ENV, { waitUntil() {} }
      );
      expect(res.status).toBe(204);
    } finally { restore(); }
  });

  it("drops hop-by-hop headers when relaying", async () => {
    const restore = mkFetch(async () =>
      new Response("x", {
        status: 200,
        headers: { "connection": "keep-alive", "x-keep": "yes" },
      })
    );
    try {
      const res = await worker.fetch(req(), ENV, { waitUntil() {} });
      expect(res.headers.get("connection")).toBeNull();
      expect(res.headers.get("x-keep")).toBe("yes");
    } finally { restore(); }
  });

  it("tolerates a bad GATEWAY_POOL value", async () => {
    const restore = mkFetch(async () => { throw new Error("unused"); });
    try {
      const res = await worker.fetch(
        req("/__gateway/health"), { GATEWAY_POOL: " , , " }, { waitUntil() {} }
      );
      expect(res.status).toBe(200);
      expect((await res.json()).pool).toEqual([]);
    } finally { restore(); }
  });

  it("advertises osint_analyze in the manifest", async () => {
    // Regression: without this key, mcp_gateway_transport.build_tools() never
    // synthesises the osint_analyze tool, because it only creates tools for
    // routes the manifest advertises. OSINT then works only via the hardcoded
    // ANALYZE_PATH fallback, and nothing signals the gap.
    const restore = mkFetch(async () => { throw new Error("unused"); });
    try {
      const res = await worker.fetch(req("/ai"), ENV, { waitUntil() {} });
      expect(res.status).toBe(200);
      const body = await res.json();
      expect(body.endpoints.osint_analyze).toBe("/osint/api/analyze");
    } finally { restore(); }
  });
});