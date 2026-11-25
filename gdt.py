"""GD&T for Épure: suggestions (pending until the user confirms), target parsing, and frame/symbol geometry.
All sizes here are paper mm; callers multiply by k (paper mm -> drawing units)."""
import math, re

TYPES = ['position', 'flatness', 'perpendicularity', 'parallelism', 'profile', 'runout', 'concentricity']
SYM = {'position': '⌖', 'flatness': '⏥', 'perpendicularity': '⟂', 'parallelism': '∥', 'profile': '⌓',
       'runout': '↗', 'concentricity': '◎'}
NEEDS_DATUM = {'position', 'perpendicularity', 'parallelism', 'runout', 'concentricity'}
NO_DATUM = {'flatness'}
DIA_DEFAULT = {'position': True, 'concentricity': True}      # tolerance zone is a cylinder (axis features)
FACE_NAME = {('z', -1): 'bottom', ('z', 1): 'top', ('y', -1): 'front', ('y', 1): 'back', ('x', -1): 'left', ('x', 1): 'right'}
AX = {'x': 0, 'y': 1, 'z': 2}
HF = 5.0          # frame height (paper mm)
DBOX = 5.0        # datum box size


# ------------------------------------------------------------------ targets
def parse_face(t, F):
    """'zmin' / 'xmax' / 'z=12.5' (model mm) -> (axis, level, sign) or None."""
    m = re.fullmatch(r'\s*([xyz])\s*(min|max|=\s*(-?\d+(?:\.\d+)?))\s*', str(t), re.I)
    if not m: return None
    ax = m.group(1).lower(); lo, hi = F['bbox']; i = AX[ax]
    if m.group(2).lower() == 'min': return ax, lo[i], -1
    if m.group(2).lower() == 'max': return ax, hi[i], 1
    lev = float(m.group(3))
    return ax, lev, (1 if abs(lev - hi[i]) < abs(lev - lo[i]) else -1)


def face_key(t, F):
    f = parse_face(t, F)
    return None if f is None else f"{f[0]}={f[1]:.4f}"


def face_label(spec, t, fmt):
    F = spec['features']; ax, lev, sg = parse_face(t, F)
    lo, hi = F['bbox']; i = AX[ax]
    if abs(lev - lo[i]) < 1e-6 or abs(lev - hi[i]) < 1e-6:
        return f"the {FACE_NAME[(ax, sg)]} face ({ax.upper()} {'min' if sg < 0 else 'max'})"
    return f"the face at {ax.upper()} = {fmt(spec, lev)}"


def find_feature(spec, fid):
    F = spec['features']
    for coll in ('holes', 'cyls', 'slots', 'bolt_circles', 'slopes', 'fillets', 'chamfers'):
        for f in F.get(coll, []):
            if f['id'] == str(fid).upper(): return f
    return None


def valid_target(spec, t):
    return parse_face(t, spec['features']) is not None or find_feature(spec, t) is not None


# ------------------------------------------------------------------ text form
def fcf_text(spec, fc, fmt):
    tol = ('Ø' if fc.get('dia') else '') + fmt(spec, fc['value']) + (' Ⓜ' if fc.get('mmc') else '')
    return ' | '.join([SYM[fc['type']], tol] + list(fc.get('datums', [])))


def tol_text(spec, fc, fmt):
    return ('Ø' if fc.get('dia') else '') + fmt(spec, fc['value'])


# ------------------------------------------------------------------ suggestions
def _area(F, ax, lev):
    for l, a in F['levels'][ax]:
        if abs(l - lev) < 1e-4: return a
    return 0.0


