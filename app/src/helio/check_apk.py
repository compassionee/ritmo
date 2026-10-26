"""Checks the final manifest of an APK of the helio flavor, with aapt2.

    python3 -I app/src/helio/check_apk.py <aapt2> <apk> --package <applicationId> [--release] [--min-version-code N]

- the package (applicationId) is the expected one, for example io.github.compassionee.ritmo or, for a
  personal build that updates an older install, nodomain.freeyourgadget.gadgetbridge.helio;

- a single launcher activity: Helio, in the :helio process, without trampolines;
- Helio stays in the recent apps when it goes to the background: no excludeFromRecents, noHistory,
  autoRemoveFromRecents, finishOnTaskLaunch, clearTaskOnLaunch, documentLaunchMode or launchMode
  singleTask/singleInstance;
- ControlCenterv2 without LAUNCHER;
- the actions activity and the band status provider not exported, in the main process;
- authorities (including the Pebble provider's) and permissions inside the package;
- with --release: no android:debuggable;
- the barcode scanner not exported, in the :helio process, without intent-filter;
- the auth key field not exported, in the main process (where the device preferences are), without intent-filter;
- no network: neither android.permission.INTERNET nor Gadgetbridge's Internet Helper permission (requests
  passed to that separate app);
- the food catalog can work on the phone: the shipped package is in the assets, Chaquopy ships the native
  modules it needs (_lzma, zlib, _sqlite3, with FTS5 in its SQLite), the app's Python has the catalog
  module but not the builder (tools/) nor the catalog sources.
"""
import io
import re
import subprocess
import sys
import zipfile

PACKAGE_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9_]*(\.[a-zA-Z][a-zA-Z0-9_]*)+$")
# class names (Java package, independent of the applicationId)
HELIO = "nodomain.freeyourgadget.gadgetbridge.helio.HelioActivity"
ACTIONS = "nodomain.freeyourgadget.gadgetbridge.helio.HelioGbActionActivity"
PROVIDER = "nodomain.freeyourgadget.gadgetbridge.helio.HelioBandStateProvider"
CONTROL_CENTER = "nodomain.freeyourgadget.gadgetbridge.activities.ControlCenterv2"
SCANNER = "nodomain.freeyourgadget.gadgetbridge.helio.HelioScanActivity"
AUTH_KEY = "nodomain.freeyourgadget.gadgetbridge.helio.HelioAuthKeyActivity"
# permissions that would give the app network access (directly, or through the Internet Helper app)
NETWORK_PERMISSIONS = ("android.permission.INTERNET", "nodomain.freeyourgadget.internethelper.INTERNET")
CATALOG_ASSET = "assets/helio/catalog/generic.tar"
NATIVE_MODULES = ("_lzma", "zlib", "_sqlite3")  # used by helio_catalog on the phone


def parse_xmltree(text):
    """Tree of (tag, attributes, children) from the output of aapt2 dump xmltree."""
    root = ("root", {}, [])
    stack = [(-1, root)]
    for line in text.splitlines():
        m = re.match(r"^(\s*)E: (\S+)", line)
        if m:
            depth = len(m.group(1))
            while stack[-1][0] >= depth:
                stack.pop()
            node = (m.group(2), {}, [])
            stack[-1][1][2].append(node)
            stack.append((depth, node))
            continue
        m = re.match(r'^\s*A: (?:http://schemas.android.com/apk/res/android:)?([\w:]+)(?:\(0x[0-9a-f]+\))?=(.*)$', line)
        if m and len(stack) > 1:
            value = m.group(2)
            q = re.match(r'^"(.*)" \(Raw: .*\)$', value) or re.match(r'^"(.*)"$', value)
            if q:
                value = q.group(1)
            else:
                value = re.sub(r"^\(type 0x[0-9a-f]+\)", "", value)
            stack[-1][1][1][m.group(1)] = value
    return root


def walk(node, tag):
    if node[0] == tag:
        yield node
    for child in node[2]:
        yield from walk(child, tag)


def categories(activity):
    return {c[1].get("name") for f in activity[2] if f[0] == "intent-filter" for c in f[2] if c[0] == "category"}


def is_true(value):
    return value in ("true", "0xffffffff")


# attributes that would remove Helio from recents or close it when coming back from the icon
RECENTS_FLAGS = ("excludeFromRecents", "noHistory", "autoRemoveFromRecents", "finishOnTaskLaunch",
                 "clearTaskOnLaunch")
# launchMode: 0 standard, 1 singleTop; documentLaunchMode: 0 none
ALLOWED_LAUNCH_MODES = ("0", "1", "0x0", "0x1")


