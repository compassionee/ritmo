# Ritmo

Project: <https://github.com/compassionee/ritmo>

Ritmo is an Android app to see and understand the data of a heart rate band: sleep, resting heart
rate, HRV, SpO₂, stress, steps, workouts, calories, nutrition and weight. It works without an account
and without network: the data stays on the phone. The interface is available in English and Italian
(the system language by default, selectable in the Profile).

Foods are searched on the phone in local databases: generic foods (USDA FoodData Central and ANSES
Ciqual, shipped with the app, with Italian names) and, optionally, packaged products from Open Food Facts
(an extract of the products sold in Europe, imported from a file), also by barcode with the camera. No food or barcode is looked up
online; the only optional network use is downloading the whole extract from an address you set. Your
own foods always come first and the databases never change them.

It is a fork of [Gadgetbridge](https://codeberg.org/Freeyourgadget/Gadgetbridge), which handles the
Bluetooth connection to the band and the database. On top of it, in a separate process, Ritmo runs its
web interface and its Python code (with [Chaquopy](https://chaquo.com/chaquopy/)) inside a WebView that
cannot access the network. The same interface can also run as a small server on a home computer.

The code was born for the **Amazfit Helio Strap**, supported by Gadgetbridge, which is why some
internal names (flavor `helio`, `helio_app.py`, `HELIO_*` variables) refer to that model. Ritmo is not
affiliated with or endorsed by Amazfit, Zepp Health or the Gadgetbridge project. Product names are used
only to state compatibility.

> **Ritmo is not a medical device.** It is not meant to diagnose, treat or prevent any disease.
> Estimates and values (calories, max heart rate, VO₂max, sleep, recovery…) come from published
> formulas and wrist sensors, which can be wrong. For any health decision, talk to a doctor.

## What is inside

| Path | Content |
|---|---|
| `app/src/helio/` | the app flavor: Activity, WebView router, Python bridge, tests |
| `app/src/helio/helio-app/` | web interface, Python code and tests, the same as the server version |
| `app/src/helio/README.md` | how it is built, security, updating from Gadgetbridge |
| `docs/band-formats.md` | record formats of the band (interoperability) |
| `README-Gadgetbridge.md` | the original Gadgetbridge README |

## Building

You need JDK 21, the Android SDK (compileSdk as in Gadgetbridge), Python 3.13 on the computer (for the
tests) and Node.js 20 or later (for the JavaScript tests).

```
./gradlew assembleHelioDebug                 # debug APK, arm64-v8a only
./gradlew assembleHelioRelease               # release APK, signed if configured
```

- This repository builds only the `helio` flavor: Chaquopy needs minSdk 24 and a 64-bit ABI, so the
  other Gadgetbridge variants are disabled.
- Do not put `clean` in the same command as the build: if needed, run `./gradlew clean` on its own
  first.
- The default applicationId is `io.github.compassionee.ritmo`. Change it with
  `-PhelioApplicationId=...` or with `helio.applicationId=...` in `local.properties` (not versioned).
  The app's authorities follow it. See "Package" in `app/src/helio/README.md`.
- Release signing is configured in `local.properties`. See `app/src/helio/README.md`. Keys, passwords
  and personal `.properties` files never go into the repository: `.gitignore` excludes them.

Tests:

```
./gradlew testHelioDebugUnitTest --tests 'nodomain.freeyourgadget.gadgetbridge.helio.*' \
    --tests 'nodomain.freeyourgadget.gadgetbridge.GBApplicationHelioProcessTest'
app/src/helio/run-tests.sh app/build/outputs/apk/helio/release/app-helio-release.apk
```

`run-tests.sh` runs the Python tests of Ritmo and of the bridge, the JavaScript tests (Node) and the
manifest check with `aapt2`. The browser tests (Playwright with Chromium) run in English and in Italian when
`HELIO_UI_PYTHON` points to a Python with Playwright installed. All test data is synthetic.

## Translations

Code, comments, tests and documentation are in English. Interface texts live in
`app/src/helio/helio-app/static/i18n/` (`en.json`, `it.json`, same keys) and native Android strings in
`app/src/helio/res/values*`. `tests/test_i18n.py` checks that every key exists in every language with
the same placeholders, that no visible text is written in the code, and that no Italian text appears
outside `it.json` and `res/values-it`.

## Licences

| Component | Licence |
|---|---|
| Ritmo (code in `app/src/helio/`, `app/src/testHelio/`, `docs/`) | AGPL-3.0-or-later |
| Gadgetbridge | AGPL-3.0-or-later ([LICENSE](LICENSE)); artwork as in [LICENSE.artwork](LICENSE.artwork) (CC BY-SA 4.0 and others) |
| Chaquopy 17.1 (Gradle plugin and runtime) | MIT |
| Python 3.13 (interpreter and standard library included in the APK) | PSF License |
| OpenSSL and SQLite shipped with Python by Chaquopy | Apache-2.0 / public domain |
| `tzdata` 2026.4 (PyPI) | Apache-2.0 (IANA data in the public domain) |
| AndroidX WebKit 1.14, AndroidX CameraX (barcode scanner) | Apache-2.0 |
| ZXing core (barcode decoding, already a Gadgetbridge dependency) | Apache-2.0 |
| Food catalog `app/src/helio/helio-app/catalog/generic.tar`: USDA FoodData Central (Foundation Foods, SR Legacy) | public domain (CC0 1.0); cite "U.S. Department of Agriculture, Agricultural Research Service. FoodData Central" |
| Same catalog: ANSES Ciqual 2025 food composition table | Licence Ouverte / Etalab 2.0; cite "Anses. Ciqual French food composition table", with the version date |
| Italian names of the generic foods (`catalog/aliases-it.tsv`) | AGPL-3.0-or-later (Ritmo) |
| Open Food Facts extract of the products sold in Europe (not in this repository: built with `helio-app/tools/build_food_db.py` and imported on the phone) | ODbL 1.0 (database), DbCL 1.0 (contents): "Contains data from Open Food Facts, available under the Open Database License"; a distributed extract stays ODbL |
| Other Gadgetbridge dependencies | see `gradle/libs.versions.toml` and their licences |

The launcher icon is Gadgetbridge's (CC BY-SA 4.0). The app shows these licences and attributions in
Profile > About. The Italian food composition tables of CREA are not used: they have no licence for reuse.

Copyright © 2026 compassionee (<https://github.com/compassionee/ritmo>), for Ritmo's code. Copyright of
the Gadgetbridge contributors for the rest. This program is free software: you can redistribute it
and/or modify it under the terms of the GNU Affero General Public License, version 3 or later, as
published by the Free Software Foundation. It is distributed without any warranty. See
[LICENSE](LICENSE).
