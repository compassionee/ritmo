"""Translation tests (standard library only).

- every key exists in every language, with the same placeholders and the same plural forms;
- every key used by the code (Python, page, Java) exists in the catalogs, including those built at runtime;
- no visible text written directly in the code (Python, app.js, index.html, Java);
- changing language redraws without reloading data.
"""
import ast
import json
import re
import tempfile
import unittest
from pathlib import Path

from test_helio import APP_DIR, DAY, build_db, load_app

STATIC = APP_DIR / "static"
LANGS = ("it", "en")
CAT = {lang: json.loads((STATIC / "i18n" / f"{lang}.json").read_text()) for lang in LANGS}
JS = (STATIC / "app.js").read_text()
HTML = (STATIC / "index.html").read_text()
PY_FILES = [APP_DIR / "helio_app.py", APP_DIR / "helio_transfer.py", APP_DIR / "helio_science.py"]
# in the Android app (the Gadgetbridge flavor) there are also the Python bridge and the Java code
FLAVOR = APP_DIR.parent if (APP_DIR.parent / "python" / "helio_android.py").is_file() else None
if FLAVOR:
    PY_FILES.append(FLAVOR / "python" / "helio_android.py")
JAVA = sorted(FLAVOR.glob("java/**/*.java")) if FLAVOR else []

PLACEHOLDER = re.compile(r"\{(\w+)(?::(\w+))?\}")
FORMATS = {None, "0", "1", "2", "int", "time", "date", "dateshort", "datelong", "weekday", "weekdayshort", "month",
           "dur", "pct", "list", "raw", "signed"} | {f"{p}_{d}" for p in ("il", "dal", "al", "del")
                                                     for d in ("date", "datelong", "dateshort", "month")}


def forms(v):
    return list(v.values()) if isinstance(v, dict) else [v]


def placeholders(v):
    return {m.group(1) for f in forms(v) for m in PLACEHOLDER.finditer(f)}


def strip_js_comments(code):
    code = re.sub(r"/\*.*?\*/", "", code, flags=re.S)
    return re.sub(r"(?m)^\s*//.*$|(?<=[;{}),])\s*//[^\n\"'`]*$", "", code)


def fstring_pattern(node):
    """Regex of a key built with an f-string: M(f"act.{k}") -> ^act\\.[^.]+$"""
    parts = []
    for v in node.values:
        parts.append(re.escape(v.value) if isinstance(v, ast.Constant) else r"[\w-]+")
    return "^" + "".join(parts) + "$"


def python_keys(path):
    """Keys used in a Python file: (literals, patterns of the built keys)."""
    tree = ast.parse(path.read_text())
    lits, pats = set(), set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Call):
            f = n.func
            name = f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", "")
            arg = None
            if name in ("M", "UserError", "TransferError") and n.args:
                arg = n.args[0]
            elif name == "tr" and len(n.args) > 1:
                arg = n.args[1]
            if arg is None:
                continue
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                lits.add(arg.value)
            elif isinstance(arg, ast.JoinedStr):
                pats.add(fstring_pattern(arg))
        # {"$": "key"} written by hand (Android bridge)
        if isinstance(n, ast.Dict):
            for k, v in zip(n.keys, n.values):
                if isinstance(k, ast.Constant) and k.value == "$" and isinstance(v, ast.Constant) and v.value != "+":
                    lits.add(v.value)
    return lits, pats


def js_keys():
    code = strip_js_comments(JS)
    lits = set(re.findall(r'\bt\(\s*"([\w.-]+)"', code))
    pats = {"^" + re.sub(r"\\\$\\\{[^}]*\\\}", r"[\\w-]+", re.escape(k)) + "$"
            for k in re.findall(r"\bt\(\s*`([^`]+)`", code)}
    # keys passed to functions or tables and translated later (STATUS_TXT, saveForm...): "a.b" strings known to the catalog
    for k in re.findall(r'"([a-z][\w-]*\.[\w.-]+)"', code):
        if k.split(".")[0] in {x.split(".")[0] for x in CAT["it"]}:
            lits.add(k)
    lits |= set(re.findall(r'data-i18n="([\w.-]+)"', HTML))
    for spec in re.findall(r'data-i18n-attr="([^"]+)"', HTML):
        lits |= {p.split(":")[1].strip() for p in spec.split(";") if ":" in p}
    return lits, pats


