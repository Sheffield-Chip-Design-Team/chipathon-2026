# Register Map

Internal registers accessible via SPI slave interface (RPi SPI0 CS1 → ASIC). See [System Architecture](System%20Diagram.md) for the full interface description.

All registers are 8-bit. Multi-byte values are big-endian (MSB at lower address). Addresses not listed here return `0x00` on read and ignore writes.

---

## Address map

| Address | Name | R/W | Reset | Block | Description |
| --- | --- | --- | --- | --- | --- |
| **Chip Identity & Global Control** (`0x00`–`0x0F`) | | | | | |
| `0x00` | `CHIP_ID` | R | `0xA7` | — | Chip identification byte |
| `0x01` | `CHIP_REV` | R | `0x01` | — | Silicon revision |
| `0x02` | `CPU_RESET` | R/W | `0x01` | Control | [0] = cpu_reset (1 = PicoRV32 held in reset); write 0 to start CPU after firmware load |
| `0x03` | `DEBUG_CTRL` | R/W | `0x00` | JTAG TAP | [0] JTAG_EN: 0=normal (TCK_IRQ=IRQ, TMS/TDI/TDO_GPIO_n=GPIO), 1=debug (4-pin JTAG active); [7:1] reserved |
| `0x04` | `GPIO_DIR` | R/W | `0x00` | JTAG TAP | [0] GPIO_0 dir (TMS_GPIO0), [1] GPIO_1 dir (TDI_GPIO1), [2] GPIO_2 dir (TDO_GPIO2); 1=output, 0=input; [7:3] reserved. Ignored when JTAG_EN=1. |
| `0x05` | `GPIO_OUT` | R/W | `0x00` | JTAG TAP | [0] GPIO_0 drive value, [1] GPIO_1, [2] GPIO_2; only drives pad when corresponding GPIO_DIR bit=1; [7:3] reserved. Ignored when JTAG_EN=1. |
| `0x06` | `GPIO_IN` | R | `0x00` | JTAG TAP | [0] GPIO_0 sampled pad value, [1] GPIO_1, [2] GPIO_2; valid when JTAG_EN=0 and corresponding GPIO_DIR bit=0; [7:3] reserved |
| `0x07`–`0x0F` | — | — | — | — | Reserved |
| **Mode & Configuration** (`0x10`–`0x1F`) | | | | | |
| `0x10` | `MIMO_CTRL` | R/W | `0xF0` | Control | [0] MODE (0=MRC, 1=passthrough); [1] reserved, write 0; [3:2] reserved; [7:4] ANTENNA_EN (1 bit per antenna, default all 4 enabled) |
| `0x11` | `SF_CFG` | R/W | `0x07` | FFT Engine | [2:0] sf (0=SF5, 1=SF6, … 7=SF12); [7:3] reserved |
| `0x12` | `CAPTURE_CTRL` | R/W | `0x00` | Baseband SRAM | [0] CAPTURE_EN (write 1 to arm); [1] CAPTURE_MODE (0=raw samples, 1=FFT output); [7:2] reserved |
| `0x13` | `CAPTURE_STATUS` | R | `0x00` | Baseband SRAM | [0] CAPTURE_DONE; [1] CAPTURE_OVERFLOW; [7:2] reserved |
| `0x14` | `CAPTURE_PTR_HI` | R | `0x00` | Baseband SRAM | Capture write pointer [19:16] — current write position in capture RAM |
| `0x15` | `CAPTURE_PTR_MID` | R | `0x00` | Baseband SRAM | Capture write pointer [15:8] |
| `0x16` | `CAPTURE_PTR_LO` | R | `0x00` | Baseband SRAM | Capture write pointer [7:0]; frozen while `fft_active` |
| `0x17` | `TX_CTRL` | R/W | `0x00` | PicoRV32 FW | [0] TX_PREP; [1] TX_DONE; [2] TX_ACTIVE |
| `0x18` | `ENERGY_THR_HI` | R/W | `0x00` | Energy Measurement | Optional coarse energy-floor threshold [15:8]; used only if `SC_CFG.ENERGY_GATE_EN=1` |
| `0x19` | `ENERGY_THR_LO` | R/W | `0x00` | Energy Measurement | Optional coarse energy-floor threshold [7:0] |
| `0x1A` | `LOW_BAT_THR` | R/W | `0x02` | Control | Low battery threshold configuration |
| `0x1B` | `DECIM_CFG` | R/W | `0x00` | ΣΔ Decimator | [1:0] DECIM_RATIO: 0=32× (1 MHz), 1=64× (500 kHz), 2=128× (250 kHz), 3=256× (125 kHz); [7:2] reserved |
| `0x1C` | `SC_THR_HI` | R/W | `0x73` | Schmidl-Cox | Detection threshold θ_SC [15:8] (Q1.15); default 0.90 |
| `0x1D` | `SC_THR_LO` | R/W | `0x33` | Schmidl-Cox | Detection threshold θ_SC [7:0] |
| `0x1E` | `SC_HITS_REQ` | R/W | `0x02` | Schmidl-Cox | Number of consecutive above-threshold SC hits required for `sc_lock`; valid range 1–3 |
| `0x1F` | `SC_CFG` | R/W | `0x00` | Schmidl-Cox | [0] ENERGY_GATE_EN optional coarse energy floor enable; [7:1] reserved |
| **Frequency Configuration** (`0x20`–`0x2F`) | | | | | |
| `0x20` | — | — | — | — | Reserved (was DELTA_F_HI; NT=2 removed) |
| `0x21` | — | — | — | — | Reserved (was DELTA_F_LO; NT=2 removed) |
| **Gain Control** (`0x30`–`0x3F`) | | | | | |
| `0x30` | `RX_GAIN_0` | R/W | `0x3E` | SPI Master | SX1257_1 RegRxAnaGain mirror: [7:5] RxLnaGain (1=G1 max … 6=G6 min; steps: G1–G3 6 dB each, G3–G6 12 dB each), [4:1] RxBbGain (0–15, 2 dB/step, gain = −24+2×val dB), [0] LnaZin keep 0 (50 Ω); written by PicoRV32 AGC loop |
| `0x31` | `RX_GAIN_1` | R/W | `0x3E` | SPI Master | SX1257_2 RegRxAnaGain mirror |
| `0x32` | `RX_GAIN_2` | R/W | `0x3E` | SPI Master | SX1257_3 RegRxAnaGain mirror |
| `0x33` | `RX_GAIN_3` | R/W | `0x3E` | SPI Master | SX1257_4 RegRxAnaGain mirror |
| `0x34` | `TX_GAIN_0` | R/W | `0x08` | SPI Master | SX1257_1 TxGain — used during TDD TX window |
| `0x35` | `TX_GAIN_1` | R/W | `0x08` | SPI Master | SX1257_2 TxGain — used during TDD TX window |
| **Status Summary** (`0x40`–`0x4F`) | | | | | |
| `0x40` | `ACTIVE_MODE` | R | `0x00` | Control | Current active mode: 0=MRC, 1=passthrough; latched at idle boundary from MIMO_CTRL.MODE shadow |
| `0x41` | `COND_NUM_HI` | R | `0x00` | PicoRV32 FW | Channel matrix condition number [15:8], log dB, 0.1 dB/LSB; updated after each packet |
| `0x42` | `COND_NUM_LO` | R | `0x00` | PicoRV32 FW | Condition number [7:0] |
| `0x43` | `SNR_0_HI` | R | `0x00` | PicoRV32 FW | Post-combining SNR for node 0 [15:8], 0.1 dB/LSB; signed |
| `0x44` | `SNR_0_LO` | R | `0x00` | PicoRV32 FW | SNR node 0 [7:0] |
| `0x45` | — | — | — | — | Reserved (was SNR_1; NT=2 removed) |
| `0x46` | — | — | — | — | Reserved |
| `0x47` | `SC_STAT_HI` | R | `0x00` | Schmidl-Cox | Current Λ²[s] magnitude-squared value [15:8] (Q4.12) |
| `0x48` | `SC_STAT_LO` | R | `0x00` | Schmidl-Cox | Current Λ²[s] magnitude-squared value [7:0] |
| `0x49` | `IRQ_STATUS` | R | `0x00` | IRQ Controller | Sticky IRQ source bits: [0] CORR_LOCK, [1] H_READY, [2] W_MISSED_PACKET, [3] CAPTURE_DONE, [4] CAPTURE_OVERFLOW, [5] TX_PREP, [6] TX_DONE |
| `0x4A` | `IRQ_CLEAR` | W | `0x00` | IRQ Controller | Write 1 to clear corresponding `IRQ_STATUS` bit |
| `0x4B` | `PACKET_STATUS` | R | `0x00` | Packet Control FSM | [0] PACKET_ACTIVE; [3:1] PACKET_PHASE; [4] LIVE_FFT_READY; [5] W_PENDING; [6] W_VALID; [7] W_MISSED_PACKET |
| `0x4C` | `W_CTRL` | R/W | `0x00` | Packet Control FSM / Combiner | [0] W_COMMIT write-1 pulse; [1] W_VALID read-only; [2] W_PENDING read-only; [3] W_MISSED_PACKET read-only; [7:4] reserved |
| `0x4D` | `ACTIVE_ANTENNA_EN` | R | `0x0F` | Packet Control FSM | Latched active antenna mask for current packet |
| **Energy Measurement** (`0x50`–`0x57`) | | | | | |
| `0x50` | `ENERGY_0_HI` | R | `0x00` | Energy Measurement | Σ\|x\|² antenna 0 [15:8]; snapshot at correlator lock |
| `0x51` | `ENERGY_0_LO` | R | `0x00` | Energy Measurement | Σ\|x\|² antenna 0 [7:0] |
| `0x52` | `ENERGY_1_HI` | R | `0x00` | Energy Measurement | Σ\|x\|² antenna 1 [15:8] |
| `0x53` | `ENERGY_1_LO` | R | `0x00` | Energy Measurement | Σ\|x\|² antenna 1 [7:0] |
| `0x54` | `ENERGY_2_HI` | R | `0x00` | Energy Measurement | Σ\|x\|² antenna 2 [15:8] |
| `0x55` | `ENERGY_2_LO` | R | `0x00` | Energy Measurement | Σ\|x\|² antenna 2 [7:0] |
| `0x56` | `ENERGY_3_HI` | R | `0x00` | Energy Measurement | Σ\|x\|² antenna 3 [15:8] |
| `0x57` | `ENERGY_3_LO` | R | `0x00` | Energy Measurement | Σ\|x\|² antenna 3 [7:0] |
| **SC Correlator Energy** (`0x58`–`0x67`) | | | | | |
| `0x58` | `CORR_MAG_0_HI` | R | `0x00` | Correlator Bank | \|c₀\|² per-branch SC autocorr magnitude antenna 0 [15:8] |
| `0x59` | `CORR_MAG_0_LO` | R | `0x00` | Correlator Bank | [7:0] |
| `0x5A` | `CORR_MAG_1_HI` | R | `0x00` | Correlator Bank | \|c₁\|² antenna 1 [15:8] |
| `0x5B` | `CORR_MAG_1_LO` | R | `0x00` | Correlator Bank | [7:0] |
| `0x5C` | `CORR_MAG_2_HI` | R | `0x00` | Correlator Bank | \|c₂\|² antenna 2 [15:8] |
| `0x5D` | `CORR_MAG_2_LO` | R | `0x00` | Correlator Bank | [7:0] |
| `0x5E` | `CORR_MAG_3_HI` | R | `0x00` | Correlator Bank | \|c₃\|² antenna 3 [15:8] |
| `0x5F` | `CORR_MAG_3_LO` | R | `0x00` | Correlator Bank | [7:0] |
| `0x60`–`0x6F` | — | — | — | — | Reserved (was NT=2 node 2 correlator; NT=2 removed) |
| **H Matrix** (`0x70`–`0x8F`) | | | | | |
| `0x70` | `H_00_RE_HI` | R | `0x00` | PicoRV32 FW | H[antenna 0, node 1] real [15:8], int16 Q1.15 |
| `0x71` | `H_00_RE_LO` | R | `0x00` | PicoRV32 FW | H[0,1] real [7:0] |
| `0x72` | `H_00_IM_HI` | R | `0x00` | PicoRV32 FW | H[0,1] imag [15:8] |
| `0x73` | `H_00_IM_LO` | R | `0x00` | PicoRV32 FW | H[0,1] imag [7:0] |
| `0x74` | `H_10_RE_HI` | R | `0x00` | PicoRV32 FW | H[antenna 1, node 1] real [15:8] |
| `0x75` | `H_10_RE_LO` | R | `0x00` | PicoRV32 FW | [7:0] |
| `0x76` | `H_10_IM_HI` | R | `0x00` | PicoRV32 FW | H[1,1] imag [15:8] |
| `0x77` | `H_10_IM_LO` | R | `0x00` | PicoRV32 FW | [7:0] |
| `0x78` | `H_20_RE_HI` | R | `0x00` | PicoRV32 FW | H[antenna 2, node 1] real [15:8] |
| `0x79` | `H_20_RE_LO` | R | `0x00` | PicoRV32 FW | [7:0] |
| `0x7A` | `H_20_IM_HI` | R | `0x00` | PicoRV32 FW | H[2,1] imag [15:8] |
| `0x7B` | `H_20_IM_LO` | R | `0x00` | PicoRV32 FW | [7:0] |
| `0x7C` | `H_30_RE_HI` | R | `0x00` | PicoRV32 FW | H[antenna 3, node 1] real [15:8] |
| `0x7D` | `H_30_RE_LO` | R | `0x00` | PicoRV32 FW | [7:0] |
| `0x7E` | `H_30_IM_HI` | R | `0x00` | PicoRV32 FW | H[3,1] imag [15:8] |
| `0x7F` | `H_30_IM_LO` | R | `0x00` | PicoRV32 FW | [7:0] |
| `0x80` | `H_01_RE_HI` | R | `0x00` | PicoRV32 FW | H[antenna 0, node 2] real [15:8] (NT=2 only) |
| `0x81` | `H_01_RE_LO` | R | `0x00` | PicoRV32 FW | [7:0] |
| `0x82` | `H_01_IM_HI` | R | `0x00` | PicoRV32 FW | H[0,2] imag [15:8] |
| `0x83` | `H_01_IM_LO` | R | `0x00` | PicoRV32 FW | [7:0] |
| `0x84` | `H_11_RE_HI` | R | `0x00` | PicoRV32 FW | H[1,2] real [15:8] |
| `0x85` | `H_11_RE_LO` | R | `0x00` | PicoRV32 FW | [7:0] |
| `0x86` | `H_11_IM_HI` | R | `0x00` | PicoRV32 FW | H[1,2] imag [15:8] |
| `0x87` | `H_11_IM_LO` | R | `0x00` | PicoRV32 FW | [7:0] |
| `0x88` | `H_21_RE_HI` | R | `0x00` | PicoRV32 FW | H[2,2] real [15:8] |
| `0x89` | `H_21_RE_LO` | R | `0x00` | PicoRV32 FW | [7:0] |
| `0x8A` | `H_21_IM_HI` | R | `0x00` | PicoRV32 FW | H[2,2] imag [15:8] |
| `0x8B` | `H_21_IM_LO` | R | `0x00` | PicoRV32 FW | [7:0] |
| `0x8C` | `H_31_RE_HI` | R | `0x00` | PicoRV32 FW | H[3,2] real [15:8] |
| `0x8D` | `H_31_RE_LO` | R | `0x00` | PicoRV32 FW | [7:0] |
| `0x8E` | `H_31_IM_HI` | R | `0x00` | PicoRV32 FW | H[3,2] imag [15:8] |
| `0x8F` | `H_31_IM_LO` | R | `0x00` | PicoRV32 FW | [7:0] |
| **W Vector — MRC weights** (`0x90`–`0x9F`) | | | | | |
| `0x90` | `W_0_RE_HI` | R/W | `0x00` | MRC Combiner | w[antenna 0] real [15:8], int16 Q1.15; written by weight gen / PicoRV32 |
| `0x91` | `W_0_RE_LO` | R/W | `0x00` | MRC Combiner | [7:0] |
| `0x92` | `W_0_IM_HI` | R/W | `0x00` | MRC Combiner | w[0] imag [15:8] |
| `0x93` | `W_0_IM_LO` | R/W | `0x00` | MRC Combiner | [7:0] |
| `0x94` | `W_1_RE_HI` | R/W | `0x00` | MRC Combiner | w[antenna 1] real [15:8] |
| `0x95` | `W_1_RE_LO` | R/W | `0x00` | MRC Combiner | [7:0] |
| `0x96` | `W_1_IM_HI` | R/W | `0x00` | MRC Combiner | w[1] imag [15:8] |
| `0x97` | `W_1_IM_LO` | R/W | `0x00` | MRC Combiner | [7:0] |
| `0x98` | `W_2_RE_HI` | R/W | `0x00` | MRC Combiner | w[antenna 2] real [15:8] |
| `0x99` | `W_2_RE_LO` | R/W | `0x00` | MRC Combiner | [7:0] |
| `0x9A` | `W_2_IM_HI` | R/W | `0x00` | MRC Combiner | w[2] imag [15:8] |
| `0x9B` | `W_2_IM_LO` | R/W | `0x00` | MRC Combiner | [7:0] |
| `0x9C` | `W_3_RE_HI` | R/W | `0x00` | MRC Combiner | w[antenna 3] real [15:8] |
| `0x9D` | `W_3_RE_LO` | R/W | `0x00` | MRC Combiner | [7:0] |
| `0x9E` | `W_3_IM_HI` | R/W | `0x00` | MRC Combiner | w[3] imag [15:8] |
| `0x9F` | `W_3_IM_LO` | R/W | `0x00` | MRC Combiner | [7:0] |
| `0xA0`–`0xAF` | — | — | — | — | Reserved (was W row 2 for NT=2 node 2; NT=2 removed) |
| **Noise Variance N₀** (`0xB0`–`0xB7`) | | | | | |
| `0xB0` | `N0_0_HI` | R | `0x00` | FFT Engine | Noise variance N₀ antenna 0 [15:8], int16 |
| `0xB1` | `N0_0_LO` | R | `0x00` | FFT Engine | [7:0] |
| `0xB2` | `N0_1_HI` | R | `0x00` | FFT Engine | N₀ antenna 1 [15:8] |
| `0xB3` | `N0_1_LO` | R | `0x00` | FFT Engine | [7:0] |
| `0xB4` | `N0_2_HI` | R | `0x00` | FFT Engine | N₀ antenna 2 [15:8] |
| `0xB5` | `N0_2_LO` | R | `0x00` | FFT Engine | [7:0] |
| `0xB6` | `N0_3_HI` | R | `0x00` | FFT Engine | N₀ antenna 3 [15:8] |
| `0xB7` | `N0_3_LO` | R | `0x00` | FFT Engine | [7:0] |
| `0xB8` | `EPS_SUB_HI` | R | `0x00` | FFT Engine | Fractional CFO estimate `eps_sub` [15:8], signed Q1.15 bins |
| `0xB9` | `EPS_SUB_LO` | R | `0x00` | FFT Engine | `eps_sub` [7:0] |
| `0xBA`–`0xBF` | — | — | — | — | Reserved |
| **FFT Diagnostics** (`0xC0`–`0xC9`) | | | | | |
| `0xC0` | `FFT_PEAK_BIN_A_HI` | R | `0x00` | FFT Engine | Node 1 (+Δf) peak bin [15:8]; range 0–(2^SF−1) |
| `0xC1` | `FFT_PEAK_BIN_A_LO` | R | `0x00` | FFT Engine | Node 1 peak bin [7:0] |
| `0xC2` | `FFT_PEAK_BIN_B_HI` | R | `0x00` | FFT Engine | Node 2 (−Δf) peak bin [15:8] (NT=2 only; 0 in NT=1) |
| `0xC3` | `FFT_PEAK_BIN_B_LO` | R | `0x00` | FFT Engine | Node 2 peak bin [7:0] |
| `0xC4` | `FFT_PEAK_MAG_A_HI` | R | `0x00` | FFT Engine | Node 1 peak magnitude² [15:8] |
| `0xC5` | `FFT_PEAK_MAG_A_LO` | R | `0x00` | FFT Engine | Node 1 peak magnitude² [7:0] |
| `0xC6` | `FFT_PEAK_MAG_B_HI` | R | `0x00` | FFT Engine | Node 2 peak magnitude² [15:8] (NT=2 only) |
| `0xC7` | `FFT_PEAK_MAG_B_LO` | R | `0x00` | FFT Engine | Node 2 peak magnitude² [7:0] |
| `0xC8` | `FFT_NOISE_HI` | R | `0x00` | FFT Engine | Average off-peak noise magnitude [15:8] |
| `0xC9` | `FFT_NOISE_LO` | R | `0x00` | FFT Engine | Average off-peak noise magnitude [7:0] |
| **SX1257 Pass-Through** (`0xCA`–`0xCD`) | | | | | |
| `0xCA` | `SX_TARGET` | R/W | `0x00` | SPI Master | [1:0] device address: 0=SX1257_1, 1=SX1257_2, 2=SX1257_3, 3=SX1257_4; drives CS_A[1:0] → board-level 74HC139 decoder; [7:2] reserved |
| `0xCB` | `SX_ADDR` | R/W | `0x00` | SPI Master | [6:0] target SX1257 register address |
| `0xCC` | `SX_DATA` | R/W | `0x00` | SPI Master | Write data [7:0]; overwritten with read data after a read transaction completes |
| `0xCD` | `SX_CTRL` | R/W | `0x00` | SPI Master | [0] RNW: 1=read, 0=write; [1] START: write 1 to trigger transaction, self-clears when BUSY deasserts; [2] BUSY: read-only, 1 while SPI transaction in progress |
| **SC Bring-Up Debug** (`0xD0`–`0xD9`) | | | | | |
| `0xD0` | `SC_DBG_FLAGS` | R | `0x00` | Schmidl-Cox | [0] `SC_HIT`; [2:1] current hit counter; [3] `SC_LOCK`; [7:4] reserved |
| `0xD1` | `SC_DBG_RSVD` | R | `0x00` | Schmidl-Cox | Reserved for future SC bring-up status |
| `0xD2` | `SC_FIRST_HIT_3` | R | `0x00` | Schmidl-Cox | First-hit sample-count snapshot [31:24] |
| `0xD3` | `SC_FIRST_HIT_2` | R | `0x00` | Schmidl-Cox | First-hit sample-count snapshot [23:16] |
| `0xD4` | `SC_FIRST_HIT_1` | R | `0x00` | Schmidl-Cox | First-hit sample-count snapshot [15:8] |
| `0xD5` | `SC_FIRST_HIT_0` | R | `0x00` | Schmidl-Cox | First-hit sample-count snapshot [7:0] |
| `0xD6` | `SC_LOCK_SNAP_3` | R | `0x00` | Schmidl-Cox | Lock sample-count snapshot [31:24] |
| `0xD7` | `SC_LOCK_SNAP_2` | R | `0x00` | Schmidl-Cox | Lock sample-count snapshot [23:16] |
| `0xD8` | `SC_LOCK_SNAP_1` | R | `0x00` | Schmidl-Cox | Lock sample-count snapshot [15:8] |
| `0xD9` | `SC_LOCK_SNAP_0` | R | `0x00` | Schmidl-Cox | Lock sample-count snapshot [7:0] |
| `0xCE`–`0xCF`, `0xDA`–`0xFF` | — | — | — | — | Reserved |

