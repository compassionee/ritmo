// Tests of the script injected into the WebView (helio-android.js), with Node and no browser:
//
//     node --test app/src/helio/js-tests/helio-android.test.mjs
//
// The script runs as it is in a context with fake window, fetch and HelioBridge. HelioBridge does
// what HelioWebRouter does in Java (message -> Handler -> answer) and passes the requests to the
// real helio_android.handle in Python, on a test database.
import { spawn } from "node:child_process";
import { readFileSync, existsSync } from "node:fs";
import { createInterface } from "node:readline";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { after, before, test } from "node:test";
import assert from "node:assert/strict";
import vm from "node:vm";

const HERE = dirname(fileURLToPath(import.meta.url));
const SHIM = readFileSync(join(HERE, "../assets/helio/android/helio-android.js"), "utf8");
const STATIC = join(HERE, "../helio-app/static");
const ORIGIN = "https://appassets.androidplatform.net";

let python, lines, statePath;

before(async () => {
  python = spawn("python3", ["-I", join(HERE, "bridge_stdio.py")], { stdio: ["pipe", "pipe", "inherit"] });
  lines = createInterface({ input: python.stdout })[Symbol.asyncIterator]();
  const ready = JSON.parse((await lines.next()).value);
  assert.equal(ready.ready, true);
  statePath = ready.state;
});

after(() => python.stdin.end());

// one request at a time, like the Activity's single thread
let queue = Promise.resolve();
function callPython(req) {
  const run = queue.then(async () => {
    python.stdin.write(JSON.stringify(req) + "\n");
    return JSON.parse((await lines.next()).value);
  });
  queue = run.catch(() => {});
  return run;
}

/** A fake page with the script loaded. replyDelay delays the "Java" answer. */
function page({ replyDelay = 0, hash = "" } = {}) {
  const nativeCalls = [];
  const sent = [];
  const replaced = [];
  const bridge = {
    onmessage: null,
    postMessage(data) {
      sent.push(JSON.parse(data));
      const msg = JSON.parse(data);
      if (msg.type === "view") return;  // like HelioWebRouter: Java keeps it, no answer
      callPython({ method: msg.method, path: msg.path, headers: msg.headers, body: msg.body }).then((r) => {
        const reply = JSON.stringify({
          id: msg.id, status: r.status, reason: r.reason, headers: r.headers,
          body: Buffer.from(r.body64, "base64").toString("utf8"),
        });
        setTimeout(() => bridge.onmessage && bridge.onmessage({ data: reply }), replyDelay);
      });
    },
  };
  const window = {
    location: { origin: ORIGIN, pathname: "/", search: "", hash },
    history: { replaceState: (state, title, url) => { replaced.push(url); window.location.hash = ""; } },
    HelioBridge: bridge,
    fetch: async (input, init) => {
      nativeCalls.push({ input: String(input instanceof Request ? input.url : input), init });
      return new Response("{}", { status: 200, headers: { "Content-Type": "application/json" } });
    },
  };
  // as in the browser, relative URLs are resolved against the page
  class PageRequest extends Request {
    constructor(input, init) {
      super(typeof input === "string" ? new URL(input, ORIGIN + "/") : input, init);
    }
  }
  const ctx = vm.createContext({ window, Request: PageRequest, Response, URL, DOMException, setTimeout });
  vm.runInContext(SHIM, ctx);
  return { fetch: (...a) => window.fetch(...a), nativeCalls, sent, window, replaced };
}

// the same call as postJson in app.js
function postJson(p, path, payload, signal) {
  return p.fetch(path, {
    method: "POST", headers: { "Content-Type": "application/json", "X-Helio": "1" },
    body: JSON.stringify(payload), signal, cache: "no-store",
  });
}

test("app.js and index.html are as the bridge expects them", () => {
  const app = readFileSync(join(STATIC, "app.js"), "utf8");
  assert.match(app, /fetch\(path, \{ method: "POST", headers: \{ "Content-Type": "application\/json", "X-Helio": "1" \}/);
  assert.match(readFileSync(join(STATIC, "index.html"), "utf8"), /<script src="app\.js"><\/script>/);
});

test("a POST from app.js goes through the bridge and saves as on the server", async () => {
  const p = page();
  const r = await postJson(p, "/api/weight", { date: "2026-03-10", kg: 79.4 });
  assert.equal(r.status, 200);
  assert.deepEqual(await r.json(), { ok: true });
  assert.equal(p.nativeCalls.length, 0);
  assert.equal(p.sent.length, 1);
  assert.equal(p.sent[0].headers["x-helio"], "1");
  assert.equal(p.sent[0].headers["content-type"], "application/json");
  assert.match(readFileSync(join(statePath, "weights.json"), "utf8"), /79\.4/);
});

test("without X-Helio Handler refuses, as on the server", async () => {
  const p = page();
  const r = await p.fetch("/api/weight", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ date: "2026-03-10", kg: 80 }),
  });
  assert.equal(r.status, 403);
  assert.equal(r.ok, false);
  assert.deepEqual(await r.json(), { error: { $: "err.forbidden" } });
});

