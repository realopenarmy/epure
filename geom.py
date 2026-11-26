"""Épure geometry kernel: load CAD/mesh, extract features, hidden-line projections.
All model coordinates are millimetres. View 2D coords: u = p.X, v = p.(N x X)."""
import math, os, tempfile
from OCP.STEPControl import STEPControl_Reader
from OCP.IGESControl import IGESControl_Reader
from OCP.StlAPI import StlAPI_Reader
from OCP.BRepTools import BRepTools
from OCP.BRep import BRep_Builder
from OCP.TopoDS import TopoDS_Shape, TopoDS
from OCP.IFSelect import IFSelect_RetDone
from OCP.ShapeUpgrade import ShapeUpgrade_UnifySameDomain
from OCP.Bnd import Bnd_Box
from OCP.BRepBndLib import BRepBndLib
from OCP.TopExp import TopExp_Explorer
from OCP.TopAbs import TopAbs_FACE, TopAbs_EDGE, TopAbs_WIRE, TopAbs_REVERSED, TopAbs_OUT, TopAbs_SOLID, TopAbs_SHELL
from OCP.BRepAdaptor import BRepAdaptor_Surface, BRepAdaptor_Curve
from OCP.GeomAbs import GeomAbs_Cylinder, GeomAbs_Plane, GeomAbs_Line, GeomAbs_Circle, GeomAbs_Cone, GeomAbs_Torus
from OCP.gp import gp_Pnt, gp_Dir, gp_Ax2, gp_Vec
from OCP.HLRBRep import HLRBRep_Algo, HLRBRep_HLRToShape
from OCP.HLRAlgo import HLRAlgo_Projector
from OCP.GCPnts import GCPnts_QuasiUniformDeflection
from OCP.BRepClass3d import BRepClass3d_SolidClassifier
from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
from OCP.BRepAlgoAPI import BRepAlgoAPI_Common
from OCP.BRepGProp import BRepGProp
from OCP.GProp import GProp_GProps
from OCP.BRepTools import BRepTools_WireExplorer
from OCP.BRepBuilderAPI import BRepBuilderAPI_Sewing, BRepBuilderAPI_MakeSolid
from OCP.BRepMesh import BRepMesh_IncrementalMesh

SOLID_EXT = {'.step': 'step', '.stp': 'step', '.iges': 'iges', '.igs': 'iges', '.brep': 'brep', '.brp': 'brep'}
MESH_EXT = {'.stl': 'stl', '.obj': 'obj'}
NATIVE_EXT = {'.f3d', '.f3z', '.sldprt', '.sldasm', '.ipt', '.iam', '.prt', '.catpart', '.par', '.x_t', '.x_b',
              '.3dm', '.skp', '.fcstd', '.dwg', '.asm', '.jt', '.sat', '.3mf', '.ply', '.glb', '.gltf', '.fbx'}

# view frames: (N toward viewer, X = u axis). v = N x X
VIEW_FRAMES = {
    'front': ((0, -1, 0), (1, 0, 0)),   # u=x v=z
    'top':   ((0, 0, 1), (1, 0, 0)),    # u=x v=y
    'right': ((1, 0, 0), (0, 1, 0)),    # u=y v=z
    'iso':   ((1, -1, 1), (1, 1, 0)),
}
# which 3D axis maps to u / v in each ortho frame
VIEW_AXES = {'front': ('x', 'z'), 'top': ('x', 'y'), 'right': ('y', 'z')}
SECTION_FRAME = {'y': 'front', 'x': 'right', 'z': 'top'}  # section along plane normal -> frame
AXI = {'x': 0, 'y': 1, 'z': 2}


class LoadError(Exception):
    pass


def _norm(v):
    l = math.sqrt(sum(c * c for c in v)); return tuple(c / l for c in v)


def _cross(a, b):
    return (a[1]*b[2]-a[2]*b[1], a[2]*b[0]-a[0]*b[2], a[0]*b[1]-a[1]*b[0])


def frame(name):
    N, X = VIEW_FRAMES[name]
    N, X = _norm(N), _norm(X)
    return N, X, _cross(N, X)


def project(p, name):
    N, X, Y = frame(name)
    return (sum(p[i]*X[i] for i in range(3)), sum(p[i]*Y[i] for i in range(3)))


