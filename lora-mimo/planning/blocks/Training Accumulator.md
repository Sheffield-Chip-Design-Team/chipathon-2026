# Training Accumulator

RX path block (non-FFT frontend). See [Non-FFT LoRa Frontend Proposal](../Non-FFT%20LoRa%20Frontend%20Proposal.md) for context.

**Owner:** TBD
**Status:** Draft

---

## Role

Estimates one complex channel coefficient per receive branch by cross-correlating preamble samples against a nominated reference antenna. The output `Z_j` feeds weight generation directly — no FFT, no chirp LUT, no SRAM access required.

The LoRa preamble upchirps are used as the training sequence. Their constant amplitude means CFO cancels exactly in the cross-product.

---

## How it works

During the preamble, branch `j` receives:

```
rx_j[n] = h_j · s[n] + noise_j
```

where `s[n] = upchirp[n mod M] · exp(j·ω·n)` is the CFO-shifted upchirp and `ω` is the common CFO across all branches.

Cross-correlating branch `j` against the nominated reference branch `r`:

```
Z_j = Σ_n rx_j[n] · conj(rx_r[n])
    ≈ h_j · conj(h_r) · N_acc · Σ|s[n]|² / N_acc  +  noise
    = h_j · conj(h_r) · N_acc  +  noise
```

Because `|s[n]|² = 1` (constant-amplitude chirp), the CFO term `exp(j·ω·n)` cancels exactly in the product — **no CFO correction is needed at any CFO value**, including the ±8.9-bin worst case at ±20 ppm / 868 MHz / SF6.

### MRC combining from cross-correlation estimates

Setting `w_j = conj(Z_j)` and combining:

```
y[n] = Σ_j conj(Z_j) · rx_j[n]
     = h_r · Σ_j |h_j|² · s[n]  +  noise
```

The combining gain is `Σ_j |h_j|²` — full MRC gain. The `h_r` phase factor is a common rotation that the SX1302 handles downstream.

### Why the chirp-ref approach has a CFO nulling problem

The previous chirp-ref approach (`Z_j = Σ rx_j[n] · conj(chirp_ref[n mod M])`) produces a Dirichlet-kernel attenuation proportional to `|sin(π·ε·N_acc/M)| / |sin(π·ε/M)|`. At integer-bin CFO with `N_acc = k·M`, this collapses to zero. At SF6/125 kHz, the first null occurs at just 390 Hz (0.45 ppm). The cross-correlation approach has no such nulls.

---

## Reference branch selection

The reference branch is selected by the 2-bit register field `TACC_REF_SEL[1:0]`:

| `TACC_REF_SEL` | Reference branch |
|---|---|
| `00` | Branch 0 (default) |
| `01` | Branch 1 |
| `10` | Branch 2 |
| `11` | Branch 3 |

**Default is branch 0.** This is adequate for most deployments. The register allows the host or PicoRV32 to redirect the reference to a known-good antenna during bring-up, or to work around a hardware fault on branch 0.

The reference branch contributes noise to all other estimates via the cross-product. The strongest branch should be preferred as reference. Automatic reference selection (using per-branch energy) was considered but deferred: it would require running all 4×4 cross-correlators simultaneously to avoid a sequencing conflict with the energy detector window. A static register is the practical baseline.

### Effect of a weak reference

If the reference branch is in a fading null (`|h_r| ≈ 0`), all `Z_j` are noise-dominated and combining weights are poor. In a 4-branch independent Rayleigh channel the probability all branches are simultaneously weak is low, and the default branch 0 reference is adequate for almost all cases. If branch 0 has a persistent fault, `TACC_REF_SEL` redirects without firmware change.

---

## Recovering absolute channel magnitudes via E_ref

Cross-correlation gives only relative estimates `Z_j ≈ h_j · conj(h_r) · N_acc`. This means:

- Per-branch `|h_j|` is not directly available
- Fading status, per-antenna link quality, EMA smoothing across packets, and ALMMSE all require absolute magnitudes
- Host telemetry registers exporting `Z_j_scaled` would carry only relative values without further context

**Fix: accumulate reference branch energy over the same window.**

In parallel with the cross-correlation accumulators, accumulate the squared magnitude of the reference branch:

```
E_ref = Σ_n |rx_r[n]|²  ≈  (|h_r|² + N0) · N_acc
```

