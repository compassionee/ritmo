"""check_apk.py: attributes that would remove Helio from recents, on manifests written by hand
in the format of `aapt2 dump xmltree`."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import check_apk  # noqa: E402

ANDROID = "http://schemas.android.com/apk/res/android"


def manifest(extra=""):
    lines = [
        "N: android=" + ANDROID,
        "  E: manifest (line=2)",
        "    E: application (line=3)",
        "      E: activity (line=4)",
        f'        A: {ANDROID}:name(0x01010003)="{check_apk.HELIO}" (Raw: "{check_apk.HELIO}")',
        f'        A: {ANDROID}:process(0x01010011)=":helio" (Raw: ":helio")',
        f'        A: {ANDROID}:taskAffinity(0x01010012)="nodomain.freeyourgadget.gadgetbridge.helio.app" (Raw: "x")',
    ]
    lines += [f"        A: {ANDROID}:{a}" for a in extra.split("|") if a]
    lines += [
        "        E: intent-filter (line=5)",
        "          E: category (line=6)",
        f'            A: {ANDROID}:name(0x01010003)="android.intent.category.LAUNCHER" (Raw: "x")',
    ]
    return "\n".join(lines)


def helio(text):
    return next(a for a in check_apk.walk(check_apk.parse_xmltree(text), "activity")
                if a[1].get("name") == check_apk.HELIO)


class Recents(unittest.TestCase):
    def test_clean_manifest(self):
        self.assertEqual(check_apk.recents_errors(helio(manifest())), [])
        self.assertEqual(check_apk.recents_errors(helio(manifest("launchMode(0x0101001d)=1"))), [])

    def test_each_flag_is_reported(self):
        for flag, rid in (("excludeFromRecents", "0x01010017"), ("noHistory", "0x0101022d"),
                          ("autoRemoveFromRecents", "0x01010433"), ("finishOnTaskLaunch", "0x01010014"),
                          ("clearTaskOnLaunch", "0x01010015")):
            for value in ("true", "0xffffffff"):
                with self.subTest(flag=flag, value=value):
                    errors = check_apk.recents_errors(helio(manifest(f"{flag}({rid})={value}")))
                    self.assertEqual(errors, [f"HelioActivity has {flag}"])
            self.assertEqual(check_apk.recents_errors(helio(manifest(f"{flag}({rid})=false"))), [])

    def test_launch_modes(self):
        for mode in ("2", "3", "4"):
            self.assertEqual(check_apk.recents_errors(helio(manifest(f"launchMode(0x0101001d)={mode}"))),
                             [f"HelioActivity has launchMode {mode}"])
        for mode in ("1", "2", "3"):
            self.assertEqual(check_apk.recents_errors(helio(manifest(f"documentLaunchMode(0x01010445)={mode}"))),
                             [f"HelioActivity has documentLaunchMode {mode}"])



def full_dump(pkg, pebble="@0x7f120001", band=None, permission=None, version_code=257, scanner=(":helio", "false"),
              auth_key=(None, "false"), uses=("android.permission.BLUETOOTH_CONNECT",)):
    """Minimal but complete badging and xmltree for check_dump."""
    band = band or f"{pkg}.helio_band_state"
    permission = permission or f"{pkg}.DYNAMIC_RECEIVER_NOT_EXPORTED_PERMISSION"
    badging = (f"package: name='{pkg}' versionCode='{version_code}' versionName='x'\n"
               f"launchable-activity: name='{check_apk.HELIO}'  label='Ritmo' icon=''\n")

    def attr(name, rid, value):
        quoted = value if value.startswith("@") else f'"{value}" (Raw: "{value}")'
        return f"        A: {ANDROID}:{name}({rid})={quoted}"

    lines = ["N: android=" + ANDROID, "  E: manifest (line=2)",
             f"    E: permission (line=3)", attr("name", "0x01010003", permission).replace("        ", "      ", 1),
             *[line for u in uses for line in ("    E: uses-permission (line=3)",
                                                attr("name", "0x01010003", u).replace("        ", "      ", 1))],
             "    E: application (line=4)",
             "      E: activity (line=5)", attr("name", "0x01010003", check_apk.HELIO),
             attr("process", "0x01010011", ":helio"),
             "        E: intent-filter (line=6)", "          E: category (line=7)",
             f'            A: {ANDROID}:name(0x01010003)="android.intent.category.LAUNCHER" (Raw: "x")',
             "      E: activity (line=8)", attr("name", "0x01010003", check_apk.CONTROL_CENTER),
             "      E: activity (line=9)", attr("name", "0x01010003", check_apk.ACTIONS),
             attr("exported", "0x01010010", "false")]
    if scanner:
        lines += ["      E: activity (line=9)", attr("name", "0x01010003", check_apk.SCANNER),
                  attr("process", "0x01010011", scanner[0]), attr("exported", "0x01010010", scanner[1])]
    if auth_key:
        lines += ["      E: activity (line=9)", attr("name", "0x01010003", check_apk.AUTH_KEY),
                  attr("exported", "0x01010010", auth_key[1])]
        if auth_key[0] is not None:
            lines.append(attr("process", "0x01010011", auth_key[0]))
        if len(auth_key) > 2:
            lines += ["        E: intent-filter (line=6)", "          E: action (line=7)",
                      f'            A: {ANDROID}:name(0x01010003)="{auth_key[2]}" (Raw: "x")']
    lines += [
             "      E: provider (line=10)", attr("name", "0x01010003", check_apk.PROVIDER),
             attr("authorities", "0x01010018", band), attr("exported", "0x01010010", "false"),
             "      E: provider (line=11)",
             attr("name", "0x01010003", "nodomain.freeyourgadget.gadgetbridge.contentprovider.PebbleContentProvider"),
             attr("authorities", "0x01010018", pebble)]
    return badging, "\n".join(lines)


class Package(unittest.TestCase):
    STRINGS = {"0x7f120001": "io.github.compassionee.ritmo.pebble_provider"}

    def test_project_package(self):
        pkg = "io.github.compassionee.ritmo"
        errors, launchers = check_apk.check_dump(*full_dump(pkg), pkg, True, 257, self.STRINGS)
        self.assertEqual((errors, launchers), ([], [check_apk.HELIO]))

    def test_personal_package_of_an_older_install(self):
        pkg = "nodomain.freeyourgadget.gadgetbridge.helio"
        strings = {"0x7f120001": pkg + ".pebble_provider"}
        self.assertEqual(check_apk.check_dump(*full_dump(pkg, version_code=258), pkg, True, 258, strings)[0], [])

    def test_other_package_than_expected(self):
        errors, _ = check_apk.check_dump(*full_dump("io.github.compassionee.ritmo"),
                                         "nodomain.freeyourgadget.gadgetbridge.helio", True, None, self.STRINGS)
        self.assertIn("package io.github.compassionee.ritmo, expected nodomain.freeyourgadget.gadgetbridge.helio", errors)

    def test_pebble_authority_must_follow_the_package(self):
        pkg = "io.github.compassionee.ritmo"
        # the old fixed string: two installs would conflict
        old = {"0x7f120001": "nodomain.freeyourgadget.gadgetbridge.helio.pebble_provider"}
        errors, _ = check_apk.check_dump(*full_dump(pkg), pkg, True, None, old)
        self.assertEqual(len([e for e in errors if "PebbleContentProvider" in e]), 1)
        # unresolvable reference
        errors, _ = check_apk.check_dump(*full_dump(pkg), pkg, True, None, {})
        self.assertTrue(any("PebbleContentProvider @0x7f120001" in e for e in errors))

    def test_band_authority_and_permission(self):
        pkg = "io.github.compassionee.ritmo"
        dump = full_dump(pkg, band="io.github.compassionee.ritmox.helio_band_state", permission="other.PERMISSION")
        errors, _ = check_apk.check_dump(*dump, pkg, True, None, self.STRINGS)
        self.assertTrue(any("HelioBandStateProvider" in e for e in errors))
        self.assertIn("permission outside the package: other.PERMISSION", errors)

    def test_version_code(self):
        pkg = "io.github.compassionee.ritmo"
        errors, _ = check_apk.check_dump(*full_dump(pkg, version_code=257), pkg, True, 258, self.STRINGS)
        self.assertEqual(errors, ["versionCode 257, expected at least 258"])

    def test_scanner(self):
        pkg = "io.github.compassionee.ritmo"
        for scanner, error in ((None, "HelioScanActivity missing"), ((":helio", "true"), "HelioScanActivity is exported"),
                               (("", "false"), "HelioScanActivity is not in the :helio process")):
            errors, _ = check_apk.check_dump(*full_dump(pkg, scanner=scanner), pkg, True, None, self.STRINGS)
            self.assertIn(error, errors)

    def test_auth_key_field(self):
        pkg = "io.github.compassionee.ritmo"
        for auth_key, error in ((None, "HelioAuthKeyActivity missing"),
                                ((None, "true"), "HelioAuthKeyActivity is exported"),
                                ((":helio", "false"), "HelioAuthKeyActivity is not in the main process"),
                                ((None, "false", "android.intent.action.SEND"), "HelioAuthKeyActivity has an intent-filter")):
            errors, _ = check_apk.check_dump(*full_dump(pkg, auth_key=auth_key), pkg, True, None, self.STRINGS)
            self.assertEqual(errors, [error], auth_key)

    def test_no_network_permission(self):
        pkg = "io.github.compassionee.ritmo"
        for perm in check_apk.NETWORK_PERMISSIONS:
            dump = full_dump(pkg, uses=("android.permission.BLUETOOTH_CONNECT", perm))
            errors, _ = check_apk.check_dump(*dump, pkg, True, None, self.STRINGS)
            self.assertEqual(errors, [f"network permission: {perm}"])
        # also when only aapt2 badging lists it
        badging, tree = full_dump(pkg)
        errors, _ = check_apk.check_dump(badging + "uses-permission: name='android.permission.INTERNET'\n", tree,
                                         pkg, True, None, self.STRINGS)
        self.assertEqual(errors, ["network permission: android.permission.INTERNET"])

    def test_invalid_package_argument(self):
        for bad in (None, "", "ritmo", "io..ritmo", "1io.ritmo", "io.ritmo;x"):
            with self.assertRaises(ValueError, msg=bad):
                check_apk.check_dump(*full_dump("io.github.compassionee.ritmo"), bad, True)

    def test_package_is_required_on_the_command_line(self):
        self.assertEqual(check_apk.main(["aapt2", "x.apk", "--release"]), 2)

    def test_string_resources(self):
        text = ("    resource 0x7f120001 string/pebble_content_provider\n"
                '      () "io.github.compassionee.ritmo.pebble_provider"\n'
                '      (it) "other"\n'
                "    resource 0x7f120002 string/other\n"
                '      () "x"\n')
        self.assertEqual(check_apk.string_resources(text),
                         {"0x7f120001": "io.github.compassionee.ritmo.pebble_provider", "0x7f120002": "x"})

if __name__ == "__main__":
    unittest.main()


def fake_apk(drop=(), lzma=True, fts5=True, app_extra=()):
    """An APK with the files check_contents looks at (zip in memory)."""
    import io
    import zipfile

    def inner(names):
        b = io.BytesIO()
        with zipfile.ZipFile(b, "w") as z:
            for n in names:
                z.writestr(n, b"x")
        return b.getvalue()

    files = {
        check_apk.CATALOG_ASSET: b"tar",
        "assets/chaquopy/bootstrap-native/arm64-v8a/zlib.cpython-313-aarch64-linux-android.so": b"so",
        "assets/chaquopy/stdlib-arm64-v8a.imy": inner(["_sqlite3.cpython-313-aarch64-linux-android.so"]),
        "lib/arm64-v8a/libsqlite3_python.so": b"..ENABLE_FTS5.." if fts5 else b"..",
        "assets/chaquopy/app.imy": inner(["helio_app.pyc", "helio_catalog.pyc", *app_extra]),
    }
    if lzma:
        files["assets/chaquopy/bootstrap-native/arm64-v8a/_lzma.cpython-313-aarch64-linux-android.so"] = b"so"
    for d in drop:
        files.pop(d)
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w") as z:
        for n, data in files.items():
            z.writestr(n, data)
    return zipfile.ZipFile(io.BytesIO(b.getvalue()))


class Contents(unittest.TestCase):
    """What the food catalog needs inside the APK."""

    def test_complete(self):
        self.assertEqual(check_apk.check_contents(fake_apk()), [])

    def test_each_missing_part(self):
        self.assertIn(f"{check_apk.CATALOG_ASSET} missing", check_apk.check_contents(fake_apk(drop=[check_apk.CATALOG_ASSET])))
        self.assertIn("Python module _lzma missing in the APK", check_apk.check_contents(fake_apk(lzma=False)))
        self.assertIn("SQLite without FTS5 (the catalog search would use LIKE)", check_apk.check_contents(fake_apk(fts5=False)))
        self.assertIn("Python module _sqlite3 missing in the APK",
                      check_apk.check_contents(fake_apk(drop=["assets/chaquopy/stdlib-arm64-v8a.imy"])))
        errors = check_apk.check_contents(fake_apk(app_extra=["tools/build_food_db.pyc"]))
        self.assertTrue(any(e.startswith("files for the computer") for e in errors))