def load(path, model_units='mm'):
    """Return (shape, is_mesh, warnings)."""
    ext = os.path.splitext(path)[1].lower()
    warns = []
    if not os.path.exists(path):
        raise LoadError(f'file not found: {path}')
    if ext in NATIVE_EXT:
        raise LoadError(f'{ext} is a native/proprietary format that cannot be read headless. '
                        'Please export STEP (.step/.stp) from your CAD app (IGES or BREP also work).')
    if ext in SOLID_EXT:
        kind = SOLID_EXT[ext]
        if kind == 'step':
            r = STEPControl_Reader()
            if r.ReadFile(path) != IFSelect_RetDone:
                raise LoadError('STEP read failed')
            r.TransferRoots(); shape = r.OneShape()
        elif kind == 'iges':
            r = IGESControl_Reader()
            if r.ReadFile(path) != IFSelect_RetDone:
                raise LoadError('IGES read failed')
            r.TransferRoots(); shape = r.OneShape()
        else:
            shape = TopoDS_Shape(); BRepTools.Read_s(shape, path, BRep_Builder())
            if model_units != 'mm':
                shape = _scale(shape, 25.4)
        if shape.IsNull():
            raise LoadError('no geometry found in file')
        if kind == 'iges':
            exp = TopExp_Explorer(shape, TopAbs_SOLID)
            if not exp.More():
                warns.append('IGES file has surfaces but no closed solid; hole/thru detection may be unreliable.')
        return shape, False, warns
    if ext in MESH_EXT:
        stl = path
        if ext == '.obj':
            import trimesh
            m = trimesh.load(path, force='mesh')
            stl = tempfile.mktemp(suffix='.stl'); m.export(stl)
        shape = TopoDS_Shape()
        if not StlAPI_Reader().Read(shape, stl):
            raise LoadError('mesh read failed')
        sew = BRepBuilderAPI_Sewing(1e-4); sew.Add(shape); sew.Perform(); shape = sew.SewedShape()
        u = ShapeUpgrade_UnifySameDomain(shape, True, True, True); u.Build(); shape = u.Shape()
        try:
            exp = TopExp_Explorer(shape, TopAbs_SHELL)
            if exp.More():
                ms = BRepBuilderAPI_MakeSolid(TopoDS.Shell_s(exp.Current()))
                if ms.IsDone(): shape = ms.Solid()
        except Exception:
            pass
        if model_units != 'mm':
            shape = _scale(shape, 25.4)
        warns.append('Mesh input (STL/OBJ): curved surfaces are faceted, holes are not recognised as holes, '
                     'and all dimensions are approximate. Export STEP for an exact drawing.')
        return shape, True, warns
    raise LoadError(f'unsupported file type {ext}; send STEP/STP, IGES, BREP, STL or OBJ')


def _scale(shape, f):
    from OCP.gp import gp_Trsf
    from OCP.BRepBuilderAPI import BRepBuilderAPI_Transform
    t = gp_Trsf(); t.SetScale(gp_Pnt(0, 0, 0), f)
    return BRepBuilderAPI_Transform(shape, t, True).Shape()


def mesh_deflection(shape):
    b = Bnd_Box(); BRepBndLib.Add_s(shape, b, False); x0, y0, z0, x1, y1, z1 = b.Get()
    return max(0.01, min(0.2, max(x1 - x0, y1 - y0, z1 - z0) / 5000))


def bbox(shape, fast=False):
    """fast: box of a fine mesh (seconds on big assemblies; exact AddOptimal can take minutes on helical threads)."""
    b = Bnd_Box(); b.SetGap(0)
    if fast:
        BRepMesh_IncrementalMesh(shape, mesh_deflection(shape), False, 0.35, True)
        BRepBndLib.Add_s(shape, b, True)
    else:
        BRepBndLib.AddOptimal_s(shape, b, False, False)
    x0, y0, z0, x1, y1, z1 = b.Get()
    return [x0, y0, z0], [x1, y1, z1]


def _axis_name(d, tol=1e-6):
    for i, n in enumerate('xyz'):
        if abs(abs(d[i]) - 1) < tol:
            return n
    return None


def _is_out(classifier_shape, p):
    c = BRepClass3d_SolidClassifier(classifier_shape, gp_Pnt(*p), 1e-6)
    return c.State() == TopAbs_OUT


