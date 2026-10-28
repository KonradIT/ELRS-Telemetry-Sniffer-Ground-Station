# Heltec WiFi LoRa 32 V4: sub-GHz sniffer on an SX1262

This target ports the sniffer to the **Heltec WiFi LoRa 32 V4** (ESP32-S3FN8
with an SX1262, 863–928 MHz) on ExpressLRS **3.5.6**. The board receives
**sub-GHz ExpressLRS LoRa links only**; it has no 2.4 GHz radio, so the
configuration page offers the frequency plan (AU915, FCC915, EU868 or IN866)
but **no 2.4 GHz / sub-GHz band switch**. That switch exists only on the
dual-band [T3-S3 LR1121 target](06_lr1121_usb.md).

**Status: receiving confirmed on one board.** A third-party bench test on
27 October 2026 (V4 specimen with an ESP32-S3 rev 0.2 and **16 MB** flash,
`_Serial` build, FCC915, 100 Hz link) locked about 8 seconds after boot and
stayed locked for a 120-second capture: 11,313 packets accepted by the radio,
11,212 passing the ExpressLRS CRC, 258 CRC-valid CRSF telemetry frames (GPS,
battery, attitude, flight mode) with zero malformed frames, and coordinates
shown on the OLED. No pin, TCXO, SPI or modulation change was needed. The
obstacle found instead was the Wi-Fi fallback: with the link not up during the
first 60 seconds the station became a permanent configuration access point
and never listened again. The firmware now restarts out of an unused access
point (see *Configure over Wi-Fi* below), and debug USB logging no longer
blocks when no host reads the port. The 25/50 Hz and LR1121 SF5 rates, the
`_USB` target and other V4 revisions remain unverified.

## What ExpressLRS 3.5.6 lacks and how the port fills it

ExpressLRS 3.5.6 ships drivers for the SX127x, SX128x and LR1121 only. The
integration therefore adds a fourth radio type, `RADIO_SX126X`, with:

- a receive-only SX1261/SX1262 driver in
  [`firmware/lib/SX126xDriver/`](../firmware/lib/SX126xDriver/), modelled on the
  SX1280 and LR1121 ExpressLRS drivers (same `SX12xxDriverCommon` contract,
  DIO1 interrupt on RX done, continuous receive between hops, RFAMP front-end
  control, every transmit path turned into a receive request by `GHOST_NO_TX`);
- a rate table whose slots stay **index-aligned** with the SX127x and LR1121
  tables, because ExpressLRS sync packets carry the transmitter's table index;
- `RADIO_SX126X` branches in `common.h`, `common.cpp`, `targets.h`, `FHSS`,
  `config.cpp`, `device.cpp`, `devWIFI.cpp`, `build_html.py` and
  `build_flags.py`, applied by [`integrate.py`](../firmware/integrate.py) as
  anchored, idempotent edits;
