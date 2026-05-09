"""MRC receiver DSP chain (Stages 3–6 from the DSP Flow Equations).

Stage 3 — Energy Detector:
  See sim.stages for energy_detector

Stage 4 — FFT-based channel estimation (replaces CorrelatorBank):
  h_hat_j = D_j[k_peak] / M   where D_j = FFT(dechirp(rx_j))
  k_peak found by incoherent sum across antennas and symbols.

Stage 5a — Phase extraction (firmware):
  φ_j = ∠ h_hat_j

Stage 5b — Phase correction (RTL complex multiply):
  x'_j[m] = x_j[m] · exp(-j·φ_j)

Stage 5c — Real combining coefficient (firmware):
  c_j = |h_hat_j| / (Σ_j |h_hat_j|² + N0)

Stage 6 — MRC combining (RTL real MAC × 4):
  y[m] = Σ_j  c_j · x'_j[m]

Stage 7 — ALMMSE combining:
  ŷ[n] = W · x[n] (2 output nodes)
"""

import numpy as np
from .stages import energy_detector
from .fixed import quantize_q1_15


# ---------------------------------------------------------------------------
# Stage 4 — FFT-based channel estimation
# ---------------------------------------------------------------------------

def estimate_channel(rx_preamble: np.ndarray, M: int, N_sym: int) -> np.ndarray:
    """
    Estimate per-antenna channel coefficients from N_sym preamble upchirps.

    Algorithm
    ---------
    1. Dechirp each symbol: d[n] = rx[n] · exp(-jπn²/M)
    2. FFT → tone at bin k_peak = timing_offset + round(CFO·M/BW)
    3. Peak bin found by incoherent magnitude sum across all antennas and
       symbols — robust to timing and CFO, same bin for all antennas.
    4. Coherently average D[k_peak] / M across N_sym symbols per antenna.

    Parameters
    ----------
    rx_preamble : (NR, N_sym * M) complex array
    M           : samples per symbol (2^SF)
    N_sym       : number of preamble symbols

    Returns
    -------
    h_hat : (NR,) complex channel estimates
    """
    NR = rx_preamble.shape[0]
    n = np.arange(M)
    ref = np.exp(-1j * np.pi * n**2 / M)

    # Incoherent peak search across all antennas and symbols
    mag_sum = np.zeros(M)
    for j in range(NR):
        for s in range(N_sym):
            seg = rx_preamble[j, s*M:(s+1)*M]
            mag_sum += np.abs(np.fft.fft(seg * ref))
    peak_bin = int(np.argmax(mag_sum))

    # Coherent average of complex peak value per antenna
    h_hat = np.zeros(NR, dtype=complex)
    for j in range(NR):
        acc = 0j
        for s in range(N_sym):
            seg = rx_preamble[j, s*M:(s+1)*M]
            acc += np.fft.fft(seg * ref)[peak_bin]
        h_hat[j] = acc / (N_sym * M)

    return h_hat


# ---------------------------------------------------------------------------
# Stages 5a / 5c
# ---------------------------------------------------------------------------

def compute_weights(h_hat: np.ndarray, N0: float):
    """
    Return per-antenna phase corrections φ and real combining coefficients c.

    Matches PicoRV32 firmware (see PicoRV32 Integration.md):
      denom = Σ_j (|H_j|² + N0)          (per-antenna N0, summed)
      w_j   = H_j* / denom

    Decomposed for the two-stage RTL path:
      phi_j = ∠H_j                        (Stage 5a — phase correction input)
      c_j   = |H_j| / denom               (Stage 5c — real MAC weight)

    phi is stored as a fraction of π (fits Q1.15 without clipping):
      phi_q15 = phi_rad / π  ∈ [−1, 1)

    Returns
    -------
    phi : (NR,) phase in radians (reconstructed from Q1.15 storage)
    c   : (NR,) real non-negative combining weights (Q1.15)
    """
    phi_rad = np.angle(h_hat)                          # Stage 5a, range [-π, π]
    mag     = np.abs(h_hat)

    # Spec denominator: Σ_k(|H_k|² + N0_k) — per-antenna N0, then summed
    denom = np.sum(mag ** 2) + len(h_hat) * N0        # Stage 5c

    c = mag / denom

    # phi stored as turns of π so Q1.15 spans the full [-π, π] range without clipping
    phi_q15 = quantize_q1_15(phi_rad / np.pi)         # Q1.15 in units of π
    c_q15   = quantize_q1_15(c)

    return phi_q15 * np.pi, c_q15                     # return phi back in radians


# ---------------------------------------------------------------------------
# Stage 5b + Stage 6 / 7
# ---------------------------------------------------------------------------

def mrc_combine(rx_payload: np.ndarray, phi: np.ndarray, c: np.ndarray) -> np.ndarray:
    """
    Phase-correct then coherently combine NR received sample streams.

    Parameters
    ----------
    rx_payload : (NR, n_samples) complex array
    phi        : (NR,) phase corrections
    c          : (NR,) real combining weights

    Returns
    -------
    y : (n_samples,) combined signal
    """
    x_prime = rx_payload * np.exp(-1j * phi)[:, None]   # Stage 5b
    return np.sum(c[:, None] * x_prime, axis=0)          # Stage 6

def almmse_combine(rx_payload: np.ndarray, W: np.ndarray) -> np.ndarray:
    """
    ALMMSE combining for NT=2.
    
    Parameters
    ----------
    rx_payload : (NR, n_samples) complex array
    W          : (2, NR) complex weight matrix

    Returns
    -------
    y : (2, n_samples) combined signal nodes
    """
    # Quantize W to Q1.15
    W_q = quantize_q1_15(W.real) + 1j * quantize_q1_15(W.imag)
    return W_q @ rx_payload


# ---------------------------------------------------------------------------
# Fixed-point helpers
# ---------------------------------------------------------------------------

def quantise(x: np.ndarray, bits: int) -> np.ndarray:
    """
    Saturating fixed-point quantisation with peak-normalised full scale.

    Models an AGC + ADC: the block peak sets full scale, then the signal is
    rounded to `bits` levels and the scale is restored.  This isolates
    quantisation noise from clipping so the wordwidth sweep reflects only
    the ADC resolution, not an under-range / over-range artefact.
    """
    if np.iscomplexobj(x):
        peak = np.max(np.abs(x))
        if peak == 0:
            return x
        levels = 2 ** (bits - 1) - 1
        x_n = x / peak
        re = np.clip(np.round(x_n.real * levels), -levels, levels) / levels
        im = np.clip(np.round(x_n.imag * levels), -levels, levels) / levels
        return (re + 1j * im) * peak
    peak = np.max(np.abs(x))
    if peak == 0:
        return x
    levels = 2 ** (bits - 1) - 1
    return np.clip(np.round(x / peak * levels), -levels, levels) / levels * peak
