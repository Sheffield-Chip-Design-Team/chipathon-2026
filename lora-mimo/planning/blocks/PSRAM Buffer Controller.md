# PSRAM Buffer Controller

Optional same-packet MRC extension. See [Packet Control FSM](Packet%20Control%20FSM.md) and [ALMMSE-MRC Combiner](ALMMSE-MRC%20Combiner.md) for context.

**Owner:** TBD  
**Status:** Not started  
**Target device:** APS6404L-3SQR (AP Memory, 64 Mbit QPI PSRAM)

---

## Function

Enables same-packet MRC by buffering the received IQ stream into external PSRAM from `sc_lock` and replaying the complete packet (preamble + payload) through the MRC combiner once weights are committed. The SX1302 output is held at zero during BUFFERING and receives the MRC-combined replay starting from `W_commit`.

`iq_in` is a direct fanout from the ΣΔ decimator — the same full-precision stream that feeds the Training Accumulator. The PSRAM path does not read from or write to the on-chip Frontend Buffer SRAM; those two paths are independent and share only the wire from the decimator output.

When `PSRAM_EN=0` (default) the block tristates all QPI data pins and the system operates identically to the standard next-packet path with no PSRAM device fitted.

---

## Additional functionality enabled by PSRAM

Because the PSRAM path stores packet samples rather than acting only as a live delay line, it enables several capabilities beyond the minimum same-packet replay requirement.

### 1. Extended training windows

In baseline live mode, the training accumulator stops at preamble end so weights are ready before live payload. With PSRAM replay, the estimator window may extend later into the packet:

```
Z_j = Σ_n rx_j[n] · conj(rx_r[n])
```

Since the branch-reference estimator depends on the common packet samples rather than a local chirp template, a larger accumulation window can improve estimate SNR when the channel is approximately constant over the packet.

### 2. Longer noise-estimation windows

PSRAM allows firmware or hardware to estimate noise over a longer quiet interval than the small on-chip acquisition window:

```
N0_hat_j = (1/N) · Σ_{n in W_noise} |rx_j[n]|²
```

This lowers noise-estimation variance and improves:

- ALMMSE / noise-aware weighting
- branch-health metrics
- signal-to-noise confidence measurements

The selected noise window must be classified as signal-free; otherwise the estimate is biased by packet energy or interference.

### 3. Multi-window channel-stability checks

A stored packet can be split into multiple estimation regions:

```
Z_j^(1) = Σ_{n in W1} rx_j[n] · conj(rx_r[n])
Z_j^(2) = Σ_{n in W2} rx_j[n] · conj(rx_r[n])
```

Comparing `Z_j^(1)` and `Z_j^(2)` provides a direct packet-level test of the constant-channel assumption. If the channel estimate drifts across windows, firmware can prefer an earlier window or reduce trust in the combined result.

### 4. Offline combiner comparison on identical packets

The same buffered packet can be replayed through several combining laws:

```
y_SC[n], y_EGC[n], y_MRC[n], y_ALMMSE[n]
```

This is valuable for algorithm validation because every method is tested on the exact same received data, not on different over-the-air packets.

### 5. Adaptive reference-branch selection

The current training path uses a nominated reference branch. With full packet storage, firmware can evaluate branch quality first and then choose the reference branch that maximises training robustness, for example:

```
r* = argmax_j Σ_{n in W} |rx_j[n]|²
```

This reduces the probability that all `Z_j` estimates are degraded by a weak or faded reference antenna.

### 6. Post-lock packet validation and timing analysis

Although the active path uses `sc_lock` plus back-calculated `timing_ref`, PSRAM allows the packet to be inspected after capture without extending the critical path. This supports:

- checking whether `sc_lock` was a true preamble hit
- measuring `timing_ref` error against offline analysis
- studying late-lock cases and acquisition margin
- testing whether longer training windows materially improve the estimate

### 7. Packet-failure capture for bring-up and debug

PSRAM can preserve raw multi-branch packet captures associated with:

- false locks
- missed locks
- saturated first packets
- weak-reference events
- weight-commit misses
- bad SX1302 decodes after combining

This is one of the highest-value bring-up features because problematic packets can be replayed or analysed offline rather than reproduced over the air.

### 8. Calibration and mismatch characterisation

Stored raw packet data provides a direct way to inspect per-branch gain, phase, clipping, and timing behaviour across real packets. This is useful for:

