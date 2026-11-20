# openEMS (FDTD)

openEMS solves Maxwell's equations in the time domain on a rectilinear grid. One run with
a broadband pulse gives the S-parameters over the whole band.

## Run a generated script

```bash
cd <name>_openEMS_simulation
python3 <name>_openEMS.py
```

The script:

1. builds the model in CSXCAD from the exported STL files (or boxes),
2. builds the graded mesh and checks that no port lies inside the PML,
3. runs openEMS on all CPU cores until the energy has fallen to `min_decrement`,
4. writes `simulation_output/Sparameters.csv` and `.png`.

Useful switches at the top of the script:

| Variable | Effect |
|---|---|
| `setup_only = True` | Build the model and mesh only, without the FDTD run. Open `simulation_output/<name>.xml` in AppCSXCAD to check it. |
| `mesh_graded = False` | Use the classic equidistant mesh blocks instead of the graded mesh |
| `mesh_max_ratio` | Maximum growth from one cell to the next (default 1.3) |
| `mesh_merge_rel` | Graded mesh: planes closer than this fraction of the local cell are merged (default 0.5) |
| `mesh_merge_tol` | Without grading: mesh lines closer than this are merged (default 50 nm) |
| `mesh_max_res` | Largest cell, `wavelength_min / N` (default N = 20) |
| `mesh_air_margin` | Clear air between the structure and the PML (default λ/4 at fmax) |

These variables are written from **Grid Settings → Solver mesh (global)**, so set them there
before generating; editing the script is only needed for quick experiments.

## Rules that make openEMS results correct

These come from the [13-step findings](../findings/openems.md):

- **Conductivity goes into `kappa`.** `sigma` is magnetic conductivity in CSXCAD.
- **Every metal face and port plane must be a mesh line.** Points on an STL's own surface
  count as *outside* the STL, so thin STL layers may not conduct sideways. Boxes include
  their surface. In the benchmark the thin layers are therefore built as boxes.
- **No sliver cells.** Two mesh lines 10 nm apart set the timestep for the whole run.
- **Grow cells smoothly** (≤ 1.3×) up to λ/20 in air.
- **Keep ports out of the PML:** λ/4 of air, then the 8 PML cells.
- **Thin metal (< 1 µm) is best modelled as a conducting sheet.** It keeps its
  conductivity and thickness without forcing sub-micron cells.
- **Stop at −50 dB** (`min_decrement = 1e-5`). A Gaussian pulse with `f0 = fc = fmax/2`
  covers DC to fmax.

## Cost in the benchmark

| | Exported (original add-on) | OTTER graded mesh |
|---|---|---|
| Cells | 206 M | 2.17 M |
| Smallest cell | ~1e-12 mm | 1.33 µm |
| Timestep | collapses towards 0 | 2.72e-15 s |
| Runtime / RAM | not runnable (≫ 31 GB) | ~70 min on 16 cores / 0.35 GB |
