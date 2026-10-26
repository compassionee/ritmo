// Tests of "pull down to sync" (app.js, block between pull:begin and pull:end), with Node and
// no browser:
//
//     node --test app/src/helio/js-tests/pull-sync.test.mjs
//
// The block runs as it is in a context with a fake document, window, clock and timers.
// postJson/loadBand/load are fake and record the calls; the rules on 30 seconds and on a
// sync in progress belong to Python (test_helio_android.py): here what counts is what the page does
// with the answers.
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { test } from "node:test";
import assert from "node:assert/strict";
import vm from "node:vm";
import { loadI18n } from "./i18n-node.mjs";

const HERE = dirname(fileURLToPath(import.meta.url));
const APP = readFileSync(join(HERE, "../helio-app/static/app.js"), "utf8");
const BLOCK = APP.slice(APP.indexOf("// pull:begin"), APP.indexOf("// pull:end"));
const { I18N } = await loadI18n("en", ["en-GB"]);

function element(parent = null, scrollTop = 0, tag = "div") {
  const classes = new Set();
  return {
    parentElement: parent, scrollTop, tag, hidden: true, textContent: "", style: {},
    classList: {
      add: (...c) => c.forEach((x) => classes.add(x)),
      remove: (...c) => c.forEach((x) => classes.delete(x)),
      toggle: (c, on) => (on ? classes.add(c) : classes.delete(c)),
      contains: (c) => classes.has(c),
    },
    closest(sel) {
      for (let el = this; el; el = el.parentElement) {
        if (sel.split(",").map((s) => s.trim()).includes(el.tag)) return el;
      }
      return null;
    },
  };
}

/** Fake page. responses: answers of /api/android/action in order; bands: band states for loadBand. */
function page({ android = true, responses = [{ ok: true, sync: true }], bands = [], band = { last_sync: 100, busy: null } } = {}) {
  const root = element(null, 0, "html");
  const body = element(root, 0, "body");
  const main = element(body, 0, "main");
  const card = element(main, 0, "div");
  const ids = { pullBox: element(body), pullText: element(body) };
  const sheet = element(body, 0, "div");
  const listeners = {};
  const calls = { post: [], load: 0, loadBand: 0, toast: [] };
  let clock = 1_000_000;
  const ctx = {
    ANDROID: android, BAND: band,
    window: {
      scrollY: 0,
      addEventListener: (type, fn) => { (listeners["window:" + type] ||= []).push(fn); },
    },
    document: {
      scrollingElement: root, documentElement: root,
      querySelectorAll: (sel) => (sel === ".sheet" ? [sheet] : []),
      addEventListener: (type, fn) => { (listeners[type] ||= []).push(fn); },
    },
    Date: { now: () => clock },
    setTimeout: (fn, ms) => { clock += ms; setImmediate(fn); },
    $: (id) => ids[id],
    postJson: async (path, payload) => {
      calls.post.push([path, payload]);
      const r = responses.shift();
      if (r instanceof Error) throw r;
      return r;
    },
    loadBand: async () => { calls.loadBand++; if (bands.length) ctx.BAND = bands.shift(); },
    load: async () => { calls.load++; },
    toast: (msg, err) => calls.toast.push([msg, !!err]),
    t: I18N.t, tx: I18N.tx,
  };
  vm.createContext(ctx);
  vm.runInContext(BLOCK + "\nthis.pull = { pullInstall, pullSync, busy: () => pullBusy };", ctx);
  if (android) ctx.pull.pullInstall();

  const fire = (type, x, y, target = card, touches = 1) => {
    const t = { clientX: x, clientY: y };
    const e = { target, touches: type === "touchend" ? [] : Array(touches).fill(t), changedTouches: [t] };
    (listeners[type] || []).forEach((fn) => fn(e));
  };
  const drag = (points, target = card) => {
    fire("touchstart", points[0][0], points[0][1], target);
    for (const [x, y] of points.slice(1)) fire("touchmove", x, y, target);
    fire("touchend", ...points[points.length - 1], target);
  };
  const scroll = () => (listeners["window:scroll"] || []).forEach((fn) => fn({}));
  const settle = async () => { for (let i = 0; i < 400 && ctx.pull.busy(); i++) await new Promise((r) => setImmediate(r)); };
  return { ctx, ids, root, main, card, sheet, calls, fire, drag, scroll, settle, listeners,
    tick: (ms) => { clock += ms; }, tag: (t) => element(card, 0, t) };
}

