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


# ---------------------------------------------------------------------------
# Noise floor estimator — firmware policy model
# ---------------------------------------------------------------------------

class NoiseFloorEstimator:
    """
    Firmware-side per-branch noise floor estimator.

    Models the idle-state noise sampling policy from Weight Generation.md:
    when packet_phase == IDLE, sc_lock is inactive, and all per-branch
    energy_j < noise_thresh, firmware reads ENERGY[0..3] and updates
    a per-branch EMA of the noise power.

    Parameters
    ----------
    NR          : number of receive branches
    alpha_shift : EMA decay exponent; alpha = 2^(-alpha_shift). Default 4 → α=0.0625.
    noise_thresh: per-branch per-sample energy threshold above which the near-far
                  guard rejects the symbol window. None disables the guard.
    """

    def __init__(
        self,
        NR: int,
        alpha_shift: int = 4,
        noise_thresh: float | None = None,
    ):
        self.NR = NR
        self.alpha = 2.0 ** (-alpha_shift)
        self.noise_thresh = noise_thresh
        self._sigma2_j = np.zeros(NR)
        self._n_updates = 0
        self._n_rejected = 0

    def update(self, energy_sum_j: np.ndarray, n_window: int) -> bool:
        """
        Attempt a noise floor update from one symbol window.

        Parameters
        ----------
        energy_sum_j : (NR,) per-branch Σ|x|² over the symbol window (ENERGY registers)
        n_window     : samples in the window (M = 2^SF)

        Returns
        -------
        accepted : True if the near-far guard passed and the EMA was updated
        """
        energy_per_sample = energy_sum_j / n_window

        # Near-far guard: reject if any branch exceeds threshold
        if self.noise_thresh is not None and np.any(energy_per_sample > self.noise_thresh):
            self._n_rejected += 1
            return False

        if self._n_updates == 0:
            self._sigma2_j = energy_per_sample.copy()   # cold-start: seed the EMA
        else:
            self._sigma2_j = (
                (1.0 - self.alpha) * self._sigma2_j + self.alpha * energy_per_sample
            )
        self._n_updates += 1
        return True

    @property
    def estimate(self) -> np.ndarray:
        """Current per-branch noise power estimate σ²_j (per sample)."""
        return self._sigma2_j.copy()

    @property
    def n_updates(self) -> int:
        return self._n_updates

    @property
    def n_rejected(self) -> int:
        return self._n_rejected


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


# ---------------------------------------------------------------------------
# SW-path noise-weighted MRC — firmware computes via W_SHADOW
# ---------------------------------------------------------------------------

def compute_nw_mrc_weights(
    Z_j: np.ndarray,
    sigma2_j: np.ndarray,
    n_acc: int,
    antenna_en: int = 0xF,
) -> np.ndarray:
    """
    SW-path noise-weighted MRC.

    w_j = conj(Z_j) / σ²_j

    This is the optimal MRC combiner with per-branch noise weighting.
    With equal σ²_j it is exactly proportional to plain MRC (same branch
    ratios, different overall scale which cancels in demodulation).
    With unequal σ²_j, high-noise branches are downweighted relative to
    low-noise branches, recovering diversity gain that plain MRC loses when
    branch noise floors differ.

    Contrast with the per-branch MMSE form conj(Z_j)/(|Z_j|²+σ²_j·n_acc),
    which has a signal-dependent denominator per branch and does NOT reduce
    to plain MRC even when noise is equal.

    σ²_j is obtained from NoiseFloorEstimator.estimate after sufficient idle
    symbol windows have been accumulated.

    Parameters
    ----------
    Z_j      : (NR,) complex cross-correlation from training_accumulate()
    sigma2_j : (NR,) per-branch noise power estimate (per sample) from NoiseFloorEstimator
    n_acc    : accumulation sample count (unused in weight formula; retained for API symmetry)
    antenna_en : enabled antenna bitmask (bit j = antenna j)

    Returns
    -------
    w : (NR,) complex Q1.15 weights
    """
    NR = len(Z_j)
    mask = np.array([(antenna_en >> j) & 1 for j in range(NR)], dtype=bool)

    Z = Z_j.copy().astype(complex)
    Z[~mask] = 0.0

    s2 = sigma2_j.copy().astype(float)
    s2[~mask] = np.inf   # disabled branches → zero weight

    safe_s2 = np.where(s2 > 0, s2, 1.0)
    w_raw = np.where(mask, np.conj(Z) / safe_s2, 0.0 + 0.0j)

    # Normalise to fill Q1.15: scale so max |w_j| = 1
    max_abs = np.max(np.abs(w_raw))
    if max_abs > 0:
        w_raw = w_raw / max_abs

    return quantize_q1_15(w_raw.real) + 1j * quantize_q1_15(w_raw.imag)
