#   author: Lubomir Jagos
#
#   Palace mesher which is copied into the generated Palace python script (see getHelperFunctionsSource()), it's kept here as real
#   python code so it can be tested without FreeCAD. Generated script defines MODEL (objects, materials, ports, mesh sizes from GUI)
#   and calls palaceBuildMesh(MODEL) and palaceConfigDomainsAndBoundaries(MODEL, groups).
#
#   Recipe from mesh_palace.py (PCB-to-chip bondwire model, Palace result compared against CST), order of steps matters:
#       1. import all objects, remove exact duplicate solids (two bodies at same place can't be fragmented or meshed)
#       2. read everything needed later (wire feet, port rectangles snapped to real geometry) BEFORE fragmentation,
#          fragment() renumbers entities and old tags become invalid
#       3. ONE fragment() of all volumes, air and port rectangles together, then removeAllDuplicates(), a second pass renumbers all
#          faces and a new plate on existing face is a duplicate ('Invalid boundary mesh (overlapping facets)')
#       4. volume fragment gets material of object with highest priority covering it, metal wins over dielectric wins over air
#       5. metals are holes: volume removed with recursive=False, its faces stay as PEC / conductivity boundary of neighbours,
#          just faces still touching meshed volume are used (face between two removed metals makes MFEM abort in STable3D)
#       6. graded size fields combined by Min field, Mesh.MeshSizeExtendFromBoundary = 0 (otherwise micrometer sizes spread into whole
#          substrate), ports have own size, wire feet and air inside wire loops refined (loop inductance)
#       7. port mesh nodes snapped exactly on port rectangle, Palace checks lumped port rectangle with relative tolerance 1e-6
#
import inspect

def palaceLog(msg):
	print("### " + msg, flush=True)

def palaceMakeRect(rect):
	"""
	Axis aligned rectangle rect = {'axis': 0|1|2, 'coord': value, 'min': [a, b], 'max': [a, b]} (min/max in remaining two axes
	in x, y, z order), returns gmsh OCC surface tag.
	"""
	axis = rect['axis']
	inPlane = [k for k in range(3) if k != axis]
	corners = [(rect['min'][0], rect['min'][1]), (rect['max'][0], rect['min'][1]), (rect['max'][0], rect['max'][1]), (rect['min'][0], rect['max'][1])]
	points = []
	for u, v in corners:
		p = [0.0, 0.0, 0.0]
		p[axis] = rect['coord']
		p[inPlane[0]] = u
		p[inPlane[1]] = v
		points.append(gmsh.model.occ.addPoint(*p))
	lines = [gmsh.model.occ.addLine(points[k], points[(k + 1) % 4]) for k in range(4)]
	return gmsh.model.occ.addPlaneSurface([gmsh.model.occ.addCurveLoop(lines)])

def palaceSnapRect(rect, vertexCoords, relTol=1e-4):
	"""
	Snap rectangle coordinates to real geometry vertices (port edges lay on material boundaries, their precision comes from STEP file,
	typed numbers miss them). vertexCoords = [sorted x values, sorted y values, sorted z values].
	"""
	axis = rect['axis']
	inPlane = [k for k in range(3) if k != axis]
	spans = [rect['max'][k] - rect['min'][k] for k in range(2)]

	def snap(value, axisIndex, tol):
		values = vertexCoords[axisIndex]
		if len(values) == 0 or tol <= 0:
			return value
		k = int(np.clip(np.searchsorted(values, value), 1, len(values) - 1))
		nearest = values[k - 1] if abs(values[k - 1] - value) <= abs(values[k] - value) else values[k]
		return float(nearest) if abs(nearest - value) <= tol else value

	snapped = dict(rect)
	snapped['coord'] = snap(rect['coord'], axis, relTol * min(spans))
	snapped['min'] = [snap(rect['min'][k], inPlane[k], relTol * spans[k]) for k in range(2)]
	snapped['max'] = [snap(rect['max'][k], inPlane[k], relTol * spans[k]) for k in range(2)]
	return snapped

