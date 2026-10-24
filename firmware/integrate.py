#!/usr/bin/env python3
"""
Integrate the Ghost sniffer into an ExpressLRS 3.5.x source tree.

Usage:
    python3 integrate.py /path/to/ExpressLRS/src

- Copies the sniffer + transport sources into <ELRS>/src/src/
- Applies the (idempotent, anchor-based) edits to src/rx_main.cpp
- Safe to run multiple times; each edit checks whether it is already applied.

Radio behavior changes are guarded by the GHOST_* build flags. The integration
also adds a read-only reassembly-length accessor and preserves firmware.bin
during filesystem-only builds and uploads for every environment.
"""
import os, shutil, sys

HERE = os.path.dirname(os.path.abspath(__file__))
SRC_FILES = [
    "sniffer.h", "sniffer.cpp",
    "devTransport_Serial.cpp",
    "devTransport_BLE.cpp", "devTransport_WiFiUDP.cpp",
    "devGhostDisplay.h", "devGhostDisplay.cpp",
]

# (anchor, replacement) — anchor must appear verbatim & exactly once.
EDITS = [
    # 1. include
    ('#include "stubborn_receiver.h"',
     '#include "stubborn_receiver.h"\n#include "sniffer.h"'),

    # Raw USB needs the same early peripheral/interrupt initialization as
    # debug USB, before the radio and hardware timer are started. The normal
    # non-debug branches below retain SerialLogger on NullStream.
    ('static void setupSerial()\n{\n',
     'static void setupSerial()\n{\n'
     '#if defined(GHOST_SERIAL_RAW)\n'
     '    // Native USB carries binary CRSF; ELRS logs stay on NullStream.\n'
     '    USBSerial.begin(460800);\n'
     '    USBSerial.setTxTimeoutMs(0);\n'
     '#endif\n'),

    # 2. never transmit; in the telemetry slot, arm RX to OVERHEAR the aircraft's
    #    telemetry instead of transmitting our own.
    ('    if ((connectionState == disconnected) || (ExpressLRS_currTlmDenom == 1) || (alreadyTLMresp == true) || (modresult != 0) || !teamraceHasModelMatch)\n'
     '    {\n'
     '        return false; // don\'t bother sending tlm if disconnected or TLM is off\n'
     '    }',
     '    if ((connectionState == disconnected) || (ExpressLRS_currTlmDenom == 1) || (alreadyTLMresp == true) || (modresult != 0) || !teamraceHasModelMatch)\n'
     '    {\n'
     '        return false; // don\'t bother sending tlm if disconnected or TLM is off\n'
     '    }\n'
     '#if defined(GHOST_NO_TX)\n'
     '    // Ghost sniffer: this IS a telemetry slot. Rather than transmit our own\n'
     '    // telemetry, arm the radio in RX so we overhear the aircraft telemetry.\n'
     '    // IMPORTANT: do NOT set alreadyTLMresp here. That flag makes the tick skip\n'
     '    // LQCalc.inc(), leaving LQCalc.currentIsSet() == true, which makes RXdoneISR\n'
     '    // DROP the incoming telemetry packet before ProcessRFPacket ever runs.\n'
     '    Radio.RXnb();\n'
     '    return false;\n'
     '#endif'),

    # 3. capture TLM packets
    ('    case PACKET_TYPE_TLM:\n        if (firmwareOptions.is_airport)',
     '    case PACKET_TYPE_TLM:\n'
     '#if defined(GHOST_SNIFFER)\n'
     '        Sniffer_ProcessTLM(otaPktPtr);\n'
     '#endif\n'
     '        if (firmwareOptions.is_airport)'),

    # 4. keep reassembler in sync with the air-rate
    ('    OtaUpdateSerializers(smWideOr8ch, ModParams->PayloadLength);',
     '    OtaUpdateSerializers(smWideOr8ch, ModParams->PayloadLength);\n'
     '#if defined(GHOST_SNIFFER)\n'
     '    Sniffer_OnRateChanged();\n'
     '#endif'),

    # 5. init transports at boot
    ('    devicesStart();',
     '    devicesStart();\n'
     '#if defined(GHOST_SNIFFER)\n'
     '    Ghost_SetupTransports();\n'
     '#endif'),

    # 6. poll for completed CRSF frames + update OLED each loop
    ('    devicesUpdate(now);',
     '    devicesUpdate(now);\n'
     '#if defined(GHOST_SNIFFER)\n'
     '    Ghost_Loop(now);\n'
     '#endif'),

    # 7. diagnostic: count every demodulated packet before CRC/UID check
    ('bool ICACHE_RAM_ATTR ProcessRFPacket(SX12xxDriverCommon::rx_status const status)\n{\n',
     'bool ICACHE_RAM_ATTR ProcessRFPacket(SX12xxDriverCommon::rx_status const status)\n{\n'
     '#if defined(GHOST_SNIFFER)\n'
     '    Sniffer_OnRawPacket(status == SX12xxDriverCommon::SX12XX_RX_OK);\n'
     '#endif\n'),

    # 8. per-packet RSSI/SNR - AFTER GetLastPacketStats() so the stats belong to
    #    the packet we just received. Lets the sniffer measure RSSI/LQ of the
    #    DRONE's telemetry packets (PACKET_TYPE_TLM) separately from the RC packets
    #    that dominate CRSF::LinkStatistics (which reflect the handset TX link).
    ('    // Store the LQ/RSSI/Antenna\n'
     '    Radio.GetLastPacketStats();\n'
     '    getRFlinkInfo();',
     '    // Store the LQ/RSSI/Antenna\n'
     '    Radio.GetLastPacketStats();\n'
     '    getRFlinkInfo();\n'
     '#if defined(GHOST_SNIFFER)\n'
     '    Sniffer_OnPacketStats(otaPktPtr->std.type, Radio.LastPacketRSSI, Radio.LastPacketSNRRaw);\n'
     '#endif'),
]

