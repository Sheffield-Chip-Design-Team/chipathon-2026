# AFE Characterisation Board

Prototype PCB for early RF / analog-front-end bring-up before the full ASIC control plane is ready.

---

## Purpose

Provide a simple 4-channel receive front end that can be interfaced directly to the Arty A100 FPGA for:

- SX1257 bring-up
- shared-clock / coherence validation across 4 RX channels
- baseband capture for Schmidl-Cox and FFT bring-up
- analog front-end characterisation before ASIC integration

This board is a lab and architecture-risk-reduction platform, not the final product PCB.

---

## Planned Contents

- 4 × SX1257
- 1 × TCXO fanout / buffer device
  - exact part number TBD
  - intended to distribute one common reference to all 4 SX1257 devices
- SMA connectors for RF inputs
- headers for baseband / digital interfacing to the Arty A100 FPGA
- power, reset, and SPI access needed for basic SX1257 configuration

---

## Why This Board Exists

The main goal is to decouple AFE characterisation from the rest of the ASIC schedule.

This lets the team test:

- whether 4 SX1257 channels can be clocked coherently from a shared reference
- gain / phase consistency across channels
- packet-to-packet CFO / phase stability
- real Schmidl-Cox trigger behavior on live hardware
- FFT capture / estimation behavior with realistic front-end impairments

without waiting for the full ASIC control plane, SPI path, or combiner integration to be complete.

---

## Key Interface Questions

Before schematic freeze, define at least:

- FPGA header pinout
  - signal naming
  - lane ordering
  - bank allocation on Arty A100
- logic voltage standard
  - ensure SX1257 digital I/O levels and FPGA bank standards are compatible
- baseband format
  - exact exported signal type
  - sample clocking relationship
  - whether the FPGA sees parallel sample buses, bitstreams, or another intermediate format
- reset / enable strategy
  - per-device versus shared reset
- SPI programming path
  - how the FPGA or host configures all 4 SX1257s deterministically

---

## Bring-Up Measurements

This board should be used to gather at least the following:

- reference-clock quality at the TCXO output and at each SX1257 clock input
- channel-to-channel phase offset and stability
- channel-to-channel gain spread
- noise floor and spur profile per channel
- packet detect repeatability with shared-clock 4-channel capture
- timing relationship between channels at the FPGA header

Recommended early tests:

1. Single-channel RX bring-up on one SX1257.
2. Shared-clock validation across all 4 channels.
3. Same-signal injection into multiple channels and measure relative phase / amplitude.
4. Capture LoRa preambles into the FPGA and validate `sc_lock`, `timing_ref`, and FFT acquisition.

---

## Design Considerations

- Keep the TCXO fanout path symmetric and low-jitter.
- Preserve clean grounding and supply partitioning for RF, clocking, and digital sections.
- Add enough test access to debug clock, SPI, reset, and at least one representative baseband path.
- If possible, make the baseband header mapping simple and probe-friendly rather than pin-count-optimal.

---

## Relationship To ASIC Work

This board supports:

- Schmidl-Cox threshold / hit-count tuning on real hardware
- validation of the 8-symbol FFT acquisition assumptions
- assessment of whether next-packet versus same-packet weight application is likely to matter in practice
- early confidence in multi-channel coherence before committing further RTL / architecture effort

It should be treated as an input to the ASIC architecture, not only as a lab tool.