const PULL = [[100, 100], [101, 130], [102, 200], [102, 300]];  // 200 px downwards

test("pulling at the top starts the sync and then reloads the day", async () => {
  const p = page({ bands: [{ busy: "Fetching", last_sync: 100 }, { busy: null, last_sync: 160 }] });
  p.tick(1000);
  p.drag(PULL);
  assert.equal(p.ids.pullBox.hidden, false);
  assert.ok(p.ids.pullBox.classList.contains("busy"));
  await p.settle();
  assert.equal(JSON.stringify(p.calls.post), JSON.stringify([["/api/android/action", { action: "pull" }]]));
  assert.equal(p.calls.loadBand, 2);  // loadBand sees the new last sync and reloads by itself
  assert.equal(p.calls.load, 0);
  assert.equal(p.ids.pullBox.hidden, true);
});

test("the indicator stays until the transfer finishes", async () => {
  const busy = { busy: "Fetching", last_sync: 100 };
  const p = page({ bands: [busy, busy, busy, busy, { busy: null, last_sync: 200 }] });
  p.tick(1000);
  p.drag(PULL);
  // after every check with the band still working, the indicator is there
  while (p.calls.loadBand < 4) {
    await new Promise((r) => setImmediate(r));
    assert.equal(p.ids.pullBox.hidden, false);
    assert.equal(p.ctx.pull.busy(), true);
  }
  await p.settle();
  assert.equal(p.calls.loadBand, 5);
  assert.equal(p.ids.pullBox.hidden, true);
});

test("recent sync or sync in progress: only the data is refreshed", async () => {
  const recent = page({ responses: [{ ok: true, sync: false, reason: "recent" }] });
  recent.tick(1000);
  recent.drag(PULL);
  await recent.settle();
  assert.equal(recent.calls.load, 1);
  assert.equal(recent.calls.loadBand, 1);
  assert.match(recent.calls.toast[0][0], /synced recently/);

  // in progress: wait for it and then reload, without starting another one
  const busy = page({ responses: [{ ok: true, sync: false, reason: "busy" }],
    bands: [{ busy: "Fetching", last_sync: 100 }, { busy: null, last_sync: 130 }] });
  busy.tick(1000);
  busy.drag(PULL);
  await busy.settle();
  assert.equal(busy.calls.post.length, 1);
  assert.equal(busy.calls.loadBand, 2);
});

test("a second pull during the sync sends nothing", async () => {
  const busy = { busy: "Fetching", last_sync: 100 };
  const p = page({ bands: [busy, busy, busy, { busy: null, last_sync: 200 }] });
  p.tick(1000);
  p.drag(PULL);
  p.drag(PULL);
  p.drag(PULL);
  await p.settle();
  assert.equal(p.calls.post.length, 1);
});

test("band not answering: the indicator does not stay forever", async () => {
  const idle = { busy: null, last_sync: 100, status: "collegata" };
  const p = page({ bands: Array(40).fill(idle) });
  p.tick(1000);
  p.drag(PULL);
  await p.settle();
  assert.equal(p.ctx.pull.busy(), false);
  assert.equal(p.ids.pullBox.hidden, true);
  assert.ok(p.calls.loadBand <= 12);
  assert.equal(p.calls.load, 1);
});

test("bridge error: message and data refreshed", async () => {
  const p = page({ responses: [new Error("request not allowed")] });
  p.tick(1000);
  p.drag(PULL);
  await p.settle();
  assert.deepEqual(p.calls.toast, [["Failed: request not allowed", true]]);
  assert.equal(p.calls.load, 1);
});

