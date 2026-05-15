"""Training Accumulator model — non-FFT preamble channel estimation.

Corresponds to planning/blocks/Training Accumulator.md.

Primary path — cross-correlation against nominated reference branch:

    Z_j = Σ_n  raw_j[n] · conj(raw_ref[n])

where raw_ref = raw_j[ref_sel] and the sum runs from sc_lock_sample to
timing_ref + 8·M − 1.

Z_j / n_acc  ≈  h_j · conj(h_ref)

The common CFO exp(j·ω·n) cancels exactly in the cross-product because
|s[n]|² = 1 for a constant-amplitude LoRa upchirp. This holds at all CFO
values — no Dirichlet attenuation, no integer-bin nulls.

MRC combining using w_j = conj(Z_j) gives y[n] = h_ref · Σ|h_j|² · s[n],
i.e. full MRC gain with h_ref as a common phase rotation (handled by SX1302).
"""

import numpy as np


def chirp_reference(M: int) -> np.ndarray:
    """
    Complex upchirp reference LUT, length M.

    chirp_ref[n] = exp(j·π·n²/M)   for n = 0, …, M−1

    Retained for diagnostic use and the alternative chirp-ref path.
    Not used in the primary cross-correlation accumulation path.
    """
    n = np.arange(M)
    return np.exp(1j * np.pi * n ** 2 / M)


def training_accumulate(
    raw_j: np.ndarray,
    sc_lock_sample: int,
    timing_ref: int,
    M: int,
    ref_sel: int = 0,
) -> tuple[np.ndarray, int]:
    """
    Cross-correlate preamble samples against reference branch to estimate
    per-branch relative channel coefficients.

    Parameters
    ----------
    raw_j : (NR, N_samples) complex array
        Full-precision decimator output samples. Must NOT be 8-bit saturated
        (see Frontend Buffer Controller spec).
    sc_lock_sample : int
        Sample index at which sc_lock asserted. Accumulation starts here.
    timing_ref : int
        Preamble-start sample index back-calculated by the SC detector.
        Accumulation ends at timing_ref + 8·M − 1.
    M : int
        Samples per symbol (2^SF).
    ref_sel : int
        Reference branch index (0–3). Controlled by TACC_REF_SEL register.
        Default 0. The best-known antenna for the deployment should be used.

    Returns
    -------
    Z_j : (NR,) complex
        Cross-correlation output. Z_j / n_acc ≈ h_j · conj(h_ref).
        For j == ref_sel: Z_j is real (auto-correlation = branch energy).
    n_acc : int
        Number of samples accumulated.

    Notes
    -----
    With SC_HITS_REQ = 2 and SF6 (M=64), sc_lock fires approximately
    3·M samples into the preamble, so ~5 of 8 symbols are accumulated
    (n_acc ≈ 320). This gives a ~2 dB SNR penalty vs ideal; see spec.
    """
    NR, N_samples = raw_j.shape

    acc_start = sc_lock_sample
    acc_end   = min(timing_ref + 8 * M - 1, N_samples - 1)

    if acc_start > acc_end:
        return np.zeros(NR, dtype=complex), 0

    window     = raw_j[:, acc_start:acc_end + 1]   # (NR, n_acc)
    ref_window = raw_j[ref_sel, acc_start:acc_end + 1]  # (n_acc,)

    # Z_j = Σ_n raw_j[n] · conj(raw_ref[n])
    Z_j = window @ np.conj(ref_window)              # (NR,)

    n_acc = acc_end - acc_start + 1
    return Z_j, n_acc


def apply_calibration(
    Z_j: np.ndarray,
    cal_j: np.ndarray | None,
) -> np.ndarray:
    """
    Apply per-branch static gain/phase calibration.

    H_j_cal = Z_j * conj(cal_j)

    cal_j : (NR,) complex calibration coefficients (default 1+0j = bypass).
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
    Z_j       : (NR,) complex cross-correlation estimates from training_accumulate()
    mode      : 'mrc' | 'egc' | 'sc' | 'bypass'
    antenna_en: bitmask of enabled antennas (bit 0 = antenna 0)
    cal_j     : (NR,) complex calibration coefficients, or None

    Returns
    -------
    w : (NR,) complex Q1.15 weights

    Notes
    -----
    Z_j ≈ h_j · conj(h_ref) · n_acc.  Division by n_acc cancels in all
    weight ratios.  The common h_ref factor also cancels: conj(Z_j) =
    conj(h_j) · h_ref, and h_ref is a common scalar across all branches.
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

    Not used in the weight computation path — diagnostic readback only.
    Note: with the cross-correlation scheme, Z_j = h_j·conj(h_ref) so
    angle(C_pool) reflects the spread of h_j phases, not CFO directly.
    This function is retained for compatibility but its interpretation
    changes with the cross-correlation scheme.
    """
    C_pool = np.sum(Z_j)
    return -float(np.angle(C_pool))
