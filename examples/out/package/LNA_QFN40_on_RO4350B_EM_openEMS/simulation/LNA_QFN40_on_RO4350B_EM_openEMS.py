# OpenEMS FDTD Analysis Automation Script
#
#
# EXPORT WARNINGS (settings known to give wrong or slow results, check them):
#   - dielectric has higher priority than conductor it touches, conductor is replaced by dielectric on shared faces, move conductors above dielectrics in priority list: ALL_LNA_Passive (-9300) > QFN_40_6x6_body (-9400)
#
# To be run with python.
# based on: FreeCAD to OpenEMS plugin by Lubomir Jagos, see https://github.com/LubomirJagos/FreeCAD-OpenEMS-Export
# Edited and automated by Mohsen
# This file has been automatically generated. Manual changes may be overwritten.
########################################################################################################################
import math
import numpy as np
import os, tempfile, shutil
from pylab import *
import csv
import CSXCAD
from openEMS import openEMS
from openEMS.physical_constants import *

#
# FUNCTION TO CONVERT CARTESIAN TO CYLINDRICAL COORDINATES
#     returns coordinates in order [theta, r, z]
#
def cart2pol(pointCoords):
	theta = np.arctan2(pointCoords[1], pointCoords[0])
	r = np.sqrt(pointCoords[0] ** 2 + pointCoords[1] ** 2)
	z = pointCoords[2]
	return theta, r, z

#
# HELPER FUNCTIONS: range with endpoint for mesh blocks, mesh finalization (merge sliver lines, smooth grading, air margin to PML)
#
def arangeWithEndpoint(start, stop, step=1, endpoint=True):
	"""
	np.arange() which always ends exactly at stop, arangeWithEndpoint(0,10,2.5) = [0, 2.5, 5, 7.5, 10]
	(comparing floats with == dropped the end line of a mesh block most of the time)
	"""
	if start == stop or step == 0:
		return np.array([start])

	arr = np.arange(start, stop, step)
	if endpoint:
		if len(arr) > 1 and abs(stop - arr[-1]) < 0.5 * abs(step):
			arr[-1] = stop		# last cell would be smaller than half a step, move last line to endpoint
		else:
			arr = np.append(arr, stop)
	return arr

def meshMergeCloseLines(lines, tol, fixed=()):
	"""
	Merge mesh lines closer than tol, sliver cells make the FDTD timestep collapse.
	Lines lying on geometry or port planes (fixed) are kept in preference, outer lines are kept.
	"""
	lines = np.unique(np.asarray(lines, dtype=float))
	if len(lines) < 2 or tol <= 0:
		return lines

	fixed = np.unique(np.asarray(fixed, dtype=float))
	def isFixed(v):
		return fixed.size > 0 and np.min(np.abs(fixed - v)) <= 1e-3 * tol

	keep = [lines[0]]
	for v in lines[1:]:
		if v - keep[-1] < tol:
			if isFixed(v) and not isFixed(keep[-1]) and len(keep) > 1:
				keep[-1] = v
			continue
		keep.append(v)
	keep[-1] = lines[-1]
	return np.array(keep)

def meshGradedLines(fixed, regions, maxRes, ratio=1.3, mergeRel=0.5):
	"""
	Graded mesh lines: fixed lines (geometry and port planes, domain ends) are kept, between them cell size follows
	the local target size and grows by at most ratio from cell to cell.
		regions  - list of (start, stop, size), target size inside region, outside it grows by (ratio-1)*distance
		maxRes   - maximum cell size (lambda/20 in air)
		mergeRel - fixed lines closer than mergeRel * local size are merged (no sliver cells)
	"""
	fixed = np.unique(np.round(np.asarray(fixed, dtype=float), 9))
	if len(fixed) < 2:
		return fixed
	if len(regions) == 0:
		regions = [(fixed[0], fixed[-1], maxRes)]
	g = ratio - 1
	a = np.array([r[0] for r in regions])[None]
	b = np.array([r[1] for r in regions])[None]
	h = np.array([r[2] for r in regions])[None]
	def size(x):
		x = np.atleast_1d(x)[:, None]
		return np.minimum(maxRes, np.min(h + g * np.maximum(0, np.maximum(a - x, x - b)), axis=1))

	keep = [fixed[0]]
	for v in fixed[1:]:
		if v - keep[-1] >= mergeRel * size(0.5 * (v + keep[-1]))[0]:
			keep.append(v)
	keep[-1] = fixed[-1]

	out = [keep[0]]
	hMin = max(min(r[2] for r in regions), 1e-12)
	for x0, x1 in zip(keep[:-1], keep[1:]):
		t = np.linspace(x0, x1, int(min(400000, max(400, 8 * (x1 - x0) / hMin))))
		inv = 1.0 / size(t)
		cum = np.concatenate([[0], np.cumsum(0.5 * (inv[1:] + inv[:-1]) * np.diff(t))])
		n = max(1, int(np.ceil(cum[-1] - 0.2)))
		out += list(np.interp(np.arange(1, n) * cum[-1] / n, cum, t)) + [x1]
	return np.array(out)

def meshFinalizeLines(lines, fixed, maxRes, ratio=1.3, mergeTol=0.0, structMin=None, structMax=None, pmlCells=(0, 0), airMargin=0.0, regions=None, mergeRel=0.5):
	"""
	Final mesh lines for one direction:
		- lines already in grid and fixed lines (geometry and port planes) are kept,
		- domain is extended so there is airMargin of clear air between structure and PML plus room for the PML cells,
		- regions given (list of (start, stop, size)): graded mesh from meshGradedLines(), fixed lines closer than mergeRel * local size merged,
		- no regions: lines closer than mergeTol are merged and gaps are filled so neighbouring cells grow at most by ratio and are at most maxRes.
	"""
	import CSXCAD.SmoothMeshLines

	regions = list(regions) if regions else []
	lines = np.concatenate([np.asarray(lines, dtype=float), np.asarray(fixed, dtype=float)])
	domain = np.concatenate([lines, [r[0] for r in regions], [r[1] for r in regions]])
	if len(domain) == 0:
		return lines
	domainMin, domainMax = domain.min(), domain.max()

	if maxRes > 0 and structMin is not None and structMax is not None:
		if pmlCells[0] > 0:
			domainMin = min(domainMin, structMin - airMargin - pmlCells[0] * maxRes)
		if pmlCells[1] > 0:
			domainMax = max(domainMax, structMax + airMargin + pmlCells[1] * maxRes)
	lines = np.concatenate([lines, [domainMin, domainMax]])

	if len(regions) > 0 and maxRes > 0:
		return meshGradedLines(lines, regions, maxRes, ratio, mergeRel)

	lines = meshMergeCloseLines(lines, mergeTol, fixed)
	if maxRes > 0 and len(lines) > 1:
		lines = np.asarray(CSXCAD.SmoothMeshLines.SmoothMeshLines(lines, maxRes, ratio), dtype=float)
		lines = meshMergeCloseLines(lines, mergeTol, fixed)

	return lines

def meshCheckPml(lines, structMin, structMax, pmlCells=(0, 0)):
	"""
	Returns list of warning strings when the PML cells reach into the structure (ports inside absorbing boundary).
	"""
	warnings = []
	lines = np.sort(lines)
	if pmlCells[0] > 0 and len(lines) > pmlCells[0] and lines[pmlCells[0]] > structMin:
		warnings.append(f"PML at min side reaches {lines[pmlCells[0]]:g}, structure starts at {structMin:g}")
	if pmlCells[1] > 0 and len(lines) > pmlCells[1] and lines[-1 - pmlCells[1]] < structMax:
		warnings.append(f"PML at max side reaches {lines[-1 - pmlCells[1]]:g}, structure ends at {structMax:g}")
	return warnings

def readSTL(path):
	"""
	Triangles (n, 3, 3) of an ASCII or binary STL, rounded to 1 nm (float32 noise breaks 'on the mesh line').
	"""
	import struct
	data = open(path, 'rb').read()
	if data[:5] == b'solid' and b'facet' in data[:1000]:
		v = [list(map(float, l.split()[1:4])) for l in data.decode(errors='ignore').splitlines() if l.strip().startswith('vertex')]
		return np.round(np.array(v, dtype=float).reshape(-1, 3, 3), 6)
	n = struct.unpack('<I', data[80:84])[0]
	a = np.frombuffer(data[84:84 + 50 * n], dtype=np.uint8).reshape(n, 50)
	return np.round(np.frombuffer(a[:, 12:48].tobytes(), dtype='<f4').reshape(n, 3, 3).astype(float), 6)

def flatOutlines(tris, k):
	"""
	Closed outline loops (2 x n arrays, the two in-plane coordinates) of a flat triangulation normal to axis k:
	edges used by one triangle only, chained.
	"""
	other = [i for i in range(3) if i != k]
	pts = np.round(tris[:, :, other], 9)
	count = {}
	for t in pts:
		for a, b in ((0, 1), (1, 2), (2, 0)):
			e = tuple(sorted((tuple(t[a]), tuple(t[b]))))
			count[e] = count.get(e, 0) + 1
	nxt = {}
	for (p, q), c in count.items():
		if c == 1:
			nxt.setdefault(p, []).append(q)
			nxt.setdefault(q, []).append(p)
	loops, seen = [], set()
	for start in nxt:
		if start in seen:
			continue
		loop, prev, cur = [start], None, start
		seen.add(start)
		while True:
			cand = [q for q in nxt[cur] if q != prev and q not in seen]
			if not cand:
				break
			prev, cur = cur, cand[0]
			seen.add(cur)
			loop.append(cur)
		if len(loop) >= 3:
			loops.append(np.array(loop).T)
	return loops

def addSTL(CSX, prop, path, priority, solids):
	"""
	STL body on property prop. A flat STL becomes polygons on a PEC sheet property (openEMS never uses a zero-volume
	polyhedron), a solid one a polyhedron, remembered in solids as (property, bbox min, bbox max, primitive).
	"""
	tris = readSTL(path)
	lo, hi = tris.reshape(-1, 3).min(0), tris.reshape(-1, 3).max(0)
	k = int(np.argmin(hi - lo))
	if hi[k] - lo[k] > 1e-9:
		prim = prop.AddPolyhedronReader(path, priority=priority)
		prim.ReadFile()
		solids.append((prop, lo, hi, prim))
		return prim
	sheets = [p for p in CSX.GetAllProperties() if p.GetName() == 'PEC_sheets']
	sheetProp = sheets[0] if sheets else CSX.AddMetal('PEC_sheets')
	for loop in flatOutlines(tris, k):
		sheetProp.AddPolygon(loop, 'xyz'[k], float(lo[k]), priority=priority)

def isConductor(prop):
	"""
	Metal, conducting sheet or material with kappa > 1e4 S/m.
	"""
	t = prop.GetTypeString()
	if t in ('Metal', 'ConductingSheet'):
		return True
	if t == 'Material':
		kappa = prop.GetMaterialProperty('kappa')
		return np.max(np.atleast_1d(kappa)) > 1e4
	return False

def liftConductorPriorities(CSX):
	"""
	Every conductor above every dielectric (where a dielectric of higher priority overlaps a conductor it takes the
	edges and the conductor is lost), lumped ports above every conductor (a port overlapped by metal is shorted).
	Returns the priority lift.
	"""
	props = CSX.GetAllProperties()
	conductors = [p for prop in props if isConductor(prop) for p in prop.GetAllPrimitives()]
	dielectrics = [p for prop in props if prop.GetTypeString() == 'Material' and not isConductor(prop) for p in prop.GetAllPrimitives()]
	lift = 0
	if conductors and dielectrics:
		lift = max(max(p.GetPriority() for p in dielectrics) - min(p.GetPriority() for p in conductors) + 1, 0)
		for p in conductors:
			p.SetPriority(p.GetPriority() + lift)
	if conductors:
		top = max(p.GetPriority() for p in conductors)
		for prop in props:
			if prop.GetTypeString() == 'LumpedElement':
				for p in prop.GetAllPrimitives():
					p.SetPriority(max(p.GetPriority(), top + 1))
	return lift

def conductorBoxes(CSX):
	"""
	(primitive, bounding box [[xmin, ymin, zmin], [xmax, ymax, zmax]]) of every conductor primitive.
	"""
	boxes = []
	for prop in CSX.GetAllProperties():
		if isConductor(prop):
			for prim in prop.GetAllPrimitives():
				boxes.append((prim, np.array(prim.GetBoundBox(), dtype=float)))
	return boxes

def snapSheets(boxes, k, minCell):
	"""
	Zero-thickness conductors normal to axis k closer than minCell are moved onto one plane (the first of their
	cluster), returns the planes; they must be mesh lines.
	"""
	sheets = sorted([(bb[0][k], prim) for prim, bb in boxes if abs(bb[1][k] - bb[0][k]) < 1e-9], key=lambda x: x[0])
	planes = []
	for pos, prim in sheets:
		if planes and pos - planes[-1] < minCell:
			if abs(planes[-1] - pos) > 1e-12:
				if prim.GetTypeName() == 'Polygon':
					prim.SetElevation(planes[-1])
				elif prim.GetTypeName() == 'Box':
					start, stop = np.array(prim.GetStart(), dtype=float), np.array(prim.GetStop(), dtype=float)
					start[k] = stop[k] = planes[-1]
					prim.SetStart(start)
					prim.SetStop(stop)
		else:
			planes.append(pos)
	return planes

def meshMinCell(lines, boxes, k, minCell, must=()):
	"""
	Mesh lines at least minCell apart, taken by priority: must lines (sheet and port planes) and the domain ends,
	mid planes of conductors thinner than minCell (one line inside so they conduct), conductor faces, all other lines.
	"""
	faces, mids = [], []
	for prim, bb in boxes:
		lo, hi = bb[0][k], bb[1][k]
		if hi - lo < 1e-9:
			continue
		if hi - lo < minCell:
			mids.append(0.5 * (lo + hi))
		else:
			faces += [lo, hi]
	taken = []
	def take(candidates, force=False):
		for c in sorted(set(float(c) for c in candidates)):   # exact values: a sheet must sit exactly on its line
			if force or not taken or np.min(np.abs(np.array(taken) - c)) >= minCell - 1e-12:
				taken.append(c)
	lines = np.asarray(lines, dtype=float)
	take([m for m in must if lines.min() <= m <= lines.max()] + [lines.min(), lines.max()], force=True)
	take([m for m in mids if lines.min() <= m <= lines.max()])
	take([f for f in faces if lines.min() <= f <= lines.max()])
	take(lines)
	return np.unique(taken)

def portPlanes(CSX, k):
	"""
	Planes of lumped port and excitation boxes normal to axis k (they must stay mesh lines).
	"""
	planes = []
	for prop in CSX.GetAllProperties():
		if prop.GetTypeString() in ('LumpedElement', 'Excitation'):
			for prim in prop.GetAllPrimitives():
				bb = np.array(prim.GetBoundBox(), dtype=float)
				planes += [bb[0][k], bb[1][k]]
	return planes

def applyMinCell(CSX, grid, minCells, solids=()):
	"""
	Smallest cell minCells = [x, y, z] (0 = leave the direction as it is) without losing conductors:
	sheets snapped onto shared planes, lines thinned by meshMinCell(), then every conductor box and thin solid STL
	conductor with fewer than two lines inside is stretched to its enclosing cell (a 0.85 um via between two chip
	metals on one line joins nothing). Returns the number of stretched bodies.
	"""
	boxes = conductorBoxes(CSX)
	for k, d in enumerate(['x', 'y', 'z']):
		if minCells[k] > 0:
			must = snapSheets(boxes, k, minCells[k]) + portPlanes(CSX, k)
			grid.SetLines(d, meshMinCell(grid.GetLines(d, True), boxes, k, minCells[k], must))
	stretched = 0
	for prim, bb in boxes:
		if prim.GetTypeName() != 'Box':
			continue
		start, stop = np.array(prim.GetStart(), dtype=float), np.array(prim.GetStop(), dtype=float)
		changed = False
		for k, d in enumerate(['x', 'y', 'z']):
			lo, hi = min(start[k], stop[k]), max(start[k], stop[k])
			g = grid.GetLines(d, True)
			if minCells[k] <= 0 or hi - lo < 1e-9 or np.sum((g >= lo - 1e-9) & (g <= hi + 1e-9)) >= 2 or lo < g[0] or hi > g[-1]:
				continue
			a, b = g[g <= lo + 1e-9][-1], g[g >= hi - 1e-9][0]
			if b - a < 1e-12:   # box sits on one line: take the neighbouring cell on the side of its centre
				i = int(np.argmin(np.abs(g - a)))
				b = g[min(i + 1, len(g) - 1)] if 0.5 * (lo + hi) >= a else a
				a = g[max(i - 1, 0)] if 0.5 * (lo + hi) < a else a
			start[k], stop[k] = (a, b) if start[k] <= stop[k] else (b, a)
			changed = True
		if changed:
			prim.SetStart(start)
			prim.SetStop(stop)
			stretched += 1
	for prop, lo, hi, prim in solids:
		k = int(np.argmin(hi - lo))
		g = grid.GetLines('xyz'[k], True)
		if minCells[k] <= 0 or not isConductor(prop) or np.sum((g >= lo[k] - 1e-9) & (g <= hi[k] + 1e-9)) >= 2 or lo[k] < g[0] or hi[k] > g[-1]:
			continue
		start, stop = np.array(lo, dtype=float), np.array(hi, dtype=float)
		start[k], stop[k] = g[g <= lo[k] + 1e-9][-1], g[g >= hi[k] - 1e-9][0]
		if stop[k] - start[k] > 1e-12:
			prop.AddBox(start, stop, priority=prim.GetPriority())   # stand-in box over its footprint
			stretched += 1
	return stretched

def extendIntoPml(CSX, grid, structMin, structMax, pmlCells, tol=1e-6):
	"""
	Bodies (boxes and STL bounding boxes, not air) with a face on the structure bounds in x or y continue as boxes to
	the end of the grid on PML faces without a lumped port: a board cut out of a bigger board runs into the absorber
	like in reference solvers (its end in air rings, the energy does not decay). z is the stacking direction: the
	ground below and the mold on top end where they end. Returns the number of added boxes.
	"""
	portBoxes = [np.array(p.GetBoundBox(), dtype=float) for prop in CSX.GetAllProperties()
	             if prop.GetTypeString() in ('LumpedElement', 'Excitation') for p in prop.GetAllPrimitives()]
	bodies = [(prop, prim) for prop in CSX.GetAllProperties() for prim in prop.GetAllPrimitives()]   # not the boxes added here
	added = 0
	for k, d in enumerate(['x', 'y']):
		g = grid.GetLines(d, True)
		for side, face in ((0, structMin[k]), (1, structMax[k])):
			# no any(): the generated script does 'from pylab import *', numpy's any() of a generator is always True
			portFace = np.any([abs(bb[side][k] - face) <= tol or abs(bb[1 - side][k] - face) <= tol for bb in portBoxes])
			if pmlCells[d][side] <= 0 or portFace:
				continue
			for prop, prim in bodies:
				t = prop.GetTypeString()
				if t not in ('Metal', 'ConductingSheet', 'Material'):
					continue
				if t == 'Material' and not isConductor(prop) and np.max(np.atleast_1d(prop.GetMaterialProperty('epsilon'))) <= 1.0:
					continue   # air
				bb = np.array(prim.GetBoundBox(), dtype=float)
				if abs(bb[side][k] - face) > tol:
					continue
				start, stop = bb[0].copy(), bb[1].copy()
				start[k], stop[k] = (g[0] - 1, face) if side == 0 else (face, g[-1] + 1)
				prop.AddBox(start, stop, priority=prim.GetPriority())
				added += 1
	return added

# Change current path to script file folder
#
abspath = os.path.abspath(__file__)
dname = os.path.dirname(abspath)
os.chdir(dname)
stl_solids = []     # solid STL bodies (property, bbox min, bbox max, primitive), see addSTL() and applyMinCell()

## constants
unit    = 0.001 # Model coordinates and lengths will be specified in mm.
fc_unit = 0.001 # STL files are exported in FreeCAD standard units (mm).

## switches & options
draw_3d_pattern = 0  # this may take a while...
use_pml = 0          # use pml boundaries instead of mur

currDir = os.getcwd()
print(currDir)

# setup_only : dry run to view geometry, validate settings, no FDTD computations
# debug_pec  : generated PEC skeleton (use ParaView to inspect)
debug_pec = False
setup_only = False or bool(os.environ.get('SETUP_ONLY'))     # env SETUP_ONLY=1: build and check the model only

## prepare simulation folder
Sim_Path = os.path.join(currDir, 'simulation_output')
Sim_CSX = 'LNA_QFN40_on_RO4350B_EM.xml'
if os.path.exists(Sim_Path):
	shutil.rmtree(Sim_Path)   # clear previous directory
	os.mkdir(Sim_Path)    # create empty simulation folder

## setup FDTD parameter & excitation function
max_timesteps = 1000000
min_decrement = 1e-05 # 10*log10(min_decrement) dB  (i.e. 1E-5 means -50 dB)
if os.environ.get('END_DB'):     # env END_DB=-40: end criterion in dB
	min_decrement = 10 ** (float(os.environ['END_DB']) / 10)
CSX = CSXCAD.ContinuousStructure()
FDTD = openEMS(NrTS=max_timesteps, EndCriteria=min_decrement)
FDTD.SetCSX(CSX)

#######################################################################################################################################
# BOUNDARY CONDITIONS
#######################################################################################################################################
BC = ["PML_8","PML_8","PML_8","PML_8","PML_8","PML_8"]
FDTD.SetBoundaryCond(BC)

#######################################################################################################################################
# COORDINATE SYSTEM
#######################################################################################################################################
def mesh():
	x,y,z

mesh.x = np.array([]) # mesh variable initialization (Note: x y z implies type Cartesian).
mesh.y = np.array([])
mesh.z = np.array([])

# mesh_graded = True: 'Fixed Distance' grid settings are cell size regions for graded mesh (see MESH FINALIZATION),
#                     False: they are filled with equidistant lines as they are (classic behaviour)
mesh_graded = True
mesh_regions = {'x': [], 'y': [], 'z': []}

openEMS_grid = CSX.GetGrid()
openEMS_grid.SetDeltaUnit(unit) # First call with empty mesh to set deltaUnit attribute.

#######################################################################################################################################
# EXCITATION excitation
#######################################################################################################################################
f0 = 5.0*1000000000.0
fc = 5.0*1000000000.0
FDTD.SetGaussExcite(f0, fc)
max_res = C0 / (f0 + fc) / 20

#######################################################################################################################################
# MATERIALS AND GEOMETRY
#######################################################################################################################################
materialList = {}

## MATERIAL - Aluminium 1% Si wire (AlSi1)
materialList['Aluminium 1% Si wire (AlSi1)'] = CSX.AddMaterial('Aluminium 1% Si wire (AlSi1)')

# conductivity 3.4e+07 S/m from GUI 'Sigma' is electric conductivity -> kappa (in CSXCAD sigma is magnetic conductivity)
materialList['Aluminium 1% Si wire (AlSi1)'].SetMaterialProperty(epsilon=1, mue=1, kappa=3.4e+07)
addSTL(CSX, materialList['Aluminium 1% Si wire (AlSi1)'], os.path.join(currDir,'Wire_GND4_24_gen_model.stl'), -7900, stl_solids)
addSTL(CSX, materialList['Aluminium 1% Si wire (AlSi1)'], os.path.join(currDir,'Wire_GND14_5_gen_model.stl'), -8000, stl_solids)
addSTL(CSX, materialList['Aluminium 1% Si wire (AlSi1)'], os.path.join(currDir,'Wire_VTMP_36_gen_model.stl'), -8100, stl_solids)
addSTL(CSX, materialList['Aluminium 1% Si wire (AlSi1)'], os.path.join(currDir,'Wire_RFOUT_25_gen_model.stl'), -8200, stl_solids)
addSTL(CSX, materialList['Aluminium 1% Si wire (AlSi1)'], os.path.join(currDir,'Wire_GND3_26_gen_model.stl'), -8300, stl_solids)
addSTL(CSX, materialList['Aluminium 1% Si wire (AlSi1)'], os.path.join(currDir,'Wire_RFIN_6_gen_model.stl'), -8400, stl_solids)
addSTL(CSX, materialList['Aluminium 1% Si wire (AlSi1)'], os.path.join(currDir,'Wire_IDC_15_gen_model.stl'), -8500, stl_solids)
addSTL(CSX, materialList['Aluminium 1% Si wire (AlSi1)'], os.path.join(currDir,'Wire_IHEAT_17_gen_model.stl'), -8600, stl_solids)
addSTL(CSX, materialList['Aluminium 1% Si wire (AlSi1)'], os.path.join(currDir,'Wire_GNDHEAT_19_gen_model.stl'), -8700, stl_solids)
addSTL(CSX, materialList['Aluminium 1% Si wire (AlSi1)'], os.path.join(currDir,'Wire_VCC_13_gen_model.stl'), -8800, stl_solids)
addSTL(CSX, materialList['Aluminium 1% Si wire (AlSi1)'], os.path.join(currDir,'Wire_GND15_7_gen_model.stl'), -8900, stl_solids)

## MATERIAL - Copper (Cu)
materialList['Copper (Cu)'] = CSX.AddMaterial('Copper (Cu)')

