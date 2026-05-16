# Register Map Delta — Non-FFT Frontend

Changes to [Register Map](Register%20Map.md) for the non-FFT frontend path. Apply these changes when finalising the register map for tapeout.

---

## Registers removed (FFT-specific, addresses freed for reuse)

| Address | Name | Reason |
|---|---|---|
| `0x12` | `CAPTURE_CTRL` | FFT capture SRAM removed |
| `0x13` | `CAPTURE_STATUS` | FFT capture SRAM removed |
| `0x14`–`0x16` | `CAPTURE_PTR_*` | FFT capture write pointer removed |
| `0x70`–`0x8F` | H matrix (32 regs) | FFT-derived channel matrix; replaced by Z_j |
| `0xB0`–`0xB7` | `N0_*` (8 regs) | FFT noise variance; no equivalent in non-FFT path |
| `0xB8`–`0xB9` | `EPS_SUB` | FFT fractional CFO; not used in non-FFT path |
| `0xC0`–`0xC9` | FFT diagnostics (10 regs) | FFT peak bin, peak magnitude, noise floor |

Freed address space: `0x12`–`0x16`, `0x70`–`0x8F`, `0xB0`–`0xB9`, `0xC0`–`0xC9`

---

## Registers modified

### `0x11` — `SF_CFG`

Description update only. Remove reference to FFT engine. SF_CFG now configures M = 2^SF for:

- Frontend Buffer Controller (rolling window depth)
- SC correlator (symbol length)
- Training Accumulator (accumulation window)
- Packet Control FSM (timing arithmetic)

### `0x49` — `IRQ_STATUS`

| Bit | Old | New |
|---|---|---|
| [1] | `H_READY` — FFT outputs H/N0 valid | `TRAINING_DONE` — training accumulator complete; weight gen should run |
| [3] | `CAPTURE_DONE` — diagnostic capture complete | `PACKET_DONE` — FSM returned to IDLE (packet ended or timed out) |
| [4] | `CAPTURE_OVERFLOW` — capture buffer overwritten | Reserved |

### `0x4B` — `PACKET_STATUS`

| Bits | Old | New |
|---|---|---|
| [3:1] `PACKET_PHASE` | 0=IDLE, 1=PREAMBLE_DETECTED, 2=FFT_WAIT, 3=W_COMMIT_WINDOW, 4=PAYLOAD_ACTIVE, 5=PACKET_DONE | 0=IDLE, 1=PREAMBLE_ACQ, 2=W_PENDING, 3=PAYLOAD_ACTIVE |
| [4] | `LIVE_FFT_READY` — 8-symbol FFT window resident | `TRAINING_DONE` — training accumulator complete this packet |
| [5] | `W_PENDING` — H_READY occurred, W commit pending | `W_PENDING` — training done, W commit pending (semantics unchanged) |

### `0x4C` — `W_CTRL`

Description update: W_SHADOW is now written by the weight generation block (hardware state machine or PicoRV32 reading Z_j), not by PicoRV32 reading FFT H/N0. Interface and bit layout unchanged.

### `0x90`–`0xAF` — W matrix → W vector

`0x90`–`0x9F`: W is now a 4-element complex vector (not a 2×4 matrix). Computed from Z_j by the weight generation block (hardware or PicoRV32 software path). NT=2 ALMMSE is removed; only MRC and passthrough modes apply.

`0xA0`–`0xAF`: Reserved (were NT=2 second-row weights).

---

## NT=2 / ALMMSE removal

Decided after non-FFT architecture lock. All NT=2 references are eliminated.

