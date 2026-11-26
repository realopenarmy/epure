"""Photo mode: a structured part spec (JSON or YAML) -> a simple CadQuery solid -> STEP.

The person describes the part from photos and answers; the bot writes a spec; this module builds
the solid and writes model.step, a shaded preview (model_preview.png) and build.json. The STEP then
goes through the normal `epure make` step unchanged. See PLAYBOOK.md section 15 and README "Photo mode".

Spec (all lengths in the spec's "units", "mm" or "in"; numbers may be {"value": N, "est": true}
for an unconfirmed estimate, e.g. scaled from a coin in the photo):

  {"units": "mm",
   "body": {"type": "plate",    "length": L, "width": W, "thickness": T}          # also "block"
         | {"type": "l_bracket","length": L, "base": D, "height": H, "thickness": T, "bend_radius": R}
         | {"type": "turned",   "segments": [{"dia": D, "length": L}, ...]},     # along +Z from z=0
   "features": [...]}

Prismatic body box: x 0..L (left to right), y 0..W (front to back), z 0..T (bottom to top).
The origin is the left-front-bottom corner; feature positions are measured from it.
l_bracket: base plate x 0..L, y 0..D, z 0..T; upright wall x 0..L, y 0..T, z 0..H at the front edge.

Features (prismatic): hole, hole_pattern (rect), bolt_circle, slot, pocket, step, chamfer, fillet.
Bodies also: {"type": "revolve", "profile": [{"z": Z, "dia": D}, ...], "smooth": true} (spline; "corner" breaks it).
Features (turned/revolve): bore, cross_hole, groove, tab, chamfer, fillet (by at= or z=). Segments may taper (dia_start/dia_end).
Top-level "simplified": "..." prints SIMPLIFIED FROM PHOTOS - ... on the drawing (photo mark).
"""
import json, math, os, sys

FACES = {   # face: (axis normal, coordinate of the face from the box, inward sign, (u axis, v axis))
    'top': ('z', 'max', -1, ('x', 'y')), 'bottom': ('z', 'min', +1, ('x', 'y')),
    'front': ('y', 'min', +1, ('x', 'z')), 'back': ('y', 'max', -1, ('x', 'z')),
    'left': ('x', 'min', +1, ('y', 'z')), 'right': ('x', 'max', -1, ('y', 'z')),
}
BRACKET_FACES = {'base': 'top', 'base_bottom': 'bottom', 'upright': 'back', 'upright_outside': 'front'}
PRISMATIC = ('plate', 'block', 'l_bracket')
DIAM_KEYS = ('dia', 'bc_dia', 'cbore_dia', 'csk_dia', 'dia_start', 'dia_end', 'hole_dia')
NOT_SIZES = ('count', 'start_angle', 'csk_angle', 'nx', 'ny', 'nz')
OUT_OF_SCOPE = ('freeform', 'organic', 'casting', 'sculpted', 'molded', 'forging')


# Scale references: known size in mm across the dimension the bot measures in the photo.
# Any other object works if the person gives its size (`--ref-mm`).
REFERENCES = {
    'penny': 19.05, 'nickel': 21.21, 'dime': 17.91, 'quarter': 24.26,          # US coins, diameter
    'euro_1': 23.25, 'euro_2': 25.75,                                          # euro coins, diameter
    'card_long': 85.60, 'card_short': 53.98,                                   # credit / ID card (ISO 7810 ID-1)
    'aa_long': 50.5, 'aa_dia': 14.5, 'aaa_long': 44.5, 'aaa_dia': 10.5,        # batteries
    'letter_long': 279.4, 'letter_short': 215.9, 'a4_long': 297.0, 'a4_short': 210.0,   # paper sheets
}


def scale_estimate(ref_px, feature_px, ref=None, ref_mm=None):
    """Size of a feature from its length in pixels and a reference object's length in pixels, in mm.
    Only meaningful when the reference lies on the feature's plane and the shot is roughly straight on."""
    if ref_mm is None:
        if ref not in REFERENCES: raise SpecError(f'unknown reference {ref!r}; give its size with --ref-mm or use one of {sorted(REFERENCES)}')
        ref_mm = REFERENCES[ref]
    if ref_px <= 0 or feature_px <= 0 or ref_mm <= 0: raise SpecError('pixel lengths and the reference size must be > 0')
    return ref_mm * feature_px / ref_px


class SpecError(ValueError):
    pass


# ------------------------------------------------------------------ loading + values
def load(path):
    with open(path, encoding='utf-8') as f:
        txt = f.read()
    if path.lower().endswith(('.yaml', '.yml')):
        import yaml
        return yaml.safe_load(txt)
    return json.loads(txt)


class Ctx:
    """Unit conversion plus a log of every estimated value used."""
    def __init__(self, spec):
        u = spec.get('units', 'mm')
        if u not in ('mm', 'in'): raise SpecError(f'units must be mm or in, not {u!r}')
        self.units, self.k, self.estimates, self.derived = u, (25.4 if u == 'in' else 1.0), [], []

    def num(self, obj, key, where, default=None, required=True):
        if key not in obj or obj[key] is None:
            if default is not None or not required: return default
            raise SpecError(f'{where}: missing "{key}"')
        v = obj[key]
        est = False
        if isinstance(v, dict):
            if 'value' not in v: raise SpecError(f'{where}.{key}: an object needs "value"')
            est = bool(v.get('est') or v.get('estimate'))
            v = v['value']
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            raise SpecError(f'{where}.{key}: expected a number, got {v!r}')
        mm = float(v) * self.k
        if est:
            kind = 'diameter' if key in DIAM_KEYS else ('radius' if key in ('radius', 'bend_radius', 'corner_radius') else 'length')
            self.estimates.append({'where': f'{where}.{key}', 'value': float(v), 'units': self.units, 'mm': mm, 'kind': kind})
        return mm

    def depth(self, obj, where, thru_len):
        d = obj.get('depth', 'thru')
        if d in ('thru', 'through', None): return thru_len, True
        return self.num(obj, 'depth', where), False