- the vendor board definition
  [`firmware/boards/heltec_wifi_lora_32_V4.json`](../firmware/boards/heltec_wifi_lora_32_V4.json)
  (from the
  [Heltec PlatformIO guide](https://wiki.heltec.org/docs/devices/open-source-hardware/esp32-series/three-platform/PlatformIO)),
  copied into the ExpressLRS `boards/` directory.

## Supported air rates

| ExpressLRS rate | Transmitter | Receivable on the V4 |
|---|---|---|
| 25 Hz (SF9), 50 Hz (SF8), 100 Hz (SF7) | SX127x or LR1121, 900 MHz LoRa | Yes |
| 250 Hz and 200 Hz Full (SF5) | LR1121 only, 900 MHz LoRa | Expected; same SF5 modem family, unverified |
| 200 Hz, 100 Hz Full, D50 (SF6) | SX127x or LR1121 | **No** |
| Any 2.4 GHz mode, GFSK 1000 Hz, dual band | — | **No** |

The SF6 modes are excluded on purpose: the SX1261/2 datasheet states that its
SF6 is **not backward compatible with SX127x SF6**, and ExpressLRS configures
the LR1121 into that SX127x-compatible SF6 for the 900 MHz 200 Hz, 100 Hz Full
and D50 modes. No register workaround for the SX126x is documented. The
corresponding table slots are present but marked unsupported, so the receiver
skips them while scanning and ignores a transmitter's request to switch to
them. Use 100 Hz, 50 Hz or 25 Hz on the link you want to monitor (or 250 Hz /
200 Hz Full on an LR1121 transmitter, once verified).

Sources:
[Semtech SX1261/2 datasheet](https://m5stack-doc.oss-cn-shenzhen.aliyuncs.com/1198/DS_SX1261_2_V2-2.pdf),
[StuartsProjects on SX1262 SF6](https://stuartsprojects.github.io/2019/10/08/sx1262-improved-LoRa-device.html),
[ExpressLRS 3.5.6 rate tables](https://github.com/ExpressLRS/ExpressLRS/blob/3.5.6/src/src/common.cpp).

## Hardware layout and front end

Pins come from the runtime hardware layout
[`heltec_wifi_lora_32_v4_sniffer_rx.json`](../firmware/hardware/heltec_wifi_lora_32_v4_sniffer_rx.json)
(staged as `hardware.json`):

| Function | GPIO |
|---|---|
| SPI SCK / MISO / MOSI | 9 / 11 / 10 |
| Radio NSS / RST | 8 / 12 |
| Radio DIO1 (IRQ) / BUSY | 14 / 13 |
| Front-end LDO enable (`power_enable`) | 7 |
| Front-end chip enable CSD (`power_rxen`) | 2 |
| KCT8103L CTX, low = LNA path (`power_txen`) | 5 |
| OLED SDA / SCL / RST | 17 / 18 / 21 |
| OLED supply Vext (active low) | 36 |
| LED | 35 |

The V4 has a PA/LNA front end: a **GC1109** on V4.2 boards or a **KCT8103L**
on V4.3 boards, both powered through an LDO on GPIO7 and enabled by GPIO2. The
radio's **DIO2** drives the TX/RX select of either chip (the driver enables the
SX1262 "DIO2 as RF switch" function), and the ExpressLRS RFAMP code holds GPIO2
high and GPIO5 low whenever the receiver is armed, which selects the LNA path on
both revisions. The GC1109 CPS line (GPIO46) is left floating; it is ignored in
receive mode. Because the firmware never transmits, the PA is never keyed. Pin
and front-end facts come from the Heltec `pins_arduino.h` and the
[Meshtastic Heltec V4 variant](https://github.com/meshtastic/firmware/blob/master/variants/esp32s3/heltec_v4/variant.h).

The SX1262 module's **TCXO is powered from DIO3 at 1.8 V**; the driver enables
it (`GHOST_SX126X_TCXO=2`), repeats the chip calibration and clears the
resulting oscillator start error. Hops pause in `STDBY_XOSC`, so the TCXO keeps
running and the 5 ms start-up delay only applies after a rate change. The
module's DC-DC regulator and boosted LNA gain are enabled, the LoRa sync word
is the SX127x-compatible private value, and the receiver runs in continuous
mode with the implicit-header timeout workaround applied for timed windows.

The 0.96 in SSD1315 OLED (SSD1306 compatible) is driven by `devGhostDisplay.cpp`
on SDA 17 / SCL 18 with reset on GPIO21, after switching Vext (GPIO36) on.

## USB, board definition and flags

The V4 exposes the ESP32-S3 **native USB** (VID 0x303A, PID 0x1001), like the
T3-S3, so `USBSerial` carries binary CRSF in the `_USB` build and logs in the
`_Serial` build. The vendor JSON enables `ARDUINO_USB_CDC_ON_BOOT`, which
renames `USBSerial`; the target INI therefore overrides
`board_build.extra_flags` with the stock ESP32-S3 USB flags and selects the
generic `esp32s3` Arduino variant (the pinned core 2.0.11 has no V4 variant and
all pins come from the hardware layout anyway). Flash is declared as 8 MB by the
vendor file while the bench-tested specimen reported 16 MB; ExpressLRS keeps
its 4 MB `min_spiffs` partition layout, which fits either chip.

## Configure over Wi-Fi

Behaviour matches the LR1121 target: with no matching link during the first
**60 seconds after boot**, the station opens the ExpressLRS configuration
access point (**ExpressLRS RX**, password **expresslrs**, http://10.0.0.1/;
the OLED shows the details).

1. On **Options**, choose the transmitter's **frequency plan**: EU868, FCC915,
   AU915 or IN866. There is no band switch on this board.
2. On **Model**, enter the binding phrase and save it.
3. Save any changed options and reboot to resume reception.

The 433 MHz plans are hidden because the board's RF path covers 863–928 MHz.
A blank Wi-Fi timeout disables the automatic access point; the saved settings
carry the sniffer format marker, so an explicit `-1` stays disabled across
boots on every sniffer target.

ExpressLRS stops the radio while Wi-Fi is up, so an access point that opened
automatically because the aircraft was simply not powered yet used to silence
the sniffer until the next power cycle. Now, if nobody joins that automatic
access point for **3 minutes** (build option `GHOST_WIFI_IDLE_RESTART_MS`),
the station restarts and listens again; the 60-second deadline re-arms after
the restart, so a station left powered keeps alternating between listening
and a configuration window until a link appears. A client connected to the
access point, or Wi-Fi started on purpose, is never interrupted.

## Windows build and flash

Run from this repository in PowerShell against a **separate** ExpressLRS 3.5.6
checkout. `--domain` stages the default frequency plan (EU868 when omitted).

```powershell
python -m pip install platformio pyserial
git clone --branch 3.5.6 --depth 1 https://github.com/ExpressLRS/ExpressLRS.git ..\ExpressLRS-heltec-v4
python firmware/build.py ..\ExpressLRS-heltec-v4 --env Heltec_V4_Sniffer_900_RX_USB --domain FCC915 --phrase "your-binding-phrase" --upload-port COM12
```

| Environment | USB output | Consumer |
|---|---|---|
| `Heltec_V4_Sniffer_900_RX_USB` | Binary CRSF frames | `capture_crsf.py` or a binary CRSF application |
| `Heltec_V4_Sniffer_900_RX_Serial` | Readable diagnostics and `[CRSF]` hex lines | Serial terminal or the Python GCS |

The script installs the SX126x driver, the board definition, the target INI,
the hardware layout and the binding UID, builds firmware and filesystem, then
flashes both. Hold **PRG/BOOT**, tap **RST** and release if the upload cannot
sync. The script also puts ExpressLRS's vendored esptool 4.x first on
`PYTHONPATH` for PlatformIO: an esptool 5.x installed in the PlatformIO
virtualenv otherwise shadows it and the build stops with
`ImportError: cannot import name 'make_image' from 'esptool.cmds'`. Running
`pio run` by hand needs the same `PYTHONPATH=<checkout>/src/python/external/esptool`.

`--unbound` instead of `--phrase` stages an image without a UID: the receiver
boots into ExpressLRS bind mode (listening only, it never transmits), opens the
Wi-Fi access point after the 60-second timeout, and the phrase entered on the
**Model** tab is saved to the device configuration. That configuration survives
reflashing the same unbound image. To produce one file for a browser flasher
such as Spacehuhn's ESP Web Tool (written at offset `0x0`), merge the build
outputs after `pio run -e <env>` and `pio run -e <env> -t buildfs`:

```powershell
esptool --chip esp32s3 merge_bin -o heltec_v4_factory.bin --flash_mode keep --flash_freq keep --flash_size keep `
  0x0 bootloader.bin 0x8000 partitions.bin 0xe000 boot_app0.bin 0x10000 firmware.bin 0x3d0000 spiffs.bin
```

The files are in `.pio/build/<env>/`; `spiffs.bin` carries `hardware.json` and
`options.json`, and `0x3d0000` is the SPIFFS offset of the `min_spiffs`
partition table ExpressLRS uses. Capture and verification work exactly as described for the
[LR1121 target](06_lr1121_usb.md#verify-aircraft-telemetry); the `D` command
reports the plan, current frequency and UID.

## Bring-up checklist

1. Flash `Heltec_V4_Sniffer_900_RX_Serial`. The boot log must show
   `Read SX126x LoRa sync word: 1424` followed by `SX126x Ready!`. A value of
   `0` or `ffff` means the SPI pins or reset line are wrong; `radioFailed` is
   also reported by the `D` command in the `_USB` build.
2. With the handset on, the heartbeat should settle on `LOCK` at 100 Hz, 50 Hz
   or 25 Hz. If it only ever shows `SRCH`, confirm the link is not using an SF6
   mode (200 Hz, 100 Hz Full, D50) and that the plan matches the transmitter.
   While searching, the `_Serial` build also prints one `[SX126x]` line per
   second with the chip status (`mode=5` is receive), the device error bits,
   the interrupt and RX-done counters and the BUSY timeout count; the
   heartbeat's `raw=` and `ok=` counters separate demodulated packets from
   those that passed the ExpressLRS CRC and UID check. The `_USB` build
   reports the same counters in its `D` reply.
3. If the link locks but RSSI is far worse than the T3-S3 on the same bench,
   suspect the front end: GPIO7, GPIO2 and GPIO5 levels (see the table above)
   or the DIO2 switch setting.
4. Aircraft frames (`GPS`, `BATT`, `ATT`, `MODE`) confirm telemetry; `LINK`
   lines alone are synthesized locally.