def recents_errors(activity):
    attrs = activity[1]
    errors = [f"HelioActivity has {a}" for a in RECENTS_FLAGS if is_true(attrs.get(a, ""))]
    if attrs.get("launchMode", "0") not in ALLOWED_LAUNCH_MODES:
        errors.append(f"HelioActivity has launchMode {attrs['launchMode']}")
    if attrs.get("documentLaunchMode", "0") not in ("0", "0x0"):
        errors.append(f"HelioActivity has documentLaunchMode {attrs['documentLaunchMode']}")
    if attrs.get("taskAffinity", None) == "":
        errors.append("HelioActivity without taskAffinity")
    return errors


def string_resources(text):
    """{"0x7f…": value} from the strings of aapt2 dump resources (default configuration only)."""
    out, current = {}, None
    for line in text.splitlines():
        m = re.match(r"^\s*resource (0x[0-9a-f]+) string/\S+", line)
        if m:
            current = m.group(1)
            continue
        m = re.match(r'^\s*\(\) "(.*)"$', line)
        if m and current and current not in out:
            out[current] = m.group(1)
        elif not line.startswith("      "):
            current = None
    return out


def check(aapt2, apk, pkg, release, min_version_code=None):
    def dump(*args):
        return subprocess.run([aapt2, "dump", *args, apk], capture_output=True, text=True, check=True).stdout
    errors, launchers = check_dump(dump("badging"), dump("xmltree", "--file", "AndroidManifest.xml"), pkg, release,
                                   min_version_code, string_resources(dump("resources")))
    with zipfile.ZipFile(apk) as z:
        errors += check_contents(z)
    return errors, launchers


def check_contents(z):
    """The files the food catalog needs on the phone (z: the APK as a ZipFile)."""
    errors = []
    names = set(z.namelist())
    if CATALOG_ASSET not in names:
        errors.append(f"{CATALOG_ASSET} missing")
    native = set()
    for n in names:
        m = re.match(r"assets/chaquopy/bootstrap-native/[^/]+/([A-Za-z_0-9]+)\.cpython-[^/]*\.so$", n)
        if m:
            native.add(m.group(1))
    for n in (x for x in names if re.match(r"assets/chaquopy/stdlib-[^/]+\.imy$", x)):
        with zipfile.ZipFile(io.BytesIO(z.read(n))) as inner:
            native |= {m.group(1) for m in (re.match(r"([A-Za-z_0-9]+)\.cpython-[^/]*\.so$", i) for i in inner.namelist()) if m}
    for mod in NATIVE_MODULES:
        if mod not in native:
            errors.append(f"Python module {mod} missing in the APK")
    sqlite = [n for n in names if re.match(r"lib/[^/]+/libsqlite3_python\.so$", n)]
    if not sqlite:
        errors.append("libsqlite3_python.so missing")
    elif any(b"ENABLE_FTS5" not in z.read(n) for n in sqlite):
        errors.append("SQLite without FTS5 (the catalog search would use LIKE)")
    if "assets/chaquopy/app.imy" in names:
        with zipfile.ZipFile(io.BytesIO(z.read("assets/chaquopy/app.imy"))) as app:
            inner = app.namelist()
        if not any(i.startswith("helio_catalog.") for i in inner):
            errors.append("helio_catalog missing in the app's Python")
        extra = [i for i in inner if i.startswith(("tools/", "catalog/"))]
        if extra:
            errors.append(f"files for the computer in the app's Python: {extra[:3]}")
    else:
        errors.append("assets/chaquopy/app.imy missing")
    return errors


