"""Channel models: flat Rayleigh fading + AWGN.

Each antenna sees an independent CN(0,1) coefficient, constant over one packet.
Optional random PLL phase offset (uniform ±π) is folded into the coefficient
before transmission — the preamble FFT estimator absorbs it automatically.
"""

import numpy as np


def rayleigh_coefficients(NR: int, pll_phase_random: bool = True) -> np.ndarray:
    """Return NR independent unit-power complex fading coefficients."""
    h = (np.random.randn(NR) + 1j * np.random.randn(NR)) / np.sqrt(2)
    if pll_phase_random:
        h = h * np.exp(1j * np.random.uniform(-np.pi, np.pi, NR))
    return h


def apply_channel(signal: np.ndarray, h: complex, N0: float) -> np.ndarray:
    """Multiply by fading coefficient and add CN(0, N0) noise."""
    n = len(signal)
    noise = np.sqrt(N0 / 2) * (np.random.randn(n) + 1j * np.random.randn(n))
    return h * signal + noise