def features(shape, is_mesh):
    """Detect holes, bosses (external cylinders), slots and axis-aligned plane levels."""
    lo, hi = bbox(shape)
    ext = [hi[i] - lo[i] for i in range(3)]
    cyls = []
    planes = []
    slanted, cones, tori = [], [], []
    exp = TopExp_Explorer(shape, TopAbs_FACE)
    while exp.More():
        f = TopoDS.Face_s(exp.Current()); exp.Next()
        s = BRepAdaptor_Surface(f)
        t = s.GetType()
        umin, umax, vmin, vmax = BRepTools.UVBounds_s(f)
        if t == GeomAbs_Cylinder:
            c = s.Cylinder(); ax = c.Axis(); d = ax.Direction(); l = ax.Location()
            dv = (d.X(), d.Y(), d.Z()); an = _axis_name(dv)
            r = c.Radius()
            um, vm = (umin+umax)/2, (vmin+vmax)/2
            p = s.Value(um, vm)
            # point on axis at same height
            P = (p.X(), p.Y(), p.Z()); L = (l.X(), l.Y(), l.Z())
            tproj = sum((P[i]-L[i])*dv[i] for i in range(3))
            A = tuple(L[i]+dv[i]*tproj for i in range(3))
            radial = _norm(tuple(P[i]-A[i] for i in range(3)))
            from OCP.BRepLProp import BRepLProp_SLProps
            props = BRepLProp_SLProps(s, um, vm, 1, 1e-6)
            n = props.Normal(); nv = (n.X(), n.Y(), n.Z())
            if f.Orientation() == TopAbs_REVERSED:
                nv = tuple(-c_ for c_ in nv)
            internal = sum(nv[i]*radial[i] for i in range(3)) < 0
            # axial extent: use v range
            a0 = tuple(L[i]+dv[i]*vmin for i in range(3)); a1 = tuple(L[i]+dv[i]*vmax for i in range(3))
            cyls.append(dict(axis=an, dir=dv, r=r, internal=internal, span=umax-umin, p0=a0, p1=a1, rpt=P))
        elif t == GeomAbs_Plane:
            pl = s.Plane(); d = pl.Axis().Direction(); an = _axis_name((d.X(), d.Y(), d.Z()), 1e-5)
            if an:
                o = pl.Location(); lev = (o.X(), o.Y(), o.Z())[AXI[an]]
                g = GProp_GProps(); BRepGProp.SurfaceProperties_s(f, g)
                planes.append(dict(axis=an, level=lev, area=g.Mass()))
            else:
                g = GProp_GProps(); BRepGProp.SurfaceProperties_s(f, g); cm = g.CentreOfMass()
                fb = Bnd_Box(); fb.SetGap(0); BRepBndLib.AddOptimal_s(f, fb, False, False)
                slanted.append(dict(n=(d.X(), d.Y(), d.Z()), centroid=[cm.X(), cm.Y(), cm.Z()], bb=list(fb.Get()), area=g.Mass()))
        elif t == GeomAbs_Cone:
            c = s.Cone(); ax = c.Axis(); d = ax.Direction(); l = ax.Location()
            dv = (d.X(), d.Y(), d.Z()); an = _axis_name(dv)
            fb = Bnd_Box(); fb.SetGap(0); BRepBndLib.AddOptimal_s(f, fb, False, False)
            cones.append(dict(axis=an, loc=[l.X(), l.Y(), l.Z()], semi=abs(math.degrees(c.SemiAngle())), bb=list(fb.Get()),
                              span=umax-umin, rref=c.RefRadius()))
        elif t == GeomAbs_Torus:
            tr = s.Torus(); ax = tr.Axis(); d = ax.Direction(); l = ax.Location()
            um, vm = (umin+umax)/2, (vmin+vmax)/2; p = s.Value(um, vm)
            tori.append(dict(axis=_axis_name((d.X(), d.Y(), d.Z())), loc=[l.X(), l.Y(), l.Z()], R=tr.MajorRadius(),
                             r=tr.MinorRadius(), vspan=vmax-vmin, uspan=umax-umin, pt=[p.X(), p.Y(), p.Z()]))
    # merge cylinders sharing axis line & radius
    groups = []
    for c in cyls:
        if c['axis'] is None:
            continue
        ai = AXI[c['axis']]
        key_pt = [c['p0'][i] for i in range(3) if i != ai]
        for g in groups:
            if g['axis'] == c['axis'] and abs(g['r']-c['r']) < 1e-4 and g['internal'] == c['internal'] and \
               all(abs(a-b) < 1e-4 for a, b in zip(g['key'], key_pt)):
                g['span'] += c['span']
                g['lo'] = min(g['lo'], c['p0'][ai], c['p1'][ai]); g['hi'] = max(g['hi'], c['p0'][ai], c['p1'][ai])
                g['rpts'].append(c['rpt'])
                break
        else:
            groups.append(dict(axis=c['axis'], r=c['r'], internal=c['internal'], span=c['span'], key=key_pt,
                               lo=min(c['p0'][ai], c['p1'][ai]), hi=max(c['p0'][ai], c['p1'][ai]), rpts=[c['rpt']]))
    holes, bosses, halfs, arcs = [], [], [], []
    for g in groups:
        ai = AXI[g['axis']]
        ctr = []
        k = 0
        for i in range(3):
            if i == ai: ctr.append((g['lo']+g['hi'])/2)
            else: ctr.append(g['key'][k]); k += 1
        g['center'] = ctr
        if g['span'] > 2*math.pi - 0.05:
            (holes if g['internal'] else bosses).append(g)
        elif abs(g['span'] - math.pi) < 0.05 and g['internal']:
            halfs.append(g)
        else:
            arcs.append(g)
    # thru test for holes
    out = []
    for i, h in enumerate(sorted(holes, key=lambda h: (h['axis'], round(-h['center'][1], 3), round(h['center'][0], 3)))):
        ai = AXI[h['axis']]
        def pt(val):
            p = list(h['center']); p[ai] = val; return p
        eps = 0.05
        thru = False
        if not is_mesh:
            try:
                def open_end(val):
                    # end is open if points just past it, near the hole wall, are outside material
                    o = [i for i in range(3) if i != ai]
                    for du, dv in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                        p = pt(val); p[o[0]] += du*h['r']*0.97; p[o[1]] += dv*h['r']*0.97
                        if not _is_out(shape, p): return False
                    return True
                thru = open_end(h['lo']-eps) and open_end(h['hi']+eps)
            except Exception:
                thru = False
        out.append(dict(id=f'H{i+1}', type='hole', axis=h['axis'], center=h['center'], dia=2*h['r'],
                        depth=h['hi']-h['lo'], lo=h['lo'], hi=h['hi'], thru=thru))
    bout = []
    for i, b in enumerate(sorted(bosses, key=lambda b: (b['axis'], b['lo']))):
        bout.append(dict(id=f'C{i+1}', type='cyl', axis=b['axis'], center=b['center'], dia=2*b['r'],
                         lo=b['lo'], hi=b['hi']))
    # slots: pair half cylinders with same axis dir & radius, sharing axial range
    slots = []
    used = set()
    for i, a in enumerate(halfs):
        if i in used: continue
        best = None
        for j, b in enumerate(halfs):
            if j <= i or j in used: continue
            if a['axis'] == b['axis'] and abs(a['r']-b['r']) < 1e-4 and abs(a['lo']-b['lo']) < 1e-3 and abs(a['hi']-b['hi']) < 1e-3:
                d = math.dist(a['center'], b['center'])
                if best is None or d < best[0]: best = (d, j)
        if best:
            used.add(i); used.add(best[1]); b = halfs[best[1]]
            slots.append((a, b))
    sout = []
    for i, (a, b) in enumerate(slots):
        sout.append(dict(id=f'S{i+1}', type='slot', axis=a['axis'], c1=a['center'], c2=b['center'],
                         center=[(a['center'][k]+b['center'][k])/2 for k in range(3)],
                         width=2*a['r'], length=math.dist(a['center'], b['center']), lo=a['lo'], hi=a['hi']))
    # plane levels, exclude slot side faces and hole/counterbore floors (their depth is in the hole callout)
    levels = {'x': {}, 'y': {}, 'z': {}}
    for p in planes:
        skip = False
        for h in out:
            if h['axis'] == p['axis'] and min(abs(p['level']-h['lo']), abs(p['level']-h['hi'])) < 1e-4:
                ring = [math.pi*(h['dia']/2)**2] + [math.pi*((h2['dia']/2)**2 - (h['dia']/2)**2) for h2 in out
                                                     if h2['axis'] == h['axis'] and h2['dia'] < h['dia']]
                ring = [abs(a_) for a_ in ring]
                # exact floor/annulus, or a small ring around the hole (counterbore floor, merged into the hole callout)
                if any(abs(p['area'] - a_) <= 0.01*a_ + 1e-6 for a_ in ring) or p['area'] < math.pi * h['dia']**2: skip = True
        for s in sout:
            ai = AXI[p['axis']]
            if abs(s['c1'][ai]-s['c2'][ai]) < 1e-6 and AXI[s['axis']] != ai and \
               min(abs(p['level']-(s['c1'][ai]+s['width']/2)), abs(p['level']-(s['c1'][ai]-s['width']/2))) < 1e-4:
                skip = True
        if skip: continue
        key = round(p['level'], 4)
        levels[p['axis']][key] = levels[p['axis']].get(key, 0) + p['area']
    for i, h in enumerate(halfs):
        if i not in used: arcs.append(h)
    if is_mesh:   # triangle facets: every facet strip looks like a chamfer/slope - don't guess
        blends, chamfers, slopes, warns = [], [], [], ['mesh model: fillets, chamfers and slopes are not detected']
    else:
        blends, chamfers, slopes, warns = _blends_chamfers(arcs, tori, cones, slanted, out, bout, lo, hi)
    bcs, bwarn = _bolt_circles(out); warns += bwarn
    return dict(bbox=[lo, hi], holes=out, cyls=bout, slots=sout, fillets=blends, chamfers=chamfers, slopes=slopes, bolt_circles=bcs, feature_warnings=warns,
                levels={k: sorted(([lv, ar] for lv, ar in v.items()), key=lambda t: t[0]) for k, v in levels.items()})


