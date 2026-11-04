# What the Zepp app prepares to send, and why Ritmo exists

This note summarises a **static** privacy review of the official companion app of the Amazfit Helio
Strap (Zepp 10.9.0, Google Play build, base APK only), done in October 2026 for interoperability and
privacy purposes. It explains the design choices of Ritmo: no account, no cloud, no network.

> **Limits.** The app was never run and no network traffic was observed. The presence of code shows
> what the app is *able* to prepare and send, not that every transfer happens: consent screens, region
> and settings can change what is actually sent. About 79% of the classes are obfuscated, so searches
> by name are incomplete. Native libraries and bundled web code were not analysed. Nothing here is
> legal advice or a statement about how Zepp Health handles data on its servers.

## Health data prepared for the cloud
The app contains upload code for most health data the band records, each sent as an event with a type
and a value: heart rate (per minute and per second), HRV, SpO₂ (including overnight dips and apnoea
events), stress, a readiness score that includes sleep-apnoea and atrial-fibrillation risk fields,
breathing rate, sleep, daily activity, and menstrual-cycle tracking with symptoms, mood states and
free-text notes.

## Identifiers attached to the data
Health records are not anonymous: almost every payload carries the account user ID, the serial number
of the device, its model and source, the time zone, and in some cases a push identifier of the phone.
The app also contains code that asks a public service for the phone's public IP address.

## Tracking and analytics
- The app's own analytics module is the most widespread tracker: it is called from hundreds of classes
  and sends to the vendor's analytics endpoints, with strings referring to a tracker linked to the
  user's identity.
- Firebase Analytics collection is disabled by default in the manifest; Firebase Performance is enabled
  after login or consent.
- Google advertising ID, Facebook app events (login and sharing), and SDKs for payments, messaging
  apps, voice assistants, maps and OCR are present. No SDK that shows ads was found.

## Permissions
About 100 permissions are declared; most sensitive ones are used in code: precise and background
location, contacts, call log and SMS (caller names and quick replies), phone state, the list of
installed apps, the advertising ID, and Health Connect (27 write and 2 read permissions).

## What Ritmo does instead
- The Ritmo APK has **no network permission**: it cannot send anything, and a test checks this on every
  release.
- Data from the band goes over Bluetooth to Gadgetbridge and stays in the app's private storage.
- There is no account, no analytics, no advertising ID and no crash reporting.
- Backups and exports are files that you create and keep.

This review was used to decide what Ritmo should never do.
