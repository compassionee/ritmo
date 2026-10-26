// The page's i18n.js loaded in Node: the catalogs are read from disk with a fake fetch that counts
// the requests (for the tests that check what is downloaded).
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import vm from "node:vm";

export const STATIC = join(dirname(fileURLToPath(import.meta.url)), "../helio-app/static");

export async function loadI18n(lang = "it", languages = ["it-IT"]) {
  const fetched = [];
  const ctx = {
    navigator: { languages, language: languages[0] },
    fetch: async (url) => {
      fetched.push(String(url));
      const body = readFileSync(join(STATIC, String(url)), "utf8");
      return { ok: true, status: 200, json: async () => JSON.parse(body) };
    },
    Intl, Number, String, Date, Math, Object, Array, JSON,
  };
  vm.createContext(ctx);
  vm.runInContext(readFileSync(join(STATIC, "i18n.js"), "utf8") + "\nthis.I18N = I18N;", ctx);
  await ctx.I18N.set(lang);
  return { I18N: ctx.I18N, fetched };
}