def check_dump(badging, xmltree, pkg, release, min_version_code=None, strings=None):
    """Checks on the output of aapt2 dump badging and xmltree: (errors, launcher activities).
    strings: the resource strings, for authorities written as references (@0x7f…)."""
    strings = strings or {}
    if not PACKAGE_RE.match(pkg or ""):
        raise ValueError(f"invalid package: {pkg!r}")
    errors = []
    tree = parse_xmltree(xmltree)

    package = re.search(r"package: name='([^']+)'", badging).group(1)
    version_code = int(re.search(r"versionCode='(\d+)'", badging).group(1))
    if min_version_code is not None and version_code < min_version_code:
        errors.append(f"versionCode {version_code}, expected at least {min_version_code}")
    launchers = re.findall(r"launchable-activity: name='([^']+)'", badging)
    if package != pkg:
        errors.append(f"package {package}, expected {pkg}")
    if launchers != [HELIO]:
        errors.append(f"launcher activities: {launchers}, expected only {HELIO}")

    activities = {a[1].get("name"): a for a in walk(tree, "activity")}
    launcher_by_manifest = [n for n, a in activities.items() if "android.intent.category.LAUNCHER" in categories(a)]
    if launcher_by_manifest != [HELIO]:
        errors.append(f"activities with LAUNCHER in the manifest: {launcher_by_manifest}")
    if CONTROL_CENTER not in activities:
        errors.append("ControlCenterv2 missing")
    helio = activities.get(HELIO)
    if helio is None or helio[1].get("process") != ":helio":
        errors.append("HelioActivity is not in the :helio process")
    if helio is not None:
        errors += recents_errors(helio)
    # the icon opens Helio directly: no alias or main-process activity in the launcher
    aliases = [a[1].get("name") for a in walk(tree, "activity-alias")
               if "android.intent.category.LAUNCHER" in categories(a)]
    if aliases:
        errors.append(f"activity-alias in the launcher: {aliases}")
    actions = activities.get(ACTIONS)
    if actions is None:
        errors.append("HelioGbActionActivity missing")
    else:
        if is_true(actions[1].get("exported", "")):
            errors.append("HelioGbActionActivity is exported")
        if "process" in actions[1]:
            errors.append("HelioGbActionActivity is not in the main process")
        if any(c[0] == "intent-filter" for c in actions[2]):
            errors.append("HelioGbActionActivity has an intent-filter")

    scanner = activities.get(SCANNER)
    if scanner is None:
        errors.append("HelioScanActivity missing")
    else:
        if is_true(scanner[1].get("exported", "")):
            errors.append("HelioScanActivity is exported")
        if scanner[1].get("process") != ":helio":
            errors.append("HelioScanActivity is not in the :helio process")
        if any(c[0] == "intent-filter" for c in scanner[2]):
            errors.append("HelioScanActivity has an intent-filter")

    auth_key = activities.get(AUTH_KEY)
    if auth_key is None:
        errors.append("HelioAuthKeyActivity missing")
    else:
        if is_true(auth_key[1].get("exported", "")):
            errors.append("HelioAuthKeyActivity is exported")
        if "process" in auth_key[1]:
            errors.append("HelioAuthKeyActivity is not in the main process")
        if any(c[0] == "intent-filter" for c in auth_key[2]):
            errors.append("HelioAuthKeyActivity has an intent-filter")

    requested = {u[1].get("name") for u in walk(tree, "uses-permission")}
    requested |= set(re.findall(r"uses-permission: name='([^']+)'", badging))
    for perm in NETWORK_PERMISSIONS:
        if perm in requested:
            errors.append(f"network permission: {perm}")

    providers = {p[1].get("name"): p for p in walk(tree, "provider")}
    band = providers.get(PROVIDER)
    if band is None:
        errors.append("HelioBandStateProvider missing")
    else:
        if is_true(band[1].get("exported", "")):
            errors.append("HelioBandStateProvider is exported")
        if "process" in band[1]:
            errors.append("HelioBandStateProvider is not in the main process")
    for name, p in providers.items():
        auth = p[1].get("authorities", "")
        if auth.startswith("@"):
            auth = strings.get(auth[1:], auth)
        if not auth or auth.startswith("@") or any(not a.startswith(pkg + ".") for a in auth.split(";")):
            errors.append(f"authority outside the package: {name} {auth}")
    for perm in walk(tree, "permission"):
        if not perm[1].get("name", "").startswith(pkg + "."):
            errors.append(f"permission outside the package: {perm[1].get('name')}")

    app = next(walk(tree, "application"))
    if release and is_true(app[1].get("debuggable", "")):
        errors.append("debuggable release APK")

    return errors, launchers


def main(argv):
    if len(argv) < 2:
        print(__doc__)
        return 2
    min_vc = None
    if "--min-version-code" in argv[2:]:
        min_vc = int(argv[argv.index("--min-version-code") + 1])
    if "--package" not in argv[2:] or argv.index("--package") + 1 >= len(argv):
        print("missing --package <applicationId>")
        return 2
    pkg = argv[argv.index("--package") + 1]
    errors, launchers = check(argv[0], argv[1], pkg, "--release" in argv[2:], min_vc)
    if errors:
        for e in errors:
            print("ERROR:", e)
        return 1
    print(f"OK: {pkg}, a single launcher activity ({launchers[0]}), stays in recents, actions, band status and auth key not exported, no network permission"
          + (", not debuggable" if "--release" in argv[2:] else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
