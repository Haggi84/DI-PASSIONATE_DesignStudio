# PCB_Chip_0926: from the exported script to a working simulation

This file lists, step by step, the changes applied to the fresh FreeCAD-OpenEMS export
`PCB_Chip_openEMS.py` (the unchanged export is kept as
`PCB_Chip_openEMS_original.py`). Each change is marked with the same step number (`# Step n`) in the code.

---

## Step 1: stop criterion

- **Change:** `min_decrement = 0.001` → `1e-5` (stop at −50 dB instead of −30 dB).
- **Reason:** the S-parameters are computed from the port signals with a Fourier transform. The
  signals must have died away when the simulation stops, otherwise the spectrum is cut off.

## Step 2: excitation

- **Change:** `f0 = 20 GHz, fc = 1 GHz` → `f0 = fc = 16 GHz`. The air cell size `max_res` is based on
  30 GHz (λ/20 ≈ 0.5 mm).
- **Reason:** the exported pulse only contains 19–21 GHz, so results at other frequencies would be
  noise. The new pulse covers DC to 32 GHz, enough for 100 Hz – 30 GHz results.

## Step 3: geometry as boxes

- **Change:** each chip metal (TM2_1…8), the PCB copper, FR4 and SiO2 are built as native CSXCAD
  **boxes** from their STL's bounding box (`stl_box()`), with coordinates rounded to 1 nm.
- **Reason:**
  - openEMS treats points on an STL's own surface as *outside* it. Thin STL layers then do not conduct
    sideways, and a port lying on a metal surface does not touch the metal.
  - A box includes its own surface, so it always conducts.
  - The 1 nm rounding puts zero-thickness sheets and ports exactly on mesh lines. STL values are
    float32, so −2.9 is stored as −2.9000000954.

## Step 4: priorities

- **Change:** SiO2 9700 (above almost all chip metals) → FR4 100, SiO2 200, metals 1000,
  bondwires 1100, ports 10000.
- **Reason:** metals must win over dielectrics where they share a face.

## Step 5: Metal5 and via stacks

- **Change:**
  - Metal5 (0.49 µm) becomes a zero-thickness sheet at its top face (its conductivity is set in
    Step 13).
  - Each ground via stack keeps its three layers (TopVia1, TopMetal1, TopVia2) as separate boxes with
    their own sizes (conductivities in Step 13).
- **Reason:**
  - Metal5 is port 1's ground. As a sheet it conducts sideways and is port 1's reference plane.
  - The sheet removes the 0.49 µm cell, which keeps the timestep about 4× larger. The via layers need no
    extra mesh lines, so they do not reduce the timestep.

## Step 6: bondwires as lossy gold

- **Change:** the gold material of the three bondwire STL tubes is defined with **`kappa=45610000`**
  (electric conductivity, S/m) instead of `sigma=45610000`:
  `SetMaterialProperty(epsilon=1, mue=1, kappa=45610000)`.
- **Reason:** in CSXCAD `kappa` is electric conductivity and `sigma` is **magnetic** conductivity.
  With `sigma` the bondwires carry no current, the chip is not connected to the PCB, and S11 = 0 dB.
  With `kappa` the bondwires are real lossy gold conductors, as in CST.

## Step 7: SiO2 merged

- **Change:** SIO2 and SIO2_2 become one box.
- **Reason:** they are the same material. SIO2_2 is only 0.2 µm thick and would otherwise force a
  0.2 µm cell.

## Step 8: PCB copper as lossy copper

- **Change:** COPPER (PCB line, GND, PAD) is defined with **`kappa=5.8e7`** (S/m) instead of
  `sigma=5.8e+07`: `SetMaterialProperty(epsilon=1, mue=1, kappa=5.8e7)`.
- **Reason:** same as Step 6. With `sigma` the whole PCB is not a conductor. With `kappa` it is lossy
  copper, as in CST.
