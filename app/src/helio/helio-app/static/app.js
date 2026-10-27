"use strict";

/* ================= utilities ================= */
const $ = (id) => document.getElementById(id);
const NS = "http://www.w3.org/2000/svg";
const pad2 = (n) => String(n).padStart(2, "0");
function isoLocal(d) { return `${d.getFullYear()}-${pad2(d.getMonth() + 1)}-${pad2(d.getDate())}`; }
function parseIso(s) { const [y, m, d] = s.split("-").map(Number); return new Date(y, m - 1, d); }
function addDays(s, n) { const d = parseIso(s); d.setDate(d.getDate() + n); return isoLocal(d); }
// times in the data's time zone (the server's or the phone's, from the summary); before the first summary the system's
let TZ = Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
const today = () => new Intl.DateTimeFormat("sv-SE", { timeZone: TZ, year: "numeric", month: "2-digit", day: "2-digit" }).format(new Date());
let current = today();

const fmtTime = (t) => new Date(t * 1000).toLocaleTimeString(I18N.locale(), { hour: "2-digit", minute: "2-digit", timeZone: TZ });
// clock:begin
// Local clock times <-> instants in the data's time zone (TZ, an IANA name), with the offset of each date and never a
// fixed one: on the day the clock goes back (25 hours) an hour repeats, on the day it goes forward (23 hours) one is
// missing. The tests run this block on its own (js-tests/clock.test.mjs).
const clockFormats = {};
function clockParts(ts) {
  const f = clockFormats[TZ] || (clockFormats[TZ] = new Intl.DateTimeFormat("en-GB", { timeZone: TZ, hourCycle: "h23",
    year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit" }));
  const p = {};
  for (const x of f.formatToParts(new Date(ts * 1000))) if (x.type !== "literal") p[x.type] = Number(x.value);
  return p;
}
// offset of TZ from UTC at the instant ts (seconds)
function tzOffset(ts) {
  const p = clockParts(ts);
  return Date.UTC(p.year, p.month - 1, p.day, p.hour, p.minute, p.second) / 1000 - Math.floor(ts);
}
// local date ("YYYY-MM-DD") of the instant ts
function isoAt(ts) {
  const p = clockParts(ts);
  return `${p.year}-${String(p.month).padStart(2, "0")}-${String(p.day).padStart(2, "0")}`;
}
// instant of the local time hh:mm of the day iso ("YYYY-MM-DD"): a repeated time is its first occurrence (second:
// the other one), a time the clock skips becomes the one after the jump (02:30 -> 03:30)
function wallToTs(iso, hh, mm, second = false) {
  const [y, m, d] = iso.split("-").map(Number);
  const wall = Date.UTC(y, m - 1, d, hh, mm) / 1000;
  const before = tzOffset(wall - 86400), after = tzOffset(wall + 86400);  // elapsed: the offsets a day before and after
  const found = [...new Set([before, after])].map((o) => wall - o).filter((t) => tzOffset(t) === wall - t).sort((a, b) => a - b);
  if (!found.length) return wall - before;
  return second ? found[found.length - 1] : found[0];
}
// chart ticks: instants between x0 and x1 that are whole local hours multiple of every (1, 6...)
function hourTicks(x0, x1, every) {
  const out = [];
  for (let t = Math.ceil(x0 / 900) * 900; t <= x1; t += 900) {  // every quarter of an hour, whatever the offset
    const p = clockParts(t);
    if (p.minute === 0 && p.hour % every === 0) out.push(t);
  }
  return out;
}
// clock:end
const fmtNum = (n) => (n == null ? "–" : Math.round(n).toLocaleString(I18N.locale()));
const fmtDec = (n, digits = 1) => (n == null ? "–" : Number(n).toLocaleString(I18N.locale(), { maximumFractionDigits: digits, minimumFractionDigits: 0 }));
const fmtDur = (min) => I18N.dur(min);
const fmtShort = (s) => I18N.fmt(s, "dateshort");
// the day's energy as the server gives it (day_energy): today "at least", the day is not over
const kcalText = (v, partial) => (partial ? t("kcal.at_least", { v }) : fmtNum(v));
function dayLabel(s) {
  if (s === today()) return t("day.today");
  if (s === addDays(today(), -1)) return t("day.yesterday");
  return parseIso(s).toLocaleDateString(I18N.locale(), { weekday: "long", day: "numeric", month: "long" });
}
const wdLetter = (s) => parseIso(s).toLocaleDateString(I18N.locale(), { weekday: "narrow" }).toUpperCase();

function el(name, attrs = {}, parent) {
  const n = document.createElementNS(NS, name);
  for (const [k, v] of Object.entries(attrs)) n.setAttribute(k, v);
  if (parent) parent.appendChild(n);
  return n;
}
function h(tag, cls, text, parent) {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text != null) n.textContent = text;
  if (parent) parent.appendChild(n);
  return n;
}
const svg = (w, hgt) => el("svg", { viewBox: `0 0 ${w} ${hgt}`, role: "img" });
function empty(box, text) { box.replaceChildren(h("div", "empty", text)); }
// source title as published (original language) and, if present, what it measures, translated
const srcTitle = (src) => (src.what ? `${src.title}: ${tx(src.what)}` : src.title);
function link(parent, src) {
  const a = h("a", null, srcTitle(src), parent);
  a.href = src.url; a.target = "_blank"; a.rel = "noopener noreferrer";
  if (src.coi === "present") {
    const b = h("span", "coi warn", t("src.coi_badge"), parent);
    b.title = [tx(src.note), tx(src.why)].filter(Boolean).join(" ");
  }
  return a;
}
function sources(box, list) {
  box.replaceChildren();
  for (const s of list || []) link(h("span", "src-item", null, box), s);
}
// collapsible sources for cards with several references: a conflict of interest stays visible in the title
function sourcesFold(box, list) {
  box.replaceChildren();
  if (!list || !list.length) return;
  const d = h("details", "src-fold", null, box);
  const coi = list.filter((s) => s.coi === "present").length;
  h("summary", null, coi ? t("src.fold_coi", { n: list.length, coi }) : t("src.fold", { n: list.length }), d);
  sources(h("div", "h-src", null, d), list);
}

/* ================= charts ================= */
function ring(target, value, goal, cls, width = 12) {
  target.replaceChildren();
  const r = 60 - width / 2 - 2, c = 2 * Math.PI * r;
  const p = goal ? Math.max(0, (value || 0) / goal) : 0;
  el("circle", { cx: 60, cy: 60, r, fill: "none", class: "ring-track", "stroke-width": width }, target);
  const arc = (frac, klass, wdt) => el("circle", { cx: 60, cy: 60, r, fill: "none", class: klass, "stroke-width": wdt,
    "stroke-linecap": "round", "stroke-dasharray": `${c * Math.min(1, frac)} ${c}`, transform: "rotate(-90 60 60)" }, target);
  if (p > 0) arc(p, cls, width);
  if (p > 1) arc(p - 1, `${cls}-over`, width * 0.45); // second, thinner lap beyond 100%
}

function lineChart(box, pts, x0, x1, opts = {}) {
  if (!pts.length) return empty(box, opts.emptyText || t("common.no_data"));
  const W = 340, H = opts.h || 170, L = 28, R = 6, T = 8, B = opts.compact ? 4 : 20;
  const s = svg(W, H);
  const vals = pts.map((p) => p[1]);
  let lo = opts.min ?? Math.floor((Math.min(...vals) - 5) / 10) * 10;
  let hi = opts.max ?? Math.ceil((Math.max(...vals) + 5) / 10) * 10;
  if (hi - lo < 20) hi = lo + 20;
  const x = (t) => L + ((t - x0) / (x1 - x0)) * (W - L - R);
  const y = (v) => T + (1 - (v - lo) / (hi - lo)) * (H - T - B);
  for (const v of [lo, (lo + hi) / 2, hi]) {
    el("line", { x1: L, x2: W - R, y1: y(v), y2: y(v), class: "grid" }, s);
    el("text", { x: L - 4, y: y(v) + 3, "text-anchor": "end" }, s).textContent = Math.round(v);
  }
  if (!opts.compact) {
    // ticks on whole hours of the local clock (00, 06, 12, 18 for a day): 7 or 5 hours apart when the clock changes
    const long = x1 - x0 > 6 * 3600;  // elapsed: charts longer than 6 hours get a tick every 6 hours
    for (const t of hourTicks(x0, x1, long ? 6 : 1)) {
      if (long && t >= x1) continue;
      el("text", { x: x(t), y: H - 4, "text-anchor": t === x0 ? "start" : "middle" }, s).textContent = fmtTime(t).slice(0, 2);
    }
  }
  let d = "", area = "", seg = [];
  const flush = () => {
    if (seg.length > 1) {
      d += "M" + seg.map((p) => `${x(p[0]).toFixed(1)},${y(p[1]).toFixed(1)}`).join("L");
      area += `M${x(seg[0][0]).toFixed(1)},${H - B}` + seg.map((p) => `L${x(p[0]).toFixed(1)},${y(p[1]).toFixed(1)}`).join("") +
        `L${x(seg[seg.length - 1][0]).toFixed(1)},${H - B}Z`;
    } else if (seg.length === 1) el("circle", { cx: x(seg[0][0]), cy: y(seg[0][1]), r: 2, class: opts.dot || "f-hr" }, s);
    seg = [];
  };
  pts.forEach((p, i) => { if (i && p[0] - pts[i - 1][0] > (opts.gap || 900)) flush(); seg.push(p); });
  flush();
  if (area) el("path", { d: area, class: opts.area || "f-hr-soft" }, s);
  if (d) el("path", { d, fill: "none", class: opts.stroke || "s-hr", "stroke-width": 1.8, "stroke-linejoin": "round" }, s);
  box.replaceChildren(s);
}

function barChart(box, values, labels, opts = {}) {
  if (!values.some((v) => v)) return empty(box, opts.emptyText || t("common.no_data"));
  const W = 340, H = opts.h || 150, L = 4, R = 4, T = 16, B = 20;
  const s = svg(W, H);
  const max = Math.max(...values.map((v) => v || 0), opts.goal || 0) * 1.12 || 1;
  const n = values.length, gap = n > 12 ? 3 : 10;
  const bw = (W - L - R - gap * (n - 1)) / n;
  const y = (v) => T + (1 - v / max) * (H - T - B);
  values.forEach((v, i) => {
    const bx = L + i * (bw + gap);
    el("rect", { x: bx, y: T, width: bw, height: H - B - T, rx: Math.min(8, bw / 2), fill: "#1f232a" }, s);
    if (v > 0) el("rect", { x: bx, y: y(v), width: bw, height: Math.max(2, H - B - y(v)), rx: Math.min(8, bw / 2),
      class: i === opts.highlight ? opts.cls : `${opts.cls}-dim` }, s);
    if (labels[i]) el("text", { x: bx + bw / 2, y: H - 5, "text-anchor": "middle" }, s).textContent = labels[i];
    if (opts.fmt && v > 0) {
      const inside = opts.inside && H - B - y(v) > 22;
      el("text", { x: bx + bw / 2, y: inside ? y(v) + 14 : y(v) - 4, "text-anchor": "middle", class: inside ? "in-bar" : "" }, s)
        .textContent = opts.fmt(v);
    }
  });
  if (opts.goal) el("line", { x1: L, x2: W - R, y1: y(opts.goal), y2: y(opts.goal), class: "goal" }, s);
  box.replaceChildren(s);
}
const kfmt = (v) => (v >= 1000 ? t("fmt.thousands", { v: Math.round(v / 100) / 10 }) : String(Math.round(v)));

// daily trend with the personal range (dotted) as in health apps
function bandChart(box, series, band, dec) {
  const pts = series.map((p, i) => [i, p[1]]).filter((p) => p[1] != null);
  if (!pts.length) return empty(box, t("chart.no_measure"));
  const W = 340, H = 150, L = 6, R = 34, T = 12, B = 20, n = series.length;
  const s = svg(W, H);
  const vals = pts.map((p) => p[1]).concat(band && band.low != null ? [band.low, band.high] : []);
  let lo = Math.min(...vals), hi = Math.max(...vals);
  const pad = Math.max((hi - lo) * 0.25, dec ? 0.3 : 2);
  lo -= pad; hi += pad;
  const x = (i) => L + (n === 1 ? (W - L - R) / 2 : (i / (n - 1)) * (W - L - R));
  const y = (v) => T + (1 - (v - lo) / (hi - lo)) * (H - T - B);
  if (band && band.low != null) {
    el("rect", { x: L, y: y(band.high), width: W - L - R, height: y(band.low) - y(band.high), class: "band" }, s);
    for (const v of [band.low, band.high]) {
      el("line", { x1: L, x2: W - R, y1: y(v), y2: y(v), class: "band-line" }, s);
      el("text", { x: W - R + 4, y: y(v) + 3 }, s).textContent = fmtDec(v, dec);
    }
  } else {
    for (const v of [lo + pad, hi - pad]) el("text", { x: W - R + 4, y: y(v) + 3 }, s).textContent = fmtDec(v, dec);
  }
  series.forEach((p, i) => {
    if (i % 7 === (n - 1) % 7) el("text", { x: x(i), y: H - 5, "text-anchor": "middle" }, s).textContent = fmtShort(p[0]);
  });
  if (pts.length > 1) el("path", { d: "M" + pts.map((p) => `${x(p[0]).toFixed(1)},${y(p[1]).toFixed(1)}`).join("L"),
    fill: "none", class: "s-vit", "stroke-width": 2, "stroke-linejoin": "round" }, s);
  pts.forEach((p) => {
    const out = band && band.low != null && (p[1] > band.high || p[1] < band.low);
    el("circle", { cx: x(p[0]), cy: y(p[1]), r: p[0] === n - 1 ? 4.5 : 3, class: out ? "pt-out" : "f-vit" }, s);
  });
  box.replaceChildren(s);
}

// sleep stages on four rows, joined by vertical lines at every stage change
const STAGES = [["awake", "stage.awake"], ["rem", "stage.rem"], ["light", "stage.light"], ["deep", "stage.deep"]];
function stageChart(box, sl) {
  const W = 340, rowH = 46, T = 4, B = 22, H = T + rowH * 4 + B, L = 0, R = 0;
  const s = svg(W, H);
  const x = (t) => L + ((t - sl.start) / (sl.end - sl.start)) * (W - L - R);
  const rowY = (i) => T + rowH * i + 22;           // bar of each row
  const barH = 14;
  STAGES.forEach(([k, name], i) => {
    const t = el("text", { x: 2, y: T + rowH * i + 13, class: "st-label" }, s);
    t.textContent = I18N.t(name);
    const dur = el("tspan", { class: "st-dur", dx: 6 }, t);
    dur.textContent = `· ${fmtDur(sl[k] || 0)}`;
    el("rect", { x: L, y: rowY(i), width: W - L - R, height: barH, rx: barH / 2, class: "st-track" }, s);
  });
  const idx = (st) => STAGES.findIndex((z) => z[0] === st);
  let prev = null;
  for (const [a, b, st] of sl.timeline) {
    const i = idx(st);
    if (prev && prev.i !== i) {
      const y1 = rowY(prev.i) + barH / 2, y2 = rowY(i) + barH / 2;
      el("line", { x1: x(a), x2: x(a), y1, y2, class: "st-link" }, s);
    }
    prev = { i };
  }
  for (const [a, b, st] of sl.timeline) {
    const i = idx(st);
    el("rect", { x: x(a), y: rowY(i), width: Math.max(2, x(b) - x(a)), height: barH, rx: Math.min(barH / 2, Math.max(1, (x(b) - x(a)) / 2)), class: `st-${st}` }, s);
  }
  const mid = (sl.start + sl.end) / 2;
  el("text", { x: 2, y: H - 4 }, s).textContent = fmtTime(sl.start);
  el("text", { x: W / 2, y: H - 4, "text-anchor": "middle" }, s).textContent = fmtTime(mid);
  el("text", { x: W - 2, y: H - 4, "text-anchor": "end" }, s).textContent = fmtTime(sl.end);
  box.replaceChildren(s);
}

/* ================= state and navigation ================= */
let S = null, T = null, V = null, tab = "today", vitSel = "rhr";
const TITLES = { today: "tab.today", fitness: "tab.fitness", sleep: "tab.sleep", food: "title.food", health: "tab.health", profile: "ui.app.profile_method" };

function show(name) {
  tab = name;
  document.querySelectorAll(".tab").forEach((t) => { t.hidden = t.id !== `tab-${name}`; });
  document.querySelectorAll(".tabbtn").forEach((b) => b.classList.toggle("active", b.dataset.tab === name));
  $("avatar").classList.toggle("on", name === "profile");
  $("title").textContent = t(TITLES[name]);
  window.scrollTo({ top: 0 });
  viewSave();
}

// insight tags (server codes) and the section they lead to
const TAG_TAB = { fitness: "fitness", cardio: "fitness", activity: "fitness", sleep: "sleep",
  vitals: "health", recovery: "health", data: "profile", nutrition: "food" };
const TAG_GO = { fitness: "go.fitness", sleep: "go.sleep", health: "go.health", profile: "go.profile", food: "go.food" };

/* ================= TODAY ================= */
function renderToday(d) {
  const b = d.battery;
  $("battValue").textContent = b ? `${b.v}%` : "–";
  $("battChip").classList.toggle("low", !!b && b.v <= 20);
  if (b) { const hgt = Math.max(1, 14 * b.v / 100); $("battLevel").setAttribute("y", String(19 - hgt)); $("battLevel").setAttribute("height", String(hgt)); }

  $("planCard").hidden = !d.plan_today;
  $("planText").textContent = d.plan_today || "";
  $("planCard").querySelector(".tag").textContent = current === today() ? t("ui.today.planned_today") : t("today.planned");
  const ex = $("planEx"); ex.replaceChildren();
  for (const [name, vol] of (d.plan && d.plan.exercises) || []) { const li = h("li", null, null, ex); h("span", null, name, li); if (vol) h("small", null, vol, li); }
  const adv = current === today() ? d.advice : null;
  $("adviceBox").hidden = !adv;
  if (adv) {
    $("adviceBox").className = `advice a-${adv.status}`;
    $("adviceTitle").textContent = tx(adv.title);
    $("adviceText").textContent = tx(adv.text);
    sourcesFold($("adviceSrc"), adv.sources);
  }
  const strengthToday = (d.plan && d.plan.types.includes("strength")) || (d.trainings || []).some((w) => (w.label ? w.label.type : w.suggestion.type) === "strength");
  $("logStrength").hidden = !strengthToday;

  const zw = d.zone_minutes_week, zg = d.goals.zone_week;
  ring($("cardioRing"), zw, zg, "s-cardio", 13);
  $("cardioPct").textContent = `${Math.round((100 * zw) / zg)}%`;
  $("cardioSub").textContent = t("today.zone_of_goal", { v: zw, goal: zg });
  $("cardioToday").hidden = !d.zone_minutes;
  $("cardioToday").textContent = `+${d.zone_minutes}`;

  $("stepsValue").textContent = fmtNum(d.steps);
  $("stepsFill").style.width = `${Math.min(100, (100 * d.steps) / d.goals.steps)}%`;
  $("readyValue").textContent = d.readiness ? d.readiness.score : "–";
  const sl = d.sleep;
  $("sleepValue").textContent = sl ? fmtDur(sl.total) : "–";
  $("sleepTileLbl").textContent = sl && sl.score ? t("today.sleep_score", { score: sl.score }) : t("tab.sleep");
  $("sleepFill").style.width = sl ? `${Math.min(100, (100 * sl.total) / d.goals.sleep)}%` : "0%";

  const list = $("insights"); list.replaceChildren();
  for (const it of d.insights) {
    const c = h("div", "card insight", null, list);
    h("span", `tag t-${it.tone}`, t(`tag.${it.tag}`), c);
    h("h3", null, tx(it.title), c);
    h("p", null, tx(it.text), c);
    const go = TAG_TAB[it.tag];
    if (go) { const btn = h("button", "go", t(TAG_GO[go]), c); btn.addEventListener("click", () => show(go)); }
  }
  if (!d.insights.length) empty(h("div", "card", null, list), t("today.no_insights"));

  const k = d.kcal, tot = Math.max(1, k.total);
  $("kcalTotal").textContent = kcalText(k.total, k.partial);
  $("kcalRange").textContent = k.range ? t("today.kcal_range", { lo: k.range[0], hi: k.range[1] }) : t("ui.today.total_kcal_estimate");
  $("kcalBmr").textContent = fmtNum(k.bmr);
  $("kcalActive").textContent = fmtNum(k.active);
  $("kcalBmrSeg").style.width = `${(100 * k.bmr) / tot}%`;
  $("kcalActSeg").style.width = `${(100 * k.active) / tot}%`;
  $("activeValue").textContent = d.active_min;
  $("activeSub").textContent = t("today.active_goal", { goal: d.goals.active });
  $("activeFill").style.width = `${Math.min(100, (100 * d.active_min) / d.goals.active)}%`;
  $("distValue").textContent = d.jumps ? t("today.distance_jumps", { km: d.distance_km, n: d.jumps }) : t("today.distance", { km: d.distance_km });

  const hr = d.hr;
  $("hrLast").textContent = hr.last ? hr.last.v : "–";
  $("hrLastTime").textContent = hr.last ? t("today.at_time", { t: hr.last.t }) : "";
  $("hrResting").textContent = hr.resting ?? "–";
  $("hrRange").textContent = hr.min != null ? `${hr.min}–${hr.max}` : "–";
  lineChart($("hrSpark"), hr.series, d.day_start, d.day_end, { h: 70, compact: true, emptyText: t("today.wear_band_hr") });

  $("stressValue").textContent = d.stress ? d.stress.avg : "–";
  $("stressSub").textContent = d.stress ? t("today.stress_sub", { level: stressLabel(d.stress.avg), last: d.stress.last }) : t("common.no_data_lc");
  $("spo2Value").textContent = d.spo2 ? `${d.spo2.avg}%` : "–";
  $("spo2Sub").textContent = d.spo2 ? spo2Text(d.spo2.min, d.spo2.max, d.spo2.isolated_dips) : t("common.not_active");

  const p = d.profile;
  $("profileWarn").hidden = !p.missing.length;
  if (p.missing.length) {
    $("profileWarn").textContent = t("today.profile_missing", { fields: p.missing.map((m) => ({ $: `missing.${m}` })) });
  }
}

const stressLabel = (v) => t(v < 40 ? "stress.relax" : v < 60 ? "stress.light" : v < 80 ? "stress.moderate" : "stress.high");
function spo2Text(min, max, dips) {
  const parts = [t("spo2.min", { v: min })];
  if (max != null) parts.push(t("spo2.max", { v: max }));
  if (dips) parts.push(t("spo2.dips", { n: dips }));
  return parts.join(" · ");
}

/* ================= FITNESS ================= */
// original icons, drawn as strokes (24×24)
const TYPE_ICONS = {
  walk: "M13 4.5a1.8 1.8 0 1 0 0-.1M10 21l2-6 2.5 2.5V21M8.5 11.5l2.5-3.5 3 1.5 1.5 3 2.5.5M11 8l-1.2 5.2 2.7 2",
  hike: "M3 20l6-10 4 6 2.5-4L21 20zM13 4.5a1.8 1.8 0 1 0 0-.1",
  run: "M14.5 4.5a1.8 1.8 0 1 0 0-.1M6 21l3.5-6 3 2.5V21M8 10l3-3 3.5 2.5L17 12h3M11 7l-1.5 5.5 3 2",
  strength: "M3 9v6M6 7v10M18 7v10M21 9v6M6 12h12",
  swim: "M2 17c2 0 2-1.5 4-1.5s2 1.5 4 1.5 2-1.5 4-1.5 2 1.5 4 1.5 2-1 4-1M2 21c2 0 2-1.5 4-1.5s2 1.5 4 1.5 2-1.5 4-1.5 2 1.5 4 1.5 2-1 4-1M15.5 6a1.8 1.8 0 1 0 0-.1M5 13l5-4 3 2 4-3",
  rope: "M12 4.5a1.8 1.8 0 1 0 0-.1M12 7v7M9 21l3-7 3 7M8 10l4-1 4 1M5 5c-2 6 0 14 7 16 7-2 9-10 7-16",
  work: "M12 4.5a1.8 1.8 0 1 0 0-.1M12 7v8M9 21l3-6 3 6M8 10h8M16 10v4h3V9",
  other: "M2 12h4l2-5 4 10 2-5h8",
};
const TYPE_TINT = { walk: "", hike: "trek", run: "run", strength: "ex", swim: "swim", rope: "run", work: "act", other: "act" };
function typeIcon(type, parent) {
  const box = h("span", `w-ico ${TYPE_TINT[type] || "act"}`, null, parent);
  const s = el("svg", { viewBox: "0 0 24 24", "aria-hidden": "true" }, box);
  el("path", { d: TYPE_ICONS[type] || TYPE_ICONS.other, fill: "none", stroke: "currentColor", "stroke-width": 2, "stroke-linecap": "round", "stroke-linejoin": "round" }, s);
  return box;
}
function gbKindType(w) {
  return w.source === "band" ? "strength" : { running: "run", walking: "walk", exercise: "strength" }[w.kind] || "other";
}
const CONF = { high: "conf.high", medium: "conf.medium", low: "conf.low" };

function renderFitness(d, td) {
  const list = $("workoutList"); list.replaceChildren();
  const tr = d.trainings || [];
  const inside = new Set(tr.flatMap((x) => x.segments));
  const others = d.workouts.filter((w) => !(w.source === "gadgetbridge" && inside.has(w.start)) && !tr.some((x) => w.start >= x.start && w.start <= x.end));
  if (!tr.length && !others.length) {
    $("workoutSummary").textContent = "";
    empty(h("div", "card", null, list), t("fit.no_workouts"));
  } else {
    const totMin = tr.reduce((a, w) => a + w.duration_min, 0);
    $("workoutSummary").textContent = tr.length ? t("fit.summary", { n: tr.length, min: totMin }) : "";
  }
  for (const w of [...tr].reverse()) {
    const type = w.label ? w.label.type : w.suggestion.type;
    const c = h("div", "card workout", null, list);
    const row = h("button", "w-row", null, c);
    typeIcon(type, row);
    const m = h("span", "w-main", null, row);
    h("b", null, tx(w.name), m);
    const parts = [`${fmtTime(w.start)}–${fmtTime(w.end)}`, fmtDur(w.duration_min)];
    if (w.distance_km >= 0.1 && ["walk", "hike", "run"].includes(type)) parts.push(t("unit.km2", { v: w.distance_km }));
    if (w.label && w.label.load_kg && usesLoad(w.label.type)) parts.push(t("fit.load_kg", { kg: w.label.load_kg }));
    h("small", null, parts.join(" · "), m);
    const ld = h("span", "w-load", null, row);
    h("b", null, String(w.trimp), ld);
    h("small", null, t("fit.load_lc"), ld);

    const sug = h("div", `w-sug${w.label ? " done" : ""}`, null, c);
    if (w.label) {
      const box = h("span", "w-sug-text", null, sug);
      h("b", null, w.source === "manual" ? t("fit.entered_by_you") : t("fit.confirmed"), box);
      box.append(` · ${t("fit.active_kcal", { v: w.kcal })}`);
      if (w.kcal_range) h("small", null, t("fit.kcal_plausible", { lo: w.kcal_range[0], hi: w.kcal_range[1] }), box);
      const ch = h("button", "w-btn ghost", t("common.edit"), sug);
      ch.addEventListener("click", () => openSheet(w, d));
    } else {
      const box = h("span", "w-sug-text", null, sug);
      h("b", null, t("fit.looks_like", { name: w.suggestion.name }), box);
      h("small", null, `${t(CONF[w.suggestion.confidence] || "conf.low")} · ${tx(w.suggestion.reason)}`, box);
      const btns = h("span", "w-btns", null, sug);
      const ok = h("button", "w-btn", t("fit.confirm"), btns);
      ok.addEventListener("click", () => saveLabel(w, w.suggestion.type, w.suggestion.day, w.suggestion.load_kg || 0, ok));
      const ch = h("button", "w-btn ghost", t("fit.change"), btns);
      ch.addEventListener("click", () => openSheet(w, d));
    }

    const det = h("div", "w-detail", null, c); det.hidden = true;
    row.addEventListener("click", () => { det.hidden = !det.hidden; if (!det.hidden && !det.dataset.done) { fillWorkout(det, w, d); det.dataset.done = "1"; } });
  }
  if (others.length) {
    const box = h("div", "card others", null, list);
    h("h4", "sub", t("fit.other_moves"), box);
    for (const w of others) {
      const r = h("div", "o-row", null, box);
      typeIcon(gbKindType(w), r);
      const m = h("span", "w-main", null, r);
      h("b", null, tx(w.name), m);
      h("small", null, [fmtTime(w.start), fmtDur(w.duration_min), t("fit.steps_n", { n: w.steps })].join(" · "), m);
    }
  }

  $("fitKcal").textContent = kcalText(d.kcal.total, d.kcal.partial);
  barChart($("weekKcal"), td.days.map((x) => x.kcal), td.days.map((x) => wdLetter(x.date)),
    { cls: "bar-kcal", highlight: 6, fmt: kfmt, h: 150, emptyText: t("fit.no_data_7d") });

  const L = td.load;
  $("loadToday").textContent = String(d.trimp ?? 0);
  $("loadWeek").textContent = fmtNum(L.week);
  $("loadAvg").textContent = L.avg_week != null ? t("fit.load_avg", { v: L.avg_week }) : t("fit.load_avg_wait", { n: L.days });
  $("loadNote").textContent = tx(d.load_note.text);
  sourcesFold($("loadSrc"), d.load_note.sources);
  barChart($("loadChart"), L.series.map((x) => x[1] || 0), L.series.map((x, i) => (i % 7 === 6 ? fmtShort(x[0]) : "")),
    { cls: "bar-load", highlight: L.series.length - 1, h: 120, emptyText: t("fit.no_training_yet") });

  const P = d.pai;
  $("paiNote").textContent = tx(d.proprietary.pai.text);
  sourcesFold($("paiSrc"), d.proprietary.pai.sources);
  $("paiWeek").textContent = P ? P.week : "–";
  $("paiToday").textContent = P ? P.today : "–";
  $("paiFill").style.width = P ? `${Math.min(100, P.week)}%` : "0%";
  const pl = $("paiLegend"); pl.replaceChildren();
  if (P) for (const [cls, name, pts, min] of [["pai-low", "pai.low", P.low, P.min_low], ["pai-mod", "pai.moderate", P.moderate, P.min_moderate], ["pai-high", "pai.high", P.high, P.min_high]]) {
    const s = h("span", null, null, pl); h("i", `sw ${cls}`, null, s); s.append(`${t(name)} `); h("b", null, String(pts), s); s.append(` · ${fmtDur(min || 0)}`);
  }

  const zw = d.zone_minutes_week, zg = d.goals.zone_week;
  $("zoneWeek").textContent = zw;
  $("zoneWeekUnit").textContent = t("fit.zone_goal_unit", { goal: zg });
  $("zoneWeekFill").style.width = `${Math.min(100, (100 * zw) / zg)}%`;
  const zb = $("zoneBars"); zb.replaceChildren();
  const zmax = Math.max(1, ...d.zones);
  d.zones.forEach((m, i) => {
    const row = h("div", "zrow", null, zb);
    const nm = h("div", "zname", null, row); h("span", null, t(`zone.${i}`), nm);
    h("small", null, i < 4 ? t("fit.zone_range", { lo: d.zone_bounds[i], hi: d.zone_bounds[i + 1] - 1 }) : t("fit.zone_from", { lo: d.zone_bounds[i] }), nm);
    const bar = h("div", "ztrack", null, row); const f = h("div", `z${i}`, null, bar); f.style.width = `${(100 * m) / zmax}%`;
    h("span", "zmin", fmtDur(m), row);
  });
  $("hrmaxLabel").textContent = t("fit.zones_note", { hrmax: d.profile.hrmax, how: hrmaxText(d.profile) });
  sourcesFold($("hrmaxSrc"), d.hrmax_sources);
  renderVo2(d.vo2max);

  const steps = td.days.map((x) => x.steps);
  barChart($("weekSteps"), steps, td.days.map((x) => wdLetter(x.date)), { cls: "bar-steps", goal: td.goals.steps, highlight: 6, fmt: kfmt, inside: true, h: 160, emptyText: t("fit.no_steps_7d") });
  const withData = steps.filter((s) => s > 0);
  $("weekStepsSub").textContent = withData.length
    ? t("fit.steps_week", { avg: withData.reduce((a, b) => a + b, 0) / withData.length, n: steps.filter((s) => s >= td.goals.steps).length }) : "";
}

const saveTimeoutMs = () => window.HELIO_SAVE_TIMEOUT_MS || 20000;  // tests shorten it
const postLabel = (payload) => postJson("/api/label", payload);
// server error: translated message ({"$": key}) or text
const errText = (body, status) => (body && body.error ? tx(body.error) : t("err.status", { status }));
async function postJson(path, payload) {
  // without an answer within 20 s it is aborted: the button never stays stuck on "Saving…"
  const ctl = new AbortController();
  const timer = setTimeout(() => ctl.abort(), saveTimeoutMs());
  try {
    const r = await fetch(path, { method: "POST", headers: { "Content-Type": "application/json", "X-Helio": "1" }, body: JSON.stringify(payload), signal: ctl.signal, cache: "no-store" });
    const body = await r.json().catch(() => ({}));
    if (r.status === 401) throw new Error(t("err.session_expired"));
    if (!r.ok) throw new Error(errText(body, r.status));
    return body;
  } catch (e) {
    if (e.name === "AbortError") throw new Error(t("err.timeout"));
    if (e instanceof TypeError) throw new Error(t("err.unreachable"));
    throw e;
  } finally {
    clearTimeout(timer);
  }
}

let toastTimer = null;
function toast(msg, isError = false) {
  const el_ = $("toast");
  el_.textContent = msg; el_.classList.toggle("err", isError); el_.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { el_.hidden = true; }, isError ? 6000 : 2500);
}

let saving = false;
async function saveLabel(w, type, day, loadKg, btn, manual = false, oldStart = null, workLevel = null) {
  if (saving) return;  // double tap: a single save
  saving = true;
  const label = btn ? btn.textContent : "";
  const inSheet = !$("sheet").hidden;
  if (btn) { btn.disabled = true; btn.textContent = t("common.saving"); }
  $("sheetErr").hidden = true;
  let ok = false;
  try {
    // first the new times, then the old ones are removed: if something goes wrong nothing is lost
    await postLabel({ start: w.start, end: w.end, type, day: type === "strength" ? day : null, load_kg: loadKg || 0, manual,
      work_level: type === "work" ? workLevel : null });
    if (manual && type && oldStart && oldStart !== w.start) await postLabel({ start: oldStart, end: oldStart + 60, type: null, manual: true });
    ok = true;
  } catch (e) {
    if (inSheet) { $("sheetErr").textContent = t("err.not_saved", { msg: e.message }); $("sheetErr").hidden = false; }
    else toast(t("err.not_saved", { msg: e.message }), true);
  } finally {
    saving = false;
    if (btn) { btn.disabled = false; btn.textContent = ok ? label : t("common.retry"); }
  }
  if (!ok) return;
  closeSheet();
  toast(type ? t("common.saved") : t("fit.label_removed"));
  await load();
}

let sheetW = null, sheetType = null, sheetDay = null, sheetManual = false, sheetDayStart = 0, sheetLoadTouched = false;
let sheetWork = null;
// default added load of each strength day (from the plan): the same for the whole session
const strengthDayLoad = (day) => (S && S.strength_day_loads && day ? S.strength_day_loads[String(day)] : 0) || 0;
const hhmm = (ts) => {
  // time for <input type="time"> fields: always HH:MM, whatever the language
  const p = new Intl.DateTimeFormat("en-GB", { hour: "2-digit", minute: "2-digit", hourCycle: "h23", timeZone: TZ }).formatToParts(new Date(ts * 1000));
  return `${p.find((x) => x.type === "hour").value}:${p.find((x) => x.type === "minute").value}`;
};
function openSheet(w, d) {
  sheetManual = !w || w.source === "manual";
  sheetDayStart = d.day_start;
  sheetW = w;
  sheetType = w ? (w.label ? w.label.type : w.suggestion.type) : "other";
  sheetDay = (w && ((w.label && w.label.day) || w.suggestion.day)) || (d.plan && d.plan.day) || null;
  $("sheetTitle").textContent = w ? t("ui.sheet.what_activity_was_it") : t("fit.add_activity");
  $("sheetTime").textContent = w ? t("fit.sheet_time", { from: w.start, to: w.end, min: w.duration_min, hr: w.avg_hr ?? "–" })
    : t("fit.sheet_new", { day: dayLabel(current) });
  $("sheetTimeRow").hidden = !sheetManual;
  if (sheetManual) { $("sheetFrom").value = w ? hhmm(w.start) : "09:00"; $("sheetTo").value = w ? hhmm(w.end) : "10:00"; }
  sheetLoadTouched = false;
  const startLoad = w && w.label ? w.label.load_kg : sheetType === "strength" ? (w && w.suggestion.load_kg) || strengthDayLoad(sheetDay) : 0;
  $("sheetLoad").value = startLoad ? startLoad : "";
  $("sheetRemove").hidden = !(w && w.label);
  $("sheetErr").hidden = true;
  for (const [id, key] of [["sheetSave", "common.save"], ["sheetRemove", "ui.sheet.remove_label"]]) { $(id).disabled = false; $(id).textContent = t(key); }
  const grid = $("sheetTypes"); grid.replaceChildren();
  for (const at of d.activity_types) {
    const b = h("button", `type-btn${at.key === sheetType ? " on" : ""}`, null, grid);
    typeIcon(at.key, b);
    h("span", null, t(`act.${at.key}`), b);
    b.addEventListener("click", () => { sheetType = at.key; grid.querySelectorAll(".type-btn").forEach((x) => x.classList.toggle("on", x === b)); sheetExtras(); });
  }
  // intensity of a work activity: its own choice, otherwise the Profile's default (shown, but only saved if chosen)
  sheetWork = (w && w.label && w.label.work_level) || null;
  renderWorkLevels(d);
  const days = $("sheetDays"); days.replaceChildren();
  for (const n of [1, 2, 3]) {
    const b = h("button", n === sheetDay ? "on" : "", t("plan.strength_day_n", { n }), days);
    b.addEventListener("click", () => {
      sheetDay = n; days.querySelectorAll("button").forEach((x) => x.classList.toggle("on", x === b));
      if (!sheetLoadTouched) $("sheetLoad").value = strengthDayLoad(n) || "";
    });
  }
  sheetExtras();
  $("sheet").hidden = false;
  document.body.classList.add("noscroll");
}
// the added load applies only to the types given by the server (walking, hiking, strength)
const usesLoad = (type) => !!(S && S.activity_types.some((at) => at.key === type && at.load));
function renderWorkLevels(d) {
  const box = $("sheetWork"); box.replaceChildren();
  const def = (d.profile && d.profile.work_level) || "moving";
  const shown = sheetWork || def;
  for (const lv of d.work_levels || []) {
    const b = h("button", lv.key === shown ? "on" : "", null, box);
    b.dataset.level = lv.key;
    h("b", null, t(`work.${lv.key}`), b);
    h("small", null, t("work.src", { code: lv.code, met: lv.met }), b);
    b.addEventListener("click", () => {
      sheetWork = lv.key;
      box.querySelectorAll("button").forEach((x) => x.classList.toggle("on", x === b));
      $("sheetWorkNote").textContent = t(`work.${lv.key}.desc`);
    });
  }
  $("sheetWorkNote").textContent = `${t(`work.${shown}.desc`)}${sheetWork ? "" : " " + t("fit.work_level_note", { level: t(`work.${def}`) })}`;
}
function sheetWorkLevel() {
  // the choice made now or the one the activity already had; nothing = the Profile's default
  return sheetType === "work" ? sheetWork : null;
}
function sheetExtras() {
  $("sheetWorkRow").hidden = $("sheetWorkNote").hidden = sheetType !== "work";
  $("sheetDayRow").hidden = sheetType !== "strength";
  $("sheetLoadRow").hidden = !usesLoad(sheetType);
  if (sheetType === "strength" && !sheetLoadTouched && $("sheetLoad").value === "") $("sheetLoad").value = strengthDayLoad(sheetDay) || "";
}
function closeSheet() { $("sheet").hidden = true; document.body.classList.remove("noscroll"); }

function fillWorkout(det, w, d) {
  const st = h("div", "w-stats", null, det);
  for (const [v, key] of [[fmtDur(w.duration_min), "fit.stat_duration"], [w.steps != null ? fmtNum(w.steps - (w.jumps || 0)) : "–", "fit.stat_steps"],
    [w.distance_km != null ? fmtDec(w.distance_km, 2) : "–", "fit.stat_km"], [w.avg_hr ?? "–", "fit.stat_avg_hr"], [w.max_hr ?? "–", "fit.stat_max_hr"],
    [fmtNum(w.kcal), "fit.stat_kcal"]]) {
    const b = h("div", null, null, st); h("b", null, String(v), b); h("span", null, t(key), b);
  }
  const method = w.kcal_method ? tx(w.kcal_method) : t("fit.method_default");
  h("small", "w-method", w.kcal_range ? t("fit.method_range", { method, lo: w.kcal_range[0], hi: w.kcal_range[1] }) : t("fit.method", { method }), det);
  if (w.jumps) h("small", "w-method", t("fit.jumps_note", { n: w.jumps }), det);
  if (w.label && w.label.type === "strength") {
    const b = h("button", "w-btn ghost", t("strength.log_sets"), det);
    b.addEventListener("click", (e) => { e.stopPropagation(); openStrength(w.label.day || null); });
  }
  if (w.rope && w.rope.length) {
    const mins = w.rope.reduce((a, [s, e]) => a + Math.round((e - s) / 60), 0);
    h("small", "w-method", t("fit.rope_note", { min: mins, spans: w.rope.map(([s, e]) => `${fmtTime(s)}–${fmtTime(e)}`).join(", ") }), det);
  }
  if (w.segments && w.segments.length) h("small", "w-method", t("fit.segments_note", { n: w.segments.length }), det);
  const chart = h("div", "chart", null, det);
  lineChart(chart, w.hr, w.start, w.end, { h: 110, gap: 300 });
  const zt = w.zones.reduce((a, b) => a + b, 0);
  if (!zt) return;
  const zs = h("div", "zstack", null, det);
  w.zones.forEach((m, i) => { if (m) { const z = h("div", `z${i}`, null, zs); z.style.width = `${(100 * m) / zt}%`; } });
  const lg = h("div", "legend", null, det);
  w.zones.forEach((m, i) => { if (m) { const s = h("span", null, null, lg); h("i", `sw z${i}`, null, s); s.append(`${t(`zone.${i}`)} `); h("b", null, `${m}′`, s); } });
}

/* ================= SLEEP ================= */
function renderSleep(d, td) {
  const sl = d.sleep;
  $("sleepScore").hidden = !(sl && sl.score);
  $("sleepScore").textContent = sl && sl.score ? t("sleep.zepp_score", { score: sl.score }) : "";
  $("sleepScoreNote").hidden = !(sl && sl.score);
  $("sleepScoreNote").textContent = t("sleep.score_note", { text: d.proprietary.sleep_score.text });
  const si = d.sleep_insight;
  $("sleepTitle").textContent = si ? tx(si.title) : t("sleep.none_title");
  $("sleepText").textContent = si ? tx(si.text) : t("sleep.none_text");
  if (sl) stageChart($("stageChart"), sl); else empty($("stageChart"), t("sleep.no_night"));
  $("sleepBed").textContent = sl && sl.bed_start ? `${fmtTime(sl.bed_start)} – ${fmtTime(sl.bed_end)}` : "–";
  $("sleepSource").textContent = sl ? t(sl.source === "band" ? "sleep.src_band" : "sleep.src_samples") : "–";
  const naps = sl && sl.naps ? sl.naps : [];
  $("sleepNapWrap").hidden = !naps.length;
  $("sleepNaps").textContent = naps.length ? t("sleep.naps", { n: naps.length, min: sl.nap_min }) : "–";

  const hl = d.sleep_highlights || [], box = $("sleepHighlights"); box.replaceChildren();
  const ok = hl.filter((x) => x.ok).length;
  $("hlTitle").textContent = hl.length ? t("sleep.hl_title", { ok, n: hl.length }) : t("ui.sleep.key_points");
  for (const x of hl) {
    const r = h("div", `hl${x.ok ? "" : " bad"}`, null, box);
    const top = h("div", "row", null, r);
    h("b", null, tx(x.title), top);
    h("span", `pill ${x.ok ? "good" : x.level === 2 ? "info" : "warn"}`, tx(x.label), top);
    h("div", "hl-val", t("sleep.hl_value", { value: x.display, ref: x.ref }), r);
    sources(h("div", "hl-src", null, r), x.sources);
  }
  if (!hl.length) empty(box, t("common.no_data"));

  $("sleepHr").textContent = sl && sl.avg_hr ? sl.avg_hr : "–";
  $("sleepHrSub").textContent = sl && sl.min_hr ? t("sleep.hr_sub", { min: sl.min_hr }) : t("ui.sleep.average_bpm");
  const hb = d.baselines && d.baselines.hrv;
  const hrvN = sl && sl.hrv != null ? sl.hrv : d.hrv && d.hrv.night;
  $("hrvValue").textContent = hrvN ?? "–";
  $("hrvSub").textContent = hrvN == null ? t("common.no_data_lc") : hb && hb.mean != null ? t("sleep.hrv_band", { lo: hb.low, hi: hb.high }) : t("sleep.hrv_median");
  $("respValue").textContent = sl && sl.resp != null ? fmtDec(sl.resp) : "–";
  $("sleepSpo2").textContent = sl && sl.spo2 != null ? `${sl.spo2}%` : "–";
  $("sleepSpo2Sub").textContent = sl && sl.spo2_min != null ? spo2Text(sl.spo2_min, null, sl.spo2_dips) : t("common.not_active");
  $("sleepTemp").textContent = sl && sl.temp != null ? `${fmtDec(sl.temp)}°` : "–";

  renderSri(d.sri);
  barChart($("sleepWeek"), td.days.map((x) => x.sleep || 0), td.days.map((x) => wdLetter(x.date)),
    { goal: td.goals.sleep, cls: "bar-sleep", highlight: 6, fmt: (v) => `${Math.floor(v / 60)}:${pad2(v % 60)}`, inside: true, h: 160, emptyText: t("sleep.none_7d") });
}

/* ================= HEALTH ================= */
const VIT_ORDER = ["rhr", "hrv", "resp", "spo2", "temp"];
const VIT_STATUS = { normal: "vit.in_band", above: "vit.above", below: "vit.below" };
function vitStatusText(k, v) {
  if (v.mean == null) return null;
  return t(VIT_STATUS[v.status] || "vit.collecting");
}

// the chip chosen by the user (for the day shown): the automatic choice of the value out of range applies only
// until a chip is tapped, otherwise every redraw would bring it back (the chips seemed not to work)
let vitPick = null;
function renderVitals(vits) {
  if (vitPick && vitPick.day !== current) vitPick = null;
  const out = VIT_ORDER.filter((k) => vits[k].mean != null && (vits[k].status === "above" || vits[k].status === "below"));
  const ready = VIT_ORDER.filter((k) => vits[k].mean != null);
  if (out.length) {
    const k = out[0], v = vits[k];
    $("vitTitle").textContent = t(v.status === "above" ? "vit.title_above" : "vit.title_below", { name: t(`vit.short.${k}`) });
    $("vitText").textContent = t(k === "rhr" ? "vit.text_rhr" : k === "hrv" ? "vit.text_hrv" : "vit.text_other");
    vitSel = vitPick ? vitPick.key : k;
  } else if (ready.length) {
    $("vitTitle").textContent = t("vit.all_in_band");
    $("vitText").textContent = t("vit.all_in_band_text");
  } else {
    const n = Math.max(...VIT_ORDER.map((k) => vits[k].n_base || 0));
    $("vitTitle").textContent = t("vit.building");
    $("vitText").textContent = t("vit.building_text", { n });
  }
  const chips = $("vitChips"); chips.replaceChildren();
  for (const k of VIT_ORDER) {
    const b = h("button", k === vitSel ? "on" : "", t(`vit.short.${k}`), chips);
    b.addEventListener("click", () => { vitSel = k; vitPick = { key: k, day: current }; renderVitals(vits); });
  }
  const v = vits[vitSel];
  $("vitName").textContent = tx(v.title);
  const lv = v.last ? v.last[1] : null;
  $("vitValue").textContent = lv == null ? "–" : fmtDec(lv, v.decimals);
  $("vitUnit").textContent = lv == null ? "" : `${tx(v.unit)}${v.last[0] !== current ? ` · ${fmtShort(v.last[0])}` : ""}`;
  const st = vitStatusText(vitSel, v);
  $("vitPill").hidden = !st;
  $("vitPill").textContent = st ? (vitSel === "rhr" || vitSel === "hrv" ? t("vit.avg7", { status: st }) : st) : "";
  $("vitPill").className = `pill ${v.status === "normal" ? "good" : v.status === "above" || v.status === "below" ? "warn" : "info"}`;
  bandChart($("vitChart"), v.series, v.mean != null ? { low: v.low, high: v.high } : null, v.decimals);
}

function renderHealth(d, td, vits) {
  renderVitals(vits);
  const r = d.readiness;
  $("readyScore").textContent = r ? r.score : "–";
  $("readyLabel").textContent = r ? tx(r.label) : t("health.ready_wait");
  const parts = $("readyParts"); parts.replaceChildren();
  if (r) r.parts.forEach((p) => h("li", null, tx(p), parts));

  const hr = td.health, sum = $("healthSummary"); sum.replaceChildren();
  const ready = hr.items.filter((i) => i.status === "ok");
  if (!ready.length) h("p", "muted", t("health.collecting"), sum);
  else {
    if (hr.strengths.length) { const row = h("div", "hs-row", null, sum); h("b", null, t("health.strengths"), row); row.append(I18N.list(hr.strengths)); }
    if (hr.improve.length) { const row = h("div", "hs-row", null, sum); h("b", null, t("health.improve"), row); row.append(I18N.list(hr.improve)); }
    const mid = ready.filter((i) => i.level === 2).map((i) => i.title);
    if (mid.length) { const row = h("div", "hs-row", null, sum); h("b", null, t("health.average"), row); row.append(I18N.list(mid)); }
  }
  const list = $("healthItems"); list.replaceChildren();
  for (const it of hr.items) {
    const c = h("div", "card hcard", null, list);
    const head = h("div", "h-head", null, c);
    h("h4", null, tx(it.title), head);
    h("span", "h-period", tx(it.period), head);
    const val = h("div", "h-value", null, c);
    if (it.status === "ok") {
      h("b", null, it.display ? tx(it.display) : fmtDec(it.value, 2), val);
      h("span", "unitlbl", it.display ? "" : tx(it.unit), val);
      h("span", `pill lv${it.level}`, tx(it.label), val);
    } else h("span", "pill wait", tx(it.label), val);
    const [lo, hi] = it.scale;
    const bar = h("div", "bandbar", null, c);
    const labels = h("div", "bandlabels", null, c);
    const pos = (v) => (100 * (Math.min(hi, Math.max(lo, v)) - lo)) / (hi - lo);
    for (const b of it.bands) {
      const a = Math.max(lo, b.from), z = Math.min(hi, b.to);
      if (z <= a) continue;
      const seg = h("div", `bseg lv${b.level}`, null, bar);
      seg.style.flex = String(z - a); seg.title = tx(b.label);
      if (it.status === "ok" && it.value >= b.from && it.value < b.to) seg.classList.add("on");
      if (a > lo) { const l = h("span", null, it.key === "sleep" ? t("unit.hours_short", { v: a }) : fmtNum(a), labels); l.style.left = `${pos(a)}%`; }
    }
    if (it.status === "ok") { const m = h("div", "marker", null, bar); m.style.left = `${pos(it.value)}%`; }
    h("p", "h-text", tx(it.text), c);
    sources(h("div", "h-src", null, c), it.sources);
  }

  const st = d.stress;
  $("stressCard").hidden = !st;
  $("stressNote").textContent = tx(d.proprietary.stress.text);
  sourcesFold($("stressSrc"), d.proprietary.stress.sources);
  if (st) {
    $("stressAvgLbl").textContent = t("health.stress_avg", { avg: st.avg, max: st.max });
    lineChart($("stressChart"), st.series, d.day_start, d.day_end, { h: 120, min: 0, max: 100, stroke: "s-stress", area: "f-stress-soft", dot: "f-hr", gap: 3600 });
    const bar = $("stressBar"), lg = $("stressLegend"); bar.replaceChildren(); lg.replaceChildren();
    for (const [k, cls, name] of [["relax", "relax", "stress.l_relax"], ["light", "light-s", "stress.l_light"], ["moderate", "moderate", "stress.l_moderate"], ["high", "high", "stress.l_high"]]) {
      const pct = st.share[k] || 0;
      if (pct > 0) { const s = h("i", `seg ${cls}`, null, bar); s.style.width = `${pct}%`; }
      const l = h("span", null, null, lg); h("i", `sw ${cls}`, null, l); l.append(`${t(name)} `); h("b", null, `${pct}%`, l);
    }
  }
}

/* ================= PROFILE ================= */
function renderProfile(d) {
  const p = d.profile;
  const sexL = t({ M: "sex.male", F: "sex.female" }[p.sex] || "sex.unknown");
  $("profileText").textContent = t("prof.summary", {
    age: p.age, sex: sexL, weight: p.weight, wsrc: t(`wsrc.${p.weight_src}`), height: p.height, stride: Math.round(p.stride_m * 100),
    hrmax: p.hrmax, how: hrmaxText(p), bmr: p.bmr, steps: d.goals.steps, active: d.goals.active, sleep: d.goals.sleep, zone: d.goals.zone_week });
  const manual = Object.entries(p.src || {}).filter(([, v]) => v === "app").map(([k]) => PROF_NAMES[k]).filter(Boolean).map((k) => t(k));
  if (manual.length) $("profileText").textContent += " " + t("prof.manual", { fields: manual });
  sourcesFold($("kcalSrc"), d.kcal_sources);
  renderSources(d.sources_index);
  exportLinks();
  renderLangChoice();
  const q = d.quality;
  $("qualityDay").textContent = dayLabel(current);
  $("qualitySummary").textContent = t("quality.summary", { hr: q.hr_fixes.length, hr_n: q.hr_samples, motion: q.spo2_motion, dips: q.spo2_dips, spo2_n: q.spo2_samples });
  const box = $("qualityRules"); box.replaceChildren();
  for (const r of q.rules) {
    const c = h("div", "rule", null, box);
    h("h4", null, tx(r.title), c);
    h("p", null, tx(r.text), c);
    sources(h("div", "h-src", null, c), r.sources);
  }
}

const PROF_NAMES = { age: "prof.f.age", sex: "prof.f.sex", height: "prof.f.height", hrmax: "prof.f.hrmax", rest_hr: "prof.f.rest_hr", stride_m: "prof.f.stride",
  steps_goal: "prof.f.steps_goal", sleep_goal_min: "prof.f.sleep_goal", active_goal: "prof.f.active_goal", zone_goal_week: "prof.f.zone_goal",
  altitude_goal_m: "prof.f.altitude_goal", work_level: "prof.f.work_level" };

/* Profile sheet: values entered by hand, only in the app's data; the server checks the ranges */
const PROF_FIELDS = [
  // [id, field, from field to saved value, from saved value to field, hint from the starting value]
  ["profBirth", "birth_date", (v) => v, (v) => v, () => ""],
  ["profHeight", "height_cm", Number, String, (b) => (b.height_cm ? fmtDec(b.height_cm) : "")],
  ["profHrmax", "hrmax", Number, String, (b, e) => t("prof.hint_estimate", { v: e.hrmax_pred })],
  ["profRest", "rest_hr", Number, String, () => t("prof.hint_band")],
  ["profStride", "stride_m", (v) => Number(v) / 100, (v) => String(Math.round(v * 100)), (b, e) => String(Math.round(e.stride_m * 100))],
  ["profSteps", "steps_goal", Number, String, (b, e) => String(e.steps_goal)],
  ["profSleep", "sleep_goal_min", (v) => Math.round(Number(v) * 60), (v) => String(v / 60), (b, e) => fmtDec(e.sleep_goal_min / 60, 2)],
  ["profActive", "active_goal", Number, String, (b, e) => String(e.active_goal)],
  ["profZone", "zone_goal_week", Number, String, (b, e) => String(e.zone_goal_week)],
  ["profAltitude", "altitude_goal_m", Number, String, () => t("prof.hint_none")],
  ["profAltitudeName", "altitude_goal_name", (v) => v, (v) => v, () => t("prof.hint_altitude_name")],
];

async function openProfile() {
  let pr;
  try { pr = await getJson("/api/profile"); } catch (e) { toast(t("err.failed", { msg: e.message }), true); return; }
  const saved = pr.saved || {}, base = pr.base || {}, eff = pr.effective || {};
  for (const [id, key, , toField, hint] of PROF_FIELDS) {
    $(id).value = saved[key] != null ? toField(saved[key]) : "";
    $(id).placeholder = hint(base, eff);
  }
  $("profSex").value = saved.sex || "";
  const work = $("profWork"); work.replaceChildren();
  for (const lv of pr.work_levels || []) {
    const o = h("option", null, `${t(`work.${lv.key}`)} · ${t("work.src", { code: lv.code, met: lv.met })}`, work);
    o.value = lv.key;
  }
  work.value = saved.work_level || eff.work_level || "moving";
  $("profWorkNote").textContent = t(`work.${work.value}.desc`);
  $("profSex").options[0].textContent = base.sex ? t("prof.sex_from_gb", { sex: t(base.sex === "F" ? "sex.female" : "sex.male") }) : t("sex.unknown");
  $("profErr").hidden = true;
  if (S) sourcesFold($("profSrc"), S.kcal_sources);
  openModal("profSheet");
}

function readProfile() {
  const out = {};
  for (const [id, key, fromField] of PROF_FIELDS) {
    const raw = String($(id).value).trim();
    const num = id === "profAltitudeName" || id === "profBirth" ? raw : raw.replace(",", ".");
    out[key] = num === "" ? null : fromField(num);
  }
  out.sex = $("profSex").value || null;
  out.work_level = $("profWork").value || null;
  return out;
}
function hrmaxText(p) {
  if (p.hrmax_src === "observed") {
    return p.hrmax_obs_at ? t("hrmax.observed_on", { d: p.hrmax_obs_at, pred: p.hrmax_pred }) : t("hrmax.observed", { pred: p.hrmax_pred });
  }
  if (p.hrmax_src === "set") return t("hrmax.set");
  return p.hrmax_obs ? t("hrmax.age_peak", { peak: p.hrmax_obs }) : t("hrmax.age");
}

function renderSources(idx) {
  const box = $("sourcesList"); box.replaceChildren();
  const order = { present: 0, not_declared: 1, none: 2 };
  const sorted = [...idx.sources].sort((a, b) => order[a.coi] - order[b.coi] || a.title.localeCompare(b.title));
  for (const s of sorted) {
    const r = h("div", `src-row coi-${s.coi}`, null, box);
    link(r, s);
    h("small", null, t("src.row", { kind: t(`src.kind.${s.kind}`), coi: t(`src.coi.${s.coi}`), note: tx(s.note), why: s.why ? tx(s.why) : "" }).trim(), r);
  }
  $("sourcesSummary").textContent = t("src.show_all", { n: idx.sources.length, coi: idx.sources.filter((s) => s.coi === "present").length });
  const rm = $("removedList"); rm.replaceChildren();
  for (const x of idx.removed) {
    // original source title (if any) and description in the chosen language
    const li = h("li", null, null, rm);
    h("b", null, x.title ? `${x.title} — ${tx(x.what)}` : tx(x.what), li);
    li.append(`: ${tx(x.why)}`);
  }
}

function exportLinks() {
  const days = Number($("exportRange").value), fmt = $("exportFmt").value;
  const from = addDays(today(), -(days - 1)), to = today();
  for (const [id, kind] of [["expDays", "days"], ["expTrain", "workouts"], ["expStrength", "strength"], ["expFood", "food"]]) {
    $(id).href = `/api/export.csv?kind=${kind}&from=${from}&to=${to}&fmt=${fmt}&lang=${I18N.lang()}`;
  }
}

/* ================= VO2max, week, regularity ================= */
function renderVo2(v) {
  $("vo2Value").textContent = v.value != null ? fmtDec(v.value) : "–";
  $("vo2Range").textContent = v.value != null ? t("vo2.range", { lo: v.low, hi: v.high, met: v.met }) : "";
  const pts = v.series.map((p, i) => [i, p[1]]).filter((p) => p[1] != null);
  if (pts.length) bandChart($("vo2Chart"), v.series, null, 1); else empty($("vo2Chart"), t("vo2.need_rest"));
  const a = v.altitude;
  $("vo2Alt").hidden = !a;
  $("vo2Alt").textContent = a ? t(a.extrapolated ? "vo2.altitude_extrap" : "vo2.altitude", {
    m: a.m, name: a.name || t("vo2.altitude_noname"), pct: Math.round(a.fraction * 100), lo: Math.round(a.frac_low * 100), hi: Math.round(a.frac_high * 100), v: a.value }) : "";
  $("vo2Text").textContent = tx(v.text);
  sourcesFold($("vo2Src"), v.sources);
}

const STATUS_TXT = { done: "wk.done", different: "wk.other", skipped: "wk.skipped", rest: "wk.rest", extra: "wk.extra", today: "wk.today", missing: "wk.missing" };
let weekWhich = "auto", WEEK = null, STRW = null;
function renderWeek(w) {
  $("weekCard").hidden = !w;
  if (!w) return;
  const c = w.current, df = w.diff;
  const prevMode = w.which === "previous";
  $("weekCur").classList.toggle("on", !prevMode); $("weekPrev").classList.toggle("on", prevMode);
  const title = t(prevMode ? "wk.previous" : "wk.current", { from: w.start, to: w.end });
  $("weekTitle").textContent = c.plan_total ? t("wk.title_plan", { title, done: c.plan_done, total: c.plan_total }) : title;
  const days = $("weekDays"); days.replaceChildren();
  for (const x of c.days) {
    const b = h("div", `wd st-${x.status}`, null, days);
    h("b", null, I18N.fmt(x.date, "weekdayshort"), b);
    h("small", null, STATUS_TXT[x.status] ? t(STATUS_TXT[x.status]) : "", b);
    const plan = x.plan || t("wk.no_plan");
    b.title = x.done.length ? t("wk.day_done", { plan, done: x.done.map((w2) => tx(w2.name)) }) : plan;
  }
  const st = $("weekStats"); st.replaceChildren();
  // comparison with the previous week: green if it goes the useful way (better = 1 more is better, -1 less is better, 0 neutral)
  const row = (key, value, k, unit = "", dec = 0, better = 1) => {
    const r = h("div", "ws-row", null, st); h("span", null, t(key), r);
    const v = h("b", null, value, r);
    if (k && df[k] != null && df[k] !== 0) {
      const cls = better === 0 ? "flat" : df[k] * better > 0 ? "up" : "down";
      h("span", `delta ${cls}`, ` ${df[k] > 0 ? "+" : ""}${fmtDec(df[k], dec)}${unit}`, v);
    }
  };
  row("wk.zone", fmtNum(c.zone_minutes), "zone_minutes");
  row("wk.strength", t("wk.strength_value", { days: c.strength_days, min: c.strength_min }), "strength_days");
  row("wk.load", fmtNum(c.trimp), "trimp", "", 0, 0);
  row("wk.steps", c.steps_avg != null ? fmtNum(c.steps_avg) : "–", "steps_avg");
  row("wk.sleep", c.sleep_avg != null ? fmtDur(c.sleep_avg) : "–", "sleep_avg", ` ${t("unit.min")}`, 0, 0);
  row("wk.rhr", c.rhr_avg != null ? t("unit.bpm_v", { v: c.rhr_avg }) : "–", "rhr_avg", "", 1, -1);
  row("wk.hrv", c.hrv_avg != null ? t("unit.ms_v", { v: c.hrv_avg }) : "–", "hrv_avg", ` ${t("unit.ms")}`);
  if (c.sri != null) row("wk.sri", String(c.sri), "sri");
  if (c.weight) row("wk.weight", t("unit.kg_v", { v: c.weight.kg }), "weight", ` ${t("unit.kg")}`, 1, 0);
  sourcesFold($("weekSrc"), w.sources);
}

function renderSri(r) {
  $("sriValue").textContent = r.value != null ? r.value : "–";
  $("sriUnit").textContent = r.value != null ? (r.prev != null ? t("sri.of100_prev", { prev: r.prev }) : t("sri.of100")) : t("sri.collecting", { have: r.pairs_h, need: r.need_h });
  const hm = (m) => (m == null ? "–" : `${pad2(Math.floor(m / 60))}:${pad2(m % 60)}`);
  $("sriOnset").textContent = r.onset.mean != null ? t("sri.time_sd", { time: hm(r.onset.mean), sd: r.onset.sd }) : "–";
  $("sriWake").textContent = r.wake.mean != null ? t("sri.time_sd", { time: hm(r.wake.mean), sd: r.wake.sd }) : "–";
  $("sriText").textContent = tx(r.text);
  sourcesFold($("sriSrc"), r.sources);
}

/* ================= strength log ================= */
let STR = null, strDay = null;
function renderStrength(st) {
  STRW = st;
  $("strengthWeek").textContent = st ? (st.week.volume ? t("str.week_volume", { n: st.week.days, sets: st.week.sets, kg: st.week.volume })
    : t("str.week", { n: st.week.days, sets: st.week.sets })) : "";
  const box = $("strengthList"); box.replaceChildren();
  if (!st || !st.progress.length) { empty(box, t("str.none")); }
  else for (const ex of st.progress) {
    const r = h("div", "str-row", null, box);
    h("b", null, ex.name, r);
    const last = ex.sessions[ex.sessions.length - 1];
    let txt;
    const loadTxt = last.best_kg ? ` ${t("str.with_kg", { kg: last.best_kg })}` : "";
    if (ex.kind === "time") txt = t("str.time_sets", { sets: last.sets, sec: last.sec }) + loadTxt;
    else if (ex.kind === "system" && last.e1rm) txt = t("str.rep_sets", { sets: last.sets, reps: last.reps }) + loadTxt + " · "
      + t(last.e1rm_added >= 0 ? "str.e1rm_plus" : "str.e1rm_minus", { kg: Math.abs(last.e1rm_added) });
    else txt = t("str.rep_sets", { sets: last.sets, reps: last.reps }) + (last.volume ? ` · ${t("str.lifted", { kg: last.volume })}` : "");
    h("small", null, `${fmtShort(last.date)}: ${txt}`, r);
    const metric = ex.kind === "time" ? (ex.sessions.some((x) => x.hold) ? "hold" : "sec") : ex.kind === "system" ? "e1rm" : (ex.sessions.some((x) => x.volume) ? "volume" : "reps");
    const vals = ex.sessions.map((x) => x[metric] || 0);
    if (vals.length > 1) { const sp = h("div", "spark mini", null, r); barChart(sp, vals, ex.sessions.map(() => ""), { cls: "bar-load", highlight: vals.length - 1, h: 40 }); }
  }
  sourcesFold($("strengthSrc"), st ? st.sources : []);
}

async function openStrength(forzaDay = null) {
  try {
    STR = await getJson(`/api/strength?date=${current}${forzaDay ? `&day=${forzaDay}` : ""}`);
  } catch (e) { toast(t("err.not_loaded", { msg: e.message }), true); return; }
  strDay = STR.plan_day;
  $("strSub").textContent = t("str.sub", { day: dayLabel(current), plan: STR.plan || t("wk.no_plan"), kg: STR.body_weight });
  $("strLoad").value = STR.load_kg ? STR.load_kg : "";
  $("strLoadNote").textContent = { plan: t("str.load_plan"), allenamento: t("str.load_training") }[STR.load_src] || "";
  const days = $("strDays"); days.replaceChildren();
  $("strDayRow").hidden = !STR.strength_day_numbers.length;
  for (const n of STR.strength_day_numbers) {
    const b = h("button", n === strDay ? "on" : "", t("plan.strength_day_n", { n }), days);
    b.addEventListener("click", () => openStrength(n));
  }
  const list = $("strList"); list.replaceChildren();
  const saved = Object.fromEntries((STR.entries || []).map((e) => [e.name, e.sets]));
  if (!STR.exercises.length) empty(list, t("str.no_exercises"));
  for (const ex of STR.exercises) {
    const box = h("div", "str-ex", null, list);
    box.dataset.name = ex.name; box.dataset.kind = ex.kind;
    const head = h("div", "row", null, box); h("b", null, ex.name, head); if (ex.detail) h("small", null, ex.detail, head);
    const sets = h("div", "str-sets", null, box);
    const start = saved[ex.name] || (ex.last ? ex.last.sets : null) || [{ reps: null, sec: null }];
    start.forEach((st) => addSetRow(sets, ex, st));
    if (ex.last) h("small", "muted", t("str.last_time", { d: ex.last.date, sets: ex.last.sets.map((x) => setText(ex.kind, x)).join(", ") }), box);
    const add = h("button", "add-btn small", t("str.add_set"), box);
    add.addEventListener("click", () => addSetRow(sets, ex, { reps: null, sec: null }));
  }
  $("strErr").hidden = true;
  $("strSave").disabled = false; $("strSave").textContent = t("common.save");
  $("strNote").textContent = t("str.note");
  openModal("strSheet");
}
const setText = (kind, x) => (kind === "time" ? t("unit.sec_v", { v: x.sec || 0 }) : String(x.reps ?? 0)) + (x.kg ? ` ${t("str.with_kg", { kg: x.kg })}` : "");
function addSetRow(box, ex, st) {
  const r = h("div", "set-row", null, box);
  h("span", "set-n", `${box.children.length}`, r);
  if (ex.kind === "time") {
    const s = h("input", "in-sec", null, r); s.type = "number"; s.inputMode = "numeric"; s.min = "0"; s.max = "3600"; s.placeholder = t("str.seconds"); if (st.sec != null) s.value = st.sec;
  } else {
    const rp = h("input", "in-reps", null, r); rp.type = "number"; rp.inputMode = "numeric"; rp.min = "0"; rp.max = "200"; rp.placeholder = t("str.reps"); if (st.reps != null) rp.value = st.reps;
  }
  const x = h("button", "set-x", "×", r); x.setAttribute("aria-label", t("str.remove_set"));
  x.addEventListener("click", () => { r.remove(); [...box.children].forEach((c, i) => { c.querySelector(".set-n").textContent = String(i + 1); }); });
}
function readStrength() {
  const entries = [];
  for (const box of $("strList").querySelectorAll(".str-ex")) {
    const sets = [];
    for (const r of box.querySelectorAll(".set-row")) {
      const num = (sel) => { const i = r.querySelector(sel); if (!i || i.value === "") return null; return Number(String(i.value).replace(",", ".")); };
      const st = { reps: num(".in-reps"), sec: num(".in-sec") };
      if (st.reps != null || st.sec != null) sets.push(st);
    }
    entries.push({ name: box.dataset.name, sets });
  }
  return entries;
}

/* ================= plan and weight ================= */
let PLAN = null, planEdit = null, planSel = "1", WEIGHT = null;
// weekdays from Monday (ISO 1-7), in the chosen language
const isoWeekday = (k, style) => I18N.fmt(addDays("2024-01-01", k - 1), style); // 1/1/2024 was a Monday
const planEmpty = (pl) => !pl || Object.values(pl.plan).every((x) => !x.text && !x.types.length && !(x.exercises || []).length);
function renderPlanCard(pl) {
  const ul = $("planWeek"); ul.replaceChildren();
  if (!pl) return;
  const emptyPlan = planEmpty(pl);
  $("planEmpty").hidden = !emptyPlan;
  $("planOpen").textContent = t(emptyPlan ? "plan.create" : "common.edit");
  $("planSource").textContent = emptyPlan ? "" : t(pl.source === "app" ? "plan.src_app" : "plan.src_config");
  if (emptyPlan) return;
  for (let k = 1; k <= 7; k++) {
    const x = pl.plan[String(k)];
    const li = h("li", null, null, ul);
    h("b", null, isoWeekday(k, "weekdayshort"), li);
    h("span", null, x.text || "—", li);
  }
}
function openPlan() {
  if (!PLAN) { toast(t("plan.unavailable"), true); return; }
  planEdit = JSON.parse(JSON.stringify(PLAN.plan));
  planSel = String(((parseIso(current).getDay() + 6) % 7) + 1);
  $("planErr").hidden = true;
  $("planSave").disabled = false; $("planSave").textContent = t("common.save");
  $("planTemplates").hidden = !(PLAN.templates || []).length;
  const tb = $("planTemplateList"); tb.replaceChildren();
  for (const tpl of PLAN.templates || []) {
    const b = h("button", "w-btn ghost", t(`plan.tpl.${tpl.id}`), tb);
    b.addEventListener("click", () => useTemplate(tpl));
  }
  sources($("planTemplateSrc"), (PLAN.templates || []).flatMap((x) => x.sources));
  drawPlanDay();
  openModal("planSheet");
}
// template chosen by the user: its texts become part of their plan, in the current language
function useTemplate(tpl) {
  for (let k = 1; k <= 7; k++) {
    const x = tpl.days[String(k)];
    planEdit[String(k)] = { text: tx(x.text), types: [...x.types], day: x.day || null, load_kg: null,
      exercises: (x.exercises || []).map(([n, d]) => [tx(n), tx(d)]) };
  }
  drawPlanDay();
}
function savePlanFields() {
  const x = planEdit[planSel];
  x.text = $("planTextIn").value;
  const lv = String($("planLoad").value).trim().replace(",", ".");
  x.load_kg = x.types.includes("strength") && lv !== "" ? Number(lv) : null;
  x.exercises = [...$("planExList").querySelectorAll(".pe-row")].map((r) => [r.querySelector(".pe-name").value, r.querySelector(".pe-detail").value])
    .filter((e) => e[0].trim());
}
function drawPlanDay() {
  const days = $("planDays"); days.replaceChildren();
  for (let i = 1; i <= 7; i++) {
    const k = String(i);
    const b = h("button", k === planSel ? "on" : "", isoWeekday(i, "weekdayshort"), days);
    b.title = isoWeekday(i, "weekday");
    b.addEventListener("click", () => { savePlanFields(); planSel = k; drawPlanDay(); });
  }
  const x = planEdit[planSel];
  $("planTextIn").value = x.text || "";
  const types = $("planTypes"); types.replaceChildren();
  for (const pt of PLAN.types) {
    const b = h("button", x.types.includes(pt.key) ? "on" : "", t(`act.${pt.key}`), types);
    b.addEventListener("click", () => {
      x.types = x.types.includes(pt.key) ? x.types.filter((y) => y !== pt.key) : [...x.types, pt.key];
      if (!x.types.includes("strength")) x.day = null;
      savePlanFields(); drawPlanDay();
    });
  }
  $("planStrengthDayRow").hidden = !x.types.includes("strength");
  $("planLoadRow").hidden = !x.types.includes("strength");
  $("planLoad").value = x.load_kg != null ? x.load_kg : "";
  const fz = $("planStrengthDay"); fz.replaceChildren();
  for (const n of [1, 2, 3]) {
    const b = h("button", x.day === n ? "on" : "", t("plan.strength_day_n", { n }), fz);
    b.addEventListener("click", () => { x.day = n; savePlanFields(); drawPlanDay(); });
  }
  const list = $("planExList"); list.replaceChildren();
  x.exercises.forEach((e, i) => addPlanEx(list, e, i));
}
function addPlanEx(list, e, i) {
  const r = h("div", "pe-row", null, list);
  const n = h("input", "pe-name", null, r); n.type = "text"; n.maxLength = 60; n.placeholder = t("plan.exercise"); n.value = e[0];
  const dt = h("input", "pe-detail", null, r); dt.type = "text"; dt.maxLength = 80; dt.placeholder = t("plan.sets_reps"); dt.value = e[1] || "";
  const up = h("button", "pe-btn", "↑", r); up.setAttribute("aria-label", t("plan.move_up"));
  const rm = h("button", "pe-btn", "×", r); rm.setAttribute("aria-label", t("plan.remove_exercise"));
  up.addEventListener("click", () => { savePlanFields(); const ex = planEdit[planSel].exercises; if (i > 0) { [ex[i - 1], ex[i]] = [ex[i], ex[i - 1]]; } drawPlanDay(); });
  rm.addEventListener("click", () => { savePlanFields(); planEdit[planSel].exercises.splice(i, 1); drawPlanDay(); });
}

function renderWeightCard(w) {
  if (!w) return;
  $("weightValue").textContent = fmtDec(w.current.kg);
  $("weightSrc").textContent = w.current.src === "log" ? t("w.since", { d: w.current.date }) : t(`wsrc.${w.current.src}`);
}
function openWeight() {
  if (!WEIGHT) { toast(t("w.unavailable"), true); return; }
  $("wKg").value = WEIGHT.current.kg; $("wDate").value = today(); $("wDate").max = today();
  $("wErr").hidden = true; $("wSave").disabled = false; $("wSave").textContent = t("common.save");
  const ul = $("wList"); ul.replaceChildren();
  if (!WEIGHT.entries.length) h("li", "muted", t("w.none"), ul);
  for (const [dt, kg] of WEIGHT.entries) {
    const li = h("li", null, null, ul);
    h("span", null, t("w.entry", { d: dt, kg }), li);
    const x = h("button", "set-x", "×", li); x.setAttribute("aria-label", t("common.remove"));
    x.addEventListener("click", () => saveForm("wSheet", "wSave", "wErr", "/api/weight", { date: dt, kg: null }, t("w.removed")));
  }
  openModal("wSheet");
}

/* ================= FOOD ================= */
// NUT: /api/nutrition of the day shown; FOODS: /api/foods (foods, recipes, saved meals)
let NUT = null, FOODS = null, foodsTab = "food", pickMeal = null, pickName = "", entryCtx = null, entryMeal = null;
let foodEdit = null, recipeEdit = null, mealEdit = null, mealFrom = null, foodReturn = null, foodCat = null, foodBarcode = null;
const NUT_PILL = { ok: "good", caution: "att", probable: "warn", certain: "bad", incomplete: "info", info: "info", in_progress: "info" };
const MACRO_LEGEND = [["carbs", "nut.carbs"], ["protein", "nut.protein"], ["fat", "nut.fat"]];
const FOOD_FIELDS = [["fKcal", "kcal"], ["fProtein", "protein"], ["fCarbs", "carbs"], ["fFat", "fat"], ["fFiber", "fiber"],
  ["fSat", "sat"], ["fSalt", "salt"], ["fPolyols", "polyols"], ["fAlcohol", "alcohol"]];
const BASE = ["protein", "carbs", "fat", "fiber"];
const signed = (v) => (v > 0 ? `+${fmtNum(v)}` : v < 0 ? `−${fmtNum(-v)}` : "0");
const numIn = (id) => { const v = String($(id).value).trim().replace(",", "."); return v === "" ? null : Number(v); };
const mealName = (key) => t(`meal.${key}`);
function showErr(id, msg) { $(id).textContent = msg; $(id).hidden = false; }

function renderFood(n, F) {
  const ok = !!(n && F);
  $("nutError").hidden = ok;
  $("nutError").textContent = ok ? "" : t("nut.unavailable");
  for (const id of ["nutHero", "nutMeals", "nutDayCard", "nutWeekCard", "nutWeightCard", "nutAthleteCard", "foodsCard"]) $(id).hidden = !ok;
  if (!ok) { $("nutRecoveryCard").hidden = true; return; }
  updateFoodLists(F);
  document.querySelectorAll("#goalSwitch button").forEach((b) => b.classList.toggle("on", b.dataset.goal === n.goal));

  // day's balance
  const tot = n.totals, ex = n.expenditure, bal = n.balance;
  $("nutTag").textContent = n.today ? t("nut.balance_today") : t("ui.food.balance");
  $("nutKcal").textContent = fmtNum(tot.kcal);
  $("nutRange").textContent = n.n ? t("nut.range", { lo: n.range.kcal[0], hi: n.range.kcal[1] }) : t("nut.no_food_day");
  $("nutExp").textContent = ex ? kcalText(ex.kcal, ex.partial) : "–";
  $("nutBal").textContent = bal ? signed(bal.kcal) : "–";
  const top = Math.max(tot.kcal, ex ? ex.kcal : 0, 1);
  $("nutBarIn").style.width = `${Math.min(100, (100 * tot.kcal) / top)}%`;
  $("nutBarExp").hidden = !ex;
  if (ex) $("nutBarExp").style.left = `${Math.min(100, (100 * ex.kcal) / top)}%`;
  const split = $("nutSplit"), lg = $("nutSplitLegend"); split.replaceChildren(); lg.replaceChildren();
  split.hidden = !n.shares;
  if (n.shares) for (const [k, name] of MACRO_LEGEND) {
    const seg = h("i", `seg m-${k}`, null, split); seg.style.width = `${n.shares[k].value}%`;
    const l = h("span", null, null, lg); h("i", `sw m-${k}`, null, l); l.append(`${t(name)} `); h("b", null, `${n.shares[k].value}%`, l);
  }
  const cb = $("nutComplete");
  cb.textContent = n.complete ? t("nut.day_complete") : t("ui.food.mark_day_as_complete");
  cb.classList.toggle("on", n.complete);
  $("nutCompleteNote").textContent = n.complete
    ? t(ex && !ex.partial && !ex.valid ? "nut.complete_note_nobalance" : "nut.complete_note")
    : t("nut.incomplete_note");

  renderMeals(n);
  $("nutDaySub").textContent = n.complete ? t("nut.day_complete_lc") : n.n ? t("nut.in_progress") : "";
  renderNutChecks($("nutDayChecks"), n.day_checks, t("nut.checks_empty"));

  // last 7 days
  const wk = n.week;
  $("nutWeekSub").textContent = t("nut.complete_days", { n: wk.days });
  const issues = wk.checks.filter((c) => c.status === "certain" || c.status === "probable").length;
  $("nutWeekTitle").textContent = wk.days < wk.need ? t("nut.need_days", { need: wk.need, have: wk.days })
    : issues ? t("nut.issues", { n: issues }) : t("nut.week_ok");
  nutWeekChart($("nutWeekChart"), n.days);
  const wl = $("nutWeekLegend"); wl.replaceChildren();
  for (const [cls, name] of [["food", "nut.legend_intake"], ["exp", "nut.legend_exp"]]) {
    const l = h("span", null, null, wl); h("i", `sw ${cls}`, null, l); l.append(t(name));
  }
  renderNutChecks($("nutWeekChecks"), wk.checks, "");
  if (wk.days >= wk.need && !wk.energy) {
    h("p", "note", t("nut.energy_need", { need: wk.need, have: wk.energy_days }), $("nutWeekChecks"));
  }

  // recovery between two close workouts (Craven 2021)
  const rc = n.recovery;
  $("nutRecoveryCard").hidden = !rc;
  if (rc) {
    const ul = $("nutRecoveryPairs"); ul.replaceChildren();
    for (const p of rc.pairs) h("li", null, t("nut.recovery_pair", { first: p.first, end: p.first_end, second: p.second, start: p.second_start, gap: p.gap_h }), ul);
    $("nutRecoveryText").textContent = tx(rc.text);
    sources($("nutRecoverySrc"), rc.sources);
  }

  // check against weight (Hall 2008)
  const wc = n.weight_check;
  $("nutWeightText").textContent = tx(wc.text);
  $("nutWeightPill").hidden = wc.status === "collecting";
  $("nutWeightPill").className = `pill ${wc.status === "ok" ? "good" : "warn"}`;
  $("nutWeightPill").textContent = t(wc.status === "ok" ? "nut.coherent" : "nut.to_check");
  sources($("nutWeightSrc"), wc.sources);

  // doses for athletes: flagged reference only
  const a = n.athlete;
  $("nutAthleteValue").textContent = a.g_per_kg != null ? fmtDec(a.g_per_kg) : "–";
  $("nutAthleteUnit").textContent = t("nut.athlete_unit", { lo: a.range[0], hi: a.range[1], cat: t(`load.${a.category}`) });
  $("nutAthleteText").textContent = tx(a.text);
  sources($("nutAthleteSrc"), a.sources);

  renderFoodsList(F);
  $("nutMethod").textContent = tx(n.method.text);
  sourcesFold($("nutMethodSrc"), n.method.sources);
}

function renderNutChecks(box, list, emptyText) {
  box.replaceChildren();
  if (!list || !list.length) { if (emptyText) empty(box, emptyText); return; }
  for (const c of list) {
    const r = h("div", `chk st-${c.status}`, null, box);
    const head = h("div", "row", null, r); h("b", null, tx(c.title), head);
    const running = c.status === "in_progress";
    if (!running) h("span", `pill ${NUT_PILL[c.status] || "info"}`, c.status === "certain" || c.status === "probable" ? t("nut.label_status", { label: c.label, status: t(`nut.st.${c.status}`) }) : tx(c.label), head);
    h("div", "chk-val", tx(c.display), r);
    // day in progress: no judgement, only what is missing (minimums) or how much of the limit is used (maximums)
    if (running && c.goal && c.value != null && c.dir) {
      const bar = h("div", `bar chk-bar ${c.dir}`, null, r), fill = h("i", null, null, bar);
      fill.style.width = `${Math.min(100, (100 * c.value) / c.goal)}%`;
      const left = c.goal - c.value, unit = t(c.key === "sat" ? "nut.unit_points" : "nut.unit_g");
      h("div", "chk-target", c.dir === "min" ? (left > 0 ? t("nut.missing_for", { v: left, unit, target: c.target }) : t("nut.reached", { target: c.target }))
        : t("nut.of_limit", { pct: (100 * c.value) / c.goal, target: c.target }), r);
    } else h("div", "chk-target", t("nut.reference", { target: c.target }), r);
    if (!running) h("p", null, tx(c.text), r);
    sourcesFold(h("div", null, null, r), c.sources);
  }
}

function renderMeals(n) {
  const box = $("nutMeals"); box.replaceChildren();
  for (const m of n.meals) {
    const c = h("div", "card meal", null, box);
    const head = h("div", "row", null, c); h("b", null, mealName(m.key), head); h("small", null, m.entries.length ? t("unit.kcal_v", { v: m.kcal }) : "", head);
    for (const e of m.entries) {
      const b = h("button", "meal-entry", null, c);
      const nm = h("span", null, e.name, b);
      if (e.src && e.src.src !== "user") h("span", "src-tag", srcShort(e.src.src), nm);
      h("small", null, t("nut.entry_line", { g: e.g, kcal: e.kcal }), b);
      b.addEventListener("click", () => openEntry({ mode: "edit", entry: e, meal: m.key }));
    }
    const btns = h("div", "meal-btns", null, c);
    const add = h("button", "add-btn small", null, btns); h("span", null, "+", add); add.append(` ${t("common.add")}`);
    add.dataset.meal = m.key;
    add.addEventListener("click", () => openPick(m.key));
    if (m.entries.length > 1) {
      const sv = h("button", "w-btn ghost", t("nut.save_as_meal"), btns);
      sv.addEventListener("click", () => openMealEditor(null, m));
    }
  }
}

// intake bars and expenditure mark, day by day
function nutWeekChart(box, days) {
  const vals = days.map((x) => x.intake || 0), exps = days.map((x) => x.expenditure || 0);
  if (!vals.some((v) => v) && !exps.some((v) => v)) return empty(box, t("nut.no_meals_7d"));
  const W = 340, H = 140, L = 4, R = 4, T = 14, B = 20, k = days.length, gap = 10;
  const s = svg(W, H);
  const top = Math.max(...vals, ...exps) * 1.1 || 1;
  const bw = (W - L - R - gap * (k - 1)) / k, y = (v) => T + (1 - v / top) * (H - T - B);
  days.forEach((x, i) => {
    const bx = L + i * (bw + gap);
    el("rect", { x: bx, y: T, width: bw, height: H - B - T, rx: Math.min(8, bw / 2), fill: "#1f232a" }, s);
    if (vals[i] > 0) el("rect", { x: bx, y: y(vals[i]), width: bw, height: Math.max(2, H - B - y(vals[i])), rx: Math.min(8, bw / 2), class: x.complete ? "bar-food" : "bar-food-dim" }, s);
    if (exps[i]) el("line", { x1: bx + 3, x2: bx + bw - 3, y1: y(exps[i]), y2: y(exps[i]), class: "exp-mark" }, s);
    el("text", { x: bx + bw / 2, y: H - 5, "text-anchor": "middle" }, s).textContent = wdLetter(x.date);
  });
  box.replaceChildren(s);
}

function updateFoodLists(F) {
  for (const [id, onlyBase] of [["foodNames", false], ["ingredientNames", true]]) {
    const dl = $(id); dl.replaceChildren();
    for (const f of F.foods) if (!onlyBase || f.kind === "food") { const o = h("option", null, null, dl); o.value = f.name; }
  }
}

const per100Text = (p) => t("food.per100_line", { kcal: p.kcal, p: p.protein, c: p.carbs, f: p.fat, fi: p.fiber });
function renderFoodsList(F) {
  const q = $("foodsSearch").value.trim().toLowerCase();
  document.querySelectorAll("#foodsSwitch button").forEach((b) => b.classList.toggle("on", b.dataset.k === foodsTab));
  const nA = F.foods.filter((f) => f.kind === "food").length, nR = F.foods.length - nA, nM = F.meals.length;
  $("foodsCount").textContent = [t("food.n_foods", { n: nA }), t("food.n_recipes", { n: nR }), t("food.n_meals", { n: nM })].join(" · ");
  const box = $("foodsList"); box.replaceChildren();
  if (foodsTab === "pasto") {
    const list = F.meals.filter((m) => !q || m.name.toLowerCase().includes(q));
    for (const m of list) {
      const b = h("button", "food-row", null, box); const tb = h("span", null, null, b);
      h("b", null, m.name, tb); h("small", null, m.items.map((i) => t("food.item_g", { name: i.name, g: i.g })).join(", "), tb);
      h("small", null, t("unit.kcal_v", { v: m.kcal }), b);
      b.addEventListener("click", () => openMealEditor(m));
    }
    if (!list.length) empty(box, q ? t("common.no_results") : t("food.no_meals"));
    return;
  }
  const list = F.foods.filter((f) => f.kind === foodsTab && (!q || f.name.toLowerCase().includes(q)));
  for (const f of list) {
    const b = h("button", "food-row", null, box); const tb = h("span", null, null, b);
    h("b", null, f.name, tb);
    h("small", null, t(f.kind === "recipe" ? "food.per100_cooked" : "food.per100", { values: per100Text(f.per100) }), tb);
    if (f.origin) h("span", "src-tag", t("cat.from", { src: srcShort(f.origin.src) }), tb.firstChild);
    if (f.check && !f.check.ok) h("span", "pill warn", t("food.kcal_check"), b);
    else if (f.missing.length) h("span", "pill info", t("food.without", { what: f.missing.map((k) => ({ $: `food.missing.${k}` })) }), b);
    b.addEventListener("click", () => (f.kind === "recipe" ? openRecipeEditor(f) : openFoodEditor(f)));
  }
  if (!list.length) empty(box, q ? t("common.no_results") : t(foodsTab === "recipe" ? "food.no_recipes" : "food.no_foods"));
}

/* ---------- adding to the diary ---------- */
function openPick(meal) {
  if (!FOODS) { toast(t("food.unavailable"), true); return; }
  pickMeal = meal; pickName = mealName(meal);
  $("pickTitle").textContent = t("food.add_to", { meal: pickName.toLowerCase() });
  $("pickSearch").value = "";
  $("pickScan").hidden = !ANDROID;  // the camera exists only in the app; in the browser the code can be typed
  drawPick();
  openModal("pickSheet");
}
function drawPick() {
  const q = $("pickSearch").value.trim().toLowerCase();
  const box = $("pickList"); box.replaceChildren();
  const match = (x) => !q || x.name.toLowerCase().includes(q);
  const meals = FOODS.meals.filter(match), foods = FOODS.foods.filter(match);
  if (meals.length) h("div", "pick-head", t("ui.food.saved_meals"), box);
  for (const m of meals) {
    const b = h("button", "pick-row", null, box); const tb = h("span", null, null, b);
    h("b", null, m.name, tb); h("small", null, m.items.map((i) => i.name).join(", "), tb);
    h("small", null, t("unit.kcal_v", { v: m.kcal }), b);
    b.addEventListener("click", () => nutSave("/api/diary", { date: current, op: "add_meal", meal: pickMeal, template: m.id }, t("food.added", { name: m.name }), { sheet: "pickSheet" }));
  }
  if (foods.length) h("div", "pick-head", t("food.foods_recipes"), box);
  for (const f of foods) {
    const b = h("button", "pick-row", null, box); const tb = h("span", null, null, b);
    h("b", null, f.name, tb);
    h("small", null, t(f.kind === "recipe" ? "food.recipe_kcal100" : "food.kcal100", { v: f.per100.kcal }), tb);
    b.addEventListener("click", () => { closeModal("pickSheet"); openEntry({ mode: "new", food: f, meal: pickMeal }); });
  }
  if (!meals.length && !foods.length) empty(box, t(FOODS.foods.length ? "food.pick_no_results" : "food.pick_none"));
  catSearchSoon($("pickSearch").value.trim());
}

/* ---------- food catalog (community databases, searched on the phone: no request leaves it) ---------- */
let CATALOG = null, catTimer = null, catSeq = 0;
const NUTR_ROWS = ["kcal", "protein", "carbs", "fat", "fiber", "sat", "salt"];
const srcShort = (src) => (src === "user" ? t("cat.src.user") : t(`cat.src.${src}`));
const isBarcode = (q) => /^\d{8,14}$/.test(q);
function catSearchSoon(q) {
  clearTimeout(catTimer);
  const seq = ++catSeq;
  const box = $("pickCat");
  if (q.length < 2) { box.replaceChildren(); return; }
  catTimer = setTimeout(async () => {
    let r;
    try {
      r = isBarcode(q) ? await getJson(`/api/catalog/barcode?code=${q}&lang=${I18N.lang()}`)
        : await getJson(`/api/catalog/search?q=${encodeURIComponent(q)}&lang=${I18N.lang()}`);
    } catch (e) {
      if (seq === catSeq) { box.replaceChildren(); empty(box, t("err.failed", { msg: e.message })); }
      return;
    }
    if (seq !== catSeq) return;
    drawCatResults(r, isBarcode(q));
  }, 180);
}
// countries of a community product, in the page language; from the GS1 prefix only when the source has none
function countryText(it) {
  if (!it.countries || !it.countries.length) return "";
  let names;
  try {
    const dn = new Intl.DisplayNames([I18N.locale()], { type: "region" });
    names = it.countries.slice(0, 3).map((c) => dn.of(c.toUpperCase()));
  } catch (e) {
    names = it.countries.slice(0, 3).map((c) => c.toUpperCase());
  }
  const list = names.join(", ") + (it.countries.length > 3 ? " …" : "");
  return it.country_from_gs1 ? t("cat.country_gs1", { country: list }) : list;
}
function catLine(it) {
  return [it.brand, countryText(it), t("food.kcal100", { v: it.per100.kcal })].filter(Boolean).join(" · ");
}
function drawCatResults(r, byCode) {
  const box = $("pickCat"); box.replaceChildren();
  if (byCode) for (const f of r.mine) {
    const food = FOODS && FOODS.foods.find((x) => x.id === f.id);
    if (!food) continue;
    const b = h("button", "pick-row", null, box); const tb = h("span", null, null, b);
    h("b", null, food.name, tb); h("small", null, t("food.kcal100", { v: food.per100.kcal }), tb);
    b.addEventListener("click", () => { closeModal("pickSheet"); openEntry({ mode: "new", food, meal: pickMeal }); });
  }
  h("div", "pick-head", t("cat.community"), box);
  for (const it of r.community) {
    const b = h("button", "pick-row", null, box); const tb = h("span", null, null, b);
    h("b", null, it.name, tb);
    h("small", null, catLine(it), tb);
    h("span", "src-tag", it.short, b);
    b.addEventListener("click", () => { closeModal("pickSheet"); openEntry({ mode: "new", cat: it, meal: pickMeal }); });
  }
  if (!r.community.length) empty(box, t(byCode ? "cat.code_none" : "cat.none"));
  if (byCode && !r.community.length && !r.mine.length) {
    const nb = h("button", "add-btn small", null, box); h("span", null, "+", nb); nb.append(` ${t("cat.new_with_code", { code: r.code })}`);
    nb.addEventListener("click", () => { closeModal("pickSheet"); openFoodEditor(null, "", pickMeal, null, r.code); });
  }
}
// a scanned code: one product opens its details (name, brand and country before adding), several are listed to choose
async function lookupScanned(code) {
  $("pickSearch").value = code;
  let r;
  try { r = await getJson(`/api/catalog/barcode?code=${code}&lang=${I18N.lang()}`); } catch (e) { toast(t("err.failed", { msg: e.message }), true); return; }
  const mine = r.mine.map((f) => FOODS && FOODS.foods.find((x) => x.id === f.id)).filter(Boolean);
  if (mine.length + r.community.length === 1) {
    closeModal("pickSheet");
    if (mine.length) openEntry({ mode: "new", food: mine[0], meal: pickMeal });
    else openEntry({ mode: "new", cat: r.community[0], meal: pickMeal });
    return;
  }
  drawPick();
  if (mine.length + r.community.length > 1) toast(t("cat.scan_choose", { n: mine.length + r.community.length }));
}
function valuesTable(box, per100, srcOf) {
  box.replaceChildren();
  for (const k of NUTR_ROWS) {
    const v = per100[k];
    h("span", null, t(`nutr.${k}`), box);
    h("span", v == null ? "na" : null, v == null ? t("cat.na") : t(k === "kcal" ? "cat.kcal_v" : "cat.g_v", { v }), box);
    const s = v == null ? null : srcOf(k);
    const tag = h("span", s ? `src-tag${s === "user" ? " user" : ""}` : null, s ? srcShort(s) : "", box);
    if (s) tag.title = t("cat.value_from", { src: srcShort(s) });
  }
}
// barcode scanner of the app: the digits come back here and are looked up in the local catalog only
async function scanBarcode() {
  try { await postJson("/api/android/action", { action: "scan" }); } catch (e) { toast(t("err.failed", { msg: e.message }), true); }
}
window.helioScanned = (r) => {
  if (r && r.code) {
    lookupScanned(r.code);
  } else if (r && (r.error === "permission" || r.error === "camera")) {
    toast(t(`cat.scan_${r.error}`), true);
  }
};
function catDisplayName(it) { return it.brand ? `${it.name} (${it.brand})`.slice(0, 60) : it.name.slice(0, 60); }

function openEntry(ctx) {
  entryCtx = ctx; entryMeal = ctx.meal;
  const isNew = ctx.mode === "new", cat = ctx.cat || null;
  const food = cat ? null : isNew ? ctx.food : (FOODS && FOODS.foods.find((f) => f.id === ctx.entry.food)) || null;
  entryCtx.per100 = cat ? cat.per100 : isNew ? ctx.food.per100 : ctx.entry.per100;
  $("entryTitle").textContent = cat ? (cat.brand ? `${cat.name} (${cat.brand})` : cat.name) : isNew ? ctx.food.name : ctx.entry.name;
  const entrySrc = !isNew && ctx.entry.src && ctx.entry.src.src !== "user" ? ctx.entry.src : null;
  $("entrySub").textContent = cat ? [cat.brand, countryText(cat), t("cat.entry_sub", { src: srcShort(cat.src), version: cat.version })].filter(Boolean).join(" · ")
    : isNew ? t(ctx.food.kind === "recipe" ? "food.entry_kcal_cooked" : "food.kcal100", { v: ctx.food.per100.kcal })
    : entrySrc ? t("cat.entry_copied", { src: srcShort(entrySrc.src) }) : t("food.entry_copied");
  // every value with its source: the catalog, the user's food (a copy keeps the source of unchanged values) or the diary copy
  const fsrc = food && food.src ? food.src : null;
  valuesTable($("entryValues"), entryCtx.per100, (k) => (cat ? cat.src : entrySrc ? entrySrc.src : fsrc && fsrc[k] ? fsrc[k] : "user"));
  $("entrySaveMine").hidden = !cat;
  $("entryG").value = isNew ? "" : ctx.entry.g;
  const ps = $("entryPortions"); ps.replaceChildren();
  const portions = (cat ? cat.portions : food && food.portions) || [];
  ps.hidden = !portions.length;
  for (const [name, g] of portions) {
    const b = h("button", "", t("food.item_g", { name, g }), ps);
    b.addEventListener("click", () => { $("entryG").value = g; entryPreview(); });
  }
  const ms = $("entryMeals"); ms.replaceChildren();
  for (const m of FOODS.meal_names) {
    const b = h("button", m.key === entryMeal ? "on" : "", mealName(m.key), ms);
    b.addEventListener("click", () => { entryMeal = m.key; ms.querySelectorAll("button").forEach((x) => x.classList.toggle("on", x === b)); });
  }
  $("entryRemove").hidden = isNew;
  $("entryErr").hidden = true;
  for (const [id, key] of [["entrySave", isNew ? "common.add" : "common.save"], ["entryRemove", "common.remove"]]) { $(id).disabled = false; $(id).textContent = t(key); }
  entryPreview();
  openModal("entrySheet");
  if (isNew) $("entryG").focus();
}
function entryPreview() {
  const g = numIn("entryG"), p = entryCtx.per100;
  if (!(g > 0)) { $("entryPreview").textContent = t("food.entry_hint"); return; }
  const v = (k) => (p[k] * g) / 100;
  $("entryPreview").textContent = p.fiber == null
    ? t("cat.entry_preview_nofi", { kcal: v("kcal"), p: v("protein"), c: v("carbs"), f: v("fat") })
    : t("food.entry_preview", { kcal: v("kcal"), p: v("protein"), c: v("carbs"), f: v("fat"), fi: v("fiber") });
}

/* ---------- foods, recipes, saved meals ---------- */
// cat: community food to copy among the user's foods ("Save to my foods")
function openFoodEditor(food, presetName = "", returnMeal = null, cat = null, barcode = null) {
  if (!FOODS) { toast(t("food.unavailable"), true); return; }
  foodEdit = food; foodReturn = returnMeal; foodCat = cat; foodBarcode = barcode;
  const base = cat ? cat.per100 : food ? food.per100 : null;
  $("foodTitle").textContent = t(food ? "food.edit" : cat ? "cat.save_mine_title" : "food.new");
  $("foodName").value = food ? food.name : cat ? catDisplayName(cat) : presetName;
  for (const [id, k] of FOOD_FIELDS) $(id).value = base && base[k] != null ? base[k] : "";
  $("foodOpt").open = !!(base && ["sat", "salt", "polyols", "alcohol"].some((k) => base[k] != null));
  $("foodPortions").replaceChildren();
  for (const p of (cat ? cat.portions : food && food.portions) || []) addPortionRow(p);
  const origin = cat ? { src: cat.src, version: cat.version } : food && food.origin;
  $("foodOrigin").hidden = !origin;
  $("foodOrigin").textContent = origin ? t("cat.copy_note", { src: srcShort(origin.src), version: origin.version }) : "";
  $("fFiber").placeholder = fiberOptional() ? t("cat.not_in_source") : "";
  foodSrcTags();
  $("foodPastRow").hidden = !food; $("foodPast").checked = false;
  $("foodDelete").hidden = !food;
  $("foodErr").hidden = true;
  for (const [id, key] of [["foodSave", "common.save"], ["foodDelete", "common.delete"]]) { $(id).disabled = false; $(id).textContent = t(key); }
  foodCheck();
  openModal("foodSheet");
}
function addPortionRow(p = ["", ""]) {
  const r = h("div", "it-row", null, $("foodPortions"));
  const nm = h("input", "pt-name", null, r); nm.type = "text"; nm.maxLength = 30; nm.placeholder = t("food.portion_name"); nm.value = p[0];
  const g = h("input", "pt-g", null, r); g.type = "number"; g.inputMode = "decimal"; g.min = "0.1"; g.max = "2000"; g.placeholder = t("unit.g"); g.value = p[1];
  const x = h("button", "pe-btn", "×", r); x.setAttribute("aria-label", t("food.remove_portion"));
  x.addEventListener("click", () => r.remove());
}
function readFood() {
  const per100 = {};
  for (const [id, k] of FOOD_FIELDS) { const v = numIn(id); if (v != null) per100[k] = v; }
  const portions = [...$("foodPortions").querySelectorAll(".it-row")]
    .map((r) => [r.querySelector(".pt-name").value.trim(), Number(String(r.querySelector(".pt-g").value).replace(",", "."))])
    .filter((p) => p[0] || p[1]);
  const out = { id: foodEdit ? foodEdit.id : undefined, kind: "food", name: $("foodName").value.trim(), per100, portions };
  if (foodCat) {
    out.origin = { src: foodCat.src, ref: foodCat.ref };
    if (foodCat.barcode) out.barcode = foodCat.barcode;
  } else if (foodBarcode) {
    out.barcode = foodBarcode;  // created after a scan that found nothing: the next scan finds it
  }
  return out;
}
// a copy of a community food without fibre in its source can stay without fibre
function fiberOptional() {
  return !!((foodCat && foodCat.per100.fiber == null) || (foodEdit && foodEdit.origin && foodEdit.src && foodEdit.src.fiber == null && foodEdit.per100.fiber == null));
}
const baseFields = () => (fiberOptional() ? BASE.filter((k) => k !== "fiber") : BASE);
// source of each value while editing: unchanged values keep the catalog's, changed ones are the user's
function foodSrcTags() {
  const f = readFood().per100;
  for (const [id, k] of FOOD_FIELDS) {
    const label = $(id).parentElement;
    let tag = label.querySelector(".src-tag");
    let src = null;
    if (f[k] != null && foodCat) src = foodCat.per100[k] === f[k] ? foodCat.src : "user";
    else if (f[k] != null && foodEdit && foodEdit.origin && foodEdit.src) src = foodEdit.per100[k] === f[k] && foodEdit.src[k] ? foodEdit.src[k] : "user";
    if (!src) { if (tag) tag.remove(); continue; }
    if (!tag) tag = h("span", "src-tag", null, label.firstElementChild);
    tag.className = `src-tag${src === "user" ? " user" : ""}`;
    tag.textContent = srcShort(src);
  }
}
// same check as the server: EU factors and label rounding
function foodCheck() {
  foodSrcTags();
  const f = { ...readFood().per100 }, E = FOODS ? FOODS.factors : null;
  const box = $("foodCheck"); box.classList.remove("bad");
  if (!E || baseFields().some((k) => f[k] == null)) { box.textContent = t("food.check_need"); return; }
  if (f.fiber == null) f.fiber = 0;
  const pol = f.polyols || 0;
  const kcal = E.carbs * Math.max(0, f.carbs - pol) + E.polyols * pol + E.protein * f.protein + E.fat * f.fat + E.fiber * f.fiber + E.alcohol * (f.alcohol || 0);
  const rt = (v) => (v >= 10 ? 0.5 : v >= 0.5 ? 0.05 : 0.5);
  const tol = 0.5 + BASE.reduce((acc, k) => acc + E[k] * rt(f[k]), 0) + ["polyols", "alcohol"].reduce((acc, k) => acc + (f[k] ? E[k] * rt(f[k]) : 0), 0);
  if (f.kcal == null) { box.textContent = t("food.check_computed", { kcal }); return; }
  const diff = f.kcal - kcal;
  if (Math.abs(diff) <= tol) { box.textContent = t("food.check_ok", { kcal }); return; }
  box.classList.add("bad");
  box.textContent = t("food.check_bad", { kcal, label: f.kcal, diff: Math.abs(diff) });
}

function openRecipeEditor(rec, presetName = "", returnMeal = null) {
  if (!FOODS) { toast(t("food.unavailable"), true); return; }
  recipeEdit = rec; foodReturn = returnMeal;
  $("recipeTitle").textContent = t(rec ? "recipe.edit" : "recipe.new");
  $("recipeName").value = rec ? rec.name : presetName;
  const box = $("recipeItems"); box.replaceChildren();
  for (const it of rec ? rec.items : [{ name: "", g: "" }]) addItemRow(box, it, "ingredientNames", recipePreview);
  $("recipeCooked").value = rec ? rec.cooked_g : "";
  $("recipePastRow").hidden = !rec; $("recipePast").checked = false;
  $("recipeDelete").hidden = !rec;
  $("recipeErr").hidden = true;
  for (const [id, key] of [["recipeSave", "common.save"], ["recipeDelete", "common.delete"]]) { $(id).disabled = false; $(id).textContent = t(key); }
  recipePreview();
  openModal("recipeSheet");
}
function addItemRow(box, it, listId, onChange) {
  const r = h("div", "it-row", null, box);
  const nm = h("input", "it-name", null, r); nm.type = "text"; nm.setAttribute("list", listId); nm.placeholder = t("ui.food.food"); nm.autocomplete = "off"; nm.value = it.name || "";
  const g = h("input", "it-g", null, r); g.type = "number"; g.inputMode = "decimal"; g.min = "0.1"; g.max = "10000"; g.placeholder = t("unit.g"); g.value = it.g ?? "";
  const x = h("button", "pe-btn", "×", r); x.setAttribute("aria-label", t("common.remove"));
  x.addEventListener("click", () => { r.remove(); onChange(); });
  nm.addEventListener("input", onChange); g.addEventListener("input", onChange);
}
const foodByName = (name, onlyBase) => FOODS.foods.find((f) => f.name.toLowerCase() === name.trim().toLowerCase() && (!onlyBase || f.kind === "food"));
function readItems(box, onlyBase) {
  const items = [], bad = [];
  for (const r of box.querySelectorAll(".it-row")) {
    const name = r.querySelector(".it-name").value.trim(), raw = r.querySelector(".it-g").value;
    if (!name && !raw) continue;
    const f = name ? foodByName(name, onlyBase) : null, g = Number(String(raw).replace(",", "."));
    if (!f || !(g > 0)) bad.push(name || t("food.row_no_name")); else items.push([f.id, g, f]);
  }
  return { items, bad };
}
function recipePreview() {
  const { items, bad } = readItems($("recipeItems"), true);
  const notFound = bad.length ? ` ${t("food.not_found", { names: bad.join(", ") })}` : "";
  if (!items.length) { $("recipePreview").textContent = t("recipe.hint") + notFound; return; }
  const raw = items.reduce((acc, it) => acc + it[1], 0), cooked = numIn("recipeCooked") || raw;
  const tot = (k) => items.reduce((acc, [, g, f]) => acc + ((f.per100[k] || 0) * g) / 100, 0);
  const per = (k) => (tot(k) * 100) / cooked;
  $("recipePreview").textContent = t("recipe.preview", { kcal: tot("kcal"), cooked, raw, kcal100: per("kcal"), p: per("protein"), c: per("carbs"), f: per("fat"), fi: per("fiber") })
    + (bad.length ? ` ${t("food.not_found_mine", { names: bad.join(", ") })}` : "");
}

function openMealEditor(m, fromDiary = null) {
  if (!FOODS) { toast(t("food.unavailable"), true); return; }
  mealEdit = m; mealFrom = fromDiary;
  $("mealTitle").textContent = t(m ? "meal.edit" : "ui.mealSheet.new_saved_meal");
  $("mealName").value = m ? m.name : fromDiary ? t("meal.default_name", { meal: mealName(fromDiary.key) }) : "";
  const box = $("mealItems"); box.replaceChildren();
  const rows = m ? m.items : fromDiary ? fromDiary.entries.map((e) => {
    const f = FOODS.foods.find((x) => x.id === e.food);
    return { name: f ? f.name : e.name, g: e.g };
  }) : [{ name: "", g: "" }];
  for (const it of rows) addItemRow(box, it, "foodNames", mealPreview);
  $("mealDelete").hidden = !m;
  $("mealErr").hidden = true;
  for (const [id, key] of [["mealSave", "common.save"], ["mealDelete", "common.delete"]]) { $(id).disabled = false; $(id).textContent = t(key); }
  mealPreview();
  openModal("mealSheet");
}
function mealPreview() {
  const { items, bad } = readItems($("mealItems"), false);
  const kcal = items.reduce((acc, [, g, f]) => acc + (f.per100.kcal * g) / 100, 0);
  $("mealPreview").textContent = (items.length ? t("meal.preview", { n: items.length, kcal }) : t("meal.hint"))
    + (bad.length ? ` ${t("food.not_found", { names: bad.join(", ") })}` : "");
}

// common save: the button always comes back, error in the sheet or in a toast, then reload
async function nutSave(path, payload, okMsg, opt = {}) {
  if (formSaving) return null;
  formSaving = true;
  const btn = opt.btn ? $(opt.btn) : null, label = btn ? btn.textContent : "";
  if (btn) { btn.disabled = true; btn.textContent = t("common.saving"); }
  if (opt.err) $(opt.err).hidden = true;
  let res = null;
  try { res = await postJson(path, payload); }
  catch (e) { if (opt.err) showErr(opt.err, t("err.not_saved", { msg: e.message })); else toast(t("err.not_saved", { msg: e.message }), true); }
  finally { formSaving = false; if (btn) { btn.disabled = false; btn.textContent = res ? label : t("common.retry"); } }
  if (!res) return null;
  if (opt.sheet) closeModal(opt.sheet);
  toast(typeof okMsg === "function" ? okMsg(res) : okMsg);
  await load();
  return res;
}
async function afterFoodSaved(res) {
  // food created from the "Add" sheet: go straight to the grams
  const ret = foodReturn; foodReturn = null;
  if (!res || !ret || !FOODS) return;
  const f = FOODS.foods.find((x) => x.id === res.id);
  if (f) openEntry({ mode: "new", food: f, meal: ret });
}
const savedMsg = (key) => (res) => (res.updated ? t("food.saved_fixed", { what: t(key), n: res.updated }) : t(key));

/* ================= sheets (bottom panels) ================= */
function openModal(id) { $(id).hidden = false; document.body.classList.add("noscroll"); }
function closeModal(id) { $(id).hidden = true; if (![...document.querySelectorAll(".sheet")].some((x) => !x.hidden)) document.body.classList.remove("noscroll"); }
let formSaving = false;
async function saveForm(sheetId, btnId, errId, path, payload, okMsg) {
  if (formSaving) return;
  formSaving = true;
  const btn = $(btnId), label = btn.textContent;
  btn.disabled = true; btn.textContent = t("common.saving"); $(errId).hidden = true;
  let ok = false;
  try { await postJson(path, payload); ok = true; }
  catch (e) { $(errId).textContent = t("err.not_saved", { msg: e.message }); $(errId).hidden = false; }
  finally { formSaving = false; btn.disabled = false; btn.textContent = ok ? label : t("common.retry"); }
  if (!ok) return;
  closeModal(sheetId);
  toast(okMsg);
  await load();
}

function renderSync(d) {
  if (!d.last_sample) { $("syncLabel").textContent = t("sync.none"); return; }
  const age = Date.now() / 1000 - d.last_sample;
  const when = age < 86400 ? t("today.at_time", { t: d.last_sample })  // elapsed: less than 24 hours ago
    : new Date(d.last_sample * 1000).toLocaleString(I18N.locale(), { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit", timeZone: TZ });
  $("syncLabel").textContent = t("sync.last", { when });
}

/* ================= loading ================= */
async function getJson(url) {
  const r = await fetch(url, { cache: "no-store" });
  const body = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(errText(body, r.status));
  return body;
}

/* ================= band (only in the Android app, where Helio and Gadgetbridge are the same app) ================= */
const ANDROID = window.HELIO_ANDROID === true;
let BAND = null;
let bandLastSync;

function bandWhen(ts) {
  const d = new Date(ts * 1000);
  const day = (x) => x.toLocaleDateString("en-CA", { timeZone: TZ });
  const time = d.toLocaleTimeString(I18N.locale(), { hour: "2-digit", minute: "2-digit", timeZone: TZ });
  if (day(d) === day(new Date())) return time;
  return `${d.toLocaleDateString(I18N.locale(), { day: "numeric", month: "short", timeZone: TZ })} ${time}`;
}

function renderBand(b) {
  BAND = b;
  $("bandDot").hidden = false;
  $("bandDot").classList.toggle("on", b.connected);
  $("bandDot").classList.toggle("wait", b.status === "connecting");
  $("bandSync").hidden = !b.last_sync;
  $("bandSync").textContent = b.last_sync ? bandWhen(b.last_sync) : "";
  const status = t(`band.status.${b.status}`);
  $("battChip").setAttribute("aria-label", b.last_sync ? t("band.aria_synced", { status, when: bandWhen(b.last_sync) }) : t("band.aria", { status }));
  const parts = [t("band.aria", { status })];
  if (b.busy) parts.push(b.busy);
  parts.push(b.last_sync ? t("band.last_sync", { when: bandWhen(b.last_sync) }) : t("band.no_sync"));
  $("bandSub").textContent = parts.join(" · ");
}

async function loadBand() {
  if (!ANDROID) return;
  try {
    const b = await getJson("/api/android/band");
    // a sync that just finished brings new data: reload the day
    const synced = bandLastSync !== undefined && b.last_sync && b.last_sync !== bandLastSync;
    bandLastSync = b.last_sync;
    renderBand(b);
    if (synced) load();
  } catch (e) {
    // band status is an extra: without it Helio works the same
  }
}

function openBand() {
  if (BAND) renderBand(BAND);
  openModal("bandSheet");
  loadBand();
}

async function bandAction(action) {
  try {
    await postJson("/api/android/action", { action });
    closeModal("bandSheet");
    if (action === "sync") toast(t("band.sync_started"));
  } catch (e) {
    toast(t("err.failed", { msg: e.message }), true);
  }
}

/* ================= view and Back button in the app (Android) ================= */
// view:begin
// Android can kill Helio's process while it is in the background: section, day and
// scroll go to Java (HELIO_SAVE_VIEW, from helio-android.js), which keeps them in the Activity
// state and passes them back on reopening (HELIO_RESTORE). None of this exists on the server.
const VIEW_SCROLL_MS = 300;
let viewTimer = null, viewLast = "";

function viewSend() {
  viewTimer = null;
  if (!ANDROID || typeof window.HELIO_SAVE_VIEW !== "function") return;
  const v = { tab, date: current, y: Math.max(0, Math.round(window.scrollY || 0)) };
  const key = JSON.stringify(v);
  if (key === viewLast) return;
  viewLast = key;
  window.HELIO_SAVE_VIEW(v);
}

function viewSave() {
  if (!ANDROID) return;
  if (viewTimer) clearTimeout(viewTimer);
  viewTimer = null;
  viewSend();
}

function viewScrolled() {
  if (!viewTimer) viewTimer = setTimeout(viewSend, VIEW_SCROLL_MS);
}

// section names saved by versions up to 259 (Italian codes): a view saved before an update still restores
const LEGACY_TABS = { oggi: "today", sonno: "sleep", salute: "health", cibo: "food", profilo: "profile" };
// sensible values only: existing section, date not in the future, non-negative scroll
function viewRestore(r) {
  if (!r || typeof r !== "object") return null;
  if (typeof r.date === "string" && /^\d{4}-\d{2}-\d{2}$/.test(r.date) && r.date <= today()) current = r.date;
  const t = typeof r.tab === "string" ? LEGACY_TABS[r.tab] || r.tab : null;
  if (t && Object.prototype.hasOwnProperty.call(TITLES, t) && t !== tab) show(t);
  return Number.isInteger(r.y) && r.y > 0 && r.y <= 1000000 ? r.y : null;
}

// Back: closes the open sheet, then goes back to Today; from Today (false) Java closes Helio
function helioBack() {
  const open = [...document.querySelectorAll(".sheet")].filter((x) => !x.hidden);
  if (open.length) {
    closeModal(open[open.length - 1].id);
    return true;
  }
  if (tab !== "today") {
    show("today");
    return true;
  }
  return false;
}

function viewInstall() {
  window.helioBack = helioBack;
  window.addEventListener("scroll", viewScrolled, { passive: true });
  const y = viewRestore(window.HELIO_RESTORE);
  return y;
}
// view:end

/* ================= pull down to sync (app only) ================= */
// pull:begin
// The gesture starts only with the page still at the top: the window and every container under the finger
// at scroll zero, no scrolling in the last moments (someone flicking up
// reaches the top with a finger moving down) and the finger going straight down.
// If a sync is running or finished less than 30 s ago, Python does not start one: only the data
// is refreshed. On the server ANDROID is false and the gesture does not exist.
const PULL_DEAD = 14;            // px before it counts as a drag
const PULL_TRIGGER = 140;        // px of finger travel to sync
const PULL_SETTLE_MS = 500;      // the page must have been still for at least this long
const PULL_POLL_MS = 2000;
const PULL_START_MS = 20 * 1000; // without signs of a transfer within this time, stop waiting
const PULL_MAX_MS = 3 * 60 * 1000;
let pullTouch = null, pullDragging = false, pullBusy = false, pullLastScroll = -Infinity;

function pullAtTop(target) {
  const root = document.scrollingElement || document.documentElement;
  if ((window.scrollY || 0) > 0 || (root && root.scrollTop > 0)) return false;
  for (let el = target; el && el !== root; el = el.parentElement) {
    if (el.scrollTop > 0) return false;
  }
  return true;
}

function pullCanStart(e) {
  if (!ANDROID || pullBusy || e.touches.length !== 1) return false;
  if ([...document.querySelectorAll(".sheet")].some((x) => !x.hidden)) return false;
  const t = e.target;
  if (t && t.closest && t.closest("input, select, textarea, .chips, .seg-switch")) return false;
  if (Date.now() - pullLastScroll < PULL_SETTLE_MS) return false;
  return pullAtTop(t);
}

function pullShow(progress, text, state) {
  const box = $("pullBox");
  box.hidden = false;
  box.classList.toggle("ready", state === "ready");
  box.classList.toggle("busy", state === "busy");
  box.style.opacity = String(Math.min(1, progress * 1.5));
  box.style.transform = `translate(-50%, ${Math.round(-60 + progress * 110)}px)`;
  $("pullText").textContent = text;
}

function pullHide() {
  const box = $("pullBox");
  box.hidden = true;
  box.classList.remove("ready", "busy");
  box.style.opacity = "0";
  box.style.transform = "translate(-50%, -60px)";
}

function pullCancel() {
  pullTouch = null;
  if (pullDragging && !pullBusy) pullHide();
  pullDragging = false;
}

function pullMove(e) {
  if (!pullTouch) return;
  if (e.touches.length !== 1) return pullCancel();
  const dx = e.touches[0].clientX - pullTouch.x, dy = e.touches[0].clientY - pullTouch.y;
  if (!pullDragging) {
    // upwards (scrolling the page) or sideways (changing day): not a drag
    if (dy < 0 || Math.abs(dx) > PULL_DEAD) return pullCancel();
    if (dy < PULL_DEAD) return;
    if (Math.abs(dx) * 1.5 > dy) return pullCancel();
    pullDragging = true;
  }
  if (!pullAtTop(pullTouch.target)) return pullCancel();
  pullTouch.dy = dy;
  const p = Math.max(0, Math.min(1, (dy - PULL_DEAD) / PULL_TRIGGER));
  pullShow(p, t(p >= 1 ? "pull.release" : "pull.drag"), p >= 1 ? "ready" : "");
}

function pullEnd() {
  const go = pullTouch && pullDragging && pullTouch.dy - PULL_DEAD >= PULL_TRIGGER && pullAtTop(pullTouch.target);
  pullCancel();
  if (go) pullSync();
}

const pullSleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

// waits for the end of the transfer: loadBand reloads the day when the last sync changes
async function pullWait(before, started) {
  const t0 = Date.now();
  let seenBusy = false;
  while (Date.now() - t0 < PULL_MAX_MS) {
    await pullSleep(PULL_POLL_MS);
    await loadBand();
    const b = BAND;
    if (!b) continue;
    if (b.busy) { seenBusy = true; continue; }
    if (b.last_sync && b.last_sync !== before) return true;
    if (seenBusy || !started) return false;  // finished without new data, or the running one ended
    if (Date.now() - t0 > PULL_START_MS && b.status !== "connecting") return false;
  }
  return false;
}

async function pullSync() {
  if (pullBusy) return;
  pullBusy = true;
  pullShow(1, t("pull.updating"), "busy");
  try {
    const before = BAND ? BAND.last_sync : null;
    let answer = null;
    try {
      answer = await postJson("/api/android/action", { action: "pull" });
    } catch (e) {
      toast(t("err.failed", { msg: e.message }), true);
    }
    let reloaded = false;
    if (answer && (answer.sync || answer.reason === "busy")) {
      pullShow(1, t(answer.sync ? "pull.syncing" : "pull.sync_running"), "busy");
      reloaded = await pullWait(before, answer.sync);
    } else if (answer && answer.reason === "recent") {
      toast(t("pull.recent"));
    }
    if (!reloaded) {
      await load();
      await loadBand();
    }
  } finally {
    pullBusy = false;
    pullHide();
  }
}

function pullInstall() {
  window.addEventListener("scroll", () => {
    pullLastScroll = Date.now();
    if (pullTouch) pullCancel();
  }, { capture: true, passive: true });
  const start = (e) => {
    pullCancel();
    if (pullCanStart(e)) pullTouch = { x: e.touches[0].clientX, y: e.touches[0].clientY, dy: 0, target: e.target };
  };
  for (const [type, fn] of [["touchstart", start], ["touchmove", pullMove], ["touchend", pullEnd], ["touchcancel", pullCancel]]) {
    document.addEventListener(type, fn, { passive: true });
  }
}
// pull:end

let loadSeq = 0;
async function load() {
  // quick taps on the arrows or a save during a load: only the latest answer counts
  const seq = ++loadSeq;
  viewSave();
  $("dayLabel").textContent = dayLabel(current);
  $("next").disabled = current >= today();
  try {
    // secondary tabs must not block the day: if an answer is missing, that tab stays hidden
    const soft = (p) => p.catch(() => null);
    const res = await Promise.all([getJson(`/api/summary?date=${current}`), getJson(`/api/trends?end=${current}`),
      getJson(`/api/vitals?end=${current}`), soft(getJson(`/api/week?date=${current}&which=${weekWhich}`)),
      soft(getJson(`/api/strength?date=${current}`)), soft(getJson("/api/plan")), soft(getJson("/api/weight")),
      soft(getJson(`/api/nutrition?date=${current}`)), soft(getJson("/api/foods"))]);
    if (seq !== loadSeq) return;
    [S, T, V, WEEK, STRW, PLAN, WEIGHT, NUT, FOODS] = res;
    if (S.tz) { TZ = S.tz; I18N.setTimeZone(TZ); }
    $("error").hidden = true;
    loadError = null;
    renderAll();
  } catch (e) {
    if (seq !== loadSeq) return;
    loadError = e.message;
    $("error").textContent = t("err.load", { msg: e.message });
    $("error").hidden = false;
  }
}

// draws everything from the data already downloaded: also used by the language change, which reloads nothing
let loadError = null;
function renderAll() {
  $("dayLabel").textContent = dayLabel(current);
  $("title").textContent = t(TITLES[tab]);
  if (loadError) { $("error").textContent = t("err.load", { msg: loadError }); }
  if (!S) return;
  renderSync(S); renderToday(S); renderWeek(WEEK); renderFitness(S, T); renderStrength(STRW); renderSleep(S, T); renderHealth(S, T, V);
  renderProfile(S); renderPlanCard(PLAN); renderWeightCard(WEIGHT); renderFood(NUT, FOODS); renderCatalog(CATALOG);
  if (!$("aboutSheet").hidden) renderAbout();
  if (BAND) renderBand(BAND);
}

/* ================= food database (Profile) ================= */
async function loadCatalog() {
  try { CATALOG = await getJson("/api/catalog/info"); } catch (e) { CATALOG = null; }
  renderCatalog(CATALOG);
}
function renderCatalog(c) {
  $("catalogCard").hidden = !c;
  if (!c) return;
  const ul = $("catalogList"); ul.replaceChildren();
  for (const kind of c.kinds) {
    const m = c.installed[kind], li = h("li", null, null, ul);
    h("b", null, t(`cat.kind.${kind}`), li);
    h("small", null, m ? t("cat.installed", { n: m.rows, version: m.version, mb: m.file_size / 1048576 })
      : t(kind === "off" ? "cat.off_missing" : "cat.generic_missing"), li);
    if (m) h("small", null, m.sources.map((x) => `${x.title} (${x.version}) · ${x.license}`).join(" · "), li);
  }
  for (const kind of c.outdated || []) h("li", "warn", t("cat.outdated", { kind: t(`cat.kind.${kind}`) }), ul);
  $("catalogRemove").hidden = !c.installed.off;
  $("catalogImport").hidden = !ANDROID;
  $("catalogServerNote").hidden = ANDROID;
  $("catalogServerNote").textContent = t("cat.server_import");
}
async function catalogOp(payload, btn, okMsg) {
  $("catalogErr").hidden = true;
  const b = $(btn), label = b.textContent; b.disabled = true; b.textContent = t("common.saving");
  try {
    const r = await postJson("/api/catalog", payload);
    toast(typeof okMsg === "function" ? okMsg(r) : okMsg);
    await loadCatalog();
  } catch (e) {
    showErr("catalogErr", e.message);
  } finally {
    b.disabled = false; b.textContent = label;
  }
}
// answer of the app after "Import from file" (the file picker and the checks run on the phone)
window.helioCatalogImported = (r) => {
  $("catalogStatus").hidden = true;
  if (r && r.ok) toast(t("cat.imported", { n: r.rows, version: r.version }));
  else showErr("catalogErr", (r && r.message) || t("cat.import_failed"));
  loadCatalog();
};
let removeArmed = false;
$("catalogImport").addEventListener("click", async () => {
  try {
    await postJson("/api/android/action", { action: "catalog_import" });
    // the check and the copy run in the app after the file is chosen: a large package takes a minute or two
    $("catalogStatus").textContent = t("cat.import_running");
    $("catalogStatus").hidden = false;
  } catch (e) { showErr("catalogErr", e.message); }
});
$("catalogRemove").addEventListener("click", () => {
  // two taps: the first one asks for confirmation
  if (!removeArmed) { removeArmed = true; $("catalogRemove").textContent = t("cat.remove_confirm"); setTimeout(() => { removeArmed = false; $("catalogRemove").textContent = t("cat.remove_off"); }, 4000); return; }
  removeArmed = false;
  catalogOp({ op: "remove", kind: "off" }, "catalogRemove", t("cat.removed"));
});
$("catalogBench").addEventListener("click", async () => {
  const b = $("catalogBench"); b.disabled = true; $("catalogBenchOut").textContent = t("cat.bench_running");
  try {
    const r = await getJson(`/api/catalog/bench?lang=${I18N.lang()}`);
    $("catalogBenchOut").textContent = t("cat.bench_result", { n: r.queries.length, median: r.median_ms, max: r.max_ms });
  } catch (e) {
    $("catalogBenchOut").textContent = t("err.failed", { msg: e.message });
  } finally { b.disabled = false; }
});

/* ================= About: licences and attributions ================= */
const ABOUT_SOFTWARE = [
  ["Gadgetbridge", "AGPL-3.0-or-later", "https://codeberg.org/Freeyourgadget/Gadgetbridge"],
  ["ZXing", "Apache-2.0", "https://github.com/zxing/zxing"],
  ["AndroidX CameraX", "Apache-2.0", "https://developer.android.com/jetpack/androidx/releases/camera"],
  ["Python", "PSF-2.0", "https://docs.python.org/3/license.html"],
  ["Chaquopy", "MIT", "https://chaquo.com/chaquopy/"],
];
const RITMO_URL = "https://github.com/compassionee/ritmo";
function extLink(parent, href, text) {
  const a = h("a", null, text || href, parent);
  a.href = href; a.target = "_blank"; a.rel = "noopener noreferrer";
  return a;
}
async function openAbout() {
  if (!CATALOG) await loadCatalog();
  renderAbout();
  openModal("aboutSheet");
}
function renderAbout() {
  const app = $("aboutApp"); app.replaceChildren();
  app.append(`${t("about.app_text")} `);
  extLink(app, RITMO_URL);
  const data = $("aboutData"); data.replaceChildren();
  const c = CATALOG || { installed: {}, sources: {} };
  const versions = {};
  for (const m of Object.values(c.installed)) for (const x of m.sources) versions[x.id] = x.version;
  for (const id of ["off", "usda_fnd", "usda_sr", "ciqual"]) {
    const s = c.sources[id];
    if (!s) continue;
    const li = h("li", null, null, data);
    h("b", null, s.title, li);
    h("small", null, t(`about.cite.${id}`, { version: versions[id] || t("about.not_installed") }), li);
    const lic = h("small", null, `${t("about.licence")}: `, li);
    extLink(lic, s.license_url, s.license);
    if (s.contents_license) { lic.append(" · "); extLink(lic, s.contents_license_url, s.contents_license); }
    extLink(h("small", null, null, li), s.url);
  }
  h("li", null, t("about.it_names"), data);
  const soft = $("aboutSoft"); soft.replaceChildren();
  for (const [name, lic, url] of ABOUT_SOFTWARE) {
    const li = h("li", null, null, soft);
    h("b", null, name, li); h("small", null, lic, li); extLink(h("small", null, null, li), url);
  }
}
$("aboutOpen").addEventListener("click", openAbout);
$("catalogAbout").addEventListener("click", openAbout);

/* ================= language ================= */
// choice saved in the app's data (settings.json, included in the export); empty = system language
function renderLangChoice() {
  const sel = $("langSel");
  if (sel) sel.value = I18N.choice() || "";
}
async function changeLang(choice) {
  try {
    await postJson("/api/settings", { lang: choice || null });
  } catch (e) {
    toast(t("err.not_saved", { msg: e.message }), true);
    renderLangChoice();
    return;
  }
  await I18N.set(choice || null);
  renderAll();
}

$("sheetClose").addEventListener("click", closeSheet);
$("sheet").addEventListener("click", (e) => { if (e.target === $("sheet")) closeSheet(); });
// times typed in the sheet -> instants, with the offset of that day (clock block): a field left as it was keeps the
// saved instant (also the second 02:30 of the day the clock goes back)
function sheetInterval() {
  const from = $("sheetFrom").value, to = $("sheetTo").value;
  if (!from || !to) return null;
  const hm = (v) => v.split(":").map(Number);
  const day = isoAt(sheetDayStart);
  const a = sheetW && from === hhmm(sheetW.start) ? sheetW.start : wallToTs(day, ...hm(from));
  let b = sheetW && to === hhmm(sheetW.end) ? sheetW.end : wallToTs(day, ...hm(to));
  if (b <= a) b = wallToTs(day, ...hm(to), true);       // in the repeated hour: its second occurrence
  if (b <= a) b = wallToTs(addDays(day, 1), ...hm(to));  // ending after midnight
  return { start: a, end: b };
}
$("sheetSave").addEventListener("click", () => {
  // hidden field = ignored value: from strength with 15 kg to swimming the load is not sent
  const load_ = usesLoad(sheetType) ? parseFloat(String($("sheetLoad").value).replace(",", ".")) || 0 : 0;
  const err = (m) => { $("sheetErr").textContent = m; $("sheetErr").hidden = false; };
  if (sheetType === "strength" && !sheetDay) return err(t("v.strength_day"));
  if (sheetManual) {
    const iv = sheetInterval();
    if (!iv) return err(t("v.times"));
    if (iv.end - iv.start > 12 * 3600) return err(t("v.max_12h"));  // elapsed: at most 12 hours
    return saveLabel({ ...(sheetW || {}), ...iv }, sheetType, sheetDay, load_, $("sheetSave"), true, sheetW ? sheetW.start : null,
      sheetWorkLevel());
  }
  saveLabel(sheetW, sheetType, sheetDay, load_, $("sheetSave"), false, null, sheetWorkLevel());
});
$("sheetRemove").addEventListener("click", () => saveLabel(sheetW, null, null, 0, $("sheetRemove"), sheetManual));
$("addActivity").addEventListener("click", () => { if (S) openSheet(null, S); });
for (const [sheet, btn] of [["strSheet", "strClose"], ["planSheet", "planClose"], ["wSheet", "wClose"], ["profSheet", "profClose"], ["pickSheet", "pickClose"],
  ["entrySheet", "entryClose"], ["foodSheet", "foodClose"], ["recipeSheet", "recipeClose"], ["mealSheet", "mealClose"],
  ["aboutSheet", "aboutClose"]]) {
  $(btn).addEventListener("click", () => closeModal(sheet));
  $(sheet).addEventListener("click", (e) => { if (e.target === $(sheet)) closeModal(sheet); });
}
$("sheetLoad").addEventListener("input", () => { sheetLoadTouched = true; });
$("profWork").addEventListener("change", () => { $("profWorkNote").textContent = t(`work.${$("profWork").value}.desc`); });
$("logStrength").addEventListener("click", () => openStrength());
$("strengthOpen").addEventListener("click", () => openStrength());
$("strSave").addEventListener("click", () => {
  const raw = String($("strLoad").value).trim().replace(",", ".");
  const loadKg = raw === "" ? 0 : Number(raw);
  if (!(loadKg >= 0 && loadKg <= 60)) { $("strErr").textContent = t("v.load_range"); $("strErr").hidden = false; return; }
  saveForm("strSheet", "strSave", "strErr", "/api/strength", { date: current, entries: readStrength(), load_kg: loadKg }, t("str.saved"));
});
$("planOpen").addEventListener("click", openPlan);
$("planAddEx").addEventListener("click", () => { savePlanFields(); planEdit[planSel].exercises.push(["", ""]); drawPlanDay(); const r = $("planExList").lastElementChild; if (r) r.querySelector(".pe-name").focus(); });
$("planSave").addEventListener("click", () => { savePlanFields(); saveForm("planSheet", "planSave", "planErr", "/api/plan", { plan: planEdit }, t("plan.saved")); });
$("planReset").addEventListener("click", () => saveForm("planSheet", "planReset", "planErr", "/api/plan", { reset: true }, t("plan.cleared")));
$("profOpen").addEventListener("click", openProfile);
$("profSave").addEventListener("click", () => saveForm("profSheet", "profSave", "profErr", "/api/profile", { profile: readProfile() }, t("prof.saved")));
$("profReset").addEventListener("click", () => saveForm("profSheet", "profReset", "profErr", "/api/profile", { reset: true }, t("prof.reset_done")));
$("langSel").addEventListener("change", (e) => changeLang(e.target.value));
$("weightOpen").addEventListener("click", openWeight);
$("wSave").addEventListener("click", () => {
  const kg = Number(String($("wKg").value).replace(",", "."));
  if (!$("wKg").value || !(kg >= 30 && kg <= 250)) { $("wErr").textContent = t("v.weight_range"); $("wErr").hidden = false; return; }
  if (!$("wDate").value || $("wDate").value > today()) { $("wErr").textContent = t("v.date_today"); $("wErr").hidden = false; return; }
  saveForm("wSheet", "wSave", "wErr", "/api/weight", { date: $("wDate").value, kg }, t("w.saved"));
});
$("exportRange").addEventListener("change", exportLinks);
$("exportFmt").addEventListener("change", exportLinks);
$("weekCur").addEventListener("click", () => { weekWhich = "current"; load(); });
$("weekPrev").addEventListener("click", () => { weekWhich = "previous"; load(); });
$("prev").addEventListener("click", () => { current = addDays(current, -1); load(); });
$("next").addEventListener("click", () => { if (current < today()) { current = addDays(current, 1); load(); } });
document.querySelectorAll(".tabbtn").forEach((b) => b.addEventListener("click", () => show(b.dataset.tab)));
document.querySelectorAll("[data-go]").forEach((b) => b.addEventListener("click", () => show(b.dataset.go)));
$("avatar").addEventListener("click", () => show(tab === "profile" ? "today" : "profile"));
$("battChip").addEventListener("click", () => (ANDROID ? openBand() : show("profile")));
$("bandClose").addEventListener("click", () => closeModal("bandSheet"));
$("bandSheet").addEventListener("click", (e) => { if (e.target === $("bandSheet")) closeModal("bandSheet"); });
document.querySelectorAll("[data-band-action]").forEach((b) => b.addEventListener("click", () => bandAction(b.dataset.bandAction)));

// food
document.querySelectorAll("#goalSwitch button").forEach((b) => b.addEventListener("click", () => {
  if (NUT && b.dataset.goal !== NUT.goal) nutSave("/api/goal", { goal: b.dataset.goal }, t("nut.goal_set", { goal: b.textContent.toLowerCase() }));
}));
$("nutComplete").addEventListener("click", () => {
  if (NUT) nutSave("/api/diary", { date: current, op: "complete", complete: !NUT.complete }, t(NUT.complete ? "nut.reopened" : "nut.completed"), { btn: "nutComplete" });
});
document.querySelectorAll("#foodsSwitch button").forEach((b) => b.addEventListener("click", () => { foodsTab = b.dataset.k; if (FOODS) renderFoodsList(FOODS); }));
$("foodsSearch").addEventListener("input", () => { if (FOODS) renderFoodsList(FOODS); });
$("newFood").addEventListener("click", () => openFoodEditor(null));
$("newRecipe").addEventListener("click", () => openRecipeEditor(null));
$("newMeal").addEventListener("click", () => openMealEditor(null));
$("pickSearch").addEventListener("input", drawPick);
$("pickScan").addEventListener("click", scanBarcode);
$("pickNewFood").addEventListener("click", () => { closeModal("pickSheet"); openFoodEditor(null, $("pickSearch").value.trim(), pickMeal); });
$("pickNewRecipe").addEventListener("click", () => { closeModal("pickSheet"); openRecipeEditor(null, $("pickSearch").value.trim(), pickMeal); });
$("entryG").addEventListener("input", entryPreview);
$("entrySave").addEventListener("click", () => {
  const g = numIn("entryG");
  if (g == null || !(g >= 0.1 && g <= 5000)) return showErr("entryErr", t("v.grams"));
  const c = entryCtx;
  const payload = c.mode === "new" && c.cat ? { date: current, op: "add", meal: entryMeal, catalog: { src: c.cat.src, ref: c.cat.ref }, g, lang: I18N.lang() }
    : c.mode === "new" ? { date: current, op: "add", meal: entryMeal, food: c.food.id, g }
    : { date: current, op: "update", id: c.entry.id, g, meal: entryMeal };
  const name = c.cat ? catDisplayName(c.cat) : c.mode === "new" ? c.food.name : "";
  nutSave("/api/diary", payload, c.mode === "new" ? t("food.added", { name }) : t("food.entry_updated"), { sheet: "entrySheet", btn: "entrySave", err: "entryErr" });
});
$("entrySaveMine").addEventListener("click", () => { const c = entryCtx; closeModal("entrySheet"); openFoodEditor(null, "", c.meal, c.cat); });
$("entryRemove").addEventListener("click", () => nutSave("/api/diary", { date: current, op: "remove", id: entryCtx.entry.id }, t("food.entry_removed"),
  { sheet: "entrySheet", btn: "entryRemove", err: "entryErr" }));
for (const [id] of FOOD_FIELDS) $(id).addEventListener("input", foodCheck);
$("foodAddPortion").addEventListener("click", () => addPortionRow());
$("foodSave").addEventListener("click", async () => {
  const f = readFood();
  if (!f.name) return showErr("foodErr", t("v.food_name"));
  if (baseFields().some((k) => f.per100[k] == null)) return showErr("foodErr", t("v.food_base"));
  if (f.portions.some((p) => !p[0] || !(p[1] > 0))) return showErr("foodErr", t("v.portion"));
  const res = await nutSave("/api/food", { food: f, apply_past: !!foodEdit && $("foodPast").checked }, savedMsg("food.saved"),
    { sheet: "foodSheet", btn: "foodSave", err: "foodErr" });
  afterFoodSaved(res);
});
$("foodDelete").addEventListener("click", () => nutSave("/api/food", { delete: foodEdit.id }, t("food.deleted"), { sheet: "foodSheet", btn: "foodDelete", err: "foodErr" }));
$("recipeAddItem").addEventListener("click", () => addItemRow($("recipeItems"), {}, "ingredientNames", recipePreview));
$("recipeCooked").addEventListener("input", recipePreview);
$("recipeSave").addEventListener("click", async () => {
  const name = $("recipeName").value.trim(), { items, bad } = readItems($("recipeItems"), true);
  if (!name) return showErr("recipeErr", t("v.recipe_name"));
  if (bad.length) return showErr("recipeErr", t("v.ingredients_bad", { names: bad.join(", ") }));
  if (!items.length) return showErr("recipeErr", t("v.ingredient_one"));
  const cooked = numIn("recipeCooked");
  if (cooked != null && !(cooked >= 1 && cooked <= 20000)) return showErr("recipeErr", t("v.cooked_range"));
  const food = { id: recipeEdit ? recipeEdit.id : undefined, kind: "recipe", name, items: items.map(([id, g]) => [id, g]), cooked_g: cooked };
  const res = await nutSave("/api/food", { food, apply_past: !!recipeEdit && $("recipePast").checked }, savedMsg("recipe.saved"),
    { sheet: "recipeSheet", btn: "recipeSave", err: "recipeErr" });
  afterFoodSaved(res);
});
$("recipeDelete").addEventListener("click", () => nutSave("/api/food", { delete: recipeEdit.id }, t("recipe.deleted"), { sheet: "recipeSheet", btn: "recipeDelete", err: "recipeErr" }));
$("mealAddItem").addEventListener("click", () => addItemRow($("mealItems"), {}, "foodNames", mealPreview));
$("mealSave").addEventListener("click", () => {
  const name = $("mealName").value.trim(), { items, bad } = readItems($("mealItems"), false);
  if (!name) return showErr("mealErr", t("v.meal_name"));
  if (bad.length) return showErr("mealErr", t("v.meal_items_bad", { names: bad.join(", ") }));
  if (!items.length) return showErr("mealErr", t("v.meal_item_one"));
  const meal = { id: mealEdit ? mealEdit.id : undefined, name, items: items.map(([id, g]) => [id, g]),
    meal: mealEdit ? mealEdit.meal : mealFrom ? mealFrom.key : null };
  nutSave("/api/meal", { meal }, t("meal.saved"), { sheet: "mealSheet", btn: "mealSave", err: "mealErr" });
});
$("mealDelete").addEventListener("click", () => nutSave("/api/meal", { delete: mealEdit.id }, t("meal.deleted"), { sheet: "mealSheet", btn: "mealDelete", err: "mealErr" }));

let sx = null, sy = null;
document.addEventListener("touchstart", (e) => {
  // no day change with a sheet open or while scrolling a row of buttons
  const busy = [...document.querySelectorAll(".sheet")].some((x) => !x.hidden);
  if (busy || (e.target.closest && e.target.closest(".chips, .seg-switch, input, select, textarea"))) { sx = null; return; }
  sx = e.touches[0].clientX; sy = e.touches[0].clientY;
}, { passive: true });
document.addEventListener("touchend", (e) => {
  if (sx == null) return;
  const dx = e.changedTouches[0].clientX - sx, dy = e.changedTouches[0].clientY - sy;
  sx = null;
  if (Math.abs(dx) > 70 && Math.abs(dx) > Math.abs(dy) * 1.5) (dx > 0 ? $("prev") : $("next")).click();
}, { passive: true });

if (ANDROID) pullInstall();
document.addEventListener("visibilitychange", () => { if (!document.hidden) { load(); loadBand(); } });
if (ANDROID) setInterval(() => { if (!document.hidden) loadBand(); }, 15 * 1000);
setInterval(() => { if (!document.hidden && current === today()) load(); }, 5 * 60 * 1000);
if ("serviceWorker" in navigator && window.isSecureContext) navigator.serviceWorker.register("sw.js").catch(() => {});

async function start() {
  let saved = null;
  try { saved = (await getJson("/api/settings")).lang || null; } catch (e) { saved = null; }
  try { await I18N.set(saved); } catch (e) { await I18N.set("en").catch(() => {}); }
  const restoreY = ANDROID ? viewInstall() : null;
  await load();
  if (restoreY) requestAnimationFrame(() => window.scrollTo({ top: restoreY }));
  loadBand();
  loadCatalog();
}
start();
