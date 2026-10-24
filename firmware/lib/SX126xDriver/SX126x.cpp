// =====================================================================
// Receive-only SX1261/SX1262 driver for the ExpressLRS 3.5.6 telemetry
// sniffer (Heltec WiFi LoRa 32 V4). Follows the SX1280 and LR1121 ExpressLRS
// drivers: same SX12xxDriverCommon contract, DIO1 interrupt on RX done,
// continuous receive between hops, RFAMP front-end control. LoRa only.
//
// Build options supplied by the target INI:
//   GHOST_SX126X_TCXO=<code>   DIO3 drives the module TCXO (0x02 = 1.8 V)
//   GHOST_SX126X_DIO2_RFSW=1   DIO2 is the RF switch / FEM TX-RX select
//   GHOST_NO_TX                every transmit path becomes a receive request
// =====================================================================
#if defined(RADIO_SX126X) && !defined(UNIT_TEST)

#include "SX126x_Regs.h"
#include "SX126x_hal.h"
#include "SX126x.h"
#include "logging.h"
#include "RFAMP_hal.h"

static SX126xHal hal;
static RFAMP_hal RFAMP;
SX126xDriver *SX126xDriver::instance = NULL;

#ifdef USE_HARDWARE_DCDC
    #ifndef OPT_USE_HARDWARE_DCDC
        #define OPT_USE_HARDWARE_DCDC true
    #endif
#else
    #define OPT_USE_HARDWARE_DCDC false
#endif

SX126xDriver::SX126xDriver(): SX12xxDriverCommon()
{
    instance = this;
    timeout = SX126X_RX_TIMEOUT_CONTINUOUS;
    currOpmode = SX126X_MODE_SLEEP;
    pwrCurrent = PWRPENDING_NONE;
    pwrPending = PWRPENDING_NONE;
    fallBackMode = SX126X_MODE_FS;
    lastSuccessfulPacketRadio = SX12XX_Radio_1;
    processingPacketRadio = SX12XX_Radio_1;
    transmittingRadio = SX12XX_Radio_1;
    PayloadLength = 8;
    IQinverted = false;
    currFreq = 868000000;
    irqCount = 0;
    rxDoneCount = 0;
}

void SX126xDriver::End()
{
    if (currOpmode != SX126X_MODE_SLEEP)
    {
        SetMode(SX126X_MODE_SLEEP, SX12XX_Radio_1);
    }
    hal.end();
    RFAMP.TXRXdisable();
    RemoveCallbacks();
    PayloadLength = 8; // Dummy default value which is overwritten during setup.
}

