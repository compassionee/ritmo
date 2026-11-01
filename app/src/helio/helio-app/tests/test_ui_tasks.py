"""Browser tests of the progress bar of the app's long operations (window.helioTask, sent by HelioTaskReporter): what
runs and how far, the page usable meanwhile, gone at the end; unknown names are ignored.
"""
import unittest

from test_ui import UIBase, sync_playwright

try:
    from playwright.sync_api import expect
except ImportError:
    expect = None


@unittest.skipIf(sync_playwright is None, "Playwright not installed (normal on the server)")
class TaskBarUI(UIBase):
    def send(self, event):
        self.page.evaluate("(e) => window.helioTask(e)", event)

    def test_phases_percent_and_end(self):
        bar, prog = self.page.locator("#taskBar"), self.page.locator("#taskProg")
        expect(bar).to_be_hidden()
        self.send({"task": "catalog_import", "phase": "start", "end": False, "ok": True})
        expect(bar).to_be_visible()
        expect(bar).to_contain_text(self.L("task.catalog_import"))
        self.assertIsNone(prog.get_attribute("value"))                 # no percentage yet: indeterminate
        self.send({"task": "catalog_import", "phase": "unpack", "percent": 40, "end": False, "ok": True})
        expect(bar).to_have_text(" · ".join([self.L("task.catalog_import"), self.L("task.phase.unpack"),
                                             self.L("task.percent", v=40)]))
        self.assertEqual(prog.evaluate("(p) => p.value"), 40)
        # the page stays usable: another tab opens under the bar
        self.page.click('button.tabbtn[data-tab="sleep"]')
        expect(self.page.locator("#tab-sleep")).to_be_visible()
        for width in (360, 412):
            self.page.set_viewport_size({"width": width, "height": 800})
            self.assertFalse(self.page.evaluate("document.documentElement.scrollWidth > innerWidth"), width)
        self.send({"task": "catalog_import", "phase": "check", "end": True, "ok": True})
        expect(bar).to_be_hidden()

    def test_unknown_names_are_ignored(self):
        self.send({"task": "anything", "phase": "start", "end": False})
        self.send({"task": "csv", "phase": "<b>", "end": False})
        self.send(None)
        expect(self.page.locator("#taskBar")).to_be_hidden()
        self.send({"task": "csv", "phase": "run", "end": False})
        expect(self.page.locator("#taskBar")).to_contain_text(self.L("task.csv"))


if __name__ == "__main__":
    unittest.main()