def message_keys(obj, out):
    """All message keys {"$": key} inside an API answer."""
    if isinstance(obj, dict):
        if isinstance(obj.get("$"), str) and obj["$"] != "+":
            out.add(obj["$"])
        for v in obj.values():
            message_keys(v, out)
    elif isinstance(obj, list):
        for v in obj:
            message_keys(v, out)
    return out


class Catalogs(unittest.TestCase):
    def test_same_keys_in_every_language(self):
        for lang in LANGS[1:]:
            self.assertEqual(sorted(set(CAT["it"]) - set(CAT[lang])), [], f"keys missing in {lang}")
            self.assertEqual(sorted(set(CAT[lang]) - set(CAT["it"])), [], f"keys only in {lang}")

    def test_same_placeholders_and_plural_forms(self):
        for key, it in CAT["it"].items():
            for lang in LANGS[1:]:
                other = CAT[lang][key]
                self.assertEqual(type(it), type(other), (key, lang))
                if isinstance(it, dict):
                    self.assertIn("other", it, key)
                    self.assertIn("other", other, (key, lang))
                    self.assertEqual(placeholders(it) | {"n"}, placeholders(other) | {"n"}, (key, lang))
                else:
                    self.assertEqual(placeholders(it), placeholders(other), (key, lang))

    def test_formats_are_known_and_texts_not_empty(self):
        for lang in LANGS:
            for key, v in CAT[lang].items():
                for f in forms(v):
                    self.assertTrue(f.strip(), (key, lang))
                    for m in PLACEHOLDER.finditer(f):
                        self.assertIn(m.group(2), FORMATS, (key, lang, m.group(0)))

    def test_italian_prepositions_before_dates_use_the_elision_formats(self):
        # "dal {d:date}" would print "dal 11 febbraio": the preposition goes in the format ({d:dal_date} -> "dall'11")  lang: it
        bare = re.compile(r"(?i)\b(il|dal|al|del|nel|sul)\s+\{\w+:(date|datelong|dateshort|month)\}")  # lang: it
        for key, v in CAT["it"].items():
            for f in forms(v):
                self.assertNotRegex(f, bare, key)
        self.assertIn("{from:dal_date} {to:al_date}", CAT["it"]["comp.trend"])

    def test_band_means_only_the_device(self):
        # in Italian "fascia" is the device; a personal range is an "intervallo", heart rate "zone", steps "livelli"
        as_range = re.compile(r"(?i)\b(tua|tue|mia|nella|sulla)\s+fasc[ei]a?\b"  # lang: it
                              r"|\bfasc[ei]a?\s+(personal[ei]|dei|del|delle)\b")  # lang: it
        for key, v in CAT["it"].items():
            for f in forms(v):
                self.assertNotRegex(f, as_range, key)
        self.assertEqual(CAT["it"]["vit.building"], "Il tuo intervallo personale è in costruzione")  # lang: it
        # and in English "band" is the device, not a range
        for key, v in CAT["en"].items():
            for f in forms(v):
                self.assertNotRegex(f, r"(?i)\b(your|personal|step)\s+bands?\b|\bin\s+band\b", key)

    def test_no_leftover_italian_in_english(self):
        it_words = re.compile(r"\b(?:il|della|degli|delle|nella|sono|giorni|allenamento|riposo|sonno)\b", re.I)  # lang: it
        for key, v in CAT["en"].items():
            if key.startswith(("csv.v.", "lang.")):
                continue
            for f in forms(v):
                self.assertNotRegex(f, it_words, key)


