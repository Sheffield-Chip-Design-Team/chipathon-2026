# DSP Flow

The digital signal processing chain is receive-only. The ASIC sits between four SX1257 RF front-ends and an SX1302 LoRa baseband processor, performing multi-antenna combining before passing re-modulated bitstreams to the SX1302 for LoRa demodulation.

> **Note — Non-FFT frontend direction.**
> The pipeline below reflects the original FFT-based acquisition design (Schmidl-Cox trigger → 3-pass RCTSL FFT → PicoRV32 weight computation). Active architecture work is instead proceeding on a **non-FFT streaming frontend** — see [Non-FFT LoRa Frontend Proposal](Non-FFT%20LoRa%20Frontend%20Proposal.md).
>
> The FFT path is not used for the following reasons:
> - The 3-pass RCTSL FFT requires ~128 KB of on-chip SRAM staging at SF12, which is incompatible with the available memory budget
> - A streaming correlator-based approach (SC detection + preamble training accumulation) achieves the same outputs — timing, channel estimates, and combining weights — without any FFT staging SRAM
> - With a fixed 8-symbol preamble, `timing_ref` from SC is sufficient to locate the packet boundary, removing the need for the SFD timing refiner that the FFT path previously provided
> - CFO estimation is not required for MRC/EGC weight computation because common CFO cancels in the weight ratios; the SX1302 handles residual CFO during demodulation
>
> This document is retained as a reference for the original design intent and for the bypass / passthrough path, which is unchanged.

Three operating modes share the same hardware:

| Mode | Config | Combining | Output |
| --- | --- | --- | --- |
| 1 | NT=1, NR=4 | MRC | ΣΔ re-mod → SX1302 Radio A |
| 2 | NT=2, NR=4 | ALMMSE | ΣΔ re-mod ×2 → SX1302 Radio A+B |
| 3 | NT=1, NR=1 | Passthrough (bypass) | ΣΔ re-mod → SX1302 Radio A; Radio B idle |

---

## Stage-by-stage pipeline

| Stage | Block | Input | Output | Rate ($f_s$) | Mode |
| --- | --- | --- | --- | --- | --- |
| 1 | SX1257 ΣΔ ADC (×4) | RF signal at each antenna | 1-bit I + 1-bit Q × 4 | 32 MS/s | All |
| 2 | ΣΔ Decimator — CIC + FIR (×4) | 1-bit I+Q × 4 | int8 complex I+Q × 4 | **125 k – 1 MS/s** | All |
| 3 | Schmidl-Cox Preamble Detector | int8 I+Q × 4 | sc_lock, timing_ref | Per 2 sym | Mode 1 & 2 |
| 3.5 | Packet Control FSM | sc_lock, timing_ref, sample_count | live_fft_ready, safe_switch, active mode/antenna | Per packet | Mode 1 & 2 |
| 4 | FFT Engine — Preamble Acq. (3-pass, RCTSL + Channel Est.) | int8 I+Q × 4 from SRAM | H matrix (4×NT), N₀, eps_sub | Per packet | Mode 1 & 2 |
| 5 | Weight Computation (PicoRV32 firmware) | H (4×2), N₀ (4×1) | W matrix (2×4 int16 Q1.15) | Per packet | Mode 1 & 2 |
| 6 | ALMMSE/MRC Combiner | W (2×4 int16), x[n] (4×1 int8) | ŷ[n] (2×1 int16) per sample | $f_s$ | Mode 1 & 2 |
| 6' | Bypass MUX | int8 I+Q from selected antenna | int16 I+Q (sign-extended) | $f_s$ | Mode 3 only |
| 7 | ΣΔ Re-modulator ×2 (3rd order) | int16 I+Q | 1-bit I+Q × 2 streams | 32 MS/s | All |

---

## Mode 3 — Passthrough (Bypass)

`MIMO_CTRL.MODE = 2` (register value 2, referred to as Mode 3 in human-facing numbering).

Stages 3–6 (Schmidl-Cox detector, FFT preamble engine, PicoRV32 weight computation, ALMMSE/MRC combiner) are clock-gated and their outputs ignored. A bypass MUX immediately after the decimators routes a single antenna's int8 samples directly into REMOD_A, sign-extended to int16:

```
bypass_sel = lowest set bit of ANTENNA_EN[3:0]
remod_a_in = sign_extend_8to16(x[bypass_sel][n])
remod_b_in = 16'h0000  (midscale — REMOD_B held idle)
```

**Antenna selection.** The lowest-numbered enabled antenna in `ANTENNA_EN` (bit 4 = ant0, bit 5 = ant1, …) is used. If all ANTENNA_EN bits are set (default `0xF0`), ant0 is selected. Disable unwanted antennas via `ANTENNA_EN` before entering passthrough mode to choose a specific antenna.