test("body over the limit: 400 from Handler", async () => {
  const p = page();
  const r = await postJson(p, "/api/goal", { pad: "x".repeat(600) });
  assert.equal(r.status, 400);
  assert.deepEqual(await r.json(), { error: { $: "err.invalid_request" } });
});

test("percorso sconosciuto: 404", async () => {
  const p = page();
  const r = await postJson(p, "/api/nonesiste", {});
  assert.equal(r.status, 404);
});

test("GETs and other origins do not go through the bridge", async () => {
  const p = page();
  await p.fetch("/api/summary?date=2026-03-10", { cache: "no-store" });
  await p.fetch("https://example.org/api/weight", { method: "POST", body: "{}" });
  await p.fetch("/style.css");
  assert.equal(p.sent.length, 0);
  assert.equal(p.nativeCalls.length, 3);
});

test("AbortController aborts the request and the late answer is ignored", async () => {
  const p = page({ replyDelay: 200 });
  const ctl = new AbortController();
  const pending = postJson(p, "/api/weight", { date: "2026-03-10", kg: 80 }, ctl.signal);
  setTimeout(() => ctl.abort(), 20);
  await assert.rejects(pending, (e) => e.name === "AbortError");
  await new Promise((r) => setTimeout(r, 300));  // the answer arrives later: no error
});

test("request already aborted: it does not even start", async () => {
  const p = page();
  const ctl = new AbortController();
  ctl.abort();
  await assert.rejects(postJson(p, "/api/weight", { date: "2026-03-10", kg: 80 }, ctl.signal),
    (e) => e.name === "AbortError");
  assert.equal(p.sent.length, 0);
});

test("concurrent answers go back to the right request", async () => {
  const p = page();
  const [a, b] = await Promise.all([
    postJson(p, "/api/weight", { date: "2026-03-05", kg: 81 }),
    p.fetch("/api/weight", { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" }),
  ]);
  assert.equal(a.status, 200);
  assert.equal(b.status, 403);
});

test("without HelioBridge the script does not touch fetch", () => {
  const calls = [];
  const window = { location: { origin: ORIGIN }, fetch: (...a) => calls.push(a) };
  const ctx = vm.createContext({ window, Request, Response, URL, DOMException });
  const before = window.fetch;
  vm.runInContext(SHIM, ctx);
  assert.equal(window.fetch, before);
  assert.equal(window.HELIO_ANDROID, undefined);  // on the server app.js does not show the band menu
  assert.ok(existsSync(join(HERE, "bridge_stdio.py")));
});

test("in the app the page knows it is inside Gadgetbridge", () => {
  assert.equal(page().window.HELIO_ANDROID, true);
});

test("the band menu goes through the bridge: Python approves only known actions with X-Helio", async () => {
  const p = page();
  const ok = await postJson(p, "/api/android/action", { action: "sync" });
  assert.equal(ok.status, 200);
  assert.equal(ok.headers.get("X-Helio-Action"), "sync");
  const unknown = await postJson(p, "/api/android/action", { action: "reboot" });
  assert.equal(unknown.status, 400);
  assert.equal(unknown.headers.get("X-Helio-Action"), null);
  const noHeader = await p.fetch("/api/android/action", {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ action: "sync" }),
  });
  assert.equal(noHeader.status, 403);
  assert.equal(p.nativeCalls.length, 0);
});

test("app.js uses /api/android only with HELIO_ANDROID", () => {
  const app = readFileSync(join(STATIC, "app.js"), "utf8");
  assert.match(app, /const ANDROID = window\.HELIO_ANDROID === true;/);
  assert.match(app, /async function loadBand\(\) \{\n  if \(!ANDROID\) return;/);
  assert.match(app, /postJson\("\/api\/android\/action", \{ action \}\)/);
});

test("view to restore: read from the URL fragment, which then disappears", () => {
  const view = { tab: "sleep", date: "2026-10-11", y: 420 };
  const p = page({ hash: "#view=" + encodeURIComponent(JSON.stringify(view)) });
  assert.equal(JSON.stringify(p.window.HELIO_RESTORE), JSON.stringify(view));
  assert.deepEqual(p.replaced, ["/"]);
  assert.equal(p.window.location.hash, "");
});

test("fragment without a view or broken: nothing to restore", () => {
  for (const hash of ["", "#view=%7Brotto", "#altro", "#view=1#2"]) {
    const p = page({ hash });
    assert.equal(p.window.HELIO_RESTORE, null, hash);
    assert.deepEqual(p.replaced, hash ? ["/"] : [], hash);
  }
});

test("the view goes to Java with a message without answer, only with the expected fields", () => {
  const p = page();
  p.window.HELIO_SAVE_VIEW({ tab: "food", date: "2026-10-12", y: 80, segreto: "x" });
  assert.equal(p.sent.length, 1);
  assert.equal(JSON.stringify(p.sent[0]), JSON.stringify({ type: "view", view: { tab: "food", date: "2026-10-12", y: 80 } }));
});