# Plural nouns (and the words that agree with them) a count can govern: "{n} giorni" prints "1 giorni" when n is 1.  # lang: it
PLURAL_WORDS = {
    "it": set("giorni anni mesi settimane ore minuti notti passi pesate alimenti prodotti ricette pasti porzioni voci "  # lang: it
              "caratteri ricerche letture cali punti fonti sedute allenamenti volte risvegli errori salti segmenti "  # lang: it
              "esercizi misure righe studi prove partecipanti coorti segnalate scartate scartati inclusi esclusi "  # lang: it
              "corretti corrette aggiunti fatte".split()),  # lang: it
    "en": set("days years months weeks hours minutes nights steps weigh-ins foods products recipes meals portions entries "
              "characters searches readings dips points sources sessions workouts times awakenings errors jumps segments "
              "exercises sets reps measurements rows studies trials participants cohorts".split()),
}
# words that end the search for the noun: "{total} come errori" is not "{total} errori"  # lang: it
NOUN_STOP = set("come per con di del della dei degli nel nella nei negli in su tra da a al as for with of on from to".split())  # lang: it
# "{a} of {b} days": the noun goes with the second count
COUNT = re.compile(r"\{(\w+)(?::(?:0|int))?\}(?:\s*(?:/|di|su|of|out of)\s*\{(\w+)(?::(?:0|int))?\})?")  # lang: it
# Counts that are never 1, so a plural noun is right: thresholds and limits of the code, values shown only above a goal
NEVER_ONE = {
    "adv.collecting.text": {"need"}, "adv.collecting.title": {"need"},          # BASE_MIN nights (14)
    "health.need_days": {"need"}, "nut.need_days": {"need"}, "nut.energy_need": {"need"},  # 3 days
    "sri.collecting": {"need"},                                                   # SRI_MIN_PAIRS (120 hours)
    "bc.need": {"weighins", "span"}, "wc.need": {"weighins", "span", "days"},    # WEIGHT_CHECK (4, 21, 21)
    "wc.short_need": {"need_w", "need_d"}, "comp.short.need": {"weighins", "span"},
    "bc.short_period": {"span"}, "comp.short.period": {"span"},
    "comp.trend": {"n", "span"},                                  # computed only with at least 4 weigh-ins over 21 days
    "comp.choice": {"months"}, "comp.fm_old": {"months"},         # 6 months, flagged only beyond 6
    "comp.sig.strength": {"ref"}, "bc.sig.better": {"n"}, "bc.sig.mixed": {"n"},  # STRENGTH_WEEK (2 days)
    "bc.you_why.both_good": {"n"}, "bc.you_why.both_low": {"n"}, "bc.you_why.protein_only": {"n"},
    "bc.you_why.strength_only": {"n"},
    "bc.need_measure": {"age", "ahi"}, "err.birth_range": {"hi"},  # ages from PROFILE_AGE (10-110) and the BMI estimate
    "prof.short": {"age"}, "prof.summary": {"age", "steps"},       # step goal at least 1,000
    "ins.steps_goal.text": {"steps", "goal"}, "ins.val.steps": {"v"},  # shown only with the step goal reached
    "ins.who_week.text": {"min"},                                  # shown only above the weekly goal (at least 30)
    "e.entries_max": {"max"}, "e.export_days": {"max"}, "e.foods_max": {"max"}, "e.items_count": {"max"},
    "e.meals_max": {"max"}, "e.plan_text_long": {"max"}, "e.portions_max": {"max"}, "e.sets_max": {"max"},
    "e.text_len": {"max"}, "err.name_invalid": {"max"},
}


def counted_nouns(text, lang):
    """(count placeholder, words) for each count followed by a plural noun within three words."""
    for m in COUNT.finditer(text):
        words = []
        for w in text[m.end():].split()[:3]:
            word = w.strip(".,;:()·").lower()
            if "{" in w or word in NOUN_STOP:
                break
            words.append(word)
            if w != w.rstrip(".,;:()·"):
                break
        if any(w in PLURAL_WORDS[lang] for w in words):
            yield m.group(2) or m.group(1), m.group(0) + " " + " ".join(words)


