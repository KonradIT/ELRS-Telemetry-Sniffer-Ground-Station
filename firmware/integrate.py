#!/usr/bin/env python3
"""
Integrate the Ghost sniffer into an ExpressLRS 3.5.x source tree.

Usage:
    python3 integrate.py /path/to/ExpressLRS/src

- Copies the sniffer + transport sources into <ELRS>/src/src/
- Installs the receive-only SX126x radio driver (lib/SX126xDriver) and the
  Heltec WiFi LoRa 32 V4 board definition (boards/) used by the Heltec target
- Applies the (idempotent, anchor-based) edits to src/rx_main.cpp and the
  upstream files listed in the EXTRA_EDITS patch sets
- Safe to run multiple times; each edit checks whether it is already applied,
  and UPGRADES rewrites text left behind by earlier versions of this script.

Radio behavior changes are guarded by the GHOST_* build flags. The integration
also adds a read-only reassembly-length accessor, preserves firmware.bin
during filesystem-only builds and uploads for every environment, and adds the
RADIO_SX126X radio type (SX1261/SX1262, sub-GHz LoRa) alongside the stock
SX127x, SX128x and LR1121 types.
"""
import os, shutil, sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
from wifi_config import EXTRA_EDITS as WIFI_CONFIG_EDITS, UPGRADES as WIFI_CONFIG_UPGRADES
from wifi_fallback import EXTRA_EDITS as WIFI_FALLBACK_EDITS, UPGRADES as WIFI_FALLBACK_UPGRADES

LEGACY_BAND_FILTER = (
    "#if defined(GHOST_SNIFFER) && defined(GHOST_LR1121_2400_ONLY)\n"
    "    // Scan SX1280-compatible 2.4 GHz LoRa; LR1121 GFSK is not FLRC.\n"
    "    if (ModParams->radio_type != RADIO_TYPE_LR1121_LORA_2G4)\n"
    "        return false;\n"
    "#endif\n\n"
)
BAND_FILTER = (
    "#if defined(GHOST_SNIFFER)\n"
    "    // Follow the selected band; a single LR1121 receives one link.\n"
    "    const auto wantedType = firmwareOptions.ghost_subghz\n"
    "        ? RADIO_TYPE_LR1121_LORA_900 : RADIO_TYPE_LR1121_LORA_2G4;\n"
    "    if (ModParams->radio_type != wantedType)\n"
    "        return false;\n"
    "#endif\n\n"
)
SRC_FILES = [
    "sniffer.h", "sniffer.cpp",
    "devTransport_Serial.cpp",
    "devTransport_BLE.cpp", "devTransport_WiFiUDP.cpp",
    "devGhostDisplay.h", "devGhostDisplay.cpp",
]
# Additional PlatformIO libraries and board definitions, copied verbatim.
LIB_FILES = {
    "SX126xDriver": [
        "SX126xDriver.h", "SX126x.h", "SX126x.cpp", "SX126x_Regs.h",
        "SX126x_hal.h", "SX126x_hal.cpp",
    ],
}
BOARD_FILES = ["heltec_wifi_lora_32_V4.json"]

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

    # Debug builds log over native USB too. HWCDC blocks for its TX timeout
    # per write once the buffer fills and nobody reads the port, which stalls
    # the main loop (CRSF reassembly, OLED, Wi-Fi deadline). Drop bytes instead.
    ('        USBSerial.begin(serialBaud);\n        SerialLogger = &USBSerial;',
     '        USBSerial.begin(serialBaud);\n        SerialLogger = &USBSerial;\n'
     '#if defined(GHOST_SNIFFER)\n'
     '        USBSerial.setTxTimeoutMs(0); // drop log bytes rather than stall the loop on an unread host\n'
     '#endif'),
    ('    USBSerial.begin(460800);\n    SerialLogger = &USBSerial;',
     '    USBSerial.begin(460800);\n    SerialLogger = &USBSerial;\n'
     '#if defined(GHOST_SNIFFER)\n'
     '    USBSerial.setTxTimeoutMs(0); // drop log bytes rather than stall the loop on an unread host\n'
     '#endif'),

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
        ('#include "OTA.h"', '#include "OTA.h"\n#if defined(GHOST_SNIFFER)\n#include "options.h"\n#endif'),
        ("    // Dual Band modes not supported for hardware with only a single LR1121",
         BAND_FILTER + "    // Dual Band modes not supported for hardware with only a single LR1121"),
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

