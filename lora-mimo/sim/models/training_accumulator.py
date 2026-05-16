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
from .weight_generation import (
    WeightGenerator,
    shift_normalise,
    apply_calibration as _apply_calibration,
    compute_weights_hw,
)


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
    """Thin wrapper — canonical implementation is weight_generation.apply_calibration."""
    return _apply_calibration(Z_j, cal_j)


def compute_weights(
    Z_j: np.ndarray,
    mode: str = "mrc",
    antenna_en: int = 0xF,
    cal_j: np.ndarray | None = None,
) -> np.ndarray:
    """
    Compute combining weights from training accumulator output.

    Delegates to WeightGenerator which models the full hardware FSM:
    SHIFT (int64→int32) → CALIBRATE → COMPUTE → SCALE (Q1.15).

    Parameters
    ----------
    Z_j       : (NR,) complex cross-correlation estimates from training_accumulate()
    mode      : 'mrc' | 'egc' | 'sc' | 'bypass'
    antenna_en: bitmask of enabled antennas (bit 0 = antenna 0)
    cal_j     : (NR,) complex Q1.15 calibration coefficients, or None

    Returns
    -------
    w : (NR,) complex Q1.15 weights
    """
    wgen = WeightGenerator(mode=mode, antenna_en=antenna_en, cal_j=cal_j)
    w, _ = wgen.process(Z_j)
    return w


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