def suggest(spec, fmt):
    F = spec['features']; lo, hi = F['bbox']
    ext = [hi[i] - lo[i] for i in range(3)]
    faces = []
    for ax in 'xyz':
        i = AX[ax]
        for sg, lev in ((-1, lo[i]), (1, hi[i])):
            a = _area(F, ax, lev)
            if a > 0: faces.append((a, sg, ax, lev))
    if spec.get('mesh') or not faces: return []
    inch = spec['units'] == 'in'
    V = (lambda mm, inches: inches * 25.4) if inch else (lambda mm, inches: mm)
    out = []
    def add(**kw):
        kw['id'] = f"G{len(out) + 1}"; kw['status'] = 'pending'; out.append(kw)
    # turned part (shaft): datum on the main diameter, runout on the other coaxial diameters
    cyls = F.get('cyls', [])
    if cyls:
        def lat(c): return math.pi * c['dia'] * (c['hi'] - c['lo'])
        main = max(cyls, key=lat)
        j = AX[main['axis']]
        coax = [c for c in cyls if c['axis'] == main['axis'] and
                all(abs(c['center'][q] - main['center'][q]) < 1e-3 for q in range(3) if q != j)]
        if lat(main) > 2 * max(f_[0] for f_ in faces) and len(coax) >= 2:
            add(type='datum', letter='A', target=main['id'],
                text=f"Datum A = the Ø{fmt(spec, main['dia'])} diameter ({main['id']}): the longest diameter, most likely "
                     f"where the part is held or runs in bearings. The other diameters are checked against its axis.")
            ro = V(0.05, 0.002)
            for c in coax:
                if c is main: continue
                add(type='fcf', fcf=dict(type='runout', value=ro, dia=False, mmc=False, datums=['A'], target=c['id']),
                    text=f"Circular runout {fmt(spec, ro)} to A on the Ø{fmt(spec, c['dia'])} diameter ({c['id']}): "
                         f"turned on datum A, the dial reading on this diameter may move at most {fmt(spec, ro)}, "
                         f"so it runs true with A.")
            return out
    # A: biggest flat outside face; faces within 10% count as tied -> bottom/min side, Z first
    amax = max(f_[0] for f_ in faces)
    faces.sort(key=lambda f_: (f_[0] < 0.9 * amax, f_[1], 'zyx'.index(f_[2]), -f_[0]))
    aA, sA, axA, levA = faces[0]
    tA = f"{axA}{'min' if sA < 0 else 'max'}"
    others = [ax for ax in 'xyz' if ax != axA]
    # holes that are the big end of a counterbore/counterdrill don't get their own GD&T
    big = set()
    for h in F['holes']:
        for sm in F['holes']:
            if sm is h or sm['axis'] != h['axis'] or sm['dia'] >= h['dia'] or h['thru']: continue
            j = AX[h['axis']]
            if all(abs(sm['center'][q] - h['center'][q]) < 1e-3 for q in range(3) if q != j): big.add(h['id'])
    in_bc = {x for bc in F.get('bolt_circles', []) for x in bc['holes']}
    bores = [h for h in F['holes'] if h['axis'] == axA and h['thru'] and h['id'] not in big and h['id'] not in in_bc
             and h['dia'] >= 0.2 * min(ext[AX[o]] for o in others)]
    bore = max(bores, key=lambda h: h['dia']) if bores else None
    add(type='datum', letter='A', target=tA,
        text=f"Datum A = {face_label(spec, tA, fmt)}: the biggest flat face ({aA:,.0f} mm²), most likely the face the part "
             f"sits or bolts on. Measurements start from it.")
    letters = ['A']
    def side_faces():
        res = []
        for ax in others:
            i = AX[ax]
            for sg, lev in ((-1, lo[i]), (1, hi[i])):
                a = _area(F, ax, lev)
                o0, o1 = [o for o in 'xyz' if o != ax]
                if a > 0.1 * ext[AX[o0]] * ext[AX[o1]]:
                    res.append((sg != -1, -a, ax, sg, lev, a)); break   # min side first (where the dims start)
        res.sort()
        return res
    if bore:
        add(type='datum', letter='B', target=bore['id'],
            text=f"Datum B = the Ø{fmt(spec, bore['dia'])} bore ({bore['id']}): it locates the part sideways, so other features "
                 f"are located from its axis.")
        letters.append('B')
        sf = side_faces()
        if sf:
            _, _, ax, sg, lev, a = sf[0]; t = f"{ax}{'min' if sg < 0 else 'max'}"
            add(type='datum', letter='C', target=t,
                text=f"Datum C = {face_label(spec, t, fmt)}: stops the part turning about datum B (clocking).")
            letters.append('C')
    else:
        for (_, _, ax, sg, lev, a), L in zip(side_faces(), 'BC'):
            t = f"{ax}{'min' if sg < 0 else 'max'}"
            why = 'stops it sliding and turning on A' if L == 'B' else 'stops the last sideways slide'
            add(type='datum', letter=L, target=t,
                text=f"Datum {L} = {face_label(spec, t, fmt)} ({a:,.0f} mm²): {why}; the {ax.upper()} dimensions start here.")
            letters.append(L)
    fl = V(0.05, 0.002) if max(ext) <= 150 else V(0.1, 0.004)
    add(type='fcf', fcf=dict(type='flatness', value=fl, dia=False, mmc=False, datums=[], target=tA),
        text=f"Flatness {fmt(spec, fl)} on datum A: the whole face must lie between two parallel planes {fmt(spec, fl)} apart. "
             f"Worth it because every other measurement starts from A.")
    if bore:
        pp = V(0.05, 0.002)
        add(type='fcf', fcf=dict(type='perpendicularity', value=pp, dia=True, mmc=False, datums=['A'], target=bore['id']),
            text=f"Perpendicularity Ø{fmt(spec, pp)} to A on the Ø{fmt(spec, bore['dia'])} bore: its axis must stay inside a "
                 f"Ø{fmt(spec, pp)} cylinder square to A, so a shaft or pin goes in straight.")
    # position on hole patterns perpendicular to A
    groups = {}
    for h in F['holes']:
        if h['axis'] != axA or h['id'] in big or (bore and h['id'] == bore['id']): continue
        groups.setdefault((round(h['dia'], 3), h['thru'], round(h['depth'], 3)), []).append(h)
    for (dia, thru, depth), hs in groups.items():
        ids = [h['id'] for h in hs]
        threaded = any(i in spec.get('threads', {}) for i in ids) or any(
            t['status'] == 'pending' and t['confidence'] == 'likely' and set(ids) & set(t['holes'])
            for t in spec.get('thread_suggestions', []))
        mmc = thru and not threaded
        dat = letters[:3]
        n = len(hs)
        pv = V(0.1, 0.005)
        add(type='fcf', fcf=dict(type='position', value=pv, dia=True, mmc=mmc, datums=dat, target=ids[0], group=ids),
            text=f"Position Ø{fmt(spec, pv)}{' Ⓜ' if mmc else ''} to {'|'.join(dat)} on the {str(n) + "X " if n > 1 else ""}Ø{fmt(spec, dia)} hole{"s" if n > 1 else ""} "
                 f"({','.join(ids)}): each hole centre must be inside a Ø{fmt(spec, pv)} circle around its exact location"
                 + ("; Ⓜ (max material) gives extra tolerance as the hole comes out bigger, which suits bolt holes" if mmc else
                    "; no Ⓜ because these look like tapped or blind holes")
                 + ". Their location dimensions become basic (boxed).")
    return out


