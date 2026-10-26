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
  await I18N.set("en", ["en-GB"]);
  assert.equal(I18N.t("ins.steps_goal.text", { steps: 12182, goal: 10000 }), "12,182 of 10,000 steps.");
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
