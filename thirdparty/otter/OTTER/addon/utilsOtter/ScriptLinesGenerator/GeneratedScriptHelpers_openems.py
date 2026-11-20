#   author: Lubomir Jagos
#
#   Helper functions which are copied into the generated openEMS python script (see getHelperFunctionsSource()),
#   they are kept here as real python code so they can be tested without FreeCAD.
#
#   The mesh helpers follow what was needed to get a valid openEMS result for a PCB-to-chip bondwire
#   transition (thin chip metals next to a millimetre board):
#       - every geometry and port plane must be a mesh line,
#       - near-duplicate lines (sliver cells) make the timestep collapse and must be merged,
#       - neighbouring cells should grow smoothly (ratio ~1.3) up to lambda/20 in air,
#       - ports and structure must keep clear air to the PML, the PML cells must not cover them.
#
import inspect
import numpy as np

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

#
#   Geometry and mesh robustness (QFN package examples, OTTER_Local/New_Example/README.md): what openEMS silently
#   drops or shorts when a model has chip metal of a few um next to a millimetre board:
#       - a flat STL (zero thickness) is never used as polyhedron ('Unused primitive'), it must be a PEC polygon,
#       - a conductor overlapped by a dielectric of higher priority loses every edge to it,
#       - the smallest cell sets the time step: min cell per direction, but conductors must keep their lines,
#       - a conductor thinner than the cell holds one line at most and stops joining the metals above and below it,
#       - a board cut out of a bigger board must continue into the PML, its end in air rings and reflects.
#

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
			gap = np.min(np.abs(np.array(taken) - c)) if taken else np.inf
			# forced lines a float's rounding apart (a port at -0.42500000000001, its trace at -0.425) are one
			# line: two would make a 1e-14 cell and the timestep with it
			if (force and gap > same) or gap >= minCell - 1e-12:
				taken.append(c)
	lines = np.asarray(lines, dtype=float)
	same = 1e-9 * max(1.0, float(np.max(np.abs(lines)))) if len(lines) else 1e-9
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

def getHelperFunctionsSource():
	"""
	Source code of helper functions to copy into generated script.
	"""
	genScript = ""
	for func in [arangeWithEndpoint, meshMergeCloseLines, meshGradedLines, meshFinalizeLines, meshCheckPml,
	             readSTL, flatOutlines, addSTL, isConductor, liftConductorPriorities, conductorBoxes, snapSheets,
	             meshMinCell, portPlanes, applyMinCell, extendIntoPml]:
		genScript += inspect.getsource(func) + "\n"
	return genScript
