# PCB-to-chip model

The benchmark is a transition from an FR4 board to an IHP SG13G2 chip through three gold
bondwires (ground-signal-ground). The chip is a bare piece of the back-end-of-line stack:
just enough metal to connect the bondwires to an on-chip port.

![Geometry with an early Palace surface mesh. Top: overview and the chip side with the three bondwires. Bottom left: cut through the layer stack and the bondwire loop. Bottom right: chip metal stack with the on-chip port (green, 8 × 5.65 µm). Plot labels are in German.](../images/palace_mesh.png)

## Parts

| Part | Material | Notes |
|---|---|---|
| PCB substrate | FR4, εr = 4.3, lossless | 254 µm thick; the board is about 2 × 3.6 mm |
| PCB line, ground, PAD | Copper, 5.8e7 S/m | 35 µm thick; the chip sits on the PAD |
| Bondwires (3) | Gold, 4.561e7 S/m | Round, 30 µm diameter; loop up to z ≈ 0.63 mm |
| Chip TopMetal2 (TM2_1…8) | 3.03e7 S/m | Feed line, signal and ground pads; 11.23–14.23 µm above the chip bottom |
| Chip TopVia2 / TopMetal1 / TopVia1 | 3.143e6 / 2.78e7 / 2.191e6 S/m | Ground via stacks |
| Chip Metal5 | 2.319e7 S/m, 0.49 µm thick | Ground of port 1; 5.09–5.58 µm |
| Chip SiO2 | εr = 4.1, lossless | The two SiO2 bodies are merged into one |

The chip conductivities are those of the CST stack-up of IHP SG13G2. The full stack-up file is
reproduced at the end of [openEMS findings](../findings/openems.md#material-from-cst).

## Ports

| Port | Position | Between | Size |
|---|---|---|---|
| 1 (`port_in`), excited | x = −2.9 mm, chip edge | Metal5 and TM2_1 | 8 × 5.65 µm |
| 2 (`port_out`) | x = −0.9 mm, board edge | PCB ground and line | 500 × 254 µm |

Both ports are 50 Ω lumped ports with zero thickness.

## Frequency range and reference

- 1–30 GHz (openEMS and EMerge also give results down to 100 Hz).
- The reference is **CST Microwave Studio**, `results/Palace/CST_reference.s2p`.
- What makes this case hard: features from 0.5 µm (chip metals) to millimetres (board, air)
  in one model, and a sharp S21 notch near 28.5 GHz. The notch is a resonance of the
  ground-bondwire inductance with the capacitance between the chip ground and the PAD, so
  it reacts strongly to how each solver handles the inductance.

## Files

| File | Used by |
|---|---|
| `examples/pcb_to_chip/model/PCB_Chip.FCStd` | FreeCAD with the OTTER settings (`setting.ini`) |
| `examples/pcb_to_chip/openems/*.stl` | openEMS |
| `examples/pcb_to_chip/emerge/step_files/*.step` | EMerge (one STEP file per object) |
| `examples/pcb_to_chip/palace/geometry/PCB_toChip_clean.step` | Palace (24 solids, mm) |
