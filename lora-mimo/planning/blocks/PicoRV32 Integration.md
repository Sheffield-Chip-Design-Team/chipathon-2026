# PicoRV32 Integration

Control block. See [System Architecture](../System%20Diagram.md) for context.

**Owner:** TBD
**Status:** Not started

---

## Function

PicoRV32 RV32IM soft-core CPU managing all timing-sensitive and algorithmic tasks that are impractical in RTL. Runs firmware loaded over SPI by the RPi host. Connects to all peripherals via a custom `AHB-Lite` wrapper/interconnect.

**Bus decision.** The project bus is now `AHB-Lite`. PicoRV32 is kept as the CPU, so the master side is a custom implementation rather than a native Wishbone integration.

**Why RV32IM (not RV32I):** The ALMMSE weight computation requires a 2×2 complex matrix inversion. The closed-form determinant and reciprocal involve 32-bit multiplies — hardware MUL/DIV (the M extension) reduces the weight computation from ~1000 cycles (software multiply) to ~50 cycles.

---

## Firmware tasks

| Task | Trigger | Latency budget |
| --- | --- | --- |
| MRC weight computation | FFT `h_ready` (NT=1) | << 1 LoRa symbol (~32 ms at SF12) |
| ALMMSE weight computation | FFT `h_ready` (NT=2) | << 1 LoRa symbol |
| TX preparation (RX→TX) | `tx_prep` IRQ from TX_CTRL[0] | < 1 ms (LoRaWAN RX1 budget = 1 s) |
| TX restore (TX→RX) | `tx_done` IRQ from TX_CTRL[1] | < 1 ms |
| AGC loop | Per-packet energy update | < 1 packet |
| Mode auto-switch | NT=2 preamble pair detect | Immediate — IRQ-driven |
| SX1257 init on power-up | Startup | Before first RX |

### MRC weight computation (NT=1)
```c
// w_j = H_j* / Σ_k(|H_k|² + N0_k)   — per-antenna N0 for correct weighting
// under per-antenna AGC, each antenna may be at a different gain so N0 differs
// H is 4x1 complex int16 Q1.15; N0[4] are per-antenna int16
int32 denom = 0;
for (int j = 0; j < 4; j++)
    denom += (int32)H_re[j]*H_re[j] + (int32)H_im[j]*H_im[j] + N0[j];
for (int j = 0; j < 4; j++) {
    W_re[j] = (int16)((int32)H_re[j] * 32767 / denom);  // Q1.15 normalise
    W_im[j] = (int16)(-(int32)H_im[j] * 32767 / denom); // conjugate
}
```

### ALMMSE weight computation (NT=2)
```c
// W = (H^H * H + N0*I)^-1 * H^H
// H is 4x2 complex; use closed-form 2x2 inverse
// HH = H^H * H  (2x2 Gram matrix)
// HH^-1 = [[d,-b],[-c,a]] / (ad-bc)  (closed-form)
// W = HH^-1 * H^H
// ~20 complex MACs + 1 division (RV32IM DIV opcode)
```

See [DSP Flow §Stage 6](../DSP%20Flow.md) for full equations.

### TX preparation (RX → TX)

Triggered by `tx_prep` IRQ (host writes `TX_CTRL[0]=1`). Disables RX on TX antennas before switching them to transmit, preventing corrupted inputs reaching the combiner.

```c
void tx_prep_handler() {
    // 1. Gate TX antennas out of combiner immediately
    uint8_t ctrl = read_reg(MIMO_CTRL);
    write_reg(MIMO_CTRL, ctrl & ~0b11110000);  // clear ANTENNA_EN[0:1]
                                                // (antennas 2,3 remain active)

    // 2. Put SX1257_3/4 to standby (StandbyEnable only = 0x01)
    //    Stops SX1257_3/4 outputting corrupt IQ during TX window
    spi_master_write(2, REG_MODE, 0x01);  // SX1257_3 standby
    spi_master_write(3, REG_MODE, 0x01);  // SX1257_4 standby

    // 3. Switch SX1257_1/2 to TX (PADriverEnable|TxEnable|StandbyEnable = 0x0D)
    spi_master_write(0, REG_MODE, 0x0D);  // SX1257_1 TX
    spi_master_write(1, REG_MODE, 0x0D);  // SX1257_2 TX
    // ~12 µs SPI (4 writes) + 120 µs TS_TR; well within 1 s RX1 budget

    // 4. Mark TX active; clear IRQ; signal RPi
    write_reg(TX_CTRL, 0x04);  // TX_ACTIVE=1, TX_PREP=0
    clear_irq(IRQ_TX_PREP);
}
```

