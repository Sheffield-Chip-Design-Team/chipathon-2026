# Weight Generation

RX path block (non-FFT frontend). See [Non-FFT LoRa Frontend Proposal](../Non-FFT%20LoRa%20Frontend%20Proposal.md) for context.

**Owner:** TBD
**Status:** Draft

---

## Role

Converts the complex channel estimates `Z_j` from the training accumulator into combining weights `W`, then writes them to `W_SHADOW` and asserts `W_COMMIT`. From the Packet Control FSM's perspective the interface is unchanged from the FFT path — weight gen is the block that produces a W_COMMIT pulse after each packet's preamble.

Supports four combining modes:

| Mode | Weights | Notes |
|---|---|---|
| Bypass | First enabled antenna | No channel knowledge used |
| SC | 1 for max-power branch, 0 for others | Selection combining |
| EGC | Unit-magnitude, conjugate phase of h_j | Equal gain combining |
| MRC | Conjugate h_j scaled by total power | Maximum ratio combining; optimal at high SNR |

---

## Input: normalising Z_j to h_j

The training accumulator outputs `Z_j` (int64 complex per branch) and `n_acc` (sample count).

The per-branch channel estimate is:

```
h_j = Z_j / n_acc
```

Since `n_acc` is a common scalar across all branches, dividing by it scales all `h_j` identically. For weight computation this common scale factor cancels — the weights depend only on the relative magnitudes and phases of `h_j`. Therefore the division by `n_acc` can be omitted and weight gen can work directly with `Z_j`.

### Working precision

int64 values are impractical for direct arithmetic in hardware. Before weight computation, right-shift all `Z_j` by a common shift amount `K` to bring them into int32 range:

```
H_j = Z_j >> K
K   = max(0, bit_length(max_j |Z_j|) - 30)
```

`K` is computed from the leading-zero count of the largest `|Z_j|` component across all branches. Since `K` is common to all branches, relative magnitudes and phases are preserved exactly.

---

## Calibration

Static per-branch gain and phase mismatch is corrected before weight computation:

```
H_j_cal = H_j * conj(cal_j)
```

where `cal_j` are complex calibration coefficients stored in a register bank (Q1.15 per component, default = 1+0j = no correction).

If calibration is not loaded or bypassed, `H_j_cal = H_j`.

Calibration coefficients are written by host or firmware via SPI and do not change packet-to-packet.

---

## Weight computation by mode

### Bypass

```
w_j = 1  for j = lowest set bit of ANTENNA_EN
w_j = 0  otherwise
```

No arithmetic. Immediate.

### SC — Selection Combining

```
j_best = argmax_j |H_j_cal|²
w_j    = 1  if j == j_best,  else  0
```

Requires 4 magnitude-squared computations and a 4-way compare.

### EGC — Equal Gain Combining

```
w_j = conj(H_j_cal) / |H_j_cal|
```

Unit-magnitude weight with conjugate phase. Implementation: normalise each `H_j_cal` to magnitude 1.

Magnitude estimation options:
- CORDIC (exact, higher area)
- α·max + β·min approximation (`|z| ≈ max(|I|,|Q|) + 0.375·min(|I|,|Q|)`, ~3% error)

### MRC — Maximum Ratio Combining

```
S   = Σ_k |H_k_cal|²
w_j = conj(H_j_cal) / S
```

Requires:
1. Four magnitude-squared values
2. Sum S (int64)
3. Four complex divisions by a real scalar S

The reciprocal `1/S` is the hardware-expensive step. Implementation options:
- **Firmware (PicoRV32):** Integer division; straightforward but adds firmware latency
- **Hardware divider:** Area cost; deterministic latency
- **Reciprocal approximation:** Leading-zero normalise S, use a small LUT for the mantissa reciprocal (~8-bit precision, sufficient for Q1.15 weights)

**Implementation choice deferred.** For the first pass, firmware via PicoRV32 is acceptable given the per-packet (non-sample-rate) computation.

---

## Output scaling to Q1.15

Final weights are scaled to fit int16 Q1.15 (range ±1.0, i.e. ±32767):

```
w_j_Q15 = round(w_j · 2^15)
```

with saturation to ±32767.

For MRC, the scaling ensures `Σ |w_j|² ≈ 1` (unit-norm weights), which keeps combiner output power consistent across modes and antenna configurations.

---

## W_SHADOW write and commit

After weights are computed, the block writes to the `W_SHADOW` register bank and asserts `W_COMMIT`:

```
W_SHADOW[j].I = w_j_Q15.I   for j = 0..3
W_SHADOW[j].Q = w_j_Q15.Q
W_COMMIT = 1  (one cycle pulse)
```

The Packet Control FSM then copies `W_SHADOW` to `W_ACTIVE` at the next `safe_switch` boundary (next inter-packet idle). If a packet is active when `W_COMMIT` fires, `W_MISSED_PACKET` is set and the current packet stays in bypass — this is expected next-packet behaviour, not an error.

This interface is identical to the existing PicoRV32 → W_SHADOW flow and requires no changes to the Packet Control FSM or combiner.

---

## Implementation path

Two implementation options:

### Option A — Hardware state machine (preferred long-term)