# ExpressLRS 3.5.6 rate table for the SX1261/SX1262. OTA sync packets carry
# the transmitter's own table index, so slots 0-5 mirror the SX127x table and
# slots 6-13 mirror the LR1121 table. Only RADIO_TYPE_SX126x_LORA rows are
# selectable: an SX126x cannot demodulate the SX127x-style SF6 used by 200 Hz,
# 100 Hz full resolution and D50 (rows 0, 1, 5), and rows 6-11 are LR1121
# 2.4 GHz modes. Rows 12 and 13 are the LR1121 sub-GHz SF5 modes, which share
# the SX126x LoRa modem encoding.
SX126X_RATE_TABLE = (
    '#if defined(RADIO_SX126X)\n'
    '\n'
    '#include "SX126xDriver.h"\n'
    'SX126xDriver DMA_ATTR Radio;\n'
    '\n'
    '// Receive-only SX1262 port: slots 0-5 follow the SX127x table and 6-13 the\n'
    '// LR1121 table because OTA sync packets carry the transmitter\'s table index.\n'
    '// Only RADIO_TYPE_SX126x_LORA rows are selectable. SX127x-style SF6 (rows 0,\n'
    '// 1, 5) is not demodulable by an SX126x and rows 6-11 are 2.4 GHz modes.\n'
    'expresslrs_mod_settings_s ExpressLRS_AirRateConfig[RATE_MAX] = {\n'
    '    {0,  RADIO_TYPE_SX127x_LORA,      RATE_LORA_200HZ,     SX126X_LORA_BW_500, SX126X_LORA_SF6, SX126X_LORA_CR_4_7,  8, TLM_RATIO_1_64, 4,  5000, OTA4_PACKET_SIZE, 1},\n'
    '    {1,  RADIO_TYPE_SX127x_LORA,      RATE_LORA_100HZ_8CH, SX126X_LORA_BW_500, SX126X_LORA_SF6, SX126X_LORA_CR_4_8,  8, TLM_RATIO_1_32, 4, 10000, OTA8_PACKET_SIZE, 1},\n'
    '    {2,  RADIO_TYPE_SX126x_LORA,      RATE_LORA_100HZ,     SX126X_LORA_BW_500, SX126X_LORA_SF7, SX126X_LORA_CR_4_7,  8, TLM_RATIO_1_32, 4, 10000, OTA4_PACKET_SIZE, 1},\n'
    '    {3,  RADIO_TYPE_SX126x_LORA,      RATE_LORA_50HZ,      SX126X_LORA_BW_500, SX126X_LORA_SF8, SX126X_LORA_CR_4_7, 10, TLM_RATIO_1_16, 4, 20000, OTA4_PACKET_SIZE, 1},\n'
    '    {4,  RADIO_TYPE_SX126x_LORA,      RATE_LORA_25HZ,      SX126X_LORA_BW_500, SX126X_LORA_SF9, SX126X_LORA_CR_4_7, 10, TLM_RATIO_1_8,  2, 40000, OTA4_PACKET_SIZE, 1},\n'
    '    {5,  RADIO_TYPE_SX127x_LORA,      RATE_DVDA_50HZ,      SX126X_LORA_BW_500, SX126X_LORA_SF6, SX126X_LORA_CR_4_7,  8, TLM_RATIO_1_64, 2,  5000, OTA4_PACKET_SIZE, 4},\n'
    '    {6,  RADIO_TYPE_LR1121_LORA_2G4,  RATE_LORA_250HZ,     SX126X_LORA_BW_500, SX126X_LORA_SF6, SX126X_LORA_CR_4_8, 14, TLM_RATIO_1_64, 4,  4000, OTA4_PACKET_SIZE, 1},\n'
    '    {7,  RADIO_TYPE_LR1121_LORA_2G4,  RATE_LORA_150HZ,     SX126X_LORA_BW_500, SX126X_LORA_SF7, SX126X_LORA_CR_4_8, 12, TLM_RATIO_1_32, 4,  6666, OTA4_PACKET_SIZE, 1},\n'
    '    {8,  RADIO_TYPE_LR1121_LORA_2G4,  RATE_LORA_100HZ_8CH, SX126X_LORA_BW_500, SX126X_LORA_SF7, SX126X_LORA_CR_4_8, 12, TLM_RATIO_1_32, 4, 10000, OTA8_PACKET_SIZE, 1},\n'
    '    {9,  RADIO_TYPE_LR1121_LORA_2G4,  RATE_LORA_50HZ,      SX126X_LORA_BW_500, SX126X_LORA_SF8, SX126X_LORA_CR_4_8, 12, TLM_RATIO_1_16, 2, 20000, OTA4_PACKET_SIZE, 1},\n'
    '    {10, RADIO_TYPE_LR1121_LORA_DUAL, RATE_LORA_150HZ,     SX126X_LORA_BW_500, SX126X_LORA_SF6, SX126X_LORA_CR_4_8, 12, TLM_RATIO_1_32, 4,  6666, OTA4_PACKET_SIZE, 1},\n'
    '    {11, RADIO_TYPE_LR1121_LORA_DUAL, RATE_LORA_100HZ_8CH, SX126X_LORA_BW_500, SX126X_LORA_SF6, SX126X_LORA_CR_4_8, 18, TLM_RATIO_1_32, 4, 10000, OTA8_PACKET_SIZE, 1},\n'
    '    {12, RADIO_TYPE_SX126x_LORA,      RATE_LORA_250HZ,     SX126X_LORA_BW_500, SX126X_LORA_SF5, SX126X_LORA_CR_4_8,  8, TLM_RATIO_1_64, 4,  4000, OTA4_PACKET_SIZE, 1},\n'
    '    {13, RADIO_TYPE_SX126x_LORA,      RATE_LORA_200HZ_8CH, SX126X_LORA_BW_500, SX126X_LORA_SF5, SX126X_LORA_CR_4_7,  8, TLM_RATIO_1_64, 4,  5000, OTA8_PACKET_SIZE, 1}};\n'
    '\n'
    'expresslrs_rf_pref_params_s ExpressLRS_AirRateRFperf[RATE_MAX] = {\n'
    '    {0,  -112,  4380, 3000, 2500, 600, 5000, SNR_SCALE( 1), SNR_SCALE(3.0)},\n'
    '    {1,  -112,  6690, 3500, 2500, 600, 5000, SNR_SCALE( 1), SNR_SCALE(3.0)},\n'
    '    {2,  -117,  8770, 3500, 2500, 600, 5000, SNR_SCALE( 1), SNR_SCALE(2.5)},\n'
    '    {3,  -120, 18560, 4000, 2500, 600, 5000, SNR_SCALE(-1), SNR_SCALE(1.5)},\n'
    '    {4,  -123, 29950, 6000, 4000, 600, 5000, SNR_SCALE(-3), SNR_SCALE(0.5)},\n'
    '    {5,  -112,  4380, 3000, 2500, 600, 5000, SNR_SCALE( 1), SNR_SCALE(3.0)},\n'
    '    {6,  -108,  3300, 3000, 2500,   6, 5000, SNR_SCALE( 3), SNR_SCALE(9.5)},\n'
    '    {7,  -112,  5871, 3500, 2500,  10, 5000, SNR_SCALE( 0), SNR_SCALE(8.5)},\n'
    '    {8,  -112,  7605, 3500, 2500,  11, 5000, SNR_SCALE( 0), SNR_SCALE(8.5)},\n'
    '    {9,  -115, 10798, 4000, 2500,   0, 5000, SNR_SCALE(-1), SNR_SCALE(6.5)},\n'
    '    {10, -112,  5871, 3500, 2500,  10, 5000, SNR_SCALE( 0), SNR_SCALE(8.5)},\n'
    '    {11, -112,  7456, 3500, 2500,  11, 5000, SNR_SCALE( 0), SNR_SCALE(8.5)},\n'
    '    {12, -111,  3216, 3500, 2500, 600, 5000, SNR_SCALE( 1), SNR_SCALE(3.0)},\n'
    '    {13, -111,  4240, 3500, 2500, 600, 5000, SNR_SCALE( 1), SNR_SCALE(3.0)}};\n'
    '#endif\n'
    '\n'
)

