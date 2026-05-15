# QSPI PSRAM Same-Packet MRC Exploration

## Goal

Evaluate whether an external `QSPI PSRAM` device can make **same-packet MRC** practical while keeping **next-packet weights** as the default fallback path.

This note is intentionally architectural. It is not yet a controller spec.

## Context

The current low-risk architecture is:

- estimate weights from packet `N`
- apply them to packet `N+1`

That avoids payload-delay buffering and keeps the receive path fully streaming.

The proposed extension is:

- buffer decimated per-antenna samples for the current packet
- compute weights from the current packet preamble
- if the weights are ready soon enough, replay buffered samples and apply **same-packet MRC**
- otherwise fall back to **next-packet MRC**

So the intended behavior is:

- **default:** next-packet weights
- **optional optimization:** same-packet weights when buffering and timing allow

## Base assumptions

These estimates use the current repo assumptions:

- decimator output is `int8 I + int8 Q` per branch
- `NR = 4`
- per decimated sample time, total buffered data is:

`4 branches * 2 bytes = 8 bytes`

- decimated sample rate is:

`f_s = BW`

- LoRa symbol length in samples is:

`M = 2^SF`

Therefore:

- bytes per symbol across 4 branches:

`B_sym = 8 * 2^SF bytes`

- byte rate into the packet buffer:

`B_rate = 8 * BW bytes/s`

## Buffering cost per symbol

Per-symbol storage across all four receive branches:

| SF | `M = 2^SF` | Bytes / symbol |
| --- | ---: | ---: |
| 7 | 128 | 1,024 B |
| 8 | 256 | 2,048 B |
| 9 | 512 | 4,096 B |
| 10 | 1024 | 8,192 B |
| 11 | 2048 | 16,384 B |
| 12 | 4096 | 32,768 B |

This table is independent of bandwidth.

## External-memory traffic

### Write bandwidth

Since each decimated sample time across 4 branches produces `8 B`:

`BW_write = 8 * BW`

| BW | Write bandwidth |
| --- | ---: |
| `125 kHz` | `1.0 MB/s` |
| `250 kHz` | `2.0 MB/s` |
| `500 kHz` | `4.0 MB/s` |
| `1 MHz` | `8.0 MB/s` |

### Read + write bandwidth

If same-packet replay is active while capture is still in progress, the external memory may need both:

- sustained write for incoming packet samples
- sustained read for delayed replay

Approximate combined traffic:

`BW_total ~= 16 * BW`

| BW | Read + write bandwidth |
| --- | ---: |
| `125 kHz` | `2.0 MB/s` |
| `250 kHz` | `4.0 MB/s` |
| `500 kHz` | `8.0 MB/s` |
| `1 MHz` | `16.0 MB/s` |

This is the first useful feasibility filter for a candidate `QSPI PSRAM` part and controller.

## Packet-size estimate at decimated rate

If the buffer stores the **entire packet** across all 4 branches:

`B_packet = T_packet * BW * 8 bytes`

Equivalently:

`B_packet = N_sym_total * 2^SF * 8 bytes`

Representative examples at `BW = 125 kHz`, `CR = 4/5`, explicit header, CRC enabled, preamble length `8`:

| Case | Approx packet time | Approx packet buffer size |
| --- | ---: | ---: |
| `SF7`, `16 B` payload | `51.46 ms` | `51.5 kB` |
| `SF7`, `32 B` payload | `71.94 ms` | `71.9 kB` |
| `SF12`, `16 B` payload | `1.319 s` | `1.32 MB` |

Implication:

- full-packet same-packet replay is modest at `SF7`
- full-packet same-packet replay grows quickly at high `SF`

## Timing margin for same-packet combining

If weights are derived from the packet preamble and then applied from the first payload symbol onward, the useful margin is roughly the time from the end of the repeated preamble observation to the payload boundary.

For standard LoRa framing, a reasonable planning estimate is:

`T_guard ~= 4.25 * 2^SF / BW`

At `BW = 125 kHz`:

| SF | `T_guard` |
| --- | ---: |
| `SF7` | `4.352 ms` |
| `SF8` | `8.704 ms` |
| `SF9` | `17.408 ms` |
| `SF10` | `34.816 ms` |
| `SF11` | `69.632 ms` |
| `SF12` | `139.264 ms` |

This is the relevant margin if the design only needs to delay the receive path enough to start combining at payload entry.

If instead the design wants the ability to replay essentially from the preamble region onward with final weights, the delay budget is closer to a full preamble observation window. A simple planning estimate is:

`T_delay_full ~= 8 * 2^SF / BW`

At `SF7/BW125k` this is:

`8.192 ms`

## Minimum useful buffer depths

Useful depth can be expressed directly in symbol counts.

### `4.25` symbols of storage

This roughly matches the post-preamble guard before payload:

`B_4.25 = 4.25 * 8 * 2^SF = 34 * 2^SF bytes`

