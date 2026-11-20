# Using the FreeCAD add-on

The add-on is a dialog with tabs. In each tab you define one part of the simulation and
**assign** it to objects in your FreeCAD model. At the end it writes a Python script for
the solver you picked.

![The add-on dialog (from the original FreeCAD-OpenEMS-Export documentation)](../images/GUI_example_1.png)

## 1. Prepare the model

- Make **one FreeCAD object per material region**: board, each copper layer, each bondwire,
  each chip metal, and the dielectrics.
- Draw an **air box** around everything. It becomes the absorbing boundary. Leave at
  least **λ/4 at the highest frequency** between the structure and the box (2.5 mm at 30 GHz);
  OTTER warns you if the gap is smaller.
- Draw each **port** as a thin box or a face spanning the gap between signal and ground
  (for example from the PCB ground to the line at the board edge). OTTER turns a thin box
  into a zero-thickness plate on the face that touches the geometry.
- Work in **mm**.

## 2. Pick the solver

Choose **openEMS**, **EMerge** or **Palace** in the *Solver type* box at the top. The tabs adapt:
only settings that the chosen solver supports are shown.

## 3. Materials

| Material type | openEMS | EMerge | Palace |
|---|---|---|---|
| *metal* (PEC) | PEC box / STL | PEC | Metal removed from mesh; its faces are PEC |
| *userdefined* with conductivity σ | Lossy metal (`kappa`) | Lossy material | ≥ 1e5 S/m: removed, faces are a *Conductivity* boundary |
| *userdefined* dielectric (εr, tan δ) | Dielectric | Dielectric | Meshed volume |
| *conducting sheet* (σ + thickness) | `AddConductingSheet` (one sheet, no thin cells) | – | *Conductivity* boundary with thickness |

!!! danger "Enter conductivity in the 'Sigma' field"
    For EMerge and Palace, the GUI field *Sigma* is electric conductivity in S/m. For openEMS,
    if you fill in only *Sigma* (with *Kappa* left at 0), OTTER writes the value to CSXCAD as
    `kappa`, because in CSXCAD `sigma` means **magnetic** conductivity. The original export
    wrote it as `sigma`, so copper and gold carried no current and S11 came out at 0 dB.

**Priorities:** where objects overlap, the higher priority wins. Give metals a higher
priority than the dielectrics they touch, otherwise the dielectric replaces the metal on
the shared face. OTTER checks this and warns you.

## 4. Grid (openEMS) or mesh sizes (EMerge, Palace)

The **Grid Settings** tab has three sub-tabs:

| Sub-tab | Used by | Contents |
|---|---|---|
| *openEMS grid* | openEMS | Grid items assigned to objects (*Fixed distance*, *Fixed count*, …) |
| *FEM grid* | EMerge, Palace | Maximum element sizes assigned to objects |
| *Solver mesh (global)* | the selected solver | The mesh recipe for the whole model, saved with the settings file. Only the group of the selected solver is shown. |

- **openEMS:** with *graded mesh* on (default), each *Fixed distance* grid item is a **target
  cell size** for the objects it is assigned to; the radio button then reads
  *Fixed distance (max. cell size, graded)*. OTTER builds one graded mesh:
    - every geometry and port plane becomes a mesh line,
    - a face shared by two bodies of the same material (SiO2 on a 0.2 µm SiO2_2 layer) is
      **not** a mesh line,
    - planes closer than half the local cell are merged (sliver cells would collapse the
      timestep),
    - cells grow by at most 1.3× from one cell to the next, up to λ/20 in air,
    - λ/4 of clear air plus the PML cells is added outside the structure, so ports never
      sit inside the PML.
- **EMerge and Palace:** the size you set is the maximum element size on that object. For
  bondwires, give the **wires** a size: Palace then refines the air inside the loops
  automatically (*loop factor* × wire size), and warns if a wire has no size.

**Solver mesh (global) settings.** Hover over a setting in the dialog for its full
explanation. The defaults are the values that matched CST in the benchmark.