STD_INCL = [60, 82, 90, 100, 118, 120]    # common countersink / drill-point included angles


def _blends_chamfers(arcs, tori, cones, slanted, holes, bosses, lo, hi):
    """Fillets/rounds (partial cylinders, toroidal blends) and chamfers (small bevel planes, short cones)."""
    ext = [hi[i] - lo[i] for i in range(3)]
    warns = []
    fil = []
    for g in arcs:
        if g['axis'] is None: continue
        ai = AXI[g['axis']]
        apt = list(g['rpts'][0])
        fil.append(dict(type='fillet', kind='cyl', axis=g['axis'], r=g['r'], center=g['center'], apt=apt,
                        lo=g['lo'], hi=g['hi'], concave=g['internal']))
    ntor_skip = 0
    for t in tori:
        if t['axis'] is None or t['vspan'] > math.pi * 0.75 or t['r'] > 0.5 * max(ext):
            ntor_skip += 1; continue
        ai = AXI[t['axis']]
        # anchor: rotate the face point onto the +u silhouette later (store radial distance and height)
        p = t['pt']; c = t['loc']
        rho = math.sqrt(sum((p[i]-c[i])**2 for i in range(3) if i != ai))
        fil.append(dict(type='fillet', kind='torus', axis=t['axis'], r=t['r'], center=c, rho=rho, h=p[ai],
                        apt=p, lo=p[ai], hi=p[ai], full=t['uspan'] > 2*math.pi - 0.05))
    if ntor_skip: warns.append(f'{ntor_skip} doubly-curved blend face(s) (corner patches / non-axis tori) not called out')
    for i, f in enumerate(fil): f['id'] = f'F{i+1}'
    ch, slopes = [], []
    ncomp = 0
    for p in slanted:
        n = p['n']; zero = [i for i in range(3) if abs(n[i]) < 1e-6]
        if len(zero) != 1:
            ncomp += 1
            continue  # compound-angle face: not a simple chamfer
        c = zero[0]; a, b = [i for i in range(3) if i != c]
        bb = p['bb']; la = bb[a+3]-bb[a]; lb = bb[b+3]-bb[b]; lc = bb[c+3]-bb[c]
        lim = 0.25 * min(ext[a], ext[b])
        if max(la, lb) <= lim + 1e-9 and lc >= 1.5 * max(la, lb):
            ang = math.degrees(math.atan2(lb, la))
            ch.append(dict(type='chamfer', kind='plane', edge_axis='xyz'[c], legs=[la, lb], leg_axes=['xyz'[a], 'xyz'[b]],
                           angle=ang, apt=p['centroid'], center=p['centroid']))
        else:
            # slope seen edge-on along axis c: segment between opposite corners of its a/b extent
            p0 = list(p['centroid']); p1 = list(p['centroid'])
            if n[a] * n[b] > 0:
                p0[a], p0[b], p1[a], p1[b] = bb[a], bb[b+3], bb[a+3], bb[b]
            else:
                p0[a], p0[b], p1[a], p1[b] = bb[a], bb[b], bb[a+3], bb[b+3]
            ang = math.degrees(math.atan2(lb, la))
            slopes.append(dict(type='slope', edge_axis='xyz'[c], n=list(n), p0=p0, p1=p1, angle_from=['xyz'[a], ang],
                               center=p['centroid'], apt=p['centroid'], area=p['area']))
    for cn in cones:
        if cn['axis'] is None or cn['span'] < 2*math.pi - 0.05: continue
        ai = AXI[cn['axis']]; bb = cn['bb']
        axial = bb[ai+3]-bb[ai]
        o = [i for i in range(3) if i != ai]
        rmax = (bb[o[0]+3]-bb[o[0]])/2
        radial = axial * math.tan(math.radians(cn['semi']))
        ctr = list(cn['loc']); ctr[o[0]] = (bb[o[0]]+bb[o[0]+3])/2; ctr[o[1]] = (bb[o[1]]+bb[o[1]+3])/2
        ctr[ai] = (bb[ai]+bb[ai+3])/2
        # coaxial holes touching either end of the cone (hole chamfer, countersink, counterdrill)
        linked = []
        for h in holes:
            if h['axis'] == cn['axis'] and all(abs(h['center'][j]-ctr[j]) < 1e-3 for j in o) and \
               (abs(h['lo']-bb[ai]) < 1e-3 or abs(h['hi']-bb[ai+3]) < 1e-3 or abs(h['lo']-bb[ai+3]) < 1e-3 or abs(h['hi']-bb[ai]) < 1e-3):
                linked.append(h)
        linked.sort(key=lambda h: h['dia'])
        hole = linked[0]['id'] if linked else None
        kind = 'cone'
        if len(linked) >= 2:
            kind = 'cdrill'          # cone joining a smaller hole to a larger blind hole
        elif linked and rmax >= 1.25 * linked[0]['dia'] / 2 and 25 <= cn['semi'] <= 65:
            kind = 'csk'
        elif axial > 0.25 * max(rmax, 1e-6) and axial > 0.15 * ext[ai]:
            continue  # a real taper, not a chamfer
        incl = 2 * cn['semi']
        snap = min(STD_INCL, key=lambda a: abs(a - incl))
        ch.append(dict(type='chamfer', kind='cone', sub=kind, axis=cn['axis'], legs=[axial, radial], angle=cn['semi'],
                       included=snap if abs(snap - incl) <= 1.0 else round(incl, 1), included_measured=incl,
                       center=ctr, rmax=rmax, rmin=rmax - radial, lo=bb[ai], hi=bb[ai+3], hole=hole,
                       holes=[h['id'] for h in linked], apt=ctr))
    for i, c in enumerate(ch): c['id'] = f'K{i+1}'
    for i, c in enumerate(slopes): c['id'] = f'P{i+1}'
    if ncomp: warns.append(f'{ncomp} compound-angle face(s) (tilted on two axes) not dimensioned')
    # counterdrill: the larger hole ends in the cone, so it is blind even though the radial probe sees the cone void
    for c in ch:
        if c.get('sub') == 'cdrill':
            for h in holes:
                if h['id'] in c['holes'][1:]: h['thru'] = False
    return fil, ch, slopes, warns


