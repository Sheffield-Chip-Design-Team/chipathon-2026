# IRQ Controller

Control block. See [System Architecture](../System%20Diagram.md) for context.

**Owner:** TBD
**Status:** Not started

---

## Function

Collects interrupt sources from DSP blocks and routes them to PicoRV32 (internal) and to the RPi host (external GPIO pad). Provides sticky status/clear bits that are visible through the internal AHB-Lite interface and mirrored to the SPI-visible `IRQ_STATUS`/`IRQ_CLEAR` registers.

---

## Interrupt sources

| Source | Block | Description |
| --- | --- | --- |
| `corr_lock` | Correlator Bank / Packet Control FSM | Preamble detected — packet FSM entered `PREAMBLE_ACQ` |
| `training_done` | Training Accumulator / Packet Control FSM | Preamble accumulation complete — firmware should compute W |
| `W_missed_packet` | Packet Control FSM | W was not committed before packet completion; packet stayed in bypass |
| `packet_done` | Packet Control FSM | FSM returned to IDLE (packet ended or timed out) |
| `tx_prep` | TX_CTRL[0] register | Host requests TX preparation — disable RX antennas, switch SX1257s to TX |
| `tx_done` | TX_CTRL[1] register | Host signals TX complete — restore SX1257s to RX, re-enable antennas |

---

## Interface

| Port | Direction | Width | Description |
| --- | --- | --- | --- |
| `corr_lock` | in | 1 | From correlator bank |
| `training_done` | in | 1 | From Training Accumulator / Packet Control FSM |
| `W_missed_packet` | in | 1 | From Packet Control FSM |
| `packet_done` | in | 1 | From Packet Control FSM (FSM returned to IDLE) |
| `irq_out` | out | 1 | Level-high IRQ to PicoRV32 |
| `IRQ` | out | 1 | GPIO pad to RPi (active high) |
| `wb_addr` | in | 8 | AHB-Lite address |
| `wb_rdata` | out | 32 | IRQ status register |
| `wb_wdata` | in | 32 | IRQ clear (write 1 to clear) |
| `wb_we` | in | 1 | — |
| `wb_stb` | in | 1 | — |
| `wb_ack` | out | 1 | — |
| `clk_32m` | in | — | Master clock |
| `rst_n` | in | — | — |

---

## Register (AHB-Lite and SPI mirror, read/clear)

Mirrors `IRQ_STATUS` (`0x49`) and `IRQ_CLEAR` (`0x4A`) in the register map.

| Bit | Source | Clear |
| --- | --- | --- |
| [0] | `corr_lock` | Write 1 to bit [0] |
| [1] | `training_done` | Write 1 to bit [1] |
| [2] | `W_missed_packet` | Write 1 to bit [2] |
| [3] | `packet_done` | Write 1 to bit [3] |
| [4] | reserved | — |
| [5] | `tx_prep` | Write 1 to bit [5] |
| [6] | `tx_done` | Write 1 to bit [6] |

`irq_out` = OR of all uncleared sources. `IRQ` pad mirrors `irq_out`.

The SPI-facing register map exposes the same bit layout at `IRQ_STATUS` (`0x49`) and `IRQ_CLEAR` (`0x4A`). Firmware and host software should treat the IRQ wire as a doorbell: read `IRQ_STATUS` to identify the source, service the corresponding block/registers, then write 1s to `IRQ_CLEAR` for serviced bits.

---

## Implementation notes

**Level vs edge.** Sources are level signals from their respective blocks. Latch on rising edge into sticky bits. Clear by writing 1 to the corresponding bit. Source block de-asserts its signal after being consumed.

**Clock domain.** All current interrupt sources (`corr_lock`, `h_ready`, `W_missed_packet`, `capture_done`, `capture_overflow`, `tx_prep`, `tx_done`) are generated inside the 32 MHz domain — no CDC required. If any future source comes from outside the 32 MHz domain (e.g. a SX1257 DIO pin), it must pass through a 2-FF synchroniser before entering the sticky-bit latch. Do not add unsynchronised external signals directly to the IRQ OR tree.

**RPi IRQ.** The `IRQ` pad is a level-high output. RPi GPIO should be configured for rising-edge interrupt. RPi firmware reads the SPI Slave status register to determine source, then clears via SPI write.

---

## Verification

| Test | Method | Pass criterion |
| --- | --- | --- |
| corr_lock IRQ | Assert `corr_lock`; read WB register | Bit [0] set; `irq_out` high |
| training_done IRQ | Assert `training_done`; read WB register | Bit [1] set; firmware W computation can start |
| W missed IRQ | Assert `W_missed_packet`; read WB register | Bit [2] set; packet remains bypass |
| packet_done IRQ | Assert `packet_done`; read WB register | Bit [3] set |
| Clear IRQ | Write 1 to bit [0] | Bit [0] clears; `irq_out` low if no other source |
| Multiple simultaneous | Assert all sources | All bits set; `irq_out` high |
| Clear one, others remain | Clear only bit [1] | Bit [0] and [2] still set; `irq_out` still high |

---

## Related blocks

- [Correlator Bank](Correlator%20Bank.md) — `corr_lock` source
- [Training Accumulator](Training%20Accumulator.md) — `training_done` source
- [Packet Control FSM](Packet%20Control%20FSM.md) — `W_missed_packet`, `packet_done` sources
- [PicoRV32 Integration](PicoRV32%20Integration.md) — internal IRQ target
- [System Architecture](../System%20Diagram.md) — `IRQ` pad to RPi
