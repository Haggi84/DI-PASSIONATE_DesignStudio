# EMerge findings

## First problem: micrometre geometry and the OpenCASCADE tolerance

EMerge models in metres. The smallest chip parts of the benchmark are far below a millimetre:

| Part | Size | In metres |
|---|---|---|
| Metal5 (port 1 ground) | 0.49 µm thick | 4.9e-7 |
| Port 1 | 8 × 5.65 µm | 8e-6 × 5.65e-6 |
| Bondwires | 30 µm diameter | 3e-5 |

These sizes come close to the geometric tolerance of OpenCASCADE (1e-7), which EMerge uses
through gmsh for the STEP import, the boolean operations and the meshing. The tiny
structures trip up gmsh and OpenCASCADE.

| | EMerge 2.8.5 (first attempt) | EMerge 3.0 (solution) |
|---|---|---|
| Approach | Scale every length up by hand (`mm = 1.0`) and every frequency down by 1000 (MHz) while meshing | `sim.set_scale_factor(1000)` |
| What the user has to change | All lengths and all frequencies, consistently | Only the length units (`mm = 0.001*1000`) |
| Mesh used by the solver | Scaled back by hand: EMerge's internal mesh arrays (nodes, edge lengths, areas, volumes) divided by 1000, then the frequencies set back to GHz | Scaled back by 1/1000 automatically before solving |
| Version | 2.8.5 | **3.0.0a6** (pre-release); the script enforces 3.0.x with `check_version("3.0.0")` |

With the scaling, Metal5 becomes 4.9e-4 model units, far above the tolerance. Frequencies
stay in real Hz, and the lumped-port width and height are given in real metres (`h1/s`).
Details: [EMerge user guide](../user-guide/emerge.md#version-and-scaling).

## What the benchmark shows

The EMerge run (`examples/pcb_to_chip/emerge/emerge_simulation.py`) treats the PCB copper
and the chip metals as **PEC** and the bondwires as **gold** (4.561e7 S/m). Its curve is
almost the same as openEMS with all metals PEC:

| | S11 error vs CST, RMS / max | S21 notch |
|---|---|---|
| EMerge | 0.99 / 2.65 dB | 27.00 GHz, −25.9 dB |
| openEMS, all PEC | 0.95 / 2.68 dB | 27.00 GHz, −29.2 dB |
| openEMS, lossy bondwires and copper | 0.13 / 0.40 dB | 28.50 GHz, −25.3 dB |
| CST | – | 28.53 GHz, −28.2 dB |

An FDTD and an FEM solver with similar material models agree with each other. The 1.5 GHz
gap to CST therefore comes mainly from the **material model** (no copper losses, PEC
chip metals), not from EMerge itself.

## Settings used

- EMerge 3.0.0a6 with `set_scale_factor(1000)`.
- STEP import of every object (one file per object), priorities as in FreeCAD.
- Mesh: global resolution 0.33, 0.1 mm on the bondwires, 100 µm on chip metals, 10 µm on
  the SiO2, 0.25 mm on the PCB, 2 mm in air, port faces width/5, curved-boundary meshing 6.
- Lumped ports, 50 Ω, along z; absorbing boundary on the outer faces of an open region with
  1 mm of air around the structure.
- `safe_mode = True`. The script as shipped sweeps 1–30 GHz in 50 points. The curve in
  `results/EMerge/Sparameters.csv` uses the same 129 frequency points as openEMS
  (100 Hz – 30 GHz), so it came from a modified version of this sweep.

## Next steps (not yet run)

These follow from the openEMS and Palace findings and are the obvious checks for EMerge:

1. **Lossy PCB copper** (5.8e7 S/m) and the chip stack-up conductivities. In openEMS this
   moved the notch to 28.5 GHz.
2. **A larger air region.** The open region adds 1 mm of air, less than λ/4 at 30 GHz
   (2.5 mm). Palace V5 uses 3 mm.
3. **A fine mesh in the air inside the bondwire loops**, as in Palace V5 (`M_LOOP` =
   25 µm). Then refine further until the notch frequency changes by less than 0.2 GHz.
4. **A finer frequency step around 26–30 GHz**, to resolve the notch depth.
