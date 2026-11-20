# Findings: what made the results correct

These findings come from bringing three solvers into agreement with CST on the
[PCB-to-chip benchmark](../benchmark/model.md). Most are not specific to this structure:
they apply to any model with thin metal layers, bondwires or other small features in a
large air volume.

## Lessons for all solvers

| # | Finding | Effect if ignored | Applies to |
|---|---|---|---|
| 1 | **Bondwire and copper losses shift the resonance.** Lossy gold and copper move the S21 notch from 27.0 to 28.5 GHz (CST: 28.53). The losses are only 0.5–1.5 % of the power. | Notch ~1.5 GHz low, S11 ~1 dB high | all |
| 2 | **The loop inductance comes from the air inside the bondwire loops.** Mesh that air finely (25 µm max in the benchmark). | FEM underestimates L (0.36–0.44 nH instead of ~0.7 nH); the notch wanders between 23 and 30 GHz | EMerge, Palace |
| 3 | **Absorbing boundary at least λ/4 away** at fmax (3 mm at 30 GHz). | Bondwire radiation is reflected; notch depth and shape are wrong | all |
| 4 | **Metal wins over dielectric.** Give conductors a higher priority than the dielectrics they touch. | The dielectric replaces the metal on shared faces | openEMS, Palace |
| 5 | **Ports must touch metal exactly** and lie on a mesh line or face. | Port floats: open circuit, S11 ≈ 0 dB | all |
| 6 | **Use zero-thickness ports and sheet models for sub-micron metals.** | Sub-micron cells shrink the FDTD timestep and the FEM element quality | all |

## Solver-specific findings

<div class="grid cards" markdown>

- **[openEMS (13 steps)](openems.md)**

    `kappa` vs `sigma`, the graded mesh (206 M → 2.17 M cells), ports out of the PML,
    stop at −50 dB, broadband pulse, conducting sheets, chip stack-up.

- **[Palace (V5)](palace.md)**

    Loop-air refinement, 3 mm margin, one-pass fragmentation, metals as holes, port
    snapping, `L0 = 1e-3`, the binary under MPI, sweep from 1 GHz.

- **[EMerge](emerge.md)**

    Agrees with openEMS when both use PEC metals; what to add next.

- **[QFN package (lumped ports)](qfn-package.md)**

    Smallest cell per direction (z resolves the chip metal gaps), thin conductors kept
    connected, flat STL sheets, board into the PML, EMerge conductivity and one process
    per frequency group.

</div>

## Where the add-on helps

Each finding is now either applied automatically when the add-on writes a script, or
reported as an **export warning**. The full list is in
[Changes to the add-on](../reference/addon-changes.md).
