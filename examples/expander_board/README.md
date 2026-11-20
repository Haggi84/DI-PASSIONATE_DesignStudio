# Expander evaluation board — chip, package, board, EM

The IHP i2c-gpio-expander (SG13G2, open MPW TO_Nov2024) from die to a
board that does something, back into FreeCAD, and on to an EM model.

## What the board does

It makes the expander usable from any 3.3 V I2C controller (Raspberry Pi,
microcontroller, Qwiic / STEMMA QT boards; daisy-chained over J1/J2):

| Block | Parts | Why |
|---|---|---|
| Supply | J1/J2 3.3 V → U2 AP2112K-1.2 → R2 0 Ω → U1 VDD (leads 2, 13) | the core takes 1.1–1.2 V; R2 lifted = an ammeter for the core current |
| Decoupling | C4/C5 100 nF at each VDD lead, C6 1 µF, C2/C3 1 µF at the LDO, C1 10 µF | |
| Clock | X1 50 MHz CMOS oscillator (Abracon ASE, 3.3 V) → R1 33 Ω at the source → lead 7 | the design is synchronous at 50 MHz from an external clock (IHP); the one fast net |
| Reset | R6 10 k / C8 100 nF (~1 ms), SW1, J4 | IHP: hold reset ≥ 1 µs while the core supply settles |
| I2C | R3/R4 2.2 kΩ (400 kHz fast mode), R5 10 k on INT | the pads are open drain, pull-ups needed (IHP) |
| Address | R7–R9 10 k pull-downs, JP1–JP3 to 3.3 V | default 0b000 |
| GPIO | J3 2x5 header (GPIO0–7, 3.3 V, GND), D1–D8 with 1 kΩ | |
| Test | TP1 CLK, TP2 +1V2, TP3 +3V3, TP4 GND | |

4 layers, 45 x 32 mm, 1.6 mm FR-4: F.Cu signals over In1 = GND, In2 = 3.3 V
(both layer type "power plane": no tracks on them), B.Cu, ground pours on
both outer layers. Every SMD pad of GND and 3.3 V reaches its plane through
a short track and a via (fan-out in `route_board.py`).

**Open point — read this first.** The die has no IOVDD/IOVSS pads (IHP's
`doc/source/known-issues.rst`): the IO ring has no supply pad of its own.
Whether the IOs work at all, and at which levels, is not known. The board
is built as the chip was meant to be used (3.3 V IO); it is also the test
fixture to find out — TP1–TP4, R2 for the core current, J4 for reset/INT.

The package: IHP packages its open tapeouts in QFN or open QFN with 24 to
64 pins, so a **QFN-24 4x4**, not a QFN-20: 20 wires, 4 leads unused.

## Files

| File | What |
|---|---|
| `expander_pinlist.csv` | the pin list with the board's net names (signal pads by GDS name, supply pads by their GDS labels) |
| `make_package.py` | step 1 (FreeCAD): die in QFN-24, wires by the pin list, LVS, MIL-STD-883, KiCad library, bonding diagram |
| `make_board.py` | step 2 (KiCad's Python): schematic, project, placed board |
| `route_board.py` | step 3 (KiCad's Python + Freerouting): routing, ground pours |
| `check_netlist.py` | the schematic's netlist against the design, ERC-like checks |
| `make_assembly.py` | step 4 (FreeCAD): the board back in, package on U1, the clock path cut out |
| `kicad/` | **the KiCad project** — open `expander_eval.kicad_pro` |
| `doc/` | schematic PDF, board picture, DRC report |

`kicad/` is written by KiCad 7 (schematic, board) and by Package Studio
(`ExpanderEval.*`, KiCad 8 format): open it with **KiCad 8 or 9**, which
converts the KiCad 7 files when you save.

## Checked

- Package: 20 wires, LVS 0 errors, MIL-STD-883 TM 2010 0 rejects, wires 1.42–1.80 mm.
- Schematic: its netlist (kicad-cli) equals the design — 29 nets, 128 pins;
  every power input driven, no two outputs on a net, no single-pin net.
  KiCad's own ERC was not run here (KiCad 7's kicad-cli has none): run it.
- Board: fully routed (0 unconnected); DRC 0 errors, 32 warnings (silkscreen
  overlaps — tidy the reference texts before ordering). KiCad's own ERC and
  DRC run in the KiCad GUI: 0 errors each (the one ERC warning is KiCad 7
  not reading the KiCad 8 library).
- FreeCAD: the board reads back with all 29 nets, the package lands with
  lead 1 on pad 1.

Found on the way and fixed in Package Studio:
- the exported QFN-24 4x4 footprint had touching corner lands (pads 6/7 =
  GPIO5/CLK shorted): `pcb.qfn_lands` keeps 0.2 mm between corner lands;
- KiCad zone fills with holes (one outline with slits) came in with the
  holes misread — the In1 ground under U1 was missing: `kicad_board._rings`;
- the EM panel built the model with OTTER's default 1 µm cells and ran out
  of memory on a board cut-out: it now takes the panel's mesh setting;
- the generated openEMS script's graded-mesh helper took > 7 GB for ~700
  mesh regions: Package Studio evaluates it in chunks (same lines, 0.45 GB).

## Steps

In FreeCAD (Package Studio):

1. *Import GDS* → `examples/data/i2c_gpio_expander_SG13G2Top.gds.gz`.
2. Housing from the JEDEC catalogue: **QFN-24 4x4**; *Place* the chip on the paddle.
3. *Pin list & LVS* → *Load pin list…* → `expander_pinlist.csv` → *Lay wires by net* → *Check (LVS)*.
4. *Bonding diagram* → a folder (for the assembly house).
5. *KiCad library* → a folder: footprint, symbol, STEP. (Already in `kicad/`.)

In KiCad 8/9:

6. Open `kicad/expander_eval.kicad_pro`. The project's library tables
   point at `ExpanderEval.*` in the project folder.
7. Schematic: *Inspect → Electrical Rules Checker*. Change what you need
   (another oscillator, other connectors), then *Tools → Update PCB from
   Schematic*.
8. Board: check, tidy the silkscreen, *Inspect → Design Rules Checker*,
   refill zones (B). Re-route if you moved things (interactive router, or
   Freerouting as in `route_board.py`).
9. Save.

Back in FreeCAD:

10. *KiCad board* → `expander_eval.kicad_pcb`, region empty → the whole
    board. Select the package and U1's lands → *Place*.
11. *KiCad board* again, region **`113, 110, 126.5, 125`** (KiCad mm): the
    clock path X1 → R1 → U1 with the ground around it. Place the package on U1.
12. *Ports* and 13. *EM simulation*: step by step, with screenshots, in the
    guide "Anleitung: Expander-Board in KiCad und Package Studio bis zur
    EM-Simulation" (a Claude doc). In short: four ports — X1's output and
    U1's leads 7 and 8 standing from the land down to In1, R1 as a 33-ohm
    lumped port that is not excited —; chip metal "No metal", 0.01–3 GHz,
    mesh coarse / 50 µm set before the build; *Make simulation package*:
    5.15 M cells, ~0.8 GB. `make_assembly.py` sets the same ports.

Scripted, the same:

    freecadcmd examples/expander_board/make_package.py
    python3   examples/expander_board/make_board.py         # KiCad's Python
    python3   examples/expander_board/route_board.py freerouting.jar
    freecadcmd examples/expander_board/make_assembly.py

`make_board.py` writes the board unrouted: run `route_board.py` after it.

Sources: IHP-GmbH/TO_Nov2024 `i2c-gpio-expander` (pin-out, design notes,
known issues; GPLv3), packaging options in that repository's README.
