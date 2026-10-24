// SPI/GPIO layer for the receive-only SX1261/SX1262 sniffer driver.
// Derived from the ExpressLRS SX1280 HAL (single radio, 8-bit opcodes).
#if defined(RADIO_SX126X) && !defined(UNIT_TEST)

#include "SX126x_Regs.h"
#include "SX126x_hal.h"
#include <SPIEx.h>
#include "logging.h"

SX126xHal *SX126xHal::instance = NULL;

SX126xHal::SX126xHal()
{
    instance = this;
    IsrCallback_1 = nullptr;
    busyTimeouts = 0;
}

void SX126xHal::end()
{
    detachInterrupt(GPIO_PIN_DIO1);
    SPIEx.end();
    IsrCallback_1 = nullptr; // remove callbacks
}

void SX126xHal::init()
{
    DBGLN("SX126x Hal Init");

    if (GPIO_PIN_BUSY != UNDEF_PIN)
    {
        pinMode(GPIO_PIN_BUSY, INPUT);
    }
    pinMode(GPIO_PIN_DIO1, INPUT);
    pinMode(GPIO_PIN_NSS, OUTPUT);
    digitalWrite(GPIO_PIN_NSS, HIGH);

#if defined(PLATFORM_ESP32)
    SPIEx.begin(GPIO_PIN_SCK, GPIO_PIN_MISO, GPIO_PIN_MOSI, GPIO_PIN_NSS); // sck, miso, mosi, ss
    gpio_pullup_en((gpio_num_t)GPIO_PIN_MISO);
    // The SX126x SPI interface is specified up to 16 MHz; leave margin.
    SPIEx.setFrequency(10000000);
    SPIEx.setHwCs(true);
    spiEnableSSPins(SPIEx.bus(), SX12XX_Radio_1);
#else
#error "The SX126x sniffer driver supports ESP32 targets only"
#endif

    attachInterrupt(digitalPinToInterrupt(GPIO_PIN_DIO1), this->dioISR_1, RISING);
}

void SX126xHal::reset()
{
    DBGLN("SX126x Reset");

    if (GPIO_PIN_RST != UNDEF_PIN)
    {
        pinMode(GPIO_PIN_RST, OUTPUT);
        digitalWrite(GPIO_PIN_RST, LOW);
        delay(2); // NRESET must stay low for at least 100 us
        digitalWrite(GPIO_PIN_RST, HIGH);
        delay(20); // BUSY stays high while the chip calibrates after reset
    }

    WaitOnBusy(SX12XX_Radio_1);
    DBGLN("SX126x Ready!");
}

void ICACHE_RAM_ATTR SX126xHal::WriteCommand(SX126x_RadioCommands_t command, uint8_t val, SX12XX_Radio_Number_t radioNumber)
{
    WriteCommand(command, &val, 1, radioNumber);
}

void ICACHE_RAM_ATTR SX126xHal::WriteCommand(SX126x_RadioCommands_t command, const uint8_t *buffer, uint8_t size, SX12XX_Radio_Number_t radioNumber)
{
    WORD_ALIGNED_ATTR uint8_t OutBuffer[WORD_PADDED(size + 1)];
    OutBuffer[0] = (uint8_t)command;
    memcpy(OutBuffer + 1, buffer, size);

    WaitOnBusy(radioNumber);
    // A full-duplex transfer returns only after NSS rises, so the next
    // WaitOnBusy observes this command's BUSY period instead of a stale level
    // left over from before the asynchronous write completed.
    SPIEx.read(radioNumber, OutBuffer, size + 1); // Returned bytes ignored
    delayMicroseconds(1); // BUSY asserts within a microsecond of NSS rising
}

void ICACHE_RAM_ATTR SX126xHal::ReadCommand(SX126x_RadioCommands_t opcode, uint8_t *buffer, uint8_t size, SX12XX_Radio_Number_t radioNumber)
{
    // Response: [status during opcode][status during NOP][data...]
    WORD_ALIGNED_ATTR uint8_t OutBuffer[WORD_PADDED(size + 2)];
    memset(OutBuffer, 0, size + 2);
    OutBuffer[0] = (uint8_t)opcode;

    WaitOnBusy(radioNumber);
    SPIEx.read(radioNumber, OutBuffer, size + 2);
    memcpy(buffer, OutBuffer + 2, size);
}

uint8_t ICACHE_RAM_ATTR SX126xHal::GetStatus(SX12XX_Radio_Number_t radioNumber)
{
    WORD_ALIGNED_ATTR uint8_t OutBuffer[WORD_PADDED(2)] = {SX126X_RADIO_GET_STATUS, 0x00};

    WaitOnBusy(radioNumber);
    SPIEx.read(radioNumber, OutBuffer, 2);
    return OutBuffer[1];
}

