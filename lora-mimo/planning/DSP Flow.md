# DSP Flow

The digital signal processing chain is receive-only. The ASIC sits between four SX1257 RF front-ends and an SX1302 LoRa baseband processor, performing multi-antenna combining before passing re-modulated bitstreams to the SX1302 for LoRa demodulation.

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
| 3 | Schmidl-Cox Preamble Detector | int8 I+Q × 4 | sc_lock, timing_ref, eps_sub | Per 2 sym | Mode 1 & 2 |
| 4 | FFT Engine — Preamble Acq. (2-pass, iterative radix-2, SF5–SF12) | int8 I+Q × 4 from SRAM | H matrix (4×NT), N₀ | Per packet | Mode 1 & 2 |
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

Sliding-window autocorrelation across adjacent dechirped symbols. Detects the LoRa preamble without requiring prior knowledge of timing offset or CFO — both shift the dechirped tone to the same bin in consecutive symbols, so their ratio cancels.

```
SC_j[s] = dot( dechirp(rx_j, s) ,  dechirp(rx_j, s+1)* )
         = |h_j|² · M · exp(j·2π·k_cfo / M)   (exact, any timing)
```

**Detection criterion** (incoherent sum across antennas):

```
Λ[s] = Σ_j |SC_j[s]| / √(E_j[s] · E_j[s+1])  ≥  θ_SC  (default 0.90)
```

**Outputs:**
- `sc_lock` — asserted when Λ exceeds threshold for two consecutive symbol pairs
- `timing_ref` — sample counter value at lock, used to align FFT capture windows
- `eps_sub` — sub-bin fractional CFO offset, extracted from `∠SC / −2π`; range ±0.5 bin

The magnitude ratio Λ is CFO-immune and timing-offset-immune; only a non-chirp (data or noise) symbol breaks the autocorrelation. This matches the approach in **rpp0/gr-lora** (`detect_preamble_autocorr()`, `decoder_impl.cc:340`), which uses SC for detection only — the actual integer CFO bin `k_peak` is found by the downstream FFT.

The SC phase gives `ε_sub` for free with no extra FFT pass. Stage 4 Pass 1 only needs to find the integer bin `k_peak`; it uses `ε_sub` from Stage 3 directly for the per-symbol phase correction in Pass 2.

Exposed in status register `SC_STAT` (see [Register Map](Register%20Map.md)).

---

## Stage 4 — FFT Engine — Preamble Acquisition (2-pass)

Triggered by `sc_lock`. Two passes extract timing, CFO, and the full channel matrix.

**Pass 1 — Coarse (2 symbols, ~16 µs at SF7 / 125 kHz):**

```
D_j[k] = FFT( dechirp(rx_j, s) )   for s = {0, 1}
k_peak  = argmax Σ_j Σ_s |D_j[k]|   (incoherent)
```

Finds the integer bin `k_peak ∈ {0 … M−1}` — the coarse CFO bin that SC cannot provide. `ε_sub` is taken directly from Stage 3 (no redundant computation needed).

**Pass 2 — Coherent (8 symbols, aligned to k_peak bin):**

Each symbol accumulates an inter-symbol phase of `2π·ε_sub` from the sub-bin offset. Without correction, symbols partially cancel. The fix rotates symbol `s` by the accumulated phase before summing:

```
h_hat_j = (1 / (N_sym · M)) · Σ_s  D_j[s][k_peak] · exp(+j·2π·ε_sub·s)
```

`ε_sub` comes from Stage 3 — no recomputation needed. SFD downchirp used to confirm sample-accurate timing before h_hat is committed.

**Outputs:**
- `H` — 4×NT complex channel matrix (h_hat per antenna/node)
- `N₀` — per-antenna noise variance from off-peak bins
- `h_ready` — asserted when H is valid; triggers Stage 5 weight computation

---

## Stage 5 — Weight Computation

Runs on PicoRV32 (RV32IM) after `h_ready` from Stage 4. Unaffected by decimation ratio change.

> **Known limitation — MRC degradation at low SNR.**
> MRC combining quality is bounded by channel estimation quality. At low SNR (observed in simulation at −5 dB per-antenna SNR with SF7), 8-symbol FFT averaging produces a noisy `H` estimate. Imperfect phase corrections can cause antenna streams to add partially destructively, making estimated MRC *worse* than the best single antenna. Ideal MRC (using true H) always equals or exceeds the best single antenna — the gap is the estimation loss.
>
> **Implication for verification:** The BER vs SNR sweep should compare estimated MRC against ideal MRC (genie-aided) to quantify this loss across operating SNR range. The EMA averaging in firmware (see [PicoRV32 Integration](blocks/PicoRV32%20Integration.md)) partially mitigates this on static channels by smoothing H across packets.

---

## Stage 6 — ALMMSE/MRC Combining

Time-domain combining performed at the decimated rate $f_s$.

**NT=1 (MRC):** `y[n] = w^H · x[n]`
**NT=2 (ALMMSE):** `ŷ[n] = W · x[n]`

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
The detection threshold `θ_SC` (register `SC_THR`) should be set per deployment environment.
*   **Default:** 0.90 (works well for static indoor channels; matches rpp0/gr-lora default).
*   **Low SNR / mobile:** reduce to 0.75 to trade false-alarm rate for sensitivity.
*   **False-alarm floor:** at threshold 0.90, noise-only Λ < 0.10 with > 99.9% probability (SF7, NR=4).

### 3. Resolution & Calibration
Running at lower bandwidths (e.g., 125 kHz) increases frequency resolution by **8×** ($122 \text{ Hz}$ per bin at SF7).
*   **Bring-up Tip:** Perform initial crystal calibration and CFO estimation at the lowest bandwidth to achieve the highest precision before switching to wideband modes.
*   **Automatic Gain Scaling:** The `ΣΔ Decimator` provides automatic scaling; however, ensure that the `SC_THR` is re-evaluated if switching between $R=32$ and $R=256$, as the noise floor shape may change slightly due to different CIC stopband responses.

---

## Key design constraints

| Constraint | Value | Impact |
| --- | --- | --- |
| Programmable Decimation | 32× to 256× | Native support for 125, 250, 500, 1000 kHz BW |
| SC detection window | 2M samples (2 symbols) | Schmidl-Cox threshold Λ ≥ 0.90; CFO-immune |
| Preamble coherent avg | 8 symbols | Optimal sensitivity (4.5 dB gain vs 1-symbol) |
| FFT SRAM (SF12) | 32 KB | Covers full symbol at any BW due to $f_s = BW$ |
| ΣΔ re-mod order | 3rd order | High stability and SQNR across all OSRs (32–256) |
