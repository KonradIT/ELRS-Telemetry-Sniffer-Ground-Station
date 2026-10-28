"""Exercise the SX126x receive-only driver on the host with a scripted SPI bus.

The ExpressLRS platform headers are replaced by small stubs that record every
SPI frame and GPIO call, so the command sequences the Heltec WiFi LoRa 32 V4
radio will see can be checked without hardware.
"""

from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


FIRMWARE = Path(__file__).resolve().parents[1]
DRIVER = FIRMWARE / "lib" / "SX126xDriver"

STUBS = {
    "Arduino.h": r"""
#pragma once
#include <stdint.h>
#include <string.h>
#define HIGH 1
#define LOW 0
#define INPUT 0
#define OUTPUT 1
#define RISING 1
#define constrain(amt, low, high) ((amt) < (low) ? (low) : ((amt) > (high) ? (high) : (amt)))
typedef int gpio_num_t;
void pinMode(int pin, int mode);
void digitalWrite(int pin, int value);
int digitalRead(int pin);
uint32_t micros();
uint32_t millis();
void delay(uint32_t ms);
void delayMicroseconds(uint32_t us);
int digitalPinToInterrupt(int pin);
void attachInterrupt(int pin, void (*isr)(), int mode);
void detachInterrupt(int pin);
void gpio_pullup_en(gpio_num_t pin);
""",
    "targets.h": r"""
#pragma once
#include "Arduino.h"
#define ICACHE_RAM_ATTR
#define WORD_ALIGNED_ATTR
#define WORD_PADDED(size) (((size) + 3) & ~3)
#define DMA_ATTR
#define UNDEF_PIN (-1)
#define PLATFORM_ESP32 1
#define GPIO_PIN_BUSY 13
#define GPIO_PIN_DIO1 14
#define GPIO_PIN_NSS 8
#define GPIO_PIN_SCK 9
#define GPIO_PIN_MISO 11
#define GPIO_PIN_MOSI 10
#define GPIO_PIN_RST 12
#define GPIO_PIN_PA_ENABLE 7
#define GPIO_PIN_RX_ENABLE 2
#define GPIO_PIN_TX_ENABLE 5
#define GPIO_PIN_RX_ENABLE_2 UNDEF_PIN
#define GPIO_PIN_TX_ENABLE_2 UNDEF_PIN
#define USE_HARDWARE_DCDC
#define OPT_USE_HARDWARE_DCDC true
""",
    "logging.h": r"""
#pragma once
#define DBGLN(...)
#define DBG(...)
""",
    "SX12xxDriverCommon.h": r"""
#pragma once
#include <targets.h>
typedef uint8_t SX12XX_Radio_Number_t;
enum { SX12XX_Radio_NONE = 0, SX12XX_Radio_1 = 1, SX12XX_Radio_2 = 2, SX12XX_Radio_All = 3 };
class SX12xxDriverCommon
{
public:
    typedef uint8_t rx_status;
    enum { SX12XX_RX_OK = 0, SX12XX_RX_CRC_FAIL = 1 << 0, SX12XX_RX_TIMEOUT = 1 << 1, SX12XX_RX_SYNCWORD_ERROR = 1 << 2 };
    SX12xxDriverCommon(): RXdoneCallback(nullCallbackRx), TXdoneCallback(nullCallbackTx) {}
    static bool nullCallbackRx(rx_status) { return false; }
    static void nullCallbackTx() {}
    bool (*RXdoneCallback)(rx_status);
    void (*TXdoneCallback)();
    #define RXBuffSize 16
    uint8_t RXdataBuffer[RXBuffSize];
    uint32_t currFreq;
    uint8_t PayloadLength;
    bool IQinverted;
    SX12XX_Radio_Number_t processingPacketRadio;
    SX12XX_Radio_Number_t lastSuccessfulPacketRadio;
    SX12XX_Radio_Number_t transmittingRadio;
    SX12XX_Radio_Number_t GetProcessingPacketRadio() { return processingPacketRadio; }
    SX12XX_Radio_Number_t GetLastSuccessfulPacketRadio() { return lastSuccessfulPacketRadio; }
    SX12XX_Radio_Number_t GetLastTransmitRadio() { return transmittingRadio; }
    int8_t LastPacketRSSI;
    int8_t LastPacketRSSI2;
    int8_t LastPacketSNRRaw;
    int8_t FuzzySNRThreshold;
    bool isFirstRxIrq = true;
protected:
    void RemoveCallbacks() { RXdoneCallback = nullCallbackRx; TXdoneCallback = nullCallbackTx; }
};
""",
    "RFAMP_hal.h": r"""
#pragma once
#include "SX12xxDriverCommon.h"
class RFAMP_hal
{
public:
    static RFAMP_hal *instance;
    RFAMP_hal() { instance = this; }
    void init() { inits++; }
    void TXenable(SX12XX_Radio_Number_t) { txEnables++; }
    void RXenable() { rxEnables++; }
    void TXRXdisable() { disables++; }
    int inits = 0, txEnables = 0, rxEnables = 0, disables = 0;
};
""",
    "SPIEx.h": r"""
#pragma once
#include <stdint.h>
#include <deque>
#include <map>
#include <vector>
#include "targets.h"
typedef std::vector<uint8_t> Frame;
class SPIExClass
{
public:
    std::vector<Frame> log;                      // bytes clocked out, one entry per transfer
    std::map<uint8_t, std::deque<Frame>> replies; // scripted MISO frames per opcode
    uint32_t frequency = 0;
    void begin(int, int, int, int) {}
    void end() {}
    void setFrequency(uint32_t f) { frequency = f; }
    void setHwCs(bool) {}
    int bus() { return 0; }
    void expect(uint8_t opcode, Frame frame) { replies[opcode].push_back(frame); }
    void read(uint8_t cs, uint8_t *data, uint32_t size)
    {
        (void)cs;
        log.push_back(Frame(data, data + size));
        auto pending = replies.find(data[0]);
        if (pending != replies.end() && !pending->second.empty())
        {
            Frame frame = pending->second.front();
            pending->second.pop_front();
            for (uint32_t i = 0; i < size && i < frame.size(); i++) data[i] = frame[i];
        }
        else
        {
            memset(data, 0, size);
        }
    }
    void write(uint8_t cs, uint8_t *data, uint32_t size) { read(cs, data, size); }
};
extern SPIExClass SPIEx;
void spiEnableSSPins(int bus, uint8_t mask);
""",
}