---

## Register details

### `0x00` — CHIP_ID (read-only)

Fixed identification value. First register read during bring-up to confirm SPI comms are working.

| Bits | Field | Description |
| --- | --- | --- |
| [7:0] | `ID` | Always `0xA7` |

---

### `0x01` — CHIP_REV (read-only)

Silicon revision. Increment for each tapeout.

| Bits | Field | Description |
| --- | --- | --- |
| [7:0] | `REV` | `0x01` for first tapeout |

---

### `0x02` — CPU_RESET (read/write)

PicoRV32 reset control. Used during SPI firmware load sequence.

| Bits | Field | Description |
| --- | --- | --- |
| [0] | `CPU_RESET` | 1 = PicoRV32 held in reset (default after power-on); write 0 to release after firmware is loaded |
| [7:1] | — | Reserved, write 0 |

Boot sequence:
```
RPi: assert cpu_reset=1 (write 0x01 to 0x02)
RPi: write firmware.bin to IMEM base address (0x0000) over SPI
RPi: de-assert cpu_reset=0 (write 0x00 to 0x02)
PicoRV32: fetch from 0x00000, begin execution
```

---

### `0x03` — DEBUG_CTRL (read/write)

Controls JTAG debug mode for the four dual-function pads (`TCK_IRQ`, `TMS_GPIO0`, `TDI_GPIO1`, `TDO_GPIO2`).