def _bolt_circles(holes):
    """3+ identical parallel holes equidistant from a common centre and evenly spaced -> bolt circle."""
    groups = {}
    for h in holes:
        groups.setdefault((h['axis'], round(h['dia'], 3), h['thru'], round(h['depth'], 3)), []).append(h)
    bcs, warns = [], []
    for key, hs in groups.items():
        if len(hs) < 3: continue
        ai = AXI[key[0]]; o = [i for i in range(3) if i != ai]
        cx = sum(h['center'][o[0]] for h in hs) / len(hs); cy = sum(h['center'][o[1]] for h in hs) / len(hs)
        try:   # least-squares circle fit (centroid is only the centre when spacing is even)
            import numpy as np
            P = np.array([[h['center'][o[0]] - cx, h['center'][o[1]] - cy] for h in hs])
            A = np.c_[2*P, np.ones(len(P))]; sol = np.linalg.lstsq(A, (P**2).sum(1), rcond=None)[0]
            if np.linalg.matrix_rank(A) == 3: cx, cy = cx + sol[0], cy + sol[1]
        except Exception:
            pass
        rs = [math.hypot(h['center'][o[0]] - cx, h['center'][o[1]] - cy) for h in hs]
        R = sum(rs) / len(rs)
        if R < 1e-6 or max(abs(r - R) for r in rs) > 1e-3 + 1e-4 * R: continue
        angs = sorted(math.atan2(h['center'][o[1]] - cy, h['center'][o[0]] - cx) for h in hs)
        gaps = [(angs[(i+1) % len(angs)] - angs[i]) % (2*math.pi) for i in range(len(angs))]
        even = max(abs(g - 2*math.pi/len(hs)) for g in gaps) < 1e-3
        ids = [h['id'] for h in hs]
        if not even:
            if len(hs) != 4:
                warns.append(f"holes {','.join(ids)} lie on a Ø{2*R:.2f} circle but are not evenly spaced; located by X/Y instead")
            continue
        c = [0, 0, 0]; c[o[0]] = cx; c[o[1]] = cy; c[ai] = hs[0]['center'][ai]
        start = min((math.degrees(a) % 360 for a in angs))
        bcs.append(dict(id=f'BC{len(bcs)+1}', type='bc', axis=key[0], center=c, dia=2*R, n=len(hs), holes=ids,
                        start_angle=start, lo=hs[0]['lo'], hi=hs[0]['hi']))
    return bcs, warns


