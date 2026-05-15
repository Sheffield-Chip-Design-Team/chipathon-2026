# Packet Control FSM

RX path control block (non-FFT frontend). See [Non-FFT LoRa Frontend Proposal](../Non-FFT%20LoRa%20Frontend%20Proposal.md) and [DSP Flow](../DSP%20Flow.md) for context.

**Owner:** TBD
**Status:** Rewritten for non-FFT path

---

## Role

Owns packet phase and no-glitch switching between bypass and combined output. Converts SC timing events and weight-readiness signals into deterministic control for the frontend buffer, weight generation, and combiner.

Compared to the FFT-path FSM, this version is significantly simplified:

- No FFT trigger, no capture protection, no SRAM window management
- No `h_ready` input — replaced by `training_done` from the training accumulator
- Critical path is: `sc_lock → training_done → W_commit → safe_switch`

The FSM must never backpressure `iq_valid`. If weight computation misses the current packet, the live stream stays in bypass — this is expected next-packet behaviour, not an error.

---

## State Machine

```
        sc_lock
IDLE ──────────────► PREAMBLE_ACQ
 ▲                        │
 │                  training_done
 │                        │
 │                        ▼
 │                    W_PENDING ──── timeout / W_commit ──► PAYLOAD_ACTIVE
 │                                                                │
 └────────────────────────────── packet_end / timeout ───────────┘
```

| State | Entry condition | Active behaviour | Exit condition |
|---|---|---|---|
| `IDLE` | Reset; packet end; timeout | `safe_switch=1`; promote `W_SHADOW→W_ACTIVE` if `W_commit_pending`; unfreeze FRONTEND_BUF | `sc_lock` |
| `PREAMBLE_ACQ` | `sc_lock` | Latch `timing_ref`, `ACTIVE_MODE`, `ACTIVE_ANTENNA_EN`; freeze FRONTEND_BUF; combiner=bypass; raise `IRQ_CORR_LOCK` | `training_done` or preamble timeout |
| `W_PENDING` | `training_done` | Raise `IRQ_TRAINING_DONE`; weight gen computes and writes `W_SHADOW`; combiner stays bypass | `W_commit` or payload-start timeout |
| `PAYLOAD_ACTIVE` | Payload phase begins | Combiner = `W_ACTIVE` if `W_valid`, else bypass; set `W_MISSED_PACKET` if W was not committed before this state | `packet_end` or timeout |

### Packet end detection

The ASIC has no explicit framing signal from SX1302. Packet end is detected by either:

1. **New `sc_lock`** — a new preamble detected implies the previous packet is done
2. **Configurable timeout** — `timing_ref + PKT_TIMEOUT_SYMS * M` where `PKT_TIMEOUT_SYMS` is a register-configurable maximum packet length in symbols (default covers the maximum LoRa payload at the configured SF/BW/CR)

Whichever fires first terminates the current packet and returns the FSM to IDLE.

---

## Timing Events

### Preamble timeout

Training should complete within the 8-symbol preamble window:

```
preamble_timeout = timing_ref + 8M + PREAMBLE_GUARD
```

`PREAMBLE_GUARD` is a small configurable margin (default 2M) to account for timing_ref accuracy. If `training_done` has not asserted by this point, the FSM transitions to PAYLOAD_ACTIVE without valid weights, and `W_MISSED_PACKET` is set.

### Payload start estimate

The FSM enters PAYLOAD_ACTIVE no later than:

```
payload_start_estimate = timing_ref + 12M   (approximate sync + SFD length at SF6)
```

If `W_commit` fires before this point and the receiver is between packets, `W_valid_set` promotes the weights. Otherwise the commit is queued for the next safe_switch.

### Safe switch

`safe_switch=1` only while in IDLE (between packets). This is the only window where:

- `W_ACTIVE` is updated from `W_SHADOW`
- `ACTIVE_MODE` and `ACTIVE_ANTENNA_EN` are updated from their shadow registers
- FRONTEND_BUF is unfrozen

Mid-packet changes to mode or antenna mask are accepted into shadow registers but do not take effect until the next IDLE entry.

---

## W_commit handling

`W_commit` may arrive in any state. The FSM sets `W_commit_pending` as a sticky flag:

```
W_commit asserted → W_commit_pending = 1
In IDLE           → W_valid_set = 1, W_ACTIVE ← W_SHADOW, W_commit_pending = 0
```

| W_commit timing | Result |
|---|---|
| Arrives in W_PENDING or PAYLOAD_ACTIVE | Queued; activates at next IDLE entry |
| Arrives in IDLE | Immediately promotes W_SHADOW → W_ACTIVE |
| Never arrives before packet end | W_MISSED_PACKET set; combiner stays bypass for that packet |

---

## FRONTEND_BUF control

| FSM event | FRONTEND_BUF action |
|---|---|
| sc_lock (IDLE → PREAMBLE_ACQ) | Assert `buf_freeze` — stop overwriting acquisition history |
| packet_end (any → IDLE) | Deassert `buf_freeze` — resume rolling acquisition |

The buffer is frozen from sc_lock until packet end so that the 2-symbol acquisition history is preserved for optional post-lock diagnostics. Freezing does not affect the live sample path to the training accumulator or combiner — those receive samples directly from the decimator.

---

## Combiner source policy

| Condition | `combiner_source` |
|---|---|
| `W_valid = 0` (no committed weights yet) | Bypass (lowest enabled antenna) |
| In PREAMBLE_ACQ or W_PENDING | Bypass |
| In PAYLOAD_ACTIVE, `W_valid = 1` | W_ACTIVE |
| In PAYLOAD_ACTIVE, `W_valid = 0` | Bypass |
| `W_MISSED_PACKET = 1` for current packet | Bypass (this packet only) |

