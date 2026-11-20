# Phase 1 — Validate against a measurement

Nothing the EM model says is worth more than its agreement with one measured
part. This is the plan for the first one, and the tools for it.

## The reference: the LNA in the QFN-40 on RO4350B

`examples/lna_qfn40_on_board.py` — the SG13G2 LNA in an open-cavity QFN-40
on the RO4350B board with GCPW lines.

| Item | What |
|---|---|
| DUT board | the board of the example: connectors, GCPW, the QFN with the LNA |
| 2x-thru coupon | on the **same panel**: connector + GCPW + connector, exactly twice the DUT's fixture length (each connector to the package's land toe) |
| line coupon | a second thru of other length (for a check of the line model) |
| package-only coupon | optional: the QFN with a *thru die* (a die with a TopMetal2 line from RFIN to RFOUT) — separates package from LNA |

Build at least two DUT boards and two coupon sets: board-to-board spread is
part of the tolerance.

## Measurement

- 2-port VNA, 0.1–10 GHz (the EM band plus margin), ≥ 801 points, IF bandwidth
  ≤ 1 kHz, SOLT calibration to the cable ends (connector-type cal kit).
- DUT unbiased (S-parameters of the passive structure: package and lines) and
  biased (the LNA's gain — compare with the LNA's on-wafer S-parameters
  cascaded with the package model).
- Save Touchstone (.s2p), with the cal kit, power, IFBW in the header comment.

## Analysis (`tools/ps_validate.py`)

```
python tools/ps_validate.py measured.s2p simulated.s2p --thru 2xthru.s2p --tol 1.0 --out report/
```

1. **Quality** of every measured file — IEEE P370 passivity, reciprocity,
   causality. *Poor* data is re-measured, not analysed.
2. **De-embedding** with the 2x-thru (IEEE P370 NZC). Tested here on
   synthetic data: exact for a fixture that is a plain line; with a
   discontinuity inside the fixture (a 0.05 pF step) |S21| is still within
   0.03 dB but |S11| is off by up to 2.5 dB. So the coupon's launch must be
   the DUT's launch, and **|S21| is the acceptance quantity; |S11| is
   reported, not judged**, unless the fixture is a clean line.
3. **Comparison** with the simulation: max and RMS Δ|S| in dB where the
   measurement is above −40 dB, max Δ phase.

## Acceptance

| Quantity | Target |
|---|---|
| Δ|S21| package + lines, 0.1–10 GHz | ≤ 1 dB max, ≤ 0.5 dB RMS |
| Δ phase S21 | ≤ 10° to 6 GHz |
| |S11| | reported |
| LNA gain, biased, cascaded model vs measured | ≤ 1.5 dB in band |

If it fails: first the measurement (quality, fixture), then the model
(mesh convergence: run *fine* against *standard*; material data — RO4350B
Dk/Df at the frequency; wire loop shape — measure loop heights under the
microscope and put them in).

## What comes out

A table of agreement per frequency band, the plot, the measured loop
heights — and with it the first paper: *an open package co-simulation flow
for the IHP SG13G2 open PDK, validated by measurement*.
