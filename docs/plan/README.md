# The plan

Package Studio's aim: **the open path from an SG13G2 GDS to a package model
the chip designer simulates with, checked against the designer's intent and
against a measurement** — for IHP open-MPW users, who have no open tool for
it. Board layout stays in KiCad, system CAD in the vendor's STEP, chip
design in xschem / KLayout / ORFS.

| Phase | What | State |
|---|---|---|
| 0 Users | 5–8 conversations, one-pager, measures of success | guide and template: `phase0_users.md` — **the conversations are to be held** |
| 1 Validation | one measured reference against the EM model | plan `phase1_validation.md`; tools: `tools/ps_validate.py`, *Compare with measurement* (P370 quality, 2x-thru, report) — **board and measurement to be made** |
| 2 Flow | pin list → wires by net → package LVS → bonding diagram; Touchstone → SPICE → xschem | built: `core/pinlist`, `core/package_lvs`, `app/netlist`, `core/bonding_diagram`, `core/circuit_model`, `tools/ps_circuit_model.py` |
| 3 Interfaces | KiCad in (board, region for EM) and out (footprint, symbol, STEP) | built: `core/kicad_pcb`, `core/kicad_board`, `core/kicad_export`, `app/kicad` |
| 4 Physics | thermal (Elmer), wire resonance, outgassing | open |
| 5 Substrates | MCM on laminate / interposer / 3D-MID, flip-chip | open |
| 6 Release | Addon Manager, CI on conda-forge FreeCAD, tutorial | open |

What was checked against something outside this code:

- the KiCad reader against real KiCad 7 and 9 files (KiCad's own demos);
- the exported footprint's pin numbering and pad positions against KiCad's
  QFN-32 library footprint, and the file against kiutils, an independent
  reader;
- footprint → KiCad board → import → package placed: every lead over the land
  of its number;
- the SPICE subcircuit, simulated in ngspice, against its Touchstone file
  (≤ 0.25 dB) — this found that scikit-rf's passivity enforcement wrecks
  fits, so the fitter now searches for a model passive as fitted;
- P370 de-embedding on synthetic fixtures: exact for a plain line; |S11| up
  to 2.5 dB off when the fixture has a discontinuity (hence: |S21| is the
  acceptance quantity in phase 1).

What was not: anything against a measured part — that is phase 1.
