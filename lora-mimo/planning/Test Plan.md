# Test Plan

## Strategy

**Primary method:** FPGA-in-the-loop, block-level first, then integration

**Golden reference:** Python simulation in `sim/` (numpy/scipy), plus real SX1257 hardware for loopback

**FPGA platform:** Digilent Arty A7-100T (Artix-7 XC7A100T, 63K LUTs). Vivado for synthesis and P&R. Est. ~30% LUT utilisation for full MIMO path.

**Test data:** Real captured LoRa I/Q samples (CF32 format, 125 kHz BW), sigma-delta modulated to 1-bit at 32 MS/s. Real-world impairments from day one. Existing captures in `sim/`.

## Verification pyramid

| Level | Method | When |
| --- | --- | --- |
| L1 — RTL simulation | iverilog + cocotb, numpy/scipy golden models | Block bring-up — catch bugs before FPGA |
| L2 — FPGA-in-the-loop | Arty A7-100T, synthetic + captured data, SX1257 digital loopback | Primary validation of combined MIMO path |
| L3 — Over-the-air | Two real LoRa nodes at f₀±Δf → SX1257 ×4 → ASIC RTL → SX1302 → ChirpStack | Final system validation |

---

## Block test sequence

### Block 1 — ΣΔ Decimator (CIC + FIR, ×4)

**Pass criterion:** RMS error vs scipy reference < 1 LSB. Frequency response within ±0.5 dB across 0–500 kHz. All 4 instances produce identical output given identical input.

**Method:**
- Generate synthetic chirp in Python → sigma-delta modulate to 1-bit @ 32 MS/s → load into FPGA block RAM → clock through decimator → capture output
- Compare against `scipy.signal.decimate()` reference via cocotb
- Check accumulator overflow: inject max-rate toggling (all 1s) input

**Test matrix:**

| Input | Expected |
| --- | --- |
| DC (all 1s) | Settles to +127 within N×R cycles |
| DC (all 0s) | Settles to −128 within N×R cycles |
| Sine at 100 kHz, −3 dBFS | Output matches scipy to ±1 LSB |
| Sine at 600 kHz (stopband) | Attenuation > 40 dB |
| Max toggle (alternating 1/0) | No accumulator wrap |

---

### Block 2 — Energy Detector

**Pass criterion:** `ENERGY[n]` registers match `Σ|x|²` computed by Python reference over the same 8-symbol window. Zero false gates on noise-only input (SNR < −20 dB) over 1000 trials.

**Method:**
- Inject known-amplitude sine through decimator → measure ENERGY vs Python reference
- Noise-only input (PRBS) → verify energy threshold gate blocks correlator trigger

---

### Block 3 — Correlator Bank ×8

**Pass criterion:** `|H_j,k|` magnitude matches Python correlator reference to within ±2 LSB after 8-symbol integration. `lock` flag asserts within ±1 symbol of Python model prediction. Cross-correlator term (wrong Δf bin) < −20 dB relative to on-bin term.

**Method:**
- Generate synthetic LoRa preamble (8 upchirps) at f₀+Δf for node 1, f₀−Δf for node 2
- Feed through 4 decimator instances (different per-antenna channel gains applied in Python)
- Compare H matrix and lock timing to Python `sim/receiver.py` reference

**Test matrix:**

| Scenario | Expected |
| --- | --- |
| NT=1, clean preamble, 0 dB SNR | lock, correct H column 0 |
| NT=1, minimum SNR (sweep to find threshold) | lock within 8 symbols |
| NT=2, both nodes present | lock, both H columns valid, cross-term < −20 dB |
| Noise only (no preamble) | no lock for 1000 symbol periods |
| Δf mismatch (±1 bin) | no lock — confirms bin selectivity |

---

### Block 4 — FFT Engine

**Pass criterion:** Peak bin matches Python `np.fft.fft()` reference for all SF5–SF12 and all test symbols. Peak > noise floor + 20 dB at 0 dB input SNR.

**Method:**
- Inject dechirped tone for a known symbol value per SF
- Exhaustive test SF7 (all 128 symbols)
- Sampled test SF8–SF12 (every 8th symbol)
- Sweep SNR to find minimum decoding level

**Test matrix:**

| Test | Pass criterion |
| --- | --- |
| All 128 symbols, SF7 | 100% correct bin |
| SF12, symbol 0, 0 dB SNR | Correct bin |
| SF12, symbol 0, −10 dB SNR | Correct bin |
| Twiddle ROM stride correctness, all SFs | Peak bin matches numpy reference |
| SRAM arbitration | No data corruption with PicoRV32 running simultaneously |
| Latency | ≤ SF × 2^(SF−1) + 2^SF cycles from trigger to `symbol_valid` |

---

### Block 5 — ALMMSE/MRC Combiner

**Pass criterion:** Combined output `ŷ[n]` matches Python matrix multiply reference to within ±2 LSB. Post-combining SNR improvement matches theoretical MRC gain (10·log10(NR) dB = 6 dB for NR=4) within 1 dB on a flat channel.

**Method:**
- Pre-load W register bank with known MRC weights computed by Python
- Inject 4-channel int8 test vectors through combiner
- Compare output stream to `W @ x` computed in numpy

**Test matrix:**

| Mode | W | Input | Expected gain |
| --- | --- | --- | --- |
| NT=1 MRC | H* / (‖H‖²+N₀) | 4 equal-amplitude channels | ~6 dB vs single antenna |
| NT=1 MRC | Degenerate (1 antenna only) | One channel active | Matches single-channel SNR |
| NT=2 ALMMSE | Computed from 2×4 H | Both nodes present | Node separation > 20 dB |
| NT=2 ALMMSE, ill-conditioned H | κ(H) >> 1 | Near-collinear channels | Output valid, no overflow |