| Bits | Field | Description |
| --- | --- | --- |
| [0] | `JTAG_EN` | 0 = normal mode (pads serve as IRQ output + GPIO_0–2); 1 = JTAG debug mode (pads serve as TCK/TMS/TDI/TDO) |
| [7:1] | — | Reserved, write 0 |

**Mode switch procedure:** RPi reconfigures its `TCK_IRQ` GPIO as input before writing `JTAG_EN=1` to avoid bus contention. On debug exit, RPi writes `JTAG_EN=0` and reconfigures its GPIO as rising-edge interrupt input. While `JTAG_EN=1`, `GPIO_DIR`, `GPIO_OUT`, and `GPIO_IN` are ignored; RPi must poll `IRQ_STATUS` via SPI to detect interrupt sources rather than relying on the pad.

---

### `0x04` — GPIO_DIR (read/write)

Direction register for GPIO_0–2 (pads `TMS_GPIO0`, `TDI_GPIO1`, `TDO_GPIO2` in normal mode). Has no effect when `JTAG_EN=1`.

| Bits | Field | Description |
| --- | --- | --- |
| [0] | `GPIO0_DIR` | Direction for GPIO_0 / `TMS_GPIO0`: 0 = input (pad sampled to `GPIO_IN[0]`), 1 = output (pad driven from `GPIO_OUT[0]`) |
| [1] | `GPIO1_DIR` | Direction for GPIO_1 / `TDI_GPIO1` |
| [2] | `GPIO2_DIR` | Direction for GPIO_2 / `TDO_GPIO2` |
| [7:3] | — | Reserved, write 0 |

