# Training Accumulator

RX path block (non-FFT frontend). See [Non-FFT LoRa Frontend Proposal](../Non-FFT%20LoRa%20Frontend%20Proposal.md) for context.

**Owner:** TBD
**Status:** Draft

---

## Role

Estimates one complex channel coefficient per receive branch by accumulating dechirped preamble samples. The output `Z_j` feeds weight generation directly — no FFT, no separate training field, no SRAM access required.

The LoRa preamble upchirps are used as the training sequence. They are a known constant-amplitude waveform and serve as a pilot for channel estimation.

---

## How it works

After dechirping branch `j` with the conjugate LoRa upchirp reference:

```
d_j[n] = raw_j[n] · conj(chirp_ref[n mod M])
```

The received signal during the preamble is:

```
d_j[n] = h_j · exp(j·ω·n) + noise
```

where `h_j` is the complex channel coefficient and `ω` is the common CFO across all branches.

Accumulating over the available preamble window:

```
Z_j = Σ_n d_j[n]  ≈  h_j · N_acc + noise_sum
```

where `N_acc` is the number of samples accumulated.

### Why CFO does not need to be corrected

The CFO term `exp(j·ω·n)` is identical across all branches. It appears as a common phase factor in every `Z_j`:

```
Z_j ≈ h_j · φ_common
```

When forming MRC or EGC weights from `Z_j`, `φ_common` cancels in the ratios. The combining weights are correctly directed regardless of CFO. Residual CFO in the combined output is handled by the SX1302 downstream.

### Why per-symbol accumulation is not needed

Per-symbol accumulation was considered to enable post-lock inter-symbol CFO correction. Since CFO correction is unnecessary (see above), a single running accumulator per branch is sufficient. This reduces hardware to 4 complex registers.

---

## Timing and arming

SC lock fires approximately `(SC_HITS_REQ + 1) · M` samples after the preamble start (symbol 0). At that point, `timing_ref` is back-calculated to symbol 0.

The training accumulator is **armed on `sc_lock`**. It accumulates from the current sample (lock time) through `timing_ref + 8M - 1` (end of the 8-symbol preamble window).

```
acc_start  = sc_lock_sample
acc_end    = timing_ref + 8·M - 1
N_acc      = acc_end - acc_start + 1
           ≈ (8 - SC_HITS_REQ - 1) · M
```

With `SC_HITS_REQ = 2` and SF6 (M=64): `N_acc ≈ 5 · 64 = 320 samples` (symbols 3–7).

`training_done` asserts when the sample counter reaches `acc_end`.

### Known limitation: early preamble symbols are missed

Symbols 0 through `SC_HITS_REQ` (approximately the first 2–3 symbols) have passed before `sc_lock` asserts and cannot be accumulated. The training gain is therefore:

```
10 · log10(N_acc / 8M)  ≈  −2 dB   (for SC_HITS_REQ = 2, SF6)
```

This is acceptable for the baseline implementation. A future enhancement could arm the accumulator on `sc_first_hit_dbg` (the first SC hit, ~1 symbol earlier) to recover one additional symbol, at the cost of requiring false-alarm handling before `sc_lock` confirms.

---

## Dechirp reference

The chirp reference for a LoRa upchirp is:

```
chirp_ref[n] = exp(j · π · n² / M)   for n = 0, 1, …, M−1
```

The accumulator multiplies each incoming sample by `conj(chirp_ref[n mod M])`.

At SF6 (M = 64), a 64-entry complex LUT is the practical implementation:

- 64 entries × 2 components × 16-bit (Q1.15) = 256 bytes
- Phase index = `iq_valid_count mod M`; increments every `iq_valid` strobe
- LUT is shared with any other block that needs the downchirp reference (e.g. a future timing refiner)

At higher SF, the LUT grows proportionally. SF7 requires 128 entries (512 bytes), SF8 requires 256 entries (1 kB). This is a secondary concern given the SF6 operating constraint.

---

## Accumulator arithmetic

Input samples are full-precision from the decimator (**not** the 8-bit saturated SRAM samples). Sample width is 12 or 16 bits per component (TBD).

| Quantity | 12-bit input | 16-bit input | Type |
|---|---|---|---|
| Sample I or Q | ±2047 | ±32767 | int12 / int16 |
| chirp_ref component (Q1.15) | — | ±32767 | int16 |
| Dechirp product component | ±2 × 2047² ≈ 8.4M | ±2 × 32767² ≈ 2.1G | int32 / int64 |
| Z_j component (sum over ~320) | ≈ ±2.7G | ≈ ±670G | int32 (tight) / int64 |

