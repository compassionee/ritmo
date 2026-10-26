# Band data formats (Huami/Zepp OS protocol, fetch module 0x004b)

Specification of the records the band returns when fetching data, written for interoperability with
Gadgetbridge. It contains only byte layouts and their meaning: no third-party code.

Certainty level of each format:
- **verified**: layout confirmed with data read from a band;
- **derived**: layout reconstructed from the behaviour of the official app, not yet compared with real
  data;
- **inferred**: interpretation of a field, plausible but not confirmed.

## Conventions

- **Little-endian** integers. `s16` is signed.
- **Timestamp:** `u32`, epoch seconds UTC.
- **Time zone:** `s8` in quarter hours; offset in minutes = sign(v) · ((|v| / 4) · 60 + (|v| % 4) · 15).
  The special value `0x80` (−128) means UTC+8.
- **START_DATE response header** (16 bytes), verified:
  `10 01 01 · u32 length · u16 year · u8 month · u8 day · u8 hour · u8 min · u8 sec · s8 tz · 00`.
  The length matches the payload's; the date is in local time: with data it is the time of the first
  record, with length 0 the requested start.
- Some formats are read while `position < length − 1`: the last byte never starts a record
  (inferred: checksum or padding).

## 0x48 SLEEP_RESULT: 594-byte records (derived)

The file is a sequence of 594-byte records (`length % 594 == 0`).

| Offset | Type | Field | Notes |
|---|---|---|---|
| 0x000 | u32 | timestamp | creation or sync (inferred) |
| 0x004 | u32 | belongTime | day the sleep belongs to |
| 0x008 | u8 | version | with `version == 0` the end of the main sleep must be taken +1 minute |
| 0x009 | u8 (bool) | hasMainSleep | |
| 0x00A | s16 | mainSleepStart | minutes, see below |
| 0x00C | s16 | mainSleepEnd | minutes |
| 0x00E | s16 | onBedTime | in bed, minutes |
| 0x010 | s16 | offBedTime | out of bed, minutes |
| 0x012 | u8 (bool) | remSupported | |
| 0x013 | u8 (bool) | napSupported | |
| 0x014 | u8 (bool) | hasNaps | |
| 0x015 | u8 | avgHr | mean heart rate during sleep |
| 0x016 | u8 | score | sleep score |
| 0x017 | s8 | napCount | valid naps |
| 0x018 | 10 × {s16 start, s16 stop, s16 length} | naps | 60 bytes, always 10 slots |
| 0x054 | s8 | mainStageCount | |
| 0x055 | s8 | napStageCount | |
| 0x056 | 50 × {s16 start, s16 stop, u8 type} | mainStages | 250 bytes; the first `mainStageCount` are valid |
| 0x150 | 50 × {s16 start, s16 stop, u8 type} | napStages | 250 bytes |
| 0x24A | s16 | remMinutes | |
| 0x24C | s16 | lightMinutes | |
| 0x24E | s16 | deepMinutes | |
| 0x250 | s16 | wakeMinutes | |

- **Minutes:** counted from midnight of the day **before** `belongTime`: 1440 is midnight of the day
  the sleep belongs to.
- **Stages:** 4 = light, 5 = deep, 7 = awake, 8 = REM. An unknown code should be treated as light.
- **Derived values:**
  - time to fall asleep = `mainSleepStart − onBedTime`;
  - time in bed after waking = `offBedTime − mainSleepEnd`;
  - awakenings = main stages of type 7.
- **Compared with Gadgetbridge (commit `069af8d`)**, which reads start and end, mean heart rate, score,
  main stages and totals, these are missing:
  - onBed/offBed;
  - the flags;
  - naps and their stages.

## 0x49 HRV: 6-byte records (derived)

| Offset | Type | Field |
|---|---|---|
| 0 | u32 | timestamp |
| 4 | s8 | time zone |
| 5 | u8 | rmssd (ms, inferred) |

At most one sample per minute. Gadgetbridge reads the same layout and calls byte 4 `unk1`, which is the
time zone.

## 0x4e SHRV_SDNN: 9-byte records (verified)

| Offset | Type | Field |
|---|---|---|
| 0 | u32 | timestamp |
| 4 | s8 | time zone |
| 5 | u32 | sdnn, in ms × 100 (inferred: the values read, divided by 100, fall in tens of ms) |

## Resting heart rate

