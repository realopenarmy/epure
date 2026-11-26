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
Features (turned): bore, cross_hole, groove, chamfer, fillet.
"""
import json, math, os, sys

FACES = {   # face: (axis normal, coordinate of the face from the box, inward sign, (u axis, v axis))
    'top': ('z', 'max', -1, ('x', 'y')), 'bottom': ('z', 'min', +1, ('x', 'y')),
    'front': ('y', 'min', +1, ('x', 'z')), 'back': ('y', 'max', -1, ('x', 'z')),
    'left': ('x', 'min', +1, ('y', 'z')), 'right': ('x', 'max', -1, ('y', 'z')),
}
BRACKET_FACES = {'base': 'top', 'base_bottom': 'bottom', 'upright': 'back', 'upright_outside': 'front'}
PRISMATIC = ('plate', 'block', 'l_bracket')
DIAM_KEYS = ('dia', 'bc_dia', 'cbore_dia', 'csk_dia')
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
        self.units, self.k, self.estimates = u, (25.4 if u == 'in' else 1.0), []

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
def build_turned(spec, c):
    import cadquery as cq
    V = cq.Vector
    segs = spec['body'].get('segments') or []
    if not segs: raise SpecError('body: a turned part needs "segments": [{"dia": D, "length": L}, ...]')
    solid, z, rad = None, 0.0, []
    for i, s in enumerate(segs):
        d = c.num(s, 'dia', f'body.segments[{i}]'); ln = c.num(s, 'length', f'body.segments[{i}]')
        if d <= 0 or ln <= 0: raise SpecError(f'body.segments[{i}]: dia and length must be > 0')
        cyl = cq.Solid.makeCylinder(d / 2, ln, V(0, 0, z), V(0, 0, 1))
        solid = cyl if solid is None else solid.fuse(cyl)
        rad.append((z, z + ln, d / 2)); z += ln
    solid = solid.clean()
    total = z
    feats = spec.get('features', [])
    for i, f in enumerate(feats):     # end chamfers / fillets on outer circular edges first
        ft = f.get('type'); where = f'features[{i}]({ft})'
        if ft not in ('chamfer', 'fillet'): continue
        at = f.get('at', 'both')
        if at not in ('start', 'end', 'both', 'steps'): raise SpecError(f'{where}: at must be start, end, both or steps')
        zs = {'start': [0.0], 'end': [total], 'both': [0.0, total], 'steps': [r_[1] for r_ in rad[:-1]]}[at]
        es = []
        for e in solid.Edges():
            if e.geomType() != 'CIRCLE': continue
            cz = e.Center().z
            if not any(abs(cz - z_) < 1e-4 for z_ in zs): continue
            if at == 'steps':   # shoulders: a chamfer breaks the outer corner, a fillet rounds the inside corner
                near = [r_[2] for r_ in rad if abs(r_[0] - cz) < 1e-4 or abs(r_[1] - cz) < 1e-4]
                want = max(near) if ft == 'chamfer' else min(near)
                if abs(e.radius() - want) > 1e-4: continue
            elif abs(e.radius() - max(r_[2] for r_ in rad if r_[0] - 1e-4 <= cz <= r_[1] + 1e-4)) > 1e-4:
                continue
            es.append(e)
        if not es: raise SpecError(f'{where}: no matching edges at {at}')
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
            R = max(r_[2] for r_ in rad) + 1
            d = V(1, 0, 0) if dr == 'x' else V(0, 1, 0)
            solid = solid.cut(cq.Solid.makeCylinder(r, 2 * R, V(0, 0, zc) - d * R, d))
        elif ft == 'groove':
            zc = c.num(f, 'z', where); w = c.num(f, 'width', where); gd = c.num(f, 'dia', where) / 2
            R = max(r_[2] for r_ in rad) + 1
            ring = cq.Solid.makeCylinder(R, w, V(0, 0, zc), V(0, 0, 1)).cut(cq.Solid.makeCylinder(gd, w, V(0, 0, zc), V(0, 0, 1)))
            solid = solid.cut(ring)
        else:
            raise SpecError(f'{where}: unknown feature type for a turned part (use bore, cross_hole, groove, chamfer, fillet)')
    return solid


# ------------------------------------------------------------------ build + outputs
def build(spec):
    """Return (cadquery Solid, list of estimates). Raises SpecError with a plain message."""
    if not isinstance(spec, dict) or 'body' not in spec: raise SpecError('spec needs a "body"')
    t = (spec['body'] or {}).get('type', '')
    if t in OUT_OF_SCOPE or spec['body'].get('out_of_scope'):
        raise SpecError(f'{t or "this part"} is out of scope for photo mode: it needs a real CAD model or a 3D scan')
    c = Ctx(spec)
    if t in PRISMATIC: solid = build_prismatic(spec, c)
    elif t == 'turned': solid = build_turned(spec, c)
    else: raise SpecError(f'body.type must be plate, block, l_bracket or turned, not {t!r}')
    solid = solid.clean()
    if not solid.isValid(): raise SpecError('the built solid is not valid; check that features stay inside the part')
    n = len(solid.Solids()) if hasattr(solid, 'Solids') else 1
    if n != 1: raise SpecError(f'the features split the part into {n} pieces; check positions and depths')
    # de-duplicate estimate log (hole patterns re-read the same value per hole)
    seen, est = set(), []
    for e in c.estimates:
        if e['where'] not in seen: seen.add(e['where']); est.append(e)
    return solid, est


def preview_png(solid, path, title='', size=(900, 680)):
    """Shaded iso render (small z-buffer rasteriser, no GL needed) for the person to confirm the shape."""
    import numpy as np
    from PIL import Image, ImageDraw, ImageFilter
    verts, tris = solid.tessellate(0.02, 0.15)
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
           'volume_mm3': round(solid.Volume(), 1), 'estimates': est, 'spec': os.path.abspath(spec_path)}
    with open(os.path.join(out, 'build.json'), 'w') as f: json.dump(res, f, indent=1, ensure_ascii=False)
    return res


ESTIMATE_NOTE = 'DIMENSIONS MARKED ≈ ARE ESTIMATED FROM PHOTOS - VERIFY BEFORE MACHINING'


def mark(E, d, est, tol=0.02):
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
        match = False
        if dm['kind'] == 'linear':
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
        if dm.get('bc'):
            bc = next((x for x in spec['features'].get('bolt_circles', []) if x['id'] == dm['bc']), None)
            if bc and hit(bc['dia'], dias): match = True
        if match:
            base = t or ('%%c<>' if dm['kind'] in ('hole', 'diameter') else '<>')
            dm['text'] = '≈' + base
            marked.append(dm['id'])
    if est and ESTIMATE_NOTE not in spec['notes']: spec['notes'].append(ESTIMATE_NOTE)
    if not est and ESTIMATE_NOTE in spec['notes']: spec['notes'].remove(ESTIMATE_NOTE)
    spec = E.render(spec, d)
    return {'ok': True, 'dir': d, 'marked': marked, 'estimates': len(est),
            'note': ESTIMATE_NOTE if est else None, 'preview': os.path.join(d, 'preview.png'),
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
            res = {'ok': True, 'estimates': est, 'ready_for_final': not est}
        elif a.op == 'scale':
            vals = [round(scale_estimate(a.ref_px, p_, a.ref, a.ref_mm), 2) for p_ in a.px]
            res = {'ok': True, 'estimates_mm': vals, 'reference_mm': a.ref_mm or REFERENCES.get(a.ref),
                   'note': 'estimates only: valid if the reference is on the same plane and the shot is straight on'}
        else:
            res = mark(E, os.path.abspath(a.dir), estimates(load(a.spec)))
    except SpecError as e:
        print(json.dumps({'ok': False, 'error': str(e)}, ensure_ascii=False)); sys.exit(2)
    print(json.dumps(res, indent=1, ensure_ascii=False))