- branch-to-branch phase/gain mismatch analysis
- validating calibration coefficients
- detecting front-end asymmetry or clipping
- comparing on-chip estimates with Python / GNU Radio post-processing

### 9. Multi-packet or long-term statistics

The PSRAM device is much larger than the minimum depth required for a single SF6/SF7 delay line, so the architecture can support capture of multiple packet segments or extended quiet regions when useful. This enables:

- long-window idle-noise collection
- packet-to-packet estimate smoothing
- debug snapshots before and after anomalous events

### Practical prioritisation

Recommended first-use priorities:

1. same-packet replay
2. raw packet capture for debug
3. longer noise-estimation windows
4. extended training windows in PSRAM mode
5. multi-window channel-stability checks

Nice-to-have follow-ons:

1. adaptive reference-branch selection
2. offline combiner comparison
3. longer-term multi-packet statistics

The key architectural point is:

```
PSRAM != only a delay line
```

It creates a packet-memory subsystem that decouples capture time from estimation time and replay time.

---

## Operating principle

During BUFFERING the SX1302-facing output is silenced (zeros). At `W_commit` the controller switches to REPLAY: it reads from `buf_base` (the sc_lock position), MRC-combines the data, and writes to SX1302. Both `rd_ptr` and `wr_ptr` advance on every `iq_valid` at 125 kS/s. Because both advance at the same rate, the gap between them remains fixed at 8M+50 samples for the rest of the packet. PSRAM acts as a fixed delay line: SX1302 receives the MRC-combined signal continuously delayed by 8M+50 samples relative to real time. REPLAY runs until `packet_end`, at which point the controller returns to IDLE.

SX1302 receives: `[silence for 8M+50 samples after sc_lock] → [MRC-combined signal starting from sc_lock, delayed by 8M+50 samples, until packet_end]`

Since the replayed preamble is the real preamble signal (just delayed), SX1302's correlator locks onto it normally and demodulates the complete packet. The introduced delay equals the time from `sc_lock` to `W_commit` — approximately `8M + 50 samples at 125 kS/s`.

**This eliminates the fallback timing problem.** Because SX1302 receives nothing during BUFFERING, `W_commit` can arrive at any point before `packet_end` and same-packet MRC is still applied to the entire payload from `buf_base`. There is no payload window to miss.

If `W_commit` never fires (hardware or firmware failure): replay in bypass mode (single antenna, no MRC gain). SX1302 still receives the complete packet.

---

## Introduced latency

| SF | Latency at BW=125 kHz |
|---|---|
| SF7 | ~8.2 ms |
| SF9 | ~32.8 ms |
| SF12 | ~262 ms |

Acceptable for LoRa IoT applications.

---

## Buffer depth

Writing begins at `sc_lock` and continues until `rd_ptr` catches up to `wr_ptr` during REPLAY. The maximum occupied depth equals the number of samples written during BUFFERING — approximately `8M + 50` samples (preamble + weight computation window).

At `f_s = BW`, 16-bit I/Q mode (8 bytes/sample across 4 branches):

`B_max ≈ 8 × 2^SF × 8 bytes = 64 × 2^SF bytes`

| SF | Max buffer occupied |
|---|---|
| SF7 | 8 kB |
| SF9 | 32 kB |
| SF12 | 256 kB |

The APS6404L provides 8 MB — more than sufficient for all SF at any supported bandwidth.

---

## Pin mux — JTAG / QPI

The four QPI data lines share pads with the JTAG debug pins:

| JTAG pin | QPI signal | APS6404L pin |
|---|---|---|
| TCK | SIO[0] (SI) | Pin 5 |
| TMS | SIO[1] (SO) | Pin 2 |
| TDI | SIO[2] | Pin 3 |
| TDO | SIO[3] | Pin 7 |

SCK (CLK) is driven from the 32 MHz clock buffer that feeds the ASIC. CE# is routed through the existing chip-select mux alongside other SPI peripherals.

When `PSRAM_EN=0` or `JTAG_OVERRIDE=1`: SIO[0–3] are tristated and JTAG operates normally. JTAG and PSRAM are mutually exclusive.

---

## Sample width

Selected by `PSRAM_CTRL[1]` (`SAMPLE_WIDTH`):

