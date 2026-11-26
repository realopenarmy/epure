"""Épure assemblies: read components (XCAF names/instances/placements, or split solids), dedupe identical parts,
spot standard hardware, and build the assembly sheet spec (balloons + BOM, no dimensions)."""
import math, os, re

HW_RE = re.compile(r'\b(bolts?|screws?|nuts?|washers?|rivets?|dowels?|pins?|studs?|circlips?|retaining ring|keys?|'
                   r'shcs|bhcs|fhcs|hhcs|set ?screw|iso ?\d{3,5}|din ?\d{2,4}|m\d+(?:\.\d+)?\s*[x×]\s*\d+)\b', re.I)


def clean_name(n):
    n = (n or '').strip()
    n = re.sub(r'(\s*#\s*\d+|\s*\(\d+\)|[_:\-]\d+|\.\d+)$', '', n)
    return n.strip() or 'PART'


def _props(shape):
    from OCP.GProp import GProp_GProps
    from OCP.BRepGProp import BRepGProp
    g = GProp_GProps(); BRepGProp.VolumeProperties_s(shape, g)
    pp = g.PrincipalProperties(); m = pp.Moments()
    a = GProp_GProps(); BRepGProp.SurfaceProperties_s(shape, a)
    return g.Mass(), a.Mass(), sorted(m)


def signature(shape):
    v, a, m = _props(shape)
    return [round(v, 2), round(a, 2)] + [round(x, 0) for x in m]


def same_sig(s1, s2, rel=1e-3):
    return all(abs(x - y) <= rel * max(abs(x), abs(y), 1e-6) for x, y in zip(s1, s2))


def read(path):
    """-> (instances, structured). instance = dict(name, proto_name, key, proto (local shape), shape (placed))."""
    import geom
    ext = os.path.splitext(path)[1].lower()
    inst = []
    if ext in ('.step', '.stp'):
        from OCP.STEPCAFControl import STEPCAFControl_Reader
        from OCP.TDocStd import TDocStd_Document
        from OCP.TCollection import TCollection_ExtendedString
        from OCP.XCAFDoc import XCAFDoc_DocumentTool
        from OCP.TDF import TDF_LabelSequence, TDF_Label
        from OCP.TDataStd import TDataStd_Name
        from OCP.TopLoc import TopLoc_Location
        from OCP.IFSelect import IFSelect_RetDone
        doc = TDocStd_Document(TCollection_ExtendedString('XmlOcaf'))
        r = STEPCAFControl_Reader(); r.SetNameMode(True)
        if r.ReadFile(path) == IFSelect_RetDone and r.Transfer(doc):
            st = XCAFDoc_DocumentTool.ShapeTool_s(doc.Main())
            def name(l):
                at = TDataStd_Name()
                return at.Get().ToExtString() if l.FindAttribute(TDataStd_Name.GetID_s(), at) else ''
            def walk(l, loc, iname):
                if st.IsAssembly_s(l):
                    cs = TDF_LabelSequence(); st.GetComponents_s(l, cs)
                    for i in range(1, cs.Length() + 1):
                        c = cs.Value(i); ref = TDF_Label(); st.GetReferredShape_s(c, ref)
                        walk(ref, loc.Multiplied(st.GetLocation_s(c)), name(c) or name(ref))
                else:
                    proto = st.GetShape_s(l)
                    if proto.IsNull(): return
                    inst.append(dict(name=iname or name(l), proto_name=name(l), key=f'L{l.Tag()}:{id(l) if False else ""}{name(l)}',
                                     proto=proto, shape=proto.Moved(loc)))
            free = TDF_LabelSequence(); st.GetFreeShapes(free)
            for i in range(1, free.Length() + 1):
                walk(free.Value(i), TopLoc_Location(), '')
    structured = len(inst) > 1
    if not structured:
        shape, is_mesh, _ = geom.load(path)
        from OCP.TopExp import TopExp_Explorer
        from OCP.TopAbs import TopAbs_SOLID
        exp = TopExp_Explorer(shape, TopAbs_SOLID); inst = []
        while exp.More():
            s = exp.Current(); exp.Next()
            inst.append(dict(name=f'SOLID {len(inst) + 1}', proto_name='', key=f'S{len(inst)}', proto=s, shape=s))
    return inst, structured


