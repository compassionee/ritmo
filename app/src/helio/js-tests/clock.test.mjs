// Tests of the page's clock (app.js, block between clock:begin and clock:end), with Node and no browser:
//
//     node --test app/src/helio/js-tests/clock.test.mjs
//
// Local times typed by the user and chart ticks are converted with the offset of their own date (Intl, the same
// time zone database as the phone), never with a fixed one: on the day the clock goes back an hour repeats, on the
// day it goes forward one is missing.
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { test } from "node:test";
import assert from "node:assert/strict";
import vm from "node:vm";

const HERE = dirname(fileURLToPath(import.meta.url));
const APP = readFileSync(join(HERE, "../helio-app/static/app.js"), "utf8");
const BLOCK = APP.slice(APP.indexOf("// clock:begin"), APP.indexOf("// clock:end"));

function clock(tz) {
  const ctx = { Intl, Date, Math, Number, String, Set };
  vm.createContext(ctx);
  vm.runInContext(`var TZ = ${JSON.stringify(tz)};\n${BLOCK}\nthis.api = { tzOffset, isoAt, wallToTs, hourTicks };`, ctx);
  // arrays made inside the context have its own prototype: copy them for the comparisons
  return { ...ctx.api, hourTicks: (...a) => Array.from(ctx.api.hourTicks(...a)) };
}
const utc = (...a) => Date.UTC(a[0], a[1] - 1, ...a.slice(2)) / 1000;
const H = 3600;

test("Rome, 25 October 2026: 25 hours, 02:00-02:59 twice", () => {
  const c = clock("Europe/Rome");
  const day = "2026-10-25";
  assert.equal(c.wallToTs(day, 0, 0), utc(2026, 10, 24, 22));
  assert.equal(c.wallToTs("2026-10-26", 0, 0) - c.wallToTs(day, 0, 0), 25 * H);
  assert.equal(c.wallToTs(day, 2, 30), utc(2026, 10, 25, 0, 30));            // first 02:30 (+2)
  assert.equal(c.wallToTs(day, 2, 30, true), utc(2026, 10, 25, 1, 30));      // second 02:30 (+1)
  assert.equal(c.wallToTs(day, 10, 0), utc(2026, 10, 25, 9));                // +1 after the change
  assert.equal(c.wallToTs(day, 10, 0, true), utc(2026, 10, 25, 9));          // not repeated: the same
  assert.equal(c.wallToTs("2026-10-24", 10, 0), utc(2026, 10, 24, 8));       // the day before: +2
  assert.equal(c.tzOffset(utc(2026, 10, 25, 0, 59)), 2 * H);
  assert.equal(c.tzOffset(utc(2026, 10, 25, 1, 0)), H);
  assert.equal(c.isoAt(utc(2026, 10, 24, 22, 30)), day);                      // 00:30 local
  assert.equal(c.isoAt(utc(2026, 10, 25, 22, 59)), day);                      // 23:59 local
  assert.equal(c.isoAt(utc(2026, 10, 25, 23, 0)), "2026-10-26");
});

test("Rome, spring 2026 and 2027: 23 hours, 02:00-02:59 missing", () => {
  const c = clock("Europe/Rome");
  for (const [day, next, y, m, d] of [["2026-03-29", "2026-03-30", 2026, 3, 29], ["2027-03-28", "2027-03-29", 2027, 3, 28]]) {
    assert.equal(c.wallToTs(next, 0, 0) - c.wallToTs(day, 0, 0), 23 * H, day);
    assert.equal(c.wallToTs(day, 1, 59), utc(y, m, d, 0, 59), day);
    assert.equal(c.wallToTs(day, 2, 30), utc(y, m, d, 1, 30), day);          // does not exist: 03:30 (+2)
    assert.equal(c.wallToTs(day, 3, 0), utc(y, m, d, 1, 0), day);
    assert.equal(c.wallToTs(day, 10, 0), utc(y, m, d, 8), day);
  }
});

test("the history keeps its own offset: an August time stays the same in winter", () => {
  const c = clock("Europe/Rome");
  assert.equal(c.wallToTs("2026-08-10", 10, 0), utc(2026, 8, 10, 8));
  assert.equal(c.tzOffset(utc(2026, 8, 10, 8)), 2 * H);
  assert.equal(c.wallToTs("2026-12-10", 10, 0), utc(2026, 12, 10, 9));
});

test("chart ticks on whole local hours: 00 06 12 18 are 7, 6, 6 hours apart on 25 October", () => {
  const c = clock("Europe/Rome");
  const x0 = c.wallToTs("2026-10-25", 0, 0), x1 = c.wallToTs("2026-10-26", 0, 0);
  const six = c.hourTicks(x0, x1, 6).filter((t) => t < x1);
  assert.deepEqual(six.map((t) => (t - x0) / H), [0, 7, 13, 19]);
  const hours = c.hourTicks(x0, x1, 1).filter((t) => t < x1);
  assert.equal(hours.length, 25);                                              // 02:00 twice
  const spring = c.hourTicks(c.wallToTs("2027-03-28", 0, 0), c.wallToTs("2027-03-29", 0, 0), 1);
  assert.equal(spring.length - 1, 23);                                         // without the next midnight
  const normal = c.hourTicks(c.wallToTs("2026-10-26", 0, 0), c.wallToTs("2026-10-27", 0, 0), 6);
  assert.deepEqual(normal.map((t) => (t - normal[0]) / H), [0, 6, 12, 18, 24]);
});

test("other zones: New York, Sydney (southern hemisphere), Kolkata (+5:30), Lord Howe (half-hour change)", () => {
  const ny = clock("America/New_York");
  assert.equal(ny.wallToTs("2026-11-01", 1, 30), utc(2026, 11, 1, 5, 30));    // first 01:30 (-4)
  assert.equal(ny.wallToTs("2026-11-01", 1, 30, true), utc(2026, 11, 1, 6, 30));
  assert.equal(ny.wallToTs("2026-03-08", 2, 30), utc(2026, 3, 8, 7, 30));     // missing: 03:30 (-4)
  const syd = clock("Australia/Sydney");
  assert.equal(syd.wallToTs("2026-04-06", 0, 0) - syd.wallToTs("2026-04-05", 0, 0), 25 * H);
  assert.equal(syd.wallToTs("2026-10-05", 0, 0) - syd.wallToTs("2026-10-04", 0, 0), 23 * H);
  const kol = clock("Asia/Kolkata");
  assert.equal(kol.wallToTs("2026-10-25", 10, 0), utc(2026, 10, 25, 4, 30));
  const x0 = kol.wallToTs("2026-10-25", 0, 0);
  assert.deepEqual(kol.hourTicks(x0, x0 + 3 * H, 1).map((t) => (t - x0) / H), [0, 1, 2, 3]);  // whole local hours
  const lh = clock("Australia/Lord_Howe");
  assert.equal(lh.wallToTs("2026-04-06", 0, 0) - lh.wallToTs("2026-04-05", 0, 0), 24.5 * H);
  assert.equal(lh.wallToTs("2026-04-05", 1, 45, true) - lh.wallToTs("2026-04-05", 1, 45), 30 * 60);
});

test("without daylight saving the conversion is the plain offset", () => {
  const c = clock("UTC");
  assert.equal(c.wallToTs("2026-10-25", 2, 30), utc(2026, 10, 25, 2, 30));
  assert.equal(c.wallToTs("2026-10-25", 2, 30, true), utc(2026, 10, 25, 2, 30));
  assert.equal(c.tzOffset(utc(2026, 10, 25, 2, 30)), 0);
});