- **Note:** lossy gold and copper (Steps 6 and 8) give the closest match to CST. Compared with ideal
  PEC conductors, the S21 resonance dip moves from 27.0 to 28.5 GHz (CST: 28.53 GHz). S21 is within
  0.05 dB of CST from 1 to 25 GHz and S11 within about 0.1–0.3 dB. Most of the shift comes from how
  openEMS maps a lossy material onto the grid: at a conductor surface it counts only about half, while
  a PEC box counts all of its surface edges. The wires, line and PAD are therefore slightly smaller
  electrically, so the loop inductance is slightly lower. The losses themselves are small (about
  0.5–1.5 % of the power).

## Step 9: mesh

- **Change:** the 25 generated "Fixed Distance" grid blocks are replaced by a graded mesh
  (`graded_lines()`):
  - Every geometry and port plane is a fixed mesh line.
  - Cells follow the local resolution and grow by at most 30% per cell: 1.5 µm at port 1, 4 µm along
    TM2_1, 10 µm on the chip, 20 µm along the bondwires (`H_WIRE`), 50–120 µm on the PCB, ~0.5 mm in
    air.
  - The domain is the structure plus 2.5 mm of air plus the 8 PML cells on every side.
  - Result: 126 × 191 × 90 = 2.17 M cells, timestep 2.85e-15 s.
- **Reason:** the exported mesh cannot give a valid result:
  - 206 M cells, far more RAM than a 31 GB machine has.
  - Sliver cells of ~1e-12 mm, which make the timestep collapse.
  - No mesh lines on the port, Metal5 or TM2 planes.
  - 2 mm air cells, so the 8 PML cells reach x = −2.894 and x = −0.917 and **both ports sit inside the
    absorbing boundary.**

### Step 9 in detail: exported mesh vs this mesh

**How the exported mesh is built** — 25 separate "Fixed Distance" blocks, one per object:
- Each block deletes the grid lines inside its bounding box and fills it with a uniform step:
  PCB substrate 3 µm, bondwires 5 µm, chip parts 1 µm, air 2 mm.
- The blocks are applied one after another, and later blocks overwrite earlier ones. The chip's SiO2
  block, applied last, replaces every other chip line in z with its own 1 µm grid.
- The fill uses `arangeWithEndpoint()`, which compares floating-point numbers with `==`, so the end
  line of a block is often silently dropped.
- There is no transition between blocks: a 1 µm block sits directly next to a 2 mm block.

**How this mesh is built** — one graded mesh from `graded_lines()`:
1. **Fixed lines:** every plane that matters is a mesh line: all layer faces (GND, FR4, PCB line,
   PAD, Metal5, TM2, SiO2 block) and the port plates and edges.
2. **Region sizes:** each region has a maximum cell size (table below).
3. **Grading:** between the fixed lines, cells follow the local size and grow by at most 30% from one
   cell to the next.
4. **Merging:** planes closer than a small fraction of the local cell size become one line, which
   prevents sliver cells (e.g. the TM2 edges at −2.86001 and −2.86000, 10 nm apart).
5. **Domain:** structure + 2.5 mm of air + the 8 PML cells, on every side.

**Cell sizes**

| Region | Exported | This mesh |
|---|---|---|
| Port 1 (chip edge) | 1 µm (SiO2 block) | **1.5 µm** |
| TM2_1 feed / Metal5 strip | 1 µm | **4 µm** |
| Rest of the chip (pads, SiO2) | 1 µm over the whole 120 × 300 µm chip | **10 µm** |
| Chip layers in z (0.289–0.303 mm) | 1 µm, **not aligned** with the layer faces | **1.5 µm, with a line on every layer face** |
| Bondwires | 5 µm over each wire's bounding box | **20 µm** corridor around the wires |
| PCB substrate / board | **3 µm** over the whole 2 × 3.6 mm board | 50 µm across the line, 100 µm along it, 120 µm on the board |
| Copper thickness (35 µm) | 3 µm | 17.5 µm (2 cells) / 35 µm (GND) |
| Air | **2 mm** | **~0.5 mm** (λ/20 at 30 GHz) |
| Air margin | airbox −6.5…3.5 mm in x: 3.6 mm left of the chip, 4.4 mm right of the board | 2.5 mm clear + 8 PML cells (~6.5 mm) |

