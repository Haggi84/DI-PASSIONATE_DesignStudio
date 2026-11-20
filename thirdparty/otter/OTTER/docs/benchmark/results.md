# Results

All runs simulate the same structure ([model](model.md)). The errors are differences in dB
from CST over 1–30 GHz, at each run's own frequency points (CST is interpolated onto them).

![|S11| and |S21| of the best run of each solver against CST](../images/overview_S11_S21.png)

## Summary

| Run | Metals | \|S11\| error RMS / max | \|S21\| error RMS | S21 notch | Data file |
|---|---|---|---|---|---|
| **CST** (reference) | lossy | – | – | **28.53 GHz, −28.2 dB** | `Palace/CST_reference.s2p` |
| openEMS v3 | lossy: bondwires, PCB copper and chip metals | **0.12 / 0.34 dB** | 0.56 dB | 28.50 GHz, −24.9 dB | `OpenEMS/Sparameters_All_Material.csv` |
| openEMS v2 | lossy bondwires and PCB copper; chip PEC | 0.13 / 0.40 dB | **0.51 dB** | 28.50 GHz, −25.3 dB | `OpenEMS/Sparameters_gold _copper.csv` |
| Palace V5 | PEC | 0.45 / 1.25 dB | 1.69 dB | 28.75 GHz, −34.4 dB | `Palace/Palace_Sparameters_V5.csv` |
| openEMS v1 | PEC | 0.95 / 2.68 dB | 4.83 dB | 27.00 GHz, −29.2 dB | `OpenEMS/OpenEMS_Sparameters.csv` |
| EMerge | PEC; bondwires gold | 0.99 / 2.65 dB | 4.63 dB | 27.00 GHz, −25.9 dB | `EMerge/Sparameters.csv` |
| Palace v3 | PEC; coarse loop mesh | 2.30 / 4.33 dB | 3.36 dB | ≥ 30 GHz | `Palace/Palace_Sparameters_v3.csv` |
| Palace v1 | PEC; coarse loop mesh | 4.03 / 7.45 dB | 5.90 dB | ≈ 23 GHz | `Palace/palace_Sparameter_v1.csv` |

All data files are under `results/`. The [interactive comparison](../comparison/index.html)
plots every run with phases and zoom.

## How to read this

- **The large S21 errors come from the notch.** In dB, a 0.25–1.5 GHz shift of a 25 dB deep,
  narrow dip gives errors of 15–25 dB at a few points. Below 25 GHz, openEMS v3 is within
  0.07 dB of CST in S21 and within 0.34 dB in S11.
- **The notch frequency measures the bondwire inductance.**
    - Runs with PEC metals and a converged mesh (openEMS v1, EMerge) put the notch at
      27.0 GHz.
    - Lossy metals move it to 28.5 GHz (openEMS v2, v3), where CST has it.
    - FEM runs with a coarse mesh in the air inside the loops underestimate the inductance,
      so their notch lands too high or anywhere in between (Palace v1, v3). Refining that
      air (Palace V5) brings the notch back to 28.75 GHz.
- **The notch depth** depends on losses and on how the boundary absorbs radiation. The
  frequency step also matters: 0.5 GHz steps (openEMS, EMerge) can miss the deepest point.
- **Palace V5 has PEC metals but its notch is close to CST's.** We did not separate two
  possible causes: the remaining FEM inductance error, or the different PEC surface
  treatment in FEM. A Palace run with *Conductivity* boundaries on the metals (supported
  by the add-on) is the next step.

## Per-solver plots

=== "CST"
    ![CST reference](../images/CST_Plot_uniform.png)

=== "openEMS v3"
    ![openEMS, all materials](../images/OpenEMS_v3_Plot_uniform.png)

=== "openEMS v1 (PEC)"
    ![openEMS PEC](../images/OpenEMS_Plot_uniform.png)

=== "EMerge"
    ![EMerge](../images/EMerge_Plot_uniform.png)

=== "Palace (V5, v3, v1)"
    ![Palace](../images/Palace_Plot_uniform.png)

## Reproduce the numbers

```bash
cd results
python3 build_comparison.py && python3 plot_images.py && python3 plot_overview.py
```

## Runtime

| Solver | Hardware | Size | Time |
|---|---|---|---|
| openEMS | 16 cores, 0.35 GB RAM | 2.17 M cells, ~211,000 timesteps | ~70 min |
| Palace V5 | 16 MPI ranks | 373 k tetrahedra, 2.5 M unknowns (order 2) | 30–60 min (+1–2 min mesh) |
| EMerge | – | – | not recorded |