---

### `0x05` — GPIO_OUT (read/write)

Output drive value for GPIO_0–2. A pad is only driven when its `GPIO_DIR` bit is 1 and `JTAG_EN=0`.

| Bits | Field | Description |
| --- | --- | --- |
| [0] | `GPIO0_OUT` | Drive value for GPIO_0 / `TMS_GPIO0` |
| [1] | `GPIO1_OUT` | Drive value for GPIO_1 / `TDI_GPIO1` |
| [2] | `GPIO2_OUT` | Drive value for GPIO_2 / `TDO_GPIO2` |
| [7:3] | — | Reserved, write 0 |

---

### `0x06` — GPIO_IN (read-only)

Sampled input value of GPIO_0–2 pads. Sampled synchronously into the 32 MHz domain via a 2-FF synchroniser per pad. Valid when `JTAG_EN=0` and the corresponding `GPIO_DIR` bit is 0. Reading this register while `GPIO_DIR[n]=1` returns the current driven value; reading it while `JTAG_EN=1` returns undefined (do not rely on).

| Bits | Field | Description |
| --- | --- | --- |
| [0] | `GPIO0_IN` | Sampled pad value of GPIO_0 / `TMS_GPIO0` |
| [1] | `GPIO1_IN` | Sampled pad value of GPIO_1 / `TDI_GPIO1` |
| [2] | `GPIO2_IN` | Sampled pad value of GPIO_2 / `TDO_GPIO2` |
| [7:3] | — | Returns 0 |

