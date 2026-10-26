"""Browser test of every button: in each section (Today, Fitness, Sleep, Food, Health, Profile) every visible
button and chip is tapped once; the test fails on a JavaScript error or when a tap has no effect at all (no
change on the page, no request, no download, no toast). Selected chips (class "on") are skipped: tapping
them again is meant to do nothing. Like test_ui.py: Chromium with Playwright, skipped on the server."""
import json
import unittest

from test_ui import UIBase, sync_playwright
import test_ui_food  # helpers only: importing the class here would run its tests twice

try:
    from playwright.sync_api import expect
except ImportError:
    expect = None

OATS = {"kcal": 372, "protein": 13.5, "carbs": 58.7, "fat": 7, "fiber": 10}

# clickable elements of a section: buttons, links styled as buttons, expanders and anything with the hand
# cursor (cards that open a sheet); not the external links (they would leave the phone) nor the elements
# inside another clickable one. Each gets a key that survives a redraw.
CLICKABLES = """(root) => {
  const box = document.querySelector(root);
  const isClick = (e) => e.matches('button, a.w-btn, summary, [role=button]') || getComputedStyle(e).cursor === 'pointer';
  const all = [...box.querySelectorAll('*')].filter((e) => {
    if (!isClick(e) || e.matches('a[target=_blank]') || e.closest('a[target=_blank]')) return false;
    if (e.parentElement && e.parentElement.closest && [...box.querySelectorAll('*')].includes(e.parentElement)
        && isClick(e.parentElement)) return false;
    const r = e.getBoundingClientRect(), s = getComputedStyle(e);
    return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && !e.disabled && !e.closest('[hidden]')
      && !e.classList.contains('on') && !e.classList.contains('active');
  });
  const seen = {};
  return all.map((e) => {
    const text = (e.textContent || '').trim().replace(/\\s+/g, ' ').slice(0, 60);
    const base = (e.id ? '#' + e.id : text + '|' + e.tagName + '|' + (e.className || ''));
    seen[base] = (seen[base] || 0) + 1;
    return { el: e, key: base, n: seen[base], label: e.id || text || e.getAttribute('aria-label') || e.className };
  });
}"""
FIND = "([root]) => (" + CLICKABLES + ")(root).map(({key, n, label}) => ({key, n, label}))"
MARK = """([root, key, n]) => {
  document.querySelectorAll('[data-click-me]').forEach((e) => e.removeAttribute('data-click-me'));
  const hit = (""" + CLICKABLES + """)(root).find((x) => x.key === key && x.n === n);
  if (hit) hit.el.setAttribute('data-click-me', '1');
  return !!hit;
}"""

# what a tap can change, as the user sees it: the text on screen, the selected chips, open sheets and
# expanders, hidden parts, the toast, the scroll (the charts are redrawn as new SVG at every render: their
# markup is not compared, their labels are part of the text)
SNAPSHOT = """() => {
  const main = document.querySelector('main');
  // which elements are selected, hidden, open or checked (by position: a selection that moves is a change)
  const state = [...document.querySelectorAll('main *, .sheet:not([hidden]) *')].map((e, i) => {
    const m = (e.classList.contains('on') || e.classList.contains('active') ? 'O' : '') + (e.hidden ? 'H' : '')
      + (e.open ? 'D' : '') + (e.checked ? 'C' : '');
    return m ? i + m : '';
  }).filter(Boolean).join(',');
  const sheets = [...document.querySelectorAll('.sheet')].filter((s) => !s.hidden);
  const toast = document.getElementById('toast');
  return [main.innerText, state, sheets.map((s) => s.id + s.innerText).join('|'),
          toast ? toast.textContent + toast.className : '', String(Math.round(window.scrollY)),
          document.getElementById('title').textContent].join('\\u0001');
}"""

SECTIONS = ("today", "fitness", "sleep", "food", "health", "profile")


