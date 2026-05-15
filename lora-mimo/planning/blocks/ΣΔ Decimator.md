# ΣΔ Decimator (CIC + FIR, ×4)

RX path stage 2. See [DSP Flow](../DSP%20Flow.md) for context.

**Owner:** TBD
**Status:** Updated for programmable BW

---

## Function

Converts the 1-bit sigma-delta bitstream from each SX1257 ΣΔ ADC into full-precision complex I+Q samples. Four identical instances — one per antenna. A programmable-ratio CIC filter performs the primary decimation (32×–256×); an FIR compensation filter corrects the sinc frequency droop.

**Goal:** Provide a baseband sampling rate $f_s$ that is slightly above the LoRa signal bandwidth (BW), providing a guard band to tolerate carrier frequency offset without aliasing.

**Output precision:** The decimator outputs full-precision samples (12-bit or 16-bit per component, TBD — see open item below). **The 8-bit saturation for SRAM storage is performed by the Frontend Buffer Controller, not here.** The training accumulator, combiner, and AGC energy tap all require full-precision samples from this block.

---

## ADC clock and output rate decision

### The aliasing problem

A LoRa signal at BW = 125 kHz occupies ±62.5 kHz in complex baseband. If the decimator output rate is exactly 125 kS/s (Nyquist = 62.5 kHz), any carrier frequency offset (CFO) shifts the signal edge above the Nyquist frequency and causes aliasing in the decimator anti-alias filter. Even a small CFO of a few hundred Hz causes the top of the chirp sweep to be clipped before it can wrap, distorting the received waveform.

### Why not simply increase R at 32 MHz?

Increasing the output rate at 32 MHz requires a non-power-of-two decimation ratio (e.g. R=200 for 160 kS/s), which breaks the simple shift-register CIC structure and requires a counter-based stage. This adds design complexity for marginal benefit.

### Chosen solution: 36 MHz ADC clock, R=256

Running the SX1257 at **36 MHz** instead of 32 MHz with the same power-of-two R=256:

```
Fs_out  = 36 MHz / 256 = 140,625 S/s
Nyquist = 70,312 Hz
Signal  = ±62,500 Hz  (125 kHz BW)
Guard   = 70,312 − 62,500 = 7,812 Hz  (±7.8 kHz)
```

This guard band covers a CFO of up to **±7.8 kHz**, corresponding to **±9.0 ppm** at 868 MHz. A TCXO (typical ±0.5–2 ppm) has 4–9× headroom within the guard. A raw crystal at ±20 ppm would exceed the guard and should not be used.

### Noise trade-off

Wider output bandwidth admits more noise. The penalty relative to the minimum 125 kS/s is:

```
10 · log10(140,625 / 125,000) = +0.51 dB
```

This is negligible. The alternative (R=200 non-power-of-two) would give a 1.1 dB noise penalty with more hardware complexity. The chosen 36 MHz / R=256 configuration is therefore the best trade-off: simple CIC, clean guard band, and near-zero noise cost.

### Why not 250 kS/s (2× oversampling)?

Doubling the output rate to 250 kS/s provides a 62.5 kHz guard (covers ±72 ppm) but costs **3 dB** of noise — half the NR=4 MRC combining gain. This is excessive given the ±9 ppm TCXO constraint already in use.

### Summary

| Config | Fs_out | Guard | Max CFO | Noise penalty | CIC structure |
|---|---|---|---|---|---|
| 32 MHz / R=256 | 125 kS/s | 0 Hz | 0 ppm | 0 dB | power-of-2 |
| 32 MHz / R=200 | 160 kS/s | 7.8 kHz | ±9 ppm | −1.1 dB | non-power-of-2 |
| **36 MHz / R=256** | **140.6 kS/s** | **7.8 kHz** | **±9 ppm** | **−0.5 dB** | **power-of-2** ✓ |
| 32 MHz / R=128 | 250 kS/s | 62.5 kHz | ±72 ppm | −3.0 dB | power-of-2 |

---

## Interface

| Port | Direction | Width | Rate | Description |
| --- | --- | --- | --- | --- |
| `iq_in_i` | in | 1 | 36 MS/s | I bitstream from SX1257 `I_OUT` |
| `iq_in_q` | in | 1 | 36 MS/s | Q bitstream from SX1257 `Q_OUT` |
| `clk_36m` | in | — | 36 MHz | Shared clock from SX1257_1 `CLK_OUT` |
| `rst_n` | in | — | — | Active-low reset |
| `decim_ratio` | in | 2 | static | 0=32× (1.125 MHz), 1=64× (562.5 kHz), 2=128× (281.25 kHz), 3=256× (140.625 kHz) |
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

**Clock domain.** Entire block runs at 36 MHz. `iq_valid` rate changes with `decim_ratio`. All downstream DSP (Energy Measurement, Correlator, Combiner) must use `iq_valid` as their clock enable.

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
| Ratio switching | Sweep `decim_ratio` in sim | `iq_valid` frequency matches $36\text{ MHz} / R$ |
| DC Scaling | Inject all 1s at $R=256$ | `iq_out` does not overflow; reaches +127 |
| 125 kHz BW support | Inject 125 kHz LoRa bitstream, $R=256$ | Downstream blocks see 1 symbol per $2^{SF}$ samples at 140.625 kS/s |
| CFO guard band | Inject LoRa signal with ±7 kHz CFO, R=256 | No aliasing artefacts in output spectrum |
| SNR characterization | Measure SQNR for each $R$ | SQNR improves as $R$ increases (more oversampling gain) |

---

## Related blocks

- [Register Map](../Register%20Map.md) — `DECIM_CFG` at `0x1B`
- [Frontend Buffer Controller](Frontend%20Buffer%20Controller.md) — receives full-precision output; performs 8-bit saturation for SRAM storage
- [Training Accumulator](Training%20Accumulator.md) — receives full-precision output directly (not from SRAM)
- [Energy Measurement](Energy%20Measurement.md) — receives full-precision output; clock-gated by `iq_valid`
- [ALMMSE-MRC Combiner](ALMMSE-MRC%20Combiner.md) — receives full-precision output
- [DSP Flow](../DSP%20Flow.md) — updated pipeline rates
