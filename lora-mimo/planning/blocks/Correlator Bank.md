# Schmidl-Cox Preamble Detector

RX path stage 3. See [DSP Flow](../DSP%20Flow.md) for context.

**Owner:** TBD
**Status:** Simplified "Low-Complexity Searcher" architecture

---

## Background

Since the **FFT Engine (Stage 4)** now provides high-precision CFO estimation via the RCTSL algorithm, the Schmidl-Cox block has been simplified to a pure "trigger" block. Its primary role is to monitor the sample stream continuously and detect the preamble boundary to wake up the more expensive downstream stages.

The **Schmidl-Cox autocorrelator** provides timing/CFO-immune detection by correlating two adjacent dechirped symbols. 

**Reference implementation:** rpp0/gr-lora, `detect_preamble_autocorr()`, `decoder_impl.cc:340`.

---

## Function

For each received antenna `j` and each consecutive symbol pair `(s, s+1)`, the block maintains a sliding-window complex correlation:

```
SC_j[s] = Σ_n  D_j[s][n] · D_j[s+1][n]*
```

where `D_j[s][n]` is the dechirped input.

**Detection Statistic (Magnitude-Squared):**

To avoid expensive hardware square roots or CORDIC-based phase extraction, the block calculates the magnitude-squared statistic and compares it against a normalized threshold using multiplication (avoiding division).

For multi-antenna operation, the antenna combine after correlation is explicitly **incoherent**:

```
Mag_SC = Σ_j |SC_j[s]|²
Energy_Ref = Σ_j E_j[s] · E_j[s+1]
```

This is intentional:

- each antenna performs a true complex Schmidl-Cox correlation locally
- the detector does **not** assume cross-antenna phase alignment
- a weak or phase-rotated antenna cannot cancel a strong antenna during the trigger decision

**Lock condition:**

```
Mag_SC  ≥  (θ_SC)² · Energy_Ref
```

`θ_SC` is the programmable detection threshold (default 0.90).

**Outputs:**
- `sc_lock` — asserted when Λ exceeds threshold for the configured number of consecutive symbol pairs
- `timing_ref` — estimated preamble-start sample index in `iq_valid` units; used by the capture controller and Stage 4

`timing_ref` is **not** the lock-edge sample counter. With `N_hit` consecutive Schmidl-Cox hits required, the detector has already consumed roughly `N_hit + 1` symbols from the candidate preamble start before it can assert `sc_lock`. The block therefore back-calculates the start index:

```
timing_ref = first_hit_candidate_sample
// equivalently, for symbol-rate hit checks:
// timing_ref = lock_sample_count - (N_hit + 1)*M + 1
```

This definition makes the handoff deterministic: downstream capture uses `timing_ref` as the preamble origin, then adds pre/post guard as needed.

### Signal Flow Sketch

The Schmidl-Cox block is best viewed as a dechirped adjacent-window autocorrelator:

```text
rx_iq
  -> dechirp with downchirp reference
  -> split into adjacent M-sample windows
       win_a = D[d : d+M]
       win_b = D[d+M : d+2M]
  -> per-antenna correlation
       SC_j[d] = Σ_n win_a[n] * conj(win_b[n])
  -> per-antenna energy
       E1_j[d] = Σ_n |win_a[n]|^2
       E2_j[d] = Σ_n |win_b[n]|^2
  -> combine antennas
       Mag_SC[d]     = Σ_j |SC_j[d]|^2
       Energy_Ref[d] = Σ_j E1_j[d] * E2_j[d]
  -> threshold compare
       hit[d] = (Mag_SC[d] >= θ_SC^2 * Energy_Ref[d])
  -> consecutive-hit counter (`SC_HITS_REQ`)
  -> `sc_lock`
  -> timing-ref back-calculator
  -> `timing_ref`
```

Interpretation:

- It is an autocorrelation-style detector because it compares one dechirped symbol window against the next dechirped symbol window.
- It is not a single-bin frequency-tuned matched filter, so constant packet CFO mainly rotates the correlation phase instead of collapsing the detection magnitude.
- Antenna combining is incoherent after the per-antenna correlations, so the trigger remains robust even when branch phases differ.
- Its role is coarse packet detection and coarse timing. Final `eps_sub` and refined timing come later from the FFT/RCTSL stage.

---

## Interface

