# Trying the Design flow toolbar

Files to try every command of the **Package Studio — Design flow** toolbar
on the SG13G2 LNA in a QFN-40. Everything here was made by
`make_example_files.py`; `../flow_walkthrough.py` runs all steps without
the GUI (`freecadcmd examples/flow_walkthrough.py`, about 1 min, out:
`examples/out/flow/`).

| File | For | What it is |
|---|---|---|
| `lna_top.sch` | Pin list & LVS | xschem top cell of the LNA — only its pins (RFIN, RFOUT, VCC, IDC, IHEAT, GNDHEAT, VTMP) |
| `lna_pinlist.csv` | Pin list & LVS | completes the .sch: the ground pads as one net GND, the RF pins on fixed leads 5/6/7 and 24/25/26 (ground-signal-ground), net names |
| `lna_pinlist_broken.csv` | Pin list & LVS | deliberately wrong — to see what the LVS reports |
| `i2c_gpio_expander.lef` | Pin list & LVS | illustrative LEF (pin names, USE POWER/GROUND/SIGNAL) for the IHP expander die |
| `lna_qfn40_board.kicad_pcb` | KiCad board | a KiCad board 30 x 20 mm: RO4350B 0.254 mm over a ground plane, FR-4, the QFN-40 footprint U1, 50-ohm GCPW to lead 6 and from lead 25, via fences, ground pours. Opens in KiCad 8/9 |
| `kicad/FlowExample.pretty`, `kicad/FlowExample.kicad_sym`, `kicad/QFN-40_6x6.step` | KiCad library | what *KiCad library* writes, for comparison |
| `touchstone/lna_pkg_sim.s2p` | Circuit model, Compare | **synthetic** "simulated" package path |
| `touchstone/lna_pkg_meas.s2p` | Compare | **synthetic** "measurement": the path with a 1.08 instead of 1.00 nH wire, in a 12 mm fixture, with noise |
| `touchstone/lna_2xthru.s2p` | Compare | **synthetic** 2x-thru of that fixture |

The Touchstone files in `touchstone/` are scikit-rf models, not an EM run
and not a measurement: they show the tools on a package-like case.
**Real, public files** — a measured microstrip line with its simulation,
IEEE P370 de-embedding data with an independent tool's result, and the
Verilog top of IHP's open i2c-gpio-expander — are in `public/`, with their
sources and licences in `public/README.md`.

## In FreeCAD, step by step

Start: a new document; **Import GDS chips** → `examples/data/ALL_LNA.gds`;
**Housing library** → JEDEC → QFN-40 6x6; select chip and housing →
**Place** (the chip onto the paddle).

1. **Pin list & LVS** (housing selected) → *Load pin list…* → select
   `lna_top.sch` **and** `lna_pinlist.csv` together (Ctrl+click; the CSV is
   read after the .sch and wins) → *Lay wires by net* → the list shows
   only "unused leads": the package matches the pin list, every wire is
   within MIL-STD-883. The RF pins are on leads 5/6/7 and 24/25/26.
2. Still in the panel: *Load pin list…* → `lna_pinlist_broken.csv` → only
   *Check (LVS)* (not *Lay*): VCC on the wrong lead, wired pads the list
   does not name, NOPAD open, RF leads without ground beside them.
   Load the two good files again before going on.
3. **Bonding diagram** → a folder → `QFN-40_6x6_bonding.svg`, `.pdf`, `_wires.csv`.
4. **KiCad library** → a folder → `PackageStudio.pretty/QFN-40_6x6.kicad_mod`,
   `PackageStudio.kicad_sym` (pins named RF_IN, VCC, …), `QFN-40_6x6.step`.
   In KiCad: Preferences → Manage Footprint / Symbol Libraries → add them.
5. **KiCad board** → `lna_qfn40_board.kicad_pcb` → empty region (whole
   board). Then select the package and the board's lands of U1 (or use
   **Place** onto the board): the package sits on its footprint. Again with
   region `85, 97, 99, 103`: only the RF input and the package's edge — the
   cut-out an EM model of the input needs; continue with **Ports** and
   **EM simulation**.
6. **Circuit model** → `touchstone/lna_pkg_sim.s2p` → `lna_pkg_sim.cir`
   (`.SUBCKT lna_pkg_sim p1 p2`) and `lna_pkg_sim.sym` beside it. In xschem:
   put the .sym in a symbol path, place it, connect p1/p2 — the netlist
   includes the subcircuit. With ngspice on the PATH the message gives the
   difference to the Touchstone file (≈ 0.1 dB).
7. **Compare with measurement** → measured `lna_pkg_meas.s2p` → simulated
   `lna_pkg_sim.s2p` → 2x-thru `lna_2xthru.s2p` → |S21| within 0.21 dB
   (pass), |S11| up to 2.6 dB off (fail): the "measured" wire is 8 % longer,
   and small reflections are sensitive to that — the reason
   `docs/plan/phase1_validation.md` judges |S21| and reports |S11|. The
   report (`validation/validation.md`, `.png`) lands beside the measured file.

With the public files:

8. The expander on its own, in its package:
   - *Import GDS* → `examples/data/i2c_gpio_expander_SG13G2Top.gds.gz`
     directly (unpacked beside it under that name). Unpacked with 7-Zip it
     is called `FMD_QNC_SG13G2Top.gds` — IHP's name inside the archive;
     the LVS then takes it as the only chip there and says so;
   - housing from the JEDEC catalogue: **QFN-20 4x4** — 20 pads, 20 leads,
     5 per side like the die's IO ring;
   - **Pin list & LVS** → *Load pin list…* → both
     `public/i2c_gpio_expander_SG13G2Top.v` (16 signal ports) and
     `public/i2c_gpio_expander_SG13G2Top_supply.csv` (the 4 supply pads the
     Verilog top has no ports for) → *Assign leads* → 20 wires,
     crossing-free, LVS clean, MIL-STD-883 passed, wires 1.3–1.5 mm.
9. **Compare with measurement** → measured `public/p370/se_fdf.s2p` →
   simulated `public/p370/deembedded_SE_NZC_se_fdf.s2p` → 2x-thru
   `public/p370/se_2xthru.s2p`: P370 de-embedding against an independent
   tool's result (they differ by the tools' side-2 convention; see
   `public/README.md`).
10. **Compare with measurement** → measured
    `public/msl_measured/P1-MSL_Thru_100-P2.s2p` → simulated
    `public/msl_simulated/P1-MSL_Thru_100-P2.s2p` → cancel the 2x-thru:
    a real VNA measurement against its simulation. Then **Circuit model** →
    the measured `P1-MSL_Stepped_140-P2.s2p` → empty band: refused (the
    line is too long for a lumped model) → again with `3`: fits.

For your own chip: the same steps with your GDS, your xschem top cell (or
the LEF of the digital flow) and a CSV for what they do not say — ground
pads, fixed leads, nets' names.

Needs, for steps 6 and 7: `scikit-rf` and `matplotlib` in FreeCAD's Python
(`<FreeCAD>/bin/python -m pip install scikit-rf matplotlib`); for the
check in step 6, `ngspice` on the PATH.
