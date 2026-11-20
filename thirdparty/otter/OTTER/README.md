# OTTER: Open Source Toolchain for Three-Dimensional Electromagnetic RF Packaging Simulation
[![Documentation](https://img.shields.io/badge/docs-OTTER-green?logo=materialformkdocs&logoColor=white)](https://mohsen-alihosseini.github.io/OTTER/)
[![License](https://img.shields.io/github/license/mohsen-alihosseini/OTTER)](https://github.com/mohsen-alihosseini/OTTER/blob/main/LICENSE)
[![docs](https://github.com/mohsen-alihosseini/OTTER/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/mohsen-alihosseini/OTTER/actions/workflows/ci.yml)

©2026

_Mohsen Alihosseini_

[Documentation](https://mohsen-alihosseini.github.io/OTTER/) | [Benchmark results](https://mohsen-alihosseini.github.io/OTTER/benchmark/results/) | [Cite](#cite-this-work)

**OTTER** sets up one FreeCAD model once and simulates it with three open-source full-wave
EM solvers:

- **[OpenEMS](https://openems.de/)**: FDTD (time domain)
- **[EMerge](https://github.com/FennisRobert/EMerge)**: FEM (frequency domain, Python)
- **[Palace](https://awslabs.github.io/palace/)**: FEM (frequency domain, MPI, large meshes)

OTTER is a FreeCAD add-on that writes a ready-to-run Python script for each solver. It is
based on [FreeCAD-OpenEMS-Export](https://github.com/LubomirJagos/FreeCAD-OpenEMS-Export)
by Lubomir Jagos. OTTER adds fixes and checks for problems that, in our tests, gave wrong
S-parameters. We found them by comparing all three solvers with CST Microwave Studio on a
PCB-to-chip bondwire transition (IHP SG13G2 chip on an FR4 board, 1–30 GHz).

![|S11| and |S21| of the PCB-to-chip transition: CST, openEMS, EMerge and Palace](docs/images/overview_S11_S21.png)

| Run | \|S11\| error vs CST, RMS / max | \|S21\| error vs CST, RMS | S21 notch (CST: 28.53 GHz, −28.2 dB) |
|---|---|---|---|
| openEMS | **0.12 / 0.34 dB** | **0.56 dB** | 28.50 GHz, −24.9 dB |
| Palace  | 0.45 / 1.25 dB | 1.69 dB | 28.75 GHz, −34.4 dB |
| EMerge | 0.99 / 2.65 dB | 4.63 dB | 27.00 GHz, −25.9 dB |
| openEMS, PEC metals | 0.95 / 2.68 dB | 4.83 dB | 27.00 GHz, −29.2 dB |

The errors are over 1–30 GHz. Most of the S21 error comes from small shifts of the sharp
notch. Below 25 GHz all runs agree with CST much more closely. See the
[results page](https://mohsen-alihosseini.github.io/OTTER/benchmark/results/).



## Repository layout

| Folder | Contents |
|---|---|
| `addon/` | The FreeCAD add-on (GUI + script generators for openEMS, EMerge and Palace) |
| `examples/pcb_to_chip/` | The benchmark: FreeCAD model and the working openEMS / EMerge / Palace setups. Each solver folder has a `README.md` (how to run, settings, result); openEMS and Palace also have a step-by-step `CHANGES.md` |
| `results/` | S-parameters of every run, the CST reference, plot scripts and the interactive comparison page |
| `docs/` | Source of the documentation website (MkDocs Material) |

## Quick start

### 1. Install the add-on in FreeCAD

```bash
git clone https://github.com/mohsen-alihosseini/OTTER
cd OTTER
MACRO_DIR=~/.local/share/FreeCAD/Macro      # FreeCAD: Macro → Macros… → "User macros location"
ln -s "$PWD/addon" "$MACRO_DIR/OTTER"
cp addon/OTTER.FCMacro "$MACRO_DIR/OTTER.FCMacro"
```

In FreeCAD: **Macro → Macros… → OTTER.FCMacro → Execute**.

OTTER is a standalone add-on: its modules have their own names (`OtterDialog`, `utilsOtter`, …),
so it can be installed next to the original FreeCAD-OpenEMS-Export without the two clashing.
The mesh recipe of each solver is set in **Grid Settings → Solver mesh (global)**.

### 2. Run the benchmark with any solver

```bash
# openEMS (FDTD), ~70 min on 16 cores, 0.35 GB RAM
cd examples/pcb_to_chip/openems && python3 PCB_Chip_openEMS.py

# EMerge (FEM), needs EMerge 3.0.0a6 (uses set_scale_factor(1000) for the micrometre chip parts)
cd examples/pcb_to_chip/emerge && SHOW_PLOTS=0 python3 emerge_simulation.py

# Palace (FEM, MPI), 30–60 min on 16 cores
cd examples/pcb_to_chip/palace && NP=16 bash run_all.sh
```

### 3. Compare

```bash
cd results
python3 build_comparison.py     # -> comparison_V3.0.html (interactive)
python3 plot_images.py          # -> per-solver PNGs in one common style
python3 plot_overview.py        # -> docs/images/overview_S11_S21.png
```

Installing the solvers and every setting are covered in the
[documentation](https://mohsen-alihosseini.github.io/OTTER/).


## License

OTTER is released under the **GNU General Public License v3.0 or later** (see `LICENSE`),
like [FreeCAD-OpenEMS-Export](https://github.com/LubomirJagos/FreeCAD-OpenEMS-Export), on which
it is based. The original add-on is © Lubomir Jagos and contributors.