SX126X_SUPPORTED_RATES = (
    "#if defined(RADIO_SX126X)\n"
    "bool isSupportedRFRate(uint8_t index)\n"
    "{\n"
    "    // Only the sub-GHz LoRa rows this radio can demodulate are selectable;\n"
    "    // the other slots exist to keep OTA sync rate indices aligned.\n"
    "    return get_elrs_airRateConfig(index)->radio_type == RADIO_TYPE_SX126x_LORA;\n"
    "}\n"
    "#endif\n"
    "\n"
)

# RADIO_SX126X: a fourth radio type (SX1261/SX1262, sub-GHz LoRa) for the
# Heltec WiFi LoRa 32 V4. Every hook mirrors an existing RADIO_LR1121 or
# RADIO_SX127X branch; the driver itself lives in lib/SX126xDriver.
RADIO_SX126X_EDITS = {
    "include/common.h": [
        ('#elif defined(RADIO_LR1121)\n#include "LR1121Driver.h"\n#elif defined(RADIO_SX128X)\n#include "SX1280Driver.h"\n',
         '#elif defined(RADIO_LR1121)\n#include "LR1121Driver.h"\n#elif defined(RADIO_SX126X)\n#include "SX126xDriver.h"\n#elif defined(RADIO_SX128X)\n#include "SX1280Driver.h"\n'),
        ("    RADIO_TYPE_SX128x_LORA,\n    RADIO_TYPE_SX128x_FLRC,\n};",
         "    RADIO_TYPE_SX128x_LORA,\n    RADIO_TYPE_SX128x_FLRC,\n    RADIO_TYPE_SX126x_LORA,\n};"),
        ("#elif defined(RADIO_SX128X)\n#define RATE_MAX 10     // 2xFLRC + 2xDVDA + 4xLoRa + 2xFullRes",
         "#elif defined(RADIO_SX126X)\n"
         "#define RATE_MAX 14     // index-aligned with SX127x (0-5) and LR1121 (6-13); sub-GHz LoRa only\n"
         "#define RATE_BINDING RATE_LORA_50HZ\n"
         "\n"
         "extern SX126xDriver Radio;\n"
         "\n"
         "#elif defined(RADIO_SX128X)\n#define RATE_MAX 10     // 2xFLRC + 2xDVDA + 4xLoRa + 2xFullRes"),
        ("#if defined(RADIO_LR1121)\nbool isSupportedRFRate(uint8_t index);",
         "#if defined(RADIO_LR1121) || defined(RADIO_SX126X)\nbool isSupportedRFRate(uint8_t index);"),
    ],
    "include/targets.h": [
        ("#elif defined(RADIO_SX127X) || defined(RADIO_LR1121)\n#if !(defined(Regulatory_Domain_AU_915)",
         "#elif defined(RADIO_SX127X) || defined(RADIO_LR1121) || defined(RADIO_SX126X)\n#if !(defined(Regulatory_Domain_AU_915)"),
    ],
    "src/common.cpp": [
        ('#if defined(RADIO_SX128X)\n\n#include "SX1280Driver.h"\nSX1280Driver DMA_ATTR Radio;',
         SX126X_RATE_TABLE + '#if defined(RADIO_SX128X)\n\n#include "SX1280Driver.h"\nSX1280Driver DMA_ATTR Radio;'),
        ("#if defined(RADIO_LR1121)\nbool isSupportedRFRate(uint8_t index)\n{",
         SX126X_SUPPORTED_RATES + "#if defined(RADIO_LR1121)\nbool isSupportedRFRate(uint8_t index)\n{"),
    ],
    "lib/FHSS/FHSS.h": [
        ("#elif defined(RADIO_LR1121)\n#define FreqCorrectionMax ((int32_t)(100000/FREQ_STEP)) // TODO - This needs checking !!!\n",
         "#elif defined(RADIO_LR1121)\n#define FreqCorrectionMax ((int32_t)(100000/FREQ_STEP)) // TODO - This needs checking !!!\n"
         "#elif defined(RADIO_SX126X)\n#define FreqCorrectionMax ((int32_t)100000) // Hz units; no AFC on this radio\n"),
        ("#if defined(RADIO_LR1121)\n#define FREQ_HZ_TO_REG_VAL(freq) (freq)\n#define FREQ_SPREAD_SCALE 1",
         "#if defined(RADIO_LR1121) || defined(RADIO_SX126X)\n#define FREQ_HZ_TO_REG_VAL(freq) (freq)\n#define FREQ_SPREAD_SCALE 1"),
    ],
    "lib/FHSS/FHSS.cpp": [
        ('#if defined(RADIO_SX127X) || defined(RADIO_LR1121)\n\n#if defined(RADIO_LR1121)\n#include "LR1121Driver.h"\n#else\n#include "SX127xDriver.h"\n#endif',
         '#if defined(RADIO_SX127X) || defined(RADIO_LR1121) || defined(RADIO_SX126X)\n\n#if defined(RADIO_LR1121)\n#include "LR1121Driver.h"\n#elif defined(RADIO_SX126X)\n#include "SX126xDriver.h"\n#else\n#include "SX127xDriver.h"\n#endif'),
    ],
    "lib/CONFIG/config.cpp": [
        ("#if defined(RADIO_SX127X) || defined(RADIO_LR1121)\n    if (rateV6 == 0)",
         "#if defined(RADIO_SX127X) || defined(RADIO_LR1121) || defined(RADIO_SX126X)\n    if (rateV6 == 0)"),
    ],
    "lib/DEVICE/device.cpp": [
        ('#elif defined(RADIO_SX128X)\n#include "SX1280Driver.h"\n#else\n#error Invalid radio configuration!',
         '#elif defined(RADIO_SX126X)\n#include "SX126xDriver.h"\n#elif defined(RADIO_SX128X)\n#include "SX1280Driver.h"\n#else\n#error Invalid radio configuration!'),
    ],
    "lib/WIFI/devWIFI.cpp": [
        ('#if defined(RADIO_LR1121)\n  json["radio-type"] = "LR1121";\n  json["has-sub-ghz"] = true;\n#endif',
         '#if defined(RADIO_LR1121)\n  json["radio-type"] = "LR1121";\n  json["has-sub-ghz"] = true;\n#endif\n'
         '#if defined(RADIO_SX126X)\n  json["radio-type"] = "SX126X";\n  json["has-sub-ghz"] = true;\n#endif'),
    ],
    "python/build_html.py": [
        ("    has_sub_ghz = '-DRADIO_SX127X=1' in env['BUILD_FLAGS'] or '-DRADIO_LR1121=1' in env['BUILD_FLAGS']",
         "    has_sub_ghz = '-DRADIO_SX127X=1' in env['BUILD_FLAGS'] or '-DRADIO_LR1121=1' in env['BUILD_FLAGS'] or '-DRADIO_SX126X=1' in env['BUILD_FLAGS']"),
        ("    elif '-DRADIO_LR1121=1' in env['BUILD_FLAGS']:\n        chip = 'LR1121'",
         "    elif '-DRADIO_LR1121=1' in env['BUILD_FLAGS']:\n        chip = 'LR1121'\n"
         "    elif '-DRADIO_SX126X=1' in env['BUILD_FLAGS']:\n        chip = 'SX126X'"),
    ],
    "python/build_flags.py": [
        ("if '-DRADIO_SX127X=1' in build_flags or '-DRADIO_LR1121=1' in build_flags:",
         "if '-DRADIO_SX127X=1' in build_flags or '-DRADIO_LR1121=1' in build_flags or '-DRADIO_SX126X=1' in build_flags:"),
    ],
}