**W recomputation not required.** During TX the SX1302 is transmitting — it does not process the ASIC re-mod output. Combined output quality during TX is irrelevant.

**SE2435L LNA protection.** Standby on SX1257_3/4 stops corrupt IQ data reaching the ASIC. SE2435L_3/4 CPS (LNA enable) is a separate signal whose source is TBD — see [SE2435L Front-End Module](SE2435L%20Front-End%20Module.md) for the open decision. If CPS cannot be driven low during TX, the LNA may compress at −13 dBm input (40 dB board isolation, +27 dBm TX); safe only if board isolation >37 dB.

### TX restore (TX → RX)

Triggered by `tx_done` IRQ (host writes `TX_CTRL[1]=1` after `lgw_send()` completes).

```c
void tx_done_handler() {
    // 1. Switch SX1257_1/2 back to RX (RxEnable|StandbyEnable = 0x03)
    spi_master_write(0, REG_MODE, 0x03);  // SX1257_1 RX
    spi_master_write(1, REG_MODE, 0x03);  // SX1257_2 RX

    // 2. Restore SX1257_3/4 to RX
    spi_master_write(2, REG_MODE, 0x03);  // SX1257_3 RX
    spi_master_write(3, REG_MODE, 0x03);  // SX1257_4 RX

    // 3. Wait TS_RE — SX1257 standby/TX → RX wake-up (typ 100 µs)
    delay_us(150);  // conservative margin; covers all 4 SX1257s

    // 5. Re-enable all antennas
    uint8_t ctrl = read_reg(MIMO_CTRL);
    write_reg(MIMO_CTRL, ctrl | 0b11110000);  // restore ANTENNA_EN[0:1]

    // 6. Clear TX_ACTIVE; clear IRQ
    write_reg(TX_CTRL, 0x00);
    clear_irq(IRQ_TX_DONE);

    // 7. Invalidate W — correlator will recompute on next preamble
    //    (channel may have changed while TX antennas were gated)
    w_valid = 0;
}
```

**Note on W invalidation.** After TX, the channel estimate from before TX may be stale. Setting `w_valid=0` causes the combiner to coast on the old W until the next correlator lock recomputes it. For a static gateway this is fine; channel coherence time >> TX window duration.

### Flat-fading-per-packet assumption

The design assumes the channel is constant across one packet — `h_hat` estimated from the preamble is applied unchanged to the payload. This holds when the channel coherence time >> packet duration.

**When the assumption can break:**

| Scenario | Risk | Notes |
| --- | --- | --- |
| Mobile node (walking, ~1.5 m/s, 868 MHz) | Coherence time ~200 ms | SF12 packets (~2.5 s) exceed this; SF7 (~50 ms) is safe |
| Dense urban / industrial multipath | High Doppler spread | Even slow nodes can see fast fading |
| SF12 at 125 kHz | Highest risk | 2.5 s exposure — longest packet by far |
| SF7–SF9 static sensors | Negligible | Packets short enough for assumption to hold comfortably |

**Impact:** If the channel changes between preamble and payload, `h_hat` is stale. MRC degrades gracefully (loses some combining gain) rather than failing catastrophically — the argmax demodulator is robust to partial phase misalignment.

**EMA interaction:** The cross-packet EMA averaging makes staleness worse for mobile nodes by blending old channel estimates into the current one. EMA should be disabled (`ALPHA_SHIFT=0`) or given a very short window for mobile deployments.

**Verification implication:** BER vs SNR sweeps should include a time-varying channel test at SF12 to characterise the degradation boundary.

---

### H and N₀ channel estimate averaging (EMA)

Both H and N₀ are estimated once per preamble by the correlator. On a stable channel this is fine; on a slowly varying channel, averaging H and N₀ across packets reduces noise on W and stabilises ALMMSE separation.

Firmware implements an exponential moving average in DMEM — no RTL changes required:

