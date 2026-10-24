#pragma once

// Semtech SX1261/SX1262 command set and parameter encodings used by the
// receive-only ExpressLRS sniffer port. Opcodes, register addresses and
// encodings follow the SX1261/2 datasheet (DS.SX1261-2.W.APP), chapters
// 13 (commands) and 15 (known limitations).

#define SX126X_XTAL_FREQ 32000000
// PLL step is 32 MHz / 2^25 = 0.95367431640625 Hz. The FHSS tables hand this
// driver frequencies in Hz (FREQ_HZ_TO_REG_VAL is the identity for
// RADIO_SX126X), so FREQ_STEP only exists for the shared FreqCorrection macros.
#define FREQ_STEP ((double)SX126X_XTAL_FREQ / 33554432.0)

#define SX126X_POWER_MIN (-9) // dBm, SX1262 high power PA
#define SX126X_POWER_MAX 22

typedef enum
{
    SX126X_MODE_SLEEP = 0x00, //! The radio is in sleep mode
    SX126X_MODE_STDBY_RC,     //! Standby with the 13 MHz RC oscillator (TCXO off)
    SX126X_MODE_STDBY_XOSC,   //! Standby with the crystal/TCXO running
    SX126X_MODE_FS,           //! Frequency synthesis, PLL locked
    SX126X_MODE_RX,           //! Receive with the configured timeout
    SX126X_MODE_RX_CONT,      //! Continuous receive
    SX126X_MODE_TX,           //! Transmit
    SX126X_MODE_CAD           //! Channel activity detection
} SX126x_RadioOperatingModes_t;

typedef enum
{
    SX126X_RADIO_RESET_STATS = 0x00,
    SX126X_RADIO_CLR_IRQSTATUS = 0x02,
    SX126X_RADIO_CLR_DEVICE_ERRORS = 0x07,
    SX126X_RADIO_SET_DIOIRQPARAMS = 0x08,
    SX126X_RADIO_WRITE_REGISTER = 0x0D,
    SX126X_RADIO_WRITE_BUFFER = 0x0E,
    SX126X_RADIO_GET_STATS = 0x10,
    SX126X_RADIO_GET_PACKETTYPE = 0x11,
    SX126X_RADIO_GET_IRQSTATUS = 0x12,
    SX126X_RADIO_GET_RXBUFFERSTATUS = 0x13,
    SX126X_RADIO_GET_PACKETSTATUS = 0x14,
    SX126X_RADIO_GET_RSSIINST = 0x15,
    SX126X_RADIO_GET_DEVICE_ERRORS = 0x17,
    SX126X_RADIO_READ_REGISTER = 0x1D,
    SX126X_RADIO_READ_BUFFER = 0x1E,
    SX126X_RADIO_SET_STANDBY = 0x80,
    SX126X_RADIO_SET_RX = 0x82,
    SX126X_RADIO_SET_TX = 0x83,
    SX126X_RADIO_SET_SLEEP = 0x84,
    SX126X_RADIO_SET_RFFREQUENCY = 0x86,
    SX126X_RADIO_SET_CADPARAMS = 0x88,
    SX126X_RADIO_CALIBRATE = 0x89,
    SX126X_RADIO_SET_PACKETTYPE = 0x8A,
    SX126X_RADIO_SET_MODULATIONPARAMS = 0x8B,
    SX126X_RADIO_SET_PACKETPARAMS = 0x8C,
    SX126X_RADIO_SET_TXPARAMS = 0x8E,
    SX126X_RADIO_SET_BUFFERBASEADDRESS = 0x8F,
    SX126X_RADIO_SET_RXTXFALLBACKMODE = 0x93,
    SX126X_RADIO_SET_RXDUTYCYCLE = 0x94,
    SX126X_RADIO_SET_PACONFIG = 0x95,
    SX126X_RADIO_SET_REGULATORMODE = 0x96,
    SX126X_RADIO_SET_DIO3_AS_TCXO_CTRL = 0x97,
    SX126X_RADIO_CALIBRATE_IMAGE = 0x98,
    SX126X_RADIO_SET_DIO2_AS_RF_SWITCH = 0x9D,
    SX126X_RADIO_STOP_TIMER_ON_PREAMBLE = 0x9F,
    SX126X_RADIO_SET_LORA_SYMB_NUM_TIMEOUT = 0xA0,
    SX126X_RADIO_GET_STATUS = 0xC0,
    SX126X_RADIO_SET_FS = 0xC1,
    SX126X_RADIO_SET_CAD = 0xC5,
    SX126X_RADIO_SET_TXCONTINUOUSWAVE = 0xD1,
    SX126X_RADIO_SET_TXCONTINUOUSPREAMBLE = 0xD2,
} SX126x_RadioCommands_t;

