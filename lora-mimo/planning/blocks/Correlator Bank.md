# Schmidl-Cox Preamble Detector

RX path stage 3. See [DSP Flow](../DSP%20Flow.md) for context.

**Owner:** TBD
**Status:** Replaces Energy Detector + Correlator Bank

---

## Background

The original Correlator Bank performed an 8-symbol time-domain dot-product against a reference chirp (`c*[n]`). This is algebraically identical to reading bin 0 of the dechirped FFT. Any timing offset `n₀` or carrier frequency offset `ε` shifts the dechirped tone to bin `(n₀ + round(ε·M/BW)) ≠ 0`, making the correlator output identically zero for any non-zero misalignment. The block was therefore unreliable without prior time/frequency synchronisation — a chicken-and-egg problem.

The **Schmidl-Cox autocorrelator** solves this by correlating two adjacent dechirped symbols rather than against a fixed reference. Because both symbols experience the same timing and CFO offset, the offsets cancel in the cross-product and the output magnitude is timing/CFO-immune.

**Reference implementation:** rpp0/gr-lora, `detect_preamble_autocorr()`, `decoder_impl.cc:340`.

---

## Function

For each received antenna `j` and each consecutive symbol pair `(s, s+1)`, compute:

```
SC_j[s] = Σ_n  D_j[s][n] · D_j[s+1][n]*
```

where `D_j[s][n] = rx_j[s·M + n] · exp(-jπn²/M)` is the dechirped symbol (M samples).

Equivalently in the frequency domain (cheaper to implement with the FFT engine already present):

```
SC_j[s] = Σ_k  FFT(D_j[s])[k] · FFT(D_j[s+1])[k]*
```

For a preamble upchirp with channel coefficient `h_j` and CFO `ε`:

```
SC_j[s] = |h_j|² · M · exp(j·2π·k_cfo/M)   (exact for any n₀, ε)
```

The phase of `SC_j` encodes the fractional CFO bin `k_cfo`; the magnitude is `|h_j|²·M`.

**Incoherent detection statistic** (combines all antennas, robust to amplitude fading):

```
Λ[s] = Σ_j |SC_j[s]| / √( E_j[s] · E_j[s+1] )
```

where `E_j[s] = Σ_n |D_j[s][n]|²` is the symbol energy. `Λ ∈ [0, NR]`; a pure preamble gives `Λ ≈ NR`.

**Lock condition:** `Λ[s] ≥ θ_SC` for two consecutive symbol pairs (prevents false locks on a single good pair).

---

## Interface

| Port | Direction | Width | Rate | Description |
| --- | --- | --- | --- | --- |
| `iq_i[3:0]` | in | 4×8 signed | $f_s$ | I from decimators |
| `iq_q[3:0]` | in | 4×8 signed | $f_s$ | Q from decimators |
| `iq_valid` | in | 1 | $f_s$ | Master sample strobe — used as **Clock Enable** |
| `sf` | in | 3 | static | Spreading factor; from `SF_CFG` register |
| `sc_thr` | in | 16 unsigned | static | Detection threshold θ_SC (Q1.15); from `SC_THR` register |
| `clk_32m` | in | — | 32 MHz | Master clock |
| `rst_n` | in | — | — | Active-low reset |
| `sc_lock` | out | 1 | per packet | Preamble detected; triggers FFT preamble engine |
| `timing_ref` | out | 32 | per packet | Sample counter at lock edge; used to align FFT windows |
| `eps_sub` | out | 16 signed Q1.15 | per packet | Sub-bin CFO offset = ∠SC / −2π; range ±0.5 bin; used by Stage 4 Pass 2 for per-symbol phase correction |
| `sc_stat` | out | 16 | per symbol | Current Λ[s] value (Q4.12 fixed-point) |

---

## Parameters

| Parameter | Value | Notes |
| --- | --- | --- |
| Detection window | 2M samples (2 symbols) | Sliding; updated every M samples |
| Lock hold | 2 consecutive hits | Reduces false-alarm probability |
| Threshold θ_SC | 0.90 (default) | Programmable via `SC_THR`; 0.75 for low-SNR environments |
| Ring buffer depth | 2M per antenna | Stores current and previous dechirped symbol |
| Accumulator width | int32 | int8 × int8 = int16; sum over M ≤ 4096 samples → 28 bits |
| Energy normalisation | int32 | Same width as correlation accumulator |

---

## Implementation notes

**Ring buffer.** Each antenna maintains a 2M-sample ring buffer of dechirped int8 samples. On each `iq_valid` strobe the incoming sample is dechirped (multiply by `exp(-jπn²/M)`) and written to the buffer. At each symbol boundary (every M valid strobes) the cross-correlation between the two most recent symbols is computed and Λ updated.

**Dechirp NCO.** `ref[n] = exp(-jπn²/M)`. Since `fs = BW`, the NCO step `1/M` is independent of the decimation ratio. The quadratic-phase accumulator resets every M samples.

**Multi-antenna combining.** Λ is the incoherent sum of per-antenna magnitudes normalised by per-antenna energy. This avoids a coherent phase assumption across antennas (different PLL offsets) while still getting NR-fold gain in the detection statistic.

**Clock gating.** All internal registers clock-gated by `iq_valid`. No activity between valid strobes.

---

## Verification

| Test | Method | Pass criterion |
| --- | --- | --- |
| Noiseless lock | Pure upchirp preamble, NR=4 | `sc_lock` asserts within 3 symbols; `k_cfo` correct |
| CFO immunity | Inject ε = ±10 kHz offset | `sc_lock` still asserts; `k_cfo` shifts by expected bin count |
| Timing immunity | Random timing offset n₀ ∈ [0, M) | `sc_lock` asserts; `k_cfo` and `timing_ref` consistent |
| False-alarm rate | White noise input, 10 000 packets | `sc_lock` rate < 0.1% |
| Low-SNR sensitivity | SNR = −10 dB per-antenna (SF7, NR=4) | `sc_lock` asserts with PD > 90% |
| Data-symbol rejection | 2 / 8 preamble slots replaced with random data | Λ degrades gracefully; lock still asserts after clean-symbol window |

---

## Related blocks

- [ΣΔ Decimator](ΣΔ%20Decimator.md) — provides `iq_valid` and `decim_ratio`
- [FFT Engine](FFT%20Engine.md) — triggered by `sc_lock`; performs 2-pass preamble acquisition
- [Register Map](../Register%20Map.md) — `SC_THR`, `SC_STAT`, `k_cfo` status registers
- [DSP Flow](../DSP%20Flow.md)