bool SX126xDriver::Begin(uint32_t minimumFrequency, uint32_t maximumFrequency)
{
    hal.init();
    hal.IsrCallback_1 = &SX126xDriver::IsrCallback_1;

    hal.reset();
    DBGLN("SX126x Begin");

    RFAMP.init();

    // Calibration, regulator and TCXO commands all require STDBY_RC.
    SetMode(SX126X_MODE_STDBY_RC, SX12XX_Radio_1);

    // Validate SPI: after reset the LoRa sync word registers hold 0x1424.
    uint8_t sync[2] = {0, 0};
    hal.ReadRegister(SX126X_REG_LORA_SYNC_WORD_MSB, sync, sizeof(sync), SX12XX_Radio_1);
    uint16_t syncWord = (uint16_t)((sync[0] << 8) | sync[1]);
    DBGLN("Read SX126x LoRa sync word: %x", syncWord);
    if (syncWord != SX126X_LORA_SYNC_WORD_PRIVATE && syncWord != SX126X_LORA_SYNC_WORD_PUBLIC)
    {
        DBGLN("SX126x failed to be detected.");
        return false;
    }

#if defined(GHOST_SX126X_TCXO)
    // DIO3 supplies the module TCXO. The oscillator cannot start before this
    // is configured, so the power-on calibration is repeated afterwards and
    // the resulting XOSC start error is cleared.
    uint8_t tcxo[4] = {GHOST_SX126X_TCXO, 0x00, 0x01, 0x40}; // 5 ms start-up in 15.625 us ticks
    hal.WriteCommand(SX126X_RADIO_SET_DIO3_AS_TCXO_CTRL, tcxo, sizeof(tcxo), SX12XX_Radio_1);
    hal.WriteCommand(SX126X_RADIO_CALIBRATE, (uint8_t)SX126X_CALIBRATE_ALL, SX12XX_Radio_1);
    delay(10); // TCXO start-up plus calibration exceed the HAL BUSY timeout
    uint8_t clearErrors[2] = {0x00, 0x00};
    hal.WriteCommand(SX126X_RADIO_CLR_DEVICE_ERRORS, clearErrors, sizeof(clearErrors), SX12XX_Radio_1);
#endif

    if (OPT_USE_HARDWARE_DCDC)
    {
        hal.WriteCommand(SX126X_RADIO_SET_REGULATORMODE, (uint8_t)SX126X_REGULATOR_DCDC, SX12XX_Radio_1); // DC-DC converter instead of LDO
    }

    // After a transmission or a timed single reception return to FS.
    fallBackMode = SX126X_MODE_FS;
    hal.WriteCommand(SX126X_RADIO_SET_RXTXFALLBACKMODE, (uint8_t)SX126X_FALLBACK_FS, SX12XX_Radio_1);

    SetFIFOaddr(0x00, 0x00);

#if defined(GHOST_SX126X_DIO2_RFSW)
    // DIO2 drives the RF switch or the FEM TX/RX select: high in TX only.
    hal.WriteCommand(SX126X_RADIO_SET_DIO2_AS_RF_SWITCH, (uint8_t)0x01, SX12XX_Radio_1);
#endif

    // Boosted LNA gain: best sensitivity for about 2 mA extra in receive.
    hal.WriteRegister(SX126X_REG_RX_GAIN, SX126X_RX_GAIN_BOOSTED, SX12XX_Radio_1);

    // SX1262 high power PA settings (table 13-21). The sniffer never transmits.
    uint8_t paConfig[4] = {0x04, 0x07, 0x00, 0x01};
    hal.WriteCommand(SX126X_RADIO_SET_PACONFIG, paConfig, sizeof(paConfig), SX12XX_Radio_1);

    CalibrateImage(minimumFrequency, maximumFrequency);

    // Force the next power update, and the lowest power
    pwrCurrent = PWRPENDING_NONE;
    SetOutputPower(SX126X_POWER_MIN);
    CommitOutputPower();

    return true;
}

void SX126xDriver::CalibrateImage(uint32_t minimumFrequency, uint32_t maximumFrequency)
{
    // 13.1.14 CalibrateImage with the band edges from table 9-2
    uint8_t cal[2];
    if (minimumFrequency >= 902000000)
    {
        cal[0] = 0xE1; cal[1] = 0xE9; // 902-928 MHz
    }
    else if (minimumFrequency >= 863000000)
    {
        cal[0] = 0xD7; cal[1] = 0xDB; // 863-870 MHz
    }
    else if (minimumFrequency >= 779000000)
    {
        cal[0] = 0xC1; cal[1] = 0xC5; // 779-787 MHz
    }
    else if (minimumFrequency >= 470000000)
    {
        cal[0] = 0x75; cal[1] = 0x81; // 470-510 MHz
    }
    else if (minimumFrequency >= 430000000)
    {
        cal[0] = 0x6B; cal[1] = 0x6F; // 430-440 MHz
    }
    else
    {
        cal[0] = (uint8_t)(((minimumFrequency / 1000000) - 1) / 4);
        cal[1] = (uint8_t)(1 + ((maximumFrequency / 1000000) + 1) / 4);
    }
    hal.WriteCommand(SX126X_RADIO_CALIBRATE_IMAGE, cal, sizeof(cal), SX12XX_Radio_1);
    delay(10); // image calibration starts the oscillator and exceeds the BUSY timeout
}