`W_valid` is set once after the first successful W_commit and cleared only if the host explicitly resets it or changes mode. It persists across packets so that an older (but still valid) W is used rather than falling back to bypass every time weight computation is slightly late.

---

## Interface

| Port | Dir | Width | Description |
|---|---|---|---|
| `clk` | in | 1 | 32 MHz system clock |
| `rst_n` | in | 1 | Active-low reset |
| `iq_valid` | in | 1 | Decimated sample strobe |
| `sample_count` | in | 32 | Free-running iq_valid sample counter |
| `sf` | in | 3 | Spreading factor; M = 2^SF |
| `sc_lock` | in | 1 | SC preamble detection event |
| `timing_ref` | in | 32 | Preamble-start sample index from SC |
| `training_done` | in | 1 | Training accumulator complete |
| `W_commit` | in | 1 | Weight gen finished writing W_SHADOW |
| `mode_shadow` | in | 2 | Host/firmware requested combining mode |
| `antenna_en_shadow` | in | 4 | Host/firmware requested antenna mask |
| `pkt_timeout_syms` | in | 8 | Max packet length in symbols (register-configurable) |
| `safe_switch` | out | 1 | Receiver idle; W/mode/antenna active banks may update |
| `W_valid_set` | out | 1 | Strobe: commit W_SHADOW → W_ACTIVE |
| `W_missed_packet` | out | 1 | Sticky: W not ready before payload; cleared on next sc_lock |
| `combiner_source` | out | 1 | 0=bypass, 1=W_ACTIVE |
| `buf_freeze` | out | 1 | FRONTEND_BUF freeze control |
| `packet_phase` | out | 3 | Encoded FSM state for status/debug |
| `packet_active` | out | 1 | Packet FSM not in IDLE |
| `active_mode` | out | 2 | Latched combining mode for current packet |
| `active_antenna_en` | out | 4 | Latched antenna mask for current packet |

---

## IRQ Sources

| IRQ | Trigger | Consumer |
|---|---|---|
| `IRQ_CORR_LOCK` | IDLE → PREAMBLE_ACQ | Debug / host visibility |
| `IRQ_TRAINING_DONE` | PREAMBLE_ACQ → W_PENDING | PicoRV32 (firmware weight path) or debug |
| `IRQ_W_MISSED_PACKET` | W_MISSED_PACKET set | Debug / threshold tuning |
| `IRQ_PACKET_DONE` | Any → IDLE | Debug / host visibility |

---

## Comparison with FFT-path FSM

| Feature | FFT path | Non-FFT path |
|---|---|---|
| After sc_lock | Wait for live FFT window (`timing_ref + 8M - 1`), trigger FFT | Wait for `training_done` (asserts at approximately same point) |
| States | IDLE / PREAMBLE_DETECTED / FFT_WAIT / W_COMMIT_WINDOW / PAYLOAD_ACTIVE / PACKET_DONE | IDLE / PREAMBLE_ACQ / W_PENDING / PAYLOAD_ACTIVE |
| SRAM management | `live_fft_ready`, `capture_protect` for 288 KB capture window | `buf_freeze` for 1 kB FRONTEND_BUF only |
| W computation trigger | `h_ready` from FFT engine | `training_done` from training accumulator |
| W computation path | PicoRV32 reads H/N0 from SRAM | PicoRV32 or hardware reads Z_j from registers |
| Combiner fallback | Bypass until W_valid | Bypass until W_valid (identical policy) |
| safe_switch policy | Identical | Identical |

---

## Verification

| Test | Method | Pass criterion |
|---|---|---|
| Normal lock and train | Inject sc_lock → training_done → W_commit in sequence | FSM traverses all states; W_valid_set asserts in IDLE; combiner uses W_ACTIVE on next packet |
| W on time | W_commit before payload start | W_MISSED_PACKET=0; W_ACTIVE valid for current packet if between packets |
| W late (next-packet) | W_commit during PAYLOAD_ACTIVE | W_MISSED_PACKET=1; combiner stays bypass this packet; W_ACTIVE updated at IDLE |
| Training timeout | training_done never asserts | Preamble timeout fires; FSM enters PAYLOAD_ACTIVE in bypass; W_MISSED_PACKET=1 |
| Packet timeout | New sc_lock never arrives | PKT_TIMEOUT_SYMS expires; FSM returns to IDLE |
| Back-to-back packets | Two sc_locks in rapid succession | First sc_lock ends current packet (IDLE); second sc_lock immediately enters PREAMBLE_ACQ |
| Mode shadow write mid-packet | Write mode_shadow during PAYLOAD_ACTIVE | active_mode unchanged until IDLE; shadow value promoted at safe_switch |
| No backpressure | Packet arrives during W_PENDING | iq_valid path unaffected; combiner stays bypass |
| buf_freeze timing | Check FRONTEND_BUF control | buf_freeze asserts at sc_lock, deasserts at packet end |

---

## Related Blocks

- [Correlator Bank (SC)](Correlator%20Bank.md) — provides `sc_lock`, `timing_ref`
- [Training Accumulator](Training%20Accumulator.md) — provides `training_done`
- [Weight Generation](Weight%20Generation.md) — provides `W_commit`
- [Frontend Buffer Controller](Frontend%20Buffer%20Controller.md) — receives `buf_freeze`
- [ALMMSE-MRC Combiner](ALMMSE-MRC%20Combiner.md) — receives `combiner_source`, `active_mode`, `active_antenna_en`
- [Register Map](../Register%20Map.md) — `PKT_TIMEOUT_SYMS`, `PACKET_PHASE`, `W_MISSED_PACKET`, IRQ registers
