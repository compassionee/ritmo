# Flavor `helio`: Gadgetbridge with the Ritmo app

One APK (configurable package, see "Package"; default `io.github.compassionee.ritmo`) and one icon, **Ritmo**: the
same web interface and the same Python code as the server, running on the phone, with Gadgetbridge
behind it for the band. In the code the component is still called `helio` (after the band model it was
born for); the visible name of the app is Ritmo.

## Helio and Gadgetbridge together

- **A single icon.** The manifest of `src/helio` removes the LAUNCHER intent-filter from
  `ControlCenterv2` (`tools:node="remove"`); `main` does not change. The persistent notification opens
  the launcher activity, hence Helio (`GB.getContentIntent` uses `getLaunchIntentForPackage`).
- **Band chip** in Helio's header: battery, green dot (connected), orange (connecting) or grey
  (disconnected or Gadgetbridge not running), time of the last sync. Tapping it opens the menu:
  - Sync now (if the band is disconnected, Gadgetbridge connects it first);
  - Band settings (`DeviceSettingsActivity`);
  - Gadgetbridge charts (`ActivityChartsActivity`);
  - Data management (`DataManagementActivity`);
  - Gadgetbridge (`ControlCenterv2`: add devices, permissions, general settings).

  On the server the page stays as it was: chip and menu appear only with `window.HELIO_ANDROID`,
  set by the app's script.
- **Pull down to sync.** With the page still at the top (window and containers under the finger at
  scroll zero, no scrolling in the last half second), a vertical drag of 140 px sends the `pull` action
  over the same bridge as the menu. Python turns it into `sync` only if no transfer is running and the
  last one, or Helio's last request, is at least 30 s old; otherwise only the data is refreshed. The
  indicator stays until the transfer ends (3 minutes at most), then the day reloads. On the server the
  gesture does not exist.
- **Band status.** Gadgetbridge keeps it only in memory. `HelioBandStateProvider`, a provider without
  data and not exported, registers a receiver of the local `GBDevice.ACTION_DEVICE_CHANGED` broadcasts
  when the main process starts and saves state, current activity and end of the last transfer in
  `shared_prefs/helio_band_state.xml` (`HelioBandState`). Helio reads it read-only
  (`GET /api/android/band`, in `helio_android.py`). If Gadgetbridge's process is not running, the band
  shows as "Gadgetbridge not running": the header carrying this information is set by the router,
  never by the page.
- **Actions.** `POST /api/android/action` goes through the bridge: Python applies the same rules as the
  other POSTs (`X-Helio`, JSON, size) and approves only known actions; the router runs it only if it
  comes from the origin `https://appassets.androidplatform.net`, from the main frame, and was approved.
  Then Helio starts `HelioGbActionActivity` with an explicit Intent (not exported, in the main process,
  invisible), which finds the band and opens the Gadgetbridge screen or starts the sync. No port, no
  network.
- The Gadgetbridge screens opened from the menu sit on top of Helio, in the same task: Back returns to
  Helio.
- **Band auth key** (menu action `authkey`): `HelioAuthKeyActivity`, in the main process, not exported and
  without intent-filter, takes a key typed or pasted by hand (32 hex digits, with or without `0x`), shows a
  clear error for a wrong format and saves it as `0x` + lowercase hex in the band's own preferences
  (`devicesettings_<MAC>`, key `authkey`, the file Gadgetbridge reads). Without a band the MAC address is
  typed too. The key is never logged nor shown again. Nothing goes on the network: `check_apk.py` checks
  that the app asks for neither `INTERNET` nor the Internet Helper permission, which the flavor removes.

## How it is built

- **Python 3.13 with Chaquopy 17.1**, `arm64-v8a` only, `minSdk` 24. In this branch the variants of the
  other flavors are disabled, because Chaquopy applies to all of them.
- **Separate `:helio` process.** `HelioActivity` and Python run there:
  - two SQLite libraries in the same process do not exclude each other with locks;
  - Gadgetbridge skips its initialisation in that process (`GBApplication.isHelioProcess`);
  - the process starts when Helio opens and ends only with **Back from the main screen** (Back first
    closes the open sheet, then goes back to Today). Leaving with Home, with another app or by turning
    the screen off it stays idle: WebView paused, JavaScript timers suspended (`pauseTimers`), no
    background work. Helio stays in the recent apps (no `excludeFromRecents`, `noHistory`,
    `autoRemoveFromRecents`...: `check_apk.py` checks it), and the persistent notification returns to
    the open task, like the icon;
  - in this process `GBApplication` is not initialised: `onTrimMemory` and `onConfigurationChanged` do
    nothing either (`onTrimMemory(TRIM_MEMORY_BACKGROUND)` used to close Helio after Home);
  - if Android kills the process, section, day and scroll come back on reopening: the page sends them to
    Java with a `view` message on the bridge (memory only, checked values), the Activity saves them in
    `onSaveInstanceState` and passes them back in the `#view=` fragment of the initial URL, which
    `helio-android.js` reads and removes.
