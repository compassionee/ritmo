"""Browser tests of the vital signs card in Health: every chip (resting heart rate, HRV, breathing, SpO2,
temperature) changes the name, the value, the unit and the chart, also when one value is out of its usual range
and the card chooses it by itself (the chips used to snap back to it). Like test_ui.py: Chromium with
Playwright, skipped on the server."""
import json
import unittest

from test_helio import DAY
from test_ui import UIBase, sync_playwright

try:
    from playwright.sync_api import expect
except ImportError:
    expect = None

ORDER = ("rhr", "hrv", "resp", "spo2", "temp")


@unittest.skipIf(sync_playwright is None, "Playwright not installed (normal on the server)")
class VitalSigns(UIBase):
    def open_health(self, out_of_range=None):
        """out_of_range: the key shown above its usual range (as on a phone with weeks of data)."""
        self.vitals = self.h.vitals(DAY)
        if out_of_range:
            for k, v in self.vitals.items():  # a usual range around each value, as with weeks of data
                x = v["last"][1]
                v.update(mean=x, low=round(x * 0.9, 1), high=round(x * 1.1, 1), status="normal")
            self.vitals[out_of_range]["status"] = "above"
            body = json.dumps(self.vitals)
            self.page.route("**/api/vitals*", lambda route: route.fulfill(status=200, content_type="application/json",
                                                                      body=body))
            self.page.reload()
            self.wait_catalog()
            expect(self.page.locator("#workoutList .card").first).to_be_attached()
        self.page.click('button.tabbtn[data-tab="health"]')
        expect(self.page.locator("#tab-health")).to_be_visible()

    def expected(self, k):
        """Name, value and unit of a vital sign as the page writes them (texts and formats of the page)."""
        v = self.vitals[k]
        name = self.page.evaluate("(m) => tx(m)", v["title"])
        value = self.page.evaluate("([x, d]) => fmtDec(x, d)", [v["last"][1], v["decimals"]])
        unit = self.page.evaluate("(m) => tx(m)", v["unit"])
        return name, value, unit

    def chart(self):
        """The drawn line and the value labels of the axis (each chart is scaled on its own values: the synthetic
        series have the same shape, the labels tell them apart)."""
        return self.page.locator("#vitChart path, #vitChart circle, #vitChart text").evaluate_all(
            "(els) => els.map((e) => e.getAttribute('d') || e.getAttribute('cy') || e.textContent).join(';')")

    def check_every_chip(self):
        charts = set()
        for i, k in enumerate(ORDER):
            chip = self.page.locator("#vitChips button").nth(i)
            expect(chip).to_have_text(self.L(f"vit.short.{k}"))
            chip.click()
            expect(chip).to_have_class("on")
            expect(self.page.locator("#vitChips button.on")).to_have_count(1)
            name, value, unit = self.expected(k)
            expect(self.page.locator("#vitName")).to_have_text(name)
            expect(self.page.locator("#vitValue")).to_have_text(value)
            expect(self.page.locator("#vitUnit")).to_contain_text(unit)
            charts.add(self.chart())
        self.assertEqual(len(charts), len(ORDER), "every vital sign draws its own chart")

    def test_every_chip_changes_the_card(self):
        self.open_health()
        self.check_every_chip()

    def test_chips_work_when_a_value_is_out_of_range(self):
        self.open_health(out_of_range="rhr")
        # the card first shows the value out of range...
        expect(self.page.locator("#vitChips button.on")).to_have_text(self.L("vit.short.rhr"))
        expect(self.page.locator("#vitTitle")).to_contain_text(self.L("vit.short.rhr"))
        # ...and every chip still changes it
        self.check_every_chip()
        # the choice stays while the page redraws (language change, refresh)
        self.page.locator("#vitChips button").nth(3).click()
        self.page.evaluate("renderAll()")
        expect(self.page.locator("#vitChips button.on")).to_have_text(self.L("vit.short.spo2"))

    def test_other_day_goes_back_to_the_value_out_of_range(self):
        self.open_health(out_of_range="hrv")
        self.page.locator("#vitChips button").nth(4).click()
        self.page.click("#prev")
        expect(self.page.locator("#vitChips button.on")).to_have_text(self.L("vit.short.hrv"))


if __name__ == "__main__":
    unittest.main()
