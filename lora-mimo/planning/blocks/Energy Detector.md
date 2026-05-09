# Energy Detector

RX path stage 3. See [DSP Flow](../DSP%20Flow.md) for context.

**Owner:** TBD
**Status:** Not started

---

## Function

Computes per-antenna received power `Σ|x|²` over a sliding window of one symbol period. Used to gate the correlator bank — prevents false triggers on noise — and exposed in status registers for diagnostic purposes.

---

## Interface

| Port | Direction | Width | Rate | Description |
| --- | --- | --- | --- | --- |
| `iq_i[3:0]` | in | 4×8 signed | 1 MS/s | I samples from all 4 decimators |
| `iq_q[3:0]` | in | 4×8 signed | 1 MS/s | Q samples from all 4 decimators |
| `iq_valid` | in | 1 | 1 MS/s | Sample strobe from decimators |
| `clk_32m` | in | — | 32 MHz | Master clock |
| `rst_n` | in | — | — | Active-low reset |
| `energy[3:0]` | out | 4×16 unsigned | per symbol | Σ\|x\|² per antenna, latched at end of symbol window |
| `energy_valid` | out | 1 | per symbol | Pulses high when `energy` outputs updated |
| `above_threshold` | out | 1 | per symbol | High if any antenna exceeds `energy_threshold` |
| `energy_threshold` | in | 16 unsigned | static | Gate level; from `ENERGY_THR` register (0x18) |

---

## Parameters

| Parameter | Value | Notes |
| --- | --- | --- |
| Window length | 2^SF samples (per symbol) | Runtime-configurable via `SF_CFG` register |
| Accumulator width | 32-bit | int8² = int16; 4096 samples × int16 → 28 bits min; 32-bit adds headroom |
| Output width | 16-bit unsigned | Saturated right-shift of 32-bit accumulator |

---

## Implementation notes

**No square root required.** `|x|² = I² + Q²` uses int8 multipliers (2 per antenna per sample = 8 total). These are the most expensive elements; share a single multiplier if gate budget is tight.

**Window alignment.** Symbol window derived from `iq_valid` count — reset accumulator every 2^SF valid samples. Must be synchronised with the correlator bank symbol clock.

**Threshold register.** Set by host or firmware via `ENERGY_THR` (0x18/0x19). High sensitivity requires a low threshold, but risks false triggers on noise. AGC loop may adjust this dynamically.

---

## Verification

| Test | Method | Pass criterion |
| --- | --- | --- |
| Known amplitude sine | cocotb | `energy` matches Python `np.sum(np.abs(x)**2)` to ±2 LSB |
| Noise-only input | PRBS at low level | `above_threshold` not asserted over 1000 symbol periods |
| Symbol-rate energy update | Count `energy_valid` pulses | One per symbol period |
| All 4 antennas independent | Different gains per channel | Each `energy[n]` matches per-channel Python reference |

---

## Related blocks

- [ΣΔ Decimator](ΣΔ%20Decimator.md) — provides int8 input
- [Correlator Bank](Correlator%20Bank.md) — gated by `above_threshold`
- [Register Map](../Register%20Map.md) — `ENERGY[0..3]` at `0x50`–`0x57`, `ENERGY_THR` at `0x18`
