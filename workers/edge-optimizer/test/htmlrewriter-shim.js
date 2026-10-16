/**
 * Test-only HTMLRewriter shim.
 *
 * HTMLRewriter is a workerd runtime global with no Node implementation, and
 * writing a faithful one is out of scope for a unit test. This shim lets the
 * header / passthrough / upload paths be tested here; the actual HTML
 * rewrites are verified for real in integration.test.js, which drives
 * `wrangler dev` (genuine workerd) and asserts on real output.
 *
 * Deliberately a pass-through: it must NOT pretend to apply handlers, or the
 * unit suite would assert against a fiction.
 */
class PassThroughRewriter {
  constructor() {
    this._handlers = [];
  }
  on(selector, handlers) {
    this._handlers.push([selector, handlers]);
    return this;
  }
  transform(response) {
    return response;
  }
}

if (typeof globalThis.HTMLRewriter === "undefined") {
  globalThis.HTMLRewriter = PassThroughRewriter;
}