def estimates(spec):
    """Unconfirmed estimates in a spec, without building it."""
    c = Ctx(spec)
    def walk(o, where):
        if isinstance(o, dict):
            for k_, v_ in o.items():
                if isinstance(v_, dict) and 'value' in v_ and (v_.get('est') or v_.get('estimate')):
                    c.num(o, k_, where)
                else:
                    walk(v_, f'{where}.{k_}')
        elif isinstance(o, list):
            for i, v_ in enumerate(o): walk(v_, f'{where}[{i}]')
    walk(spec.get('body', {}), 'body')
    for i, f in enumerate(spec.get('features', [])): walk(f, f'features[{i}]({f.get("type", "?")})')
    return c.estimates


# ------------------------------------------------------------------ prismatic
def bbox(solid):
    """Tight bounding box (the default one is padded by the tolerance)."""
    from OCP.Bnd import Bnd_Box
    from OCP.BRepBndLib import BRepBndLib
    import cadquery as cq
    b = Bnd_Box(); BRepBndLib.AddOptimal_s(solid.wrapped, b, False, False)
    return cq.occ_impl.geom.BoundBox(b)


def _face(spec, name, where):
    btype = spec['body']['type']
    if btype == 'l_bracket':
        if name not in BRACKET_FACES:
            raise SpecError(f'{where}: l_bracket face must be one of {sorted(BRACKET_FACES)}')
        return BRACKET_FACES[name], name
    if name not in FACES: raise SpecError(f'{where}: face must be one of {sorted(FACES)}')
    return name, name


def _face_geom(spec, bb, face, wall):
    """Plane coordinate, inward direction vector, u/v axes, and the thru length for this face."""
    ax, side, sign, uv = FACES[face]
    i = 'xyz'.index(ax)
    lo, hi = (bb.xmin, bb.ymin, bb.zmin)[i], (bb.xmax, bb.ymax, bb.zmax)[i]
    plane = lo if side == 'min' else hi
    thru = hi - lo
    if wall is not None:          # l_bracket: the plate under this face, not the whole bounding box
        plane, thru = wall
    n = [0.0, 0.0, 0.0]; n[i] = float(sign)
    return ax, plane, tuple(n), uv, thru


def _bracket_wall(spec, name, T, H):
    # returns (plane coordinate, wall thickness) for l_bracket faces
    return {'base': (T, T), 'base_bottom': (0.0, T), 'upright': (T, T), 'upright_outside': (0.0, T)}[name]


def _pt(ax, plane, uv, u, v):
    p = {ax: plane, uv[0]: u, uv[1]: v}
    return (p['x'], p['y'], p['z'])


def _cyl(cq, r, h, base, n, eps=0.01):
    V = cq.Vector
    start = V(*base) - V(*n) * eps      # start just outside the face; end exactly at depth h
    return cq.Solid.makeCylinder(r, h + eps, start, V(*n))


def _cut_hole(cq, solid, c, spec, f, where, u, v, geom):
    ax, plane, n, uv, thru = geom
    r = c.num(f, 'dia', where) / 2
    if r <= 0: raise SpecError(f'{where}: dia must be > 0')
    depth, is_thru = c.depth(f, where, thru)
    base = _pt(ax, plane, uv, u, v)
    V = cq.Vector
    tools = [_cyl(cq, r, depth if not is_thru else thru + 1, base, n)]
    if 'cbore_dia' in f:
        cr = c.num(f, 'cbore_dia', where) / 2; cd = c.num(f, 'cbore_depth', where)
        if cr <= r: raise SpecError(f'{where}: cbore_dia must be larger than dia')
        tools.append(_cyl(cq, cr, cd, base, n))
    if 'csk_dia' in f:
        kr = c.num(f, 'csk_dia', where) / 2; ang = float(f.get('csk_angle', 90))
        if kr <= r: raise SpecError(f'{where}: csk_dia must be larger than dia')
        h = (kr - r) / math.tan(math.radians(ang / 2))
        eps = 0.01
        tools.append(cq.Solid.makeCone(kr + eps * math.tan(math.radians(ang / 2)), r, h + eps,
                                       V(*base) - V(*n) * eps, V(*n)))
    for t in tools: solid = solid.cut(t)
    return solid


def _rounded_prism(cq, u0, v0, u1, v1, r, geom, depth, eps=0.01):
    """Rectangular (optionally corner-rounded) prism cut from a face inward to depth."""
    ax, plane, n, uv, thru = geom
    lu, lv = abs(u1 - u0), abs(v1 - v0)
    wp_plane = {'x': 'YZ', 'y': 'XZ', 'z': 'XY'}[ax]
    if r > 0 and 2 * r >= min(lu, lv): raise SpecError('pocket corner_radius is too large for its size')
    prism = cq.Workplane(wp_plane).rect(lu, lv).extrude(depth + eps)
    if r > 0: prism = prism.edges('|' + ax.upper()).fillet(r)
    s = prism.val()
    # XZ workplane extrudes along -Y; normalise so the prism spans 0..depth+eps along +axis
    i = 'xyz'.index(ax)
    bb = s.BoundingBox(); lo = (bb.xmin, bb.ymin, bb.zmin)[i]
    cu, cv = (u0 + u1) / 2, (v0 + v1) / 2
    start = plane - eps if n[i] > 0 else plane - depth
    off = {ax: start - lo, uv[0]: cu, uv[1]: cv}
    return s.translate(cq.Vector(off['x'], off['y'], off['z']))


def _edges(solid, which, bb):
    tol = 1e-4
    out = []
    for e in solid.Edges():
        if e.geomType() != 'LINE': continue
        p0, p1 = e.startPoint(), e.endPoint()
        vertical = abs(p0.x - p1.x) < tol and abs(p0.y - p1.y) < tol
        on_top = abs(p0.z - bb.zmax) < tol and abs(p1.z - bb.zmax) < tol
        on_bot = abs(p0.z - bb.zmin) < tol and abs(p1.z - bb.zmin) < tol
        outer_v = vertical and (abs(p0.x - bb.xmin) < tol or abs(p0.x - bb.xmax) < tol) and \
            (abs(p0.y - bb.ymin) < tol or abs(p0.y - bb.ymax) < tol)
        if (which == 'vertical' and outer_v) or (which == 'top' and on_top) or (which == 'bottom' and on_bot) \
                or (which == 'all' and (outer_v or on_top or on_bot)):
            out.append(e)
    return out