def hardware_kind(name, shape):
    """Standard hardware by name, else by shape (washer: thin ring; nut: hex prism with a hole; bolt: head + long shank)."""
    m = HW_RE.search(name or '')
    if m: return f'name ({m.group(0)})'
    try:
        import geom
        F = geom.features(shape, False)
        lo, hi = F['bbox']
        holes, cyls = F['holes'], F['cyls']
        from OCP.TopExp import TopExp_Explorer
        from OCP.TopAbs import TopAbs_FACE
        from OCP.BRepAdaptor import BRepAdaptor_Surface
        from OCP.GeomAbs import GeomAbs_Plane
        from OCP.TopoDS import TopoDS
        normals = []
        exp = TopExp_Explorer(shape, TopAbs_FACE)
        while exp.More():
            s = BRepAdaptor_Surface(TopoDS.Face_s(exp.Current())); exp.Next()
            if s.GetType() == GeomAbs_Plane:
                d = s.Plane().Axis().Direction(); normals.append((d.X(), d.Y(), d.Z()))
        def side_planes(ax):
            a = [0, 0, 0]; a['xyz'.index(ax)] = 1
            return sum(1 for n in normals if abs(sum(n[i] * a[i] for i in range(3))) < 1e-6)
        if len(holes) == 1:
            h = holes[0]; ai = 'xyz'.index(h['axis']); L = hi[ai] - lo[ai]
            od = max(hi[i] - lo[i] for i in range(3) if i != ai)
            if side_planes(h['axis']) == 6 and L < od: return 'shape (hex nut)'
            if not side_planes(h['axis']) and len(cyls) == 1 and L < 0.3 * od: return 'shape (washer)'
        if not holes and cyls:
            c = max(cyls, key=lambda c: c['hi'] - c['lo']); ai = 'xyz'.index(c['axis'])
            shank = c['hi'] - c['lo']; L = hi[ai] - lo[ai]
            head = side_planes(c['axis']) == 6 or any(o['axis'] == c['axis'] and o['dia'] > c['dia'] * 1.3 for o in cyls)
            if head and shank >= 2 * c['dia'] and L > shank: return 'shape (bolt/screw)'
    except Exception:
        pass
    return None


def scan(path):
    """Unique components with quantities, item numbers (parts by size, then hardware), hardware flag."""
    import geom
    inst, structured = read(path)
    groups = []
    for i, it in enumerate(inst):
        sig = signature(it['proto'])
        nm = clean_name(it['proto_name'] or it['name'])
        for g in groups:
            if g['key'] == it['key'] or same_sig(g['sig'], sig):
                g['inst'].append(i); break
        else:
            groups.append(dict(key=it['key'], sig=sig, name=nm, inst=[i]))
    for g in groups:
        p = inst[g['inst'][0]]['proto']
        g['volume'] = g['sig'][0]
        lo, hi = geom.bbox(p); g['size'] = [round(hi[j] - lo[j], 3) for j in range(3)]
        g['hardware'] = hardware_kind(g['name'], p)
    groups.sort(key=lambda g: (bool(g['hardware']), -g['volume'], g['name']))
    for n, g in enumerate(groups, start=1): g['item'] = n
    return inst, groups, structured


def write_step(shape, path):
    from OCP.STEPControl import STEPControl_Writer, STEPControl_AsIs
    import os, sys
    sys.stdout.flush(); fd = os.dup(1); nul = os.open(os.devnull, os.O_WRONLY)
    os.dup2(nul, 1)      # OCCT prints transfer statistics straight to fd 1; keep the JSON on stdout clean
    try:
        w = STEPControl_Writer(); w.Transfer(shape, STEPControl_AsIs); w.Write(path)
    finally:
        os.dup2(fd, 1); os.close(fd); os.close(nul)


def compound(shapes):
    from OCP.TopoDS import TopoDS_Compound
    from OCP.BRep import BRep_Builder
    c = TopoDS_Compound(); b = BRep_Builder(); b.MakeCompound(c)
    for s in shapes: b.Add(c, s)
    return c


