# Changes, reasons and expected result

This version is based on the original Palace workflow for the same geometry
(`geometry/PCB_toChip_clean.step`). Materials, ports and solver settings are unchanged.
The changes are to the mesh and the run procedure.

---

## 1. Main change: finer mesh in the air inside the bondwire loops

**Change** (`scripts/mesh_palace.py`, new setting `M_LOOP = 25 µm`)
- A refinement box encloses all three bondwires: x −2.848 … −1.968 mm, y −0.715 … 0.716 mm,
  z up to 0.629 mm, plus a 20 µm margin.
- Inside the box the element size is at most 25 µm. Outside, the mesh coarsens over 300 µm
  (`T_LOOP`).
- Result: the air inside the loops is meshed at about 7 µm median (was ~90 µm, up to 300 µm).
  The mesh has 373 k tetrahedra (was 97 k).

**Reason**
- S11 and the S21 dip are set by the **inductance of the bondwire loops**:
  - S11 rises with frequency because of this series inductance.
  - The S21 dip is the resonance of the ground-bondwire inductance with the capacitance between the
    chip ground and the PAD.
- That inductance comes from the **magnetic field in the air inside the loops**, between the wires
  and the PAD or PCB line.
- In the previous meshes this air had 90–300 µm elements, so a loop only ~0.3 mm tall was covered by
  just 1–3 elements.
- A finite-element solution **underestimates inductance** when that region is too coarse, and
  approaches the true value from below as the mesh is refined. The previous runs gave:

  | | Equivalent series L (1–5 GHz) | S11 at 1 GHz | S21 dip |
  |---|---|---|---|
  | previous mesh v1 | 0.36 nH | −33.2 dB | 23 GHz |
  | previous mesh v3 | 0.44 nH | −31.4 dB | ≥ 30 GHz |
  | CST (reference) | 0.69 nH | −27.2 dB | 28.5 GHz |
  | openEMS (same structure) | 0.75 nH | −26.5 dB | 27.0 GHz |

- Earlier refinements improved the mesh *on* the metal surfaces (ports, bondwire footpoints, wire
  surface) but never the air *inside* the loops, which is why the dip kept moving.

## 2. Air margin to the absorbing boundary: 0.9 mm → 3 mm

**Change:** `MARGIN = 3000 µm` (was 900 µm).

**Reason:** the bondwires radiate at the top of the band. At 0.9 mm, the 2nd-order absorbing boundary
sat only about λ/11 away at 30 GHz, which disturbs the resonance. 3 mm is closer to the distance of
CST's PML. This mainly affects how deep and sharp the S21 dip is, less its frequency. The extra air
is meshed coarsely (600 µm), so it adds few elements.

## 3. Bondwire and footpoint mesh built into the script

**Change:** the defaults in `mesh_palace.py` are now:
- bondwire surface `M_WIRE` = 5 µm (was 15 µm),
- `CURV` = 20 elements per full circle (was 10),
- footpoint zone radius `R_FOOT` = 50 µm (was 40) and transition `T_FOOT` = 400 µm (was 90).

**Reason:** these values were previously set only in `run_all.sh`. The round wires are resolved more
accurately and the mesh around the wire landings is smoother, so there are fewer poor elements.
Having them in the script means running the mesher on its own gives the same mesh as `run_all.sh`.

## 4. Frequency range: 1–30 GHz

**Change:** `MinFreq` = 1.0 GHz (was 0.001 GHz) in `config/palace_config.json`. The sweep is 1–30 GHz
in 0.25 GHz steps, 117 points.

**Reason:** below ~1 GHz the structure is electrically tiny, and the FEM formulation becomes poorly
conditioned (low-frequency breakdown). The adaptive sweep would also have to span 4.5 decades, which
it is not designed for.

## 5. Run procedure (`run_all.sh`)

**Change:**
- Palace is started as the **binary** `palace-x86_64.bin` under `mpirun`, not through the `palace`
  wrapper.
- The run happens in `run/`.
- The number of processes (`NP`) and the Python interpreter (`PY`) can be chosen.
- The geometry-cleaning step is removed.

**Reason:**
- Under `mpirun`, the `palace` wrapper starts a separate one-process run on every MPI rank. These runs
  crash on each other's output files (`json parse_error … empty input`).
- The STEP file is already cleaned, and the input of the cleaning step is not part of this folder.

---

## Expected result

| | Previous Palace (v3) | Expected with this version | Reference (CST / openEMS) |
|---|---|---|---|
| Equivalent series L (1–5 GHz) | 0.44 nH | close to ~0.7 nH | 0.69 / 0.75 nH |
| S11 at 1 GHz | −31.4 dB | about −26 … −28 dB | −27.2 / −26.5 dB |
| S21 dip | ≥ 30 GHz | about 27–29 GHz | 28.5 / 27.0 GHz |

The run takes about 30–60 min on 16 cores. The result is `run/postpro/port-S.csv`.
