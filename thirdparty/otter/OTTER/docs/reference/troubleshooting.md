# Troubleshooting

## Results

| Symptom | Likely cause | Fix |
|---|---|---|
| S11 ≈ 0 dB everywhere (openEMS) | Metal defined with `sigma`, so it does not conduct | Put the conductivity in `kappa` (OTTER does this when only *Sigma* is set) |
| S11 ≈ 0 dB, energy stays at 0 | Port does not touch metal, or lies inside the PML | Check in AppCSXCAD with `setup_only = True`; keep λ/4 of air plus the PML outside the structure |
| Noise outside part of the band | Gaussian pulse too narrow | `f0 = fc = fmax/2` |
| Ripple in S-parameters (openEMS) | Run stopped too early | `min_decrement = 1e-5` (−50 dB) |
| S21 notch too low by ~1.5 GHz | PEC metals | Lossy gold and copper (`kappa`) |
| S21 notch moves between meshes (FEM) | Coarse mesh in the air inside the bondwire loops | Refine that air (`M_LOOP`), check convergence |
| Notch depth or shape wrong | Absorbing boundary too close | ≥ λ/4 at fmax |
| All Palace frequencies wrong | Missing `"L0": 1.0e-3` | The mesh is in mm |

## Errors

| Message | Cause | Fix |
|---|---|---|
| openEMS timestep extremely small / run never ends | Sliver cells (mesh lines nm apart) or thin port boxes | Graded mesh with line merging (default in OTTER); zero-thickness ports |
| openEMS uses ≫ 10 GB RAM | Fine equidistant mesh over the whole board | Graded mesh; fine cells only near the chip |
| `Unused primitive` warnings (openEMS) | STL lies between mesh lines or has lower priority | Put its faces on mesh lines; check priorities |
| gmsh: `Invalid boundary mesh (overlapping facets)` | Several fragment passes, or a plate on an existing face | One `fragment()` of everything, then `removeAllDuplicates()` (OTTER's mesher) |
| MFEM abort in `STable3D::operator()` | Free face between two removed metals | Keep only PEC faces that touch a meshed volume |
| Palace: lumped port not found / not planar | Port face not on the rectangle within 1e-6 | Snap port nodes onto the rectangle (OTTER's mesher) |
| Palace: `json parse_error … empty input` | The `palace` wrapper was started under `mpirun` | Run the binary `palace-*.bin` under `mpirun` (`run_palace.sh`) |
| EMerge: `check_version` fails, or `set_scale_factor` does not exist | EMerge other than 3.0 | `pip install emerge==3.0.0a6` |
| EMerge / gmsh fails on the chip parts (STEP import, boolean operations, meshing) | Micrometre features in a model in metres are close to the OpenCASCADE tolerance | EMerge 3.0 with `sim.set_scale_factor(1000)` and scaled units ([details](../user-guide/emerge.md#version-and-scaling)) |

For openEMS, also see the
[openEMS discussions](https://github.com/thliebig/openEMS-Project/discussions) and the 13 steps in
[openEMS findings](../findings/openems.md).
