# IRQ Controller

Control block. See [System Architecture](../System%20Diagram.md) for context.

**Owner:** TBD
**Status:** Not started

---

## Function

Collects interrupt sources from DSP blocks and routes them to PicoRV32 (internal) and to the RPi host (external GPIO pad). Provides a Wishbone-mapped status/clear register.

---

## Interrupt sources

| Source | Block | Description |
| --- | --- | --- |
| `corr_lock` | Correlator Bank | Preamble detected — trigger W computation |
| `capture_done` | Baseband SRAM | Sample capture buffer full |
| `capture_overflow` | Baseband SRAM | Capture buffer overflowed before host read |
| `tx_prep` | TX_CTRL[0] register | Host requests TX preparation — disable RX antennas, switch SX1257s to TX |
| `tx_done` | TX_CTRL[1] register | Host signals TX complete — restore SX1257s to RX, re-enable antennas |

---

## Interface

| Port | Direction | Width | Description |
| --- | --- | --- | --- |
| `corr_lock` | in | 1 | From correlator bank |
| `capture_done` | in | 1 | From SRAM capture logic |
| `capture_overflow` | in | 1 | From SRAM capture logic |
| `irq_out` | out | 1 | Level-high IRQ to PicoRV32 |
| `IRQ` | out | 1 | GPIO pad to RPi (active high) |
| `wb_addr` | in | 8 | Wishbone address |
| `wb_rdata` | out | 32 | IRQ status register |
| `wb_wdata` | in | 32 | IRQ clear (write 1 to clear) |
| `wb_we` | in | 1 | — |
| `wb_stb` | in | 1 | — |
| `wb_ack` | out | 1 | — |
| `clk_32m` | in | — | Master clock |
| `rst_n` | in | — | — |

---

## Register (Wishbone, read/clear)

| Bit | Source | Clear |
| --- | --- | --- |
| [0] | `corr_lock` | Write 1 to bit [0] |
| [1] | `capture_done` | Write 1 to bit [1] |
| [2] | `capture_overflow` | Write 1 to bit [2] |
| [3] | `tx_prep` | Write 1 to bit [3] |
| [4] | `tx_done` | Write 1 to bit [4] |

`irq_out` = OR of all uncleared sources. `IRQ` pad mirrors `irq_out`.

---

## Implementation notes

**Level vs edge.** Sources are level signals from their respective blocks. Latch on rising edge into sticky bits. Clear by writing 1 to the corresponding bit. Source block de-asserts its signal after being consumed.

**Clock domain.** All current interrupt sources (`corr_lock`, `capture_done`, `capture_overflow`, `tx_prep`, `tx_done`) are generated inside the 32 MHz domain — no CDC required. If any future source comes from outside the 32 MHz domain (e.g. a SX1257 DIO pin), it must pass through a 2-FF synchroniser before entering the sticky-bit latch. Do not add unsynchronised external signals directly to the IRQ OR tree.

**RPi IRQ.** The `IRQ` pad is a level-high output. RPi GPIO should be configured for rising-edge interrupt. RPi firmware reads the SPI Slave status register to determine source, then clears via SPI write.

---

## Verification

| Test | Method | Pass criterion |
| --- | --- | --- |
| corr_lock IRQ | Assert `corr_lock`; read WB register | Bit [0] set; `irq_out` high |
| Clear IRQ | Write 1 to bit [0] | Bit [0] clears; `irq_out` low if no other source |
| Multiple simultaneous | Assert all sources | All bits set; `irq_out` high |
| Clear one, others remain | Clear only bit [1] | Bit [0] and [2] still set; `irq_out` still high |

---

## Related blocks

- [Correlator Bank](Correlator%20Bank.md) — `corr_lock` source
- [Baseband SRAM](Baseband%20SRAM.md) — `capture_done/overflow` sources
- [PicoRV32 Integration](PicoRV32%20Integration.md) — internal IRQ target
- [System Architecture](../System%20Diagram.md) — `IRQ` pad to RPi
