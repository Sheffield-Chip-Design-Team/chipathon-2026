# Frontend Buffer Controller

RX path block. See [Non-FFT LoRa Frontend Proposal](../Non-FFT%20LoRa%20Frontend%20Proposal.md) and [SF6 1kB Frontend Buffer Exploration](../SF6%201kB%20Frontend%20Buffer%20Exploration.md) for context.

**Owner:** TBD
**Status:** Draft

---

## Role

Manages the shared 1 kB frontend sample SRAM. Provides a 2-symbol rolling history of DC-removed (pre-dechirp) samples to the SC correlator during acquisition, then freezes or repurposes the memory after lock.

This block owns:

- the write pointer and circular addressing
- the read-before-write access sequence on the single-port SRAM
- the read address generation for the SC delayed-sample path
- the freeze/mode transition after `sc_lock`

It does **not** own:

- dechirping (downstream of the buffer)
- SC correlation arithmetic
- training accumulation (register-based, receives live samples independently of SRAM)

---

## Sample Rate

The decimator delivers samples at **125 kS/s** (32 MHz / R=256, 1× Nyquist — see [ΣΔ Decimator](ΣΔ%20Decimator.md)). At SF6 (M = 2^6 = 64 samples/symbol) and 32 MHz system clock:

```
iq_valid period  = 32 MHz / 125 kS/s = 256 clock cycles
Symbol period    = 64 × 256 = 16,384 cycles = 512 µs
Buffer depth     = 128 sample times = 2 symbols  (8-bit storage)
```

Samples/symbol = 2^SF exactly for all SF at 1× Nyquist — no fractional timing.

---

## Memory Organisation

Two single-port SRAM macros of 512 B each:

| Macro | Channels stored |
|---|---|
| `SRAM0` | ch0 and ch1 |
| `SRAM1` | ch2 and ch3 |

Both macros share a common write pointer. All four channels for the same sample time are read and written together.

### Physical word organisation

The `gf180mcu_fd_ip_sram__sram512x8m8wm1` macro is **8-bit wide** (512 words × 8 bits). Each sample time for two channels (I+Q per channel = 4 bytes) occupies **4 consecutive SRAM addresses**:

```
SRAM0, physical addresses 4k .. 4k+3  (sample time k, channels 0 & 1):
  4k+0:  ch0_I[7:0]
  4k+1:  ch0_Q[7:0]
  4k+2:  ch1_I[7:0]
  4k+3:  ch1_Q[7:0]

SRAM1, physical addresses 4k .. 4k+3  (sample time k, channels 2 & 3):
  4k+0:  ch2_I[7:0]
  4k+1:  ch2_Q[7:0]
  4k+2:  ch3_I[7:0]
  4k+3:  ch3_Q[7:0]
```

k ranges 0 – 127, giving 128 sample times = 2 symbols at SF6. Physical address range: 0 – 511 (9-bit address A[8:0]).

Four sequential single-byte accesses to one macro deliver the full two-channel IQ pair for one sample time. Both macros are accessed in lockstep (same address sequence each cycle), so all four channel bytes for a sample time are captured in 4 cycles total.

### Sample Width and Depth

Decimated samples are either **12-bit** or **16-bit** I+Q per channel (TBD — see open questions). For planning purposes both cases are considered.

**Word width per macro per sample time:**

| Storage | Bytes / sample time (2 ch) | Max sample times in 512 B | Max SF (D=M, see below) |
|---|---|---|---|
| 8-bit I + 8-bit Q | 4 B | 128 | **SF7** (M=128) |
| 16-bit I + 16-bit Q | 8 B | 64 | SF6 (M=64, exactly full — no margin) |

At 8-bit storage the 512-word macro supports up to **SF7** using the D=M read-before-write access pattern described below. At 16-bit storage depth falls to 64 sample times, limiting operation to SF6 with no margin; a 2-kB buffer (4 macros) is required for 16-bit SF7.

**The 2-symbol rolling window required for SC correlation only fits within 1 kB if samples are stored at 8-bit precision per component.**

At 16-bit storage the 1 kB buffer holds only 1 symbol per macro — insufficient for a 2-symbol SC window.

### Resolution options

Three paths to resolve the depth vs precision tradeoff:

1. **Saturate to 8 bits for SRAM storage.** The SC correlator is a detection and timing block, not a precision estimator. 8-bit storage is likely acceptable for SC performance if the write path uses **saturating arithmetic** (not bitfield truncation). The training accumulator receives full-precision live samples directly and is unaffected — it does not read from SRAM. This is the preferred option if the 1 kB budget is hard.

   **Why saturation, not truncation.** Two failure modes exist if samples are stored by taking the bottom 8 bits directly:
   - *High signal / no AGC:* a strong signal with amplitude > 127 wraps around (e.g. +300 stored as +44), corrupting the SC correlation entirely.
   - *Low signal:* weak signals near the noise floor are unaffected by the choice of saturation vs truncation — quantisation noise slightly raises the SC noise floor but SC sensitivity is already SNR-limited at this point.

   The write path must apply:
   ```
   stored_I = clamp(sample_I >> (SAMPLE_W - 8), -128, 127)
   stored_Q = clamp(sample_Q >> (SAMPLE_W - 8), -128, 127)
   ```

   Saturation preserves the sign and approximate magnitude of a clipped sample. SC correlation quality degrades gracefully at very high signal amplitude rather than producing random values.

   The SX1257 AGC should normally prevent consistent saturation, but saturation arithmetic is still required as a safety net for burst-level variations and AGC settling transients.

   **Required validation:** Simulate SC detection threshold, false-alarm rate, and timing accuracy with 8-bit saturated storage vs full-precision inputs, swept across the full SNR range including both weak-signal and strong-signal extremes.

2. **Increase SRAM to 2 kB.** Use 4 × 512 B macros (or 2 × 1 kB macros). Doubles area cost. Removes the precision tradeoff entirely. Choose this if 16-bit SC fidelity is required or if the SC threshold is sensitive to quantisation noise.

3. **Reduce to NR=2 in acquisition.** Only store 2 channels (one macro) at full width and use 2-antenna SC detection. Not preferred — loses detection diversity gain.

**This decision is deferred pending ADC precision confirmation. The controller is designed to support either mode via a parameter.**

---

## Logical Address Space and Buffer Depth

SC requires one M-sample-delayed copy of each branch — **D = M sample times** is the minimum buffer depth. The current sample arrives live from the decimator; only the delayed sample requires SRAM storage.

### D = M — read-before-write (preferred, supports SF6 and SF7)

```
D = M = 2^SF

sample_ptr   =  wr_ptr mod D           // same address for read and write
physical_base = 4 * sample_ptr         // byte 0 of the 4-byte group
```

Read and write target the **same physical addresses** each sample time. The read phase (capturing the M-old delayed sample) runs first; the write phase then overwrites those addresses with the current sample. Because reads complete before writes begin, the delayed sample is captured correctly before it is overwritten — no corruption.

At SF7: D = M = 128, physical addresses 0–511 (exactly the 512-word macro capacity).  
At SF6: D = M = 64, physical addresses 0–255 (256 B used; 256 B unused).

### D = 2M — separate read/write addresses (legacy; wastes half the buffer)

```
D = 2M

sample_ptr_write   =  wr_ptr mod 2M
sample_ptr_delayed = (wr_ptr - M) mod 2M   // M positions behind write

physical_base_write   = 4 * sample_ptr_write
physical_base_delayed = 4 * sample_ptr_delayed
```

Read and write addresses are M positions apart. At SF6: D = 128, physical addresses 0–511 (full macro). **At SF7: D = 256, requires 1024 B — does not fit in the 512 B macro.** This formulation is not needed and is not used in this implementation.

### Address summary

| SF | M | D (=M) | Physical byte range | Macro utilisation |
|---|---|---|---|---|
| 6 | 64 | 64 | 0–255 | 50% |
| 7 | 128 | 128 | 0–511 | 100% |

Both SRAM0 and SRAM1 use the same `wr_ptr` and physical address sequence, accessed in lockstep each cycle.

---

## Access Protocol

The `gf180mcu_fd_ip_sram__sram512x8m8wm1` is **single-port**. Each sample time requires 4 byte reads (delayed sample) followed by 4 byte writes (current sample) — **8 cycles total**, both macros in lockstep.

At 125 kS/s with a 32 MHz system clock there are **256 clock cycles** per `iq_valid` pulse. The 8-cycle access uses 3% of the available window.

### Per-sample-time sequence (D = M, read-before-write)

With D = M, read and write target the **same** base address. Reads run first so the M-old delayed data is captured before it is overwritten.