| Port | Direction | Width | Rate | Description |
| --- | --- | --- | --- | --- |
| `iq_i[3:0]` | in | 4×8 signed | $f_s$ | I from decimators |
| `iq_q[3:0]` | in | 4×8 signed | $f_s$ | Q from decimators |
| `iq_valid` | in | 1 | $f_s$ | Master sample strobe — used as **Clock Enable** |
| `sf` | in | 3 | static | Spreading factor; from `SF_CFG` register |
| `sc_thr` | in | 16 unsigned | static | Detection threshold θ_SC (Q1.15); from `SC_THR` register |
| `sc_hits_req` | in | 2 | static | Consecutive hits required for lock; from `SC_HITS_REQ` register |
| `clk_32m` | in | — | 32 MHz | Master clock |
| `rst_n` | in | — | — | Active-low reset |
| `sc_lock` | out | 1 | per packet | Preamble detected; arms the guarded capture-completion FSM |
| `timing_ref` | out | 32 | per packet | Estimated preamble-start sample index in `iq_valid` units |
| `sc_stat` | out | 16 | per symbol | Current Λ[s] value (Q4.12 fixed-point) |
| `sc_hit_dbg` | out | 1 | per symbol | Debug: current threshold-compare result before hit counting |
| `sc_hit_count_dbg` | out | 2 | per symbol | Debug: current consecutive-hit counter state |
| `sc_first_hit_dbg` | out | 32 | per packet | Debug: sample-count snapshot at first qualifying hit |
| `sc_lock_sample_dbg` | out | 32 | per packet | Debug: sample-count snapshot when `sc_lock` asserts |

---

## Parameters

| Parameter | Value | Notes |
| --- | --- | --- |
| Detection window | 2M samples (2 symbols) | Sliding; updated every M samples |
| Lock hold | 1–3 consecutive hits | Runtime-configurable via `SC_HITS_REQ`; default 2 |
| Threshold θ_SC | 0.90 (default) | Programmable via `SC_THR` |
| Ring buffer depth | 2M per antenna | Stores current and previous dechirped symbol |
| Accumulator width | int32 | int8 × int8 = int16; sum over M ≤ 4096 samples → 28 bits |

---

## Implementation notes

### Natural sub-blocks

Even if Schmidl-Cox is implemented as one top-level RTL block, it naturally decomposes into the following sub-blocks:

1. **Downchirp reference / dechirp front end**
   - multiplies incoming complex samples by the known LoRa downchirp reference
   - this is **not** a standard sinusoidal NCO
   - it is a deterministic downchirp reference generator, implemented for example with a ROM/LUT or equivalent phase-law generator

2. **Symbol window manager**
   - maintains current and previous symbol windows
   - tracks `M = 2^SF`
   - aligns the 2-symbol Schmidl-Cox window using `iq_valid`

3. **Per-antenna SC correlator**
   - computes
     ```text
     SC_j[s] = Σ_n D_j[s][n] * conj(D_j[s+1][n])
     ```
   - one complex accumulation path per antenna, or time-multiplexed equivalent

4. **Per-antenna energy measurement**
   - computes `E_j[s] = Σ_n |D_j[s][n]|²`
   - feeds both SC normalization support and AGC energy snapshot export

5. **Normalizer / threshold comparator**
   - forms the detection statistic
   - compares against `SC_THR`
   - optionally applies the coarse energy gate if `SC_CFG.ENERGY_GATE_EN=1`

6. **Hit counter / lock FSM**
   - applies `SC_HITS_REQ`
   - asserts `sc_lock`
   - prevents chatter or repeated lock firing on the same packet

7. **Timing-ref back-calculator**
   - converts the lock event into `timing_ref`
   - accounts for the configured hit count `N_hit`

8. **Status / snapshot export**
   - updates `SC_STAT`
   - latches `ENERGY[0..3]` for AGC and diagnostics at lock

### Bring-Up Debug Signals

For FPGA and early AFE bring-up, expose a minimal set of internal observability points even if the full control plane is not ready yet:

- `sc_hit_dbg`
  Raw threshold-compare result for the current symbol-pair check. Useful to see whether the detector is failing before or after the hit counter.
- `sc_hit_count_dbg[1:0]`
  Current consecutive-hit counter state. Useful to distinguish isolated noise hits from true preamble accumulation.
- `sc_first_hit_dbg[31:0]`
  Free-running `iq_valid` sample-count snapshot at the first qualifying hit in the eventual lock sequence.
- `sc_lock_sample_dbg[31:0]`
  Free-running `iq_valid` sample-count snapshot when `sc_lock` asserts.

