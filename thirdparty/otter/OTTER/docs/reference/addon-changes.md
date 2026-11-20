# Changes to the add-on

OTTER's `addon/` is based on
[FreeCAD-OpenEMS-Export](https://github.com/LubomirJagos42/FreeCAD-OpenEMS-Export)
(version 0.5.0) by Lubomir Jagos. Most changes are in the script generators in
`addon/utilsOtter/ScriptLinesGenerator/`; the GUI has a new *Solver mesh (global)* tab.

## Standalone add-on

OTTER is installed and runs as its own add-on, next to the original if you have it:

| Original | OTTER |
|---|---|
| `FreeCAD-OpenEMS-Export.FCMacro`, add-on folder `FreeCAD-OpenEMS-Export` | `OTTER.FCMacro`, add-on folder `OTTER` |
| `ExportOpenEMSDialog.py` (class `ExportOpenEMSDialog`) | `OtterDialog.py` (class `OtterDialog`) |
| `KiCADImporterToolDialog.py`, `MaterialConstantsCatalogDialog.py` | `OtterKiCADImporterToolDialog.py`, `OtterMaterialConstantsCatalogDialog.py` |
| packages `utilsOpenEMS`, `utils3rdParty` | `utilsOtter`, `utilsOtter3rdParty` |

Python caches imported modules by name for the whole FreeCAD session, so with the old names
whichever add-on was opened first would have supplied its modules to the other. Settings
files (`.ini`) stay compatible in both directions; the new mesh settings are stored in an
extra `[MESHSETTINGS-solver]` group, which the original add-on ignores.

## GUI: Grid Settings → Solver mesh (global)

The mesh parameters that were fixed in the generated scripts are now inputs, saved with the
settings file. Only the group of the selected solver is shown; see the
[add-on guide](../user-guide/freecad-addon.md#4-grid-openems-or-mesh-sizes-emerge-palace)
for the full list. Inputs are enabled only where the selected solver uses them (*FEM grid*:
EMerge uses only *sizeMin* of the surface mesh size; *distance (for points)* is unused).

| File | Change |
|---|---|
| `GuiHelpers/SolverMeshSettings.py` (new) | Names, types and defaults of all solver mesh settings, shared by GUI, `.ini` and generators |
| `ui/dialog_OpenEMS_Simulation_Creator.ui` | *Solver mesh (global)* sub-tab in *Grid Settings* |
| `OtterDialog.py` | `updateSolverMeshSettingsUi()`: per-solver visibility, dependent inputs, *Fixed distance* label for graded mesh |
| `SaveLoad/IniFile0v1.py` | Saves/loads the settings; older files load with defaults; files without a boundary *surfaceImpedance* entry (like `examples/pcb_to_chip/model/setting.ini`) load again |

## Script generators

| File | Change |
|---|---|
| `CommonScriptLinesGenerator.py` | Export warnings shared by all solvers; `SimpleBoundBox` for ports collapsed to planes |
| `PythonScriptLinesGenerator2_openems.py` | openEMS fixes below |
| `GeneratedScriptHelpers_openems.py` (new) | Mesh helpers copied into every openEMS script; plain Python, so they can be tested without FreeCAD |
| `PythonScriptLinesGenerator3_emerge.py` | EMerge fixes below |
| `PythonScriptLinesGenerator4_palace.py` | Palace export rewritten around the new mesher |
| `GeneratedScriptHelpers_palace.py` (new) | gmsh mesher for Palace, copied into every Palace script |

## openEMS

| Change | Finding |
|---|---|
| Conductivity entered only as *Sigma* is written as `kappa` (electric) instead of `sigma` (magnetic) | [Steps 6, 8](../findings/openems.md#step-6-bondwires-as-lossy-gold) |
| **Graded mesh** (`mesh_graded = True`): *Fixed Distance* settings become target cell sizes; all geometry and port planes are mesh lines; planes closer than 0.5 × local cell are merged; growth ≤ 1.3×; max λ/20 (all set in the GUI) | [Step 9](../findings/openems.md#step-9-mesh) |
| A face shared by two bodies of the same material over their whole cross-section is not a mesh line | [Step 7](../findings/openems.md#step-7-sio2-merged) |
| λ/4 of clear air plus the PML cells added outside the structure; check that no port lies in the PML | Step 9 |
| `arangeWithEndpoint()` no longer drops the last mesh line (float `==` comparison) | Step 9 |
| Thin lumped-port boxes become zero-thickness plates on the face that touches the geometry | [Step 10](../findings/openems.md#step-10-ports) |
| A thin object with *conducting sheet* material becomes one sheet (no cells as thin as the layer) | [Steps 5, 13](../findings/openems.md#step-13-chip-metal-conductivities-ihp-sg13g2-stack-up-from-cst) |
| Warning when a dielectric has higher priority than a conductor it touches | [Step 4](../findings/openems.md#step-4-priorities) |
| Warning when the Gaussian pulse does not cover the band; advice `f0 = fc = fmax/2` | [Step 2](../findings/openems.md#step-2-excitation) |
| Warning when `min_decrement` stops before the port signals have decayed | [Step 1](../findings/openems.md#step-1-stop-criterion) |
| Run on all cores (`engine='multithreaded'`) | [Step 11](../findings/openems.md#step-11-run-call) |
| S-parameters written to `simulation_output/Sparameters.csv` and `.png` | [Step 12](../findings/openems.md#step-12-s-parameter-output) |
| **Smallest cell** per direction (`minCell_um`, `minCellZ_um`, env `MIN_CELL_UM` / `MIN_CELL_Z_UM`): lines thinned after finalization, conductor faces and sheet planes first, sheets closer than the cell snapped onto one plane, conductors thinner than a cell stretched to their enclosing cell (`applyMinCell()`) | [QFN package](../findings/qfn-package.md#openems) |
| Flat STL bodies become PEC polygons (`addSTL()`), openEMS never uses a zero-volume polyhedron | [QFN package](../findings/qfn-package.md#openems) |
| Conductors raised above all dielectrics and lumped ports above all conductors at run time (`liftConductorPriorities()`) | [QFN package](../findings/qfn-package.md#openems) |
| Bodies on the structure bounds in x/y continue into the PML, not at port faces (`structureIntoPml`, `extendIntoPml()`) | [QFN package](../findings/qfn-package.md#openems) |
| Warning: lumped port at the structure end facing a PMC boundary | [QFN package](../findings/qfn-package.md#openems) |
| Environment overrides: `END_DB`, `OPENEMS_THREADS`, `SETUP_ONLY`, `DEBUG_PEC`, `NO_VIEW` (viewer also only with a display) | [QFN package](../findings/qfn-package.md#openems) |

## EMerge

| Change |
|---|
| Thin lumped-port boxes become plates; the port face mesh is refined to N elements across the port (GUI, default 5) |
| **Geometry scale factor** (`set_scale_factor`, EMerge 3.0): geometry and mesh sizes in scaled units, lumped-port width/height and surface roughness/thickness in real metres; warning when parts are below 10 µm and the factor is 1 |
| Curved boundary meshing and mesher safe mode from the GUI |
| Warning when the absorbing boundary is closer than λ/4 at fmax |
| S-parameters written to `simulation_output/Sparameters.csv` and `.png` after `run_sweep()` |
| Frequency groups run each in a fresh process of the script (`EMERGE_FGROUP`) and are merged into `Sparameters.csv`: EMerge keeps every solved frequency in memory until the process ends |
| Model view only with a display, env `NO_VIEW=1`; no lossy-metal warning (EMerge 3.0 makes metal volumes a surface impedance) |
| Material opacity is clamped to the range 0–1 |

## Palace

| Change | Finding |
|---|---|
| New self-contained gmsh mesher: one fragment pass, metals as holes, only faces that touch a meshed volume kept, port nodes snapped, `MeshSizeExtendFromBoundary = 0` | [Palace findings](../findings/palace.md), [user guide](../user-guide/palace.md#how-otter-meshes-for-palace) |
| Lossy metals (≥ 1e5 S/m) and conducting sheets become *Conductivity* boundaries (driven problems) | – |
| Mesh always in mm with `L0 = 1e-3`; warning if the GUI unit differs | Palace README |
| `run_palace.sh`: mesh, then the Palace **binary** under `mpirun` (`NP`, `PY`, `PALACE_BIN`) | Palace V5, item 5 |
| Warnings: sweep starting far below fmax (low-frequency breakdown), absorbing boundary closer than λ/4, unsupported port or boundary types | Palace V5, items 2 and 4 |

!!! note "What still needs the user"
    - openEMS Step 3: an object that fills its bounding box is written as a CSXCAD box;
      other bodies stay STL. Draw thin layers as boxes in FreeCAD.
    - Palace loop-air refinement needs a *FEM Max Size* grid on the bondwires (the loop size
      is *loop factor* × wire size); OTTER warns when wire-like metals have no size.
    - EMerge: loop-air refinement is not automated yet; add a fine mesh size on an air object
      around the bondwires.