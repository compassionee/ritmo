"""Every label of every chart stays inside its chart (dates, hours, values on the bars), in every tab, at 360 and 412 px.
The labels are measured in the browser; the drawing code only estimates their width (charts are often drawn hidden).
"""
import unittest

from test_ui import UIBase, sync_playwright

try:
    from playwright.sync_api import expect
except ImportError:
    expect = None

OUTSIDE = """() => [...document.querySelectorAll('.tab:not([hidden]) svg[viewBox]')].filter((s) => s.getBoundingClientRect().width > 0)
  .flatMap((s) => { const w = s.viewBox.baseVal.width;
    return [...s.querySelectorAll('text')].filter((t) => t.textContent.trim()).map((t) => {
      const b = t.getBBox(); return { text: t.textContent, x: b.x, right: b.x + b.width, w, chart: s.parentElement.id }; })
      .filter((b) => b.x < -0.5 || b.right > b.w + 0.5); })"""


@unittest.skipIf(sync_playwright is None, "Playwright not installed (normal on the server)")
class ChartLabels(UIBase):
    def test_labels_stay_inside_the_charts(self):
        for width in (360, 412):
            self.page.set_viewport_size({"width": width, "height": 900})
            for tab in ("today", "fitness", "sleep", "health", "food"):
                self.page.click(f'button.tabbtn[data-tab="{tab}"]')
                expect(self.page.locator(f"#tab-{tab}")).to_be_visible()
                checked = self.page.evaluate("() => document.querySelectorAll('.tab:not([hidden]) svg[viewBox] text').length")
                if tab != "food":  # the test database has no meals: Food is checked with data in test_ui_balance
                    self.assertGreater(checked, 0, tab)
                self.assertEqual(self.page.evaluate(OUTSIDE), [], (tab, width))

    def test_load_chart_last_date_is_whole(self):
        self.page.click('button.tabbtn[data-tab="fitness"]')
        last = self.page.evaluate("() => [...document.querySelectorAll('#loadChart svg text')].map((t) => t.textContent).filter(Boolean).pop()")
        self.assertRegex(last, r"^\d{1,2}[/.]\d{1,2}$")                               # "10/03", never "10/0"


if __name__ == "__main__":
    unittest.main()