**Result**

| | Exported mesh | This mesh |
|---|---|---|
| Size | 753 × 1405 × 195 = **206 M cells** | 126 × 191 × 90 = **2.17 M cells** |
| Smallest cell | ~1e-12 mm (duplicate lines) | 1.33 µm |
| Largest jump between neighbouring cells | up to 655× | ≤ 1.36× |
| Port 1 / port 2 | **inside the PML** (it reached x = −2.894 and −0.917) | 2.5 mm of clear air between the ports and the PML |
| Metal5 / TM2 / port planes on mesh lines | **none** (0.29409, 0.29458, 0.30023, 0.30323 and x = −0.9 missing) | **all** |
| Timestep | would collapse towards 0 | 2.85e-15 s (2.72e-15 s with the Metal5 conducting sheet) |
| Runtime / RAM | not runnable (≫ 31 GB RAM) | ~70 min, ~0.35 GB |

**What this mesh relies on**
- **The geometry as boxes (Step 3).** A box includes its own surface, so thin layers conduct with only
  their surface planes as mesh lines. No sub-micron cells are needed apart from the port.
- **The bondwire corridor** is taken from the vertices of the three STL tubes, plus a 20 µm margin. At
  20 µm (`H_WIRE`) the 30 µm tubes stay connected. Do not make it coarser.
- **Zero-thickness ports (Step 10)**, so there are no 1 µm / 0.5 µm cells from thin port boxes.

In short: the exported mesh was very fine where it did not matter (3 µm across the whole board), too
coarse where it did (2 mm air, PML over the ports), and misaligned with the layers. This mesh is fine
only where the physics needs it, grades smoothly, and puts every metal and port plane exactly on a
mesh line. It is about 100× smaller, and it runs.

## Step 10: ports

- **Change:** the 1 µm / 0.5 µm thin port boxes become zero-thickness 50 Ω plates:
  - port 1 at **x = −2.9** (chip edge, between Metal5 and TM2_1),
  - port 2 at **x = −0.9** (board edge, between GND and the PCB line),
  - at the same y and z as exported.
- **Reason:**
  - The thin boxes would force sub-micron cells (about 2× smaller timestep).
  - openEMS widens the current probe of a zero-thickness port around the plate by itself.

## Step 11: run call

- **Change:** `FDTD.Run(...)` uses all CPU cores (`engine='multithreaded'`, `numThreads=os.cpu_count()`),
  and debug flags are passed only when `True`.
- **Reason:** speed. Also, in this openEMS version a `False` flag becomes an empty command-line token
  that can break the options after it.

## Step 12: S-parameter output

- **Change:** post-processing is added after the run (the export has none):
  - `simulation_output/Sparameters.csv`: 100 Hz – 30 GHz, 129 points (10 per decade up to 1 GHz, then
    0.5 GHz steps), frequency in Hz, S11/S21 in dB and degrees, 50 Ω reference.
  - `simulation_output/Sparameters.png`: S11 and S21, linear GHz axis, −40…+2 dB.
- **Reason:** the exported script produces no results. The pulse from Step 2 already covers 100 Hz, so
  the low-frequency points need no longer simulation.

## Step 13: chip-metal conductivities (IHP SG13G2 stack-up from CST)

