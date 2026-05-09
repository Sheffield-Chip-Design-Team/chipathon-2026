# SWD TAP (PicoRV32 Debug)

Control block. See [System Architecture](../System%20Diagram.md) for context.

**Owner:** TBD
**Status:** Not started

---

## Function

Serial Wire Debug (SWD) interface for post-silicon PicoRV32 firmware debugging. Exposes two pads (`SWDCLK` + `SWDIO`) consumed by an external probe (e.g. J-Link, DAPLink, or Raspberry Pi bit-bang SWD).

---

## Interface

| Port | Direction | Width | Description |
| --- | --- | --- | --- |
| `SWDCLK` | in | 1 | Clock from probe |
| `SWDIO` | bidir | 1 | Bidirectional data |
| `clk_32m` | in | — | Master clock (synchroniser) |
| `rst_n` | in | — | — |
| `cpu_halt` | out | 1 | Halts PicoRV32 for register access |
| `cpu_reg_addr` | out | 5 | Register file address for read/write |
| `cpu_reg_rdata` | in | 32 | Register file read data |
| `cpu_reg_wdata` | out | 32 | Register file write data |
| `cpu_reg_we` | out | 1 | Register write enable |
| `mem_addr` | out | 32 | Memory access address |
| `mem_rdata` | in | 32 | Memory read data |
| `mem_wdata` | out | 32 | Memory write data |
| `mem_we` | out | 1 | Memory write enable |

---

## Implementation notes

**Scope.** A minimal SWD TAP is sufficient: halt/resume, register read/write, memory read/write. Full CoreSight DAP is out of scope. Target implementation: ~1,000–2,000 gates.

**Existing IP.** Consider using an existing open-source SWD TAP implementation compatible with PicoRV32's debug port. The PicoRV32 repo documents a debug interface; adapt for SWD framing.

**SWDIO bidir.** Requires a bidirectional pad with output enable. Drive direction controlled by SWD protocol state (host drives during writes; TAP drives during reads).

**Clock domain.** SWD clock is asynchronous to 32 MHz. Synchronise SWDCLK edges into the 32 MHz domain; alternatively, implement the TAP fully in the SWDCLK domain with handshake to PicoRV32.

**Post-silicon priority.** This block is a bring-up aid, not in the functional data path. If gate budget is tight, drop it and rely on SPI register readback for debugging.

---

## Verification

| Test | Method | Pass criterion |
| --- | --- | --- |
| Halt + register read | cocotb SWD model; halt PicoRV32; read PC | Correct PC value |
| Memory read | Read known IMEM contents | Data matches what was loaded |
| Resume | Release halt | PicoRV32 continues execution |
| Breakpoint (if supported) | Set PC match; resume | Halts at correct instruction |

---

## Related blocks

- [PicoRV32 Integration](PicoRV32%20Integration.md) — debug target
- [System Architecture](../System%20Diagram.md) — `SWDCLK` / `SWDIO` pads