| Type | Length | Layout | Certainty |
|---|---|---|---|
| 0x3a RESTING_HEART_RATE | 6 | `u32 ts · s8 tz · u8 hr` | derived, as in Gadgetbridge |
| 0x3d MAX_HEART_RATE | 6 | `u32 ts · s8 tz · u8 value` | derived |
| 0x56 REST_HR | 8 | `u32 ts · s8 tz · u8 resting · u8 baseline · u8 level` | derived; inferred to be the resting heart rate on waking. `level` not decoded |

## SpO2

With some versions the first byte of the file is the format version and must be removed before the
records. Versions other than 1 and 2 are not supported.

| Type | Version | Layout | Certainty |
|---|---|---|---|
| 0x25 SPO2_SINGLE | v1 or no version | 5 bytes: `u32 ts · u8 x`, spo2 = `x & 0x7F`, flag = `x & 0x80` (inferred: automatic measurement) | derived |
| | v2 | 65 bytes: `u32 ts · u8 x · 60 bytes history` | derived |
| 0x26 SPO2_SLEEP | v1 or no version | 6 bytes: `u32 ts · u8 · u8` | derived |
| | v2 | 30 bytes: `u32 ts · u8 spo2 · u8 duration · 6 B high · 6 B low · 8 B quality · 4 B extension` | derived |
| 0x27 SPO2_EVENT | v2 | `u8 version` + 485-byte records: `u32 ts · u8 · 480 bytes` | structure verified; content of the 480 bytes **not clarified** (they are not two u8 curves of 240) |
| | v1 | 5 bytes: `u32 ts · u8 drop` | derived |
| 0x28 | — | 12 bytes: `u32 start · u32 end · u32 odi` | derived |
| 0x2b | — | 5 bytes: `u32 ts · u8` | derived |

## Stress

| Type | Layout | Certainty |
|---|---|---|
| 0x13 all day | 1 byte per minute (u8), time = header date + i × 60 s; values > 100 = no measurement | derived |
| 0x12 single | 5 bytes: `u32 ts · u8 stress` | derived |

Bands: 0–39 relax, 40–59 normal, 60–79 medium, 80–100 high.

## Heart rate per second

### 0x4c SEC_HR v1 (derived)
5-byte records: `u32 ts · u8 hr`.

### 0x57 SEC_HR_V2 (verified)

Sequence of packets:

| Field | Type |
|---|---|
| start_ts | u32 |
| count | u16: packet capacity (300 in every packet read) |
| samples | `count` × u8, one per second: sample i has time `start_ts + i` |

- Trailing `0xFF` bytes are padding: the valid samples are those before them.
- With `count == 0` the packet is empty (6 bytes).
- The same `start_ts` can appear twice, first with a single sample and then complete: for the same
  `start_ts` keep the packet with more samples.
- Between consecutive packets one second is usually missing (`next ts = ts + valid + 1`): it is not
  clear whether it is lost.
- A 0 value as the last valid sample is probably an invalid reading.

## 0x46 RECOVERY_HEART_RATE (derived)

Variable-length blocks: `u32 trackId · u32 count · count × u8 hr`. Inferred:
- `trackId` is the start of the workout;
- the curve is the heart rate after the end of the exercise, probably one per second.

A truncated block ends the file.

## Other formats (derived, not used here)

- **0x4a OSA_RESULT:** `u32 belongTime · u32 odiValidTime · u32 odi · u8 score · u8 code · u8 version · u8 events`, then 240 × `{u32 time, s32 value, s32 duration}`.
- **0x38 breathing during sleep:** 8 bytes, `u32 ts · s8 tz · u8 breaths/min · u8 snr · u8 channel`.
- **0x39 breathing events:** 569 bytes, `u32 ts · s8 tz · …` (rest not interpreted).
- **0x08 PPG_RR:** format not determined.

## Status in Gadgetbridge (commit `069af8d`)

| Type | Status | To add |
|---|---|---|
| 0x48 | partial | onBed/offBed, flags, naps, `version == 0` |
| 0x49 | yes | byte 4 = time zone |
| 0x4e | no | 9 bytes, sdnn in ms × 100 |
| 0x3a / 0x3d | yes | — |
| 0x56 | no | 8 bytes with baseline and level |
| 0x25 / 0x26 | yes | bit 0x80 of 0x25 |
| 0x27 | no | v2: structure known, content to be clarified |
| 0x12 / 0x13 | yes | bands |
| 0x4c / 0x57 | no | heart rate per second |
| 0x46 | no | recovery curves |