# Additional upstream hooks required by the LR1121 board and by passive CRSF
# validation. Keep every mutation reproducible and idempotent in this script.
EXTRA_EDITS = {
    "python/build_env_setup.py": [
        ('Import("env", "projenv")',
         'Import("env")\n'
         'from SCons.Script import COMMAND_LINE_TARGETS'),
        ("# Remove stale binary so the platform is forced to build a new one and attach options/hardware-layout files\n"
         "try:\n"
         "    os.remove(env['PROJECT_BUILD_DIR'] + '/' + env['PIOENV'] +'/'+ env['PROGNAME'] + '.bin')\n"
         "except FileNotFoundError:\n"
         "    None",
         "# Regenerate options for firmware builds, but preserve the finished\n"
         "# firmware artifact during separate filesystem or no-build uploads.\n"
         "if not set(COMMAND_LINE_TARGETS).intersection(('buildfs', 'uploadfs', 'uploadfsota', 'nobuild')):\n"
         "    try:\n"
         "        os.remove(env['PROJECT_BUILD_DIR'] + '/' + env['PIOENV'] +'/'+ env['PROGNAME'] + '.bin')\n"
         "    except FileNotFoundError:\n"
         "        pass"),
    ],
    "lib/StubbornReceiver/stubborn_receiver.h": [
        ("    bool HasFinishedData();",
         "    bool HasFinishedData();\n"
         "    uint8_t GetReceivedLength() const { return currentOffset; }"),
    ],
    "src/common.cpp": [
        ("    // Dual Band modes not supported for hardware with only a single LR1121",
         "#if defined(GHOST_SNIFFER) && defined(GHOST_LR1121_2400_ONLY)\n"
         "    // Scan SX1280-compatible 2.4 GHz LoRa; LR1121 GFSK is not FLRC.\n"
         "    if (ModParams->radio_type != RADIO_TYPE_LR1121_LORA_2G4)\n"
         "        return false;\n"
         "#endif\n\n"
         "    // Dual Band modes not supported for hardware with only a single LR1121"),
    ],
    "lib/LR1121Driver/LR1121_hal.cpp": [
        ("    constexpr uint32_t wtimeoutUS = 1000U;",
         "#if defined(GHOST_SNIFFER)\n"
         "    // Match ELRS 3.6.3: some LR1121 startup commands exceed 1 ms.\n"
         "    constexpr uint32_t wtimeoutUS = 2000U;\n"
         "#else\n"
         "    constexpr uint32_t wtimeoutUS = 1000U;\n"
         "#endif"),
        ("    gpio_pullup_en((gpio_num_t)GPIO_PIN_MISO);\n"
         "    SPIEx.setFrequency(17500000);",
         "    gpio_pullup_en((gpio_num_t)GPIO_PIN_MISO);\n"
         "#if defined(GHOST_SNIFFER)\n"
         "    // LR1121 specifies at most 16 MHz; leave margin on the T3-S3.\n"
         "    SPIEx.setFrequency(10000000);\n"
         "#else\n"
         "    SPIEx.setFrequency(17500000);\n"
         "#endif"),
        ("    SPIEx.write(radioNumber, OutBuffer, size + 2);",
         "#if defined(GHOST_SNIFFER)\n"
         "    // Finish clocking this command before the next BUSY check. The\n"
         "    // asynchronous write can otherwise return before BUSY asserts.\n"
         "    SPIEx.read(radioNumber, OutBuffer, size + 2); // Returned bytes ignored\n"
         "    delayMicroseconds(1); // Allow BUSY to assert after NSS rises\n"
         "#else\n"
         "    SPIEx.write(radioNumber, OutBuffer, size + 2);\n"
         "#endif"),
        ("    SPIEx.write(radioNumber, OutBuffer, 2);",
         "#if defined(GHOST_SNIFFER)\n"
         "    // Same synchronous command boundary as the payload overload.\n"
         "    SPIEx.read(radioNumber, OutBuffer, 2); // Returned bytes ignored\n"
         "    delayMicroseconds(1);\n"
         "#else\n"
         "    SPIEx.write(radioNumber, OutBuffer, 2);\n"
         "#endif"),
    ],
    "lib/LR1121Driver/LR1121.cpp": [
        ("    uint8_t version[5] = {0};\n"
         "    hal.WriteCommand(LR11XX_SYSTEM_GET_VERSION_OC, SX12XX_Radio_1);\n"
         "    hal.ReadCommand(version, sizeof(version), SX12XX_Radio_1);",
         "    uint8_t version[5] = {0};\n"
         "#if defined(GHOST_SNIFFER)\n"
         "    // Retry the initial probe: a cold T3-S3 can return an invalid\n"
         "    // first response after reset. Keep this delay out of RX handling.\n"
         "    const uint32_t probeStart = millis();\n"
         "    while (!hal.WaitOnBusy(SX12XX_Radio_1) && millis() - probeStart < 50)\n"
         "        delay(1);\n"
         "    for (uint8_t attempt = 0; attempt < 5; ++attempt)\n"
         "    {\n"
         "        if (attempt) delay(5);\n"
         "        memset(version, 0, sizeof(version));\n"
         "        if (!hal.WaitOnBusy(SX12XX_Radio_1)) continue;\n"
         "        hal.WriteCommand(LR11XX_SYSTEM_GET_VERSION_OC, SX12XX_Radio_1);\n"
         "        hal.ReadCommand(version, sizeof(version), SX12XX_Radio_1);\n"
         "        if (version[2] == 0x03) break;\n"
         "    }\n"
         "#else\n"
         "    hal.WriteCommand(LR11XX_SYSTEM_GET_VERSION_OC, SX12XX_Radio_1);\n"
         "    hal.ReadCommand(version, sizeof(version), SX12XX_Radio_1);\n"
         "#endif"),
        ("    CommitOutputPower();\n"
         "}\n\n"
         "void LR1121Driver::ConfigModParamsFSK",
         "    CommitOutputPower();\n"
         "#if defined(GHOST_SNIFFER)\n"
         "    // Restore IRQ routing after the radio configuration has settled.\n"
         "    // T3-S3/LR1121 FW 1.1 receives packets but leaves DIO9 low with\n"
         "    // only the early Begin() setup; reapplying this mask restores RX.\n"
         "    delayMicroseconds(20);\n"
         "    if (hal.WaitOnBusy(SX12XX_Radio_All))\n"
         "    {\n"
         "        SetDioIrqParams();\n"
         "        delayMicroseconds(20);\n"
         "        hal.WaitOnBusy(SX12XX_Radio_All);\n"
         "    }\n"
         "#endif\n"
         "}\n\n"
         "void LR1121Driver::ConfigModParamsFSK"),
        ("    //Clear Errors\n"
         "    hal.WriteCommand(LR11XX_SYSTEM_CLEAR_ERRORS_OC, SX12XX_Radio_All); // Remove later?  Might not be required???",
         "    //Clear Errors\n"
         "    hal.WriteCommand(LR11XX_SYSTEM_CLEAR_ERRORS_OC, SX12XX_Radio_All); // Remove later?  Might not be required???\n"
         "#if defined(GHOST_LR1121_TCXO)\n"
         "    // LilyGo T3-S3 LR1121 uses a 3.0 V TCXO, voltage code 6.\n"
         "    // Enable it before calibration or any synthesizer operation.\n"
         "    SetMode(LR1121_MODE_STDBY_RC, SX12XX_Radio_All);\n"
         "    uint8_t tcxoConfig[] = {GHOST_LR1121_TCXO, 0, 0, 164}; // 5 ms at 32768 Hz\n"
         "    hal.WriteCommand(LR11XX_SYSTEM_SET_TCXO_MODE_OC, tcxoConfig, sizeof(tcxoConfig), SX12XX_Radio_All);\n"
         "    delay(10); // TCXO startup exceeds the HAL's 1 ms BUSY timeout\n"
         "    uint8_t calibration[] = {0x3f};\n"
         "    hal.WriteCommand(LR11XX_SYSTEM_CALIBRATE_OC, calibration, sizeof(calibration), SX12XX_Radio_All);\n"
         "    delay(10);\n"
         "#endif"),
        ("void LR1121Driver::startCWTest(uint32_t freq, SX12XX_Radio_Number_t radioNumber)\n{",
         "void LR1121Driver::startCWTest(uint32_t freq, SX12XX_Radio_Number_t radioNumber)\n{\n"
         "#if defined(GHOST_NO_TX)\n"
         "    return; // Passive sniffer: prohibit continuous-wave transmission too.\n"
         "#endif"),
        ("    case LR1121_MODE_TX:\n"
         "        // Table 7-3: SetTx Command\n"
         "        hal.WriteCommand(LR11XX_RADIO_SET_TX_OC, buf, 3, radioNumber);",
         "    case LR1121_MODE_TX:\n"
         "#if !defined(GHOST_NO_TX)\n"
         "        // Table 7-3: SetTx Command\n"
         "        hal.WriteCommand(LR11XX_RADIO_SET_TX_OC, buf, 3, radioNumber);\n"
         "#endif"),
        ("void ICACHE_RAM_ATTR LR1121Driver::TXnb(uint8_t * data, uint8_t size, SX12XX_Radio_Number_t radioNumber)\n{",
         "void ICACHE_RAM_ATTR LR1121Driver::TXnb(uint8_t * data, uint8_t size, SX12XX_Radio_Number_t radioNumber)\n{\n"
         "#if defined(GHOST_NO_TX)\n"
         "    RXnb();\n"
         "    return; // Defense in depth: no alternate caller may transmit.\n"
         "#endif"),
    ],
}