HARNESS = r"""
#include <cassert>
#include <cstdio>
#include <vector>
#include "SX126x.h"
#include "RFAMP_hal.h"
#include <SPIEx.h>

SPIExClass SPIEx;
RFAMP_hal *RFAMP_hal::instance = nullptr;
static int busyLevel = LOW;
static void (*dioIsr)() = nullptr;
void pinMode(int, int) {}
void digitalWrite(int, int) {}
int digitalRead(int pin) { return pin == GPIO_PIN_BUSY ? busyLevel : LOW; }
static uint32_t fakeMicros = 0;
uint32_t micros() { fakeMicros += 10; return fakeMicros; }
uint32_t millis() { return fakeMicros / 1000; }
void delay(uint32_t ms) { fakeMicros += ms * 1000; }
void delayMicroseconds(uint32_t us) { fakeMicros += us; }
int digitalPinToInterrupt(int pin) { return pin; }
void attachInterrupt(int, void (*isr)(), int) { dioIsr = isr; }
void detachInterrupt(int) { dioIsr = nullptr; }
void gpio_pullup_en(gpio_num_t) {}
void spiEnableSSPins(int, uint8_t) {}

static bool has(const Frame &frame) {
    for (const Frame &sent : SPIEx.log) if (sent == frame) return true;
    return false;
}
static int indexOf(uint8_t opcode) {
    for (size_t i = 0; i < SPIEx.log.size(); i++) if (SPIEx.log[i][0] == opcode) return (int)i;
    return -1;
}
static bool rxDone = false;
static uint8_t rxStatus = 0xFF;
static bool onRxDone(uint8_t status) { rxDone = true; rxStatus = status; return true; }
static bool txDone = false;
static void onTxDone() { txDone = true; }

int main()
{
    SX126xDriver radio;
    radio.RXdoneCallback = &onRxDone;
    radio.TXdoneCallback = &onTxDone;

    // Begin: the LoRa sync word registers answer with their reset default.
    SPIEx.expect(0x1D, {0, 0, 0, 0, 0x14, 0x24});
    assert(radio.Begin(865275000, 869575000));
    assert(dioIsr != nullptr);
    assert(SPIEx.frequency == 10000000);
    assert(RFAMP_hal::instance->inits == 1);
    assert(has({0x80, 0x00}));                               // STDBY_RC first
    assert(has({0x1D, 0x07, 0x40, 0x00, 0x00, 0x00}));       // sync word read request
    assert(has({0x97, 0x02, 0x00, 0x01, 0x40}));             // DIO3 TCXO 1.8 V, 5 ms
    assert(has({0x89, 0x7F}));                               // calibrate all blocks
    assert(has({0x07, 0x00, 0x00}));                         // clear device errors
    assert(has({0x96, 0x01}));                               // DC-DC regulator
    assert(has({0x93, 0x40}));                               // fall back to FS
    assert(has({0x8F, 0x00, 0x00}));                         // buffer base addresses
    assert(has({0x9D, 0x01}));                               // DIO2 as RF switch
    assert(has({0x0D, 0x08, 0xAC, 0x96}));                   // boosted RX gain
    assert(has({0x95, 0x04, 0x07, 0x00, 0x01}));             // SX1262 PA config
    assert(has({0x98, 0xD7, 0xDB}));                         // image calibration 863-870 MHz
    assert(indexOf(0x80) < indexOf(0x1D));
    assert(indexOf(0x97) < indexOf(0x89));                   // TCXO enabled before calibrating
    assert(indexOf(0x89) < indexOf(0x07));
    assert(indexOf(0x89) < indexOf(0x98));
    assert(radio.GetOpMode() == SX126X_MODE_STDBY_RC);

    // Config for ExpressLRS 100 Hz: BW 500 kHz, SF7, CR 4/7, implicit 8-byte
    // packets, 8-symbol preamble, continuous receive, standard IQ.
    SPIEx.log.clear();
    SPIEx.expect(0x1D, {0, 0, 0, 0, 0x09});                  // IQ polarity register, bit 2 clear
    radio.Config(SX126X_LORA_BW_500, SX126X_LORA_SF7, SX126X_LORA_CR_4_7, 868000000, 8, true, 8, 0);
    assert(has({0x80, 0x00}));
    assert(has({0x8A, 0x01}));                               // LoRa packet type
    assert(has({0x8B, 0x07, 0x06, 0x03, 0x00}));             // SF7, BW500, CR4/7, LDRO off
    assert(has({0x8C, 0x00, 0x08, 0x01, 0x08, 0x00, 0x00})); // preamble 8, implicit, 8 bytes, CRC off, standard IQ
    assert(has({0x0D, 0x07, 0x36, 0x0D}));                   // IQ polarity bit 2 set for standard IQ
    assert(has({0x0D, 0x07, 0x40, 0x14, 0x24}));             // private sync word, SX127x 0x12 equivalent
    assert(has({0x86, 0x36, 0x40, 0x00, 0x00}));             // 868 MHz = 0x36400000 PLL steps
    assert(has({0x08, 0x02, 0x63, 0x00, 0x03, 0x00, 0x00, 0x00, 0x00})); // IRQ mask, DIO1 = TX|RX done
    assert(has({0x02, 0xFF, 0xFF}));                         // IRQs cleared
    assert(indexOf(0x8A) < indexOf(0x8B));
    assert(indexOf(0x8B) < indexOf(0x8C));
    assert(indexOf(0x8C) < indexOf(0x86));
    assert(!has({0x82, 0xFF, 0xFF, 0xFF}));                  // not armed while configuring
    assert(radio.timeout == 0xFFFFFF);
    assert(radio.currFreq == 868000000);
    assert(radio.PayloadLength == 8);
    assert(!radio.IQinverted);

    // Arm the receiver: front end to RX, continuous SetRx.
    SPIEx.log.clear();
    radio.RXnb();
    assert(SPIEx.log.size() == 1 && SPIEx.log[0] == Frame({0x82, 0xFF, 0xFF, 0xFF}));
    assert(RFAMP_hal::instance->rxEnables == 1);
    assert(radio.GetOpMode() == SX126X_MODE_RX);

    // A hop while receiving pauses in STDBY_XOSC, retunes and re-arms.
    SPIEx.log.clear();
    radio.SetFrequencyReg(869575000);
    uint32_t reg = (uint32_t)(((uint64_t)869575000 << 25) / 32000000);
    Frame retune = {0x86, (uint8_t)(reg >> 24), (uint8_t)(reg >> 16), (uint8_t)(reg >> 8), (uint8_t)reg};
    assert(SPIEx.log.size() == 3);
    assert(SPIEx.log[0] == Frame({0x80, 0x01}));
    assert(SPIEx.log[1] == retune);
    assert(SPIEx.log[2] == Frame({0x82, 0xFF, 0xFF, 0xFF}));
    assert(radio.currFreq == 869575000);
    assert(radio.GetOpMode() == SX126X_MODE_RX);

    // DIO1 rises with RX done: read the 8-byte payload, deliver it, clear IRQs.
    SPIEx.log.clear();
    SPIEx.expect(0x12, {0, 0, 0x00, 0x02});                  // GetIrqStatus -> RX_DONE
    SPIEx.expect(0x13, {0, 0, 0x08, 0x00});                  // 8 bytes at offset 0
    SPIEx.expect(0x1E, {0, 0, 0, 0xA1, 0xB2, 0xC3, 0xD4, 0xE5, 0xF6, 0x07, 0x18});
    radio.isFirstRxIrq = true;
    dioIsr();
    const uint8_t payload[8] = {0xA1, 0xB2, 0xC3, 0xD4, 0xE5, 0xF6, 0x07, 0x18};
    assert(rxDone && rxStatus == SX12xxDriverCommon::SX12XX_RX_OK);
    assert(memcmp(radio.RXdataBuffer, payload, sizeof(payload)) == 0);
    assert(has({0x1E, 0x00, 0x00, 0, 0, 0, 0, 0, 0, 0, 0}));  // ReadBuffer from offset 0
    assert(has({0x02, 0xFF, 0xFF}));
    assert(!radio.isFirstRxIrq);
    assert(!has({0x0D, 0x09, 0x02, 0x00}));                  // no RTC workaround in continuous mode
    assert(radio.GetOpMode() == SX126X_MODE_RX);             // continuous receive keeps listening
    assert(radio.GetProcessingPacketRadio() == SX12XX_Radio_1);

    // Packet statistics: RssiPkt 200 -> -100 dBm, SnrPkt -8 -> -2 dB raw units.
    SPIEx.expect(0x14, {0, 0, 200, (uint8_t)-8, 190});
    radio.GetLastPacketStats();
    assert(radio.LastPacketRSSI == -100);
    assert(radio.LastPacketSNRRaw == -8);
    SPIEx.expect(0x15, {0, 0, 150});
    assert(radio.GetRssiInst(SX12XX_Radio_1) == -75);

    // The passive build never transmits: TXnb re-arms receive, CW is suppressed.
    SPIEx.log.clear();
    uint8_t data[8] = {1, 2, 3, 4, 5, 6, 7, 8};
    radio.TXnb(data, sizeof(data), SX12XX_Radio_1);
    assert(has({0x82, 0xFF, 0xFF, 0xFF}));
    for (const Frame &sent : SPIEx.log) assert(sent[0] != 0x83 && sent[0] != 0x0E && sent[0] != 0xD1);
    assert(RFAMP_hal::instance->txEnables == 0);
    SPIEx.log.clear();
    radio.startCWTest(868000000, SX12XX_Radio_1);
    assert(SPIEx.log.empty());

    // A timed receive window applies the implicit-header timeout workaround.
    radio.SetRxTimeoutUs(20000);
    assert(radio.timeout == 1280);                           // 20 ms / 15.625 us
    SPIEx.log.clear();
    radio.RXnb();
    assert(has({0x82, 0x00, 0x05, 0x00}));
    SPIEx.expect(0x12, {0, 0, 0x00, 0x02});
    SPIEx.expect(0x1D, {0, 0, 0, 0, 0x00});                  // event mask register
    SPIEx.expect(0x13, {0, 0, 0x08, 0x00});
    dioIsr();
    assert(has({0x0D, 0x09, 0x02, 0x00}));                   // RTC stopped
    assert(has({0x0D, 0x09, 0x44, 0x02}));                   // timeout event cleared
    assert(radio.GetOpMode() == SX126X_MODE_FS);             // single receive fell back to FS

    // Begin fails when the radio does not answer, and FCC915 calibrates its band.
    SPIEx.log.clear();
    SPIEx.expect(0x1D, {0, 0, 0, 0, 0x00, 0x00});
    assert(!radio.Begin(865275000, 869575000));
    SPIEx.expect(0x1D, {0, 0, 0, 0, 0x14, 0x24});
    assert(radio.Begin(903500000, 926900000));
    assert(has({0x98, 0xE1, 0xE9}));

    // Diagnostics: interrupt counters and the status/error readbacks.
    assert(radio.irqCount == 2 && radio.rxDoneCount == 2);
    assert(radio.GetBusyTimeouts() == 0);
    SPIEx.expect(0xC0, {0, 0x52});                           // chip mode 5 = RX, command status 1
    assert(radio.GetStatus() == 0x52);
    SPIEx.expect(0x17, {0, 0, 0x00, 0x20});                  // GetDeviceErrors: XOSC start error
    assert(radio.GetDeviceErrors() == 0x0020);

    // A stuck BUSY line times out instead of hanging forever, and is counted.
    busyLevel = HIGH;
    uint32_t before = fakeMicros;
    radio.ClearIrqStatus(SX126X_IRQ_RADIO_ALL, SX12XX_Radio_1);
    assert(fakeMicros - before >= 2000 && fakeMicros - before < 20000);
    assert(radio.GetBusyTimeouts() == 1);
    busyLevel = LOW;
    assert(!txDone);

    puts("OK");
    return 0;
}
"""


