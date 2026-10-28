#pragma once

#include "SX126x_Regs.h"
#include "SX12xxDriverCommon.h"

// SPI and GPIO access for a single SX1261/SX1262 (BUSY, DIO1, NSS, RESET).
// Every command waits for BUSY to fall first, and command writes complete
// synchronously so the following BUSY check sees this command's busy period
// rather than a stale low level from before the transfer finished.
class SX126xHal
{
public:
    static SX126xHal *instance;

    SX126xHal();

    void init();
    void end();
    void reset();

    void ICACHE_RAM_ATTR WriteCommand(SX126x_RadioCommands_t command, uint8_t val, SX12XX_Radio_Number_t radioNumber);
    void ICACHE_RAM_ATTR WriteCommand(SX126x_RadioCommands_t opcode, const uint8_t *buffer, uint8_t size, SX12XX_Radio_Number_t radioNumber);
    // Reads the payload of a GET_* command. The two status bytes clocked out
    // during the opcode and the following NOP are discarded.
    void ICACHE_RAM_ATTR ReadCommand(SX126x_RadioCommands_t opcode, uint8_t *buffer, uint8_t size, SX12XX_Radio_Number_t radioNumber);
    uint8_t ICACHE_RAM_ATTR GetStatus(SX12XX_Radio_Number_t radioNumber);

    void ICACHE_RAM_ATTR WriteRegister(uint16_t address, const uint8_t *buffer, uint8_t size, SX12XX_Radio_Number_t radioNumber);
    void ICACHE_RAM_ATTR WriteRegister(uint16_t address, uint8_t value, SX12XX_Radio_Number_t radioNumber);
    void ICACHE_RAM_ATTR ReadRegister(uint16_t address, uint8_t *buffer, uint8_t size, SX12XX_Radio_Number_t radioNumber);
    uint8_t ICACHE_RAM_ATTR ReadRegister(uint16_t address, SX12XX_Radio_Number_t radioNumber);

    void ICACHE_RAM_ATTR WriteBuffer(uint8_t offset, const uint8_t *buffer, uint8_t size, SX12XX_Radio_Number_t radioNumber);
    void ICACHE_RAM_ATTR ReadBuffer(uint8_t offset, uint8_t *buffer, uint8_t size, SX12XX_Radio_Number_t radioNumber);

    bool ICACHE_RAM_ATTR WaitOnBusy(SX12XX_Radio_Number_t radioNumber);

    static ICACHE_RAM_ATTR void dioISR_1();
    void (*IsrCallback_1)(); // function pointer for the DIO1 callback

    uint32_t busyTimeouts; // diagnostics: commands issued after BUSY stayed high too long
};