---

### `0x10` — MIMO_CTRL (read/write)

| Bits | Field | Description |
| --- | --- | --- |
| [0] | `MODE` | 0 = MRC NR=4 (default); 1 = passthrough (bypass, single-antenna) |
| [1] | — | Reserved, write 0 |
| [3:2] | — | Reserved, write 0 |
| [7:4] | `ANTENNA_EN` | Bit per antenna (bit 4=ant0, bit 5=ant1, bit 6=ant2, bit 7=ant3); default `0xF0` (all enabled) |

**MODE=1 (passthrough):** Stages 4–8 (frontend buffer, SC detector, training accumulator, weight generation, MRC combiner) are bypassed entirely. The lowest-numbered antenna with its `ANTENNA_EN` bit set is selected; its int8 decimated I+Q samples are sign-extended to int16 and routed directly to REMOD_A. PicoRV32 firmware is not involved and the W vector registers are ignored. Use this mode to obtain a single-antenna baseline for SNR/BER comparison against MRC combining gain.

In MODE=0, before current-packet W has been committed, the live combiner falls back to this bypass antenna. PicoRV32 (or hardware weight gen) writes W into shadow registers and commits it atomically; the combiner only reads the active W bank.

Writes to `MODE` and `ANTENNA_EN` update shadow configuration while a packet is active. Hardware latches `ACTIVE_MODE` and `ACTIVE_ANTENNA_EN` only when the receiver is idle between packets. This prevents antenna/mode glitches in the live remodulated stream.