---

### Block 6 — ΣΔ Re-modulator ×2

**Pass criterion:** Re-demodulated output (Python decimation of 1-bit re-mod stream) matches int16 input to within ±3 LSB RMS. In-band SQNR > 80 dB at full scale.

**Method:**
- Inject known int16 sine → capture 1-bit output → decimate in Python → compare to input
- Stability test: inject input at −3 dBFS and 0 dBFS (should clip/saturate not diverge)
- Both re-mod instances tested independently and simultaneously

**Test matrix:**

| Input | Expected |
| --- | --- |
| Sine at −6 dBFS | SQNR > 80 dB after Python decimation |
| Input at 0 dBFS | Integrators saturate, no runaway |
| DC input | Output bitstream average matches DC value |
| Re-mod B idle (Mode 1) | REMOD_B_I/Q pads driven to defined idle level |

---

### Block 7 — SPI Slave (host interface)

**Pass criterion:** All register R/W operations via RPi SPI0 match expected values. CHIP_ID reads `0xA7`. Burst SRAM readback produces byte-identical data to what was written. Firmware load and CPU_RESET sequence boots PicoRV32.

**Method:**
- cocotb testbench simulates RPi SPI master; write and read back every defined register
- Burst read of capture SRAM region (`0x40000`–`0x87FFF`)
- Firmware load sequence: assert CPU_RESET, load test binary, de-assert, verify PicoRV32 fetches from 0x0000

---

### Block 8 — SPI Master (→ SX1257)

**Pass criterion:** All SX1257 register writes produce correct SPI transactions (correct chip select, correct opcode/address/data sequence). No bus contention with SPI slave during simultaneous activity.

**Method:**
- Logic analyser / cocotb SPI monitor: capture SPI_MOSI/SCK/CSn during a `RegMode` write
- Verify byte sequence matches SX1257 register write format (§5.1 of SX1257 datasheet)
- Verify MISO tristating while acting as master

---

### Block 9 — PicoRV32 + Firmware

**Pass criterion:** Firmware computes correct W matrix (verified against Python reference) within one LoRa symbol period of correlator lock. AGC converges within 3 packets on a static channel. Mode auto-switch triggers correctly on NT=2 preamble pair.

**Method:**
- Write H matrix and N₀ to registers; release CPU_RESET; read back W matrix after IRQ
- Compare W to Python `W = (H^H @ H + N0*I)^-1 @ H^H`
- Inject two-node preamble (NT=2); verify ACTIVE_MODE register switches to 1

---

## SX1257 loopback validation

Uses SX1257 built-in loopback once hardware is assembled.

### Digital loopback (SX1257 §3.8.1)

Connects `I_IN`/`Q_IN` to `I_OUT`/`Q_OUT` inside the SX1257 — validates the round-trip digital baseband path without RF.

| Test | Method | Pass criterion |
| --- | --- | --- |
| Single-tone round-trip | Enable digital loopback; inject known symbol; check SX1302 RX | SX1302 decodes correct packet |
| Interface timing | Check I/Q setup/hold vs CLK_OUT falling edge (logic analyser) | No timing violations |

### RF loopback (SX1257 §3.8.2)

| Test | Method | Pass criterion |
| --- | --- | --- |
| I/Q gain mismatch | Enable RF loopback; inspect decimator output spectrum | < 1 dB mismatch |
| TX DC offset | Check FFT bin 0 from diagnostic capture | < −30 dBc |

---

## Integration test — full MIMO path

First test with all blocks connected. Run after all block tests pass.

| Test | Method | Pass criterion |
| --- | --- | --- |
| NT=1 MRC, single node, SF7 | Real node → SX1257 ×4 → ASIC RTL → SX1302 → ChirpStack | Packet received and decoded |
| NT=1 MRC, sensitivity sweep | Vary node TX power | Sensitivity ≥ standard SX1302 single-antenna (−125 dBm SF7) |
| NT=1 MRC, gain vs single antenna | Compare PER with 1 vs 4 antennas enabled | ≥ 4 dB improvement at threshold SNR |
| NT=2 ALMMSE, two nodes, SF7 | Both nodes transmit simultaneously | Both packets received and separated |
| Mode auto-switch | Start in NT=1; bring up second node | ACTIVE_MODE transitions to 1 within one packet |
| AGC settling | Start at mid-gain; vary path loss by 20 dB | AGC converges within 3 packets |

---

## End-to-end over-the-air validation

Two Heltec V3 nodes (or equivalent) configured at f₀±Δf → 4 antennas → SX1257 ×4 → ASIC → SX1302 → RPi ChirpStack

**Pass criterion:** PER ≤ 1% for both nodes simultaneously at −10 dB SNR.

---

## Test data pipeline

Real LoRa captures available in `sim/` (CF32 format). Processing for RTL stimulus:

```
1. Load CF32 at 250 kHz BW (sim/load_capture.py)
2. Resample to 32 MS/s
3. Sigma-delta modulate to 1-bit (1st-order Python modulator)
4. Pack to bitstream file
5. Load into FPGA block RAM → feed to decimator RTL
```

For multi-antenna testing: apply independent per-antenna complex gains and phase shifts in Python to simulate a spatial channel before sigma-delta modulation.

---

## Tooling

| Task | Tool |
| --- | --- |
| Golden reference model | Python — `sim/` (numpy, scipy) |
| Sigma-delta modulation | Python script |
| RTL simulation | iverilog + cocotb |
| FPGA bitstream | Vivado (Artix-7 XC7A100T) |
| In-circuit debug | Vivado ILA over USB-JTAG |
| SPI traffic capture | Saleae Logic / sigrok |
| Physical synthesis | Yosys + OpenROAD (GF180MCU) |
| Regression runner | Makefile + cocotb |