| Solver | Setting | Default | Benchmark origin |
|---|---|---|---|
| openEMS | graded mesh | on | Step 9 |
| openEMS | mesh finalization (planes on mesh lines, air and PML outside) | on | Steps 9, 10 |
| openEMS | no mesh line between bodies of the same material | on | Step 7 |
| openEMS | max. growth of neighbouring cells | 1.3 | Step 9 |
| openEMS | max. cell in air = λmin / N | N = 20 | Step 2 |
| openEMS | graded: merge planes closer than (× local cell) | 0.5 | Step 9 |
| openEMS | not graded: merge lines closer than | 50 nm | Step 9 |
| openEMS | clear air between structure and PML | auto = λ/4 at fmax (2.5 mm at 30 GHz) | Step 9 |
| EMerge | geometry scale factor (`set_scale_factor`, EMerge ≥ 3.0) | 1 (off); use **1000** for micrometre parts | [EMerge scaling](emerge.md#version-and-scaling) |
| EMerge | curved boundary meshing | 6 | benchmark script |
| EMerge | lumped port face: elements across port | 5 | benchmark script |
| EMerge | mesher safe mode | on | benchmark script |
| Palace | max. element size in air | auto = λ/15 at fmax | `M_AIR` |
| Palace | air margin without boundary object | auto = 0.3 λ at fmax | `MARGIN` |
| Palace | elements per full circle on curved surfaces | 20 | `CURV` |
| Palace | lumped port: elements across smaller side | 5 | `M_PORT_IN` |
| Palace | size growth from metal surface into air | 2.5 | – |
| Palace | fuse volumes of the same dielectric | on | SiO2 merge |
| Palace | wire foot refinement radius / growth | 10 × wire size / 1.5 | `R_FOOT`, `T_FOOT` |
| Palace | air inside wire loops (× wire size, 0 = off) / box margin / growth | 5 / 4 / 2.0 | `M_LOOP` = 5 × 5 µm, `T_LOOP` |
| Palace | gmsh threads | all cores | – |

When you change the solver, the GUI also disables inputs that the selected solver's
generator does not use: in the *FEM grid* tab, EMerge uses only *sizeMin* of *surface mesh
size*, and *distance (for points)* is not used by either FEM solver.

## 5. Excitation and frequency range

- **openEMS:** a Gaussian pulse contains `f0 − fc … f0 + fc`. For broadband S-parameters
  use **`f0 = fc = fmax/2`** (DC to fmax). OTTER warns if your pulse does not cover the band.
- **EMerge / Palace:** frequency sweep `fmin … fmax`. For Palace, start at **fmax/30 or
  higher** (1 GHz here). Lower starting frequencies make the FEM problem ill-conditioned
  (low-frequency breakdown).

## 6. Ports

Use **lumped ports**, 50 Ω, one excited. The port must touch metal on both ends:

- openEMS: the port plate lies on a mesh line (added automatically).
- EMerge: the face mesh is refined to 5 elements across the port.
- Palace: the port rectangle is snapped to the real geometry vertices, and the mesh nodes
  are moved exactly onto it (Palace checks the port faces with a relative tolerance of 1e-6).

## 7. Simulation settings (openEMS)

- **End criterion:** `min_decrement = 1e-5` (−50 dB). With −30 dB the port signals are
  cut off before they have decayed, and the spectrum is wrong. OTTER warns about this.
- The run uses all CPU cores (`engine='multithreaded'`).

## 8. Write the script

Click **Generate … Files**. The files go to a folder next to your `.FCStd`:

| Solver | Folder | Run with |
|---|---|---|
| openEMS | `<name>_openEMS_simulation/` | `python3 <name>_openEMS.py` |
| EMerge | `<name>_EMerge_simulation/` | `python3 <name>_emerge.py` |
| Palace | `<name>_Palace_simulation/` | `NP=16 bash run_palace.sh` |

The openEMS and EMerge scripts write `simulation_output/Sparameters.csv` and `Sparameters.png`.
Palace writes `port-S.csv` to the output directory set in the Palace settings.

!!! tip "Read the export warnings"
    OTTER lists settings that are **known to give wrong or very slow results** in a message
    box after writing the script, and again as a comment block at the top of the script.
    Each warning says what to change.