def build_prismatic(spec, c):
    import cadquery as cq
    b = spec['body']; t = b['type']
    if t in ('plate', 'block'):
        L = c.num(b, 'length', 'body'); W = c.num(b, 'width', 'body'); T = c.num(b, 'thickness', 'body')
        if min(L, W, T) <= 0: raise SpecError('body: length, width and thickness must be > 0')
        solid = cq.Solid.makeBox(L, W, T)
        H = T
    else:
        L = c.num(b, 'length', 'body'); D = c.num(b, 'base', 'body'); H = c.num(b, 'height', 'body')
        T = c.num(b, 'thickness', 'body'); R = c.num(b, 'bend_radius', 'body', default=0.0)
        if min(L, D, H, T) <= 0: raise SpecError('body: length, base, height and thickness must be > 0')
        if D <= T or H <= T: raise SpecError('body: base and height must be larger than thickness')
        solid = cq.Solid.makeBox(L, D, T).fuse(cq.Solid.makeBox(L, T, H)).clean()
        if R > 0:   # formed bend: inner radius R, outer radius R + T on the corner edges along X
            def corner(y, z):
                return [e for e in solid.Edges() if e.geomType() == 'LINE'
                        and abs(e.startPoint().y - y) < 1e-4 and abs(e.endPoint().y - y) < 1e-4
                        and abs(e.startPoint().z - z) < 1e-4 and abs(e.endPoint().z - z) < 1e-4]
            if R + T >= min(D, H): raise SpecError('body: bend_radius is too large for the leg lengths')
            solid = solid.fillet(R, corner(T, T))
            solid = solid.fillet(R + T, corner(0.0, 0.0))
    bb = bbox(solid)
    feats = spec.get('features', [])
    # edge treatments on the plain body first (before holes), so selection stays simple
    for i, f in enumerate(feats):
        ft = f.get('type'); where = f'features[{i}]({ft})'
        if ft in ('chamfer', 'fillet'):
            which = f.get('edges', 'vertical')
            if which not in ('vertical', 'top', 'bottom', 'all'):
                raise SpecError(f'{where}: edges must be vertical, top, bottom or all')
            es = _edges(solid, which, bb)
            if not es: raise SpecError(f'{where}: no {which} edges found')
            size = c.num(f, 'size' if ft == 'chamfer' else 'radius', where)
            try:
                solid = solid.chamfer(size, None, es) if ft == 'chamfer' else solid.fillet(size, es)
            except Exception as e:
                raise SpecError(f'{where}: could not apply {ft} {size:g} mm; it may be bigger than a wall or edge it touches ({e})')
    for i, f in enumerate(feats):
        ft = f.get('type'); where = f'features[{i}]({ft})'
        if ft in ('chamfer', 'fillet'): continue
        if ft not in ('hole', 'hole_pattern', 'bolt_circle', 'slot', 'pocket', 'step'):
            raise SpecError(f'{where}: unknown feature type for a {t} (use hole, hole_pattern, bolt_circle, slot, pocket, step, chamfer, fillet)')
        face, fname = _face(spec, f.get('face', 'top' if t != 'l_bracket' else 'base'), where)
        wall = _bracket_wall(spec, fname, T, H) if t == 'l_bracket' else None
        geom = _face_geom(spec, bb, face, wall)
        uv = geom[3]
        if ft == 'hole':
            solid = _cut_hole(cq, solid, c, spec, f, where, c.num(f, uv[0], where), c.num(f, uv[1], where), geom)
        elif ft == 'hole_pattern':
            u0, v0 = c.num(f, uv[0], where), c.num(f, uv[1], where)
            nu, nv = int(f.get('n' + uv[0], 1)), int(f.get('n' + uv[1], 1))
            du, dv = c.num(f, 'd' + uv[0], where, default=0.0), c.num(f, 'd' + uv[1], where, default=0.0)
            if nu < 1 or nv < 1: raise SpecError(f'{where}: counts must be >= 1')
            for a_ in range(nu):
                for b_ in range(nv):
                    solid = _cut_hole(cq, solid, c, spec, f, where, u0 + a_ * du, v0 + b_ * dv, geom)
        elif ft == 'bolt_circle':
            cu, cv = c.num(f, 'c' + uv[0], where), c.num(f, 'c' + uv[1], where)
            R = c.num(f, 'bc_dia', where) / 2; nh = int(f.get('count', 0)); a0 = float(f.get('start_angle', 0))
            if nh < 2: raise SpecError(f'{where}: count must be >= 2')
            for k_ in range(nh):
                a = math.radians(a0 + 360.0 * k_ / nh)
                solid = _cut_hole(cq, solid, c, spec, f, where, cu + R * math.cos(a), cv + R * math.sin(a), geom)
        elif ft == 'slot':
            u1, v1 = c.num(f, uv[0] + '1', where), c.num(f, uv[1] + '1', where)
            u2, v2 = c.num(f, uv[0] + '2', where), c.num(f, uv[1] + '2', where)
            w = c.num(f, 'width', where); depth, is_thru = c.depth(f, where, geom[4])
            if is_thru: depth = geom[4] + 1
            ax, plane, n, _, _ = geom
            ln = math.hypot(u2 - u1, v2 - v1)
            tool = _cyl(cq, w / 2, depth, _pt(ax, plane, uv, u1, v1), n).fuse(_cyl(cq, w / 2, depth, _pt(ax, plane, uv, u2, v2), n))
            if ln > 1e-6:
                ang = math.atan2(v2 - v1, u2 - u1)
                if abs(math.sin(ang)) > 1e-9 and abs(math.cos(ang)) > 1e-9:
                    raise SpecError(f'{where}: slots must run along {uv[0]} or {uv[1]}')
                if abs(math.sin(ang)) < 1e-9: ua, ub, va, vb = min(u1, u2), max(u1, u2), v1 - w / 2, v1 + w / 2
                else: ua, ub, va, vb = u1 - w / 2, u1 + w / 2, min(v1, v2), max(v1, v2)
                tool = tool.fuse(_rounded_prism(cq, ua, va, ub, vb, 0, geom, depth))
            solid = solid.cut(tool)
        elif ft == 'pocket':
            u, v = c.num(f, uv[0], where), c.num(f, uv[1], where)   # pocket centre
            lu, lv = c.num(f, 'length', where), c.num(f, 'width', where)
            depth, is_thru = c.depth(f, where, geom[4])
            if is_thru: depth = geom[4] + 1
            r = c.num(f, 'corner_radius', where, default=0.0)
            solid = solid.cut(_rounded_prism(cq, u - lu / 2, v - lv / 2, u + lu / 2, v + lv / 2, r, geom, depth))
        elif ft == 'step':
            # cut a full-width rebate along one edge of the face: edge is "+x", "-x", "+y", ... (in face axes)
            edge = f.get('edge', '')
            if len(edge) != 2 or edge[0] not in '+-' or edge[1] not in uv:
                raise SpecError(f'{where}: edge must be +{uv[0]}, -{uv[0]}, +{uv[1]} or -{uv[1]}')
            size = c.num(f, 'size', where); depth = c.num(f, 'depth', where)
            lims = {'x': (bb.xmin, bb.xmax), 'y': (bb.ymin, bb.ymax), 'z': (bb.zmin, bb.zmax)}
            ua, ub = lims[uv[0]]; va, vb = lims[uv[1]]
            ea = edge[1]; lo, hi = lims[ea]
            rng = (hi - size, hi) if edge[0] == '+' else (lo, lo + size)
            if ea == uv[0]: ua, ub = rng
            else: va, vb = rng
            solid = solid.cut(_rounded_prism(cq, ua, va, ub, vb, 0, geom, depth))
    return solid


