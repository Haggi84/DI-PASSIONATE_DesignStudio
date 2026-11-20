---
title: openEMS findings (13 steps)
---

# openEMS: from the exported script to a correct result

The add-on's original export of the benchmark could not run: it needed 206 M cells, put
both ports inside the PML and defined every metal with *magnetic* conductivity. The 13
steps below turned it into a run that matches CST within 0.34 dB (S11) and 0.07 dB (S21) up
to 25 GHz. Each step is marked `# Step n` in
`examples/pcb_to_chip/openems/PCB_Chip_openEMS.py`. The unchanged export is next to it as
`PCB_Chip_openEMS_original.py`.

Steps 1, 2, 4, 5, 6, 7, 8, 9, 10, 11 and 12 are now applied or checked automatically by the add-on,
and Step 3 for objects that fill their bounding box. The mesh parameters of Step 9 are set in
**Grid Settings → Solver mesh (global)**; see [Changes to the add-on](../reference/addon-changes.md).

---

--8<-- "examples/pcb_to_chip/openems/CHANGES.md:3"
