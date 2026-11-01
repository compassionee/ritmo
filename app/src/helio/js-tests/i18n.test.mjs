// The page's translation engine (static/i18n.js): system language, Intl formats, plurals,
// server messages and language switch without new API requests.
import { test } from "node:test";
import assert from "node:assert/strict";
import { loadI18n } from "./i18n-node.mjs";

test("switching language downloads only the catalog, once, and no data", async () => {
  const { I18N, fetched } = await loadI18n("it");
  const msg = { $: "si.short.text", total: 340 };
  const it = I18N.tx(msg);
  await I18N.set("en");
  const en = I18N.tx(msg);
  await I18N.set("it");
  assert.deepEqual(fetched, ["i18n/it.json", "i18n/en.json"]);   // no /api request, catalogs cached
  assert.equal(it, "Hai dormito 5h 40m: nelle meta-analisi il rischio più basso è intorno alle 7 ore.");  // lang: it
  assert.equal(en, "You slept 5h 40m: in meta-analyses the lowest risk is at about 7 hours.");
  assert.equal(I18N.tx(msg), it);
});

test("default language: the system's, English if it is not among the available ones", async () => {
  const { I18N } = await loadI18n("it");
  assert.equal(await I18N.set(null, ["it-CH", "en"]), "it");
  assert.equal(await I18N.set(null, ["en-US"]), "en");
  assert.equal(await I18N.set(null, ["de-DE", "fr"]), "en");
  assert.equal(await I18N.set(null, ["de-DE", "it-IT"]), "it");
  assert.equal(I18N.choice(), null);
  assert.equal(await I18N.set("en", ["it-IT"]), "en");             // the choice in the Profile wins
  assert.equal(I18N.choice(), "en");
});

test("numbers and dates with Intl in the chosen language", async () => {
  const { I18N } = await loadI18n("it", ["it-IT"]);
  assert.equal(I18N.t("ins.steps_goal.text", { steps: 12182, goal: 10000 }), "12.182 passi su 10.000.");  // lang: it
  assert.equal(I18N.num(12345.67, 1), "12.345,7");
  assert.equal(I18N.fmt("2026-03-10", "datelong"), "10 marzo 2026");
  // signed: typographic minus, "+" for positive values, Intl grouping
  assert.equal(I18N.fmt(-1188.4, "signed"), "−1.188");   // thousands always grouped, also with 4 digits
  assert.equal(I18N.fmt(2374, "signed"), "+2.374");
  assert.equal(I18N.num(2374, 0), "2.374");
  assert.equal(I18N.num(0.8, 2, true), "0,80");
  assert.equal(I18N.fmt(-26110, "signed"), "−26.110");
  // Italian elision before dates read with a vowel: uno, otto, undici; diciotto does not elide
  assert.equal(I18N.fmt("2026-02-11", "dal_date"), "dall'11 febbraio");
  assert.equal(I18N.fmt("2026-02-18", "dal_date"), "dal 18 febbraio");
  assert.equal(I18N.fmt("2026-03-01", "dal_date"), "dall'1 marzo");
  assert.equal(I18N.fmt("2026-03-08", "al_date"), "all'8 marzo");
  assert.equal(I18N.fmt("2026-03-05", "al_date"), "al 5 marzo");
  assert.equal(I18N.fmt("2026-03-11", "il_date"), "l'11 marzo");
  assert.equal(I18N.fmt("2026-03-08", "del_datelong"), "dell'8 marzo 2026");
  // month and year without the day: simple preposition, no article
  assert.equal(I18N.fmt("2026-04-10", "dal_month"), "da aprile 2026");
  assert.equal(I18N.fmt("2026-03-10", "dal_month"), "da marzo 2026");
  assert.equal(I18N.fmt("2026-04-10", "al_month"), "ad aprile 2026");
  assert.equal(I18N.fmt("2026-10-10", "al_month"), "a ottobre 2026");
  assert.equal(I18N.fmt("2026-08-10", "del_month"), "di agosto 2026");
  // plan of the week: understandable without context
  assert.equal(I18N.t("wk.title_plan", { title: "Settimana", done: 0, n: 1 }), "Settimana · piano: 0 di 1 seduta fatta");  // lang: it
  assert.equal(I18N.t("wk.title_plan", { title: "Settimana", done: 2, n: 3 }), "Settimana · piano: 2 di 3 sedute fatte");  // lang: it
  // counted nouns inside a message: each count with its own plural form
  assert.equal(I18N.t("quality.summary", { hr: { $: "quality.hr", n: 1, total: 1410 }, motion: { $: "quality.motion", n: 0 },
    dips: { $: "spo2.dips", n: 1 }, spo2_n: 4 }),
  "Battito: 1 minuto scartato su 1.410 come errore del sensore. SpO₂: 0 letture in movimento scartate e 1 calo isolato su 4.");  // lang: it
  assert.equal(I18N.t("comp.trend", { n: 8, from: "2026-02-11", to: "2026-03-08", span: 25, v: "−1,52", lo: "−1,67", hi: "−1,38" })
    .slice(0, 41), "Retta di 8 pesate dall'11 febbraio all'8 ");  // lang: it
  assert.equal(I18N.fmt(105, "signed"), "+105");
  assert.equal(I18N.fmt(0.2, "signed"), "0");
  await I18N.set("en", ["en-GB"]);
  assert.equal(I18N.fmt(-1188, "signed"), "−1,188");
  assert.equal(I18N.t("comp.trend", { n: 8, from: "2026-02-11", to: "2026-03-08", span: 25, v: "1", lo: "0", hi: "2" })
    .slice(0, 46), "Line through 8 weigh-ins from 11 February to 8");
  assert.equal(I18N.t("ins.steps_goal.text", { steps: 12182, goal: 10000 }), "12,182 of 10,000 steps.");
  assert.equal(I18N.t("str.rep_sets", { sets: { $: "str.n_sets", n: 1 }, reps: { $: "str.n_reps", n: 8 } }), "1 set · 8 reps");
  assert.equal(I18N.fmt("2026-03-10", "datelong"), "10 March 2026");
  await I18N.set("en", ["en-US"]);
  assert.equal(I18N.locale(), "en-US");
  assert.equal(I18N.fmt("2026-03-10", "datelong"), "March 10, 2026");
});

test("plurals, lists, joined sentences and missing keys", async () => {
  const { I18N } = await loadI18n("en", ["en-GB"]);
  assert.equal(I18N.t("ins.hr_fixes.title", { n: 1 }), "1 sensor error corrected");
  assert.equal(I18N.t("ins.hr_fixes.title", { n: 3 }), "3 sensor errors corrected");
  assert.equal(I18N.tx(["a", "b", "c"]), "a, b and c");
  assert.equal(I18N.tx({ $: "+", parts: [{ $: "adv.rhr_also_high" }, null, "End."] }),
    "Your resting heart rate is also above your range. End.");
  assert.equal(I18N.t("no.such.key"), "⟦no.such.key⟧");
  assert.equal(I18N.tx("user's own text"), "user's own text");      // user data stays as it is
  await I18N.set("it");
  assert.equal(I18N.t("ins.hr_fixes.title", { n: 1 }), "1 errore del sensore corretto");  // lang: it
  assert.equal(I18N.tx(["a", "b", "c"]), "a, b e c");                // Italian list conjunction
});