# ------------------------------------------------------------------ turned
def _turned_edges(solid, rad, total, ft, f, where, c):
    """Circular outer edges picked by `at` (start|end|both|steps) or by `z` (a height along the axis)."""
    if 'z' in f:
        zs, mode = [c.num(f, 'z', where)], f.get('corner', 'outer')
        if mode not in ('outer', 'inner'): raise SpecError(f'{where}: corner must be outer or inner')
    else:
        at = f.get('at', 'both')
        if at not in ('start', 'end', 'both', 'steps'): raise SpecError(f'{where}: at must be start, end, both or steps (or give z)')
        zs = {'start': [0.0], 'end': [total], 'both': [0.0, total], 'steps': [r_[1] for r_ in rad[:-1]]}[at]
        # shoulders: a chamfer breaks the outer corner, a fillet rounds the inside corner
        mode = ('outer' if ft == 'chamfer' else 'inner') if at == 'steps' else 'outer'
    es = []
    for e in solid.Edges():
        if e.geomType() != 'CIRCLE': continue
        cz = e.Center().z
        if not any(abs(cz - z_) < 1e-4 for z_ in zs): continue
        near = [r_[3] for r_ in rad if abs(r_[1] - cz) < 1e-4] + [r_[2] for r_ in rad if abs(r_[0] - cz) < 1e-4]
        if not near: continue
        want = max(near) if mode == 'outer' else min(near)
        if abs(e.radius() - want) > 1e-4: continue
        es.append(e)
    if not es: raise SpecError(f'{where}: no matching edge there (check z or at)')
    return es


def _tab(cq, f, where, c, total, end):
    """Flat tab with a round top and a hole through it (a keyring loop), standing on an end face."""
    V = cq.Vector
    w = c.num(f, 'width', where); t = c.num(f, 'thickness', where); h = c.num(f, 'height', where)
    hd = c.num(f, 'hole_dia', where, required=False)
    if h <= w / 2: raise SpecError(f'{where}: height must be more than half the width')
    if hd is not None and hd >= w: raise SpecError(f'{where}: hole_dia must be smaller than width')
    sink = 1.0                                          # overlap into the part so the tab fuses
    tab = cq.Solid.makeBox(w, t, h - w / 2 + sink, V(-w / 2, -t / 2, -sink))
    tab = tab.fuse(cq.Solid.makeCylinder(w / 2, t, V(0, -t / 2, h - w / 2), V(0, 1, 0)))
    if hd: tab = tab.cut(cq.Solid.makeCylinder(hd / 2, t + 2, V(0, -t / 2 - 1, h - w / 2), V(0, 1, 0)))
    if end == 'start': tab = tab.mirror('XY')
    return tab.translate(V(0, 0, total if end == 'end' else 0.0)).clean()


def _profile(spec, c):
    """Half profile (r across, z up) on the XZ workplane, closed to the axis, ready to revolve about Z.
    `turned`: straight steps, tapers (dia_start/dia_end), and arcs (`bulge`: + bows out, - bows in).
    `revolve`: profile points [{"z", "dia"}, ...], straight between points or one smooth spline (`smooth`)."""
    import cadquery as cq
    b = spec['body']
    if b['type'] == 'revolve':
        pts = b.get('profile') or []
        if len(pts) < 2: raise SpecError('body: a revolve needs "profile": at least 2 points [{"z": Z, "dia": D}, ...]')
        P = [(c.num(p_, 'dia', f'body.profile[{i}]') / 2, c.num(p_, 'z', f'body.profile[{i}]')) for i, p_ in enumerate(pts)]
        if abs(P[0][1]) > 1e-9: raise SpecError('body.profile: start the first point at z = 0 (the start end)')
        if any(P[i + 1][1] <= P[i][1] for i in range(len(P) - 1)): raise SpecError('body.profile: z must go up from point to point')
        if any(r_ <= 0 for r_, _ in P): raise SpecError('body.profile: every dia must be > 0')
        smooth = bool(b.get('smooth', True))
        wp = cq.Workplane('XZ').moveTo(0, 0).lineTo(*P[0])
        if smooth and len(P) > 2:
            # one smooth spline, broken at any point marked "corner": true (a crease, e.g. where two pieces meet)
            cuts = [0] + [i for i, p_ in enumerate(pts) if p_.get('corner') and 0 < i < len(P) - 1] + [len(P) - 1]
            for a_, b_ in zip(cuts, cuts[1:]):
                part = P[a_:b_ + 1]
                if len(part) > 2: _check_spline_inside(cq, part); wp = wp.spline(part[1:], includeCurrent=True)
                else: wp = wp.lineTo(*part[-1])
        else:
            for p_ in P[1:]: wp = wp.lineTo(*p_)
        wp = wp.lineTo(0, P[-1][1]).close()
        rad = [(P[i][1], P[i + 1][1], P[i][0], P[i + 1][0]) for i in range(len(P) - 1)]
        return wp, rad, P[-1][1]
    segs = b.get('segments') or []
    if not segs: raise SpecError('body: a turned part needs "segments": [{"dia": D, "length": L}, ...]')
    z, rad = 0.0, []
    wp = cq.Workplane('XZ').moveTo(0, 0)
    cur = (0.0, 0.0)
    for i, s in enumerate(segs):
        w_ = f'body.segments[{i}]'
        if 'dia' in s: d0 = d1 = c.num(s, 'dia', w_)
        elif 'dia_start' in s or 'dia_end' in s: d0, d1 = c.num(s, 'dia_start', w_), c.num(s, 'dia_end', w_)   # a taper
        else: raise SpecError(f'{w_}: give "dia", or "dia_start" and "dia_end" for a taper')
        ln = c.num(s, 'length', w_)
        if min(d0, d1) <= 0 or ln <= 0: raise SpecError(f'{w_}: diameters and length must be > 0')
        bulge = c.num(s, 'bulge', w_, required=False) or 0.0
        r0, r1 = d0 / 2, d1 / 2
        if abs(cur[0] - r0) > 1e-9 or abs(cur[1] - z) > 1e-9: wp = wp.lineTo(r0, z)
        if bulge:
            rm = (r0 + r1) / 2 + bulge
            if rm <= 0: raise SpecError(f'{w_}: bulge {bulge:g} cuts through the axis')
            wp = wp.threePointArc((rm, z + ln / 2), (r1, z + ln))
        else:
            wp = wp.lineTo(r1, z + ln)
        cur = (r1, z + ln)
        rad.append((z, z + ln, r0, r1)); z += ln
        if any(e['where'].startswith(f'body.segments[{j}].length') for j in range(i + 1) for e in c.estimates):
            c.derived.append(z)            # a shoulder position that adds up estimated lengths is an estimate too
    wp = wp.lineTo(0, z).close()
    return wp, rad, z