# conductivity 5.96e+07 S/m from GUI 'Sigma' is electric conductivity -> kappa (in CSXCAD sigma is magnetic conductivity)
materialList['Copper (Cu)'].SetMaterialProperty(epsilon=1, mue=1, kappa=5.96e+07)
#object Label: Copper_Bottom_GND (box)
materialList['Copper (Cu)'].AddBox([-14.9495, -4.5, -0.035], [14.9495, 4.5, 0.0], priority=9600)
addSTL(CSX, materialList['Copper (Cu)'], os.path.join(currDir,'Copper_Vias_GND_29_gen_model.stl'), 9500, stl_solids)
addSTL(CSX, materialList['Copper (Cu)'], os.path.join(currDir,'Copper_Vias_GND_4_gen_model.stl'), 8600, stl_solids)
addSTL(CSX, materialList['Copper (Cu)'], os.path.join(currDir,'Copper_Vias_GND_25_gen_model.stl'), -400, stl_solids)
addSTL(CSX, materialList['Copper (Cu)'], os.path.join(currDir,'Copper_Vias_GND_26_gen_model.stl'), -500, stl_solids)
addSTL(CSX, materialList['Copper (Cu)'], os.path.join(currDir,'Copper_Vias_GND_27_gen_model.stl'), -600, stl_solids)
addSTL(CSX, materialList['Copper (Cu)'], os.path.join(currDir,'Copper_Vias_GND_28_gen_model.stl'), -700, stl_solids)
addSTL(CSX, materialList['Copper (Cu)'], os.path.join(currDir,'Copper_Top_GND_gen_model.stl'), -800, stl_solids)
addSTL(CSX, materialList['Copper (Cu)'], os.path.join(currDir,'Copper_Top_RFIN_gen_model.stl'), -900, stl_solids)
addSTL(CSX, materialList['Copper (Cu)'], os.path.join(currDir,'Copper_Top_RFOUT_gen_model.stl'), -1000, stl_solids)
addSTL(CSX, materialList['Copper (Cu)'], os.path.join(currDir,'Copper_Top_IDC_gen_model.stl'), -1100, stl_solids)
addSTL(CSX, materialList['Copper (Cu)'], os.path.join(currDir,'Copper_Top_IHEAT_gen_model.stl'), -1200, stl_solids)
addSTL(CSX, materialList['Copper (Cu)'], os.path.join(currDir,'Copper_Top_GNDHEAT_gen_model.stl'), -1300, stl_solids)
addSTL(CSX, materialList['Copper (Cu)'], os.path.join(currDir,'Copper_Vias_GND_3_gen_model.stl'), 8500, stl_solids)
addSTL(CSX, materialList['Copper (Cu)'], os.path.join(currDir,'Copper_Top_VCC_gen_model.stl'), -1400, stl_solids)
addSTL(CSX, materialList['Copper (Cu)'], os.path.join(currDir,'Copper_Top_VTMP_gen_model.stl'), -1500, stl_solids)
#object Label: Copper_Top_NC_2 (box)
materialList['Copper (Cu)'].AddBox([-1.875, 2.05, 0.254], [-1.625, 3.35, 0.289], priority=-1600)
#object Label: Copper_Top_NC_3 (box)
materialList['Copper (Cu)'].AddBox([-1.375, 2.05, 0.254], [-1.125, 3.35, 0.289], priority=-1700)
#object Label: Copper_Top_NC_5 (box)
materialList['Copper (Cu)'].AddBox([-3.35, 1.625, 0.254], [-2.05, 1.875, 0.289], priority=-1800)
#object Label: Copper_Top_NC_6 (box)
materialList['Copper (Cu)'].AddBox([-3.35, 1.125, 0.254], [-2.05, 1.375, 0.289], priority=-1900)
#object Label: Copper_Top_NC_7 (box)
materialList['Copper (Cu)'].AddBox([-3.35, -1.875, 0.254], [-2.05, -1.625, 0.289], priority=-2000)
#object Label: Copper_Top_NC_9 (box)
materialList['Copper (Cu)'].AddBox([-1.875, -3.35, 0.254], [-1.625, -2.05, 0.289], priority=-2100)
#object Label: Copper_Top_NC_12 (box)
materialList['Copper (Cu)'].AddBox([0.625, 2.05, 0.254], [0.875, 3.35, 0.289], priority=-2200)
#object Label: Copper_Top_NC_13 (box)
materialList['Copper (Cu)'].AddBox([1.125, 2.05, 0.254], [1.375, 3.35, 0.289], priority=-2300)
addSTL(CSX, materialList['Copper (Cu)'], os.path.join(currDir,'Copper_Vias_GND_5_gen_model.stl'), 8400, stl_solids)
#object Label: Copper_Top_NC_14 (box)
materialList['Copper (Cu)'].AddBox([1.625, 2.05, 0.254], [1.875, 3.35, 0.289], priority=-2400)
#object Label: Copper_Top_NC_16 (box)
materialList['Copper (Cu)'].AddBox([2.05, 1.625, 0.254], [3.35, 1.875, 0.289], priority=-2500)
#object Label: Copper_Top_NC_17 (box)
materialList['Copper (Cu)'].AddBox([2.05, 1.125, 0.254], [3.35, 1.375, 0.289], priority=-2600)
#object Label: Copper_Top_NC_18 (box)
materialList['Copper (Cu)'].AddBox([2.05, -1.875, 0.254], [3.35, -1.625, 0.289], priority=-2700)
#object Label: Copper_Top_NC_1 (box)
materialList['Copper (Cu)'].AddBox([-2.375, 2.6, 0.254], [-2.125, 3.35, 0.289], priority=-2800)
#object Label: Copper_Top_NC_4 (box)
materialList['Copper (Cu)'].AddBox([-3.35, 2.125, 0.254], [-2.6, 2.375, 0.289], priority=-2900)
#object Label: Copper_Top_NC_8 (box)
materialList['Copper (Cu)'].AddBox([-3.35, -2.375, 0.254], [-2.6, -2.125, 0.289], priority=-3000)
#object Label: Copper_Top_NC_10 (box)
materialList['Copper (Cu)'].AddBox([-2.375, -3.35, 0.254], [-2.125, -2.6, 0.289], priority=-3100)
#object Label: Copper_Top_NC_11 (box)
materialList['Copper (Cu)'].AddBox([2.125, 2.6, 0.254], [2.375, 3.35, 0.289], priority=-3200)
#object Label: Copper_Top_NC_15 (box)
materialList['Copper (Cu)'].AddBox([2.6, 2.125, 0.254], [3.35, 2.375, 0.289], priority=-3300)
addSTL(CSX, materialList['Copper (Cu)'], os.path.join(currDir,'Copper_Vias_GND_8_gen_model.stl'), 8300, stl_solids)
#object Label: Copper_Top_NC_19 (box)
materialList['Copper (Cu)'].AddBox([2.6, -2.375, 0.254], [3.35, -2.125, 0.289], priority=-3400)
#object Label: Copper_Top_NC_20 (box)
materialList['Copper (Cu)'].AddBox([2.125, -3.35, 0.254], [2.375, -2.6, 0.289], priority=-3500)
addSTL(CSX, materialList['Copper (Cu)'], os.path.join(currDir,'Copper_Vias_GND_2_gen_model.stl'), 8200, stl_solids)
addSTL(CSX, materialList['Copper (Cu)'], os.path.join(currDir,'Copper_Vias_GND_7_gen_model.stl'), 8100, stl_solids)
addSTL(CSX, materialList['Copper (Cu)'], os.path.join(currDir,'Copper_Vias_GND_9_gen_model.stl'), 8000, stl_solids)
addSTL(CSX, materialList['Copper (Cu)'], os.path.join(currDir,'Copper_Vias_GND_12_gen_model.stl'), 7900, stl_solids)
addSTL(CSX, materialList['Copper (Cu)'], os.path.join(currDir,'Copper_Vias_GND_6_gen_model.stl'), 7800, stl_solids)
addSTL(CSX, materialList['Copper (Cu)'], os.path.join(currDir,'Copper_Vias_GND_11_gen_model.stl'), 7700, stl_solids)
addSTL(CSX, materialList['Copper (Cu)'], os.path.join(currDir,'Copper_Vias_GND_30_gen_model.stl'), 9400, stl_solids)
addSTL(CSX, materialList['Copper (Cu)'], os.path.join(currDir,'Copper_Vias_GND_10_gen_model.stl'), 7600, stl_solids)
addSTL(CSX, materialList['Copper (Cu)'], os.path.join(currDir,'Copper_Vias_GND_13_gen_model.stl'), 7500, stl_solids)
addSTL(CSX, materialList['Copper (Cu)'], os.path.join(currDir,'Copper_Vias_GND_16_gen_model.stl'), 7400, stl_solids)
addSTL(CSX, materialList['Copper (Cu)'], os.path.join(currDir,'Copper_Vias_GND_37_gen_model.stl'), 7300, stl_solids)
addSTL(CSX, materialList['Copper (Cu)'], os.path.join(currDir,'Copper_Vias_GND_38_gen_model.stl'), 7200, stl_solids)
addSTL(CSX, materialList['Copper (Cu)'], os.path.join(currDir,'Copper_Vias_GND_39_gen_model.stl'), 7100, stl_solids)
addSTL(CSX, materialList['Copper (Cu)'], os.path.join(currDir,'Copper_Vias_GND_40_gen_model.stl'), 7000, stl_solids)
addSTL(CSX, materialList['Copper (Cu)'], os.path.join(currDir,'Copper_Vias_GND_41_gen_model.stl'), 6900, stl_solids)
addSTL(CSX, materialList['Copper (Cu)'], os.path.join(currDir,'Copper_Vias_GND_42_gen_model.stl'), 6800, stl_solids)
addSTL(CSX, materialList['Copper (Cu)'], os.path.join(currDir,'Copper_Vias_GND_43_gen_model.stl'), 6700, stl_solids)
addSTL(CSX, materialList['Copper (Cu)'], os.path.join(currDir,'Copper_Vias_GND_31_gen_model.stl'), 9300, stl_solids)
addSTL(CSX, materialList['Copper (Cu)'], os.path.join(currDir,'Copper_Vias_GND_44_gen_model.stl'), 6600, stl_solids)
addSTL(CSX, materialList['Copper (Cu)'], os.path.join(currDir,'Copper_Vias_GND_45_gen_model.stl'), 6500, stl_solids)
addSTL(CSX, materialList['Copper (Cu)'], os.path.join(currDir,'Copper_Vias_GND_46_gen_model.stl'), 6400, stl_solids)
addSTL(CSX, materialList['Copper (Cu)'], os.path.join(currDir,'Copper_Vias_GND_47_gen_model.stl'), 6300, stl_solids)
addSTL(CSX, materialList['Copper (Cu)'], os.path.join(currDir,'Copper_Vias_GND_48_gen_model.stl'), 6200, stl_solids)
addSTL(CSX, materialList['Copper (Cu)'], os.path.join(currDir,'Copper_Vias_GND_49_gen_model.stl'), 6100, stl_solids)
addSTL(CSX, materialList['Copper (Cu)'], os.path.join(currDir,'Copper_Vias_GND_50_gen_model.stl'), 6000, stl_solids)
addSTL(CSX, materialList['Copper (Cu)'], os.path.join(currDir,'Copper_Vias_GND_51_gen_model.stl'), 5900, stl_solids)
addSTL(CSX, materialList['Copper (Cu)'], os.path.join(currDir,'Copper_Vias_GND_52_gen_model.stl'), 5800, stl_solids)
addSTL(CSX, materialList['Copper (Cu)'], os.path.join(currDir,'Copper_Vias_GND_53_gen_model.stl'), 5700, stl_solids)
addSTL(CSX, materialList['Copper (Cu)'], os.path.join(currDir,'Copper_Vias_GND_32_gen_model.stl'), 9200, stl_solids)
addSTL(CSX, materialList['Copper (Cu)'], os.path.join(currDir,'Copper_Vias_GND_54_gen_model.stl'), 5600, stl_solids)
addSTL(CSX, materialList['Copper (Cu)'], os.path.join(currDir,'Copper_Vias_GND_55_gen_model.stl'), 5500, stl_solids)
addSTL(CSX, materialList['Copper (Cu)'], os.path.join(currDir,'Copper_Vias_GND_56_gen_model.stl'), 5400, stl_solids)
addSTL(CSX, materialList['Copper (Cu)'], os.path.join(currDir,'Copper_Vias_GND_89_gen_model.stl'), 5300, stl_solids)
addSTL(CSX, materialList['Copper (Cu)'], os.path.join(currDir,'Copper_Vias_GND_15_gen_model.stl'), 5200, stl_solids)
addSTL(CSX, materialList['Copper (Cu)'], os.path.join(currDir,'Copper_Vias_GND_14_gen_model.stl'), 5100, stl_solids)
addSTL(CSX, materialList['Copper (Cu)'], os.path.join(currDir,'Copper_Vias_GND_65_gen_model.stl'), 5000, stl_solids)
addSTL(CSX, materialList['Copper (Cu)'], os.path.join(currDir,'Copper_Vias_GND_66_gen_model.stl'), 4900, stl_solids)
addSTL(CSX, materialList['Copper (Cu)'], os.path.join(currDir,'Copper_Vias_GND_57_gen_model.stl'), 4800, stl_solids)
addSTL(CSX, materialList['Copper (Cu)'], os.path.join(currDir,'Copper_Vias_GND_58_gen_model.stl'), 4700, stl_solids)
addSTL(CSX, materialList['Copper (Cu)'], os.path.join(currDir,'Copper_Vias_GND_33_gen_model.stl'), 9100, stl_solids)
addSTL(CSX, materialList['Copper (Cu)'], os.path.join(currDir,'Copper_Vias_GND_59_gen_model.stl'), 4600, stl_solids)
addSTL(CSX, materialList['Copper (Cu)'], os.path.join(currDir,'Copper_Vias_GND_60_gen_model.stl'), 4500, stl_solids)
addSTL(CSX, materialList['Copper (Cu)'], os.path.join(currDir,'Copper_Vias_GND_61_gen_model.stl'), 4400, stl_solids)
addSTL(CSX, materialList['Copper (Cu)'], os.path.join(currDir,'Copper_Vias_GND_62_gen_model.stl'), 4300, stl_solids)
addSTL(CSX, materialList['Copper (Cu)'], os.path.join(currDir,'Copper_Vias_GND_63_gen_model.stl'), 4200, stl_solids)
addSTL(CSX, materialList['Copper (Cu)'], os.path.join(currDir,'Copper_Vias_GND_64_gen_model.stl'), 4100, stl_solids)
addSTL(CSX, materialList['Copper (Cu)'], os.path.join(currDir,'Copper_Vias_GND_67_gen_model.stl'), 4000, stl_solids)
addSTL(CSX, materialList['Copper (Cu)'], os.path.join(currDir,'Copper_Vias_GND_68_gen_model.stl'), 3900, stl_solids)
addSTL(CSX, materialList['Copper (Cu)'], os.path.join(currDir,'Copper_Vias_GND_69_gen_model.stl'), 3800, stl_solids)
addSTL(CSX, materialList['Copper (Cu)'], os.path.join(currDir,'Copper_Vias_GND_70_gen_model.stl'), 3700, stl_solids)
addSTL(CSX, materialList['Copper (Cu)'], os.path.join(currDir,'Copper_Vias_GND_34_gen_model.stl'), 9000, stl_solids)
addSTL(CSX, materialList['Copper (Cu)'], os.path.join(currDir,'Copper_Vias_GND_71_gen_model.stl'), 3600, stl_solids)
addSTL(CSX, materialList['Copper (Cu)'], os.path.join(currDir,'Copper_Vias_GND_72_gen_model.stl'), 3500, stl_solids)
addSTL(CSX, materialList['Copper (Cu)'], os.path.join(currDir,'Copper_Vias_GND_73_gen_model.stl'), 3400, stl_solids)
addSTL(CSX, materialList['Copper (Cu)'], os.path.join(currDir,'Copper_Vias_GND_74_gen_model.stl'), 3300, stl_solids)
addSTL(CSX, materialList['Copper (Cu)'], os.path.join(currDir,'Copper_Vias_GND_75_gen_model.stl'), 3200, stl_solids)
addSTL(CSX, materialList['Copper (Cu)'], os.path.join(currDir,'Copper_Vias_GND_76_gen_model.stl'), 3100, stl_solids)
addSTL(CSX, materialList['Copper (Cu)'], os.path.join(currDir,'Copper_Vias_GND_77_gen_model.stl'), 3000, stl_solids)
addSTL(CSX, materialList['Copper (Cu)'], os.path.join(currDir,'Copper_Vias_GND_78_gen_model.stl'), 2900, stl_solids)
addSTL(CSX, materialList['Copper (Cu)'], os.path.join(currDir,'Copper_Vias_GND_79_gen_model.stl'), 2800, stl_solids)
addSTL(CSX, materialList['Copper (Cu)'], os.path.join(currDir,'Copper_Vias_GND_80_gen_model.stl'), 2700, stl_solids)
addSTL(CSX, materialList['Copper (Cu)'], os.path.join(currDir,'Copper_Vias_GND_35_gen_model.stl'), 8900, stl_solids)
addSTL(CSX, materialList['Copper (Cu)'], os.path.join(currDir,'Copper_Vias_GND_81_gen_model.stl'), 2600, stl_solids)
addSTL(CSX, materialList['Copper (Cu)'], os.path.join(currDir,'Copper_Vias_GND_82_gen_model.stl'), 2500, stl_solids)
addSTL(CSX, materialList['Copper (Cu)'], os.path.join(currDir,'Copper_Vias_GND_83_gen_model.stl'), 2400, stl_solids)
addSTL(CSX, materialList['Copper (Cu)'], os.path.join(currDir,'Copper_Vias_GND_84_gen_model.stl'), 2300, stl_solids)
addSTL(CSX, materialList['Copper (Cu)'], os.path.join(currDir,'Copper_Vias_GND_85_gen_model.stl'), 2200, stl_solids)
addSTL(CSX, materialList['Copper (Cu)'], os.path.join(currDir,'Copper_Vias_GND_86_gen_model.stl'), 2100, stl_solids)
addSTL(CSX, materialList['Copper (Cu)'], os.path.join(currDir,'Copper_Vias_GND_87_gen_model.stl'), 2000, stl_solids)
addSTL(CSX, materialList['Copper (Cu)'], os.path.join(currDir,'Copper_Vias_GND_88_gen_model.stl'), 1900, stl_solids)
addSTL(CSX, materialList['Copper (Cu)'], os.path.join(currDir,'Copper_Vias_GND_90_gen_model.stl'), 1800, stl_solids)
addSTL(CSX, materialList['Copper (Cu)'], os.path.join(currDir,'Copper_Vias_GND_91_gen_model.stl'), 1700, stl_solids)
addSTL(CSX, materialList['Copper (Cu)'], os.path.join(currDir,'Copper_Vias_GND_36_gen_model.stl'), 8800, stl_solids)
addSTL(CSX, materialList['Copper (Cu)'], os.path.join(currDir,'Copper_Vias_GND_92_gen_model.stl'), 1600, stl_solids)
addSTL(CSX, materialList['Copper (Cu)'], os.path.join(currDir,'Copper_Vias_GND_93_gen_model.stl'), 1500, stl_solids)
addSTL(CSX, materialList['Copper (Cu)'], os.path.join(currDir,'Copper_Vias_GND_94_gen_model.stl'), 1400, stl_solids)
addSTL(CSX, materialList['Copper (Cu)'], os.path.join(currDir,'Copper_Vias_GND_95_gen_model.stl'), 1300, stl_solids)
addSTL(CSX, materialList['Copper (Cu)'], os.path.join(currDir,'Copper_Vias_GND_96_gen_model.stl'), 1200, stl_solids)
addSTL(CSX, materialList['Copper (Cu)'], os.path.join(currDir,'Copper_Vias_GND_97_gen_model.stl'), 1100, stl_solids)
addSTL(CSX, materialList['Copper (Cu)'], os.path.join(currDir,'Copper_Vias_GND_98_gen_model.stl'), 1000, stl_solids)
addSTL(CSX, materialList['Copper (Cu)'], os.path.join(currDir,'Copper_Vias_GND_99_gen_model.stl'), 900, stl_solids)
addSTL(CSX, materialList['Copper (Cu)'], os.path.join(currDir,'Copper_Vias_GND_100_gen_model.stl'), 800, stl_solids)
addSTL(CSX, materialList['Copper (Cu)'], os.path.join(currDir,'Copper_Vias_GND_101_gen_model.stl'), 700, stl_solids)
addSTL(CSX, materialList['Copper (Cu)'], os.path.join(currDir,'Copper_Vias_GND_1_gen_model.stl'), 8700, stl_solids)
addSTL(CSX, materialList['Copper (Cu)'], os.path.join(currDir,'Copper_Vias_GND_102_gen_model.stl'), 600, stl_solids)
addSTL(CSX, materialList['Copper (Cu)'], os.path.join(currDir,'Copper_Vias_GND_103_gen_model.stl'), 500, stl_solids)
addSTL(CSX, materialList['Copper (Cu)'], os.path.join(currDir,'Copper_Vias_GND_17_gen_model.stl'), 400, stl_solids)
addSTL(CSX, materialList['Copper (Cu)'], os.path.join(currDir,'Copper_Vias_GND_18_gen_model.stl'), 300, stl_solids)
addSTL(CSX, materialList['Copper (Cu)'], os.path.join(currDir,'Copper_Vias_GND_19_gen_model.stl'), 200, stl_solids)
addSTL(CSX, materialList['Copper (Cu)'], os.path.join(currDir,'Copper_Vias_GND_20_gen_model.stl'), 100, stl_solids)
addSTL(CSX, materialList['Copper (Cu)'], os.path.join(currDir,'Copper_Vias_GND_21_gen_model.stl'), 0, stl_solids)
addSTL(CSX, materialList['Copper (Cu)'], os.path.join(currDir,'Copper_Vias_GND_22_gen_model.stl'), -100, stl_solids)
addSTL(CSX, materialList['Copper (Cu)'], os.path.join(currDir,'Copper_Vias_GND_23_gen_model.stl'), -200, stl_solids)
addSTL(CSX, materialList['Copper (Cu)'], os.path.join(currDir,'Copper_Vias_GND_24_gen_model.stl'), -300, stl_solids)

## MATERIAL - Copper alloy C194 (leadframe)
materialList['Copper alloy C194 (leadframe)'] = CSX.AddMaterial('Copper alloy C194 (leadframe)')

# conductivity 3.77e+07 S/m from GUI 'Sigma' is electric conductivity -> kappa (in CSXCAD sigma is magnetic conductivity)
materialList['Copper alloy C194 (leadframe)'].SetMaterialProperty(epsilon=1, mue=1, kappa=3.77e+07)
#object Label: QFN_40_6x6_leadframe_1 (box)
materialList['Copper alloy C194 (leadframe)'].AddBox([-1.85, -1.846, 0.289], [1.85, 1.854, 0.489], priority=-3600)
#object Label: QFN_40_6x6_leadframe_2 (box)
materialList['Copper alloy C194 (leadframe)'].AddBox([-3.0, -2.371, 0.289], [-2.0, -2.121, 0.489], priority=-3700)
#object Label: QFN_40_6x6_leadframe_3 (box)
materialList['Copper alloy C194 (leadframe)'].AddBox([-3.0, -1.871, 0.289], [-2.0, -1.621, 0.489], priority=-3800)
#object Label: QFN_40_6x6_leadframe_4 (box)
materialList['Copper alloy C194 (leadframe)'].AddBox([-3.0, -1.371, 0.289], [-2.0, -1.121, 0.489], priority=-3900)
#object Label: QFN_40_6x6_leadframe_5 (box)
materialList['Copper alloy C194 (leadframe)'].AddBox([-3.0, -0.871, 0.289], [-2.0, -0.621, 0.489], priority=-4000)
#object Label: QFN_40_6x6_leadframe_6 (box)
materialList['Copper alloy C194 (leadframe)'].AddBox([-3.0, -0.371, 0.289], [-2.0, -0.121, 0.489], priority=-4100)
#object Label: QFN_40_6x6_leadframe_7 (box)
materialList['Copper alloy C194 (leadframe)'].AddBox([-3.0, 0.129, 0.289], [-2.0, 0.379, 0.489], priority=-4200)
#object Label: QFN_40_6x6_leadframe_8 (box)
materialList['Copper alloy C194 (leadframe)'].AddBox([-3.0, 0.629, 0.289], [-2.0, 0.879, 0.489], priority=-4300)
#object Label: QFN_40_6x6_leadframe_9 (box)
materialList['Copper alloy C194 (leadframe)'].AddBox([-3.0, 1.129, 0.289], [-2.0, 1.379, 0.489], priority=-4400)
#object Label: QFN_40_6x6_leadframe_10 (box)
materialList['Copper alloy C194 (leadframe)'].AddBox([-3.0, 1.629, 0.289], [-2.0, 1.879, 0.489], priority=-4500)
#object Label: QFN_40_6x6_leadframe_11 (box)
materialList['Copper alloy C194 (leadframe)'].AddBox([-3.0, 2.129, 0.289], [-2.0, 2.379, 0.489], priority=-4600)
#object Label: QFN_40_6x6_leadframe_12 (box)
materialList['Copper alloy C194 (leadframe)'].AddBox([-2.375, -2.996, 0.289], [-2.125, -1.996, 0.489], priority=-4700)
#object Label: QFN_40_6x6_leadframe_13 (box)
materialList['Copper alloy C194 (leadframe)'].AddBox([-1.875, -2.996, 0.289], [-1.625, -1.996, 0.489], priority=-4800)
#object Label: QFN_40_6x6_leadframe_14 (box)
materialList['Copper alloy C194 (leadframe)'].AddBox([-1.375, -2.996, 0.289], [-1.125, -1.996, 0.489], priority=-4900)
#object Label: QFN_40_6x6_leadframe_15 (box)
materialList['Copper alloy C194 (leadframe)'].AddBox([-0.875, -2.996, 0.289], [-0.625, -1.996, 0.489], priority=-5000)
#object Label: QFN_40_6x6_leadframe_16 (box)
materialList['Copper alloy C194 (leadframe)'].AddBox([-0.375, -2.996, 0.289], [-0.125, -1.996, 0.489], priority=-5100)
#object Label: QFN_40_6x6_leadframe_17 (box)
materialList['Copper alloy C194 (leadframe)'].AddBox([0.125, -2.996, 0.289], [0.375, -1.996, 0.489], priority=-5200)
#object Label: QFN_40_6x6_leadframe_18 (box)
materialList['Copper alloy C194 (leadframe)'].AddBox([0.625, -2.996, 0.289], [0.875, -1.996, 0.489], priority=-5300)
#object Label: QFN_40_6x6_leadframe_19 (box)
materialList['Copper alloy C194 (leadframe)'].AddBox([1.125, -2.996, 0.289], [1.375, -1.996, 0.489], priority=-5400)
#object Label: QFN_40_6x6_leadframe_20 (box)
materialList['Copper alloy C194 (leadframe)'].AddBox([1.625, -2.996, 0.289], [1.875, -1.996, 0.489], priority=-5500)
#object Label: QFN_40_6x6_leadframe_21 (box)
materialList['Copper alloy C194 (leadframe)'].AddBox([2.125, -2.996, 0.289], [2.375, -1.996, 0.489], priority=-5600)
#object Label: QFN_40_6x6_leadframe_22 (box)
materialList['Copper alloy C194 (leadframe)'].AddBox([2.0, -2.371, 0.289], [3.0, -2.121, 0.489], priority=-5700)
#object Label: QFN_40_6x6_leadframe_23 (box)
materialList['Copper alloy C194 (leadframe)'].AddBox([2.0, -1.871, 0.289], [3.0, -1.621, 0.489], priority=-5800)
#object Label: QFN_40_6x6_leadframe_24 (box)
materialList['Copper alloy C194 (leadframe)'].AddBox([2.0, -1.371, 0.289], [3.0, -1.121, 0.489], priority=-5900)
#object Label: QFN_40_6x6_leadframe_25 (box)
materialList['Copper alloy C194 (leadframe)'].AddBox([2.0, -0.871, 0.289], [3.0, -0.621, 0.489], priority=-6000)
#object Label: QFN_40_6x6_leadframe_26 (box)
materialList['Copper alloy C194 (leadframe)'].AddBox([2.0, -0.371, 0.289], [3.0, -0.121, 0.489], priority=-6100)
#object Label: QFN_40_6x6_leadframe_27 (box)
materialList['Copper alloy C194 (leadframe)'].AddBox([2.0, 0.129, 0.289], [3.0, 0.379, 0.489], priority=-6200)
#object Label: QFN_40_6x6_leadframe_28 (box)
materialList['Copper alloy C194 (leadframe)'].AddBox([2.0, 0.629, 0.289], [3.0, 0.879, 0.489], priority=-6300)
#object Label: QFN_40_6x6_leadframe_29 (box)
materialList['Copper alloy C194 (leadframe)'].AddBox([2.0, 1.129, 0.289], [3.0, 1.379, 0.489], priority=-6400)
#object Label: QFN_40_6x6_leadframe_30 (box)
materialList['Copper alloy C194 (leadframe)'].AddBox([2.0, 1.629, 0.289], [3.0, 1.879, 0.489], priority=-6500)
#object Label: QFN_40_6x6_leadframe_31 (box)
materialList['Copper alloy C194 (leadframe)'].AddBox([2.0, 2.129, 0.289], [3.0, 2.379, 0.489], priority=-6600)
#object Label: QFN_40_6x6_leadframe_32 (box)
materialList['Copper alloy C194 (leadframe)'].AddBox([-2.375, 2.004, 0.289], [-2.125, 3.004, 0.489], priority=-6700)
#object Label: QFN_40_6x6_leadframe_33 (box)
materialList['Copper alloy C194 (leadframe)'].AddBox([-1.875, 2.004, 0.289], [-1.625, 3.004, 0.489], priority=-6800)
#object Label: QFN_40_6x6_leadframe_34 (box)
materialList['Copper alloy C194 (leadframe)'].AddBox([-1.375, 2.004, 0.289], [-1.125, 3.004, 0.489], priority=-6900)
#object Label: QFN_40_6x6_leadframe_35 (box)
materialList['Copper alloy C194 (leadframe)'].AddBox([-0.875, 2.004, 0.289], [-0.625, 3.004, 0.489], priority=-7000)
#object Label: QFN_40_6x6_leadframe_36 (box)
materialList['Copper alloy C194 (leadframe)'].AddBox([-0.375, 2.004, 0.289], [-0.125, 3.004, 0.489], priority=-7100)
#object Label: QFN_40_6x6_leadframe_37 (box)
materialList['Copper alloy C194 (leadframe)'].AddBox([0.125, 2.004, 0.289], [0.375, 3.004, 0.489], priority=-7200)
#object Label: QFN_40_6x6_leadframe_38 (box)
materialList['Copper alloy C194 (leadframe)'].AddBox([0.625, 2.004, 0.289], [0.875, 3.004, 0.489], priority=-7300)
#object Label: QFN_40_6x6_leadframe_39 (box)
materialList['Copper alloy C194 (leadframe)'].AddBox([1.125, 2.004, 0.289], [1.375, 3.004, 0.489], priority=-7400)
#object Label: QFN_40_6x6_leadframe_40 (box)
materialList['Copper alloy C194 (leadframe)'].AddBox([1.625, 2.004, 0.289], [1.875, 3.004, 0.489], priority=-7500)
#object Label: QFN_40_6x6_leadframe_41 (box)
materialList['Copper alloy C194 (leadframe)'].AddBox([2.125, 2.004, 0.289], [2.375, 3.004, 0.489], priority=-7600)

## MATERIAL - Die attach epoxy, Ag-filled
materialList['Die attach epoxy, Ag-filled'] = CSX.AddMaterial('Die attach epoxy, Ag-filled')

# conductivity 100000 S/m from GUI 'Sigma' is electric conductivity -> kappa (in CSXCAD sigma is magnetic conductivity)
materialList['Die attach epoxy, Ag-filled'].SetMaterialProperty(epsilon=1, mue=1, kappa=100000)
#object Label: Die_attach_ALL_LNA (box)
materialList['Die attach epoxy, Ag-filled'].AddBox([-0.39, -0.486, 0.489], [0.39, 0.494, 0.514], priority=-7700)

## MATERIAL - IHP SG13G2 - EPI
materialList['IHP SG13G2 - EPI'] = CSX.AddMaterial('IHP SG13G2 - EPI')

materialList['IHP SG13G2 - EPI'].SetMaterialProperty(epsilon=11.9, mue=1, kappa=5)
#object Label: ALL_LNA_EPI (box)
materialList['IHP SG13G2 - EPI'].AddBox([-0.39, -0.486, 0.694], [0.39, 0.494, 0.69775], priority=-9200)

## MATERIAL - IHP SG13G2 - Passive
materialList['IHP SG13G2 - Passive'] = CSX.AddMaterial('IHP SG13G2 - Passive')

materialList['IHP SG13G2 - Passive'].SetMaterialProperty(epsilon=6.6, mue=1)
#object Label: ALL_LNA_Passive (box)
materialList['IHP SG13G2 - Passive'].AddBox([-0.39, -0.486, 0.71348], [0.39, 0.494, 0.71388], priority=-9300)

## MATERIAL - IHP SG13G2 - SiO2
materialList['IHP SG13G2 - SiO2'] = CSX.AddMaterial('IHP SG13G2 - SiO2')

materialList['IHP SG13G2 - SiO2'].SetMaterialProperty(epsilon=4.1, mue=1)
#object Label: ALL_LNA_SiO2 (box)
materialList['IHP SG13G2 - SiO2'].AddBox([-0.39, -0.486, 0.69775], [0.39, 0.494, 0.71348], priority=-9500)

## MATERIAL - IHP SG13G2 - Substrate
materialList['IHP SG13G2 - Substrate'] = CSX.AddMaterial('IHP SG13G2 - Substrate')

materialList['IHP SG13G2 - Substrate'].SetMaterialProperty(epsilon=11.9, mue=1, kappa=2)
#object Label: ALL_LNA_Substrate (box)
materialList['IHP SG13G2 - Substrate'].AddBox([-0.39, -0.486, 0.514], [0.39, 0.494, 0.694], priority=-9100)

## MATERIAL - IHP SG13G2 - TopMetal2
materialList['IHP SG13G2 - TopMetal2'] = CSX.AddMaterial('IHP SG13G2 - TopMetal2')

# conductivity 3.03e+07 S/m from GUI 'Sigma' is electric conductivity -> kappa (in CSXCAD sigma is magnetic conductivity)
materialList['IHP SG13G2 - TopMetal2'].SetMaterialProperty(epsilon=1, mue=1, kappa=3.03e+07)
addSTL(CSX, materialList['IHP SG13G2 - TopMetal2'], os.path.join(currDir,'ALL_LNA_TopMetal2_gen_model.stl'), -7800, stl_solids)

## MATERIAL - Mould compound (EMC)
materialList['Mould compound (EMC)'] = CSX.AddMaterial('Mould compound (EMC)')

materialList['Mould compound (EMC)'].SetMaterialProperty(epsilon=3.8, mue=1, kappa=0.0111)
addSTL(CSX, materialList['Mould compound (EMC)'], os.path.join(currDir,'QFN_40_6x6_body_gen_model.stl'), -9400, stl_solids)

## MATERIAL - Rogers RO4350B
materialList['Rogers RO4350B'] = CSX.AddMaterial('Rogers RO4350B')

materialList['Rogers RO4350B'].SetMaterialProperty(epsilon=3.66, mue=1, kappa=0.003955)
addSTL(CSX, materialList['Rogers RO4350B'], os.path.join(currDir,'RO4350B_Substrate_gen_model.stl'), -9000, stl_solids)

## MATERIAL - PEC
materialList['PEC'] = CSX.AddMetal('PEC')



#######################################################################################################################################
# GRID LINES
#######################################################################################################################################

## GRID - ALL_LNA_SiO2 - ALL_LNA_SiO2 (Fixed Distance)
mesh.z = np.delete(mesh.z, np.argwhere((mesh.z >= 0.69775) & (mesh.z <= 0.7134803)))
mesh_regions['z'].append((0.69775, 0.7134803, 0.00524))
if not mesh_graded:
	mesh.z = np.concatenate((mesh.z, arangeWithEndpoint(0.69775,0.7134803,0.00524)))

## GRID - QFN_40_6x6_body - QFN_40_6x6_body (Fixed Distance)
mesh.z = np.delete(mesh.z, np.argwhere((mesh.z >= 0.289) & (mesh.z <= 1.139)))
mesh_regions['z'].append((0.289, 1.139, 0.283))
if not mesh_graded:
	mesh.z = np.concatenate((mesh.z, arangeWithEndpoint(0.289,1.139,0.283)))

## GRID - ALL_LNA_Passive - ALL_LNA_Passive (Fixed Distance)
mesh.z = np.delete(mesh.z, np.argwhere((mesh.z >= 0.7134803) & (mesh.z <= 0.7138803)))
mesh_regions['z'].append((0.7134803, 0.7138803, 0.005))
if not mesh_graded:
	mesh.z = np.concatenate((mesh.z, arangeWithEndpoint(0.7134803,0.7138803,0.005)))

## GRID - ALL_LNA_EPI - ALL_LNA_EPI (Fixed Distance)
mesh.z = np.delete(mesh.z, np.argwhere((mesh.z >= 0.694) & (mesh.z <= 0.69775)))
mesh_regions['z'].append((0.694, 0.69775, 0.005))
if not mesh_graded:
	mesh.z = np.concatenate((mesh.z, arangeWithEndpoint(0.694,0.69775,0.005)))

## GRID - ALL_LNA_Substrate - ALL_LNA_Substrate (Fixed Distance)
mesh.z = np.delete(mesh.z, np.argwhere((mesh.z >= 0.514) & (mesh.z <= 0.694)))
mesh_regions['z'].append((0.514, 0.694, 0.06))
if not mesh_graded:
	mesh.z = np.concatenate((mesh.z, arangeWithEndpoint(0.514,0.694,0.06)))

## GRID - RO4350B_Substrate - RO4350B_Substrate (Fixed Distance)
mesh.z = np.delete(mesh.z, np.argwhere((mesh.z >= 0) & (mesh.z <= 0.254)))
mesh_regions['z'].append((0, 0.254, 0.0847))
if not mesh_graded:
	mesh.z = np.concatenate((mesh.z, arangeWithEndpoint(0,0.254,0.0847)))

## GRID - Wire_GND15_7 - Wire_GND15_7 (Fixed Distance)
mesh.x = np.delete(mesh.x, np.argwhere((mesh.x >= -2.27891203814) & (mesh.x <= -0.264099407057)))
mesh_regions['x'].append((-2.27891203814, -0.264099407057, 0.0163))
if not mesh_graded:
	mesh.x = np.concatenate((mesh.x, arangeWithEndpoint(-2.27891203814,-0.264099407057,0.0163)))
mesh.y = np.delete(mesh.y, np.argwhere((mesh.y >= -0.77521525231) & (mesh.y <= -0.103645205039)))
mesh_regions['y'].append((-0.77521525231, -0.103645205039, 0.0163))
if not mesh_graded:
	mesh.y = np.concatenate((mesh.y, arangeWithEndpoint(-0.77521525231,-0.103645205039,0.0163)))
mesh.z = np.delete(mesh.z, np.argwhere((mesh.z >= 0.489) & (mesh.z <= 0.890888107166)))
mesh_regions['z'].append((0.489, 0.890888107166, 0.0163))
if not mesh_graded:
	mesh.z = np.concatenate((mesh.z, arangeWithEndpoint(0.489,0.890888107166,0.0163)))

## GRID - Wire_VCC_13 - Wire_VCC_13 (Fixed Distance)
mesh.x = np.delete(mesh.x, np.argwhere((mesh.x >= -1.28518453756) & (mesh.x <= -0.132815408836)))
mesh_regions['x'].append((-1.28518453756, -0.132815408836, 0.0163))
if not mesh_graded:
	mesh.x = np.concatenate((mesh.x, arangeWithEndpoint(-1.28518453756,-0.132815408836,0.0163)))
mesh.y = np.delete(mesh.y, np.argwhere((mesh.y >= -2.27532488609) & (mesh.y <= -0.358553914427)))
mesh_regions['y'].append((-2.27532488609, -0.358553914427, 0.0163))
if not mesh_graded:
	mesh.y = np.concatenate((mesh.y, arangeWithEndpoint(-2.27532488609,-0.358553914427,0.0163)))
mesh.z = np.delete(mesh.z, np.argwhere((mesh.z >= 0.489) & (mesh.z <= 0.891128331412)))
mesh_regions['z'].append((0.489, 0.891128331412, 0.0163))
if not mesh_graded:
	mesh.z = np.concatenate((mesh.z, arangeWithEndpoint(0.489,0.891128331412,0.0163)))

## GRID - Wire_GNDHEAT_19 - Wire_GNDHEAT_19 (Fixed Distance)
mesh.x = np.delete(mesh.x, np.argwhere((mesh.x >= 0.107760097082) & (mesh.x <= 1.78879622975)))
mesh_regions['x'].append((0.107760097082, 1.78879622975, 0.0164))
if not mesh_graded:
	mesh.x = np.concatenate((mesh.x, arangeWithEndpoint(0.107760097082,1.78879622975,0.0164)))
mesh.y = np.delete(mesh.y, np.argwhere((mesh.y >= -2.27407419816) & (mesh.y <= -0.358437366368)))
mesh_regions['y'].append((-2.27407419816, -0.358437366368, 0.0164))
if not mesh_graded:
	mesh.y = np.concatenate((mesh.y, arangeWithEndpoint(-2.27407419816,-0.358437366368,0.0164)))
mesh.z = np.delete(mesh.z, np.argwhere((mesh.z >= 0.489) & (mesh.z <= 0.891155644939)))
mesh_regions['z'].append((0.489, 0.891155644939, 0.0164))
if not mesh_graded:
	mesh.z = np.concatenate((mesh.z, arangeWithEndpoint(0.489,0.891155644939,0.0164)))

## GRID - Wire_IHEAT_17 - Wire_IHEAT_17 (Fixed Distance)
mesh.x = np.delete(mesh.x, np.argwhere((mesh.x >= 0.0118453834387) & (mesh.x <= 0.780995000848)))
mesh_regions['x'].append((0.0118453834387, 0.780995000848, 0.0163))
if not mesh_graded:
	mesh.x = np.concatenate((mesh.x, arangeWithEndpoint(0.0118453834387,0.780995000848,0.0163)))
mesh.y = np.delete(mesh.y, np.argwhere((mesh.y >= -2.27523234867) & (mesh.y <= -0.359525547249)))
mesh_regions['y'].append((-2.27523234867, -0.359525547249, 0.0163))
if not mesh_graded:
	mesh.y = np.concatenate((mesh.y, arangeWithEndpoint(-2.27523234867,-0.359525547249,0.0163)))
mesh.z = np.delete(mesh.z, np.argwhere((mesh.z >= 0.489) & (mesh.z <= 0.890803812592)))
mesh_regions['z'].append((0.489, 0.890803812592, 0.0163))
if not mesh_graded:
	mesh.z = np.concatenate((mesh.z, arangeWithEndpoint(0.489,0.890803812592,0.0163)))

## GRID - Wire_RFIN_6 - Wire_IDC_15 (Fixed Distance)
mesh.x = np.delete(mesh.x, np.argwhere((mesh.x >= -0.272428514817) & (mesh.x <= -0.0404768365202)))
mesh_regions['x'].append((-0.272428514817, -0.0404768365202, 0.0163))
if not mesh_graded:
	mesh.x = np.concatenate((mesh.x, arangeWithEndpoint(-0.272428514817,-0.0404768365202,0.0163)))
mesh.y = np.delete(mesh.y, np.argwhere((mesh.y >= -2.27271107177) & (mesh.y <= -0.362827783779)))
mesh_regions['y'].append((-2.27271107177, -0.362827783779, 0.0163))
if not mesh_graded:
	mesh.y = np.concatenate((mesh.y, arangeWithEndpoint(-2.27271107177,-0.362827783779,0.0163)))
mesh.z = np.delete(mesh.z, np.argwhere((mesh.z >= 0.489) & (mesh.z <= 0.89115367474)))
mesh_regions['z'].append((0.489, 0.89115367474, 0.0163))
if not mesh_graded:
	mesh.z = np.concatenate((mesh.z, arangeWithEndpoint(0.489,0.89115367474,0.0163)))

## GRID - Wire_RFIN_6 - Wire_RFIN_6 (Fixed Distance)
mesh.x = np.delete(mesh.x, np.argwhere((mesh.x >= -2.27684396872) & (mesh.x <= -0.266674727301)))
mesh_regions['x'].append((-2.27684396872, -0.266674727301, 0.0163))
if not mesh_graded:
	mesh.x = np.concatenate((mesh.x, arangeWithEndpoint(-2.27684396872,-0.266674727301,0.0163)))
mesh.y = np.delete(mesh.y, np.argwhere((mesh.y >= -0.268793332523) & (mesh.y <= -0.00770054103425)))
mesh_regions['y'].append((-0.268793332523, -0.00770054103425, 0.0163))
if not mesh_graded:
	mesh.y = np.concatenate((mesh.y, arangeWithEndpoint(-0.268793332523,-0.00770054103425,0.0163)))
mesh.z = np.delete(mesh.z, np.argwhere((mesh.z >= 0.489) & (mesh.z <= 0.891153832058)))
mesh_regions['z'].append((0.489, 0.891153832058, 0.0163))
if not mesh_graded:
	mesh.z = np.concatenate((mesh.z, arangeWithEndpoint(0.489,0.891153832058,0.0163)))

## GRID - Wire_RFOUT_25 - Wire_GND3_26 (Fixed Distance)
mesh.x = np.delete(mesh.x, np.argwhere((mesh.x >= 0.239321459477) & (mesh.x <= 2.27668185579)))
mesh_regions['x'].append((0.239321459477, 2.27668185579, 0.0163))
if not mesh_graded:
	mesh.x = np.concatenate((mesh.x, arangeWithEndpoint(0.239321459477,2.27668185579,0.0163)))
mesh.y = np.delete(mesh.y, np.argwhere((mesh.y >= 0.0348916017386) & (mesh.y <= 0.276403502175)))
mesh_regions['y'].append((0.0348916017386, 0.276403502175, 0.0163))
if not mesh_graded:
	mesh.y = np.concatenate((mesh.y, arangeWithEndpoint(0.0348916017386,0.276403502175,0.0163)))
mesh.z = np.delete(mesh.z, np.argwhere((mesh.z >= 0.489) & (mesh.z <= 0.891146316138)))
mesh_regions['z'].append((0.489, 0.891146316138, 0.0163))
if not mesh_graded:
	mesh.z = np.concatenate((mesh.z, arangeWithEndpoint(0.489,0.891146316138,0.0163)))

