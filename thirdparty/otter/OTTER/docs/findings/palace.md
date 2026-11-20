---
title: Palace findings (V5)
---

# Palace: getting the bondwire inductance right

Palace v1 and v3 had the right materials, ports and solver settings, but put the S21 notch
at 23 GHz and above 30 GHz. Version V5 changes only the mesh and the run procedure, and
brings the notch to 28.75 GHz (CST: 28.53 GHz). The mesher recipe is built into the Palace
script that the add-on generates.

Below is the V5 change log (`examples/pcb_to_chip/palace/CHANGES.md`). How to run the
workflow and all mesh settings are on the [Palace user guide page](../user-guide/palace.md).

---

--8<-- "examples/pcb_to_chip/palace/CHANGES.md:3"