# ---------------------------------------------------------------- threads
def scan_step_threads(path):
    """OCCT does not import AP242 hole/thread features, so scan the raw STEP text for thread designations."""
    import re
    try:
        txt = open(path, 'r', errors='ignore').read()
    except Exception:
        return []
    found = set()
    for m in re.finditer(r"\bM(\d{1,2}(?:\.\d)?)\s*[xX×]\s*(\d(?:\.\d{1,2})?)", txt): found.add(f'M{m.group(1)}x{m.group(2)}')
    for m in re.finditer(r"\bM(\d{1,2}(?:\.\d)?)\b(?=[^'\n]{0,20}(?:THREAD|Thread|thread|TAP|Tap|tap))", txt): found.add(f'M{m.group(1)}')
    for m in re.finditer(r"(#\d{1,2}|\d/\d{1,2}|1)\s*-\s*(\d{1,2})\s*(UNC|UNF)", txt): found.add(f'{m.group(1)}-{m.group(2)} {m.group(3)}')
    has_thread_entity = bool(re.search(r"\b(THREAD|SCREW_THREAD|THREADED_HOLE)\b", txt))
    return sorted(found) + (['<thread entity present>'] if has_thread_entity and not found else [])


# ---------------------------------------------------------------- projection
def _edge_prims(edge, deflection):
    c = BRepAdaptor_Curve(edge)
    t = c.GetType()
    f, l = c.FirstParameter(), c.LastParameter()
    if t == GeomAbs_Line:
        a, b = c.Value(f), c.Value(l)
        if a.Distance(b) < 1e-7: return []
        return [dict(t='line', p=[[a.X(), a.Y()], [b.X(), b.Y()]])]
    if t == GeomAbs_Circle:
        circ = c.Circle(); ctr = circ.Location(); r = circ.Radius(); zdir = circ.Axis().Direction().Z()
        if abs(abs(zdir) - 1) < 1e-6:
            if abs((l - f) - 2*math.pi) < 1e-6:
                return [dict(t='circle', c=[ctr.X(), ctr.Y()], r=r)]
            a, b = c.Value(f), c.Value(l)
            ang = lambda p: math.degrees(math.atan2(p.Y()-ctr.Y(), p.X()-ctr.X()))
            a0, a1 = ang(a), ang(b)
            if zdir < 0: a0, a1 = a1, a0
            return [dict(t='arc', c=[ctr.X(), ctr.Y()], r=r, a0=a0, a1=a1)]
    d = GCPnts_QuasiUniformDeflection(c, deflection)
    pts = []
    if d.IsDone():
        for i in range(1, d.NbPoints()+1):
            p = d.Value(i); pts.append([p.X(), p.Y()])
    if len(pts) >= 2:
        return [dict(t='poly', p=pts)]
    return []


def _compound_prims(comp, deflection):
    out = []
    if comp is None or comp.IsNull(): return out
    exp = TopExp_Explorer(comp, TopAbs_EDGE)
    while exp.More():
        out += _edge_prims(TopoDS.Edge_s(exp.Current()), deflection); exp.Next()
    return out