void ICACHE_RAM_ATTR SX126xHal::WriteRegister(uint16_t address, const uint8_t *buffer, uint8_t size, SX12XX_Radio_Number_t radioNumber)
{
    WORD_ALIGNED_ATTR uint8_t OutBuffer[WORD_PADDED(size + 3)];
    OutBuffer[0] = SX126X_RADIO_WRITE_REGISTER;
    OutBuffer[1] = (uint8_t)((address & 0xFF00) >> 8);
    OutBuffer[2] = (uint8_t)(address & 0x00FF);
    memcpy(OutBuffer + 3, buffer, size);

    WaitOnBusy(radioNumber);
    SPIEx.read(radioNumber, OutBuffer, size + 3); // Returned bytes ignored
    delayMicroseconds(1);
}

void ICACHE_RAM_ATTR SX126xHal::WriteRegister(uint16_t address, uint8_t value, SX12XX_Radio_Number_t radioNumber)
{
    WriteRegister(address, &value, 1, radioNumber);
}

void ICACHE_RAM_ATTR SX126xHal::ReadRegister(uint16_t address, uint8_t *buffer, uint8_t size, SX12XX_Radio_Number_t radioNumber)
{
    // [opcode][addr MSB][addr LSB][NOP -> status][data...]
    WORD_ALIGNED_ATTR uint8_t OutBuffer[WORD_PADDED(size + 4)];
    memset(OutBuffer, 0, size + 4);
    OutBuffer[0] = SX126X_RADIO_READ_REGISTER;
    OutBuffer[1] = (uint8_t)((address & 0xFF00) >> 8);
    OutBuffer[2] = (uint8_t)(address & 0x00FF);

    WaitOnBusy(radioNumber);
    SPIEx.read(radioNumber, OutBuffer, size + 4);
    memcpy(buffer, OutBuffer + 4, size);
}

uint8_t ICACHE_RAM_ATTR SX126xHal::ReadRegister(uint16_t address, SX12XX_Radio_Number_t radioNumber)
{
    uint8_t data = 0;
    ReadRegister(address, &data, 1, radioNumber);
    return data;
}

void ICACHE_RAM_ATTR SX126xHal::WriteBuffer(uint8_t offset, const uint8_t *buffer, uint8_t size, SX12XX_Radio_Number_t radioNumber)
{
    WORD_ALIGNED_ATTR uint8_t OutBuffer[WORD_PADDED(size + 2)];
    OutBuffer[0] = SX126X_RADIO_WRITE_BUFFER;
    OutBuffer[1] = offset;
    memcpy(OutBuffer + 2, buffer, size);

    WaitOnBusy(radioNumber);
    SPIEx.read(radioNumber, OutBuffer, size + 2); // Returned bytes ignored
    delayMicroseconds(1);
}

void ICACHE_RAM_ATTR SX126xHal::ReadBuffer(uint8_t offset, uint8_t *buffer, uint8_t size, SX12XX_Radio_Number_t radioNumber)
{
    // [opcode][offset][NOP -> status][data...]
    WORD_ALIGNED_ATTR uint8_t OutBuffer[WORD_PADDED(size + 3)];
    memset(OutBuffer, 0, size + 3);
    OutBuffer[0] = SX126X_RADIO_READ_BUFFER;
    OutBuffer[1] = offset;

    WaitOnBusy(radioNumber);
    SPIEx.read(radioNumber, OutBuffer, size + 3);
    memcpy(buffer, OutBuffer + 3, size);
}

bool ICACHE_RAM_ATTR SX126xHal::WaitOnBusy(SX12XX_Radio_Number_t radioNumber)
{
    (void)radioNumber; // single radio
    if (GPIO_PIN_BUSY == UNDEF_PIN)
    {
        delayMicroseconds(100); // no BUSY line: allow the longest routine command
        return true;
    }

    // Calibration and TCXO start-up take longer; Begin() adds explicit delays there.
    constexpr uint32_t wtimeoutUS = 2000U;
    uint32_t startTime = 0;
    while (digitalRead(GPIO_PIN_BUSY) == HIGH)
    {
        uint32_t now = micros();
        if (startTime == 0) startTime = now;
        if ((now - startTime) > wtimeoutUS)
        {
            busyTimeouts++;
            return false;
        }
    }
    return true;
}

void ICACHE_RAM_ATTR SX126xHal::dioISR_1()
{
    if (instance->IsrCallback_1)
        instance->IsrCallback_1();
}

#endif // RADIO_SX126X && !UNIT_TEST