```c
// DMEM: 32 bytes for H_prev, 8 bytes for N0_prev
int16 H_prev_re[4][2], H_prev_im[4][2];
uint16_t N0_prev[4];

// IRQ_STATUS bits:
#define IRQ_CORR_LOCK        (1u << 0)
#define IRQ_H_READY          (1u << 1)
#define IRQ_W_MISSED_PACKET  (1u << 2)

void irq_handler() {
    uint8_t irq = read_reg(IRQ_STATUS);

    if (irq & IRQ_CORR_LOCK) {
        agc_update();
        clear_irq(IRQ_CORR_LOCK);
    }

    if (irq & IRQ_H_READY) {
        // Saturation check: if any antenna was saturating, discard this packet
        bool saturated = false;
        for (int n = 0; n < 4; n++)
            if (read_energy(n) > AGC_SAT_GUARD) { saturated = true; break; }

        if (!saturated) {
            read_H_registers(H_new_re, H_new_im);   // from 0x70-0x8F
            read_N0_registers(N0_new);              // from 0xB0-0xB7

            // EMA: reset if any antenna's gain changed (H and N0 scale with gain)
            if (ema_reset_pending) {
                H_prev = H_new;
                N0_prev = N0_new;
                ema_reset_pending = false;
            } else {
                // H_avg = H_prev + (H_new - H_prev) >> H_ALPHA_SHIFT
                // N0_avg = N0_prev + (N0_new - N0_prev) >> N0_ALPHA_SHIFT
                for each element:
                    H_avg = H_prev + ((H_new - H_prev) >> H_ALPHA_SHIFT);
                for each antenna:
                    N0_avg = N0_prev + ((N0_new - N0_prev) >> N0_ALPHA_SHIFT);
                H_prev = H_avg;
                N0_prev = N0_avg;
            }

            compute_W(H_avg, N0_avg);
            write_W_shadow_registers(W);            // to 0x90-0xAF
            write_reg(W_CTRL, W_COMMIT);
        }

        clear_irq(IRQ_H_READY);
    }

    if (irq & IRQ_W_MISSED_PACKET) {
        stats.w_missed++;
        clear_irq(IRQ_W_MISSED_PACKET);
    }
}
```

`ALPHA_SHIFT` parameters are firmware compile-time constants. Synchronizing the averaging of H and N₀ ensures the ALMMSE formula uses an "age-matched" set of parameters, preventing weight jitter. To disable averaging, set `ALPHA_SHIFT=0`.

**Timing:** MRC ~50 cycles, ALMMSE ~100 cycles at 32 MHz. After correlator lock there are sync word + header symbols before data (~3 ms at SF7) — margin is >1000×.

### Pre-boot DELTA_F calibration (first-packet fix)

On first contact with a node, the correlator integrates at the programmed `DELTA_F`. At SF7, a ±20 ppm crystal error puts the node ~17 bins off — the correlator produces a degraded H on the first packet. Fix: RPi writes a measured or previously stored `DELTA_F` before releasing `CPU_RESET`:

```
RPi startup sequence:
  1. Write CPU_RESET=1
  2. Load firmware via SPI burst
  3. Write DELTA_F_HI/LO with last-known node frequency offset
     (from prior session, node registration database, or manufacturer spec)
  4. Write CPU_RESET=0
```

`DELTA_F` is writable at `0x20`/`0x21` and takes effect immediately on the correlator. No RTL changes required. For a fresh deployment with no prior data, use the node's nominal Δf; drift compensation will correct it within the first few packets.

### Periodic DELTA_F drift compensation

Node crystal drift shifts the actual carrier away from the programmed Δf over time (temperature-driven; timescale minutes). Firmware corrects this by periodically reading `FFT_PEAK_BIN_A/B` after each FFT run:

```c
static uint16_t delta_f_hz   = DELTA_F_INIT_HZ;  // loaded from register at boot
static uint16_t packet_count = 0;

// Called from irq_handler() after FFT completes:
void drift_compensation() {
    if (++packet_count % DRIFT_COMP_INTERVAL != 0) return;

    // Node 1 (+Δf)
    uint16_t k_a    = (read_reg(FFT_PEAK_BIN_A_HI) << 8) | read_reg(FFT_PEAK_BIN_A_LO);
    uint16_t k_prog = (uint32_t)delta_f_hz * (1 << SF) / BW_HZ;
    int16_t  err_a  = (int16_t)k_a - (int16_t)k_prog;
    delta_f_hz     += (int32_t)err_a * BW_HZ / (1 << SF);

    // NT=2: node 2 (−Δf) — bin should be M − k_a; verify independently
    if (nt_mode == 2) {
        uint16_t k_b   = (read_reg(FFT_PEAK_BIN_B_HI) << 8) | read_reg(FFT_PEAK_BIN_B_LO);
        uint16_t k_b_expected = (1 << SF) - k_a;
        int16_t  err_b = (int16_t)k_b - (int16_t)k_b_expected;
        // Log err_b; large value indicates nodes are not at symmetric offsets
    }

    write_reg(DELTA_F_HI, delta_f_hz >> 8);
    write_reg(DELTA_F_LO, delta_f_hz & 0xFF);
}
```

`DRIFT_COMP_INTERVAL` — every 10 packets is conservative; every packet is safe (drift per packet << 1 bin). No RTL changes required.