void SX126xDriver::startCWTest(uint32_t freq, SX12XX_Radio_Number_t radioNumber)
{
#if defined(GHOST_NO_TX)
    (void)freq;
    (void)radioNumber;
    return; // Passive sniffer: prohibit continuous-wave transmission too.
#else
    (void)radioNumber;
    SetMode(SX126X_MODE_STDBY_RC, SX12XX_Radio_1);
    SetFrequencyHz(freq, SX12XX_Radio_1);
    CommitOutputPower();
    RFAMP.TXenable(SX12XX_Radio_1);
    uint8_t dummy = 0; // we just need a buffer for the write command
    hal.WriteCommand(SX126X_RADIO_SET_TXCONTINUOUSWAVE, &dummy, 0, SX12XX_Radio_1);
    currOpmode = SX126X_MODE_TX;
#endif
}

void SX126xDriver::Config(uint8_t bw, uint8_t sf, uint8_t cr, uint32_t freq,
                          uint8_t PreambleLength, bool InvertIQ, uint8_t _PayloadLength, uint32_t interval)
{
    PayloadLength = _PayloadLength;
    // ExpressLRS sub-GHz links use standard IQ: the SX127x driver has no IQ
    // inversion and the LR1121 driver forces standard IQ below 1 GHz.
    (void)InvertIQ;
    IQinverted = false;

    SetMode(SX126X_MODE_STDBY_RC, SX12XX_Radio_1);
    hal.WriteCommand(SX126X_RADIO_SET_PACKETTYPE, (uint8_t)SX126X_PACKET_TYPE_LORA, SX12XX_Radio_1);
    DBG("Config LoRa ");
    ConfigModParamsLoRa(bw, sf, cr);
    SetPacketParamsLoRa(PreambleLength, SX126X_LORA_PACKET_FIXED_LENGTH, _PayloadLength, SX126X_LORA_IQ_NORMAL);
    SetLoRaSyncWord(SX126X_LORA_SYNC_WORD_PRIVATE);
    SetFrequencyHz(freq, SX12XX_Radio_1);
    SetRxTimeoutUs(interval);

    uint16_t dio1Mask = SX126X_IRQ_TX_DONE | SX126X_IRQ_RX_DONE;
    uint16_t irqMask = dio1Mask | SX126X_IRQ_HEADER_ERROR | SX126X_IRQ_CRC_ERROR | SX126X_IRQ_RX_TX_TIMEOUT;
    SetDioIrqParams(irqMask, dio1Mask);
    ClearIrqStatus(SX126X_IRQ_RADIO_ALL, SX12XX_Radio_1);
}

void SX126xDriver::SetRxTimeoutUs(uint32_t interval)
{
    if (interval)
    {
        uint32_t ticks = (uint32_t)(((uint64_t)interval * 1000U) / SX126X_RX_TIMEOUT_PERIOD_BASE_NANOS);
        timeout = ticks < SX126X_RX_TIMEOUT_CONTINUOUS ? ticks : (SX126X_RX_TIMEOUT_CONTINUOUS - 1);
    }
    else
    {
        timeout = SX126X_RX_TIMEOUT_CONTINUOUS; // no timeout, continuous mode
    }
}

/***
 * @brief: Schedule an output power change after the next transmit
 ***/
void SX126xDriver::SetOutputPower(int8_t power)
{
    int8_t pwrNew = constrain(power, SX126X_POWER_MIN, SX126X_POWER_MAX);

    if ((pwrPending == PWRPENDING_NONE && pwrCurrent != pwrNew) || pwrPending != pwrNew)
    {
        pwrPending = pwrNew;
        DBGLN("SetPower: %d", pwrPending);
    }
}