# Text written by earlier versions of this integration, rewritten to the
# current form before the anchors are checked. Keyed by upstream file.
UPGRADES = {
    "src/common.cpp": [(LEGACY_BAND_FILTER, BAND_FILTER)],
}
for _patchset in (WIFI_CONFIG_UPGRADES, WIFI_FALLBACK_UPGRADES):
    for _relative, _pairs in _patchset.items():
        UPGRADES.setdefault(_relative, []).extend(_pairs)


def apply_edits(path, edits, upgrades=()):
    with open(path, encoding="utf-8") as source:
        text = source.read()
    # Upgrade checkouts integrated with the previous 2.4 GHz-only port.
    if os.path.basename(path) == "common.cpp":
        text = text.replace(LEGACY_BAND_FILTER, BAND_FILTER)
    for old, new in upgrades:
        if old in text:
            text = text.replace(old, new)
            print(f"{os.path.basename(path)}: upgraded an earlier edit")
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


def combined_edits():
    """Every (file -> edits) patch set merged in application order."""
    combined = {"src/rx_main.cpp": list(EDITS)}
    for patchset in (EXTRA_EDITS, RADIO_SX126X_EDITS, WIFI_CONFIG_EDITS, WIFI_FALLBACK_EDITS):
        for relative, edits in patchset.items():
            combined.setdefault(relative, []).extend(edits)
    return combined


