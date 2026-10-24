#pragma once

#include "targets.h"
#include "SX126x_Regs.h"
#include "SX126x_hal.h"
#include "SX12xxDriverCommon.h"

#define RADIO_SNR_SCALE 4 // LastPacketSNRRaw holds the raw SnrPkt value, 1/4 dB units

// Receive-only SX1261/SX1262 driver for the ExpressLRS telemetry sniffer.
// Single radio, LoRa only; implements the subset of the SX1280/LR1121 driver
// contract that rx_main.cpp, POWERMGNT and devWIFI use.
class SX126xDriver: public SX12xxDriverCommon
{
public:
    static SX126xDriver *instance;

    ///////////Radio Variables////////
    uint32_t timeout; // SetRx timeout in 15.625 us ticks, SX126X_RX_TIMEOUT_CONTINUOUS for none

    ////////////////Configuration Functions/////////////
    SX126xDriver();
    bool Begin(uint32_t minimumFrequency, uint32_t maximumFrequency);
    void End();
    void SetTxIdleMode() { SetMode(SX126X_MODE_FS, SX12XX_Radio_1); }; // set Idle mode used when switching from RX to TX
    void Config(uint8_t bw, uint8_t sf, uint8_t cr, uint32_t freq,
                uint8_t PreambleLength, bool InvertIQ, uint8_t PayloadLength, uint32_t interval);
    void SetFrequencyHz(uint32_t freq, SX12XX_Radio_Number_t radioNumber);
    void SetFrequencyReg(uint32_t freq, SX12XX_Radio_Number_t radioNumber = SX12XX_Radio_All);
    void SetRxTimeoutUs(uint32_t interval);
    void SetOutputPower(int8_t power);
    void startCWTest(uint32_t freq, SX12XX_Radio_Number_t radioNumber);

    bool GetFrequencyErrorbool() { return false; }
    bool FrequencyErrorAvailable() const { return false; }

    void TXnb(uint8_t * data, uint8_t size, SX12XX_Radio_Number_t radioNumber);
    void RXnb(SX126x_RadioOperatingModes_t rxMode = SX126X_MODE_RX);

    uint16_t GetIrqStatus(SX12XX_Radio_Number_t radioNumber);
    void ClearIrqStatus(uint16_t irqMask, SX12XX_Radio_Number_t radioNumber);

    uint8_t GetRxBufferAddr(SX12XX_Radio_Number_t radioNumber);
    int8_t GetRssiInst(SX12XX_Radio_Number_t radioNumber);
    void GetLastPacketStats();
    SX126x_RadioOperatingModes_t GetOpMode() const { return currOpmode; }

    // Diagnostics (read from the main loop while not receiving)
    uint8_t GetStatus();        // 13.5.1 GetStatus: bits 6:4 chip mode, 3:1 command status
    uint16_t GetDeviceErrors(); // 13.5.8 GetDeviceErrors
    uint32_t GetBusyTimeouts() const;
    volatile uint32_t irqCount;    // DIO1 interrupts handled
    volatile uint32_t rxDoneCount; // of which RX done

private:
    // constant used for no power change pending
    // must not be a valid power value (-9..22 dBm)
    static const int8_t PWRPENDING_NONE = 0x7f;

    SX126x_RadioOperatingModes_t currOpmode;
    int8_t pwrCurrent;
    int8_t pwrPending;
    SX126x_RadioOperatingModes_t fallBackMode;

    void SetMode(SX126x_RadioOperatingModes_t OPmode, SX12XX_Radio_Number_t radioNumber);
    void SetFIFOaddr(uint8_t txBaseAddr, uint8_t rxBaseAddr);
    void CalibrateImage(uint32_t minimumFrequency, uint32_t maximumFrequency);

    // LoRa functions
    void ConfigModParamsLoRa(uint8_t bw, uint8_t sf, uint8_t cr);
    void SetPacketParamsLoRa(uint8_t PreambleLength, SX126x_RadioLoRaPacketLengthsModes_t HeaderType,
                             uint8_t PayloadLength, uint8_t InvertIQ);
    void SetLoRaSyncWord(uint16_t syncWord);

    void SetDioIrqParams(uint16_t irqMask,
                         uint16_t dio1Mask = SX126X_IRQ_RADIO_NONE,
                         uint16_t dio2Mask = SX126X_IRQ_RADIO_NONE,
                         uint16_t dio3Mask = SX126X_IRQ_RADIO_NONE);

    static void IsrCallback_1();
    void IsrCallback();
    bool RXnbISR(uint16_t irqStatus); // ISR for non-blocking RX routine
    void TXnbISR(); // ISR for non-blocking TX routine
    void CommitOutputPower();
};