**Purpose.** Provides a hardware-verified single-antenna baseline with identical front-end, decimation, and re-modulation paths as the MRC/ALMMSE modes. BER vs SNR comparisons against Mode 1 and Mode 2 isolate purely the combining gain contribution.

**Latency.** Passthrough introduces only the decimator pipeline latency (same as other modes) plus 1 cycle for the bypass MUX — no additional latency from combining or weight computation.

**PicoRV32.** Firmware is not involved in the passthrough datapath. The CPU continues running (AGC loop, TDD switching) unless held in reset.

---

## Stage 2 — ΣΔ Decimation

Programmable CIC filter decimates the 32 MS/s bitstream to match the LoRa bandwidth (BW). This ensures that all downstream DSP blocks see exactly one symbol per $2^{SF}$ samples.

| BW Selection | Ratio ($R$) | Sample Rate ($f_s$) | LSB Resolution |
| --- | --- | --- | --- |
| 125 kHz | 256× | 125 kS/s | 122 Hz (SF7 bin) |
| 250 kHz | 128× | 250 kS/s | 244 Hz |
| 500 kHz | 64× | 500 kS/s | 488 Hz |
| 1000 kHz | 32× | 1000 kS/s | 976 Hz |

A 32-tap FIR compensation filter corrects the sinc frequency droop. The entire downstream pipeline is clock-gated by the `iq_valid` strobe from this block.

---

## Stage 3 — Schmidl-Cox Preamble Detector

Sliding-window autocorrelation across adjacent dechirped symbols. Detects the LoRa preamble and provides coarse timing.

```
SC_j[s] = dot( dechirp(rx_j, s) ,  dechirp(rx_j, s+1)* )
         = |h_j|² · M · exp(j·2π·k_cfo / M)   (exact, any timing)
```

**Detection criterion** (incoherent sum across antennas):

```
Λ[s] = Σ_j |SC_j[s]| / √(E_j[s] · E_j[s+1])  ≥  θ_SC  (default 0.90)
```

**Outputs:**
- `sc_lock` — asserted when Λ exceeds threshold for the configured number of consecutive symbol pairs
- `timing_ref` — estimated preamble-start sample index in `iq_valid` units, used to align FFT capture windows

`sc_lock` is not the FFT compute trigger. Since a Schmidl-Cox detector with `N_hit` required hits asserts only after observing about `N_hit + 1` symbols from the candidate preamble start, `timing_ref` is back-calculated to the estimated preamble origin. The Packet Control FSM then waits only until the live 8-symbol RCTSL window is resident:

```
fft_start      = timing_ref
live_fft_ready = sample_count reached timing_ref + 8M - 1
```

Diagnostic capture may additionally keep pre/post guard samples around the live window:

```
capture_start = timing_ref - M/2
capture_len   = 9M samples per antenna
```

At SF12 and NR=4 this diagnostic/protected capture requires `9 × 4096 × 4 × 2 bytes = 288 KB` of sample capture SRAM: 0.5 symbol pre-guard, 8 preamble symbols, and 0.5 symbol post-guard. The post-guard must not delay `live_fft_ready`; it is for analysis/protection, not a live FFT dependency.

The SC phase is no longer the primary source for fractional CFO (`eps_sub`). Instead, the system uses the more robust RCTSL algorithm in Stage 4. SC is now dedicated to high-sensitivity lock detection and timing recovery.

Exposed in status register `SC_STAT` (see [Register Map](Register%20Map.md)).

---

## Stage 3.5 — Packet Control FSM

Owns packet phase and safe handoff between the historical capture/FFT path and the live combiner/remod path. It latches `timing_ref`, `ACTIVE_MODE`, and `ACTIVE_ANTENNA_EN` at packet start, asserts `live_fft_ready` when the 8-symbol RCTSL window is resident, and permits W/mode/antenna switching only at `safe_switch` boundaries.

Key outputs:

```
live_fft_ready  = sample_count reached timing_ref + 8M - 1
safe_switch     = packet idle boundary
combiner_source = BYPASS until W_ACTIVE is valid for this packet
```

If W computation misses the safe switch point, the current packet remains in bypass and `W_MISSED_PACKET` is set. The FSM must not backpressure `iq_valid`; capture overflow or late W are status events, not live-stream stalls.

With no mid-packet switching, `safe_switch` means the receiver is idle between packets. If `W_COMMIT` arrives while a packet is active, the current packet stays in bypass and the committed W is deferred to the next idle boundary.

See [Packet Control FSM](blocks/Packet%20Control%20FSM.md).

---

## Stage 4 — FFT Engine — Preamble Acquisition (3-pass)

