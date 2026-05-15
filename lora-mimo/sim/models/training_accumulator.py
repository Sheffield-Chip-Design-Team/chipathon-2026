"""Training Accumulator model — non-FFT preamble channel estimation.

Corresponds to planning/blocks/Training Accumulator.md.

Z_j = Σ_n  raw_j[n] · conj(chirp_ref[n mod M])

where the sum runs from sc_lock_sample to timing_ref + 8·M − 1.

Z_j / n_acc  ≈  h_j · φ_common

where φ_common is a common CFO phase factor that cancels in all weight
computation modes (MRC, EGC, SC) because it appears identically in every
branch. No CFO correction is needed before weight computation.
"""

import numpy as np


def chirp_reference(M: int) -> np.ndarray:
    """
    Complex upchirp reference LUT, length M.

    chirp_ref[n] = exp(j·π·n²/M)   for n = 0, …, M−1

    The training accumulator multiplies each sample by conj(chirp_ref[n mod M]).
    At SF6 (M=64) this is a 64-entry LUT — 256 bytes in hardware.
    """
    n = np.arange(M)
    return np.exp(1j * np.pi * n ** 2 / M)


def training_accumulate(
    raw_j: np.ndarray,
    sc_lock_sample: int,
    timing_ref: int,
    M: int,
) -> tuple[np.ndarray, int]:
    """
    Accumulate dechirped preamble samples to estimate per-branch channel.

    Parameters
    ----------
    raw_j : (NR, N_samples) complex array
        Full-precision decimator output samples. Sample index 0 = start of
        the simulation frame. Must be full-precision (NOT 8-bit saturated
        SRAM samples — see Frontend Buffer Controller spec).
    sc_lock_sample : int
        Sample index at which sc_lock asserted. Accumulation starts here.
    timing_ref : int
        Preamble-start sample index back-calculated by the SC detector.
        Accumulation ends at timing_ref + 8·M − 1.
    M : int
        Samples per symbol (2^SF).

    Returns
    -------
    Z_j : (NR,) complex
        Raw accumulator output. Z_j / n_acc ≈ h_j · φ_common.
    n_acc : int
        Number of samples accumulated.

    Notes
    -----
    With SC_HITS_REQ = 2 and SF6 (M=64), sc_lock fires approximately
    3·M samples into the preamble, so ~5 of 8 symbols are accumulated
    (n_acc ≈ 320). This gives a ~2 dB SNR penalty vs ideal; see spec.
    """
    NR, N_samples = raw_j.shape
    lut = chirp_reference(M)

    acc_start = sc_lock_sample
    acc_end   = timing_ref + 8 * M - 1
    acc_end   = min(acc_end, N_samples - 1)

    if acc_start > acc_end:
        return np.zeros(NR, dtype=complex), 0

    indices      = np.arange(acc_start, acc_end + 1)
    phase_idx    = indices % M
    conj_ref     = np.conj(lut[phase_idx])                     # (n_acc,)
    Z_j          = raw_j[:, acc_start:acc_end + 1] @ conj_ref  # (NR,)

    n_acc = acc_end - acc_start + 1
    return Z_j, n_acc


def apply_calibration(
    Z_j: np.ndarray,
    cal_j: np.ndarray | None,
) -> np.ndarray:
    """
    Apply per-branch static gain/phase calibration.

    H_j_cal = H_j * conj(cal_j)

    cal_j : (NR,) complex calibration coefficients, Q1.15 (default 1+0j).
    If None, calibration is bypassed (H_j_cal = Z_j).
    """
    if cal_j is None:
        return Z_j.copy()
    return Z_j * np.conj(cal_j)


def compute_weights(
    Z_j: np.ndarray,
    mode: str = "mrc",
    antenna_en: int = 0xF,
    cal_j: np.ndarray | None = None,
) -> np.ndarray:
    """
    Compute combining weights from training accumulator output.

    Parameters
    ----------
    Z_j       : (NR,) complex channel estimates from training_accumulate()
    mode      : 'mrc' | 'egc' | 'sc' | 'bypass'
    antenna_en: bitmask of enabled antennas (bit 0 = antenna 0)
    cal_j     : (NR,) complex calibration coefficients, or None

    Returns
    -------
    w : (NR,) complex Q1.15 weights

    Notes
    -----
    Division by n_acc is skipped — it is a common scalar across all branches
    and cancels in the weight ratios. Similarly, the common CFO phase factor
    φ_common cancels in all modes. See planning/blocks/Weight Generation.md.
    """
    from .fixed import quantize_q1_15

    NR = len(Z_j)
    mask = np.array([(antenna_en >> j) & 1 for j in range(NR)], dtype=bool)

    H_j = apply_calibration(Z_j, cal_j)
    H_j[~mask] = 0.0

    if mode == "bypass":
        w = np.zeros(NR, dtype=complex)
        enabled = np.flatnonzero(mask)
        if len(enabled):
            w[enabled[0]] = 1.0
        return w

    if mode == "sc":
        mag_sq = np.abs(H_j) ** 2
        mag_sq[~mask] = -1.0
        j_best = int(np.argmax(mag_sq))
        w = np.zeros(NR, dtype=complex)
        if mask[j_best]:
            w[j_best] = 1.0
        return w

    if mode == "egc":
        mag = np.abs(H_j)
        w = np.where(mag > 0, np.conj(H_j) / np.where(mag > 0, mag, 1.0), 0j)
        w[~mask] = 0.0
        return quantize_q1_15(w.real) + 1j * quantize_q1_15(w.imag)

    if mode == "mrc":
        S = float(np.sum(np.abs(H_j) ** 2))
        if S == 0:
            return np.zeros(NR, dtype=complex)
        w = np.conj(H_j) / S
        return quantize_q1_15(w.real) + 1j * quantize_q1_15(w.imag)

    raise ValueError(f"Unknown combining mode: {mode!r}. Use 'mrc', 'egc', 'sc', or 'bypass'.")


def cfo_diagnostic(Z_j: np.ndarray) -> float:
    """
    Pooled CFO diagnostic from training accumulator output.

    C_pool = Σ_j Z_j   (coherent sum across branches)
    cfo_diag = -angle(C_pool) / M   [rad/sample]

    This is a coarse CFO estimate for diagnostic readback only.
    It is NOT used in the weight computation path — see spec.
    """
    C_pool = np.sum(Z_j)
    return -float(np.angle(C_pool))
