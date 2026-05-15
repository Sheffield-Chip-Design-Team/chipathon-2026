# ΣΔ Re-modulator

RX path stage 9. See [DSP Flow](../DSP%20Flow.md) for context.

**Owner:** TBD
**Status:** Not started

---

## Function

3rd order feed-forward sigma-delta modulator converting int16 combined samples back to a 1-bit I+Q bitstream at 32 MS/s for the SX1302 Radio A input. Single instance.

```
Three cascaded ideal integrators (Z⁻¹/(1−Z⁻¹)) with feed-forward
coefficients summed at a 1-bit quantiser.
Integrators must saturate — not wrap — to ensure stability.
Input must be kept below −3 dBFS for stable operation.
```

OSR = 256 / 128 / 64 for 125 / 250 / 500 kHz BW respectively (32 MS/s / f_s). In-band SQNR > 100 dB at the lowest OSR (64, 500 kHz BW) — int16 input is conservative at all supported bandwidths.

---

## Interface

| Port | Direction | Width | Rate | Description |
| --- | --- | --- | --- | --- |
| `in_i` | in | 16 signed | f_s | I from combiner |
| `in_q` | in | 16 signed | f_s | Q from combiner |
| `in_valid` | in | 1 | f_s | Sample strobe |
| `en` | in | 1 | static | 0 = output driven to midscale idle (for gating during TX window if required) |
| `clk_32m` | in | — | 32 MHz | Master clock |
| `rst_n` | in | — | — | Active-low reset |
| `out_i` | out | 1 | 32 MS/s | 1-bit I bitstream → SX1302 |
| `out_q` | out | 1 | 32 MS/s | 1-bit Q bitstream → SX1302 |

---

## Parameters

| Parameter | Value | Notes |
| --- | --- | --- |
| Modulator order | 3 | Feed-forward topology |
| OSR | 256 / 128 / 64 | 32 MS/s / f_s; 125 kHz BW → OSR=256, 250 kHz → 128, 500 kHz → 64 |
| Integrator width | 20-bit signed | Prevents saturation at full-scale input |
| Feed-forward coefficients | Per NTF design | Optimise for SQNR; see Lee/Schreier DELSIG reference |
| Input full-scale | −3 dBFS max | Stability constraint for 3rd order; firmware must ensure combiner output stays within this |

---

## Implementation notes

**3rd order feed-forward structure.** Three integrators; output fed back to comparator only (not to integrators directly). Feed-forward coefficients set the noise transfer function (NTF) zeros. This topology avoids integrator saturation from large input and is preferred over feedback-only designs.

**Integrator saturation.** Each integrator accumulator must clamp to ±(2^(width−1)−1) rather than wrap. Wrap-around causes instability that does not self-recover. Use saturating adders.

**Input level constraint.** 3rd order ΣΔ modulators with Lee's criterion require input < −3 dBFS. PicoRV32 firmware must scale W matrix output to stay within this limit. Consider a right-shift register between combiner and re-mod (e.g. divide by 2 to guarantee headroom).

**Clock domain.** Input is at f_s (in_valid strobe); modulator runs at 32 MHz. On `in_valid`, latch the input into a register and run the 3rd order loop at 32 MHz for the next 32 cycles.

**TX gating.** If the SX1302 does not cleanly ignore REMOD_A while transmitting, set `en=0` for the TX window (drives output low). See TX chain notes in [System Architecture](../System%20Architecture.md).

---

## Verification

| Test | Method | Pass criterion |
| --- | --- | --- |
| Sine at −6 dBFS, 10 kHz | cocotb: inject; Python decimate output | SQNR > 80 dB after decimation |
| Sine at −3 dBFS | Same | Stable output; SQNR > 70 dB |
| Input at 0 dBFS | Same | Integrators saturate; output does not latch up |
| DC input | Various DC levels | Output bitstream average matches input; no runaway |
| Output gated | `en=0` | `out_i` / `out_q` held low |
| Reset recovery | Assert `rst_n`, release, inject sine | Stable output within 100 cycles |

---

## Related blocks

- [ALMMSE-MRC Combiner](ALMMSE-MRC%20Combiner.md) — int16 input
- [System Architecture](../System%20Diagram.md) — REMOD_CLK routing, SX1302 interface
- [DSP Flow](../DSP%20Flow.md)
