/**
 * Integration tests: the HTML rewrites, run against real workerd.
 *
 * The unit suite uses a pass-through HTMLRewriter shim, so the rewrite
 * behaviour it covers is verified here instead, by driving `wrangler dev`
 * (genuine workerd) and asserting on what a real browser would receive.
 *
 * Run: npm run test:integration
 */
import { spawn } from "node:child_process";
import { setTimeout as sleep } from "node:timers/promises";
import { afterAll, beforeAll, describe, expect, it } from "vitest";

const PORT = Number(process.env.SELFTEST_PORT || 8793);
const BASE = `http://127.0.0.1:${PORT}`;

let proc;

async function waitForServer(timeoutMs = 90000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    try {
      const res = await fetch(`${BASE}/__selftest`, {
        signal: AbortSignal.timeout(4000),
      });
      if (res.status) return;
    } catch {
      /* not up yet */
    }
    await sleep(1000);
  }
  throw new Error("wrangler dev did not start in time");
}

beforeAll(async () => {
  proc = spawn(
    "npx",
    ["wrangler", "dev", "--port", String(PORT), "--ip", "127.0.0.1",
     "--var", "ENABLE_SELFTEST:1"],
    { stdio: "ignore" },
  );
  await waitForServer();
}, 150000);

afterAll(() => {
  try { proc?.kill("SIGKILL"); } catch { /* already gone */ }
});

describe("HTMLRewriter against real workerd", () => {
  it("strips comments, including conditional ones", async () => {
    const body = await (await fetch(`${BASE}/__selftest`)).text();
    expect(body).toContain("KEEP_ME");
    expect(body).not.toContain("DROP_COMMENT");
    expect(body).not.toContain("<![endif]");
  }, 60000);

  it("defers blocking external scripts", async () => {
    const body = await (await fetch(`${BASE}/__selftest`)).text();
    // Attribute order is not guaranteed, so match the tag as a whole and
    // assert both attributes are present on it.
    const tag = body.match(/<script[^>]*\/app\.js[^>]*>/)?.[0] ?? "";
    expect(tag).toBeTruthy();
    expect(tag).toMatch(/\bdefer\b/);
  }, 60000);

  it("adds lazy loading to images", async () => {
    const body = await (await fetch(`${BASE}/__selftest`)).text();
    expect(body).toContain('loading="lazy"');
    expect(body).toContain('decoding="async"');
  }, 60000);

  it("collapses inter-tag whitespace but preserves <pre>", async () => {
    const body = await (await fetch(`${BASE}/__selftest`)).text();
    // <pre> content must survive byte-for-byte.
    expect(body).toMatch(/<pre>line1\s*\n\s*\n\s*line2<\/pre>/);
    // Head whitespace should have collapsed.
    expect(body).not.toMatch(/\n\s{4,}<p>/);
  }, 60000);

  it("sets optimizer headers and omits the pi bloat", async () => {
    const res = await fetch(`${BASE}/__selftest`);
    expect(res.headers.get("x-optimizer")).toBe("phase2");
    expect(res.headers.get("x-optimizer-time-ms")).toBeTruthy();
    expect(res.headers.get("x-math-pi-value")).toBeNull();
    expect(res.headers.get("x-math-pi-precision-digits")).toBeNull();
    expect(res.headers.get("x-links-speed-e8pi-report")).toBeNull();
  }, 60000);
});