def _check_spline_inside(cq, P):
    """A spline through the points must not swing through the axis."""
    e = cq.Edge.makeSpline([cq.Vector(r_, 0, z_) for r_, z_ in P])
    if any(e.positionAt(k / 60).x <= 1e-6 for k in range(61)):
        raise SpecError('body.profile: the smooth curve dips through the axis; add a point or use "smooth": false')


def is_curved(spec):
    b = spec.get('body') or {}
    if b.get('type') == 'revolve': return bool(b.get('smooth', True)) and len(b.get('profile') or []) > 2
    return any(s_.get('bulge') for s_ in b.get('segments') or [] if isinstance(s_, dict))


def stations(spec):
    """Key profile stations (z mm, dia mm, estimated?) to dimension on a curved part, start to end."""
    if not is_curved(spec): return []
    c = Ctx(spec); b = spec['body']; out = []
    isest = lambda v: isinstance(v, dict) and bool(v.get('est') or v.get('estimate'))
    if b['type'] == 'revolve':
        for i, p_ in enumerate(b['profile']):
            out.append((c.num(p_, 'z', 'p'), c.num(p_, 'dia', 'p'), isest(p_['z']) or isest(p_['dia'])))
        return out
    z = 0.0
    for s_ in b['segments']:
        d0 = c.num(s_, 'dia', 's') if 'dia' in s_ else c.num(s_, 'dia_start', 's')
        d1 = c.num(s_, 'dia', 's') if 'dia' in s_ else c.num(s_, 'dia_end', 's')
        ln = c.num(s_, 'length', 's'); e_ = any(isest(v) for v in s_.values())
        if not out or abs(out[-1][0] - z) > 1e-6: out.append((z, d0, e_))
        bu = c.num(s_, 'bulge', 's', required=False)
        if bu: out.append((z + ln / 2, d0 / 2 + d1 / 2 + 2 * bu, e_))
        z += ln
        out.append((z, d1, e_))
    return out


def build_turned(spec, c):
    import cadquery as cq
    V = cq.Vector
    wp, rad, total = _profile(spec, c)
    solid = wp.revolve(360, (0, 0, 0), (0, 1, 0)).val().clean()
    rmax = max(bbox(solid).xmax, max(max(r_[2], r_[3]) for r_ in rad))
    feats = spec.get('features', [])
    for i, f in enumerate(feats):     # chamfers / fillets on outer circular edges first
        ft = f.get('type'); where = f'features[{i}]({ft})'
        if ft not in ('chamfer', 'fillet'): continue
        es = _turned_edges(solid, rad, total, ft, f, where, c)
        size = c.num(f, 'size' if ft == 'chamfer' else 'radius', where)
        try:
            solid = solid.chamfer(size, None, es) if ft == 'chamfer' else solid.fillet(size, es)
        except Exception as e:
            raise SpecError(f'{where}: could not apply {ft} {size:g} mm; it may be bigger than a wall or edge it touches ({e})')
    for i, f in enumerate(feats):
        ft = f.get('type'); where = f'features[{i}]({ft})'
        if ft in ('chamfer', 'fillet'): continue
        if ft == 'bore':
            r = c.num(f, 'dia', where) / 2
            depth, is_thru = c.depth(f, where, total)
            frm = f.get('from', 'start')
            if frm not in ('start', 'end'): raise SpecError(f'{where}: from must be start or end')
            if is_thru: solid = solid.cut(cq.Solid.makeCylinder(r, total + 2, V(0, 0, -1), V(0, 0, 1)))
            elif frm == 'start': solid = solid.cut(cq.Solid.makeCylinder(r, depth + 0.01, V(0, 0, -0.01), V(0, 0, 1)))
            else: solid = solid.cut(cq.Solid.makeCylinder(r, depth + 0.01, V(0, 0, total - depth), V(0, 0, 1)))
        elif ft == 'cross_hole':
            r = c.num(f, 'dia', where) / 2; zc = c.num(f, 'z', where)
            dr = f.get('direction', 'x')
            if dr not in ('x', 'y'): raise SpecError(f'{where}: direction must be x or y')
            R = rmax + 1
            d = V(1, 0, 0) if dr == 'x' else V(0, 1, 0)
            solid = solid.cut(cq.Solid.makeCylinder(r, 2 * R, V(0, 0, zc) - d * R, d))
        elif ft == 'groove':
            zc = c.num(f, 'z', where); w = c.num(f, 'width', where); gd = c.num(f, 'dia', where) / 2
            R = rmax + 1
            ring = cq.Solid.makeCylinder(R, w, V(0, 0, zc), V(0, 0, 1)).cut(cq.Solid.makeCylinder(gd, w, V(0, 0, zc), V(0, 0, 1)))
            solid = solid.cut(ring)
        elif ft == 'tab':
            end = f.get('at', 'end')
            if end not in ('start', 'end'): raise SpecError(f'{where}: at must be start or end')
            solid = solid.fuse(_tab(cq, f, where, c, total, end)).clean()
            if c.estimates: c.derived.append(total + c.num(f, 'height', where))
        else:
            raise SpecError(f'{where}: unknown feature type for a turned part (use bore, cross_hole, groove, tab, chamfer, fillet)')
    return solid