def hlr(shape, view, hidden=True, deflection=None, fast=False):
    if fast and face_count(shape) > EXACT_HLR_MAX_FACES:
        return hlr_poly(shape, view, hidden, deflection)
    N, X, _ = frame(view)
    if deflection is None:
        lo, hi = bbox(shape); deflection = max(1e-3, max(hi[i]-lo[i] for i in range(3)) / 2000)
    algo = HLRBRep_Algo(); algo.Add(shape)
    algo.Projector(HLRAlgo_Projector(gp_Ax2(gp_Pnt(0, 0, 0), gp_Dir(*N), gp_Dir(*X))))
    algo.Update(); algo.Hide()
    h = HLRBRep_HLRToShape(algo)
    vis = _compound_prims(h.VCompound(), deflection) + _compound_prims(h.OutLineVCompound(), deflection)
    hid = []
    if hidden:
        hid = _compound_prims(h.HCompound(), deflection) + _compound_prims(h.OutLineHCompound(), deflection)
    return dict(visible=vis, hidden=_drop_covered(hid, vis))


EXACT_HLR_MAX_FACES = 1500     # above this, exact HLR can take many minutes (threaded fasteners etc.) -> mesh-based HLR


def face_count(shape):
    from OCP.TopTools import TopTools_IndexedMapOfShape
    from OCP.TopExp import TopExp
    m = TopTools_IndexedMapOfShape(); TopExp.MapShapes_s(shape, TopAbs_FACE, m)
    return m.Extent()


def _chain_segments(segs, tol):
    """Join 2D segments that share endpoints (2-valent joints) into polylines, then drop near-collinear points (RDP)."""
    q = lambda p: (round(p[0] * 1e5), round(p[1] * 1e5))
    adj, used = {}, [False] * len(segs)
    for i, (a, b) in enumerate(segs):
        adj.setdefault(q(a), []).append(i); adj.setdefault(q(b), []).append(i)
    def extend(end_pt):
        pts = []
        while True:
            k = q(end_pt); nb = adj[k]
            if len(nb) != 2: return pts
            j = next((j for j in nb if not used[j]), None)
            if j is None: return pts
            used[j] = True; a, b = segs[j]
            end_pt = b if q(a) == k else a
            pts.append(end_pt)
    out = []
    for i, (a, b) in enumerate(segs):
        if used[i]: continue
        used[i] = True
        fwd = extend(b); back = extend(a)
        pts = list(reversed(back)) + [a, b] + fwd
        out.append(_rdp(_dedupe(pts), tol))
    return out


def _dedupe(pts):
    o = [pts[0]]
    for p in pts[1:]:
        if abs(p[0] - o[-1][0]) > 1e-9 or abs(p[1] - o[-1][1]) > 1e-9: o.append(p)
    return o


def _rdp(pts, tol):
    if len(pts) < 3: return pts
    keep = [False] * len(pts); keep[0] = keep[-1] = True
    st = [(0, len(pts) - 1)]
    while st:
        i, j = st.pop()
        a, b = pts[i], pts[j]; dx, dy = b[0] - a[0], b[1] - a[1]; L = math.hypot(dx, dy)
        dm, k = -1, -1
        for m in range(i + 1, j):
            p = pts[m]
            d = abs(dx * (a[1] - p[1]) - dy * (a[0] - p[0])) / L if L > 1e-12 else math.hypot(p[0] - a[0], p[1] - a[1])
            if d > dm: dm, k = d, m
        if dm > tol: keep[k] = True; st += [(i, k), (k, j)]
    return [p for p, f in zip(pts, keep) if f]


def _segs_of(comp):
    out = []
    if comp is None or comp.IsNull(): return out
    exp = TopExp_Explorer(comp, TopAbs_EDGE)
    while exp.More():
        c = BRepAdaptor_Curve(TopoDS.Edge_s(exp.Current())); exp.Next()
        a, b = c.Value(c.FirstParameter()), c.Value(c.LastParameter())
        if a.Distance(b) > 1e-9: out.append(((a.X(), a.Y()), (b.X(), b.Y())))
    return out


def hlr_poly(shape, view, hidden=True, deflection=None):
    """Mesh-based hidden-line removal: seconds instead of minutes on big assemblies; lines come back as polylines."""
    from OCP.HLRBRep import HLRBRep_PolyAlgo, HLRBRep_PolyHLRToShape
    N, X, _ = frame(view)
    lo, hi = bbox(shape, fast=True); size = max(hi[i] - lo[i] for i in range(3))    # also meshes the shape
    if deflection is None: deflection = max(1e-3, size / 2000)
    algo = HLRBRep_PolyAlgo(shape)
    algo.Projector(HLRAlgo_Projector(gp_Ax2(gp_Pnt(0, 0, 0), gp_Dir(*N), gp_Dir(*X)))); algo.Update()
    h = HLRBRep_PolyHLRToShape(); h.Update(algo)
    def prims(*comps):
        segs = []
        for c in comps: segs += _segs_of(c)
        out = []
        for pl in _chain_segments(segs, deflection / 2):     # mesh slivers far below print size are dropped
            if len(pl) < 2 or sum(math.dist(a, b) for a, b in zip(pl, pl[1:])) < deflection / 10: continue
            out.append(dict(t='poly', p=[list(p) for p in pl]) if len(pl) > 2 else dict(t='line', p=[list(pl[0]), list(pl[-1])]))
        return out
    vis = prims(h.VCompound(), h.OutLineVCompound())
    hid = prims(h.HCompound(), h.OutLineHCompound()) if hidden else []
    return dict(visible=vis, hidden=_drop_covered(hid, vis))