class PluralForms(unittest.TestCase):
    """A count before a plural noun comes from a plural entry ({"one", "other"} chosen by n) or is never 1;
    texts with several counts take each one as a message with its own plural ({"$": "spo2.dips", "n": 1})."""

    def test_detector(self):
        self.assertEqual([g for g, _ in counted_nouns("{dips} cali isolati su {spo2_n}", "it")], ["dips"])  # lang: it
        self.assertEqual([g for g, _ in counted_nouns("{n} o più giorni", "it")], ["n"])  # lang: it
        self.assertEqual([g for g, _ in counted_nouns("{d} di {need_d} giorni completi", "it")], ["need_d"])  # lang: it
        self.assertEqual([g for g, _ in counted_nouns("{min:0} zone minutes", "en")], ["min"])
        self.assertEqual(list(counted_nouns("{v:0} kcal, {total} come errori; {d:dur} di passi", "it")), [])  # lang: it

    def test_numbers_have_plural_forms(self):
        found, bad = set(), []
        for lang in LANGS:
            for key, v in CAT[lang].items():
                for form, text in (v.items() if isinstance(v, dict) else [(None, v)]):
                    for count, words in counted_nouns(text, lang):
                        if count in NEVER_ONE.get(key, ()):
                            found.add((key, count))
                        elif not (isinstance(v, dict) and count == "n" and form != "one"):
                            bad.append((lang, key, form, words))
        self.assertEqual(bad, [])
        # no stale exception
        self.assertEqual(sorted({(k, c) for k, cs in NEVER_ONE.items() for c in cs} - found), [])

    def test_nested_counts_pick_their_own_form(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "gb.db"
            build_db(db)
            h = load_app(db, HELIO_STATE=str(Path(tmp) / "state"))
            summary = h.tr("it", "quality.summary", hr={"$": "quality.hr", "n": 1, "total": 1410},
                           motion={"$": "quality.motion", "n": 2}, dips={"$": "spo2.dips", "n": 1}, spo2_n=40)
            self.assertEqual(summary, "Battito: 1 minuto scartato su 1.410 come errore del sensore. "  # lang: it
                                      "SpO₂: 2 letture in movimento scartate e 1 calo isolato su 40.")  # lang: it
            self.assertTrue(h.tr("en", "h.strength.base", min=h.M("common.n_minutes", n=1)).endswith("In the last 7 days: 1 minute."))


class KeysUsedExist(unittest.TestCase):
    def check(self, lits, pats, where):
        missing = sorted(k for k in lits if k not in CAT["it"])
        self.assertEqual(missing, [], f"keys used in {where} but missing from the catalogs")
        for p in pats:
            self.assertTrue(any(re.match(p, k) for k in CAT["it"]), f"{where}: no key for the pattern {p}")

    def test_python(self):
        for path in PY_FILES:
            self.check(*python_keys(path), path.name)

    def test_page(self):
        self.check(*js_keys(), "app.js / index.html")

    def test_java(self):
        for path in JAVA:
            keys = set(re.findall(r'errorResponse\(\s*\d+,\s*"[^"]*",\s*"([\w.]+)"\)', path.read_text()))
            self.check(keys, set(), path.name)

    def test_every_message_of_the_api_exists(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "gb.db"
            build_db(db)
            h = load_app(db, HELIO_STATE=str(Path(tmp) / "state"))
            h.save_profile({"profile": {"altitude_goal_m": 3000}})
            found = set()
            for res in (h.summary(DAY), h.trends(DAY), h.vitals(DAY), h.week_summary(DAY), h.strength_day(DAY),
                        h.plan_payload(), h.weight_payload(), h.profile_payload(), h.foods_payload(),
                        h.nutrition_day(DAY), h.settings_load(), h.balance_payload(DAY, "28d"),
                        h.balance_payload(DAY, "since")):
                message_keys(res, found)
            self.assertGreater(len(found), 100)
            self.assertEqual(sorted(k for k in found if k not in CAT["it"]), [])
            # and every message renders in every language without leftover placeholders
            for lang in LANGS:
                for k in found:
                    self.assertNotIn("{", h.tr(lang, k), (k, lang))


# ---------------------------------------------------------------- text written in the code
WORDS = re.compile(r"[A-Za-zÀ-ÿ]{3,}")
SQL = re.compile(r"^\s*(SELECT|INSERT|UPDATE|DELETE|CREATE|PRAGMA|WITH|AND)\b|\bFROM\b|\bWHERE\b")
# allowed technical texts: HTTP headers, MIME types, policies, original source citations
PY_ALLOWED = re.compile(r"charset=|default-src|Max-Age=|realm=|attachment; filename|^[\w.+-]+/[\w.+-]+$|^Not found$"
                        r"|^(?:Not Found|Bad Request|Method Not Allowed|Forbidden|Connection: close|Content-Length: \{\})$"
                        r"|^[\w.]+\(\) was not called$|^read only$")
# words searched in texts typed by the user (exercise names), never shown
PY_KEYWORDS = {"wall sit", "warm up"}


def python_texts(path):
    tree = ast.parse(path.read_text())
    skip = set()
    for n in ast.walk(tree):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Module)) and n.body \
                and isinstance(n.body[0], ast.Expr) and isinstance(n.body[0].value, ast.Constant):
            skip.add(id(n.body[0].value))  # docstring
        if isinstance(n, ast.Call):
            f = n.func
            name = f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", "")
            # server log, command line, regex, SQL, translation keys
            if name in ("print", "debug", "info", "warning", "error", "exception", "log_message", "compile", "match",
                        "search", "sub", "findall", "fullmatch", "execute", "executemany", "executescript",
                        "add_argument", "add_parser", "ArgumentParser", "M", "tr", "UserError", "TransferError"):
                for a in ast.walk(n):
                    skip.add(id(a))
        # source citations: original title, not translated
        if isinstance(n, ast.Assign) and any(getattr(t, "id", "") in ("SOURCES", "REMOVED_SOURCES") for t in n.targets):
            for a in ast.walk(n.value):
                skip.add(id(a))
    out = []
    for n in ast.walk(tree):
        if id(n) in skip:
            continue
        if isinstance(n, ast.JoinedStr):
            s = "".join(v.value if isinstance(v, ast.Constant) else "{}" for v in n.values)
            for v in ast.walk(n):
                skip.add(id(v))
        elif isinstance(n, ast.Constant) and isinstance(n.value, str):
            s = n.value
        else:
            continue
        if len(WORDS.findall(s)) >= 2 and " " in s.strip() and not SQL.search(s) and not PY_ALLOWED.search(s) \
                and s not in PY_KEYWORDS:
            out.append((n.lineno, s))
    return out