---

### `0x11` — SF_CFG (read/write)

Spreading factor selection for the FFT engine.

| Bits | Field | Description |
| --- | --- | --- |
| [2:0] | `sf` | 0 = SF5 (M=32) … 7 = SF12 (M=4096) |
| [7:3] | — | Reserved, write 0 |

The FFT engine uses `M = 2^(sf+5)` points. Changing `sf` takes effect from the next triggered FFT.

---

### `0x12` — CAPTURE_CTRL (read/write)

Controls the Baseband SRAM sample capture trigger and guarded handoff.

| Bits | Field | Description |
| --- | --- | --- |
| [0] | `CAPTURE_EN` | Write 1 to arm capture; hardware clears to 0 when the guarded capture window is frozen |
| [1] | `CAPTURE_MODE` | 0 = raw time-domain samples from all 4 decimators; 1 = FFT output Z_j[k] all bins all antennas |
| [7:2] | — | Reserved, write 0 |

Capture uses the Baseband SRAM sample-capture region from `0x40000` to `0x87FFF` (288 KB). In mode 0 this holds exactly 9 full SF12 symbols across all 4 antennas at 2 bytes/sample:

```
9 * 4096 * 4 * 2 bytes = 288 KB
```

Normal preamble acquisition freezes a guarded capture window:

```
capture_start = timing_ref - M/2
capture_len   = 9M samples per antenna
fft_start     = timing_ref
```

The FFT engine consumes the 8-symbol RCTSL window starting at `timing_ref` as soon as that live window is resident. The extra 0.5M pre/post guard absorbs Schmidl-Cox timing uncertainty and supports timing diagnostics, but it must not block the live FFT trigger. Read back via SPI burst from `0x40000`.

---

### `0x13` — CAPTURE_STATUS (read-only)

| Bits | Field | Description |
| --- | --- | --- |
| [0] | `CAPTURE_DONE` | 1 = guarded capture window frozen and ready to read; same handoff condition as internal `capture_window_ready` |
| [1] | `CAPTURE_OVERFLOW` | 1 = capture buffer wrapped before host read; data may be stale |
| [7:2] | — | Reserved |

---

### `0x20` / `0x21` — reserved

These addresses were `DELTA_F_HI` / `DELTA_F_LO` — the NT=2 node frequency offset. NT=2 has been removed. Addresses `0x20`–`0x21` are reserved; do not write.

---

### `0x30`–`0x35` — RX_GAIN / TX_GAIN (read/write)

SX1257 gain register values, mirrored from PicoRV32 AGC loop writes. Host may pre-set these before releasing `CPU_RESET` to override AGC initial gain; PicoRV32 will update them at the first AGC cycle.