| Address | Change |
|---|---|
| `0x10` `MIMO_CTRL` [1:0] | Was 2-bit field (0=MRC, 1=ALMMSE, 2=passthrough, 3=auto); now [0]=MODE (0=MRC, 1=passthrough), [1]=reserved. Reset value corrected to `0xF0`. |
| `0x20`–`0x21` `DELTA_F` | Reserved. Was NT=2 node frequency offset; no longer needed. |
| `0x34`–`0x35` `TX_GAIN_0/1` | Removed "NT=2 only" annotation; these are still used for TDD TX window switching. |
| `0x40` `ACTIVE_MODE` | Values: 0=MRC, 1=passthrough (was 0=MRC, 1=ALMMSE). |
| `0x45`–`0x46` `SNR_1` | Reserved (was NT=2 node 2 SNR). |
| `0x60`–`0x6F` | Reserved (was NT=2 node 2 correlator magnitudes). |
| `0xA0`–`0xAF` | Reserved (was NT=2 second row of W matrix). |

---

## JTAG / GPIO addition

New registers at `0x03`–`0x06`. These addresses were previously unused (reserved) in the `0x00`–`0x0F` global control block.

| Address | Name | R/W | Reset | Description |
|---|---|---|---|---|
| `0x03` | `DEBUG_CTRL` | R/W | `0x00` | [0] `JTAG_EN`: 0=normal (TCK_IRQ=IRQ out, TMS/TDI/TDO_GPIO_n=GPIO), 1=JTAG debug (4-pin JTAG active, IRQ suppressed on pad); [7:1] reserved |
| `0x04` | `GPIO_DIR` | R/W | `0x00` | [0] GPIO_0 dir (`TMS_GPIO0`), [1] GPIO_1 dir (`TDI_GPIO1`), [2] GPIO_2 dir (`TDO_GPIO2`); 1=output, 0=input; ignored when `JTAG_EN=1`; [7:3] reserved |
| `0x05` | `GPIO_OUT` | R/W | `0x00` | [0] GPIO_0 drive, [1] GPIO_1, [2] GPIO_2; only drives pad when `GPIO_DIR[n]=1` and `JTAG_EN=0`; [7:3] reserved |
| `0x06` | `GPIO_IN` | R | `0x00` | [0] GPIO_0 sampled, [1] GPIO_1, [2] GPIO_2; valid when `JTAG_EN=0` and `GPIO_DIR[n]=0`; [7:3] return 0 |

`0x07`–`0x0F` remain reserved.

---

## Registers added

### `0x12`–`0x16` — Frontend Buffer (replaces Capture CTRL)

| Address | Name | R/W | Reset | Description |
|---|---|---|---|---|
| `0x12` | `FRONTEND_CFG` | R/W | `0x00` | [0] STORE_W: 0=8-bit saturated storage (1kB = 2 symbols at SF6), 1=16-bit storage (1kB = 1 symbol only — requires 2kB SRAM); [1] BIST_RUN: write 1 to trigger SRAM BIST, self-clears; [7:2] reserved |
| `0x13` | `FRONTEND_STATUS` | R | `0x00` | [1:0] BUF_MODE (0=idle, 1=acquiring, 2=frozen, 3=post-lock); [2] BUF_VALID (buffer has ≥ M samples); [3] SRAM0_BIST_PASS; [4] SRAM1_BIST_PASS; [5] BUF_FREEZE active |
| `0x14` | `BUF_WR_PTR` | R | `0x00` | [6:0] current write pointer mod 128; [7] buf_freeze active |
| `0x15` | `PKT_TIMEOUT_SYMS` | R/W | `0x50` | Packet timeout in symbols (default 80 = sufficient for max LoRa payload at SF6/125kHz/CR4-5). FSM returns to IDLE if no new sc_lock arrives within this many symbols of timing_ref |
| `0x16` | reserved | — | `0x00` | — |

### `0x70`–`0x8F` — Z_j scaled readback (replaces H matrix)

Training accumulator output exposed for PicoRV32 firmware weight computation path. Values are the int64 Z_j right-shifted by `Z_SHIFT` (see `0xB3`) to fit in int32. Written by hardware after `training_done`; valid until next sc_lock.

