// Tests of the view and the Back button (app.js, block between view:begin and view:end), with Node and no
// browser:
//
//     node --test app/src/helio/js-tests/view-state.test.mjs
//
// Section, day and scroll go to Java (HELIO_SAVE_VIEW) and come back after the process was
// killed (HELIO_RESTORE). The block runs as it is with fake show/closeModal/today.
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { test } from "node:test";
import assert from "node:assert/strict";
import vm from "node:vm";

const HERE = dirname(fileURLToPath(import.meta.url));
const APP = readFileSync(join(HERE, "../helio-app/static/app.js"), "utf8");
const BLOCK = APP.slice(APP.indexOf("// view:begin"), APP.indexOf("// view:end"));
const TITLES = { today: "Oggi", fitness: "Fitness", sleep: "Sonno", food: "Alimentazione", health: "Salute", profile: "Profilo e metodo" };

function page({ android = true, restore = null, sheets = [] } = {}) {
  const saved = [];
  const timers = [];
  const listeners = {};
  const ctx = {
    ANDROID: android, TITLES: JSON.parse(JSON.stringify(TITLES)),
    tab: "today", current: "2026-10-13", shown: [], closed: [],
    today: () => "2026-10-13",
    window: {
      scrollY: 0, HELIO_RESTORE: restore,
      HELIO_SAVE_VIEW: (v) => saved.push(JSON.parse(JSON.stringify(v))),
      addEventListener: (type, fn) => { (listeners[type] ||= []).push(fn); },
    },
    document: { querySelectorAll: (sel) => (sel === ".sheet" ? sheets : []) },
    setTimeout: (fn) => { timers.push(fn); return timers.length; },
    clearTimeout: () => {},
  };
  // tab, current and show belong to app.js: here they do the same things the block needs
  vm.createContext(ctx);
  vm.runInContext(`
    var tab = this.tab, current = this.current;
    function show(name) { tab = name; this.shown.push(name); viewSave(); }
    function closeModal(id) { this.closed.push(id); for (const s of document.querySelectorAll(".sheet")) if (s.id === id) s.hidden = true; }
    ${BLOCK}
    this.api = { viewInstall, viewSave, helioBack, state: () => ({ tab, current }) };
  `.replace(/this\.shown/g, "globalThis.shown").replace(/this\.closed/g, "globalThis.closed"), ctx);
  const flush = () => { while (timers.length) timers.shift()(); };
  const scroll = (y) => { ctx.window.scrollY = y; (listeners.scroll || []).forEach((fn) => fn()); };
  return { ctx, api: ctx.api, saved, flush, scroll, listeners, timers };
}

test("reopening after the process was killed: section, day and scroll", () => {
  const p = page({ restore: { tab: "sleep", date: "2026-10-11", y: 420 } });
  const y = p.api.viewInstall();
  assert.equal(y, 420);
  assert.equal(JSON.stringify(p.api.state()), JSON.stringify({ tab: "sleep", current: "2026-10-11" }));
  assert.deepEqual([...p.ctx.shown], ["sleep"]);
});

test("a view saved by version 259 (Italian section names) still restores", () => {
  for (const [old, now] of [["oggi", "today"], ["sonno", "sleep"], ["salute", "health"], ["cibo", "food"], ["profilo", "profile"]]) {  // legacy
    const p = page({ restore: { tab: old, date: "2026-10-11", y: 10 } });
    p.api.viewInstall();
    assert.equal(p.api.state().tab, now);
  }
});

test("invalid values to restore are ignored", () => {
  for (const restore of [null, "x", { tab: "admin" }, { tab: "constructor" }, { date: "2026-10-14" }, { date: "11/10/2026" },
    { y: -3 }, { y: 1.5 }, { y: "420" }, { y: 2000000 }]) {
    const p = page({ restore });
    assert.equal(p.api.viewInstall(), null, JSON.stringify(restore));
    assert.equal(JSON.stringify(p.api.state()), JSON.stringify({ tab: "today", current: "2026-10-13" }), JSON.stringify(restore));
  }
});

test("changing section or day: the view goes to Java at once, only once", () => {
  const p = page();
  p.api.viewInstall();
  p.api.viewSave();
  p.api.viewSave();  // unchanged: not repeated
  p.ctx.window.scrollY = 0;
  p.api.helioBack(); // from Today nothing changes
  assert.equal(p.saved.length, 1);
  assert.equal(JSON.stringify(p.saved[0]), JSON.stringify({ tab: "today", date: "2026-10-13", y: 0 }));
});

test("scroll: a single message after a pause, with the final position", () => {
  const p = page();
  p.api.viewInstall();
  for (const y of [10, 200, 380, 433.6]) p.scroll(y);
  assert.equal(p.timers.length, 1);
  assert.equal(p.saved.length, 0);
  p.flush();
  assert.equal(JSON.stringify(p.saved), JSON.stringify([{ tab: "today", date: "2026-10-13", y: 434 }]));
});

test("Back: first the open sheet, then Today, then Java closes Helio", () => {
  const a = { id: "bandSheet", hidden: false }, b = { id: "pickSheet", hidden: false };
  const p = page({ restore: { tab: "food" }, sheets: [a, b] });
  p.api.viewInstall();
  assert.equal(p.api.helioBack(), true);
  assert.deepEqual([...p.ctx.closed], ["pickSheet"]);
  assert.equal(p.api.helioBack(), true);
  assert.deepEqual([...p.ctx.closed], ["pickSheet", "bandSheet"]);
  assert.equal(p.api.helioBack(), true);
  assert.equal(p.api.state().tab, "today");
  assert.equal(p.api.helioBack(), false);
  assert.equal(typeof p.ctx.window.helioBack, "function");
});

test("on the server no view and no Back", () => {
  const p = page({ android: false });
  p.api.viewSave();
  assert.equal(p.saved.length, 0);
  assert.match(APP, /\n  const restoreY = ANDROID \? viewInstall\(\) : null;\n/);
  assert.equal(APP.match(/viewInstall\(\)/g).length, 2);
});
