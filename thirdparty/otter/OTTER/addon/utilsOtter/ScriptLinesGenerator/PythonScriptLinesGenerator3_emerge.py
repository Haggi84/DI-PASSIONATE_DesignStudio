#   author: Lubomir Jagos
#
#
import os
from PySide import QtGui, QtCore, QtWidgets
import numpy as np
import re
import math

from utilsOtter.GlobalFunctions.GlobalFunctions import _bool, _r, _r2
from utilsOtter.ScriptLinesGenerator.OctaveScriptLinesGenerator2 import OctaveScriptLinesGenerator2
from utilsOtter.GuiHelpers.GuiHelpers import GuiHelpers
from utilsOtter.GuiHelpers.FactoryCadInterface import FactoryCadInterface

from utilsOtter.ScriptLinesGenerator.CommonScriptLinesGenerator import CommonScriptLinesGenerator, SimpleBoundBox
from utilsOtter.ScriptLinesGenerator.PythonScriptLinesGenerator2_openems import PythonScriptLinesGenerator2_openems
from utilsOtter.GuiHelpers.SolverMeshSettings import getSolverMeshSettings


class PythonScriptLinesGenerator3_emerge(PythonScriptLinesGenerator2_openems):

    #
    #   constructor, get access to form GUI
    #
    def __init__(self, form, statusBar = None):
        super(PythonScriptLinesGenerator3_emerge, self).__init__(form, statusBar)
        self.portBoundaryConditionScriptLinesBuffer = []
        self.createdObjectNameList = []
        self.createdObjectBoundaryNameList = []

        self.cadHelpers = FactoryCadInterface.createHelper()

    def getCoordinateSystemScriptLines(self):
        genScript = ""

        genScript += "#######################################################################################################################################\n"
        genScript += "# COORDINATE SYSTEM\n"
        genScript += "#######################################################################################################################################\n"

        """ # Till now not used, just using rectangular coordination type, cylindrical MUST BE IMPLEMENTED!
        gridCoordsType = self.getModelCoordsType()
        if (gridCoordsType == "rectangular"):
            genScript += "CSX = InitCSX('CoordSystem',0); # Cartesian coordinate system.\n"
        elif (gridCoordsType == "cylindrical"):
            genScript += "CSX = InitCSX('CoordSystem',1); # Cylindrical coordinate system.\n"
        else:
            genScript += "%%%%%% ERROR GRID COORDINATION SYSTEM TYPE UNKNOWN"				
        """

        genScript += "def mesh():\n"
        genScript += "\tx,y,z\n"
        genScript += "\n"
        genScript += "mesh.x = np.array([]) # mesh variable initialization (Note: x y z implies type Cartesian).\n"
        genScript += "mesh.y = np.array([])\n"
        genScript += "mesh.z = np.array([])\n"
        genScript += "\n"
        genScript += "openEMS_grid = CSX.GetGrid()\n"
        genScript += "openEMS_grid.SetDeltaUnit(unit) # First call with empty mesh to set deltaUnit attribute.\n"
        genScript += "\n"

        return genScript

    def getMaterialDefinitionsScriptLines(self, items, outputDir=None, generateObjects=True):
        genScript = ""

        genScript += "#######################################################################################################################################\n"
        genScript += "# MATERIALS AND GEOMETRY\n"
        genScript += "#######################################################################################################################################\n"

        # PEC is created by default due it's used when microstrip port is defined, so it's here to have it here.
        # Note that the user will need to create a metal named 'PEC' and populate it to avoid a warning
        # about "no primitives assigned to metal 'PEC'".

        if not items:
            return genScript

        materialCounter = -1    #increment of this variable is at beginning f for loop so start at 0
        simObjectCounter = 0

        # now export material children, if it's object export as STL, if it's curve export as curve
        if (generateObjects):
            for [item, currSetting] in items:

                #
                #   Materials are stored in variables in python script, so this is counter to create universal name ie. material_1, material_2, ...
                #
                materialCounter += 1

                print(currSetting)
                if (currSetting.getName() == 'Material Default'):
                    print("#Material Default")
                    print("---")
                    continue

                print("#")
                print("#MATERIAL")
                print("#name: " + currSetting.getName())
                print("#epsilon, mue, tand, sigma")
                print("#" + str(currSetting.constants['epsilon']) + ", " + str(currSetting.constants['mue']) + ", " + str(currSetting.constants['kappa']) + ", " + str(currSetting.constants['sigma']))

                genScript += f"## MATERIAL - {currSetting.getName()}\n"
                materialPythonVariable = f"materialList['{currSetting.getName()}']"

                if (currSetting.type == 'metal'):
                    #this is working, same as in lib
                    # genScript += f"{materialPythonVariable} = em.Material(name='{currSetting.getName()}', _metal=True, cond=1e30)\n"

                    #using predefined library with materials
                    genScript += f"helperFunctionsObj.addMaterial('{currSetting.getName()}', em.lib.PEC)\n"

                    self.internalMaterialIndexNamesList[currSetting.getName()] = materialPythonVariable
                elif (currSetting.type == 'userdefined'):
                    self.internalMaterialIndexNamesList[currSetting.getName()] = materialPythonVariable

                    smp_args = []
                    if str(currSetting.constants['epsilon']) != "0":
                        smp_args.append(f"er={str(currSetting.constants['epsilon'])}")
                    if str(currSetting.constants['mue']) != "0":
                        smp_args.append(f"ur={str(currSetting.constants['mue'])}")
                    if (str(currSetting.constants['tand']) != "0"):
                        smp_args.append(f"tand={str(currSetting.constants['tand'])}")
                    if str(currSetting.constants['sigma']) != "0":
                        smp_args.append(f"cond={str(currSetting.constants['sigma'])}")

                    genScript += f"helperFunctionsObj.addMaterial('{currSetting.getName()}', em.Material(name='{currSetting.getName()}', " + ", ".join(smp_args) + "))\n"

                    # a metal volume (sigma >= 1e5 S/m) needs no warning: EMerge >= 3.0 turns it into a surface impedance
                    # (conductor loss kept, nothing meshed inside); EMerge 2.x meshed inside it

                # first print all current material children names
                for k in range(item.childCount()):
                    childName = item.child(k).text(0)
                    print("##Children:")
                    print("\t" + childName)

                # now export material children, if it's object export as STL, if it's curve export as curve
                for k in range(item.childCount()):
                    simObjectCounter += 1               #counter for objects
                    childName = item.child(k).text(0)

                    self.createdObjectNameList.append(childName)    #add this object into list of created objects which will be used to create named group in exported mesh

                    #
                    #	getting item priority
                    #
                    objModelPriorityItemName = item.parent().text(0) + ", " + item.text(0) + ", " + childName
                    objModelPriority = self.getItemPriority(objModelPriorityItemName)

                    # getting reference to FreeCAD object
                    freeCadObj = [i for i in self.cadHelpers.getObjects() if (i.Label) == childName][0]

                    #
                    #   Set color of material by object, this can overwite it color multiple time, what to do, good enough for now
                    #
                    colorStr = ""
                    colorTuple = freeCadObj.ViewObject.DiffuseColor[0]
                    colorStr += "#" + format(int(255 * colorTuple[0]), '02x') + format(int(255 * colorTuple[1]), '02x') + format(int(255 * colorTuple[2]), '02x')
                    genScript += f"helperFunctionsObj.setMaterialColor('{currSetting.getName()}', color='{colorStr}', opacity={min(1.0, max(0.0, 1.0 - freeCadObj.ViewObject.Transparency/100.0))})\n"
                    genScript += f"\n"

                    #
                    #   HERE IS OBJECT GENERATOR THERE ARE FEW SPECIAL CASES WHICH ARE HANDLED FIRST AND IF OBJECT IS NORMAL STRUCTURE AT THE END IS GENERATED AS .stl FILR:
                    #       - conducting sheet = just plane is generated, in case of 3D object shell of object bounding box is generated
                    #       - discretized edge = curve from line is generated
                    #       - sketch           = curve is generated from its vertices
                    #
                    if (currSetting.type == 'conducting sheet'):
                        #
                        #   Here comes object generator for conducting sheet. It's possible to define it as plane in XY, XZ, YZ plane in cartesian coords and XY plane in cylindrical coords.
                        #   So in case of conducting sheet no .stl is generated, it will be generated rectangle based on bounding box.
                        #
                        genScript += "##conducting sheet object\n"
                        genScript += f"#object Label: {freeCadObj.Label}\n"
                        bbCoords = freeCadObj.Shape.BoundBox

                        if (freeCadObj.Name.find("Sketch") > -1):
                            #
                            # If object is sketch then it's added as it outline
                            #

                            normDir, elevation, points = self.getSketchPointsForConductingSheet(freeCadObj)
                            if not normDir.startswith("ERROR"):
                                genScript += "points = [[],[]]\n"
                                if len(points[0])  == 0:
                                    genScript += "## ERROR, no points for polygon for conducting sheet nothing generated"
                                else:
                                    for k in range(len(points[0])):
                                        genScript += f"points[0].append({points[0][k]})\n"
                                        genScript += f"points[1].append({points[1][k]})\n"
                                genScript += "\n"

                                genScript += f"{materialPythonVariable}.AddPolygon(points, '{normDir}', {elevation}, priority={objModelPriority})\n"
                                genScript += "\n"
                                print("material conducting sheet: polygon into conducting sheet added.")
                            else:
                                genScript += f"## {normDir}\n"
                                genScript += "\n"
                                print("ERROR: material conducting sheet: " + normDir)

                        elif (_r(bbCoords.XMin) == _r(bbCoords.XMax) or _r(bbCoords.YMin) == _r(bbCoords.YMax) or _r(bbCoords.ZMin) == _r(bbCoords.ZMax)):
                            #
                            # Adding planar object into conducting sheet, if it consists from faces then each face is added as polygon.
                            #

                            normDir, elevation, facesList = self.getFacePointsForConductingSheet(freeCadObj)
                            if normDir != "":
                                for face in facesList:
                                    genScript += f"points = [[],[]]\n"
                                    for pointIndex in range(len(face[0])):
                                        genScript += f"points[0].append({face[0][pointIndex]})\n"
                                        genScript += f"points[1].append({face[1][pointIndex]})\n"
                                        genScript += "\n"
                                    genScript += f"{materialPythonVariable}.AddPolygon(points, '{normDir}', {elevation}, priority={objModelPriority})\n"
                                    genScript += "\n"
                            else:
                                genScript += f"#\tObject has no faces, conducting sheet is generated based on object bounding box since it's planar.\n"
                                genScript += f"{materialPythonVariable}.AddBox([{_r(bbCoords.XMin)},{_r(bbCoords.YMin)},{_r(bbCoords.ZMin)}], [{_r(bbCoords.XMax)},{_r(bbCoords.YMax)},{_r(bbCoords.ZMax)}], priority={objModelPriority})\n"
                                genScript += "\n"

                        else:
                            #
                            # If object is 3D object then it's boundaries are added as conducting sheets.
                            #

                            genScript += f"#\tObject is 3D so there are sheets on its boundary box generated.\n"
                            genScript += f"{materialPythonVariable}.AddBox([{_r(bbCoords.XMin)}, {_r(bbCoords.YMin)}, {_r(bbCoords.ZMin)}], [{_r(bbCoords.XMax)}, {_r(bbCoords.YMax)}, {_r(bbCoords.ZMin)}], priority={objModelPriority})\n"
                            genScript += f"{materialPythonVariable}.AddBox([{_r(bbCoords.XMin)}, {_r(bbCoords.YMin)}, {_r(bbCoords.ZMin)}], [{_r(bbCoords.XMax)}, {_r(bbCoords.YMin)}, {_r(bbCoords.ZMax)}], priority={objModelPriority})\n"
                            genScript += f"{materialPythonVariable}.AddBox([{_r(bbCoords.XMin)}, {_r(bbCoords.YMin)}, {_r(bbCoords.ZMin)}], [{_r(bbCoords.XMin)}, {_r(bbCoords.YMax)}, {_r(bbCoords.ZMax)}], priority={objModelPriority})\n"
                            genScript += f"{materialPythonVariable}.AddBox([{_r(bbCoords.XMin)}, {_r(bbCoords.YMin)}, {_r(bbCoords.ZMax)}], [{_r(bbCoords.XMax)}, {_r(bbCoords.YMax)}, {_r(bbCoords.ZMax)}], priority={objModelPriority})\n"
                            genScript += f"{materialPythonVariable}.AddBox([{_r(bbCoords.XMin)}, {_r(bbCoords.YMax)}, {_r(bbCoords.ZMin)}], [{_r(bbCoords.XMax)}, {_r(bbCoords.YMax)}, {_r(bbCoords.ZMax)}], priority={objModelPriority})\n"
                            genScript += f"{materialPythonVariable}.AddBox([{_r(bbCoords.XMax)}, {_r(bbCoords.YMin)}, {_r(bbCoords.ZMin)}], [{_r(bbCoords.XMax)}, {_r(bbCoords.YMax)}, {_r(bbCoords.ZMax)}], priority={objModelPriority})\n"
                            genScript += "\n"

                    elif (freeCadObj.Name.find("Discretized_Edge") > -1):
                        #
                        #	Adding discretized curve
                        #

                        curvePoints = freeCadObj.Points
                        genScript += "points = [[],[],[]]\n"
                        for k in range(0, len(curvePoints)):
                            genScript += f"points[0].append({_r(curvePoints[k].x)})\n"
                            genScript += f"points[1].append({_r(curvePoints[k].y)})\n"
                            genScript += f"points[2].append({_r(curvePoints[k].z)})\n"
                            genScript += "\n"

                        genScript += f"{materialPythonVariable}.AddCurve(points, priority={objModelPriority})\n"
                        genScript += "\n"
                        print("Curve added to generated script using its points.")

                    elif (freeCadObj.Name.find("Sketch") > -1):
                        #
                        #	Adding JUST LINE SEGMENTS FROM SKETCH, THIS NEED TO BE IMPROVED TO PROPERLY GENERATE CURVE FROM SKETCH,
                        #	there can be circle, circle arc and maybe something else in sketch geometry
                        #

                        genScript += "points = [[],[],[]]\n"

                        """
                        # WRONG SINCE StartPoint, EndPoint are defined in XY and not in absolute coordinates
                        for geometryObj in freeCadObj.Geometry:
                            if (str(type(geometryObj)).find("LineSegment") > -1):
                                genScript += f"points[0].append({geometryObj.StartPoint.x})\n"
                                genScript += f"points[1].append({geometryObj.StartPoint.y})\n"
                                genScript += f"points[2].append({geometryObj.StartPoint.z})\n"

                                genScript += f"points[0].append({geometryObj.EndPoint.x})\n"
                                genScript += f"points[1].append({geometryObj.EndPoint.y})\n"
                                genScript += f"points[2].append({geometryObj.EndPoint.z})\n"

                                genScript += "\n"
                        """

                        for v in freeCadObj.Shape.OrderedVertexes:
                            genScript += f"points[0].append({_r(v.X)})\n"
                            genScript += f"points[1].append({_r(v.Y)})\n"
                            genScript += f"points[2].append({_r(v.Z)})\n"
                            genScript += "\n"

                        #   HERE IS MADE ASSUMPTION THAT:
                        #       We suppose in sketch there are no mulitple closed sketches
                        #
                        #   Add first vertex into list
                        #
                        v = freeCadObj.Shape.OrderedVertexes[0]
                        if len(freeCadObj.OpenVertices) == 0:
                            genScript += f"points[0].append({_r(v.X)})\n"
                            genScript += f"points[1].append({_r(v.Y)})\n"
                            genScript += f"points[2].append({_r(v.Z)})\n"
                            genScript += "\n"

                        genScript += f"{materialPythonVariable}.AddCurve(points, priority={objModelPriority})\n"
                        genScript += "\n"
                        print("Line segments from sketch added.")

                    elif freeCadObj.Name.startswith('Sphere'):
                        bbox = freeCadObj.Shape.BoundBox

                        radius = max(
                            bbox.XMax - bbox.XMin,
                            bbox.YMax - bbox.YMin,
                            bbox.ZMax - bbox.ZMin
                        ) / 2.0

                        # Get center
                        position = (
                            (bbox.XMin + bbox.XMax) / 2,
                            (bbox.YMin + bbox.YMax) / 2,
                            (bbox.ZMin + bbox.ZMax) / 2
                        )

                        cadLengthUnit = self.getFreeCADInternalUnitLengthStr()
                        genScript += f"position = {str(position)}\n"
                        genScript += f"position = tuple([x*{cadLengthUnit} for x in position])\n"
                        genScript += f"newSphereObj = em.geo.Sphere(radius={str(radius)}*{cadLengthUnit}, position=position)\n"
                        genScript += f"newSphereObj.give_name('{freeCadObj.Label}')\n"
                        genScript += f"newSphereObj.name = '{freeCadObj.Label}'\n"
                        genScript += f"newSphereObj.prio_set({objModelPriority})\n"

                    else:
                        #
                        #   Going through each concrete material items and generate their .step files
                        #
                        currDir, baseName = self.getCurrDir()
                        stepModelFileName = childName + "_gen_model.step"
                        genScript += f"helperFunctionsObj.importStepFile(name='{childName}', filename='{stepModelFileName}', directory=[currDir, 'stepfiles'], unit=mm, priority={objModelPriority}, materialName='{currSetting.getName()}')\n"

                        # output directory path construction, if there is no parameter for output dir then output is in current freecad file dir
                        if (not outputDir is None):
                            stepfileOutputDir = os.path.join(outputDir, 'stepfiles')
                            try:
                                os.makedirs(stepfileOutputDir)
                            except:
                                pass
                            exportFileName = os.path.join(stepfileOutputDir, stepModelFileName)
                        else:
                            stepfileOutputDir = os.path.join(currDir, 'stepfiles')
                            try:
                                os.makedirs(stepfileOutputDir)
                            except:
                                pass
                            exportFileName = os.path.join(stepfileOutputDir, stepModelFileName)

                        self.cadHelpers.exportSTEP([freeCadObj], exportFileName)
                        print("Material object exported as STEP into: " + stepModelFileName)

                genScript += "\n"   #newline after each COMPLETE material category code generated

            genScript += "\n"

        return genScript

    def getObjectUsedInSomeCategoryStepImportScriptLines(self, items, outputDir=None, generateObjects=True, itemsCategoryName=""):
        genScript = ""

        genScript += f"# Imported objects used as {itemsCategoryName}\n"
        genScript += "#\n"
        genScript += "\n"

        if items == None:
            genScript += "# NO OBJECTS USED, NOTHING TO IMPORT\n"
            return genScript

        # now export material children, if it's object export as STL, if it's curve export as curve
        for [item, currSetting] in items:

            # first print all current material children names
            for k in range(item.childCount()):
                childName = item.child(k).text(0)
                print("##Children:")
                print("\t" + childName)

            # now export material children, if it's object export as STL, if it's curve export as curve
            for k in range(item.childCount()):
                childName = item.child(k).text(0)

                self.createdObjectBoundaryNameList.append(childName)  # add this object into list of created objects which will be used to create named group in exported mesh

                #
                #	getting item priority
                #
                objModelPriorityItemName = item.parent().text(0) + ", " + item.text(0) + ", " + childName
                objModelPriority = self.getItemPriority(objModelPriorityItemName)

                # getting reference to FreeCAD object
                freeCadObj = [i for i in self.cadHelpers.getObjects() if (i.Label) == childName][0]

                if freeCadObj.Name.startswith('Sphere'):
                    bbox = freeCadObj.Shape.BoundBox

                    radius = max(
                        bbox.XMax - bbox.XMin,
                        bbox.YMax - bbox.YMin,
                        bbox.ZMax - bbox.ZMin
                    ) / 2.0

                    # Get center
                    position = (
                        (bbox.XMin + bbox.XMax) / 2,
                        (bbox.YMin + bbox.YMax) / 2,
                        (bbox.ZMin + bbox.ZMax) / 2
                    )

                    cadLengthUnit = self.getFreeCADInternalUnitLengthStr()
                    genScript += f"position = {str(position)}\n"
                    genScript += f"position = tuple([x*{cadLengthUnit} for x in position])\n"
                    genScript += f"newSphereObj = em.geo.Sphere(radius={str(radius)}*{cadLengthUnit}, position=position)\n"
                    genScript += f"newSphereObj.give_name('{freeCadObj.Label}')\n"
                    genScript += f"newSphereObj.name = '{freeCadObj.Label}'\n"
                    genScript += f"newSphereObj.prio_set({objModelPriority})\n"

                else:
                    #
                    #   Going through each concrete material items and generate their .step files
                    #
                    currDir, baseName = self.getCurrDir()
                    stepModelFileName = childName + "_gen_model.step"
                    genScript += f"helperFunctionsObj.importStepFile(name='{childName}', filename='{stepModelFileName}', directory=[currDir, 'stepfiles'], unit=mm, priority={objModelPriority})\n"

                    #output directory path construction, if there is no parameter for output dir then output is in current freecad file dir
                    if (not outputDir is None):
                        stepfileOutputDir = os.path.join(outputDir, 'stepfiles')
                        try:
                            os.makedirs(stepfileOutputDir)
                        except:
                            pass
                        exportFileName = os.path.join(stepfileOutputDir, stepModelFileName)
                    else:
                        stepfileOutputDir = os.path.join(currDir, 'stepfiles')
                        try:
                            os.makedirs(stepfileOutputDir)
                        except:
                            pass
                        exportFileName = os.path.join(stepfileOutputDir, stepModelFileName)

                    self.cadHelpers.exportSTEP([freeCadObj], exportFileName)
                    print(f"{itemsCategoryName} object exported as STEP into: " + stepModelFileName)

            genScript += "\n"   #newline after each COMPLETE material category code generated

        return genScript

    def getCartesianOrCylindricalScriptLinesFromStartStop(self, bbCoords, startPointName=None, stopPointName=None):
        genScript = "";
        refUnit = self.getUnitLengthFromUI_m()  # Coordinates need to be given in drawing units
        sf = self.getFreeCADUnitLength_m() / refUnit  # scaling factor for FreeCAD units to drawing units

        strPortCoordsCartesianToCylindrical = ""
        strPortCoordsCartesianToCylindrical += "[generatedAuxTheta, generatedAuxR, generatedAuxZ] = cart2pol(portStart);\n"
        strPortCoordsCartesianToCylindrical += "portStart = [generatedAuxR, generatedAuxTheta, generatedAuxZ];\n"
        strPortCoordsCartesianToCylindrical += "[generatedAuxTheta, generatedAuxR, generatedAuxZ] = cart2pol(portStop);\n"
        strPortCoordsCartesianToCylindrical += "portStop = [generatedAuxR, generatedAuxTheta, generatedAuxZ];\n"

        if (self.getModelCoordsType() == "cylindrical"):
            # CYLINDRICAL COORDINATE TYPE USED
            if ((bbCoords.XMin <= 0 and bbCoords.YMin <= 0 and bbCoords.XMax >= 0 and bbCoords.YMax >= 0) or
                (bbCoords.XMin >= 0 and bbCoords.YMin >= 0 and bbCoords.XMax <= 0 and bbCoords.YMax <= 0)
            ):
                if (_r2(bbCoords.XMin) == _r2(bbCoords.XMax) or _r2(bbCoords.YMin) == _r2(bbCoords.YMax)):
                    #
                    #   Object is thin it's plane or line crossing origin
                    #
                    radius1 = -math.sqrt((sf * bbCoords.XMin) ** 2 + (sf * bbCoords.YMin) ** 2)
                    theta1 = math.atan2(bbCoords.YMin, bbCoords.XMin)
                    radius2 = math.sqrt((sf * bbCoords.XMax) ** 2 + (sf * bbCoords.YMax) ** 2)

                    genScript += 'portStart = [{0:g}, {1:g}, {2:g}]\n'.format(_r(radius1), _r(theta1), _r(sf * bbCoords.ZMin))
                    genScript += 'portStop = [{0:g}, {1:g}, {2:g}]\n'.format(_r(radius2), _r(theta1), _r(sf * bbCoords.ZMax))
                    genScript += '\n'
                else:
                    #
                    # origin [0,0,0] is contained inside boundary box, so now must used theta 0-360deg
                    #
                    radius1 = math.sqrt((sf * bbCoords.XMin) ** 2 + (sf * bbCoords.YMin) ** 2)
                    radius2 = math.sqrt((sf * bbCoords.XMax) ** 2 + (sf * bbCoords.YMax) ** 2)

                    genScript += 'portStart = [ 0, -math.pi, {0:g} ]\n'.format(_r(sf * bbCoords.ZMin))
                    genScript += 'portStop  = [ {0:g}, math.pi, {1:g} ]\n'.format(_r(max(radius1, radius2)), _r(sf * bbCoords.ZMax))
                    genScript += '\n'
            else:
                #
                # port is lying outside origin
                #
                genScript += 'portStart = [ {0:g}, {1:g}, {2:g} ]\n'.format(_r(sf * bbCoords.XMin),
                                                                             _r(sf * bbCoords.YMin),
                                                                             _r(sf * bbCoords.ZMin))
                genScript += 'portStop  = [ {0:g}, {1:g}, {2:g} ]\n'.format(_r(sf * bbCoords.XMax),
                                                                             _r(sf * bbCoords.YMax),
                                                                             _r(sf * bbCoords.ZMax))
                genScript += strPortCoordsCartesianToCylindrical

                if (bbCoords.YMin <= 0 and bbCoords.YMax >= 0):
                    #
                    #   special case when planar object lays on X axis like in Y+ and Y- in this case theta is generated:
                    #       -pi for start point
                    #       +pi for stop point
                    #   therefore to correct this since theta is in range -pi..+pi I have to add 360deg so +2*pi for start point will get it right as it should be
                    #

                    #18th June - LuboJ - commented this hack, it's causing problems, theta mas be <-2*pi. 2*pi> if it's more there is some positioning problem, for now disabled, need to be RETHINK AGAIN!
                    #   - issue observed using octave script, disabled for python also
                    #genScript += f"portStart[1] += 2*math.pi\n"
                    pass

        else:
            # CARTESIAN GRID USED
            genScript += 'portStart = [ {0:g}, {1:g}, {2:g} ]\n'.format(_r(sf * bbCoords.XMin),
                                                                         _r(sf * bbCoords.YMin),
                                                                         _r(sf * bbCoords.ZMin))
            genScript += 'portStop  = [ {0:g}, {1:g}, {2:g} ]\n'.format(_r(sf * bbCoords.XMax),
                                                                         _r(sf * bbCoords.YMax),
                                                                         _r(sf * bbCoords.ZMax))

        if (not startPointName is None):
            genScript = genScript.replace("portStart", startPointName)
        if (not stopPointName is None):
            genScript = genScript.replace("portStop", stopPointName)

        return genScript

    def getPortDefinitionsScriptLines(self, items):
        genScript = ""
        if not items:
            return genScript

        refUnit = self.getUnitLengthFromUI_m()  # Coordinates need to be given in drawing units
        sf = self.getFreeCADUnitLength_m() / refUnit  # scaling factor for FreeCAD units to drawing units

        # port index counter, they are generated into port{} cell variable for octave, cells index starts at 1
        genScriptPortCount = 1

        # nf2ff box counter, they are stored inside octave cell variable {} so this is to index them properly, in octave cells index starts at 1
        genNF2FFBoxCounter = 1

        #
        #   This here generates string for port excitation field, ie. for z+ generates [0 0 1], for y- generates [0 -1 0]
        #       Options for select field x,y,z were removed from GUI, but left here due there could be saved files from previous versions
        #       with these options so to keep backward compatibility they are treated as positive direction in that directions.
        #

        #baseVectorStr = {'x': '[1 0 0]', 'y': '[0 1 0]', 'z': '[0 0 1]', 'x+': '[1 0 0]', 'y+': '[0 1 0]', 'z+': '[0 0 1]', 'x-': '[-1 0 0]', 'y-': '[0 -1 0]', 'z-': '[0 0 -1]', 'XY plane, top layer': '[0 0 -1]', 'XY plane, bottom layer': '[0 0 1]', 'XZ plane, front layer': '[0 -1 0]', 'XZ plane, back layer': '[0 1 0]', 'YZ plane, right layer': '[-1 0 0]', 'YZ plane, left layer': '[1 0 0]',}
        #ERROR: followed baseVectorStr is just to generate something but need to take into consideration also sign of propagation direction
        baseVectorStr = {'x': "'x'", 'y': "'y'", 'z': "'z'", 'x+': "'x'", 'y+': "'y'", 'z+': "'z'", 'x-': "'x'", 'y-': "'y'", 'z-': "'z'", 'XY plane, top layer': "'z'", 'XY plane, bottom layer': "'z'", 'XZ plane, front layer': "'y'", 'XZ plane, back layer': "'y'", 'YZ plane, right layer': "'z'", 'YZ plane, left layer': "'x'",}

        mslDirStr = {'x': "'x'", 'y': "'y'", 'z': "'z'", 'x+': "'x'", 'y+': "'y'", 'z+': "'z'", 'x-': "'x'", 'y-': "'y'", 'z-': "'z'",}
        coaxialDirStr = {'x': '0', 'y': '1', 'z': '2', 'x+': '0', 'y+': '1', 'z+': '2', 'x-': '0', 'y-': '1', 'z-': '2',}
        coplanarDirStr = {'x': '0', 'y': '1', 'z': '2', 'x+': '0', 'y+': '1', 'z+': '2', 'x-': '0', 'y-': '1', 'z-': '2',}
        striplineDirStr = {'x': '0', 'y': '1', 'z': '2', 'x+': '0', 'y+': '1', 'z+': '2', 'x-': '0', 'y-': '1', 'z-': '2',}
        probeDirStr = {'x': '0', 'y': '1', 'z': '2', 'x+': '0', 'y+': '1', 'z+': '2', 'x-': '0', 'y-': '1', 'z-': '2',}

        genScript += "#######################################################################################################################################\n"
        genScript += "# PORTS\n"
        genScript += "#######################################################################################################################################\n"
        genScript += "portGeometry = {}   # port plates, mesh on them is refined after geometry is committed\n"
        genScript += "portFaceSize = {}\n"
        genScript += "\n"

        for [item, currSetting] in items:

            print(f"#PORT - {currSetting.getName()} - {currSetting.getType()}")

            objs = self.cadHelpers.getObjects()
            for k in range(item.childCount()):
                childName = item.child(k).text(0)

                self.createdObjectNameList.append(childName)  # add this object into list of created objects which will be used to create named group in exported mesh

                genScript += "## PORT - " + currSetting.getName() + " - " + childName + "\n"

                freecadObjects = [i for i in objs if (i.Label) == childName]

                # print(freecadObjects)
                for obj in freecadObjects:
                    # BOUNDING BOX
                    bbCoords = obj.Shape.BoundBox
                    print('\tFreeCAD lumped port BoundBox: ' + str(bbCoords))

                    #
                    #	getting item priority
                    #
                    priorityItemName = item.parent().text(0) + ", " + item.text(0) + ", " + childName
                    priorityIndex = self.getItemPriority(priorityItemName)

                    #
                    # PORT openEMS GENERATION INTO VARIABLE
                    #
                    if (currSetting.getType() == 'lumped'):
                        self.checkLumpedPortConductorOverlap(obj)
                        # thin port box -> zero-thickness plate on face touching geometry
                        if currSetting.direction.lower() in ['x', 'y', 'z']:
                            bbCoords = self.getLumpedPortPlaneBoundBox(bbCoords, currSetting.direction, obj.Label)
                            if min(bbCoords.XLength, bbCoords.YLength, bbCoords.ZLength) > 0:
                                #
                                #   3D port (box) is EMerge plate on its face touching geometry, thinnest side across excitation direction
                                #   (3D shape has no face normal for general plate below)
                                #
                                directionAxis = 'xyz'.index(currSetting.direction.lower())
                                dims = [bbCoords.XLength, bbCoords.YLength, bbCoords.ZLength]
                                plateAxis = min([k for k in range(3) if k != directionAxis], key=lambda k: dims[k])
                                lo = [bbCoords.XMin, bbCoords.YMin, bbCoords.ZMin][plateAxis]
                                hi = [bbCoords.XMax, bbCoords.YMax, bbCoords.ZMax][plateAxis]
                                plane = self.getPlaneTouchingModel(plateAxis, lo, hi, excludeLabel=obj.Label)
                                bbCoords = SimpleBoundBox.fromBoundBox(bbCoords).collapsed(plateAxis, plane)
                                self.addExportWarning(f"port '{obj.Label}' is 3D object, it's used as plate at {'xyz'[plateAxis]} = {plane:g}, draw port as face or thin box")
                        genScript += self.getCartesianOrCylindricalScriptLinesFromStartStop(bbCoords)

                        genScript += "portStart = [k*fc_unit for k in portStart]  #dimension scaled by freecad unit to meters\n"
                        genScript += "portStop = [k*fc_unit for k in portStop]  #dimension scaled by freecad unit to meters\n"
                        genScript += f"w = abs(portStart[0] - portStop[0])\n"
                        genScript += f"h = abs(portStart[1] - portStop[1])\n"
                        genScript += f"th = abs(portStart[2] - portStop[2])\n"

                        portR = ""
                        portWidth = ""
                        portHeight = ""
                        portDirection = ""
                        portExcitationAmplitude = ""
                        portGeometryObject = ""

                        if currSetting.infiniteResistance:
                            portR = "float('inf')"
                        else:
                            portR = f"{str(currSetting.R)}*{str(currSetting.getRUnits())}"

                        if (currSetting.direction.lower() == "x"):
                            portDirection = f"em.XAX"
                        elif (currSetting.direction.lower() == "y"):
                            portDirection = f"em.YAX"
                        elif (currSetting.direction.lower() == "z"):
                            portDirection = f"em.ZAX"
                        elif (currSetting.direction.lower() == "custom"):
                            portDirection = f"tuple({str(currSetting.directionCustomVector)})"
                        else:
                            portDirection = 'None'

                        portExcitationAmplitude = str(currSetting.excitationAmplitude)

                        PORT_NAME = childName
                        if bbCoords.XLength == 0 or bbCoords.YLength == 0 or bbCoords.ZLength == 0:

                            #
                            #   Create lumped port script line based on its orientation for now supports X,Y,Z axis
                            #
                            if bbCoords.XLength == 0:
                                portWidth = "h"
                                portHeight = "th"
                                portGeometryObject = f"em.geo.Plate(name='{PORT_NAME}', origin=portStart, u=[0,h,0], v=[0,0,th])"
                            elif bbCoords.YLength == 0:
                                portWidth = "w"
                                portHeight = "th"
                                portGeometryObject = f"em.geo.Plate(name='{PORT_NAME}', origin=portStart, u=[w,0,0], v=[0,0,th])"
                            elif bbCoords.ZLength == 0:
                                portWidth = "w"
                                portHeight = "h"
                                portGeometryObject = f"em.geo.Plate(name='{PORT_NAME}', origin=portStart, u=[w,0,0], v=[0,h,0])"
                        else:
                            #
                            #   General method calculate u,v vectors using start stop poiint and normal of face using freecad normal vector
                            #
                            objNormalVector = obj.Shape.normalAt(0.5, 0.5)  #returns face normal vector in center of mass (in the middle of face)
                            genScript += f"portNormalVector = [{objNormalVector.x}, {objNormalVector.y}, {objNormalVector.z}]\n"
                            genScript += f"portStart, portWidth, portHeight = helperFunctionsObj.create_emerge_plane_data(portStart, portStop, portNormalVector)\n"

                            portWidth = "portWidth"
                            portHeight = "portHeight"
                            portGeometryObject = f"em.geo.Plate(name='{PORT_NAME}', origin=portStart, u=portWidth, v=portHeight)"

                        self.portBoundaryConditionScriptLinesBuffer.append(f"helperFunctionsObj.setPortAsLumpedPort('{PORT_NAME}')\n")
                        genScript += f'#portName: "{obj.Label}" -> portNumber: {genScriptPortCount}\n'
                        genScript += f"portGeometry[{genScriptPortCount}] = {portGeometryObject}\n"
                        portFaceElements = getSolverMeshSettings(self.form, 'emerge')['portFaceElements']
                        genScript += f"portFaceSize[{genScriptPortCount}] = float(np.linalg.norm({portWidth})) / {portFaceElements}    # port face mesh: {portFaceElements} elements across port width\n"
                        genScript += f"helperFunctionsObj.addLumpedPort(name='{PORT_NAME}', portStart=portStart, width=emergeRealLength({portWidth}), height=emergeRealLength({portHeight}), R={portR}, direction={portDirection}, power={portExcitationAmplitude}, geometryObject=portGeometry[{genScriptPortCount}])\n"
                        self.sparamPorts.append([genScriptPortCount, PORT_NAME, currSetting.isActive, not currSetting.infiniteResistance])

                        # internalPortName = currSetting.name + " - " + obj.Label
                        internalPortName = PORT_NAME
                        self.internalPortIndexNamesList[internalPortName] = genScriptPortCount
                        genScriptPortCount += 1

                    elif (currSetting.getType() == 'modal'):
                        PORT_NAME = childName

                        portExcitationAmplitude = str(currSetting.excitationAmplitude)
                        portModeType = currSetting.modalModeType
                        portMixedMaterials = currSetting.modalMixedMaterials
                        portImpedanceDefinition = currSetting.modalImpedanceDefinition

                        genScript += self.getCartesianOrCylindricalScriptLinesFromStartStop(bbCoords)
                        genScript += "portStart = [k*fc_unit for k in portStart]  #dimension scaled by freecad unit to meters\n"
                        genScript += "portStop = [k*fc_unit for k in portStop]  #dimension scaled by freecad unit to meters\n"

                        objNormalVector = obj.Shape.normalAt(0.5, 0.5)
                        genScript += f"portNormalVector = [{objNormalVector.x}, {objNormalVector.y}, {objNormalVector.z}]\n"
                        genScript += f"portStart, portWidth, portHeight = helperFunctionsObj.create_emerge_plane_data(portStart, portStop, portNormalVector)\n"

                        portGeometryObject = f"em.geo.Plate(name='{PORT_NAME}', origin=portStart, u=portWidth, v=portHeight)"

                        self.portBoundaryConditionScriptLinesBuffer.append(f"helperFunctionsObj.setPortAsModalPort('{PORT_NAME}')\n")
                        genScript += f'#portName: "{obj.Label}" -> portNumber: {genScriptPortCount}\n'
                        genScript += f"helperFunctionsObj.addModalPort(name='{PORT_NAME}', mode='{portModeType}', mixedMaterials={portMixedMaterials}, impedanceDefinition='{portImpedanceDefinition}', power={portExcitationAmplitude}, geometryObject={portGeometryObject})\n"

                        internalPortName = PORT_NAME    #this is freecad object label since they are unique (must be set in freecad settings itself)
                        self.internalPortIndexNamesList[internalPortName] = genScriptPortCount
                        genScriptPortCount += 1

                    elif (currSetting.getType() == 'rectangular waveguide'):
                        PORT_NAME = childName

                        portModeName = currSetting.modeName
                        match = re.match('TE([0-9]{1})([0-9]{1})', portModeName)
                        portModeNumber1, portModeNumber2 = match.groups()
                        portModeType = str(tuple(portModeNumber1, portModeNumber2))

                        portPermittivity = 1.0
                        portExcitationAmplitude = str(currSetting.excitationAmplitude)

                        portStartX, portStartY, portStartZ, portStopX, portStopY, portStopZ, waveguideWidth, waveguideHeight = currSetting.getRectangularWaveguideStartStopWidthHeight(bbCoords, sf)
                        bbCoords.XMin = portStartX
                        bbCoords.YMin = portStartY
                        bbCoords.ZMin = portStartZ
                        bbCoords.XMax = portStopX
                        bbCoords.YMax = portStopY
                        bbCoords.ZMax = portStopZ
                        genScript += self.getCartesianOrCylindricalScriptLinesFromStartStop(bbCoords)

                        objNormalVector = obj.Shape.normalAt(0.5, 0.5)
                        genScript += f"portNormalVector = [{objNormalVector.x}, {objNormalVector.y}, {objNormalVector.z}]\n"
                        genScript += f"portStart, portWidth, portHeight = helperFunctionsObj.create_emerge_plane_data(portStart, portStop, portNormalVector)\n"

                        portGeometryObject = f"em.geo.Plate(name='{PORT_NAME}', origin=portStart, u=portWidth, v=portHeight)"

                        self.portBoundaryConditionScriptLinesBuffer.append(f"helperFunctionsObj.setPortAsRectangularWaveguidePort('{PORT_NAME}')\n")
                        genScript += f'#portName: "{obj.Label}" -> portNumber: {genScriptPortCount}\n'
                        genScript += f"helperFunctionsObj.addRectangularWaveguidePort(name='{PORT_NAME}', mode='{portModeType}', er={portPermittivity}, power={portExcitationAmplitude}, geometryObject={portGeometryObject})\n"

                        internalPortName = PORT_NAME    #this is freecad object label since they are unique (must be set in freecad settings itself)
                        self.internalPortIndexNamesList[internalPortName] = genScriptPortCount
                        genScriptPortCount += 1

                    elif (currSetting.getType() == 'coaxial'):
                        PORT_NAME = childName

                        portExcitationAmplitude = str(currSetting.excitationAmplitude)

                        portStartX, portStartY, portStartZ, portStopX, portStopY, portStopZ, coaxialRadius = currSetting.getCoaxialStartStopAndRadius(bbCoords, sf)

                        #
                        #   This is important, radius is calculated from bounding box coordinates from FreeCAD so must be multiplied by metric units used in FreeCAD.
                        #   LuboJ ERROR: not sure if scaling done right
                        #
                        coaxialRadius = coaxialRadius * self.getFreeCADUnitLength_m() / self.getUnitLengthFromUI_m()

                        #
                        #   LuboJ ERROR: not sure if scaling done right
                        #
                        coaxialInnerRadius, coaxialShellThickness, coaxialFeedShift, coaxialMeasPlaneShift = currSetting.getCoaxialInnerRadiusShellThicknessFeedShiftMeasShift()
                        coaxialInnerRadius = _r(coaxialInnerRadius / self.getUnitLengthFromUI_m())
                        coaxialShellThickness = _r(coaxialShellThickness / self.getUnitLengthFromUI_m())
                        coaxialFeedShift = _r(coaxialFeedShift / self.getUnitLengthFromUI_m())
                        coaxialMeasPlaneShift = _r(coaxialMeasPlaneShift / self.getUnitLengthFromUI_m())

                        #
                        #   Port start and end need to be shifted into middle of feed plane, this is done inside function in port settings
                        #
                        bbCoords.XMin = portStartX
                        bbCoords.YMin = portStartY
                        bbCoords.ZMin = portStartZ
                        bbCoords.XMax = portStopX
                        bbCoords.YMax = portStopY
                        bbCoords.ZMax = portStopZ
                        genScript += self.getCartesianOrCylindricalScriptLinesFromStartStop(bbCoords)

                        genScript += 'r_i = ' + str(coaxialInnerRadius) + '\n'
                        genScript += 'r_o = ' + str(coaxialRadius - coaxialShellThickness) + '\n'
                        genScript += 'r_os = ' + str(coaxialRadius) + '\n'

                        objNormalVector = obj.Shape.normalAt(0.5, 0.5)
                        genScript += f"portNormalVector = [{objNormalVector.x}, {objNormalVector.y}, {objNormalVector.z}]\n"
                        genScript += f"portStart, portWidth, portHeight = helperFunctionsObj.create_emerge_plane_data(portStart, portStop, portNormalVector)\n"
                        genScript += f"er = helperFunctionsObj.getMaterial('{currSetting.materialName}').er.value\n"

                        portGeometryObject = f"em.geo.Plate(name='{PORT_NAME}', origin=portStart, u=portWidth, v=portHeight)"

                        self.portBoundaryConditionScriptLinesBuffer.append(f"helperFunctionsObj.setPortAsCoaxPort('{PORT_NAME}')\n")
                        genScript += f'#portName: "{obj.Label}" -> portNumber: {genScriptPortCount}\n'
                        genScript += f"helperFunctionsObj.addCoaxPort(name'{PORT_NAME}', inner_radius=r_i, outer_radius=r_o, er=er, power={portExcitationAmplitude}, geometryObject={portGeometryObject})\n"

                        internalPortName = PORT_NAME    #this is freecad object label since they are unique (must be set in freecad settings itself)
                        self.internalPortIndexNamesList[internalPortName] = genScriptPortCount
                        genScriptPortCount += 1

                    else:
                        genScript += '# Unknown port type. Nothing was generated.\n'
                        genScript += 'raise BaseException("Unknown port type. Nothing was generated.")\n'

            genScript += "\n"

        return genScript

    def getLumpedPartDefinitionsScriptLines(self, items, outputDir):
        genScript = ""
        if not items:
            return genScript

        refUnit = self.getUnitLengthFromUI_m()  # Coordinates need to be given in drawing units
        sf = self.getFreeCADUnitLength_m() / refUnit  # scaling factor for FreeCAD units to drawing units

        genScript += "#######################################################################################################################################\n"
        genScript += "# LUMPED PART\n"
        genScript += "#######################################################################################################################################\n"

        for [item, currentSetting] in items:

            # traverse through all children item for this particular lumped part settings
            objs = self.cadHelpers.getObjects()
            objsExport = []
            for k in range(item.childCount()):
                childName = item.child(k).text(0)
                print("#LUMPED PART " + currentSetting.getType())

                freecadObjects = [i for i in objs if (i.Label) == childName]
                for obj in freecadObjects:

                    lumpedPartParams = []
                    if ('r' in currentSetting.getType().lower()):
                        lumpedPartParams.append(f"R={currentSetting.getR(unitsAsText=True)}")
                    if ('l' in currentSetting.getType().lower()):
                        lumpedPartParams.append(f"L={currentSetting.getL(unitsAsText=True)}")
                    if ('c' in currentSetting.getType().lower()):
                        lumpedPartParams.append(f"C={currentSetting.getC(unitsAsText=True)}")

                    impedanceFunctionParamStr = ""
                    if (currentSetting.getCombinationType() == 'series'):
                        impedanceFunctionParamStr = f"series_impedance({','.join(lumpedPartParams)})"
                    else:
                        #default behavior is that impedance behaves as parallel, this is because in openEMS it's this wasy so also apply for EMerge
                        impedanceFunctionParamStr = f"parallel_impedance({','.join(lumpedPartParams)})"

                    width=0.0
                    height=0.0
                    bb = obj.Shape.BoundBox
                    bbBoxDimensions = [bb.XMax - bb.XMin, bb.YMax - bb.YMin, bb.ZMax - bb.ZMin]
                    if bbBoxDimensions[0] == 0:
                        width = bbBoxDimensions[1]
                        height = bbBoxDimensions[2]
                    elif bbBoxDimensions[1] == 0:
                        width = bbBoxDimensions[0]
                        height = bbBoxDimensions[2]
                    elif bbBoxDimensions[2] == 0:
                        width = bbBoxDimensions[0]
                        height = bbBoxDimensions[1]
                    else:
                        #TODO: This is special case when it has to be somehow decided how width and height are figure out.
                        ...

                    genScript += f"helperFunctionsObj.setLumpedElementToObject(name='{childName}', impedance_function={impedanceFunctionParamStr}, width=emergeRealLength({width}*mm), height=emergeRealLength({height}*mm))\n"

                    #
                    #   Export STEP file for LumpedPart
                    #       - output directory path construction, if there is no parameter for output dir then output is in current freecad file dir
                    #
                    currDir, baseName = self.getCurrDir()
                    stepModelFileName = childName + "_gen_model.step"

                    #output directory path construction, if there is no parameter for output dir then output is in current freecad file dir
                    if (not outputDir is None):
                        stepfileOutputDir = os.path.join(outputDir, 'stepfiles')
                        try:
                            os.makedirs(stepfileOutputDir)
                        except:
                            pass
                        exportFileName = os.path.join(stepfileOutputDir, stepModelFileName)
                    else:
                        stepfileOutputDir = os.path.join(currDir, 'stepfiles')
                        try:
                            os.makedirs(stepfileOutputDir)
                        except:
                            pass
                        exportFileName = os.path.join(stepfileOutputDir, stepModelFileName)

                    self.cadHelpers.exportSTEP([obj], exportFileName)
                    print("LumpedPart object exported as STEP into: " + stepModelFileName)

        return genScript

    def getHelperPackageImportScriptLines(self):
        """
        Import of EMerge helper package (pip package by FreeCAD-OpenEMS-Export author, not part of EMerge), missing package stops script
        with install command instead of bare ModuleNotFoundError.
        """
        genScript = ""
        genScript += "try:\n"
        genScript += "\tfrom basicemergesolverhelperpackage import EMergeHelperFunctions\n"
        genScript += "\tfrom basicemergesolverhelperpackage.EMergeConstants import *\n"
        genScript += "except ModuleNotFoundError as e:\n"
        genScript += "\traise ModuleNotFoundError(\"EMerge helper package is missing, install it into the python environment used for EMerge: \"\n"
        genScript += "\t                          \"pip install basicemergesolverhelperpackage (tested with 0.0.9)\") from e\n"
        return genScript

    def getScaleFactorScriptLines(self, scaleFactor):
        """
        Geometry scale factor (EMerge >= 3.0 sim.set_scale_factor()): model is built and meshed scaleFactor times larger and scaled back
        before solving, micrometre parts would be close to OpenCASCADE tolerance in metres (gmsh fails). Geometry and mesh sizes are
        given in scaled units, so length units are scaled, physical lengths (lumped port width/height, roughness, thickness) stay real
        metres through emergeRealLength(). Frequencies stay in Hz.
        """
        genScript = ""
        genScript += "#\n"
        genScript += "# GEOMETRY SCALE FACTOR, grid tab -> Solver mesh (global)\n"
        genScript += "#   geometry and mesh sizes are in scaled units (length units below are scaled), physical lengths given to solver\n"
        genScript += "#   (lumped port width/height, roughness, thickness) are real metres, emergeRealLength() converts them\n"
        genScript += "#\n"
        genScript += f"SCALE = {scaleFactor:g}\n"
        if scaleFactor != 1:
            genScript += "if not hasattr(simulationObj, 'set_scale_factor'):\n"
            genScript += "\traise RuntimeError('geometry scale factor needs EMerge 3.0 or newer (sim.set_scale_factor), set it to 1 in OTTER grid tab -> Solver mesh or update EMerge')\n"
            genScript += "simulationObj.set_scale_factor(SCALE)\n"
            genScript += "m, cm, mm, um, nm, pm = [v * SCALE for v in (1.0, 1e-2, 1e-3, 1e-6, 1e-9, 1e-12)]\n"
            genScript += "fc_unit = fc_unit * SCALE\n"
        genScript += "\n"
        genScript += "def emergeRealLength(value):\n"
        genScript += "\tif SCALE == 1:\n"
        genScript += "\t\treturn value\n"
        genScript += "\treturn np.asarray(value, dtype=float) / SCALE if hasattr(value, '__len__') else value / SCALE\n"
        genScript += "\n"
        return genScript

    def checkEmergeScaleFactor(self, itemsByClassName, scaleFactor, minFeature_m=10e-6):
        """
        Warning when model has micrometre parts and geometry is not scaled, gmsh / OpenCASCADE fail on them in metres.
        """
        if scaleFactor != 1:
            return
        smallest = None
        for [item, currSetting] in itemsByClassName.get("MaterialSettingsItem", None) or []:
            for k in range(item.childCount()):
                bb = self.getModelObjectBoundBox(item.child(k).text(0))
                if bb is None:
                    continue
                dims = [d for d in [bb.XLength, bb.YLength, bb.ZLength] if d > 0]
                if dims:
                    smallest = min(dims) if smallest is None else min(smallest, min(dims))
        if smallest is not None and smallest * self.getFreeCADUnitLength_m() < minFeature_m:
            self.addExportWarning(f"smallest part is {smallest * self.getFreeCADUnitLength_m() * 1e6:g} um, in metres it is close to the OpenCASCADE tolerance and gmsh can fail, " +
                                  "set geometry scale factor 1000 in grid tab -> Solver mesh (global), needs EMerge 3.0")

    def getFemExcitationFreqRange(self):
        """
        Returns (fmin, fmax) in Hz of sweep or gaussian excitation or None.
        """
        excitationCategory = self.form.objectAssignmentRightTreeWidget.findItems("Excitation", QtCore.Qt.MatchFixedString)
        if len(excitationCategory) == 0 or excitationCategory[0].childCount() == 0:
            return None
        currSetting = excitationCategory[0].child(0).data(0, QtCore.Qt.UserRole)
        unit = currSetting.getUnitsAsNumber(currSetting.units)
        if currSetting.getType() == 'sweep':
            return (currSetting.sweep['fmin'] * unit, currSetting.sweep['fmax'] * unit)
        elif currSetting.getType() == 'fem_gaussian':
            return ((currSetting.femGaussian['f0'] - currSetting.femGaussian['fc']) * unit, (currSetting.femGaussian['f0'] + currSetting.femGaussian['fc']) * unit)
        return None

    def checkFemModelSettings(self, itemsByClassName, checkLowFrequency=False):
        """
        Checks known to give wrong FEM results, adds export warnings:
            - absorbing boundary closer than lambda/4 at highest frequency to structure disturbs resonances (bondwires radiate)
            - (palace) sweep starting far below fmax, FEM gets poorly conditioned at low frequency (low frequency breakdown)
        """
        freqRange = self.getFemExcitationFreqRange()
        if freqRange is None or freqRange[1] <= 0:
            return
        fmin, fmax = freqRange

        if checkLowFrequency and fmin < fmax / 100:
            self.addExportWarning(f"sweep starts at {fmin/1e9:g} GHz, far below fmax {fmax/1e9:g} GHz, below ~1 GHz small structures are electrically tiny " +
                                  "and FEM becomes poorly conditioned (low frequency breakdown), start sweep at fmax/30 or higher")

        # structure extent without air, in FreeCAD units
        structMin, structMax = None, None
        for [item, currSetting] in itemsByClassName.get("MaterialSettingsItem", None) or []:
            constants = getattr(currSetting, 'constants', {})
            try:
                isAir = currSetting.type == 'userdefined' and float(constants.get('epsilon', 1)) in [0.0, 1.0] and \
                        float(constants.get('mue', 1)) in [0.0, 1.0] and float(constants.get('sigma', 0)) == 0
            except:
                isAir = False
            if isAir:
                continue
            for k in range(item.childCount()):
                bb = self.getModelObjectBoundBox(item.child(k).text(0))
                if bb is None:
                    continue
                bbMin, bbMax = [bb.XMin, bb.YMin, bb.ZMin], [bb.XMax, bb.YMax, bb.ZMax]
                structMin = bbMin if structMin is None else [min(a, b) for a, b in zip(structMin, bbMin)]
                structMax = bbMax if structMax is None else [max(a, b) for a, b in zip(structMax, bbMax)]
        if structMin is None:
            return

        quarterWavelength = 3e8 / fmax / 4 / self.getFreeCADUnitLength_m()
        for [item, currSetting] in itemsByClassName.get("BoundaryConditionSettingsItem", None) or []:
            if currSetting.getType() != "Absorbing":
                continue
            for k in range(item.childCount()):
                childName = item.child(k).text(0)
                bb = self.getModelObjectBoundBox(childName)
                if bb is None:
                    continue
                margin = min([structMin[0] - bb.XMin, structMin[1] - bb.YMin, structMin[2] - bb.ZMin,
                              bb.XMax - structMax[0], bb.YMax - structMax[1], bb.ZMax - structMax[2]])
                if margin < quarterWavelength:
                    self.addExportWarning(f"absorbing boundary '{childName}' is {margin:g} mm from structure, less than lambda/4 = {quarterWavelength:g} mm at {fmax/1e9:g} GHz, " +
                                          "it disturbs resonances (ie. radiating bondwires), make airbox larger")

    def getSParametersFemPostprocessingScriptLines(self, fileSuffix=""):
        """
        EMerge S-parameters for excited port into simulation_output/Sparameters<suffix>.csv and .png, simulationResult from run_sweep() is used.
        """
        genScript = ""
        ports = [p for p in self.sparamPorts if p[3]]
        excitedPorts = [p for p in ports if p[2]]
        if len(excitedPorts) == 0:
            return genScript

        genScript += "#######################################################################################################################################\n"
        genScript += "# POSTPROCESSING - S-parameters for excited port into simulation_output/Sparameters.csv and .png\n"
        genScript += "#######################################################################################################################################\n"
        genScript += "import matplotlib.pyplot as plt\n"
        genScript += "import csv\n"
        genScript += "os.makedirs(Sim_Path, exist_ok=True)\n"
        genScript += "sparamGrid = simulationResult.scalar.grid\n"
        genScript += "sparamFreq = np.asarray(sparamGrid.freq)\n"
        genScript += f"excited_port = {excitedPorts[0][0]}\n"
        genScript += f"sparam_ports = {[p[0] for p in ports]}\n"
        genScript += "sparams = {f'S{n}{excited_port}': np.asarray(sparamGrid.S(n, excited_port)) for n in sparam_ports}\n"
        genScript += f"sparamFileName = os.path.join(Sim_Path, 'Sparameters{fileSuffix}')\n"
        genScript += "\n"
        genScript += "with open(sparamFileName + '.csv', 'w', newline='') as csvFile:\n"
        genScript += "\twriter = csv.writer(csvFile)\n"
        genScript += "\twriter.writerow(['Frequency_Hz'] + [c for name in sparams for c in (name + '_dB', name + '_phase_deg')])\n"
        genScript += "\tfor i in range(len(sparamFreq)):\n"
        genScript += "\t\trow = ['%.6g' % sparamFreq[i]]\n"
        genScript += "\t\tfor name, s in sparams.items():\n"
        genScript += "\t\t\trow += ['%.3f' % (20*np.log10(np.abs(s[i]) + 1e-30)), '%.2f' % np.angle(s[i], deg=True)]\n"
        genScript += "\t\twriter.writerow(row)\n"
        genScript += "\n"
        genScript += "sparamFig = plt.figure(figsize=(9, 5.5))\n"
        genScript += "for name, s in sparams.items():\n"
        genScript += "\tplt.plot(sparamFreq/1e9, 20*np.log10(np.abs(s) + 1e-30), linewidth=2, label=name)\n"
        genScript += "plt.grid(True)\n"
        genScript += "plt.xlabel('Frequency (GHz)')\n"
        genScript += "plt.ylabel('Magnitude (dB)')\n"
        genScript += "plt.title('S-parameters (EMerge)')\n"
        genScript += "plt.legend()\n"
        genScript += "sparamFig.savefig(sparamFileName + '.png', dpi=150, bbox_inches='tight')\n"
        genScript += "plt.close(sparamFig)\n"
        genScript += "print('S-parameters saved to', sparamFileName + '.csv / .png')\n"
        genScript += "\n"

        return genScript

    def getBoundaryConditionScriptLines(self, items):
        genScript = ""
        if not items:
            return genScript

        refUnit = self.getUnitLengthFromUI_m()  # Coordinates need to be given in drawing units
        sf = self.getFreeCADUnitLength_m() / refUnit  # scaling factor for FreeCAD units to drawing units

        genScript += "#######################################################################################################################################\n"
        genScript += "# BOUNDARY CONDITIONS PART\n"
        genScript += "#######################################################################################################################################\n"

        for [item, currentSetting] in items:
            for k in range(item.childCount()):
                childName = item.child(k).text(0)
                print("#BOUNDARY CONDITION TYPE: " + currentSetting.getType())
                if currentSetting.getType() == 'surfaceImpedance':
                    genScript += f"helperFunctionsObj.setSurfaceImpedanceBoundaryConditionToObject(name=\"{childName}\", surface_conductance={currentSetting.surfaceImpedance['conductance']}, surface_roughness=emergeRealLength({currentSetting.surfaceImpedance['roughness']}*{currentSetting.surfaceImpedance['roughnessUnits']}), thickness=emergeRealLength({currentSetting.surfaceImpedance['thickness']}*{currentSetting.surfaceImpedance['thicknessUnits']}), material=\"{currentSetting.surfaceImpedance['material']}\")\n"
                else:
                    genScript += f"helperFunctionsObj.setBoundaryConditionToObject(name=\"{childName}\", type=\"{currentSetting.getType()}\")\n"

        return genScript

    def getOrderedGridDefinitionsScriptLines(self, items):
        genScript = ""
        meshPrioritiesCount = self.form.meshPriorityTreeView.topLevelItemCount()

        if (not items) or (meshPrioritiesCount == 0):
            return genScript

        refUnit = self.getUnitLengthFromUI_m()  # Coordinates need to be given in drawing units
        refUnitStr = self.form.simParamsDeltaUnitList.currentText()
        sf = self.getFreeCADUnitLength_m() / refUnit  # scaling factor for FreeCAD units to drawing units

        genScript += "#######################################################################################################################################\n"
        genScript += "# GRID LINES\n"
        genScript += "#######################################################################################################################################\n"

        # Create lists and dict to be able to resolve ordered list of (grid settings instance <-> FreeCAD object) associations.
        # In its current form, this implies user-defined grid lines have to be associated with the simulation volume.
        _assoc = lambda idx: list(map(str.strip, self.form.meshPriorityTreeView.topLevelItem(idx).text(0).split(',')))
        orderedAssociations = [_assoc(k) for k in reversed(range(meshPrioritiesCount))]
        gridSettingsNodeNames = [gridSettingsNode.text(0) for [gridSettingsNode, gridSettingsInst] in items]
        fcObjects = {obj.Label: obj for obj in self.cadHelpers.getObjects()}

        for gridSettingsNodeName in gridSettingsNodeNames:
            print("Grid type : " + gridSettingsNodeName)

        for k, [categoryName, gridName, FreeCADObjectName] in enumerate(orderedAssociations):

            print("Grid priority level {} : {} :: {}".format(k, FreeCADObjectName, gridName))

            if not (gridName in gridSettingsNodeNames):
                print("Failed to resolve '{}'.".format(gridName))
                continue
            itemListIdx = gridSettingsNodeNames.index(gridName)

            #GridSettingsItem object from GUI
            gridSettingsInst = items[itemListIdx][1]

            #Grid category object from GUI
            gridCategoryObj = items[itemListIdx][0]

            #
            #   Fixed Distance, Fixed Count mesh boundaries coords obtain
            #
            if (gridSettingsInst.getType() in ['FEM Max Size']):
                fcObject = fcObjects.get(FreeCADObjectName, None)
                if (not fcObject):
                    print("Failed to resolve '{}'.".format(FreeCADObjectName))
                    continue

                ### Produce script output.

                #
                #   This should make sure that just real objects have set mesh size
                #       21May2026 - seems not working properly with PCB imported from KiCAD as some real objects don't have Shape and
                #                   therefore no mesh size line is generated, so commented for now!
                #
                # if (not "Shape" in dir(fcObject)):
                #     print(f"\tFailed to find Shape in object {FreeCADObjectName}")
                #     continue

                if gridSettingsInst.femMesh['femUseMaxElementSize'] == True:
                    genScript += f"helperFunctionsObj.setObjSize(name='{FreeCADObjectName}', size={gridSettingsInst.femMesh['femMaxElementSize']}*{gridSettingsInst.femMesh['femMaxSizeUnits']})\n"
                if gridSettingsInst.femMesh['femUseMaxBoundarySize'] == True:
                    genScript += f"helperFunctionsObj.setObjBoundarySize(name='{FreeCADObjectName}', size={gridSettingsInst.femMesh['femMaxBoundarySize']}*{gridSettingsInst.femMesh['femMaxSizeUnits']})\n"
                if gridSettingsInst.femMesh['femUseMaxFaceSize'] == True:
                    genScript += f"helperFunctionsObj.setObjFaceSize(name='{FreeCADObjectName}', size={gridSettingsInst.femMesh['femMaxFaceSize']}*{gridSettingsInst.femMesh['femMaxSizeUnits']})\n"
                if gridSettingsInst.femMesh['femUseMaxDomainSize'] == True:
                    genScript += f"helperFunctionsObj.setObjVolumeSize(name='{FreeCADObjectName}', size={gridSettingsInst.femMesh['femMaxDomainSize']}*{gridSettingsInst.femMesh['femMaxSizeUnits']})\n"
                if gridSettingsInst.femMesh['femUseSurfaceMeshSize'] == True:
                    genScript += f"#TODO: {FreeCADObjectName} - femUseSurfaceMeshSize not implemented yet!\n"
                    genScript += f"helperFunctionsObj.setObjBoundarySize(name='{FreeCADObjectName}', size={gridSettingsInst.femMesh['femSurfaceMeshSizeSizeMin']}*{gridSettingsInst.femMesh['femMaxSizeUnits']})\n"

                if gridSettingsInst.femMesh['femUseMaxUserDefined'] == True:
                    genScript += gridSettingsInst.femMesh['femMaxUserDefined']+"\n"

        genScript += "\n"
        return genScript

    def getInitScriptLines(self):
        genScript = ""
        genScript += "# To be run with python.\n"
        genScript += "# based on: FreeCAD to OpenEMS plugin by Lubomir Jagos, see https://github.com/LubomirJagos/FreeCAD-OpenEMS-Export\n"
        genScript += "# Edited and automated by Mohsen\n"
        genScript += "# This file has been automatically generated. Manual changes may be overwritten.\n"
        genScript += "#" * 120 + "\n"
        genScript += "import numpy as np\n"
        genScript += "import emerge as em\n"
        genScript += "import os, shutil\n"
        genScript += "\n"
        genScript += self.getHelperPackageImportScriptLines()
        genScript += "\n"

        genScript += "# Change current path to script file folder\n"
        genScript += "#\n"
        genScript += "abspath = os.path.abspath(__file__)\n"
        genScript += "dname = os.path.dirname(abspath)\n"
        genScript += "os.chdir(dname)\n"

        genScript += "## constants\n"
        genScript += "unit    = " + str(self.getUnitLengthFromUI_m()) + " # Model coordinates and lengths will be specified in " + self.form.simParamsDeltaUnitList.currentText() + ".\n"
        genScript += "fc_unit = " + str(self.getFreeCADUnitLength_m()) + " # STL files are exported in FreeCAD standard units (mm).\n"
        genScript += "\n"

        return genScript

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

                genScript += "#######################################################################################################################################\n"
                genScript += "# EXCITATION " + currSetting.getName() + "\n"
                genScript += "#######################################################################################################################################\n"

                if (currSetting.getType() == 'sweep'):
                    genScript += f"fmin = {str(currSetting.sweep['fmin'])}*{str(currSetting.getUnitsAsNumber(currSetting.units))}\n"
                    genScript += f"fmax = {str(currSetting.sweep['fmax'])}*{str(currSetting.getUnitsAsNumber(currSetting.units))}\n"
                    genScript += f"resolution = {str(currSetting.sweep['resolution'])}\n"
                    genScript += f"npoints = {str(currSetting.sweep['npoints'])}\n"

                    #
                    #   Normaly frequency range is used, when frequency groups are going to be used these commands to specify resolution and
                    #   frequency mesh are used later in loop.
                    #
                    if self.form.simParamsUseFrequencyGroups_emerge.isChecked() == False:
                        genScript += f"simulationObj.mw.set_frequency_range(fmin, fmax, npoints)\n"
                        genScript += f"simulationObj.mw.set_resolution(resolution)\n"
                    else:
                        genScript += f"ngroups = {self.form.simParamsNumberOfFrequencyGroups_emerge.value()}\n"
                        genScript += f"npgroup = npoints // ngroups + 1\n"
                        genScript += f"\n"
                        genScript += f"frequencies = np.linspace(fmin, fmax, npoints)\n"
                        genScript += f"fgroups = [frequencies[i * npgroup: (i + 1) * npgroup] for i in range(ngroups)]\n"

                else:
                    genScript += f"# ERROR: Excitation type \"{currSetting.getType()}\" not implemented in script generator!\n"

                genScript += "\n"
            else:
                self.guiHelpers.displayMessage("<font color='#FF0000'><b>Missing excitation, please define one.</b></font>")
                pass
            pass
        return genScript

    def generateSimulationScript(self, outputDir=None):
        """
        General method which should be used as interface in other objects. Generate simulation script.
        :param outputDir:
        :return:
        """
        self.generateEmergeScript(outputDir)

    def generateEmergeScript(self, outputDir=None):
        """
        Generates result simulation script for EMerge

        :param outputDir:
        :return:
        """

        self.resetExportState()
        self.portBoundaryConditionScriptLinesBuffer = []
        self.createdObjectNameList = []
        self.createdObjectBoundaryNameList = []

        # Create outputDir relative to local FreeCAD file if output dir does not exists
        #   if outputDir is set to same value
        #   if outputStr is None then folder with name as FreeCAD file with suffix _openEMS_simulation is created
        outputDir = self.createOuputDir(outputDir)

        # Update status bar to inform user that exporting has begun.
        if self.statusBar is not None:
            self.statusBar.showMessage("Generating EMerge script and geometry files ...", 5000)
            QtWidgets.QApplication.processEvents()

        # Constants and variable initialization.

        refUnit = self.getUnitLengthFromUI_m()  # Coordinates need to be given in drawing units
        refUnitStr = self.form.simParamsDeltaUnitList.currentText()
        sf = self.getFreeCADUnitLength_m() / refUnit  # scaling factor for FreeCAD units to drawing units

        # List categories and items.

        itemsByClassName = self.getItemsByClassName()
        self.checkFemModelSettings(itemsByClassName)

        meshSettings = getSolverMeshSettings(self.form, 'emerge')
        self.checkEmergeScaleFactor(itemsByClassName, meshSettings['scaleFactor'])

        # Write script header.

        genScript = ""

        genScript += "## EMerge simulation\n"
        genScript += "#\n"
        genScript += "#\n"
        genScript += self.getExportWarningsScriptLines()

        genScript += self.getInitScriptLines()

        genScript += "\n"
        genScript += "currDir = os.getcwd()\n"
        genScript += "print(currDir)\n"
        genScript += "\n"

        # Write simulation settings.

        genScript += "## prepare simulation folder, if dir exits remove and create new one to be empty\n"
        genScript += "Sim_Path = os.path.join(currDir, 'simulation_output')\n"

        if self.form.simParamsUseFrequencyGroups_emerge.isChecked():
            genScript += "if os.path.exists(Sim_Path) and 'EMERGE_FGROUP' not in os.environ:     # a frequency group process keeps the other groups' results\n"
        else:
            genScript += "if os.path.exists(Sim_Path):\n"
        genScript += "\tshutil.rmtree(Sim_Path)   # clear previous directory\n"
        genScript += "\tos.mkdir(Sim_Path)    # create empty simulation folder\n"
        genScript += "\n"

        print("======================== REPORT BEGIN ========================\n")

        genScript += "#######################################################################################################################################\n"
        genScript += "# SIMULATION OBJECT AND HELPER FUNCTIONS OBJECT\n"
        genScript += "#######################################################################################################################################\n"

        simulationName = self.cadHelpers.getCurrentDocumentFileName()

        genScript += f"simulationObj = em.Simulation(\"{simulationName}\", save_file=True)\n"
        solverEngineStr = self.form.simParamsSolverEngine_emerge.currentText()
        if(solverEngineStr.upper() == "CUDA"):
            genScript += "simulationObj.mw.solveroutine.set_solver(em.EMSolver.CUDSS)\n"
        elif(solverEngineStr.upper() == "PARDISO"):
            genScript += "simulationObj.mw.solveroutine.set_solver(em.EMSolver.PARDISO)\n"
        elif(solverEngineStr.upper() == "SUPERLU"):
            genScript += "simulationObj.mw.solveroutine.set_solver(em.EMSolver.SUPERLU)\n"
        elif(solverEngineStr.upper() == "UMFPACK"):
            genScript += "simulationObj.mw.solveroutine.set_solver(em.EMSolver.UMFPACK)\n"
        elif(solverEngineStr.upper() == "LAPACK"):
            genScript += "simulationObj.mw.solveroutine.set_solver(em.EMSolver.LAPACK)\n"
        elif(solverEngineStr.upper() == "ARPACK"):
            genScript += "simulationObj.mw.solveroutine.set_solver(em.EMSolver.ARPACK)\n"
        elif(solverEngineStr.upper() == "SMART_ARPACK"):
            genScript += "simulationObj.mw.solveroutine.set_solver(em.EMSolver.SMART_ARPACK)\n"
        elif(solverEngineStr.upper() == "SMART_ARPACK_BMA"):
            genScript += "simulationObj.mw.solveroutine.set_solver(em.EMSolver.SMART_ARPACK_BMA)\n"
        elif(solverEngineStr.upper() == "MUMPS"):
            genScript += "simulationObj.mw.solveroutine.set_solver(em.EMSolver.MUMPS)\n"

        genScript += self.getScaleFactorScriptLines(meshSettings['scaleFactor'])

        genScript += f"helperFunctionsObj = EMergeHelperFunctions(simulationObj)\n"
        genScript += "\n"

        self.reportFreeCADItemSettings(itemsByClassName.get("FreeCADSettingsItem", None))

        #
        # EXCITATION - Write excitation definition.
        #
        genScript += self.getExcitationScriptLines()

        #
        # If frequency groups are used then generated code till know must be stored and genScript will collect generated script lines
        # which will be set to start with \t since they will be used in loop when simulation will be run part by part for frequency
        # group.
        #
        storedGenScript = ""
        if self.form.simParamsUseFrequencyGroups_emerge.isChecked():
            genScript += "# EMerge keeps every solved frequency in memory until the process ends (QFN examples: 6 frequencies of 134k tetrahedra\n"
            genScript += "# fit 20 GB, the 7th was killed): each frequency group runs in a fresh process of this script (env EMERGE_FGROUP = group\n"
            genScript += "# index, run one group by hand the same way), this process merges their CSV files into Sparameters.csv\n"
            genScript += "if len(fgroups) > 1 and 'EMERGE_FGROUP' not in os.environ:\n"
            genScript += "\timport subprocess, sys, glob, csv\n"
            genScript += "\tfailed = []\n"
            genScript += "\tfor i in range(len(fgroups)):\n"
            genScript += "\t\tif len(fgroups[i]) == 0:\n"
            genScript += "\t\t\tcontinue\n"
            genScript += "\t\tprint(f'frequency group {i + 1} of {len(fgroups)} ({fgroups[i][0] / 1e9:g} .. {fgroups[i][-1] / 1e9:g} GHz) in a fresh process', flush=True)\n"
            genScript += "\t\tresult = subprocess.run([sys.executable, os.path.abspath(__file__)], env=dict(os.environ, EMERGE_FGROUP=str(i)))\n"
            genScript += "\t\tif result.returncode != 0:\n"
            genScript += "\t\t\tprint(f'WARNING frequency group {i + 1} failed (exit code {result.returncode})')\n"
            genScript += "\t\t\tfailed.append(i + 1)\n"
            genScript += "\trows, header = {}, None\n"
            genScript += "\tfor path in sorted(glob.glob(os.path.join(Sim_Path, 'Sparameters_fgroup_*.csv'))):\n"
            genScript += "\t\twith open(path) as f:\n"
            genScript += "\t\t\treader = csv.reader(f)\n"
            genScript += "\t\t\theader = next(reader)\n"
            genScript += "\t\t\tfor row in reader:\n"
            genScript += "\t\t\t\trows[float(row[0])] = row\n"
            genScript += "\tif header:\n"
            genScript += "\t\twith open(os.path.join(Sim_Path, 'Sparameters.csv'), 'w', newline='') as f:\n"
            genScript += "\t\t\twriter = csv.writer(f)\n"
            genScript += "\t\t\twriter.writerow(header)\n"
            genScript += "\t\t\twriter.writerows(rows[k] for k in sorted(rows))\n"
            genScript += "\t\tprint('merged', len(rows), 'frequencies into', os.path.join(Sim_Path, 'Sparameters.csv'))\n"
            genScript += "\tif failed:\n"
            genScript += "\t\tprint(f'ERROR frequency groups {failed} failed, their frequencies are missing')\n"
            genScript += "\tsys.exit(1 if failed else 0)\n"
            genScript += "\n"
            genScript += "for fgroupIndex, fgroup in enumerate(fgroups):\n"
            genScript += "\tif len(fgroup) == 0 or ('EMERGE_FGROUP' in os.environ and fgroupIndex != int(os.environ['EMERGE_FGROUP'])):\n"
            genScript += "\t\tcontinue\n"
            genScript += "\tsimulationObj.reset(mesh=True, geometry=True, physics=True)\n"
            if meshSettings['scaleFactor'] != 1:
                genScript += "\tsimulationObj.set_scale_factor(SCALE)\n"
            genScript += "\thelperFunctionsObj = EMergeHelperFunctions(simulationObj)\n"
            genScript += "\tsimulationObj.mw.set_frequencies(fgroup)  # (fmin, fmax, npoints)\n"
            genScript += "\tsimulationObj.mw.set_resolution(resolution)\n"
            genScript += "\n"

            # store generated python lines till now and reset genScript variable
            storedGenScript = genScript
            genScript = "\n"    #now it must start with \n since later all \n -> \t\n and without this first line will have no tab, it's just simple hack

        #
        # MATERIAL - Write material definitions.
        #
        genScript += self.getMaterialDefinitionsScriptLines(itemsByClassName.get("MaterialSettingsItem", None), outputDir)

        #
        # Write import STEP objects which are used as:
        #    - boundary conditions
        #    - lumped elements
        #
        genScript += self.getObjectUsedInSomeCategoryStepImportScriptLines(itemsByClassName.get("BoundaryConditionSettingsItem", None), outputDir, itemsCategoryName="boundary conditions")
        genScript += self.getObjectUsedInSomeCategoryStepImportScriptLines(itemsByClassName.get("LumpedPartSettingsItem", None), outputDir, itemsCategoryName="lumped elements")
        genScript += "\n"

        # Write port definitions.
        genScript += self.getPortDefinitionsScriptLines(itemsByClassName.get("PortSettingsItem", None))

        #
        #   Commit geometry before mesh definition.
        #       This will reassign dimtags associated inside OpenCASCADe core, so it's done before mesh size definitions.
        #
        genScript += "#######################################################################################################################################\n"
        genScript += "# COMPLETE GEOMETRY\n"
        genScript += "#######################################################################################################################################\n"
        genScript += "\n"
        genScript += "simulationObj.commit_geometry()\n"
        genScript += "\n"

        if meshSettings['curvedBoundary'] > 0:
            genScript += "# finer mesh on curved surfaces (round bondwires), grid tab -> Solver mesh (global)\n"
            genScript += "if hasattr(simulationObj.mesher, 'set_curved_boundary_meshing'):\n"
            genScript += f"\tsimulationObj.mesher.set_curved_boundary_meshing({meshSettings['curvedBoundary']})\n"
            genScript += "else:\n"
            genScript += "\tprint('INFO: this EMerge version has no set_curved_boundary_meshing(), curved boundary meshing skipped')\n"
            genScript += "\n"

        # Write grid definitions.
        genScript += self.getOrderedGridDefinitionsScriptLines(itemsByClassName.get("GridSettingsItem", None))

        # Write probes definitions
        # genScript += self.getProbeDefinitionsScriptLines(itemsByClassName.get("ProbeSettingsItem", None))

        # Write NF2FF probe grid definitions.
        genScript += self.getNF2FFDefinitionsScriptLines(itemsByClassName.get("ProbeSettingsItem", None))

        genScript += "#\n"
        genScript += f"# Port faces are meshed finer ({meshSettings['portFaceElements']} elements across port width), lumped port field must be resolved\n"
        genScript += "#\n"
        genScript += "for portNumber, portGeometryObj in globals().get('portGeometry', {}).items():\n"
        genScript += "\tsimulationObj.mesher.set_face_size(portGeometryObj, portFaceSize[portNumber])\n"
        genScript += "\n"

        genScript += "#\n"
        genScript += "# First mesh must be created on existing geometry, safe mode runs mesh diagnostics\n"
        genScript += "#\n"
        genScript += f"simulationObj.settings.safe_mode = {meshSettings['safeMode']}\n"
        genScript += "\n"
        genScript += "#\n"
        genScript += "# EMerge size check (~100k tetrahedra) estimates domain from bounding box of all gmsh entities, zero-length (degenerate) edges\n"
        genScript += "# of swept bodies (bondwires) keep unscaled coordinates there and the check fails for small model, same estimate from volumes only\n"
        genScript += "#\n"
        genScript += "try:\n"
        genScript += "\timport gmsh\n"
        genScript += "\tvolumeBoxes = [gmsh.model.getBoundingBox(3, t) for d, t in gmsh.model.getEntities(3)]\n"
        genScript += "\tif volumeBoxes:\n"
        genScript += "\t\tdomainSize = [max(b[k + 3] for b in volumeBoxes) - min(b[k] for b in volumeBoxes) for k in range(3)]\n"
        genScript += "\t\twavelengthMin = 299792458 / simulationObj.mw.frequencies[-1] * SCALE    # gmsh coordinates are scaled\n"
        genScript += "\t\tsizeCheckElements = int(5 * np.prod(domainSize) / wavelengthMin**3)\n"
        genScript += "\t\tprint(f'size check from volumes: ~{sizeCheckElements} tetrahedra (EMerge limit 100000)')\n"
        genScript += "\t\tif sizeCheckElements <= 100_000:\n"
        genScript += "\t\t\tsimulationObj.settings.size_check = False\n"
        genScript += "except Exception as e:\n"
        genScript += "\tprint(f'size check from volumes skipped: {e}')\n"
        genScript += "\n"
        # genScript += "simulationObj.commit_geometry()\n"
        genScript += "simulationObj.generate_mesh()\n"
        genScript += "\n"

        genScript += "#\n"
        genScript += "# Now follows boundary condition definition\n"
        genScript += "#\n"

        #
        #   Port are added when geometry is created because they must be meshed but then there is also needed to create boundary condition for them
        #
        for portBcScriptLine in self.portBoundaryConditionScriptLinesBuffer:
            genScript += portBcScriptLine
        genScript += "\n"

        #
        #   LUMPED ELEMENTS
        #
        genScript += self.getLumpedPartDefinitionsScriptLines(itemsByClassName.get("LumpedPartSettingsItem", None), outputDir)
        genScript += "\n"

        #
        #   Genereate code to define boundary conditions
        #       - they must lay on some already existing mesh!
        #
        genScript += self.getBoundaryConditionScriptLines(itemsByClassName.get("BoundaryConditionSettingsItem", None))
        genScript += "\n"

        genScript += "#######################################################################################################################################\n"
        genScript += "# EXPERIMENT EXPORT MESH WITH NAMED GROUP OF MESH\n"
        genScript += "#######################################################################################################################################\n"
        for objectName in set(self.createdObjectNameList):  #converting list to set make it unique, because some names could be under more categories and we want to use them just once
            genScript += f"helperFunctionsObj.createGmshNamedGroup('{objectName}', '{objectName}')\n"
        for objectName in set(self.createdObjectBoundaryNameList):  #converting list to set make it unique, because some names could be under more categories and we want to use them just once
            genScript += f"helperFunctionsObj.createGmshNamedGroup('{objectName}', '{objectName}_2D', useBoundary=True)\n"
        genScript += "\n"
        genScript += "try:\n"
        genScript += "\tos.mkdir('mesh')\n"
        genScript += "except:\n"
        genScript += "\tpass\n"
        genScript += f"simulationObj.export(os.path.join('mesh', f'{simulationName}{'_fgroup_{fgroupIndex}' if self.form.simParamsUseFrequencyGroups_emerge.isChecked() else ''}.msh'))\n"
        genScript += "\n"

        #
        #   Display model in window first as volumes, then it displays mesh.
        #
        genScript += "#######################################################################################################################################\n"
        genScript += "# DISPLAY MODEL\n"
        genScript += "#######################################################################################################################################\n"

        # 3D view only with a display (it blocks or fails on a server), env NO_VIEW=1 switches it off
        viewCondition = "not os.environ.get('NO_VIEW') and (os.name == 'nt' or os.environ.get('DISPLAY') or os.environ.get('WAYLAND_DISPLAY'))"
        if self.form.simParamsUseFrequencyGroups_emerge.isChecked():
            genScript += "# display model and mesh just first time, in next frequency groups there will be no display\n"
            genScript += f"if fgroupIndex == 0 and {viewCondition}:\n"
            genScript += "\tsimulationObj.view()\n"
            genScript += "\tsimulationObj.view(plot_mesh=True, volume_mesh=False)\n"
            genScript += "\n"
        else:
            genScript += f"if {viewCondition}:\n"
            genScript += "\tsimulationObj.view()\n"
            genScript += "\tsimulationObj.view(plot_mesh=True, volume_mesh=False)\n"
            genScript += "\n"

        print("======================== REPORT END ========================\n")

        # Finalize script.

        genScript += "#######################################################################################################################################\n"
        genScript += "# RUN and save results\n"
        genScript += "#######################################################################################################################################\n"

        if self.form.simParamsDisableRAMCheck_emerge.isChecked():
            genScript += "simulationObj.settings.check_ram = False\n"

        genScript += ("#" if self.form.generateJustPreviewCheckbox.isChecked() else "") + "simulationResult = simulationObj.mw.run_sweep(frequency_groups=1)   # default precomputes all frequencies together (RAM)\n"
        genScript += "\n"

        if not self.form.generateJustPreviewCheckbox.isChecked():
            genScript += self.getSParametersFemPostprocessingScriptLines("_fgroup_{fgroupIndex}" if self.form.simParamsUseFrequencyGroups_emerge.isChecked() else "")
            if self.form.simParamsUseFrequencyGroups_emerge.isChecked():
                genScript = genScript.replace("sparamFileName = os.path.join(Sim_Path, 'Sparameters_fgroup_{fgroupIndex}')", "sparamFileName = os.path.join(Sim_Path, f'Sparameters_fgroup_{fgroupIndex}')")

        #
        #   When frequency groups are used then we need to add \t on each generated line
        #   Then add stored generated scripted line at beginning.
        #
        if self.form.simParamsUseFrequencyGroups_emerge.isChecked():
            genScript = genScript.replace("\n", "\n\t")
            genScript = storedGenScript + genScript     #add stored scripted lines to beginning
            genScript += "\n"

        genScript += ("#" if self.form.generateJustPreviewCheckbox.isChecked() else "") + "simulationObj.save()\n"
        genScript += "\n"

        #
        #   Write _emerge.py script file to current directory.
        #
        currDir, nameBase = self.getCurrDir()

        if (not outputDir is None):
            fileName = f"{outputDir}/{nameBase}_emerge.py"
        else:
            fileName = f"{currDir}/{nameBase}_emerge.py"

        f = open(fileName, "w", encoding='utf-8')
        f.write(genScript)
        f.close()

        # Show message or update status bar to inform user that exporting has finished.

        self.guiHelpers.displayMessage('Simulation script written to: ' + fileName + self.getExportWarningsMessage(), forceModal=True)
        print('Simulation script written to: ' + fileName)

        return

    #
    #	Write NF2FF Button clicked, generate script to display far field pattern
    #
    def writeNf2ffButtonClicked(self, outputDir=None):
        genScript = ""
        genScript += "# Plot far field for structure.\n"
        genScript += "#\n"

        genScript += self.getInitScriptLines()

        genScript += "currDir = os.getcwd()\n"
        genScript += "Sim_Path = os.path.join(currDir, r'simulation_output')\n"
        genScript += "print(currDir)\n"
        genScript += "\n"

        #
        #   ATTENTION THIS IS SPECIFIC FOR FAR FIELD PLOTTING, plotFrequency and frequencies count
        #       port is calculated to get P_in (input power)
        #
        boundaryConditionObjectName = self.form.portNf2ffEmergeObjectList.currentText().split('-')[1].strip()

        cadLengthUnit = self.getFreeCADInternalUnitLengthStr()

        genScript += f"""
#######################################################################################################################################
# Farfield plot and 3D gain generated
#######################################################################################################################################
import emerge as em
import numpy as np
from emerge.plot import smith, plot_sp
import os

simulationObj = em.Simulation("{self.cadHelpers.getCurrentDocumentFileName()}", load_file=True)
simulationResult = simulationObj.data.mw

#######################################################################################################################################
# FAR FIELD PLOT
#######################################################################################################################################
mm = 0.001
currDir = os.getcwd()

#######################################################################################################################################
#   ADD BOUNDARY SELECTION SAME AS SIMULATION FILE
#######################################################################################################################################

boundary_selection = None
for geometryObj in simulationObj.state.manager.geometry_list[simulationObj.modelname].values():
	if geometryObj.name == '{boundaryConditionObjectName}' or geometryObj.name.startswith('{boundaryConditionObjectName}'):
		boundary_selection = geometryObj.boundary()

simulationObj.mw.bc.AbsorbingBoundary(boundary_selection)


# add model files into display
for geoObj in simulationObj.state.manager.geometry_list[simulationObj.modelname].values():
	simulationObj.display.add_object(geoObj)

# display far field
field = simulationResult.field.find(freq={self.form.boundaryNf2ffEmergeFreq.value()}*1e6)
ff3d = field.farfield_3d(boundary_selection, origin=({self.form.diagramPlacementXNF2FFEmerge.value()}*{cadLengthUnit}, {self.form.diagramPlacementYNF2FFEmerge.value()}*{cadLengthUnit}, {self.form.diagramPlacementZNF2FFEmerge.value()}*{cadLengthUnit}))
simulationObj.display.add_field(ff3d.surfplot('{self.form.polarizationNf2ffEmerge.currentText()}','{self.form.quantityNf2ffEmerge.currentText()}',True, dB=True, rmax={self.form.diagramRMaxNF2FFEmerge.value()}*{cadLengthUnit}, offset=({self.form.diagramPlacementXNF2FFEmerge.value()}*mm, {self.form.diagramPlacementYNF2FFEmerge.value()}*mm, {self.form.diagramPlacementZNF2FFEmerge.value()}*mm)))
simulationObj.display.show()

"""

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

    #
    #	Write NF2FF Button clicked, generate script to display far field pattern
    #
    def writeFieldButtonClicked(self, outputDir=None):
        cadLengthUnit = self.getFreeCADInternalUnitLengthStr()

        genScript = ""
        genScript += "# Plot far field for structure.\n"
        genScript += "#\n"

        genScript += self.getInitScriptLines()

        genScript += "currDir = os.getcwd()\n"
        genScript += "Sim_Path = os.path.join(currDir, r'simulation_output')\n"
        genScript += "print(currDir)\n"
        genScript += "\n"

        #
        #   ATTENTION THIS IS SPECIFIC FOR FAR FIELD PLOTTING, plotFrequency and frequencies count
        #       port is calculated to get P_in (input power)
        #
        cutplaneScriptLine = ""
        if self.form.cutplaneXFieldProcessingEmerge.value() == 0.0 and self.form.cutplaneYFieldProcessingEmerge.value() == 0.0:
            cutplaneScriptLine = f"result = simulationResult.field.find(freq={self.form.frequencyFieldProcessingEmerge.value()}*1e6).cutplane({self.form.discretizationStepSizeFieldProcessingEmerge.value()}, z={self.form.cutplaneZFieldProcessingEmerge.value()}*{cadLengthUnit})"
        elif self.form.cutplaneXFieldProcessingEmerge.value() == 0.0 and self.form.cutplaneZFieldProcessingEmerge.value() == 0.0:
            cutplaneScriptLine = f"result = simulationResult.field.find(freq={self.form.frequencyFieldProcessingEmerge.value()}*1e6).cutplane({self.form.discretizationStepSizeFieldProcessingEmerge.value()}, y={self.form.cutplaneZFieldProcessingEmerge.value()}*{cadLengthUnit})"
        elif self.form.cutplaneYFieldProcessingEmerge.value() == 0.0 and self.form.cutplaneZFieldProcessingEmerge.value() == 0.0:
            cutplaneScriptLine = f"result = simulationResult.field.find(freq={self.form.frequencyFieldProcessingEmerge.value()}*1e6).cutplane({self.form.discretizationStepSizeFieldProcessingEmerge.value()}, x={self.form.cutplaneZFieldProcessingEmerge.value()}*{cadLengthUnit})"

        genScript += f"""## display field in model
#
#
import emerge as em
import numpy as np
from emerge.plot import smith, plot_sp

from emerge.plot import plot_ff, plot_ff_polar  #added for far field plot
import os

simulationObj = em.Simulation("{self.cadHelpers.getCurrentDocumentFileName()}", load_file=True)
simulationResult = simulationObj.data.mw

#######################################################################################################################################
# E FIELD PLOT
#######################################################################################################################################
mm = 0.001
currDir = os.getcwd()

# add model files into display
for geoObj in simulationObj.state.manager.geometry_list[simulationObj.modelname].values():
	simulationObj.display.add_object(geoObj, opacity=0.1)

{cutplaneScriptLine}
plot_data = result.scalar('{self.form.typeFieldProcessingEmerge.currentText()}','{self.form.metricFieldProcessingEmerge.currentText()}')
X, Y, Z, F = plot_data.xyzf
simulationObj.display.add_surf(X,Y,Z,F)				        #static field display
#simulationObj.display.animate().add_surf(X,Y,Z,F, opacity=0.7)	#animated version of display

simulationObj.display.show()

"""

        #
        # WRITE OpenEMS Script file into current dir
        #
        currDir, nameBase = self.getCurrDir()

        self.createOuputDir(outputDir)
        if (not outputDir is None):
            fileName = f"{outputDir}/{nameBase}_draw_field_{self.form.typeFieldProcessingEmerge.currentText()}.py"
        else:
            fileName = f"{currDir}/{nameBase}_draw_field_{self.form.typeFieldProcessingEmerge.currentText()}.py"

        f = open(fileName, "w", encoding='utf-8')
        f.write(genScript)
        f.close()
        print('Script to display far field written into: ' + fileName)
        self.guiHelpers.displayMessage('Script to display far field written into: ' + fileName, forceModal=False)

    def drawS11ButtonClicked(self, outputDir=None, portName=""):
        genScript = ""

        itemsByClassName = self.getItemsByClassName()
        items = itemsByClassName.get("PortSettingsItem", None)

        genScriptPortCount = 1
        portNamesAndNumbersList = {}
        for [item, currSetting] in items:
            for k in range(item.childCount()):
                childName = item.child(k).text(0)
                # portName = f"{currSetting.name} - {childName}"
                portName = childName

                # PORT openEMS GENERATION INTO VARIABLE
                if (currSetting.getType() == 'lumped'):
                    portNamesAndNumbersList[portName] = genScriptPortCount
                    genScriptPortCount += 1

        sourcePortName:str = self.form.drawS11Port.currentText().split(' - ')[1]
        sourcePortNumber = portNamesAndNumbersList[sourcePortName]

        genScript += f"## EMerge simulation - S{sourcePortNumber}{sourcePortNumber}\n"
        genScript += "#\n"
        genScript += "#\n"
        genScript += "import emerge as em\n"
        genScript += "import numpy as np\n"
        genScript += "from emerge.plot import smith, plot_sp\n"
        genScript += "\n"
        genScript += self.getHelperPackageImportScriptLines()
        genScript += "\n"

        simulationName = self.cadHelpers.getCurrentDocumentFileName()
        genScript += f"simulationObj = em.Simulation(\"{simulationName}\", load_file=True)\n"
        genScript += "simulationResult = simulationObj.data.mw\n"
        genScript += "helperFunctionsObj = EMergeHelperFunctions(simulationObj)\n"
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
        genScript += "sourcePortNumber = portNamesAndNumbersList[sourcePortName]\n"
        genScript += "\n"

        if self.form.simParamsGenerateTouchstoneFile_emerge.isChecked():
            genScript += "# Generate touchstone file available since EMerge 2.5.5\n"
            genScript += f'simulationResult.scalar.grid.export_touchstone("{simulationName}.s1p", Z0ref=50, format="RI", funit="GHz")\n'
            genScript += "\n"
        if self.form.simParamsGenerateTouchstoneFileDense_emerge.isChecked():
            genScript += "# Generate touchstone file available since EMerge 2.5.5, frequency axis have more interpolated points\n"
            genScript += f"dense_frequencies = simulationResult.scalar.grid.dense_f({self.form.simParamsGenerateTouchstoneFileDenseNPoints_emerge.value()})\n"
            genScript += f'simulationResult.scalar.grid.export_touchstone("{simulationName}_dense.s1p", Z0ref=50, format="RI", funit="GHz", dense_freq=dense_frequencies)\n'
            genScript += "\n"

        genScript += f'helperFunctionsObj.exportCSV_SParam("{simulationName}_s11.csv", sourcePortNumber, sourcePortNumber)\n'
        genScript += "helperFunctionsObj.plotSParamUsingPortNumbers(sourcePortNumber, sourcePortNumber)\n"
        genScript += "\n"

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
                # portName = f"{currSetting.name} - {childName}"
                portName = childName

                # PORT openEMS GENERATION INTO VARIABLE
                if (currSetting.getType() == 'lumped'):
                    portNamesAndNumbersList[portName] = genScriptPortCount
                    genScriptPortCount += 1

        sourcePortName = self.form.drawS21Source.currentText().split(' - ')[1]
        targetPortName = self.form.drawS21Target.currentText().split(' - ')[1]
        sourcePortNumber = portNamesAndNumbersList[sourcePortName]
        targetPortNumber = portNamesAndNumbersList[targetPortName]

        #
        #   Generate script plotting S21 from source port to output port
        #
        genScript += f"## EMerge simulation - S{targetPortNumber}{sourcePortNumber}\n"
        genScript += f"#\ttransfer from '{sourcePortName}' -> '{targetPortName}'\n"
        genScript += "#\n"
        genScript += "#\n"
        genScript += "import emerge as em\n"
        genScript += "import numpy as np\n"
        genScript += "from emerge.plot import smith, plot_sp\n"
        genScript += "\n"
        genScript += self.getHelperPackageImportScriptLines()
        genScript += "\n"

        simulationName = self.cadHelpers.getCurrentDocumentFileName()
        genScript += f"simulationObj = em.Simulation(\"{simulationName}\", load_file=True)\n"
        genScript += "simulationResult = simulationObj.data.mw\n"
        genScript += "helperFunctionsObj = EMergeHelperFunctions(simulationObj)\n"
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

        # --- Post-process S-parameters ------------------------------------------
        genScript += "###############################################################################\n"
        genScript += "# PLOT S DATA\n"
        genScript += "###############################################################################\n"
        genScript += f"sourcePortName = '{sourcePortName}'\n"
        genScript += f"targetPortName = '{targetPortName}'\n"
        genScript += "sourcePortNumber = portNamesAndNumbersList[sourcePortName]\n"
        genScript += "targetPortNumber = portNamesAndNumbersList[targetPortName]\n"
        genScript += "\n"

        if self.form.simParamsGenerateTouchstoneFile_emerge.isChecked():
            genScript += "# Generate touchstone file available since EMerge 2.5.5\n"
            genScript += f'simulationResult.scalar.grid.export_touchstone("{simulationName}.s2p", Z0ref=50, format="RI", funit="GHz")\n'
            genScript += "\n"
        if self.form.simParamsGenerateTouchstoneFileDense_emerge.isChecked():
            genScript += "# Generate touchstone file available since EMerge 2.5.5, frequency axis have more interpolated points\n"
            genScript += f"dense_frequencies = simulationResult.scalar.grid.dense_f({self.form.simParamsGenerateTouchstoneFileDenseNPoints_emerge.value()})\n"
            genScript += f'simulationResult.scalar.grid.export_touchstone("{simulationName}_dense.s2p", Z0ref=50, format="RI", funit="GHz", dense_freq=dense_frequencies)\n'
            genScript += "\n"

        genScript += f'helperFunctionsObj.exportCSV_SParam("{simulationName}_s11.csv", sourcePortNumber, sourcePortNumber)\n'
        genScript += f'helperFunctionsObj.exportCSV_SParam("{simulationName}_s21.csv", sourcePortNumber, targetPortNumber)\n'
        genScript += "helperFunctionsObj.plotSParamUsingPortNumbers(sourcePortNumber, targetPortNumber, plotS11=True)\n"
        genScript += "\n"

        #
        # WRITE OpenEMS Script file into current dir
        #
        currDir, nameBase = self.getCurrDir()

        self.createOuputDir(outputDir)
        if (not outputDir is None):
            fileName = f"{outputDir}/{nameBase}_draw_S{targetPortNumber}{sourcePortNumber}.py"
        else:
            fileName = f"{currDir}/{nameBase}_draw_S{targetPortNumber}{sourcePortNumber}.py"

        f = open(fileName, "w", encoding='utf-8')
        f.write(genScript)
        f.close()

        print('Draw result from simulation file written to: ' + fileName)
        self.guiHelpers.displayMessage('Draw result from simulation file written to: ' + fileName, forceModal=False)