This is a single real int64 accumulator — no extra multiplier hardware (the reference branch samples are already present in the cross-multiply datapath).

At moderate-to-high SNR (`|h_r|² >> N0`):

```
|h_r|²  ≈  E_ref / N_acc

|h_j|²  ≈  |Z_j|² / (E_ref · N_acc)
```

The second identity follows from `|Z_j| ≈ |h_j| · |h_r| · N_acc`, so `|Z_j|² / (E_ref · N_acc) ≈ |h_j|² · |h_r|² · N_acc² / (|h_r|² · N_acc · N_acc) = |h_j|²`.

**What this restores:**

| Feature | Without E_ref | With E_ref |
|---|---|---|
| Per-branch `\|h_j\|` | ✗ relative only | ✓ absolute |
| Fading status / link quality | ✗ | ✓ |
| EMA smoothing across packets | ✗ drifts with `\|h_r\|` | ✓ stable absolute energy |
| Host telemetry per branch | ✗ meaningless without context | ✓ |
| ALMMSE | ✗ | ✓ (combined with N0 estimate) |

**N0 bias.** `E_ref` overestimates `|h_r|²` by `N0 · N_acc`. At low SNR this inflates the denominator, causing `|h_j|²` to be underestimated. For telemetry and EMA this bias is acceptable. For ALMMSE a separate N0 estimate (e.g. from a noise-only window before the preamble) would be needed to debias.

`E_ref` is exported as an additional output port and readable by PicoRV32 via the register map alongside `Z_j_scaled`.

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

This is acceptable for the baseline implementation.

---

## Accumulator arithmetic

Input samples are full-precision from the decimator (**not** the 8-bit saturated SRAM samples). Sample width is 12 or 16 bits per component (TBD).

The cross-product `rx_j[n] · conj(rx_r[n])` produces:

| Quantity | 12-bit input | 16-bit input | Type |
|---|---|---|---|
| Sample I or Q | ±2047 | ±32767 | int12 / int16 |
| Cross-product component | ±2 × 2047² ≈ 8.4M | ±2 × 32767² ≈ 2.1G | int32 / int64 |
| Z_j component (sum over ~320) | ≈ ±2.7G | ≈ ±670G | int32 (tight) / int64 |

**Use int64 per accumulator component.** int64 covers both 12-bit and 16-bit inputs safely. At 12-bit the sum is borderline for int32; at 16-bit int32 overflows.

Total register cost: 4 branches × 2 components (I, Q) × 64 bits = **64 bytes**.

The reference branch samples `rx_r[n]` must be buffered for one clock cycle so all four cross-multipliers read the same sample. A single 2×W-bit register per component suffices.

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
| `ref_sel` | in | 2 | static | Reference branch index from `TACC_REF_SEL` register |
| `Z_j[3:0]` | out | 4×2×64 | per packet | Complex cross-correlation estimates (I+Q, int64 per branch) |
| `E_ref` | out | 64 | per packet | Reference branch energy: `Σ\|rx_r[n]\|²` (int64, real). Used to recover absolute `\|h_j\|²` — see Recovering absolute channel magnitudes. |
| `training_done` | out | 1 | per packet | Asserts when accumulation is complete; triggers weight gen |
| `n_acc` | out | 10 | per packet | Number of samples accumulated (for weight gen normalisation) |

---

## Sub-blocks

1. **Reference branch mux**
   - Selects `rx_r[n]` from `raw_j[ref_sel]`
   - Output registered to align timing with other branches

2. **Complex cross-multiplier array**
   - `d_j[n] = raw_j[n] · conj(rx_r[n])` per branch (4 parallel instances)
   - For `j == ref_sel`: `d_j = |rx_r[n]|²` (real — auto-correlation of reference)
   - Operates on full-precision input samples; no LUT required