def palaceBoxesClusters(boxes, gapFactor=0.25):
	"""
	Merge boxes [xmin, ymin, zmin, xmax, ymax, zmax] which overlap or are closer than gapFactor * their larger size (neighbouring
	bondwires), returns list of merged boxes.
	"""
	clusters = [list(b) for b in boxes]
	merged = True
	while merged:
		merged = False
		for i in range(len(clusters)):
			for j in range(i + 1, len(clusters)):
				a, b = clusters[i], clusters[j]
				size = max(max(a[k + 3] - a[k] for k in range(3)), max(b[k + 3] - b[k] for k in range(3)))
				gap = max(max(a[k] - b[k + 3], b[k] - a[k + 3]) for k in range(3))
				if gap <= gapFactor * size:
					clusters[i] = [min(a[k], b[k]) for k in range(3)] + [max(a[k + 3], b[k + 3]) for k in range(3)]
					del clusters[j]
					merged = True
					break
			if merged:
				break
	return clusters

def palaceBuildMesh(model):
	"""
	Builds and writes Palace mesh for MODEL, returns groups (physical group attributes) used by palaceConfigDomainsAndBoundaries().
	MODEL keys:
		objects     - list of {'name', 'file' (STEP) or 'rect', 'role', 'group', 'priority', 'bc', 'material'}
		              role: 'dielectric' (volume, material group), 'pec' (metal removed, PEC faces), 'conductor' (lossy metal removed,
		              Conductivity faces), 'sheet' (surface: Conductivity with thickness, volume: removed, Conductivity faces), 'boundary' (object used as
		              boundary condition bc, enclosing volume is air domain), 'port', 'lumped'
		materials, conductors, sheets, ports, lumpedParts - properties for Palace config
		meshSizes   - {objectName: {'size', 'boundary', 'face', 'volume', 'surface': [sizeMin, sizeMax, distMin, distMax]}} in mm
		settings    - global mesh settings, see generated script
	"""
	settings = model['settings']
	lossyAsPec = settings.get('lossyMetalsAsPec', False)     # lossy metals and conducting sheets as PEC (conductivity boundary is just for driven problem)
	roleRank = {'boundaryVolume': 3, 'pec': 3, 'conductor': 3, 'sheetVolume': 3, 'dielectric': 2, 'domain': 0}

	gmsh.option.setNumber("General.Terminal", 1)
	gmsh.option.setNumber("Geometry.Tolerance", 1e-6)
	gmsh.option.setString("Geometry.OCCTargetUnit", "MM")
	gmsh.model.add(model.get('name', 'palace_model'))

	#
	#	1. import objects
	#
	objects = []
	for obj in model['objects']:
		obj = dict(obj)
		obj['dimtags'] = []
		if obj.get('file'):
			obj['dimtags'] = list(gmsh.model.occ.importShapes(obj['file'], highestDimOnly=True))
		objects.append(obj)
	gmsh.model.occ.synchronize()

	# object role from geometry: boundary object which is volume enclosing everything is air domain, other boundary volumes are removed like metals
	volumeObjects = [o for o in objects if any(d == 3 for d, t in o['dimtags'])]
	def objBBox(o):
		bbs = [gmsh.model.occ.getBoundingBox(d, t) for d, t in o['dimtags']]
		return [min(b[k] for b in bbs) for k in range(3)] + [max(b[k + 3] for b in bbs) for k in range(3)] if bbs else None
	modelBBoxes = [objBBox(o) for o in objects if o['role'] != 'boundary' and o['dimtags']]
	modelBBoxes += [[r['coord'] if k == r['axis'] else r['min'][[j for j in range(3) if j != r['axis']].index(k)] for k in range(3)] +
	                [r['coord'] if k == r['axis'] else r['max'][[j for j in range(3) if j != r['axis']].index(k)] for k in range(3)]
	                for r in [o['rect'] for o in objects if o.get('rect')]]
	modelBBox = [min(b[k] for b in modelBBoxes) for k in range(3)] + [max(b[k + 3] for b in modelBBoxes) for k in range(3)]

	domain = None
	for o in objects:
		if o['role'] == 'boundary' and o in volumeObjects:
			b = objBBox(o)
			encloses = all(b[k] <= modelBBox[k] + 1e-6 and b[k + 3] >= modelBBox[k + 3] - 1e-6 for k in range(3))
			if encloses and (domain is None or np.prod([b[k + 3] - b[k] for k in range(3)]) > np.prod([objBBox(domain)[k + 3] - objBBox(domain)[k] for k in range(3)])):
				domain = o
	for o in objects:
		if o['role'] == 'boundary' and o is not domain and o in volumeObjects:
			o['role'] = 'boundaryVolume'
		if o['role'] == 'sheet' and o in volumeObjects:
			o['role'] = 'sheetVolume'
		if o['role'] == 'conductor' and lossyAsPec:
			o['role'] = 'pec'

	if domain is None:
		margin = settings['airMargin']
		tag = gmsh.model.occ.addBox(modelBBox[0] - margin, modelBBox[1] - margin, modelBBox[2] - margin,
		                            modelBBox[3] - modelBBox[0] + 2 * margin, modelBBox[4] - modelBBox[1] + 2 * margin, modelBBox[5] - modelBBox[2] + 2 * margin)
		gmsh.model.occ.synchronize()
		domain = {'name': 'AIRBOX_AUTO', 'role': 'boundary', 'bc': 'Absorbing', 'priority': 0, 'dimtags': [(3, tag)]}
		objects.append(domain)
		palaceLog(f"no boundary object encloses model, air box with {margin:g} mm margin and Absorbing boundary created")
	domain['role'] = 'domain'
	domain['material'] = domain.get('material') or 'AIR'
	palaceLog(f"air domain: {domain['name']} ({domain['bc']}), material {domain['material']}")

	# duplicate solids (same bounding box and volume) can't be fragmented or meshed, keep one with highest priority
	seen = {}
	for o in sorted(objects, key=lambda o: (roleRank.get(o['role'], 1), o.get('priority', 0)), reverse=True):
		kept = []
		for d, t in o['dimtags']:
			if d != 3:
				kept.append((d, t))
				continue
			key = tuple(np.round(gmsh.model.occ.getBoundingBox(3, t), 9)) + (round(gmsh.model.occ.getMass(3, t), 15),)
			if key in seen:
				palaceLog(f"duplicate solid of '{o['name']}' same as '{seen[key]}' removed")
				gmsh.model.occ.remove([(3, t)], recursive=True)
			else:
				seen[key] = o['name']
				kept.append((d, t))
		o['dimtags'] = kept
	gmsh.model.occ.synchronize()

	# volumes of same dielectric material are fused (like SiO2 + SiO2_2), internal face would keep thin layer (0.2 um) which needs tiny elements
	if settings.get('fuseSameMaterial', True):
		byMaterial = {}
		for o in objects:
			if o['role'] == 'dielectric' and any(d == 3 for d, t in o['dimtags']):
				byMaterial.setdefault(o['group'], []).append(o)
		for material, objs in byMaterial.items():
			if len(objs) < 2:
				continue
			dimtags = [dt for o in objs for dt in o['dimtags'] if dt[0] == 3]
			try:
				fused, fusedMap = gmsh.model.occ.fuse(dimtags[:1], dimtags[1:])
				gmsh.model.occ.synchronize()
			except Exception as e:
				palaceLog(f"fusing '{material}' volumes failed ({e}), they stay separate")
				continue
			keep = max(objs, key=lambda o: o.get('priority', 0))
			keep['fusedNames'] = [o['name'] for o in objs]
			for o in objs:
				o['dimtags'] = [dt for dt in fused if dt[0] == 3] if o is keep else []
			palaceLog(f"{len(objs)} '{material}' objects fused into {len(keep['dimtags'])} volume(s): " + ", ".join(keep['fusedNames']))

	# wires: conductors filling less than half of their bounding box (bondwires, loops), air inside is refined
	for o in objects:
		o['isWire'] = False
		if o['role'] in ['pec', 'conductor', 'boundaryVolume'] and o['dimtags']:
			b = objBBox(o)
			bboxVolume = np.prod([b[k + 3] - b[k] for k in range(3)])
			volume = sum(gmsh.model.occ.getMass(d, t) for d, t in o['dimtags'] if d == 3)
			o['isWire'] = bboxVolume > 0 and volume < 0.5 * bboxVolume
			o['bbox'] = b

	#
	#	2. rectangles (ports, lumped parts, sheets) snapped to real geometry vertices, created before fragmentation
	#
	vertexCoords = [[], [], []]
	for d, t in gmsh.model.getEntities(0):
		xyz = gmsh.model.getValue(0, t, [])
		for k in range(3):
			vertexCoords[k].append(xyz[k])
	vertexCoords = [np.unique(np.round(np.array(v), 12)) for v in vertexCoords]
	for o in objects:
		if o.get('rect'):
			o['rect'] = palaceSnapRect(o['rect'], vertexCoords)
			o['dimtags'] = [(2, palaceMakeRect(o['rect']))]
	gmsh.model.occ.synchronize()

	#
	#	3. one fragmentation of everything
	#
	inputs = []
	for o in objects:
		for dimtag in o['dimtags']:
			inputs.append((dimtag, o))
	inputs.sort(key=lambda x: -x[0][0])     # volumes first
	inputDimtags = [dt for dt, o in inputs]
	out, outmap = gmsh.model.occ.fragment(inputDimtags[:1], inputDimtags[1:])
	gmsh.model.occ.synchronize()
	volumesAfterFragment = set(t for d, t in gmsh.model.getEntities(3))
	gmsh.model.occ.removeAllDuplicates()
	gmsh.model.occ.synchronize()
	if set(t for d, t in gmsh.model.getEntities(3)) != volumesAfterFragment:
		raise Exception("removeAllDuplicates() renumbered volumes after fragmentation, material assignment would be wrong")
	aliveVolumes = set(t for d, t in gmsh.model.getEntities(3))
	aliveFaces = set(t for d, t in gmsh.model.getEntities(2))

	#
	#	4. volume owner: highest role rank (metal > dielectric > air), then highest priority from GUI
	#
	owner = {}
	faceOwner = {}
	faceRank = {'port': 4, 'lumped': 3, 'sheet': 2, 'boundary': 1}
	domainVolumes = set()
	for (dimtag, o), pieces in zip(inputs, outmap):
		for d, t in pieces:
			if d == 3 and t in aliveVolumes:
				if o is domain:
					domainVolumes.add(t)
				rank = (roleRank.get(o['role'], 1), o.get('priority', 0))
				if t not in owner or rank > (roleRank.get(owner[t]['role'], 1), owner[t].get('priority', 0)):
					owner[t] = o
			elif d == 2 and t in aliveFaces and dimtag[0] == 2:
				rank = faceRank.get(o['role'], 1)
				if t not in faceOwner or rank > faceRank.get(faceOwner[t]['role'], 1):
					faceOwner[t] = o

	outside = [t for t in aliveVolumes if t not in domainVolumes]
	if outside:
		gmsh.model.occ.remove([(3, t) for t in outside], recursive=True)
		gmsh.model.occ.synchronize()
		palaceLog(f"{len(outside)} volume parts outside air domain removed")

	# wire feet: faces shared by wire and other metal (current density singularity, CST refines there)
	metalRoles = ['pec', 'conductor', 'boundaryVolume', 'sheetVolume']
	feet = []
	for t, o in owner.items():
		if not o.get('isWire') or t in outside:
			continue
		for d, f in gmsh.model.getBoundary([(3, t)], oriented=False):
			up, down = gmsh.model.getAdjacencies(2, abs(f))
			others = [owner.get(v) for v in up if v != t and v in owner]
			if others and others[0] is not o and others[0]['role'] in metalRoles:
				feet.append((gmsh.model.occ.getCenterOfMass(2, abs(f)), o, others[0]))

	#
	#	5. metals removed, their faces are boundaries
	#
	metalVolumes = [t for t, o in owner.items() if o['role'] in metalRoles and t not in outside]
	metalFaceOwner = {}
	for t in metalVolumes:
		for d, f in gmsh.model.getBoundary([(3, t)], oriented=False):
			metalFaceOwner[abs(f)] = owner[t]
	if metalVolumes:
		gmsh.model.occ.remove([(3, t) for t in metalVolumes], recursive=False)
		gmsh.model.occ.synchronize()

	volumes = [t for d, t in gmsh.model.getEntities(3)]
	faceUse = {}
	for t in volumes:
		for d, f in gmsh.model.getBoundary([(3, t)], oriented=False):
			faceUse[abs(f)] = faceUse.get(abs(f), 0) + 1
		# face inside a volume (ie. port inside substrate under microstrip end) is embedded by fragmentation, not on its boundary,
		# it's interior face meshed on both sides
		for d, f in gmsh.model.mesh.getEmbedded(3, t):
			if d == 2:
				faceUse[f] = faceUse.get(f, 0) + 2
	attached = set(faceUse.keys())
	palaceLog(f"{len(metalVolumes)} metal volume parts removed -> {len(volumes)} volumes, {len(gmsh.model.getEntities(2))} faces")

	#
	#	face groups
	#
	faceGroups = {}       # key -> list of faces, key = ('port', name), ('lumped', name), ('conductivity', group), ('bc', type)
	faceGroupObjects = {}
	def addFace(key, f, o):
		faceGroups.setdefault(key, []).append(f)
		faceGroupObjects.setdefault(key, set()).add(o['name'])

	assigned = set()
	for f, o in faceOwner.items():
		if f not in attached:
			continue
		if o['role'] == 'port':
			addFace(('port', o['group']), f, o)
		elif o['role'] == 'lumped':
			addFace(('lumped', o['group']), f, o)
		elif o['role'] == 'sheet':
			addFace(('bc', 'PEC') if lossyAsPec else ('sheet', o['group']), f, o)
		elif o['role'] == 'boundary':
			addFace(('bc', o['bc']), f, o)
		else:
			continue
		assigned.add(f)

	dropped = 0
	for f, o in metalFaceOwner.items():
		if f in assigned:
			continue
		if f not in attached:
			dropped += 1
			continue
		if o['role'] == 'pec':
			addFace(('bc', 'PEC'), f, o)
		elif o['role'] == 'conductor':
			addFace(('conductor', o['group']), f, o)
		elif o['role'] == 'sheetVolume':
			addFace(('bc', 'PEC') if lossyAsPec else ('sheetVolume', o['group']), f, o)
		elif o['role'] == 'boundaryVolume':
			addFace(('bc', o['bc']), f, o)
		assigned.add(f)
	palaceLog(f"metal faces: {len(metalFaceOwner) - dropped} touching volume, {dropped} free floating dropped (between two removed metals)")

	for f, n in faceUse.items():
		if n == 1 and f not in assigned:
			addFace(('bc', domain['bc']), f, domain)
			assigned.add(f)

	for (kind, name), faces in faceGroups.items():
		if kind == 'port' or kind == 'lumped':
			o = [o for o in objects if o['group'] == name and o['role'] == kind][0]
			if o.get('rect'):
				r = o['rect']
				area = sum(gmsh.model.occ.getMass(2, f) for f in faces)
				rectArea = (r['max'][0] - r['min'][0]) * (r['max'][1] - r['min'][1])
				palaceLog(f"{kind} {name}: {len(faces)} face(s), area {area:.6e} of {rectArea:.6e} mm^2 ({100 * area / rectArea if rectArea else 0:.1f} %)")
	for o in objects:
		if o['role'] in ['port', 'lumped'] and ('port' if o['role'] == 'port' else 'lumped', o['group']) not in faceGroups:
			raise Exception(f"{o['role']} '{o['name']}' has no face in mesh, it must lay inside air domain between conductors")

	#
	#	6. mesh sizes
	#
	M_AIR = settings['airSize']
	gmsh.option.setNumber("Mesh.MeshSizeExtendFromBoundary", 0)
	gmsh.option.setNumber("Mesh.MeshSizeFromPoints", 0)
	gmsh.option.setNumber("Mesh.MeshSizeFromCurvature", settings['curvature'])
	gmsh.option.setNumber("Mesh.Algorithm3D", 10)                       # HXT, parallel
	gmsh.option.setNumber("General.NumThreads", settings.get('threads') or os.cpu_count() or 1)     # 0 = all cores
	gmsh.option.setNumber("Mesh.MeshSizeMax", M_AIR)
	gmsh.option.setNumber("Mesh.Optimize", 1)
	gmsh.option.setNumber("Mesh.OptimizeNetgen", 1)

	# entities of each object after fragmentation
	objectVolumes = {}
	objectFaces = {}
	for t in volumes:
		o = owner.get(t, domain)
		objectVolumes.setdefault(o['name'], []).append(t)
		for d, f in gmsh.model.getBoundary([(3, t)], oriented=False):
			objectFaces.setdefault(o['name'], set()).add(abs(f))
	for key, faces in faceGroups.items():
		for f in faces:
			o = metalFaceOwner.get(f) if f not in faceOwner else faceOwner[f]
			if o is not None:
				objectFaces.setdefault(o['name'], set()).add(f)

	fields = []
	def distanceThreshold(faces, sizeMin, sizeMax, distMin, distMax):
		fd = gmsh.model.mesh.field.add("Distance")
		gmsh.model.mesh.field.setNumbers(fd, "SurfacesList", sorted(faces))
		gmsh.model.mesh.field.setNumber(fd, "Sampling", 120)
		ft = gmsh.model.mesh.field.add("Threshold")
		gmsh.model.mesh.field.setNumber(ft, "InField", fd)
		gmsh.model.mesh.field.setNumber(ft, "SizeMin", sizeMin)
		gmsh.model.mesh.field.setNumber(ft, "SizeMax", sizeMax)
		gmsh.model.mesh.field.setNumber(ft, "DistMin", distMin)
		gmsh.model.mesh.field.setNumber(ft, "DistMax", distMax)
		fields.append(ft)
	def constant(size, faces=(), vols=()):
		fc = gmsh.model.mesh.field.add("Constant")
		if faces:
			gmsh.model.mesh.field.setNumbers(fc, "SurfacesList", sorted(faces))
		if vols:
			gmsh.model.mesh.field.setNumbers(fc, "VolumesList", sorted(vols))
		gmsh.model.mesh.field.setNumber(fc, "IncludeBoundary", 1)
		gmsh.model.mesh.field.setNumber(fc, "VIn", size)
		gmsh.model.mesh.field.setNumber(fc, "VOut", M_AIR)
		fields.append(fc)

	objectSize = {}
	for o in objects:
		# fused objects share entities, smallest sizes of all of them are used
		spec = {}
		for name in o.get('fusedNames', [o['name']]):
			for key, value in model['meshSizes'].get(name, {}).items():
				spec[key] = value if key == 'surface' or key not in spec else min(spec[key], value)
		if not spec:
			continue
		faces = objectFaces.get(o['name'], set())
		vols = objectVolumes.get(o['name'], [])
		if spec.get('size'):
			size = spec['size']
			objectSize[o['name']] = size
			if vols:
				constant(size, vols=vols)
			elif faces:
				# removed metal or surface: size on its faces, from 2.5 elements away it grows into air by sizeGrowth per unit distance
				distMin = 2.5 * size
				distanceThreshold(faces, size, M_AIR, distMin, distMin + max(M_AIR - size, 0) / settings['sizeGrowth'])
		for key in ['boundary', 'face']:
			if spec.get(key) and faces:
				constant(spec[key], faces=faces)
				objectSize.setdefault(o['name'], spec[key])
		if spec.get('volume') and vols:
			constant(spec['volume'], vols=vols)
		if spec.get('surface') and faces:
			sizeMin, sizeMax, distMin, distMax = spec['surface']
			distanceThreshold(faces, sizeMin, sizeMax, distMin, distMax)
			objectSize.setdefault(o['name'], sizeMin)

	# ports: own size, coupled to neighbour group they get either far too many or far too few elements
	for o in objects:
		if o['role'] != 'port':
			continue
		faces = faceGroups.get(('port', o['group']), [])
		size = model['ports'][o['group']].get('meshSize')
		if not size:
			r = o.get('rect')
			size = min(r['max'][0] - r['min'][0], r['max'][1] - r['min'][1]) / settings['portElements'] if r else M_AIR
		for f in faces:
			gmsh.model.mesh.setSize([(0, p) for d, p in gmsh.model.getBoundary([(2, f)], recursive=True)], size)
		constant(size, faces=faces)
		palaceLog(f"port {o['group']}: mesh size {size * 1e3:.3g} um")

	# wire feet: small elements in ball around contact, long transition
	for center, wire, other in feet:
		wireSize = objectSize.get(wire['name'])
		if not wireSize:
			continue
		footSize = min(wireSize, objectSize.get(other['name'], wireSize))
		fb = gmsh.model.mesh.field.add("Ball")
		for k, name in enumerate(["XCenter", "YCenter", "ZCenter"]):
			gmsh.model.mesh.field.setNumber(fb, name, center[k])
		gmsh.model.mesh.field.setNumber(fb, "Radius", settings['footRadius'] * wireSize)
		gmsh.model.mesh.field.setNumber(fb, "Thickness", max(M_AIR - footSize, 0) / settings['footGrowth'])
		gmsh.model.mesh.field.setNumber(fb, "VIn", footSize)
		gmsh.model.mesh.field.setNumber(fb, "VOut", M_AIR)
		fields.append(fb)
	if feet:
		palaceLog(f"{len(feet)} wire feet refined")

	# air inside wire loops: loop inductance (S11, resonance) comes from field in this air, FEM underestimates it when coarse
	wires = [o for o in objects if o.get('isWire') and objectSize.get(o['name'])]
	if wires and settings['loopFactor'] > 0:
		loopSize = settings['loopFactor'] * min(objectSize[o['name']] for o in wires)
		pad = settings['loopPad'] * min(objectSize[o['name']] for o in wires)
		for box in palaceBoxesClusters([o['bbox'] for o in wires]):
			fbx = gmsh.model.mesh.field.add("Box")
			for k, name in enumerate(["XMin", "YMin", "ZMin"]):
				gmsh.model.mesh.field.setNumber(fbx, name, box[k] - pad)
			for k, name in enumerate(["XMax", "YMax", "ZMax"]):
				gmsh.model.mesh.field.setNumber(fbx, name, box[k + 3] + pad)
			gmsh.model.mesh.field.setNumber(fbx, "VIn", loopSize)
			gmsh.model.mesh.field.setNumber(fbx, "VOut", M_AIR)
			gmsh.model.mesh.field.setNumber(fbx, "Thickness", max(M_AIR - loopSize, 0) / settings['loopGrowth'])
			fields.append(fbx)
			palaceLog(f"air inside wire loops refined: {loopSize * 1e3:.3g} um in box x[{box[0]:.4f},{box[3]:.4f}] y[{box[1]:.4f},{box[4]:.4f}] z[{box[2]:.4f},{box[5]:.4f}]")

	for code in model.get('userMeshCode', []):
		exec(code, {'gmsh': gmsh, 'np': np, 'objectVolumes': objectVolumes, 'objectFaces': objectFaces, 'fields': fields})

	if fields:
		fmin = gmsh.model.mesh.field.add("Min")
		gmsh.model.mesh.field.setNumbers(fmin, "FieldsList", fields)
		gmsh.model.mesh.field.setAsBackgroundMesh(fmin)

	#
	#	physical groups, volumes grouped by material, faces by boundary
	#
	groups = {'materials': {}, 'faces': {}, 'faceObjects': {}}
	materialVolumes = {}
	for t in volumes:
		o = owner.get(t, domain)
		materialVolumes.setdefault(domain['material'] if o is domain else o['group'], []).append(t)
	for k, (material, vols) in enumerate(sorted(materialVolumes.items()), start=1):
		gmsh.model.addPhysicalGroup(3, vols, k, name=material)
		groups['materials'][material] = k
	for k, (key, faces) in enumerate(sorted(faceGroups.items()), start=101):
		gmsh.model.addPhysicalGroup(2, sorted(set(faces)), k, name=f"{key[0]}_{key[1]}")
		groups['faces'][key] = k
		groups['faceObjects'][key] = sorted(faceGroupObjects[key])
	palaceLog("physical groups: " + ", ".join([f"{m}={a}" for m, a in groups['materials'].items()] + [f"{k[0]} {k[1]}={a}" for k, a in groups['faces'].items()]))

	#
	#	mesh
	#
	t0 = time.time()
	gmsh.model.mesh.generate(3)
	tets = gmsh.model.mesh.getElementsByType(4)[0]
	quality = np.asarray(gmsh.model.mesh.getElementQualities(tets, "minSICN"))
	palaceLog(f"MESH OK in {time.time() - t0:.1f} s: nodes {len(gmsh.model.mesh.getNodes()[0]):,} tetrahedra {len(tets):,}, " +
	          f"minSICN min {quality.min():.4f} mean {quality.mean():.4f} bad(<0.05) {(quality < 0.05).sum()}")

	#
	#	7. port nodes exactly on port rectangle (Palace lumped port check rel. tolerance 1e-6)
	#
	for o in objects:
		if o['role'] != 'port' or not o.get('rect'):
			continue
		r = o['rect']
		inPlane = [k for k in range(3) if k != r['axis']]
		nsnap = 0
		for f in faceGroups.get(('port', o['group']), []):
			nodeTags, coords, _ = gmsh.model.mesh.getNodes(2, f, includeBoundary=True)
			for k, nodeTag in enumerate(nodeTags):
				xyz = list(coords[3 * k:3 * k + 3])
				new = list(xyz)
				new[r['axis']] = r['coord']
				for j, axis in enumerate(inPlane):
					span = r['max'][j] - r['min'][j]
					for edge in (r['min'][j], r['max'][j]):
						if abs(new[axis] - edge) < 1e-4 * span:
							new[axis] = edge
				if new != xyz:
					gmsh.model.mesh.setNode(nodeTag, new, [])
					nsnap += 1
		palaceLog(f"port {o['group']}: {nsnap} nodes snapped on port rectangle")

	meshFile = model['meshFile']
	if os.path.dirname(meshFile):
		os.makedirs(os.path.dirname(meshFile), exist_ok=True)
	gmsh.option.setNumber("Mesh.MshFileVersion", 2.2)
	gmsh.option.setNumber("Mesh.Binary", 0)
	gmsh.write(meshFile)
	palaceLog(f"mesh written: {meshFile} ({os.path.getsize(meshFile) / 1e6:.1f} MB)")

	return groups