**Use int64 per accumulator component.** At 12-bit input the sum fits in int32 (max ~2.7×10⁹ < 2.1×10⁹ — actually tight, borderline). At 16-bit input int32 overflows. int64 covers both cases safely.

Total register cost: 4 branches × 2 components (I, Q) × 64 bits = **64 bytes**.

---

## Interface

| Port | Dir | Width | Rate | Description |
|---|---|---|---|---|
| `clk` | in | 1 | 32 MHz | System clock |
| `rst_n` | in | 1 | — | Active-low reset |
| `iq_valid` | in | 1 | f_s | Sample strobe |
| `raw_j[3:0]` | in | 4×2×W | f_s | Full-precision DC-removed samples from decimator (W = 12 or 16 bits per component) |
| `sc_lock` | in | 1 | per packet | Arms the accumulator |
| `timing_ref` | in | 32 | per packet | Preamble-start sample index; defines acc_end |
| `sf` | in | 3 | static | Spreading factor; sets M = 2^SF |
| `Z_j[3:0]` | out | 4×2×64 | per packet | Complex channel estimates (I+Q, int64 per branch) |
| `training_done` | out | 1 | per packet | Asserts when accumulation is complete; triggers weight gen |
| `n_acc` | out | 10 | per packet | Number of samples accumulated (for weight gen normalisation) |

---

## Sub-blocks

1. **Chirp reference LUT**
   - 64-entry complex LUT (SF6)
   - Phase index driven by `iq_valid_count mod M`
   - Resets phase index on `sc_lock` to align with `timing_ref` phase

2. **Complex multiplier**
   - `d_j[n] = raw_j[n] · conj(chirp_ref[phase_idx])` per branch
   - Time-multiplexed across 4 branches or 4 parallel instances
   - Operates on full-precision input samples

3. **Accumulator array**
   - 4 × complex int64 registers (`Z_j[0..3]`)
   - Reset on `sc_lock`
   - `Z_j += d_j` every `iq_valid` while accumulator is active

4. **Window controller**
   - Tracks `acc_start` (latched at `sc_lock`) and `acc_end` (= `timing_ref + 8M - 1`)
   - Gates accumulator enable between these bounds
   - Asserts `training_done` and latches `n_acc` when `acc_end` is reached

---

## Operating sequence

```
1. sc_lock asserts at sample N_lock.
2. acc_start = N_lock. acc_end = timing_ref + 8M - 1.
3. Accumulator resets: Z_j[0..3] = 0.
4. LUT phase index aligns to (N_lock mod M).
5. Each iq_valid: d_j = raw_j · conj(LUT[phase]); Z_j += d_j; phase++.
6. At sample acc_end: training_done asserts. Z_j and n_acc are latched.
7. Weight gen reads Z_j[0..3] and computes W.
8. Accumulator idles until next sc_lock.
```

---

## Verification

| Test | Method | Pass criterion |
|---|---|---|
| Noiseless single-path | Known h_j, no noise, SF6 | `Z_j / n_acc` matches `h_j` within rounding |
| CFO immunity | Inject ε = ±10 kHz; compute weights from Z_j | Weights correctly phase-aligned to h_j; combining gain unaffected |
| Accumulation window | Check sample count in Z_j | `n_acc ≈ (8 - SC_HITS_REQ - 1) · M` |
| Overflow check | Max-amplitude 16-bit input | No int64 overflow after n_acc samples |
| Multi-branch | NR=4, independent h_j per branch | Each Z_j independently estimates correct h_j |
| LUT phase alignment | Verify phase resets correctly at sc_lock | Z_j phase error < 1 LSB vs reference accumulation |

---

## Known Limitations

- **Early preamble symbols missed.** Approximately `(SC_HITS_REQ + 1)` preamble symbols are not accumulated. Training SNR is reduced by ~2 dB vs ideal (5 of 8 symbols with `SC_HITS_REQ = 2`).
- **SF6 only under 1 kB SRAM constraint.** The chirp reference LUT and accumulator register cost scale with M. SF7 and above require larger LUTs.
- **Sample width TBD.** int64 accumulators handle both 12-bit and 16-bit inputs. Once sample width is confirmed, the accumulator may be reducible to int32 (12-bit path only).

---

## Related Blocks

- [Frontend Buffer Controller](Frontend%20Buffer%20Controller.md) — holds rolling sample history; training accumulator reads from the decimator directly, not from SRAM
- [Correlator Bank (SC)](Correlator%20Bank.md) — provides `sc_lock`, `timing_ref`
- [ΣΔ Decimator](ΣΔ%20Decimator.md) — provides `raw_j` and `iq_valid`
- Weight Generation — consumes `Z_j` and `n_acc`; block spec TBD
