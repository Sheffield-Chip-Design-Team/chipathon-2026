# ASIC Pinout

GF180MCU MIMO ASIC — logical pad list. Physical pad numbers and positions are not yet assigned (pending floorplan). Total: **25 pads** (22 signal + 3 supply/ground) at the ≤25 per-team chipathon allocation limit.

**Related:** [System Architecture](System%20Architecture.md)

---

## Signal pads (22)

All signal pads use **GF180 5V-capable IO cells** from the chipathon padring library, operated on a **3.3V `VDD_IO` rail** to match the SX1257/SX1302/RPi board interfaces. Core logic runs at **3.3V**, so no internal level translation is required between the core and SRAM domains.

### RX data from SX1257 (8 pads, input)

| Pad name | Dir | Connected to | Description |
|---|---|---|---|
| `IQ_DATA_I[0]` | in | SX1257_1 I_OUT (pin 15) | 1-bit ΣΔ RX I stream, antenna 1 |
| `IQ_DATA_Q[0]` | in | SX1257_1 Q_OUT (pin 14) | 1-bit ΣΔ RX Q stream, antenna 1 |
| `IQ_DATA_I[1]` | in | SX1257_2 I_OUT | 1-bit ΣΔ RX I stream, antenna 2 |
| `IQ_DATA_Q[1]` | in | SX1257_2 Q_OUT | 1-bit ΣΔ RX Q stream, antenna 2 |
| `IQ_DATA_I[2]` | in | SX1257_3 I_OUT | 1-bit ΣΔ RX I stream, antenna 3 |
| `IQ_DATA_Q[2]` | in | SX1257_3 Q_OUT | 1-bit ΣΔ RX Q stream, antenna 3 |
| `IQ_DATA_I[3]` | in | SX1257_4 I_OUT | 1-bit ΣΔ RX I stream, antenna 4 |
| `IQ_DATA_Q[3]` | in | SX1257_4 Q_OUT | 1-bit ΣΔ RX Q stream, antenna 4 |

> **Polarity note (SX1257 Table 1-1 typo):** Table 1-1 of the SX1257 datasheet v1.2 describes pin 14 Q_OUT as "I channel" and pin 15 I_OUT as "Q channel" — this is a Semtech typo. The §3.7.1 block diagram is correct. Connect I_OUT (pin 15) → `IQ_DATA_I[n]` and Q_OUT (pin 14) → `IQ_DATA_Q[n]`.

### Clock (1 pad, input)

| Pad name | Dir | Connected to | Description |
|---|---|---|---|
| `IQ_CLK` | in | PCB TCXO clock buffer output | 32 MHz master clock. Shared reference: same buffer also drives SX1257_1–4 XTB (pin 8) via separate PCB traces. This pad is the ASIC core clock. |

### ΣΔ re-mod output to SX1302 (2 pads, output)

| Pad name | Dir | Connected to | Description |
|---|---|---|---|
| `REMOD_A_I` | out | SX1302 Radio A I input | 1-bit ΣΔ MRC combined stream |
| `REMOD_A_Q` | out | SX1302 Radio A Q input | 1-bit ΣΔ MRC combined stream Q |

> **SX1302 clock:** SX1302 CLK_IN is driven by SX1257_1 CLK_OUT (pin 10) directly on the PCB — no ASIC pad required. See board-level pin dispositions in [System Architecture](System%20Architecture.md).

### SPI bus — shared host config and SX1257 config (6 pads, bidirectional)

The ASIC acts as SPI slave (host → ASIC) and SPI master (ASIC → SX1257) on the same three-wire bus. Bus ownership is determined by chip-select assertion.

SX1257 device selection uses a board-level **74HC139 2-to-4 decoder**: the ASIC drives a 2-bit address (`CS_A[1:0]`) rather than four individual chip-select lines, saving 2 pads. The decoder enable is tied to GND (always active); the selected output goes low, all others remain high. SPI CLK quiescent = no transaction on spuriously-selected devices.

