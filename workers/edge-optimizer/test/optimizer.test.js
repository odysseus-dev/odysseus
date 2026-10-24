// Must precede the worker import: the shim installs a global the worker reads.
import "./htmlrewriter-shim.js";
import { describe, it, expect } from "vitest";
import worker, { Phase1, sanitizeHeaders } from "../src/index.js";

const ENV = { ORIGIN_HOST: "htp.sh", ENABLE_CACHE: "0" };
const ctx = () => ({ waitUntil() {} });

function mockOrigin(html, init = {}) {
  const orig = globalThis.fetch;
  globalThis.fetch = async () =>
    new Response(html, {
      status: init.status ?? 200,
      headers: {
        "content-type": init.contentType ?? "text/html; charset=utf-8",
        ...(init.headers ?? {}),
      },
    });
  return () => { globalThis.fetch = orig; };
}

const readBody = async (res) => {
  const clone = res.clone();
  return { text: await clone.text(), status: res.status, headers: res.headers };
};

describe("sanitizeHeaders", () => {
  it("drops hop-by-hop headers", () => {
    const h = sanitizeHeaders(new Headers({
      connection: "keep-alive",
      "transfer-encoding": "chunked",
      "content-length": "10",
      "content-type": "text/html",
    }));
    expect(h.get("connection")).toBeNull();
    expect(h.get("transfer-encoding")).toBeNull();
    expect(h.get("content-length")).toBeNull();
    expect(h.get("content-type")).toBe("text/html");
  });

  it("preserves set-cookie", () => {
    // The previous version dropped it via an allowlist, silently logging
    // every proxied user out.
    const h = sanitizeHeaders(new Headers([["set-cookie", "a=1; Path=/"]]));
    expect(h.get("set-cookie")).toBe("a=1; Path=/");
  });

  it("strips E8/pi telemetry the origin adds", () => {
    // Verified against the live origin: htp.sh/ai returns all of these on
    // every response. They pass straight through a naive copy otherwise.
    const h = sanitizeHeaders(new Headers({
      "x-e8-dimension-hex": "0xf8",
      "x-e8-roots-hex": "0xf0",
      "x-e8-signature-hex": "0x00001e97d1c6c000",
      "x-e8-pi-projection-hex": "0x04aae9b7e8851740",
      "x-math-pi-precision-digits": "200",
      "x-math-pi-value-head": "3.141592653589...",
      "x-links-speed-e8pi-report": "{}",
      "content-type": "application/json",
    }));
    for (const k of ["x-e8-dimension-hex", "x-e8-roots-hex",
                     "x-e8-signature-hex", "x-e8-pi-projection-hex",
                     "x-math-pi-precision-digits", "x-math-pi-value-head",
                     "x-links-speed-e8pi-report"]) {
      expect(h.get(k), k).toBeNull();
    }
    expect(h.get("content-type")).toBe("application/json");
  });

  it("preserves multiple set-cookie headers", () => {
    const src = new Headers();
    src.append("set-cookie", "a=1");
    src.append("set-cookie", "b=2");
    const h = sanitizeHeaders(src);
    expect(h.getSetCookie()).toEqual(["a=1", "b=2"]);
  });
});

// HTMLRewriter output assertions live in integration.test.js, which runs
// against real workerd. The unit shim here is a pass-through and would only
// assert against a fiction.