void ICACHE_RAM_ATTR SX126xDriver::CommitOutputPower()
{
    if (pwrPending == PWRPENDING_NONE)
        return;

    pwrCurrent = pwrPending;
    pwrPending = PWRPENDING_NONE;
    uint8_t buf[2] = {(uint8_t)pwrCurrent, (uint8_t)SX126X_RAMP_200_US};
    hal.WriteCommand(SX126X_RADIO_SET_TXPARAMS, buf, sizeof(buf), SX12XX_Radio_1);
}

void SX126xDriver::SetMode(SX126x_RadioOperatingModes_t OPmode, SX12XX_Radio_Number_t radioNumber)
{
    WORD_ALIGNED_ATTR uint8_t buf[4] = {0};

    switch (OPmode)
    {
    case SX126X_MODE_SLEEP:
        hal.WriteCommand(SX126X_RADIO_SET_SLEEP, (uint8_t)0x04, radioNumber); // warm start, keep configuration
        break;

    case SX126X_MODE_STDBY_RC:
        hal.WriteCommand(SX126X_RADIO_SET_STANDBY, (uint8_t)SX126X_STDBY_RC, radioNumber);
        break;

    // The TCXO keeps running in STDBY_XOSC, so hops avoid its start-up delay.
    case SX126X_MODE_STDBY_XOSC:
        hal.WriteCommand(SX126X_RADIO_SET_STANDBY, (uint8_t)SX126X_STDBY_XOSC, radioNumber);
        break;

    case SX126X_MODE_FS:
        hal.WriteCommand(SX126X_RADIO_SET_FS, buf, 0, radioNumber);
        break;

    case SX126X_MODE_RX:
        buf[0] = (uint8_t)(timeout >> 16);
        buf[1] = (uint8_t)(timeout >> 8);
        buf[2] = (uint8_t)timeout;
        hal.WriteCommand(SX126X_RADIO_SET_RX, buf, 3, radioNumber);
        break;

    case SX126X_MODE_RX_CONT:
        buf[0] = 0xFF;
        buf[1] = 0xFF;
        buf[2] = 0xFF;
        hal.WriteCommand(SX126X_RADIO_SET_RX, buf, 3, radioNumber);
        break;

    case SX126X_MODE_TX:
#if defined(GHOST_NO_TX)
        return; // Passive sniffer: the radio never enters transmit mode.
#else
        hal.WriteCommand(SX126X_RADIO_SET_TX, buf, 3, radioNumber); // no TX timeout
        break;
#endif

    case SX126X_MODE_CAD:
    default:
        return;
    }

    currOpmode = OPmode;
}

void SX126xDriver::ConfigModParamsLoRa(uint8_t bw, uint8_t sf, uint8_t cr)
{
    // Care must be taken to set the modulation parameters only after SetPacketType().
    // Every ExpressLRS 500 kHz rate keeps the symbol time below 16 ms, so low
    // data rate optimisation stays off, matching the SX127x and LR1121 links.
    WORD_ALIGNED_ATTR uint8_t rfparams[4] = {sf, bw, cr, SX126X_LORA_LDRO_OFF};
    hal.WriteCommand(SX126X_RADIO_SET_MODULATIONPARAMS, rfparams, sizeof(rfparams), SX12XX_Radio_1);
}

void SX126xDriver::SetPacketParamsLoRa(uint8_t PreambleLength, SX126x_RadioLoRaPacketLengthsModes_t HeaderType,
                                       uint8_t PayloadLength, uint8_t InvertIQ)
{
    uint8_t buf[6];
    buf[0] = 0x00;                // preamble length MSB
    buf[1] = PreambleLength;      // preamble length LSB
    buf[2] = HeaderType;
    buf[3] = PayloadLength;
    buf[4] = SX126X_LORA_CRC_OFF; // ExpressLRS validates its own OTA CRC
    buf[5] = InvertIQ;
    hal.WriteCommand(SX126X_RADIO_SET_PACKETPARAMS, buf, sizeof(buf), SX12XX_Radio_1);

    // 15.4 Optimizing the Inverted IQ Operation: bit 2 of 0x0736 is set for
    // standard IQ and cleared for inverted IQ.
    uint8_t polarity = hal.ReadRegister(SX126X_REG_IQ_POLARITY, SX12XX_Radio_1);
    polarity = InvertIQ ? (uint8_t)(polarity & ~0x04) : (uint8_t)(polarity | 0x04);
    hal.WriteRegister(SX126X_REG_IQ_POLARITY, polarity, SX12XX_Radio_1);
}

