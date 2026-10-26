"""Tests of the source metadata: study type, funding and conflicts of interest.

The project's rule: sources with conflicts of interest are replaced by independent sources;
they stay only if there is no alternative, and in that case they are flagged with the reason.
"""
import json
import re
import tempfile
import unittest
from pathlib import Path

from test_helio import T, APP_DIR, DAY, build_db, load_app

SERVER = (APP_DIR / "helio_app.py").read_text()
JS = (APP_DIR / "static" / "app.js").read_text()
HTML = (APP_DIR / "static" / "index.html").read_text()
CAT = {lang: json.loads((APP_DIR / "static" / "i18n" / f"{lang}.json").read_text()) for lang in ("it", "en")}
COI = {"none", "not_declared", "present"}
KINDS = {k.split(".", 2)[2] for k in CAT["it"] if k.startswith("src.kind.")}


def collect(obj, out):
    """All sources (dictionaries with url and coi) inside an API answer."""
    if isinstance(obj, dict):
        if "url" in obj and "coi" in obj and "title" in obj:
            out.append(obj)
        for k, v in obj.items():
            if k != "sources_index":
                collect(v, out)
    elif isinstance(obj, list):
        for v in obj:
            collect(v, out)
    return out


class Metadata(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        build_db(Path(cls.tmp.name) / "gb.db")
        cls.h = load_app(Path(cls.tmp.name) / "gb.db", HELIO_STATE=str(Path(cls.tmp.name) / "state"))

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_every_source_is_complete(self):
        for k, s in self.h.SOURCES.items():
            self.assertTrue(s["title"].strip(), k)
            self.assertRegex(s["url"], r"^https://[a-z0-9.-]+\.[a-z]{2,}/", k)
            self.assertIn(s["kind"], KINDS, k)
            self.assertIn(s["coi"], COI, k)
            for lang in CAT:  # summary and notes: in the catalogs, in every language
                self.assertTrue(len(CAT[lang].get(f"src.{k}.note", "")) > 15, (k, lang))
                self.assertTrue(CAT[lang].get(f"src.{k}.what"), (k, lang))
            self.assertRegex(s["title"], r"\d{4}|American Academy|Australian", k)  # year or institution

    def test_sources_with_conflicts_are_flagged_with_a_reason(self):
        for k, s in self.h.SOURCES.items():
            if s["coi"] == "present":
                self.assertTrue(s.get("flagged"), k)
            if s.get("flagged"):  # also without declared conflicts, if flagged for other reasons (e.g. consensus)
                self.assertTrue(len(CAT["it"].get(f"src.{k}.why", "")) > 20, k)
                self.assertTrue(len(CAT["en"].get(f"src.{k}.why", "")) > 20, k)

    def test_flagged_sources_are_few_and_known(self):
        flagged = {k for k, s in self.h.SOURCES.items() if s.get("flagged")}
        # the three nutrition ones: no independent alternative on protein for strength training (Refalo 2025,
        # Morton 2018) and the carbohydrate doses for athletes, shown only as a reference (ACSM 2016)
        self.assertEqual(flagged, {"nsf_q", "ohayon", "pai", "refalo", "morton_prot", "acsm_nutr"})

    def test_removed_sources_are_gone(self):
        for k in self.h.REMOVED_SOURCES:
            self.assertNotIn(k, self.h.SOURCES)
            for lang in CAT:
                self.assertTrue(len(CAT[lang].get(f"srcrm.{k}.why", "")) > 20, (k, lang))
        self.assertNotIn("PMC2613573", SERVER + JS + HTML)          # wrong link of the step bands
        self.assertNotIn("keytel_gross", SERVER)
        self.assertNotIn("Keytel", JS + HTML)
        self.assertNotRegex(SERVER, r"\(Keytel\)")
        self.assertNotIn("PMC7534938", SERVER + JS + HTML)          # old reference to the acute/chronic ratio
        self.assertNotIn("HUNT, su oltre 40.000", SERVER + JS + HTML)  # old PAI sentence without declared conflicts  # lang: it

    def test_main_evidence_is_independent(self):
        for k in ("paluch", "who", "zhang", "cappuccio", "momma", "han", "granero", "ferri_marini", "tanaka",
                  "madden", "fuller", "odriscoll", "ais_hrv", "vesterinen", "bittencourt", "impellizzeri"):
            self.assertEqual(self.h.SOURCES[k]["coi"], "none", k)

    def test_meta_analyses_back_the_health_report(self):
        kinds = {s["kind"] for s in self.h.SOURCES.values()}
        self.assertIn("meta_analysis", kinds)
        report = self.h.trends(DAY)["health"]
        for it in report["items"]:
            strong = [s for s in it["sources"] if s["kind"] in ("meta_analysis", "guideline", "systematic_review", "consensus")]
            self.assertTrue(strong, it["key"])

    def test_every_source_used_by_the_api_is_known(self):
        s = self.h.summary(DAY)
        found = collect(s, []) + collect(self.h.trends(DAY), []) + collect(self.h.week_summary(DAY), [])
        found += collect(self.h.strength_day(DAY), [])
        self.assertTrue(found)
        for f in found:
            self.assertIn(f["key"], self.h.SOURCES)
            self.assertEqual(f["coi"], self.h.SOURCES[f["key"]]["coi"])

    def test_no_unused_sources(self):
        used = set(re.findall(r'"([a-z_]+)"', " ".join(re.findall(r"src\(([^)]*)\)", SERVER))))
        for rule in self.h.CLEAN_RULES:
            used |= set(rule["sources"])
        used |= set(re.findall(r'\["([a-z_]+)"(?:, "[a-z_]+")*\]', SERVER))
        used |= set(re.findall(r'\("([a-z_]+)",\)', SERVER))
        used |= {m for grp in re.findall(r'\[((?:"[a-z_]+",? ?)+)\]', SERVER) for m in re.findall(r'"([a-z_]+)"', grp)}
        unused = sorted(set(self.h.SOURCES) - used)
        self.assertEqual(unused, [], f"sources never cited: {unused}")

    def test_sources_index_lists_everything(self):
        idx = self.h.summary(DAY)["sources_index"]
        self.assertEqual({s["key"] for s in idx["sources"]}, set(self.h.SOURCES))
        self.assertEqual({r["key"] for r in idx["removed"]}, set(self.h.REMOVED_SOURCES))
        json.dumps(idx)

    def test_proprietary_metrics_are_labelled(self):
        s = self.h.summary(DAY)
        for k in ("stress", "sleep_score", "pai"):
            note = s["proprietary"][k]
            self.assertTrue(note["text"])
            self.assertTrue(note["sources"])
        self.assertIn("unpublished", T(s["proprietary"]["stress"]["text"]))
        self.assertIn("35%", T(s["proprietary"]["sleep_score"]["text"]))  # wake specificity (Concheiro 2023)
        self.assertIn("licensing", T(s["proprietary"]["pai"]["text"]))
        self.assertNotIn("pai", [i["key"] for i in self.h.trends(DAY)["health"]["items"]])  # PAI is no longer an assessment

    def test_page_shows_conflict_badges_and_proprietary_labels(self):
        self.assertIn('src.coi === "present"', JS)
        self.assertIn("conflitti", CAT["it"]["src.coi_badge"])
        self.assertIn('data-i18n="ui.health.zepp_algorithm"', HTML)
        self.assertEqual(CAT["en"]["ui.health.zepp_algorithm"], "Zepp algorithm")
        self.assertIn('data-i18n="ui.fitness.licensed_algorithm"', HTML)

    def test_method_texts_match_the_sources(self):
        self.assertIn("heart rate reserve", T(self.h.DEFAULT_KCAL_METHOD))
        self.assertIn("Looney", T(self.h.DEFAULT_KCAL_METHOD))
        txt = T(self.h.summary(DAY)["load_note"]["text"])
        self.assertIn("Banister", txt)
        self.assertIn("dropping it", txt)


if __name__ == "__main__":
    unittest.main(verbosity=2)