These are intended as debug-only exports:

- route to FPGA ILA / SignalTap probes during bring-up
- optionally mirror into SPI-readable status registers
- keep them out of the packet-critical data path

Recommended usage:

- If `SC_STAT` looks healthy but `sc_hit_dbg` never asserts, threshold scaling is likely wrong.
- If `sc_hit_dbg` pulses but `sc_hit_count_dbg` never reaches `SC_HITS_REQ`, noise or timing jitter is likely dominating.
- If `sc_lock` asserts but FFT alignment looks wrong, compare `sc_first_hit_dbg`, `sc_lock_sample_dbg`, and `timing_ref` to verify the back-calculation.

**Multiplication over Division.** The threshold check is performed using `|SC|² ≥ θ² · E₁E₂`. This avoids the area-intensive hardware divider and CORDIC blocks previously required for phase/frequency extraction.

**Sensitivity controls.** Two runtime knobs control the sensitivity / false-lock tradeoff:

- `SC_THR`: lower value increases sensitivity but raises false-lock risk
- `SC_HITS_REQ`: lower value reduces lock latency and increases sensitivity; higher value is more conservative

Recommended operating range:

- `SC_HITS_REQ = 1`: aggressive weak-signal mode
- `SC_HITS_REQ = 2`: default
- `SC_HITS_REQ = 3`: conservative / noisy environment mode

**Preamble length note.** The Schmidl-Cox trigger is not inherently tied to an 8-symbol preamble. It only needs enough repeated upchirps to accumulate the required consecutive hits:

- minimum useful repeated-upchirp span is approximately `SC_HITS_REQ + 1` symbols
- longer preambles generally make SC lock easier, not harder
- the current **8-symbol assumption belongs to Stage 4 FFT acquisition**, which consumes a canonical 8-symbol slice starting at `timing_ref`

So for packets with more than 8 preamble symbols, SC may lock early and the downstream FFT still reads the chosen 8-symbol acquisition window beginning at `timing_ref`.

**Low-Power Design.** Since this block is "always-on," it is heavily clock-gated by `iq_valid`. The complex multipliers used for dechirping and correlation are shared or time-multiplexed across antennas where possible.

**Timing Accuracy.** While SC provides the `timing_ref`, the precision is ± few samples. The downstream FFT Engine (Stage 4) uses the up/down chirp transition (SFD) to refine this to sample-accurate timing.

**Capture handoff.** The detector must not freeze sample capture directly on `sc_lock`. Instead:

1. A free-running `iq_valid` sample counter tags every decimated sample written to the circular capture buffer.
2. On `sc_lock`, the detector latches `timing_ref = estimated preamble start`.
3. The capture controller computes:

```
capture_start = timing_ref - M/2
capture_len   = 9*M samples per antenna   // 0.5M pre + 8M preamble + 0.5M post
fft_start     = timing_ref
```

4. The live FFT trigger may assert as soon as `timing_ref + 8M - 1` has been written.
5. Capture may continue until `capture_start + capture_len` has been written for diagnostics/readback; diagnostic guard completion must not block live FFT.

For SF12, the guarded capture window is `9 * 4096 * 4 antennas * 2 bytes = 288 KB`.

---

## Verification

| Test | Method | Pass criterion |
| --- | --- | --- |
| Noiseless lock | Pure upchirp preamble, NR=4 | `sc_lock` asserts within `N_hit + 1` symbols |
| CFO immunity | Inject ε = ±10 kHz offset | `sc_lock` still asserts regardless of frequency |
| Timing immunity | Random timing offset n₀ ∈ [0, M) | `sc_lock` asserts consistently |
| False-alarm rate | White noise input, 10 000 packets | `sc_lock` rate < 0.1% |
| Multiplication check | Verify threshold logic | Logic triggers identically to floating-point division |
| Hit-count sweep | Run with `SC_HITS_REQ = 1,2,3` | Lock latency and false-alarm behavior match expectation |

---

## Related blocks

- [ΣΔ Decimator](ΣΔ%20Decimator.md) — provides `iq_valid`
- [Packet Control FSM](Packet%20Control%20FSM.md) — owns packet phase and live FFT readiness after `sc_lock`
- [FFT Engine](FFT%20Engine.md) — triggered after live 8-symbol capture readiness; performs 3-pass preamble acquisition (RCTSL)
- [Register Map](../Register%20Map.md) — `SC_THR`, `SC_HITS_REQ`, `SC_STAT` status registers
