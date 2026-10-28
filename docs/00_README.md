# ELRS Telemetry Sniffer — "Ghost RX" on LILYGO T3‑S3

A passive receiver, built on ExpressLRS 3.5.x, that overhears the telemetry a
drone's ELRS receiver sends back to its handset, decodes it into CRSF frames,
and streams it to an Android phone over **BLE** and/or **WiFi‑UDP**. Knowledge
of the target link's **binding phrase** is assumed.

> **Status: this file is the docs-folder index.** For current project status,
> credits, hardware/compatibility notes, build instructions, and the roadmap,
> see the top-level [`../README.md`](../README.md) — that's the maintained
> entry point.
>
> **If you are reproducing or extending this, read `05_working_config_and_gotchas.md`
> first** — it has the known‑good pin layout/config and every non‑obvious fix.

This folder contains the full design, firmware modifications, transport code,
and an Android viewer app — everything needed to implement it.

## What "passive" means here

The sniffer is a **second receiver** on the link. It follows the transmitter's
sync and frequency hopping exactly like a normal RX, but in the telemetry slot
— where a normal RX *transmits* its telemetry — it stays in receive and
overhears the aircraft RX's telemetry instead. It **never transmits**, so it is
invisible to and non‑interfering with the monitored link. This is the same
approach as the reference project `HGSAFD8162/Expresslrs-Ghost-RX`.

## File map

```
telemetry-sniffer/
├─ docs/
│  ├─ 00_README.md              ← you are here
│  ├─ 01_protocol_reference.md  ← how ELRS privacy/telemetry works (code-anchored)
│  ├─ 02_build_flash_guide.md   ← general build/flash + layout
│  ├─ 03_bringup_runbook.md     ← phased bench bring-up with success criteria
│  ├─ 04_flash_serial.md        ← build & flash the serial-debug build
│  ├─ 05_working_config_and_gotchas.md ← READ FIRST: known-good config + every fix
│  ├─ 06_lr1121_usb.md          ← T3-S3 LR1121 target: dual band, binary USB CRSF
│  └─ 07_heltec_v4.md           ← Heltec WiFi LoRa 32 V4 target: SX1262, sub-GHz only
├─ firmware/
│  ├─ build.sh                              ← one command: integrate + patch + build (SX1280)
│  ├─ build.py                              ← cross-platform stage/build for every target
│  ├─ integrate.py                          ← copies sources + patches ELRS (idempotent)
│  ├─ rx_main_ghost.patch                   ← verified unified diff (git apply / patch -p1)
│  ├─ boards/heltec_wifi_lora_32_V4.json    ← vendor PlatformIO board definition
│  ├─ lib/SX126xDriver/                     ← receive-only SX1261/SX1262 driver (RADIO_SX126X)
│  ├─ hardware/*.json                       ← runtime pin layouts per board (recommended)
│  ├─ target/T3S3_Sniffer_RX.h              ← static pin header (fallback)
│  ├─ targets/*.ini                         ← PlatformIO envs per board
│  └─ src/
│     ├─ sniffer.h / sniffer.cpp            ← core: UID-from-phrase, TLM capture, reassembly
│     ├─ rx_main.patch.md                   ← human-readable explanation of the 6 edits
│     ├─ devTransport_Serial.cpp            ← serial debug output (decoded + raw CRSF hex)
│     ├─ devTransport_BLE.cpp               ← BLE GATT (Nordic UART Service) — done, working
│     └─ devTransport_WiFiUDP.cpp           ← WiFi UDP broadcast of CRSF frames — deferred
├─ gcs/                                     ← Python ground-station app (macOS native + browser)
└─ android/
   └─ app/…                                 ← Compose app skeleton: CRSF parser + BLE client + UI
                                               (drafted, NOT YET FUNCTIONAL — see roadmap)
```

Also see `../ELRS_Telemetry_Sniffer_Plan.md` for the higher‑level architecture
and rationale.

## How the pieces fit

1. **UID** is derived from the passphrase (`Sniffer_SetUidFromPhrase`) → sets the
   OTA CRC seed and the FHSS hop table. This is what lets us validate and follow
   the target link.
2. The **firmware** (stock ELRS RX + `rx_main.patch.md` edits + `sniffer.cpp`)
   locks to the link, never transmits, and captures `PACKET_TYPE_TLM` packets.
3. `sniffer.cpp` reassembles the telemetry chunks into **CRSF frames** using the
   same `StubbornReceiver` the real handset uses.
4. Each completed frame is fanned out to registered **transports**
   (`devTransport_BLE.cpp`, `devTransport_WiFiUDP.cpp`).
5. The **Android app** connects over BLE (or listens on UDP), parses CRSF, and
   displays battery/GPS/attitude/mode/link.

## Status & scope

These are integration‑ready **drafts** against ELRS 3.5.6. The protocol/decode
logic is lifted directly from the ELRS source, but a few hardware specifics
(PA/RF‑switch pins, timer re‑arm behaviour) must be confirmed on the bench —
each such spot is flagged `VERIFY` in the code and in `03_bringup_runbook.md`.

## Responsible use

Receiving is passive, but monitoring a link you don't operate can raise
radio‑monitoring, privacy, and drone‑surveillance questions that vary by
country. The clean, unambiguous use is **monitoring your own aircraft** — a
redundant telemetry display, an antenna‑tracker feed, or a backup ground
station. Keep it scoped to links whose passphrase is yours because it's your
link. Check local regulations before anything else.