void SX126xDriver::SetLoRaSyncWord(uint16_t syncWord)
{
    uint8_t buf[2] = {(uint8_t)(syncWord >> 8), (uint8_t)syncWord};
    hal.WriteRegister(SX126X_REG_LORA_SYNC_WORD_MSB, buf, sizeof(buf), SX12XX_Radio_1);
}

void ICACHE_RAM_ATTR SX126xDriver::SetFrequencyHz(uint32_t freq, SX12XX_Radio_Number_t radioNumber)
{
    // 13.4.1 SetRfFrequency: RF frequency = Freq * 32 MHz / 2^25
    uint32_t regfreq = (uint32_t)(((uint64_t)freq << 25) / SX126X_XTAL_FREQ);
    WORD_ALIGNED_ATTR uint8_t buf[4];
    buf[0] = (uint8_t)(regfreq >> 24);
    buf[1] = (uint8_t)(regfreq >> 16);
    buf[2] = (uint8_t)(regfreq >> 8);
    buf[3] = (uint8_t)regfreq;

    // A frequency change is only guaranteed outside receive mode. Hops happen
    // between packets, so pause in STDBY_XOSC (the TCXO keeps running) and
    // re-arm the receiver on the new channel.
    SX126x_RadioOperatingModes_t const previousMode = currOpmode;
    bool const receiving = previousMode == SX126X_MODE_RX || previousMode == SX126X_MODE_RX_CONT;
    if (receiving)
    {
        SetMode(SX126X_MODE_STDBY_XOSC, radioNumber);
    }

    hal.WriteCommand(SX126X_RADIO_SET_RFFREQUENCY, buf, sizeof(buf), radioNumber);
    currFreq = freq;

    if (receiving)
    {
        SetMode(previousMode, radioNumber);
    }
}

void ICACHE_RAM_ATTR SX126xDriver::SetFrequencyReg(uint32_t freq, SX12XX_Radio_Number_t radioNumber)
{
    (void)radioNumber;
    // FHSS supplies Hz for this radio (FREQ_HZ_TO_REG_VAL is the identity).
    SetFrequencyHz(freq, SX12XX_Radio_1);
}

void SX126xDriver::SetFIFOaddr(uint8_t txBaseAddr, uint8_t rxBaseAddr)
{
    uint8_t buf[2] = {txBaseAddr, rxBaseAddr};
    hal.WriteCommand(SX126X_RADIO_SET_BUFFERBASEADDRESS, buf, sizeof(buf), SX12XX_Radio_1);
}

void SX126xDriver::SetDioIrqParams(uint16_t irqMask, uint16_t dio1Mask, uint16_t dio2Mask, uint16_t dio3Mask)
{
    uint8_t buf[8];
    buf[0] = (uint8_t)((irqMask >> 8) & 0x00FF);
    buf[1] = (uint8_t)(irqMask & 0x00FF);
    buf[2] = (uint8_t)((dio1Mask >> 8) & 0x00FF);
    buf[3] = (uint8_t)(dio1Mask & 0x00FF);
    buf[4] = (uint8_t)((dio2Mask >> 8) & 0x00FF);
    buf[5] = (uint8_t)(dio2Mask & 0x00FF);
    buf[6] = (uint8_t)((dio3Mask >> 8) & 0x00FF);
    buf[7] = (uint8_t)(dio3Mask & 0x00FF);
    hal.WriteCommand(SX126X_RADIO_SET_DIOIRQPARAMS, buf, sizeof(buf), SX12XX_Radio_1);
}

