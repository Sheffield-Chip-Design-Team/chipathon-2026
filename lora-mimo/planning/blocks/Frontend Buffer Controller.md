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

## Memory Organisation

Two single-port SRAM macros of 512 B each:

| Macro | Channels stored |
|---|---|
| `SRAM0` | ch0 and ch1 |
| `SRAM1` | ch2 and ch3 |

Both macros share a common write pointer. All four channels for the same sample time are read and written together.

### Physical word organisation

Each 512 B macro stores **two channels** — one complete IQ sample per channel per word:

```
SRAM0 word at address k:  { ch1_Q[7:0], ch1_I[7:0], ch0_Q[7:0], ch0_I[7:0] }   (8-bit mode)
SRAM1 word at address k:  { ch3_Q[7:0], ch3_I[7:0], ch2_Q[7:0], ch2_I[7:0] }   (8-bit mode)
```

One SRAM access (one address) retrieves or stores the IQ pair for **both channels** on that macro simultaneously. This means a single read from SRAM0 + single read from SRAM1 delivers all four channel samples for a given sample time — matching the SC correlator's per-sample-time input requirement.

### Sample Width and Depth

Decimated samples are either **12-bit** or **16-bit** I+Q per channel (TBD — see open questions). For planning purposes both cases are considered.

**Word width per macro per sample time:**

| Storage format | Bits per channel (I+Q) | Word width for 2 channels | Bytes per word | Depth in 512 B (SF6, M=64) |
|---|---|---|---|---|
| 8-bit I + 8-bit Q (truncated) | 16 bits | 32 bits | 4 B | 128 sample times = **2 symbols** ✓ |
| 16-bit I + 16-bit Q | 32 bits | 64 bits | 8 B | 64 sample times = **1 symbol** ✗ |

At 8-bit storage each macro is 32 bits wide × 128 deep = 512 B. At 16-bit storage a 32-bit-wide macro would require 2 accesses per sample time (one per channel), halving effective throughput and still only reaching 1-symbol depth.

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

## Logical Address Space

At depth `D` sample times (D = 128 for 8-bit, D = 64 for 16-bit):

```
write address:  wr_ptr mod D
delayed address: (wr_ptr - M + 1) mod D    // one symbol ago, aligned to current sample
```

where `M = 2^SF` is the symbol length. At SF6, M = 64.

Both SRAM0 and SRAM1 use the same `wr_ptr` and are accessed in lockstep.

---

## Access Protocol

The SRAM macros are **single-port write-after-read**. One read and one write occur per `iq_valid` sample time, in strict sequence.

At SF6 / 125 kHz, there are 256 clock cycles between `iq_valid` pulses at 32 MHz. The two-phase access completes well within this window.

### Per-sample-time sequence

```
Cycle 0:  iq_valid asserts. Latch incoming raw sample from decimator.
Cycle 1:  Apply read address = (wr_ptr - M + 1) mod D to SRAM0 and SRAM1.
Cycle 2:  SRAM read data valid. Capture delayed samples for SC correlator.
          (Read data is held until next iq_valid.)
Cycle 3:  Apply write address = wr_ptr mod D. Apply write data = current sample.
          Assert write enable on SRAM0 and SRAM1.
Cycle 4:  Write completes.
Cycle 5+: Idle until next iq_valid.
```

Write enable is never asserted simultaneously with a read to the same address. The delayed address and the write address are always separated by M sample times and therefore never alias within the 2M-depth buffer (provided D ≥ 2M, i.e. 8-bit storage mode).

In 16-bit storage mode with D = M, the delayed address equals the write address. This is a hazard: the SRAM would read a sample that is about to be overwritten. This reinforces that 16-bit storage requires a deeper buffer (D ≥ 2M = 128 sample times → 2 kB).

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
| `iq_valid` | in | 1 | Decimator sample strobe — one pulse per new sample time |
| `sample_in[NR][W]` | in | 4×W | Incoming DC-removed samples, W = sample width (12 or 16 bit I+Q) |
| `sf` | in | 3 | Spreading factor; sets M = 2^SF |
| `sc_lock` | in | 1 | From SC detector; triggers freeze |
| `buf_mode` | out | 2 | Current operating mode (acquisition / locked / post-lock / idle) |
| `buf_valid` | out | 1 | Buffer has ≥ M valid samples; gates SC enable |
| `current_sample[NR][W]` | out | 4×W | Current sample (live, to SC and dechirp) |
| `delayed_sample[NR][W]` | out | 4×W | Sample from M times ago (from SRAM, to SC) |
| `delayed_valid` | out | 1 | Delayed sample read is valid this cycle |
| `wr_ptr` | out | 7 | Current write pointer (7 bits for depth up to 128) |
| `sram0_addr` | out | 7 | Address to SRAM0 |
| `sram0_wdata` | out | 32 | Write data to SRAM0 (2 channels packed) |
| `sram0_rdata` | in | 32 | Read data from SRAM0 |
| `sram0_we` | out | 1 | Write enable to SRAM0 |
| `sram1_addr` | out | 7 | Address to SRAM1 |
| `sram1_wdata` | out | 32 | Write data to SRAM1 |
| `sram1_rdata` | in | 32 | Read data from SRAM1 |
| `sram1_we` | out | 1 | Write enable to SRAM1 |

---

## Parameters

| Parameter | Values | Notes |
|---|---|---|
| `SAMPLE_W` | 8, 12, 16 | Bit width per I or Q component. Storage width in SRAM may differ (see resolution options). |
| `STORE_W` | 8 or 16 | Actual bits stored per component. 8 = truncated mode (2-symbol depth at 1 kB). 16 = full-precision (requires 2 kB for 2-symbol depth). |
| `NR` | 4 | Number of receive branches. |
| `SF_MAX` | 6 | Maximum supported SF. Determines maximum `M` and therefore minimum required buffer depth. |

---

## BIST

At boot, before entering acquisition mode, both SRAM macros should be tested independently:

- March-style write/read pattern on each macro
- Results exposed in status register: `SRAM0_BIST_PASS`, `SRAM1_BIST_PASS`

Degraded-mode policy if one macro fails (per SF6 exploration doc):

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

4. **Buffer depth parameterisation at higher SF?** At SF7 with 8-bit storage, M = 128 = full buffer depth → no room for 2-symbol window. The buffer is architecturally SF6-only under the 1 kB constraint. This is a known limitation.

---

## Known Limitations

- Under a hard 1 kB budget with 8-bit storage: SF6 is the maximum operating point. SF7 and above require a larger buffer.
- Under 16-bit storage with 1 kB: buffer depth is only 1 symbol — insufficient for SC. Requires 2 kB or truncation.
- Same-packet weight application is not supported by this buffer. Next-packet weights are the baseline.

---

## Related Blocks

- [ΣΔ Decimator](ΣΔ%20Decimator.md) — provides `iq_valid` and `sample_in`
- [Correlator Bank (SC)](Correlator%20Bank.md) — consumes `current_sample` and `delayed_sample`
- [Non-FFT LoRa Frontend Proposal](../Non-FFT%20LoRa%20Frontend%20Proposal.md) — overall chain context
- [SF6 1kB Frontend Buffer Exploration](../SF6%201kB%20Frontend%20Buffer%20Exploration.md) — memory budget rationale
