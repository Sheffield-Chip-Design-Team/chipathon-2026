# Simulation Framework

This directory contains the simulation environment for the LoRa MIMO ASIC. The project is organized to separate model implementations from testing and analysis scripts.

## Directory Structure

```text
sim/
├── __init__.py       # Package initialization
├── models/           # DSP component implementations
│   ├── channel.py    # Channel modeling (Rayleigh fading)
│   ├── converter.py  # ADC and Re-modulator models
│   ├── decimator.py  # ΣΔ Decimator (CIC + FIR)
│   ├── fixed.py      # Bit-true fixed-point arithmetic primitives
│   ├── lora.py       # LoRa CSS modulation and demodulation
│   ├── receiver.py   # FFT-based channel estimation and combining stages
│   ├── stages.py     # Energy detector model
│   └── sync.py       # Schmidl-Cox trigger / timing-ref model
└── tests/            # Verification and analysis scripts
    ├── debug_chain.py     # DSP chain integrity debug
    ├── debug_decimator.py # Decimator logic verification
    ├── debug_levels.py    # Signal level/quantization debug
    ├── debug_lora.py      # Modulation recovery verification
    ├── debug_remod.py     # Re-modulator and filtering debug
    ├── run_ber.py         # Main BER vs SNR sweep and fixed-point analysis
    ├── test_correlator.py # FFT-based preamble/channel-estimator tests
    └── test_sync.py       # Schmidl-Cox trigger tests
```

## Running Simulations

All simulations are executed as modules from the project root:

- **Run BER Curves (default MRC):**
  `python3 -m sim.tests.run_ber --nt 1`
  
- **Run BER Curves (ALMMSE):**
  `python3 -m sim.tests.run_ber --nt 2`

- **Run Bit-Width Sweep:**
  `python3 -m sim.tests.run_ber --fixedpoint`

## Design Notes

- **Bit-True Modeling:** The simulation employs bit-true modeling to reflect ASIC hardware constraints:
  - **Fixed-Point Library:** Found in `sim/models/fixed.py`, providing primitives for quantization (`quantize`), saturation, and `Q1.15` format support.
  - **Stage-Specific Precision:** Components like the `SigmaDeltaDecimator`, `CorrelatorBank`, and `EnergyDetector` enforce hardware-appropriate bit-widths (e.g., `int8` interfaces, 27-bit accumulators) and handle intermediate bit-growth and truncation.
- **DSP Chain:** The signal chain follows the current ASIC specification:
  1. ADC (Stage 1)
  2. ΣΔ Decimator (Stage 2)
  3. Schmidl-Cox trigger + energy measurement (Stage 3)
  4. FFT-based preamble acquisition / channel estimation (Stage 4)
  5. Weight computation and combining (Stages 5-7)
  6. Re-modulator (Stage 8)
