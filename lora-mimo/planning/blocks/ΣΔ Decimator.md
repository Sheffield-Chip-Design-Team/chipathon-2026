# ΣΔ Decimator (CIC + FIR, ×4)

RX path stage 2. See [DSP Flow](../DSP%20Flow.md) for context.

**Owner:** TBD
**Status:** Updated for programmable BW

---

## Function

Converts the 1-bit sigma-delta bitstream from each SX1257 ΣΔ ADC into full-precision complex I+Q samples. Four identical instances — one per antenna. A programmable-ratio CIC filter performs the primary decimation (32×–256×); an FIR compensation filter corrects the sinc frequency droop.

**Goal:** Provide a baseband sampling rate $f_s$ that always matches the LoRa signal bandwidth (BW), ensuring the downstream blocks see exactly $2^{SF}$ samples per symbol.

**Output precision:** The decimator outputs full-precision samples (12-bit or 16-bit per component, TBD — see open item below). **The 8-bit saturation for SRAM storage is performed by the Frontend Buffer Controller, not here.** The training accumulator, combiner, and AGC energy tap all require full-precision samples from this block.

---

## Interface

| Port | Direction | Width | Rate | Description |
| --- | --- | --- | --- | --- |
| `iq_in_i` | in | 1 | 32 MS/s | I bitstream from SX1257 `I_OUT` |
| `iq_in_q` | in | 1 | 32 MS/s | Q bitstream from SX1257 `Q_OUT` |
| `clk_32m` | in | — | 32 MHz | Shared clock from SX1257_1 `CLK_OUT` |
| `rst_n` | in | — | — | Active-low reset |
| `decim_ratio` | in | 2 | static | 0=32× (1 MHz), 1=64× (500 kHz), 2=128× (250 kHz), 3=256× (125 kHz) |
| `iq_out_i` | out | 12–16 signed | $f_s$ | Decimated I sample (full precision; width TBD) |
| `iq_out_q` | out | 12–16 signed | $f_s$ | Decimated Q sample (full precision; width TBD) |
| `iq_valid` | out | 1 | $f_s$ | High for one cycle when output is valid |

---

## Parameters

| Parameter | Value | Notes |
| --- | --- | --- |
| Decimation ratios ($R$) | 32, 64, 128, 256 | Powers of 2; matches standard LoRa BWs |
| CIC stages ($N$) | 3 | Balanced for area and stopband rejection |
| Accumulator width | 25-bit | `1 + N*log2(R_max) = 1 + 3*8 = 25` bits |
| FIR taps | 32 | Coefficients programmable or optimized for $R=32$ |
| Output width | 12–16 bit signed (TBD) | Convergent rounding from normalized accumulator; see open item |

---

## Implementation notes

**Programmable CIC.** The CIC filter (integrator-comb) supports variable $R$ by changing the comb delay strobe frequency.
1. Integrators run at 32 MHz.
2. Combs run at the decimated rate $f_s = 32\text{ MHz} / R$.
3. Strobe derived from a counter: `if count == R-1: count <= 0; strobe <= 1`.

**Accumulator Scaling.** The CIC gain is $G = R^N$. As $R$ increases, the output magnitude grows significantly:
* $R=32 \rightarrow G = 32^3 = 2^{15}$
* $R=256 \rightarrow G = 256^3 = 2^{24}$

The block must normalize the result before the FIR stage to maintain constant signal swing. A programmable right-shift targeting the output width `W_out` (12 or 16 bits, TBD):
* `shift = N * log2(R) - (W_out - 1)`
* Example at 12-bit, $R=256$: shift by $24 - 11 = 13$ bits.
* Example at 16-bit, $R=256$: shift by $24 - 15 = 9$ bits.

Implement the shift as a parameter so it can be adjusted when output width is decided.

**FIR Compensation.** The droop shape depends on $R$. However, since LoRa is a wideband signal and we are sampling at the Nyquist rate ($f_s = BW$), the correction is primarily for the roll-off at the band edges. A single FIR coefficient set optimized for $R=32$ is usually sufficient for higher ratios, but a programmable coefficient SRAM can be added if silicon characterization shows significant ripple.

**Clock domain.** Entire block runs at 32 MHz. `iq_valid` rate changes with `decim_ratio`. All downstream DSP (Energy Measurement, Correlator, Combiner) must use `iq_valid` as their clock enable.

---

## Open items

**Output width TBD.** 12-bit or 16-bit per component. This affects:
- Accumulator shift amount
- Training accumulator int64 overflow margin (see [Training Accumulator](Training%20Accumulator.md) §Accumulator arithmetic)
- Frontend Buffer saturation shift (`clamp(sample >> (W_out - 8), -128, 127)`)
- Combiner input headroom

Decision can be deferred to RTL — implement the output width as a parameter. 16-bit is the safe choice; 12-bit saves area in the combiner and training accumulator.

---

## Verification

| Test | Method | Pass criterion |
| --- | --- | --- |
| Ratio switching | Sweep `decim_ratio` in sim | `iq_valid` frequency matches $32\text{ MHz} / R$ |
| DC Scaling | Inject all 1s at $R=256$ | `iq_out` does not overflow; reaches +127 |
| 125 kHz BW support | Inject 125 kHz LoRa bitstream, $R=256$ | Correlator/FFT downstream see 1 symbol per $2^{SF}$ samples |
| SNR characterization | Measure SQNR for each $R$ | SQNR improves as $R$ increases (more oversampling gain) |

---

## Related blocks

- [Register Map](../Register%20Map.md) — `DECIM_CFG` at `0x1B`
- [Frontend Buffer Controller](Frontend%20Buffer%20Controller.md) — receives full-precision output; performs 8-bit saturation for SRAM storage
- [Training Accumulator](Training%20Accumulator.md) — receives full-precision output directly (not from SRAM)
- [Energy Measurement](Energy%20Measurement.md) — receives full-precision output; clock-gated by `iq_valid`
- [ALMMSE-MRC Combiner](ALMMSE-MRC%20Combiner.md) — receives full-precision output
- [DSP Flow](../DSP%20Flow.md) — updated pipeline rates
