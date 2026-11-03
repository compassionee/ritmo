# Security policy

## Reporting a problem

Please report security or privacy problems **privately**, not in a public issue: open the
[Security tab](https://github.com/compassionee/ritmo/security) of this repository and choose
**Report a vulnerability**. Describe what you found, how to reproduce it and which version you used.
Do not attach personal health data: use synthetic data or describe the values.

Ritmo is maintained by one person in spare time, so there is no fixed response time; reports are read
and answered as soon as possible.

## Scope

- **In scope:** Ritmo's own code (`app/src/helio/`, `app/src/testHelio/`) and the released APKs: for
  example data leaving the phone, a network access the app should not have, components reachable by
  other apps, export/import or backup archives that can be abused, signature problems.
- **Gadgetbridge:** problems in the Bluetooth, device or database code that also affect Gadgetbridge
  should be reported to [Gadgetbridge](https://codeberg.org/Freeyourgadget/Gadgetbridge) as well.

## Supported versions

Only the [latest release](https://github.com/compassionee/ritmo/releases/latest) receives fixes.

## Verifying a release

Each release lists the SHA-256 of the APK and of the signing certificate; see "Checking the download"
in the [README](README.md#-installation).