- **Change:** the chip metals, which were ideal PEC, get the conductivities of the CST stack-up:

  | Chip part | Model | Conductivity (S/m) |
  |---|---|---|
  | TopMetal2 (TM2_1…8: feed line, signal and ground pads) | lossy boxes (`kappa`) | 3.03e7 |
  | TopVia2 | lossy boxes | 3.143e6 |
  | TopMetal1 | lossy boxes | 2.78e7 |
  | TopVia1 | lossy boxes | 2.191e6 |
  | Metal5 | **conducting sheet** (`CSX.AddConductingSheet`, thickness 0.49 µm) | 2.319e7 |

  Metal5 stays a zero-thickness sheet. The openEMS conducting-sheet model includes its real thickness
  and conductivity without needing sub-micron cells.
- **Reason:** to use the same chip materials as CST. The stack-up also confirms the model: all layer
  heights match exactly (Metal5 5.09–5.58 µm, TopMetal2 11.23–14.23 µm above the chip bottom), SiO2 is
  εr = 4.1, and the silicon substrate is covered by a ground layer at the chip bottom, which is the
  copper PAD in this model.
- **Effect:** small. The metals now absorb about 1 % of the power (0.7–2 %). S21 drops by only
  0.01–0.03 dB, and the S21 dip stays at 28.5 GHz, slightly shallower. Compared with CST from 1 to
  25 GHz, S21 is within 0.07 dB and S11 within 0.34 dB. The chip metals are thin and short; the result
  is dominated by the bondwires and PCB copper (Steps 6 and 8).

  | | S21 dip | max. difference from CST, 1–25 GHz |
  |---|---|---|
  | all metals PEC | 27.00 GHz, −25.9 dB | — |
  | lossy gold + copper, chip PEC | 28.50 GHz, −25.3 dB | S11 0.40 dB, S21 0.08 dB |
  | **+ chip metals from the stack-up (this version)** | **28.50 GHz, −24.9 dB** | **S11 0.34 dB, S21 0.07 dB** |
  | CST | 28.53 GHz, −28.2 dB | — |

---

## How to run

```bash
cd examples/pcb_to_chip/openems
python3 PCB_Chip_openEMS.py
```

- It takes about 70 min on 16 cores and about 0.35 GB of RAM (stops at −50 dB after about 211,000
  timesteps; timestep 2.72e-15 s).
- It stops by itself at −50 dB and writes `Sparameters.csv` and `Sparameters.png` to
  `simulation_output/`.
- Run only one copy at a time: every start clears `simulation_output/`. Stop it with Ctrl+C.

## Output folders

> **In the OTTER repository** the raw output folders are not included (about 3 MB each). Their
> `Sparameters.csv` files are in `results/OpenEMS/`: `OpenEMS_Sparameters.csv` (PEC),
> `Sparameters_gold _copper.csv` (lossy gold and copper) and `Sparameters_All_Material.csv` (current version).

The original run folder held three result folders. They are the same simulation
with three different metal settings; the mesh, ports and everything else are identical.

| Folder | Metals in that run | S21 dip | Difference from CST (1–25 GHz) |
|---|---|---|---|
| `simulation_output_PEC/` | **All metals ideal (PEC):** bondwires, PCB copper and chip metals | 27.00 GHz, −25.9 dB | S21 up to about 0.6 dB, S11 about 1 dB |
| `simulation_output_lossy_gold_copper/` | **Lossy bondwires** (gold, 4.561e7 S/m) and **PCB copper** (5.8e7 S/m); chip metals still PEC (Steps 6 and 8) | 28.50 GHz, −25.3 dB | S21 0.08 dB, S11 0.40 dB |
| `simulation_output/` | **Current version:** lossy gold and copper **plus chip metals from the CST stack-up** (Step 13) | 28.50 GHz, −24.9 dB | **S21 0.07 dB, S11 0.34 dB**, the best match |

Each folder contains the same files:

