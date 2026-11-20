---
title: QFN package findings (lumped ports)
---

# QFN package: openEMS and EMerge against Palace

Four QFN packages (QFN20 4×4 and QFN16 3×3, each with a bonded B3 chip and with a full chip
stack M5…TM2) on an RO4350B board, bondwires of 25 µm, chip metals of 0.5–3 µm, compared
with Palace using lumped 50 Ω ports. Pass rule: |ΔS11| ≤ 1 dB, |ΔS21| ≤ 1.1 dB,
|Δ∠S21| ≤ 13° up to 20 GHz. Scripts, results and plots: `OTTER_Local/New_Example/`.

Result: openEMS within the tolerance up to 16–19 GHz on every model (S21 0.1–0.6 dB over
the limit at 18–20 GHz), in 1–1.5 h instead of more than 10 h. EMerge 3.0 off on all four
(S11 about 2.5 dB low at 1–3 GHz, S21 1–1.5 dB low at 10–15 GHz).

## Model reading (OTTER_Local)

- **"port" must be a word of the label.** `"port" in label` made the 98 chip bodies of a STEP
  import (`import_1`, `import_2`, …) ports; they were dropped and the chip was an open
  circuit (S21 −35 dB). Now `Port_1`, `port2`, `LumpedPort` are ports, `import_5`,
  `Export`, `Support` are not.
- **Bodies in different FreeCAD containers are different groups**, even at the same height:
  the chip vias (`Chip_Metall`) and the oxide pieces between them (`Chip_SiO2`) share their
  z range; as one group named SiO2 the vias became oxide, and setting the group to copper
  turned all of the oxide into copper (chip shorted).
- **Bodies sharing a label** are exported by OTTER as one STL with one material. The model
  now warns about shared labels.

## openEMS

- **Smallest cell, separately in z.** The time step follows the smallest cell: geometry
  lines alone gave 0.65 µm cells and more than 10 h. Mesh setting `minCell_um` (x/y) and
  `minCellZ_um` (z) thin the lines after mesh finalization, conductor faces and sheet
  planes first. z must resolve the thinnest gap between two metals facing each other:
  the chip ground M5 lies 5.65 µm under the signal pad TM2; 10 µm in z put both on one line
  (short, invisible in the material dump because M5 is a conducting sheet), 2 µm was right.
  OTTER_Local sets x/y = bondwire diameter / 5 (5 µm) and z = thinnest facing gap / 2.5
  (2 µm), leaving out via-level gaps and chip inter-metal oxide below 2 µm.
- **x/y cell sets the accuracy above 15 GHz.** At 10 µm the bondwire resonance moved from
  about 25 to 21 GHz (S11 6.4 dB off at 20 GHz), 5 µm gave 23 GHz, 2.5 µm little more.
- **Thin conductors stay connected.** A conductor thinner than the cell holds one line at
  most and turns into a sheet on it: a 0.85 µm via between two chip metals then joins
  nothing. Such boxes are stretched to the enclosing cell, thin solid STL conductors get a
  stand-in box (`applyMinCell()`).
- **A flat STL is never used** ("Unused primitive"): zone-B chip sheets were missing. Flat
  STL bodies become PEC polygons (`addSTL()`).
- **Conductors above all dielectrics, ports above all conductors** (`liftConductorPriorities()`).
- **Lumped ports and PMC.** A lumped port on a PMC face is dead (S11 = 0 dB); a line touching
  a PMC face head-on acts shorted (S11 ≈ −0.1 dB). Use PML with air beyond the line end.
- **A board cut out of a bigger board runs into the PML** (mesh setting `structureIntoPml`,
  set by OTTER_Local when the board touches the airbox): bodies on the structure bounds in
  x/y continue as boxes into the PML, except at faces with a lumped port. A board ending
  in air rings, the energy stays at −15…−20 dB.
- **End at −40 dB** was enough here (−50 dB costs about 30 % more time); environment
  `END_DB=-40` overrides the setting without regenerating.
- **Generated scripts run on a server**: `NO_VIEW=1` (no viewer, also without a display),
  `OPENEMS_THREADS`, `SETUP_ONLY=1` with `DEBUG_PEC=1` for a material dump to check
  that port 1 connects to port 2 and is not shorted to ground before a long run.
- **`from pylab import *` replaces `any`, `all` and `sum`** in generated scripts with numpy's;
  `any(generator)` is then always True. Helper code copied into scripts must not use them
  on generators.

## EMerge

- **Metals keep their conductivity.** EMerge 3.0 turns a volume with σ > 1e4 S/m into a
  surface impedance by itself; PEC lost the conductor loss (0.3 % instead of 5 % at
  1 GHz). Only zero-thickness sheets are PEC.
- **One process per frequency group.** EMerge keeps every solved frequency in memory until
  the process ends: with 134k tetrahedra 6 frequencies fit 20 GB, the 7th was killed. The
  generated script runs each frequency group in a fresh process of itself
  (`EMERGE_FGROUP`) and merges the CSV files. OTTER_Local does not turn groups on: its export
  runs the whole sweep in one run (`run_sweep(frequency_groups=1)`), large models may run out of RAM.
- **EMerge's own RAM check is 2–3× too high**; out-of-core Pardiso failed (MKL error −9/−10);
  gmsh Delaunay was the only 3D algorithm that meshed these models.
- **Open: EMerge mesh sizes from OTTER_Local are too fine for these packages.** The generated
  model 02 meshed to 188,587 nodes; EMerge 3.0 then asks for a nodes × nodes table (132 GiB)
  and stops. The hand-made EMerge model (`New_Example/0X_*/emerge/emerge_lumped.py`, about
  130k tetrahedra, 14–18 GB) used Palace's sizes × 2.5–3 and 50–80 µm in the chip silicon and
  oxide. Until the starter has that rule, use the hand-made script or coarser *FEM Max Size* grids.