| Mode | Per-sample storage | Bandwidth at 1 MS/s | Max f_s |
|---|---|---|---|
| 0 — 16-bit I/Q (default) | 4ch × 16b = 8 bytes | 64 Mb/s | 1 MS/s |
| 1 — 32-bit I/Q | 4ch × 32b = 16 bytes | 128 Mb/s | 500 kS/s |

In 16-bit mode the decimator output is right-shifted by `(W_IN − 8)` before serialisation. With ~6 dB AGC headroom, effective SQNR ≈ 44 dB — well above the LoRa noise floor at any SF.

---

## APS6404L protocol

### Power-up initialisation

The device powers on in SPI mode. Firmware pulses `init_start` after tPU (≥150 µs). The controller then issues:

1. **RSTEN** `0x66` — SPI, serial on SIO[0], 8 clock cycles
2. **RST** `0x99` — SPI, serial on SIO[0], 8 clock cycles; wait tRST ≥ 50 ns
3. **Enter QPI** `0x35` — SPI, serial on SIO[0], 8 clock cycles
4. Assert `qe_init_done` — all subsequent accesses use QPI

### QPI write (BUFFERING and REPLAY)

Command `0x02` or `0x38`, zero wait cycles:

```
[CE# low]
  2 clocks : command byte as two nibbles on SIO[3:0]
  6 clocks : 24-bit address as six nibbles on SIO[3:0]
  2N clocks: N data bytes as nibbles on SIO[3:0]
[CE# high]
```

8-byte write (16-bit I/Q mode): 2 + 6 + 16 = **24 cycles = 750 ns** at 32 MHz.

### QPI fast quad read (REPLAY)

Command `0xEB`, 6 wait cycles, max 133 MHz:

```
[CE# low]
  2 clocks : command byte on SIO[3:0]
  6 clocks : 24-bit address on SIO[3:0]
  6 clocks : wait/dummy cycles
  2N clocks: N data bytes driven by PSRAM on SIO[3:0]
[CE# high]
```

8-byte read: 2 + 6 + 6 + 16 = **30 cycles = 938 ns** at 32 MHz.

### Timing compliance at 32 MHz (APS6404L standard grade)

| Parameter | Requirement | At 32 MHz |
|---|---|---|
| tCEM — max CE# low | ≤ 8 µs | 938 ns max ✓ |
| tCPH — CE# high between bursts | ≥ 18 ns | 31.25 ns (1 cycle) ✓ |
| tCLK — min clock period | ≥ 7.5 ns | 31.25 ns ✓ |
| tACLK — CLK to output delay | ≤ 5.5 ns | Sample on falling edge ✓ |

At 32 MHz the device is running at 24% of its rated speed. No special signal integrity precautions beyond the datasheet-recommended 1 µF + 100 nF decoupling on VDD.

### Refresh

Self-managed internally. CE# is deasserted between every transaction giving continuous refresh windows. At 125 kS/s the bus is idle ~87% of each sample period; no host action required.

---

## State machine

```
RESET → UNINIT → QE_INIT → IDLE
                              │  sc_lock ∧ PSRAM_EN=1
                              ▼
                         BUFFERING ──── W_commit ──► REPLAY ──── packet_end ──► IDLE
                              │                         │
                              └── packet_end ───────────┘
                                  (no W_commit:
                                   bypass replay → IDLE)
```

| State | Entry | Active behaviour | Exit |
|---|---|---|---|
| `UNINIT` | Reset | CE# high; hold until `init_start` | `init_start` |
| `QE_INIT` | `init_start` | Issue RSTEN + RST + Enter QPI sequence; assert `qe_init_done` | sequence done |
| `IDLE` | Init done; `PSRAM_EN=0`; packet end | Tristate SIO[0–3]; SCK gated; reset wr_ptr=rd_ptr=buf_base | `sc_lock` ∧ `PSRAM_EN=1` |
| `BUFFERING` | `sc_lock` | Output zeros to SX1302; QPI write each live sample; wr_ptr++ on `iq_valid` | `W_commit` or `packet_end` |
| `REPLAY` | `W_commit` | Interleave QPI write (wr_ptr++) and QPI read (rd_ptr++) from buf_base; both advance at live rate maintaining constant 8M+50 gap; feed MRC-combined read data to SX1302 | `packet_end` |

Note: `rd_ptr` and `wr_ptr` both advance at 125 kS/s in REPLAY. The gap is fixed at 8M+50 samples — PSRAM is a delay line, not a catch-up buffer. REPLAY continues until `packet_end`.

