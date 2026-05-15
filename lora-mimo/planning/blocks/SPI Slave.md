# SPI Slave (Host Interface)

Control block. See [System Architecture](../System%20Diagram.md) for context.

**Owner:** TBD
**Status:** Not started

---

## Function

SPI slave providing the RPi (SPI0 CS1) with register read/write access to all ASIC configuration and status registers, and firmware load (burst write to PicoRV32 IMEM).

> **Non-FFT path:** The burst SRAM read feature (Baseband SRAM capture region `0x40000`–`0x87FFF`) is **not required** — the 544 KB Baseband SRAM does not exist in the non-FFT architecture. The `sram_addr/rdata/req/grant` ports and burst read command can be omitted from the initial implementation. The core requirement is register access and firmware load only.

---

## Interface

| Port | Direction | Width | Description |
| --- | --- | --- | --- |
| `HOST_CS` | in | 1 | Active-low chip select from RPi SPI0 CS1 |
| `SPI_SCK` | in | 1 | SPI clock from RPi (up to 10 MHz) |
| `SPI_MOSI` | in | 1 | Data from RPi |
| `SPI_MISO` | out | 1 | Data to RPi |
| `clk_32m` | in | — | Master clock (register domain) |
| `rst_n` | in | — | Active-low reset |
| `reg_addr` | out | 8 | Decoded register address |
| `reg_wdata` | out | 8 | Write data |
| `reg_we` | out | 1 | Write enable to register bank |
| `reg_rdata` | in | 8 | Read data from register bank |
| `sram_addr` | out | 20 | *(Optional — not required for non-FFT)* Address for SRAM burst read |
| `sram_rdata` | in | 32 | *(Optional — not required for non-FFT)* Read data from SRAM |
| `sram_req` | out | 1 | *(Optional — not required for non-FFT)* SRAM bus request |
| `sram_grant` | in | 1 | *(Optional — not required for non-FFT)* SRAM bus grant |

---

## Protocol

**SPI mode:** Mode 0 (CPOL=0, CPHA=0). MSB first.

**Single register access (2 bytes):**
```
Byte 0: [7] R/W̄  [6:0] address
Byte 1: data (write) or don't-care (read)
MISO byte 1: register contents (read) or 0x00 (write)
```

**Burst SRAM read (N+2 bytes) — FFT path only, not required for non-FFT:**
```
Byte 0: 0x80 | burst_flag | high_addr
Byte 1: low_addr
Bytes 2…N+1: MISO returns consecutive SRAM bytes, address auto-increments
```

**Firmware load (burst write to IMEM):**
```
Write CPU_RESET=1 (0x02 ← 0x01)
Burst write to IMEM base address
Write CPU_RESET=0 (0x02 ← 0x00)
```

---

## Implementation notes

**Clock domain crossing.** SPI clock (up to 10 MHz) and 32 MHz system clock are asynchronous. Synchronise `HOST_CS` and `SPI_SCK` edges into the 32 MHz domain with a 2-FF synchroniser. Alternatively, run the SPI state machine entirely in the SPI clock domain and use a handshake for register access.

**MISO tristate.** Drive `SPI_MISO` only when `HOST_CS` is asserted. Tristate (or drive low) otherwise — the line is shared with the ASIC's SPI master output via the shared SPI bus.

**Bus conflict.** `HOST_CS` and `SX1257_CS[3:0]` are mutually exclusive by design (RPi and PicoRV32 never assert simultaneously). No explicit arbitration needed if firmware protocol is respected.

**Register bank.** Thin address decoder maps `reg_addr` to the register file. Writable registers latch `reg_wdata` on `reg_we`. Read-only registers ignore `reg_we`.

---

## Verification

| Test | Method | Pass criterion |
| --- | --- | --- |
| CHIP_ID read | cocotb SPI master; read 0x00 | Returns 0xA7 |
| Register write + readback | Write known pattern to all R/W registers; read back | Byte-identical readback |
| Read-only register write | Write to CHIP_ID; read back | Still returns 0xA7 |
| Burst SRAM read | Fill SRAM with known pattern; burst read via SPI | Data matches pattern |
| Firmware load | Write 256-byte binary via burst; read IMEM back | Contents match |
| CPU_RESET sequence | Assert, load, de-assert; monitor PicoRV32 fetch | CPU starts fetching from 0x0000 |
| Back-to-back transactions | Multiple single-byte accesses | No missed edges; correct data each transaction |

---

## Related blocks

- [Register Map](../Register%20Map.md) — all register addresses
- [Register Map Delta - Non-FFT](../Register%20Map%20Delta%20-%20Non-FFT.md) — updated register set for non-FFT path
- [PicoRV32 Integration](PicoRV32%20Integration.md) — IMEM target for firmware load; CPU_RESET register
- [AHB-Lite Bus](AHB-Lite%20Bus.md) — internal bus for register access