| Address | Name | R/W | Reset | Description |
|---|---|---|---|---|
| `0x70`–`0x73` | `Z0_I` | R | `0x00` | Branch 0 I component [31:0] big-endian int32 |
| `0x74`–`0x77` | `Z0_Q` | R | `0x00` | Branch 0 Q component [31:0] |
| `0x78`–`0x7B` | `Z1_I` | R | `0x00` | Branch 1 I [31:0] |
| `0x7C`–`0x7F` | `Z1_Q` | R | `0x00` | Branch 1 Q [31:0] |
| `0x80`–`0x83` | `Z2_I` | R | `0x00` | Branch 2 I [31:0] |
| `0x84`–`0x87` | `Z2_Q` | R | `0x00` | Branch 2 Q [31:0] |
| `0x88`–`0x8B` | `Z3_I` | R | `0x00` | Branch 3 I [31:0] |
| `0x8C`–`0x8F` | `Z3_Q` | R | `0x00` | Branch 3 Q [31:0] |

### `0xB0`–`0xB9` — Training diagnostics (replaces N0 / EPS_SUB)

| Address | Name | R/W | Reset | Description |
|---|---|---|---|---|
| `0xB0` | `TRAINING_STATUS` | R | `0x00` | [0] TRAINING_DONE (latched, cleared on next sc_lock); [1] TRAINING_ARMED (accumulator active); [7:2] reserved |
| `0xB1` | `N_ACC_HI` | R | `0x00` | Samples accumulated in last training window [15:8] |
| `0xB2` | `N_ACC_LO` | R | `0x00` | Samples accumulated [7:0] |
| `0xB3` | `Z_SHIFT` | R | `0x00` | [5:0] right-shift K applied to Z_j for Z_j_scaled register readout; common across all branches |
| `0xB4` | `C_POOL_I_HI` | R | `0x00` | Pooled SC correlator real part [15:8] int16 — CFO diagnostic; latched at sc_lock |
| `0xB5` | `C_POOL_I_LO` | R | `0x00` | C_POOL I [7:0] |
| `0xB6` | `C_POOL_Q_HI` | R | `0x00` | Pooled SC correlator imag part [15:8] |
| `0xB7` | `C_POOL_Q_LO` | R | `0x00` | C_POOL Q [7:0] |
| `0xB8` | `CFO_DIAG_HI` | R | `0x00` | Coarse CFO estimate -angle(C_pool)/M [15:8] Q1.15 rad/sample — diagnostic only; not used in weight path |
| `0xB9` | `CFO_DIAG_LO` | R | `0x00` | CFO_DIAG [7:0] |

### `0xC0`–`0xC9` — Calibration coefficients (replaces FFT diagnostics)

Per-branch static gain/phase calibration coefficients applied before weight computation. Written by host at startup; not updated at runtime. Default = 1+0j (no correction) for all branches.

| Address | Name | R/W | Reset | Description |
|---|---|---|---|---|
| `0xC0` | `CAL_0_I_HI` | R/W | `0x7F` | Branch 0 calibration I [15:8] Q1.15; default 1.0 = 0x7FFF |
| `0xC1` | `CAL_0_I_LO` | R/W | `0xFF` | Branch 0 calibration I [7:0] |
| `0xC2` | `CAL_0_Q_HI` | R/W | `0x00` | Branch 0 calibration Q [15:8]; default 0.0 |
| `0xC3` | `CAL_0_Q_LO` | R/W | `0x00` | Branch 0 calibration Q [7:0] |
| `0xC4` | `CAL_1_I_HI` | R/W | `0x7F` | Branch 1 calibration I [15:8] |
| `0xC5` | `CAL_1_I_LO` | R/W | `0xFF` | Branch 1 calibration I [7:0] |
| `0xC6` | `CAL_1_Q_HI` | R/W | `0x00` | Branch 1 calibration Q [15:8] |
| `0xC7` | `CAL_1_Q_LO` | R/W | `0x00` | Branch 1 calibration Q [7:0] |
| `0xC8` | `CAL_2_I_HI` | R/W | `0x7F` | Branch 2 calibration I [15:8] |
| `0xC9` | `CAL_2_I_LO` | R/W | `0xFF` | Branch 2 calibration I [7:0] |