def palaceConfigDomainsAndBoundaries(model, groups):
	"""
	Palace config "Domains" and "Boundaries" for mesh groups from palaceBuildMesh().
	"""
	problemType = model['settings'].get('problemType', 'Driven').lower()
	materials = []
	for name, attribute in groups['materials'].items():
		properties = dict(model['materials'].get(name, {'Permittivity': 1.0}))
		if problemType == 'transient' and 'LossTan' in properties:
			del properties['LossTan']     # transient problem type does not support loss tangent
		materials.append(dict(Attributes=[attribute], **properties))

	boundaries = {}
	for (kind, name), attribute in groups['faces'].items():
		if kind == 'bc':
			boundaries.setdefault(name, {'Attributes': []})['Attributes'].append(attribute)
			if name == 'Absorbing':
				boundaries[name]['Order'] = 2 if problemType == 'driven' else 1
		elif kind == 'conductor':
			boundaries.setdefault('Conductivity', []).append(dict(Attributes=[attribute], **model['conductors'][name]))
		elif kind == 'sheet':
			# surface sheet: conductivity with metal thickness
			boundaries.setdefault('Conductivity', []).append(dict(Attributes=[attribute], **model['sheets'][name]))
		elif kind == 'sheetVolume':
			# faces of removed 3D sheet object: conductivity without thickness, thickness is already the removed volume
			properties = {k: v for k, v in model['sheets'][name].items() if k != 'Thickness'}
			boundaries.setdefault('Conductivity', []).append(dict(Attributes=[attribute], **properties))
		elif kind == 'lumped':
			boundaries.setdefault('Impedance', []).append(dict(Attributes=[attribute], **model['lumpedParts'][name]))

	portAttributes = {name: attribute for (kind, name), attribute in groups['faces'].items() if kind == 'port'}
	ports = []
	for name, port in sorted(model['ports'].items(), key=lambda p: p[1]['Index']):
		entry = {'Index': port['Index'], 'Attributes': [portAttributes[name]]}
		if problemType == 'electrostatic':
			boundaries.setdefault('Terminal', []).append(entry)
		elif problemType == 'magnetostatic':
			entry['Direction'] = port['Direction']
			boundaries.setdefault('SurfaceCurrent', []).append(entry)
		else:
			entry.update({'Direction': port['Direction'], 'R': port['R'], 'Excitation': port['Excitation']})
			boundaries.setdefault('LumpedPort', []).append(entry)

	return {'Materials': materials}, boundaries

def palaceBoundaryObjectAttributes(groups, objectName):
	"""
	Attributes of faces created from given object, ie. far field is computed on absorbing boundary of air box object.
	"""
	return [attribute for key, attribute in groups['faces'].items() if objectName in groups['faceObjects'][key]]

def getHelperFunctionsSource():
	"""
	Source code of helper functions to copy into generated script.
	"""
	genScript = ""
	for func in [palaceLog, palaceMakeRect, palaceSnapRect, palaceBoxesClusters, palaceBuildMesh, palaceConfigDomainsAndBoundaries, palaceBoundaryObjectAttributes]:
		genScript += inspect.getsource(func) + "\n"
	return genScript
