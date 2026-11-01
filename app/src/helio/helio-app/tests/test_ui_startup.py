"""Browser tests of Profile > About > Startup times: the page's steps of this opening, the Java and Python sides on the
same line (in the app), a copy as plain text; only steps and API paths, never dates or other data.
"""
import re
import unittest

from test_ui import UIBase, sync_playwright

try:
    from playwright.sync_api import expect
except ImportError:
    expect = None


@unittest.skipIf(sync_playwright is None, "Playwright not installed (normal on the server)")
class StartupTimesUI(UIBase):
    def open_times(self):
        self.page.click("#avatar")
        self.page.click("#aboutOpen")
        expect(self.page.locator("#aboutSheet")).to_be_visible()
        box = self.page.locator("#bootBox")
        self.assertFalse(box.evaluate("(x) => x.open"))                               # closed until asked
        box.locator("> summary").click()
        return self.page.locator("#bootList li")

    def test_page_steps_from_the_opening(self):
        rows = self.open_times()
        expect(rows.last).to_contain_text(re.compile(r"\d"))
        text = self.page.locator("#bootList").inner_text()
        for key in ("page.settings", "page.data", "page.render", "page.paint"):
            self.assertIn(self.L("boot.k." + key.replace(".", "_")), text, key)
        # in order from the opening of the page (the tests fix the page's clock, so not from zero here)
        ats = [int(re.sub(r"\D", "", x)) for x in self.page.locator("#bootList .boot-at").all_inner_texts()]
        self.assertEqual(ats, sorted(ats))
        expect(rows.last).to_have_class("boot-total")
        self.assertNotRegex(text, r"20\d\d-\d\d")                                     # no dates
        for width in (360, 412):
            self.page.set_viewport_size({"width": width, "height": 800})
            self.assertFalse(self.page.evaluate("document.documentElement.scrollWidth > innerWidth"), width)

    def test_java_and_python_sides_on_one_line(self):
        """As in the app: Java's process start is zero, Python's seconds become milliseconds, each API row has the
        WebView's time with Python's share, and Python's steps go between Java's."""
        out = self.page.evaluate("""() => {
            const z = performance.timeOrigin - 3000;
            const java = { marks: [{ key: "android.process", start: z, end: z + 400 },
                                   { key: "android.python", start: z + 900, end: z + 1700 }],
                           api: [{ path: "/api/summary", start: z + 1800, end: z + 2600 }] };
            const py = { marks: [{ key: "py.import", start: (z + 1750) / 1000, end: (z + 1790) / 1000 }],
                         api: [{ path: "/api/summary", start: (z + 2000) / 1000, end: (z + 2500) / 1000 },
                               { path: "/api/plan", start: (z + 2550) / 1000, end: (z + 2560) / 1000 }] };
            const { zero, rows } = bootRows(java, py);
            return { zero: zero - z, rows: rows.filter((r) => !r.key || !r.key.startsWith("page."))
                .map((r) => [r.key || r.path, Math.round(r.start - z), Math.round(r.end - r.start), r.py && Math.round(r.py)]) };
        }""")
        self.assertEqual(out["zero"], 0)
        self.assertEqual(out["rows"], [["android.process", 0, 400, None], ["android.python", 900, 800, None],
                                       ["py.import", 1750, 40, None], ["/api/summary", 1800, 800, 500],
                                       ["/api/plan", 2550, 10, None]])

    def test_copy_as_text(self):
        self.page.evaluate("""() => { window.__copied = null;
            Object.defineProperty(navigator, "clipboard", { configurable: true,
              value: { writeText: (s) => { window.__copied = s; return Promise.resolve(); } } }); }""")
        self.open_times()
        expect(self.page.locator("#bootList li").last).to_be_visible()
        self.page.click("#bootCopy")
        expect(self.page.locator("#toast")).to_have_text(self.L("boot.copied"))
        copied = self.page.evaluate("window.__copied")
        lines = copied.split("\n")
        self.assertEqual(lines[0], self.L("boot.title"))
        self.assertTrue(any(self.L("boot.k.page_data") in x for x in lines))
        self.assertEqual(lines[-1], self.page.locator("#bootList li.boot-total").inner_text())
        self.assertNotRegex(copied, r"20\d\d-\d\d")


if __name__ == "__main__":
    unittest.main()