# ------------------------------------------------------------------ build + outputs
def build(spec):
    """Return (cadquery Solid, list of estimates). Raises SpecError with a plain message."""
    if not isinstance(spec, dict) or 'body' not in spec: raise SpecError('spec needs a "body"')
    t = (spec['body'] or {}).get('type', '')
    if t in OUT_OF_SCOPE or spec['body'].get('out_of_scope'):
        raise SpecError(f'{t or "this part"} has no simple main form to model. I can draw its envelope (a bounding box or '
                        'cylinder, as a plate/block or one-segment turned body), and a STEP file or 3D scan gives the real shape')
    c = Ctx(spec)
    if t in PRISMATIC: solid = build_prismatic(spec, c)
    elif t in ('turned', 'revolve'): solid = build_turned(spec, c)
    else: raise SpecError(f'body.type must be plate, block, l_bracket, turned or revolve, not {t!r}')
    solid = solid.clean()
    if not solid.isValid(): raise SpecError('the built solid is not valid; check that features stay inside the part')
    n = len(solid.Solids()) if hasattr(solid, 'Solids') else 1
    if n != 1: raise SpecError(f'the features split the part into {n} pieces; check positions and depths')
    # de-duplicate estimate log (hole patterns re-read the same value per hole)
    seen, est = set(), []
    for e in c.estimates:
        if e['where'] not in seen: seen.add(e['where']); est.append(e)
    for z_ in sorted(set(round(v, 6) for v in c.derived)):
        est.append({'where': f'derived: position {z_ / c.k:g} {c.units} from the start end', 'value': z_ / c.k,
                    'units': c.units, 'mm': z_, 'kind': 'length', 'derived': True})
    return solid, est


def all_estimated(spec):
    """True when every size in the spec is an estimate (nothing measured yet), so every dimension is ≈."""
    n = {'plain': 0, 'est': 0}
    def walk(o, key=None):
        if isinstance(o, dict):
            if 'value' in o and (o.get('est') or o.get('estimate')): n['est'] += 1; return
            for k_, v_ in o.items(): walk(v_, k_)
        elif isinstance(o, list):
            for v_ in o: walk(v_, key)
        elif isinstance(o, (int, float)) and not isinstance(o, bool) and key not in NOT_SIZES and o != 0:
            n['plain'] += 1                             # a 0 is the datum (e.g. the first profile point), not a size
    walk(spec.get('body', {})); walk(spec.get('features', []))
    return n['est'] > 0 and n['plain'] == 0


def preview_png(solid, path, title='', size=(900, 680)):
    """Shaded iso render (small z-buffer rasteriser, no GL needed) for the person to confirm the shape."""
    import numpy as np
    from PIL import Image, ImageDraw, ImageFilter
    bb0 = bbox(solid); size_ = max(bb0.xlen, bb0.ylen, bb0.zlen)
    verts, tris = solid.tessellate(max(0.01, size_ * 2e-4), 0.08)
    P = np.array([(v.x, v.y, v.z) for v in verts], float); T = np.array(tris, int)
    # iso camera from front-right-top, like the drawing's iso view
    fwd = np.array([1.0, -1.0, 1.0]) / math.sqrt(3)          # towards the camera
    right = np.cross([0, 0, 1.0], fwd); right /= np.linalg.norm(right)
    up = np.cross(fwd, right)
    Q = np.stack([P @ right, P @ up, P @ fwd], 1)
    W, H = size; pad = 60
    lo, hi = Q[:, :2].min(0), Q[:, :2].max(0)
    sc = min((W - 2 * pad) / max(hi[0] - lo[0], 1e-9), (H - 2 * pad - 40) / max(hi[1] - lo[1], 1e-9))
    X = (Q[:, 0] - (lo[0] + hi[0]) / 2) * sc + W / 2
    Y = H / 2 + 20 - (Q[:, 1] - (lo[1] + hi[1]) / 2) * sc
    Z = Q[:, 2]
    tri = P[T]
    nrm = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
    nrm /= np.linalg.norm(nrm, axis=1, keepdims=True) + 1e-12
    nrm *= np.where((nrm @ fwd) < 0, -1, 1)[:, None]          # face the camera (winding is not consistent)
    light = np.array([0.2, -0.6, 0.9]); light /= np.linalg.norm(light)
    shade = 0.40 + 0.60 * np.clip(nrm @ light, 0, 1)
    zbuf = np.full((H, W), -np.inf); img = np.ones((H, W, 3)); nbuf = np.zeros((H, W, 3))
    base = np.array([0.62, 0.72, 0.86])
    for i, (a_, b_, c_) in enumerate(T):
        xs, ys = X[[a_, b_, c_]], Y[[a_, b_, c_]]
        x0, x1 = max(int(xs.min()), 0), min(int(xs.max()) + 1, W - 1)
        y0, y1 = max(int(ys.min()), 0), min(int(ys.max()) + 1, H - 1)
        if x1 < x0 or y1 < y0: continue
        gx, gy = np.meshgrid(np.arange(x0, x1 + 1) + 0.5, np.arange(y0, y1 + 1) + 0.5)
        d = (ys[1] - ys[2]) * (xs[0] - xs[2]) + (xs[2] - xs[1]) * (ys[0] - ys[2])
        if abs(d) < 1e-12: continue
        w0 = ((ys[1] - ys[2]) * (gx - xs[2]) + (xs[2] - xs[1]) * (gy - ys[2])) / d
        w1 = ((ys[2] - ys[0]) * (gx - xs[2]) + (xs[0] - xs[2]) * (gy - ys[2])) / d
        w2 = 1 - w0 - w1
        inside = (w0 >= -1e-6) & (w1 >= -1e-6) & (w2 >= -1e-6)
        if not inside.any(): continue
        z = w0 * Z[a_] + w1 * Z[b_] + w2 * Z[c_]
        zb = zbuf[y0:y1 + 1, x0:x1 + 1]
        upd = inside & (z > zb)
        zb[upd] = z[upd]
        img[y0:y1 + 1, x0:x1 + 1][upd] = base * shade[i]
        nbuf[y0:y1 + 1, x0:x1 + 1][upd] = nrm[i]
    # outline: silhouette and sharp creases (normal jumps between neighbouring pixels)
    edge = np.zeros((H, W), bool)
    fg = np.isfinite(zbuf)
    for dy, dx in ((0, 1), (1, 0)):
        n2 = np.roll(nbuf, (-dy, -dx), (0, 1)); f2 = np.roll(fg, (-dy, -dx), (0, 1))
        z2 = np.roll(zbuf, (-dy, -dx), (0, 1))
        crease = fg & f2 & ((nbuf * n2).sum(2) < 0.80)
        with np.errstate(invalid="ignore"): depth = fg & f2 & (np.abs(zbuf - z2) > 3.0 / sc)
        edge |= crease | depth | (fg != f2)
    img[edge] = (0.12, 0.14, 0.18)
    im = Image.fromarray((img * 255).astype('uint8'))
    dr = ImageDraw.Draw(im)
    from PIL import ImageFont
    try: font = ImageFont.load_default(size=16)
    except TypeError: font = None
    dr.text((20, 14), (title + ' - ' if title else '') + 'PHOTO-MODE MODEL: CHECK THE SHAPE BEFORE THE DRAWING', fill=(20, 20, 20), font=font)
    bb = bbox(solid)
    dr.text((20, H - 26), f'SIZE {bb.xlen:.2f} x {bb.ylen:.2f} x {bb.zlen:.2f} MM (X x Y x Z)', fill=(20, 20, 20), font=font)
    im.save(path)
    return path