`RX_GAIN_n` mirrors the full SX1257_n `RegRxAnaGain` byte (addr 0x0C): bits [7:5] = `RxLnaGain` (1=G1 max, 6=G6 min, 6 dB/step), bits [4:1] = `RxBbGain` (0–15, 2 dB/step), bit [0] = `LnaZin` (keep 0 for 50 Ω). Reset value `0x3E` = G1 + BB_MAX (maximum gain on power-up for best weak-signal sensitivity; saturated first packets are handled by discarding H rather than reducing starting gain). `TX_GAIN_n` maps to SX1257_n `RegTxGain`.

---

### `0x50`–`0x57` — ENERGY[0..3] (read-only)

Per-antenna energy estimates `Σ|x|²` computed over the last 8 symbols by the energy measurement. Snapshot latched at correlator lock. int16, unsigned, arbitrary units (proportional to received power before gain control).

Use for relative power comparison across antennas (e.g. to disable a faulty antenna via `ANTENNA_EN`).

---

### `0x58`–`0x5F` — CORR_MAG[0..3] (read-only) / `0x60`–`0x6F` — reserved

`0x58`–`0x5F`: Per-branch SC autocorrelation magnitude |c_j|² for antennas 0–3. Latched at `sc_lock`. int16, unsigned. Used for per-antenna link quality assessment and AGC diagnostics.

`0x60`–`0x6F`: Reserved (were NT=2 node 2 correlator magnitudes; NT=2 removed).

---

### `0xD0`–`0xD9` — SC Bring-Up Debug (read-only)

Optional Schmidl-Cox debug visibility intended primarily for FPGA / bench bring-up before the full firmware control loop is complete.

- `SC_DBG_FLAGS` (`0xD0`)
  - bit `[0]`: current raw threshold-compare result `SC_HIT`
  - bits `[2:1]`: current consecutive-hit counter state
  - bit `[3]`: current `SC_LOCK` state
- `SC_FIRST_HIT_[3:0]` (`0xD2`–`0xD5`)
  - 32-bit free-running `iq_valid` sample-count snapshot taken at the first qualifying hit of the eventual lock sequence
- `SC_LOCK_SNAP_[3:0]` (`0xD6`–`0xD9`)
  - 32-bit free-running `iq_valid` sample-count snapshot taken when `sc_lock` asserts

These registers are debug aids, not part of the packet-processing control path. On FPGA prototypes they may be mirrored directly from ILA-observable debug nets. On ASIC they may be omitted if area pressure is severe, but they are strongly recommended for first-silicon bring-up.

---

### `0x49` — IRQ_STATUS (read-only)

Sticky interrupt source bits. The external/internal IRQ line is asserted while any enabled source is set.

| Bit | Field | Meaning |
| --- | --- | --- |
| [0] | `CORR_LOCK` | Schmidl-Cox detected preamble; Packet Control FSM entered `PREAMBLE_DETECTED` |
| [1] | `H_READY` | FFT Engine has written H/N₀/eps_sub; PicoRV32 should compute W |
| [2] | `W_MISSED_PACKET` | W was not committed before safe switch; current packet remains bypass |
| [3] | `CAPTURE_DONE` | Diagnostic capture window complete |
| [4] | `CAPTURE_OVERFLOW` | Capture window was overwritten or second packet arrived while protected |
| [5] | `TX_PREP` | Host requested TX preparation |
| [6] | `TX_DONE` | Host indicated TX complete |
| [7] | — | Reserved |

### `0x4A` — IRQ_CLEAR (write-only)

Write 1s to clear corresponding `IRQ_STATUS` bits. Writing 0 leaves a bit unchanged.

### `0x4B` — PACKET_STATUS (read-only)

Packet Control FSM status.

| Bits | Field | Description |
| --- | --- | --- |
| [0] | `PACKET_ACTIVE` | Packet FSM is not idle |
| [3:1] | `PACKET_PHASE` | 0=IDLE, 1=PREAMBLE_DETECTED, 2=FFT_WAIT, 3=W_COMMIT_WINDOW, 4=PAYLOAD_ACTIVE, 5=PACKET_DONE |
| [4] | `LIVE_FFT_READY` | 8-symbol live RCTSL window is resident |
| [5] | `W_PENDING` | `H_READY` has occurred and W commit is pending |
| [6] | `W_VALID` | `W_ACTIVE` is valid for the current packet |
| [7] | `W_MISSED_PACKET` | W missed the current packet safe-switch point |

### `0x4C` — W_CTRL (read/write)

Firmware writes W coefficients into the `0x90`–`0xAF` shadow register bank, then writes `W_CTRL[0]=1` to request an atomic commit.

| Bits | Field | Description |
| --- | --- | --- |
| [0] | `W_COMMIT` | Write 1 pulse after all W shadow registers are written; hardware commits when the receiver next becomes idle |
| [1] | `W_VALID` | Read-only mirror of active W valid state |
| [2] | `W_PENDING` | Read-only; W commit requested but not yet activated |
| [3] | `W_MISSED_PACKET` | Read-only; W arrived too late for current packet |
| [7:4] | — | Reserved |

### `0x4D` — ACTIVE_ANTENNA_EN (read-only)

Latched antenna-enable mask used by the live packet. Host writes to `MIMO_CTRL.ANTENNA_EN` update shadow configuration during an active packet; this register shows the no-glitch active copy.

---

### `0x70`–`0x8F` — H matrix (read-only)

Channel matrix H estimated by the FFT Engine after live RCTSL/peak/channel-estimation passes. `IRQ_STATUS.H_READY` indicates these registers and N₀/eps_sub are valid for firmware.

