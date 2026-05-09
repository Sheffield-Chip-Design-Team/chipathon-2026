import numpy as np
from sim.models.lora import modulate, demodulate
from sim.models.decimator import SigmaDeltaDecimator

def test_decimator_preservation():
    M = 128
    b = 42
    s = modulate(b, M)
    
    # Decimator with ratio 1 (should just pass through)
    decimator = SigmaDeltaDecimator(ratio=1, output_bits=16)
    s_dec = decimator.process(s)
    
    b_rx = demodulate(s_dec)
    print(f"TX: {b}, RX: {b_rx}")

if __name__ == "__main__":
    test_decimator_preservation()