def cmd_build(spec_path, out):
    spec = load(spec_path)
    solid, est = build(spec)
    os.makedirs(out, exist_ok=True)
    import cadquery as cq
    step = os.path.join(out, 'model.step')
    cq.exporters.export(cq.Workplane().add(solid), step)
    png = preview_png(solid, os.path.join(out, 'model_preview.png'), spec.get('name', ''))
    bb = bbox(solid)
    res = {'ok': True, 'step': step, 'model_preview': png,
           'size_mm': [round(bb.xlen, 3), round(bb.ylen, 3), round(bb.zlen, 3)],
           'volume_mm3': round(solid.Volume(), 1), 'estimates': est, 'all_estimated': all_estimated(spec),
           'simplified_note': simplified_note(spec['simplified']) if spec.get('simplified') else None,
           'spec': os.path.abspath(spec_path)}
    with open(os.path.join(out, 'build.json'), 'w') as f: json.dump(res, f, indent=1, ensure_ascii=False)
    return res


ESTIMATE_NOTE = 'DIMENSIONS MARKED ≈ ARE ESTIMATED FROM PHOTOS - VERIFY BEFORE MACHINING'
SIMPLIFIED_PREFIX = 'SIMPLIFIED FROM PHOTOS'
CURVES_NOTE = 'CURVES ESTIMATED FROM PHOTOS - PROFILE APPROXIMATE'


def simplified_note(text):
    """Drawing note for a simplified model; `text` says what is left out, e.g. 'LETTERING AND RIBS NOT SHOWN'."""
    t = str(text).strip().upper()
    if t.startswith(SIMPLIFIED_PREFIX): return t
    return f'{SIMPLIFIED_PREFIX} - {t}' if t else SIMPLIFIED_PREFIX


def _closest_on(shape, xyz):
    from OCP.BRepExtrema import BRepExtrema_DistShapeShape
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeVertex
    from OCP.gp import gp_Pnt
    v = BRepBuilderAPI_MakeVertex(gp_Pnt(*xyz)).Vertex()
    d = BRepExtrema_DistShapeShape(shape.wrapped, v); d.Perform()
    p = d.PointOnShape1(1)
    return (p.X(), p.Y(), p.Z())


def fillet_leaders(spec, solid, c=None):
    """Turned fillets placed by `z` (e.g. a rounded bottle shoulder) that blend a cylinder into a taper.
    The drawing engine only calls out fillets between square faces, so photo mark adds a leader for these.
    Returns [(point_mm (x, 0, z), radius_mm, is_estimate)]."""
    if spec.get('body', {}).get('type') not in ('turned', 'revolve'): return []
    c = c or Ctx(spec)
    out = []
    tori = [f for f in solid.Faces() if f.geomType() in ('TORUS', 'BSPLINE')]   # cylinder-to-cone blends come out as B-splines
    for i, f in enumerate(spec.get('features', [])):
        if f.get('type') != 'fillet' or 'z' not in f: continue
        z = c.num(f, 'z', 'x'); r = c.num(f, 'radius', 'x')
        est = isinstance(f['radius'], dict) and bool(f['radius'].get('est') or f['radius'].get('estimate'))
        for t in tori:
            bb = t.BoundingBox()
            if bb.zmin - 1e-3 <= z <= bb.zmax + 1e-3 and bb.zlen <= 2 * r + 1e-3:
                p_ = _closest_on(t, (bb.xmax + 50, 0.0, (bb.zmin + bb.zmax) / 2))   # mid-height of the blend, +x side
                out.append(((round(math.hypot(p_[0], p_[1]), 4), 0.0, round(p_[2], 4)), r, est)); break
    return out


