# Wishbone Bus

Control block. See [System Architecture](../System%20Diagram.md) for context.

**Owner:** TBD
**Status:** Not started

---

## Function

Wishbone B4 shared bus connecting PicoRV32 (master) to all on-chip peripherals (slaves). Single master — no arbitration needed.

---

## Slave map

| Address range | Slave | Notes |
| --- | --- | --- |
| `0x10000`–`0x100FF` | Register bank | ASIC config/status registers |
| `0x10100`–`0x101FF` | SPI master | SX1257 config writes |
| `0x10200`–`0x102FF` | IRQ controller | Source read/clear |
| `0x10300`–`0x103FF` | SWD TAP | Debug interface |
| `0x20000`–`0x5FFFF` | Baseband SRAM (via arbiter) | Sample capture / FFT staging |

---

## Interface (per slave)

Standard Wishbone B4 signals: `ADR`, `DAT_O`, `DAT_I`, `WE`, `STB`, `ACK`, `CYC`. Single cycle (no burst) for registers; burst optional for SRAM.

---

## Implementation notes

**Single master.** PicoRV32 is the only Wishbone master — no arbiter logic needed. Address decode is a simple priority mux on `ADR`.

**Wait states.** Register bank and IRQ controller should ack in 1 cycle. SPI master and Baseband SRAM arbiter may insert wait states (STB held, ACK delayed); PicoRV32 halts the pipeline until ACK.

**Shared bus reset.** All slaves deassert ACK on `rst_n`. PicoRV32 must not assert CYC/STB until after reset is released and SRAM/SPI macros are stable.

---

## Verification

| Test | Method | Pass criterion |
| --- | --- | --- |
| Register R/W via Wishbone | cocotb: PicoRV32 model writes/reads each slave | Correct data; ACK in expected cycles |
| Wait state handling | SRAM with 2-cycle latency | PicoRV32 stalls until ACK; data correct |
| Address decode | Access each slave address range | No aliasing; correct slave responds |

---

## Related blocks

- [PicoRV32 Integration](PicoRV32%20Integration.md) — bus master
- All peripheral blocks — bus slaves
