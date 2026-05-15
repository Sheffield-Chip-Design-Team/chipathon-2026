# AGC

Per-antenna automatic gain control for the four SX1257 receive chains.

The AGC loop is implemented in the ASIC firmware on PicoRV32. It adjusts each SX1257's `RegRxAnaGain` independently based on energy measured at packet lock.

---

## Why AGC lives in the ASIC

This design cannot rely on the SX1302's normal gain-control path for the receive antennas used by MRC/ALMMSE.

Reasons:

- The four SX1257s are controlled through the ASIC SPI master, not through the SX1302 control path.
- Combining happens before the SX1302 sees the signal, so gain decisions must be made per antenna on the raw branches.
- The ASIC needs per-antenna energy and saturation handling before channel estimation and combining.
- The gateway may run different gain settings on different antennas; this is invisible to the downstream SX1302.

The result is:

- SX1302 remains the downstream LoRa demodulator
- PicoRV32 owns RX gain control for `SX1257_0..3`

---

## Trigger and timing

AGC runs once per packet at `IRQ_STATUS.CORR_LOCK`.

Sequence:

1. Energy detector latches per-antenna energy over the last 8 symbols
2. `CORR_LOCK` IRQ fires
3. PicoRV32 reads the energy snapshot
4. PicoRV32 updates `RegRxAnaGain` for each antenna if needed
5. New gain applies to the next packet

Important constraints:

- no mid-packet gain changes
- AGC is independent of the later `H_READY` / W-computation path
- AGC is skipped during TX windows
- between packets, gain stays frozen

This is intentional: maximum idle gain preserves weak-packet sensitivity, and any gain change after lock would break packet consistency.

---

## Controlled register

Each SX1257 uses `RegRxAnaGain (0x0C)`.

Bit layout:

| Bits | Field | Range | Meaning |
| --- | --- | --- | --- |
| [7:5] | `RxLnaGain` | 1..6 | `1 = G1` max gain, `6 = G6` min gain |
| [4:1] | `RxBbGain` | 0..15 | Baseband gain, 2 dB per step |
| [0] | `LnaZin` | 0 | Keep 50 ohm setting |

Notes:

- `RxLnaGain` is inverted: larger register value means less gain
- LNA steps are non-uniform: 6 dB for `G1..G3`, 12 dB for `G3..G6`
- usable total range is about 70 dB; nominal register range is 78 dB

Host-visible mirrors:

- `RX_GAIN_0` at `0x30`
- `RX_GAIN_1` at `0x31`
- `RX_GAIN_2` at `0x32`
- `RX_GAIN_3` at `0x33`

These mirror PicoRV32 writes and may also be preset by the host before releasing `CPU_RESET`.

---

## AGC input

AGC uses the per-antenna energy snapshot captured at lock:

```text
ENERGY_n = Σ |x_n|² over the last 8 symbols
```

Properties:

- int16 unsigned
- arbitrary units
- proportional to received power before gain control
- latched at correlator lock so all later firmware reads are packet-consistent

**Non-FFT path: energy tap point must be full-precision decimator output.**

In the non-FFT frontend, the FRONTEND_BUF writes 8-bit *saturated* samples to SRAM. If the energy measurement reads from SRAM rather than the decimator, strong signals that are being clamped to ±127 will appear at lower energy than they actually are — the saturation hides the true signal level from AGC.

The energy measurement must tap the **full-precision samples from the decimator output** (the same point used by the training accumulator), before the 8-bit saturation applied at the SRAM write path. This ensures AGC sees the true received power and can correctly step down gain when a strong signal arrives.

---

## Control policy

Use BB gain for fine tracking and LNA gain for coarse correction.

Policy:

- start at maximum gain for first-packet sensitivity
- if energy is slightly high or low, step BB gain by 2 dB
- if BB hits a limit, step LNA gain and restore BB near mid-scale
- if saturation is detected, step LNA down immediately

Current thresholds:

```c
#define AGC_TARGET_LO  0x0800   // too cold
#define AGC_TARGET_HI  0x6000   // too hot
#define AGC_SAT_GUARD  0xE000   // near saturation
```

Current initial state:

```c
#define LNA_G1  1
#define LNA_G6  6
#define BB_MAX  15
#define BB_MIN  0
#define BB_MID  7
```

Initial gain on all four antennas:

```c
lna_gain[n] = LNA_G1
bb_gain[n]  = BB_MAX
```

Rationale:

- weak first packets should not be missed because startup gain was conservative
- strong first packets may saturate, but that packet can be discarded and the next one will be cleaner

---

## Firmware behavior

Current AGC update shape:

```c
if (TX_ACTIVE) return;

for each antenna n:
    e = read_energy(n)

    if e > AGC_SAT_GUARD:
        step LNA down immediately, restore BB to mid if possible
    else if e > AGC_TARGET_HI:
        reduce BB, or step LNA down if BB already at minimum
    else if e < AGC_TARGET_LO:
        increase BB, or step LNA up if BB already at maximum
    else:
        leave gain unchanged

    if gain changed:
        write SX1257 RegRxAnaGain
        mirror RX_GAIN_n register
        set ema_reset_pending
```

Expected behavior:

- fine tracking converges in 1-2 packets once near the target window
- large overdrive may take multiple packets if an LNA boundary is crossed
- static-channel convergence target in planning is within 3 packets

---

## Interaction with channel estimation and W

Gain changes do not apply inside the current packet.

That matters for consistency:

- `Z_j` (non-FFT path) or `H/N0` (FFT path) are estimated from one packet under one gain setting
- W is computed from those packet-consistent values
- the next packet may use a different gain, and therefore a new `Z_j`

Because `Z_j` scales with gain, gain changes invalidate cross-packet smoothing of channel estimates.

Current rule:

- if any antenna gain changed, set `ema_reset_pending = true`
- on the following packet, skip any cross-packet accumulation and seed from the new estimate directly

This prevents averaging channel estimates measured under incompatible gain states.

---

## Known limitations

### No between-packet probing

AGC only updates on actual packet locks. During silence, gain does not adapt.

This is a deliberate tradeoff:

- pro: keeps maximum idle sensitivity
- con: the first packet after a large path-loss change may saturate or be under-ranged

### Saturation-first policy

The current policy accepts that a very strong first packet may not produce a valid `H`.

Mitigation:

- discard corrupted `H`
- keep previous W if available
- otherwise wait for the next clean packet

### Per-packet control only

There is no mid-packet recovery path. If a packet starts with bad gain, that packet is not fixed in flight.

---

## Open calibration items

- calibrate `AGC_TARGET_LO`, `AGC_TARGET_HI`, and `AGC_SAT_GUARD` on silicon
- verify the energy metric tracks useful headroom across all supported decimation settings
- confirm one clipped branch does not poison the full packet estimate path
- define branch-masking policy if one antenna is persistently saturated, dead, or noisy
- check AGC behavior under strong blockers and near-far conditions

---

## Verification targets

Current planning targets already call for:

- AGC convergence within 3 packets on a static channel
- AGC settling under a 20 dB path-loss change

Useful additional checks:

- one-branch saturation while other branches remain usable
- per-antenna gain mismatch after convergence
- effect of gain change on EMA reset behavior
- false-lock plus AGC mis-adjustment interaction

---

## Related docs

- [PicoRV32 Integration](./PicoRV32%20Integration.md)
- [Register Map](../Register%20Map.md)
- [Energy Measurement](./Energy%20Detector.md)
- [SPI Master](./SPI%20Master.md)
- [Test Plan](../Test%20Plan.md)