@unittest.skipIf(sync_playwright is None, "Playwright not installed (normal on the server)")
class EveryButton(UIBase):
    api = test_ui_food.FoodUI.api
    food = test_ui_food.FoodUI.food

    def open_section(self, name):
        if name == "profile":
            if not self.page.locator("#tab-profile").is_visible():
                self.page.click("#avatar")
        else:
            self.page.click(f'button.tabbtn[data-tab="{name}"]')
        expect(self.page.locator(f"#tab-{name}")).to_be_visible()

    def settle(self):
        """No request in flight for a while (Playwright's "networkidle" covers only the page load, not the
        later fetches): a reload started by an earlier tap must not redraw the page during a check."""
        quiet = 0
        for _ in range(200):
            self.page.wait_for_timeout(25)
            quiet = quiet + 1 if not self.inflight else 0
            if quiet >= 6:
                return

    def close_sheets(self):
        for _ in range(4):
            open_ = [s for s in self.page.locator(".sheet:not([hidden])").all()]
            if not open_:
                return
            x = open_[-1].locator(".sheet-x")
            if x.count():
                x.first.click()
            else:
                self.page.evaluate("(id) => closeModal(id)", open_[-1].get_attribute("id"))
            self.page.wait_for_timeout(100)

    def tap_sheet(self, section, sheet, opener_root, opener):
        dead = []
        for inner in self.page.evaluate(FIND, [f"#{sheet}"]):
            self.close_sheets()
            self.open_section(section)
            if not self.page.evaluate(MARK, [opener_root, opener["key"], opener["n"]]):
                break
            self.page.locator("[data-click-me]").click(timeout=3000)
            self.page.wait_for_timeout(150)
            if self.page.locator(f"#{sheet}").is_hidden():
                break
            self.settle()
            if not self.page.evaluate(MARK, [f"#{sheet}", inner["key"], inner["n"]]):
                continue
            target = self.page.locator("[data-click-me]")
            downloads = []
            self.page.once("download", lambda d: downloads.append(d))
            try:
                target.evaluate("e => e.scrollIntoView({block: 'center'})")
                self.requests.clear()
                before = self.page.evaluate(SNAPSHOT)
                target.click(timeout=3000)
            except Exception as e:
                dead.append(f"{section} > {sheet}: {inner['label']} (cannot be tapped: {str(e).splitlines()[0][:80]})")
                continue
            self.page.wait_for_timeout(250)
            self.settle()
            self.tapped[sheet] = self.tapped.get(sheet, 0) + 1
            if self.page.evaluate(SNAPSHOT) == before and not self.requests and not downloads:
                dead.append(f"{section} > {sheet}: {inner['label']}")
        self.close_sheets()
        return dead

    def tap_everything(self, section, root=None, depth=0):
        """Taps every element once; returns the labels of the taps without any effect."""
        root = root or f"#tab-{section}"
        self.open_section(section)
        dead, done = [], set()
        for item in self.page.evaluate(FIND, [root]):
            ident = (item["key"], item["n"])
            if ident in done:
                continue
            done.add(ident)
            self.close_sheets()
            self.open_section(section)
            self.settle()  # the effects of the previous tap are over before this one is marked and checked
            if not self.page.evaluate(MARK, [root, item["key"], item["n"]]):
                continue  # gone after an earlier tap (list redrawn): not this test's business
            target = self.page.locator("[data-click-me]")
            if not target.is_visible():
                continue
            downloads = []
            self.page.once("download", lambda d: downloads.append(d))
            try:
                target.evaluate("e => e.scrollIntoView({block: 'center'})")  # not under the fixed tab bar
                self.requests.clear()
                before = self.page.evaluate(SNAPSHOT)
                target.click(timeout=3000)
            except Exception as e:  # visible but covered or moving: a tap the user cannot make
                dead.append(f"{section}: {item['label']} (cannot be tapped: {str(e).splitlines()[0][:80]})")
                continue
            self.page.wait_for_timeout(250)
            self.settle()
            after = self.page.evaluate(SNAPSHOT)
            self.tapped[section] = self.tapped.get(section, 0) + 1
            if before == after and not self.requests and not downloads:
                dead.append(f"{section}: {item['label']}")
            opened = [x.get_attribute("id") for x in self.page.locator(".sheet:not([hidden])").all()]
            if opened and depth == 0 and opened[-1] not in self.sheets_done:
                # a sheet opened: tap each of its buttons too, reopening it with the same tap each time
                self.sheets_done.add(opened[-1])
                dead += self.tap_sheet(section, opened[-1], root, item)
        self.close_sheets()
        return dead

    def setUp(self):
        super().setUp()
        self.tapped = {}
        self.sheets_done = set()
        self.requests = []
        # values out of their usual range (as on a phone with a few weeks of data): the vital signs card
        # chooses the one out of range by itself, and its chips must still change the chart
        self.page.route("**/api/vitals*", self.vitals_out_of_range)
        self.inflight = set()
        self.page.on("request", lambda r: (self.requests.append(r.url), self.inflight.add(r)))
        self.page.on("requestfinished", lambda r: self.inflight.discard(r))
        self.page.on("requestfailed", lambda r: self.inflight.discard(r))
        # a food, a saved meal and a diary entry: the Food section shows its lists and buttons
        fid = self.food("Oats", OATS, portions=[["1 cup", 40]])
        self.api("/api/meal", {"meal": {"name": "Breakfast", "items": [[fid, 40]]}})
        self.page.reload()
        self.wait_catalog()
        expect(self.page.locator("#workoutList .card").first).to_be_attached()  # the day's data is on the page

    @staticmethod
    def vitals_out_of_range(route):
        r = route.fetch()
        d = r.json()
        for k in d:
            d[k].update(mean=50.0, low=45.0, high=55.0, status="normal")
        d["rhr"]["status"] = "above"
        route.fulfill(response=r, body=json.dumps(d))

    def test_every_button_does_something(self):
        dead = []
        for section in SECTIONS:
            dead += self.tap_everything(section)
        dead += self.tap_everything("today", "header")  # day arrows, band chip, avatar
        for section in SECTIONS:
            self.assertGreater(self.tapped.get(section, 0), 0, section)
        self.assertEqual(dead, [], "taps without any effect")
        self.assertEqual(self.errors, [], "JavaScript errors")


if __name__ == "__main__":
    unittest.main()