3. **Accumulator array**
   - 4 × complex int64 registers (`Z_j[0..3]`)
   - 1 × real int64 register (`E_ref`) — reference branch energy `Σ|rx_r[n]|²`
   - Reset on `sc_lock`
   - `Z_j += d_j` and `E_ref += |rx_r[n]|²` every `iq_valid` while accumulator is active
   - `E_ref` shares the reference branch sample already buffered for the cross-multipliers — no extra memory reads

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
4. Reference branch latched from raw_j[ref_sel].
5. Each iq_valid: d_j = raw_j[j] · conj(rx_r); Z_j += d_j.
6. At sample acc_end: training_done asserts. Z_j and n_acc are latched.
7. Weight gen reads Z_j[0..3] and computes W = conj(Z_j) / S.
8. Accumulator idles until next sc_lock.
```

---

## Verification

| Test | Method | Pass criterion |
|---|---|---|
| Noiseless single-path | Known h_j, no noise, SF6 | `Z_j / n_acc` matches `h_j · conj(h_ref)` within rounding |
| CFO immunity — small | Inject ε = ±0.3 bins | Weight magnitudes identical to zero-CFO case |
| CFO immunity — large | Inject ε = ±8.9 bins (±20 ppm / SF6) | Weight magnitudes identical to zero-CFO case; no Dirichlet attenuation |
| CFO immunity — integer bin | Inject ε = ±1, ±2, … bins | No combining gain collapse; Z_j magnitude unaffected |
| Accumulation window | Check sample count | `n_acc ≈ (8 - SC_HITS_REQ - 1) · M` |
| Overflow check | Max-amplitude 16-bit input | No int64 overflow after n_acc samples |
| Multi-branch | NR=4, independent h_j per branch | Z_j = h_j · conj(h_ref) · n_acc for each j |
| Ref branch selection | Set ref_sel = 1, 2, 3 | Correct branch used as reference; other estimates rotate accordingly |
| Weak reference | h_ref ≈ 0 (deep null) | Z_j near zero; weight gen falls back gracefully (SC selects best remaining) |

---

## Known Limitations

- **Early preamble symbols missed.** Approximately `(SC_HITS_REQ + 1)` preamble symbols are not accumulated. Training SNR is reduced by ~2 dB vs ideal (5 of 8 symbols with `SC_HITS_REQ = 2`).
- **Relative estimates only (combining).** `Z_j` estimates `h_j · conj(h_ref)`, not `h_j` independently. This is sufficient for MRC/EGC/SC combining. Absolute per-branch magnitude is recovered from `E_ref` — see Recovering absolute channel magnitudes. N0 bias in `E_ref` affects ALMMSE at low SNR; a separate noise-floor estimate is needed to debias for that use case.
- **Weak reference degrades all estimates.** If `h_ref ≈ 0`, all `Z_j` are noise-dominated. Mitigated by static `TACC_REF_SEL` pointing to the best-known antenna for the deployment.
- **SF6 only under 1 kB SRAM constraint.** Accumulator register cost scales with NR only (not M), but the Frontend Buffer SRAM constraint limits operation to SF6.
- **Sample width TBD.** int64 accumulators handle both 12-bit and 16-bit inputs. Once sample width is confirmed, the accumulator may be reducible to int32 (12-bit path only).

---

## Alternative: chirp-reference accumulation

The original design correlated against an internally generated chirp LUT:

```
Z_j = Σ raw_j[n] · conj(chirp_ref[n mod M])
```

This gives absolute `h_j` estimates but is susceptible to Dirichlet-kernel attenuation at large CFO. At ±20 ppm / 868 MHz / SF6 (±8.9 bins), accumulation over 5 complete symbols produces nulls whenever CFO crosses an integer bin boundary. The cross-correlation scheme above was adopted as the primary path because it is CFO-immune with no additional hardware cost (cross-multipliers replace the LUT multiply; the LUT itself is eliminated).

The chirp-ref path remains viable if absolute `h_j` is required for a future feature (e.g. ALMMSE, per-branch telemetry). It can be re-enabled by routing `chirp_ref[n mod M]` into the reference input of the cross-multiplier array in place of `raw_j[ref_sel]`.

---

## Related Blocks

- [Frontend Buffer Controller](Frontend%20Buffer%20Controller.md) — holds rolling sample history; training accumulator reads from the decimator directly, not from SRAM
- [Correlator Bank (SC)](Correlator%20Bank.md) — provides `sc_lock`, `timing_ref`
- [ΣΔ Decimator](ΣΔ%20Decimator.md) — provides `raw_j` and `iq_valid`
- [Weight Generation](Weight%20Generation.md) — consumes `Z_j` and `n_acc`; dual hardware/software path
- Register Map — `TACC_REF_SEL[1:0]` field selects reference branch