- **WebView without network:**
  - origin `https://appassets.androidplatform.net` and `blockNetworkLoads`;
  - every request to other origins gets an empty 403;
  - no access to files or content providers, Safe Browsing and metrics disabled;
  - remote debugging disabled, except in builds with `-PhelioWebViewDebug=true`.
- **Static files:** `helio-app/static/` becomes assets (`helio/static/`), served by
  `WebViewAssetLoader` with the same CSP as the server. `sw.js` is not included and service workers are
  blocked.
- **API:**
  - GET `/api/*` requests are intercepted;
  - POSTs go through the `helio-android.js` script and `WebMessageListener`, because the WebView does
    not expose the body of intercepted requests;
  - both reach `helio_android.handle`, which runs the **same `Handler` class** as the server on a fake
    in-memory connection, without ports. The same checks therefore apply (`X-Helio`, `Content-Type`,
    `POST_LIMITS`, errors).
- **Source links:** they open in the browser with an Intent, only on tap and only for `https`.
- **CSV export:** the location is chosen with Android's file picker.
- **Database:** Gadgetbridge's internal one, read-only, with `HELIO_DB_LIVE=1`:
  - no `immutable`, so data still in the WAL is visible too;
  - 5 s busy timeout;
  - the caches also follow the `-wal` file.
- **Private data:**
  - Helio's state is in `files/helio-state/`;
  - the profile (measurements and goals) is in `files/helio-config.json`;
  - there is no default plan: at first the page offers "Create your plan", from scratch or from the
    optional template based on the WHO 2020 guidelines (with its source); the chosen plan is in
    `files/helio-state/plan.json`;
  - the language chosen in the Profile is in `files/helio-state/settings.json` (exported and imported
    with the rest); without a choice the system language applies, with English as the fallback.
- **Languages:** English and Italian. The texts are in `helio-app/static/i18n/{en,it}.json` (same keys).
  Python sends neutral messages `{"$": key, ...params}` that the page renders with `static/i18n.js`,
  with numbers and dates formatted by `Intl`; source titles stay in their original language. The native
  dialogs (export, import) use `res/values` (English) and `res/values-it`. For a new language: a file in
  `static/i18n/` with the same keys (`tests/test_i18n.py` checks it), the entry in the selector and
  `LANGS` in `helio_app.py` and `i18n.js`. The code, comments and tests are in English; Italian text
  belongs only in `it.json` and `res/values-it` (`test_i18n.py` reports Italian text anywhere else,
  except lines marked `lang: it`).
- **Codes in the data** (activity types, meals, goals, statuses, tabs, export kinds, CSV format) are English.
  Data, backups and saved views from versions up to 259 used Italian codes: `LEGACY_CODES` in
  `helio_app.py` (and `LEGACY_TABS` in `app.js` for the saved view) converts them when read, and every state
  file is rewritten with the English codes when the app starts. `tests/test_legacy_codes.py` checks old data,
  the import of an old backup and that no old code is left outside the tables.

## Food database (community catalogs)