## GRID - Wire_RFOUT_25 - Wire_RFOUT_25 (Fixed Distance)
mesh.x = np.delete(mesh.x, np.argwhere((mesh.x >= 0.239293379433) & (mesh.x <= 2.27671590902)))
mesh_regions['x'].append((0.239293379433, 2.27671590902, 0.0163))
if not mesh_graded:
	mesh.x = np.concatenate((mesh.x, arangeWithEndpoint(0.239293379433,2.27671590902,0.0163)))
mesh.y = np.delete(mesh.y, np.argwhere((mesh.y >= -0.268431894785) & (mesh.y <= -0.0227943499299)))
mesh_regions['y'].append((-0.268431894785, -0.0227943499299, 0.0163))
if not mesh_graded:
	mesh.y = np.concatenate((mesh.y, arangeWithEndpoint(-0.268431894785,-0.0227943499299,0.0163)))
mesh.z = np.delete(mesh.z, np.argwhere((mesh.z >= 0.489) & (mesh.z <= 0.891146824276)))
mesh_regions['z'].append((0.489, 0.891146824276, 0.0163))
if not mesh_graded:
	mesh.z = np.concatenate((mesh.z, arangeWithEndpoint(0.489,0.891146824276,0.0163)))

## GRID - Wire_GND14_5 - Wire_VTMP_36 (Fixed Distance)
mesh.x = np.delete(mesh.x, np.argwhere((mesh.x >= -0.272071872835) & (mesh.x <= -0.0520826897744)))
mesh_regions['x'].append((-0.272071872835, -0.0520826897744, 0.0163))
if not mesh_graded:
	mesh.x = np.concatenate((mesh.x, arangeWithEndpoint(-0.272071872835,-0.0520826897744,0.0163)))
mesh.y = np.delete(mesh.y, np.argwhere((mesh.y >= 0.270193382512) & (mesh.y <= 2.28054320148)))
mesh_regions['y'].append((0.270193382512, 2.28054320148, 0.0163))
if not mesh_graded:
	mesh.y = np.concatenate((mesh.y, arangeWithEndpoint(0.270193382512,2.28054320148,0.0163)))
mesh.z = np.delete(mesh.z, np.argwhere((mesh.z >= 0.489) & (mesh.z <= 0.891150876581)))
mesh_regions['z'].append((0.489, 0.891150876581, 0.0163))
if not mesh_graded:
	mesh.z = np.concatenate((mesh.z, arangeWithEndpoint(0.489,0.891150876581,0.0163)))

## GRID - Wire_GND14_5 - Wire_GND14_5 (Fixed Distance)
mesh.x = np.delete(mesh.x, np.argwhere((mesh.x >= -2.27659283308) & (mesh.x <= -0.266954311237)))
mesh_regions['x'].append((-2.27659283308, -0.266954311237, 0.0163))
if not mesh_graded:
	mesh.x = np.concatenate((mesh.x, arangeWithEndpoint(-2.27659283308,-0.266954311237,0.0163)))
mesh.y = np.delete(mesh.y, np.argwhere((mesh.y >= 0.0499185557236) & (mesh.y <= 0.27615998876)))
mesh_regions['y'].append((0.0499185557236, 0.27615998876, 0.0163))
if not mesh_graded:
	mesh.y = np.concatenate((mesh.y, arangeWithEndpoint(0.0499185557236,0.27615998876,0.0163)))
mesh.z = np.delete(mesh.z, np.argwhere((mesh.z >= 0.489) & (mesh.z <= 0.891151646565)))
mesh_regions['z'].append((0.489, 0.891151646565, 0.0163))
if not mesh_graded:
	mesh.z = np.concatenate((mesh.z, arangeWithEndpoint(0.489,0.891151646565,0.0163)))

## GRID - Wire_GND4_24 - Wire_GND4_24 (Fixed Distance)
mesh.x = np.delete(mesh.x, np.argwhere((mesh.x >= 0.236685753354) & (mesh.x <= 2.27884106308)))
mesh_regions['x'].append((0.236685753354, 2.27884106308, 0.0163))
if not mesh_graded:
	mesh.x = np.concatenate((mesh.x, arangeWithEndpoint(0.236685753354,2.27884106308,0.0163)))
mesh.y = np.delete(mesh.y, np.argwhere((mesh.y >= -0.774878638149) & (mesh.y <= -0.118761163377)))
mesh_regions['y'].append((-0.774878638149, -0.118761163377, 0.0163))
if not mesh_graded:
	mesh.y = np.concatenate((mesh.y, arangeWithEndpoint(-0.774878638149,-0.118761163377,0.0163)))
mesh.z = np.delete(mesh.z, np.argwhere((mesh.z >= 0.489) & (mesh.z <= 0.890960854988)))
mesh_regions['z'].append((0.489, 0.890960854988, 0.0163))
if not mesh_graded:
	mesh.z = np.concatenate((mesh.z, arangeWithEndpoint(0.489,0.890960854988,0.0163)))

## GRID - ALL_LNA_TopMetal2 - ALL_LNA_TopMetal2 (Fixed Distance)
mesh.x = np.delete(mesh.x, np.argwhere((mesh.x >= -0.333605) & (mesh.x <= 0.31397)))
mesh_regions['x'].append((-0.333605, 0.31397, 0.216))
if not mesh_graded:
	mesh.x = np.concatenate((mesh.x, arangeWithEndpoint(-0.333605,0.31397,0.216)))
mesh.y = np.delete(mesh.y, np.argwhere((mesh.y >= -0.4296) & (mesh.y <= 0.35469)))
mesh_regions['y'].append((-0.4296, 0.35469, 0.216))
if not mesh_graded:
	mesh.y = np.concatenate((mesh.y, arangeWithEndpoint(-0.4296,0.35469,0.216)))
mesh.z = np.delete(mesh.z, np.argwhere((mesh.z >= 0.7089803) & (mesh.z <= 0.7119803)))
mesh_regions['z'].append((0.7089803, 0.7119803, 0.005))
if not mesh_graded:
	mesh.z = np.concatenate((mesh.z, arangeWithEndpoint(0.7089803,0.7119803,0.005)))

## GRID - Die_attach_ALL_LNA - Die_attach_ALL_LNA (Fixed Distance)
mesh.x = np.delete(mesh.x, np.argwhere((mesh.x >= -0.39) & (mesh.x <= 0.39)))
mesh_regions['x'].append((-0.39, 0.39, 0.26))
if not mesh_graded:
	mesh.x = np.concatenate((mesh.x, arangeWithEndpoint(-0.39,0.39,0.26)))
mesh.y = np.delete(mesh.y, np.argwhere((mesh.y >= -0.486) & (mesh.y <= 0.494)))
mesh_regions['y'].append((-0.486, 0.494, 0.26))
if not mesh_graded:
	mesh.y = np.concatenate((mesh.y, arangeWithEndpoint(-0.486,0.494,0.26)))
mesh.z = np.delete(mesh.z, np.argwhere((mesh.z >= 0.489) & (mesh.z <= 0.514)))
mesh_regions['z'].append((0.489, 0.514, 0.025))
if not mesh_graded:
	mesh.z = np.concatenate((mesh.z, arangeWithEndpoint(0.489,0.514,0.025)))

## GRID - QFN_40_6x6_leadframe_1 - QFN_40_6x6_leadframe_41 (Fixed Distance)
mesh.x = np.delete(mesh.x, np.argwhere((mesh.x >= 2.125) & (mesh.x <= 2.375)))
mesh_regions['x'].append((2.125, 2.375, 0.0833))
if not mesh_graded:
	mesh.x = np.concatenate((mesh.x, arangeWithEndpoint(2.125,2.375,0.0833)))
mesh.y = np.delete(mesh.y, np.argwhere((mesh.y >= 2.004) & (mesh.y <= 3.004)))
mesh_regions['y'].append((2.004, 3.004, 0.0833))
if not mesh_graded:
	mesh.y = np.concatenate((mesh.y, arangeWithEndpoint(2.004,3.004,0.0833)))
mesh.z = np.delete(mesh.z, np.argwhere((mesh.z >= 0.289) & (mesh.z <= 0.489)))
mesh_regions['z'].append((0.289, 0.489, 0.2))
if not mesh_graded:
	mesh.z = np.concatenate((mesh.z, arangeWithEndpoint(0.289,0.489,0.2)))

## GRID - QFN_40_6x6_leadframe_1 - QFN_40_6x6_leadframe_40 (Fixed Distance)
mesh.x = np.delete(mesh.x, np.argwhere((mesh.x >= 1.625) & (mesh.x <= 1.875)))
mesh_regions['x'].append((1.625, 1.875, 0.0833))
if not mesh_graded:
	mesh.x = np.concatenate((mesh.x, arangeWithEndpoint(1.625,1.875,0.0833)))
mesh.y = np.delete(mesh.y, np.argwhere((mesh.y >= 2.004) & (mesh.y <= 3.004)))
mesh_regions['y'].append((2.004, 3.004, 0.0833))
if not mesh_graded:
	mesh.y = np.concatenate((mesh.y, arangeWithEndpoint(2.004,3.004,0.0833)))
mesh.z = np.delete(mesh.z, np.argwhere((mesh.z >= 0.289) & (mesh.z <= 0.489)))
mesh_regions['z'].append((0.289, 0.489, 0.2))
if not mesh_graded:
	mesh.z = np.concatenate((mesh.z, arangeWithEndpoint(0.289,0.489,0.2)))

## GRID - QFN_40_6x6_leadframe_1 - QFN_40_6x6_leadframe_39 (Fixed Distance)
mesh.x = np.delete(mesh.x, np.argwhere((mesh.x >= 1.125) & (mesh.x <= 1.375)))
mesh_regions['x'].append((1.125, 1.375, 0.0833))
if not mesh_graded:
	mesh.x = np.concatenate((mesh.x, arangeWithEndpoint(1.125,1.375,0.0833)))
mesh.y = np.delete(mesh.y, np.argwhere((mesh.y >= 2.004) & (mesh.y <= 3.004)))
mesh_regions['y'].append((2.004, 3.004, 0.0833))
if not mesh_graded:
	mesh.y = np.concatenate((mesh.y, arangeWithEndpoint(2.004,3.004,0.0833)))
mesh.z = np.delete(mesh.z, np.argwhere((mesh.z >= 0.289) & (mesh.z <= 0.489)))
mesh_regions['z'].append((0.289, 0.489, 0.2))
if not mesh_graded:
	mesh.z = np.concatenate((mesh.z, arangeWithEndpoint(0.289,0.489,0.2)))

## GRID - QFN_40_6x6_leadframe_1 - QFN_40_6x6_leadframe_38 (Fixed Distance)
mesh.x = np.delete(mesh.x, np.argwhere((mesh.x >= 0.625) & (mesh.x <= 0.875)))
mesh_regions['x'].append((0.625, 0.875, 0.0833))
if not mesh_graded:
	mesh.x = np.concatenate((mesh.x, arangeWithEndpoint(0.625,0.875,0.0833)))
mesh.y = np.delete(mesh.y, np.argwhere((mesh.y >= 2.004) & (mesh.y <= 3.004)))
mesh_regions['y'].append((2.004, 3.004, 0.0833))
if not mesh_graded:
	mesh.y = np.concatenate((mesh.y, arangeWithEndpoint(2.004,3.004,0.0833)))
mesh.z = np.delete(mesh.z, np.argwhere((mesh.z >= 0.289) & (mesh.z <= 0.489)))
mesh_regions['z'].append((0.289, 0.489, 0.2))
if not mesh_graded:
	mesh.z = np.concatenate((mesh.z, arangeWithEndpoint(0.289,0.489,0.2)))

## GRID - QFN_40_6x6_leadframe_1 - QFN_40_6x6_leadframe_37 (Fixed Distance)
mesh.x = np.delete(mesh.x, np.argwhere((mesh.x >= 0.125) & (mesh.x <= 0.375)))
mesh_regions['x'].append((0.125, 0.375, 0.0833))
if not mesh_graded:
	mesh.x = np.concatenate((mesh.x, arangeWithEndpoint(0.125,0.375,0.0833)))
mesh.y = np.delete(mesh.y, np.argwhere((mesh.y >= 2.004) & (mesh.y <= 3.004)))
mesh_regions['y'].append((2.004, 3.004, 0.0833))
if not mesh_graded:
	mesh.y = np.concatenate((mesh.y, arangeWithEndpoint(2.004,3.004,0.0833)))
mesh.z = np.delete(mesh.z, np.argwhere((mesh.z >= 0.289) & (mesh.z <= 0.489)))
mesh_regions['z'].append((0.289, 0.489, 0.2))
if not mesh_graded:
	mesh.z = np.concatenate((mesh.z, arangeWithEndpoint(0.289,0.489,0.2)))

## GRID - QFN_40_6x6_leadframe_1 - QFN_40_6x6_leadframe_36 (Fixed Distance)
mesh.x = np.delete(mesh.x, np.argwhere((mesh.x >= -0.375) & (mesh.x <= -0.125)))
mesh_regions['x'].append((-0.375, -0.125, 0.0833))
if not mesh_graded:
	mesh.x = np.concatenate((mesh.x, arangeWithEndpoint(-0.375,-0.125,0.0833)))
mesh.y = np.delete(mesh.y, np.argwhere((mesh.y >= 2.004) & (mesh.y <= 3.004)))
mesh_regions['y'].append((2.004, 3.004, 0.0833))
if not mesh_graded:
	mesh.y = np.concatenate((mesh.y, arangeWithEndpoint(2.004,3.004,0.0833)))
mesh.z = np.delete(mesh.z, np.argwhere((mesh.z >= 0.289) & (mesh.z <= 0.489)))
mesh_regions['z'].append((0.289, 0.489, 0.2))
if not mesh_graded:
	mesh.z = np.concatenate((mesh.z, arangeWithEndpoint(0.289,0.489,0.2)))

## GRID - QFN_40_6x6_leadframe_1 - QFN_40_6x6_leadframe_35 (Fixed Distance)
mesh.x = np.delete(mesh.x, np.argwhere((mesh.x >= -0.875) & (mesh.x <= -0.625)))
mesh_regions['x'].append((-0.875, -0.625, 0.0833))
if not mesh_graded:
	mesh.x = np.concatenate((mesh.x, arangeWithEndpoint(-0.875,-0.625,0.0833)))
mesh.y = np.delete(mesh.y, np.argwhere((mesh.y >= 2.004) & (mesh.y <= 3.004)))
mesh_regions['y'].append((2.004, 3.004, 0.0833))
if not mesh_graded:
	mesh.y = np.concatenate((mesh.y, arangeWithEndpoint(2.004,3.004,0.0833)))
mesh.z = np.delete(mesh.z, np.argwhere((mesh.z >= 0.289) & (mesh.z <= 0.489)))
mesh_regions['z'].append((0.289, 0.489, 0.2))
if not mesh_graded:
	mesh.z = np.concatenate((mesh.z, arangeWithEndpoint(0.289,0.489,0.2)))

## GRID - QFN_40_6x6_leadframe_1 - QFN_40_6x6_leadframe_34 (Fixed Distance)
mesh.x = np.delete(mesh.x, np.argwhere((mesh.x >= -1.375) & (mesh.x <= -1.125)))
mesh_regions['x'].append((-1.375, -1.125, 0.0833))
if not mesh_graded:
	mesh.x = np.concatenate((mesh.x, arangeWithEndpoint(-1.375,-1.125,0.0833)))
mesh.y = np.delete(mesh.y, np.argwhere((mesh.y >= 2.004) & (mesh.y <= 3.004)))
mesh_regions['y'].append((2.004, 3.004, 0.0833))
if not mesh_graded:
	mesh.y = np.concatenate((mesh.y, arangeWithEndpoint(2.004,3.004,0.0833)))
mesh.z = np.delete(mesh.z, np.argwhere((mesh.z >= 0.289) & (mesh.z <= 0.489)))
mesh_regions['z'].append((0.289, 0.489, 0.2))
if not mesh_graded:
	mesh.z = np.concatenate((mesh.z, arangeWithEndpoint(0.289,0.489,0.2)))

## GRID - QFN_40_6x6_leadframe_1 - QFN_40_6x6_leadframe_33 (Fixed Distance)
mesh.x = np.delete(mesh.x, np.argwhere((mesh.x >= -1.875) & (mesh.x <= -1.625)))
mesh_regions['x'].append((-1.875, -1.625, 0.0833))
if not mesh_graded:
	mesh.x = np.concatenate((mesh.x, arangeWithEndpoint(-1.875,-1.625,0.0833)))
mesh.y = np.delete(mesh.y, np.argwhere((mesh.y >= 2.004) & (mesh.y <= 3.004)))
mesh_regions['y'].append((2.004, 3.004, 0.0833))
if not mesh_graded:
	mesh.y = np.concatenate((mesh.y, arangeWithEndpoint(2.004,3.004,0.0833)))
mesh.z = np.delete(mesh.z, np.argwhere((mesh.z >= 0.289) & (mesh.z <= 0.489)))
mesh_regions['z'].append((0.289, 0.489, 0.2))
if not mesh_graded:
	mesh.z = np.concatenate((mesh.z, arangeWithEndpoint(0.289,0.489,0.2)))

## GRID - QFN_40_6x6_leadframe_1 - QFN_40_6x6_leadframe_32 (Fixed Distance)
mesh.x = np.delete(mesh.x, np.argwhere((mesh.x >= -2.375) & (mesh.x <= -2.125)))
mesh_regions['x'].append((-2.375, -2.125, 0.0833))
if not mesh_graded:
	mesh.x = np.concatenate((mesh.x, arangeWithEndpoint(-2.375,-2.125,0.0833)))
mesh.y = np.delete(mesh.y, np.argwhere((mesh.y >= 2.004) & (mesh.y <= 3.004)))
mesh_regions['y'].append((2.004, 3.004, 0.0833))
if not mesh_graded:
	mesh.y = np.concatenate((mesh.y, arangeWithEndpoint(2.004,3.004,0.0833)))
mesh.z = np.delete(mesh.z, np.argwhere((mesh.z >= 0.289) & (mesh.z <= 0.489)))
mesh_regions['z'].append((0.289, 0.489, 0.2))
if not mesh_graded:
	mesh.z = np.concatenate((mesh.z, arangeWithEndpoint(0.289,0.489,0.2)))

## GRID - QFN_40_6x6_leadframe_1 - QFN_40_6x6_leadframe_31 (Fixed Distance)
mesh.x = np.delete(mesh.x, np.argwhere((mesh.x >= 2) & (mesh.x <= 3)))
mesh_regions['x'].append((2, 3, 0.0833))
if not mesh_graded:
	mesh.x = np.concatenate((mesh.x, arangeWithEndpoint(2,3,0.0833)))
mesh.y = np.delete(mesh.y, np.argwhere((mesh.y >= 2.129) & (mesh.y <= 2.379)))
mesh_regions['y'].append((2.129, 2.379, 0.0833))
if not mesh_graded:
	mesh.y = np.concatenate((mesh.y, arangeWithEndpoint(2.129,2.379,0.0833)))
mesh.z = np.delete(mesh.z, np.argwhere((mesh.z >= 0.289) & (mesh.z <= 0.489)))
mesh_regions['z'].append((0.289, 0.489, 0.2))
if not mesh_graded:
	mesh.z = np.concatenate((mesh.z, arangeWithEndpoint(0.289,0.489,0.2)))

## GRID - QFN_40_6x6_leadframe_1 - QFN_40_6x6_leadframe_30 (Fixed Distance)
mesh.x = np.delete(mesh.x, np.argwhere((mesh.x >= 2) & (mesh.x <= 3)))
mesh_regions['x'].append((2, 3, 0.0833))
if not mesh_graded:
	mesh.x = np.concatenate((mesh.x, arangeWithEndpoint(2,3,0.0833)))
mesh.y = np.delete(mesh.y, np.argwhere((mesh.y >= 1.629) & (mesh.y <= 1.879)))
mesh_regions['y'].append((1.629, 1.879, 0.0833))
if not mesh_graded:
	mesh.y = np.concatenate((mesh.y, arangeWithEndpoint(1.629,1.879,0.0833)))
mesh.z = np.delete(mesh.z, np.argwhere((mesh.z >= 0.289) & (mesh.z <= 0.489)))
mesh_regions['z'].append((0.289, 0.489, 0.2))
if not mesh_graded:
	mesh.z = np.concatenate((mesh.z, arangeWithEndpoint(0.289,0.489,0.2)))

## GRID - QFN_40_6x6_leadframe_1 - QFN_40_6x6_leadframe_29 (Fixed Distance)
mesh.x = np.delete(mesh.x, np.argwhere((mesh.x >= 2) & (mesh.x <= 3)))
mesh_regions['x'].append((2, 3, 0.0833))
if not mesh_graded:
	mesh.x = np.concatenate((mesh.x, arangeWithEndpoint(2,3,0.0833)))
mesh.y = np.delete(mesh.y, np.argwhere((mesh.y >= 1.129) & (mesh.y <= 1.379)))
mesh_regions['y'].append((1.129, 1.379, 0.0833))
if not mesh_graded:
	mesh.y = np.concatenate((mesh.y, arangeWithEndpoint(1.129,1.379,0.0833)))
mesh.z = np.delete(mesh.z, np.argwhere((mesh.z >= 0.289) & (mesh.z <= 0.489)))
mesh_regions['z'].append((0.289, 0.489, 0.2))
if not mesh_graded:
	mesh.z = np.concatenate((mesh.z, arangeWithEndpoint(0.289,0.489,0.2)))

## GRID - QFN_40_6x6_leadframe_1 - QFN_40_6x6_leadframe_28 (Fixed Distance)
mesh.x = np.delete(mesh.x, np.argwhere((mesh.x >= 2) & (mesh.x <= 3)))
mesh_regions['x'].append((2, 3, 0.0833))
if not mesh_graded:
	mesh.x = np.concatenate((mesh.x, arangeWithEndpoint(2,3,0.0833)))
mesh.y = np.delete(mesh.y, np.argwhere((mesh.y >= 0.629) & (mesh.y <= 0.879)))
mesh_regions['y'].append((0.629, 0.879, 0.0833))
if not mesh_graded:
	mesh.y = np.concatenate((mesh.y, arangeWithEndpoint(0.629,0.879,0.0833)))
mesh.z = np.delete(mesh.z, np.argwhere((mesh.z >= 0.289) & (mesh.z <= 0.489)))
mesh_regions['z'].append((0.289, 0.489, 0.2))
if not mesh_graded:
	mesh.z = np.concatenate((mesh.z, arangeWithEndpoint(0.289,0.489,0.2)))

## GRID - QFN_40_6x6_leadframe_1 - QFN_40_6x6_leadframe_27 (Fixed Distance)
mesh.x = np.delete(mesh.x, np.argwhere((mesh.x >= 2) & (mesh.x <= 3)))
mesh_regions['x'].append((2, 3, 0.0833))
if not mesh_graded:
	mesh.x = np.concatenate((mesh.x, arangeWithEndpoint(2,3,0.0833)))
mesh.y = np.delete(mesh.y, np.argwhere((mesh.y >= 0.129) & (mesh.y <= 0.379)))
mesh_regions['y'].append((0.129, 0.379, 0.0833))
if not mesh_graded:
	mesh.y = np.concatenate((mesh.y, arangeWithEndpoint(0.129,0.379,0.0833)))
mesh.z = np.delete(mesh.z, np.argwhere((mesh.z >= 0.289) & (mesh.z <= 0.489)))
mesh_regions['z'].append((0.289, 0.489, 0.2))
if not mesh_graded:
	mesh.z = np.concatenate((mesh.z, arangeWithEndpoint(0.289,0.489,0.2)))

## GRID - QFN_40_6x6_leadframe_1 - QFN_40_6x6_leadframe_26 (Fixed Distance)
mesh.x = np.delete(mesh.x, np.argwhere((mesh.x >= 2) & (mesh.x <= 3)))
mesh_regions['x'].append((2, 3, 0.0833))
if not mesh_graded:
	mesh.x = np.concatenate((mesh.x, arangeWithEndpoint(2,3,0.0833)))
mesh.y = np.delete(mesh.y, np.argwhere((mesh.y >= -0.371) & (mesh.y <= -0.121)))
mesh_regions['y'].append((-0.371, -0.121, 0.0833))
if not mesh_graded:
	mesh.y = np.concatenate((mesh.y, arangeWithEndpoint(-0.371,-0.121,0.0833)))
mesh.z = np.delete(mesh.z, np.argwhere((mesh.z >= 0.289) & (mesh.z <= 0.489)))
mesh_regions['z'].append((0.289, 0.489, 0.2))
if not mesh_graded:
	mesh.z = np.concatenate((mesh.z, arangeWithEndpoint(0.289,0.489,0.2)))

## GRID - QFN_40_6x6_leadframe_1 - QFN_40_6x6_leadframe_25 (Fixed Distance)
mesh.x = np.delete(mesh.x, np.argwhere((mesh.x >= 2) & (mesh.x <= 3)))
mesh_regions['x'].append((2, 3, 0.0833))
if not mesh_graded:
	mesh.x = np.concatenate((mesh.x, arangeWithEndpoint(2,3,0.0833)))
mesh.y = np.delete(mesh.y, np.argwhere((mesh.y >= -0.871) & (mesh.y <= -0.621)))
mesh_regions['y'].append((-0.871, -0.621, 0.0833))
if not mesh_graded:
	mesh.y = np.concatenate((mesh.y, arangeWithEndpoint(-0.871,-0.621,0.0833)))
mesh.z = np.delete(mesh.z, np.argwhere((mesh.z >= 0.289) & (mesh.z <= 0.489)))
mesh_regions['z'].append((0.289, 0.489, 0.2))
if not mesh_graded:
	mesh.z = np.concatenate((mesh.z, arangeWithEndpoint(0.289,0.489,0.2)))

## GRID - QFN_40_6x6_leadframe_1 - QFN_40_6x6_leadframe_24 (Fixed Distance)
mesh.x = np.delete(mesh.x, np.argwhere((mesh.x >= 2) & (mesh.x <= 3)))
mesh_regions['x'].append((2, 3, 0.0833))
if not mesh_graded:
	mesh.x = np.concatenate((mesh.x, arangeWithEndpoint(2,3,0.0833)))
mesh.y = np.delete(mesh.y, np.argwhere((mesh.y >= -1.371) & (mesh.y <= -1.121)))
mesh_regions['y'].append((-1.371, -1.121, 0.0833))
if not mesh_graded:
	mesh.y = np.concatenate((mesh.y, arangeWithEndpoint(-1.371,-1.121,0.0833)))
mesh.z = np.delete(mesh.z, np.argwhere((mesh.z >= 0.289) & (mesh.z <= 0.489)))
mesh_regions['z'].append((0.289, 0.489, 0.2))
if not mesh_graded:
	mesh.z = np.concatenate((mesh.z, arangeWithEndpoint(0.289,0.489,0.2)))

## GRID - QFN_40_6x6_leadframe_1 - QFN_40_6x6_leadframe_23 (Fixed Distance)
mesh.x = np.delete(mesh.x, np.argwhere((mesh.x >= 2) & (mesh.x <= 3)))
mesh_regions['x'].append((2, 3, 0.0833))
if not mesh_graded:
	mesh.x = np.concatenate((mesh.x, arangeWithEndpoint(2,3,0.0833)))
mesh.y = np.delete(mesh.y, np.argwhere((mesh.y >= -1.871) & (mesh.y <= -1.621)))
mesh_regions['y'].append((-1.871, -1.621, 0.0833))
if not mesh_graded:
	mesh.y = np.concatenate((mesh.y, arangeWithEndpoint(-1.871,-1.621,0.0833)))
mesh.z = np.delete(mesh.z, np.argwhere((mesh.z >= 0.289) & (mesh.z <= 0.489)))
mesh_regions['z'].append((0.289, 0.489, 0.2))
if not mesh_graded:
	mesh.z = np.concatenate((mesh.z, arangeWithEndpoint(0.289,0.489,0.2)))

## GRID - QFN_40_6x6_leadframe_1 - QFN_40_6x6_leadframe_22 (Fixed Distance)
mesh.x = np.delete(mesh.x, np.argwhere((mesh.x >= 2) & (mesh.x <= 3)))
mesh_regions['x'].append((2, 3, 0.0833))
if not mesh_graded:
	mesh.x = np.concatenate((mesh.x, arangeWithEndpoint(2,3,0.0833)))
mesh.y = np.delete(mesh.y, np.argwhere((mesh.y >= -2.371) & (mesh.y <= -2.121)))
mesh_regions['y'].append((-2.371, -2.121, 0.0833))
if not mesh_graded:
	mesh.y = np.concatenate((mesh.y, arangeWithEndpoint(-2.371,-2.121,0.0833)))
mesh.z = np.delete(mesh.z, np.argwhere((mesh.z >= 0.289) & (mesh.z <= 0.489)))
mesh_regions['z'].append((0.289, 0.489, 0.2))
if not mesh_graded:
	mesh.z = np.concatenate((mesh.z, arangeWithEndpoint(0.289,0.489,0.2)))

## GRID - QFN_40_6x6_leadframe_1 - QFN_40_6x6_leadframe_21 (Fixed Distance)
mesh.x = np.delete(mesh.x, np.argwhere((mesh.x >= 2.125) & (mesh.x <= 2.375)))
mesh_regions['x'].append((2.125, 2.375, 0.0833))
if not mesh_graded:
	mesh.x = np.concatenate((mesh.x, arangeWithEndpoint(2.125,2.375,0.0833)))
mesh.y = np.delete(mesh.y, np.argwhere((mesh.y >= -2.996) & (mesh.y <= -1.996)))
mesh_regions['y'].append((-2.996, -1.996, 0.0833))
if not mesh_graded:
	mesh.y = np.concatenate((mesh.y, arangeWithEndpoint(-2.996,-1.996,0.0833)))
mesh.z = np.delete(mesh.z, np.argwhere((mesh.z >= 0.289) & (mesh.z <= 0.489)))
mesh_regions['z'].append((0.289, 0.489, 0.2))
if not mesh_graded:
	mesh.z = np.concatenate((mesh.z, arangeWithEndpoint(0.289,0.489,0.2)))

## GRID - QFN_40_6x6_leadframe_1 - QFN_40_6x6_leadframe_20 (Fixed Distance)
mesh.x = np.delete(mesh.x, np.argwhere((mesh.x >= 1.625) & (mesh.x <= 1.875)))
mesh_regions['x'].append((1.625, 1.875, 0.0833))
if not mesh_graded:
	mesh.x = np.concatenate((mesh.x, arangeWithEndpoint(1.625,1.875,0.0833)))
mesh.y = np.delete(mesh.y, np.argwhere((mesh.y >= -2.996) & (mesh.y <= -1.996)))
mesh_regions['y'].append((-2.996, -1.996, 0.0833))
if not mesh_graded:
	mesh.y = np.concatenate((mesh.y, arangeWithEndpoint(-2.996,-1.996,0.0833)))
mesh.z = np.delete(mesh.z, np.argwhere((mesh.z >= 0.289) & (mesh.z <= 0.489)))
mesh_regions['z'].append((0.289, 0.489, 0.2))
if not mesh_graded:
	mesh.z = np.concatenate((mesh.z, arangeWithEndpoint(0.289,0.489,0.2)))

## GRID - QFN_40_6x6_leadframe_1 - QFN_40_6x6_leadframe_19 (Fixed Distance)
mesh.x = np.delete(mesh.x, np.argwhere((mesh.x >= 1.125) & (mesh.x <= 1.375)))
mesh_regions['x'].append((1.125, 1.375, 0.0833))
if not mesh_graded:
	mesh.x = np.concatenate((mesh.x, arangeWithEndpoint(1.125,1.375,0.0833)))
mesh.y = np.delete(mesh.y, np.argwhere((mesh.y >= -2.996) & (mesh.y <= -1.996)))
mesh_regions['y'].append((-2.996, -1.996, 0.0833))
if not mesh_graded:
	mesh.y = np.concatenate((mesh.y, arangeWithEndpoint(-2.996,-1.996,0.0833)))
mesh.z = np.delete(mesh.z, np.argwhere((mesh.z >= 0.289) & (mesh.z <= 0.489)))
mesh_regions['z'].append((0.289, 0.489, 0.2))
if not mesh_graded:
	mesh.z = np.concatenate((mesh.z, arangeWithEndpoint(0.289,0.489,0.2)))

## GRID - QFN_40_6x6_leadframe_1 - QFN_40_6x6_leadframe_18 (Fixed Distance)
mesh.x = np.delete(mesh.x, np.argwhere((mesh.x >= 0.625) & (mesh.x <= 0.875)))
mesh_regions['x'].append((0.625, 0.875, 0.0833))
if not mesh_graded:
	mesh.x = np.concatenate((mesh.x, arangeWithEndpoint(0.625,0.875,0.0833)))
mesh.y = np.delete(mesh.y, np.argwhere((mesh.y >= -2.996) & (mesh.y <= -1.996)))
mesh_regions['y'].append((-2.996, -1.996, 0.0833))
if not mesh_graded:
	mesh.y = np.concatenate((mesh.y, arangeWithEndpoint(-2.996,-1.996,0.0833)))
mesh.z = np.delete(mesh.z, np.argwhere((mesh.z >= 0.289) & (mesh.z <= 0.489)))
mesh_regions['z'].append((0.289, 0.489, 0.2))
if not mesh_graded:
	mesh.z = np.concatenate((mesh.z, arangeWithEndpoint(0.289,0.489,0.2)))

## GRID - QFN_40_6x6_leadframe_1 - QFN_40_6x6_leadframe_17 (Fixed Distance)
mesh.x = np.delete(mesh.x, np.argwhere((mesh.x >= 0.125) & (mesh.x <= 0.375)))
mesh_regions['x'].append((0.125, 0.375, 0.0833))
if not mesh_graded:
	mesh.x = np.concatenate((mesh.x, arangeWithEndpoint(0.125,0.375,0.0833)))
mesh.y = np.delete(mesh.y, np.argwhere((mesh.y >= -2.996) & (mesh.y <= -1.996)))
mesh_regions['y'].append((-2.996, -1.996, 0.0833))
if not mesh_graded:
	mesh.y = np.concatenate((mesh.y, arangeWithEndpoint(-2.996,-1.996,0.0833)))
mesh.z = np.delete(mesh.z, np.argwhere((mesh.z >= 0.289) & (mesh.z <= 0.489)))
mesh_regions['z'].append((0.289, 0.489, 0.2))
if not mesh_graded:
	mesh.z = np.concatenate((mesh.z, arangeWithEndpoint(0.289,0.489,0.2)))

## GRID - QFN_40_6x6_leadframe_1 - QFN_40_6x6_leadframe_16 (Fixed Distance)
mesh.x = np.delete(mesh.x, np.argwhere((mesh.x >= -0.375) & (mesh.x <= -0.125)))
mesh_regions['x'].append((-0.375, -0.125, 0.0833))
if not mesh_graded:
	mesh.x = np.concatenate((mesh.x, arangeWithEndpoint(-0.375,-0.125,0.0833)))
mesh.y = np.delete(mesh.y, np.argwhere((mesh.y >= -2.996) & (mesh.y <= -1.996)))
mesh_regions['y'].append((-2.996, -1.996, 0.0833))
if not mesh_graded:
	mesh.y = np.concatenate((mesh.y, arangeWithEndpoint(-2.996,-1.996,0.0833)))
mesh.z = np.delete(mesh.z, np.argwhere((mesh.z >= 0.289) & (mesh.z <= 0.489)))
mesh_regions['z'].append((0.289, 0.489, 0.2))
if not mesh_graded:
	mesh.z = np.concatenate((mesh.z, arangeWithEndpoint(0.289,0.489,0.2)))

## GRID - QFN_40_6x6_leadframe_1 - QFN_40_6x6_leadframe_15 (Fixed Distance)
mesh.x = np.delete(mesh.x, np.argwhere((mesh.x >= -0.875) & (mesh.x <= -0.625)))
mesh_regions['x'].append((-0.875, -0.625, 0.0833))
if not mesh_graded:
	mesh.x = np.concatenate((mesh.x, arangeWithEndpoint(-0.875,-0.625,0.0833)))