| SF | Bytes |
| --- | ---: |
| `SF7` | `4.25 kB` |
| `SF9` | `17.0 kB` |
| `SF12` | `136 kB` |

### `8` symbols of storage

This roughly matches a full preamble observation delay:

`B_8 = 8 * 8 * 2^SF = 64 * 2^SF bytes`

| SF | Bytes |
| --- | ---: |
| `SF7` | `8 kB` |
| `SF9` | `32 kB` |
| `SF12` | `256 kB` |

### `12.25` symbols of storage

This covers both a preamble-sized delay and a payload-entry guard:

`B_12.25 = 12.25 * 8 * 2^SF = 98 * 2^SF bytes`

| SF | Bytes |
| --- | ---: |
| `SF7` | `12.25 kB` |
| `SF9` | `49 kB` |
| `SF12` | `392 kB` |

## When PSRAM becomes worthwhile

For a small on-chip design, external memory becomes attractive once the required deterministic packet buffer is clearly larger than a small internal FIFO.

Practical rule of thumb:

- if buffering need is only a few `kB`, internal SRAM is cleaner
- once required buffering is above roughly `16–32 kB`, `QSPI PSRAM` becomes attractive
- if the custom-scheme direction keeps only about `2 kB` of on-chip SRAM, then any meaningful same-packet replay path effectively requires external memory

In short:

- `SF7` payload-entry delay may be possible with a modest external buffer
- high-`SF` full-packet replay strongly favors external memory

## Why a small on-chip FIFO is still needed

Even with `QSPI PSRAM`, a small internal FIFO is still useful.

The FIFO absorbs:

- PSRAM access latency
- command/burst overhead
- arbitration gaps between write and read traffic
- clock-domain or controller elasticity

So the intended architecture should be:

- **small internal FIFO** for real-time elasticity
- **external PSRAM** for packet-delay storage

The FIFO is not a replacement for PSRAM, and PSRAM is not a replacement for the FIFO.

## Recommended operating modes

### Mode A — bypass / stale weights allowed

- current packet uses bypass or previously active weights
- no dependency on new weight availability

### Mode B — next-packet MRC

- estimate `W` from packet `N`
- apply `W` starting at packet `N+1`
- no external-memory dependency in the critical path

This should remain the baseline architecture.

### Mode C — same-packet MRC attempt with PSRAM

- start buffering packet samples immediately after detection
- estimate `W` from the current packet preamble
- if `W_ready` arrives before replay reaches payload boundary, use same-packet MRC
- otherwise automatically fall back to Mode B behavior

This should be treated as an optimization, not a correctness requirement.

## Suggested control policy

1. Detect packet start.
2. Start writing decimated `NR=4` samples into FIFO + PSRAM.
3. Estimate timing, CFO, and channel from the current preamble.
4. Compute `W`.
5. If `W_ready` is early enough, arm same-packet replay/combining.
6. If not, continue with bypass or old weights for the current packet and promote `W` to next-packet use.

Critical design rule:

- the live receive path must never stall waiting for PSRAM

If PSRAM misses timing, the design must degrade gracefully to next-packet operation.

## Main risks

### 1. Throughput is not the only issue

Nominal MB/s may look easy, but controller efficiency matters:

- burst length
- turnaround penalties
- refresh / internal busy time
- arbitration between write and replay reads

### 2. Replay policy complexity

Same-packet combining requires a clean policy for:

- replay start point
- payload boundary alignment
- switching from buffered to live samples
- interaction with `SX1302`-facing output timing

### 3. Long-packet scaling

At high `SF`, even decimated-rate packet buffering becomes large.

This makes:

- payload-entry-only delay

much more realistic than:

- full-packet buffering with arbitrary replay freedom

### 4. Architectural drift

The original custom-scheme argument explicitly tried to avoid:

- replay / delayed-payload buffering
- memory-heavy receive structures

So PSRAM support should be optional and justified by measured benefit.

## Provisional conclusion

`QSPI PSRAM` is a reasonable optional extension if the goal is:

- same-packet MRC when timing closes
- next-packet MRC as the robust fallback

The key observations are:

- decimated-rate buffering is plausible
- raw `32 MS/s` sigma-delta buffering is not the right approach
- weight computation time is not the bottleneck
- the main issues are buffer depth, memory bandwidth, and deterministic replay timing

Most likely practical direction:

- keep **next-packet MRC** as the mandatory mode
- add **same-packet MRC via PSRAM** only as a best-effort enhancement
- keep a **small internal FIFO** in front of the PSRAM path

## Next questions

1. What exact LoRa packet formats and maximum payload lengths must same-packet mode support?
2. What `QSPI PSRAM` part and sustained burst bandwidth are realistic on the board?
3. Does same-packet mode need full-packet replay, or only enough delay to start combining at payload entry?
4. How should replay timing interact with the `SX1302` input path and any re-modulation latency?
