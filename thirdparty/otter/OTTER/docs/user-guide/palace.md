# Palace (FEM, MPI)

[AWS Palace](https://awslabs.github.io/palace/) is a parallel finite-element solver.
It handles large meshes, high element order and adaptive fast frequency sweeps on many
MPI ranks.

## Run a generated script

```bash
cd <name>_Palace_simulation
NP=16 bash run_palace.sh
```

`run_palace.sh` does two things:

1. `SHOW_GMSH_GUI=0 python3 <name>_palace.py`: builds the mesh (`.msh`) and writes the
   Palace configuration (`<name>.json`),
2. `mpirun -np $NP palace-x86_64.bin <name>.json`: runs the solver. The S-parameters end up
   in `port-S.csv` in the output directory set in the Palace settings, and the log in
   `palace.log`.

| Variable | Default | Meaning |
|---|---|---|
| `NP` | all cores | Number of MPI ranks |
| `PY` | `python3` | Python interpreter that has `gmsh` |
| `PALACE_BIN` | `palace-x86_64.bin` next to `palace` | The Palace binary (not the wrapper) |
| `SHOW_GMSH_GUI` | on if a display is available | `0` = no gmsh window (servers); `run_palace.sh` always sets 0 |

## How OTTER meshes for Palace

The mesher follows the recipe that made the benchmark work (see
[Palace findings](../findings/palace.md)). The order of the steps matters:

1. Import all objects and remove exact duplicate solids.
2. Read everything needed later (bondwire foot points, port rectangles snapped to the real
   geometry) **before** fragmenting, because fragmenting renumbers all entities.
3. Do **one** `fragment()` of all volumes, the air and the port rectangles, then
   `removeAllDuplicates()`. A second fragment pass causes *"Invalid boundary mesh
   (overlapping facets)"*.
4. Each volume fragment gets the material of the highest-priority object that covers it.
5. **Metals become holes:** their volume is removed and their faces become PEC or
   *Conductivity* boundaries. Only faces that still touch a meshed volume are kept; a
   face between two removed metals makes MFEM abort (`STable3D`).
6. Size fields are combined with a *Min* field, with `Mesh.MeshSizeExtendFromBoundary = 0`
   so that micrometre sizes do not spread into the substrate. Ports, wire feet and the air
   inside the wire loops are refined.
7. Port mesh nodes are moved exactly onto the port rectangle.

The configuration always uses **`"L0": 1.0e-3`**, because the mesh is in mm. Without it,
every frequency is wrong.

## The benchmark (Palace V5)

```bash
cd examples/pcb_to_chip/palace
NP=16 bash run_all.sh
```

Mesh sizes are environment variables in µm, read by `scripts/mesh_palace.py`:

| Variable | Default | Region |
|---|---:|---|
| `M_LOOP` | 25 | **air inside the bondwire loops** (transition `T_LOOP` = 300) |
| `M_WIRE` | 5 | bondwire surfaces |
| `M_FOOT` | 4 | bondwire foot points (`R_FOOT` = 50, `T_FOOT` = 400) |
| `M_CHIP` | 4 | chip metals and thin SiO2 |
| `M_PORT_IN` / `M_PORT_OUT` | 1.2 / 40 | port faces |
| `M_PCB` | 250 | PCB copper and FR4 |
| `M_AIR` | 600 | air (maximum) |
| `MARGIN` | 3000 | air margin to the absorbing boundary |
| `CURV` | 20 | elements per full circle on round wires |

Solver: order 2, adaptive fast sweep 1–30 GHz in 0.25 GHz steps, tolerance 1e-3,
2nd-order absorbing boundary. Metals are PEC.

!!! tip "Convergence check"
    Reduce `M_LOOP` (e.g. `M_LOOP=15`) until the S21 notch frequency changes by less than
    about 0.2 GHz.

!!! warning "The material table is specific to this geometry"
    `mesh_palace.py` matches solids to materials by bounding box (table `REF`). Update that
    table if you change the geometry, or generate the script from FreeCAD with the add-on
    instead.