Search, barcodes and the food details read local SQLite files only: no food or barcode is ever looked up
online (tests block every socket while searching, and the browser tests abort any request leaving the
page's server).

- **Two read-only catalogs** in `files/helio-catalog/`, next to `helio-state/` and never inside it:
  `generic.sqlite` (USDA FoodData Central Foundation + SR Legacy, ANSES Ciqual, with the Italian names of
  `helio-app/catalog/aliases-it.tsv`), shipped in the APK as the asset `helio/catalog/generic.tar` and
  installed by Python on first use (again after an app update); `off.sqlite` (Open Food Facts, products sold
  in Europe: EU 27, Norway, Iceland, Liechtenstein, Switzerland, United Kingdom, plus those without a country
  but with a European GS1 prefix; about 2.1 million products, 158 MB package, 562 MB installed), imported by
  the user (Profile > Food database > Import from file, action `catalog_import`) or downloaded from an
  optional HTTPS address (empty by default). Each installed file has its manifest next to it
  (`<kind>.json`); a file of an older schema is not opened and the card asks to import the new package.
- **Package** (schema 2): a `.tar` with `manifest.json` (format, schema, kind, version, sources, rows, sizes
  and SHA-256 of both files) and `catalog.sqlite.xz`. The FTS5 index is built with the database on the
  computer, where the builder also runs `PRAGMA integrity_check` and the FTS5 integrity-check; on the phone
  installing is a check (sizes, both hashes, objects, columns, rows and every value) and an atomic rename,
  about a minute for the European package. Peak free space while importing: about 1.3 GB.
- **Search**: two bounded FTS5 lookups (names starting with the first word, then any match; the builder puts
  products with an Italian name and complete values first in the file), then ranking in Python on about two
  hundred candidates: 6-27 ms on the computer for 2.1 million products; Profile > Food database > Test
  speed measures it on the phone.
- **Barcodes**: stored and looked up as GTIN-14 (`gtin_key`): EAN-8, UPC-A, EAN-13 and GTIN-14 with or
  without leading zeros are the same code. Products of Open Food Facts that share a code are merged when
  they are the same product; different products with the same code are all kept, and the scanner lists them
  with name, brand and country to choose; a single product opens its details before it is added.
- **The user's data is never touched**: installing, updating or removing a catalog only writes in its own
  folder (`foods.json` stays identical byte for byte, tested); a community food enters the user's foods
  only with "Save to my foods", as a new copy that keeps its origin (each value shows its source: the
  catalog while unchanged, the user once corrected); diary entries of community foods keep a copy of the
  values and the source. The export and import of Ritmo's data never include the catalog.
- **Network**: only `helio_catalog_fetch.py` downloads, imported only by "Update from URL".
- Builder: `helio-app/tools/build_food_db.py` (USDA/Ciqual/OFF to EU label values; the Open Food Facts
  part reads the Parquet export with pyarrow, on the computer only); licences and attributions in the
  About screen.

## Folders

| Path | Content |
|---|---|
| `helio-app/` | Helio as installed on the server (code, `static/`, `tests/`) |
| `python/helio_android.py` | bridge between Java and `Handler`, export and import |
| `assets/helio/android/helio-android.js` | script injected before `app.js` |
| `java/.../helio/` | Activity, request router, static files, Python backend, band actions and status |
| `check_apk.py` | check of the final manifest with aapt2 |
| `python-tests/`, `js-tests/`, `../testHelio/` | tests of the link between WebView and Python |
| `python-requirements.txt` | `tzdata` pinned with hashes (`--require-hashes`) |

## Package (applicationId)

The flavor's applicationId is chosen with a Gradle property, in this order:

1. `-PhelioApplicationId=...` on the command line;
2. `helio.applicationId=...` in `local.properties` (not versioned);
3. otherwise `io.github.compassionee.ritmo`.

A personal build that must update an existing install keeps the old id in `local.properties`, for
example `helio.applicationId=nodomain.freeyourgadget.gadgetbridge.helio`: same package and same key, so
it updates without migrating data. Changing the id means a new app: Gadgetbridge and Ritmo data to
export and import again, band to pair again, permissions to grant again.

The authorities follow the id: `${applicationId}.helio_band_state`, `${applicationId}.screenshot_provider`
and `pebble_content_provider`, which the flavor generates as `<applicationId>.pebble_provider`.
`check_apk.py` wants the expected package (`--package`); `run-tests.sh` takes it from `HELIO_PACKAGE`,
then from `local.properties`, then uses the project's one.

## Build

```
./gradlew assembleHelioDebug       # debug key
./gradlew assembleHelioRelease     # signed with your release key, not debuggable
```

Do not put `clean` in the same command as the build: Chaquopy loses its proguard file. If needed, run
`./gradlew clean` on its own first.

Release signing reads `local.properties` (not versioned):

```
helio.signing=/path/to/your/signing.properties
```

That file (also outside the repository) contains `storeFile`, `storePassword`, `keyAlias` and
`keyPassword`. A new key can be created for example with:

```
keytool -genkeypair -keystore release.p12 -storetype PKCS12 -alias release \
    -keyalg RSA -keysize 4096 -validity 11000
```

Keep the keystore and passwords in an encrypted backup: without the same key updates cannot be
installed, the app would have to be uninstalled, losing its data. Without `helio.signing` the release
stays unsigned.

## Importing Helio data from the server

1. **Copy `helio_transfer.py` to the server**, next to `helio_app.py` (for example in
   `/opt/helio-app/`).
2. **Create the archive, as root.** The export runs as `nobody`, the same user as the service, so it
   reads the state with the same permissions and cannot write elsewhere. `/etc/helio-app.env` is
   readable only by root (600): root reads it and passes it to `nobody` through a pipe
   (`--env /dev/stdin`), without changing the file's permissions.

   ```
   # state folder and plan, with the same defaults as the service
   S=$(sed -n 's/^HELIO_STATE=//p' /etc/helio-app.env | tail -n1); S=${S:-/var/lib/helio-app}
   P=$(sed -n 's/^HELIO_PLAN=//p' /etc/helio-app.env | tail -n1); P=${P:-/etc/helio-plan.json}
   install -d -m 700 -o nobody -g nogroup /tmp/helio-export
   cat /etc/helio-app.env | runuser -u nobody -- python3 /opt/helio-app/helio_transfer.py export \
       --state "$S" --env /dev/stdin --plan "$P" -o /tmp/helio-export/helio-state.tgz
   install -m 600 /tmp/helio-export/helio-state.tgz /root/helio-state.tgz
   rm -rf /tmp/helio-export
   ```

   Only measurements and goals (`PROFILE_KEYS`) are taken from the `.env` file: never passwords, TLS
   keys, addresses or paths; the content passes only through the process memory. The plan is included
   only if the state has no plan edited in the app.
3. **Check the archive:**

   ```
   python3 /opt/helio-app/helio_transfer.py check /root/helio-state.tgz
   ```

   Then copy it to the computer (`scp root@<server>:/root/helio-state.tgz .`) and delete it from the
   server.
4. **Import it into the app.** Copy the file to the phone, then in Ritmo open the ⋮ menu and choose
   **Import Ritmo data**. The picker shows only `.tgz`/`.gz` archives and files of unknown type
   (`HelioArchive.IMPORT_MIME_TYPES`); the export uses Android's type for `.tgz`
   (`application/x-gtar-compressed`), so the name does not become `.tgz.gz`. The archive is checked
   before anything is touched:
   - expected file names;
   - valid JSON;
   - SHA-256 hashes;
   - sizes.

   The previous data stays in `helio-state.bak-<date>`, which keeps the last 3 copies. Then Ritmo
   closes: open it again.

**Export Ritmo data** creates the same format, which can be imported on the server or into another
install of the app.

The band history (Gadgetbridge's database) is separate: it moves with Gadgetbridge's own database
export and import.

## Updating Helio

```
app/src/helio/sync-helio.sh <Helio folder>    # copies into helio-app/ and runs the tests
```

## When a new Gadgetbridge comes out

1. **Rebase the branch on the new master:**

   ```
   git fetch origin master
   git rebase origin/master helio-app
   ```

   The possible conflicts are few:
   - `app/build.gradle`: `helio` flavor, Chaquopy, signing, assets;
   - `gradle/libs.versions.toml` and `build.gradle.kts`;
   - the guard in `GBApplication.onCreate`;
   - `PebbleContentProvider`.
2. **Check the AGP version.** Chaquopy 17.1 supports AGP 7.3–9.4. If Gadgetbridge moves to a newer one,
   update Chaquopy, if a compatible version exists, or keep the previous AGP in this branch.
3. **Check the database schema.** If the version number in `GBDaoGenerator` changed, verify the tables
   and columns Helio reads, with the tests on the real database (`HELIO_LIVE_DB=<copy of the database>`).
4. **Run the tests:**

   ```
   HELIO_UI_PYTHON=<python with Playwright> \
       app/src/helio/run-tests.sh app/build/outputs/apk/helio/release/app-helio-release.apk
   ./gradlew testHelioDebugUnitTest --tests 'nodomain.freeyourgadget.gadgetbridge.helio.*'
   ```

   If Gadgetbridge changed the intent-filter of `ControlCenterv2`, the removal in the `src/helio`
   manifest no longer finds it and there are two icons again: `check_apk.py` reports it.

5. **Rebuild with the same key:**

   ```
   ./gradlew assembleHelioRelease
   ```

   The flavor's `versionCode` (`helio { versionCode = ... }` in `app/build.gradle`) must be higher than
   the installed one and not lower than Gadgetbridge's: increase it at every release.