typedef enum
{
    SX126X_STDBY_RC = 0x00,
    SX126X_STDBY_XOSC = 0x01,
} SX126x_RadioStandbyModes_t;

typedef enum
{
    SX126X_REGULATOR_LDO = 0x00,
    SX126X_REGULATOR_DCDC = 0x01,
} SX126x_RadioRegulatorModes_t;

typedef enum
{
    SX126X_PACKET_TYPE_GFSK = 0x00,
    SX126X_PACKET_TYPE_LORA = 0x01,
} SX126x_RadioPacketTypes_t;

// SetRxTxFallbackMode: operating mode entered after TX or a single RX
typedef enum
{
    SX126X_FALLBACK_STDBY_RC = 0x20,
    SX126X_FALLBACK_STDBY_XOSC = 0x30,
    SX126X_FALLBACK_FS = 0x40,
} SX126x_RadioFallbackModes_t;

// SetDIO3AsTCXOCtrl supply voltage codes
typedef enum
{
    SX126X_TCXO_CTRL_1_6V = 0x00,
    SX126X_TCXO_CTRL_1_7V = 0x01,
    SX126X_TCXO_CTRL_1_8V = 0x02,
    SX126X_TCXO_CTRL_2_2V = 0x03,
    SX126X_TCXO_CTRL_2_4V = 0x04,
    SX126X_TCXO_CTRL_2_7V = 0x05,
    SX126X_TCXO_CTRL_3_0V = 0x06,
    SX126X_TCXO_CTRL_3_3V = 0x07,
} SX126x_RadioTcxoCtrlVoltage_t;

typedef enum
{
    SX126X_RAMP_10_US = 0x00,
    SX126X_RAMP_20_US = 0x01,
    SX126X_RAMP_40_US = 0x02,
    SX126X_RAMP_80_US = 0x03,
    SX126X_RAMP_200_US = 0x04,
    SX126X_RAMP_800_US = 0x05,
    SX126X_RAMP_1700_US = 0x06,
    SX126X_RAMP_3400_US = 0x07,
} SX126x_RadioRampTimes_t;

// LoRa modulation parameters. These encodings are shared with the LR11xx
// family, so the sub-GHz rows of the LR1121 ExpressLRS rate table carry over.
typedef enum
{
    SX126X_LORA_SF5 = 0x05,
    SX126X_LORA_SF6 = 0x06,
    SX126X_LORA_SF7 = 0x07,
    SX126X_LORA_SF8 = 0x08,
    SX126X_LORA_SF9 = 0x09,
    SX126X_LORA_SF10 = 0x0A,
    SX126X_LORA_SF11 = 0x0B,
    SX126X_LORA_SF12 = 0x0C,
} SX126x_RadioLoRaSpreadingFactors_t;

typedef enum
{
    SX126X_LORA_BW_7 = 0x00,   // 7.81 kHz
    SX126X_LORA_BW_10 = 0x08,  // 10.42 kHz
    SX126X_LORA_BW_15 = 0x01,  // 15.63 kHz
    SX126X_LORA_BW_20 = 0x09,  // 20.83 kHz
    SX126X_LORA_BW_31 = 0x02,  // 31.25 kHz
    SX126X_LORA_BW_41 = 0x0A,  // 41.67 kHz
    SX126X_LORA_BW_62 = 0x03,  // 62.50 kHz
    SX126X_LORA_BW_125 = 0x04, // 125 kHz
    SX126X_LORA_BW_250 = 0x05, // 250 kHz
    SX126X_LORA_BW_500 = 0x06, // 500 kHz
} SX126x_RadioLoRaBandwidths_t;