If `packet_end` fires during BUFFERING (W_commit never arrived): initiate bypass replay from `buf_base` — SX1302 receives the packet without MRC gain. `W_MISSED_PACKET` is set by the Packet Control FSM.

### Read pointer

At REPLAY entry: `rd_ptr = buf_base`. The controller reads from the sc_lock position, so SX1302 receives the full preamble (from sc_lock onwards) in the replayed stream and can lock normally. No `payload_start_estimate` needed.

---

## SX1302 output mux

```
SX1302 input ◄────┬─ IDLE or PSRAM_EN=0 ───────► live IQ from decimator (bypass)
                  ├─ BUFFERING ────────────────► zeros
                  └─ REPLAY ──────────────────► MRC-combined PSRAM read data
```

`sx1302_out_sel` (2-bit) drives the upstream mux. The combiner receives live IQ continuously in all states; the mux selects whether its output, PSRAM replay, or zeros reach SX1302. There is no direct-bypass MRC state during a packet — in REPLAY the MRC-combined data always comes from PSRAM, which is permanently one 8M+50-sample window behind.

---

## Interface

| Port | Dir | Width | Description |
|---|---|---|---|
| `clk_32m` | in | 1 | 32 MHz system clock |
| `rst_n` | in | 1 | Active-low reset |
| `psram_en` | in | 1 | Enable PSRAM path; from `PSRAM_CTRL[0]` (`0xB0`) |
| `sample_width` | in | 1 | 0=16-bit I/Q, 1=32-bit I/Q; from `PSRAM_CTRL[2]` (`0xB0`) |
| `jtag_en` | in | 1 | JTAG active; from `DEBUG_CTRL[0]` (`0x03`); forces SIO[0–3] tristate and sets `PAD_CONFLICT` when asserted with `psram_en` |
| `init_start` | in | 1 | Firmware strobe: begin QE init after tPU |
| `iq_in[3:0]` | in | 4×32 | Live IQ samples from decimator |
| `iq_valid` | in | 1 | Sample strobe |
| `sc_lock` | in | 1 | Preamble detection event |
| `W_commit` | in | 1 | Weight generation complete strobe |
| `packet_end` | in | 1 | Packet end event from Packet Control FSM |
| `sck_en` | out | 1 | Enable 32 MHz CLK to PSRAM; gated low in IDLE |
| `ce_n` | out | 1 | PSRAM CE# active-low; via CS mux |
| `sio_out[3:0]` | out | 4 | Data driven to PSRAM during cmd/addr/write phases |
| `sio_in[3:0]` | in | 4 | Data received from PSRAM during read data phase |
| `sio_oe[3:0]` | out | 4 | Output-enable per pin; 0 during PSRAM read data and in IDLE |
| `iq_replay[3:0]` | out | 4×32 | MRC-combined PSRAM read data to SX1302 output mux |
| `sx1302_out_sel` | out | 2 | SX1302 input mux select: 0=live bypass (IDLE/PSRAM_EN=0), 1=zeros (BUFFERING), 2=PSRAM replay (REPLAY) |
| `qe_init_done` | out | 1 | QE init complete; PSRAM ready |
| `buf_active` | out | 1 | High in BUFFERING or REPLAY |

---

## Registers

**`PSRAM_CTRL`** `0xB0` (R/W, default `0x00`)

| Bit | Name | Default | Description |
|---|---|---|---|
| [0] | `PSRAM_EN` | 0 | 0 = next-packet mode; 1 = same-packet PSRAM replay |
| [1] | `PSRAM_CLR_ERR` | 0 | Write-1 pulse: clear OVERFLOW and REPLAY_MISSED sticky flags |
| [2] | `SAMPLE_WIDTH` | 0 | 0 = 16-bit I/Q (default); 1 = 32-bit I/Q. See Sample width table. |
| [7:3] | — | 0 | Reserved |

Note: JTAG/QPI pad conflict is handled implicitly. `DEBUG_CTRL` (`0x03`) bit `JTAG_EN` is ignored while `PSRAM_EN=1`; setting both simultaneously sets `PAD_CONFLICT` in `PSRAM_STATUS`. No separate `JTAG_OVERRIDE` register bit is needed.

**`PSRAM_STATUS`** `0xB1` (R, default `0x00`)

