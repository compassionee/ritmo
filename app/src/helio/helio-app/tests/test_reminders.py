"""The reminders of the Android app as Python saves them in settings.json (HelioReminders reads them in Java when an
alarm goes off): off by default, every field checked, only the fields sent change, exported with the data, small
enough for the /api/settings limit. The diary and weight files the conditions are read from keep the format Java
expects (HelioRemindersTest uses the same examples).
"""
import json
import threading
import unittest
import urllib.error
import urllib.request

from test_helio import DAY
from test_nutrition import StoreBase

ON = {"day": {"on": True, "time": "22:15"}, "weigh": {"on": True, "time": "06:45", "days": [1, 3, 5]}}


class Reminders(StoreBase):
    def test_off_by_default(self):
        r = self.h.settings_load()["reminders"]
        self.assertEqual(r, {"day": {"on": False, "time": "21:30"},
                             "weigh": {"on": False, "time": "07:30", "days": [1, 2, 3, 4, 5, 6, 7]}})
        self.assertFalse(any(rule["on"] for rule in r.values()))

    def test_saved_alone_and_exported(self):
        self.h.save_settings({"lang": "it"})
        res = self.h.save_settings({"reminders": ON})
        self.assertEqual(res["reminders"], ON)
        self.assertEqual(res["lang"], "it")                                  # the other fields stay
        self.h.save_settings({"save_mine": True})
        self.assertEqual(self.h.settings_load()["reminders"], ON)            # and the reminders stay too
        saved = json.loads((self.state / "settings.json").read_text())
        self.assertEqual(saved["reminders"], ON)
        import helio_transfer as ht
        self.assertEqual(json.loads(ht.state_files(self.state)["settings.json"])["reminders"], ON)

    def test_invalid_values_are_refused(self):
        self.h.save_settings({"reminders": ON})
        day, weigh = ON["day"], ON["weigh"]
        bad = [None, [], {}, {"day": day}, {**ON, "other": day},
               {**ON, "day": {**day, "on": 1}}, {**ON, "day": {**day, "time": "24:00"}},
               {**ON, "day": {**day, "time": "7:30"}}, {**ON, "day": {**day, "time": "07:60"}},
               {**ON, "day": {**day, "days": [1]}}, {**ON, "weigh": {"on": True, "time": "06:45"}},
               {**ON, "weigh": {**weigh, "days": [0]}}, {**ON, "weigh": {**weigh, "days": [8]}},
               {**ON, "weigh": {**weigh, "days": [1, 1]}}, {**ON, "weigh": {**weigh, "days": [True]}},
               {**ON, "weigh": {**weigh, "days": "1"}}, {**ON, "weigh": {**weigh, "days": [1.0]}}]
        for b in bad:
            with self.assertRaises(self.h.UserError, msg=b):
                self.h.save_settings({"reminders": b})
        # weigh-in on with no day: a clear message
        with self.assertRaises(self.h.UserError) as cm:
            self.h.save_settings({"reminders": {**ON, "weigh": {**weigh, "days": []}}})
        self.assertEqual(cm.exception.msg["$"], "e.reminder_days")
        # off with no day is fine
        self.h.save_settings({"reminders": {**ON, "weigh": {**weigh, "on": False, "days": []}}})
        self.assertEqual(self.h.settings_load()["reminders"]["weigh"]["days"], [])
        self.assertEqual(self.h.settings_load()["reminders"]["day"], day)

    def test_damaged_file_means_off(self):
        self.state.mkdir(parents=True, exist_ok=True)
        (self.state / "settings.json").write_text(json.dumps({"reminders": {
            "day": {"on": "yes", "time": "25:00"}, "weigh": {"on": True, "time": "x", "days": [0, 2, "3", 9, 2]}}}))
        r = self.h.settings_load()["reminders"]
        self.assertEqual(r["day"], {"on": False, "time": "21:30"})
        self.assertEqual(r["weigh"], {"on": True, "time": "07:30", "days": [2]})

    def test_condition_files_keep_the_format_java_reads(self):
        """diary-YYYY-MM.json {"YYYY-MM-DD": {"complete": true}} and weights.json {"YYYY-MM-DD": kg}."""
        d = DAY.isoformat()
        self.h.diary_op({"op": "complete", "date": d, "complete": True})
        month = json.loads((self.state / f"diary-{d[:7]}.json").read_text())
        self.assertIs(month[d]["complete"], True)
        self.h.save_weight(DAY, 72.4)
        self.assertEqual(json.loads((self.state / "weights.json").read_text())[d], 72.4)


class RemindersApi(StoreBase):
    def setUp(self):
        super().setUp()
        self.srv = self.h.ThreadingHTTPServer(("127.0.0.1", 0), self.h.Handler)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.srv.server_address[1]}"

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()

    def post(self, body):
        r = urllib.request.Request(self.base + "/api/settings", data=json.dumps(body).encode(), method="POST",
                                   headers={"Content-Type": "application/json", "X-Helio": "1"})
        try:
            with urllib.request.urlopen(r, timeout=10) as resp:
                return resp.status, json.loads(resp.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"{}")

    def test_the_largest_body_fits(self):
        full = {"day": {"on": True, "time": "23:59"},
                "weigh": {"on": True, "time": "23:59", "days": [1, 2, 3, 4, 5, 6, 7]}}
        # the page sends JSON.stringify: no spaces
        self.assertLess(len(json.dumps({"reminders": full}, separators=(",", ":"))), self.h.Handler.POST_LIMITS["/api/settings"])
        code, res = self.post({"reminders": full})
        self.assertEqual(code, 200)
        self.assertEqual(res["reminders"], full)
        code, res = self.post({"reminders": {"day": {"on": True}}})
        self.assertEqual(code, 400)


if __name__ == "__main__":
    unittest.main()