def main():
    if len(sys.argv) != 2:
        print(__doc__); sys.exit(1)
    elrs_src = os.path.abspath(sys.argv[1])            # .../ExpressLRS/src
    dst_src = os.path.join(elrs_src, "src")            # .../ExpressLRS/src/src
    rx_main = os.path.join(dst_src, "rx_main.cpp")
    if not os.path.isfile(rx_main):
        print(f"ERROR: {rx_main} not found. Point me at ExpressLRS/src"); sys.exit(2)

    # Validate every anchor before changing any upstream file.
    pending = {}
    for relative, edits in combined_edits().items():
        path = os.path.join(elrs_src, relative)
        pending[path] = apply_edits(path, edits, UPGRADES.get(relative, ()))

    # copy sources
    for f in SRC_FILES:
        shutil.copy2(os.path.join(HERE, "src", f), os.path.join(dst_src, f))
        print(f"copied  src/{f}")
    for library, files in LIB_FILES.items():
        target = os.path.join(elrs_src, "lib", library)
        os.makedirs(target, exist_ok=True)
        for f in files:
            shutil.copy2(os.path.join(HERE, "lib", library, f), os.path.join(target, f))
            print(f"copied  lib/{library}/{f}")
    for f in BOARD_FILES:
        shutil.copy2(os.path.join(HERE, "boards", f), os.path.join(elrs_src, "boards", f))
        print(f"copied  boards/{f}")

    for path, text in pending.items():
        with open(path, "w", encoding="utf-8", newline="\n") as target:
            target.write(text)
    print("\nDone. Copy the target INI for your radio into the ELRS targets/ directory:")
    print("  LR1121: targets/t3s3_lr1121_sniffer.ini")
    print("    pio run -e T3S3_Sniffer_LR1121_RX_USB")
    print("  SX1262: targets/heltec_v4_sniffer.ini")
    print("    pio run -e Heltec_V4_Sniffer_900_RX_USB")
    print("  SX1280: targets/t3s3_sniffer.ini")
    print("    pio run -e T3S3_Sniffer_2400_RX_Serial")

if __name__ == "__main__":
    main()