mesh.y = np.delete(mesh.y, np.argwhere((mesh.y >= -2.996) & (mesh.y <= -1.996)))
mesh_regions['y'].append((-2.996, -1.996, 0.0833))
if not mesh_graded:
	mesh.y = np.concatenate((mesh.y, arangeWithEndpoint(-2.996,-1.996,0.0833)))
mesh.z = np.delete(mesh.z, np.argwhere((mesh.z >= 0.289) & (mesh.z <= 0.489)))
mesh_regions['z'].append((0.289, 0.489, 0.2))
if not mesh_graded:
	mesh.z = np.concatenate((mesh.z, arangeWithEndpoint(0.289,0.489,0.2)))

## GRID - QFN_40_6x6_leadframe_1 - QFN_40_6x6_leadframe_14 (Fixed Distance)
mesh.x = np.delete(mesh.x, np.argwhere((mesh.x >= -1.375) & (mesh.x <= -1.125)))
mesh_regions['x'].append((-1.375, -1.125, 0.0833))
if not mesh_graded:
	mesh.x = np.concatenate((mesh.x, arangeWithEndpoint(-1.375,-1.125,0.0833)))
mesh.y = np.delete(mesh.y, np.argwhere((mesh.y >= -2.996) & (mesh.y <= -1.996)))
mesh_regions['y'].append((-2.996, -1.996, 0.0833))
if not mesh_graded:
	mesh.y = np.concatenate((mesh.y, arangeWithEndpoint(-2.996,-1.996,0.0833)))
mesh.z = np.delete(mesh.z, np.argwhere((mesh.z >= 0.289) & (mesh.z <= 0.489)))
mesh_regions['z'].append((0.289, 0.489, 0.2))
if not mesh_graded:
	mesh.z = np.concatenate((mesh.z, arangeWithEndpoint(0.289,0.489,0.2)))

## GRID - QFN_40_6x6_leadframe_1 - QFN_40_6x6_leadframe_13 (Fixed Distance)
mesh.x = np.delete(mesh.x, np.argwhere((mesh.x >= -1.875) & (mesh.x <= -1.625)))
mesh_regions['x'].append((-1.875, -1.625, 0.0833))
if not mesh_graded:
	mesh.x = np.concatenate((mesh.x, arangeWithEndpoint(-1.875,-1.625,0.0833)))
mesh.y = np.delete(mesh.y, np.argwhere((mesh.y >= -2.996) & (mesh.y <= -1.996)))
mesh_regions['y'].append((-2.996, -1.996, 0.0833))
if not mesh_graded:
	mesh.y = np.concatenate((mesh.y, arangeWithEndpoint(-2.996,-1.996,0.0833)))
mesh.z = np.delete(mesh.z, np.argwhere((mesh.z >= 0.289) & (mesh.z <= 0.489)))
mesh_regions['z'].append((0.289, 0.489, 0.2))
if not mesh_graded:
	mesh.z = np.concatenate((mesh.z, arangeWithEndpoint(0.289,0.489,0.2)))

## GRID - QFN_40_6x6_leadframe_1 - QFN_40_6x6_leadframe_12 (Fixed Distance)
mesh.x = np.delete(mesh.x, np.argwhere((mesh.x >= -2.375) & (mesh.x <= -2.125)))
mesh_regions['x'].append((-2.375, -2.125, 0.0833))
if not mesh_graded:
	mesh.x = np.concatenate((mesh.x, arangeWithEndpoint(-2.375,-2.125,0.0833)))
mesh.y = np.delete(mesh.y, np.argwhere((mesh.y >= -2.996) & (mesh.y <= -1.996)))
mesh_regions['y'].append((-2.996, -1.996, 0.0833))
if not mesh_graded:
	mesh.y = np.concatenate((mesh.y, arangeWithEndpoint(-2.996,-1.996,0.0833)))
mesh.z = np.delete(mesh.z, np.argwhere((mesh.z >= 0.289) & (mesh.z <= 0.489)))
mesh_regions['z'].append((0.289, 0.489, 0.2))
if not mesh_graded:
	mesh.z = np.concatenate((mesh.z, arangeWithEndpoint(0.289,0.489,0.2)))

## GRID - QFN_40_6x6_leadframe_1 - QFN_40_6x6_leadframe_11 (Fixed Distance)
mesh.x = np.delete(mesh.x, np.argwhere((mesh.x >= -3) & (mesh.x <= -2)))
mesh_regions['x'].append((-3, -2, 0.0833))
if not mesh_graded:
	mesh.x = np.concatenate((mesh.x, arangeWithEndpoint(-3,-2,0.0833)))
mesh.y = np.delete(mesh.y, np.argwhere((mesh.y >= 2.129) & (mesh.y <= 2.379)))
mesh_regions['y'].append((2.129, 2.379, 0.0833))
if not mesh_graded:
	mesh.y = np.concatenate((mesh.y, arangeWithEndpoint(2.129,2.379,0.0833)))
mesh.z = np.delete(mesh.z, np.argwhere((mesh.z >= 0.289) & (mesh.z <= 0.489)))
mesh_regions['z'].append((0.289, 0.489, 0.2))
if not mesh_graded:
	mesh.z = np.concatenate((mesh.z, arangeWithEndpoint(0.289,0.489,0.2)))

## GRID - QFN_40_6x6_leadframe_1 - QFN_40_6x6_leadframe_10 (Fixed Distance)
mesh.x = np.delete(mesh.x, np.argwhere((mesh.x >= -3) & (mesh.x <= -2)))
mesh_regions['x'].append((-3, -2, 0.0833))
if not mesh_graded:
	mesh.x = np.concatenate((mesh.x, arangeWithEndpoint(-3,-2,0.0833)))
mesh.y = np.delete(mesh.y, np.argwhere((mesh.y >= 1.629) & (mesh.y <= 1.879)))
mesh_regions['y'].append((1.629, 1.879, 0.0833))
if not mesh_graded:
	mesh.y = np.concatenate((mesh.y, arangeWithEndpoint(1.629,1.879,0.0833)))
mesh.z = np.delete(mesh.z, np.argwhere((mesh.z >= 0.289) & (mesh.z <= 0.489)))
mesh_regions['z'].append((0.289, 0.489, 0.2))
if not mesh_graded:
	mesh.z = np.concatenate((mesh.z, arangeWithEndpoint(0.289,0.489,0.2)))

## GRID - QFN_40_6x6_leadframe_1 - QFN_40_6x6_leadframe_9 (Fixed Distance)
mesh.x = np.delete(mesh.x, np.argwhere((mesh.x >= -3) & (mesh.x <= -2)))
mesh_regions['x'].append((-3, -2, 0.0833))
if not mesh_graded:
	mesh.x = np.concatenate((mesh.x, arangeWithEndpoint(-3,-2,0.0833)))
mesh.y = np.delete(mesh.y, np.argwhere((mesh.y >= 1.129) & (mesh.y <= 1.379)))
mesh_regions['y'].append((1.129, 1.379, 0.0833))
if not mesh_graded:
	mesh.y = np.concatenate((mesh.y, arangeWithEndpoint(1.129,1.379,0.0833)))
mesh.z = np.delete(mesh.z, np.argwhere((mesh.z >= 0.289) & (mesh.z <= 0.489)))
mesh_regions['z'].append((0.289, 0.489, 0.2))
if not mesh_graded:
	mesh.z = np.concatenate((mesh.z, arangeWithEndpoint(0.289,0.489,0.2)))

## GRID - QFN_40_6x6_leadframe_1 - QFN_40_6x6_leadframe_8 (Fixed Distance)
mesh.x = np.delete(mesh.x, np.argwhere((mesh.x >= -3) & (mesh.x <= -2)))
mesh_regions['x'].append((-3, -2, 0.0833))
if not mesh_graded:
	mesh.x = np.concatenate((mesh.x, arangeWithEndpoint(-3,-2,0.0833)))
mesh.y = np.delete(mesh.y, np.argwhere((mesh.y >= 0.629) & (mesh.y <= 0.879)))
mesh_regions['y'].append((0.629, 0.879, 0.0833))
if not mesh_graded:
	mesh.y = np.concatenate((mesh.y, arangeWithEndpoint(0.629,0.879,0.0833)))
mesh.z = np.delete(mesh.z, np.argwhere((mesh.z >= 0.289) & (mesh.z <= 0.489)))
mesh_regions['z'].append((0.289, 0.489, 0.2))
if not mesh_graded:
	mesh.z = np.concatenate((mesh.z, arangeWithEndpoint(0.289,0.489,0.2)))

## GRID - QFN_40_6x6_leadframe_1 - QFN_40_6x6_leadframe_7 (Fixed Distance)
mesh.x = np.delete(mesh.x, np.argwhere((mesh.x >= -3) & (mesh.x <= -2)))
mesh_regions['x'].append((-3, -2, 0.0833))
if not mesh_graded:
	mesh.x = np.concatenate((mesh.x, arangeWithEndpoint(-3,-2,0.0833)))
mesh.y = np.delete(mesh.y, np.argwhere((mesh.y >= 0.129) & (mesh.y <= 0.379)))
mesh_regions['y'].append((0.129, 0.379, 0.0833))
if not mesh_graded:
	mesh.y = np.concatenate((mesh.y, arangeWithEndpoint(0.129,0.379,0.0833)))
mesh.z = np.delete(mesh.z, np.argwhere((mesh.z >= 0.289) & (mesh.z <= 0.489)))
mesh_regions['z'].append((0.289, 0.489, 0.2))
if not mesh_graded:
	mesh.z = np.concatenate((mesh.z, arangeWithEndpoint(0.289,0.489,0.2)))

## GRID - QFN_40_6x6_leadframe_1 - QFN_40_6x6_leadframe_6 (Fixed Distance)
mesh.x = np.delete(mesh.x, np.argwhere((mesh.x >= -3) & (mesh.x <= -2)))
mesh_regions['x'].append((-3, -2, 0.0833))
if not mesh_graded:
	mesh.x = np.concatenate((mesh.x, arangeWithEndpoint(-3,-2,0.0833)))
mesh.y = np.delete(mesh.y, np.argwhere((mesh.y >= -0.371) & (mesh.y <= -0.121)))
mesh_regions['y'].append((-0.371, -0.121, 0.0833))
if not mesh_graded:
	mesh.y = np.concatenate((mesh.y, arangeWithEndpoint(-0.371,-0.121,0.0833)))
mesh.z = np.delete(mesh.z, np.argwhere((mesh.z >= 0.289) & (mesh.z <= 0.489)))
mesh_regions['z'].append((0.289, 0.489, 0.2))
if not mesh_graded:
	mesh.z = np.concatenate((mesh.z, arangeWithEndpoint(0.289,0.489,0.2)))

## GRID - QFN_40_6x6_leadframe_1 - QFN_40_6x6_leadframe_5 (Fixed Distance)
mesh.x = np.delete(mesh.x, np.argwhere((mesh.x >= -3) & (mesh.x <= -2)))
mesh_regions['x'].append((-3, -2, 0.0833))
if not mesh_graded:
	mesh.x = np.concatenate((mesh.x, arangeWithEndpoint(-3,-2,0.0833)))
mesh.y = np.delete(mesh.y, np.argwhere((mesh.y >= -0.871) & (mesh.y <= -0.621)))
mesh_regions['y'].append((-0.871, -0.621, 0.0833))
if not mesh_graded:
	mesh.y = np.concatenate((mesh.y, arangeWithEndpoint(-0.871,-0.621,0.0833)))
mesh.z = np.delete(mesh.z, np.argwhere((mesh.z >= 0.289) & (mesh.z <= 0.489)))
mesh_regions['z'].append((0.289, 0.489, 0.2))
if not mesh_graded:
	mesh.z = np.concatenate((mesh.z, arangeWithEndpoint(0.289,0.489,0.2)))

## GRID - QFN_40_6x6_leadframe_1 - QFN_40_6x6_leadframe_4 (Fixed Distance)
mesh.x = np.delete(mesh.x, np.argwhere((mesh.x >= -3) & (mesh.x <= -2)))
mesh_regions['x'].append((-3, -2, 0.0833))
if not mesh_graded:
	mesh.x = np.concatenate((mesh.x, arangeWithEndpoint(-3,-2,0.0833)))
mesh.y = np.delete(mesh.y, np.argwhere((mesh.y >= -1.371) & (mesh.y <= -1.121)))
mesh_regions['y'].append((-1.371, -1.121, 0.0833))
if not mesh_graded:
	mesh.y = np.concatenate((mesh.y, arangeWithEndpoint(-1.371,-1.121,0.0833)))
mesh.z = np.delete(mesh.z, np.argwhere((mesh.z >= 0.289) & (mesh.z <= 0.489)))
mesh_regions['z'].append((0.289, 0.489, 0.2))
if not mesh_graded:
	mesh.z = np.concatenate((mesh.z, arangeWithEndpoint(0.289,0.489,0.2)))

## GRID - QFN_40_6x6_leadframe_1 - QFN_40_6x6_leadframe_3 (Fixed Distance)
mesh.x = np.delete(mesh.x, np.argwhere((mesh.x >= -3) & (mesh.x <= -2)))
mesh_regions['x'].append((-3, -2, 0.0833))
if not mesh_graded:
	mesh.x = np.concatenate((mesh.x, arangeWithEndpoint(-3,-2,0.0833)))
mesh.y = np.delete(mesh.y, np.argwhere((mesh.y >= -1.871) & (mesh.y <= -1.621)))
mesh_regions['y'].append((-1.871, -1.621, 0.0833))
if not mesh_graded:
	mesh.y = np.concatenate((mesh.y, arangeWithEndpoint(-1.871,-1.621,0.0833)))
mesh.z = np.delete(mesh.z, np.argwhere((mesh.z >= 0.289) & (mesh.z <= 0.489)))
mesh_regions['z'].append((0.289, 0.489, 0.2))
if not mesh_graded:
	mesh.z = np.concatenate((mesh.z, arangeWithEndpoint(0.289,0.489,0.2)))

## GRID - QFN_40_6x6_leadframe_1 - QFN_40_6x6_leadframe_2 (Fixed Distance)
mesh.x = np.delete(mesh.x, np.argwhere((mesh.x >= -3) & (mesh.x <= -2)))
mesh_regions['x'].append((-3, -2, 0.0833))
if not mesh_graded:
	mesh.x = np.concatenate((mesh.x, arangeWithEndpoint(-3,-2,0.0833)))
mesh.y = np.delete(mesh.y, np.argwhere((mesh.y >= -2.371) & (mesh.y <= -2.121)))
mesh_regions['y'].append((-2.371, -2.121, 0.0833))
if not mesh_graded:
	mesh.y = np.concatenate((mesh.y, arangeWithEndpoint(-2.371,-2.121,0.0833)))
mesh.z = np.delete(mesh.z, np.argwhere((mesh.z >= 0.289) & (mesh.z <= 0.489)))
mesh_regions['z'].append((0.289, 0.489, 0.2))
if not mesh_graded:
	mesh.z = np.concatenate((mesh.z, arangeWithEndpoint(0.289,0.489,0.2)))

## GRID - QFN_40_6x6_leadframe_1 - QFN_40_6x6_leadframe_1 (Fixed Distance)
mesh.x = np.delete(mesh.x, np.argwhere((mesh.x >= -1.85) & (mesh.x <= 1.85)))
mesh_regions['x'].append((-1.85, 1.85, 0.0833))
if not mesh_graded:
	mesh.x = np.concatenate((mesh.x, arangeWithEndpoint(-1.85,1.85,0.0833)))
mesh.y = np.delete(mesh.y, np.argwhere((mesh.y >= -1.846) & (mesh.y <= 1.854)))
mesh_regions['y'].append((-1.846, 1.854, 0.0833))
if not mesh_graded:
	mesh.y = np.concatenate((mesh.y, arangeWithEndpoint(-1.846,1.854,0.0833)))
mesh.z = np.delete(mesh.z, np.argwhere((mesh.z >= 0.289) & (mesh.z <= 0.489)))
mesh_regions['z'].append((0.289, 0.489, 0.2))
if not mesh_graded:
	mesh.z = np.concatenate((mesh.z, arangeWithEndpoint(0.289,0.489,0.2)))

## GRID - Copper_Top_GND - Copper_Top_NC_20 (Fixed Distance)
mesh.x = np.delete(mesh.x, np.argwhere((mesh.x >= 2.125) & (mesh.x <= 2.375)))
mesh_regions['x'].append((2.125, 2.375, 0.0833))
if not mesh_graded:
	mesh.x = np.concatenate((mesh.x, arangeWithEndpoint(2.125,2.375,0.0833)))
mesh.y = np.delete(mesh.y, np.argwhere((mesh.y >= -3.35) & (mesh.y <= -2.6)))
mesh_regions['y'].append((-3.35, -2.6, 0.0833))
if not mesh_graded:
	mesh.y = np.concatenate((mesh.y, arangeWithEndpoint(-3.35,-2.6,0.0833)))
mesh.z = np.delete(mesh.z, np.argwhere((mesh.z >= 0.254) & (mesh.z <= 0.289)))
mesh_regions['z'].append((0.254, 0.289, 0.035))
if not mesh_graded:
	mesh.z = np.concatenate((mesh.z, arangeWithEndpoint(0.254,0.289,0.035)))

## GRID - Copper_Top_GND - Copper_Top_NC_19 (Fixed Distance)
mesh.x = np.delete(mesh.x, np.argwhere((mesh.x >= 2.6) & (mesh.x <= 3.35)))
mesh_regions['x'].append((2.6, 3.35, 0.0833))
if not mesh_graded:
	mesh.x = np.concatenate((mesh.x, arangeWithEndpoint(2.6,3.35,0.0833)))
mesh.y = np.delete(mesh.y, np.argwhere((mesh.y >= -2.375) & (mesh.y <= -2.125)))
mesh_regions['y'].append((-2.375, -2.125, 0.0833))
if not mesh_graded:
	mesh.y = np.concatenate((mesh.y, arangeWithEndpoint(-2.375,-2.125,0.0833)))
mesh.z = np.delete(mesh.z, np.argwhere((mesh.z >= 0.254) & (mesh.z <= 0.289)))
mesh_regions['z'].append((0.254, 0.289, 0.035))
if not mesh_graded:
	mesh.z = np.concatenate((mesh.z, arangeWithEndpoint(0.254,0.289,0.035)))

## GRID - Copper_Top_GND - Copper_Top_NC_15 (Fixed Distance)
mesh.x = np.delete(mesh.x, np.argwhere((mesh.x >= 2.6) & (mesh.x <= 3.35)))
mesh_regions['x'].append((2.6, 3.35, 0.0833))
if not mesh_graded:
	mesh.x = np.concatenate((mesh.x, arangeWithEndpoint(2.6,3.35,0.0833)))
mesh.y = np.delete(mesh.y, np.argwhere((mesh.y >= 2.125) & (mesh.y <= 2.375)))
mesh_regions['y'].append((2.125, 2.375, 0.0833))
if not mesh_graded:
	mesh.y = np.concatenate((mesh.y, arangeWithEndpoint(2.125,2.375,0.0833)))
mesh.z = np.delete(mesh.z, np.argwhere((mesh.z >= 0.254) & (mesh.z <= 0.289)))
mesh_regions['z'].append((0.254, 0.289, 0.035))
if not mesh_graded:
	mesh.z = np.concatenate((mesh.z, arangeWithEndpoint(0.254,0.289,0.035)))

## GRID - Copper_Top_GND - Copper_Top_NC_11 (Fixed Distance)
mesh.x = np.delete(mesh.x, np.argwhere((mesh.x >= 2.125) & (mesh.x <= 2.375)))
mesh_regions['x'].append((2.125, 2.375, 0.0833))
if not mesh_graded:
	mesh.x = np.concatenate((mesh.x, arangeWithEndpoint(2.125,2.375,0.0833)))
mesh.y = np.delete(mesh.y, np.argwhere((mesh.y >= 2.6) & (mesh.y <= 3.35)))
mesh_regions['y'].append((2.6, 3.35, 0.0833))
if not mesh_graded:
	mesh.y = np.concatenate((mesh.y, arangeWithEndpoint(2.6,3.35,0.0833)))
mesh.z = np.delete(mesh.z, np.argwhere((mesh.z >= 0.254) & (mesh.z <= 0.289)))
mesh_regions['z'].append((0.254, 0.289, 0.035))
if not mesh_graded:
	mesh.z = np.concatenate((mesh.z, arangeWithEndpoint(0.254,0.289,0.035)))

## GRID - Copper_Top_GND - Copper_Top_NC_10 (Fixed Distance)
mesh.x = np.delete(mesh.x, np.argwhere((mesh.x >= -2.375) & (mesh.x <= -2.125)))
mesh_regions['x'].append((-2.375, -2.125, 0.0833))
if not mesh_graded:
	mesh.x = np.concatenate((mesh.x, arangeWithEndpoint(-2.375,-2.125,0.0833)))
mesh.y = np.delete(mesh.y, np.argwhere((mesh.y >= -3.35) & (mesh.y <= -2.6)))
mesh_regions['y'].append((-3.35, -2.6, 0.0833))
if not mesh_graded:
	mesh.y = np.concatenate((mesh.y, arangeWithEndpoint(-3.35,-2.6,0.0833)))
mesh.z = np.delete(mesh.z, np.argwhere((mesh.z >= 0.254) & (mesh.z <= 0.289)))
mesh_regions['z'].append((0.254, 0.289, 0.035))
if not mesh_graded:
	mesh.z = np.concatenate((mesh.z, arangeWithEndpoint(0.254,0.289,0.035)))

## GRID - Copper_Top_GND - Copper_Top_NC_8 (Fixed Distance)
mesh.x = np.delete(mesh.x, np.argwhere((mesh.x >= -3.35) & (mesh.x <= -2.6)))
mesh_regions['x'].append((-3.35, -2.6, 0.0833))
if not mesh_graded:
	mesh.x = np.concatenate((mesh.x, arangeWithEndpoint(-3.35,-2.6,0.0833)))
mesh.y = np.delete(mesh.y, np.argwhere((mesh.y >= -2.375) & (mesh.y <= -2.125)))
mesh_regions['y'].append((-2.375, -2.125, 0.0833))
if not mesh_graded:
	mesh.y = np.concatenate((mesh.y, arangeWithEndpoint(-2.375,-2.125,0.0833)))
mesh.z = np.delete(mesh.z, np.argwhere((mesh.z >= 0.254) & (mesh.z <= 0.289)))
mesh_regions['z'].append((0.254, 0.289, 0.035))
if not mesh_graded:
	mesh.z = np.concatenate((mesh.z, arangeWithEndpoint(0.254,0.289,0.035)))

## GRID - Copper_Top_GND - Copper_Top_NC_4 (Fixed Distance)
mesh.x = np.delete(mesh.x, np.argwhere((mesh.x >= -3.35) & (mesh.x <= -2.6)))
mesh_regions['x'].append((-3.35, -2.6, 0.0833))
if not mesh_graded:
	mesh.x = np.concatenate((mesh.x, arangeWithEndpoint(-3.35,-2.6,0.0833)))
mesh.y = np.delete(mesh.y, np.argwhere((mesh.y >= 2.125) & (mesh.y <= 2.375)))
mesh_regions['y'].append((2.125, 2.375, 0.0833))
if not mesh_graded:
	mesh.y = np.concatenate((mesh.y, arangeWithEndpoint(2.125,2.375,0.0833)))
mesh.z = np.delete(mesh.z, np.argwhere((mesh.z >= 0.254) & (mesh.z <= 0.289)))
mesh_regions['z'].append((0.254, 0.289, 0.035))
if not mesh_graded:
	mesh.z = np.concatenate((mesh.z, arangeWithEndpoint(0.254,0.289,0.035)))

## GRID - Copper_Top_GND - Copper_Top_NC_1 (Fixed Distance)
mesh.x = np.delete(mesh.x, np.argwhere((mesh.x >= -2.375) & (mesh.x <= -2.125)))
mesh_regions['x'].append((-2.375, -2.125, 0.0833))
if not mesh_graded:
	mesh.x = np.concatenate((mesh.x, arangeWithEndpoint(-2.375,-2.125,0.0833)))
mesh.y = np.delete(mesh.y, np.argwhere((mesh.y >= 2.6) & (mesh.y <= 3.35)))
mesh_regions['y'].append((2.6, 3.35, 0.0833))
if not mesh_graded:
	mesh.y = np.concatenate((mesh.y, arangeWithEndpoint(2.6,3.35,0.0833)))
mesh.z = np.delete(mesh.z, np.argwhere((mesh.z >= 0.254) & (mesh.z <= 0.289)))
mesh_regions['z'].append((0.254, 0.289, 0.035))
if not mesh_graded:
	mesh.z = np.concatenate((mesh.z, arangeWithEndpoint(0.254,0.289,0.035)))

## GRID - Copper_Top_GND - Copper_Top_NC_18 (Fixed Distance)
mesh.x = np.delete(mesh.x, np.argwhere((mesh.x >= 2.05) & (mesh.x <= 3.35)))
mesh_regions['x'].append((2.05, 3.35, 0.0833))
if not mesh_graded:
	mesh.x = np.concatenate((mesh.x, arangeWithEndpoint(2.05,3.35,0.0833)))
mesh.y = np.delete(mesh.y, np.argwhere((mesh.y >= -1.875) & (mesh.y <= -1.625)))
mesh_regions['y'].append((-1.875, -1.625, 0.0833))
if not mesh_graded:
	mesh.y = np.concatenate((mesh.y, arangeWithEndpoint(-1.875,-1.625,0.0833)))
mesh.z = np.delete(mesh.z, np.argwhere((mesh.z >= 0.254) & (mesh.z <= 0.289)))
mesh_regions['z'].append((0.254, 0.289, 0.035))
if not mesh_graded:
	mesh.z = np.concatenate((mesh.z, arangeWithEndpoint(0.254,0.289,0.035)))

## GRID - Copper_Top_GND - Copper_Top_NC_17 (Fixed Distance)
mesh.x = np.delete(mesh.x, np.argwhere((mesh.x >= 2.05) & (mesh.x <= 3.35)))
mesh_regions['x'].append((2.05, 3.35, 0.0833))
if not mesh_graded:
	mesh.x = np.concatenate((mesh.x, arangeWithEndpoint(2.05,3.35,0.0833)))
mesh.y = np.delete(mesh.y, np.argwhere((mesh.y >= 1.125) & (mesh.y <= 1.375)))
mesh_regions['y'].append((1.125, 1.375, 0.0833))
if not mesh_graded:
	mesh.y = np.concatenate((mesh.y, arangeWithEndpoint(1.125,1.375,0.0833)))
mesh.z = np.delete(mesh.z, np.argwhere((mesh.z >= 0.254) & (mesh.z <= 0.289)))
mesh_regions['z'].append((0.254, 0.289, 0.035))
if not mesh_graded:
	mesh.z = np.concatenate((mesh.z, arangeWithEndpoint(0.254,0.289,0.035)))

## GRID - Copper_Top_GND - Copper_Top_NC_16 (Fixed Distance)
mesh.x = np.delete(mesh.x, np.argwhere((mesh.x >= 2.05) & (mesh.x <= 3.35)))
mesh_regions['x'].append((2.05, 3.35, 0.0833))
if not mesh_graded:
	mesh.x = np.concatenate((mesh.x, arangeWithEndpoint(2.05,3.35,0.0833)))
mesh.y = np.delete(mesh.y, np.argwhere((mesh.y >= 1.625) & (mesh.y <= 1.875)))
mesh_regions['y'].append((1.625, 1.875, 0.0833))
if not mesh_graded:
	mesh.y = np.concatenate((mesh.y, arangeWithEndpoint(1.625,1.875,0.0833)))
mesh.z = np.delete(mesh.z, np.argwhere((mesh.z >= 0.254) & (mesh.z <= 0.289)))
mesh_regions['z'].append((0.254, 0.289, 0.035))
if not mesh_graded:
	mesh.z = np.concatenate((mesh.z, arangeWithEndpoint(0.254,0.289,0.035)))

## GRID - Copper_Top_GND - Copper_Top_NC_14 (Fixed Distance)
mesh.x = np.delete(mesh.x, np.argwhere((mesh.x >= 1.625) & (mesh.x <= 1.875)))
mesh_regions['x'].append((1.625, 1.875, 0.0833))
if not mesh_graded:
	mesh.x = np.concatenate((mesh.x, arangeWithEndpoint(1.625,1.875,0.0833)))
mesh.y = np.delete(mesh.y, np.argwhere((mesh.y >= 2.05) & (mesh.y <= 3.35)))
mesh_regions['y'].append((2.05, 3.35, 0.0833))
if not mesh_graded:
	mesh.y = np.concatenate((mesh.y, arangeWithEndpoint(2.05,3.35,0.0833)))
mesh.z = np.delete(mesh.z, np.argwhere((mesh.z >= 0.254) & (mesh.z <= 0.289)))
mesh_regions['z'].append((0.254, 0.289, 0.035))
if not mesh_graded:
	mesh.z = np.concatenate((mesh.z, arangeWithEndpoint(0.254,0.289,0.035)))

## GRID - Copper_Top_GND - Copper_Top_NC_13 (Fixed Distance)
mesh.x = np.delete(mesh.x, np.argwhere((mesh.x >= 1.125) & (mesh.x <= 1.375)))
mesh_regions['x'].append((1.125, 1.375, 0.0833))
if not mesh_graded:
	mesh.x = np.concatenate((mesh.x, arangeWithEndpoint(1.125,1.375,0.0833)))
mesh.y = np.delete(mesh.y, np.argwhere((mesh.y >= 2.05) & (mesh.y <= 3.35)))
mesh_regions['y'].append((2.05, 3.35, 0.0833))
if not mesh_graded:
	mesh.y = np.concatenate((mesh.y, arangeWithEndpoint(2.05,3.35,0.0833)))
mesh.z = np.delete(mesh.z, np.argwhere((mesh.z >= 0.254) & (mesh.z <= 0.289)))
mesh_regions['z'].append((0.254, 0.289, 0.035))
if not mesh_graded:
	mesh.z = np.concatenate((mesh.z, arangeWithEndpoint(0.254,0.289,0.035)))

## GRID - Copper_Top_GND - Copper_Top_NC_12 (Fixed Distance)
mesh.x = np.delete(mesh.x, np.argwhere((mesh.x >= 0.625) & (mesh.x <= 0.875)))
mesh_regions['x'].append((0.625, 0.875, 0.0833))
if not mesh_graded:
	mesh.x = np.concatenate((mesh.x, arangeWithEndpoint(0.625,0.875,0.0833)))
mesh.y = np.delete(mesh.y, np.argwhere((mesh.y >= 2.05) & (mesh.y <= 3.35)))
mesh_regions['y'].append((2.05, 3.35, 0.0833))
if not mesh_graded:
	mesh.y = np.concatenate((mesh.y, arangeWithEndpoint(2.05,3.35,0.0833)))
mesh.z = np.delete(mesh.z, np.argwhere((mesh.z >= 0.254) & (mesh.z <= 0.289)))
mesh_regions['z'].append((0.254, 0.289, 0.035))
if not mesh_graded:
	mesh.z = np.concatenate((mesh.z, arangeWithEndpoint(0.254,0.289,0.035)))

## GRID - Copper_Top_GND - Copper_Top_NC_9 (Fixed Distance)
mesh.x = np.delete(mesh.x, np.argwhere((mesh.x >= -1.875) & (mesh.x <= -1.625)))
mesh_regions['x'].append((-1.875, -1.625, 0.0833))
if not mesh_graded:
	mesh.x = np.concatenate((mesh.x, arangeWithEndpoint(-1.875,-1.625,0.0833)))
mesh.y = np.delete(mesh.y, np.argwhere((mesh.y >= -3.35) & (mesh.y <= -2.05)))
mesh_regions['y'].append((-3.35, -2.05, 0.0833))
if not mesh_graded:
	mesh.y = np.concatenate((mesh.y, arangeWithEndpoint(-3.35,-2.05,0.0833)))
mesh.z = np.delete(mesh.z, np.argwhere((mesh.z >= 0.254) & (mesh.z <= 0.289)))
mesh_regions['z'].append((0.254, 0.289, 0.035))
if not mesh_graded:
	mesh.z = np.concatenate((mesh.z, arangeWithEndpoint(0.254,0.289,0.035)))

## GRID - Copper_Top_GND - Copper_Top_NC_7 (Fixed Distance)
mesh.x = np.delete(mesh.x, np.argwhere((mesh.x >= -3.35) & (mesh.x <= -2.05)))
mesh_regions['x'].append((-3.35, -2.05, 0.0833))
if not mesh_graded:
	mesh.x = np.concatenate((mesh.x, arangeWithEndpoint(-3.35,-2.05,0.0833)))
mesh.y = np.delete(mesh.y, np.argwhere((mesh.y >= -1.875) & (mesh.y <= -1.625)))
mesh_regions['y'].append((-1.875, -1.625, 0.0833))
if not mesh_graded:
	mesh.y = np.concatenate((mesh.y, arangeWithEndpoint(-1.875,-1.625,0.0833)))
mesh.z = np.delete(mesh.z, np.argwhere((mesh.z >= 0.254) & (mesh.z <= 0.289)))
mesh_regions['z'].append((0.254, 0.289, 0.035))
if not mesh_graded:
	mesh.z = np.concatenate((mesh.z, arangeWithEndpoint(0.254,0.289,0.035)))

## GRID - Copper_Top_GND - Copper_Top_NC_6 (Fixed Distance)
mesh.x = np.delete(mesh.x, np.argwhere((mesh.x >= -3.35) & (mesh.x <= -2.05)))
mesh_regions['x'].append((-3.35, -2.05, 0.0833))
if not mesh_graded:
	mesh.x = np.concatenate((mesh.x, arangeWithEndpoint(-3.35,-2.05,0.0833)))
mesh.y = np.delete(mesh.y, np.argwhere((mesh.y >= 1.125) & (mesh.y <= 1.375)))
mesh_regions['y'].append((1.125, 1.375, 0.0833))
if not mesh_graded:
	mesh.y = np.concatenate((mesh.y, arangeWithEndpoint(1.125,1.375,0.0833)))
mesh.z = np.delete(mesh.z, np.argwhere((mesh.z >= 0.254) & (mesh.z <= 0.289)))
mesh_regions['z'].append((0.254, 0.289, 0.035))
if not mesh_graded:
	mesh.z = np.concatenate((mesh.z, arangeWithEndpoint(0.254,0.289,0.035)))

## GRID - Copper_Top_GND - Copper_Top_NC_5 (Fixed Distance)
mesh.x = np.delete(mesh.x, np.argwhere((mesh.x >= -3.35) & (mesh.x <= -2.05)))
mesh_regions['x'].append((-3.35, -2.05, 0.0833))
if not mesh_graded:
	mesh.x = np.concatenate((mesh.x, arangeWithEndpoint(-3.35,-2.05,0.0833)))
mesh.y = np.delete(mesh.y, np.argwhere((mesh.y >= 1.625) & (mesh.y <= 1.875)))
mesh_regions['y'].append((1.625, 1.875, 0.0833))
if not mesh_graded:
	mesh.y = np.concatenate((mesh.y, arangeWithEndpoint(1.625,1.875,0.0833)))
mesh.z = np.delete(mesh.z, np.argwhere((mesh.z >= 0.254) & (mesh.z <= 0.289)))
mesh_regions['z'].append((0.254, 0.289, 0.035))
if not mesh_graded:
	mesh.z = np.concatenate((mesh.z, arangeWithEndpoint(0.254,0.289,0.035)))

## GRID - Copper_Top_GND - Copper_Top_NC_3 (Fixed Distance)
mesh.x = np.delete(mesh.x, np.argwhere((mesh.x >= -1.375) & (mesh.x <= -1.125)))
mesh_regions['x'].append((-1.375, -1.125, 0.0833))
if not mesh_graded:
	mesh.x = np.concatenate((mesh.x, arangeWithEndpoint(-1.375,-1.125,0.0833)))