def anchor_points(inst, groups, comp_vis, view, comp=None, budget=2.0, max_edges=40):
    """For each item: a view (u, v) point in the middle of the longest stretch of one of its 3D edges that is really
    visible - on a drawn line of the whole-assembly view AND with a clear ray to the viewer (nothing in front).
    Bounded: one instance per item (others only if it shows nothing), its `max_edges` longest edges (projected),
    rays tested only against parts that can be in front, early stop on a good run, `budget` seconds per item."""
    import geom, time
    from OCP.TopExp import TopExp
    from OCP.TopTools import TopTools_IndexedMapOfShape
    from OCP.TopAbs import TopAbs_EDGE
    from OCP.TopoDS import TopoDS
    from OCP.BRepAdaptor import BRepAdaptor_Curve
    from OCP.IntCurvesFace import IntCurvesFace_ShapeIntersector
    from OCP.gp import gp_Lin, gp_Pnt, gp_Dir
    from OCP.Bnd import Bnd_Box
    from OCP.BRepBndLib import BRepBndLib
    N = geom.frame(view)[0]
    cell = 2.0
    segs = {}
    for p in comp_vis:
        pts = geom.prim_points(p, 64)
        for a, b in zip(pts, pts[1:]):
            x0, x1 = sorted((a[0], b[0])); y0, y1 = sorted((a[1], b[1]))
            for cx in range(math.floor(x0 / cell) - 1, math.floor(x1 / cell) + 2):
                for cy in range(math.floor(y0 / cell) - 1, math.floor(y1 / cell) + 2):
                    segs.setdefault((cx, cy), []).append((a, b))

    def dseg(q, a, b):
        dx, dy = b[0] - a[0], b[1] - a[1]; L2 = dx * dx + dy * dy
        t = 0 if L2 == 0 else max(0, min(1, ((q[0] - a[0]) * dx + (q[1] - a[1]) * dy) / L2))
        return math.hypot(q[0] - a[0] - t * dx, q[1] - a[1] - t * dy)

    def on_line(q, tol=0.15):
        return any(dseg(q, a, b) < tol for a, b in segs.get((math.floor(q[0] / cell), math.floor(q[1] / cell)), []))

    box2, depth = [], []
    for it in inst:
        bb = Bnd_Box(); BRepBndLib.Add_s(it['shape'], bb); x0, y0, z0, x1, y1, z1 = bb.Get()
        cs = [(x, y, z) for x in (x0, x1) for y in (y0, y1) for z in (z0, z1)]
        pr = [geom.project(c, view) for c in cs]
        box2.append((min(q[0] for q in pr), min(q[1] for q in pr), max(q[0] for q in pr), max(q[1] for q in pr)))
        dn = [sum(c[k] * N[k] for k in range(3)) for c in cs]; depth.append((min(dn), max(dn)))
    rays = {}

    def ray_of(jj):
        if jj not in rays:
            r = IntCurvesFace_ShapeIntersector(); r.Load(inst[jj]['shape'], 1e-4); rays[jj] = r
        return rays[jj]

    def clear(p3, q2):
        d = sum(p3[k] * N[k] for k in range(3))
        o = gp_Pnt(p3[0] + N[0] * 0.02, p3[1] + N[1] * 0.02, p3[2] + N[2] * 0.02); ln = gp_Lin(o, gp_Dir(*N))
        for jj, b in enumerate(box2):      # only parts whose outline covers the point and that reach in front of it
            if not (b[0] - 0.5 <= q2[0] <= b[2] + 0.5 and b[1] - 0.5 <= q2[1] <= b[3] + 0.5): continue
            if depth[jj][1] < d + 0.01: continue
            r = ray_of(jj); r.Perform(ln, 0.0, 1e7)
            if r.NbPnt() > 0: return False
        return True

    item_of = {ii: g['item'] for g in groups for ii in g['inst']}

    def crowded(q, item):     # inside another item's outline box -> a reader can't tell which part the dot is on
        return any(item_of.get(jj) != item and b[0] - 1 <= q[0] <= b[2] + 1 and b[1] - 1 <= q[1] <= b[3] + 1
                   for jj, b in enumerate(box2))

    def runs(flags):
        out, run, start = [], 0, 0
        for j, f in enumerate(flags + [False]):
            if f:
                if run == 0: start = j
                run += 1
            else:
                if run: out.append((run, start))
                run = 0
        return sorted(out, reverse=True)

    n = 16; good = int(0.6 * (n + 1) + 0.999)
    out = {}
    for g in groups:
        t_end = time.monotonic() + budget
        best = fallback = None; done = False
        for ii in g['inst']:
            m = TopTools_IndexedMapOfShape(); TopExp.MapShapes_s(inst[ii]['shape'], TopAbs_EDGE, m)
            edges = []
            for k in range(1, m.Extent() + 1):
                try:
                    c = BRepAdaptor_Curve(TopoDS.Edge_s(m.FindKey(k)))
                except Exception:
                    continue
                t0, t1 = c.FirstParameter(), c.LastParameter()
                P3 = []
                for j in range(n + 1):
                    q = c.Value(t0 + (t1 - t0) * j / n); P3.append((q.X(), q.Y(), q.Z()))
                P2 = [geom.project(q, view) for q in P3]
                edges.append((sum(math.dist(a, b) for a, b in zip(P2, P2[1:])), P3, P2))
            edges.sort(key=lambda e: -e[0])
            for _, P3, P2 in edges[:max_edges]:
                if time.monotonic() > t_end: done = True; break
                onl = [on_line(q2) for q2 in P2]
                rs = [r for r in runs(onl) if r[0] >= 3]
                if not rs: continue
                if fallback is None or rs[0][0] > fallback[0]:
                    fallback = (rs[0][0], P2[rs[0][1] + rs[0][0] // 2])
                if best and sum(math.dist(a, b) for a, b in zip(P2[rs[0][1]:rs[0][1] + rs[0][0]],
                                                                 P2[rs[0][1] + 1:rs[0][1] + rs[0][0]])) <= best[0]:
                    continue        # even fully clear, this edge can't beat what we have
                flags = list(onl)
                for ln_, st in rs:
                    for j in range(st, st + ln_): flags[j] = clear(P3[j], P2[j])
                cr = runs(flags)
                if not cr or cr[0][0] < 3: continue
                run, start = cr[0]
                L = sum(math.dist(a, b) for a, b in zip(P2[start:start + run], P2[start + 1:start + run]))
                run_pts = P2[start:start + run]; mid = run // 2
                order = sorted(range(1, len(run_pts) - 1) or [0], key=lambda j: abs(j - mid))
                q = next((run_pts[j] for j in order if not crowded(run_pts[j], g['item'])), None)
                score = L if q is not None else 0.2 * L
                q = q or run_pts[mid]
                if best is None or score > best[0]: best = (score, q)
                if run >= good and score == L: done = True; break
            if best or done: break
        pick = best[1] if best else (fallback[1] if fallback and time.monotonic() > t_end else None)
        if pick: out[g['item']] = [pick[0], pick[1]]
    return out
