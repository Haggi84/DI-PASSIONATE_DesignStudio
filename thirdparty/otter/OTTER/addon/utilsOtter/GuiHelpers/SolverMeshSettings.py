#
#   Global mesh settings of each solver (Grid Settings tab -> 'Solver mesh (global)').
#
#   These are the parameters of the mesh recipes that gave correct S-parameters for the PCB-to-chip benchmark
#   (examples/pcb_to_chip/*/CHANGES.md). They apply to the whole model, unlike grid items which are assigned to objects.
#   This list is the only place where widget names, defaults and ranges are defined: GUI, .ini save/load and script
#   generators all use it.
#
#   Entry: key -> (widget name, kind, default)
#       kind: 'bool' QCheckBox, 'int' QSpinBox, 'float' QDoubleSpinBox
#   Value 0 of 'auto' entries means the generator calculates the value from the frequency range (see tooltips in GUI).
#

SOLVER_MESH_SETTINGS = {
    'openems': {
        'graded':               ('meshGraded_openems',              'bool',  True),     # 'Fixed Distance' grids are max. cell size regions of one graded mesh
        'finalize':             ('meshFinalize_openems',            'bool',  True),     # geometry/port planes as mesh lines, slivers merged, air margin to PML
        'mergeSameMaterial':    ('meshMergeSameMaterial_openems',   'bool',  True),     # no mesh line on face shared by two boxes of same material (Step 7)
        'maxRatio':             ('meshMaxRatio_openems',            'float', 1.3),      # max. ratio of neighbouring cells
        'cellsPerWavelength':   ('meshCellsPerWavelength_openems',  'int',   20),       # max. cell = lambda_min / N
        'mergeRel':             ('meshMergeRel_openems',            'float', 0.5),      # graded: planes closer than mergeRel * local cell are merged
        'mergeTol_nm':          ('meshMergeTol_openems',            'float', 50.0),     # not graded: lines closer than this are merged [nm]
        'airMargin_mm':         ('meshAirMargin_openems',           'float', 0.0),      # clear air between structure and PML [mm], 0 = lambda/4 at fmax
        'minCell_um':           ('meshMinCell_openems',             'float', 0.0),      # smallest cell in x and y [um], conductors keep a line inside, 0 = off
        'minCellZ_um':          ('meshMinCellZ_openems',            'float', 0.0),      # smallest cell in z [um] (resolve the thinnest gap between metals), 0 = as x/y
        'structureIntoPml':     ('meshStructureIntoPml_openems',    'bool',  False),    # bodies on the structure bounds in x/y continue into the PML (board cut out of a bigger one), not at port faces
    },
    'emerge': {
        'scaleFactor':          ('meshScaleFactor_emerge',          'float', 1.0),      # sim.set_scale_factor(), EMerge >= 3.0, 1000 for micrometre parts
        'curvedBoundary':       ('meshCurvedBoundary_emerge',       'int',   6),        # sim.mesher.set_curved_boundary_meshing(), 0 = off
        'portFaceElements':     ('meshPortFaceElements_emerge',     'int',   5),        # lumped port face mesh size = port width / N
        'safeMode':             ('meshSafeMode_emerge',             'bool',  True),     # sim.settings.safe_mode, mesh diagnostics
    },
    'palace': {
        'airSize_mm':           ('meshAirSize_palace',              'float', 0.0),      # max. element size in air [mm], 0 = lambda/15 at fmax
        'airMargin_mm':         ('meshAirMargin_palace',            'float', 0.0),      # air around model without boundary object [mm], 0 = 0.3 lambda at fmax
        'curvature':            ('meshCurvature_palace',            'int',   20),       # elements per full circle on curved surfaces
        'portElements':         ('meshPortElements_palace',         'int',   5),        # port mesh size = smaller port side / N
        'sizeGrowth':           ('meshSizeGrowth_palace',           'float', 2.5),      # element size growth from metal surface into air
        'fuseSameMaterial':     ('meshFuseSameMaterial_palace',     'bool',  True),     # volumes of same dielectric fused (SiO2 + SiO2_2)
        'footRadius':           ('meshFootRadius_palace',           'float', 10.0),     # wire foot refinement ball radius [x wire size]
        'footGrowth':           ('meshFootGrowth_palace',           'float', 1.5),      # growth around wire foot
        'loopFactor':           ('meshLoopFactor_palace',           'float', 5.0),      # air inside wire loops [x wire size], 0 = off
        'loopPad':              ('meshLoopPad_palace',              'float', 4.0),      # wire loop box margin [x wire size]
        'loopGrowth':           ('meshLoopGrowth_palace',           'float', 2.0),      # growth out of wire loop box
        'threads':              ('meshThreads_palace',              'int',   0),        # gmsh threads, 0 = all cores
    },
}

def getDefaultSolverMeshSettings():
    """
    Default values {solver: {key: value}}.
    """
    return {solver: {key: spec[2] for key, spec in entries.items()} for solver, entries in SOLVER_MESH_SETTINGS.items()}

def getSolverMeshSettings(form, solver=None):
    """
    Values from GUI {solver: {key: value}} or just {key: value} for given solver. Missing widget gives default value.
    """
    values = {}
    for solverName, entries in SOLVER_MESH_SETTINGS.items():
        if solver is not None and solverName != solver:
            continue
        values[solverName] = {}
        for key, (widgetName, kind, default) in entries.items():
            widget = getattr(form, widgetName, None)
            if widget is None:
                values[solverName][key] = default
            elif kind == 'bool':
                values[solverName][key] = bool(widget.isChecked())
            elif kind == 'int':
                values[solverName][key] = int(widget.value())
            else:
                values[solverName][key] = float(widget.value())
    return values[solver] if solver is not None else values

def setSolverMeshSettings(form, values):
    """
    Set GUI from {solver: {key: value}}, unknown keys and wrong values are ignored (older .ini files), missing keys keep GUI value.
    """
    for solverName, entries in SOLVER_MESH_SETTINGS.items():
        solverValues = values.get(solverName, {}) if isinstance(values, dict) else {}
        for key, (widgetName, kind, default) in entries.items():
            widget = getattr(form, widgetName, None)
            if widget is None or key not in solverValues:
                continue
            try:
                if kind == 'bool':
                    value = solverValues[key]
                    widget.setChecked(value if isinstance(value, bool) else str(value).lower() in ['1', 'true'])
                elif kind == 'int':
                    widget.setValue(int(float(solverValues[key])))
                else:
                    widget.setValue(float(solverValues[key]))
            except Exception as e:
                print(f"Solver mesh setting {solverName}.{key} not loaded: {e}")