Triggered after `sc_lock` plus live 8-symbol capture readiness. Three passes extract fine CFO, integer timing/bin, and the full channel matrix using the RCTSL (Recursive Continuous Time Signal Likelihood) algorithm for sub-bin precision. Diagnostic guard capture must not block this live trigger.

**Pass 1 — Fine CFO via RCTSL (~8 symbols):**

Concatenates $N_{sym}$ dechirped preamble symbols starting at `timing_ref` and performs an **unpadded live FFT** on the $N_{sym} \cdot M$ concatenated samples. An incoherent magnitude-squared sum is taken across all antennas to produce a high-resolution spectrum $P[k]$. A 2× zero-padded mode may be retained for SF7/SF8 validation or diagnostics, but it is not required in the live acquisition path.

```
eps_sub = RCTSL_Correction(argmax P[k])
```

RCTSL quadratic correction (Cui Yang et al.) provides sub-bin accuracy without requiring zero padding. The live SF12 RCTSL transform is $8 \text{ symbols} \times 4096 \text{ samples} = 32768$ complex points, requiring 128 KB of int16-complex staging. Optional 2× padded diagnostics require 256 KB.

**Pass 2 — Coarse Integer Bin:**

Standard length-$M$ FFT on dechirped samples (corrected by `eps_sub` in the time domain). 

```
k_peak  = argmax Σ_j Σ_s |FFT(rx_corr_j, s)|
```

Finds the integer bin `k_peak ∈ {0 … M−1}`.

**Pass 3 — Coherent Channel Estimation:**

Coherent average of $D_j[s][k\_peak]$ across $N_{sym}$ symbols per antenna. Since `eps_sub` was removed in the time domain before Pass 2, no inter-symbol phase rotation is needed in the accumulator.

```
h_hat_j = (1 / (N_sym · M)) · Σ_s  D_j[s][k_peak]
```

**Outputs:**
- `H` — 4×NT complex channel matrix (h_hat per antenna/node)
- `N₀` — per-antenna noise variance from off-peak bins
- `eps_sub` — final fractional CFO estimate
- `h_ready` — asserted when H is valid; triggers Stage 5 weight computation

---

## Stage 5 — Weight Computation

Runs on PicoRV32 (RV32IM) after `h_ready` from Stage 4. Unaffected by decimation ratio change.

Firmware reads H/N0/eps_sub, computes W, writes the `W_SHADOW` register bank, then pulses `W_CTRL.W_COMMIT`. The Packet Control FSM copies `W_SHADOW` into `W_ACTIVE` only when the receiver becomes idle. If the commit arrives while a packet is active, `W_MISSED_PACKET` is set for that packet and the live path stays in bypass.

The timing budget is therefore bounded by packet phase, not by capture guard length:

```
available_cycles = (safe_switch_sample - h_ready_sample) * (32 MHz / f_s)
required_cycles  = FFT tail/writeback + PicoRV32 W calculation + W_SHADOW writes
```

The implementation goal is for `h_ready -> W_COMMIT` to complete before the current packet ends. The fail-safe behavior is bypass for the current packet, never a mid-packet W update.

**Weight-application policy note.**

There are two architectural choices for applying the weights estimated from a packet preamble:

1. **Same-packet application**
   - estimate `H/N0/eps_sub` from the current packet preamble
   - compute `W`
   - apply `W` to a delayed or buffered version of the same packet payload
   - requires additional live-path buffering or an explicitly supported mid-packet safe-switch policy

2. **Next-packet application**
   - estimate `H/N0/eps_sub` from packet `N`
   - apply the resulting `W` beginning with packet `N+1`
   - avoids payload-delay buffering and keeps the live path simple
   - risks stale weights if the channel changes between packets

**Current architecture choice:** default to **next-packet application**. The no-mid-packet-switching Packet Control FSM already matches this policy: if `W_COMMIT` arrives while a packet is active, the current packet stays in bypass and the committed `W` is activated only at the next idle boundary.

**Future option:** same-packet application may be revisited if FPGA experiments show that a practical FIFO / SRAM delay can cover the `sc_lock -> h_ready -> W_COMMIT` latency at the supported SF range.

> **Known limitation — MRC degradation at low SNR.**
> MRC combining quality is bounded by channel estimation quality. At low SNR (observed in simulation at −5 dB per-antenna SNR with SF7), 8-symbol FFT averaging produces a noisy `H` estimate. Imperfect phase corrections can cause antenna streams to add partially destructively, making estimated MRC *worse* than the best single antenna. Ideal MRC (using true H) always equals or exceeds the best single antenna — the gap is the estimation loss.
>
> **Implication for verification:** The BER vs SNR sweep should compare estimated MRC against ideal MRC (genie-aided) to quantify this loss across operating SNR range. The EMA averaging in firmware (see [PicoRV32 Integration](blocks/PicoRV32%20Integration.md)) partially mitigates this on static channels by smoothing H across packets.