| File | Contents |
|---|---|
| `Sparameters.csv` | **The result:** 100 Hz – 30 GHz, 129 points, S11/S21 in dB and degrees |
| `Sparameters.png` | Plot of S11 and S21 |
| `port_ut_1`, `port_it_1`, `port_ut_2`, `port_it_2` | Raw time signals from openEMS: voltage (`ut`) and current (`it`) at port 1 and port 2. `Sparameters.csv` is calculated from these. |
| `PCB_Chip.xml` | The complete model (geometry, materials, mesh, ports) as openEMS used it; it can be opened in AppCSXCAD to view the model in 3D |
| `et`, `ht` | The excitation signal openEMS generated (electric and magnetic parts), for reference only |

Only `simulation_output/` is rewritten when the script runs again. The other two are saved copies of
earlier stages, kept for comparison. Rename `simulation_output/` first if you want to keep a result.



## Material from CST:
```bash
<Stackup schemaVersion="2.0">
<Materials>
<Material Name="Activ" Type="Conductor" Permittivity="1" DielectricLossTangent="0" Conductivity="357141.0" Color="00ff00"/>
<Material Name="Metal1" Type="Conductor" Permittivity="1" DielectricLossTangent="0" Conductivity="21640000.0" Color="39bfff"/>
<Material Name="Metal2" Type="Conductor" Permittivity="1" DielectricLossTangent="0" Conductivity="23190000.0" Color="ccccd9"/>
<Material Name="Metal3" Type="Conductor" Permittivity="1" DielectricLossTangent="0" Conductivity="23190000.0" Color="d80000"/>
<Material Name="Metal4" Type="Conductor" Permittivity="1" DielectricLossTangent="0" Conductivity="23190000.0" Color="93e837"/>
<Material Name="Metal5" Type="Conductor" Permittivity="1" DielectricLossTangent="0" Conductivity="23190000.0" Color="dcd146"/>
<Material Name="TopMetal1" Type="Conductor" Permittivity="1" DielectricLossTangent="0" Conductivity="27800000.0" Color="ffe6bf"/>
<Material Name="TopMetal2" Type="Conductor" Permittivity="1" DielectricLossTangent="0" Conductivity="30300000.0" Color="ff8000"/>
<Material Name="TopVia2" Type="Conductor" Permittivity="1" DielectricLossTangent="0" Conductivity="3143000.0" Color="ff8000"/>
<Material Name="TopVia1" Type="Conductor" Permittivity="1" DielectricLossTangent="0" Conductivity="2191000.0" Color="ffe6bf"/>
<Material Name="Via4" Type="Conductor" Permittivity="1" DielectricLossTangent="0" Conductivity="1660000.0" Color="deac5e"/>
<Material Name="Via3" Type="Conductor" Permittivity="1" DielectricLossTangent="0" Conductivity="1660000.0" Color="9ba940"/>
<Material Name="Via2" Type="Conductor" Permittivity="1" DielectricLossTangent="0" Conductivity="1660000.0" Color="ff3736"/>
<Material Name="Via1" Type="Conductor" Permittivity="1" DielectricLossTangent="0" Conductivity="1660000.0" Color="ccccff"/>
<Material Name="Cont" Type="Conductor" Permittivity="1" DielectricLossTangent="0" Conductivity="2390000.0" Color="00ffff"/>
<Material Name="Passive" Type="Dielectric" Permittivity="6.6" DielectricLossTangent="0.0" Conductivity="0" Color="a0a0f0"/>
<Material Name="SiO2" Type="Dielectric" Permittivity="4.1" DielectricLossTangent="0.0" Conductivity="0" Color="fffcad"/>
<Material Name="Substrate" Type="Semiconductor" Permittivity="11.9" DielectricLossTangent="0" Conductivity="2.0" Color="01e0ff"/>
<Material Name="EPI" Type="Semiconductor" Permittivity="11.9" DielectricLossTangent="0" Conductivity="5.0" Color="294fff"/>
<Material Name="AIR" Type="Dielectric" Permittivity="1.0" DielectricLossTangent="0.0" Conductivity="0" Color="d0d0d0"/>
<Material Name="Vmim" Type="Conductor" Permittivity="1" DielectricLossTangent="0" Conductivity="2191000.0" Color="ffe6bf"/>
<Material Name="MIM" Type="Conductor" Permittivity="1" DielectricLossTangent="0" Conductivity="500000.0" Color="e6ffbf"/>
<Material Name="LOWLOSS" Type="Conductor" Permittivity="1" DielectricLossTangent="0" Conductivity="1E10" Color="ff0000"/>
</Materials>
<ELayers LengthUnit="um">
<Dielectrics>
<Dielectric Name="AIR" Material="AIR" Thickness="200.0000"/>
<Dielectric Name="Passive" Material="Passive" Thickness="0.4000"/>
<Dielectric Name="SiO2" Material="SiO2" Thickness="15.7303"/>
<Dielectric Name="EPI" Material="EPI" Thickness="3.7500"/>
<Dielectric Name="Substrate" Material="Substrate" Thickness="180.0000"/>
</Dielectrics>
<Layers>
<Substrate Offset="183.75"/>
<Layer Name="Activ" Type="conductor" Zmin="0.0000" Zmax="0.4000" Material="Activ" Layer="1"/>
<Layer Name="Metal1" Type="conductor" Zmin="1.0400" Zmax="1.4600" Material="Metal1" Layer="8"/>
<Layer Name="Metal2" Type="conductor" Zmin="2.0000" Zmax="2.4900" Material="Metal2" Layer="10"/>
<Layer Name="Metal3" Type="conductor" Zmin="3.0300" Zmax="3.5200" Material="Metal3" Layer="30"/>
<Layer Name="Metal4" Type="conductor" Zmin="4.0600" Zmax="4.5500" Material="Metal4" Layer="50"/>
<Layer Name="Metal5" Type="conductor" Zmin="5.0900" Zmax="5.5800" Material="Metal5" Layer="67"/>
<Layer Name="TopMetal1" Type="conductor" Zmin="6.4303" Zmax="8.4303" Material="TopMetal1" Layer="126"/>
<Layer Name="TopMetal2" Type="conductor" Zmin="11.2303" Zmax="14.2303" Material="TopMetal2" Layer="134"/>
<Layer Name="TopVia2" Type="via" Zmin="8.4303" Zmax="11.2303" Material="TopVia2" Layer="133"/>
<Layer Name="TopVia1" Type="via" Zmin="5.5800" Zmax="6.4303" Material="TopVia1" Layer="125"/>
<Layer Name="Via4" Type="via" Zmin="4.5500" Zmax="5.0900" Material="Via4" Layer="66"/>
<Layer Name="Via3" Type="via" Zmin="3.5200" Zmax="4.0600" Material="Via3" Layer="49"/>
<Layer Name="Via2" Type="via" Zmin="2.4900" Zmax="3.0300" Material="Via2" Layer="29"/>
<Layer Name="Via1" Type="via" Zmin="1.4600" Zmax="2.0000" Material="Via1" Layer="19"/>
<Layer Name="Cont" Type="via" Zmin="0.4000" Zmax="1.0400" Material="Cont" Layer="6"/>
<Layer Name="SUBGND" Type="conductor" Zmin="-3.75" Zmax="0" Material="LOWLOSS" Layer="250"/>
<Layer Name="BACKSIDEGND" Type="conductor" Zmin="-190" Zmax="-183.75" Material="LOWLOSS" Layer="251"/>
<Layer Name="MIM" Type="conductor" Zmin="5.6043" Zmax="5.7540" Material="MIM" Layer="36"/>
<Layer Name="Vmim" Type="via" Zmin="5.7540" Zmax="6.4303" Material="Vmim" Layer="129"/>
<Layer Name="LBE" Type="dielectric" Zmin="-183.75" Zmax="0" Material="AIR" Layer="157"/>
</Layers>
</ELayers>
</Stackup>
```