| Pad name | Dir | Connected to | Description |
|---|---|---|---|
| `SPI_MOSI` | bidir | RPi SPI0 MOSI; SX1257_1–4 SDI | ASIC drives when acting as master (SX1257 config); RPi drives when acting as slave (host config + firmware load) |
| `SPI_MISO` | bidir | RPi SPI0 MISO; SX1257_1–4 SDO | ASIC drives when slave; ASIC tristates when master; SX1257 drives during ASIC→SX1257 reads |
| `SPI_SCK` | bidir | RPi SPI0 SCLK; SX1257_1–4 SCK | RPi drives when host→ASIC; ASIC drives when ASIC→SX1257. Max 10 MHz |
| `CS_A[0]` | out | 74HC139 A0 input | LSB of SX1257 device address |
| `CS_A[1]` | out | 74HC139 A1 input | MSB of SX1257 device address |
| `HOST_CS` | in | RPi SPI0 CS1 | Active-low. Selects ASIC as SPI slave for host config and firmware load |

**74HC139 decode table (board-level):**

| `CS_A[1:0]` | SX1257 selected |
|---|---|
| `00` | SX1257_1 |
| `01` | SX1257_2 |
| `10` | SX1257_3 |
| `11` | SX1257_4 |

> **Broadcast writes removed.** With individual CS pads the SPI master could assert multiple lines simultaneously to write the same register to several SX1257s in one transaction. With the decoder only one device is selectable at a time; multi-device config requires sequential transactions (4 × ~1.6 µs at 10 MHz — negligible for startup).

### JTAG debug / IRQ / GPIO (4 pads, dual-function)

All four pads are dual-function, controlled by the `JTAG_EN` config bit (default 0 after reset = normal mode). In normal mode the pads serve as IRQ output and three general-purpose GPIOs. In debug mode they form a standard 4-pin JTAG interface for PicoRV32 post-silicon debugging.

| Pad name | `JTAG_EN=0` (default) | `JTAG_EN=1` (debug) | Connected to | Notes |
|---|---|---|---|---|
| `TCK_IRQ` | out — IRQ active-high | in — JTAG TCK from probe | RPi GPIO (IRQ) / JTAG probe TCK | IO cell output-enable gated by `JTAG_EN`. RPi GPIO must be configured as input/high-Z before setting `JTAG_EN=1`. |
| `TMS_GPIO0` | bidir — GPIO_0 (firmware-controlled) | in — JTAG TMS from probe | Board GPIO / JTAG probe TMS | In normal mode: general-purpose bidirectional GPIO driven by `GPIO_OUT[0]` / `GPIO_DIR[0]`, readable via `GPIO_IN[0]`. |
| `TDI_GPIO1` | bidir — GPIO_1 (firmware-controlled) | in — JTAG TDI from probe | Board GPIO / JTAG probe TDI | In normal mode: general-purpose bidirectional GPIO driven by `GPIO_OUT[1]` / `GPIO_DIR[1]`, readable via `GPIO_IN[1]`. |
| `TDO_GPIO2` | bidir — GPIO_2 (firmware-controlled) | out — JTAG TDO to probe | Board GPIO / JTAG probe TDO | In normal mode: general-purpose bidirectional GPIO driven by `GPIO_OUT[2]` / `GPIO_DIR[2]`, readable via `GPIO_IN[2]`. In debug mode: output-enable always asserted (JTAG drives TDO). |

**GPIO use cases (JTAG_EN=0):** board-level control signals with no dedicated ASIC pad — e.g. SE2435L CPS for antenna 3/4, external LED, test-point toggle. Three GPIOs available (GPIO_0–2).

**Mode switch procedure:**
1. RPi writes `JTAG_EN=1` via SPI before connecting probe.
2. RPi GPIO on `TCK_IRQ` configured as input to avoid contention.
3. Probe drives TCK, TMS, TDI; ASIC drives TDO.
4. On debug exit: RPi writes `JTAG_EN=0`; RPi GPIO reconfigured as rising-edge interrupt input; firmware resumes GPIO_0–2 control.

**JTAG_EN location:** `DEBUG_CTRL` register (`0x03[0]`) in the register map. `GPIO_DIR` (`0x04`), `GPIO_OUT` (`0x05`), and `GPIO_IN` (`0x06`) control pad direction and drive value in normal mode.

