# Example: the SG13G2 LNA in a QFN-40 on its RF test board

`lna_qfn40_on_board.py` builds a complete assembly with the workbench's own
functions and leaves it ready for the EM simulation:

| | |
|---|---|
| Chip | `data/ALL_LNA.gds` (IHP SG13G2, 780 × 980 µm, 38 pads) |
| Housing | JEDEC open-cavity QFN-40 6 × 6 mm, chip on the paddle, 25 µm die attach |
| Wires | 11 × 25 µm AlSi1 wedge: GSG at RFIN (leads 5/6/7) and RFOUT (24/25/26), VCC 13, IDC 15, IHEAT 17, GNDHEAT 19, VTMP 36 |
| Board | `data/LNA_QFN40_JEDEC_RO4350B_copper_rf_cutout.step`: RF strip of the test board, 0.254 mm RO4350B, 50 Ω GCPW (w 0.44, s 0.20 mm), ground plane, via fences, 35 µm copper |
| Ports | Port1/2 at the board edges (line to ground plane), Port3/4 on the die across the TopMetal2 gap RFIN–GND14 and RFOUT–GND3; Port1 excited |
| Materials | RO4350B and copper on the board, PDK and housing library for the rest |
| EM model | the die as TopMetal2 only (pads), mesh *coarse*, smallest cell 5 µm: ~25 M openEMS cells, ~4 GB, 0.5–10 GHz |

The result is the 4-port S-matrix of connector → line → lead → wire → die
pad. Cascade it with the LNA's own S-parameters (measured on-wafer at the
same pads) in a circuit simulator.

## Run

In FreeCAD (Package Studio installed), Python console:

```python
exec(open(r"%APPDATA%\FreeCAD\v1-1\Mod\PackageStudio\examples\lna_qfn40_on_board.py".replace("%APPDATA%", __import__("os").environ["APPDATA"])).read())
```

or **Macro › Macros…**, choose the file, **Execute**. Takes about a minute.
It writes to `examples/out/`:

- `LNA_QFN40_on_RO4350B.FCStd` — the assembly; open it, then **EM simulation**
  to look at the model, change the solver, mesh or ports
- `LNA_QFN40_on_RO4350B_report.json` — what was checked (wire rules,
  readiness, OTTER's checks, materials, port contacts, mesh)
- `package/LNA_QFN40_on_RO4350B_EM_openEMS.zip` (GUI only) — the simulation
  package: unzip, `cd simulation`, run the script with openEMS' Python;
  S11, S21, S31, S41 go to `simulation_output/Sparameters.csv`

Headless (`freecadcmd examples/lna_qfn40_on_board.py`) does everything but the
package: OTTER's script generator needs the GUI.

## Expected findings

- *Port3/Port4 touch only ALL_LNA_TopMetal2* — the die's TopMetal2 is one
  part, so the check sees one conductor. The report's `die_port_pieces` shows
  each die port joins two separate solids of it (signal and ground pad): fine.
- The readiness note about 7,091 polygons concerns the default levels; the
  script builds TopMetal2 only.

## Run time

openEMS steps the whole grid at the time step of its smallest cell (5 µm: ~11 fs).
At ~100 M cells/s that is ~0.25 s per step; expect most of a day to reach −50 dB.
`set END_DB=-40` stops earlier, `set NO_VIEW=1` skips the 3-D preview.
The package sits 4 µm off centre in y on purpose (see `MESH_NUDGE_Y`): centred,
a die port edge lies 2 µm from the RF line's edge and that cell would halve the time step.

## Variations

```python
doc, report = build()
simulate(doc, solver="EMerge")                 # or "Palace"
simulate(doc, mesh="normal")                   # ~44 M cells, ~7 GB
simulate(doc, chip_levels=None)                # TopMetal1 + TopVia2 + TopMetal2: ~150 M cells
```

Another excited port: set `Excited` on the port object (Port3 to drive from the die).

The board's numbers and pin assignment come from the KiCad project
(`LNA_QFN40_JEDEC_RO4350B`, see its README).


# Example: an S-band receiver MCM in a 1U CubeSat

`cubesat_sband_mcm.py` goes the whole way, from two dies to the satellite:

| Level | What |
|---|---|
| Dies | `data/ALL_LNA.gds` (analog/RF) and `data/i2c_gpio_expander_SG13G2Top.gds.gz`: IHP-Open-DesignLib, tapeout Nov 2024 (MPW T576), an 8-bit I2C GPIO expander made with the OpenROAD flow, 1.05 × 1.05 mm, GPLv3 (`data/i2c_gpio_expander.COPYING.GPLv3`, from github.com/IHP-GmbH/TO_Nov2024) |
| MCM | both on the paddle of an open-cavity QFN-32 5x5, corner to corner, 0.3 mm apart; 29 wires to leads and one die-to-die wire (expander GPIO 0 → the LNA's heater input IHEAT); all 30 pass MIL-STD-883 TM 2010 |
| Board | the COM board, PC/104 size: MMCX → 50 Ω GCPW → RFIN; RFOUT → GCPW → transceiver (placeholder); 50 MHz oscillator; reset/I2C/interrupt to the CubeSat Kit bus; LDO, decoupling |
| System | a 1U CubeSat with EPS, battery and OBC stand-in boards; integration check against CDS Rev 14.1 |
| EM | the RF path cut out of the board (x −32…−13, y −17…−4 mm), four ports (two line ends, the LNA's RF pads), openEMS 1–4 GHz; ~24 M cells, ~4 GB |

Headless: `freecadcmd examples/cubesat_sband_mcm.py` (≈ 4 min; `PS_EXAMPLE_EM=0`
skips the EM part). Out: `out/cubesat/CubeSat_SBand_MCM.FCStd`,
`…_RF_EM.FCStd`, `…_report.json`.

What it is not: a flight design. The expander as taped out has no IOVDD/IOVSS
pads (IHP's known issues); the transceiver, EPS, battery and OBC are bodies
for mass, centre of gravity and clearances; supply planes are not drawn; the
PC/104 hole pattern is the common 5.08 mm inset, not checked against a
drawing; the structure is generic, not a vendor's.


# Trying the new design-flow commands

`flow/` holds files for every command of the Design flow toolbar on the LNA
in a QFN-40 (pin lists as xschem/CSV/LEF, a KiCad board with the footprint,
synthetic Touchstone files) — see `flow/README.md` for the steps in FreeCAD.
`flow_walkthrough.py` runs them all without the GUI:
`freecadcmd examples/flow_walkthrough.py`.


# From chip to a board that does something

`expander_board/` takes the IHP i2c-gpio-expander from its GDS into a
QFN-24 4x4, onto a 4-layer evaluation board designed in KiCad (schematic,
routed board, DRC clean: Qwiic I2C, 1.2 V LDO, 50 MHz oscillator, reset,
address jumpers, GPIO header and LEDs), back into FreeCAD, and cuts out the
clock path for an EM model — see `expander_board/README.md`.
