"use strict";
// Page translations. Texts live in i18n/<lang>.json (same keys in every language); the
// server sends neutral messages {"$": key, ...params} and the page renders them here, with dates and
// numbers formatted by Intl in the chosen language. Changing language does not reload data: it redraws.
//
// Placeholders: {name} or {name:format}. Formats: 0, 1, 2 (decimals), int, time (epoch seconds),
// date, dateshort, datelong, weekday, weekdayshort, month, dur (minutes), pct, list, raw.
// Plurals: the value is an object {"one": ..., "other": ...} chosen by the parameter n.
const I18N = (() => {
  const LANGS = ["it", "en"];
  const LOCALES = { it: "it-IT", en: "en-GB" };
  const cache = {};
  const listeners = [];
  let lang = "it", dict = {}, choice = null, tz = undefined, sysList = [];

  function systemLang(list) {
    for (const l of list || []) {
      const base = String(l || "").slice(0, 2).toLowerCase();
      if (LANGS.includes(base)) return base;
    }
    return "en";
  }

  async function loadDict(l) {
    if (!cache[l]) {
      const r = await fetch(`i18n/${l}.json`, { cache: "no-cache" });
      if (!r.ok) throw new Error(`i18n ${l}: ${r.status}`);
      cache[l] = await r.json();
    }
    return cache[l];
  }

  const locale = () => {
    // English: the system variant if it is English (en-US, en-AU...), otherwise en-GB
    if (lang === "en") {
      const en = sysList.find((x) => String(x || "").toLowerCase().startsWith("en-"));
      if (en) return en;
    }
    return LOCALES[lang];
  };

  function num(v, digits) {
    if (v == null || Number.isNaN(Number(v))) return "–";
    return Number(v).toLocaleString(locale(), { maximumFractionDigits: digits, minimumFractionDigits: 0 });
  }
  const isoDate = (s) => { const [y, m, d] = String(s).split("-").map(Number); return new Date(Date.UTC(y, m - 1, d, 12)); };
  const dateFmt = (s, o) => isoDate(s).toLocaleDateString(locale(), { timeZone: "UTC", ...o });

  function dur(min) {
    if (min == null) return "–";
    min = Math.round(min);
    const h = Math.floor(min / 60), m = min % 60;
    return h ? t("fmt.dur_hm", { h, m: String(m).padStart(2, "0") }) : t("fmt.dur_m", { m });
  }

  function format(v, spec) {
    if (v == null) return "–";
    if (v && typeof v === "object" && !Array.isArray(v)) return tx(v);
    switch (spec) {
      case "0": case "int": return num(v, 0);
      case "1": return num(v, 1);
      case "2": return num(v, 2);
      case "time": return new Date(v * 1000).toLocaleTimeString(locale(), { hour: "2-digit", minute: "2-digit", timeZone: tz });
      case "date": return dateFmt(v, { day: "numeric", month: "long" });
      case "datelong": return dateFmt(v, { day: "numeric", month: "long", year: "numeric" });
      case "dateshort": return dateFmt(v, { day: "numeric", month: "numeric" });
      case "weekday": return dateFmt(v, { weekday: "long" });
      case "weekdayshort": return dateFmt(v, { weekday: "short" });
      case "month": return dateFmt(v, { month: "long", year: "numeric" });
      case "dur": return dur(v);
      case "pct": return `${num(v, 0)}%`;
      case "list": return list(v);
      case "raw": return String(v);
      default:
        if (Array.isArray(v)) return list(v);
        return typeof v === "number" ? num(v, 1) : String(v);
    }
  }

  function list(items) {
    const parts = (items || []).map((x) => (x && typeof x === "object" ? tx(x) : String(x)));
    try { return new Intl.ListFormat(locale(), { style: "long", type: "conjunction" }).format(parts); }
    catch (e) { return parts.join(", "); }
  }

  function pick(entry, params) {
    if (entry && typeof entry === "object") {
      const n = Number(params && params.n);
      const cat = new Intl.PluralRules(locale()).select(Number.isFinite(n) ? n : 0);
      return entry[cat] ?? entry.other;
    }
    return entry;
  }

  // text of the key with its parameters; a missing key is visible (and the tests report it)
  function t(key, params = {}) {
    const raw = pick(dict[key], params);
    if (raw == null) return `⟦${key}⟧`;
    return raw.replace(/\{(\w+)(?::(\w+))?\}/g, (m, name, spec) => format(params[name], spec));
  }

  // server message ({"$": key, ...}; {"$": "+", parts} = joined sentences), ready text
  // (user data) or list
  function tx(v) {
    if (v == null) return "";
    if (Array.isArray(v)) return list(v);
    if (typeof v === "object" && v.$ === "+") return (v.parts || []).map(tx).filter(Boolean).join(" ");
    if (typeof v === "object" && typeof v.$ === "string") return t(v.$, v);
    return String(v);
  }

  // static elements: data-i18n for the text, data-i18n-attr="placeholder:key;aria-label:key"
  function apply(root) {
    if (!root && typeof document === "undefined") return;  // outside the browser (tests)
    const scope = root || document;
    scope.querySelectorAll("[data-i18n]").forEach((n) => { n.textContent = t(n.dataset.i18n); });
    scope.querySelectorAll("[data-i18n-attr]").forEach((n) => {
      for (const pair of n.dataset.i18nAttr.split(";")) {
        const [attr, key] = pair.split(":").map((s) => s.trim());
        if (attr && key) n.setAttribute(attr, t(key));
      }
    });
    if (typeof document !== "undefined" && document.documentElement) document.documentElement.lang = lang;
  }

  // choice: "it", "en" or null (system language)
  async function set(next, systemList) {
    choice = LANGS.includes(next) ? next : null;
    sysList = systemList || (typeof navigator !== "undefined" ? navigator.languages || [navigator.language] : []);
    const target = choice || systemLang(sysList);
    dict = await loadDict(target);
    lang = target;
    apply();
    for (const cb of listeners) cb(lang);
    return lang;
  }

  return {
    LANGS, t, tx, set, apply, list, dur,
    num: (v, d = 1) => num(v, d),
    fmt: format,
    lang: () => lang,
    choice: () => choice,
    locale,
    systemLang,
    setTimeZone: (z) => { tz = z; },
    onChange: (cb) => listeners.push(cb),
  };
})();
const t = I18N.t, tx = I18N.tx;