### Chip reset (1 pad, input)

| Pad name | Dir | Connected to | Description |
|---|---|---|---|
| `RESETB` | in | RPi GPIO or power-on RC | Active-low. Resets all logic including PicoRV32. CPU reset is additionally software-controlled via SPI register for BIST-then-boot sequence. |

---

## Supply and ground pads (3 pads)

| Pad name | Voltage | Count | Notes |
|---|---|---|---|
| `VDD_IO` | 3.3V | 1 | Powers GF180 5V-capable padring cells in 3.3V operation. External SX1257 SPI, IQ data, and SX1302 interfaces are 3.3V CMOS. |
| `VDD_CORE` | 3.3V | 1 | Core digital + SRAM supply. Single pad — IR drop must be verified in floorplan. |
| `GND` | 0V | 1 | Ground. Single pad — placement should favour the highest switching-current region. |

---

## Pad budget summary

| Group | Count |
|---|---|
| RX data (IQ_DATA_I/Q ×4) | 8 |
| Clock (IQ_CLK) | 1 |
| ΣΔ re-mod to SX1302 | 2 |
| SPI bus (MOSI/MISO/SCK + CS_A[1:0] + HOST_CS) | 6 |
| JTAG/IRQ/GPIO mux (TCK_IRQ + TMS_GPIO0 + TDI_GPIO1 + TDO_GPIO2) | 4 |
| RESETB | 1 |
| **Signal subtotal** | **22** |
| VDD_IO (3.3V) | 1 |
| VDD_CORE (3.3V) | 1 |
| GND | 1 |
| **Supply/ground subtotal** | **3** |
| **Total** | **25** |

---

## Pads NOT on ASIC

The following signals are board-level only — no ASIC pad allocated:

| Signal | Reason | Disposition |
|---|---|---|
| SX1257 DIO0–DIO3 (×4 devices) | 0 spare ASIC pads | PLL lock polled via `RegModeStatus` (0x11) over SPI instead |
| SX1257 individual NSS (×4) | Replaced by 74HC139 decoder | ASIC drives 2-bit address `CS_A[1:0]`; decoder generates individual active-low NSS lines on the PCB |
| SX1257 RESET (pin 9, ×4) | 0 spare ASIC pads | Decision pending: floating (POR only) or RPi GPIO |
| SX1257 CLK_IN (pin 11, ×4) | Not needed — XTB shared TCXO used for lock | Leave NC on all 4 devices |
| SX1257 CLK_OUT (pin 10) | SX1257_1: CLK_OUT → SX1302 CLK_IN (PCB trace, no ASIC pad) | SX1257_2–4: leave NC |
| SE2435L CTX/CPS (ant 3/4) | Covered by JTAG/GPIO mux pads | GPIO_0–2 (`TMS_GPIO0`, `TDI_GPIO1`, `TDO_GPIO2`) available when `JTAG_EN=0`; see [SE2435L Front-End Module](blocks/SE2435L%20Front-End%20Module.md) |

---

## Open items

- Physical pad placement / ordering around die perimeter — pending floorplan
- Confirm `RESETB` is a dedicated pad vs. managed by chipathon harness (Caravel or equivalent)
- Resolve SE2435L_3/4 CPS control source before PCB layout — `TMS_GPIO0` (GPIO_0) and `TDI_GPIO1` (GPIO_1) are candidates for control signals
- Resolve SX1257 RESET (floating vs. RPi-controlled) before PCB layout
- **IR drop verification required** — single VDD_CORE and single GND pad; floorplan must place power pad near highest switching-current block (ΣΔ decimators or PicoRV32) and rely on on-chip power mesh; may need decoupling capacitor cells near critical blocks
- **Pad-library assumption** — chipathon integration documentation provides 5V-capable GF180 IO cells, not native 3.3V-only pad cells; current plan is to run those pads from a 3.3V `VDD_IO` rail for 3.3V board signaling, accepting any speed impact noted by the integration team
- **Consider power ring strategy** — GF180MCU IO ring includes power rails; confirm whether VDD_CORE/GND pads feed a global ring or require explicit mesh routing in the core