def _drop_covered(hid, vis, tol=1e-4):
    """Remove hidden lines/circles lying exactly under visible ones (they'd print as dashes over the outline)."""
    vl = [p['p'] for p in vis if p['t'] == 'line']
    vc = [(p['c'], p['r']) for p in vis if p['t'] in ('circle', 'arc')]
    out = []
    for h in hid:
        if h['t'] == 'line':
            a, b = h['p']; L = math.dist(a, b)
            if L < 1e-9: continue
            d = ((b[0]-a[0])/L, (b[1]-a[1])/L)
            ivs = []
            for va, vb in vl:
                # collinear?
                if abs((va[0]-a[0])*d[1] - (va[1]-a[1])*d[0]) > tol or abs((vb[0]-a[0])*d[1] - (vb[1]-a[1])*d[0]) > tol: continue
                t1 = (va[0]-a[0])*d[0] + (va[1]-a[1])*d[1]; t2 = (vb[0]-a[0])*d[0] + (vb[1]-a[1])*d[1]
                ivs.append((min(t1, t2), max(t1, t2)))
            ivs.sort(); cov = 0.0; covered = False
            for t1, t2 in ivs:
                if t1 > cov + tol: break
                cov = max(cov, t2)
                if cov >= L - tol: covered = True; break
            if covered: continue
        elif h['t'] == 'circle':
            if any(math.dist(c, h['c']) < tol and abs(r - h['r']) < tol for c, r in vc): continue
        out.append(h)
    return out


def prim_points(p, n=48):
    if p['t'] == 'line' or p['t'] == 'poly': return p['p']
    if p['t'] == 'circle':
        return [[p['c'][0]+p['r']*math.cos(2*math.pi*i/n), p['c'][1]+p['r']*math.sin(2*math.pi*i/n)] for i in range(n+1)]
    a0, a1 = p['a0'], p['a1']
    if a1 < a0: a1 += 360
    k = max(4, int(n*(a1-a0)/360))
    return [[p['c'][0]+p['r']*math.cos(math.radians(a0+(a1-a0)*i/k)), p['c'][1]+p['r']*math.sin(math.radians(a0+(a1-a0)*i/k))] for i in range(k+1)]


def prims_bbox(prims):
    xs, ys = [], []
    for p in prims:
        if p['t'] == 'circle':
            xs += [p['c'][0]-p['r'], p['c'][0]+p['r']]; ys += [p['c'][1]-p['r'], p['c'][1]+p['r']]
        else:
            for q in prim_points(p, 72): xs.append(q[0]); ys.append(q[1])
    if not xs: return [0, 0, 0, 0]
    return [min(xs), min(ys), max(xs), max(ys)]


def section(shape, axis, at, hidden=False):
    """Cut along plane axis=at; keep the half away from the viewer of the matching frame. Returns prims + hatch loops."""
    view = SECTION_FRAME[axis]
    N, X, Y = frame(view)
    lo, hi = bbox(shape)
    pad = 10 + max(hi[i]-lo[i] for i in range(3))
    blo = [lo[i]-pad for i in range(3)]; bhi = [hi[i]+pad for i in range(3)]
    ai = AXI[axis]
    # viewer is at +N side; remove material between viewer and plane -> keep side opposite to N
    if N[ai] > 0: bhi[ai] = at
    else: blo[ai] = at
    box = BRepPrimAPI_MakeBox(gp_Pnt(*blo), gp_Pnt(*bhi)).Shape()
    cut = BRepAlgoAPI_Common(shape, box).Shape()
    res = hlr(cut, view, hidden)
    loops = []
    exp = TopExp_Explorer(cut, TopAbs_FACE)
    while exp.More():
        f = TopoDS.Face_s(exp.Current()); exp.Next()
        s = BRepAdaptor_Surface(f)
        if s.GetType() != GeomAbs_Plane: continue
        pl = s.Plane(); d = pl.Axis().Direction(); o = pl.Location()
        if _axis_name((d.X(), d.Y(), d.Z()), 1e-5) != axis or abs((o.X(), o.Y(), o.Z())[ai]-at) > 1e-5: continue
        wexp = TopExp_Explorer(f, TopAbs_WIRE)
        while wexp.More():
            w = TopoDS.Wire_s(wexp.Current()); wexp.Next()
            pts = []
            we = BRepTools_WireExplorer(w, f)
            while we.More():
                e = we.Current(); c = BRepAdaptor_Curve(e)
                dfl = GCPnts_QuasiUniformDeflection(c, max(1e-3, pad/4000))
                ep = [c.Value(c.FirstParameter()), c.Value(c.LastParameter())] if not dfl.IsDone() else \
                     [dfl.Value(i) for i in range(1, dfl.NbPoints()+1)]
                ep = [(p.X(), p.Y(), p.Z()) for p in ep]
                if e.Orientation() == TopAbs_REVERSED: ep.reverse()
                for p in ep:
                    q = [sum(p[i]*X[i] for i in range(3)), sum(p[i]*Y[i] for i in range(3))]
                    if not pts or math.dist(pts[-1], q) > 1e-6: pts.append(q)
                we.Next()
            if len(pts) >= 3: loops.append(pts)
    res['hatch'] = loops
    return res
