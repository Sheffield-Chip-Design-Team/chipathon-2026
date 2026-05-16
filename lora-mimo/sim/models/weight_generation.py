"""
Weight Generation model — hardware FSM path.

Corresponds to planning/blocks/Weight Generation.md.

Hardware FSM state sequence:
    IDLE → SHIFT → CALIBRATE → COMPUTE → SCALE → WRITE → IDLE

The SHIFT state is the critical step absent from the raw training_accumulator
compute_weights() helper: Z_j values are int64-range (up to ~2^42 for 4×
int8 branches accumulated over 8×2^12 samples at SF12), so they must be
right-shifted to int32 range before multiplication-heavy COMPUTE steps.
"""

import numpy as np
from .fixed import quantize_q1_15


def shift_normalise(Z_j: np.ndarray) -> tuple[np.ndarray, int]:
    """
    SHIFT state: reduce int64-range Z_j to int32 range via common right-shift K.

    K = max(0, ceil(log2(max_component)) - 31)

    Common shift preserves relative magnitudes and phases exactly.

    Returns
    -------
    H_j : (NR,) complex, values in int32 range
    K   : int, bits shifted (0 if already in int32 range)
    """
    INT32_MAX = 2**31 - 1
    max_component = float(max(
        np.max(np.abs(Z_j.real)),
        np.max(np.abs(Z_j.imag)),
    ))
    if max_component == 0.0:
        return np.zeros_like(Z_j, dtype=complex), 0
    if max_component <= INT32_MAX:
        return Z_j.astype(complex), 0
    K = int(np.ceil(np.log2(max_component / INT32_MAX)))
    return (Z_j / (2 ** K)).astype(complex), K


def apply_calibration(H_j: np.ndarray, cal_j: np.ndarray | None) -> np.ndarray:
    """
    CALIBRATE state: H_j_cal = H_j * conj(cal_j).

    cal_j : (NR,) complex Q1.15 calibration coefficients.
            None = unity (no correction, default).
    """
    if cal_j is None:
        return H_j.copy()
    return H_j * np.conj(cal_j)


def compute_weights_hw(
    H_j_cal: np.ndarray,
    mode: str = "mrc",
    antenna_en: int = 0xF,
    E_ref_H: float | None = None,
) -> np.ndarray:
    """
    COMPUTE + SCALE states: produce Q1.15 combining weights.

    Parameters
    ----------
    H_j_cal   : (NR,) complex calibrated channel estimates (int32-range floats)
    mode      : 'mrc' | 'egc' | 'sc' | 'bypass'
    antenna_en: bitmask of enabled antennas (bit j = antenna j)
    E_ref_H   : E_ref scaled to H space = E_ref / 2^K.
                When provided, MRC uses w_j = conj(H) * E_ref_H / Σ|H|²,
                giving |w_j| ≈ |h_j|/Σ|h_k|² (Q1.15-friendly).
                When None, falls back to w_j = conj(H) / Σ|H|² (tiny for large inputs).

    Returns
    -------
    w : (NR,) complex Q1.15 weights
    """
    NR = len(H_j_cal)
    mask = np.array([(antenna_en >> j) & 1 for j in range(NR)], dtype=bool)
    H = H_j_cal.copy()
    H[~mask] = 0.0

    if mode == "bypass":
        w = np.zeros(NR, dtype=complex)
        enabled = np.flatnonzero(mask)
        if len(enabled):
            w[enabled[0]] = 1.0 + 0j
        return w

    if mode == "sc":
        mag_sq = np.abs(H) ** 2
        mag_sq[~mask] = -1.0
        j_best = int(np.argmax(mag_sq))
        w = np.zeros(NR, dtype=complex)
        if mask[j_best]:
            w[j_best] = 1.0 + 0j
        return w

    if mode == "egc":
        mag = np.abs(H)
        safe_mag = np.where(mag > 0, mag, 1.0)
        w = np.where(mag > 0, np.conj(H) / safe_mag, 0j)
        w[~mask] = 0.0
        return quantize_q1_15(w.real) + 1j * quantize_q1_15(w.imag)

    if mode == "mrc":
        S = float(np.sum(np.abs(H) ** 2))
        if S == 0.0:
            return np.zeros(NR, dtype=complex)
        if E_ref_H is not None and E_ref_H > 0.0:
            # E_ref normalisation: |w_j| ≈ |h_j|/Σ|h_k|² (fits Q1.15)
            w = np.conj(H) * E_ref_H / S
        else:
            w = np.conj(H) / S
        return quantize_q1_15(w.real) + 1j * quantize_q1_15(w.imag)

    raise ValueError(f"Unknown mode {mode!r}. Use 'mrc', 'egc', 'sc', or 'bypass'.")


class WeightGenerator:
    """
    Hardware weight generation FSM model.

    Models the full SHIFT → CALIBRATE → COMPUTE → SCALE → WRITE path.
    Input is Z_j (int64-range complex from the training accumulator);
    output is W (Q1.15 complex) ready to write to W_HW registers.

    Parameters
    ----------
    mode      : combining mode ('mrc', 'egc', 'sc', 'bypass')
    antenna_en: enabled antenna bitmask (default 0xF = all four)
    cal_j     : (NR,) complex Q1.15 calibration coefficients, or None

    Usage
    -----
    wgen = WeightGenerator(mode='mrc')
    w, K = wgen.process(Z_j)
    """

    def __init__(
        self,
        mode: str = "mrc",
        antenna_en: int = 0xF,
        cal_j: np.ndarray | None = None,
    ):
        self.mode = mode
        self.antenna_en = antenna_en
        self.cal_j = cal_j

    def process(self, Z_j: np.ndarray, E_ref: float | None = None) -> tuple[np.ndarray, int]:
        """
        Run the full FSM from Z_j to Q1.15 weights.

        Parameters
        ----------
        Z_j   : (NR,) complex channel estimates from training_accumulate()
        E_ref : reference branch energy from training_accumulate(); when provided,
                MRC weights are scaled to |w_j| ≈ |h_j|/Σ|h_k|² (Q1.15-friendly).

        Returns
        -------
        w : (NR,) complex Q1.15 weights
        K : common shift applied in the SHIFT state (diagnostic)
        """
        H_j, K = shift_normalise(Z_j)
        H_j_cal = apply_calibration(H_j, self.cal_j)
        # Scale E_ref to H space: H_j = Z_j / 2^K, so E_ref_H = E_ref / 2^K
        E_ref_H = E_ref / (2 ** K) if (E_ref is not None and K >= 0) else None
        w = compute_weights_hw(H_j_cal, mode=self.mode, antenna_en=self.antenna_en,
                               E_ref_H=E_ref_H)
        return w, K