def mark(E, d, est, tol=0.02, simplified=None, mark_all=False, leaders=(), curved=False, stations_=()):
    """Prefix ≈ on every dimension of the drawing in d whose value matches an unconfirmed estimate.
    Conservative: a confirmed dimension with the same value is marked too. E is the epure module."""
    spec = E.read_spec(d)
    lens = [e['mm'] for e in est if e['kind'] == 'length']
    dias = [e['mm'] for e in est if e['kind'] == 'diameter']
    rads = [e['mm'] for e in est if e['kind'] == 'radius']
    feats = {f['id']: f for f in E.all_feats(spec)}
    hit = lambda v, L: any(abs(v - x) <= tol for x in L)
    marked = []
    for dm in spec['dims']:
        t = dm.get('text') or ''
        if '≈' in t: continue
        f = feats.get(dm.get('feature'))
        match = mark_all and bool(est)
        if match: pass
        elif dm['kind'] == 'linear':
            try:
                v = abs(E.ref_value(spec, dm['b'], dm['axis']) - E.ref_value(spec, dm['a'], dm['axis']))
                match = hit(v, lens) or hit(v, dias)
            except Exception:
                match = False
        elif f is not None and f.get('type') == 'slot':
            match = hit(f['width'], lens + dias) or hit(f['length'], lens) or hit(f['length'] + f['width'], lens)
        elif f is not None and f.get('dia') is not None:
            match = hit(f['dia'], dias)
        elif f is not None and f.get('r') is not None:
            match = hit(f['r'], rads)
        elif f is not None and f.get('type') == 'chamfer' and f.get('legs'):
            match = hit(f['legs'][0], lens)
        if dm.get('bc') and not match:
            bc = next((x for x in spec['features'].get('bolt_circles', []) if x['id'] == dm['bc']), None)
            if bc and hit(bc['dia'], dias): match = True
        if match:
            base = t or ('%%c<>' if dm['kind'] in ('hole', 'diameter') else '<>')
            dm['text'] = '≈' + base
            marked.append(dm['id'])
    import geom
    spec['dims'] = [dm for dm in spec['dims'] if not dm.get('photo')]
    have = [f['r'] for f in spec['features'].get('fillets', [])]
    for pt, r, e_ in leaders:
        if any(abs(r - h) <= tol for h in have): continue        # the engine already calls it out
        txt = ('≈' if e_ or mark_all else '') + 'R' + E.fmt(spec, r)
        dm = {'id': E.next_id(spec['dims'], 'D'), 'kind': 'leader', 'view': 'front', 'text': txt, 'angle': 30,
              'dist': 8, 'auto': False, 'photo': True, 'point': list(geom.project(pt, 'front'))}
        spec['dims'].append(dm); marked.append(dm['id'])
    if stations_:                                       # curved profile: diameters across at key stations + their heights
        zs = [z_ for z_, _, _ in stations_]; z0, z1 = min(zs), max(zs); inset = 0.01 * (z1 - z0)
        off = 10.0
        for z_, dia, e_ in stations_:
            at = min(max(z_, z0 + inset), z1 - inset)          # just inside the end faces
            pre = '≈' if (e_ or mark_all) else ''
            dm = {'id': E.next_id(spec['dims'], 'D'), 'kind': 'linear', 'view': 'front', 'axis': 'x', 'a': -dia / 2,
                  'b': dia / 2, 'side': 'inside', 'at': at, 'text': pre + '%%c<>', 'auto': False, 'photo': True}
            spec['dims'].append(dm); marked.append(dm['id'])
            if z0 + 1e-6 < z_ < z1 - 1e-6:
                dm = {'id': E.next_id(spec['dims'], 'D'), 'kind': 'linear', 'view': 'right', 'axis': 'z', 'a': z0, 'b': z_,
                      'side': 'left', 'offset': off, 'text': pre + '<>', 'auto': False, 'photo': True}
                off += 7.0
                spec['dims'].append(dm); marked.append(dm['id'])
    # photo-mode notes are rebuilt every run, always in the same order
    spec['notes'] = [n for n in spec['notes'] if n not in (ESTIMATE_NOTE, CURVES_NOTE) and not n.startswith(SIMPLIFIED_PREFIX)]
    if est: spec['notes'].append(ESTIMATE_NOTE)
    if curved: spec['notes'].append(CURVES_NOTE)
    if simplified: spec['notes'].append(simplified_note(simplified))
    spec = E.render(spec, d)
    return {'ok': True, 'dir': d, 'marked': marked, 'estimates': len(est),
            'note': ESTIMATE_NOTE if est else None, 'simplified_note': simplified_note(simplified) if simplified else None,
            'curves_note': CURVES_NOTE if curved else None, 'preview': os.path.join(d, 'preview.png'),
            'audit_errors': spec['resolved']['audit_errors']}


def main(argv, E=None):
    import argparse
    ap = argparse.ArgumentParser(prog='epure photo')
    sp = ap.add_subparsers(dest='op', required=True)
    b = sp.add_parser('build', help='spec -> model.step + model_preview.png + build.json')
    b.add_argument('spec'); b.add_argument('--out', required=True)
    c = sp.add_parser('check', help='list unconfirmed estimates and spec errors without writing files')
    c.add_argument('spec')
    m = sp.add_parser('mark', help='after epure make: put ≈ on dimensions that are still estimates')
    m.add_argument('dir'); m.add_argument('--spec', required=True)
    k = sp.add_parser('scale', help='estimate a size from a reference object of known size in the same photo')
    k.add_argument('--ref', help='a name from REFERENCES (penny, card_long, aa_long, a4_short, ...)')
    k.add_argument('--ref-mm', type=float, help='or the reference size in mm, for any object the person measured')
    k.add_argument('--ref-px', type=float, required=True); k.add_argument('--px', type=float, nargs='+', required=True)
    a = ap.parse_args(argv)
    try:
        if a.op == 'build':
            res = cmd_build(a.spec, os.path.abspath(a.out))
        elif a.op == 'check':
            spec = load(a.spec); _, est = build(spec)
            res = {'ok': True, 'estimates': est, 'ready_for_final': not est,
                   'simplified_note': simplified_note(spec['simplified']) if spec.get('simplified') else None}
        elif a.op == 'scale':
            vals = [round(scale_estimate(a.ref_px, p_, a.ref, a.ref_mm), 2) for p_ in a.px]
            res = {'ok': True, 'estimates_mm': vals, 'reference_mm': a.ref_mm or REFERENCES.get(a.ref),
                   'note': 'estimates only: valid if the reference is on the same plane and the shot is straight on'}
        else:
            sp_ = load(a.spec)
            solid, est = build(sp_)
            res = mark(E, os.path.abspath(a.dir), est, simplified=sp_.get('simplified'), mark_all=all_estimated(sp_),
                       leaders=fillet_leaders(sp_, solid), curved=is_curved(sp_), stations_=stations(sp_))
    except SpecError as e:
        print(json.dumps({'ok': False, 'error': str(e)}, ensure_ascii=False)); sys.exit(2)
    print(json.dumps(res, indent=1, ensure_ascii=False))