| Bit | Name | Description |
|---|---|---|
| [2:0] | `STATE` | Current FSM state: 0=IDLE, 1=QE_INIT, 2=BUFFERING, 3=REPLAY |
| [3] | `INIT_DONE` | QE init complete; safe to set `PSRAM_EN=1` |
| [4] | `REPLAY_ACTIVE` | In REPLAY; SX1302 receiving MRC-combined PSRAM stream |
| [5] | `REPLAY_MISSED` | Sticky: `packet_end` fired before `W_commit`; last packet used bypass (no MRC gain) |
| [6] | `OVERFLOW` | Sticky: wr_ptr wrapped — buffer exhausted |
| [7] | `PAD_CONFLICT` | Sticky: `JTAG_EN=1` and `PSRAM_EN=1` asserted simultaneously |

**`PSRAM_PKT_BYTES_HI`** `0xB2` / **`PSRAM_PKT_BYTES_LO`** `0xB3` (R, default `0x00`)

Bytes written to PSRAM for the current (or most recent) packet. Big-endian 16-bit value. Useful for verifying buffer depth against SF at bring-up.

**`PSRAM_RD_OFFSET`** `0xB4` (R, default `0x00`)

Replay start offset [7:0] — low 8 bits of `buf_base` relative to the PSRAM base address. Diagnostic readback.

---

## Power

| Condition | Current (APS6404L, 3.3 V, 25°C) |
|---|---|
| Active at 133 MHz (typ) | 5.5 mA |
| Active at 32 MHz (estimated) | ~1.3 mA |
| Standby (CE# high) | ~100 µA |
| Average at 125 kS/s | ~260 µA |

---

## Verification

| Test | Method | Pass criterion |
|---|---|---|
| PSRAM_EN=0 | Inject packet with PSRAM_EN=0 | SIO[0–3] tristated; combiner sees live stream; next-packet behaviour unchanged |
| QE init | Pulse `init_start`; monitor SIO[0] | RSTEN→RST→Enter QPI sequence correct; `qe_init_done` asserts |
| tCEM | Measure CE# low duration | ≤ 938 ns (< 4 µs extended, < 8 µs standard grade) |
| tCPH | Measure CE# high between transactions | ≥ 31.25 ns > 18 ns minimum |
| BUFFERING | `sc_lock` with PSRAM_EN=1 | `buf_active` asserts; zeros to SX1302; writes begin at buf_base; wr_ptr increments per `iq_valid` |
| REPLAY entry | `W_commit` during BUFFERING | rd_ptr=buf_base; interleaved write+read both advance at live rate; constant 8M+50 gap maintained; SX1302 receives MRC-combined replay from preamble |
| Delay-line model | Monitor wr_ptr − rd_ptr throughout REPLAY | Gap stays constant at 8M+50 ± 1 sample; never narrows to zero |
| SX1302 output | Monitor SX1302 input during BUFFERING | Zeros; no live samples forwarded |
| Packet end | `packet_end` during REPLAY | Returns to IDLE; SIO[0–3] tristated; `buf_active` deasserts |
| No W_commit | `packet_end` during BUFFERING | `REPLAY_MISSED` set; bypass replay from buf_base; SX1302 receives packet without MRC gain |
| PAD_CONFLICT | Set `JTAG_EN=1` and `PSRAM_EN=1` simultaneously | `PAD_CONFLICT` bit set in `PSRAM_STATUS`; SIO[0–3] tristated; PSRAM transactions suspended |
| SF12 buffer depth | SF12 packet with PSRAM_EN=1 | wr_ptr − rd_ptr ≈ 8M+50 samples ≈ 256 kB (16-bit mode); no wrap; no OVERFLOW flag |

---

## Related blocks

- [Packet Control FSM](Packet%20Control%20FSM.md) — `sc_lock`, `W_commit`, `packet_end`
- [ΣΔ Decimator](ΣΔ%20Decimator.md) — live `iq_in`
- [ALMMSE-MRC Combiner](ALMMSE-MRC%20Combiner.md) — applies weights to PSRAM replay stream during REPLAY
- [JTAG TAP](JTAG%20TAP.md) — shares SIO[0–3] pads; mutually exclusive with PSRAM_EN=1
- [Register Map](../Register%20Map.md) — `PSRAM_CTRL` (0x16), `PSRAM_STATUS` (0xBA), `PSRAM_PKT_BYTES` (0xBB–0xBC), `PSRAM_RD_OFFSET` (0xBD)
