// Unit tests for static/js/i18n-core.js (pure translation logic, no DOM).
// Run: node --test tests/i18n_core.test.mjs
//
// i18n-core.js is a browser ES module living under a package.json with no
// "type":"module", so Node would treat the bare .js as CommonJS. Importing it
// through a data: URL forces ESM evaluation without touching project config.
// (Works because the core file has zero imports of its own.)
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

const src = readFileSync(new URL("../static/js/i18n-core.js", import.meta.url), "utf8");
const core = await import(
  "data:text/javascript;base64," + Buffer.from(src).toString("base64")
);
const { buildMaps, translate, lookupKey, interpolate, compilePattern, normalize } = core;

const catalog = {
  _meta: { code: "ja", name: "Japanese", nativeName: "日本語", dir: "ltr" },
  _overrides: { "theme.saved_btn": "保存済み" },
  Appearance: "外観",
  Saved: "保存しました",
  "· {n} msgs": "· {n} 件",
};
const maps = buildMaps(catalog);

test("exact source match", () => {
  assert.equal(translate("Appearance", maps), "外観");
});

test("whitespace around the core is preserved", () => {
  assert.equal(translate("  Appearance ", maps), "  外観 ");
});

test("unknown strings return null (left untouched)", () => {
  assert.equal(translate("Definitely not a UI string", maps), null);
});

test("placeholder pattern maps dynamic text onto the template", () => {
  assert.equal(translate("· 3 msgs", maps), "· 3 件");
});

test("data-i18n override wins for collisions", () => {
  assert.equal(lookupKey("theme.saved_btn", "Saved", maps), "保存済み");
});

test("data-i18n key with no override falls back to source translation", () => {
  assert.equal(lookupKey("some.unmapped.key", "Saved", maps), "保存しました");
});

test("interpolate fills and leaves unknown placeholders visible", () => {
  assert.equal(interpolate("Hi {name}", { name: "Ada" }), "Hi Ada");
  assert.equal(interpolate("Hi {name}", {}), "Hi {name}");
});

test("compilePattern captures named params", () => {
  const p = compilePattern("Deleted {count} of {total}", "{count}/{total} 削除");
  const m = p.re.exec("Deleted 2 of 5");
  assert.ok(m);
  assert.deepEqual(p.names, ["count", "total"]);
});

test("empty/whitespace input is a no-op", () => {
  assert.equal(translate("   ", maps), null);
  assert.equal(translate("", maps), null);
});

test("normalize collapses internal whitespace and trims", () => {
  assert.equal(normalize("  More   Tools\n"), "More Tools");
  assert.equal(normalize("a\t b  c"), "a b c");
});

test("irregular whitespace still matches the catalog key", () => {
  // exact key with collapsed inner whitespace + preserved outer whitespace
  assert.equal(translate(" Appearance ", maps), " 外観 ");
  // pattern key tolerates messy spacing too
  assert.equal(translate("·   3   msgs", maps), "· 3 件");
});
