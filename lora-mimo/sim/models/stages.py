import numpy as np
from .fixed import quantize

def energy_detector(rx_signal: np.ndarray, bits: int = 16) -> np.ndarray:
    """
    Stage 3 — Energy Detector.

    Computes signal energy per antenna over a symbol period using bit-accurate accumulation.

    Parameters
    ----------
    rx_signal : (NR, N_samples) complex array
    bits      : Number of bits for the output energy register (default 16)

    Returns
    -------
    energy : (NR,) real energy (quantized)
    """
    # Explicitly quantize to int8 (signed) to model the hardware input
    re = quantize(rx_signal.real, 8, signed=True)
    im = quantize(rx_signal.imag, 8, signed=True)

    # Sum of squares of real and imag parts: int8 * int8 = int16.
    energy = np.sum(re**2 + im**2, axis=1)

    # Quantize to energy register width
    return quantize(energy, bits, signed=False)

# CorrelatorBank (Stage 4) removed.
#
# A time-domain dot-product correlator only produces output at bin 0 of the
# dechirped FFT.  Any timing offset n0 or CFO shifts the dechirped tone to
# bin (n0 + ε·M/BW), making the correlator output identically zero for any
# non-zero misalignment — the same failure mode as the FFT at a single bin.
#
# The FFT Engine (Stage 5) already computes all M bins and therefore:
#   - finds the signal regardless of timing or CFO offset;
#   - provides timing and CFO estimates from the peak bin and inter-symbol
#     phase, which the correlator cannot supply;
#   - yields the channel estimate h_hat = D[k_peak] / M, identical to the
#     correlator result after correction.
#
# Keeping a separate correlator block would add hardware (8 MACs) for a
# function that is a strict subset of what the FFT already does.
