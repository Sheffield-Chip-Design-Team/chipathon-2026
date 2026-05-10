"""Channel models: flat Rayleigh fading + AWGN.

Each antenna sees an independent CN(0,1) coefficient, constant over one packet.
Optional random PLL phase offset (uniform ±π) is folded into the coefficient
before transmission — the preamble FFT estimator absorbs it automatically.
"""

import numpy as np
from scipy.ndimage import shift


def rayleigh_coefficients(NR: int, pll_phase_random: bool = True) -> np.ndarray:
    """Return NR independent unit-power complex fading coefficients."""
    h = (np.random.randn(NR) + 1j * np.random.randn(NR)) / np.sqrt(2)
    if pll_phase_random:
        h = h * np.exp(1j * np.random.uniform(-np.pi, np.pi, NR))
    return h


def apply_channel(signal: np.ndarray, h: complex, N0: float, 
                  n_off: float = 0, k_cfo: float = 0, M: int = 128) -> np.ndarray:
    """
    Apply fading, noise, timing offset, and CFO.

    Parameters
    ----------
    signal : np.ndarray
        Input signal.
    h : complex
        Channel coefficient.
    N0 : float
        Noise power.
    n_off : float
        Timing offset in samples.
    k_cfo : float
        CFO in bins (k_cfo = f_off * M / BW).
    M : int
        Samples per symbol (2^SF).
    """
    # Apply fading
    signal = h * signal

    # Apply Timing Offset (fractional)
    if n_off != 0:
        signal = shift(signal.real, n_off) + 1j * shift(signal.imag, n_off)

    # Apply CFO
    if k_cfo != 0:
        # Phase shift per sample: 2 * pi * f_off / fs.
        # Since fs = BW, phase shift per sample = 2 * pi * k_cfo / M.
        n = np.arange(len(signal))
        signal = signal * np.exp(1j * 2 * np.pi * k_cfo * n / M)

    # Add noise
    n_samples = len(signal)
    noise = np.sqrt(N0 / 2) * (np.random.randn(n_samples) + 1j * np.random.randn(n_samples))
    return signal + noise