def apply_edits(path, edits):
    with open(path, encoding="utf-8") as source:
        text = source.read()
    for i, (anchor, repl) in enumerate(edits, 1):
        if repl in text:
            print(f"{os.path.basename(path)} edit {i}: already applied")
            continue
        n = text.count(anchor)
        if n != 1:
            raise ValueError(f"{path} edit {i}: anchor found {n} times (expected 1); ELRS version mismatch")
        text = text.replace(anchor, repl)
        print(f"{os.path.basename(path)} edit {i}: applied")
    return text


def main():
    if len(sys.argv) != 2:
        print(__doc__); sys.exit(1)
    elrs_src = os.path.abspath(sys.argv[1])            # .../ExpressLRS/src
    dst_src = os.path.join(elrs_src, "src")            # .../ExpressLRS/src/src
    rx_main = os.path.join(dst_src, "rx_main.cpp")
    if not os.path.isfile(rx_main):
        print(f"ERROR: {rx_main} not found. Point me at ExpressLRS/src"); sys.exit(2)

    # Validate every anchor before changing any upstream file.
    pending = {rx_main: apply_edits(rx_main, EDITS)}
    for relative, edits in EXTRA_EDITS.items():
        path = os.path.join(elrs_src, relative)
        pending[path] = apply_edits(path, edits)

    # copy sources
    for f in SRC_FILES:
        shutil.copy2(os.path.join(HERE, "src", f), os.path.join(dst_src, f))
        print(f"copied  src/{f}")

    for path, text in pending.items():
        with open(path, "w", encoding="utf-8", newline="\n") as target:
            target.write(text)
    print("\nDone. Copy the target INI for your radio into the ELRS targets/ directory:")
    print("  LR1121: targets/t3s3_lr1121_sniffer.ini")
    print("    pio run -e T3S3_Sniffer_LR1121_RX_USB")
    print("  SX1280: targets/t3s3_sniffer.ini")
    print("    pio run -e T3S3_Sniffer_2400_RX_Serial")

if __name__ == "__main__":
    main()