Layout: H[NR_index, NT_index]. For NT=1 only columns 0 (H[0..3, 0]) are valid; for NT=2 both columns populated.

---

### `0x90`–`0x9F` — W vector (read/write) / `0xA0`–`0xAF` — reserved

`0x90`–`0x9F`: MRC weight vector w (4 complex coefficients, int16 Q1.15). Written by the hardware weight generation FSM (auto path) or by PicoRV32 firmware (software path). These are the `W_SHADOW` bank; the live combiner reads only `W_ACTIVE`.

`W_ACTIVE` updates atomically after `W_CTRL.W_COMMIT` is pulsed and the Packet Control FSM reaches an idle boundary. Host reads of `0x90`–`0x9F` return the shadow bank for diagnostics/manual override.

For MRC: `w = conj(Z_j) / ||Z||` (computed by weight gen hardware or firmware).

`0xA0`–`0xAF` are reserved (were W row 2 for NT=2; NT=2 removed).

---

### `0xB0`–`0xB7` — N₀[0..3] (read-only)

Per-antenna noise variance estimates, computed by the FFT Engine from off-peak bins after preamble acquisition. int16, unsigned. Used by PicoRV32 firmware to set the regularisation term σ² in the ALMMSE weight computation.

### `0xB8`–`0xB9` — EPS_SUB (read-only)

Fractional CFO estimate from the Stage 4 RCTSL pass. Signed Q1.15 in FFT-bin units, valid when `IRQ_STATUS.H_READY=1`. Firmware may read it for diagnostics, drift tracking, or payload correction policy.

---

### `0xCA`–`0xCD` — SX1257 pass-through

Allows the RPi to issue arbitrary SX1257 register read/write transactions via the ASIC SPI master. Intended for initial SX1257 configuration (PLL frequency, filter bandwidth, PA config) and diagnostics. The SX1302 HAL's built-in SX1257 init path cannot reach the SX1257s in this design (they are on the ASIC SPI bus, not SX1302's), so the host must configure them through this interface before releasing `CPU_RESET`.

**Write sequence:**
```
1. Write SX_TARGET  ← device address (0–3 for SX1257_1–4)
2. Write SX_ADDR    ← SX1257 register address (e.g. 0x03 = RegFrfMsb)
3. Write SX_DATA    ← value to write
4. Write SX_CTRL    ← 0x02  (RNW=0, START=1)
5. Poll  SX_CTRL    until BUSY (bit 2) = 0
```

**Read sequence:**
```
1. Write SX_TARGET  ← device address (0–3)
2. Write SX_ADDR    ← SX1257 register address
3. Write SX_CTRL    ← 0x03  (RNW=1, START=1)
4. Poll  SX_CTRL    until BUSY (bit 2) = 0
5. Read  SX_DATA    ← register contents
```

**Arbitration.** PicoRV32 firmware must poll `SX_CTRL[2]` (BUSY) before issuing any SPI master transaction. The host should only issue pass-through commands during a known idle window — either before `CPU_RESET` is released, or after asserting `CPU_RESET=1` again to freeze firmware.

**No broadcast.** Only one device is selectable per transaction. For registers that apply uniformly to all SX1257s (e.g. frequency, filter bandwidth), the host or firmware must issue four sequential transactions with `SX_TARGET` = 0, 1, 2, 3.

**Typical init registers** written via pass-through at startup:

| SX1257 reg | Addr | Purpose |
| --- | --- | --- |
| `RegFrfMsb` | `0x03` | PLL frequency MSB (868 MHz → `0xD9`) |
| `RegFrfMid` | `0x04` | PLL frequency mid (`0x06`) |
| `RegFrfLsb` | `0x05` | PLL frequency LSB (`0x66`) |
| `RegBwSsb` | `0x10` | RX filter bandwidth |
| `RegClkSelect` | `0x0E` | CLKOUT control; typically disabled (0x00) on all chips to reduce EMI as ASIC is driven by central buffer |
| `RegTxDac` | `0x16` | TX DAC gain (SX1257_1/2 only) |

Issue four sequential writes (`SX_TARGET` = 0, 1, 2, 3) for registers that apply uniformly (frequency, filter). Per-chip settings (TX DAC, CLK enable) require individual targeting anyway.

---

## Address range reservations

| Range | Block |
| --- | --- |
| `0x00`–`0x02` | Chip identity and global control |
| `0x03`–`0x06` | JTAG/GPIO control (`DEBUG_CTRL`, `GPIO_DIR`, `GPIO_OUT`, `GPIO_IN`) |
| `0x07`–`0x0F` | Reserved |
| `0x10`–`0x1F` | Mode & configuration (incl. capture write pointer at `0x14`–`0x16`) |
| `0x20`–`0x2F` | Frequency configuration |
| `0x30`–`0x3F` | Gain control |
| `0x40`–`0x4F` | Status summary |
| `0x50`–`0x57` | Energy detector |
| `0x58`–`0x67` | Correlator magnitudes — node 1 (+Δf) |
| `0x68`–`0x6F` | Correlator magnitudes — node 2 (−Δf) |
| `0x70`–`0x8F` | H matrix |
| `0x90`–`0xAF` | W matrix |
| `0xB0`–`0xB7` | Noise variance N₀ |
| `0xB8`–`0xB9` | Fractional CFO `eps_sub` |
| `0xBA`–`0xBF` | Reserved |
| `0xC0`–`0xC9` | FFT diagnostics |
| `0xCA`–`0xCD` | SX1257 pass-through |
| `0xCE`–`0xFF` | Reserved |