# ------------------------------------------------------------------ geometry (paper mm; caller passes k)
def _poly(msp, pts, k, att, closed=False):
    msp.add_lwpolyline([(x * 1, y * 1) for x, y in pts], close=closed, dxfattribs=att)


def draw_symbol(msp, typ, cx, cy, k, att):
    """GD&T characteristic symbol drawn as geometry (fonts in CAD apps often lack these glyphs)."""
    P = lambda x, y: (cx + x * k, cy + y * k)
    lw = dict(att)
    if typ == 'position':
        msp.add_circle(P(0, 0), 1.15 * k, dxfattribs=lw)
        msp.add_line(P(-1.7, 0), P(1.7, 0), dxfattribs=lw); msp.add_line(P(0, -1.7), P(0, 1.7), dxfattribs=lw)
    elif typ == 'flatness':
        _poly(msp, [P(-1.7, -0.7), P(0.9, -0.7), P(1.7, 0.7), P(-0.9, 0.7)], k, lw, closed=True)
    elif typ == 'perpendicularity':
        msp.add_line(P(-1.6, -1.3), P(1.6, -1.3), dxfattribs=lw); msp.add_line(P(0, -1.3), P(0, 1.6), dxfattribs=lw)
    elif typ == 'parallelism':
        msp.add_line(P(-1.3, -1.4), P(-0.1, 1.4), dxfattribs=lw); msp.add_line(P(0.1, -1.4), P(1.3, 1.4), dxfattribs=lw)
    elif typ == 'profile':
        msp.add_arc(P(0, -0.8), 1.6 * k, 0, 180, dxfattribs=lw); msp.add_line(P(-1.6, -0.8), P(1.6, -0.8), dxfattribs=lw)
    elif typ == 'runout':
        msp.add_line(P(-1.3, -1.3), P(1.3, 1.3), dxfattribs=lw)
        msp.add_solid([P(1.5, 1.5), P(0.4, 1.2), P(1.2, 0.4)], dxfattribs=lw)
    elif typ == 'concentricity':
        msp.add_circle(P(0, 0), 0.7 * k, dxfattribs=lw); msp.add_circle(P(0, 0), 1.5 * k, dxfattribs=lw)


