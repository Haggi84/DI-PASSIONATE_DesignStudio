Reference files from KiCad (GPL-3.0-or-later, the KiCad project):

- `microwave.kicad_pcb` — github.com/KiCad/kicad-source-mirror, demos/microwave (KiCad 9 format)
- `basic_test.kicad_pcb` — same repository, qa/data/cli/basic_test (KiCad 7 format)
- `qfn32.kicad_mod` — github.com/KiCad/kicad-footprints,
  Package_DFN_QFN.pretty/QFN-32-1EP_5x5mm_P0.5mm_EP3.45x3.45mm.kicad_mod

Used by tests/test_design_flow.py to read real KiCad output and to compare the
exported footprint's pin numbering with KiCad's own library.