---

## Stage 6 — ALMMSE/MRC Combining

Time-domain combining performed at the decimated rate $f_s$.

**NT=1 (MRC):** `y[n] = w^H · x[n]`
**NT=2 (ALMMSE):** `ŷ[n] = W · x[n]`

Capture and FFT run in parallel with the live stream. Before current-packet W is valid, the combiner falls back to the selected bypass antenna rather than outputting zeros:

```
if !W_valid:
    y[0] = sign_extend_8to16(x[bypass_sel][n])
    y[1] = 0
else:
    y = combine(W_ACTIVE, x[n])
```

PicoRV32 writes W into a shadow register bank and commits it atomically to `W_ACTIVE` after all words are written. The live MACs never read partially-written W.

`W_ACTIVE`, `ACTIVE_MODE`, and `ACTIVE_ANTENNA_EN` switch only when the receiver is idle between packets. If W is not ready, or a packet is still active, the current packet stays in bypass and the commit is deferred.

---

## Stage 7 — ΣΔ Re-modulation

3rd order ΣΔ modulator converts combined samples back to 32 MS/s bitstreams.
*   For **125 kHz BW**, the oversampling ratio (OSR) is **256**, providing extremely high SQNR.
*   For **1000 kHz BW**, the OSR is **32**, matching the original design spec.

---

## Bring-up & Calibration Recommendations

The programmable decimation ratio introduces dynamic constraints that must be managed by the host or PicoRV32 firmware during system operation.

### 1. Analog Filter Matching
To prevent out-of-band noise from aliasing into the signal path, the **SX1257 analog roofing filter** (`RegRxBw`, 0x0D) must be matched to the selected digital bandwidth in `DECIM_CFG`.

| DECIM_CFG | Digital BW | Recommended SX1257 Analog BW |
| --- | --- | --- |
| `0x03` | 125 kHz | 250 kHz (minimum setting) |
| `0x02` | 250 kHz | 250 kHz |
| `0x01` | 500 kHz | 500 kHz |
| `0x00` | 1000 kHz | 750 kHz (max; some roll-off expected) |

**Note:** If the analog filter is left wider than the digital sampling rate (e.g., decimate to 125 kS/s while filtering at 750 kHz), any signals or noise in the 62.5 kHz to 375 kHz range will alias directly into the LoRa signal band.

### 2. Schmidl-Cox Threshold Calibration
Schmidl-Cox sensitivity should be configurable per deployment environment with two knobs:

- detection threshold `θ_SC` via register `SC_THR`
- consecutive hit requirement via register `SC_HITS_REQ`

Recommended starting points:
*   **Default:** 0.90 (works well for static indoor channels; matches rpp0/gr-lora default).
*   **Low SNR / mobile:** reduce to 0.75 to trade false-alarm rate for sensitivity.
*   **Hit count:** default `SC_HITS_REQ = 2`; reduce to `1` for aggressive weak-signal mode, increase to `3` for conservative/noisy environments.
*   **False-alarm floor:** at threshold 0.90, noise-only Λ < 0.10 with > 99.9% probability (SF7, NR=4).

### 3. Resolution & Calibration
Running at lower bandwidths (e.g., 125 kHz) increases frequency resolution by **8×** ($122 \text{ Hz}$ per bin at SF7).
*   **Bring-up Tip:** Perform initial crystal calibration and CFO estimation at the lowest bandwidth to achieve the highest precision before switching to wideband modes.
*   **Automatic Gain Scaling:** The `ΣΔ Decimator` provides automatic scaling; however, ensure that `SC_THR` and, if needed, `SC_HITS_REQ` are re-evaluated if switching between $R=32$ and $R=256$, as the noise floor shape may change slightly due to different CIC stopband responses.

---

## Key design constraints

| Constraint | Value | Impact |
| --- | --- | --- |
| Programmable Decimation | 32× to 256× | Native support for 125, 250, 500, 1000 kHz BW |
| SC detection window | 2M samples (2 symbols) | Schmidl-Cox threshold Λ ≥ 0.90; CFO-immune |
| Preamble coherent avg | 8 symbols | Optimal sensitivity (4.5 dB gain vs 1-symbol) |
| FFT SRAM (SF12 RCTSL) | 128 KB live / 256 KB optional | Live path supports 32,768-pt unpadded RCTSL; optional 65,536-pt padded diagnostics |
| ΣΔ re-mod order | 3rd order | High stability and SQNR across all OSRs (32–256) |