# words found in almost every Italian or English sentence (CSS classes and codes do not have them)
FUNCTION_WORDS = re.compile(r"(?i)(?:^|\s)(?:di|il|la|le|per|con|del|della|nel|una|un|è|sono|the|of|and|to|with|"  # lang: it
                            r"for|is|are|your|il tuo|la tua)(?:\s|$)")  # lang: it


def js_texts():
    code = strip_js_comments(JS)
    out = []
    for m in re.finditer(r'"((?:[^"\\\n]|\\.)*)"|`((?:[^`\\]|\\.)*)`|\'((?:[^\'\\\n]|\\.)*)\'', code):
        s = next(x for x in m.groups() if x is not None)
        plain = re.sub(r"\$\{[^}]*\}", " ", s)
        after = code[m.end():m.end() + 2].lstrip()
        if after.startswith(":"):
            continue  # object key (server codes)
        if FUNCTION_WORDS.search(plain) or (WORDS.search(plain) and re.match(r"[A-ZÀ-Ý][a-zà-ÿ]+\b", plain.strip())
                                            and " " in plain.strip()):
            out.append(s)
    # single texts set by hand in elements: h(tag, class, "Text"), x.textContent = "Text"
    out += re.findall(r'\bh\("\w+", [^,]+, "([A-Za-zÀ-ÿ][^"]*)"', code)
    out += re.findall(r'\.(?:textContent|title|placeholder|innerText|ariaLabel) = "([A-Za-zÀ-ÿ][^"]*)"', code)
    out += re.findall(r'\b(?:toast|alert|confirm)\("([^"]*[A-Za-zÀ-ÿ][^"]*)"', code)
    out += re.findall(r'setAttribute\("(?:aria-label|title|placeholder)", "([^"]+)"\)', code)
    # only texts with letters (not symbols such as ×)
    return [s for s in out if s != "use strict" and re.search(r"[^\W\d_]", s)]


class NoHardcodedText(unittest.TestCase):
    def test_python(self):
        for path in PY_FILES:
            self.assertEqual(python_texts(path), [], f"{path.name}: texts to move to the catalogs")

    def test_app_js(self):
        self.assertEqual(js_texts(), [], "app.js: texts to move to the catalogs (use t())")

    def test_index_html(self):
        body = re.sub(r"<script.*?</script>|<style.*?</style>|<!--.*?-->", "", HTML, flags=re.S)
        texts = []
        for m in re.finditer(r"<(\w+)([^>]*)>([^<]*)", body):
            tag, attrs, text = m.groups()
            if re.search(r"[A-Za-zÀ-ÿ]{2,}", text) and "data-i18n=" not in attrs and tag != "title":
                texts.append(text.strip())
            for attr in ("placeholder", "aria-label", "title", "alt"):
                if re.search(rf'\s{attr}="[^"]*[A-Za-zÀ-ÿ]', attrs):
                    texts.append(f"{tag}[{attr}]")
        self.assertEqual(texts, [], "index.html: texts without data-i18n / data-i18n-attr")

    def test_java(self):
        texts = []
        for path in JAVA:
            code = re.sub(r"/\*.*?\*/|//[^\n]*", "", path.read_text(), flags=re.S)
            for s in re.findall(r'"((?:[^"\\]|\\.)*)"', code):
                # Italian texts in the code: they belong in res/values*/strings.xml
                if re.search(r"(?i)\b(?:non|della|richiesta|errore|riprova|salvat|importa|esporta)\b", s):  # lang: it
                    texts.append(f"{path.name}: {s}")
        self.assertEqual(texts, [])

    def test_android_strings_in_both_languages(self):
        if not FLAVOR:
            self.skipTest("only in the Gadgetbridge flavor")
        # translatable="false" (an example key, the app's name) is only in the default resources
        names = lambda p: set(re.findall(r'<string name="(helio_\w+)"(?! translatable="false")', p.read_text()))  # noqa: E731
        en = names(FLAVOR / "res" / "values" / "strings.xml")
        it = names(FLAVOR / "res" / "values-it" / "strings.xml")
        self.assertTrue(en)
        self.assertEqual(en, it)