A small FSM triggered by `training_done`. Runs through SHIFT → CALIBRATE → COMPUTE → WRITE states. Deterministic latency of a few hundred cycles (well within the inter-packet budget). No firmware involvement for the weight path.

### Option B — PicoRV32 firmware (acceptable first pass)

Training accumulator exposes `Z_j` and `n_acc` as SPI-readable registers. `training_done` raises an IRQ to PicoRV32. Firmware reads `Z_j`, computes weights (including the 1/S division), writes `W_SHADOW`, pulses `W_COMMIT`.

This reuses the existing PicoRV32 infrastructure. Latency is bounded by the IRQ response time and firmware execution (~few thousand cycles at 32 MHz = < 1 ms, well within inter-packet gap). PicoRV32 also continues its existing roles (AGC loop, TDD switching).

The register interface for Z_j should be designed to support both options — hardware state machine and firmware can share the same register-mapped `Z_j` readback path.

---

## Interface

| Port | Dir | Width | Rate | Description |
|---|---|---|---|---|
| `clk` | in | 1 | 32 MHz | System clock |
| `rst_n` | in | 1 | — | Active-low reset |
| `training_done` | in | 1 | per packet | Trigger from training accumulator |
| `Z_j[3:0]` | in | 4×2×64 | per packet | Complex channel estimates (int64 I+Q per branch) |
| `n_acc` | in | 10 | per packet | Number of samples in Z_j |
| `combining_mode` | in | 2 | static | 0=bypass, 1=SC, 2=EGC, 3=MRC; from `MIMO_CTRL` register |
| `antenna_en` | in | 4 | static | Enabled branch mask |
| `cal_j[3:0]` | in | 4×2×16 | static | Calibration coefficients (Q1.15 per branch, default 1+0j) |
| `W_shadow[3:0]` | out | 4×2×16 | per packet | Weights written to W_SHADOW bank (Q1.15 I+Q) |
| `W_commit` | out | 1 | per packet | One-cycle strobe to Packet Control FSM |
| `wgen_active` | out | 1 | per packet | Weight computation in progress |
| `wgen_mode_dbg` | out | 2 | per packet | Combining mode that was used for this W |

---

## Timing

```
training_done asserts
      ↓
Weight gen computes H_j, calibrates, computes w_j  (~few hundred cycles, HW)
      ↓                                             (~few thousand cycles, FW)
W_SHADOW written, W_COMMIT pulsed
      ↓
Packet Control FSM promotes W_SHADOW → W_ACTIVE at next safe_switch
      ↓
Weights active on next packet
```

Available budget: from `training_done` to end of current packet. At SF6/125 kHz, a minimal LoRa packet (payload entry at ~12.25 symbols) gives:

```
T_available ≈ (12.25 - 8) · M / BW = 4.25 · 64 / 125000 ≈ 2.2 ms
             = 70,400 clock cycles at 32 MHz
```

Both hardware and firmware paths complete well within this budget.

---

## Verification

| Test | Method | Pass criterion |
|---|---|---|
| MRC noiseless | Known h_j, exact Z_j | w_j matches conj(h_j)/Σ\|h\|² within Q1.15 rounding |
| EGC noiseless | Known h_j | \|w_j\| = 1, angle(w_j) = -angle(h_j) |
| SC noiseless | One strong branch | w_j = 1 on correct branch, 0 elsewhere |
| Bypass | Any input | w_j = 1 on lowest enabled antenna |
| Calibration | Load non-unity cal_j | h_j_cal = H_j * conj(cal_j) before weight compute |
| W_SHADOW write | Check register after wgen_active falls | All 8 half-words match expected Q1.15 values |
| W_COMMIT timing | Check FSM interaction | Packet Control FSM defers to next idle boundary if packet is active |
| Shift normalisation | Z_j with large dynamic range | K computed correctly; no overflow in H_j after shift |
| All branches equal | \|Z_j\| identical for j=0..3 | MRC weights equal-magnitude, EGC weights equal-magnitude |

---

## Known Limitations

- **Division implementation TBD.** MRC requires 1/S. Reciprocal approximation, hardware divider, or firmware path each have different area/latency tradeoffs. Not resolved for initial implementation.
- **No noise weighting.** True MRC uses `w_j = conj(h_j) / σ²_j` (noise-whitened). This block uses equal per-branch noise assumption. If branch noise is unequal (e.g. one antenna is partially blocked), weights are suboptimal.
- **Calibration is static.** Per-branch calibration coefficients are not updated at runtime. Temperature drift or hardware replacement requires a manual recalibration via SPI.

---

## Related Blocks

- [Training Accumulator](Training%20Accumulator.md) — provides Z_j, n_acc, training_done
- [ALMMSE-MRC Combiner](ALMMSE-MRC%20Combiner.md) — consumes W_ACTIVE at sample rate
- [Packet Control FSM](Packet%20Control%20FSM.md) — receives W_COMMIT, manages safe_switch
- [PicoRV32 Integration](PicoRV32%20Integration.md) — optional firmware path for weight computation
- [Register Map](../Register%20Map.md) — MIMO_CTRL, W_SHADOW, cal_j registers
