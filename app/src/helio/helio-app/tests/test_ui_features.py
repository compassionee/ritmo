"""Browser tests of the newer features: week, HRV advice, strength log, editable plan,
weight, CSV export, sources with conflicts of interest and labels on the Zepp algorithms.

Like test_ui.py: Chromium with Playwright on the development computer, skipped on the server.
"""
import json
import unittest
import urllib.request
from pathlib import Path

from test_ui import CATALOG, UIBase, sync_playwright

try:
    from playwright.sync_api import expect
except ImportError:
    expect = None

PLAN = {"2": {"text": "Strength training – day 1", "types": ["strength"], "day": 1,
              "exercises": [["Wide-grip pull-ups", "4×8"], ["Plank", "3×1 min"], ["Lunges with 15 kg", "3×10"],
                            ["Rope finisher", "10 min"]]}}


@unittest.skipIf(sync_playwright is None, "Playwright not installed (normal on the server)")
class UIFeatures(UIBase):
    # ------------------------------------------------------------ helpers
    def api(self, path, body):
        req = urllib.request.Request(self.base.rstrip("/") + path, data=json.dumps(body).encode(), method="POST",
                                     headers={"Content-Type": "application/json", "X-Helio": "1"})
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read())

    def reload(self):
        self.h._daycache.clear()
        self.page.reload()
        expect(self.page.locator("#workoutList .card").first).to_be_attached()

    def tab(self, name):
        if name == "profile":
            self.page.click("#avatar")
        else:
            self.page.click(f'button.tabbtn[data-tab="{name}"]')
        expect(self.page.locator(f"#tab-{name}")).to_be_visible()

    def toast(self, text):
        expect(self.page.locator("#toast")).to_have_text(text)

    def with_plan(self, plan=PLAN):
        self.assertEqual(self.api("/api/plan", {"plan": plan}), {"ok": True})
        self.reload()

    def state_json(self, name):
        f = self.state / name
        return json.loads(f.read_text()) if f.exists() else None

    # ------------------------------------------------------------ Today
    def test_calorie_range(self):
        expect(self.page.locator("#kcalRange")).to_contain_text(self.Lpart("today.kcal_range", 1))

    def test_week_card_and_switch(self):
        expect(self.page.locator("#weekCard")).to_be_visible()
        expect(self.page.locator("#weekTitle")).to_contain_text(self.Lpart("wk.current"))
        expect(self.page.locator("#weekDays .wd")).to_have_count(7)
        self.page.click("#weekPrev")
        expect(self.page.locator("#weekTitle")).to_contain_text(self.Lpart("wk.previous"))
        expect(self.page.locator("#weekPrev")).to_have_class("on")
        self.page.click("#weekCur")
        expect(self.page.locator("#weekTitle")).to_contain_text(self.Lpart("wk.current"))
        expect(self.page.locator("#weekSrc summary")).to_contain_text(self.L("src.fold", n=5))

    def test_week_card_shows_plan_status(self):
        self.with_plan()
        # Tuesday strength planned, walk and run done: "did other"
        expect(self.page.locator("#weekDays .wd.st-different")).to_have_count(1)
        expect(self.page.locator("#weekTitle")).to_contain_text(self.L("wk.title_plan", title="", done=0, total=1).lstrip(" ·"))

    def test_advice_box(self):
        expect(self.page.locator("#adviceBox")).to_be_hidden()       # no plan, no advice
        self.with_plan({"2": {"text": "Run", "types": ["run"], "exercises": []}})
        expect(self.page.locator("#adviceBox")).to_be_visible()
        expect(self.page.locator("#adviceTitle")).to_have_text(self.L("adv.collecting.title"))
        expect(self.page.locator("#adviceSrc summary")).to_have_text(self.L("src.fold", n=3))
        expect(self.page.locator("#logStrength")).to_be_hidden()

    def test_secondary_cards_do_not_block_the_day(self):
        self.page.route("**/api/week*", lambda r: r.fulfill(status=500, body="{}", content_type="application/json"))
        self.page.route("**/api/weight", lambda r: r.abort())
        self.page.click("#prev")
        expect(self.page.locator("#dayLabel")).to_have_text(self.L("day.yesterday"))
        expect(self.page.locator("#error")).to_be_hidden()
        expect(self.page.locator("#weekCard")).to_be_hidden()
        self.tab("profile")
        self.page.click("#weightOpen")
        expect(self.page.locator("#toast")).to_contain_text(self.L("w.unavailable"))
        self.page.unroute("**/api/week*")
        self.page.unroute("**/api/weight")
        self.errors.clear()  # the answers rejected on purpose show up in the console

    # ------------------------------------------------------------ labels and sources
    def test_proprietary_labels(self):
        self.tab("health")
        expect(self.page.locator("#stressCard")).to_contain_text(self.L("ui.health.zepp_algorithm"))
        expect(self.page.locator("#stressNote")).to_contain_text(self.L("note.zepp")[:40])
        self.tab("fitness")
        expect(self.page.locator("#paiTag")).to_have_text(self.L("ui.fitness.licensed_algorithm"))
        expect(self.page.locator("#paiNote")).to_contain_text(self.L("note.pai")[:40])
        expect(self.page.locator("#paiSrc summary")).to_contain_text(self.L("src.fold_coi", n=9, coi=1).split("·")[-1].strip())
        self.page.click("#paiSrc summary")
        expect(self.page.locator("#paiSrc .coi.warn")).to_have_count(1)
        self.tab("sleep")
        expect(self.page.locator("#sleepScore")).to_contain_text("Zepp")
        expect(self.page.locator("#sleepScoreNote")).to_be_visible()
        expect(self.page.locator("#sleepHighlights .coi.warn").first).to_be_visible()

    def test_sources_list(self):
        self.tab("profile")
        n = len(self.h.SOURCES)
        flagged = sum(1 for s in self.h.SOURCES.values() if s["coi"] == "present")
        self.assertEqual(flagged, 6)
        expect(self.page.locator("#sourcesSummary")).to_contain_text(self.L("src.show_all", n=n, coi=6))
        self.page.click("#sourcesSummary")
        expect(self.page.locator("#sourcesList .src-row")).to_have_count(n)
        expect(self.page.locator("#sourcesList .src-row.coi-present")).to_have_count(6)
        expect(self.page.locator("#removedList li")).to_have_count(len(self.h.REMOVED_SOURCES))
        expect(self.page.locator("#removedList")).to_contain_text("Keytel 2005")
        expect(self.page.locator("#removedList")).to_contain_text("IOM 2005")
        expect(self.page.locator("#kcalSrc summary")).to_contain_text(self.L("src.fold", n=10))

    def test_fitness_cards(self):
        self.tab("fitness")
        expect(self.page.locator("#loadNote")).to_contain_text(self.L("load.note")[:40])
        expect(self.page.locator("#loadAvg")).to_contain_text(self.L("fit.load_avg_wait", n=1))
        expect(self.page.locator("#vo2Value")).to_have_text("–")         # a single day of resting heart rate
        expect(self.page.locator("#vo2Text")).to_contain_text(self.L("vo2.need_text"))
        expect(self.page.locator("#hrmaxLabel")).to_contain_text(self.Lpart("hrmax.age"))
        expect(self.page.locator("#strengthList")).to_contain_text(self.L("str.none"))

    def test_sleep_regularity_collecting(self):
        self.tab("sleep")
        expect(self.page.locator("#sriValue")).to_have_text("–")
        expect(self.page.locator("#sriUnit")).to_contain_text(self.Lpart("sri.collecting"))
        expect(self.page.locator("#sriOnset")).to_have_text("–")               # a single night: no average of the times

    # ------------------------------------------------------------ plan
    def test_plan_editor_save_and_reset(self):
        self.tab("profile")
        self.page.click("#planOpen")
        expect(self.page.locator("#planSheet")).to_be_visible()
        expect(self.page.locator("#planDays button.on")).to_have_text(self.page.evaluate("I18N.fmt('2024-01-02', 'weekdayshort')"))  # Tuesday, the day shown
        self.page.fill("#planTextIn", "Test strength")
        self.page.locator("#planTypes button", has_text=self.L("act.strength")).click()
        expect(self.page.locator("#planStrengthDayRow")).to_be_visible()
        self.page.locator("#planStrengthDay button", has_text=self.L("plan.strength_day_n", n=1)).click()
        self.page.click("#planAddEx")
        row = self.page.locator("#planExList .pe-row").last
        row.locator(".pe-name").fill("Pull-ups")
        row.locator(".pe-detail").fill("3×8")
        self.page.click("#planSave")
        self.toast(self.L("plan.saved"))
        saved = self.state_json("plan.json")
        self.assertEqual(saved["2"], {"text": "Test strength", "types": ["strength"], "day": 1, "exercises": [["Pull-ups", "3×8"]]})
        expect(self.page.locator("#planSource")).to_contain_text(self.L("plan.src_app"))
        self.tab("today")
        expect(self.page.locator("#planText")).to_have_text("Test strength")
        expect(self.page.locator("#planEx")).to_contain_text("Pull-ups")
        expect(self.page.locator("#logStrength")).to_be_visible()
        self.tab("profile")
        self.page.click("#planOpen")
        expect(self.page.locator("#planTextIn")).to_have_value("Test strength")
        self.page.click("#planReset")
        self.toast(self.L("plan.cleared"))
        self.assertIsNone(self.state_json("plan.json"))
        self.tab("today")
        expect(self.page.locator("#planCard")).to_be_hidden()

    def test_plan_editor_keeps_other_days_and_reorders(self):
        self.with_plan({"2": {"text": "Strength", "types": ["strength"], "day": 1, "exercises": [["A", "1"], ["B", "2"], ["C", "3"]]},
                        "4": {"text": "Giovedì", "types": ["run"], "exercises": []}})
        self.tab("profile")
        self.page.click("#planOpen")
        self.page.locator("#planExList .pe-row").nth(1).locator(".pe-btn", has_text="↑").click()
        self.page.locator("#planExList .pe-row").nth(2).locator(".pe-btn", has_text="×").click()
        self.page.locator("#planDays button").nth(6).click()                      # Sunday
        self.page.fill("#planTextIn", "Work")
        self.page.locator("#planTypes button", has_text=self.L("act.work")).click()
        self.page.click("#planSave")
        self.toast(self.L("plan.saved"))
        saved = self.state_json("plan.json")
        self.assertEqual(saved["2"]["exercises"], [["B", "2"], ["A", "1"]])
        self.assertEqual(saved["4"]["text"], "Giovedì")
        self.assertEqual((saved["7"]["text"], saved["7"]["types"]), ("Work", ["work"]))

    def test_plan_server_error_is_shown(self):
        self.tab("profile")
        self.page.click("#planOpen")
        self.page.route("**/api/plan", lambda r: r.fulfill(status=400, content_type="application/json",
                                                            body=json.dumps({"error": "invalid plan"}))
                        if r.request.method == "POST" else r.continue_())
        self.page.click("#planSave")
        expect(self.page.locator("#planErr")).to_have_text(self.L("err.not_saved", msg="invalid plan"))
        expect(self.page.locator("#planSave")).to_have_text(self.L("common.retry"))
        expect(self.page.locator("#planSave")).to_be_enabled()
        self.page.unroute("**/api/plan")

    # ------------------------------------------------------------ strength
    def test_strength_log_flow(self):
        """One added load per session: it applies to every set, plank included (rope is not logged)."""
        self.with_plan()
        self.page.click("#logStrength")
        expect(self.page.locator("#strSheet")).to_be_visible()
        expect(self.page.locator("#strLoad")).to_have_value("15")                # from the plan (kg in the lunges name)
        expect(self.page.locator("#strLoadNote")).to_contain_text(self.L("str.load_plan"))
        ex = self.page.locator("#strList .str-ex")
        expect(ex).to_have_count(3)                                               # rope is not logged
        expect(self.page.locator("#strList .in-kg")).to_have_count(0)            # no kg set by set
        pull, plank, lunges = ex.nth(0), ex.nth(1), ex.nth(2)
        pull.locator(".in-reps").first.fill("8")
        pull.locator(".add-btn").click()
        pull.locator(".set-row").nth(1).locator(".in-reps").fill("6")
        plank.locator(".in-sec").fill("60")
        lunges.locator(".in-reps").fill("10")
        self.page.fill("#strLoad", "12.5")
        self.page.click("#strSave")
        self.toast(self.L("str.saved"))
        self.assertEqual(self.posts[-1]["load_kg"], 12.5)
        day = self.state_json("strength.json")["2026-03-10"]
        self.assertEqual(day["load_kg"], 12.5)
        saved = day["entries"]
        self.assertEqual(saved[0], {"name": "Wide-grip pull-ups", "sets": [{"reps": 8, "kg": 12.5, "sec": None},
                                                                           {"reps": 6, "kg": 12.5, "sec": None}]})
        self.assertEqual(saved[1]["sets"], [{"reps": None, "kg": 12.5, "sec": 60}])
        self.assertEqual(saved[2]["sets"], [{"reps": 10, "kg": 12.5, "sec": None}])
        self.tab("fitness")
        expect(self.page.locator("#strengthList")).to_contain_text("Wide-grip pull-ups")
        expect(self.page.locator("#strengthList")).to_contain_text(self.Lpart("str.e1rm_plus"))
        expect(self.page.locator("#strengthList")).to_contain_text(self.L("str.with_kg", kg=12.5))
        expect(self.page.locator("#strengthWeek")).to_contain_text(self.L("str.week_volume", n=1, sets=4, kg=300))
        self.page.click("#strengthOpen")                                          # reopening: saved values
        expect(self.page.locator("#strLoad")).to_have_value("12.5")
        expect(self.page.locator("#strLoadNote")).to_have_text("")
        expect(self.page.locator("#strList .str-ex").first.locator(".in-reps").nth(1)).to_have_value("6")

    def test_strength_load_validation(self):
        self.with_plan()
        self.page.click("#logStrength")
        self.page.locator("#strList .in-reps").first.fill("8")
        self.page.fill("#strLoad", "70")
        self.posts.clear()
        self.page.click("#strSave")
        expect(self.page.locator("#strErr")).to_have_text(self.L("v.load_range"))
        self.assertEqual(self.posts, [])
        self.page.fill("#strLoad", "")                                            # empty: bodyweight session
        self.page.click("#strSave")
        self.toast(self.L("str.saved"))
        self.assertEqual(self.state_json("strength.json")["2026-03-10"]["load_kg"], 0)

    def test_strength_day_switch_and_validation(self):
        self.with_plan({**PLAN, "4": {"text": "Strength 2", "types": ["strength"], "day": 2, "exercises": [["Squat", "4×25"]]}})
        self.page.click("#logStrength")
        self.page.locator("#strDays button", has_text=self.L("plan.strength_day_n", n=2)).click()
        expect(self.page.locator("#strDays button.on")).to_have_text(self.L("plan.strength_day_n", n=2))
        expect(self.page.locator("#strList .str-ex")).to_have_count(1)
        self.page.locator("#strList .in-reps").fill("500")
        self.page.click("#strSave")
        expect(self.page.locator("#strErr")).to_contain_text(self.L("e.set_reps"))
        expect(self.page.locator("#strSave")).to_have_text(self.L("common.retry"))
        self.assertIsNone(self.state_json("strength.json"))

    def test_remove_a_set(self):
        self.with_plan()
        self.page.click("#logStrength")
        pull = self.page.locator("#strList .str-ex").first
        pull.locator(".add-btn").click()
        pull.locator(".add-btn").click()
        expect(pull.locator(".set-row")).to_have_count(3)
        pull.locator(".set-row").nth(1).locator(".set-x").click()
        expect(pull.locator(".set-row")).to_have_count(2)
        expect(pull.locator(".set-n").nth(1)).to_have_text("2")                  # numbering updated

    # ------------------------------------------------------------ weight
    def test_weight_update_and_delete(self):
        self.tab("profile")
        expect(self.page.locator("#weightValue")).to_have_text("74")
        self.page.click("#weightOpen")
        self.page.fill("#wKg", "80.5")
        self.page.click("#wSave")
        self.toast(self.L("w.saved"))
        self.assertEqual(self.state_json("weights.json"), {"2026-03-10": 80.5})
        expect(self.page.locator("#weightValue")).to_have_text(self.page.evaluate("I18N.num(80.5, 1)"))
        expect(self.page.locator("#weightSrc")).to_have_text(self.L("w.since", d="2026-03-10"))
        expect(self.page.locator("#profileText")).to_contain_text(self.L("wsrc.log"))
        self.page.click("#weightOpen")
        expect(self.page.locator("#wList li")).to_have_count(1)
        self.page.locator("#wList .set-x").click()
        self.toast(self.L("w.removed"))
        expect(self.page.locator("#weightValue")).to_have_text("74")

    def test_weight_validation(self):
        self.tab("profile")
        self.page.click("#weightOpen")
        self.page.fill("#wKg", "20")
        self.posts.clear()
        self.page.click("#wSave")
        expect(self.page.locator("#wErr")).to_have_text(self.L("v.weight_range"))
        self.assertEqual(self.posts, [])

    # ------------------------------------------------------------ export
    def test_export_downloads(self):
        self.tab("profile")
        self.page.select_option("#exportRange", "90")
        href = self.page.get_attribute("#expDays", "href")
        self.assertIn("kind=days&from=2025-12-11&to=2026-03-10&fmt=eu", href)
        with self.page.expect_download() as dl:
            self.page.click("#expDays")
        text = Path(dl.value.path()).read_text(encoding="utf-8")
        cols = lambda *c: [CATALOG[f"csv.col.{x}"] for x in c]  # noqa: E731  (header in the page language)
        self.assertTrue(text.startswith("﻿" + ";".join(cols("date", "steps", "rope_jumps"))))
        self.assertEqual(len(text.strip().splitlines()), 91)
        self.assertEqual(dl.value.suggested_filename, "helio-days-2025-12-11_2026-03-10.csv")
        self.page.select_option("#exportFmt", "std")
        with self.page.expect_download() as dl2:
            self.page.click("#expTrain")
        self.assertTrue(Path(dl2.value.path()).read_text(encoding="utf-8").startswith(",".join(cols("date", "start", "end"))))


if __name__ == "__main__":
    unittest.main(verbosity=2)