mesh.y = np.delete(mesh.y, np.argwhere((mesh.y >= 2.05) & (mesh.y <= 3.35)))
mesh_regions['y'].append((2.05, 3.35, 0.0833))
if not mesh_graded:
	mesh.y = np.concatenate((mesh.y, arangeWithEndpoint(2.05,3.35,0.0833)))
mesh.z = np.delete(mesh.z, np.argwhere((mesh.z >= 0.254) & (mesh.z <= 0.289)))
mesh_regions['z'].append((0.254, 0.289, 0.035))
if not mesh_graded:
	mesh.z = np.concatenate((mesh.z, arangeWithEndpoint(0.254,0.289,0.035)))

## GRID - Copper_Top_GND - Copper_Top_NC_2 (Fixed Distance)
mesh.x = np.delete(mesh.x, np.argwhere((mesh.x >= -1.875) & (mesh.x <= -1.625)))
mesh_regions['x'].append((-1.875, -1.625, 0.0833))
if not mesh_graded:
	mesh.x = np.concatenate((mesh.x, arangeWithEndpoint(-1.875,-1.625,0.0833)))
mesh.y = np.delete(mesh.y, np.argwhere((mesh.y >= 2.05) & (mesh.y <= 3.35)))
mesh_regions['y'].append((2.05, 3.35, 0.0833))
if not mesh_graded:
	mesh.y = np.concatenate((mesh.y, arangeWithEndpoint(2.05,3.35,0.0833)))
mesh.z = np.delete(mesh.z, np.argwhere((mesh.z >= 0.254) & (mesh.z <= 0.289)))
mesh_regions['z'].append((0.254, 0.289, 0.035))
if not mesh_graded:
	mesh.z = np.concatenate((mesh.z, arangeWithEndpoint(0.254,0.289,0.035)))

## GRID - Copper_Top_GND - Copper_Top_VTMP (Fixed Distance)
mesh.x = np.delete(mesh.x, np.argwhere((mesh.x >= -0.4) & (mesh.x <= -0.1)))
mesh_regions['x'].append((-0.4, -0.1, 0.0833))
if not mesh_graded:
	mesh.x = np.concatenate((mesh.x, arangeWithEndpoint(-0.4,-0.1,0.0833)))
mesh.y = np.delete(mesh.y, np.argwhere((mesh.y >= 2.05) & (mesh.y <= 4.5)))
mesh_regions['y'].append((2.05, 4.5, 0.0833))
if not mesh_graded:
	mesh.y = np.concatenate((mesh.y, arangeWithEndpoint(2.05,4.5,0.0833)))
mesh.z = np.delete(mesh.z, np.argwhere((mesh.z >= 0.254) & (mesh.z <= 0.289)))
mesh_regions['z'].append((0.254, 0.289, 0.035))
if not mesh_graded:
	mesh.z = np.concatenate((mesh.z, arangeWithEndpoint(0.254,0.289,0.035)))

## GRID - Copper_Top_GND - Copper_Top_VCC (Fixed Distance)
mesh.x = np.delete(mesh.x, np.argwhere((mesh.x >= -1.81213203436) & (mesh.x <= -1.1)))
mesh_regions['x'].append((-1.81213203436, -1.1, 0.0833))
if not mesh_graded:
	mesh.x = np.concatenate((mesh.x, arangeWithEndpoint(-1.81213203436,-1.1,0.0833)))
mesh.y = np.delete(mesh.y, np.argwhere((mesh.y >= -4.5) & (mesh.y <= -2.05)))
mesh_regions['y'].append((-4.5, -2.05, 0.0833))
if not mesh_graded:
	mesh.y = np.concatenate((mesh.y, arangeWithEndpoint(-4.5,-2.05,0.0833)))
mesh.z = np.delete(mesh.z, np.argwhere((mesh.z >= 0.254) & (mesh.z <= 0.289)))
mesh_regions['z'].append((0.254, 0.289, 0.035))
if not mesh_graded:
	mesh.z = np.concatenate((mesh.z, arangeWithEndpoint(0.254,0.289,0.035)))

## GRID - Copper_Top_GND - Copper_Top_GNDHEAT (Fixed Distance)
mesh.x = np.delete(mesh.x, np.argwhere((mesh.x >= 1.6) & (mesh.x <= 2.31213203436)))
mesh_regions['x'].append((1.6, 2.31213203436, 0.0833))
if not mesh_graded:
	mesh.x = np.concatenate((mesh.x, arangeWithEndpoint(1.6,2.31213203436,0.0833)))
mesh.y = np.delete(mesh.y, np.argwhere((mesh.y >= -4.5) & (mesh.y <= -2.05)))
mesh_regions['y'].append((-4.5, -2.05, 0.0833))
if not mesh_graded:
	mesh.y = np.concatenate((mesh.y, arangeWithEndpoint(-4.5,-2.05,0.0833)))
mesh.z = np.delete(mesh.z, np.argwhere((mesh.z >= 0.254) & (mesh.z <= 0.289)))
mesh_regions['z'].append((0.254, 0.289, 0.035))
if not mesh_graded:
	mesh.z = np.concatenate((mesh.z, arangeWithEndpoint(0.254,0.289,0.035)))

## GRID - Copper_Top_GND - Copper_Top_IHEAT (Fixed Distance)
mesh.x = np.delete(mesh.x, np.argwhere((mesh.x >= 0.187867965644) & (mesh.x <= 0.9)))
mesh_regions['x'].append((0.187867965644, 0.9, 0.0833))
if not mesh_graded:
	mesh.x = np.concatenate((mesh.x, arangeWithEndpoint(0.187867965644,0.9,0.0833)))
mesh.y = np.delete(mesh.y, np.argwhere((mesh.y >= -4.5) & (mesh.y <= -2.05)))
mesh_regions['y'].append((-4.5, -2.05, 0.0833))
if not mesh_graded:
	mesh.y = np.concatenate((mesh.y, arangeWithEndpoint(-4.5,-2.05,0.0833)))
mesh.z = np.delete(mesh.z, np.argwhere((mesh.z >= 0.254) & (mesh.z <= 0.289)))
mesh_regions['z'].append((0.254, 0.289, 0.035))
if not mesh_graded:
	mesh.z = np.concatenate((mesh.z, arangeWithEndpoint(0.254,0.289,0.035)))

## GRID - Copper_Top_GND - Copper_Top_IDC (Fixed Distance)
mesh.x = np.delete(mesh.x, np.argwhere((mesh.x >= -0.812132034356) & (mesh.x <= -0.1)))
mesh_regions['x'].append((-0.812132034356, -0.1, 0.0833))
if not mesh_graded:
	mesh.x = np.concatenate((mesh.x, arangeWithEndpoint(-0.812132034356,-0.1,0.0833)))
mesh.y = np.delete(mesh.y, np.argwhere((mesh.y >= -4.5) & (mesh.y <= -2.05)))
mesh_regions['y'].append((-4.5, -2.05, 0.0833))
if not mesh_graded:
	mesh.y = np.concatenate((mesh.y, arangeWithEndpoint(-4.5,-2.05,0.0833)))
mesh.z = np.delete(mesh.z, np.argwhere((mesh.z >= 0.254) & (mesh.z <= 0.289)))
mesh_regions['z'].append((0.254, 0.289, 0.035))
if not mesh_graded:
	mesh.z = np.concatenate((mesh.z, arangeWithEndpoint(0.254,0.289,0.035)))

## GRID - Copper_Top_GND - Copper_Top_RFOUT (Fixed Distance)
mesh.x = np.delete(mesh.x, np.argwhere((mesh.x >= 2.05) & (mesh.x <= 14.9)))
mesh_regions['x'].append((2.05, 14.9, 0.0833))
if not mesh_graded:
	mesh.x = np.concatenate((mesh.x, arangeWithEndpoint(2.05,14.9,0.0833)))
mesh.y = np.delete(mesh.y, np.argwhere((mesh.y >= -0.47) & (mesh.y <= -0.03)))
mesh_regions['y'].append((-0.47, -0.03, 0.0833))
if not mesh_graded:
	mesh.y = np.concatenate((mesh.y, arangeWithEndpoint(-0.47,-0.03,0.0833)))
mesh.z = np.delete(mesh.z, np.argwhere((mesh.z >= 0.254) & (mesh.z <= 0.289)))
mesh_regions['z'].append((0.254, 0.289, 0.035))
if not mesh_graded:
	mesh.z = np.concatenate((mesh.z, arangeWithEndpoint(0.254,0.289,0.035)))

## GRID - Copper_Top_GND - Copper_Top_RFIN (Fixed Distance)
mesh.x = np.delete(mesh.x, np.argwhere((mesh.x >= -14.9) & (mesh.x <= -2.05)))
mesh_regions['x'].append((-14.9, -2.05, 0.0833))
if not mesh_graded:
	mesh.x = np.concatenate((mesh.x, arangeWithEndpoint(-14.9,-2.05,0.0833)))
mesh.y = np.delete(mesh.y, np.argwhere((mesh.y >= -0.47) & (mesh.y <= -0.03)))
mesh_regions['y'].append((-0.47, -0.03, 0.0833))
if not mesh_graded:
	mesh.y = np.concatenate((mesh.y, arangeWithEndpoint(-0.47,-0.03,0.0833)))
mesh.z = np.delete(mesh.z, np.argwhere((mesh.z >= 0.254) & (mesh.z <= 0.289)))
mesh_regions['z'].append((0.254, 0.289, 0.035))
if not mesh_graded:
	mesh.z = np.concatenate((mesh.z, arangeWithEndpoint(0.254,0.289,0.035)))

## GRID - Copper_Top_GND - Copper_Top_GND (Fixed Distance)
mesh.x = np.delete(mesh.x, np.argwhere((mesh.x >= -14.9495) & (mesh.x <= 14.9495)))
mesh_regions['x'].append((-14.9495, 14.9495, 0.0833))
if not mesh_graded:
	mesh.x = np.concatenate((mesh.x, arangeWithEndpoint(-14.9495,14.9495,0.0833)))
mesh.y = np.delete(mesh.y, np.argwhere((mesh.y >= -4.5) & (mesh.y <= 4.5)))
mesh_regions['y'].append((-4.5, 4.5, 0.0833))
if not mesh_graded:
	mesh.y = np.concatenate((mesh.y, arangeWithEndpoint(-4.5,4.5,0.0833)))
mesh.z = np.delete(mesh.z, np.argwhere((mesh.z >= 0.254) & (mesh.z <= 0.289)))
mesh_regions['z'].append((0.254, 0.289, 0.035))
if not mesh_graded:
	mesh.z = np.concatenate((mesh.z, arangeWithEndpoint(0.254,0.289,0.035)))

## GRID - Copper_Vias_GND_29 - Copper_Vias_GND_28 (Fixed Distance)
mesh.x = np.delete(mesh.x, np.argwhere((mesh.x >= -8.34932078839) & (mesh.x <= -8.05)))
mesh_regions['x'].append((-8.34932078839, -8.05, 0.0998))
if not mesh_graded:
	mesh.x = np.concatenate((mesh.x, arangeWithEndpoint(-8.34932078839,-8.05,0.0998)))
mesh.y = np.delete(mesh.y, np.argwhere((mesh.y >= -1.24983010088) & (mesh.y <= -0.950169899123)))
mesh_regions['y'].append((-1.24983010088, -0.950169899123, 0.0998))
if not mesh_graded:
	mesh.y = np.concatenate((mesh.y, arangeWithEndpoint(-1.24983010088,-0.950169899123,0.0998)))
mesh.z = np.delete(mesh.z, np.argwhere((mesh.z >= 0) & (mesh.z <= 0.254)))
mesh_regions['z'].append((0, 0.254, 0.254))
if not mesh_graded:
	mesh.z = np.concatenate((mesh.z, arangeWithEndpoint(0,0.254,0.254)))

## GRID - Copper_Vias_GND_29 - Copper_Vias_GND_27 (Fixed Distance)
mesh.x = np.delete(mesh.x, np.argwhere((mesh.x >= -8.34932078839) & (mesh.x <= -8.05)))
mesh_regions['x'].append((-8.34932078839, -8.05, 0.0998))
if not mesh_graded:
	mesh.x = np.concatenate((mesh.x, arangeWithEndpoint(-8.34932078839,-8.05,0.0998)))
mesh.y = np.delete(mesh.y, np.argwhere((mesh.y >= 0.450169899123) & (mesh.y <= 0.749830100877)))
mesh_regions['y'].append((0.450169899123, 0.749830100877, 0.0998))
if not mesh_graded:
	mesh.y = np.concatenate((mesh.y, arangeWithEndpoint(0.450169899123,0.749830100877,0.0998)))
mesh.z = np.delete(mesh.z, np.argwhere((mesh.z >= 0) & (mesh.z <= 0.254)))
mesh_regions['z'].append((0, 0.254, 0.254))
if not mesh_graded:
	mesh.z = np.concatenate((mesh.z, arangeWithEndpoint(0,0.254,0.254)))

## GRID - Copper_Vias_GND_29 - Copper_Vias_GND_26 (Fixed Distance)
mesh.x = np.delete(mesh.x, np.argwhere((mesh.x >= -9.34932078839) & (mesh.x <= -9.05)))
mesh_regions['x'].append((-9.34932078839, -9.05, 0.0998))
if not mesh_graded:
	mesh.x = np.concatenate((mesh.x, arangeWithEndpoint(-9.34932078839,-9.05,0.0998)))
mesh.y = np.delete(mesh.y, np.argwhere((mesh.y >= -1.24983010088) & (mesh.y <= -0.950169899123)))
mesh_regions['y'].append((-1.24983010088, -0.950169899123, 0.0998))
if not mesh_graded:
	mesh.y = np.concatenate((mesh.y, arangeWithEndpoint(-1.24983010088,-0.950169899123,0.0998)))
mesh.z = np.delete(mesh.z, np.argwhere((mesh.z >= 0) & (mesh.z <= 0.254)))
mesh_regions['z'].append((0, 0.254, 0.254))
if not mesh_graded:
	mesh.z = np.concatenate((mesh.z, arangeWithEndpoint(0,0.254,0.254)))

## GRID - Copper_Vias_GND_29 - Copper_Vias_GND_25 (Fixed Distance)
mesh.x = np.delete(mesh.x, np.argwhere((mesh.x >= -9.34932078839) & (mesh.x <= -9.05)))
mesh_regions['x'].append((-9.34932078839, -9.05, 0.0998))
if not mesh_graded:
	mesh.x = np.concatenate((mesh.x, arangeWithEndpoint(-9.34932078839,-9.05,0.0998)))
mesh.y = np.delete(mesh.y, np.argwhere((mesh.y >= 0.450169899123) & (mesh.y <= 0.749830100877)))
mesh_regions['y'].append((0.450169899123, 0.749830100877, 0.0998))
if not mesh_graded:
	mesh.y = np.concatenate((mesh.y, arangeWithEndpoint(0.450169899123,0.749830100877,0.0998)))
mesh.z = np.delete(mesh.z, np.argwhere((mesh.z >= 0) & (mesh.z <= 0.254)))
mesh_regions['z'].append((0, 0.254, 0.254))
if not mesh_graded:
	mesh.z = np.concatenate((mesh.z, arangeWithEndpoint(0,0.254,0.254)))

## GRID - Copper_Vias_GND_29 - Copper_Vias_GND_24 (Fixed Distance)
mesh.x = np.delete(mesh.x, np.argwhere((mesh.x >= -10.3493207884) & (mesh.x <= -10.05)))
mesh_regions['x'].append((-10.3493207884, -10.05, 0.0998))
if not mesh_graded:
	mesh.x = np.concatenate((mesh.x, arangeWithEndpoint(-10.3493207884,-10.05,0.0998)))
mesh.y = np.delete(mesh.y, np.argwhere((mesh.y >= -1.24983010088) & (mesh.y <= -0.950169899123)))
mesh_regions['y'].append((-1.24983010088, -0.950169899123, 0.0998))
if not mesh_graded:
	mesh.y = np.concatenate((mesh.y, arangeWithEndpoint(-1.24983010088,-0.950169899123,0.0998)))
mesh.z = np.delete(mesh.z, np.argwhere((mesh.z >= 0) & (mesh.z <= 0.254)))
mesh_regions['z'].append((0, 0.254, 0.254))
if not mesh_graded:
	mesh.z = np.concatenate((mesh.z, arangeWithEndpoint(0,0.254,0.254)))

## GRID - Copper_Vias_GND_29 - Copper_Vias_GND_23 (Fixed Distance)
mesh.x = np.delete(mesh.x, np.argwhere((mesh.x >= -10.3493207884) & (mesh.x <= -10.05)))
mesh_regions['x'].append((-10.3493207884, -10.05, 0.0998))
if not mesh_graded:
	mesh.x = np.concatenate((mesh.x, arangeWithEndpoint(-10.3493207884,-10.05,0.0998)))
mesh.y = np.delete(mesh.y, np.argwhere((mesh.y >= 0.450169899123) & (mesh.y <= 0.749830100877)))
mesh_regions['y'].append((0.450169899123, 0.749830100877, 0.0998))
if not mesh_graded:
	mesh.y = np.concatenate((mesh.y, arangeWithEndpoint(0.450169899123,0.749830100877,0.0998)))
mesh.z = np.delete(mesh.z, np.argwhere((mesh.z >= 0) & (mesh.z <= 0.254)))
mesh_regions['z'].append((0, 0.254, 0.254))
if not mesh_graded:
	mesh.z = np.concatenate((mesh.z, arangeWithEndpoint(0,0.254,0.254)))

## GRID - Copper_Vias_GND_29 - Copper_Vias_GND_22 (Fixed Distance)
mesh.x = np.delete(mesh.x, np.argwhere((mesh.x >= -11.3493207884) & (mesh.x <= -11.05)))
mesh_regions['x'].append((-11.3493207884, -11.05, 0.0998))
if not mesh_graded:
	mesh.x = np.concatenate((mesh.x, arangeWithEndpoint(-11.3493207884,-11.05,0.0998)))
mesh.y = np.delete(mesh.y, np.argwhere((mesh.y >= -1.24983010088) & (mesh.y <= -0.950169899123)))
mesh_regions['y'].append((-1.24983010088, -0.950169899123, 0.0998))
if not mesh_graded:
	mesh.y = np.concatenate((mesh.y, arangeWithEndpoint(-1.24983010088,-0.950169899123,0.0998)))
mesh.z = np.delete(mesh.z, np.argwhere((mesh.z >= 0) & (mesh.z <= 0.254)))
mesh_regions['z'].append((0, 0.254, 0.254))
if not mesh_graded:
	mesh.z = np.concatenate((mesh.z, arangeWithEndpoint(0,0.254,0.254)))

## GRID - Copper_Vias_GND_29 - Copper_Vias_GND_21 (Fixed Distance)
mesh.x = np.delete(mesh.x, np.argwhere((mesh.x >= -11.3493207884) & (mesh.x <= -11.05)))
mesh_regions['x'].append((-11.3493207884, -11.05, 0.0998))
if not mesh_graded:
	mesh.x = np.concatenate((mesh.x, arangeWithEndpoint(-11.3493207884,-11.05,0.0998)))
mesh.y = np.delete(mesh.y, np.argwhere((mesh.y >= 0.450169899123) & (mesh.y <= 0.749830100877)))
mesh_regions['y'].append((0.450169899123, 0.749830100877, 0.0998))
if not mesh_graded:
	mesh.y = np.concatenate((mesh.y, arangeWithEndpoint(0.450169899123,0.749830100877,0.0998)))
mesh.z = np.delete(mesh.z, np.argwhere((mesh.z >= 0) & (mesh.z <= 0.254)))
mesh_regions['z'].append((0, 0.254, 0.254))
if not mesh_graded:
	mesh.z = np.concatenate((mesh.z, arangeWithEndpoint(0,0.254,0.254)))

## GRID - Copper_Vias_GND_29 - Copper_Vias_GND_20 (Fixed Distance)
mesh.x = np.delete(mesh.x, np.argwhere((mesh.x >= -12.3493207884) & (mesh.x <= -12.05)))
mesh_regions['x'].append((-12.3493207884, -12.05, 0.0998))
if not mesh_graded:
	mesh.x = np.concatenate((mesh.x, arangeWithEndpoint(-12.3493207884,-12.05,0.0998)))
mesh.y = np.delete(mesh.y, np.argwhere((mesh.y >= -1.24983010088) & (mesh.y <= -0.950169899123)))
mesh_regions['y'].append((-1.24983010088, -0.950169899123, 0.0998))
if not mesh_graded:
	mesh.y = np.concatenate((mesh.y, arangeWithEndpoint(-1.24983010088,-0.950169899123,0.0998)))
mesh.z = np.delete(mesh.z, np.argwhere((mesh.z >= 0) & (mesh.z <= 0.254)))
mesh_regions['z'].append((0, 0.254, 0.254))
if not mesh_graded:
	mesh.z = np.concatenate((mesh.z, arangeWithEndpoint(0,0.254,0.254)))

## GRID - Copper_Vias_GND_29 - Copper_Vias_GND_19 (Fixed Distance)
mesh.x = np.delete(mesh.x, np.argwhere((mesh.x >= -12.3493207884) & (mesh.x <= -12.05)))
mesh_regions['x'].append((-12.3493207884, -12.05, 0.0998))
if not mesh_graded:
	mesh.x = np.concatenate((mesh.x, arangeWithEndpoint(-12.3493207884,-12.05,0.0998)))
mesh.y = np.delete(mesh.y, np.argwhere((mesh.y >= 0.450169899123) & (mesh.y <= 0.749830100877)))
mesh_regions['y'].append((0.450169899123, 0.749830100877, 0.0998))
if not mesh_graded:
	mesh.y = np.concatenate((mesh.y, arangeWithEndpoint(0.450169899123,0.749830100877,0.0998)))
mesh.z = np.delete(mesh.z, np.argwhere((mesh.z >= 0) & (mesh.z <= 0.254)))
mesh_regions['z'].append((0, 0.254, 0.254))
if not mesh_graded:
	mesh.z = np.concatenate((mesh.z, arangeWithEndpoint(0,0.254,0.254)))

## GRID - Copper_Vias_GND_29 - Copper_Vias_GND_18 (Fixed Distance)
mesh.x = np.delete(mesh.x, np.argwhere((mesh.x >= -13.3493207884) & (mesh.x <= -13.05)))
mesh_regions['x'].append((-13.3493207884, -13.05, 0.0998))
if not mesh_graded:
	mesh.x = np.concatenate((mesh.x, arangeWithEndpoint(-13.3493207884,-13.05,0.0998)))
mesh.y = np.delete(mesh.y, np.argwhere((mesh.y >= -1.24983010088) & (mesh.y <= -0.950169899123)))
mesh_regions['y'].append((-1.24983010088, -0.950169899123, 0.0998))
if not mesh_graded:
	mesh.y = np.concatenate((mesh.y, arangeWithEndpoint(-1.24983010088,-0.950169899123,0.0998)))
mesh.z = np.delete(mesh.z, np.argwhere((mesh.z >= 0) & (mesh.z <= 0.254)))
mesh_regions['z'].append((0, 0.254, 0.254))
if not mesh_graded:
	mesh.z = np.concatenate((mesh.z, arangeWithEndpoint(0,0.254,0.254)))

## GRID - Copper_Vias_GND_29 - Copper_Vias_GND_17 (Fixed Distance)
mesh.x = np.delete(mesh.x, np.argwhere((mesh.x >= -13.3493207884) & (mesh.x <= -13.05)))
mesh_regions['x'].append((-13.3493207884, -13.05, 0.0998))
if not mesh_graded:
	mesh.x = np.concatenate((mesh.x, arangeWithEndpoint(-13.3493207884,-13.05,0.0998)))
mesh.y = np.delete(mesh.y, np.argwhere((mesh.y >= 0.450169899123) & (mesh.y <= 0.749830100877)))
mesh_regions['y'].append((0.450169899123, 0.749830100877, 0.0998))
if not mesh_graded:
	mesh.y = np.concatenate((mesh.y, arangeWithEndpoint(0.450169899123,0.749830100877,0.0998)))
mesh.z = np.delete(mesh.z, np.argwhere((mesh.z >= 0) & (mesh.z <= 0.254)))
mesh_regions['z'].append((0, 0.254, 0.254))
if not mesh_graded:
	mesh.z = np.concatenate((mesh.z, arangeWithEndpoint(0,0.254,0.254)))

## GRID - Copper_Vias_GND_29 - Copper_Vias_GND_103 (Fixed Distance)
mesh.x = np.delete(mesh.x, np.argwhere((mesh.x >= 13.0506792116) & (mesh.x <= 13.35)))
mesh_regions['x'].append((13.0506792116, 13.35, 0.0998))
if not mesh_graded:
	mesh.x = np.concatenate((mesh.x, arangeWithEndpoint(13.0506792116,13.35,0.0998)))
mesh.y = np.delete(mesh.y, np.argwhere((mesh.y >= -3.34983010088) & (mesh.y <= -3.05016989912)))
mesh_regions['y'].append((-3.34983010088, -3.05016989912, 0.0998))
if not mesh_graded:
	mesh.y = np.concatenate((mesh.y, arangeWithEndpoint(-3.34983010088,-3.05016989912,0.0998)))
mesh.z = np.delete(mesh.z, np.argwhere((mesh.z >= 0) & (mesh.z <= 0.254)))
mesh_regions['z'].append((0, 0.254, 0.254))
if not mesh_graded:
	mesh.z = np.concatenate((mesh.z, arangeWithEndpoint(0,0.254,0.254)))

## GRID - Copper_Vias_GND_29 - Copper_Vias_GND_102 (Fixed Distance)
mesh.x = np.delete(mesh.x, np.argwhere((mesh.x >= 11.5506792116) & (mesh.x <= 11.85)))
mesh_regions['x'].append((11.5506792116, 11.85, 0.0998))
if not mesh_graded:
	mesh.x = np.concatenate((mesh.x, arangeWithEndpoint(11.5506792116,11.85,0.0998)))
mesh.y = np.delete(mesh.y, np.argwhere((mesh.y >= -3.34983010088) & (mesh.y <= -3.05016989912)))
mesh_regions['y'].append((-3.34983010088, -3.05016989912, 0.0998))
if not mesh_graded:
	mesh.y = np.concatenate((mesh.y, arangeWithEndpoint(-3.34983010088,-3.05016989912,0.0998)))
mesh.z = np.delete(mesh.z, np.argwhere((mesh.z >= 0) & (mesh.z <= 0.254)))
mesh_regions['z'].append((0, 0.254, 0.254))
if not mesh_graded:
	mesh.z = np.concatenate((mesh.z, arangeWithEndpoint(0,0.254,0.254)))

## GRID - Copper_Vias_GND_29 - Copper_Vias_GND_101 (Fixed Distance)
mesh.x = np.delete(mesh.x, np.argwhere((mesh.x >= 10.0506792116) & (mesh.x <= 10.35)))
mesh_regions['x'].append((10.0506792116, 10.35, 0.0998))
if not mesh_graded:
	mesh.x = np.concatenate((mesh.x, arangeWithEndpoint(10.0506792116,10.35,0.0998)))
mesh.y = np.delete(mesh.y, np.argwhere((mesh.y >= -3.34983010088) & (mesh.y <= -3.05016989912)))
mesh_regions['y'].append((-3.34983010088, -3.05016989912, 0.0998))
if not mesh_graded:
	mesh.y = np.concatenate((mesh.y, arangeWithEndpoint(-3.34983010088,-3.05016989912,0.0998)))
mesh.z = np.delete(mesh.z, np.argwhere((mesh.z >= 0) & (mesh.z <= 0.254)))
mesh_regions['z'].append((0, 0.254, 0.254))
if not mesh_graded:
	mesh.z = np.concatenate((mesh.z, arangeWithEndpoint(0,0.254,0.254)))

## GRID - Copper_Vias_GND_29 - Copper_Vias_GND_100 (Fixed Distance)
mesh.x = np.delete(mesh.x, np.argwhere((mesh.x >= 8.55067921161) & (mesh.x <= 8.85)))
mesh_regions['x'].append((8.55067921161, 8.85, 0.0998))
if not mesh_graded:
	mesh.x = np.concatenate((mesh.x, arangeWithEndpoint(8.55067921161,8.85,0.0998)))
mesh.y = np.delete(mesh.y, np.argwhere((mesh.y >= -3.34983010088) & (mesh.y <= -3.05016989912)))
mesh_regions['y'].append((-3.34983010088, -3.05016989912, 0.0998))
if not mesh_graded:
	mesh.y = np.concatenate((mesh.y, arangeWithEndpoint(-3.34983010088,-3.05016989912,0.0998)))
mesh.z = np.delete(mesh.z, np.argwhere((mesh.z >= 0) & (mesh.z <= 0.254)))
mesh_regions['z'].append((0, 0.254, 0.254))
if not mesh_graded:
	mesh.z = np.concatenate((mesh.z, arangeWithEndpoint(0,0.254,0.254)))

## GRID - Copper_Vias_GND_29 - Copper_Vias_GND_99 (Fixed Distance)
mesh.x = np.delete(mesh.x, np.argwhere((mesh.x >= 7.05067921161) & (mesh.x <= 7.35)))
mesh_regions['x'].append((7.05067921161, 7.35, 0.0998))
if not mesh_graded:
	mesh.x = np.concatenate((mesh.x, arangeWithEndpoint(7.05067921161,7.35,0.0998)))
mesh.y = np.delete(mesh.y, np.argwhere((mesh.y >= -3.34983010088) & (mesh.y <= -3.05016989912)))
mesh_regions['y'].append((-3.34983010088, -3.05016989912, 0.0998))
if not mesh_graded:
	mesh.y = np.concatenate((mesh.y, arangeWithEndpoint(-3.34983010088,-3.05016989912,0.0998)))
mesh.z = np.delete(mesh.z, np.argwhere((mesh.z >= 0) & (mesh.z <= 0.254)))
mesh_regions['z'].append((0, 0.254, 0.254))
if not mesh_graded:
	mesh.z = np.concatenate((mesh.z, arangeWithEndpoint(0,0.254,0.254)))

## GRID - Copper_Vias_GND_29 - Copper_Vias_GND_98 (Fixed Distance)
mesh.x = np.delete(mesh.x, np.argwhere((mesh.x >= 5.55067921161) & (mesh.x <= 5.85)))
mesh_regions['x'].append((5.55067921161, 5.85, 0.0998))
if not mesh_graded:
	mesh.x = np.concatenate((mesh.x, arangeWithEndpoint(5.55067921161,5.85,0.0998)))
mesh.y = np.delete(mesh.y, np.argwhere((mesh.y >= -3.34983010088) & (mesh.y <= -3.05016989912)))
mesh_regions['y'].append((-3.34983010088, -3.05016989912, 0.0998))
if not mesh_graded:
	mesh.y = np.concatenate((mesh.y, arangeWithEndpoint(-3.34983010088,-3.05016989912,0.0998)))
mesh.z = np.delete(mesh.z, np.argwhere((mesh.z >= 0) & (mesh.z <= 0.254)))
mesh_regions['z'].append((0, 0.254, 0.254))
if not mesh_graded:
	mesh.z = np.concatenate((mesh.z, arangeWithEndpoint(0,0.254,0.254)))

## GRID - Copper_Vias_GND_29 - Copper_Vias_GND_97 (Fixed Distance)
mesh.x = np.delete(mesh.x, np.argwhere((mesh.x >= 4.05067921161) & (mesh.x <= 4.35)))
mesh_regions['x'].append((4.05067921161, 4.35, 0.0998))
if not mesh_graded:
	mesh.x = np.concatenate((mesh.x, arangeWithEndpoint(4.05067921161,4.35,0.0998)))
mesh.y = np.delete(mesh.y, np.argwhere((mesh.y >= -3.34983010088) & (mesh.y <= -3.05016989912)))
mesh_regions['y'].append((-3.34983010088, -3.05016989912, 0.0998))
if not mesh_graded:
	mesh.y = np.concatenate((mesh.y, arangeWithEndpoint(-3.34983010088,-3.05016989912,0.0998)))
mesh.z = np.delete(mesh.z, np.argwhere((mesh.z >= 0) & (mesh.z <= 0.254)))
mesh_regions['z'].append((0, 0.254, 0.254))
if not mesh_graded:
	mesh.z = np.concatenate((mesh.z, arangeWithEndpoint(0,0.254,0.254)))

## GRID - Copper_Vias_GND_29 - Copper_Vias_GND_96 (Fixed Distance)
mesh.x = np.delete(mesh.x, np.argwhere((mesh.x >= -4.94932078839) & (mesh.x <= -4.65)))
mesh_regions['x'].append((-4.94932078839, -4.65, 0.0998))
if not mesh_graded:
	mesh.x = np.concatenate((mesh.x, arangeWithEndpoint(-4.94932078839,-4.65,0.0998)))
mesh.y = np.delete(mesh.y, np.argwhere((mesh.y >= -3.34983010088) & (mesh.y <= -3.05016989912)))
mesh_regions['y'].append((-3.34983010088, -3.05016989912, 0.0998))
if not mesh_graded:
	mesh.y = np.concatenate((mesh.y, arangeWithEndpoint(-3.34983010088,-3.05016989912,0.0998)))
mesh.z = np.delete(mesh.z, np.argwhere((mesh.z >= 0) & (mesh.z <= 0.254)))
mesh_regions['z'].append((0, 0.254, 0.254))
if not mesh_graded:
	mesh.z = np.concatenate((mesh.z, arangeWithEndpoint(0,0.254,0.254)))

## GRID - Copper_Vias_GND_29 - Copper_Vias_GND_95 (Fixed Distance)
mesh.x = np.delete(mesh.x, np.argwhere((mesh.x >= -6.44932078839) & (mesh.x <= -6.15)))
mesh_regions['x'].append((-6.44932078839, -6.15, 0.0998))
if not mesh_graded:
	mesh.x = np.concatenate((mesh.x, arangeWithEndpoint(-6.44932078839,-6.15,0.0998)))
mesh.y = np.delete(mesh.y, np.argwhere((mesh.y >= -3.34983010088) & (mesh.y <= -3.05016989912)))
mesh_regions['y'].append((-3.34983010088, -3.05016989912, 0.0998))
if not mesh_graded:
	mesh.y = np.concatenate((mesh.y, arangeWithEndpoint(-3.34983010088,-3.05016989912,0.0998)))
mesh.z = np.delete(mesh.z, np.argwhere((mesh.z >= 0) & (mesh.z <= 0.254)))
mesh_regions['z'].append((0, 0.254, 0.254))
if not mesh_graded:
	mesh.z = np.concatenate((mesh.z, arangeWithEndpoint(0,0.254,0.254)))

## GRID - Copper_Vias_GND_29 - Copper_Vias_GND_94 (Fixed Distance)
mesh.x = np.delete(mesh.x, np.argwhere((mesh.x >= -7.94932078839) & (mesh.x <= -7.65)))
mesh_regions['x'].append((-7.94932078839, -7.65, 0.0998))
if not mesh_graded:
	mesh.x = np.concatenate((mesh.x, arangeWithEndpoint(-7.94932078839,-7.65,0.0998)))
mesh.y = np.delete(mesh.y, np.argwhere((mesh.y >= -3.34983010088) & (mesh.y <= -3.05016989912)))
mesh_regions['y'].append((-3.34983010088, -3.05016989912, 0.0998))
if not mesh_graded:
	mesh.y = np.concatenate((mesh.y, arangeWithEndpoint(-3.34983010088,-3.05016989912,0.0998)))
mesh.z = np.delete(mesh.z, np.argwhere((mesh.z >= 0) & (mesh.z <= 0.254)))
mesh_regions['z'].append((0, 0.254, 0.254))
if not mesh_graded:
	mesh.z = np.concatenate((mesh.z, arangeWithEndpoint(0,0.254,0.254)))

## GRID - Copper_Vias_GND_29 - Copper_Vias_GND_93 (Fixed Distance)
mesh.x = np.delete(mesh.x, np.argwhere((mesh.x >= -9.44932078839) & (mesh.x <= -9.15)))
mesh_regions['x'].append((-9.44932078839, -9.15, 0.0998))
if not mesh_graded:
	mesh.x = np.concatenate((mesh.x, arangeWithEndpoint(-9.44932078839,-9.15,0.0998)))