# ---------------------------------------------------------------- Italian outside it.json
def italian_words():
    """Words of the Italian catalog that never appear in the English one (from the catalogs, so this file
    needs no word list): "strong" ones, plus a few that also occur in English text or code ("weak")."""
    def counts(cat):
        c = {}
        for v in cat.values():
            for f in forms(v):
                for w in re.findall(r"[A-Za-zÀ-ÿ']+", PLACEHOLDER.sub(" ", f)):
                    c[w.lower()] = c.get(w.lower(), 0) + 1
        return c
    it, en = counts(CAT["it"]), counts(CAT["en"])
    words = {w for w, n in it.items() if n >= 8 and w not in en and len(w) >= 2}
    weak = {"non", "la", "un", "da", "al", "le", "su", "si", "se", "media", "solo", "serie", "fc", "ha", "studio",  # lang: it
            "massa", "piano", "oms", "dato", "con", "lo", "e"}  # lang: it
    return words - weak, words & weak


IT_STRONG, IT_WEAK = italian_words()
IT_ACCENT = re.compile(r"\b\w*[àèìòù]\b")


def italian_line(line):
    """True if the line looks like Italian prose. Placeholders, links, identifiers (a_b, a.b, a-b) and
    single quoted words (codes stored in the data) are ignored; lines marked "lang: it" are allowed."""
    if "lang: it" in line:
        return False
    text = re.sub(r"\{[^{}]*\}|https?://\S+|[\w$]+(?:[._-][\w$]+)+", " ", line)
    text = re.sub(r"([\'\"`])[^\s\'\"`]*\1", " ", text)
    words = [w.lower() for w in re.findall(r"[A-Za-zÀ-ÿ']+", text)]
    strong = sum(w in IT_STRONG for w in words)
    weak = sum(w in IT_WEAK for w in words)
    return bool(IT_ACCENT.search(text)) or (strong >= 1 and strong + weak >= 2) or strong >= 2


def english_only_files():
    """Every file of the project that must be in English: all but it.json and res/values-it."""
    pats = ["*.py", "*.sh", "static/*.js", "static/*.html", "static/*.css", "static/*.json", "static/i18n/en.json",
            "tests/*.py", "tests/*.json", "tools/*.py"]
    files = [p for pat in pats for p in APP_DIR.glob(pat)]
    if FLAVOR:
        for pat in ("*.md", "*.py", "*.sh", "*.txt", "AndroidManifest.xml", "python/*.py", "python-tests/*.py",
                    "js-tests/*", "assets/**/*.js", "java/**/*.java", "res/values/*.xml"):
            files += FLAVOR.glob(pat)
        files += (FLAVOR.parent / "testHelio").glob("**/*.java")
        repo = FLAVOR.parents[2]
        if (repo / "docs").is_dir():
            files += [repo / "README.md"] + list((repo / "docs").glob("*.md"))
    return sorted({f for f in files if f.is_file() and f.name != "it.json"})


