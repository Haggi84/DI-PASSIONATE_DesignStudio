# PCB-to-chip transition: openEMS (FDTD)

openEMS simulation of the OTTER benchmark: an FR4 board, three gold bondwires and the top
metal stack of an IHP SG13G2 chip, 100 Hz – 30 GHz. This is the version that matches CST
best: lossy bondwires, PCB copper and chip metals.

## Result

| | \|S11\| error vs CST, RMS / max (1–30 GHz) | S21 notch |
|---|---|---|
| **openEMS (this script)** | **0.12 / 0.34 dB** | 28.50 GHz, −24.9 dB |
| CST (reference) | – | 28.53 GHz, −28.2 dB |

From 1 to 25 GHz, S21 is within 0.07 dB of CST and S11 within 0.34 dB.

## Folder contents

| File | Purpose |
|---|---|
| `PCB_Chip_openEMS.py` | The working simulation script. Every change to the exported script is marked `# Step n`. |
| `PCB_Chip_openEMS_original.py` | The unchanged script as exported by the add-on, for comparison |
| `*_gen_model.stl` | Geometry exported from FreeCAD (mm). The board, chip layers and SiO2 are rebuilt as boxes from these files' bounding boxes; the bondwires are used as STL tubes. |
| `CHANGES.md` | The 13 steps from the exported script to a correct result, with the reason for each |

## Requirements

- openEMS and CSXCAD with their Python bindings. The benchmark ran with openEMS
  v0.37.0-rc1 and CSXCAD v0.7.0-rc1.
- Python 3 with `numpy` and `matplotlib`.
- Optional: AppCSXCAD, to look at the model and the mesh in 3D.

## How to run

```bash
cd examples/pcb_to_chip/openems
python3 PCB_Chip_openEMS.py
```

- Runtime: about **70 min on 16 cores**, about **0.35 GB RAM**. The run stops by itself at
  −50 dB, after about 211,000 timesteps of 2.72e-15 s.
- Every start **deletes `simulation_output/`**. Rename it first if you want to keep a result.
  Run only one copy at a time.
- Switches at the top of the script:
    - `setup_only = True`: builds the model and the mesh without running the FDTD. Open
      `simulation_output/PCB_Chip.xml` in AppCSXCAD to check them.
    - `debug_pec = True`: also writes openEMS's PEC and material maps
      (`PEC_dump.vtp`, `material_dump.vtr`) for ParaView.

## Output (`simulation_output/`)

| File | Contents |
|---|---|
| `Sparameters.csv` | **The result:** 100 Hz – 30 GHz, 129 points, S11 and S21 in dB and degrees, 50 Ω |
| `Sparameters.png` | Plot of \|S11\| and \|S21\| |
| `port_ut_1`, `port_it_1`, `port_ut_2`, `port_it_2` | Voltage and current signals at the ports; `Sparameters.csv` is calculated from them |
| `PCB_Chip.xml` | The complete model as openEMS used it (for AppCSXCAD) |
| `et`, `ht` | The excitation signal, for reference |

## Main settings

| Setting | Value | Step in `CHANGES.md` |
|---|---|---|
| Excitation | Gaussian pulse, f0 = fc = 16 GHz (DC – 32 GHz) | 2 |
| End criterion | `min_decrement = 1e-5` (−50 dB) | 1 |
| Mesh | Graded, 126 × 191 × 90 = 2.17 M cells: 1.5 µm at port 1, 4 µm along the feed line, 10 µm on the chip, 20 µm around the bondwires (`H_WIRE`), 50–120 µm on the board, ~0.5 mm in air; growth ≤ 1.3× per cell | 9 |
| Boundary | 8 PML cells on all sides, after 2.5 mm of clear air | 9 |
| Ports | Zero-thickness 50 Ω plates: port 1 at x = −2.9 mm (chip edge, Metal5 → TM2_1), excited; port 2 at x = −0.9 mm (board edge, ground → line) | 10 |
| Bondwires | Gold, `kappa` = 4.561e7 S/m | 6 |
| PCB line, ground, PAD | Copper, `kappa` = 5.8e7 S/m | 8 |
| Chip metals | TopMetal2 3.03e7, TopVia2 3.143e6, TopMetal1 2.78e7, TopVia1 2.191e6 S/m; Metal5 as a conducting sheet (2.319e7 S/m, 0.49 µm) | 5, 13 |
| Dielectrics | FR4 εr = 4.3; SiO2 εr = 4.1 (the two SiO2 bodies merged into one box) | 7 |

> [!NOTE]
> In CSXCAD, `kappa` is the electric conductivity and `sigma` the *magnetic* one. A metal
> defined with `sigma` carries no current (S11 = 0 dB). See Steps 6 and 8.

## Earlier variants

`results/OpenEMS/` also holds two earlier runs of this script with other metal settings
(same mesh and ports):

| File | Metals | S21 notch |
|---|---|---|
| `OpenEMS_Sparameters.csv` | All metals PEC | 27.00 GHz, −29.2 dB |
| `Sparameters_gold _copper.csv` | Lossy bondwires and PCB copper; chip metals PEC | 28.50 GHz, −25.3 dB |
| `Sparameters_All_Material.csv` | All metals lossy (this script) | 28.50 GHz, −24.9 dB |