typedef enum
{
    SX126X_LORA_CR_4_5 = 0x01,
    SX126X_LORA_CR_4_6 = 0x02,
    SX126X_LORA_CR_4_7 = 0x03,
    SX126X_LORA_CR_4_8 = 0x04,
} SX126x_RadioLoRaCodingRates_t;

typedef enum
{
    SX126X_LORA_LDRO_OFF = 0x00,
    SX126X_LORA_LDRO_ON = 0x01,
} SX126x_RadioLoRaLowDataRateOptimize_t;

typedef enum
{
    SX126X_LORA_PACKET_VARIABLE_LENGTH = 0x00, //!< Explicit header transmitted over the air
    SX126X_LORA_PACKET_FIXED_LENGTH = 0x01,    //!< Implicit header, length known on both sides
    SX126X_LORA_PACKET_EXPLICIT = SX126X_LORA_PACKET_VARIABLE_LENGTH,
    SX126X_LORA_PACKET_IMPLICIT = SX126X_LORA_PACKET_FIXED_LENGTH,
} SX126x_RadioLoRaPacketLengthsModes_t;

typedef enum
{
    SX126X_LORA_CRC_OFF = 0x00,
    SX126X_LORA_CRC_ON = 0x01,
} SX126x_RadioLoRaCrcModes_t;

typedef enum
{
    SX126X_LORA_IQ_NORMAL = 0x00,
    SX126X_LORA_IQ_INVERTED = 0x01,
} SX126x_RadioLoRaIQModes_t;

typedef enum
{
    SX126X_IRQ_RADIO_NONE = 0x0000,
    SX126X_IRQ_TX_DONE = 0x0001,
    SX126X_IRQ_RX_DONE = 0x0002,
    SX126X_IRQ_PREAMBLE_DETECTED = 0x0004,
    SX126X_IRQ_SYNCWORD_VALID = 0x0008,
    SX126X_IRQ_HEADER_VALID = 0x0010,
    SX126X_IRQ_HEADER_ERROR = 0x0020,
    SX126X_IRQ_CRC_ERROR = 0x0040,
    SX126X_IRQ_CAD_DONE = 0x0080,
    SX126X_IRQ_CAD_DETECTED = 0x0100,
    SX126X_IRQ_RX_TX_TIMEOUT = 0x0200,
    SX126X_IRQ_LR_FHSS_HOP = 0x4000,
    SX126X_IRQ_RADIO_ALL = 0xFFFF,
} SX126x_RadioIrqMasks_t;

#define SX126X_CALIBRATE_ALL 0x7F
#define SX126X_RX_TIMEOUT_CONTINUOUS 0xFFFFFF // SetRx: stay in RX until commanded otherwise
#define SX126X_RX_TIMEOUT_PERIOD_BASE_NANOS 15625 // RX/TX timeout tick, 15.625 us

// Registers
#define SX126X_REG_RTC_CTRL 0x0902            // 15.3 implicit header timeout workaround
#define SX126X_REG_EVENT_MASK 0x0944          // 15.3 implicit header timeout workaround
#define SX126X_REG_IQ_POLARITY 0x0736         // 15.4 inverted IQ optimisation, bit 2
#define SX126X_REG_LORA_SYNC_WORD_MSB 0x0740
#define SX126X_REG_LORA_SYNC_WORD_LSB 0x0741
#define SX126X_REG_RX_GAIN 0x08AC
#define SX126X_RX_GAIN_POWER_SAVING 0x94
#define SX126X_RX_GAIN_BOOSTED 0x96
#define SX126X_REG_TX_MODULATION 0x0889
#define SX126X_REG_OCP 0x08E7

// 0x1424 is the reset default and matches the SX127x private sync word 0x12
// used by every ExpressLRS sub-GHz link. 0x3444 is the LoRaWAN public value.
#define SX126X_LORA_SYNC_WORD_PRIVATE 0x1424
#define SX126X_LORA_SYNC_WORD_PUBLIC 0x3444