def fcf_cells(spec, fc, fmt, text_w):
    tol = tol_text(spec, fc, fmt)
    w_tol = text_w(tol) * 1.25 + 2.4 + (4.2 if fc.get('mmc') else 0)
    return [HF, w_tol] + [HF for _ in fc.get('datums', [])]


def fcf_width(spec, fc, fmt, text_w):
    return sum(fcf_cells(spec, fc, fmt, text_w))


def draw_fcf(msp, spec, fc, x0, ymid, k, fmt, text_w, add_text, TA, right_align=False, layer='GDT'):
    """Feature control frame; (x0, ymid) drawing units = left-middle (or right-middle) of the frame."""
    att = {'layer': layer}
    cells = fcf_cells(spec, fc, fmt, text_w)
    W = sum(cells) * k
    if right_align: x0 -= W
    yb, yt = ymid - HF / 2 * k, ymid + HF / 2 * k
    msp.add_lwpolyline([(x0, yb), (x0 + W, yb), (x0 + W, yt), (x0, yt)], close=True, dxfattribs=att)
    x = x0
    for i, cw in enumerate(cells):
        if i: msp.add_line((x, yb), (x, yt), dxfattribs=att)
        cx = x + cw * k / 2
        if i == 0:
            draw_symbol(msp, fc['type'], cx, ymid, k, att)
        elif i == 1:
            tol = tol_text(spec, fc, fmt)
            add_text(msp, tol, (x + 1.2 * k, ymid), 2.5 * k, layer, TA.MIDDLE_LEFT)
            if fc.get('mmc'):
                mx = x + (1.2 + text_w(tol) * 1.25 + 2.0) * k
                msp.add_circle((mx, ymid), 1.35 * k, dxfattribs=att)
                add_text(msp, 'M', (mx, ymid), 1.7 * k, layer, TA.MIDDLE_CENTER)
        else:
            add_text(msp, fc['datums'][i - 2], (cx, ymid), 2.5 * k, layer, TA.MIDDLE_CENTER)
        x += cw * k
    return W


def draw_datum(msp, letter, base, direction, k, add_text, TA, layer='GDT', stem=2.0):
    """Datum feature symbol: filled triangle with its base on the feature at `base` (drawing units), stem along
    `direction` (unit vector, away from the feature), then a square with the letter. Returns the far end point."""
    att = {'layer': layer}
    dx, dy = direction; px, py = -dy, dx
    bx, by = base
    th, tw = 2.2 * k, 1.6 * k
    apex = (bx + dx * th, by + dy * th)
    msp.add_solid([(bx + px * tw, by + py * tw), (bx - px * tw, by - py * tw), apex], dxfattribs=att)
    s0 = apex; s1 = (apex[0] + dx * stem * k, apex[1] + dy * stem * k)
    msp.add_line(s0, s1, dxfattribs=att)
    h = DBOX * k / 2
    c = (s1[0] + dx * h, s1[1] + dy * h)
    msp.add_lwpolyline([(c[0] - h, c[1] - h), (c[0] + h, c[1] - h), (c[0] + h, c[1] + h), (c[0] - h, c[1] + h)],
                       close=True, dxfattribs=att)
    add_text(msp, letter, c, 3.0 * k, layer, TA.MIDDLE_CENTER)
    return (c[0] + dx * h, c[1] + dy * h)
