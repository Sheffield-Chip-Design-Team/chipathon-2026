import numpy as np
from .fixed import quantize

class SigmaDeltaDecimator:
    """
    Simulates the ΣΔ Decimator block (Stage 2).
    
    The block performs CIC + FIR decimation, modeling bit-growth through
    the integrator stages.
    """

    def __init__(self, ratio: int, output_bits: int = 8, stages: int = 3):
        """
        Parameters
        ----------
        ratio       : Decimation ratio (1, 32, 64, 128, or 256)
        output_bits : Number of bits for the output signal
        stages      : Number of CIC integrator stages
        """
        if ratio not in [1, 32, 64, 128, 256]:
            raise ValueError("Supported ratios: 1, 32, 64, 128, 256")
        self.ratio = ratio
        self.output_bits = output_bits
        self.stages = stages

    def process(self, rx_bitstream: np.ndarray) -> np.ndarray:
        """
        Decimates the input bitstream (32 MS/s) with modeled bit-growth.
        """
        n_output = len(rx_bitstream) // self.ratio
        
        # 1. CIC Integration (Bit growth stage)
        # Internal width is roughly InputBits + stages * log2(ratio)
        # Using float64 as a proxy for the high-precision internal accumulator
        acc = rx_bitstream.astype(np.float64)
        for _ in range(self.stages):
            acc = np.cumsum(acc)
            
        # 2. Decimate (Strobe at output rate)
        decimated = acc[self.ratio - 1 :: self.ratio]
        
        # 3. Truncation (modeled after CIC comb stages)
        # Scaled by 1/R^N to normalize, then truncate to int8
        normalized = decimated / (self.ratio ** self.stages)
        
        # Apply quantization to simulate finite wordwidth output
        re = quantize(normalized.real * (2**(self.output_bits-1)), self.output_bits) / (2**(self.output_bits-1))
        im = quantize(normalized.imag * (2**(self.output_bits-1)), self.output_bits) / (2**(self.output_bits-1))
            
        return re + 1j * im
