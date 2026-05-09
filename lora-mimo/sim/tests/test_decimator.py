import numpy as np
from sim.decimator import SigmaDeltaDecimator

def test_decimator():
    ratio = 32
    input_len = 1024
    input_signal = np.random.randn(input_len) + 1j * np.random.randn(input_len)
    
    decimator = SigmaDeltaDecimator(ratio=ratio)
    output = decimator.process(input_signal)
    
    expected_len = input_len // ratio
    assert len(output) == expected_len, f"Expected length {expected_len}, got {len(output)}"
    print(f"Test passed: Output length is {len(output)}")

if __name__ == "__main__":
    test_decimator()
