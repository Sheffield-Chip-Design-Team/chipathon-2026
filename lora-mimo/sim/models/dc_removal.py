"""
DC Removal model — per-branch IIR running-mean subtraction.

Stage 3 in the DSP Flow. Corresponds to planning/blocks/DC Removal.md.

Hardware equation (integer arithmetic):
    dc_est[j] += (raw[j][n] - dc_est[j]) >> DC_ALPHA_SHIFT
    out[j][n]  = raw[j][n] - dc_est[j]

Time constant τ ≈ 2^DC_ALPHA_SHIFT samples. Default DC_ALPHA_SHIFT = 8 (τ ≈ 256 samples).
Must be applied to full-precision decimator output (not 8-bit saturated SRAM samples).
"""

import numpy as np


class DCRemoval:
    """
    Per-branch IIR running-mean DC removal.

    Parameters
    ----------
    nr          : number of receive branches (default 4)
    alpha_shift : DC_ALPHA_SHIFT register value; time constant = 2^alpha_shift samples

    Usage
    -----
    dcr = DCRemoval(nr=4)
    out = dcr.process(samples)   # (NR, N) complex in → DC-removed out
    """

    def __init__(self, nr: int = 4, alpha_shift: int = 8):
        self.nr = nr
        self.alpha_shift = alpha_shift
        self._dc_est = np.zeros(nr, dtype=np.complex128)

    def reset(self) -> None:
        self._dc_est[:] = 0.0

    def process(self, samples: np.ndarray) -> np.ndarray:
        """
        Process one block of samples, updating the running DC estimate.

        Parameters
        ----------
        samples : (NR, N) complex array at decimator output rate

        Returns
        -------
        out : (NR, N) complex, DC removed
        """
        NR, N = samples.shape
        assert NR == self.nr, f"Expected {self.nr} branches, got {NR}"
        out = np.empty_like(samples)
        alpha = float(2 ** self.alpha_shift)

        for n in range(N):
            self._dc_est += (samples[:, n] - self._dc_est) / alpha
            out[:, n] = samples[:, n] - self._dc_est

        return out
