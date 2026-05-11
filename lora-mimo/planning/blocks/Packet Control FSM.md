# Packet Control FSM

RX path control block. See [DSP Flow](../DSP%20Flow.md) for context.

**Owner:** TBD
**Status:** Proposed

---

## Function

Owns packet phase, live FFT readiness, capture protection, and no-glitch switching between bypass and combined output. This block converts Schmidl-Cox timing plus sample-counter progress into deterministic control events for Baseband SRAM, FFT, PicoRV32 firmware, and the combiner.

The FSM is required because capture/FFT are historical paths while the combiner/remodulator are live streaming paths. It must never backpressure `iq_valid`; if control work misses the current packet, the live stream stays in bypass.

---

## State Machine

| State | Entry | Exit |
| --- | --- | --- |
| `IDLE` | Reset, packet done, timeout | `sc_lock` |
| `PREAMBLE_DETECTED` | `sc_lock`, latch `timing_ref`, `ACTIVE_MODE`, `ACTIVE_ANTENNA_EN` | Live 8-symbol window resident |
| `FFT_WAIT` | Assert/protect live capture window and trigger FFT | `h_ready` from FFT |
| `W_COMMIT_WINDOW` | Raise IRQ/status for PicoRV32 W computation | `W_commit` while packet is active, or packet done / idle |
| `PAYLOAD_ACTIVE` | Current-packet W committed, or bypass selected for this packet | packet done / timeout |
| `PACKET_DONE` | End-of-packet marker or timeout | return to `IDLE` |

If `W_commit` arrives while a packet is active, defer activation and leave the current packet in bypass until the next idle boundary.

---

## Interface

| Port | Direction | Width | Description |
| --- | --- | --- | --- |
| `iq_valid` | in | 1 | Decimated sample strobe |
| `sample_count` | in | 32 | Free-running `iq_valid` sample counter |
| `sf` | in | 3 | Spreading factor, `M = 2^(sf+5)` |
| `sc_lock` | in | 1 | Schmidl-Cox lock event |
| `timing_ref` | in | 32 | Estimated preamble-start sample index |
| `h_ready` | in | 1 | FFT outputs `H/N0/eps_sub` valid |
| `W_commit` | in | 1 | PicoRV32 finished writing `W_SHADOW` |
| `mode_shadow` | in | 2 | Host/firmware requested mode |
| `antenna_en_shadow` | in | 4 | Host/firmware requested antenna mask |
| `packet_active` | out | 1 | Packet in progress |
| `packet_phase` | out | 3 | Encoded FSM state for status/debug |
| `live_fft_ready` | out | 1 | 8-symbol window is resident; trigger FFT |
| `capture_protect` | out | 1 | Protect live RCTSL window from overwrite |
| `safe_switch` | out | 1 | Receiver is idle between packets; W/mode/antenna active banks may update |
| `W_valid_set` | out | 1 | Commit `W_SHADOW` to `W_ACTIVE` |
| `W_missed_packet` | out | 1 | W arrived too late; packet remains bypass |
| `combiner_source` | out | 1 | 0=bypass, 1=`W_ACTIVE` |
| `active_mode` | out | 2 | Latched mode for this packet |
| `active_antenna_en` | out | 4 | Latched antenna mask for this packet |

---

## Timing Rules

**Live FFT readiness.**

```
live_fft_ready when sample_count has reached timing_ref + 8M - 1
```

This event must not wait for diagnostic post-guard capture.

**Safe switching.** `W_ACTIVE`, `ACTIVE_MODE`, and `ACTIVE_ANTENNA_EN` update only when `safe_switch=1`. Under the no-mid-packet-switching policy, that means packet idle between packets. If `W_commit` arrives while a packet is active, the current packet remains in bypass and the commit is deferred to the next idle boundary.

**Weight timing policy.** The current FSM behavior corresponds to **next-packet weight application** by default:

- packet `N` preamble triggers SC/FFT estimation
- PicoRV32 computes `W` for packet `N`
- if `W_commit` arrives after packet `N` is already active, packet `N` stays in bypass
- the committed weights become active at the next idle boundary and therefore apply starting with packet `N+1`

This keeps the live path simple and glitch-free, but it assumes the channel estimate remains useful across packet boundaries. A future same-packet mode would require an additional delayed payload path or a separately verified mid-packet safe-switch mechanism.

**Bypass fallback.** Before `W_valid_set`, or if `W_missed_packet=1`, the combiner source remains bypass:

```
combiner_source = BYPASS
```

This preserves a valid single-antenna stream into SX1302.

**No backpressure.** The FSM may assert overflow/missed-packet status, but it must not stall decimator output, combiner input, or remodulator input.

---

## Status / IRQ

Expose at least:

| Status | Meaning |
| --- | --- |
| `PACKET_ACTIVE` | Packet FSM is not idle |
| `PACKET_PHASE[2:0]` | Encoded FSM state |
| `LIVE_FFT_READY` | FFT trigger point reached |
| `W_VALID` | Active W applies to current packet |
| `W_PENDING` | FFT is done and firmware W commit is pending |
| `W_MISSED_PACKET` | Packet stayed in bypass because W missed safe switch |

IRQ sources:

- `IRQ_STATUS.CORR_LOCK` on `PREAMBLE_DETECTED`
- `IRQ_STATUS.H_READY` when H/N0/eps_sub are valid and firmware should compute W
- `IRQ_STATUS.W_MISSED_PACKET` for debug/tuning
- `packet_done` if end-of-packet marker/timeout is implemented

---

## Verification

| Test | Method | Pass criterion |
| --- | --- | --- |
| Live FFT trigger | Inject SC lock and advance sample counter | `live_fft_ready` asserts at `timing_ref + 8M - 1` |
| No post-guard block | Enable diagnostic capture | `live_fft_ready` still asserts before diagnostic capture completion |
| W on time | Assert `W_commit` while packet is idle | `W_valid_set` asserts at safe switch; combiner source changes to W |
| W late | Assert `W_commit` during active packet | `W_missed_packet=1`; combiner source stays bypass until next idle boundary |
| Mode write mid-packet | Change `mode_shadow` during `PAYLOAD_ACTIVE` | `active_mode` unchanged until next idle boundary |
| No backpressure | Capture overflow during packet | `iq_valid` path continues; status bit set |

---

## Related blocks

- [Schmidl-Cox Preamble Detector](Correlator%20Bank.md) — provides `sc_lock` and `timing_ref`
- [Baseband SRAM](Baseband%20SRAM.md) — capture protection and live FFT window
- [FFT Engine](FFT%20Engine.md) — triggered by `live_fft_ready`
- [PicoRV32 Integration](PicoRV32%20Integration.md) — computes W and asserts `W_commit`
- [ALMMSE/MRC Combiner](ALMMSE-MRC%20Combiner.md) — consumes `combiner_source`, active mode, active antenna mask, and `W_valid`