mesh.y = np.delete(mesh.y, np.argwhere((mesh.y >= -3.34983010088) & (mesh.y <= -3.05016989912)))
mesh_regions['y'].append((-3.34983010088, -3.05016989912, 0.0998))
if not mesh_graded:
	mesh.y = np.concatenate((mesh.y, arangeWithEndpoint(-3.34983010088,-3.05016989912,0.0998)))
mesh.z = np.delete(mesh.z, np.argwhere((mesh.z >= 0) & (mesh.z <= 0.254)))
mesh_regions['z'].append((0, 0.254, 0.254))
if not mesh_graded:
	mesh.z = np.concatenate((mesh.z, arangeWithEndpoint(0,0.254,0.254)))

## GRID - Copper_Vias_GND_29 - Copper_Vias_GND_92 (Fixed Distance)
mesh.x = np.delete(mesh.x, np.argwhere((mesh.x >= -10.9493207884) & (mesh.x <= -10.65)))
mesh_regions['x'].append((-10.9493207884, -10.65, 0.0998))
if not mesh_graded:
	mesh.x = np.concatenate((mesh.x, arangeWithEndpoint(-10.9493207884,-10.65,0.0998)))
mesh.y = np.delete(mesh.y, np.argwhere((mesh.y >= -3.34983010088) & (mesh.y <= -3.05016989912)))
mesh_regions['y'].append((-3.34983010088, -3.05016989912, 0.0998))
if not mesh_graded:
	mesh.y = np.concatenate((mesh.y, arangeWithEndpoint(-3.34983010088,-3.05016989912,0.0998)))
mesh.z = np.delete(mesh.z, np.argwhere((mesh.z >= 0) & (mesh.z <= 0.254)))
mesh_regions['z'].append((0, 0.254, 0.254))
if not mesh_graded:
	mesh.z = np.concatenate((mesh.z, arangeWithEndpoint(0,0.254,0.254)))

## GRID - Copper_Vias_GND_29 - Copper_Vias_GND_91 (Fixed Distance)
mesh.x = np.delete(mesh.x, np.argwhere((mesh.x >= -12.4493207884) & (mesh.x <= -12.15)))
mesh_regions['x'].append((-12.4493207884, -12.15, 0.0998))
if not mesh_graded:
	mesh.x = np.concatenate((mesh.x, arangeWithEndpoint(-12.4493207884,-12.15,0.0998)))
mesh.y = np.delete(mesh.y, np.argwhere((mesh.y >= -3.34983010088) & (mesh.y <= -3.05016989912)))
mesh_regions['y'].append((-3.34983010088, -3.05016989912, 0.0998))
if not mesh_graded:
	mesh.y = np.concatenate((mesh.y, arangeWithEndpoint(-3.34983010088,-3.05016989912,0.0998)))
mesh.z = np.delete(mesh.z, np.argwhere((mesh.z >= 0) & (mesh.z <= 0.254)))
mesh_regions['z'].append((0, 0.254, 0.254))
if not mesh_graded:
	mesh.z = np.concatenate((mesh.z, arangeWithEndpoint(0,0.254,0.254)))

## GRID - Copper_Vias_GND_29 - Copper_Vias_GND_90 (Fixed Distance)
mesh.x = np.delete(mesh.x, np.argwhere((mesh.x >= -13.9493207884) & (mesh.x <= -13.65)))
mesh_regions['x'].append((-13.9493207884, -13.65, 0.0998))
if not mesh_graded:
	mesh.x = np.concatenate((mesh.x, arangeWithEndpoint(-13.9493207884,-13.65,0.0998)))
mesh.y = np.delete(mesh.y, np.argwhere((mesh.y >= -3.34983010088) & (mesh.y <= -3.05016989912)))
mesh_regions['y'].append((-3.34983010088, -3.05016989912, 0.0998))
if not mesh_graded:
	mesh.y = np.concatenate((mesh.y, arangeWithEndpoint(-3.34983010088,-3.05016989912,0.0998)))
mesh.z = np.delete(mesh.z, np.argwhere((mesh.z >= 0) & (mesh.z <= 0.254)))
mesh_regions['z'].append((0, 0.254, 0.254))
if not mesh_graded:
	mesh.z = np.concatenate((mesh.z, arangeWithEndpoint(0,0.254,0.254)))

## GRID - Copper_Vias_GND_29 - Copper_Vias_GND_88 (Fixed Distance)
mesh.x = np.delete(mesh.x, np.argwhere((mesh.x >= 13.0506792116) & (mesh.x <= 13.35)))
mesh_regions['x'].append((13.0506792116, 13.35, 0.0998))
if not mesh_graded:
	mesh.x = np.concatenate((mesh.x, arangeWithEndpoint(13.0506792116,13.35,0.0998)))
mesh.y = np.delete(mesh.y, np.argwhere((mesh.y >= 2.65016989912) & (mesh.y <= 2.94983010088)))
mesh_regions['y'].append((2.65016989912, 2.94983010088, 0.0998))
if not mesh_graded:
	mesh.y = np.concatenate((mesh.y, arangeWithEndpoint(2.65016989912,2.94983010088,0.0998)))
mesh.z = np.delete(mesh.z, np.argwhere((mesh.z >= 0) & (mesh.z <= 0.254)))
mesh_regions['z'].append((0, 0.254, 0.254))
if not mesh_graded:
	mesh.z = np.concatenate((mesh.z, arangeWithEndpoint(0,0.254,0.254)))

## GRID - Copper_Vias_GND_29 - Copper_Vias_GND_87 (Fixed Distance)
mesh.x = np.delete(mesh.x, np.argwhere((mesh.x >= 11.5506792116) & (mesh.x <= 11.85)))
mesh_regions['x'].append((11.5506792116, 11.85, 0.0998))
if not mesh_graded:
	mesh.x = np.concatenate((mesh.x, arangeWithEndpoint(11.5506792116,11.85,0.0998)))
mesh.y = np.delete(mesh.y, np.argwhere((mesh.y >= 2.65016989912) & (mesh.y <= 2.94983010088)))
mesh_regions['y'].append((2.65016989912, 2.94983010088, 0.0998))
if not mesh_graded:
	mesh.y = np.concatenate((mesh.y, arangeWithEndpoint(2.65016989912,2.94983010088,0.0998)))
mesh.z = np.delete(mesh.z, np.argwhere((mesh.z >= 0) & (mesh.z <= 0.254)))
mesh_regions['z'].append((0, 0.254, 0.254))
if not mesh_graded:
	mesh.z = np.concatenate((mesh.z, arangeWithEndpoint(0,0.254,0.254)))

## GRID - Copper_Vias_GND_29 - Copper_Vias_GND_86 (Fixed Distance)
mesh.x = np.delete(mesh.x, np.argwhere((mesh.x >= 10.0506792116) & (mesh.x <= 10.35)))
mesh_regions['x'].append((10.0506792116, 10.35, 0.0998))
if not mesh_graded:
	mesh.x = np.concatenate((mesh.x, arangeWithEndpoint(10.0506792116,10.35,0.0998)))
mesh.y = np.delete(mesh.y, np.argwhere((mesh.y >= 2.65016989912) & (mesh.y <= 2.94983010088)))
mesh_regions['y'].append((2.65016989912, 2.94983010088, 0.0998))
if not mesh_graded:
	mesh.y = np.concatenate((mesh.y, arangeWithEndpoint(2.65016989912,2.94983010088,0.0998)))
mesh.z = np.delete(mesh.z, np.argwhere((mesh.z >= 0) & (mesh.z <= 0.254)))
mesh_regions['z'].append((0, 0.254, 0.254))
if not mesh_graded:
	mesh.z = np.concatenate((mesh.z, arangeWithEndpoint(0,0.254,0.254)))

## GRID - Copper_Vias_GND_29 - Copper_Vias_GND_85 (Fixed Distance)
mesh.x = np.delete(mesh.x, np.argwhere((mesh.x >= 8.55067921161) & (mesh.x <= 8.85)))
mesh_regions['x'].append((8.55067921161, 8.85, 0.0998))
if not mesh_graded:
	mesh.x = np.concatenate((mesh.x, arangeWithEndpoint(8.55067921161,8.85,0.0998)))
mesh.y = np.delete(mesh.y, np.argwhere((mesh.y >= 2.65016989912) & (mesh.y <= 2.94983010088)))
mesh_regions['y'].append((2.65016989912, 2.94983010088, 0.0998))
if not mesh_graded:
	mesh.y = np.concatenate((mesh.y, arangeWithEndpoint(2.65016989912,2.94983010088,0.0998)))
mesh.z = np.delete(mesh.z, np.argwhere((mesh.z >= 0) & (mesh.z <= 0.254)))
mesh_regions['z'].append((0, 0.254, 0.254))
if not mesh_graded:
	mesh.z = np.concatenate((mesh.z, arangeWithEndpoint(0,0.254,0.254)))

## GRID - Copper_Vias_GND_29 - Copper_Vias_GND_84 (Fixed Distance)
mesh.x = np.delete(mesh.x, np.argwhere((mesh.x >= 7.05067921161) & (mesh.x <= 7.35)))
mesh_regions['x'].append((7.05067921161, 7.35, 0.0998))
if not mesh_graded:
	mesh.x = np.concatenate((mesh.x, arangeWithEndpoint(7.05067921161,7.35,0.0998)))
mesh.y = np.delete(mesh.y, np.argwhere((mesh.y >= 2.65016989912) & (mesh.y <= 2.94983010088)))
mesh_regions['y'].append((2.65016989912, 2.94983010088, 0.0998))
if not mesh_graded:
	mesh.y = np.concatenate((mesh.y, arangeWithEndpoint(2.65016989912,2.94983010088,0.0998)))
mesh.z = np.delete(mesh.z, np.argwhere((mesh.z >= 0) & (mesh.z <= 0.254)))
mesh_regions['z'].append((0, 0.254, 0.254))
if not mesh_graded:
	mesh.z = np.concatenate((mesh.z, arangeWithEndpoint(0,0.254,0.254)))

## GRID - Copper_Vias_GND_29 - Copper_Vias_GND_83 (Fixed Distance)
mesh.x = np.delete(mesh.x, np.argwhere((mesh.x >= 5.55067921161) & (mesh.x <= 5.85)))
mesh_regions['x'].append((5.55067921161, 5.85, 0.0998))
if not mesh_graded:
	mesh.x = np.concatenate((mesh.x, arangeWithEndpoint(5.55067921161,5.85,0.0998)))
mesh.y = np.delete(mesh.y, np.argwhere((mesh.y >= 2.65016989912) & (mesh.y <= 2.94983010088)))
mesh_regions['y'].append((2.65016989912, 2.94983010088, 0.0998))
if not mesh_graded:
	mesh.y = np.concatenate((mesh.y, arangeWithEndpoint(2.65016989912,2.94983010088,0.0998)))
mesh.z = np.delete(mesh.z, np.argwhere((mesh.z >= 0) & (mesh.z <= 0.254)))
mesh_regions['z'].append((0, 0.254, 0.254))
if not mesh_graded:
	mesh.z = np.concatenate((mesh.z, arangeWithEndpoint(0,0.254,0.254)))

## GRID - Copper_Vias_GND_29 - Copper_Vias_GND_82 (Fixed Distance)
mesh.x = np.delete(mesh.x, np.argwhere((mesh.x >= 4.05067921161) & (mesh.x <= 4.35)))
mesh_regions['x'].append((4.05067921161, 4.35, 0.0998))
if not mesh_graded:
	mesh.x = np.concatenate((mesh.x, arangeWithEndpoint(4.05067921161,4.35,0.0998)))
mesh.y = np.delete(mesh.y, np.argwhere((mesh.y >= 2.65016989912) & (mesh.y <= 2.94983010088)))
mesh_regions['y'].append((2.65016989912, 2.94983010088, 0.0998))
if not mesh_graded:
	mesh.y = np.concatenate((mesh.y, arangeWithEndpoint(2.65016989912,2.94983010088,0.0998)))
mesh.z = np.delete(mesh.z, np.argwhere((mesh.z >= 0) & (mesh.z <= 0.254)))
mesh_regions['z'].append((0, 0.254, 0.254))
if not mesh_graded:
	mesh.z = np.concatenate((mesh.z, arangeWithEndpoint(0,0.254,0.254)))

## GRID - Copper_Vias_GND_29 - Copper_Vias_GND_81 (Fixed Distance)
mesh.x = np.delete(mesh.x, np.argwhere((mesh.x >= -4.94932078839) & (mesh.x <= -4.65)))
mesh_regions['x'].append((-4.94932078839, -4.65, 0.0998))
if not mesh_graded:
	mesh.x = np.concatenate((mesh.x, arangeWithEndpoint(-4.94932078839,-4.65,0.0998)))
mesh.y = np.delete(mesh.y, np.argwhere((mesh.y >= 2.65016989912) & (mesh.y <= 2.94983010088)))
mesh_regions['y'].append((2.65016989912, 2.94983010088, 0.0998))
if not mesh_graded:
	mesh.y = np.concatenate((mesh.y, arangeWithEndpoint(2.65016989912,2.94983010088,0.0998)))
mesh.z = np.delete(mesh.z, np.argwhere((mesh.z >= 0) & (mesh.z <= 0.254)))
mesh_regions['z'].append((0, 0.254, 0.254))
if not mesh_graded:
	mesh.z = np.concatenate((mesh.z, arangeWithEndpoint(0,0.254,0.254)))

## GRID - Copper_Vias_GND_29 - Copper_Vias_GND_80 (Fixed Distance)
mesh.x = np.delete(mesh.x, np.argwhere((mesh.x >= -6.44932078839) & (mesh.x <= -6.15)))
mesh_regions['x'].append((-6.44932078839, -6.15, 0.0998))
if not mesh_graded:
	mesh.x = np.concatenate((mesh.x, arangeWithEndpoint(-6.44932078839,-6.15,0.0998)))
mesh.y = np.delete(mesh.y, np.argwhere((mesh.y >= 2.65016989912) & (mesh.y <= 2.94983010088)))
mesh_regions['y'].append((2.65016989912, 2.94983010088, 0.0998))
if not mesh_graded:
	mesh.y = np.concatenate((mesh.y, arangeWithEndpoint(2.65016989912,2.94983010088,0.0998)))
mesh.z = np.delete(mesh.z, np.argwhere((mesh.z >= 0) & (mesh.z <= 0.254)))
mesh_regions['z'].append((0, 0.254, 0.254))
if not mesh_graded:
	mesh.z = np.concatenate((mesh.z, arangeWithEndpoint(0,0.254,0.254)))

## GRID - Copper_Vias_GND_29 - Copper_Vias_GND_79 (Fixed Distance)
mesh.x = np.delete(mesh.x, np.argwhere((mesh.x >= -7.94932078839) & (mesh.x <= -7.65)))
mesh_regions['x'].append((-7.94932078839, -7.65, 0.0998))
if not mesh_graded:
	mesh.x = np.concatenate((mesh.x, arangeWithEndpoint(-7.94932078839,-7.65,0.0998)))
mesh.y = np.delete(mesh.y, np.argwhere((mesh.y >= 2.65016989912) & (mesh.y <= 2.94983010088)))
mesh_regions['y'].append((2.65016989912, 2.94983010088, 0.0998))
if not mesh_graded:
	mesh.y = np.concatenate((mesh.y, arangeWithEndpoint(2.65016989912,2.94983010088,0.0998)))
mesh.z = np.delete(mesh.z, np.argwhere((mesh.z >= 0) & (mesh.z <= 0.254)))
mesh_regions['z'].append((0, 0.254, 0.254))
if not mesh_graded:
	mesh.z = np.concatenate((mesh.z, arangeWithEndpoint(0,0.254,0.254)))

## GRID - Copper_Vias_GND_29 - Copper_Vias_GND_78 (Fixed Distance)
mesh.x = np.delete(mesh.x, np.argwhere((mesh.x >= -9.44932078839) & (mesh.x <= -9.15)))
mesh_regions['x'].append((-9.44932078839, -9.15, 0.0998))
if not mesh_graded:
	mesh.x = np.concatenate((mesh.x, arangeWithEndpoint(-9.44932078839,-9.15,0.0998)))
mesh.y = np.delete(mesh.y, np.argwhere((mesh.y >= 2.65016989912) & (mesh.y <= 2.94983010088)))
mesh_regions['y'].append((2.65016989912, 2.94983010088, 0.0998))
if not mesh_graded:
	mesh.y = np.concatenate((mesh.y, arangeWithEndpoint(2.65016989912,2.94983010088,0.0998)))
mesh.z = np.delete(mesh.z, np.argwhere((mesh.z >= 0) & (mesh.z <= 0.254)))
mesh_regions['z'].append((0, 0.254, 0.254))
if not mesh_graded:
	mesh.z = np.concatenate((mesh.z, arangeWithEndpoint(0,0.254,0.254)))

## GRID - Copper_Vias_GND_29 - Copper_Vias_GND_77 (Fixed Distance)
mesh.x = np.delete(mesh.x, np.argwhere((mesh.x >= -10.9493207884) & (mesh.x <= -10.65)))
mesh_regions['x'].append((-10.9493207884, -10.65, 0.0998))
if not mesh_graded:
	mesh.x = np.concatenate((mesh.x, arangeWithEndpoint(-10.9493207884,-10.65,0.0998)))
mesh.y = np.delete(mesh.y, np.argwhere((mesh.y >= 2.65016989912) & (mesh.y <= 2.94983010088)))
mesh_regions['y'].append((2.65016989912, 2.94983010088, 0.0998))
if not mesh_graded:
	mesh.y = np.concatenate((mesh.y, arangeWithEndpoint(2.65016989912,2.94983010088,0.0998)))
mesh.z = np.delete(mesh.z, np.argwhere((mesh.z >= 0) & (mesh.z <= 0.254)))
mesh_regions['z'].append((0, 0.254, 0.254))
if not mesh_graded:
	mesh.z = np.concatenate((mesh.z, arangeWithEndpoint(0,0.254,0.254)))

## GRID - Copper_Vias_GND_29 - Copper_Vias_GND_76 (Fixed Distance)
mesh.x = np.delete(mesh.x, np.argwhere((mesh.x >= -12.4493207884) & (mesh.x <= -12.15)))
mesh_regions['x'].append((-12.4493207884, -12.15, 0.0998))
if not mesh_graded:
	mesh.x = np.concatenate((mesh.x, arangeWithEndpoint(-12.4493207884,-12.15,0.0998)))
mesh.y = np.delete(mesh.y, np.argwhere((mesh.y >= 2.65016989912) & (mesh.y <= 2.94983010088)))
mesh_regions['y'].append((2.65016989912, 2.94983010088, 0.0998))
if not mesh_graded:
	mesh.y = np.concatenate((mesh.y, arangeWithEndpoint(2.65016989912,2.94983010088,0.0998)))
mesh.z = np.delete(mesh.z, np.argwhere((mesh.z >= 0) & (mesh.z <= 0.254)))
mesh_regions['z'].append((0, 0.254, 0.254))
if not mesh_graded:
	mesh.z = np.concatenate((mesh.z, arangeWithEndpoint(0,0.254,0.254)))

## GRID - Copper_Vias_GND_29 - Copper_Vias_GND_75 (Fixed Distance)
mesh.x = np.delete(mesh.x, np.argwhere((mesh.x >= -13.9493207884) & (mesh.x <= -13.65)))
mesh_regions['x'].append((-13.9493207884, -13.65, 0.0998))
if not mesh_graded:
	mesh.x = np.concatenate((mesh.x, arangeWithEndpoint(-13.9493207884,-13.65,0.0998)))
mesh.y = np.delete(mesh.y, np.argwhere((mesh.y >= 2.65016989912) & (mesh.y <= 2.94983010088)))
mesh_regions['y'].append((2.65016989912, 2.94983010088, 0.0998))
if not mesh_graded:
	mesh.y = np.concatenate((mesh.y, arangeWithEndpoint(2.65016989912,2.94983010088,0.0998)))
mesh.z = np.delete(mesh.z, np.argwhere((mesh.z >= 0) & (mesh.z <= 0.254)))
mesh_regions['z'].append((0, 0.254, 0.254))
if not mesh_graded:
	mesh.z = np.concatenate((mesh.z, arangeWithEndpoint(0,0.254,0.254)))

## GRID - Copper_Vias_GND_29 - Copper_Vias_GND_74 (Fixed Distance)
mesh.x = np.delete(mesh.x, np.argwhere((mesh.x >= 13.0506792116) & (mesh.x <= 13.35)))
mesh_regions['x'].append((13.0506792116, 13.35, 0.0998))
if not mesh_graded:
	mesh.x = np.concatenate((mesh.x, arangeWithEndpoint(13.0506792116,13.35,0.0998)))
mesh.y = np.delete(mesh.y, np.argwhere((mesh.y >= 4.15016989912) & (mesh.y <= 4.44983010088)))
mesh_regions['y'].append((4.15016989912, 4.44983010088, 0.0998))
if not mesh_graded:
	mesh.y = np.concatenate((mesh.y, arangeWithEndpoint(4.15016989912,4.44983010088,0.0998)))
mesh.z = np.delete(mesh.z, np.argwhere((mesh.z >= 0) & (mesh.z <= 0.254)))
mesh_regions['z'].append((0, 0.254, 0.254))
if not mesh_graded:
	mesh.z = np.concatenate((mesh.z, arangeWithEndpoint(0,0.254,0.254)))

## GRID - Copper_Vias_GND_29 - Copper_Vias_GND_73 (Fixed Distance)
mesh.x = np.delete(mesh.x, np.argwhere((mesh.x >= 11.5506792116) & (mesh.x <= 11.85)))
mesh_regions['x'].append((11.5506792116, 11.85, 0.0998))
if not mesh_graded:
	mesh.x = np.concatenate((mesh.x, arangeWithEndpoint(11.5506792116,11.85,0.0998)))
mesh.y = np.delete(mesh.y, np.argwhere((mesh.y >= 4.15016989912) & (mesh.y <= 4.44983010088)))
mesh_regions['y'].append((4.15016989912, 4.44983010088, 0.0998))
if not mesh_graded:
	mesh.y = np.concatenate((mesh.y, arangeWithEndpoint(4.15016989912,4.44983010088,0.0998)))
mesh.z = np.delete(mesh.z, np.argwhere((mesh.z >= 0) & (mesh.z <= 0.254)))
mesh_regions['z'].append((0, 0.254, 0.254))
if not mesh_graded:
	mesh.z = np.concatenate((mesh.z, arangeWithEndpoint(0,0.254,0.254)))

## GRID - Copper_Vias_GND_29 - Copper_Vias_GND_72 (Fixed Distance)
mesh.x = np.delete(mesh.x, np.argwhere((mesh.x >= 10.0506792116) & (mesh.x <= 10.35)))
mesh_regions['x'].append((10.0506792116, 10.35, 0.0998))
if not mesh_graded:
	mesh.x = np.concatenate((mesh.x, arangeWithEndpoint(10.0506792116,10.35,0.0998)))
mesh.y = np.delete(mesh.y, np.argwhere((mesh.y >= 4.15016989912) & (mesh.y <= 4.44983010088)))
mesh_regions['y'].append((4.15016989912, 4.44983010088, 0.0998))
if not mesh_graded:
	mesh.y = np.concatenate((mesh.y, arangeWithEndpoint(4.15016989912,4.44983010088,0.0998)))
mesh.z = np.delete(mesh.z, np.argwhere((mesh.z >= 0) & (mesh.z <= 0.254)))
mesh_regions['z'].append((0, 0.254, 0.254))
if not mesh_graded:
	mesh.z = np.concatenate((mesh.z, arangeWithEndpoint(0,0.254,0.254)))

## GRID - Copper_Vias_GND_29 - Copper_Vias_GND_71 (Fixed Distance)
mesh.x = np.delete(mesh.x, np.argwhere((mesh.x >= 8.55067921161) & (mesh.x <= 8.85)))
mesh_regions['x'].append((8.55067921161, 8.85, 0.0998))
if not mesh_graded:
	mesh.x = np.concatenate((mesh.x, arangeWithEndpoint(8.55067921161,8.85,0.0998)))
mesh.y = np.delete(mesh.y, np.argwhere((mesh.y >= 4.15016989912) & (mesh.y <= 4.44983010088)))
mesh_regions['y'].append((4.15016989912, 4.44983010088, 0.0998))
if not mesh_graded:
	mesh.y = np.concatenate((mesh.y, arangeWithEndpoint(4.15016989912,4.44983010088,0.0998)))
mesh.z = np.delete(mesh.z, np.argwhere((mesh.z >= 0) & (mesh.z <= 0.254)))
mesh_regions['z'].append((0, 0.254, 0.254))
if not mesh_graded:
	mesh.z = np.concatenate((mesh.z, arangeWithEndpoint(0,0.254,0.254)))

## GRID - Copper_Vias_GND_29 - Copper_Vias_GND_70 (Fixed Distance)
mesh.x = np.delete(mesh.x, np.argwhere((mesh.x >= 7.05067921161) & (mesh.x <= 7.35)))
mesh_regions['x'].append((7.05067921161, 7.35, 0.0998))
if not mesh_graded:
	mesh.x = np.concatenate((mesh.x, arangeWithEndpoint(7.05067921161,7.35,0.0998)))
mesh.y = np.delete(mesh.y, np.argwhere((mesh.y >= 4.15016989912) & (mesh.y <= 4.44983010088)))
mesh_regions['y'].append((4.15016989912, 4.44983010088, 0.0998))
if not mesh_graded:
	mesh.y = np.concatenate((mesh.y, arangeWithEndpoint(4.15016989912,4.44983010088,0.0998)))
mesh.z = np.delete(mesh.z, np.argwhere((mesh.z >= 0) & (mesh.z <= 0.254)))
mesh_regions['z'].append((0, 0.254, 0.254))
if not mesh_graded:
	mesh.z = np.concatenate((mesh.z, arangeWithEndpoint(0,0.254,0.254)))

## GRID - Copper_Vias_GND_29 - Copper_Vias_GND_69 (Fixed Distance)
mesh.x = np.delete(mesh.x, np.argwhere((mesh.x >= 5.55067921161) & (mesh.x <= 5.85)))
mesh_regions['x'].append((5.55067921161, 5.85, 0.0998))
if not mesh_graded:
	mesh.x = np.concatenate((mesh.x, arangeWithEndpoint(5.55067921161,5.85,0.0998)))
mesh.y = np.delete(mesh.y, np.argwhere((mesh.y >= 4.15016989912) & (mesh.y <= 4.44983010088)))
mesh_regions['y'].append((4.15016989912, 4.44983010088, 0.0998))
if not mesh_graded:
	mesh.y = np.concatenate((mesh.y, arangeWithEndpoint(4.15016989912,4.44983010088,0.0998)))
mesh.z = np.delete(mesh.z, np.argwhere((mesh.z >= 0) & (mesh.z <= 0.254)))
mesh_regions['z'].append((0, 0.254, 0.254))
if not mesh_graded:
	mesh.z = np.concatenate((mesh.z, arangeWithEndpoint(0,0.254,0.254)))

## GRID - Copper_Vias_GND_29 - Copper_Vias_GND_68 (Fixed Distance)
mesh.x = np.delete(mesh.x, np.argwhere((mesh.x >= 4.05067921161) & (mesh.x <= 4.35)))
mesh_regions['x'].append((4.05067921161, 4.35, 0.0998))
if not mesh_graded:
	mesh.x = np.concatenate((mesh.x, arangeWithEndpoint(4.05067921161,4.35,0.0998)))
mesh.y = np.delete(mesh.y, np.argwhere((mesh.y >= 4.15016989912) & (mesh.y <= 4.44983010088)))
mesh_regions['y'].append((4.15016989912, 4.44983010088, 0.0998))
if not mesh_graded:
	mesh.y = np.concatenate((mesh.y, arangeWithEndpoint(4.15016989912,4.44983010088,0.0998)))
mesh.z = np.delete(mesh.z, np.argwhere((mesh.z >= 0) & (mesh.z <= 0.254)))
mesh_regions['z'].append((0, 0.254, 0.254))
if not mesh_graded:
	mesh.z = np.concatenate((mesh.z, arangeWithEndpoint(0,0.254,0.254)))

## GRID - Copper_Vias_GND_29 - Copper_Vias_GND_67 (Fixed Distance)
mesh.x = np.delete(mesh.x, np.argwhere((mesh.x >= 2.55067921161) & (mesh.x <= 2.85)))
mesh_regions['x'].append((2.55067921161, 2.85, 0.0998))
if not mesh_graded:
	mesh.x = np.concatenate((mesh.x, arangeWithEndpoint(2.55067921161,2.85,0.0998)))
mesh.y = np.delete(mesh.y, np.argwhere((mesh.y >= 4.15016989912) & (mesh.y <= 4.44983010088)))
mesh_regions['y'].append((4.15016989912, 4.44983010088, 0.0998))
if not mesh_graded:
	mesh.y = np.concatenate((mesh.y, arangeWithEndpoint(4.15016989912,4.44983010088,0.0998)))
mesh.z = np.delete(mesh.z, np.argwhere((mesh.z >= 0) & (mesh.z <= 0.254)))
mesh_regions['z'].append((0, 0.254, 0.254))
if not mesh_graded:
	mesh.z = np.concatenate((mesh.z, arangeWithEndpoint(0,0.254,0.254)))

## GRID - Copper_Vias_GND_29 - Copper_Vias_GND_64 (Fixed Distance)
mesh.x = np.delete(mesh.x, np.argwhere((mesh.x >= -3.44932078839) & (mesh.x <= -3.15)))
mesh_regions['x'].append((-3.44932078839, -3.15, 0.0998))
if not mesh_graded:
	mesh.x = np.concatenate((mesh.x, arangeWithEndpoint(-3.44932078839,-3.15,0.0998)))
mesh.y = np.delete(mesh.y, np.argwhere((mesh.y >= 4.15016989912) & (mesh.y <= 4.44983010088)))
mesh_regions['y'].append((4.15016989912, 4.44983010088, 0.0998))
if not mesh_graded:
	mesh.y = np.concatenate((mesh.y, arangeWithEndpoint(4.15016989912,4.44983010088,0.0998)))
mesh.z = np.delete(mesh.z, np.argwhere((mesh.z >= 0) & (mesh.z <= 0.254)))
mesh_regions['z'].append((0, 0.254, 0.254))
if not mesh_graded:
	mesh.z = np.concatenate((mesh.z, arangeWithEndpoint(0,0.254,0.254)))

## GRID - Copper_Vias_GND_29 - Copper_Vias_GND_63 (Fixed Distance)
mesh.x = np.delete(mesh.x, np.argwhere((mesh.x >= -4.94932078839) & (mesh.x <= -4.65)))
mesh_regions['x'].append((-4.94932078839, -4.65, 0.0998))
if not mesh_graded:
	mesh.x = np.concatenate((mesh.x, arangeWithEndpoint(-4.94932078839,-4.65,0.0998)))
mesh.y = np.delete(mesh.y, np.argwhere((mesh.y >= 4.15016989912) & (mesh.y <= 4.44983010088)))
mesh_regions['y'].append((4.15016989912, 4.44983010088, 0.0998))
if not mesh_graded:
	mesh.y = np.concatenate((mesh.y, arangeWithEndpoint(4.15016989912,4.44983010088,0.0998)))
mesh.z = np.delete(mesh.z, np.argwhere((mesh.z >= 0) & (mesh.z <= 0.254)))
mesh_regions['z'].append((0, 0.254, 0.254))
if not mesh_graded:
	mesh.z = np.concatenate((mesh.z, arangeWithEndpoint(0,0.254,0.254)))

## GRID - Copper_Vias_GND_29 - Copper_Vias_GND_62 (Fixed Distance)
mesh.x = np.delete(mesh.x, np.argwhere((mesh.x >= -6.44932078839) & (mesh.x <= -6.15)))
mesh_regions['x'].append((-6.44932078839, -6.15, 0.0998))
if not mesh_graded:
	mesh.x = np.concatenate((mesh.x, arangeWithEndpoint(-6.44932078839,-6.15,0.0998)))
mesh.y = np.delete(mesh.y, np.argwhere((mesh.y >= 4.15016989912) & (mesh.y <= 4.44983010088)))
mesh_regions['y'].append((4.15016989912, 4.44983010088, 0.0998))
if not mesh_graded:
	mesh.y = np.concatenate((mesh.y, arangeWithEndpoint(4.15016989912,4.44983010088,0.0998)))
mesh.z = np.delete(mesh.z, np.argwhere((mesh.z >= 0) & (mesh.z <= 0.254)))
mesh_regions['z'].append((0, 0.254, 0.254))
if not mesh_graded:
	mesh.z = np.concatenate((mesh.z, arangeWithEndpoint(0,0.254,0.254)))

## GRID - Copper_Vias_GND_29 - Copper_Vias_GND_61 (Fixed Distance)
mesh.x = np.delete(mesh.x, np.argwhere((mesh.x >= -7.94932078839) & (mesh.x <= -7.65)))
mesh_regions['x'].append((-7.94932078839, -7.65, 0.0998))
if not mesh_graded:
	mesh.x = np.concatenate((mesh.x, arangeWithEndpoint(-7.94932078839,-7.65,0.0998)))
mesh.y = np.delete(mesh.y, np.argwhere((mesh.y >= 4.15016989912) & (mesh.y <= 4.44983010088)))
mesh_regions['y'].append((4.15016989912, 4.44983010088, 0.0998))
if not mesh_graded:
	mesh.y = np.concatenate((mesh.y, arangeWithEndpoint(4.15016989912,4.44983010088,0.0998)))
mesh.z = np.delete(mesh.z, np.argwhere((mesh.z >= 0) & (mesh.z <= 0.254)))
mesh_regions['z'].append((0, 0.254, 0.254))
if not mesh_graded:
	mesh.z = np.concatenate((mesh.z, arangeWithEndpoint(0,0.254,0.254)))

## GRID - Copper_Vias_GND_29 - Copper_Vias_GND_60 (Fixed Distance)
mesh.x = np.delete(mesh.x, np.argwhere((mesh.x >= -9.44932078839) & (mesh.x <= -9.15)))
mesh_regions['x'].append((-9.44932078839, -9.15, 0.0998))
if not mesh_graded:
	mesh.x = np.concatenate((mesh.x, arangeWithEndpoint(-9.44932078839,-9.15,0.0998)))
mesh.y = np.delete(mesh.y, np.argwhere((mesh.y >= 4.15016989912) & (mesh.y <= 4.44983010088)))
mesh_regions['y'].append((4.15016989912, 4.44983010088, 0.0998))
if not mesh_graded:
	mesh.y = np.concatenate((mesh.y, arangeWithEndpoint(4.15016989912,4.44983010088,0.0998)))
mesh.z = np.delete(mesh.z, np.argwhere((mesh.z >= 0) & (mesh.z <= 0.254)))
mesh_regions['z'].append((0, 0.254, 0.254))
if not mesh_graded:
	mesh.z = np.concatenate((mesh.z, arangeWithEndpoint(0,0.254,0.254)))

## GRID - Copper_Vias_GND_29 - Copper_Vias_GND_59 (Fixed Distance)
mesh.x = np.delete(mesh.x, np.argwhere((mesh.x >= -10.9493207884) & (mesh.x <= -10.65)))
mesh_regions['x'].append((-10.9493207884, -10.65, 0.0998))
if not mesh_graded:
	mesh.x = np.concatenate((mesh.x, arangeWithEndpoint(-10.9493207884,-10.65,0.0998)))
mesh.y = np.delete(mesh.y, np.argwhere((mesh.y >= 4.15016989912) & (mesh.y <= 4.44983010088)))
mesh_regions['y'].append((4.15016989912, 4.44983010088, 0.0998))
if not mesh_graded:
	mesh.y = np.concatenate((mesh.y, arangeWithEndpoint(4.15016989912,4.44983010088,0.0998)))