class EnglishOutsideItJson(unittest.TestCase):
    """Code, comments, tests, docs and scripts are in English: Italian text belongs only in it.json and
    res/values-it. Lines that must contain Italian (for example checks of the Italian rendering or of
    Italian keywords) are marked with "lang: it"."""

    def test_detector(self):
        self.assertTrue(italian_line(CAT["it"]["h.steps.0"]))
        self.assertTrue(italian_line("# " + CAT["it"]["adv.rest.text"]))
        self.assertFalse(italian_line(CAT["en"]["h.steps.0"]))
        self.assertFalse(italian_line('if t == "forza" and day["riposo"]:  # strength day'))  # lang: it
        self.assertFalse(italian_line(CAT["it"]["adv.rest.text"] + "  # lang: it"))

    def test_no_italian_outside_it_json(self):
        found = []
        for path in english_only_files():
            for n, line in enumerate(path.read_text(errors="replace").split("\n"), 1):
                if italian_line(line):
                    found.append(f"{path.relative_to(APP_DIR.parent)}:{n}: {line.strip()[:100]}")
        self.assertEqual(found, [], "Italian text outside it.json:\n" + "\n".join(found[:40]))


class LanguageSwitch(unittest.TestCase):
    """Changing language redraws from the data already loaded: no new API request."""

    def body(self, name):
        start = JS.index(f"function {name}(")
        depth, i = 0, JS.index("{", start)
        while True:
            depth += {"{": 1, "}": -1}.get(JS[i], 0)
            if depth == 0:
                return JS[start:i + 1]
            i += 1

    def test_change_lang_only_saves_the_choice_and_redraws(self):
        body = self.body("changeLang")
        self.assertIn('postJson("/api/settings"', body)
        self.assertIn("I18N.set(", body)
        self.assertIn("renderAll()", body)
        for call in ("load(", "getJson(", "fetch(", "location.reload"):
            self.assertNotIn(call, body.replace("I18N.set(", ""), call)

    def test_render_all_uses_cached_data(self):
        body = self.body("renderAll")
        for call in ("load(", "getJson(", "fetch(", "postJson(", "location.reload"):
            self.assertNotRegex(body, rf"(?<![\w.]){re.escape(call)}", call)

    def test_language_is_saved_in_local_data_and_exported(self):
        import helio_transfer
        self.assertTrue(helio_transfer.STATE_RE.match("settings.json"))
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "gb.db"
            build_db(db)
            h = load_app(db, HELIO_STATE=str(Path(tmp) / "state"))
            self.assertEqual(h.settings_load(), {"lang": None, "hints": [], "save_mine": False, "reminders": h.REMINDER_DEFAULTS, "hr_seconds_keep": 90})  # default: system language
            h.save_settings({"lang": "en"})
            self.assertEqual(h.settings_load(), {"lang": "en", "hints": [], "save_mine": False, "reminders": h.REMINDER_DEFAULTS, "hr_seconds_keep": 90})
            with self.assertRaises(h.UserError):
                h.save_settings({"lang": "fr"})
            # a usage hint once used: only that field changes, the language stays
            h.save_settings({"hint": "day_complete"})
            h.save_settings({"hint": "day_complete"})
            self.assertEqual(h.settings_load(), {"lang": "en", "hints": ["day_complete"], "save_mine": False, "reminders": h.REMINDER_DEFAULTS, "hr_seconds_keep": 90})
            # "Save also to my foods" remembered: only that field changes
            h.save_settings({"save_mine": True})
            self.assertEqual(h.settings_load(), {"lang": "en", "hints": ["day_complete"], "save_mine": True, "reminders": h.REMINDER_DEFAULTS, "hr_seconds_keep": 90})
            for bad in ({"hint": "other"}, {}, {"save_mine": "yes"}, {"save_mine": None}):
                with self.assertRaises(h.UserError):
                    h.save_settings(bad)
            archive = helio_transfer.export_bytes(str(Path(tmp) / "state"))
            _, files = helio_transfer.read_archive(archive)
            self.assertEqual(json.loads(files["settings.json"]), {"lang": "en", "hints": ["day_complete"], "save_mine": True})
            # and back with the import, into another phone
            other = Path(tmp) / "other"
            helio_transfer.import_bytes(archive, str(other))
            h2 = load_app(db, HELIO_STATE=str(other))
            self.assertTrue(h2.settings_load()["save_mine"])

    def test_csv_follows_the_language(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "gb.db"
            build_db(db)
            h = load_app(db)
            it = h.export_csv("days", DAY, DAY, "eu", "it").splitlines()[0]
            en = h.export_csv("days", DAY, DAY, "std", "en").splitlines()[0]
            self.assertTrue(it.lstrip("﻿").startswith(h.tr("it", "csv.col.date") + ";" + h.tr("it", "csv.col.steps")))
            self.assertTrue(en.startswith("date,steps"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
