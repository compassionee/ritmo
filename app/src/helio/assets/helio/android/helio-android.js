// Helio in the Android app. The WebView does not expose the body of intercepted requests, so
// POSTs to /api/ go through the bridge to Python (WebMessageListener "HelioBridge", accepted only
// from this origin). Method, path, headers (including X-Helio) and body stay those of the
// page: Handler does the checks, as on the server. Everything else uses the normal fetch.
(() => {
  "use strict";
  const bridge = window.HelioBridge;
  if (!bridge || typeof bridge.postMessage !== "function") return;
  // app.js shows the band status and the Gadgetbridge menu only here
  window.HELIO_ANDROID = true;

  // view to restore if Android killed the process: Java passes it in the URL fragment
  // (#view=...), which is removed at once. app.js checks every value before using it.
  window.HELIO_RESTORE = null;
  const restore = /^#view=([^#]*)$/.exec(window.location.hash);
  if (restore) {
    try {
      window.HELIO_RESTORE = JSON.parse(decodeURIComponent(restore[1]));
    } catch (e) {
      window.HELIO_RESTORE = null;
    }
  }
  if (window.location.hash) {
    window.history.replaceState(null, "", window.location.pathname + window.location.search);
  }
  // section, day and scroll to Java: a message without answer, no Python
  window.HELIO_SAVE_VIEW = (view) => {
    bridge.postMessage(JSON.stringify({ type: "view", view: { tab: view.tab, date: view.date, y: view.y } }));
  };

  const NO_BODY = new Set([101, 204, 205, 304]);
  const pending = new Map();
  let nextId = 1;

  bridge.onmessage = (event) => {
    let msg;
    try {
      msg = JSON.parse(event.data);
    } catch (e) {
      return;
    }
    const p = pending.get(msg.id);
    if (!p) return;  // request already aborted
    pending.delete(msg.id);
    p.done();
    if (msg.error) {
      p.reject(new TypeError(msg.error));
      return;
    }
    p.resolve(new Response(NO_BODY.has(msg.status) ? null : msg.body,
      { status: msg.status, statusText: msg.reason || "", headers: msg.headers || {} }));
  };

  const nativeFetch = window.fetch.bind(window);
  window.fetch = function (input, init) {
    let req;
    try {
      req = new Request(input, init);
    } catch (e) {
      return nativeFetch(input, init);
    }
    const url = new URL(req.url);
    if (req.method !== "POST" || url.origin !== window.location.origin || !url.pathname.startsWith("/api/")) {
      return nativeFetch(input, init);
    }
    const signal = req.signal;
    const aborted = () => new DOMException("The operation was aborted.", "AbortError");
    return req.text().then((body) => new Promise((resolve, reject) => {
      if (signal && signal.aborted) {
        reject(aborted());
        return;
      }
      const id = nextId++;
      const onAbort = () => {
        if (pending.delete(id)) reject(aborted());
      };
      const headers = {};
      req.headers.forEach((value, name) => {
        headers[name] = value;
      });
      pending.set(id, {
        resolve, reject,
        done: () => signal && signal.removeEventListener("abort", onAbort),
      });
      if (signal) signal.addEventListener("abort", onAbort);
      bridge.postMessage(JSON.stringify({ id, method: "POST", path: url.pathname + url.search, headers, body }));
    }));
  };
})();