uint16_t ICACHE_RAM_ATTR SX126xDriver::GetIrqStatus(SX12XX_Radio_Number_t radioNumber)
{
    (void)radioNumber;
    WORD_ALIGNED_ATTR uint8_t status[2] = {0};
    hal.ReadCommand(SX126X_RADIO_GET_IRQSTATUS, status, 2, SX12XX_Radio_1);
    return (uint16_t)((status[0] << 8) | status[1]);
}

void ICACHE_RAM_ATTR SX126xDriver::ClearIrqStatus(uint16_t irqMask, SX12XX_Radio_Number_t radioNumber)
{
    (void)radioNumber;
    uint8_t buf[2] = {(uint8_t)((irqMask >> 8) & 0x00FF), (uint8_t)(irqMask & 0x00FF)};
    hal.WriteCommand(SX126X_RADIO_CLR_IRQSTATUS, buf, sizeof(buf), SX12XX_Radio_1);
}

void ICACHE_RAM_ATTR SX126xDriver::TXnbISR()
{
    currOpmode = fallBackMode; // radio goes to FS after TX
    CommitOutputPower();
    TXdoneCallback();
}

void ICACHE_RAM_ATTR SX126xDriver::TXnb(uint8_t * data, uint8_t size, SX12XX_Radio_Number_t radioNumber)
{
    transmittingRadio = SX12XX_Radio_1;
#if defined(GHOST_NO_TX)
    (void)data;
    (void)size;
    (void)radioNumber;
    RXnb();
    return; // Defense in depth: no alternate caller may transmit.
#else
    if (radioNumber == SX12XX_Radio_NONE)
    {
        SetMode(fallBackMode, SX12XX_Radio_1);
        return;
    }
    RFAMP.TXenable(SX12XX_Radio_1); // do first to allow PA stabilise
    hal.WriteBuffer(0x00, data, size, SX12XX_Radio_1);
    SetMode(SX126X_MODE_TX, SX12XX_Radio_1);
#endif
}

bool ICACHE_RAM_ATTR SX126xDriver::RXnbISR(uint16_t irqStatus)
{
    if (timeout != SX126X_RX_TIMEOUT_CONTINUOUS)
    {
        // A timed single reception falls back to FS. 15.3 Implicit Header
        // Mode Timeout Behavior: the timeout counter keeps running after RX
        // done, so stop it and clear the event.
        currOpmode = fallBackMode;
        hal.WriteRegister(SX126X_REG_RTC_CTRL, (uint8_t)0x00, SX12XX_Radio_1);
        uint8_t events = hal.ReadRegister(SX126X_REG_EVENT_MASK, SX12XX_Radio_1);
        hal.WriteRegister(SX126X_REG_EVENT_MASK, (uint8_t)(events | 0x02), SX12XX_Radio_1);
    }

    // LoRa CRC is off and the header is implicit, so these never fire for an
    // ExpressLRS link; keep the status mapping for completeness.
    rx_status fail = SX12XX_RX_OK;
    if (irqStatus & SX126X_IRQ_CRC_ERROR)
        fail = (rx_status)(fail | SX12XX_RX_CRC_FAIL);
    if (irqStatus & SX126X_IRQ_HEADER_ERROR)
        fail = (rx_status)(fail | SX12XX_RX_SYNCWORD_ERROR);
    if (fail == SX12XX_RX_OK)
    {
        uint8_t const FIFOaddr = GetRxBufferAddr(SX12XX_Radio_1);
        hal.ReadBuffer(FIFOaddr, RXdataBuffer, PayloadLength, SX12XX_Radio_1);
    }

    return RXdoneCallback(fail);
}

void ICACHE_RAM_ATTR SX126xDriver::RXnb(SX126x_RadioOperatingModes_t rxMode)
{
    RFAMP.RXenable();
    SetMode(rxMode, SX12XX_Radio_1);
}