### `0xCE`–`0xD3` — Calibration coefficients continued (replaces reserved)

| Address | Name | R/W | Reset | Description |
|---|---|---|---|---|
| `0xCE` | `CAL_2_Q_HI` | R/W | `0x00` | Branch 2 calibration Q [15:8] |
| `0xCF` | `CAL_2_Q_LO` | R/W | `0x00` | Branch 2 calibration Q [7:0] |
| `0xD0` | `CAL_3_I_HI` | R/W | `0x7F` | Branch 3 calibration I [15:8] |
| `0xD1` | `CAL_3_I_LO` | R/W | `0xFF` | Branch 3 calibration I [7:0] |
| `0xD2` | `CAL_3_Q_HI` | R/W | `0x00` | Branch 3 calibration Q [15:8] |
| `0xD3` | `CAL_3_Q_LO` | R/W | `0x00` | Branch 3 calibration Q [7:0] |

### `0xDE`–`0xF2` — Noise Floor Estimator (new)

| Address | Name | R/W | Reset | Description |
|---|---|---|---|---|
| `0xDE` | `NFE_CTRL` | R/W | `0x04` | [0] `SIGMA2_SRC`: 0=HW (default), 1=SW override; [3:1] `NOISE_ALPHA_SHIFT`: EMA decay exponent (default 4 → α=1/16); [7:4] reserved |
| `0xDF` | `NFE_STATUS` | R | `0x00` | [0] `SIGMA2_VALID`: at least one idle window accumulated since reset or AGC gain change; [7:1] reserved |
| `0xE0` | `NOISE_THRESH_HI` | R/W | `0x00` | Near-far guard threshold [15:8]; energy above this suppresses noise update |
| `0xE1` | `NOISE_THRESH_LO` | R/W | `0x00` | Near-far guard threshold [7:0]; recommended: AGC_TARGET / 8 |
| `0xE2` | `SIGMA2_0_HW_HI` | R | `0x00` | Branch 0 hardware EMA estimate [15:8] UQ2.14 |
| `0xE3` | `SIGMA2_0_HW_LO` | R | `0x00` | Branch 0 hardware EMA estimate [7:0] |
| `0xE4` | `SIGMA2_1_HW_HI` | R | `0x00` | Branch 1 hardware EMA estimate [15:8] |
| `0xE5` | `SIGMA2_1_HW_LO` | R | `0x00` | Branch 1 hardware EMA estimate [7:0] |
| `0xE6` | `SIGMA2_2_HW_HI` | R | `0x00` | Branch 2 hardware EMA estimate [15:8] |
| `0xE7` | `SIGMA2_2_HW_LO` | R | `0x00` | Branch 2 hardware EMA estimate [7:0] |
| `0xE8` | `SIGMA2_3_HW_HI` | R | `0x00` | Branch 3 hardware EMA estimate [15:8] |
| `0xE9` | `SIGMA2_3_HW_LO` | R | `0x00` | Branch 3 hardware EMA estimate [7:0] |
| `0xEA` | `SIGMA2_0_SW_HI` | R/W | `0x00` | Branch 0 SW shadow [15:8]; written by firmware before SIGMA2_COMMIT |
| `0xEB` | `SIGMA2_0_SW_LO` | R/W | `0x00` | Branch 0 SW shadow [7:0] |
| `0xEC` | `SIGMA2_1_SW_HI` | R/W | `0x00` | Branch 1 SW shadow [15:8] |
| `0xED` | `SIGMA2_1_SW_LO` | R/W | `0x00` | Branch 1 SW shadow [7:0] |
| `0xEE` | `SIGMA2_2_SW_HI` | R/W | `0x00` | Branch 2 SW shadow [15:8] |
| `0xEF` | `SIGMA2_2_SW_LO` | R/W | `0x00` | Branch 2 SW shadow [7:0] |
| `0xF0` | `SIGMA2_3_SW_HI` | R/W | `0x00` | Branch 3 SW shadow [15:8] |
| `0xF1` | `SIGMA2_3_SW_LO` | R/W | `0x00` | Branch 3 SW shadow [7:0] |
| `0xF2` | `SIGMA2_COMMIT` | W | `0x00` | Write 1 to latch SW shadow into active SW estimate; self-clears next cycle. Has no effect when `SIGMA2_SRC=HW`. |

