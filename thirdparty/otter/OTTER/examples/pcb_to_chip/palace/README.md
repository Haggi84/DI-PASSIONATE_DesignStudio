# PCB-to-chip transition: Palace (FEM)

[AWS Palace](https://awslabs.github.io/palace/) simulation of the OTTER benchmark: an FR4
board, three gold bondwires and the top metal stack of an IHP SG13G2 chip, 1–30 GHz. The
workflow meshes the STEP geometry with gmsh and solves it with Palace. This is version V5;
`CHANGES.md` explains what changed from the earlier versions and why.

The OTTER add-on generates the same kind of workflow for your own models
(`<name>_palace.py` + `run_palace.sh`), using the mesher recipe from this folder.

## Result

| | \|S11\| error vs CST, RMS / max (1–30 GHz) | S21 notch |
|---|---|---|
| **Palace V5 (this setup)** | **0.45 / 1.25 dB** | 28.75 GHz, −34.4 dB |
| Palace v3 (coarse mesh in the bondwire loops) | 2.30 / 4.33 dB | ≥ 30 GHz |
| Palace v1 (coarse mesh in the bondwire loops) | 4.03 / 7.45 dB | 23.0 GHz |
| CST (reference) | – | 28.53 GHz, −28.2 dB |

All metals are PEC in this setup. The step from v1/v3 to V5 is the finer mesh in the air
inside the bondwire loops (see *Why `M_LOOP` matters* below).

## Folder contents

| File | Purpose |
|---|---|
| `run_all.sh` | Complete run: mesh → Palace |
| `scripts/mesh_palace.py` | Builds the Palace mesh (`run/palace_mesh.msh`) from the STEP file |
| `config/palace_config.json` | Palace settings: materials, boundaries, ports, solver, frequency sweep |
| `geometry/PCB_toChip_clean.step` | Geometry, 24 solids, units mm |
| `CHANGES.md` | What changed from the earlier Palace versions to V5, and why |

Everything a run produces goes to `run/`: the mesh, `palace.log`, and the result `postpro/port-S.csv`
(|S11|, |S21| in dB and phase, 1–30 GHz, 117 points).

## Requirements

- Python 3 with `gmsh` and `numpy`
- Palace (tested with 0.16.0; the benchmark run logged git changeset `869ee5c`) and MPI
  (tested with OpenMPI 5)

## How to run

```bash
bash run_all.sh                    # uses all CPU cores
NP=16 bash run_all.sh              # choose the number of MPI processes
M_LOOP=15 bash run_all.sh          # finer bondwire-loop mesh (convergence check)
```

`run_all.sh` calls the Palace **binary** `palace-x86_64.bin` next to the `palace` command, or
`PALACE_BIN=/path/to/palace-x86_64.bin`. Do not start the `palace` wrapper under `mpirun`: the
wrapper launches its own 1-process run on every MPI rank, and these runs crash on each other's
output files (`json parse_error … empty input`).

Mesh generation takes about 1–2 min. Palace takes 30–60 min on 16 cores, with 373 k tetrahedra and
2.5 M unknowns at order 2.

```bash
PY=/path/to/venv/bin/python3 NP=16 bash run_all.sh   # the Python that has gmsh and numpy
```

## Model

| Item | Setting |
|---|---|
| Metals (PCB copper, chip metals, bondwires) | PEC, removed from the mesh: their surfaces are the PEC boundary |
| FR4 | εr = 4.3, lossless |
| SiO2 (chip) | εr = 4.1, lossless (SIO2 and SIO2_2 merged) |
| Outer boundary | 2nd-order absorbing boundary, 3 mm from the structure |
| Port 1 (`port_in`) | lumped 50 Ω, x = −2.9 mm, between Metal5 and TM2_1 (8 × 5.65 µm) |
| Port 2 (`port_out`) | lumped 50 Ω, x = −0.9 mm, between PCB ground and line (500 × 254 µm) |
| Solver | order 2, adaptive fast frequency sweep 1–30 GHz, 0.25 GHz steps, tolerance 1e-3 |

## Mesh settings (`scripts/mesh_palace.py`, all overridable as environment variables, µm)

| Variable | Default | Region |
|---|---:|---|
| `M_LOOP` | 25 | **air inside the bondwire loops** (box around all three wires), transition `T_LOOP` = 300 |
| `M_WIRE` | 5 | bondwire surfaces |
| `M_FOOT` | 4 | bondwire foot points (ball, radius `R_FOOT` = 50, transition `T_FOOT` = 400) |
| `M_CHIP` | 4 | chip metals and thin SiO2 |
| `M_PORT_IN` / `M_PORT_OUT` | 1.2 / 40 | port faces |
| `M_PCB` | 250 | PCB copper and FR4 |
| `M_AIR` | 600 | air (maximum) |
| `MARGIN` | 3000 | air margin to the absorbing boundary |
| `CURV` | 20 | elements per full circle on the round bondwires |

**Why `M_LOOP` matters.** The bondwire loop inductance, and with it S11 and the frequency of the
S21 resonance dip, is set by the magnetic field in the air *inside* the bondwire loops. A finite-
element solution underestimates this inductance when that air is meshed coarsely. With 90–300 µm
elements there, the equivalent series inductance came out at 0.36–0.44 nH instead of ~0.7 nH,
and the S21 dip moved between 23 and 30 GHz from one mesh to the next. Check convergence by
reducing `M_LOOP` until the S21 dip frequency changes by less than ~0.2 GHz.

## Things that must stay as they are

- **`"L0": 1.0e-3`** in the config: the mesh is in mm. Without it, all frequencies are wrong.
- **Port faces are snapped to exact rectangles.** Palace checks lumped-port faces with a relative
  tolerance of 1e-6 (8 pm on the 8 µm port), so the mesher derives port 1 from the real Metal5 /
  TM2_1 surfaces and moves the port nodes exactly onto the rectangle.
- **Only PEC faces that still touch a meshed volume are kept.** Faces between two removed metals
  would otherwise make MFEM abort (`STable3D::operator()`).
- **Material lookup uses the table `REF` in `mesh_palace.py`,** which matches solids by bounding box.
  It is specific to this geometry and must be updated if the geometry changes.