mesh.z = np.delete(mesh.z, np.argwhere((mesh.z >= 0) & (mesh.z <= 0.254)))
mesh_regions['z'].append((0, 0.254, 0.254))
if not mesh_graded:
	mesh.z = np.concatenate((mesh.z, arangeWithEndpoint(0,0.254,0.254)))

## GRID - Copper_Vias_GND_29 - Copper_Vias_GND_58 (Fixed Distance)
mesh.x = np.delete(mesh.x, np.argwhere((mesh.x >= -12.4493207884) & (mesh.x <= -12.15)))
mesh_regions['x'].append((-12.4493207884, -12.15, 0.0998))
if not mesh_graded:
	mesh.x = np.concatenate((mesh.x, arangeWithEndpoint(-12.4493207884,-12.15,0.0998)))
mesh.y = np.delete(mesh.y, np.argwhere((mesh.y >= 4.15016989912) & (mesh.y <= 4.44983010088)))
mesh_regions['y'].append((4.15016989912, 4.44983010088, 0.0998))
if not mesh_graded:
	mesh.y = np.concatenate((mesh.y, arangeWithEndpoint(4.15016989912,4.44983010088,0.0998)))
mesh.z = np.delete(mesh.z, np.argwhere((mesh.z >= 0) & (mesh.z <= 0.254)))
mesh_regions['z'].append((0, 0.254, 0.254))
if not mesh_graded:
	mesh.z = np.concatenate((mesh.z, arangeWithEndpoint(0,0.254,0.254)))

## GRID - Copper_Vias_GND_29 - Copper_Vias_GND_57 (Fixed Distance)
mesh.x = np.delete(mesh.x, np.argwhere((mesh.x >= -13.9493207884) & (mesh.x <= -13.65)))
mesh_regions['x'].append((-13.9493207884, -13.65, 0.0998))
if not mesh_graded:
	mesh.x = np.concatenate((mesh.x, arangeWithEndpoint(-13.9493207884,-13.65,0.0998)))
mesh.y = np.delete(mesh.y, np.argwhere((mesh.y >= 4.15016989912) & (mesh.y <= 4.44983010088)))
mesh_regions['y'].append((4.15016989912, 4.44983010088, 0.0998))
if not mesh_graded:
	mesh.y = np.concatenate((mesh.y, arangeWithEndpoint(4.15016989912,4.44983010088,0.0998)))
mesh.z = np.delete(mesh.z, np.argwhere((mesh.z >= 0) & (mesh.z <= 0.254)))
mesh_regions['z'].append((0, 0.254, 0.254))
if not mesh_graded:
	mesh.z = np.concatenate((mesh.z, arangeWithEndpoint(0,0.254,0.254)))

## GRID - Copper_Vias_GND_29 - Copper_Vias_GND_66 (Fixed Distance)
mesh.x = np.delete(mesh.x, np.argwhere((mesh.x >= 1.05067921161) & (mesh.x <= 1.35)))
mesh_regions['x'].append((1.05067921161, 1.35, 0.0998))
if not mesh_graded:
	mesh.x = np.concatenate((mesh.x, arangeWithEndpoint(1.05067921161,1.35,0.0998)))
mesh.y = np.delete(mesh.y, np.argwhere((mesh.y >= 4.15016989912) & (mesh.y <= 4.44983010088)))
mesh_regions['y'].append((4.15016989912, 4.44983010088, 0.0998))
if not mesh_graded:
	mesh.y = np.concatenate((mesh.y, arangeWithEndpoint(4.15016989912,4.44983010088,0.0998)))
mesh.z = np.delete(mesh.z, np.argwhere((mesh.z >= 0) & (mesh.z <= 0.254)))
mesh_regions['z'].append((0, 0.254, 0.254))
if not mesh_graded:
	mesh.z = np.concatenate((mesh.z, arangeWithEndpoint(0,0.254,0.254)))

## GRID - Copper_Vias_GND_29 - Copper_Vias_GND_65 (Fixed Distance)
mesh.x = np.delete(mesh.x, np.argwhere((mesh.x >= -1.94932078839) & (mesh.x <= -1.65)))
mesh_regions['x'].append((-1.94932078839, -1.65, 0.0998))
if not mesh_graded:
	mesh.x = np.concatenate((mesh.x, arangeWithEndpoint(-1.94932078839,-1.65,0.0998)))
mesh.y = np.delete(mesh.y, np.argwhere((mesh.y >= 4.15016989912) & (mesh.y <= 4.44983010088)))
mesh_regions['y'].append((4.15016989912, 4.44983010088, 0.0998))
if not mesh_graded:
	mesh.y = np.concatenate((mesh.y, arangeWithEndpoint(4.15016989912,4.44983010088,0.0998)))
mesh.z = np.delete(mesh.z, np.argwhere((mesh.z >= 0) & (mesh.z <= 0.254)))
mesh_regions['z'].append((0, 0.254, 0.254))
if not mesh_graded:
	mesh.z = np.concatenate((mesh.z, arangeWithEndpoint(0,0.254,0.254)))

## GRID - Copper_Vias_GND_29 - Copper_Vias_GND_14 (Fixed Distance)
mesh.x = np.delete(mesh.x, np.argwhere((mesh.x >= 1.20067921161) & (mesh.x <= 1.5)))
mesh_regions['x'].append((1.20067921161, 1.5, 0.0998))
if not mesh_graded:
	mesh.x = np.concatenate((mesh.x, arangeWithEndpoint(1.20067921161,1.5,0.0998)))
mesh.y = np.delete(mesh.y, np.argwhere((mesh.y >= 0.300169899123) & (mesh.y <= 0.599830100877)))
mesh_regions['y'].append((0.300169899123, 0.599830100877, 0.0998))
if not mesh_graded:
	mesh.y = np.concatenate((mesh.y, arangeWithEndpoint(0.300169899123,0.599830100877,0.0998)))
mesh.z = np.delete(mesh.z, np.argwhere((mesh.z >= 0) & (mesh.z <= 0.254)))
mesh_regions['z'].append((0, 0.254, 0.254))
if not mesh_graded:
	mesh.z = np.concatenate((mesh.z, arangeWithEndpoint(0,0.254,0.254)))

## GRID - Copper_Vias_GND_29 - Copper_Vias_GND_15 (Fixed Distance)
mesh.x = np.delete(mesh.x, np.argwhere((mesh.x >= 1.20067921161) & (mesh.x <= 1.5)))
mesh_regions['x'].append((1.20067921161, 1.5, 0.0998))
if not mesh_graded:
	mesh.x = np.concatenate((mesh.x, arangeWithEndpoint(1.20067921161,1.5,0.0998)))
mesh.y = np.delete(mesh.y, np.argwhere((mesh.y >= -0.599830100877) & (mesh.y <= -0.300169899123)))
mesh_regions['y'].append((-0.599830100877, -0.300169899123, 0.0998))
if not mesh_graded:
	mesh.y = np.concatenate((mesh.y, arangeWithEndpoint(-0.599830100877,-0.300169899123,0.0998)))
mesh.z = np.delete(mesh.z, np.argwhere((mesh.z >= 0) & (mesh.z <= 0.254)))
mesh_regions['z'].append((0, 0.254, 0.254))
if not mesh_graded:
	mesh.z = np.concatenate((mesh.z, arangeWithEndpoint(0,0.254,0.254)))

## GRID - Copper_Vias_GND_29 - Copper_Vias_GND_89 (Fixed Distance)
mesh.x = np.delete(mesh.x, np.argwhere((mesh.x >= -13.9493207884) & (mesh.x <= -13.65)))
mesh_regions['x'].append((-13.9493207884, -13.65, 0.0998))
if not mesh_graded:
	mesh.x = np.concatenate((mesh.x, arangeWithEndpoint(-13.9493207884,-13.65,0.0998)))
mesh.y = np.delete(mesh.y, np.argwhere((mesh.y >= 1.15016989912) & (mesh.y <= 1.44983010088)))
mesh_regions['y'].append((1.15016989912, 1.44983010088, 0.0998))
if not mesh_graded:
	mesh.y = np.concatenate((mesh.y, arangeWithEndpoint(1.15016989912,1.44983010088,0.0998)))
mesh.z = np.delete(mesh.z, np.argwhere((mesh.z >= 0) & (mesh.z <= 0.254)))
mesh_regions['z'].append((0, 0.254, 0.254))
if not mesh_graded:
	mesh.z = np.concatenate((mesh.z, arangeWithEndpoint(0,0.254,0.254)))

## GRID - Copper_Vias_GND_29 - Copper_Vias_GND_56 (Fixed Distance)
mesh.x = np.delete(mesh.x, np.argwhere((mesh.x >= 4.05067921161) & (mesh.x <= 4.35)))
mesh_regions['x'].append((4.05067921161, 4.35, 0.0998))
if not mesh_graded:
	mesh.x = np.concatenate((mesh.x, arangeWithEndpoint(4.05067921161,4.35,0.0998)))
mesh.y = np.delete(mesh.y, np.argwhere((mesh.y >= -1.24983010088) & (mesh.y <= -0.950169899123)))
mesh_regions['y'].append((-1.24983010088, -0.950169899123, 0.0998))
if not mesh_graded:
	mesh.y = np.concatenate((mesh.y, arangeWithEndpoint(-1.24983010088,-0.950169899123,0.0998)))
mesh.z = np.delete(mesh.z, np.argwhere((mesh.z >= 0) & (mesh.z <= 0.254)))
mesh_regions['z'].append((0, 0.254, 0.254))
if not mesh_graded:
	mesh.z = np.concatenate((mesh.z, arangeWithEndpoint(0,0.254,0.254)))

## GRID - Copper_Vias_GND_29 - Copper_Vias_GND_55 (Fixed Distance)
mesh.x = np.delete(mesh.x, np.argwhere((mesh.x >= 4.05067921161) & (mesh.x <= 4.35)))
mesh_regions['x'].append((4.05067921161, 4.35, 0.0998))
if not mesh_graded:
	mesh.x = np.concatenate((mesh.x, arangeWithEndpoint(4.05067921161,4.35,0.0998)))
mesh.y = np.delete(mesh.y, np.argwhere((mesh.y >= 0.450169899123) & (mesh.y <= 0.749830100877)))
mesh_regions['y'].append((0.450169899123, 0.749830100877, 0.0998))
if not mesh_graded:
	mesh.y = np.concatenate((mesh.y, arangeWithEndpoint(0.450169899123,0.749830100877,0.0998)))
mesh.z = np.delete(mesh.z, np.argwhere((mesh.z >= 0) & (mesh.z <= 0.254)))
mesh_regions['z'].append((0, 0.254, 0.254))
if not mesh_graded:
	mesh.z = np.concatenate((mesh.z, arangeWithEndpoint(0,0.254,0.254)))

## GRID - Copper_Vias_GND_29 - Copper_Vias_GND_54 (Fixed Distance)
mesh.x = np.delete(mesh.x, np.argwhere((mesh.x >= 5.05067921161) & (mesh.x <= 5.35)))
mesh_regions['x'].append((5.05067921161, 5.35, 0.0998))
if not mesh_graded:
	mesh.x = np.concatenate((mesh.x, arangeWithEndpoint(5.05067921161,5.35,0.0998)))
mesh.y = np.delete(mesh.y, np.argwhere((mesh.y >= -1.24983010088) & (mesh.y <= -0.950169899123)))
mesh_regions['y'].append((-1.24983010088, -0.950169899123, 0.0998))
if not mesh_graded:
	mesh.y = np.concatenate((mesh.y, arangeWithEndpoint(-1.24983010088,-0.950169899123,0.0998)))
mesh.z = np.delete(mesh.z, np.argwhere((mesh.z >= 0) & (mesh.z <= 0.254)))
mesh_regions['z'].append((0, 0.254, 0.254))
if not mesh_graded:
	mesh.z = np.concatenate((mesh.z, arangeWithEndpoint(0,0.254,0.254)))

## GRID - Copper_Vias_GND_29 - Copper_Vias_GND_53 (Fixed Distance)
mesh.x = np.delete(mesh.x, np.argwhere((mesh.x >= 5.05067921161) & (mesh.x <= 5.35)))
mesh_regions['x'].append((5.05067921161, 5.35, 0.0998))
if not mesh_graded:
	mesh.x = np.concatenate((mesh.x, arangeWithEndpoint(5.05067921161,5.35,0.0998)))
mesh.y = np.delete(mesh.y, np.argwhere((mesh.y >= 0.450169899123) & (mesh.y <= 0.749830100877)))
mesh_regions['y'].append((0.450169899123, 0.749830100877, 0.0998))
if not mesh_graded:
	mesh.y = np.concatenate((mesh.y, arangeWithEndpoint(0.450169899123,0.749830100877,0.0998)))
mesh.z = np.delete(mesh.z, np.argwhere((mesh.z >= 0) & (mesh.z <= 0.254)))
mesh_regions['z'].append((0, 0.254, 0.254))
if not mesh_graded:
	mesh.z = np.concatenate((mesh.z, arangeWithEndpoint(0,0.254,0.254)))

## GRID - Copper_Vias_GND_29 - Copper_Vias_GND_52 (Fixed Distance)
mesh.x = np.delete(mesh.x, np.argwhere((mesh.x >= 6.05067921161) & (mesh.x <= 6.35)))
mesh_regions['x'].append((6.05067921161, 6.35, 0.0998))
if not mesh_graded:
	mesh.x = np.concatenate((mesh.x, arangeWithEndpoint(6.05067921161,6.35,0.0998)))
mesh.y = np.delete(mesh.y, np.argwhere((mesh.y >= -1.24983010088) & (mesh.y <= -0.950169899123)))
mesh_regions['y'].append((-1.24983010088, -0.950169899123, 0.0998))
if not mesh_graded:
	mesh.y = np.concatenate((mesh.y, arangeWithEndpoint(-1.24983010088,-0.950169899123,0.0998)))
mesh.z = np.delete(mesh.z, np.argwhere((mesh.z >= 0) & (mesh.z <= 0.254)))
mesh_regions['z'].append((0, 0.254, 0.254))
if not mesh_graded:
	mesh.z = np.concatenate((mesh.z, arangeWithEndpoint(0,0.254,0.254)))

## GRID - Copper_Vias_GND_29 - Copper_Vias_GND_51 (Fixed Distance)
mesh.x = np.delete(mesh.x, np.argwhere((mesh.x >= 6.05067921161) & (mesh.x <= 6.35)))
mesh_regions['x'].append((6.05067921161, 6.35, 0.0998))
if not mesh_graded:
	mesh.x = np.concatenate((mesh.x, arangeWithEndpoint(6.05067921161,6.35,0.0998)))
mesh.y = np.delete(mesh.y, np.argwhere((mesh.y >= 0.450169899123) & (mesh.y <= 0.749830100877)))
mesh_regions['y'].append((0.450169899123, 0.749830100877, 0.0998))
if not mesh_graded:
	mesh.y = np.concatenate((mesh.y, arangeWithEndpoint(0.450169899123,0.749830100877,0.0998)))
mesh.z = np.delete(mesh.z, np.argwhere((mesh.z >= 0) & (mesh.z <= 0.254)))
mesh_regions['z'].append((0, 0.254, 0.254))
if not mesh_graded:
	mesh.z = np.concatenate((mesh.z, arangeWithEndpoint(0,0.254,0.254)))

## GRID - Copper_Vias_GND_29 - Copper_Vias_GND_50 (Fixed Distance)
mesh.x = np.delete(mesh.x, np.argwhere((mesh.x >= 7.05067921161) & (mesh.x <= 7.35)))
mesh_regions['x'].append((7.05067921161, 7.35, 0.0998))
if not mesh_graded:
	mesh.x = np.concatenate((mesh.x, arangeWithEndpoint(7.05067921161,7.35,0.0998)))
mesh.y = np.delete(mesh.y, np.argwhere((mesh.y >= -1.24983010088) & (mesh.y <= -0.950169899123)))
mesh_regions['y'].append((-1.24983010088, -0.950169899123, 0.0998))
if not mesh_graded:
	mesh.y = np.concatenate((mesh.y, arangeWithEndpoint(-1.24983010088,-0.950169899123,0.0998)))
mesh.z = np.delete(mesh.z, np.argwhere((mesh.z >= 0) & (mesh.z <= 0.254)))
mesh_regions['z'].append((0, 0.254, 0.254))
if not mesh_graded:
	mesh.z = np.concatenate((mesh.z, arangeWithEndpoint(0,0.254,0.254)))

## GRID - Copper_Vias_GND_29 - Copper_Vias_GND_49 (Fixed Distance)
mesh.x = np.delete(mesh.x, np.argwhere((mesh.x >= 7.05067921161) & (mesh.x <= 7.35)))
mesh_regions['x'].append((7.05067921161, 7.35, 0.0998))
if not mesh_graded:
	mesh.x = np.concatenate((mesh.x, arangeWithEndpoint(7.05067921161,7.35,0.0998)))
mesh.y = np.delete(mesh.y, np.argwhere((mesh.y >= 0.450169899123) & (mesh.y <= 0.749830100877)))
mesh_regions['y'].append((0.450169899123, 0.749830100877, 0.0998))
if not mesh_graded:
	mesh.y = np.concatenate((mesh.y, arangeWithEndpoint(0.450169899123,0.749830100877,0.0998)))
mesh.z = np.delete(mesh.z, np.argwhere((mesh.z >= 0) & (mesh.z <= 0.254)))
mesh_regions['z'].append((0, 0.254, 0.254))
if not mesh_graded:
	mesh.z = np.concatenate((mesh.z, arangeWithEndpoint(0,0.254,0.254)))

## GRID - Copper_Vias_GND_29 - Copper_Vias_GND_48 (Fixed Distance)
mesh.x = np.delete(mesh.x, np.argwhere((mesh.x >= 8.05067921161) & (mesh.x <= 8.35)))
mesh_regions['x'].append((8.05067921161, 8.35, 0.0998))
if not mesh_graded:
	mesh.x = np.concatenate((mesh.x, arangeWithEndpoint(8.05067921161,8.35,0.0998)))
mesh.y = np.delete(mesh.y, np.argwhere((mesh.y >= -1.24983010088) & (mesh.y <= -0.950169899123)))
mesh_regions['y'].append((-1.24983010088, -0.950169899123, 0.0998))
if not mesh_graded:
	mesh.y = np.concatenate((mesh.y, arangeWithEndpoint(-1.24983010088,-0.950169899123,0.0998)))
mesh.z = np.delete(mesh.z, np.argwhere((mesh.z >= 0) & (mesh.z <= 0.254)))
mesh_regions['z'].append((0, 0.254, 0.254))
if not mesh_graded:
	mesh.z = np.concatenate((mesh.z, arangeWithEndpoint(0,0.254,0.254)))

## GRID - Copper_Vias_GND_29 - Copper_Vias_GND_47 (Fixed Distance)
mesh.x = np.delete(mesh.x, np.argwhere((mesh.x >= 8.05067921161) & (mesh.x <= 8.35)))
mesh_regions['x'].append((8.05067921161, 8.35, 0.0998))
if not mesh_graded:
	mesh.x = np.concatenate((mesh.x, arangeWithEndpoint(8.05067921161,8.35,0.0998)))
mesh.y = np.delete(mesh.y, np.argwhere((mesh.y >= 0.450169899123) & (mesh.y <= 0.749830100877)))
mesh_regions['y'].append((0.450169899123, 0.749830100877, 0.0998))
if not mesh_graded:
	mesh.y = np.concatenate((mesh.y, arangeWithEndpoint(0.450169899123,0.749830100877,0.0998)))
mesh.z = np.delete(mesh.z, np.argwhere((mesh.z >= 0) & (mesh.z <= 0.254)))
mesh_regions['z'].append((0, 0.254, 0.254))
if not mesh_graded:
	mesh.z = np.concatenate((mesh.z, arangeWithEndpoint(0,0.254,0.254)))

## GRID - Copper_Vias_GND_29 - Copper_Vias_GND_46 (Fixed Distance)
mesh.x = np.delete(mesh.x, np.argwhere((mesh.x >= 9.05067921161) & (mesh.x <= 9.35)))
mesh_regions['x'].append((9.05067921161, 9.35, 0.0998))
if not mesh_graded:
	mesh.x = np.concatenate((mesh.x, arangeWithEndpoint(9.05067921161,9.35,0.0998)))
mesh.y = np.delete(mesh.y, np.argwhere((mesh.y >= -1.24983010088) & (mesh.y <= -0.950169899123)))
mesh_regions['y'].append((-1.24983010088, -0.950169899123, 0.0998))
if not mesh_graded:
	mesh.y = np.concatenate((mesh.y, arangeWithEndpoint(-1.24983010088,-0.950169899123,0.0998)))
mesh.z = np.delete(mesh.z, np.argwhere((mesh.z >= 0) & (mesh.z <= 0.254)))
mesh_regions['z'].append((0, 0.254, 0.254))
if not mesh_graded:
	mesh.z = np.concatenate((mesh.z, arangeWithEndpoint(0,0.254,0.254)))

## GRID - Copper_Vias_GND_29 - Copper_Vias_GND_45 (Fixed Distance)
mesh.x = np.delete(mesh.x, np.argwhere((mesh.x >= 9.05067921161) & (mesh.x <= 9.35)))
mesh_regions['x'].append((9.05067921161, 9.35, 0.0998))
if not mesh_graded:
	mesh.x = np.concatenate((mesh.x, arangeWithEndpoint(9.05067921161,9.35,0.0998)))
mesh.y = np.delete(mesh.y, np.argwhere((mesh.y >= 0.450169899123) & (mesh.y <= 0.749830100877)))
mesh_regions['y'].append((0.450169899123, 0.749830100877, 0.0998))
if not mesh_graded:
	mesh.y = np.concatenate((mesh.y, arangeWithEndpoint(0.450169899123,0.749830100877,0.0998)))
mesh.z = np.delete(mesh.z, np.argwhere((mesh.z >= 0) & (mesh.z <= 0.254)))
mesh_regions['z'].append((0, 0.254, 0.254))
if not mesh_graded:
	mesh.z = np.concatenate((mesh.z, arangeWithEndpoint(0,0.254,0.254)))

## GRID - Copper_Vias_GND_29 - Copper_Vias_GND_44 (Fixed Distance)
mesh.x = np.delete(mesh.x, np.argwhere((mesh.x >= 10.0506792116) & (mesh.x <= 10.35)))
mesh_regions['x'].append((10.0506792116, 10.35, 0.0998))
if not mesh_graded:
	mesh.x = np.concatenate((mesh.x, arangeWithEndpoint(10.0506792116,10.35,0.0998)))
mesh.y = np.delete(mesh.y, np.argwhere((mesh.y >= -1.24983010088) & (mesh.y <= -0.950169899123)))
mesh_regions['y'].append((-1.24983010088, -0.950169899123, 0.0998))
if not mesh_graded:
	mesh.y = np.concatenate((mesh.y, arangeWithEndpoint(-1.24983010088,-0.950169899123,0.0998)))
mesh.z = np.delete(mesh.z, np.argwhere((mesh.z >= 0) & (mesh.z <= 0.254)))
mesh_regions['z'].append((0, 0.254, 0.254))
if not mesh_graded:
	mesh.z = np.concatenate((mesh.z, arangeWithEndpoint(0,0.254,0.254)))

## GRID - Copper_Vias_GND_29 - Copper_Vias_GND_43 (Fixed Distance)
mesh.x = np.delete(mesh.x, np.argwhere((mesh.x >= 10.0506792116) & (mesh.x <= 10.35)))
mesh_regions['x'].append((10.0506792116, 10.35, 0.0998))
if not mesh_graded:
	mesh.x = np.concatenate((mesh.x, arangeWithEndpoint(10.0506792116,10.35,0.0998)))
mesh.y = np.delete(mesh.y, np.argwhere((mesh.y >= 0.450169899123) & (mesh.y <= 0.749830100877)))
mesh_regions['y'].append((0.450169899123, 0.749830100877, 0.0998))
if not mesh_graded:
	mesh.y = np.concatenate((mesh.y, arangeWithEndpoint(0.450169899123,0.749830100877,0.0998)))
mesh.z = np.delete(mesh.z, np.argwhere((mesh.z >= 0) & (mesh.z <= 0.254)))
mesh_regions['z'].append((0, 0.254, 0.254))
if not mesh_graded:
	mesh.z = np.concatenate((mesh.z, arangeWithEndpoint(0,0.254,0.254)))

## GRID - Copper_Vias_GND_29 - Copper_Vias_GND_42 (Fixed Distance)
mesh.x = np.delete(mesh.x, np.argwhere((mesh.x >= 11.0506792116) & (mesh.x <= 11.35)))
mesh_regions['x'].append((11.0506792116, 11.35, 0.0998))
if not mesh_graded:
	mesh.x = np.concatenate((mesh.x, arangeWithEndpoint(11.0506792116,11.35,0.0998)))
mesh.y = np.delete(mesh.y, np.argwhere((mesh.y >= -1.24983010088) & (mesh.y <= -0.950169899123)))
mesh_regions['y'].append((-1.24983010088, -0.950169899123, 0.0998))
if not mesh_graded:
	mesh.y = np.concatenate((mesh.y, arangeWithEndpoint(-1.24983010088,-0.950169899123,0.0998)))
mesh.z = np.delete(mesh.z, np.argwhere((mesh.z >= 0) & (mesh.z <= 0.254)))
mesh_regions['z'].append((0, 0.254, 0.254))
if not mesh_graded:
	mesh.z = np.concatenate((mesh.z, arangeWithEndpoint(0,0.254,0.254)))

## GRID - Copper_Vias_GND_29 - Copper_Vias_GND_41 (Fixed Distance)
mesh.x = np.delete(mesh.x, np.argwhere((mesh.x >= 11.0506792116) & (mesh.x <= 11.35)))
mesh_regions['x'].append((11.0506792116, 11.35, 0.0998))
if not mesh_graded:
	mesh.x = np.concatenate((mesh.x, arangeWithEndpoint(11.0506792116,11.35,0.0998)))
mesh.y = np.delete(mesh.y, np.argwhere((mesh.y >= 0.450169899123) & (mesh.y <= 0.749830100877)))
mesh_regions['y'].append((0.450169899123, 0.749830100877, 0.0998))
if not mesh_graded:
	mesh.y = np.concatenate((mesh.y, arangeWithEndpoint(0.450169899123,0.749830100877,0.0998)))
mesh.z = np.delete(mesh.z, np.argwhere((mesh.z >= 0) & (mesh.z <= 0.254)))
mesh_regions['z'].append((0, 0.254, 0.254))
if not mesh_graded:
	mesh.z = np.concatenate((mesh.z, arangeWithEndpoint(0,0.254,0.254)))

## GRID - Copper_Vias_GND_29 - Copper_Vias_GND_40 (Fixed Distance)
mesh.x = np.delete(mesh.x, np.argwhere((mesh.x >= 12.0506792116) & (mesh.x <= 12.35)))
mesh_regions['x'].append((12.0506792116, 12.35, 0.0998))
if not mesh_graded:
	mesh.x = np.concatenate((mesh.x, arangeWithEndpoint(12.0506792116,12.35,0.0998)))
mesh.y = np.delete(mesh.y, np.argwhere((mesh.y >= -1.24983010088) & (mesh.y <= -0.950169899123)))
mesh_regions['y'].append((-1.24983010088, -0.950169899123, 0.0998))
if not mesh_graded:
	mesh.y = np.concatenate((mesh.y, arangeWithEndpoint(-1.24983010088,-0.950169899123,0.0998)))
mesh.z = np.delete(mesh.z, np.argwhere((mesh.z >= 0) & (mesh.z <= 0.254)))
mesh_regions['z'].append((0, 0.254, 0.254))
if not mesh_graded:
	mesh.z = np.concatenate((mesh.z, arangeWithEndpoint(0,0.254,0.254)))

## GRID - Copper_Vias_GND_29 - Copper_Vias_GND_39 (Fixed Distance)
mesh.x = np.delete(mesh.x, np.argwhere((mesh.x >= 12.0506792116) & (mesh.x <= 12.35)))
mesh_regions['x'].append((12.0506792116, 12.35, 0.0998))
if not mesh_graded:
	mesh.x = np.concatenate((mesh.x, arangeWithEndpoint(12.0506792116,12.35,0.0998)))
mesh.y = np.delete(mesh.y, np.argwhere((mesh.y >= 0.450169899123) & (mesh.y <= 0.749830100877)))
mesh_regions['y'].append((0.450169899123, 0.749830100877, 0.0998))
if not mesh_graded:
	mesh.y = np.concatenate((mesh.y, arangeWithEndpoint(0.450169899123,0.749830100877,0.0998)))
mesh.z = np.delete(mesh.z, np.argwhere((mesh.z >= 0) & (mesh.z <= 0.254)))
mesh_regions['z'].append((0, 0.254, 0.254))
if not mesh_graded:
	mesh.z = np.concatenate((mesh.z, arangeWithEndpoint(0,0.254,0.254)))

## GRID - Copper_Vias_GND_29 - Copper_Vias_GND_38 (Fixed Distance)
mesh.x = np.delete(mesh.x, np.argwhere((mesh.x >= 13.0506792116) & (mesh.x <= 13.35)))
mesh_regions['x'].append((13.0506792116, 13.35, 0.0998))
if not mesh_graded:
	mesh.x = np.concatenate((mesh.x, arangeWithEndpoint(13.0506792116,13.35,0.0998)))
mesh.y = np.delete(mesh.y, np.argwhere((mesh.y >= -1.24983010088) & (mesh.y <= -0.950169899123)))
mesh_regions['y'].append((-1.24983010088, -0.950169899123, 0.0998))
if not mesh_graded:
	mesh.y = np.concatenate((mesh.y, arangeWithEndpoint(-1.24983010088,-0.950169899123,0.0998)))
mesh.z = np.delete(mesh.z, np.argwhere((mesh.z >= 0) & (mesh.z <= 0.254)))
mesh_regions['z'].append((0, 0.254, 0.254))
if not mesh_graded:
	mesh.z = np.concatenate((mesh.z, arangeWithEndpoint(0,0.254,0.254)))

## GRID - Copper_Vias_GND_29 - Copper_Vias_GND_37 (Fixed Distance)
mesh.x = np.delete(mesh.x, np.argwhere((mesh.x >= 13.0506792116) & (mesh.x <= 13.35)))
mesh_regions['x'].append((13.0506792116, 13.35, 0.0998))
if not mesh_graded:
	mesh.x = np.concatenate((mesh.x, arangeWithEndpoint(13.0506792116,13.35,0.0998)))
mesh.y = np.delete(mesh.y, np.argwhere((mesh.y >= 0.450169899123) & (mesh.y <= 0.749830100877)))
mesh_regions['y'].append((0.450169899123, 0.749830100877, 0.0998))
if not mesh_graded:
	mesh.y = np.concatenate((mesh.y, arangeWithEndpoint(0.450169899123,0.749830100877,0.0998)))
mesh.z = np.delete(mesh.z, np.argwhere((mesh.z >= 0) & (mesh.z <= 0.254)))
mesh_regions['z'].append((0, 0.254, 0.254))
if not mesh_graded:
	mesh.z = np.concatenate((mesh.z, arangeWithEndpoint(0,0.254,0.254)))

## GRID - Copper_Vias_GND_29 - Copper_Vias_GND_16 (Fixed Distance)
mesh.x = np.delete(mesh.x, np.argwhere((mesh.x >= 1.20067921161) & (mesh.x <= 1.5)))
mesh_regions['x'].append((1.20067921161, 1.5, 0.0998))
if not mesh_graded:
	mesh.x = np.concatenate((mesh.x, arangeWithEndpoint(1.20067921161,1.5,0.0998)))
mesh.y = np.delete(mesh.y, np.argwhere((mesh.y >= -1.49983010088) & (mesh.y <= -1.20016989912)))
mesh_regions['y'].append((-1.49983010088, -1.20016989912, 0.0998))
if not mesh_graded:
	mesh.y = np.concatenate((mesh.y, arangeWithEndpoint(-1.49983010088,-1.20016989912,0.0998)))
mesh.z = np.delete(mesh.z, np.argwhere((mesh.z >= 0) & (mesh.z <= 0.254)))
mesh_regions['z'].append((0, 0.254, 0.254))
if not mesh_graded:
	mesh.z = np.concatenate((mesh.z, arangeWithEndpoint(0,0.254,0.254)))

## GRID - Copper_Vias_GND_29 - Copper_Vias_GND_13 (Fixed Distance)
mesh.x = np.delete(mesh.x, np.argwhere((mesh.x >= 1.20067921161) & (mesh.x <= 1.5)))
mesh_regions['x'].append((1.20067921161, 1.5, 0.0998))
if not mesh_graded:
	mesh.x = np.concatenate((mesh.x, arangeWithEndpoint(1.20067921161,1.5,0.0998)))
mesh.y = np.delete(mesh.y, np.argwhere((mesh.y >= 1.20016989912) & (mesh.y <= 1.49983010088)))
mesh_regions['y'].append((1.20016989912, 1.49983010088, 0.0998))
if not mesh_graded:
	mesh.y = np.concatenate((mesh.y, arangeWithEndpoint(1.20016989912,1.49983010088,0.0998)))
mesh.z = np.delete(mesh.z, np.argwhere((mesh.z >= 0) & (mesh.z <= 0.254)))
mesh_regions['z'].append((0, 0.254, 0.254))
if not mesh_graded:
	mesh.z = np.concatenate((mesh.z, arangeWithEndpoint(0,0.254,0.254)))

## GRID - Copper_Vias_GND_29 - Copper_Vias_GND_10 (Fixed Distance)
mesh.x = np.delete(mesh.x, np.argwhere((mesh.x >= 0.300679211614) & (mesh.x <= 0.6)))
mesh_regions['x'].append((0.300679211614, 0.6, 0.0998))
if not mesh_graded:
	mesh.x = np.concatenate((mesh.x, arangeWithEndpoint(0.300679211614,0.6,0.0998)))
mesh.y = np.delete(mesh.y, np.argwhere((mesh.y >= 0.300169899123) & (mesh.y <= 0.599830100877)))
mesh_regions['y'].append((0.300169899123, 0.599830100877, 0.0998))
if not mesh_graded:
	mesh.y = np.concatenate((mesh.y, arangeWithEndpoint(0.300169899123,0.599830100877,0.0998)))
mesh.z = np.delete(mesh.z, np.argwhere((mesh.z >= 0) & (mesh.z <= 0.254)))
mesh_regions['z'].append((0, 0.254, 0.254))
if not mesh_graded:
	mesh.z = np.concatenate((mesh.z, arangeWithEndpoint(0,0.254,0.254)))

## GRID - Copper_Vias_GND_29 - Copper_Vias_GND_11 (Fixed Distance)
mesh.x = np.delete(mesh.x, np.argwhere((mesh.x >= 0.300679211614) & (mesh.x <= 0.6)))
mesh_regions['x'].append((0.300679211614, 0.6, 0.0998))
if not mesh_graded:
	mesh.x = np.concatenate((mesh.x, arangeWithEndpoint(0.300679211614,0.6,0.0998)))
mesh.y = np.delete(mesh.y, np.argwhere((mesh.y >= -0.599830100877) & (mesh.y <= -0.300169899123)))
mesh_regions['y'].append((-0.599830100877, -0.300169899123, 0.0998))
if not mesh_graded:
	mesh.y = np.concatenate((mesh.y, arangeWithEndpoint(-0.599830100877,-0.300169899123,0.0998)))
mesh.z = np.delete(mesh.z, np.argwhere((mesh.z >= 0) & (mesh.z <= 0.254)))
mesh_regions['z'].append((0, 0.254, 0.254))
if not mesh_graded:
	mesh.z = np.concatenate((mesh.z, arangeWithEndpoint(0,0.254,0.254)))

## GRID - Copper_Vias_GND_29 - Copper_Vias_GND_6 (Fixed Distance)
mesh.x = np.delete(mesh.x, np.argwhere((mesh.x >= -0.599320788386) & (mesh.x <= -0.3)))
mesh_regions['x'].append((-0.599320788386, -0.3, 0.0998))
if not mesh_graded:
	mesh.x = np.concatenate((mesh.x, arangeWithEndpoint(-0.599320788386,-0.3,0.0998)))