---

### SC debug registers — relocated

Existing SC debug registers `0xD0`–`0xD9` conflict with the calibration extension above. Relocate to `0xD4`–`0xDD`:

| Old address | New address | Name |
|---|---|---|
| `0xD0` | `0xD4` | `SC_DBG_FLAGS` |
| `0xD1` | `0xD5` | `SC_DBG_RSVD` |
| `0xD2` | `0xD6` | `SC_FIRST_HIT_3` |
| `0xD3` | `0xD7` | `SC_FIRST_HIT_2` |
| `0xD4` | `0xD8` | `SC_FIRST_HIT_1` |
| `0xD5` | `0xD9` | `SC_FIRST_HIT_0` |
| `0xD6` | `0xDA` | `SC_LOCK_SNAP_3` |
| `0xD7` | `0xDB` | `SC_LOCK_SNAP_2` |
| `0xD8` | `0xDC` | `SC_LOCK_SNAP_1` |
| `0xD9` | `0xDD` | `SC_LOCK_SNAP_0` |

---

## Updated address range reservations

| Range | Block |
|---|---|
| `0x00`–`0x02` | Chip identity and global control — unchanged |
| `0x03`–`0x06` | JTAG/GPIO control: `DEBUG_CTRL`, `GPIO_DIR`, `GPIO_OUT`, `GPIO_IN` (new) |
| `0x07`–`0x0F` | Reserved |
| `0x10`–`0x1F` | Mode & configuration; `0x12`–`0x16` now Frontend Buffer |
| `0x20`–`0x2F` | `0x20`–`0x21` reserved (DELTA_F removed); `0x22`–`0x2F` reserved |
| `0x30`–`0x3F` | Gain control — unchanged |
| `0x40`–`0x4F` | Status summary — updated IRQ bits and PACKET_STATUS encoding |
| `0x50`–`0x57` | Energy detector — unchanged |
| `0x58`–`0x6F` | `0x58`–`0x5F` SC per-branch autocorr magnitudes; `0x60`–`0x6F` reserved (NT=2 removed) |
| `0x70`–`0x8F` | Z_j scaled readback (was H matrix) |
| `0x90`–`0xAF` | `0x90`–`0x9F` MRC weight vector w (4 complex, Q1.15); `0xA0`–`0xAF` reserved (NT=2 removed) |
| `0xB0`–`0xB9` | Training diagnostics: N_ACC, Z_SHIFT, C_POOL, CFO_DIAG (was N0/EPS_SUB) |
| `0xBA`–`0xBF` | Reserved |
| `0xC0`–`0xC9` | Calibration coefficients branches 0–2 I (was FFT diagnostics) |
| `0xCA`–`0xCD` | SX1257 pass-through — unchanged |
| `0xCE`–`0xD3` | Calibration coefficients branches 2 Q – 3 Q |
| `0xD4`–`0xDD` | SC bring-up debug (relocated from `0xD0`–`0xD9`) |
| `0xDE`–`0xF2` | Noise Floor Estimator control and readback (new) |
| `0xF3`–`0xFF` | Reserved |
