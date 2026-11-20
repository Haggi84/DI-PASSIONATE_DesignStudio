# EMerge (FEM)

[EMerge](https://github.com/FennisRobert/EMerge) is a Python finite-element solver for
frequency-domain problems. It imports STEP files directly and meshes with gmsh, so curved
parts such as bondwires keep their real shape.

## Version and scaling

!!! warning "Use EMerge 3.0 and `set_scale_factor(1000)` for chip-scale geometry"
    The benchmark script needs **EMerge 3.0** (we used the pre-release **3.0.0a6**,
    `pip install emerge==3.0.0a6`). It calls `sim.check_version("3.0.0")`, which stops on any
    other major.minor version, and it uses `sim.set_scale_factor(1000)`, which EMerge 2.8.5
    does not have.

**Why scaling is needed.** EMerge models in metres. The chip parts are tiny (Metal5 is
0.49 µm thick, port 1 is 8 × 5.65 µm, the bondwires are 30 µm thick). In metres these sizes
come close to the geometric tolerance of OpenCASCADE (1e-7), which EMerge uses through gmsh
for the STEP import, the boolean operations and the meshing, and the tiny structures trip
up gmsh and OpenCASCADE. With EMerge 2.8.5, the workaround was to scale every length up and
every frequency down by hand, and to scale EMerge's internal mesh arrays back by hand after
meshing.

**How it works.** `sim.set_scale_factor(1000)` builds and meshes the model 1000× larger and
scales the mesh back by 1/1000 before solving. With the scaling:

| Quantity | Units | Example from the benchmark script |
|---|---|---|
| Geometry, STEP import, port plates | Scaled: `mm = 1.0`, `um = 1e-3` | `STEPItems(..., unit=mm)`, `[-2.9e-3*s, ...]` with `s = 1000` |
| Mesh sizes, open-region margins | Scaled | `set_size(obj, 0.1*mm)`, `open_region(1, 1, 1)` = 1 mm |
| Frequencies | Real Hz | `set_frequency_range(1e9, 30e9, 50)` |
| Lumped-port width and height | **Real metres** | `LumpedPort(..., width=h1/s, height=th1/s)` |

EMerge prints a warning that global scaling is experimental; this is expected.

!!! note "Scaling in generated scripts"
    Set **Grid Settings → Solver mesh (global) → geometry scale factor** to **1000** for models
    with micrometre-sized parts (OTTER warns when the smallest part is below 10 µm and the
    factor is 1). The generated script then calls `set_scale_factor(SCALE)`, scales the length
    units (`mm`, `um`, … and `fc_unit`) for geometry and mesh sizes, and converts lumped-port
    width and height, surface roughness and thickness back to real metres with
    `emergeRealLength()`. It stops with a clear error on EMerge versions without
    `set_scale_factor`. With the factor 1 the script works in metres, as before.

## Run a generated script

```bash
cd <name>_EMerge_simulation
python3 <name>_emerge.py
```

The script loads every object as a STEP file, assigns materials and mesh sizes, adds
lumped ports and an absorbing boundary on the air box, runs the frequency sweep and writes
`simulation_output/Sparameters.csv` and `.png`.

## The benchmark script

`examples/pcb_to_chip/emerge/emerge_simulation.py` (see the `README.md` in that folder).
Settings worth knowing:

| Setting | Value | Meaning |
|---|---|---|
| `set_scale_factor(1000)` | 1 mm = 1 model unit | See above |
| `set_frequency_range(1e9, 30e9, 50)` | 1–30 GHz, 50 points | Sweep |
| `set_resolution(0.33)` | | Mesh resolution as a fraction of the wavelength; lower = finer |
| `set_curved_boundary_meshing(6)` | | Elements along curved edges; higher = rounder wires |
| Bondwires | 0.1 mm | Maximum element size on the wires |
| Chip metals / SiO2 | 100 µm / 10 µm | |
| PCB | 0.25 mm | |
| Air | 2 mm | `set_domain_size` |
| Port faces | port width / 5 | |
| `open_region(1, 1, 1)` | 1 mm | Air beyond the structure's bounding box, absorbing boundary outside |
| Materials | PCB and chip metals PEC, bondwires gold 4.561e7 S/m | |

Environment variables: `SHOW_PLOTS=0` (headless) and `EMERGE_THREADS=<n>`.

## Notes from the benchmark

- EMerge with **PEC PCB and chip metals** gives practically the same curve as openEMS with
  PEC metals: S21 notch at 27.0 GHz, S11 about 1 dB above CST. The difference to CST is
  mainly the missing conductor losses, not the solver. See [EMerge findings](../findings/emerge.md).
- OTTER warns when the absorbing boundary is closer than λ/4 at fmax: the bondwires
  radiate near the resonance.
- For bondwire loops, add a fine size in the **air inside the loops**. The loop
  inductance comes from that field (see [Palace findings](../findings/palace.md)).