uint8_t ICACHE_RAM_ATTR SX126xDriver::GetRxBufferAddr(SX12XX_Radio_Number_t radioNumber)
{
    (void)radioNumber;
    // 13.5.2 GetRxBufferStatus: [PayloadLengthRx][RxStartBufferPointer]
    WORD_ALIGNED_ATTR uint8_t status[2] = {0};
    hal.ReadCommand(SX126X_RADIO_GET_RXBUFFERSTATUS, status, 2, SX12XX_Radio_1);
    return status[1];
}

int8_t ICACHE_RAM_ATTR SX126xDriver::GetRssiInst(SX12XX_Radio_Number_t radioNumber)
{
    (void)radioNumber;
    WORD_ALIGNED_ATTR uint8_t status[2] = {0};
    hal.ReadCommand(SX126X_RADIO_GET_RSSIINST, status, 1, SX12XX_Radio_1);
    return -(int8_t)(status[0] / 2);
}

void ICACHE_RAM_ATTR SX126xDriver::GetLastPacketStats()
{
    // 13.5.3 GetPacketStatus (LoRa): [RssiPkt][SnrPkt][SignalRssiPkt]
    WORD_ALIGNED_ATTR uint8_t status[4] = {0};
    hal.ReadCommand(SX126X_RADIO_GET_PACKETSTATUS, status, 3, SX12XX_Radio_1);

    // RssiPkt is the average RSSI over the packet; actual power is -RssiPkt/2 dBm.
    LastPacketRSSI = -(int8_t)(status[0] / 2);
    LastPacketRSSI2 = LastPacketRSSI;
    // SnrPkt is two's complement in 1/4 dB, which is RADIO_SNR_SCALE already.
    LastPacketSNRRaw = (int8_t)status[1];
    lastSuccessfulPacketRadio = SX12XX_Radio_1;

#if defined(DEBUG_RCVR_SIGNAL_STATS)
    rxSignalStats[0].irq_count++;
    rxSignalStats[0].rssi_sum += LastPacketRSSI;
    rxSignalStats[0].snr_sum += LastPacketSNRRaw;
    if (LastPacketSNRRaw > rxSignalStats[0].snr_max)
    {
        rxSignalStats[0].snr_max = LastPacketSNRRaw;
    }
    irq_count_or++;
#endif
}

void ICACHE_RAM_ATTR SX126xDriver::IsrCallback_1()
{
    instance->IsrCallback();
}

uint8_t SX126xDriver::GetStatus()
{
    return hal.GetStatus(SX12XX_Radio_1);
}

uint16_t SX126xDriver::GetDeviceErrors()
{
    WORD_ALIGNED_ATTR uint8_t errors[2] = {0};
    hal.ReadCommand(SX126X_RADIO_GET_DEVICE_ERRORS, errors, 2, SX12XX_Radio_1);
    return (uint16_t)((errors[0] << 8) | errors[1]);
}

uint32_t SX126xDriver::GetBusyTimeouts() const
{
    return hal.busyTimeouts;
}

void ICACHE_RAM_ATTR SX126xDriver::IsrCallback()
{
    processingPacketRadio = SX12XX_Radio_1;
    irqCount++;

    uint16_t irqStatus = GetIrqStatus(SX12XX_Radio_1);
    if (irqStatus & SX126X_IRQ_TX_DONE)
    {
        RFAMP.TXRXdisable();
        TXnbISR();
    }
    else if (irqStatus & SX126X_IRQ_RX_DONE)
    {
        rxDoneCount++;
        RXnbISR(irqStatus);
        isFirstRxIrq = false; // RX isr is already fired in this period. (reset to true in tock)
    }
    else if (irqStatus == SX126X_IRQ_RADIO_NONE)
    {
        return;
    }
    ClearIrqStatus(SX126X_IRQ_RADIO_ALL, SX12XX_Radio_1);
}

#endif // RADIO_SX126X && !UNIT_TEST
