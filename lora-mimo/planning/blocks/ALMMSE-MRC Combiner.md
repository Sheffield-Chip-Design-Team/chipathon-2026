# MRC Combiner

RX path stage 8. See [DSP Flow](../DSP%20Flow.md) for context.

**Owner:** TBD
**Status:** Not started

---

## Function

Time-domain, sample-by-sample combining of 4 antenna inputs using weight vector W computed by the Weight Generation block. PicoRV32 may optionally override the shadow bank in software mode, but the combiner must not depend on firmware for baseline RX. Supports two modes:

**MRC:** inner product — scalar output
```
y[n] = w^H · x[n]     // 4 complex MACs → 1 int16 output
```

**Passthrough (bypass):** single-antenna direct route, W ignored
```
y[n] = sign_extend(x[bypass_sel][n])   // 1 antenna, sign-extend int8 → int16
```
`bypass_sel` is the index of the lowest-numbered antenna with its `ANTENNA_EN` bit set, decoded from the `bypass_ant` input.

W is produced by the Weight Generation block (hardware FSM or PicoRV32 software path) after `training_done` from the Training Accumulator. Until current-packet W is valid, the combiner must not output zeros; it falls back to the selected bypass antenna so the SX1302 continues seeing a valid single-antenna LoRa stream. In passthrough mode W registers are not read.

---

## Interface

| Port | Direction | Width | Rate | Description |
| --- | --- | --- | --- | --- |
| `x_i[3:0]` | in | 4×(12–16) signed | f_s | I from decimators (4 antennas; width TBD, matches decimator output) |
| `x_q[3:0]` | in | 4×(12–16) signed | f_s | Q from decimators |
| `x_valid` | in | 1 | f_s | Sample strobe |
| `W_re[3:0]` | in | 4×16 signed | static | W vector real — from W register bank |
| `W_im[3:0]` | in | 4×16 signed | static | W vector imaginary |
| `W_valid` | in | 1 | static | Current-packet W has been atomically committed to the active W bank |
| `mode` | in | 1 | static | 0 = MRC; 1 = passthrough |
| `bypass_ant[1:0]` | in | 2 | static | Index (0–3) of antenna to route in passthrough mode; decoded from ANTENNA_EN by control logic |
| `clk_32m` | in | — | 32 MHz | Master clock |
| `rst_n` | in | — | — | Active-low reset |
| `y_i` | out | 16 signed | f_s | Combined I output |
| `y_q` | out | 16 signed | f_s | Combined Q output |
| `y_valid` | out | 1 | f_s | Sample strobe |

---

## Parameters

| Parameter | Value | Notes |
| --- | --- | --- |
| W precision | int16 Q1.15 | Written by hardware weight generation or PicoRV32 software override |
| x precision | 12–16 bit signed (TBD) | From decimators; matches decimator output width |
| Accumulator | int32 | int16 × int16 × 4 MACs = 32-bit minimum; truncate to int16 after accumulation (verify headroom once input width is decided) |
| MACs per sample | 4 complex = 8 real MACs | |
| Output | int16 signed | Truncated from int32 accumulator |

---

## Implementation notes

**MAC structure.** Each complex MAC: `acc_re += W_re×x_i − W_im×x_q`, `acc_im += W_re×x_q + W_im×x_i`. Four complex MACs per sample.

**Accumulator saturation.** Saturate at int16 bounds after truncation — do not allow 2's-complement wrap. This prevents combining failure on ill-conditioned channels where W has large entries.

**Live output state.** Weight generation (hardware FSM or firmware) runs in parallel with the live decimator-to-remod stream. The combiner output policy is:

```
NO_W / ACQUIRING:   y = sign_extend(x[bypass_sel])
W_VALID, MODE=0:    y = w^H · x
MODE=1 passthrough: y = sign_extend(x[bypass_sel])
```

This makes the first packet recoverable as a single-antenna packet if W arrives late, and prevents mid-preamble silence from breaking SX1302 detection.

**W register read timing.** W registers must be double-buffered. The hardware weight path or PicoRV32 software path writes `W_SHADOW`, then asserts a one-cycle commit strobe after all words are written. Hardware copies `W_SHADOW` to `W_ACTIVE` atomically and sets `W_valid`. The combiner reads only `W_ACTIVE`, so firmware writes cannot glitch live MACs. If W is invalidated mid-packet, keep using the last committed `W_ACTIVE` until firmware explicitly clears `W_valid` or changes mode.

**No-glitch switching.** `W_ACTIVE`, `ACTIVE_MODE`, and `ACTIVE_ANTENNA_EN` must update only when the receiver is idle between packets. Host writes to `MODE` or `ANTENNA_EN` update shadow configuration during an active packet and commit at the next idle boundary. If current-packet W is not ready, stay in bypass for that packet rather than switching mid-symbol or at a payload boundary.

**Degenerate case.** When only 1 antenna is enabled via `ANTENNA_EN`, W is a scalar — trivially computed by firmware. Combiner still works; unused antenna inputs are zero.

**Passthrough MUX.** In passthrough mode, a 4:1 MUX on `bypass_ant` selects the raw int8 sample from one decimator, which is sign-extended to int16 and driven directly to `y[0]`. The MAC array is clock-gated. This MUX sits at the output stage of the combiner block so the bypass path has identical clocking and output register timing as the combining paths.

---

## Verification

| Test | Method | Pass criterion |
| --- | --- | --- |
| MRC, 4 equal antennas | Pre-load MRC W; inject 4-channel sine | Output power ≈ 4× single antenna (6 dB) |
| MRC, degenerate (1 antenna) | Set ANTENNA_EN=0001 | Output = single-antenna SNR |
| No current W | Start packet with `W_valid=0` | Output follows `bypass_ant`; REMOD_A receives a valid single-antenna stream |
| W commit | Write W shadow then commit | `W_ACTIVE` changes atomically; no partially-written W appears at output |
| W update mid-packet | Write new W via AHB-Lite during combining | Old W used until commit; no glitch |
| Safe switch | Assert W commit while packet is active | W activation is deferred until the next idle boundary |
| Mode write mid-packet | Host writes MODE/ANTENNA_EN during active packet | `ACTIVE_MODE`/`ACTIVE_ANTENNA_EN` unchanged until next idle boundary |
| Passthrough, ant0 selected | MODE=2, ANTENNA_EN=0001, inject sine on ant0, zeros on ant1–3 | y[0] = sign_extend(x_ant0); y[1] = 0; identical to decimator output |
| Passthrough, ant2 selected | MODE=2, ANTENNA_EN=0100 | y[0] tracks ant2 exactly; ant0/1/3 ignored |
| Passthrough vs MRC gain | Same signal, compare MODE=0 and MODE=2 output power | MRC output ≈ 6 dB higher (4 equal antennas) |
| Throughput | f_s input, MRC mode | `y_valid` 1 cycle after `x_valid` (or fixed latency ≤ 4 cycles) |

---

## Related blocks

- [ΣΔ Decimator](ΣΔ%20Decimator.md) — full-precision input (12–16 bit TBD)
- [PicoRV32 Integration](PicoRV32%20Integration.md) — optional software override path via AHB-Lite
- [ΣΔ Re-modulator](ΣΔ%20Re-modulator.md) — consumes int16 output
- [Register Map](../Register%20Map.md) — `W` matrix at `0x90`–`0xAF`
- [DSP Flow](../DSP%20Flow.md)
