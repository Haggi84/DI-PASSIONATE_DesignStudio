# Package Studio — chip packaging assemblies in FreeCAD, ready to simulate

Build an assembly — chips from GDSII, housings, lids, boards, as many of
each as the design has (a mixed-signal module with an RF chip and a
digital chip, two packages on one board) — wire it, define its ports and
materials, and get a **ready-to-run EM simulation package** for openEMS,
EMerge or Palace, written by OTTER. An assistant on a local model (Ollama)
explains the setup and changes it in plain words.

One toolbar, in the order the work goes:

| Button | What it does |
|---|---|
| **Technology** | Choose the PDK chips are imported with: IHP SG13G2, SkyWater SKY130, or your own folder |
| **Import GDS chips** | One or several layouts at once. Each comes in beside the others, with every drawn layer at its stackup height, its die body, oxide, passivation, bond pads and the PDK's materials. A layout that belongs to another technology is caught |
| **Housing library** | MirrorSemi open-cavity packages, JEDEC outlines, or your own STEP files. Each housing is its own package; its bond fingers become pads; a chip can go straight onto its paddle; a lid can be added |
| **Element Forge** | New housings from a sentence or a pasted datasheet table — a local model (Ollama) fills in the numbers, you check and edit them (values it assumed are marked), live 3-D preview, saved to your own library. Select a housing to change it in place: its pads keep their pin numbers, wires stay on their pins |
| **Import STEP part** | A board (a KiCad board exported as STEP, say), a lid, or a housing as it is |
| **Place** | A chip onto a die paddle or any face (a cavity floor, a board's copper), a lid onto a housing, a package onto a board's footprint — centred. What is placed then *belongs to* what it stands on and moves with it |
| **Move** | Move placed chips, lids, packages or boards — one or several at once — by typed offsets, arrow steps or dragging in the view. Offsets stay editable; wires follow |
| **Pads** | Chip pads from the layout; package pads from the bond fingers (one click finds them all); contact points on any flat surface |
| **Ports** | Simulation ports: rectangles in a face's plane, or standing on an edge up or down to the next edge |
| **Bond wires** | Material, diameter, wedge–wedge or ball–stitch; every bond dimension next to its MIL-STD-883 range; symmetric pairing, chip by chip; rule check |
| **Materials** | What every element is made of — thermal and electrical. The same values go into the EM simulation |
| **EM simulation** | Readiness check, exact chip metal, OTTER's checks, and the package — in one go with *Make simulation package* |
| **EM assistant** | Ask about the setup and have it changed: Ollama on this computer, or Claude / Gemini with your own key |

The menu also has **Layers** (show or hide every layer and part) and
**Export geometry** (STEP, OBJ, MSH for thermal or mechanical work).

## Installing

The workbench is a folder in FreeCAD's user Mod directory:

```
%APPDATA%\FreeCAD\v1-1\Mod\PackageStudio
```

Restart FreeCAD and choose **Package Studio** in the workbench selector. It
needs FreeCAD 1.0 or newer and `gdstk` in FreeCAD's Python
(`"C:\Program Files\FreeCAD 1.1\bin\python.exe" -m pip install gdstk`).
OTTER ships with it (`thirdparty/otter`); the solvers themselves (openEMS,
EMerge, Palace) run where you run the package. For the assistant, install
[Ollama](https://ollama.com) and pull a model (`ollama pull qwen3:8b`).

**For large chips, switch on OpenGL vertex buffers**: Edit › Preferences ›
Display › 3D View › *Use OpenGL VBO*. Turning a 2.3-million-triangle chip then
takes 37 ms a frame instead of 505 ms.

It can be installed beside Package Designer and DI-PASSIONATE: its code is
all in the package `pkgstudio`, its commands are `PS_…`, its settings under
`Mod/PackageStudio`.

## An assembly, step by step

A mixed-signal example: an RF chip and a digital chip, each in its own QFN,
both on one board.

1. **Technology** — pick the PDK. It is remembered.
2. **Import GDS chips** — select both GDS files at once. They come in side
   by side, 0.5 mm apart; each is checked against the technology on its own.
3. **Housing library** — pick a QFN, tick *Put the chip on the die paddle*.
   It takes the first chip not in a housing yet. Do it again for the second:
   the second housing is a second package, set 2 mm beside the first.
4. **Bond wires** — *Pair all, symmetric* pairs each chip with the leads of
   its own package, round its own centre; *Build wires*.
5. **Import STEP part › Board**, then **Place › Package onto a board** for
   each package: select the footprint's pads. The package becomes part of
   the board.
6. **Move** — nudge a package or a chip into its final place; the offsets
   stay editable.
7. **Ports** — draw the ports where the signals go in.
8. **Materials** — check what everything is made of.
9. **EM simulation › Make simulation package** — or fix what its list says
   first.

**Open and look:** `examples/lna_board/lna_rf_em.FCStd` is a finished EM
model — the IHP ALL_LNA in a QFN-24 on its RO4350B board, ports, materials
and EM settings set. Open it after cloning; the EM panel shows how it is
set up. See `examples/lna_board/README.md`.

The same steps as a script, ready to simulate: `examples/lna_qfn40_on_board.py`
(the SG13G2 LNA in a QFN-40 on its RO4350B test board, see `examples/README.md`).

## Element Forge: new housings in seconds

The model never writes geometry: it fills in a fixed set of numbers
(`core/elements.py`, the datasheet symbols D, E, A, e, b, L, D2, E2, A1 and
the lead counts), constrained to that schema by Ollama's structured output.
Every spec — typed, generated or loaded — goes through the same checks:
leads that would overlap, leads too wide for their pitch, too many leads for
the body, no lead length left, a name that disagrees with the numbers. A
spec that fails is handed back to the model once or twice with the exact
numbers; whatever is still wrong is listed with a fix to apply. Then the real
build is analysed like any housing: every lead must be bondable, the paddle
open.

- **Generate**: `QFN-24 4x4 mm, 0.5 mm pitch, exposed pad 2.6 mm`, or a
  datasheet's dimension table pasted in. The fields fill in as the model
  writes them.
- **Change**: `pitch 0.4, two more leads per side` — the element in the form
  is changed, the rest kept.
- Values the model **assumed** are amber (≈) until you change or confirm
  them.
- **Save to library** writes `<FreeCAD user data>/PackageStudio/elements/<name>.json`
  (or `$PKGSTUDIO_ELEMENTS`) — plain JSON, diff- and git-friendly. The
  Housing library lists them under *Mine*.
- Every housing built from numbers (JEDEC or Forge) keeps them
  (`ElementSpec`). **Update housing** rebuilds it in place.

## The design flow: pin list, LVS, assembly house, KiCad, circuit, measurement

The third toolbar, **Design flow**, connects the package to the people
around it (plan and rationale: `docs/plan/`):

| Command | For whom | What it does |
|---|---|---|
| **Pin list & LVS** | chip designer | Reads what the designer intends — CSV (`die,pad,net,kind,lead`), the xschem top cell (`.sch`), the digital flow's LEF — lays the wires **by net** (fixed leads kept as anchors, the rest crossing-free; for 2–3 dies an exact search over the lead arcs) and checks the package against it: opens, shorts, wrong leads, supplies without a lead, bonded `nc` pads, RF leads without ground neighbours |
| **Bonding diagram** | assembly house | SVG/PDF to scale and the wire list (CSV: first bond in µm from the die centre, second bond from the package centre, material, diameter, loop height, length) |
| **KiCad board** | board designer | Reads a `.kicad_pcb` (KiCad 6–9): its stack-up, lines, vias, planes, copper polygons, footprints — the whole board or a region for the EM model; packages go onto its footprints' lands |
| **KiCad library** | board designer | The package as a KiCad footprint (lands numbered as the leads — same numbering and positions as KiCad's own QFN library), a symbol whose pins carry the LVS nets, and a STEP model with dies and wires |
| **Circuit model** | chip designer | Touchstone → passive SPICE subcircuit (vector fitting, scikit-rf) + xschem symbol; with ngspice on the PATH it is simulated and compared with the Touchstone file |
| **Compare with measurement** | everyone who must trust the result | IEEE P370 quality of the measured data, 2x-thru de-embedding, max/RMS Δ per S-parameter, plot and report (`tools/ps_validate.py` does the same from the shell) |

Optional Python packages for the flow: `scikit-rf` (circuit model,
validation), `matplotlib` (plots); `ngspice` on the PATH for the circuit
model's check.

## Multi-chip modules, boards, systems

The second toolbar, **MCM & System**, takes an assembly beyond one die in one
package:

| Command | What it does |
|---|---|
| **Arrange dies (MCM)** | Several dies on one paddle — in a row, a column or corner to corner, a gap apart — and checked: on the paddle (with a margin for the die-attach fillet), clear of each other (≥ 0.15 mm), clear of the bond fingers (`core/mcm.py`) |
| **Wire the MCM** | Bond wires for all dies at once, crossing-free: the leads are shared out among the dies (a weighted Voronoi split of the lead ring), then each die is paired round its own centre. Die-to-die wires are ordinary wires between two chip pads; MIL-STD-883 judges their second bond as on a die pad (3.2.1.4a) |
| **PC/104 board** | A generated CubeSat board, 90.17 × 95.89 × 1.6 mm, 0.254 mm RO4350B over a ground plane on FR-4, corner holes, CubeSat Kit bus headers. From a script, `core/pcb.py` draws QFN land patterns, GCPW with via fences, traces, connectors and component bodies |
| **CubeSat 1U** | Rails, frames, M3 rods, spacers, solar panels, an S-band patch (`core/cubesat.py`) |
| **Into the system** | A board into the stack, at 15 mm pitch or a height you type; the spacers follow |
| **Integration check** | Envelope (100 × 100 × 113.5 mm, ≤ 6.5 mm out of a face, never on a rail), collisions between modules, every board's parts against the room in the stack, mass (≤ 2.00 kg) and centre of gravity (±2 cm) — CubeSat Design Specification Rev 14.1. Masses from the materials' densities |

The reference case is `examples/cubesat_sband_mcm.py`: an S-band receiver
front end from two real SG13G2 dies (the LNA and IHP's open
`i2c-gpio-expander`) in one QFN-32, on a CubeSat COM board, in a 1U CubeSat —
plus the RF path cut out of the board as an openEMS model. See
`examples/README.md`.

## Assemblies: what belongs to what

```
Assembly (the document)
  Board (App::Part)            imported STEP
    Package (App::Part)        placed on the board's footprint
      Housing, Lid             from the library, or STEP
      Chip (App::Part)         placed on the paddle
      Die attach, Bond wires
    Chip                       placed on the board's copper: chip-on-board
  Package 2 …
  Chip                         imported, not placed yet ("loose")
```

Chips come in **loose**, at the document's top level, beside one another.
**Place** puts a chip into the package of the housing it is placed on, or
into the board; a package placed on a board's footprint goes into the
board. From then on it moves with what it stands on, and its place is
kept exactly: only the container changes. A housing is always a package of
its own — a new one goes into the first package without a housing, else
into a new package beside the others.

**Bond wires** belong to the package or board both their pads are in, and
move with it. *Pair all, symmetric* pairs each chip on its own, with the
leads of its own package (or board), so two chips never take each other's
leads.

## Place and Move

**Place** is exact: the object's bottom centre onto the centre of the
selected face (or of several faces, such as a footprint's pads), its +Z
along their normal, turned in quarter steps. *Chip onto a die paddle* needs
nothing selected.

**Move** then moves it from there, *parametrically*: the place Place put it
is kept as its base, and a move is an offset from that base, kept in the
object's *Position* properties:

| Property | Meaning |
|---|---|
| OffsetX, OffsetY | moved along the surface it stands on, mm |
| OffsetAngle | turned about the surface normal, through its own bottom centre |

The X and Y of a move are its container's X and Y laid into the surface —
on a level paddle or board, the package's or board's X and Y, whichever way
the chip itself was turned — so several chips moved together all go the
same way. A turn is about each object's own centre.

In the **Move** panel:

- **select the rows** to move (Ctrl / Shift for several), or select them in
  the view and press *Take 3-D selection* — any face of a chip selects the
  chip;
- **step** them with the arrows (*Step*, default 0.05 mm) and turn them by
  the *Turn step*;
- **type** an offset in the table, or *Move by* ΔX ΔY;
- **drag**: *Drag in the view…*, click to grab, move — everything selected
  follows over its surface, snapped to the step — and click to drop. Esc
  puts everything back. Before grabbing, the arrow keys step and R turns;
- *Back to where placed* sets the offsets to zero; *Keep here as placed*
  makes the current place the new base.

Offsets typed in the property editor move the object too. Moved by other
means (FreeCAD's Transform), the next offset keeps that move. Wires are
rebuilt after every move — during a drag they are hidden and rebuilt on
the drop. Chips that overlap are reported.

## Technologies and the stack

A technology is a folder in `technologies/`: the PDK's KLayout layer
properties (`.lyp` — names and colours), its stackup (`.xml`) and a
`tech.json`. **Every height comes from the stackup XML** — the `.lyp` has
none, and a `.map` file (GDS ↔ LEF/DEF names) neither. Both stackup formats
in use are read and give the same heights:

- schema 2.0 (the bundled files): absolute `Zmin`/`Zmax` per layer and a
  `Substrate Offset`;
- schema 3.0 / 3.1 (IHP's current files, e.g. `SG13G2_FEM_200um.xml` from
  gds2palace): a layer or dielectric placed on another's `Top`/`Bottom`
  edge (`Reference`/`ReferenceEdge`), and `<Variables>` with `=expression`
  values (arithmetic only; nothing else is evaluated).

z = 0 is the top of the silicon. Checked against the PDKs (µm):

| SG13G2 | z | SKY130 | z |
|---|---|---|---|
| Metal1 | 1.04 – 1.46 | li1 | 0.9361 – 1.0361 |
| Metal5 | 5.09 – 5.58 | met1 | 1.3761 – 1.7361 |
| MIM | 5.6043 – 5.754 | met3 | 2.7861 – 3.6311 |
| TopMetal1 | 6.4303 – 8.4303 | met5 | 5.3711 – 6.6311 |
| TopMetal2 | 11.2303 – 14.2303 | | |
| SiO2 / passivation | 0 – 15.7303 / – 16.1303 | SiO2 | 0 – 6.6311 |
| EPI / substrate | −3.75 – 0 / 180 µm | EPI / substrate | −2 – 0 / 298 µm |

On import the chip gets, besides its drawn layers and its silicon:

- **the oxide and passivation**, die-sized, hidden and not pickable (shown,
  they would cover every pad) — offered in the export, not ticked, since
  they enclose the metal;
- **the unused PDK levels** — stack levels the PDK has and this layout does
  not draw (a digital chip without MIM, say), as faint die-sized slabs at
  their true height, listed in **Layers** under *Unused PDK levels*. They
  are not metal and are not ticked in the export.

**The wrong technology** builds nothing: no layer matches, and the chip
comes in as a bare block (an IHP layout imported as SKY130 does exactly
that). Import GDS counts how many of the layout's layers each technology
knows and offers the one that fits — ALL_LNA: SG13G2 knows 68 of 70, SKY130
one marker layer.

## Housings

| Source | What it is |
|---|---|
| **MirrorSemi** | MirrorSemi's pre-moulded open-cavity QFN/DFN packages (mirrorsemi.com), ready for die attach and wire bonding. *Load catalogue* reads their CAD page; a model is downloaded only when chosen, into a cache in your FreeCAD user folder. Nothing of theirs is shipped — their files are "all rights reserved", for your own use. A link that leads nowhere is reported, not imported. |
| **JEDEC** | Open-cavity packages generated from JEDEC outlines: QFN (MO-220), DFN (MO-229), LQFP/TQFP (MS-026), SOIC (MS-012), TSSOP (MO-153). Leadframe (paddle and leads, gull-wing outside where the outline has them) and a body with a cavity down to the bond fingers. |
| **Local folder** | Any folder of your own housing STEP files. |

Every housing, whatever its source, is read the same way: the solid with the
largest footprint is the body; the leadframe piece under its centre is the
die paddle; pieces reaching one side of the body are leads, pieces reaching
two are tie bars. A lead's *bond finger* is its top face where the body does
not cover it. This needs the leadframe as separate solids, as MirrorSemi's
models and the generated packages have it; a fully moulded body exposes no
fingers. Body and leadframe get mould compound and copper alloy C194; change
either in **Materials** (MirrorSemi bodies are pre-moulded — LCP is in the
library).

Lids are generated flat, the size of the body, in glass, ceramic, LCP or Kovar.

## Bond wires

Two bond types, shaped as a bonder makes them:

- **Wedge–wedge** (ultrasonic, aluminium — the default): a wedge on the chip
  pad and one on the lead; the wire leaves the first wedge's heel at the
  tool's wire-feed angle (45° by default), not vertically, and a short tail
  is left beyond the last wedge.
- **Ball–stitch** (thermosonic, gold, copper, silver): a flattened ball with
  the capillary's chamfer cone on the chip pad, the wire leaving it
  vertically, and a crescent stitch on the lead.

**The wire is one swept solid, wedges included.** Its centreline runs over
the first foot, bends out of the heel (radius 1.5 d), follows the loop,
bends into the second foot and runs on into the tail. The cross-section
along it is the round wire in the loop and the flattened bond on each foot,
changing over the bends, as the tool squashes the wire. There are no
separate bond blocks for the wire to sit on. A bond's thickness is not set
but follows from the wire: the flattened section keeps the wire's area
(1.5 d wide → 0.57 d thick). Each foot is sunk a tenth of its thickness into
its pad and cut flat, so **it meets the surface in a flat contact face lying
in the pad's plane** (31.8 × 70 µm for a 25 µm wire's first wedge). This
holds on a tilted surface too: each end follows its own pad's normal. The
loop's highest point is exactly the loop height.

### Wires onto a board: contact points

A board, substrate or any flat face without pads of its own gets
**contact points**: Pads › *Contact points*.

1. Select the face the wires land on and press **Use selected face**. The
   panel shows its size, e.g. *100.0000 x 50.0000 mm (X x Y)*.
2. Choose the **origin**: the *face corner* (default; X runs along the
   face's edge nearest the chip's x axis, Y across it, so a 100 x 50 mm
   face runs from (0, 0) to (100, 50)), or the *chip centre* (X, Y along the
   chip's axes).
3. A **grid with rulers** is drawn on the face in these coordinates:
   labelled lines about ten across the face in 1-2-5 steps (10 mm on a
   100 mm face), fine lines a fifth of that, millimetre labels along the X
   and Y edges. Set the spacing yourself in *Grid*, or switch it off. It is
   temporary: not exported, gone when the panel closes.
4. Type **X** and **Y** in mm and press **Add point**: X 25.5, Y 10 puts it
   exactly there. Or **Click points…** on the face (Esc ends): a red
   crosshair follows the mouse and the panel shows the X and Y a click
   would give, snapped to the fine grid lines (switch off *clicks snap*
   for free positions); a click off the face is refused.
5. The table lists every point's X and Y; edit a value to move the point.
   A point is the size of the bond that lands on it (62.5 x 37.5 µm for
   25 µm wire); *Point size › Apply* resizes the selected ones (or all).
6. Select points in the table and **Mirror** them over X (through the
   face's middle: Y → 50 − Y on a 100 x 50 face), over Y (X → 100 − X), or
   both. With the chip-centre origin the mirror lines are the chip's axes.
   An image already there, or off the face, is not made.
7. **Bond wires › Pair all, symmetric** (or *Pair selected pads*) and
   **Build wires**: their second wedges lie flat on the face.

### The rule check

The rule check applies MIL-STD-883, Test Method 2010 (internal visual),
class level B (Condition B, the default) or S (Condition A):

| Clause | Criterion |
|---|---|
| 3.2.1.1a | Ball diameter 2.0 – 5.0 × wire diameter |
| 3.2.1.1c | Ball: wire centre exit within the pad opening |
| 3.2.1.2a/b | Wedge 1.2 – 3.0 × wide (ultrasonic) or 1.5 – 3.0 × (thermosonic stitch), 1.5 – 6.0 × long |
| 3.2.1.4a | ≥ 75 % (S) / ≥ 50 % (B) of the bond on the die within the pad opening |
| 3.2.1.4d | Tail ≤ 2 wire diameters |
| 3.2.1.4e | Bond completely on the lead |
| 3.2.1.4f | Bond to bond: ≥ 0.25 mil (S) / a gap (B) |
| 3.2.2a | Wire to other wires, bonds, leads, the die edge, the lid: ≥ 2 d (S) / ≥ 1 d (B); class S accepts 1 mil within 5 mil of the bond on the die |
| 3.2.2d | No straight run without an arc |
| 3.2.2e | No wires crossing seen from above, except at different heights with ≥ 2 d |

Two assembly guidelines are reported as notes, not rejects: wire length
≤ 200 × diameter (5 mm for 25 µm) and a loop of at least 75 µm for ball
bonds (neck damage below that) or 2 × diameter for wedges. Each row of the
table shows ✓ or ✗; hovering lists every finding with its clause.

## Ports

**Ports** has its own command. A port is a flat rectangle where the
simulation feeds and measures the model.

**In a face's plane**: select a face (or an edge — the flat face beside it
that stands most upright is taken: a board's top-rim edge gives its edge
face), press **Use selected face**, choose the origin, and type the port's
centre and **Add port**, or **Click port centres…** with the grid and
crosshair. Give its size (W along the face's X, H along its Y), type
(*lumped* or *wave*), impedance (50 Ω) and, for a lumped port, the direction
of its field (±X or ±Y in its own plane). The table edits position, size,
type and impedance; **Mirror selected** mirrors over X, Y or both. Ports are
named Port1, Port2, … (P1 is often a chip pad's name).

**Or draw it on an edge.** **Draw on an edge…**, in the same box, needs no
face chosen first. It takes three clicks per port:

1. Click an edge of a chip layer, a board, a housing or a lid. The port
   starts there.
2. Click again along that edge to set the port's length.
3. Move the mouse up or down and click to set its height.

The port stands upright on the edge, and the next port can be drawn
straight away. **Esc** abandons the port being drawn; **Esc** again ends.
On a chip layer the edge is the side of the drawn GDS polygon nearest the
click, at the layer's top or bottom. The layer is shown as a mesh, and the
mesh's triangle edges are not the polygon's. Two choices set the numbers:

| Choice | Effect |
|---|---|
| **Length: from the two clicks** | The length is between the two clicks. |
| **Length: W above** | The length is W; the second click only says which way along the edge the port runs. |
| **Height: follow the mouse** | The height is where the cursor is. |
| **Height: H above** | The height is H; moving up or down picks the direction. |
| **Height: to the next edge above / below** | The port ends at the nearest edge of other shown geometry over or under its length. That is usually the ground under a trace, or for a chip layer the silicon's top. Moving up or down picks which. |

"Next edge" is measured exactly. B-rep parts (board, housing, lid, die body)
are cut with the port's own plane. A chip's layers are taken from their
GDS polygons: every polygon the port's footprint crosses gives its layer's
bottom and top. The body the port starts on doesn't count: from a trace's
top, the next edge below is the ground, not the trace's own underside.
Hidden layers and the faint slabs of levels the layout does not draw don't
count either. Show the oxide, and its boundaries count too. Type, Z0 and
field direction come from the box above. A port drawn on an edge is an
ordinary port afterwards: in the table, in the exports, and in
`ports.json`.

Ports are drawn as opaque faces, in front of the faces they lie in. A port
on an edge shares its plane with the side faces there, and when it was
drawn see-through the edges behind showed through, so it looked like an
outline.

Ports are drawn as opaque faces, in front of the faces they lie in.

**A lumped port spans the gap between two conductors**, the signal and its
ground: from a trace down to the ground plane, or in a QFN's leadframe
plane from a lead's inner end across to the die paddle. One that touches a
single conductor is warned about; one that touches none is an error — it
would feed nothing.

## Materials

Every element has a material with density, thermal conductivity, specific
heat, CTE, electrical conductivity, permittivity, permeability and loss
tangent. **There is one material system**: what the Materials panel says is
what the exports write and what the EM solver gets; a material changed in
the EM panel is written back to the element. They come from three places:

- **The chip's technology** — every material in the PDK stackup, with the
  PDK's own conductivity and permittivity (SG13G2 Metal1: 21.64 MS/m), and the
  thermal values of the bulk material it is made of.
- **The library** — bond-wire metals, leadframe alloys, moulding compounds,
  laminates, ceramics, glass; typical values with their sources.
- **Your own** — *New material…* in the Materials panel, kept in
  `PackageStudio/materials.json` in FreeCAD's user folder.

From 10⁴ S/m up a material is a conductor in the EM model (Ag-filled die
attach at 10⁵ S/m is one); below, a dielectric with its ε_r and tan δ (doped
silicon at 2 S/m is a lossy one). Set *not simulated* to leave an element
out.

## EM simulation

**EM simulation** opens a panel that goes from the assembly to a
ready-to-run package. **Make simulation package** does it all and stops at
the first thing that would make the result wrong, saying which command
fixes it. The steps, one by one:

**0. Ready to simulate?** — what the assembly still lacks, worst first:

| Error (stops) | Warning (a guess the result depends on) |
|---|---|
| no port, no excited port, two ports with one number | parts without a material — OTTER guesses from name and colour |
| a material not in the library, or without ε_r and σ | a chip not placed in a housing or on a board |
| a chip region too big to mesh | a port touching only one conductor |
| chips overlapping | MIL-STD-883 rejects on the wires |
| a wire that lost a pad; after the build: a port touching no conductor | |

**1. Chip metal** — a whole chip cannot be meshed for EM (ALL_LNA has
600,000 vias) and does not need to be: what the package adds is pad, wire
and lead. So each chip's metal is rebuilt **exactly from its GDS polygons**
inside a region — by default round the pads that have wires, 20 µm beyond
them — for the levels chosen (by default the two highest metals and the vias
between). **Fill is left out**; via arrays become blocks whose conductivity
is scaled by how much of them is via. The rest of the die is its silicon,
oxide and passivation. The table shows how many polygons each chip builds
(at most 60,000).

**2. Model** — a separate document, *<assembly> (EM model)*: every physical
part where it really is (housing, leadframe, die attach, lid, board, wires,
die body, oxide), the chip metal, one face per port, and an air box a
quarter wavelength round everything at the highest frequency (2.6 mm at
30 GHz). The assembly itself is not changed.

**3. Solver** — openEMS (FDTD), EMerge (FEM) or Palace (FEM); frequency
range; mesh density and smallest cell.

**4. Layers and materials** — the layer stack OTTER sees, each layer's
material and where it came from; guessed ones are marked. *Save chosen
materials to the assembly* writes a choice back to the elements.

**5. Ports** — every port as a lumped port with its impedance and its
excitation axis; which one is excited.

**6. Checks** — OTTER's own checks, and the openEMS mesh estimate.

**7. Package** — written by the OTTER add-on itself into a zip: the solver
script and the geometry it loads, the flat model (`.FCStd` + `.ini`, to open
with the OTTER add-on), `overview.svg`, `materials.json` (every layer, its
values and where they came from) and a README with the run command, the
chip regions, the mesh estimate and every remaining finding.

### Click a finding: the fix, shown in the 3-D view

Every line of *0. Ready?* and *6. Checks* can be clicked. A box under the
list explains it in the assembly's terms. The 3-D view zooms to it and
draws:

| Colour | Meaning |
|---|---|
| red | the part the finding is about |
| yellow | the conductors a fix would join |
| green | the fix itself |

Where the fix can be worked out, it is a button; applied, the model is
built and checked again. Otherwise *Open Ports / Materials / Move / Place*
opens the command that fixes it by hand. The preview is temporary: it goes
when another line is clicked, a fix is applied, or the panel closes.

| Finding | What is shown | The fix |
|---|---|---|
| a port with no conductor at one or both ends | the port; the nearest conductor below its centre and above it, in its footprint | the port stretched or shrunk exactly between the two; if only one side has a conductor, that end. With none in its footprint, the nearest conductors anywhere are shown, to move it to |
| a port touching only one conductor | as above | as above |
| wires in cells larger than the wire | the parts named (often via blocks OTTER reads as wires) | their own cells of at most their diameter / 1.5 — fine cells only there |
| too many mesh cells | the part that sets the model's extent | coarse mesh density; smallest cell 2 µm |
| a sub-micron cell | | smallest cell 1 µm |
| no or several excited ports | | excite the first port only |
| air boundary too close | | air box a quarter wavelength away |
| a part without a material, an unplaced or overlapping chip, a broken wire | the parts concerned, selected | *Open Materials / Place / Move / Bond wires* |

## EM assistant

The assistant panel is a chat about the EM setup. It can only do what the
panel can — solver, frequency, material per layer, mesh, ports, checks, and
search OTTER's documentation — through a fixed set of tools, each checked
like a click; it never sees or writes solver code. With every question it
is given the project, where each material came from, and the readiness
findings, so it can say what is missing and which command fixes it.

- **Local model (Ollama)** — the default: free, and nothing leaves the
  computer. The panel says whether Ollama answers at its URL
  (`http://127.0.0.1:11434`) and lists the models pulled.
- **Claude** or **Gemini** with your own API key: the project description
  and your questions go to that provider. A key is kept for the session,
  or in FreeCAD's `user.cfg` if you tick *remember*.

Build the model first (*Build model* or *Make simulation package*).

## Export geometry: what is written, exactly

Chip layers are rebuilt from the GDS polygons at export time — never from
what the view shows. Every polygon becomes a prism of its layer's height,
vertex for vertex. Metal fill is metal and is included; slits are cut out;
pin, label and boundary shapes are markers and are not.

| File | Content |
|---|---|
| STEP | Exact solids, one named product per element (`Chip/Metal1_8_0`, `Wires/Wire P1 - 1`, …) |
| OBJ | Triangle meshes, one named group per element, coloured by material. Chip layers are exact; curved parts follow the chosen deviation |
| MSH | Gmsh 4.1 volume mesh from the exact STEP: one physical group per element, and one per material (`MAT_…`) |
| `…_materials.json/.csv` | Each element, its material and every property, with units — written beside every export |

Exact solids cost about 0.6 ms per layout polygon; the export dialog says how
long before it starts, and can be cancelled.

**Only a section**: tick *Only a section* in the export dialog and press
**Pick in view…**: the view turns to the top; click two opposite corners —
a red rectangle follows the mouse — or type x and y from/to in mm. Only what
lies inside is written, at every height: chip layers are cut polygon by
polygon before any solid is built (so a section of a large chip is quick),
everything else is cut to the box. Elements wholly outside are left out, and
so are ports outside it from `_ports.json`.

## Limits

- Overlapping polygons on one layer are exported as overlapping prisms (only
  layers with slits are merged).
- Elements in an MSH geometry export are meshed independently (not conformal
  across touching elements).
- The EM model's passivation has no openings over the pads; the wire's foot
  takes their place.
- Housing analysis needs the leadframe as separate solids.
- Material values in the library are typical; for a production simulation,
  use your suppliers' datasheets (add them as your own materials).

## Tests

```powershell
& "C:\Program Files\FreeCAD 1.1\bin\freecadcmd.exe" tests\run_all.py     # 425 headless checks
& "C:\Program Files\FreeCAD 1.1\bin\python.exe" tests\run_gui_smoke.py   # 65 checks in a real FreeCAD window
```

The MirrorSemi check runs once a model is in the download cache.

GPL-3.0-or-later. OTTER (`thirdparty/otter`) is GPL-3.0-or-later too, see
its `LICENSE` and `NOTICE.md`.
