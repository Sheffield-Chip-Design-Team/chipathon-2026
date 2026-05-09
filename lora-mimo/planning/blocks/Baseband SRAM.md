# Baseband SRAM (384 KB)

Memory block. See [System Architecture](../System%20Diagram.md) for context.

**Owner:** TBD
**Status:** Not started

---

## Function

384 KB single-port OpenRAM macro on GF180MCU. Shared between the FFT engine and PicoRV32 firmware via a priority arbiter. Two logical regions with non-overlapping address ranges.

---

## Memory map

| Region | Address range | Size | Owner | Contents |
| --- | --- | --- | --- | --- |
| FFT working buffer | `0x00000`–`0x07FDF` | 32 KB | FFT engine | int16 complex working buffer; one antenna at a time; overwritten each run |
| FFT H output | `0x07FE0`–`0x07FFF` | 32 B | FFT engine | Z_j[k_A/B] for all antennas × 2 nodes; written during PEAK; firmware reads for H estimation |
| Sample capture | `0x08000`–`0x5FFFF` | 352 KB | Capture controller / host | Raw int8 I+Q from decimators; also FFT input source; paused during FFT |

Total: 0x60000 = 393,216 bytes = 384 KB.

---

## Interface (arbitrated)

| Port | Direction | Width | Description |
| --- | --- | --- | --- |
| `addr` | in | 19 | Byte address (0x00000–0x5FFFF) |
| `wdata` | in | 32 | Write data (32-bit word) |
| `rdata` | out | 32 | Read data |
| `we` | in | 1 | Write enable |
| `req` | in | 2 | Request from [0]=FFT, [1]=PicoRV32 |
| `grant` | out | 2 | Grant to each requester |
| `clk` | in | — | 32 MHz |
| `rst_n` | in | — | — |

Word-addressed access: byte address >> 2 = word address. Byte enables optional (add if needed for partial writes).

---

## Arbiter

Simple fixed-priority arbiter: FFT engine has priority over PicoRV32 during READ/COMPUTE/PEAK phases. PicoRV32 is stalled (wait-state on Wishbone) until granted.

```
if req[0]:   grant[0] = 1, grant[1] = 0   // FFT wins
else:        grant[0] = 0, grant[1] = 1   // PicoRV32 wins
```

**Capture/FFT region separation.** FFT working buffer (`0x00000`–`0x03FFF`) and capture region (`0x04000`–`0x5FFFF`) are distinct address ranges. The FFT reads from capture and writes to working buffer — no address conflict. The capture controller freezes its write pointer while `fft_active` is high (asserted by FFT engine).

---

## OpenRAM generation

Generate using the GF180MCU OpenRAM PDK configuration:

```
word_size  = 32          # bits
num_words  = 98304       # 384 KB / 4 bytes = 96K words... 
                         # SRAM compiler may require power-of-2 sizes
                         # Round up to 131072 (512 KB) if needed,
                         # or split into two 192 KB macros
```

**Action required before floorplan:** Run the GF180MCU OpenRAM compiler to get actual area, timing, and power numbers. Estimated area ~3.15 mm² for 384 KB — verify before committing to floorplan. If a single 384 KB macro is not available, split into 2× 192 KB macros with address decode logic.

---

## Verification

| Test | Method | Pass criterion |
| --- | --- | --- |
| Write + read back | cocotb: write known pattern, read back | Byte-identical |
| Arbiter priority | FFT + PicoRV32 simultaneous | FFT gets grant; PicoRV32 stalls then gets access |
| Full address range | Sweep all 96K words | No stuck bits |
| Capture region isolation | Write capture data; run FFT | FFT staging (0x00000–0x07FFF) unaffected |

---

## Related blocks

- [FFT Engine](FFT%20Engine.md) — primary user of FFT staging region
- [PicoRV32 Integration](PicoRV32%20Integration.md) — firmware access + capture region
- [SPI Slave](SPI%20Slave.md) — host burst-reads capture region via SPI
- [System Architecture](../System%20Diagram.md) — area estimate; OpenRAM action item
