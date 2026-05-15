# Memory Strategy

Covers all on-chip SRAM in the design: macro selection, voltage domain, BIST, and fallback policy.

---

## Macro allocation

| Instance | Size | Macro | VDD | Block |
|---|---|---|---|---|
| SRAM0 (ch0/ch1) | 512 B | `gf180mcu_ocd_ip_sram__sram512x8m8wm1` | 3.3 V | Frontend Buffer Controller |
| SRAM1 (ch2/ch3) | 512 B | `gf180mcu_ocd_ip_sram__sram512x8m8wm1` | 3.3 V | Frontend Buffer Controller |
| CPU SRAM (unified) | 4 KB | `gf180mcu_ocd_ip_sram__sram1024x8m8wm1` ×4 | 3.3 V | PicoRV32 Integration |

**Total on-chip SRAM: 5 KB**

A single unified SRAM holds PicoRV32 `.text`, `.data`, `.bss`, and stack. No separate IMEM/DMEM split — one AHB-Lite port, one BIST instance. Linker places `.text` at `0x00000` and stack at `0x00FFF` (growing downward).

**Area:** 4 × `sram1024x8m8wm1` = **~0.62 mm²**. Frontend Buffer adds 2 × `sram512x8m8wm1` = ~0.19 mm². Total SRAM area ~**0.81 mm²**.

### Core voltage decision — 3.3 V

**The core logic supply is 3.3 V.** All SRAM macros (`gf180mcu_ocd_ip_sram`) are 3.3 V devices. Running the core at 3.3 V places all logic and all SRAMs on the same rail, eliminating any need for level shifters at SRAM interfaces. It also allows `VDD_CORE` and `VDD_IO` to share a supply (both 3.3 V), simplifying the board power tree.

3.3 V standard cells have shorter propagation delay than 1.8 V equivalents (higher overdrive current), so timing closure at 32 MHz is expected to be straightforward for the combinational logic. The SRAM macros have a minimum cycle time of **55.6 ns** (~18 MHz) at 3.3 V — this is an intrinsic macro limit, not a voltage issue. AHB-Lite accesses to IMEM/DMEM therefore require a 2-cycle multi-cycle path constraint in the timing flow; PicoRV32's `mem_valid`/`mem_ready` handshake handles this naturally without a separate divided clock.

### SRAM macro source