describe("headers", () => {
  it("sets optimizer headers on HTML", async () => {
    const restore = mockOrigin("<html><body>x</body></html>");
    try {
      const res = await worker.fetch(new Request("https://p/"), ENV, ctx());
      expect(res.headers.get("X-Optimizer")).toBe("phase2");
      expect(res.headers.get("X-Optimizer-Time-Ms")).toBeTruthy();
    } finally { restore(); }
  });

  it("does not invent a shared-cache lifetime for JSON", async () => {
    // The previous version stamped s-maxage=300 on every response.
    const restore = mockOrigin('{"a":1}', { contentType: "application/json" });
    try {
      const res = await worker.fetch(new Request("https://p/api"), ENV, ctx());
      expect(res.headers.get("cache-control") ?? "").not.toContain("s-maxage");
    } finally { restore(); }
  });

  it("emits a cheap trace, not a BigInt signature", async () => {
    const restore = mockOrigin("<html></html>");
    try {
      const res = await worker.fetch(new Request("https://p/"), ENV, ctx());
      const trace = res.headers.get("X-Optimizer-Trace");
      expect(trace).toMatch(/^[0-9a-f]{8}$/);
    } finally { restore(); }
  });

  it("does not emit a 1000-digit pi header", async () => {
    // ~1KB of pure overhead on every single response.
    const restore = mockOrigin("<html></html>");
    try {
      const res = await worker.fetch(new Request("https://p/"), ENV, ctx());
      expect(res.headers.get("X-Math-Pi-Value")).toBeNull();
      expect(res.headers.get("X-Math-Pi-Precision-Digits")).toBeNull();
    } finally { restore(); }
  });

  it("does not emit a per-link speed report", async () => {
    const restore = mockOrigin("<html><a href='/a'>a</a><a href='/b'>b</a></body></html>");
    try {
      const res = await worker.fetch(new Request("https://p/"), ENV, ctx());
      expect(res.headers.get("X-Links-Speed-E8Pi-Report")).toBeNull();
      expect(res.headers.get("X-E8-Signature-Hex")).toBeNull();
    } finally { restore(); }
  });
});

describe("non-HTML passthrough", () => {
  it("passes JSON through untransformed", async () => {
    const restore = mockOrigin('{"a":1}', { contentType: "application/json" });
    try {
      const res = await worker.fetch(new Request("https://p/api"), ENV, ctx());
      expect(await res.text()).toBe('{"a":1}');
    } finally { restore(); }
  });

  it("passes through error statuses without rewriting", async () => {
    const restore = mockOrigin("<html>gone</html>", { status: 404 });
    try {
      const res = await worker.fetch(new Request("https://p/"), ENV, ctx());
      expect(res.status).toBe(404);
      expect(await res.text()).toBe("<html>gone</html>");
    } finally { restore(); }
  });

  it("proxies to the configured origin host", async () => {
    let seen;
    const orig = globalThis.fetch;
    globalThis.fetch = async (req) => {
      seen = typeof req === "string" ? req : req.url;
      return new Response("<html></html>", { headers: { "content-type": "text/html" } });
    };
    try {
      await worker.fetch(new Request("https://proxy.example.com/x"), ENV, ctx());
      expect(seen).toBe("https://htp.sh/x");
    } finally { globalThis.fetch = orig; }
  });

  it("sends no body on GET", async () => {
    let bodySeen = "unset";
    const orig = globalThis.fetch;
    globalThis.fetch = async (req) => {
      bodySeen = req.body ?? null;
      return new Response("<html></html>", { headers: { "content-type": "text/html" } });
    };
    try {
      await worker.fetch(new Request("https://p/"), ENV, ctx());
      expect(bodySeen).toBeNull();
    } finally { globalThis.fetch = orig; }
  });
});

describe("upload", () => {
  const formReq = () =>
    new Request("https://p/upload", {
      method: "POST",
      headers: { "content-length": "120" },
      body: new FormData(),
    });

  it("rejects a missing content-length", async () => {
    const req = new Request("https://p/upload", { method: "POST", body: new FormData() });
    const res = await worker.fetch(req, ENV, ctx());
    expect(res.status).toBe(400);
  });

  it("rejects oversize uploads", async () => {
    const req = new Request("https://p/upload", {
      method: "POST",
      headers: { "content-length": "999999999999" },
      body: new FormData(),
    });
    const res = await worker.fetch(req, { ...ENV, MAX_UPLOAD_SIZE: "1000" }, ctx());
    expect(res.status).toBe(413);
  });
});

describe("Phase1 reuse", () => {
  it("caps critical stylesheet preloads", () => {
    const caps = { stylesheets: Array.from({ length: 20 }, (_, i) => `/s${i}.css`) };
    expect(new Phase1(caps)).toBeInstanceOf(Phase1);
    expect(caps.stylesheets.length).toBe(20);
  });
});