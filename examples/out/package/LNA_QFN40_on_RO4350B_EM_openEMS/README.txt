LNA_QFN40_on_RO4350B_EM: openEMS simulation written by Package Studio with the OTTER add-on.

RUN

Needs openEMS with its Python modules (CSXCAD, openEMS).

    cd simulation
    python3 LNA_QFN40_on_RO4350B_EM_openEMS.py

FILES

    overview.svg     side and top view of the structure
    materials.json   the material of every layer, its values and where it came from
    simulation/      solver script and the geometry it loads
    project/         LNA_QFN40_on_RO4350B_EM.FCStd and LNA_QFN40_on_RO4350B_EM.ini, the flat simulation model:
                     open them in FreeCAD with the OTTER add-on to change the setup there

CHIP METAL (exact, from the GDS, inside these regions)

    Chip: x -0.3415..0.3140, y -0.4515..0.3507 mm (chip frame), levels TopMetal2

MESH

    688 × 399 × 96 = 26.35 M cells, smallest cell 5.35 µm, about 4.2 GB RAM

WARNINGS FROM THE EXPORT

  - dielectric has higher priority than conductor it touches, conductor is replaced by dielectric on shared faces, move conductors above dielectrics in priority list: ALL_LNA_Passive (-9300) > QFN_40_6x6_body (-9400)
