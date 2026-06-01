// Integration test: the real static/js/i18n.js runtime against the real
// static/locales catalogs, driven through a jsdom DOM. Proves the DOM walk,
// skip rules (model output untouched), data-i18n overrides, locale restore,
// and the MutationObserver actually work end-to-end.
//
// Run: node --test tests/i18n_dom.test.mjs   (needs jsdom: npm i -D jsdom)
//
// i18n.js + i18n-core.js are browser ES modules under a package.json with no
// "type":"module". We inline-bundle them (strip the relative import) and load
// the result as a data: URL module, with browser globals supplied by jsdom.
import { test, before } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

let JSDOM;
try {
  ({ JSDOM } = await import("jsdom"));
} catch {
  console.log("# SKIP i18n_dom: jsdom not installed (npm i -D jsdom)");
}

const read = (p) => readFileSync(new URL(p, import.meta.url), "utf8");
const ja = JSON.parse(read("../static/locales/ja.json"));
const catalogs = {
  "/static/locales/index.json": read("../static/locales/index.json"),
  "/static/locales/en.json": read("../static/locales/en.json"),
  "/static/locales/ja.json": read("../static/locales/ja.json"),
};

let i18n;

before(async () => {
  if (!JSDOM) return;
  const dom = new JSDOM(
    `<!DOCTYPE html><html lang="en"><body>
       <span class="vis-label">Appearance</span>
       <input placeholder="Search" />
       <span data-i18n="theme.saved_btn">Saved</span>
       <textarea id="composer" placeholder="Message Odysseus...">user typed text</textarea>
       <div class="msg msg-ai"><div class="body">Appearance</div></div>
       <div id="dyn"></div>
     </body></html>`,
    { url: "http://localhost/" }
  );
  const { window } = dom;
  global.window = window;
  global.document = window.document;
  // navigator (and sometimes location) are read-only getters on modern Node —
  // define rather than assign.
  for (const [k, v] of [["navigator", window.navigator], ["location", window.location]]) {
    try { global[k] = v; }
    catch { Object.defineProperty(global, k, { value: v, configurable: true }); }
  }
  global.localStorage = window.localStorage;
  global.MutationObserver = window.MutationObserver;
  global.CustomEvent = window.CustomEvent;
  global.fetch = async (url) => {
    const path = String(url).replace("http://localhost", "");
    if (catalogs[path] != null)
      return { ok: true, json: async () => JSON.parse(catalogs[path]) };
    return { ok: false, status: 404, json: async () => ({}) };
  };

  // inline-bundle core + runtime, drop the relative import, load as ESM
  const core = read("../static/js/i18n-core.js");
  const runtime = read("../static/js/i18n.js").replace(
    /import\s*\{[^}]*\}\s*from\s*["']\.\/i18n-core\.js["'];?/,
    ""
  );
  const mod = await import(
    "data:text/javascript;base64," +
      Buffer.from(core + "\n" + runtime).toString("base64")
  );
  i18n = mod.default;
  await i18n.ready;
});

const tick = () => new Promise((r) => setTimeout(r, 0));

test("switching to ja translates known UI text", { skip: !JSDOM }, async () => {
  await i18n.setLocale("ja");
  assert.equal(document.querySelector(".vis-label").textContent, "外観");
});

test("translatable attributes are localized", { skip: !JSDOM }, async () => {
  assert.equal(document.querySelector("input").getAttribute("placeholder"), ja["Search"]);
  assert.ok(ja["Search"] && ja["Search"] !== "Search");
});

test("data-i18n override wins over the default source translation", { skip: !JSDOM }, async () => {
  // "Saved" default is 保存しました; theme.saved_btn override is 保存済み
  assert.equal(document.querySelector("[data-i18n]").textContent, "保存済み");
  assert.notEqual(ja["Saved"], "保存済み");
});

test("model/user output (.msg) is never translated", { skip: !JSDOM }, async () => {
  assert.equal(document.querySelector(".msg .body").textContent, "Appearance");
});

test("MutationObserver translates dynamically-added UI", { skip: !JSDOM }, async () => {
  const dyn = document.getElementById("dyn");
  const span = document.createElement("span");
  span.className = "vis-label";
  span.textContent = "Appearance";
  dyn.appendChild(span);
  await tick();
  assert.equal(span.textContent, "外観");
});

test("textarea placeholder is localized; typed value is not", { skip: !JSDOM }, async () => {
  const ta = document.getElementById("composer");
  assert.equal(ta.getAttribute("placeholder"), ja["Message Odysseus..."]);
  assert.equal(ta.value, "user typed text"); // typed content untouched
});

test("externally re-set placeholder is re-translated (app.js resize case)", { skip: !JSDOM }, async () => {
  const ta = document.getElementById("composer");
  ta.setAttribute("placeholder", "Message Odysseus..."); // simulate app.js rewrite
  await tick();
  assert.equal(ta.getAttribute("placeholder"), ja["Message Odysseus..."]);
});

test("switching back to en restores the original English", { skip: !JSDOM }, async () => {
  await i18n.setLocale("en");
  assert.equal(document.querySelector(".vis-label").textContent, "Appearance");
  assert.equal(document.querySelector("input").getAttribute("placeholder"), "Search");
  assert.equal(document.querySelector("[data-i18n]").textContent, "Saved");
});