test("it does not start if the page is not at the top, also inside an inner container", async () => {
  const p = page();
  p.tick(1000);
  p.ctx.window.scrollY = 40;
  p.drag(PULL);
  p.ctx.window.scrollY = 0;
  p.root.scrollTop = 10;
  p.drag(PULL);
  p.root.scrollTop = 0;
  p.main.scrollTop = 80;  // scrolling inside <main>, not the window
  p.drag(PULL);
  await p.settle();
  assert.equal(p.calls.post.length, 0);
  assert.equal(p.ids.pullBox.hidden, true);
});

test("scrolling up to the top does not trigger it", async () => {
  const p = page();
  p.tick(1000);
  // the container gets back to the top during the gesture: the finger started with the page scrolled
  p.main.scrollTop = 200;
  p.fire("touchstart", 100, 100);
  p.main.scrollTop = 0;
  p.fire("touchmove", 100, 250);
  p.fire("touchend", 100, 320);
  // a series of flicks upwards: the page reaches the top and the next flick starts at once
  p.scroll();
  p.tick(200);
  p.drag(PULL);
  // scrolling during the gesture cancels it
  p.tick(1000);
  p.fire("touchstart", 100, 100);
  p.fire("touchmove", 100, 140);
  p.scroll();
  p.fire("touchmove", 100, 300);
  p.fire("touchend", 100, 300);
  await p.settle();
  assert.equal(p.calls.post.length, 0);
  // still for long enough: now yes
  p.tick(1000);
  p.drag(PULL);
  await p.settle();
  assert.equal(p.calls.post.length, 1);
});

test("short, sideways, upward or two-finger drags do not count", async () => {
  const p = page();
  p.tick(1000);
  p.drag([[100, 100], [100, 140], [100, 200]]);           // below the threshold
  p.drag([[100, 100], [160, 130], [260, 300]]);           // sideways (change of day)
  p.drag([[100, 300], [100, 280], [100, 500]]);           // upwards first
  p.fire("touchstart", 100, 100, p.card, 2);              // two fingers
  p.fire("touchmove", 100, 300, p.card, 2);
  p.fire("touchend", 100, 300);
  p.fire("touchstart", 100, 100);                         // cancelled by the system
  p.fire("touchmove", 100, 300);
  (p.listeners.touchcancel || []).forEach((fn) => fn({}));
  p.fire("touchend", 100, 300);
  await p.settle();
  assert.equal(p.calls.post.length, 0);
  assert.equal(p.ids.pullBox.hidden, true);
});

test("indicator while dragging: first drag, then release", () => {
  const p = page();
  p.tick(1000);
  p.fire("touchstart", 100, 100);
  p.fire("touchmove", 100, 160);
  assert.equal(p.ids.pullBox.hidden, false);
  assert.equal(p.ids.pullText.textContent, "Pull to sync");
  p.fire("touchmove", 100, 300);
  assert.equal(p.ids.pullText.textContent, "Release to sync");
  p.fire("touchmove", 100, 160);  // going back up below the threshold and releasing does nothing
  p.fire("touchend", 100, 160);
  assert.equal(p.ids.pullBox.hidden, true);
  assert.equal(p.calls.post.length, 0);
});

test("no gesture with a sheet open or on fields and rows of buttons", async () => {
  const p = page();
  p.tick(1000);
  p.sheet.hidden = false;
  p.drag(PULL);
  p.sheet.hidden = true;
  for (const tag of ["input", "textarea", "select", ".chips", ".seg-switch"]) p.drag(PULL, p.tag(tag));
  await p.settle();
  assert.equal(p.calls.post.length, 0);
});

test("on the server (outside the app) the gesture does not exist", async () => {
  const p = page({ android: false });
  p.tick(1000);
  p.drag(PULL);
  await p.settle();
  assert.deepEqual(p.listeners, {});
  assert.equal(p.calls.post.length, 0);
  // and app.js installs it only in the app
  assert.match(APP, /\nif \(ANDROID\) pullInstall\(\);\n/);
  assert.equal(APP.match(/pullInstall\(\)/g).length, 2);
});