**NT=2 note:** `DELTA_F` encodes the offset of node 1. Node 2 is symmetric at `M − k₁`. If `err_b` is consistently non-zero, the two nodes are not symmetrically placed; firmware can log this for the host but cannot correct it (the nodes' transmit frequencies are outside ASIC control).

### AGC loop

Triggered at each `IRQ_STATUS.CORR_LOCK`, independent of the later `IRQ_STATUS.H_READY` W-computation path. Reads per-antenna energy latched at preamble lock by the Energy Measurement and adjusts each SX1257's `RegRxAnaGain` (0x0C) independently.

**SX1257 RegRxAnaGain (0x0C) layout:**

| Bits | Field | Range | Step |
| --- | --- | --- | --- |
| [7:5] | `RxLnaGain` | 1 (G1, max) – 6 (G6, min) | 6 dB for G1–G3; **12 dB** for G3–G6 |
| [4:1] | `RxBbGain` | 0 (min) – 15 (max) | 2 dB (gain = −24 + 2×val dB) |
| [0] | `LnaZin` | keep 0 (50 Ω) | — |

Note: `RxLnaGain` is inverted — a higher register value means less gain (G1=0 dB ref, G2=−6, G3=−12, G4=−24, G5=−36, G6=−48 dB). Steps are **non-uniform**: 6 dB between G1–G3, 12 dB between G3–G6. Total range: 48 dB (LNA) + 30 dB (BB) = 78 dB; spec quotes 70 dB usable.

**Control strategy:** Use BB gain for fine tracking (±2 dB/packet). Step LNA gain only when BB hits its limit, restoring BB to mid-scale to maintain headroom. Note that a single LNA step near G3/G4 is 12 dB — if crossing that boundary, two BB steps will not fully compensate; convergence may take 2 packets instead of 1.

```c
// RegRxAnaGain bit packing
#define LNA_G1  1   // maximum LNA gain (0 dB ref)
#define LNA_G6  6   // minimum LNA gain (−48 dB)
#define BB_MAX  15  // maximum BB gain
#define BB_MIN  0   // minimum BB gain
#define BB_MID  7   // restore point after LNA step

// Energy thresholds (ENERGY register: int16 unsigned, Σ|x|² over 8 symbols)
#define AGC_TARGET_LO  0x0800   // ~3%  of full scale — increase gain
#define AGC_TARGET_HI  0x6000   // ~38% of full scale — decrease gain
#define AGC_SAT_GUARD  0xE000   // ~88% of full scale — emergency LNA step

// Start at full gain (G1 + BB_MAX) for maximum sensitivity on the first packet.
// Weak/distant nodes may only just trigger correlator lock — any gain reduction
// at startup risks missing them entirely. Strong-signal saturation is handled
// by discarding corrupted H estimates rather than reducing starting gain.
// Host may override via RX_GAIN_n before releasing CPU_RESET.
static uint8_t lna_gain[4] = {LNA_G1,  LNA_G1,  LNA_G1,  LNA_G1};
static uint8_t bb_gain[4]  = {BB_MAX,  BB_MAX,  BB_MAX,  BB_MAX};
bool ema_reset_pending = false;  // set when any gain changes; consumed by irq_handler

static void agc_write(int n) {
    uint8_t reg = (lna_gain[n] << 5) | (bb_gain[n] << 1);  // LnaZin=0
    spi_master_write(n, 0x0C, reg);
    write_reg(RX_GAIN_0 + n, reg);  // mirror to host-readable register
}

void agc_update() {
    if (read_reg(TX_CTRL) & 0x04) return;  // skip during TX window

    for (int n = 0; n < 4; n++) {
        uint16_t e = read_energy(n);
        bool changed = true;

        if (e > AGC_SAT_GUARD) {
            // Saturation: step LNA down immediately (−6 dB), restore BB to mid
            if      (lna_gain[n] < LNA_G6)  { lna_gain[n]++; bb_gain[n] = BB_MID; }
            else if (bb_gain[n]  > BB_MIN)   { bb_gain[n] = BB_MIN; }
            else                             { changed = false; }  // already at floor
        } else if (e > AGC_TARGET_HI) {
            // Too hot: reduce BB by 2 dB; step LNA if BB exhausted
            if      (bb_gain[n] > BB_MIN)    { bb_gain[n]--; }
            else if (lna_gain[n] < LNA_G6)   { lna_gain[n]++; bb_gain[n] = BB_MAX; }
            else                             { changed = false; }
        } else if (e < AGC_TARGET_LO) {
            // Too cold: increase BB by 2 dB; step LNA if BB exhausted
            if      (bb_gain[n] < BB_MAX)    { bb_gain[n]++; }
            else if (lna_gain[n] > LNA_G1)   { lna_gain[n]--; bb_gain[n] = BB_MIN; }
            else                             { changed = false; }
        } else {
            changed = false;  // within window
        }

        if (changed) { agc_write(n); ema_reset_pending = true; }
    }
}
```

**Convergence.** Starting at G1+BB_MAX gives maximum sensitivity for weak first packets. For a saturating close-range node, `AGC_SAT_GUARD` steps the LNA immediately and H is discarded for that packet — the combiner coasts on the previous W (or waits for the first clean packet if no prior W exists). Fine tracking once in the target window converges in 1–2 packets. For a known deployment, pre-set `RX_GAIN_n` via SPI before releasing `CPU_RESET` to skip convergence entirely.

**No-packets limitation.** AGC only runs at correlator lock — between packets, gain is frozen at its current setting. This is intentional: maximum gain during silence maximises the chance of detecting the next transmission. The saturation-discard path handles the first strong packet cleanly without reducing idle sensitivity.

**Interaction with W.** Gain changes take effect at the start of the next packet. H and N₀ are latched at correlator lock so they are always self-consistent within a packet — no mid-packet gain shift occurs.

**EMA invalidation on gain change.** H scales with receive gain, so `H_prev` (estimated at gain G_N) and `H_new` (at gain G_{N+1}) are not comparable. If any antenna's gain changed this packet, set `ema_reset_pending = true`. On the following correlator lock, skip the EMA and seed `H_prev = H_new` directly, then clear the flag. This ensures the EMA only ever averages estimates from the same gain setting.

**TX guard.** `agc_update()` returns immediately if `TX_ACTIVE` is set. Energy latched during TX is meaningless (combiner has gated antennas 0/1 and antennas 2/3 are receiving TX leakage, not node signal).

**Threshold calibration.** `AGC_TARGET_LO / AGC_TARGET_HI` are initial values; calibrate on silicon against measured ADC output levels from the decimator. `AGC_SAT_GUARD` should be set just below the int8 decimator output saturation point.

---

### FFT-based H estimation (post-silicon fallback)

If correlator H estimation is unreliable on silicon, firmware reads the complex H directly from the FFT H output region in Baseband SRAM (`0x07FE0`–`0x07FFF`, mapped at AHB-Lite address `0x207FE0`):

```c
// After FFT completes (fft_done IRQ or polling):
void use_fft_h() {
    uint32_t base = 0x2007FE0;  // 0x20000 (WB SRAM base) + 0x07FE0 (FFT H output)
    for (int j = 0; j < 4; j++) {
        H_re[j][0] = sram_read16(base + j*4 + 0);  // antenna j, node 1, I
        H_im[j][0] = sram_read16(base + j*4 + 2);  //                    Q
    }
    if (nt_mode == 2) {
        for (int j = 0; j < 4; j++) {
            H_re[j][1] = sram_read16(base + 0x10 + j*4 + 0);  // node 2
            H_im[j][1] = sram_read16(base + 0x10 + j*4 + 2);
        }
    }
    // H is now frequency-offset corrected; proceed with W computation
}
```

Firmware switch: compile with `#define H_SOURCE FFT` vs `#define H_SOURCE CORRELATOR`. Default is `CORRELATOR`. No RTL changes needed for either path.

---

## Memory map

| Address | Region | Size | Notes |
| --- | --- | --- | --- |
| `0x00000` | IMEM (instruction) | 32 KB | Loaded by host via SPI; PicoRV32 fetches from here |
| `0x08000` | DMEM (data/stack) | 32 KB | Separate OpenRAM macro |
| `0x10000` | AHB-Lite peripherals | — | Register bank, SPI master, IRQ, SWD |
| `0x20000` | Baseband SRAM | 544 KB | Shared with FFT engine via arbiter |

---

## Interface (AHB-Lite)

| Peripheral | WB Address | Notes |
| --- | --- | --- |
| Register bank | `0x10000` | All ASIC config/status registers |
| SPI master | `0x10100` | SX1257 register writes |
| IRQ controller | `0x10200` | IRQ source read/clear |
| Baseband SRAM arbiter | `0x20000`+ | Direct-mapped to SRAM (with arbiter) |

---

## Implementation notes

**PicoRV32 IP source.** Use the upstream PicoRV32 repo (Clifford Wolf). Enable `ENABLE_MUL`, `ENABLE_DIV`, `ENABLE_IRQ`. Disable `ENABLE_FAST_MUL` to save gates (iterative MUL is fine for firmware latency budget).

**SRAM arbitration.** PicoRV32 and FFT engine share the Baseband SRAM. A simple priority arbiter grants the bus: FFT has priority during COMPUTE phase; PicoRV32 stalls (AHB-Lite wait-state) until granted. IMEM and DMEM are separate macros — no contention with FFT.

**Firmware load flow:**
```
1. Host asserts CPU_RESET=1 via SPI register write
2. Host burst-writes firmware.bin to IMEM (0x00000)
3. Host de-asserts CPU_RESET=0
4. PicoRV32 fetches from 0x00000; executes SX1257 init, then waits for IRQ
```

**IRQ.** Schmidl-Cox lock arms capture/FFT, but W computation starts from the FFT `h_ready` IRQ. Firmware reads H/N₀ from registers, computes W, writes `W_SHADOW`, then asserts the W commit strobe. Hardware copies `W_SHADOW` into `W_ACTIVE` atomically at the next idle boundary and sets `W_valid`. The live combiner falls back to the selected bypass antenna until `W_valid` is set. If firmware finishes while a packet is active, it leaves that packet in bypass and commits W for the next packet.

---

## Verification

| Test | Method | Pass criterion |
| --- | --- | --- |
| Firmware load + boot | Load minimal test binary via SPI; monitor WB bus | CPU fetches from 0x00000 after CPU_RESET=0 |
| MRC weight computation | Pre-load H/N₀; read back W after IRQ | W matches Python `H* / (‖H‖²+N0)` to ±2 LSB |
| ALMMSE weight computation | Pre-load 4×2 H/N₀; read back W | W matches Python closed-form to ±2 LSB |
| AGC loop | Static channel; vary SX1257 gain via WB | Gain converges within 3 packets |
| AHB-Lite bus | Back-to-back peripheral accesses | No missed ack; correct data |
| SRAM arbitration | Run firmware + FFT simultaneously | No data corruption; CPU stalls correctly |

---

## Related blocks

- [AHB-Lite Bus](AHB-Lite%20Bus.md) — interconnect
- [Baseband SRAM](Baseband%20SRAM.md) — shared memory
- [SPI Master](SPI%20Master.md) — SX1257 config
- [IRQ Controller](IRQ%20Controller.md) — `h_ready`, packet, and TX IRQs
- [Packet Control FSM](Packet%20Control%20FSM.md) — packet phase, safe W commit, W missed status
- [FFT Engine](FFT%20Engine.md) — H/N₀/eps_sub source
- [ALMMSE-MRC Combiner](ALMMSE-MRC%20Combiner.md) — W register target
- [Register Map](../Register%20Map.md) — `CPU_RESET` at `0x02`
