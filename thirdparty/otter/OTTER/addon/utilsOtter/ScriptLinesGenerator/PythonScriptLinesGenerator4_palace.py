#   author: Lubomir Jagos
#
#
import os
from PySide import QtGui, QtCore, QtWidgets
import numpy as np
import re
import math

from utilsOtter.GlobalFunctions.GlobalFunctions import _bool, _r, _r2
from utilsOtter.GuiHelpers.FactoryCadInterface import FactoryCadInterface
from utilsOtter.ScriptLinesGenerator.PythonScriptLinesGenerator3_emerge import PythonScriptLinesGenerator3_emerge
from utilsOtter.ScriptLinesGenerator.CommonScriptLinesGenerator import SimpleBoundBox
from utilsOtter.ScriptLinesGenerator.GeneratedScriptHelpers_palace import getHelperFunctionsSource as getPalaceHelperFunctionsSource
from utilsOtter.GuiHelpers.SolverMeshSettings import getSolverMeshSettings
import pprint

class PythonScriptLinesGenerator4_palace(PythonScriptLinesGenerator3_emerge):

    #
    #   constructor, get access to form GUI
    #
    def __init__(self, form, statusBar = None):
        super(PythonScriptLinesGenerator4_palace, self).__init__(form, statusBar)

    def generateSimulationScript(self, outputDir=None):
        self.generatePalaceScript(outputDir)

    def generatePalaceScript(self, outputDir=None):
        self.resetExportState()

        # Create outputDir relative to local FreeCAD file if output dir does not exists
        #   if outputDir is set to same value
        #   if outputStr is None then folder with name as FreeCAD file with suffix _openEMS_simulation is created
        outputDir = self.createOuputDir(outputDir)

        # Update status bar to inform user that exporting has begun.
        if self.statusBar is not None:
            self.statusBar.showMessage("Generating Palace script and geometry files ...", 5000)
            QtWidgets.QApplication.processEvents()

        simulationName = self.cadHelpers.getCurrentDocumentFileName()
        simulationOutputMeshFile = self.form.simParamsModelMeshName_palace.text()
        simulationOutputDirectory = self.form.simParamsOutputDirectory_palace.text()
        simParamsVerbose_palace = self.form.simParamsVerbose_palace.value()
        simulationProblemType = self.form.simParamsSimulationTypeList_palace.currentText()
        simParamsModelMeshBaseUnits_palace = self.form.simParamsModelMeshBaseUnits_palace.currentText()

        linearSolverType = self.form.simParamsLinearSolverType_palace.currentText()
        linearSolverTolerance = self.form.simParamsLinearSolverTolerance_palace.value()
        linearSolverMaxIterationCount = self.form.simParamsLinearSolverMaximumIterationCount_palace.value()
        linearSolverKSPType = self.form.simParamsLinearSolverKSPType_palace.currentText()

        solverOrder = self.form.simParamsLinearSolverOrder_palace.value()
        solverDevice = self.form.simParamsSolverDevice_palace.currentText()

        # List categories and items.
        #
        itemsByClassName = self.getItemsByClassName()

        self.checkFemModelSettings(itemsByClassName, checkLowFrequency=True)
        if simParamsModelMeshBaseUnits_palace != "mm":
            self.addExportWarning(f"mesh length unit in GUI is '{simParamsModelMeshBaseUnits_palace}', STEP geometry is meshed in mm so mm (L0 = 1e-3) is used")

        # Model description for mesher: objects exported as STEP files, rectangles for ports, materials, sizes, ...
        model = self.getPalaceModel(itemsByClassName, outputDir, simulationName, simulationOutputMeshFile, simulationProblemType)

        # Write script header.
        #
        genScript = ""

        genScript += "## Palace simulation\n"
        genScript += "#\n"
        genScript += "#\n"
        genScript += "#   Run with run_palace.sh (mesh + Palace binary under mpirun), ie.: NP=16 bash run_palace.sh\n"
        genScript += "#\n"
        genScript += "#   Mesh is built by palaceBuildMesh() below (approach from mesh_palace.py): one fragmentation of all objects and port\n"
        genScript += "#   rectangles, metals removed from mesh (their faces are PEC / conductivity boundaries), port nodes snapped on port rectangle.\n"
        genScript += "#\n"
        genScript += "# To be run with python.\n"
        genScript += "# based on: FreeCAD to OpenEMS plugin by Lubomir Jagos, see https://github.com/LubomirJagos/FreeCAD-OpenEMS-Export\n"
        genScript += "# Edited and automated by Mohsen\n"
        genScript += "# This file has been automatically generated. Manual changes may be overwritten.\n"
        genScript += "#" * 120 + "\n"
        genScript += "import os\n"
        genScript += "import sys\n"
        genScript += "import time\n"
        genScript += "import json\n"
        genScript += "import numpy as np\n"
        genScript += "import gmsh\n"
        genScript += "\n"
        genScript += "# gmsh GUI window with mesh blocks script, set environment variable SHOW_GMSH_GUI=0 to run without it (ie. on server)\n"
        genScript += "SHOW_GMSH_GUI = os.environ.get('SHOW_GMSH_GUI', '1') != '0' and (os.name == 'nt' or 'DISPLAY' in os.environ)\n"
        genScript += "\n"
        genScript += "# all paths are relative to this script folder\n"
        genScript += "currDir = os.path.dirname(os.path.abspath(__file__))\n"
        genScript += "os.chdir(currDir)\n"
        genScript += "print(currDir)\n"
        genScript += "\n"

        genScript += "##########################################################################################################\n"
        genScript += "# MESHER FUNCTIONS\n"
        genScript += "##########################################################################################################\n"
        genScript += getPalaceHelperFunctionsSource()

        genScript += "##########################################################################################################\n"
        genScript += "# MODEL FROM FREECAD\n"
        genScript += "#   objects: role 'dielectric' (meshed volume), 'pec' / 'conductor' (metal removed, faces are PEC / Conductivity boundary),\n"
        genScript += "#            'sheet' (conducting sheet, Conductivity boundary with thickness), 'boundary' (object used as boundary condition,\n"
        genScript += "#            volume enclosing whole model is air domain), 'port', 'lumped'; higher priority wins where objects overlap,\n"
        genScript += "#            metal always wins over dielectric and dielectric over air\n"
        genScript += "#   all lengths in mm\n"
        genScript += "##########################################################################################################\n"
        settings = model.pop('settings')
        meshSettings = getSolverMeshSettings(self.form, 'palace')
        genScript += "MODEL = " + pprint.pformat(model, indent=1, width=160, sort_dicts=False) + "\n"
        genScript += "\n"
        genScript += "# mesh settings from grid tab -> Solver mesh (global)\n"
        genScript += "MODEL['settings'] = {\n"
        settingLines = [
            (f"'problemType': '{settings['problemType']}',", ""),
            (f"'lossyMetalsAsPec': {settings['lossyMetalsAsPec']},", "True: lossy metals and conducting sheets are PEC (Palace conductivity boundary is just for driven problem)"),
            (f"'airSize': {settings['airSize']:.6g},", "max. element size in air [mm]" + settings['airSizeComment']),
            (f"'airMargin': {settings['airMargin']:.6g},", "air around model when no boundary object encloses it [mm]" + settings['airMarginComment']),
            (f"'fuseSameMaterial': {meshSettings['fuseSameMaterial']},", "volumes of same dielectric material are fused (no internal faces, no thin layers of same material)"),
            (f"'curvature': {meshSettings['curvature']},", "elements per full circle on curved surfaces (round bondwires)"),
            (f"'portElements': {meshSettings['portElements']},", "port mesh size = smaller port side / portElements"),
            (f"'sizeGrowth': {meshSettings['sizeGrowth']:g},", "element size grows from object surface into air by this per unit distance"),
            (f"'footRadius': {meshSettings['footRadius']:g},", "wire foot (contact with other metal) refined in ball with radius footRadius * wire size"),
            (f"'footGrowth': {meshSettings['footGrowth']:g},", "... and slower growth around it (transitions matter more than fineness)"),
            (f"'loopFactor': {meshSettings['loopFactor']:g},", "air inside wire loops meshed with loopFactor * wire size (0 = off), loop inductance comes from this air"),
            (f"'loopPad': {meshSettings['loopPad']:g},", "loop box = wire bounding boxes + loopPad * wire size"),
            (f"'loopGrowth': {meshSettings['loopGrowth']:g},", "element size growth out of loop box"),
            (f"'threads': {meshSettings['threads']},", "gmsh threads, 0 = all cores"),
        ]
        for code, comment in settingLines:
            genScript += (f"\t{code:<30}# {comment}" if comment else f"\t{code}") + "\n"
        genScript += "}\n"
        genScript += "\n"

        genScript += "##########################################################################################################\n"
        genScript += "# MESH\n"
        genScript += "##########################################################################################################\n"
        genScript += "gmsh.initialize()\n"
        genScript += "groups = palaceBuildMesh(MODEL)\n"
        genScript += "if SHOW_GMSH_GUI:\n"
        genScript += "\tgmsh.fltk.run()\n"
        genScript += "gmsh.finalize()\n"
        genScript += f"print('PASS - Mesh generated and saved as {simulationOutputMeshFile}')\n"
        genScript += "\n"

        genScript += "##########################################################################################################\n"
        genScript += "# Generate Palace solver simulation .json file\n"
        genScript += "##########################################################################################################\n"
        genScript += "simulationConfig = {}\n"
        genScript += "\n"
        genScript += 'simulationConfig["Problem"] = {}\n'
        genScript += f'simulationConfig["Problem"]["Type"] = "{simulationProblemType}"\n'
        genScript += f'simulationConfig["Problem"]["Verbose"] = {simParamsVerbose_palace}\n'
        genScript += f'simulationConfig["Problem"]["Output"] = "{simulationOutputDirectory}"\n'
        genScript += '\n'
        genScript += '# mesh is in mm, without L0 = 1e-3 all frequencies are wrong\n'
        genScript += 'simulationConfig["Model"] = {}\n'
        genScript += 'simulationConfig["Model"]["Mesh"] = MODEL["meshFile"]\n'
        genScript += 'simulationConfig["Model"]["L0"] = 1.0e-3\n'
        genScript += '\n'
        genScript += 'simulationConfig["Domains"], simulationConfig["Boundaries"] = palaceConfigDomainsAndBoundaries(MODEL, groups)\n'
        genScript += '\n'
        genScript += '#\n'
        genScript += '# Linear solver is used for all problem types, here is its specification\n'
        genScript += '#\n'
        genScript += 'simulationConfig["Solver"] = {}\n'
        genScript += 'simulationConfig["Solver"]["Linear"] = {}\n'
        genScript += f'simulationConfig["Solver"]["Linear"]["Type"] = "{linearSolverType}"\n'
        genScript += f'simulationConfig["Solver"]["Linear"]["KSPType"] = "{linearSolverKSPType}"\n'
        genScript += f'simulationConfig["Solver"]["Linear"]["Tol"] = {linearSolverTolerance}\n'
        genScript += f'simulationConfig["Solver"]["Linear"]["MaxIts"] = {linearSolverMaxIterationCount}\n'
        genScript += f'simulationConfig["Solver"]["Order"] = {solverOrder}\n'
        genScript += f'simulationConfig["Solver"]["Device"] = "{solverDevice}"\n'
        genScript += '\n'

        if simulationProblemType.lower() == "magnetostatic":
            genScript += 'simulationConfig["Solver"]["Magnetostatic"] = {}\n'
            genScript += 'simulationConfig["Solver"]["Magnetostatic"]["Save"] = 2\n'
            genScript += '\n'
        elif simulationProblemType.lower() == "electrostatic":
            #help: https://awslabs.github.io/palace/stable/guide/problem/
            #   - need to specify grounded terminal
            #
            genScript += 'simulationConfig["Solver"]["Electrostatic"] = {}\n'
            genScript += 'simulationConfig["Solver"]["Electrostatic"]["Save"] = 2\n'
            genScript += '\n'
        elif simulationProblemType.lower() == "driven":
            genScript += self.getExcitationScriptLines(definitionsOnly=False)
            genScript += '\n'

            if self.form.simParamsUseNf2ff_palace.isChecked():
                boundaryConditionName = self.form.portNf2ffPalaceObjectList.currentText()   #boundary name is like 'Absorbing - airbox'
                boundaryConditionName = boundaryConditionName.split("-", 1)[1].strip()

                NSample = self.form.boundaryNf2ffPalaceNSample.value()
                theta = self.form.boundaryNf2ffPalaceTheta.value()
                phi = self.form.boundaryNf2ffPalacePhi.value()

                genScript += '# far field is computed on faces of absorbing boundary object\n'
                genScript += 'simulationConfig["Boundaries"]["Postprocessing"] = {}\n'
                genScript += f'simulationConfig["Boundaries"]["Postprocessing"]["FarField"] = {{"Attributes": palaceBoundaryObjectAttributes(groups, "{boundaryConditionName}"), "NSample": {NSample}, "ThetaPhis": [[{theta}, {phi}]]}}\n'
                genScript += '\n'
        elif simulationProblemType.lower() == "transient":
            genScript += self.getExcitationScriptLines(definitionsOnly=False)

        else:
            genScript += f"#INFO: No additional settings generated for simulation type '{simulationProblemType}'\n"
            genScript += "\n"

        currDir, nameBase = self.getCurrDir()

        genScript += f'json.dump(simulationConfig, open("{nameBase}.json", "w"), indent=2)\n'
        genScript += f'print("PASS - Palace config saved as {nameBase}.json, run solver: bash run_palace.sh")\n'
        genScript += '\n'
        genScript += '# mesh physical group table (name -> attribute), Palace config must stay plain JSON so it is written into separate file\n'
        genScript += f'with open("{nameBase}_mesh_groups.txt", "w") as outfile:\n'
        genScript += '\tfor name, attribute in groups["materials"].items():\n'
        genScript += '\t\toutfile.write(f"volume {name}\\t-> {attribute}\\n")\n'
        genScript += '\tfor (kind, name), attribute in groups["faces"].items():\n'
        genScript += '\t\toutfile.write(f"surface {kind} {name}\\t-> {attribute}\\t({\', \'.join(groups[\'faceObjects\'][(kind, name)])})\\n")\n'
        genScript += '\n'

        ##################################################################################
        # Write _palace.py script file to current directory.
        ##################################################################################
        if (not outputDir is None):
            fileName = f"{outputDir}/{nameBase}_palace.py"
        else:
            fileName = f"{currDir}/{nameBase}_palace.py"

        genScript = genScript.replace("## Palace simulation\n#\n#\n", "## Palace simulation\n#\n#\n" + self.getExportWarningsScriptLines(), 1)

        f = open(fileName, "w", encoding='utf-8')
        f.write(genScript)
        f.close()

        self.writePalaceRunScript(os.path.dirname(fileName), nameBase)

        # Show message or update status bar to inform user that exporting has finished.

        self.guiHelpers.displayMessage('Simulation script written to: ' + fileName + self.getExportWarningsMessage(), forceModal=True)
        print('Simulation script written to: ' + fileName)

        return

    def exportPalaceStep(self, freeCadObj, outputDir):
        """
        Export object into outputDir/stepfiles/<label>_gen_model.step, returns path relative to script folder.
        """
        stepfileOutputDir = os.path.join(outputDir, 'stepfiles')
        os.makedirs(stepfileOutputDir, exist_ok=True)
        stepModelFileName = freeCadObj.Label + "_gen_model.step"
        self.cadHelpers.exportSTEP([freeCadObj], os.path.join(stepfileOutputDir, stepModelFileName))
        print("Object exported as STEP into: " + stepModelFileName)
        return 'stepfiles/' + stepModelFileName

    def getPalaceRect(self, bb):
        """
        Rectangle for mesher from bounding box with zero thickness in one axis, None if box is not planar.
        """
        dims = [bb.XLength, bb.YLength, bb.ZLength]
        if min(dims) > 0:
            return None
        axis = int(np.argmin(dims))
        bbMin = [bb.XMin, bb.YMin, bb.ZMin]
        bbMax = [bb.XMax, bb.YMax, bb.ZMax]
        inPlane = [k for k in range(3) if k != axis]
        return {'axis': axis, 'coord': float(bbMin[axis]), 'min': [float(bbMin[k]) for k in inPlane], 'max': [float(bbMax[k]) for k in inPlane]}

    def getPalacePortRect(self, freeCadObj, direction, kind="port"):
        """
        Port or lumped part as rectangle: thin box becomes plate on its face touching geometry, thick box is collapsed too with warning
        (Palace lumped port is surface).
        """
        bb = self.getModelObjectBoundBox(freeCadObj.Label)
        if bb is None:
            return None
        bb = self.getLumpedPortPlaneBoundBox(bb, direction, freeCadObj.Label)
        rect = self.getPalaceRect(bb)
        if rect is None:
            dims = [bb.XLength, bb.YLength, bb.ZLength]
            directionAxis = {'x': 0, 'y': 1, 'z': 2}.get(str(direction).strip().lstrip('+-')[:1].lower(), None)
            candidates = [k for k in range(3) if k != directionAxis]
            axis = min(candidates, key=lambda k: dims[k])
            lo = [bb.XMin, bb.YMin, bb.ZMin][axis]
            hi = [bb.XMax, bb.YMax, bb.ZMax][axis]
            plane = self.getPlaneTouchingModel(axis, lo, hi, excludeLabel=freeCadObj.Label)
            rect = self.getPalaceRect(SimpleBoundBox.fromBoundBox(bb).collapsed(axis, plane))
            self.addExportWarning(f"{kind} '{freeCadObj.Label}' is 3D object, it's used as rectangle at {'xyz'[axis]} = {plane:g}, draw {kind} as face or thin box")
        return rect

    def getPalaceModel(self, itemsByClassName, outputDir, simulationName, meshFile, problemType):
        """
        Model description for palaceBuildMesh(): objects, materials, boundaries, ports, lumped parts and mesh sizes from GUI.
        STEP files are exported here.
        """
        def _num(value, default=0.0):
            try:
                return float(value)
            except:
                return default

        fcObjects = {obj.Label: obj for obj in self.cadHelpers.getObjects()}
        model = {'name': simulationName, 'meshFile': meshFile, 'objects': [], 'materials': {}, 'conductors': {}, 'sheets': {},
                 'ports': {}, 'lumpedParts': {}, 'meshSizes': {}, 'userMeshCode': []}
        isDriven = problemType.lower() == "driven"

        #
        #   Boundary condition objects, object which is also in material list (ie. airbox with air material) keeps its material
        #
        objectMaterial = {}
        for [item, currSetting] in itemsByClassName.get("MaterialSettingsItem", None) or []:
            for k in range(item.childCount()):
                objectMaterial[item.child(k).text(0)] = currSetting.getName()

        boundaryObjects = set()
        for [item, currSetting] in itemsByClassName.get("BoundaryConditionSettingsItem", None) or []:
            bcType = currSetting.getType()
            if bcType in ["Robin", "SurfaceImpedance"]:
                self.addExportWarning(f"boundary condition type '{bcType}' is not supported by Palace, objects of '{currSetting.getName()}' are skipped")
                continue
            for k in range(item.childCount()):
                childName = item.child(k).text(0)
                if not childName in fcObjects:
                    continue
                boundaryObjects.add(childName)
                obj = {'name': childName, 'file': self.exportPalaceStep(fcObjects[childName], outputDir), 'role': 'boundary', 'bc': bcType,
                       'group': bcType, 'priority': self.getItemPriority(item.parent().text(0) + ", " + item.text(0) + ", " + childName)}
                if childName in objectMaterial:
                    obj['material'] = objectMaterial[childName]
                model['objects'].append(obj)

        #
        #   Materials:
        #       - metal                   -> 'pec', removed from mesh, faces are PEC
        #       - userdefined conductor   -> 'conductor' (conductivity >= 1e5 S/m), removed from mesh, faces are Conductivity boundary
        #       - userdefined             -> 'dielectric', meshed volume
        #       - conducting sheet        -> 'sheet', planar object is rectangle with Conductivity + Thickness, 3D object is removed like metal
        #                                    and its faces are Conductivity boundary
        #
        for [item, currSetting] in itemsByClassName.get("MaterialSettingsItem", None) or []:
            if currSetting.getName() == 'Material Default':
                continue
            name = currSetting.getName()
            constants = currSetting.constants

            if currSetting.type == 'metal':
                role = 'pec'
            elif currSetting.type == 'conducting sheet':
                role = 'sheet'
                sheet = {'Conductivity': _num(constants['conductingSheetConductivity']),
                         'Thickness': _num(constants['conductingSheetThicknessValue']) * currSetting.getUnitsAsNumber(constants['conductingSheetThicknessUnits']) / 1e-3}
                if _num(constants.get('conductingSheetPermeability', 0)) > 0:
                    sheet['Permeability'] = _num(constants['conductingSheetPermeability'])
                model['sheets'][name] = sheet
                if not isDriven:
                    self.addExportWarning(f"conducting sheet '{name}': Palace conductivity boundary is just for driven problem, it's PEC for {problemType}")
            elif _num(constants.get('sigma', 0)) >= 1e5:
                role = 'conductor'
                model['conductors'][name] = {'Conductivity': _num(constants['sigma'])}
                if _num(constants.get('mue', 1)) not in [0.0, 1.0]:
                    model['conductors'][name]['Permeability'] = _num(constants['mue'])
                if not isDriven:
                    self.addExportWarning(f"material '{name}' is lossy metal, Palace conductivity boundary is just for driven problem, it's PEC for {problemType}")
            else:
                role = 'dielectric'
                material = {'Permittivity': _num(constants.get('epsilon', 1)) or 1.0}
                if _num(constants.get('mue', 1)) not in [0.0, 1.0]:
                    material['Permeability'] = _num(constants['mue'])
                if _num(constants.get('tand', 0)) != 0:
                    material['LossTan'] = _num(constants['tand'])
                if _num(constants.get('sigma', 0)) != 0:
                    material['Conductivity'] = _num(constants['sigma'])
                model['materials'][name] = material

            for k in range(item.childCount()):
                childName = item.child(k).text(0)
                if childName in boundaryObjects or not childName in fcObjects:
                    continue
                freeCadObj = fcObjects[childName]
                obj = {'name': childName, 'role': role, 'group': name,
                       'priority': self.getItemPriority(item.parent().text(0) + ", " + item.text(0) + ", " + childName)}

                # planar sheet is rectangle (Conductivity with thickness), 3D sheet object is removed like metal (Conductivity on its faces)
                rect = None
                if role == 'sheet':
                    bb = self.getModelObjectBoundBox(childName)
                    if bb is not None:
                        rect = self.getPalaceRect(bb)
                if rect is not None:
                    obj['rect'] = rect
                else:
                    obj['file'] = self.exportPalaceStep(freeCadObj, outputDir)
                model['objects'].append(obj)

        #
        #   Ports, each port object is one port with its own index
        #
        portIndex = 1
        for [item, currSetting] in itemsByClassName.get("PortSettingsItem", None) or []:
            for k in range(item.childCount()):
                childName = item.child(k).text(0)
                if not childName in fcObjects:
                    continue
                if currSetting.getType() != 'lumped':
                    self.addExportWarning(f"port '{currSetting.getName()}' type '{currSetting.getType()}' is not supported for Palace, just lumped ports")
                    continue
                if currSetting.infiniteResistance:
                    self.addExportWarning(f"port '{currSetting.getName()}' has infinite resistance, Palace lumped port needs resistance, port skipped")
                    continue

                internalPortName = currSetting.name + " - " + childName
                self.internalPortIndexNamesList[internalPortName] = portIndex

                direction = currSetting.direction
                if str(direction).lower() == 'custom':
                    direction = [float(v) for v in currSetting.directionCustomVector]
                elif len(str(direction)) == 1:
                    direction = '+' + str(direction).upper()

                obj = {'name': childName, 'role': 'port', 'group': internalPortName, 'priority': portIndex}
                rect = self.getPalacePortRect(fcObjects[childName], direction if isinstance(direction, str) else 'custom', kind="port")
                if rect is not None:
                    obj['rect'] = rect
                else:
                    obj['file'] = self.exportPalaceStep(fcObjects[childName], outputDir)
                model['objects'].append(obj)
                model['ports'][internalPortName] = {'Index': portIndex, 'Direction': direction, 'R': _num(currSetting.R) * _num(currSetting.getRUnits(), 1.0),
                                                    'Excitation': bool(currSetting.isActive)}
                self.sparamPorts.append([portIndex, internalPortName, currSetting.isActive, True])
                portIndex += 1

        #
        #   Lumped parts, for Palace lumped part is surface impedance Rs, Ls, Cs (units per square)
        #
        for [item, currSetting] in itemsByClassName.get("LumpedPartSettingsItem", None) or []:
            params = currSetting.params
            impedance = {}
            if 'r' in currSetting.getType().lower():
                impedance['Rs'] = _num(params['R']) * currSetting.getUnitsAsNumber(params['RUnits'])
            if 'l' in currSetting.getType().lower():
                impedance['Ls'] = _num(params['L']) * currSetting.getUnitsAsNumber(params['LUnits'])
            if 'c' in currSetting.getType().lower():
                impedance['Cs'] = _num(params['C']) * currSetting.getUnitsAsNumber(params['CUnits'])
            model['lumpedParts'][currSetting.getName()] = impedance

            for k in range(item.childCount()):
                childName = item.child(k).text(0)
                if not childName in fcObjects:
                    continue
                obj = {'name': childName, 'role': 'lumped', 'group': currSetting.getName(), 'priority': 0}
                rect = self.getPalacePortRect(fcObjects[childName], params.get('direction', 'z'), kind="lumped part")
                if rect is not None:
                    obj['rect'] = rect
                else:
                    obj['file'] = self.exportPalaceStep(fcObjects[childName], outputDir)
                model['objects'].append(obj)

        #
        #   Mesh sizes in mm from 'FEM Max Size' grid settings, where more settings apply to one object smaller size is used
        #
        items = itemsByClassName.get("GridSettingsItem", None) or []
        gridSettingsByName = {gridSettingsNode.text(0): gridSettingsInst for [gridSettingsNode, gridSettingsInst] in items}
        meshPrioritiesCount = self.form.meshPriorityTreeView.topLevelItemCount()
        for k in range(meshPrioritiesCount):
            [categoryName, gridName, objectName] = list(map(str.strip, self.form.meshPriorityTreeView.topLevelItem(k).text(0).split(',')))[:3]
            gridSettingsInst = gridSettingsByName.get(gridName, None)
            if gridSettingsInst is None or gridSettingsInst.getType() != 'FEM Max Size':
                continue
            femMesh = gridSettingsInst.femMesh
            toMm = gridSettingsInst.getUnitsAsNumber(femMesh.get('femMaxSizeUnits', 'mm')) / 1e-3
            spec = model['meshSizes'].setdefault(objectName, {})
            def setMin(key, value):
                spec[key] = min(spec[key], value) if key in spec else value
            if femMesh.get('femUseMaxElementSize'):
                setMin('size', femMesh['femMaxElementSize'] * toMm)
            if femMesh.get('femUseMaxBoundarySize'):
                setMin('boundary', femMesh['femMaxBoundarySize'] * toMm)
            if femMesh.get('femUseMaxFaceSize'):
                setMin('face', femMesh['femMaxFaceSize'] * toMm)
            if femMesh.get('femUseMaxDomainSize'):
                setMin('volume', femMesh['femMaxDomainSize'] * toMm)
            if femMesh.get('femUseSurfaceMeshSize'):
                spec['surface'] = [femMesh['femSurfaceMeshSizeSizeMin'] * toMm, femMesh['femSurfaceMeshSizeSizeMax'] * toMm,
                                   femMesh['femSurfaceMeshSizeDistanceMin'] * toMm, femMesh['femSurfaceMeshSizeDistanceMax'] * toMm]
            if femMesh.get('femUseMaxUserDefined') and femMesh.get('femMaxUserDefined', '').strip() != '':
                model['userMeshCode'].append(femMesh['femMaxUserDefined'])
            if len(spec) == 0:
                del model['meshSizes'][objectName]

        #
        #   Global settings: air element size lambda/15 at fmax or size set for air box object, air margin 0.3 lambda
        #
        freqRange = self.getFemExcitationFreqRange()
        wavelength = 3e8 / freqRange[1] / 1e-3 if freqRange is not None and freqRange[1] > 0 else None
        domainSizes = [model['meshSizes'][o['name']] for o in model['objects'] if o['role'] == 'boundary' and o['name'] in model['meshSizes']]
        domainSize = min([min(v for key, v in s.items() if key in ['size', 'volume']) for s in domainSizes if any(key in s for key in ['size', 'volume'])], default=None)
        modelSize = max([max(bb.XLength, bb.YLength, bb.ZLength) for bb in [self.getModelObjectBoundBox(o['name']) for o in model['objects']] if bb is not None], default=1.0)

        settings = {'problemType': problemType, 'lossyMetalsAsPec': not isDriven}
        meshSettings = getSolverMeshSettings(self.form, 'palace')
        if meshSettings['airSize_mm'] > 0:
            settings['airSize'] = meshSettings['airSize_mm']
            settings['airSizeComment'] = " (grid tab -> Solver mesh)"
        elif domainSize is not None:
            settings['airSize'] = domainSize
            settings['airSizeComment'] = " (size set for boundary object in GUI)"
        elif wavelength is not None:
            settings['airSize'] = wavelength / 15
            settings['airSizeComment'] = " (lambda/15 at fmax)"
        else:
            settings['airSize'] = modelSize / 10
            settings['airSizeComment'] = " (model size / 10)"
        if meshSettings['airMargin_mm'] > 0:
            settings['airMargin'] = meshSettings['airMargin_mm']
            settings['airMarginComment'] = " (grid tab -> Solver mesh)"
        elif wavelength is not None:
            settings['airMargin'] = 0.3 * wavelength
            settings['airMarginComment'] = " (0.3 lambda at fmax)"
        else:
            settings['airMargin'] = 0.5 * modelSize
            settings['airMarginComment'] = " (model size / 2)"

        # loop refinement needs wire mesh size, without it the air inside bondwire loops stays coarse (loop inductance too low)
        if meshSettings['loopFactor'] > 0:
            wireLikeWithoutSize = [o['name'] for o in model['objects'] if o['role'] in ['pec', 'conductor'] and o['name'] not in model['meshSizes'] and self.isWireLikeObject(o['name'])]
            if wireLikeWithoutSize:
                self.addExportWarning("wire-like metal objects without 'FEM Max Size' grid: " + ", ".join(wireLikeWithoutSize[:6]) + (" ..." if len(wireLikeWithoutSize) > 6 else "") +
                                      ", the air inside their loops is not refined (loop inductance comes from it), assign a FEM grid with max element size to the wires")
        model['settings'] = settings

        return model

    def isWireLikeObject(self, objectLabel):
        """
        True for one solid filling less than half of its bounding box (bondwire, loop), same rule as mesher uses for wires.
        """
        try:
            freeCadObj = [i for i in self.cadHelpers.getObjects() if i.Label == objectLabel][0]
            bb = freeCadObj.Shape.BoundBox
            bbVolume = bb.XLength * bb.YLength * bb.ZLength
            return bbVolume > 0 and freeCadObj.Shape.Volume < 0.5 * bbVolume
        except:
            return False

    def writePalaceRunScript(self, outputDir, nameBase):
        """
        Writes run_palace.sh: mesh generation (python script without gmsh GUI) and Palace binary started under mpirun.
        Palace wrapper 'palace' must NOT be started under mpirun, it starts own 1-process run on every MPI rank and these runs
        crash on each other's output files (json parse_error ... empty input).
        """
        runScript = ""
        runScript += "#!/bin/bash\n"
        runScript += "# Mesh -> Palace solve.\n"
        runScript += "#\n"
        runScript += "#   bash run_palace.sh                           # all CPU cores\n"
        runScript += "#   NP=16 PY=/path/to/venv/bin/python3 bash run_palace.sh\n"
        runScript += "#\n"
        runScript += "# Palace BINARY is started under mpirun (not 'palace' wrapper, under mpirun it starts one independent 1-process\n"
        runScript += "# run per rank which crash on each other's output files).\n"
        runScript += "set -eu\n"
        runScript += "cd \"$(dirname \"$0\")\"\n"
        runScript += "\n"
        runScript += "PALACE_BIN=${PALACE_BIN:-$(dirname \"$(command -v palace)\")/palace-x86_64.bin}\n"
        runScript += "MPIRUN=${MPIRUN:-mpirun}\n"
        runScript += "NP=${NP:-$(nproc)}\n"
        runScript += "PY=${PY:-python3}\n"
        runScript += "\n"
        runScript += "echo \"### 1. mesh + Palace config\"\n"
        runScript += f"SHOW_GMSH_GUI=0 $PY -u {nameBase}_palace.py\n"
        runScript += "\n"
        runScript += "echo \"### 2. Palace ($NP MPI processes)\"\n"
        runScript += f"$MPIRUN -np \"$NP\" \"$PALACE_BIN\" {nameBase}.json 2>&1 | tee palace.log\n"
        runScript += "\n"
        runScript += f"echo \"### done: {self.form.simParamsOutputDirectory_palace.text()}/port-S.csv\"\n"

        runScriptFileName = os.path.join(outputDir, "run_palace.sh")
        f = open(runScriptFileName, "w", encoding='utf-8', newline='\n')
        f.write(runScript)
        f.close()
        try:
            os.chmod(runScriptFileName, 0o755)
        except:
            pass
        print('Palace run script written to: ' + runScriptFileName)

    def getExcitationScriptLines(self, definitionsOnly=False):
        genScript = ""

        excitationCategory = self.form.objectAssignmentRightTreeWidget.findItems("Excitation", QtCore.Qt.MatchFixedString)
        if len(excitationCategory) >= 0:
            print("Excitation Settings detected")
            print("#")
            print("#EXCITATION")

            # FOR WHOLE SIMULATION THERE IS JUST ONE EXCITATION DEFINED, so first is taken!
            if (excitationCategory[0].childCount() > 0):
                item = excitationCategory[0].child(0)
                currSetting = item.data(0, QtCore.Qt.UserRole)  # At index 0 is Default Excitation.
                # Currently only 1 excitation is allowed. Multiple excitations could be managed by setting one of them as "selected" or "active", while all others are deactivated.
                # This would help the user to manage different analysis scenarios / excitation ranges.

                print("#name: " + currSetting.getName())
                print("#type: " + currSetting.getType())

                # genScript += "#######################################################################################################################################\n"
                # genScript += "# EXCITATION " + currSetting.getName() + "\n"
                # genScript += "#######################################################################################################################################\n"

                if (currSetting.getType() == 'sweep'):
                    genScript += 'simulationConfig["Solver"]["Driven"] = {}\n'
                    genScript += f'simulationConfig["Solver"]["Driven"]["MinFreq"] = {str(currSetting.sweep["fmin"])} * {str(currSetting.getUnitsAsNumber(currSetting.units))} / 1e9\n'
                    genScript += f'simulationConfig["Solver"]["Driven"]["MaxFreq"] = {str(currSetting.sweep["fmax"])} * {str(currSetting.getUnitsAsNumber(currSetting.units))} / 1e9\n'

                    if currSetting.sweep["fmax"] - currSetting.sweep["fmin"] > 0:
                        genScript += f'simulationConfig["Solver"]["Driven"]["FreqStep"] = (simulationConfig["Solver"]["Driven"]["MaxFreq"] - simulationConfig["Solver"]["Driven"]["MinFreq"]) / {str(currSetting.sweep["npoints"])}\n'
                    else:
                        genScript += f'simulationConfig["Solver"]["Driven"]["FreqStep"] = 0.1\n'

                    genScript += f'simulationConfig["Solver"]["Driven"]["SaveStep"] = {self.form.simParamsSaveStep_palace.value()}\n'
                    genScript += f'simulationConfig["Solver"]["Driven"]["AdaptiveTol"] = 1e-3\n'

                    if self.form.simParamsUseNf2ff_palace.isChecked():
                        genScript += f'simulationConfig["Solver"]["Driven"]["Samples"] = []\n'
                        genScript += f'simulationConfig["Solver"]["Driven"]["Samples"].append({{"Type": "Point", "Freq": [{self.form.boundaryNf2ffPalaceFreq.value()/1e3}], "SaveStep": 1}})\n'

                elif (currSetting.getType() == 'fem_gaussian'):
                    genScript += "import math\n"
                    genScript += "\n"
                    genScript += f'f_center = {str(currSetting.femGaussian["f0"])} * {str(currSetting.getUnitsAsNumber(currSetting.units))} / 1e9\n'
                    genScript += f'f_min = {str(currSetting.femGaussian["f0"] - currSetting.femGaussian["fc"])} * {str(currSetting.getUnitsAsNumber(currSetting.units))} / 1e9\n'
                    genScript += f'f_max = {str(currSetting.femGaussian["f0"] + currSetting.femGaussian["fc"])} * {str(currSetting.getUnitsAsNumber(currSetting.units))} / 1e9\n'
                    genScript += f'BW = f_max - f_min\n'
                    genScript += f'\n'
                    genScript += f'# Gaussian width in time domain\n'
                    genScript += f'# width ≈ 1 / (π * f_max) gives -20dB at f_max\n'
                    genScript += f'width_ns = 1.0 / (math.pi * BW) * 1e9\n'
                    genScript += f'\n'
                    genScript += f'# Simulation must run long enough for the pulse to decay\n'
                    genScript += f'# and for filter ringdown - at least 10 periods at lowest frequency\n'
                    genScript += f'T_min = 1.0 / f_min * 1e9\n'
                    genScript += f'MaxTime = 10 * T_min\n'
                    genScript += f'\n'
                    genScript += f'# Time step must satisfy Nyquist at highest frequency\n'
                    genScript += f'# dt < 1 / (2 * f_max)\n'
                    genScript += f'TimeStep = 1.0 / (2 * f_max) * 1e9 / {str(currSetting.femGaussian["timeOversampling"])}  # time oversampling\n'
                    genScript += f'\n'
                    genScript += f'simulationConfig["Solver"]["Transient"] = {{}}\n'
                    genScript += f'simulationConfig["Solver"]["Transient"]["Type"] = "GeneralizedAlpha"\n'
                    genScript += f'simulationConfig["Solver"]["Transient"]["Excitation"] = "ModulatedGaussian"  # Gaussian centered at f_center, no DC\n'
                    genScript += f'simulationConfig["Solver"]["Transient"]["ExcitationFreq"] = f_center / 1e9\n'
                    genScript += f'simulationConfig["Solver"]["Transient"]["ExcitationWidth"] = width_ns\n'
                    genScript += f'simulationConfig["Solver"]["Transient"]["MaxTime"] = MaxTime\n'
                    genScript += f'simulationConfig["Solver"]["Transient"]["TimeStep"] = TimeStep\n'
                    genScript += f'simulationConfig["Solver"]["Transient"]["SaveStep"] = {self.form.simParamsSaveStep_palace.value()}\n'

                else:
                    genScript += f"# ERROR: Excitation type \"{currSetting.getType()}\" not implemented in script generator!\n"

                genScript += "\n"
            else:
                self.guiHelpers.displayMessage("<font color='#FF0000'><b>Missing excitation, please define one.</b></font>")
                pass
            pass
        return genScript

    def drawS11ButtonClicked(self, outputDir=None, portName=""):
        genScript = ""

        itemsByClassName = self.getItemsByClassName()
        items = itemsByClassName.get("PortSettingsItem", None)

        genScriptPortCount = 1
        portNamesAndNumbersList = {}
        for [item, currSetting] in items:
            for k in range(item.childCount()):
                childName = item.child(k).text(0)
                portName = f"{currSetting.name} - {childName}"

                # PORT openEMS GENERATION INTO VARIABLE
                if (currSetting.getType() == 'lumped'):
                    portNamesAndNumbersList[portName] = genScriptPortCount
                    genScriptPortCount += 1

        sourcePortName = self.form.drawS11Port.currentText()
        sourcePortNumber = portNamesAndNumbersList[sourcePortName]

        genScript += f"## Palace simulation - S{sourcePortNumber}{sourcePortNumber}\n"
        genScript += "#\n"
        genScript += "#\n"
        genScript += "import matplotlib.pyplot as plt\n"
        genScript += "import pandas as pd\n"
        genScript += "\n"

        #
        #   Get port names and their numbers from GUI
        #
        genScript += "###############################################################################\n"
        genScript += "# PORT NAME AND THEIR NUMBERS LIST\n"
        genScript += "###############################################################################\n"
        genScript += "portNamesAndNumbersList = {}\n"
        for portName, portNumber in portNamesAndNumbersList.items():
            genScript += f'portNamesAndNumbersList["{portName}"] = {portNumber}\n'
        genScript += "\n"

        genScript += "###############################################################################\n"
        genScript += "# PLOT S DATA\n"
        genScript += "###############################################################################\n"
        genScript += "\n"
        genScript += "# Load the file without header (columns will be numbered 0, 1, 2...)\n"
        genScript += f'df = pd.read_csv("{self.form.simParamsOutputDirectory_palace.text()}/port-S.csv", comment="#", skiprows=1, header=None)\n'
        genScript += "\n"
        genScript += "# Plot: column 0 = Frequency, column 1 = S11\n"
        genScript += 'plt.plot(df.iloc[:, 0], df.iloc[:, 1], marker="o", label="|S11| (dB)")\n'
        genScript += "\n"
        genScript += 'plt.xlabel("Frequency (GHz)")\n'
        genScript += 'plt.ylabel("S11 (dB)")\n'
        genScript += 'plt.title("S11 vs Frequency")\n'
        genScript += 'plt.grid(True)\n'
        genScript += 'plt.legend()\n'
        genScript += 'plt.tight_layout()\n'
        genScript += 'plt.show()\n'
        genScript += '\n'

        #
        # WRITE OpenEMS Script file into current dir
        #
        currDir, nameBase = self.getCurrDir()

        self.createOuputDir(outputDir)
        if (not outputDir is None):
            fileName = f"{outputDir}/{nameBase}_draw_S{sourcePortNumber}{sourcePortNumber}.py"
        else:
            fileName = f"{currDir}/{nameBase}_draw_S{sourcePortNumber}{sourcePortNumber}.py"

        f = open(fileName, "w", encoding='utf-8')
        f.write(genScript)
        f.close()
        print('Draw result from simulation file written into: ' + fileName)
        self.guiHelpers.displayMessage('Draw result from simulation file written into: ' + fileName, forceModal=False)

    def drawS21ButtonClicked(self, outputDir=None, sourcePortName="", targetPortName=""):
        genScript = ""

        itemsByClassName = self.getItemsByClassName()
        items = itemsByClassName.get("PortSettingsItem", None)

        genScriptPortCount = 1
        portNamesAndNumbersList = {}
        for [item, currSetting] in items:
            for k in range(item.childCount()):
                childName = item.child(k).text(0)
                portName = f"{currSetting.name} - {childName}"

                # PORT openEMS GENERATION INTO VARIABLE
                if (currSetting.getType() == 'lumped'):
                    portNamesAndNumbersList[portName] = genScriptPortCount
                    genScriptPortCount += 1

        sourcePortName = self.form.drawS11Port.currentText()
        sourcePortNumber = portNamesAndNumbersList[sourcePortName]
        targetPortName = self.form.drawS21Target.currentText()
        targetPortNumber = portNamesAndNumbersList[targetPortName]

        graphSParamName = f"S{targetPortNumber}{sourcePortNumber}"

        #
        # This is for S21 for Driven problem when S-params are stored in file port-S.csv
        #
        if self.form.simParamsSimulationTypeList_palace.currentText().lower() == "driven":
            genScript += f"## Palace simulation - {graphSParamName}\n"
            genScript += f"#\ttransfer from '{sourcePortName}' -> '{targetPortName}'\n"
            genScript += "#\n"
            genScript += "#\n"
            genScript += "import matplotlib.pyplot as plt\n"
            genScript += "import pandas as pd\n"
            genScript += "\n"

            #
            #   Get port names and their numbers from GUI
            #
            genScript += "###############################################################################\n"
            genScript += "# PORT NAME AND THEIR NUMBERS LIST\n"
            genScript += "###############################################################################\n"
            genScript += "portNamesAndNumbersList = {}\n"
            for portName, portNumber in portNamesAndNumbersList.items():
                genScript += f'portNamesAndNumbersList["{portName}"] = {portNumber}\n'
            genScript += "\n"

            genScript += "###############################################################################\n"
            genScript += "# PLOT S DATA\n"
            genScript += "###############################################################################\n"
            genScript += f"sourcePortName = '{sourcePortName}'\n"
            genScript += f"targetPortName = '{targetPortName}'\n"
            genScript += "sourcePortNumber = portNamesAndNumbersList[sourcePortName]\n"
            genScript += "targetPortNumber = portNamesAndNumbersList[targetPortName]\n"
            genScript += "\n"
            genScript += "\n"
            genScript += "# Load the file without header (columns will be numbered 0, 1, 2...)\n"
            genScript += f'df = pd.read_csv("{self.form.simParamsOutputDirectory_palace.text()}/port-S.csv", comment="#", skiprows=1, header=None)\n'
            genScript += "\n"
            genScript += f"# Plot: column 0 = Frequency, column 1 = {graphSParamName}\n"
            genScript += f'plt.plot(df.iloc[:, 0], df.iloc[:, {(targetPortNumber-1)*2+1}], marker="o", label="|{graphSParamName}| (dB)")\n'
            genScript += "\n"
            genScript += 'plt.xlabel("Frequency (GHz)")\n'
            genScript += f'plt.ylabel("{graphSParamName} (dB)")\n'
            genScript += f'plt.title("{graphSParamName} vs Frequency")\n'
            genScript += 'plt.grid(True)\n'
            genScript += 'plt.legend()\n'
            genScript += 'plt.tight_layout()\n'
            genScript += 'plt.show()\n'
            genScript += '\n'

        #
        # This is for S21 for Driven problem when there is file port-V.csv and S-params must be calculated from voltage values on ports
        #
        if self.form.simParamsSimulationTypeList_palace.currentText().lower() == "transient":
            genScript += "import numpy as np\n"
            genScript += "import math\n"
            genScript += "\n"

            #
            #   Get port names and their numbers from GUI
            #
            genScript += "###############################################################################\n"
            genScript += "# PORT NAME AND THEIR NUMBERS LIST\n"
            genScript += "###############################################################################\n"
            genScript += "portNamesAndNumbersList = {}\n"
            for portName, portNumber in portNamesAndNumbersList.items():
                genScript += f'portNamesAndNumbersList["{portName}"] = {portNumber}\n'
            genScript += "\n"

            genScript += f"sourcePortName = '{sourcePortName}'\n"
            genScript += f"targetPortName = '{targetPortName}'\n"
            genScript += "sourcePortNumber = portNamesAndNumbersList[sourcePortName]\n"
            genScript += "targetPortNumber = portNamesAndNumbersList[targetPortName]\n"
            genScript += "\n"

            genScript += "# After simulation, Palace outputs port-V.csv with time, V(t), I(t)\n"
            genScript += "#\n"
            genScript += "data = np.loadtxt('sim_result_transient/port-V.csv', delimiter=',', skiprows=1)\n"
            genScript += "t = data[:, 0]  # time in ns\n"
            genScript += f"V_in = np.sqrt(data[:, sourcePortNumber*2]**2 + data[:, sourcePortNumber*2+1]**2)\n"
            genScript += f"V_out = np.sqrt(data[:, targetPortNumber*2]**2 + data[:, targetPortNumber*2+1]**2)\n"
            genScript += "\n"
            genScript += "dt = t[1] - t[0]\n"
            genScript += "freq = np.fft.rfftfreq(len(t), d=dt * 1e-9)  # Hz\n"
            genScript += "\n"
            genScript += "V_in_f = np.fft.rfft(V_in)\n"
            genScript += "V_out_f = np.fft.rfft(V_out)\n"
            genScript += "\n"
            genScript += "S21 = 20 * np.log10(np.abs(V_out_f / V_in_f))\n"
            genScript += "\n"
            genScript += "import matplotlib.pyplot as plt\n"
            genScript += "mask = (freq >= 20e6) & (freq <= 40e6)\n"
            genScript += "plt.plot(freq[mask] / 1e6, S21[mask])\n"
            genScript += "plt.xlabel('Frequency (MHz)')\n"
            genScript += f"plt.ylabel('{graphSParamName} (dB)')\n"
            genScript += "plt.grid(True)\n"
            genScript += "plt.show()\n"
            genScript += "\n"
            genScript += "plt.subplot(1, 2, 1)\n"
            genScript += "plt.plot(V_in)\n"
            genScript += "plt.subplot(1, 2, 2)\n"
            genScript += "plt.plot(V_out)\n"
            genScript += "plt.show()\n"
            genScript += "\n"

        #
        # WRITE OpenEMS Script file into current dir
        #
        currDir, nameBase = self.getCurrDir()

        self.createOuputDir(outputDir)
        if (not outputDir is None):
            fileName = f"{outputDir}/{nameBase}_draw_{graphSParamName}.py"
        else:
            fileName = f"{currDir}/{nameBase}_draw_{graphSParamName}.py"

        f = open(fileName, "w", encoding='utf-8')
        f.write(genScript)
        f.close()
        print('Draw result from simulation file written into: ' + fileName)
        self.guiHelpers.displayMessage('Draw result from simulation file written into: ' + fileName, forceModal=False)

    #
    #	Write NF2FF Button clicked, generate script to display far field pattern
    #
    def writeNf2ffButtonClicked(self, outputDir=None):
        currDir, nameBase = self.getCurrDir()

        genScript = ""
        genScript += "# Plot far field for structure.\n"
        genScript += "#\n"
        genScript += "import os\n"
        genScript += "try:\n"
        genScript += "\tfrom basicpalacesolverhelperpackage import PlotDiagramUtils\n"
        genScript += "except ModuleNotFoundError as e:\n"
        genScript += "\traise ModuleNotFoundError(\"Palace plot helper package is missing, install it: pip install basicpalacesolverhelperpackage\") from e\n"
        genScript += "\n"

        genScript += "currDir = os.getcwd()\n"
        genScript += "print(currDir)\n"
        genScript += "\n"

        genScript += "#######################################################################################################################################\n"
        genScript += "# Farfield plot and 3D gain generated\n"
        genScript += "#######################################################################################################################################\n"
        genScript += "plotter = PlotDiagramUtils()\n"
        genScript += f'plotter.readFromFileComputeDirectivityRadiationDiagram("{self.form.simParamsOutputDirectory_palace.text()}/farfield-rE.csv", outputfile="{nameBase}_farfield.vtk")\n'
        genScript += "\n"

        #
        # WRITE OpenEMS Script file into current dir
        #
        currDir, nameBase = self.getCurrDir()

        self.createOuputDir(outputDir)
        if (not outputDir is None):
            fileName = f"{outputDir}/{nameBase}_draw_NF2FF.py"
        else:
            fileName = f"{currDir}/{nameBase}_draw_NF2FF.py"

        f = open(fileName, "w", encoding='utf-8')
        f.write(genScript)
        f.close()
        print('Script to display far field written into: ' + fileName)
        self.guiHelpers.displayMessage('Script to display far field written into: ' + fileName, forceModal=False)

'''
Notes TODO 20Apr2026:
    + DONE -> FreeCAD addon not saving and loading palace simulation params tab - need to be added
    + creating mesh named group based on what is used, cannot use same elements in multiple groups this cause palace solver error that element in multiple attributes
        + this makes question how to properly create named group for materials??? probably just 3D mesh for them since if material is defined
          on 2D surface it is specified as boundary
        + for boundary generate just 2D surface mesh
        + whole strategy of import STEP files and define mesh on them should be re-think and not to mesh everything just needed objects
    + for Magnetostatic solver SuperLU crash with some error of matrix with zeros or whatever but AMS solver run OK
    + port priority is wrong, take it from FreeCAD widget!
    - forbit multiple assignment same object to multiple material
    - forbit multiple assignment same object to multiple boundaries
    - grid mesh size not working properly
    - grid custom mesh in generated code
    - add custom code snippet into lumped part, this will enable to add conductivity boundary into result code since lumped part is translated as surface
        - or implement Material "conducting sheet" to generate palace simulationConfig["Boundaries"]["Conductivity"] = [{"Attributes": [...], "Conductivity": ..., "Permitivity": ..., "Thickness": ...}]    
    - ...
'''