All macros are from the **`gf180mcu_ocd_ip_sram`** experimental library (https://github.com/RTimothyEdwards/gf180mcu_ocd_ip_sram). Physical dimensions:

| Macro | Width | Height | Area |
|---|---|---|---|
| `sram256x8m8wm1` | 301.3 µm | 224.9 µm | ~0.068 mm² |
| `sram512x8m8wm1` | 301.3 µm | 321.9 µm | ~0.097 mm² |
| `sram1024x8m8wm1` | 301.3 µm | 515.8 µm | ~0.156 mm² |

Frontend Buffer uses 2 × `sram512x8m8wm1` = **~0.194 mm²**. IMEM and DMEM each use 32 × `sram1024x8m8wm1` = **~4.99 mm² each** — the dominant area contributor. Target firmware footprint is **2–4 KB** (IMEM) with a matching DMEM. At 4 KB each: 8 macros = ~1.25 mm². At 2 KB each: 4 macros = ~0.62 mm².

---

## Rationale for the split

### DSP SRAMs — experimental macros at 3.3 V

The Frontend Buffer SRAMs (SRAM0, SRAM1) are in the real-time acquisition critical path. A single stuck bit causes a corrupt delayed-sample read, which degrades the SC autocorrelation statistic and can prevent preamble detection entirely. There is no runtime recovery path short of resetting the block.

The `sram512x8m8wm1` macro size exactly matches the required 2-channel × 128-sample rolling window at 8-bit storage. No level shifters are required — core logic and SRAM share the 3.3 V rail.

The 55.6 ns SRAM cycle time is not a concern for the frontend buffer: the ΣΔ decimated sample rate (125 kS/s–1 MS/s) is far below 18 MHz. The buffer controller issues at most one SRAM access per decimated sample; no multi-cycle constraint is needed here.

### CPU SRAMs — experimental macros at 3.3 V

IMEM and DMEM are not in any sample-rate path. Their only hard timing requirement is AHB-Lite read latency (≤ 2 cycles at 32 MHz). Both macros are reloaded or re-initialised on every power cycle: IMEM is written by the host over SPI before CPU reset is released; DMEM is initialised by the C runtime at boot. A partial fault is therefore recoverable without hardware modification (see Fallback strategy below).

The 55.6 ns SRAM cycle time requires a **2-cycle multi-cycle path** on all IMEM/DMEM accesses at 32 MHz. PicoRV32's `mem_valid`/`mem_ready` handshake already supports variable-latency memory — the SRAM controller holds `mem_ready` low for one extra cycle on every access. No divided clock is needed. This must be captured as a multicycle path exception in the SDC constraints file.

**Firmware footprint target: ≤4 KB total (text + data + stack).** PicoRV32 firmware handles: W vector computation from Z_j (MRC weights), TDD antenna switching, AGC loop, SX1257 init via SPI master. These tasks are simple fixed-point loops with no OS, no floating point, and minimal data structures. The unified 4 KB SRAM provides comfortable headroom for both code and data.

---

## Spreading factor support

The DSP SRAM depth (512 B per macro, 4 bytes per sample time) determines the maximum SF the Frontend Buffer can serve. SC only needs M samples of delayed storage — the current sample arrives live from the decimator. Using a **D=M read-before-write** access pattern (read the M-old byte, then immediately overwrite it with the current byte at the same address) eliminates the need for a 2M-deep buffer:

| Config | D | Bytes/macro | Max SF | Notes |
|---|---|---|---|---|
| 2 × 512 B, 8-bit storage, D=M | M | M×4 | **SF7** (M=128, 512 B exactly) | Baseline hardware |
| 2 × 512 B, 16-bit storage, D=M | M | M×8 | SF6 (M=64, 512 B exactly) | No margin |
| 4 × 512 B, 8-bit storage, D=M | M | M×4 | **SF8** (M=256, 1024 B) | Add 2 macros |
| 8 × 512 B, 8-bit storage, D=M | M | M×4 | **SF9** (M=512, 2048 B) | Add 6 macros |

SF8 support costs 2 additional proven macros (total 4 DSP SRAMs, 2 kB). The access pattern, address controller, and BIST architecture are unchanged — only the address counter width and macro count increase.

---

## BIST

BIST runs at power-on, before the host releases `CPU_RESET` for the CPU SRAMs and before acquisition mode is entered for the DSP SRAMs. All results are readable via SPI.

### DSP SRAMs (proven macros) — pass/fail only

March-5N write/read pattern on each 512 B macro independently. Simple pass/fail result is sufficient because individual bad-word address is not needed for the degraded-mode policy.

| Register | Description |
|---|---|
| `SRAM0_BIST_PASS` | 1 = SRAM0 passed all March-5N patterns |
| `SRAM1_BIST_PASS` | 1 = SRAM1 passed all March-5N patterns |

Degraded-mode policy:

| SRAM status | Acquisition mode |
|---|---|
| Both pass | Full NR=4 |
| SRAM0 fails | NR=2 using ch2/ch3 (SRAM1) |
| SRAM1 fails | NR=2 using ch0/ch1 (SRAM0) |
| Both fail | Bypass only; SC acquisition disabled |

### CPU SRAM (unified) — address-level reporting

March C- on the unified 4 KB SRAM (1 K × 32-bit words). Reports the first failing word address and the failing bit mask at that address.

| Register | Width | Description |
|---|---|---|
| `SRAM_BIST_PASS` | 1 | 1 = March C- found no faults |
| `SRAM_BIST_FAIL_ADDR` | 10 | Word address of first failing word (in units of 4 bytes) |
| `SRAM_BIST_FAIL_BITS` | 32 | Bit mask of failing bits at `SRAM_BIST_FAIL_ADDR` |

**March C- timing at 32 MHz:** 1 K words × 11 passes × ~4 cycles/word ≈ 44 K cycles ≈ 1.4 ms. Negligible at boot.

**BIST controller sequencing:**

```
Power-on
  ↓
DSP SRAM BIST (SRAM0, SRAM1 — parallel or sequential)
  ↓
CPU SRAM BIST (unified 4 KB — CPU held in reset)
  ↓
All BIST_PASS registers valid and readable via SPI
  ↓
Host reads results, programs overlay if needed (see below)
  ↓
Host releases CPU_RESET → PicoRV32 boots
```

---

## JTAG recovery — total CPU SRAM failure

If the CPU SRAM is completely unusable (BIST shows pervasive faults, overlay exhausted), normal firmware execution is impossible. However JTAG provides a partial recovery path that does not require any working SRAM:

**What works without SRAM:**

| Capability | Mechanism | Requires SRAM? |
|---|---|---|
| Halt CPU | DM asserts debug interrupt; CPU enters debug mode | No |
| Read/write x0–x31 | Abstract `Access Register` command | No — operates entirely within the register file |
| Single-step | DM resumes for one instruction, re-halts | Only if PC points to valid memory; useless if SRAM dead |
| Execute from program buffer | DM loads instructions into its own scratchpad; CPU fetches from DM, not SRAM | No — program buffer is inside the DM |
| Access ASIC SPI registers | Write an SPI transaction sequence into program buffer; execute it | No |
| Read ASIC register state | Halt, execute `lw` from peripheral address via program buffer | No |

**Program buffer execution** is the key capability: with 8–16 instruction slots in the DM, you can inject a small diagnostic loop — e.g. read `IRQ_STATUS`, read `SC_DBG_FLAGS`, or issue an SX1257 SPI transaction — and execute it with the CPU fetching entirely from the DM scratchpad. This allows diagnostic data collection and limited chip control even with a dead SRAM.

**What does not work without SRAM:** the full firmware loop (W computation, AGC, TDD switching) cannot run from the program buffer — it is too large. The DSP datapath (ΣΔ decimators, SC correlator, MRC combiner) continues to run autonomously in hardware regardless of CPU state; only the software control loop is lost.

**Implication for JTAG TAP implementation:** the DM should implement at least an 8-instruction program buffer and full abstract `Access Register` support (all 32 GPRs + CSRs). See [JTAG TAP](blocks/JTAG%20TAP.md).

---

## Fallback strategy — bad-word overlay

Writing a correct value to a stuck SRAM cell does not fix it: the cell overrides the write driver and the bad data reappears on every subsequent read. The overlay approach bypasses the macro read entirely for known-bad addresses.

### Architecture

The unified CPU SRAM has a single 16-entry content-addressable overlay:

```
CAM entry: { valid[1], addr[9:0], data[31:0] }   (total: 16 × 43 bits)
```

On every IMEM or DMEM read:

```
if any valid CAM entry matches read_addr:
    return CAM_data      ← ignores SRAM output
else:
    return SRAM_data
```

The CAM lookup adds at most 1 pipeline stage (combinational priority encoder). At 32 MHz with a simple 16-entry CAM this is well within timing.

### Programming the overlay

1. Host reads `SRAM_BIST_FAIL_ADDR` and `SRAM_BIST_FAIL_BITS` via SPI.
2. Host relinks the firmware image with a linker memory map that excludes the bad word address (the linker assigns code and data to all other addresses, leaving the bad address as a gap).
3. Host writes the correct word for the bad address into the overlay CAM via SPI registers (`SRAM_OVERLAY_n_ADDR`, `SRAM_OVERLAY_n_DATA`, `SRAM_OVERLAY_n_VALID` for n = 0..15).
4. Host writes the firmware image to the SRAM via SPI burst-write. The correct word is also written to the SRAM at the bad address — it may not stick, but the CAM overrides on read.
5. Host releases `CPU_RESET`. PicoRV32 boots; reads to bad addresses return CAM data.

### Coverage and limits

| Scenario | Outcome |
|---|---|
| ≤ 16 isolated bad words, none at reset vector | Fully recoverable via overlay + firmware relink |
| Bad word at reset vector (0x00000–0x00003) | Unrecoverable for normal boot; JTAG program buffer can still execute diagnostics |
| > 16 bad words or large contiguous fault | Overlay exhausted; normal firmware execution impossible; JTAG program buffer remains available for chip diagnostics and register inspection |
| `SRAM_BIST_PASS = 1` | Normal boot; no overlay needed |

The probability of a fault in the 4-byte reset vector is low for a random stuck-bit distribution across 32 KB (probability ~0.006%). A contiguous large fault block is more characteristic of a process defect than a random bitcell failure and is not recoverable by any software means.

### JTAG as a diagnostic complement

The JTAG TAP provides direct AHB-Lite access to IMEM and DMEM. JTAG is useful for:

- Reading back IMEM contents after firmware load to verify the overlay is working correctly
- Single-stepping the CPU through the boot sequence to observe the first fetch from a patched address
- Diagnosing DMEM faults at runtime by reading stack/data addresses while the CPU is halted

JTAG does not fix stuck cells (same limitation as SPI writes), but it provides a debug path that does not require any additional test infrastructure.

---

## Boot sequence summary

```
Power-on
    │
    ├─ DSP SRAM BIST (SRAM0, SRAM1)
    │      ├─ Both pass  → NR=4 acquisition ready
    │      ├─ One fails  → NR=2 degraded mode
    │      └─ Both fail  → bypass mode only
    │
    ├─ IMEM BIST → IMEM_BIST_PASS, IMEM_BIST_FAIL_ADDR/BITS
    ├─ DMEM BIST → DMEM_BIST_PASS, DMEM_BIST_FAIL_ADDR/BITS
    │
    └─ Host reads BIST results via SPI
           │
           ├─ All pass  ──────────────────────────── load firmware → release CPU_RESET
           │
           └─ CPU SRAM fault found
                  │
                  ├─ ≤ 16 isolated bad words, not at reset vector
                  │      relink firmware → program overlay CAM → load firmware → release CPU_RESET
                  │
                  └─ Bad reset vector or > 16 contiguous bad words
                         → chip cannot boot; report failure
```

---

## Register map additions

These registers live in the main register map at `0x10000` (AHB-Lite peripheral region).

| Register | Offset | R/W | Description |
|---|---|---|---|
| `SRAM0_BIST_PASS` | TBD | R | DSP SRAM0 BIST result (1=pass) |
| `SRAM1_BIST_PASS` | TBD | R | DSP SRAM1 BIST result (1=pass) |
| `IMEM_BIST_PASS` | TBD | R | IMEM March C- result (1=pass) |
| `IMEM_BIST_FAIL_ADDR` | TBD | R | First failing IMEM word address |
| `IMEM_BIST_FAIL_BITS` | TBD | R | Failing bit mask at `IMEM_BIST_FAIL_ADDR` |
| `DMEM_BIST_PASS` | TBD | R | DMEM March C- result (1=pass) |
| `DMEM_BIST_FAIL_ADDR` | TBD | R | First failing DMEM word address |
| `DMEM_BIST_FAIL_BITS` | TBD | R | Failing bit mask at `DMEM_BIST_FAIL_ADDR` |
| `IMEM_OVERLAY_n_ADDR` (n=0..15) | TBD | R/W | IMEM overlay CAM entry n address |
| `IMEM_OVERLAY_n_DATA` (n=0..15) | TBD | R/W | IMEM overlay CAM entry n data word |
| `IMEM_OVERLAY_n_VALID` (n=0..15) | TBD | R/W | IMEM overlay CAM entry n enable |
| `DMEM_OVERLAY_n_ADDR` (n=0..15) | TBD | R/W | DMEM overlay CAM entry n address |
| `DMEM_OVERLAY_n_DATA` (n=0..15) | TBD | R/W | DMEM overlay CAM entry n data word |
| `DMEM_OVERLAY_n_VALID` (n=0..15) | TBD | R/W | DMEM overlay CAM entry n enable |
| `BIST_CTRL` | TBD | R/W | Bit 0: re-run BIST; Bit 1: BIST in progress (R) |

---

## Related documents

- [Frontend Buffer Controller](blocks/Frontend%20Buffer%20Controller.md) — DSP SRAM BIST and degraded-mode policy
- [PicoRV32 Integration](blocks/PicoRV32%20Integration.md) — CPU SRAM BIST, overlay, boot sequence
- [Register Map](Register%20Map.md) — BIST and overlay register addresses (TBD)
- [SWD TAP](blocks/SWD%20TAP.md) — diagnostic complement to overlay
