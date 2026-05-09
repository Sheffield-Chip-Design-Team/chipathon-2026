# FFT Engine

RX path stage 5. See [DSP Flow](../DSP%20Flow.md) for context.

**Owner:** TBD
**Status:** Updated for 2× oversampling

---

## Function

Iterative radix-2 FFT on dechirped complex I+Q samples for all 4 antennas. Supports native $f_s = BW$ mode and $2\times$ oversampled $f_s = 2 \cdot BW$ mode for increased CFO breathing room.

**Goal:** Transform dechirped preamble symbols into the frequency domain for bin refinement and SNR estimation.

---

## Interface

| Port | Direction | Width | Rate | Description |
| --- | --- | --- | --- | --- |
| `trigger` | in | 1 | — | From Schmidl-Cox `sc_lock` (preamble acq) or firmware (payload/diagnostics) |
| `sf` | in | 3 | static | Spreading factor: 0=SF5 … 7=SF12 |
| `os_mode` | in | 1 | static | Oversampling mode: 0=$f_s=BW$ (1×), 1=$f_s=2\cdot BW$ (2×) |
| `iq_valid` | in | 1 | $f_s$ | Master sample strobe — used as **Clock Enable** |
| `fft_active` | out | 1 | — | High during READ/COMPUTE/PEAK — pauses capture writes |
| `clk_32m` | in | — | 32 MHz | Master clock |
| `rst_n` | in | — | — | Active-low reset |
| `fft_done` | out | 1 | — | Pulses high when all 4 antennas computed |
| `sram_addr` | out | 19 | — | Address into Baseband SRAM |
| `sram_wdata` | out | 32 | — | Write data |
| `sram_rdata` | in | 32 | — | Read data |
| `sram_we` | out | 1 | — | Write enable |

---

## Parameters

| Parameter | Value | Notes |
| --- | --- | --- |
| Max FFT size ($N_{max}$) | 8192 points | Supports SF12 at 2× oversampling |
| FFT working buffer | 32 KB | `0x00000`–`0x07FFF` (exactly 8192 complex int16) |
| Samples per symbol ($N_{sym}$) | $2^{SF} \cdot (1 + os\_mode)$ | Either $M$ or $2M$ |
| Chirp NCO step | $1 / N_{sym}$ | Q-format; scales with $SF$ and $os\_mode$ |
| Butterfly unit | 1 (reused, iterative) | 4 real MUL + 6 ADD |
| Total cycles (SF12, 2×) | ~50K read + 53,248 compute | ~6.5 ms per 4 antennas at 32 MHz |

---

## Implementation notes

**Flexible Transform Length.** The FSM must handle $N$ ranging from 32 (SF5, 1×) to 8192 (SF12, 2×). The number of passes is $\log_2(N)$.

**Chirp NCO.** The quadratic-phase NCO uses `step = 1.0 / N_sym`.
*   If `os_mode = 0`: $N_{sym} = 2^{SF}$.
*   If `os_mode = 1`: $N_{sym} = 2^{SF+1}$.
This ensures the reference chirp slope matches the incoming signal rate exactly.

**Bit-Reversal.** Samples are bit-reversed based on the current transform length $N$ during the READ phase.

**Memory Reuse.** The READ phase writes 16-bit dechirped samples (I,Q) to the working buffer. The COMPUTE phase performs the FFT in-place. The final PEAK scan reads the same buffer. For 8192 points, the buffer is 100% utilized.

**Bin Interpretation.**
*   In **1× mode**, the full FFT bandwidth equals $BW$.
*   In **2× mode**, the signal bandwidth occupies the center half ($[N/4, 3N/4]$). The FFT engine's PEAK search should be restricted to this range to avoid locking on alias artifacts or out-of-band noise.

---

## Verification

| Test | Method | Pass criterion |
| --- | --- | --- |
| **NCO & Dechirp** | Inject pure chirp signal | Buffer contains DC (constant-phase) signal post-dechirp |
| **FFT Butterfly** | Inject single-bin pulse | Output matches `np.fft.fft()` to ±2 LSB |
| **Bit-Reversal** | Verify buffer ordering | Data indexed correctly for butterfly stages |
| **In-place SRAM** | Run full COMPUTE sequence | Buffer transformed without data corruption |
| **2× Oversampling** | Inject noise in outer bins | PEAK search ignores outer half; only center bins considered |
| **Full Pipeline** | Synthetic LoRa preamble | Lock trigger → FFT peak bin matches expected freq offset |
| **Max Size (8192)** | Run SF12 in 2× mode | Transform completes; no SRAM overflow |

---

## Related blocks

- [ΣΔ Decimator](ΣΔ%20Decimator.md) — provides `iq_valid`
- [Schmidl-Cox Preamble Detector](Correlator%20Bank.md) — asserts `sc_lock` to start preamble acquisition
- [Baseband SRAM](Baseband%20SRAM.md) — provides 32 KB storage
- [Register Map](../Register%20Map.md) — `os_mode` from global control
