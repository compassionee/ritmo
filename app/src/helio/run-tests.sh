#!/bin/bash
# Helio tests in the app: Helio's code, the Python bridge, the WebView scripts and, given a built
# APK, its manifest (a single icon, actions and band status not exported).
#   app/src/helio/run-tests.sh [app-helio-release.apk]
# With an APK: HELIO_PACKAGE=<applicationId> for a package other than the one in local.properties or the project's;
# HELIO_MIN_VERSION_CODE=<n> to check the versionCode too.
# HELIO_LOG_DIR=<folder>: keeps the full logs there (only the summary and the failures on screen).
# HELIO_UI_PYTHON=<python with Playwright>: also runs the browser tests, in English and in Italian.
# The Java tests (Robolectric) run with Gradle:
#   ./gradlew testHelioDebugUnitTest --tests 'nodomain.freeyourgadget.gadgetbridge.helio.*'
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
# absolute path of the APK: the tests run in a temporary folder
APK="${1:+$(realpath "$1")}"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
cd "$WORK"
export PYTHONDONTWRITEBYTECODE=1
status=0
LOGDIR="${HELIO_LOG_DIR:-$WORK}"
mkdir -p "$LOGDIR"
STAMP="$(date +%Y%m%d-%H%M%S)"
n=0

run() {
  local name="$1"; shift
  n=$((n+1))
  local log="$LOGDIR/tests-$STAMP-$n.log"
  "$@" > "$log" 2>&1
  local rc=$?
  # one summary line; on failure, only the failed tests and their error
  local sum
  sum="$(grep -E "^(Ran [0-9]+ test|OK|FAILED)|^ℹ (tests|pass|fail) " "$log" | tr '\n' ' ')"
  echo "$([ $rc -eq 0 ] && echo ok || echo KO)  $name: ${sum:-rc=$rc}"
  if [ $rc -ne 0 ]; then
    status=1
    grep -E "^(FAIL|ERROR): |^[A-Za-z]*Error: |^not ok |^✖ " "$log" | grep -v "failing tests:" | awk '!seen[$0]++' | head -n 12
    [ -n "${HELIO_LOG_DIR:-}" ] && echo "    log: $log" || tail -n 15 "$log"
  fi
}

run "Helio tests (helio-app/tests)" python3 -m unittest discover -s "$HERE/helio-app/tests"
run "Python bridge (python-tests)" python3 -m unittest discover -s "$HERE/python-tests"
run "WebView scripts (js-tests)" node --test "$HERE/js-tests/helio-android.test.mjs" "$HERE/js-tests/pull-sync.test.mjs" "$HERE/js-tests/view-state.test.mjs" "$HERE/js-tests/i18n.test.mjs" "$HERE/js-tests/clock.test.mjs"
if [ -n "${HELIO_UI_PYTHON:-}" ]; then
  for lang in en it; do
    run "Browser tests ($lang)" env HELIO_UI_LANG=$lang "$HELIO_UI_PYTHON" -m unittest discover -s "$HERE/helio-app/tests" -p "test_ui*.py"
  done
fi
if [ -n "$APK" ]; then
  AAPT2="$(ls -d "${ANDROID_HOME:-$HOME/android-sdk}"/build-tools/*/aapt2 | sort -V | tail -n1)"
  # expected applicationId: HELIO_PACKAGE, then helio.applicationId in local.properties, then the project's
  PKG="${HELIO_PACKAGE:-}"
  LOCAL="$HERE/../../../local.properties"
  if [ -z "$PKG" ] && [ -f "$LOCAL" ]; then
    PKG="$(sed -n 's/^helio\.applicationId=//p' "$LOCAL" | tail -n1)"
  fi
  PKG="${PKG:-io.github.compassionee.ritmo}"
  case "$APK" in *release*) rel=--release ;; *) rel= ;; esac
  run "APK manifest" python3 -I "$HERE/check_apk.py" "$AAPT2" "$APK" --package "$PKG" $rel ${HELIO_MIN_VERSION_CODE:+--min-version-code $HELIO_MIN_VERSION_CODE}
fi
exit $status