class SX126xDriverTests(unittest.TestCase):
    def test_command_sequences_on_scripted_spi_bus(self):
        compiler = shutil.which("g++") or shutil.which("clang++")
        if not compiler:
            self.skipTest("A native C++ compiler is required for the driver test")
        with tempfile.TemporaryDirectory() as directory:
            stubs = Path(directory) / "stubs"
            stubs.mkdir()
            for name, content in STUBS.items():
                (stubs / name).write_text(content, encoding="utf-8")
            harness = Path(directory) / "harness.cpp"
            harness.write_text(HARNESS, encoding="utf-8")
            executable = Path(directory) / "sx126x_test.exe"
            # MinGW's libstdc++ DLL is usually not on PATH; link it in.
            static = ["-static-libstdc++", "-static-libgcc"] if sys.platform == "win32" else []
            command = [
                compiler, "-std=c++17", "-Wall", "-Wextra", "-Werror", "-Wno-vla", *static,
                "-DRADIO_SX126X=1", "-DGHOST_SNIFFER=1", "-DGHOST_NO_TX=1",
                "-DGHOST_SX126X_TCXO=2", "-DGHOST_SX126X_DIO2_RFSW=1",
                "-I", str(stubs), "-I", str(DRIVER),
                str(DRIVER / "SX126x.cpp"), str(DRIVER / "SX126x_hal.cpp"), str(harness),
                "-o", str(executable),
            ]
            compile_result = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(compile_result.returncode, 0, compile_result.stderr)
            run_result = subprocess.run([str(executable)], capture_output=True, text=True)
            self.assertEqual(run_result.returncode, 0, run_result.stderr + run_result.stdout)
            self.assertIn("OK", run_result.stdout)

    def test_driver_sources_are_guarded_for_other_radios(self):
        for name in ("SX126x.cpp", "SX126x_hal.cpp"):
            text = (DRIVER / name).read_text(encoding="utf-8")
            self.assertIn("#if defined(RADIO_SX126X) && !defined(UNIT_TEST)", text, name)
            self.assertTrue(text.rstrip().endswith("#endif // RADIO_SX126X && !UNIT_TEST"), name)


if __name__ == "__main__":
    unittest.main()
