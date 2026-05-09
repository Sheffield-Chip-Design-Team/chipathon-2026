# Project Schedule

Tapeout deadline: **1 September 2026**. Design review: **July 2026**. Today: **6 May 2026**.

See [Chipathon 2026](Chipathon%202026.md) for official phase definitions.

---

```mermaid
gantt
    title LoRa MIMO ASIC — Tapeout Schedule
    dateFormat  YYYY-MM-DD
    axisFormat  %d %b

    section Milestones
    Team kickoff                         :milestone, m1, 2026-05-09, 0d
    Phase 1 close (project defined)      :milestone, m_p1, 2026-05-31, 0d
    Phase 2 close (blocks implemented)   :milestone, m_p2, 2026-06-30, 0d
    Design review (Phase 3)              :milestone, crit, m2, 2026-07-01, 0d
    FPGA validation pass                 :milestone, m3,     2026-08-10, 0d
    GDS freeze (Phase 4)                 :milestone, crit, m4, 2026-09-01, 0d

    section DSP Simulation
    Python golden reference model        :crit, vf1,  2026-05-09, 28d

    section DSP Implementation
    ΣΔ Decimator ×4 (CIC + FIR)         :rx1,  2026-05-09, 21d
    Energy Detector ×4                   :rx2,  2026-05-09, 14d
    Correlator Bank ×8                   :crit, rx3,  2026-05-09, 28d
    FFT Engine (iterative radix-2)       :crit, rx4,  2026-05-09, 42d
    ΣΔ Re-modulator ×2                   :rx6,  2026-05-09, 14d

    section Control Plane
    Wishbone Bus                         :cb4,  2026-05-09, 10d
    IRQ Controller                       :cb3,  2026-05-23, 7d
    SPI Master + Slave                   :cb1,  2026-05-09, 21d
    OpenRAM (544 KB + 64 KB)             :crit, cm1,  2026-05-09, 14d
    PicoRV32 integration + arbiter       :crit, cm3,  2026-05-18, 21d

    section Software
    Bootloader + SX1257 startup          :fw0,  2026-05-09, 28d
    MRC + ALMMSE weight computation      :crit, fw2,  2026-06-01, 14d
    AGC loop                             :fw4,  2026-06-15, 14d
    Mode auto-switch logic               :fw5,  2026-06-22, 7d
    RPi host driver + ASIC SPI config    :fw6,  2026-05-16, 21d
    RPi ChirpStack integration + demo    :fw7,  2026-07-06, 21d

    section Verification + FPGA
    SWD TAP                              :cm4,  2026-07-01, 14d
    Block testbenches (cocotb)           :vf2,  after vf1, 70d
    FPGA bring-up (SPI blocks)           :fp0,  2026-06-01, 14d
    Integration simulation               :crit, vf4,  2026-06-15, 56d
    FPGA synthesis, bring-up + OTA test  :fp1,  2026-07-13, 28d

    section RF / Hardware
    PCB schematic & layout               :hw2,  2026-05-09, 14d
    PCB fabrication & assembly           :hw3,  2026-05-23, 14d
    PCB bring-up                         :hw4,  2026-06-06, 14d

    section Physical Design
    Trial synthesis + floorplan          :pd0,  2026-07-01, 21d
    Yosys synthesis (GF180MCU)           :crit, pd1,  2026-08-10, 7d
    OpenROAD place & route               :crit, pd2,  2026-08-17, 10d
    DRC / LVS clean (KLayout + netgen)   :crit, pd3,  2026-08-27, 5d
    Chipathon submission package         :pd4,  2026-08-28, 4d
```

---

## Critical path

The chain that determines whether September 1 is achievable:

1. **FFT Engine RTL** (May 9 → Jun 20) — most complex DSP block; iterative radix-2 with SRAM interface across 4 antennas
2. **Correlator Bank RTL** (May 9 → Jun 6) — 8 coherent integrators; determines H matrix quality
3. **Baseband SRAM OpenRAM** (May 9 → May 23) — must run OpenRAM compiler week 1; everything else depends on SRAM working
4. **PicoRV32 integration** (May 18 → Jun 8) — needs SRAM and bus; firmware can't be tested until this is done
5. **ALMMSE firmware** (Jun 8 → Jun 22) — 2×2 matrix inversion in RV32IM; must be done before integration sim
6. **Integration simulation** (Jul 1 → Jul 22) — first time all blocks connect; expect ~1 week debug margin
7. **FPGA OTA test** (Aug 3 → Aug 10) — Arty A7 validates NT=1 + NT=2 before GDS
8. **OpenROAD P&R → DRC/LVS** (Aug 17 → Sep 1) — 2.5 weeks; no float

Trial synthesis runs from Jul 1 to catch area/timing surprises while RTL is still in flux. Final P&R begins Aug 17 once RTL is frozen. FPGA OTA test and final P&R overlap deliberately (Aug 10–17) — if FPGA finds an RTL bug after Aug 17, P&R must restart. Keep FPGA test scope to packet RX, MIMO combining, IRQ rather than exhaustive corner cases.

---

## Float / risk

| Risk | Float | Mitigation |
| --- | --- | --- |
| FFT engine runs late | 1 week | Start cocotb testbench in parallel with RTL |
| OpenRAM generation fails | 0 days (critical path) | Run OpenRAM compiler week 1; use behavioural SRAM model for simulation if needed |
| Correlator bank coherence issues | 3 days | Validate with Python golden model before RTL; test each correlator independently |
| ALMMSE firmware overflow (fixed-point) | 3 days | Validate Q1.15 scaling in Python before porting to C |
| Phase coherence across SX1257s 2–4 | TBD | RF/analog team to verify CLK distribution before FPGA bring-up |
| DRC violations in P&R | 3 days | GF180MCU standard cells only; let OpenROAD handle fill |
| Chipathon shuttle deadline shifts | — | Monitor SSCS announcements; July design review gives early warning |