mesh.y = np.delete(mesh.y, np.argwhere((mesh.y >= 0.300169899123) & (mesh.y <= 0.599830100877)))
mesh_regions['y'].append((0.300169899123, 0.599830100877, 0.0998))
if not mesh_graded:
	mesh.y = np.concatenate((mesh.y, arangeWithEndpoint(0.300169899123,0.599830100877,0.0998)))
mesh.z = np.delete(mesh.z, np.argwhere((mesh.z >= 0) & (mesh.z <= 0.254)))
mesh_regions['z'].append((0, 0.254, 0.254))
if not mesh_graded:
	mesh.z = np.concatenate((mesh.z, arangeWithEndpoint(0,0.254,0.254)))

## GRID - Copper_Vias_GND_29 - Copper_Vias_GND_12 (Fixed Distance)
mesh.x = np.delete(mesh.x, np.argwhere((mesh.x >= 0.300679211614) & (mesh.x <= 0.6)))
mesh_regions['x'].append((0.300679211614, 0.6, 0.0998))
if not mesh_graded:
	mesh.x = np.concatenate((mesh.x, arangeWithEndpoint(0.300679211614,0.6,0.0998)))
mesh.y = np.delete(mesh.y, np.argwhere((mesh.y >= -1.49983010088) & (mesh.y <= -1.20016989912)))
mesh_regions['y'].append((-1.49983010088, -1.20016989912, 0.0998))
if not mesh_graded:
	mesh.y = np.concatenate((mesh.y, arangeWithEndpoint(-1.49983010088,-1.20016989912,0.0998)))
mesh.z = np.delete(mesh.z, np.argwhere((mesh.z >= 0) & (mesh.z <= 0.254)))
mesh_regions['z'].append((0, 0.254, 0.254))
if not mesh_graded:
	mesh.z = np.concatenate((mesh.z, arangeWithEndpoint(0,0.254,0.254)))

## GRID - Copper_Vias_GND_29 - Copper_Vias_GND_9 (Fixed Distance)
mesh.x = np.delete(mesh.x, np.argwhere((mesh.x >= 0.300679211614) & (mesh.x <= 0.6)))
mesh_regions['x'].append((0.300679211614, 0.6, 0.0998))
if not mesh_graded:
	mesh.x = np.concatenate((mesh.x, arangeWithEndpoint(0.300679211614,0.6,0.0998)))
mesh.y = np.delete(mesh.y, np.argwhere((mesh.y >= 1.20016989912) & (mesh.y <= 1.49983010088)))
mesh_regions['y'].append((1.20016989912, 1.49983010088, 0.0998))
if not mesh_graded:
	mesh.y = np.concatenate((mesh.y, arangeWithEndpoint(1.20016989912,1.49983010088,0.0998)))
mesh.z = np.delete(mesh.z, np.argwhere((mesh.z >= 0) & (mesh.z <= 0.254)))
mesh_regions['z'].append((0, 0.254, 0.254))
if not mesh_graded:
	mesh.z = np.concatenate((mesh.z, arangeWithEndpoint(0,0.254,0.254)))

## GRID - Copper_Vias_GND_29 - Copper_Vias_GND_7 (Fixed Distance)
mesh.x = np.delete(mesh.x, np.argwhere((mesh.x >= -0.599320788386) & (mesh.x <= -0.3)))
mesh_regions['x'].append((-0.599320788386, -0.3, 0.0998))
if not mesh_graded:
	mesh.x = np.concatenate((mesh.x, arangeWithEndpoint(-0.599320788386,-0.3,0.0998)))
mesh.y = np.delete(mesh.y, np.argwhere((mesh.y >= -0.599830100877) & (mesh.y <= -0.300169899123)))
mesh_regions['y'].append((-0.599830100877, -0.300169899123, 0.0998))
if not mesh_graded:
	mesh.y = np.concatenate((mesh.y, arangeWithEndpoint(-0.599830100877,-0.300169899123,0.0998)))
mesh.z = np.delete(mesh.z, np.argwhere((mesh.z >= 0) & (mesh.z <= 0.254)))
mesh_regions['z'].append((0, 0.254, 0.254))
if not mesh_graded:
	mesh.z = np.concatenate((mesh.z, arangeWithEndpoint(0,0.254,0.254)))

## GRID - Copper_Vias_GND_29 - Copper_Vias_GND_2 (Fixed Distance)
mesh.x = np.delete(mesh.x, np.argwhere((mesh.x >= -1.49932078839) & (mesh.x <= -1.2)))
mesh_regions['x'].append((-1.49932078839, -1.2, 0.0998))
if not mesh_graded:
	mesh.x = np.concatenate((mesh.x, arangeWithEndpoint(-1.49932078839,-1.2,0.0998)))
mesh.y = np.delete(mesh.y, np.argwhere((mesh.y >= 0.300169899123) & (mesh.y <= 0.599830100877)))
mesh_regions['y'].append((0.300169899123, 0.599830100877, 0.0998))
if not mesh_graded:
	mesh.y = np.concatenate((mesh.y, arangeWithEndpoint(0.300169899123,0.599830100877,0.0998)))
mesh.z = np.delete(mesh.z, np.argwhere((mesh.z >= 0) & (mesh.z <= 0.254)))
mesh_regions['z'].append((0, 0.254, 0.254))
if not mesh_graded:
	mesh.z = np.concatenate((mesh.z, arangeWithEndpoint(0,0.254,0.254)))

## GRID - Copper_Vias_GND_29 - Copper_Vias_GND_8 (Fixed Distance)
mesh.x = np.delete(mesh.x, np.argwhere((mesh.x >= -0.599320788386) & (mesh.x <= -0.3)))
mesh_regions['x'].append((-0.599320788386, -0.3, 0.0998))
if not mesh_graded:
	mesh.x = np.concatenate((mesh.x, arangeWithEndpoint(-0.599320788386,-0.3,0.0998)))
mesh.y = np.delete(mesh.y, np.argwhere((mesh.y >= -1.49983010088) & (mesh.y <= -1.20016989912)))
mesh_regions['y'].append((-1.49983010088, -1.20016989912, 0.0998))
if not mesh_graded:
	mesh.y = np.concatenate((mesh.y, arangeWithEndpoint(-1.49983010088,-1.20016989912,0.0998)))
mesh.z = np.delete(mesh.z, np.argwhere((mesh.z >= 0) & (mesh.z <= 0.254)))
mesh_regions['z'].append((0, 0.254, 0.254))
if not mesh_graded:
	mesh.z = np.concatenate((mesh.z, arangeWithEndpoint(0,0.254,0.254)))

## GRID - Copper_Vias_GND_29 - Copper_Vias_GND_5 (Fixed Distance)
mesh.x = np.delete(mesh.x, np.argwhere((mesh.x >= -0.599320788386) & (mesh.x <= -0.3)))
mesh_regions['x'].append((-0.599320788386, -0.3, 0.0998))
if not mesh_graded:
	mesh.x = np.concatenate((mesh.x, arangeWithEndpoint(-0.599320788386,-0.3,0.0998)))
mesh.y = np.delete(mesh.y, np.argwhere((mesh.y >= 1.20016989912) & (mesh.y <= 1.49983010088)))
mesh_regions['y'].append((1.20016989912, 1.49983010088, 0.0998))
if not mesh_graded:
	mesh.y = np.concatenate((mesh.y, arangeWithEndpoint(1.20016989912,1.49983010088,0.0998)))
mesh.z = np.delete(mesh.z, np.argwhere((mesh.z >= 0) & (mesh.z <= 0.254)))
mesh_regions['z'].append((0, 0.254, 0.254))
if not mesh_graded:
	mesh.z = np.concatenate((mesh.z, arangeWithEndpoint(0,0.254,0.254)))

## GRID - Copper_Vias_GND_29 - Copper_Vias_GND_3 (Fixed Distance)
mesh.x = np.delete(mesh.x, np.argwhere((mesh.x >= -1.49932078839) & (mesh.x <= -1.2)))
mesh_regions['x'].append((-1.49932078839, -1.2, 0.0998))
if not mesh_graded:
	mesh.x = np.concatenate((mesh.x, arangeWithEndpoint(-1.49932078839,-1.2,0.0998)))
mesh.y = np.delete(mesh.y, np.argwhere((mesh.y >= -0.599830100877) & (mesh.y <= -0.300169899123)))
mesh_regions['y'].append((-0.599830100877, -0.300169899123, 0.0998))
if not mesh_graded:
	mesh.y = np.concatenate((mesh.y, arangeWithEndpoint(-0.599830100877,-0.300169899123,0.0998)))
mesh.z = np.delete(mesh.z, np.argwhere((mesh.z >= 0) & (mesh.z <= 0.254)))
mesh_regions['z'].append((0, 0.254, 0.254))
if not mesh_graded:
	mesh.z = np.concatenate((mesh.z, arangeWithEndpoint(0,0.254,0.254)))

## GRID - Copper_Vias_GND_29 - Copper_Vias_GND_4 (Fixed Distance)
mesh.x = np.delete(mesh.x, np.argwhere((mesh.x >= -1.49932078839) & (mesh.x <= -1.2)))
mesh_regions['x'].append((-1.49932078839, -1.2, 0.0998))
if not mesh_graded:
	mesh.x = np.concatenate((mesh.x, arangeWithEndpoint(-1.49932078839,-1.2,0.0998)))
mesh.y = np.delete(mesh.y, np.argwhere((mesh.y >= -1.49983010088) & (mesh.y <= -1.20016989912)))
mesh_regions['y'].append((-1.49983010088, -1.20016989912, 0.0998))
if not mesh_graded:
	mesh.y = np.concatenate((mesh.y, arangeWithEndpoint(-1.49983010088,-1.20016989912,0.0998)))
mesh.z = np.delete(mesh.z, np.argwhere((mesh.z >= 0) & (mesh.z <= 0.254)))
mesh_regions['z'].append((0, 0.254, 0.254))
if not mesh_graded:
	mesh.z = np.concatenate((mesh.z, arangeWithEndpoint(0,0.254,0.254)))

## GRID - Copper_Vias_GND_29 - Copper_Vias_GND_1 (Fixed Distance)
mesh.x = np.delete(mesh.x, np.argwhere((mesh.x >= -1.49932078839) & (mesh.x <= -1.2)))
mesh_regions['x'].append((-1.49932078839, -1.2, 0.0998))
if not mesh_graded:
	mesh.x = np.concatenate((mesh.x, arangeWithEndpoint(-1.49932078839,-1.2,0.0998)))
mesh.y = np.delete(mesh.y, np.argwhere((mesh.y >= 1.20016989912) & (mesh.y <= 1.49983010088)))
mesh_regions['y'].append((1.20016989912, 1.49983010088, 0.0998))
if not mesh_graded:
	mesh.y = np.concatenate((mesh.y, arangeWithEndpoint(1.20016989912,1.49983010088,0.0998)))
mesh.z = np.delete(mesh.z, np.argwhere((mesh.z >= 0) & (mesh.z <= 0.254)))
mesh_regions['z'].append((0, 0.254, 0.254))
if not mesh_graded:
	mesh.z = np.concatenate((mesh.z, arangeWithEndpoint(0,0.254,0.254)))

## GRID - Copper_Vias_GND_29 - Copper_Vias_GND_36 (Fixed Distance)
mesh.x = np.delete(mesh.x, np.argwhere((mesh.x >= -4.34932078839) & (mesh.x <= -4.05)))
mesh_regions['x'].append((-4.34932078839, -4.05, 0.0998))
if not mesh_graded:
	mesh.x = np.concatenate((mesh.x, arangeWithEndpoint(-4.34932078839,-4.05,0.0998)))
mesh.y = np.delete(mesh.y, np.argwhere((mesh.y >= -1.24983010088) & (mesh.y <= -0.950169899123)))
mesh_regions['y'].append((-1.24983010088, -0.950169899123, 0.0998))
if not mesh_graded:
	mesh.y = np.concatenate((mesh.y, arangeWithEndpoint(-1.24983010088,-0.950169899123,0.0998)))
mesh.z = np.delete(mesh.z, np.argwhere((mesh.z >= 0) & (mesh.z <= 0.254)))
mesh_regions['z'].append((0, 0.254, 0.254))
if not mesh_graded:
	mesh.z = np.concatenate((mesh.z, arangeWithEndpoint(0,0.254,0.254)))

## GRID - Copper_Vias_GND_29 - Copper_Vias_GND_35 (Fixed Distance)
mesh.x = np.delete(mesh.x, np.argwhere((mesh.x >= -4.34932078839) & (mesh.x <= -4.05)))
mesh_regions['x'].append((-4.34932078839, -4.05, 0.0998))
if not mesh_graded:
	mesh.x = np.concatenate((mesh.x, arangeWithEndpoint(-4.34932078839,-4.05,0.0998)))
mesh.y = np.delete(mesh.y, np.argwhere((mesh.y >= 0.450169899123) & (mesh.y <= 0.749830100877)))
mesh_regions['y'].append((0.450169899123, 0.749830100877, 0.0998))
if not mesh_graded:
	mesh.y = np.concatenate((mesh.y, arangeWithEndpoint(0.450169899123,0.749830100877,0.0998)))
mesh.z = np.delete(mesh.z, np.argwhere((mesh.z >= 0) & (mesh.z <= 0.254)))
mesh_regions['z'].append((0, 0.254, 0.254))
if not mesh_graded:
	mesh.z = np.concatenate((mesh.z, arangeWithEndpoint(0,0.254,0.254)))

## GRID - Copper_Vias_GND_29 - Copper_Vias_GND_34 (Fixed Distance)
mesh.x = np.delete(mesh.x, np.argwhere((mesh.x >= -5.34932078839) & (mesh.x <= -5.05)))
mesh_regions['x'].append((-5.34932078839, -5.05, 0.0998))
if not mesh_graded:
	mesh.x = np.concatenate((mesh.x, arangeWithEndpoint(-5.34932078839,-5.05,0.0998)))
mesh.y = np.delete(mesh.y, np.argwhere((mesh.y >= -1.24983010088) & (mesh.y <= -0.950169899123)))
mesh_regions['y'].append((-1.24983010088, -0.950169899123, 0.0998))
if not mesh_graded:
	mesh.y = np.concatenate((mesh.y, arangeWithEndpoint(-1.24983010088,-0.950169899123,0.0998)))
mesh.z = np.delete(mesh.z, np.argwhere((mesh.z >= 0) & (mesh.z <= 0.254)))
mesh_regions['z'].append((0, 0.254, 0.254))
if not mesh_graded:
	mesh.z = np.concatenate((mesh.z, arangeWithEndpoint(0,0.254,0.254)))

## GRID - Copper_Vias_GND_29 - Copper_Vias_GND_33 (Fixed Distance)
mesh.x = np.delete(mesh.x, np.argwhere((mesh.x >= -5.34932078839) & (mesh.x <= -5.05)))
mesh_regions['x'].append((-5.34932078839, -5.05, 0.0998))
if not mesh_graded:
	mesh.x = np.concatenate((mesh.x, arangeWithEndpoint(-5.34932078839,-5.05,0.0998)))
mesh.y = np.delete(mesh.y, np.argwhere((mesh.y >= 0.450169899123) & (mesh.y <= 0.749830100877)))
mesh_regions['y'].append((0.450169899123, 0.749830100877, 0.0998))
if not mesh_graded:
	mesh.y = np.concatenate((mesh.y, arangeWithEndpoint(0.450169899123,0.749830100877,0.0998)))
mesh.z = np.delete(mesh.z, np.argwhere((mesh.z >= 0) & (mesh.z <= 0.254)))
mesh_regions['z'].append((0, 0.254, 0.254))
if not mesh_graded:
	mesh.z = np.concatenate((mesh.z, arangeWithEndpoint(0,0.254,0.254)))

## GRID - Copper_Vias_GND_29 - Copper_Vias_GND_32 (Fixed Distance)
mesh.x = np.delete(mesh.x, np.argwhere((mesh.x >= -6.34932078839) & (mesh.x <= -6.05)))
mesh_regions['x'].append((-6.34932078839, -6.05, 0.0998))
if not mesh_graded:
	mesh.x = np.concatenate((mesh.x, arangeWithEndpoint(-6.34932078839,-6.05,0.0998)))
mesh.y = np.delete(mesh.y, np.argwhere((mesh.y >= -1.24983010088) & (mesh.y <= -0.950169899123)))
mesh_regions['y'].append((-1.24983010088, -0.950169899123, 0.0998))
if not mesh_graded:
	mesh.y = np.concatenate((mesh.y, arangeWithEndpoint(-1.24983010088,-0.950169899123,0.0998)))
mesh.z = np.delete(mesh.z, np.argwhere((mesh.z >= 0) & (mesh.z <= 0.254)))
mesh_regions['z'].append((0, 0.254, 0.254))
if not mesh_graded:
	mesh.z = np.concatenate((mesh.z, arangeWithEndpoint(0,0.254,0.254)))

## GRID - Copper_Vias_GND_29 - Copper_Vias_GND_31 (Fixed Distance)
mesh.x = np.delete(mesh.x, np.argwhere((mesh.x >= -6.34932078839) & (mesh.x <= -6.05)))
mesh_regions['x'].append((-6.34932078839, -6.05, 0.0998))
if not mesh_graded:
	mesh.x = np.concatenate((mesh.x, arangeWithEndpoint(-6.34932078839,-6.05,0.0998)))
mesh.y = np.delete(mesh.y, np.argwhere((mesh.y >= 0.450169899123) & (mesh.y <= 0.749830100877)))
mesh_regions['y'].append((0.450169899123, 0.749830100877, 0.0998))
if not mesh_graded:
	mesh.y = np.concatenate((mesh.y, arangeWithEndpoint(0.450169899123,0.749830100877,0.0998)))
mesh.z = np.delete(mesh.z, np.argwhere((mesh.z >= 0) & (mesh.z <= 0.254)))
mesh_regions['z'].append((0, 0.254, 0.254))
if not mesh_graded:
	mesh.z = np.concatenate((mesh.z, arangeWithEndpoint(0,0.254,0.254)))

## GRID - Copper_Vias_GND_29 - Copper_Vias_GND_30 (Fixed Distance)
mesh.x = np.delete(mesh.x, np.argwhere((mesh.x >= -7.34932078839) & (mesh.x <= -7.05)))
mesh_regions['x'].append((-7.34932078839, -7.05, 0.0998))
if not mesh_graded:
	mesh.x = np.concatenate((mesh.x, arangeWithEndpoint(-7.34932078839,-7.05,0.0998)))
mesh.y = np.delete(mesh.y, np.argwhere((mesh.y >= -1.24983010088) & (mesh.y <= -0.950169899123)))
mesh_regions['y'].append((-1.24983010088, -0.950169899123, 0.0998))
if not mesh_graded:
	mesh.y = np.concatenate((mesh.y, arangeWithEndpoint(-1.24983010088,-0.950169899123,0.0998)))
mesh.z = np.delete(mesh.z, np.argwhere((mesh.z >= 0) & (mesh.z <= 0.254)))
mesh_regions['z'].append((0, 0.254, 0.254))
if not mesh_graded:
	mesh.z = np.concatenate((mesh.z, arangeWithEndpoint(0,0.254,0.254)))

## GRID - Copper_Vias_GND_29 - Copper_Vias_GND_29 (Fixed Distance)
mesh.x = np.delete(mesh.x, np.argwhere((mesh.x >= -7.34932078839) & (mesh.x <= -7.05)))
mesh_regions['x'].append((-7.34932078839, -7.05, 0.0998))
if not mesh_graded:
	mesh.x = np.concatenate((mesh.x, arangeWithEndpoint(-7.34932078839,-7.05,0.0998)))
mesh.y = np.delete(mesh.y, np.argwhere((mesh.y >= 0.450169899123) & (mesh.y <= 0.749830100877)))
mesh_regions['y'].append((0.450169899123, 0.749830100877, 0.0998))
if not mesh_graded:
	mesh.y = np.concatenate((mesh.y, arangeWithEndpoint(0.450169899123,0.749830100877,0.0998)))
mesh.z = np.delete(mesh.z, np.argwhere((mesh.z >= 0) & (mesh.z <= 0.254)))
mesh_regions['z'].append((0, 0.254, 0.254))
if not mesh_graded:
	mesh.z = np.concatenate((mesh.z, arangeWithEndpoint(0,0.254,0.254)))

## GRID - Copper_Bottom_GND - Copper_Bottom_GND (Fixed Distance)
mesh.x = np.delete(mesh.x, np.argwhere((mesh.x >= -14.9495) & (mesh.x <= 14.9495)))
mesh_regions['x'].append((-14.9495, 14.9495, 0.579))
if not mesh_graded:
	mesh.x = np.concatenate((mesh.x, arangeWithEndpoint(-14.9495,14.9495,0.579)))
mesh.y = np.delete(mesh.y, np.argwhere((mesh.y >= -4.5) & (mesh.y <= 4.5)))
mesh_regions['y'].append((-4.5, 4.5, 0.579))
if not mesh_graded:
	mesh.y = np.concatenate((mesh.y, arangeWithEndpoint(-4.5,4.5,0.579)))
mesh.z = np.delete(mesh.z, np.argwhere((mesh.z >= -0.035) & (mesh.z <= 0)))
mesh_regions['z'].append((-0.035, 0, 0.035))
if not mesh_graded:
	mesh.z = np.concatenate((mesh.z, arangeWithEndpoint(-0.035,0,0.035)))

for d in ['x', 'y', 'z']:
	if len(getattr(mesh, d)) > 0:
		openEMS_grid.AddLine(d, getattr(mesh, d))

#######################################################################################################################################
# PORTS
#######################################################################################################################################
port = {}
portNamesAndNumbersList = {}
## PORT - port1 - Port_1
# thin port box generated as zero-thickness plate on face touching geometry (thin box would force sub-micron cells)
portStart = [ -14.9, -0.47, 0 ]
portStop  = [ -14.9, -0.03, 0.254 ]
portR = 50
portUnits = 1
portExcitationAmplitude = 1.0
portDirection = 'z'
port[1] = FDTD.AddLumpedPort(port_nr=1, R=portR*portUnits, start=portStart, stop=portStop, p_dir=portDirection, priority=10000, excite=1.0*portExcitationAmplitude)
portNamesAndNumbersList["Port_1"] = 1;

## PORT - port2 - Port_2
# thin port box generated as zero-thickness plate on face touching geometry (thin box would force sub-micron cells)
portStart = [ 14.9, -0.47, 0 ]
portStop  = [ 14.9, -0.03, 0.254 ]
portR = 50
portUnits = 1
portExcitationAmplitude = 1.0
portDirection = 'z'
port[2] = FDTD.AddLumpedPort(port_nr=2, R=portR*portUnits, start=portStart, stop=portStop, p_dir=portDirection, priority=9900, excite=0)
portNamesAndNumbersList["Port_2"] = 2;

## PORT - port3 - Port_3
# thin port box generated as zero-thickness plate on face touching geometry (thin box would force sub-micron cells)
portStart = [ -0.3215, -0.00907, 0.7119803 ]
portStop  = [ -0.2657, 0.03093, 0.7119803 ]
portR = 50
portUnits = 1
portExcitationAmplitude = 1.0
portDirection = 'y'
port[3] = FDTD.AddLumpedPort(port_nr=3, R=portR*portUnits, start=portStart, stop=portStop, p_dir=portDirection, priority=9800, excite=0)
portNamesAndNumbersList["Port_3"] = 3;

## PORT - port4 - Port_4
# thin port box generated as zero-thickness plate on face touching geometry (thin box would force sub-micron cells)
portStart = [ 0.23817, -0.023995, 0.7119803 ]
portStop  = [ 0.29397, 0.016005, 0.7119803 ]
portR = 50
portUnits = 1
portExcitationAmplitude = 1.0
portDirection = 'y'
port[4] = FDTD.AddLumpedPort(port_nr=4, R=portR*portUnits, start=portStart, stop=portStop, p_dir=portDirection, priority=9700, excite=0)
portNamesAndNumbersList["Port_4"] = 4;

#######################################################################################################################################
# MESH FINALIZATION
#   - all geometry and port planes are mesh lines, lines closer than mesh_merge_tol are merged (sliver cells -> tiny timestep)
#   - mesh_graded: cell size follows 'Fixed Distance' regions (smallest wins where they overlap) and grows between them
#   - cells grow at most by mesh_max_ratio from cell to cell, max. cell is mesh_max_res (wavelength_min / N)
#   - mesh_air_margin of clear air between structure and PML plus PML cells, ports must not be inside PML
#######################################################################################################################################
# settings from grid tab -> Solver mesh (global)
mesh_finalize = True
mesh_max_ratio = 1.3      # max. ratio of neighbouring cells
mesh_merge_rel = 0.5      # graded: geometry planes closer than mesh_merge_rel * local cell are merged
mesh_merge_tol = 50e-9 / unit     # not graded: lines closer than this are merged
wavelength_min = 20 * max_res / unit if ('max_res' in globals() and max_res > 0) else 0      # max_res = C0 / fmax / 20
mesh_max_res = wavelength_min / 15     # max. cell in air
mesh_air_margin = wavelength_min / 4     # clear air between structure and PML, lambda/4 at highest frequency

mesh_fixed = {}
mesh_fixed['x'] = [-15.0, -14.9495, -14.9, -13.949321, -13.65, -13.349321, -13.05, -12.449321, -12.349321, -12.15, -12.05, -11.349321, -11.05, -10.949321, -10.65, -10.349321, -10.05, -9.449321, -9.349321, -9.15, -9.05, -8.349321, -8.05, -7.949321, -7.65, -7.349321, -7.05, -6.449321, -6.349321, -6.15, -6.05, -5.349321, -5.05, -4.949321, -4.65, -4.349321, -4.05, -3.449321, -3.35, -3.15, -3.0, -2.6, -2.375, -2.278912, -2.276844, -2.276593, -2.125, -2.05, -2.0, -1.949321, -1.875, -1.85, -1.812132, -1.65, -1.625, -1.499321, -1.375, -1.285185, -1.2, -1.125, -1.1, -0.875, -0.812132, -0.625, -0.599321, -0.4, -0.39, -0.375, -0.333605, -0.3215, -0.3, -0.272429, -0.272072, -0.266954, -0.266675, -0.2657, -0.264099, -0.132815, -0.125, -0.1, -0.052083, -0.040477, 0.011845, 0.10776, 0.125, 0.187868, 0.236686, 0.23817, 0.239293, 0.239321, 0.29397, 0.300679, 0.31397, 0.375, 0.39, 0.6, 0.625, 0.780995, 0.875, 0.9, 1.050679, 1.125, 1.200679, 1.35, 1.375, 1.5, 1.6, 1.625, 1.788796, 1.85, 1.875, 2.0, 2.05, 2.125, 2.276682, 2.276716, 2.278841, 2.312132, 2.375, 2.550679, 2.6, 2.85, 3.0, 3.35, 4.050679, 4.35, 5.050679, 5.35, 5.550679, 5.85, 6.050679, 6.35, 7.050679, 7.35, 8.050679, 8.35, 8.550679, 8.85, 9.050679, 9.35, 10.050679, 10.35, 11.050679, 11.35, 11.550679, 11.85, 12.050679, 12.35, 13.050679, 13.35, 14.9, 14.9495, 15.0]
mesh_fixed['y'] = [-4.5, -3.35, -3.34983, -3.05017, -2.996, -2.6, -2.375, -2.371, -2.275325, -2.275232, -2.274074, -2.272711, -2.125, -2.121, -2.05, -1.996, -1.875, -1.871, -1.846, -1.625, -1.621, -1.49983, -1.371, -1.24983, -1.20017, -1.121, -0.95017, -0.871, -0.775215, -0.774879, -0.621, -0.59983, -0.486, -0.47, -0.4296, -0.371, -0.362828, -0.359526, -0.358554, -0.358437, -0.30017, -0.268793, -0.268432, -0.121, -0.118761, -0.103645, -0.03, -0.023995, -0.022794, -0.00907, -0.007701, 0.016005, 0.03093, 0.034892, 0.049919, 0.129, 0.270193, 0.27616, 0.276404, 0.30017, 0.35469, 0.379, 0.45017, 0.494, 0.59983, 0.629, 0.74983, 0.879, 1.125, 1.129, 1.15017, 1.20017, 1.375, 1.379, 1.44983, 1.49983, 1.625, 1.629, 1.854, 1.875, 1.879, 2.004, 2.05, 2.125, 2.129, 2.280543, 2.375, 2.379, 2.6, 2.65017, 2.94983, 3.004, 3.35, 4.15017, 4.44983, 4.5]
mesh_fixed['z'] = [-0.035, 0.0, 0.127, 0.254, 0.2715, 0.289, 0.489, 0.514, 0.694, 0.69775, 0.70898, 0.71048, 0.71198, 0.71348, 0.71388, 0.890804, 0.890888, 0.890961, 0.891128, 0.891146, 0.891147, 0.891151, 0.891152, 0.891154, 0.891156, 1.139]
mesh_regions['z'].append((0.254, 0.289, 0.0175))     # thin STL conductor layer, cells inside it so it conducts (automatic)
mesh_regions['z'].append((0.254, 0.289, 0.0175))     # thin STL conductor layer, cells inside it so it conducts (automatic)
mesh_regions['z'].append((0.254, 0.289, 0.0175))     # thin STL conductor layer, cells inside it so it conducts (automatic)
mesh_regions['z'].append((0.254, 0.289, 0.0175))     # thin STL conductor layer, cells inside it so it conducts (automatic)
mesh_regions['z'].append((0.254, 0.289, 0.0175))     # thin STL conductor layer, cells inside it so it conducts (automatic)
mesh_regions['z'].append((0.254, 0.289, 0.0175))     # thin STL conductor layer, cells inside it so it conducts (automatic)
mesh_regions['z'].append((0.254, 0.289, 0.0175))     # thin STL conductor layer, cells inside it so it conducts (automatic)
mesh_regions['z'].append((0.70898, 0.71198, 0.0015))     # thin STL conductor layer, cells inside it so it conducts (automatic)
mesh_regions['z'].append((0, 0.254, 0.127))     # thin STL conductor layer, cells inside it so it conducts (automatic)
structure_min = [-15.0, -4.5, -0.035]
structure_max = [15.0, 4.5, 1.139]
pml_cells = {'x': (8, 8), 'y': (8, 8), 'z': (8, 8)}

if mesh_finalize:
	for k, d in enumerate(['x', 'y', 'z']):
		lines = meshFinalizeLines(openEMS_grid.GetLines(d), mesh_fixed[d], mesh_max_res, mesh_max_ratio, mesh_merge_tol, structure_min[k], structure_max[k], pml_cells[d], mesh_air_margin, mesh_regions[d] if mesh_graded else None, mesh_merge_rel)
		openEMS_grid.SetLines(d, lines)
		for msg in meshCheckPml(lines, structure_min[k], structure_max[k], pml_cells[d]):
			print(f'WARNING mesh {d}: {msg}, ports and structure are inside absorbing boundary')

# conductors above all dielectrics (a dielectric with higher priority takes the edges of a conductor it overlaps,
# QFN chip sheets came out missing), lumped ports above all conductors
lift = liftConductorPriorities(CSX)
if lift > 0:
	print(f'conductor priorities raised by {lift} above all dielectrics')

# smallest cell (um): lines closer than this are thinned, conductor faces and sheets keep theirs, conductors thinner
# than a cell are stretched to their enclosing cell so they stay connected; z must resolve the thinnest gap between metals
mesh_min_cell_um = float(os.environ.get('MIN_CELL_UM', 5))     # x, y, 0 = off
mesh_min_cell_z_um = float(os.environ.get('MIN_CELL_Z_UM', 5))
mesh_min_cells = [mesh_min_cell_um * 1e-6 / unit] * 2 + [mesh_min_cell_z_um * 1e-6 / unit]
if max(mesh_min_cells) > 0:
	stretched = applyMinCell(CSX, openEMS_grid, mesh_min_cells, stl_solids)
	print(f'smallest cell {mesh_min_cell_um:g} um, z {mesh_min_cell_z_um:g} um, {stretched} thin conductors stretched to their enclosing cell')

mesh.x = openEMS_grid.GetLines('x', True)
mesh.y = openEMS_grid.GetLines('y', True)
mesh.z = openEMS_grid.GetLines('z', True)
mesh_min_cell = min([np.min(np.diff(v)) for v in [mesh.x, mesh.y, mesh.z] if len(v) > 1])
print('Mesh: %d x %d x %d = %.2f M cells, smallest cell %g' % (len(mesh.x), len(mesh.y), len(mesh.z), len(mesh.x)*len(mesh.y)*len(mesh.z)/1e6, mesh_min_cell))
if len(mesh.x)*len(mesh.y)*len(mesh.z) > 50e6:
	print('WARNING mesh has more than 50 M cells, check fine mesh regions (fine mesh just where fields change fast: ports, thin metals, wires)')

#######################################################################################################################################
# RUN
#######################################################################################################################################
### Run the simulation
CSX_file = os.path.join(Sim_Path, Sim_CSX)
if not os.path.exists(Sim_Path):
	os.mkdir(Sim_Path)
CSX.Write2XML(CSX_file)
# 3D viewer only with a display (it blocks or fails on a server), env NO_VIEW=1 switches it off
if not os.environ.get('NO_VIEW') and (os.name == 'nt' or os.environ.get('DISPLAY') or os.environ.get('WAYLAND_DISPLAY')):
	from CSXCAD import AppCSXCAD_BIN
	os.system(AppCSXCAD_BIN + ' "{}"'.format(CSX_file))

# all CPU cores (env OPENEMS_THREADS), debug flags passed only when True (False flag is passed to openEMS as empty option which can break options after it)
run_opts = dict(verbose=3, cleanup=True, setup_only=setup_only, engine='multithreaded', numThreads=int(os.environ.get('OPENEMS_THREADS', os.cpu_count())))
if debug_pec or os.environ.get('DEBUG_PEC'):     # env DEBUG_PEC=1 with SETUP_ONLY=1: PEC and material dump to check connectivity
	run_opts.update(debug_PEC=True, debug_material=True)
FDTD.Run(Sim_Path, **run_opts)

#######################################################################################################################################
# POSTPROCESSING - S-parameters for excited port into simulation_output/Sparameters.csv and .png
#######################################################################################################################################
if not setup_only:
	f_start = 5e+08
	f_stop = 1e+10
	freq = np.linspace(f_start if f_start > 0 else f_stop / 500, f_stop, 501)
	excited_port = 1
	sparam_ports = [1, 2, 3, 4]
	sparam_port_names = {1: 'Port_1', 2: 'Port_2', 3: 'Port_3', 4: 'Port_4'}

	for n in sparam_ports:
		port[n].CalcPort(Sim_Path, freq)
	sparams = {f'S{n}{excited_port}': port[n].uf_ref / port[excited_port].uf_inc for n in sparam_ports}

	with open(os.path.join(Sim_Path, 'Sparameters.csv'), 'w', newline='') as csvFile:
		writer = csv.writer(csvFile)
		writer.writerow(['Frequency_Hz'] + [c for name in sparams for c in (name + '_dB', name + '_phase_deg')])
		for i in range(len(freq)):
			row = ['%.6g' % freq[i]]
			for name, s in sparams.items():
				row += ['%.3f' % (20*np.log10(np.abs(s[i]))), '%.2f' % np.angle(s[i], deg=True)]
			writer.writerow(row)

	figure(figsize=(9, 5.5))
	for name, s in sparams.items():
		plot(freq/1e9, 20*np.log10(np.abs(s)), linewidth=2, label=name)
	grid(True)
	xlabel('Frequency (GHz)')
	ylabel('Magnitude (dB)')
	title(f'S-parameters (openEMS), excited port {sparam_port_names[excited_port]}')
	legend()
	savefig(os.path.join(Sim_Path, 'Sparameters.png'), dpi=150, bbox_inches='tight')
	print('S-parameters saved to', os.path.join(Sim_Path, 'Sparameters.csv / .png'))

