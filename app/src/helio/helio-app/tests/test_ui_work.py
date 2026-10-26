"""Browser tests of the work intensity and of the day's energy (Chromium with Playwright; skipped without it).

- "Active work" shows the three intensities, each with its Compendium 2024 code and MET; the choice is saved with
  the activity, an untouched one follows the Profile's default, which the Profile sheet sets.
- Today, Fitness and Food show the same energy for today, as "at least" (the day is not over).
"""
import json
import unittest

from test_ui import UIBase, sync_playwright

if sync_playwright is not None:
    from playwright.sync_api import expect


@unittest.skipIf(sync_playwright is None, "Playwright not installed (normal on the server)")
class WorkIntensity(UIBase):
    def add_work(self, a="08:00", b="12:00"):
        self.fitness()
        self.page.click("#addActivity")
        self.pick_type(self.L("act.work"))
        self.set_times(a, b)

    def test_three_levels_with_their_compendium_codes(self):
        self.add_work()
        expect(self.page.locator("#sheetWorkRow")).to_be_visible()
        levels = self.page.locator("#sheetWork button")
        expect(levels).to_have_count(3)
        for i, (key, code, met) in enumerate((("still", "07040", 1.3), ("moving", "07041", 1.5), ("heavy", "11620", 3.8))):
            expect(levels.nth(i)).to_contain_text(self.L(f"work.{key}"))
            expect(levels.nth(i)).to_contain_text(self.L("work.src", code=code, met=met))
        # without a choice the Profile's default is shown, and said
        expect(self.page.locator("#sheetWork button.on")).to_contain_text(self.L("work.moving"))
        expect(self.page.locator("#sheetWorkNote")).to_contain_text(self.L("fit.work_level_note", level=self.L("work.moving")))
        # other types do not have it
        self.pick_type(self.L("act.walk"))
        expect(self.page.locator("#sheetWorkRow")).to_be_hidden()

    def test_the_choice_is_saved_with_the_activity(self):
        self.add_work()
        self.page.locator("#sheetWork button", has_text=self.L("work.heavy")).click()
        expect(self.page.locator("#sheetWorkNote")).to_have_text(self.L("work.heavy.desc"))
        self.save_btn().click()
        self.expect_sheet_closed()
        (lb,) = self.labels().values()
        self.assertEqual((lb["type"], lb["work_level"]), ("work", "heavy"))
        card = self.card(self.Lpart("fit.entered_by_you"))
        card.locator("button", has_text=self.L("common.edit")).click()
        expect(self.page.locator("#sheetWork button.on")).to_contain_text(self.L("work.heavy"))

    def test_untouched_follows_the_profile(self):
        self.add_work()
        self.save_btn().click()
        self.expect_sheet_closed()
        (lb,) = self.labels().values()
        self.assertNotIn("work_level", lb)
        # the Profile's default: three choices with their code, saved in the profile
        self.page.click("#avatar")                                          # the Profile is behind the avatar
        expect(self.page.locator("#tab-profile")).to_be_visible()
        self.page.click("#profOpen")
        opts = self.page.locator("#profWork option")
        expect(opts).to_have_count(3)
        expect(opts.nth(0)).to_contain_text(self.L("work.src", code="07040", met=1.3))
        self.page.select_option("#profWork", "still")
        expect(self.page.locator("#profWorkNote")).to_have_text(self.L("work.still.desc"))
        self.page.click("#profSave")
        expect(self.page.locator("#profSheet")).to_be_hidden()
        self.assertEqual(json.loads((self.state / "profile.json").read_text())["work_level"], "still")
        # saving the profile reloads the day: the untouched activity now uses the new default (still, 07040)
        self.fitness()
        self.page.wait_for_function(
            "() => (S.trainings || []).some(t => t.label && t.label.type === 'work' "
            "&& !t.label.work_level && t.label.work_level_used === 'still')")
        w = self.card(self.Lpart("fit.entered_by_you"))
        w.locator(".w-row").click()
        expect(w.locator(".w-method").first).to_contain_text("07040")


@unittest.skipIf(sync_playwright is None, "Playwright not installed (normal on the server)")
class OneEnergy(UIBase):
    def test_today_fitness_and_food_show_the_same_energy(self):
        total = self.page.evaluate("() => S.kcal.total")
        self.assertTrue(self.page.evaluate("() => S.kcal.partial"))
        text = self.L("kcal.at_least", v=total)
        expect(self.page.locator("#kcalTotal")).to_have_text(text)
        self.fitness()
        expect(self.page.locator("#fitKcal")).to_have_text(text)
        self.page.click('button.tabbtn[data-tab="food"]')
        expect(self.page.locator("#nutExp")).to_have_text(text)


if __name__ == "__main__":
    unittest.main()