```
Cycle 0:  iq_valid asserts. Latch incoming raw sample.
            base = 4 * (wr_ptr mod M)    // same address for read and write

Cycles 1–4:  Read phase (GWEN=1):
  Cycle 1:  A = base+0.  Q → ch0_I_del (SRAM0),  ch2_I_del (SRAM1).
  Cycle 2:  A = base+1.  Q → ch0_Q_del,           ch2_Q_del.
  Cycle 3:  A = base+2.  Q → ch1_I_del,           ch3_I_del.
  Cycle 4:  A = base+3.  Q → ch1_Q_del,           ch3_Q_del.
            All 4 delayed bytes captured and held for SC correlator.

Cycles 5–8:  Write phase (GWEN=0):
  Cycle 5:  A = base+0.  D = ch0_I_cur (SRAM0),  ch2_I_cur (SRAM1).
  Cycle 6:  A = base+1.  D = ch0_Q_cur,           ch2_Q_cur.
  Cycle 7:  A = base+2.  D = ch1_I_cur,           ch3_I_cur.
  Cycle 8:  A = base+3.  D = ch1_Q_cur,           ch3_Q_cur.

Cycles 9+:  CEN=1 (macros idle) until next iq_valid.
```

**Why read-before-write is safe.** The SRAM outputs the value stored at `base` on cycles 1–4 — this is the sample written M `iq_valid` periods ago (the desired delayed sample). The write on cycles 5–8 then replaces it with the current sample. The ordering is enforced by the GWEN signal: read (GWEN=1) completes before write (GWEN=0) begins. No data hazard.

**Timing margin.** 8 active cycles + 248 idle cycles per `iq_valid`. The timing margin is identical at SF6 and SF7 — the sample rate (125 kS/s) and cycle budget (256 cycles) are independent of SF.

**16-bit storage.** At 16-bit storage each sample time occupies 8 bytes. At SF7 (M=128): 128 × 8 = 1024 B per macro — exceeds the 512 B macro. 16-bit SF7 requires 4 macros (2 kB). 16-bit SF6: 64 × 8 = 512 B — exactly fits but leaves no headroom.

---

## Operating Modes

### Mode 1 — Acquisition (rolling)

Default state after reset and between packets.

- `wr_ptr` increments every `iq_valid`
- SRAM written with current incoming sample
- SRAM read returns `(wr_ptr - M + 1) mod D` for SC correlator
- SC correlator receives current sample (live) and delayed sample (from SRAM) every cycle

### Mode 2 — Locked / Freeze

Entered when `sc_lock` asserts.

- `wr_ptr` stops incrementing (or increments into a separate post-lock capture region)
- SRAM contents are frozen at the lock boundary
- The 2M samples preceding `timing_ref` are preserved for optional post-lock use (short timing confirmation or diagnostic readback)
- SC correlator no longer needs SRAM reads; block can gate SRAM clock if power saving is desired

### Mode 3 — Post-lock Observation (optional)

If the post-lock Sync/SFD region needs a short sample snapshot:

- Re-enable `wr_ptr` increment for up to `2M` samples after lock
- Overwrites acquisition history with post-lock samples
- Useful for a short downchirp/sync timing confirmation step if added later

This mode is optional and not required for the baseline non-FFT path.

### Mode 4 — Idle / Reset

Between packets, before first acquisition. `wr_ptr` may run but SC output is gated by a minimum fill count (buffer must have at least M valid samples before SC reads are meaningful).

Minimum fill requirement:

```
buf_valid = (sample_count >= M)
```

SC correlator enable gated by `buf_valid`.

---

## Interface

| Port | Dir | Width | Description |
|---|---|---|---|
| `clk` | in | 1 | 32 MHz system clock |
| `rst_n` | in | 1 | Active-low reset |
| `iq_valid` | in | 1 | Decimator sample strobe — 125 kS/s, one pulse per 256 clock cycles at SF6 |
| `sample_in[NR][W]` | in | 4×W | Incoming DC-removed samples, W = sample width (12 or 16 bit I+Q) |
| `sf` | in | 3 | Spreading factor; sets M = 2^SF |
| `sc_lock` | in | 1 | From SC detector; triggers freeze |
| `buf_mode` | out | 2 | Current operating mode (acquisition / locked / post-lock / idle) |
| `buf_valid` | out | 1 | Buffer has ≥ M valid samples; gates SC enable |
| `current_sample[NR][W]` | out | 4×W | Current sample (live, to SC and dechirp) |
| `delayed_sample[NR][W]` | out | 4×W | Sample from M times ago (from SRAM, to SC) |
| `delayed_valid` | out | 1 | Delayed sample read is valid this cycle |
| `wr_ptr` | out | 7 | Current write pointer in sample-time units (0–127) |
| `sram0_A` | out | 9 | Byte address to SRAM0 (A[8:0], 0–511) |
| `sram0_D` | out | 8 | Write data byte to SRAM0 (D[7:0]) |
| `sram0_Q` | in | 8 | Read data byte from SRAM0 (Q[7:0]) |
| `sram0_CEN` | out | 1 | SRAM0 chip enable (active-low; 1 = idle) |
| `sram0_GWEN` | out | 1 | SRAM0 global write enable (active-low; 0 = write, 1 = read) |
| `sram1_A` | out | 9 | Byte address to SRAM1 |
| `sram1_D` | out | 8 | Write data byte to SRAM1 |
| `sram1_Q` | in | 8 | Read data byte from SRAM1 |
| `sram1_CEN` | out | 1 | SRAM1 chip enable (active-low) |
| `sram1_GWEN` | out | 1 | SRAM1 global write enable (active-low) |

---

## Parameters

| Parameter | Values | Notes |
|---|---|---|
| `SAMPLE_W` | 8, 12, 16 | Bit width per I or Q component from decimator. |
| `STORE_W` | 8 or 16 | Bits stored per component in SRAM after saturation/truncation. 8 = SF7 max. 16 = SF6 max (512 B exactly full, no margin). |
| `NR` | 4 | Number of receive branches. |
| `SF_MAX` | 7 | Maximum supported SF with 8-bit storage and D=M read-before-write. SF8+ requires additional macros. |

---

## BIST

SRAM0 and SRAM1 use **`gf180mcu_fd_ip_sram__sram512x8m8wm1`** — the silicon-proven GF180MCU PDK macro (512 words × 8 bits, "5V Green" transistor class, operating range 1.62 V – 5.50 V). Both macros are operated at **1.8 V**, placing them on the same supply rail as the digital logic with no level shifters required. A fault here corrupts the SC delayed-sample path with no runtime recovery, so BIST runs at power-on before acquisition mode is entered. See [Memory Strategy](../Memory%20Strategy.md) for the full BIST and fallback architecture.

March-5N write/read pattern on each 512 B macro independently, controlled by the BIST controller block. Pass/fail only — individual bad-word address reporting is not needed for the degraded-mode policy.

Results readable via SPI:

| Register | Description |
|---|---|
| `SRAM0_BIST_PASS` | 1 = SRAM0 passed all March-5N patterns |
| `SRAM1_BIST_PASS` | 1 = SRAM1 passed all March-5N patterns |

Degraded-mode policy:

| SRAM status | Operating mode |
|---|---|
| Both pass | Full NR=4 acquisition |
| SRAM0 fails | NR=2 using ch2/ch3 only (SRAM1) |
| SRAM1 fails | NR=2 using ch0/ch1 only (SRAM0) |
| Both fail | Bypass mode only; acquisition disabled |

---

## Open Questions

1. **Sample width (12 or 16 bit)?** This is the primary unresolved dependency. Determines whether the 1 kB budget is sufficient or whether a 2 kB buffer is needed for full-precision 2-symbol SC.

2. **8-bit saturated storage acceptable for SC?** If 12 or 16-bit samples are saturated to 8 bits before SRAM write, is SC detection quality adequate across the full SNR range? **Must be validated in simulation** — sweep SC detection threshold, false-alarm rate, and timing accuracy at both weak-signal and strong-signal extremes. Note that naive bitfield truncation (taking LSBs) is not acceptable — strong signals wrap around and corrupt the correlation. Saturation (clamp to ±127) is mandatory. Training accumulator is unaffected (receives live full-precision samples).

3. **Post-lock observation mode needed?** The baseline non-FFT path does not require it. Include only if a sync/SFD timing confirmation step is added later.

4. **SF8 and above?** SF8 requires M=256 sample times × 4 bytes = 1024 B per macro — exceeds the 512 B proven macro. SF8 support requires 4 macros (2 kB). No architectural blocker; just area cost.

---

## Known Limitations

- **SF7 is the maximum with 2 × 512 B macros and 8-bit storage.** At SF7 the macro is exactly full (D=M=128, 512 B used). The read-before-write access pattern is required — D=2M does not fit.
- **SF8+ requires more macros.** SF8 needs D=M=256 → 1024 B per macro pair → 4 macros total (2 kB). No architectural change needed beyond adding macros and widening the address counter.
- **16-bit storage at SF7 does not fit.** SF7 at 16-bit storage needs 1024 B per macro — requires 4 macros. SF6 at 16-bit exactly fills one macro with no margin.
- Same-packet weight application is not supported by this buffer. Next-packet weights are the baseline.

---

## Related Blocks

- [ΣΔ Decimator](ΣΔ%20Decimator.md) — provides `iq_valid` and `sample_in`
- [Correlator Bank (SC)](Correlator%20Bank.md) — consumes `current_sample` and `delayed_sample`
- [Non-FFT LoRa Frontend Proposal](../Non-FFT%20LoRa%20Frontend%20Proposal.md) — overall chain context
- [SF6 1kB Frontend Buffer Exploration](../SF6%201kB%20Frontend%20Buffer%20Exploration.md) — memory budget rationale
