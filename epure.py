#!/usr/bin/env python3
"""Épure: 3D CAD model -> dimensioned 2D shop drawing (DXF + PNG preview + PDF).
Run with /workspace/epure/venv/bin/python (or ./epure wrapper). See README.md."""
import argparse, copy, datetime, hashlib, json, math, os, shutil, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import gdt

SHEETS = {  # landscape, paper mm
    'ANSI_A': (279.4, 215.9), 'ANSI_B': (431.8, 279.4), 'ANSI_C': (558.8, 431.8),
    'A4': (297.0, 210.0), 'A3': (420.0, 297.0), 'A2': (594.0, 420.0),
}
FAMILY = {'ANSI': ['ANSI_A', 'ANSI_B'], 'ISO': ['A4', 'A3']}   # auto-pick; C/A2 only when set explicitly
SCALES = [10, 5, 4, 2, 1, 1/2, 1/4, 1/5, 1/8, 1/10, 1/20, 1/50, 1/100]
BORDER = 10.0
TB_W, TB_ROW = 170.0, 8.0
TB_H = TB_ROW * 7
DIM_FIRST, DIM_STEP = 9.0, 6.5      # paper mm
TXT = 2.5                            # paper mm dimension text
MIN_RADIUS_PAPER = 0.5               # radius callouts smaller than this on paper are skipped (listed in 'skipped')
MAIN = ['front', 'top', 'right', 'iso']
UNITS_WORD = {'in': 'INCHES', 'mm': 'MILLIMETERS'}

DEFAULT_CONFIG = {
    'company': '', 'logo': '', 'units': 'in', 'projection': 'third', 'drawn_by': '',
    'tolerances': ['X.X ±0.1', 'X.XX ±0.01', 'X.XXX ±0.005', 'ANGLES ±0.5°'],
    'notes': ['BREAK ALL SHARP EDGES', 'ALL DIMENSIONS IN {UNITS} UNLESS NOTED', 'REMOVE ALL BURRS'],
    'sheet_family': '', 'decimals': None, 'hidden_lines': True, 'iso_view': True,
}
TITLE_FIELDS = ['company', 'logo', 'part_name', 'part_number', 'revision', 'material', 'finish', 'quantity',
                'drawn_by', 'date']


# ------------------------------------------------------------------ helpers
def scale_str(s):
    if s >= 1: return f'{s:g}:1'
    return f'1:{1/s:g}'


def parse_scale(t):
    if t in (None, '', 'auto'): return None
    a, b = str(t).split(':'); return float(a) / float(b)


def uf(spec): return 25.4 if spec['units'] == 'in' else 1.0


def fmt(spec, mm):
    return f"{mm/uf(spec):.{spec['decimals']}f}"


def load_json(p):
    if not p: return {}
    with open(p) as f: return json.load(f)


def save_spec(d, spec):
    with open(os.path.join(d, 'spec.json'), 'w') as f: json.dump(spec, f, indent=1, ensure_ascii=False)


def read_spec(d):
    with open(os.path.join(d, 'spec.json')) as f: return json.load(f)


# ------------------------------------------------------------------ geometry cache
_SHAPE = {}


def get_shape(spec):
    import geom
    key = spec['model']
    if key not in _SHAPE:
        _SHAPE[key] = geom.load(spec['model'], spec.get('model_units', 'mm'))
    return _SHAPE[key]


def view_key(spec, v):
    t = v['type']
    if t in MAIN: k = f"{t}|{spec['hidden_lines']}"
    elif t == 'section': k = f"section|{v['axis']}|{v['at']:.5f}"
    else: return None
    return hashlib.md5((spec['model'] + '|' + k).encode()).hexdigest()[:12]


def view_geom(spec, d, v, cache):
    import geom
    if v['type'] == 'detail':
        par = next(x for x in spec['views'] if x['id'] == v['parent'])
        pg = view_geom(spec, d, par, cache)
        return detail_geom(pg, v)
    k = view_key(spec, v)
    if k not in cache:
        shape = get_shape(spec)[0]
        if v['type'] == 'section':
            g = geom.section(shape, v['axis'], v['at'])
        else:
            g = geom.hlr(shape, v['type'], hidden=spec['hidden_lines'] and v['type'] != 'iso',
                         fast=spec.get('kind') in ('assembly', 'exploded'))
        g['bbox'] = geom.prims_bbox(g['visible'] + g['hidden'])
        cache[k] = g
        with open(os.path.join(d, 'geom_cache.json'), 'w') as f: json.dump(cache, f)
    return cache[k]


def clip_poly_circle(pts, c, r):
    """Clip polyline to circle; return list of polylines inside."""
    out, cur = [], []
    inside = lambda p: math.dist(p, c) <= r + 1e-9
    for i in range(len(pts)):
        p = pts[i]
        if i == 0:
            if inside(p): cur = [p]
            continue
        q = pts[i-1]
        # intersections of segment q->p with circle
        dx, dy = p[0]-q[0], p[1]-q[1]; fx, fy = q[0]-c[0], q[1]-c[1]
        a = dx*dx+dy*dy; b = 2*(fx*dx+fy*dy); cc = fx*fx+fy*fy-r*r
        ts = []
        if a > 0:
            disc = b*b-4*a*cc
            if disc >= 0:
                sd = math.sqrt(disc)
                ts = sorted(t for t in [(-b-sd)/(2*a), (-b+sd)/(2*a)] if 0 < t < 1)
        pt = lambda t: [q[0]+dx*t, q[1]+dy*t]
        for t in ts:
            x = pt(t)
            if cur: cur.append(x); out.append(cur); cur = []
            else: cur = [x]
        if inside(p):
            if not cur: cur = [p]
            else: cur.append(p)
        elif cur and not ts:
            out.append(cur); cur = []
    if len(cur) > 1: out.append(cur)
    return [o for o in out if len(o) > 1]


def detail_geom(pg, v):
    import geom
    c, r = v['center'], v['radius']
    res = {'visible': [], 'hidden': []}
    for kind in ('visible', 'hidden'):
        for p in pg[kind]:
            for seg in clip_poly_circle(geom.prim_points(p, 96), c, r):
                res[kind].append({'t': 'poly', 'p': seg})
    res['hatch'] = []
    res['bbox'] = [c[0]-r, c[1]-r, c[0]+r, c[1]+r]
    return res


# ------------------------------------------------------------------ view axes / sides
def view_frame_name(v):
    import geom
    if v['type'] in MAIN: return v['type']
    if v['type'] == 'section': return geom.SECTION_FRAME[v['axis']]
    return v.get('_pframe')


def view_axes(spec, v):
    import geom
    if v['type'] == 'detail':
        par = next(x for x in spec['views'] if x['id'] == v['parent'])
        return view_axes(spec, par)
    f = view_frame_name(v)
    return geom.VIEW_AXES.get(f)


def default_side(spec, vid, axis):
    third = spec['projection'] == 'third'
    t = {
        ('front', 'x'): 'below' if third else 'above', ('front', 'z'): 'left' if third else 'right',
        ('top', 'x'): 'above' if third else 'below', ('top', 'y'): 'left' if third else 'right',
        ('right', 'y'): 'below' if third else 'above', ('right', 'z'): 'right' if third else 'left',
    }
    if (vid, axis) in t: return t[(vid, axis)]
    v = next(x for x in spec['views'] if x['id'] == vid)
    ua, va = view_axes(spec, v)
    return 'below' if axis == ua else 'right'


# ------------------------------------------------------------------ auto dimensions
def find_lines(g, which, coord_idx, val, tol=1e-3):
    """lines in view geometry that are constant at coord_idx==val (0:u const -> vertical)."""
    out = []
    for p in g[which]:
        if p['t'] == 'poly' and len(p['p']) >= 2 and all(abs(q[coord_idx]-val) < tol for q in p['p']):
            o = 1 - coord_idx     # straight polyline (e.g. a circle seen edge-on): use its two extreme points
            a = min(p['p'], key=lambda q: q[o]); b = max(p['p'], key=lambda q: q[o])
            if math.dist(a, b) > 1e-3: out.append({'t': 'line', 'p': [a, b]})
            continue
        if p['t'] != 'line': continue
        a, b = p['p']
        if abs(a[coord_idx]-val) < tol and abs(b[coord_idx]-val) < tol and math.dist(a, b) > 1e-3:
            out.append(p)
    return out


def auto_dims(spec, d, cache):
    F = spec['features']
    lo, hi = F['bbox']
    views = {v['id']: v for v in spec['views'] if v['type'] in ('front', 'top', 'right')}
    geoms = {k: view_geom(spec, d, v, cache) for k, v in views.items()}
    cand = {'x': ['front', 'top'], 'y': ['top', 'right'], 'z': ['front', 'right']}
    stacks = {}   # (view, axis) -> list of refs (numbers or feature ids)
    AX = {'x': 0, 'y': 1, 'z': 2}

    def add(view, axis, ref):
        stacks.setdefault((view, axis), [])
        if ref not in stacks[(view, axis)]: stacks[(view, axis)].append(ref)

    # cylinders that span an axis completely (shaft) -> no overall on that axis
    def covered_by_boss(axis):
        i = AX[axis]
        for c in F['cyls']:
            if c['axis'] != axis and abs(c['center'][i]-c['dia']/2-lo[i]) < 1e-3 and abs(c['center'][i]+c['dia']/2-hi[i]) < 1e-3:
                return True
        return False

    for axis in 'xyz':
        levels = [l for l, a in F['levels'][axis]]
        areas = dict((round(l, 4), a) for l, a in F['levels'][axis])
        if spec.get('mesh'):
            amax = max(areas.values()) if areas else 0
            levels = [l for l in levels if areas[round(l, 4)] > 0.02*amax or abs(l-lo[AX[axis]]) < 1e-3 or abs(l-hi[AX[axis]]) < 1e-3]
        if not covered_by_boss(axis):
            levels = sorted(set([round(lo[AX[axis]], 4), round(hi[AX[axis]], 4)] + [round(l, 4) for l in levels]))
        if len(levels) > 7:
            keep = sorted(levels[1:-1], key=lambda l: -areas.get(round(l, 4), 0))[:5]
            levels = sorted([levels[0], levels[-1]] + keep)
        vs = [v for v in cand[axis] if v in views]
        if not vs or len(levels) < 2: continue
        def vis_count(vn):
            ua, va = views[vn] and __import__('geom').VIEW_AXES[vn]
            ci = 0 if axis == ua else 1
            return sum(1 for l in levels if find_lines(geoms[vn], 'visible', ci, l))
        best = max(vs, key=lambda vn: (vis_count(vn), -vs.index(vn)))
        datum = round(lo[AX[axis]], 4)
        for l in levels:
            if abs(l - datum) > 1e-4: add(best, axis, l)
    import geom
    circle_view = {'z': 'top', 'y': 'front', 'x': 'right'}
    # holes
    groups = {}
    # counterbores: a larger blind coaxial hole sitting on the end of a smaller one -> one callout
    cbore = {}
    for big in F['holes']:
        for sm in F['holes']:
            if sm is big or sm['axis'] != big['axis'] or sm['dia'] >= big['dia'] or big['thru']: continue
            i = AX[big['axis']]
            same_axis = all(abs(sm['center'][j]-big['center'][j]) < 1e-3 for j in range(3) if j != i)
            if same_axis and (abs(big['lo']-sm['hi']) < 1e-3 or abs(big['hi']-sm['lo']) < 1e-3):
                cbore[sm['id']] = big; big['_is_cbore'] = True
    cdrill = {}
    for kf in F.get('chamfers', []):   # counterdrill: small hole -> cone -> larger blind hole
        if kf.get('sub') == 'cdrill' and len(kf.get('holes', [])) >= 2:
            hs_ = {h['id']: h for h in F['holes']}
            sm, big = hs_[kf['holes'][0]], hs_[kf['holes'][-1]]
            cbore[sm['id']] = big; big['_is_cbore'] = True; cdrill[sm['id']] = kf
    bc_of = {hid: bc for bc in F.get('bolt_circles', []) for hid in bc['holes']}
    hole_ch = {}
    for kf in F.get('chamfers', []):
        if kf.get('hole') and kf.get('sub') != 'cdrill': hole_ch.setdefault(kf['hole'], []).append(kf)
    for bc in F.get('bolt_circles', []):
        vn = circle_view[bc['axis']]
        if vn not in views: continue
        for ax in geom.VIEW_AXES[vn]:
            on_axis = any(abs(c['center'][AX[ax]]-bc['center'][AX[ax]]) < 1e-3 and c['axis'] != ax
                          for c in F['cyls'] + [h for h in F['holes'] if h['id'] not in bc['holes']])
            if not on_axis and abs(bc['center'][AX[ax]]-lo[AX[ax]]) > 1e-4: add(vn, ax, bc['id'])
    for h in F['holes']:
        if h.get('_is_cbore'): continue
        vn = circle_view[h['axis']]
        if vn not in views: continue
        ua, va = geom.VIEW_AXES[vn]
        for ax in (ua, va) if h['id'] not in bc_of else ():
            on_boss_axis = any(c['axis'] != ax and abs(c['center'][AX[ax]]-h['center'][AX[ax]]) < 1e-3 for c in F['cyls'])
            if not on_boss_axis and abs(h['center'][AX[ax]]-lo[AX[ax]]) > 1e-4:
                add(vn, ax, h['id'])
        cb = cbore.get(h['id'])
        chs = hole_ch.get(h['id'], [])
        key = (h['axis'], round(h['dia'], 4), h['thru'], round(h['depth'], 3),
               (round(cb['dia'], 4), round(cb['depth'], 4)) if cb else None,
               bc_of[h['id']]['id'] if h['id'] in bc_of else None,
               tuple(sorted((c.get('sub', ''), round(c['legs'][0], 3), round(c['angle'], 1)) for c in chs)),
               round(cdrill[h['id']]['included'], 1) if h['id'] in cdrill else None)
        groups.setdefault(key, []).append(h)
    for h in F['holes']: h.pop('_is_cbore', None)
    dims = []
    for key, hs in groups.items():
        vn = circle_view[key[0]]
        rep = max(hs, key=lambda h: sum(geom.project(h['center'], vn)))
        n = len(hs)
        txt = (f'{n}X ' if n > 1 else '') + '<hole>'
        extra = {}
        if key[5]:
            bc = next(b for b in F['bolt_circles'] if b['id'] == key[5])
            txt += '\nEQ SP ON %%c<bc> BC'; extra['bc'] = bc['id']
            ua, va = geom.VIEW_AXES[vn]
            angs = []
            for h in hs:
                hu, hv = geom.project(h['center'], vn); cu, cv = geom.project(bc['center'], vn)
                angs.append(math.degrees(math.atan2(hv - cv, hu - cu)) % 360)
            if not any(min(a % 90, 90 - a % 90) < 0.05 for a in angs):
                txt += f'\n1ST HOLE {min(angs):.1f}° FROM HORIZONTAL'.replace('.0°', '°')
        if key[4] and key[7]:
            txt += '\nCDRILL %%c<cb> x <cbdepth> DEEP X <cdang>°'; extra['cbore'] = cbore[rep['id']]['id']
            extra['cdrill'] = cdrill[rep['id']]['id']
        elif key[4]:
            txt += '\nCBORE %%c<cb> x <cbdepth> DEEP'; extra['cbore'] = cbore[rep['id']]['id']
        if key[6]:
            extra['chamfers'] = [c['id'] for c in hole_ch.get(rep['id'], [])]
            txt += '\n<holechamfer>'
        dims.append(dict({'kind': 'hole', 'view': vn, 'feature': rep['id'], 'group': [h['id'] for h in hs], 'text': txt, 'dist': 6}, **extra))
    # slots
    sgroups = {}
    for s in F['slots']:
        vn = circle_view[s['axis']]
        if vn not in views: continue
        ua, va = geom.VIEW_AXES[vn]
        for ax in (ua, va):
            for cid in (s['id'] + '.1', s['id'] + '.2'):
                pass
        for end in ('c1', 'c2'):
            for ax in (ua, va):
                val = round(s[end][AX[ax]], 4)
                if abs(val-lo[AX[ax]]) > 1e-4: add(vn, ax, val)
        sgroups.setdefault((s['axis'], round(s['width'], 4), round(s['length'], 4)), []).append(s)
    for key, ss in sgroups.items():
        vn = circle_view[key[0]]
        rep = max(ss, key=lambda s: sum(geom.project(s['center'], vn)))
        n = len(ss)
        txt = (f'{n}X ' if n > 1 else '') + 'SLOT <w> x <l>'
        dims.append({'kind': 'leader', 'view': vn, 'feature': rep['id'], 'group': [x['id'] for x in ss], 'text': txt, 'dist': 6})
    # fillets / rounds: radius callouts where the arc is seen as an arc
    inplane0 = {'z': 'front', 'x': 'front', 'y': 'top'}
    warns = []
    def arc_visible(g, c2, r):
        return any(p['t'] in ('arc', 'circle') and math.dist(p['c'], c2) < 1e-3 and abs(p['r'] - r) < 1e-3 for p in g['visible'])
    def line_through(g, q):
        for p in g['visible']:
            if p['t'] != 'line': continue
            a, b = p['p']; L = math.dist(a, b)
            if L < 1e-9: continue
            t = ((q[0]-a[0])*(b[0]-a[0]) + (q[1]-a[1])*(b[1]-a[1])) / L**2
            if -1e-6 <= t <= 1+1e-6 and abs((q[0]-a[0])*(b[1]-a[1]) - (q[1]-a[1])*(b[0]-a[0])) / L < 1e-3: return True
        return False
    fgroups = {}
    for f in F.get('fillets', []):
        if f['kind'] == 'cyl':
            vn = circle_view[f['axis']]
            if vn not in views: continue
            if not arc_visible(geoms[vn], geom.project(f['center'], vn), f['r']):
                warns.append(f"{f['id']} R{fmt(spec, f['r'])}: arc hidden in its view, not called out"); continue
        else:
            vn = inplane0[f['axis']]
            if vn not in views: continue
            ua, va = geom.VIEW_AXES[vn]
            perp = ua if f['axis'] == va else va
            p3 = list(f['center']); p3[AX[perp]] += f['rho']; p3[AX[f['axis']]] = f['h']
            f['apt'] = p3
        fgroups.setdefault((vn, round(f['r'], 3)), []).append(f)
    for (vn, r), fs in fgroups.items():
        n = len(fs)
        dims.append({'kind': 'leader', 'view': vn, 'feature': fs[0]['id'], 'group': [x['id'] for x in fs],
                     'text': (f'{n}X ' if n > 1 else '') + 'R<r>', 'dist': 6})
    # chamfers (not on holes - those go into the hole callout)
    cgroups = {}
    for c in F.get('chamfers', []):
        if c.get('hole'): continue
        if c['kind'] == 'plane':
            vn = circle_view[c['edge_axis']]
            vis = vn in views and line_through(geoms[vn], geom.project(c['apt'], vn))
            la, lb = c['legs']
            key = ('plane', round(min(la, lb), 3), round(max(la, lb), 3))
        else:
            vn = inplane0[c['axis']]
            ua, va = geom.VIEW_AXES[vn]
            perp = ua if c['axis'] == va else va
            p3 = list(c['center']); p3[AX[perp]] += (c['rmax'] + c['rmin']) / 2
            c['apt'] = p3; vis = vn in views
            key = ('cone', round(c['legs'][0], 3), round(c['angle'], 1))
        cgroups.setdefault(key, []).append((c, vn, vis))
    for key, items in cgroups.items():
        n = len(items)
        visv = {}
        for c, vn, vis in items:
            if vis: visv.setdefault(vn, []).append(c)
        if not visv:
            warns.append(f"chamfer(s) {','.join(c['id'] for c, _, _ in items)} not visible as an edge in any view; not called out"); continue
        vn = max(visv, key=lambda v: len(visv[v]))
        if key[0] == 'cone': t = '<ca> X <cang>°'
        elif abs(key[1] - key[2]) < 1e-3: t = '<ca> X 45°'
        else: t = '<ca> X <cb>'
        dims.append({'kind': 'leader', 'view': vn, 'feature': visv[vn][0]['id'], 'group': [c['id'] for c in visv[vn]],
                     'text': (f'{n}X ' if n > 1 else '') + t, 'dist': 6})
    # slanted faces: angle in the edge-on view + locate the slope start
    for sl in F.get('slopes', []):
        vn = circle_view[sl['edge_axis']]
        if vn not in views: continue
        g = geoms[vn]; ua, va = geom.VIEW_AXES[vn]
        E0, O0 = geom.project(sl['p0'], vn), geom.project(sl['p1'], vn)
        mid = ((E0[0]+O0[0])/2, (E0[1]+O0[1])/2)
        if not line_through(g, mid):
            warns.append(f"{sl['id']}: slanted face not visible as a line in the {vn} view; angle not dimensioned"); continue
        best = None
        for E, O in ((E0, O0), (O0, E0)):
            L = math.dist(E, O); d = ((O[0]-E[0])/L, (O[1]-E[1])/L)
            for p in g['visible']:
                if p['t'] != 'line': continue
                a_, b_ = p['p']
                for P0, P1 in ((a_, b_), (b_, a_)):
                    if math.dist(P0, E) > 1e-3 or math.dist(P0, P1) < 1e-6: continue
                    r = ((P1[0]-P0[0])/math.dist(P0, P1), (P1[1]-P0[1])/math.dist(P0, P1))
                    if abs(r[0]) > 1e-6 and abs(r[1]) > 1e-6: continue   # reference must be horizontal/vertical
                    th = math.degrees(math.acos(max(-1, min(1, d[0]*r[0] + d[1]*r[1]))))
                    if th < 1 or th > 179: continue
                    ext = th > 90
                    if ext: r, th = (-r[0], -r[1]), 180 - th
                    score = (ext, abs(r[1]) > 0.5, th)   # prefer: no extension line, horizontal reference, smaller angle
                    if best is None or score < best[0]: best = (score, E, O, r, th, ext)
        if not best:
            warns.append(f"{sl['id']}: no horizontal/vertical edge meets the slope; angle not dimensioned"); continue
        _, E, O, r, th, ext = best
        dims.append({'kind': 'angular', 'view': vn, 'feature': sl['id'], 'E': list(E), 'O': list(O), 'r': list(r),
                     'ext': ext, 'value': th})
        for ax, val in ((ua, E[0]), (va, E[1])):
            done = any(abs(ref_value(spec, x, ax) - val) < 1e-3 for (v_, a_), xs in stacks.items() if a_ == ax for x in xs)
            if not done and abs(val - lo[AX[ax]]) > 1e-4:
                # put it where the slope start shows as an edge line (extension line won't cross the part)
                vs_ = [v_ for v_ in cand[ax] if v_ in views and
                       find_lines(geoms[v_], 'visible', 0 if geom.VIEW_AXES[v_][0] == ax else 1, val)]
                add(vs_[0] if vs_ else vn, ax, round(val, 4))
    spec['dim_warnings'] = warns
    # bosses: inside diameter dims
    inplane = {'z': 'front', 'x': 'front', 'y': 'top'}
    for c in F['cyls']:
        vn = inplane[c['axis']]
        if vn not in views: continue
        ua, va = geom.VIEW_AXES[vn]
        across = ua if c['axis'] == va else va
        i = AX[across]
        dims.append({'kind': 'linear', 'view': vn, 'axis': across, 'a': round(c['center'][i]-c['dia']/2, 4),
                     'b': round(c['center'][i]+c['dia']/2, 4), 'side': 'inside', 'at': round((c['lo']+c['hi'])/2, 4),
                     'text': '%%c<>', 'feature': c['id']})
    # stacks -> baseline dims
    for (vn, axis), refs in stacks.items():
        def val(r):
            return ref_value(spec, r, axis)
        refs = sorted(refs, key=val)
        # dedupe by value
        uniq = []
        for r in refs:
            if not uniq or abs(val(r)-val(uniq[-1])) > 1e-4: uniq.append(r)
        side = default_side(spec, vn, axis)
        for k, r in enumerate(uniq):
            dims.append({'kind': 'linear', 'view': vn, 'axis': axis, 'a': round(lo[AX[axis]], 4), 'b': r,
                         'side': side, 'offset': DIM_FIRST + DIM_STEP*k})
    for i, dm in enumerate(dims):
        dm['id'] = f'D{i+1}'; dm['auto'] = True
    return dims


def ref_point(spec, ref):
    """3D point (mm) for a ref: number handled elsewhere; 'H1','C2','S1','S1.1','S1.2','MIN','MAX','CENTER', 'x,y,z'."""
    F = spec['features']
    if isinstance(ref, (list, tuple)): return list(ref)
    r = str(ref).strip()
    if r.upper() == 'MIN': return F['bbox'][0]
    if r.upper() == 'MAX': return F['bbox'][1]
    if r.upper() == 'CENTER': return [(a+b)/2 for a, b in zip(*F['bbox'])]
    for coll in ('holes', 'cyls', 'slots', 'fillets', 'chamfers', 'bolt_circles', 'slopes'):
        for f in F.get(coll, []):
            if r.upper() == f['id']: return f['center']
            if coll == 'slots' and r.upper() in (f['id']+'.1', f['id']+'.2'):
                return f['c1'] if r.endswith('1') else f['c2']
    if ',' in r:
        return [float(x) for x in r.split(',')]
    raise ValueError(f'unknown reference {ref!r}')


def ref_value(spec, ref, axis):
    if isinstance(ref, (int, float)): return float(ref)
    r = str(ref).strip()
    if '=' in r:
        return float(r.split('=')[1])
    try:
        return float(r)
    except ValueError:
        pass
    return ref_point(spec, ref)['xyz'.index(axis)]


def ref_is_feature(ref):
    return isinstance(ref, str) and ref[:1].upper() in 'HCSFKBP' and not ref.replace('.', '').replace('-', '').isdigit()


# ------------------------------------------------------------------ layout
def _wlen(t):
    """Text length in Latin character widths (CJK / full-width glyphs are about 1.6 wide)."""
    import unicodedata
    return sum(1.6 if ord(c) >= 0x1100 and unicodedata.east_asian_width(c) in 'WF' else 1 for c in t)


def text_w(t):
    return _wlen(t) * TXT * 0.72


def stacked_sides(spec, vid):
    return {dm['side'] for dm in spec['dims'] if dm['view'] == vid and dm['kind'] == 'linear' and dm.get('side') not in (None, 'inside')}


def free_side(spec, vid, order=('right', 'left', 'above', 'below')):
    used = stacked_sides(spec, vid)
    for sd in order:
        if sd not in used: return sd
    return order[0]


def callout_side(spec, dm):
    if dm.get('side') in ('left', 'right', 'above', 'below'): return dm['side']
    return free_side(spec, dm['view'])


def inside_text_side(spec, dm):
    if dm.get('text_side') in ('left', 'right', 'above', 'below'): return dm['text_side']
    return free_side(spec, dm['view'])


def all_feats(spec):
    F = spec['features']
    return F['holes'] + F['cyls'] + F['slots'] + F.get('fillets', []) + F.get('chamfers', []) + F.get('bolt_circles', []) + F.get('slopes', [])


def callout_text(spec, dm):
    """Tokens: <> diameter (Ø added), <depth> hole depth, <cb>/<cbdepth> counterbore, <w>/<l> slot width/overall length."""
    F = spec['features']
    t = dm.get('text') or ('%%c<>' if dm['kind'] in ('hole', 'diameter') else '')
    f = next((x for x in all_feats(spec) if x['id'] == dm.get('feature')), None)
    if f and f['type'] == 'hole' and '<hole>' in t:
        th = next((spec.get('threads', {}).get(i) for i in (dm.get('group') or [f['id']]) if spec.get('threads', {}).get(i)), None)
        dia = 'Ø' + fmt(spec, f['dia'])
        if th:
            first = th['callout'] + (' THRU' if f['thru'] else f" x {fmt(spec, th['depth'])} DEEP")
            first += '\n' + dia + (' DRILL THRU' if f['thru'] else f" DRILL x {fmt(spec, f['depth'])} DEEP")
        else:
            first = dia + (' THRU' if f['thru'] else f" x {fmt(spec, f['depth'])} DEEP")
        t = t.replace('<hole>', first)
    if dm.get('bc'):
        bc = next(x for x in F['bolt_circles'] if x['id'] == dm['bc'])
        if bc['id'] in basic_refs(spec): t = t.replace('<bc> BC', '<bc> BC BASIC')
        t = t.replace('<bc>', fmt(spec, bc['dia']))
    if '<holechamfer>' in t:
        chs = [x for x in F.get('chamfers', []) if x['id'] in dm.get('chamfers', [])]
        lines = []
        csk = [c for c in chs if c.get('sub') == 'csk']; pl = [c for c in chs if c.get('sub') != 'csk']
        if csk:
            c = csk[0]
            lines.append(f"Ø{fmt(spec, 2*c['rmax'])} X {c['included']:g}° CSK" + (' BOTH ENDS' if len(csk) == 2 else ''))
        if pl:
            c = pl[0]
            lines.append(f"{fmt(spec, c['legs'][0])} X {c['angle']:.4g}° CHAMFER" + (' BOTH ENDS' if len(pl) == 2 else ''))
        line = '\n'.join(lines)
        t = t.replace('\n<holechamfer>', '\n' + line if line else '')
    if f and f['type'] == 'fillet':
        t = t.replace('<r>', fmt(spec, f['r']))
    if f and f['type'] == 'chamfer':
        t = t.replace('<ca>', fmt(spec, f['legs'][0])).replace('<cb>', fmt(spec, f['legs'][1])).replace('<cang>', f"{f['angle']:.4g}")
    if f and f['type'] in ('hole', 'cyl'):
        t = t.replace('%%c<>', '<>').replace('<>', 'Ø' + fmt(spec, f['dia']))
        if 'depth' in f: t = t.replace('<depth>', fmt(spec, f['depth']))
    if f and f['type'] == 'slot':
        t = t.replace('<w>', fmt(spec, f['width'])).replace('<l>', fmt(spec, f['length'] + f['width']))
    if dm.get('cdrill'):
        cd = next((x for x in F.get('chamfers', []) if x['id'] == dm['cdrill']), None)
        if cd: t = t.replace('<cdang>', f"{cd['included']:g}")
    if dm.get('cbore'):
        cb = next(x for x in F['holes'] if x['id'] == dm['cbore'])
        t = t.replace('<cb>', fmt(spec, cb['dia'])).replace('<cbdepth>', fmt(spec, cb['depth']))
    if dm.get('tol'): t += ' ' + dm['tol']
    return t.replace('%%c', 'Ø')


def refresh_bom(spec):
    """BOM rows follow the part drawings' title blocks (part no., name, material, finish)."""
    for b in spec.get('bom', []):
        pd = b.get('part_dir')
        if b.get('drawing') and pd and os.path.exists(os.path.join(pd, 'spec.json')):
            t = read_spec(pd)['title']
            b['part_number'] = t.get('part_number', b.get('part_number', ''))
            b['material'] = t.get('material', b.get('material', ''))
            b['finish'] = t.get('finish', b.get('finish', ''))
            if t.get('part_name'): b['description'] = t['part_name']


def _rect_perim(x0, y0, x1, y1):
    """Perimeter param helpers for a rectangle, t in [0, P) counter-clockwise from the right-middle."""
    w, h = x1 - x0, y1 - y0; P = 2 * (w + h)
    segs = [((x1, (y0+y1)/2), (x1, y1)), ((x1, y1), (x0, y1)), ((x0, y1), (x0, y0)), ((x0, y0), (x1, y0)), ((x1, y0), (x1, (y0+y1)/2))]
    lens = [math.dist(a, b) for a, b in segs]
    def at(t):
        t %= P
        for (a, b), L in zip(segs, lens):
            if t <= L:
                f = t / L if L else 0
                return (a[0] + (b[0]-a[0])*f, a[1] + (b[1]-a[1])*f)
            t -= L
        return segs[-1][1]
    def param(q):
        best, acc = None, 0.0
        for (a, b), L in zip(segs, lens):
            if L:
                f = max(0, min(1, ((q[0]-a[0])*(b[0]-a[0]) + (q[1]-a[1])*(b[1]-a[1])) / L**2))
                pt = (a[0] + (b[0]-a[0])*f, a[1] + (b[1]-a[1])*f)
                dd = math.dist(pt, q)
                if best is None or dd < best[0]: best = (dd, acc + f*L)
            acc += L
        return best[1]
    return P, at, param


def _spread(items, gap, P):
    """Spread perimeter params items[i][0] (sorted) so neighbours are >= gap apart, keeping their order and moving
    them as little as possible (pool-adjacent-violators on t_i - i*gap, cut open at the widest empty stretch)."""
    n = len(items)
    if n < 2: return
    ts = [it[0] for it in items]
    gaps = [((ts[(i + 1) % n] - ts[i]) % P, i) for i in range(n)]
    cut = max(gaps)[1] + 1                       # start right after the widest gap
    order = [(cut + i) % n for i in range(n)]
    base = ts[order[0]]
    u = [(ts[j] - base) % P for j in order]      # unwrapped, non-decreasing
    r = [u[i] - i * gap for i in range(n)]
    blocks = []                                  # PAV: [sum, count]
    for x in r:
        blocks.append([x, 1])
        while len(blocks) > 1 and blocks[-2][0] / blocks[-2][1] > blocks[-1][0] / blocks[-1][1]:
            sm, c = blocks.pop(); blocks[-1][0] += sm; blocks[-1][1] += c
    fit = []
    for sm, c in blocks: fit += [sm / c] * c
    for i, j in enumerate(order): items[j][0] = (base + fit[i] + i * gap) % P


def draw_balloons(msp, spec, vinfo, s, k):
    """Item balloons around the view, in the same angular order as their anchors (so leaders don't cross)."""
    from ezdxf.enums import TextEntityAlignment as TA
    att = {'layer': 'NOTES'}
    byview = {}
    for b in spec.get('balloons', []):
        if b.get('view') in vinfo and b.get('anchor'): byview.setdefault(b['view'], []).append(b)
    for vid, bs in byview.items():
        vi = vinfo[vid]; bb = vi.get('bm', vi['b'])
        LL, UR = vi.get('Tm', vi['T'])(bb[0], bb[1]), vi.get('Tm', vi['T'])(bb[2], bb[3])
        c = ((LL[0]+UR[0])/2, (LL[1]+UR[1])/2)
        off = (BALLOON_OFF + BALLOON_R) * k
        x0, y0, x1, y1 = LL[0] - off, LL[1] - off, UR[0] + off, UR[1] + off
        P, at, param = _rect_perim(x0, y0, x1, y1)
        items = []
        for b in bs:
            A = vi['T'](*b['anchor'])
            ang = math.radians(b['angle']) if b.get('angle') is not None else math.atan2(A[1]-c[1], A[0]-c[0])
            far = (c[0] + math.cos(ang) * 1e4 * k, c[1] + math.sin(ang) * 1e4 * k)
            # ray from the centre through the anchor, clipped to the balloon rectangle
            tmin = min(t_ for t_ in [((x1 if math.cos(ang) > 0 else x0) - c[0]) / (far[0]-c[0]) if abs(far[0]-c[0]) > 1e-12 else 9e9,
                                     ((y1 if math.sin(ang) > 0 else y0) - c[1]) / (far[1]-c[1]) if abs(far[1]-c[1]) > 1e-12 else 9e9])
            q = (c[0] + (far[0]-c[0])*tmin, c[1] + (far[1]-c[1])*tmin)
            items.append([param(q), b, A])
        items.sort(key=lambda t_: t_[0])
        gap = min((2 * BALLOON_R + 2.5) * k, P / max(len(items), 1))
        _spread(items, gap, P)
        for t_, b, A in items:
            C = at(t_)
            if b.get('dist'):
                dx, dy = C[0]-c[0], C[1]-c[1]; L = math.hypot(dx, dy) or 1
                C = (C[0] + dx/L*b['dist']*k, C[1] + dy/L*b['dist']*k)
            r = BALLOON_R * k
            msp.add_circle(C, r, dxfattribs=att)
            add_text(msp, str(b['item']), C, 3.5*k, 'NOTES', TA.MIDDLE_CENTER)
            L = math.dist(C, A)
            if L > r:
                E = (C[0] + (A[0]-C[0]) * r / L, C[1] + (A[1]-C[1]) * r / L)
                msp.add_line(E, A, dxfattribs=att)
            h = msp.add_hatch(color=7, dxfattribs=att)
            h.paths.add_edge_path().add_arc(A, 0.6*k, 0, 360)


def gdt_attach(spec, dm):
    """Accepted feature control frames / datum letters that belong under this callout."""
    G = spec.get('gdt') or {}
    inside = dm['kind'] == 'linear' and dm.get('side') == 'inside'
    if (dm['kind'] not in ('hole', 'diameter', 'leader') and not inside) or not (G.get('fcfs') or G.get('datums')): return [], []
    ids = {x.upper() for x in (dm.get('group') or [])} | ({dm['feature'].upper()} if dm.get('feature') else set())
    if not ids: return [], []
    F = spec['features']
    fcs = [f for f in G.get('fcfs', []) if gdt.parse_face(f['target'], F) is None
           and ({x.upper() for x in (f.get('group') or [f['target']])} & ids)]
    dts = [d_['letter'] for d_ in G.get('datums', []) if gdt.parse_face(d_['target'], F) is None and str(d_['target']).upper() in ids]
    return fcs, dts


def callout_rows(spec, dm):
    rows = []
    t = callout_text(spec, dm)
    if t:
        rows += [{'t': 'text', 's': ln, 'h': TXT * 1.7, 'w': text_w(ln)} for ln in t.split('\n')]
    rows += gdt_rows(spec, dm)
    return rows or [{'t': 'text', 's': '', 'h': TXT * 1.7, 'w': 1.0}]


def gdt_rows(spec, dm):
    rows = []
    fcs, dts = gdt_attach(spec, dm)
    rows += [{'t': 'fcf', 'f': f, 'h': gdt.HF + 0.6, 'w': gdt.fcf_width(spec, f, fmt, text_w)} for f in fcs]
    rows += [{'t': 'datum', 'l': L, 'h': 7.8, 'w': gdt.DBOX} for L in dts]
    return rows


def draw_gdt_rows(msp, spec, rows, x0, y0, k, align, prev='text'):
    """Frames/datum symbols stacked below a text line whose middle is at y0. align: 'left' (x0 = left edge),
    'right' (x0 = right edge) or 'center'."""
    from ezdxf.enums import TextEntityAlignment as TA
    allr = [{'t': prev, 'h': TXT * 1.7}] + rows
    offs = row_offsets(allr)
    for i, row in enumerate(rows, start=1):
        y = y0 - offs[i] * k
        w = row['w'] * k
        xl = x0 if align == 'left' else x0 - w if align == 'right' else x0 - w / 2
        if row['t'] == 'fcf':
            gdt.draw_fcf(msp, spec, row['f'], xl, y, k, fmt, text_w, add_text, TA)
        else:
            cx = xl + gdt.DBOX / 2 * k
            top = y + row['h'] / 2 * k
            if allr[i-1]['t'] == 'fcf': top += 0.3 * k
            else: msp.add_line((cx - 3 * k, top), (cx + 3 * k, top), dxfattribs={'layer': 'GDT'})
            gdt.draw_datum(msp, row['l'], (cx, top), (0, -1), k, add_text, TA, stem=0.3)


def row_offsets(rows):
    offs = [0.0]
    for i in range(1, len(rows)): offs.append(offs[-1] + (rows[i-1]['h'] + rows[i]['h']) / 2)
    return offs


def face_items(spec):
    """face key -> {'target', 'items': [('datum', letter) | ('fcf', fcf)]} for GD&T on planar faces."""
    G = spec.get('gdt') or {}; F = spec['features']; out = {}
    for d_ in G.get('datums', []):
        fk = gdt.face_key(d_['target'], F)
        if fk: out.setdefault(fk, {'target': d_['target'], 'items': []})['items'].append(('datum', d_['letter']))
    for f in G.get('fcfs', []):
        fk = gdt.face_key(f['target'], F)
        if fk: out.setdefault(fk, {'target': f['target'], 'items': []})['items'].append(('fcf', f))
    return out


def gdt_notes(spec):
    G = spec.get('gdt') or {}
    if not (G.get('datums') or G.get('fcfs')): return []
    n = ['DIMENSIONING AND TOLERANCING PER ASME Y14.5-2018']
    br = basic_refs(spec)
    if any(dm['kind'] == 'linear' and (dm.get('basic') or any(ref_is_feature(r_) and str(r_).upper() in br for r_ in (dm['a'], dm['b'])))
           for dm in spec['dims']):
        n.append('BOXED DIMENSIONS ARE BASIC')
    return n


def basic_refs(spec):
    """Feature ids whose location dims are basic (holes under a position tolerance, and their bolt circles)."""
    G = spec.get('gdt') or {}; ids = set()
    for f in G.get('fcfs', []):
        if f['type'] == 'position': ids |= {x.upper() for x in (f.get('group') or [f['target']])}
    for bc in spec['features'].get('bolt_circles', []):
        if set(bc['holes']) & ids: ids.add(bc['id'])
    return ids


def gdt_prepare(spec, G_):
    """Before layout: leader-only callouts for GD&T on features that have no callout, and the view/side for face GD&T."""
    import geom
    spec['dims'] = [dm for dm in spec['dims'] if not dm.get('_gdt')]
    G = spec.get('gdt') or {}; F = spec['features']; warns = []
    circle_view = {'z': 'top', 'y': 'front', 'x': 'right'}
    vids = {v['id'] for v in spec['views']}
    letters = {d_['letter'] for d_ in G.get('datums', [])}
    for f in G.get('fcfs', []):
        miss = [L for L in f.get('datums', []) if L not in letters]
        if miss: warns.append(f"{f['id']}: datum(s) {','.join(miss)} referenced but not defined (add-datum or accept-gdt)")
    targets = [d_['target'] for d_ in G.get('datums', [])] + [f['target'] for f in G.get('fcfs', [])]
    n = 0
    for t in dict.fromkeys(targets):
        if gdt.parse_face(t, F): continue
        f = gdt.find_feature(spec, t)
        if not f: warns.append(f'GD&T target {t} not found'); continue
        if any(dm['kind'] == 'linear' and dm.get('side') == 'inside' and dm.get('feature') == f['id'] for dm in spec['dims']):
            continue
        if any(gdt_attach(spec, dm)[0] or gdt_attach(spec, dm)[1] for dm in spec['dims']
               if dm['kind'] in ('hole', 'diameter', 'leader') and (f['id'] in (dm.get('group') or []) or dm.get('feature') == f['id'])):
            continue
        ax = f.get('axis') or f.get('edge_axis')
        vn = circle_view.get(ax)
        if vn not in vids: warns.append(f'GD&T on {t}: view {vn} missing'); continue
        n += 1
        spec['dims'].append({'kind': 'leader', 'view': vn, 'feature': f['id'], 'text': '', 'dist': 6, 'id': f'GD{n}', '_gdt': True})
    place = {}
    order = ['front', 'right', 'top']
    for fk, it in face_items(spec).items():
        ax, lev, sg = gdt.parse_face(it['target'], F)
        best = None
        for vid in order:
            if vid not in G_ or vid not in vids: continue
            ua, va = geom.VIEW_AXES[vid]
            if ax not in (ua, va): continue
            ci = 0 if ax == ua else 1
            segs = find_lines(G_[vid], 'visible', ci, lev)
            if not segs: continue
            seg = max(segs, key=lambda p: math.dist(*p['p']))['p']
            sd = ('right' if sg > 0 else 'left') if ax == ua else ('above' if sg > 0 else 'below')
            busy = sum(1 for dm in spec['dims'] if dm['view'] == vid and dm['kind'] in ('hole', 'diameter', 'leader')
                       and callout_side(spec, dm) == sd)
            score = (sd in stacked_sides(spec, vid), busy, order.index(vid))
            if best is None or score < best[0]: best = (score, vid, sd, seg)
        if best: place[fk] = (best[1], best[2], [list(best[3][0]), list(best[3][1])])
        else: warns.append(f"GD&T on {it['target']}: face not seen edge-on in any view")
    spec['_gdt_place'] = place
    spec['gdt_warnings'] = warns


def draw_gdt_faces(msp, spec, vinfo, s, k):
    from ezdxf.enums import TextEntityAlignment as TA
    items = face_items(spec)
    for fk, (vid, sd, seg) in spec.get('_gdt_place', {}).items():
        if vid not in vinfo or fk not in items: continue
        vi = vinfo[vid]; T = vi['T']; bb = vi['b']
        A, B = T(*seg[0]), T(*seg[1])
        dirv = {'right': (1, 0), 'left': (-1, 0), 'above': (0, 1), 'below': (0, -1)}[sd]
        D = margins(spec, vid, no_gdt=True)[sd] + 1
        LL, UR = T(bb[0], bb[1]), T(bb[2], bb[3])
        edge = {'right': UR[0], 'left': LL[0], 'above': UR[1], 'below': LL[1]}[sd]
        its = sorted(items[fk]['items'], key=lambda t: t[0] != 'datum')
        n = len(its)
        for i, it in enumerate(its):
            t = 0.22 + 0.56 * (i / (n - 1) if n > 1 else 0)
            base = (A[0] + (B[0]-A[0]) * t, A[1] + (B[1]-A[1]) * t)
            far = edge + (dirv[0] + dirv[1]) * D * k          # coordinate of the outer line along dirv
            dist = abs(far - (base[0] if dirv[0] else base[1]))
            if it[0] == 'datum':
                gdt.draw_datum(msp, it[1], base, dirv, k, add_text, TA, stem=max(0.5, dist / k - 2.2))
            else:
                Q = (far, base[1]) if dirv[0] else (base[0], far)
                att = {'layer': 'GDT'}
                msp.add_line(base, Q, dxfattribs=att)
                ah = 2.5 * k; ang = math.atan2(dirv[1], dirv[0])
                msp.add_solid([base, (base[0] + ah*math.cos(ang - 0.3), base[1] + ah*math.sin(ang - 0.3)),
                               (base[0] + ah*math.cos(ang + 0.3), base[1] + ah*math.sin(ang + 0.3))], dxfattribs=att)
                f = it[1]
                if sd == 'right': gdt.draw_fcf(msp, spec, f, Q[0], Q[1], k, fmt, text_w, add_text, TA)
                elif sd == 'left': gdt.draw_fcf(msp, spec, f, Q[0], Q[1], k, fmt, text_w, add_text, TA, right_align=True)
                elif sd == 'below': gdt.draw_fcf(msp, spec, f, Q[0] - gdt.HF/2*k, Q[1] - gdt.HF/2*k, k, fmt, text_w, add_text, TA)
                else: gdt.draw_fcf(msp, spec, f, Q[0] - gdt.HF/2*k, Q[1] + gdt.HF/2*k, k, fmt, text_w, add_text, TA)


def box_dim_text(msp, dim, k):
    """Basic dimension: draw a frame around the dimension's text (ezdxf does not render DIMGAP<0 boxes)."""
    blk = dim.dimension.get_geometry_block()
    if blk is None: return
    for e in blk:
        if e.dxftype() != 'MTEXT': continue
        from ezdxf.tools.text_size import estimate_mtext_extents
        w, h = estimate_mtext_extents(e)
        ap = e.dxf.get('attachment_point', 5); col = (ap - 1) % 3; row = (ap - 1) // 3
        x0 = -w * (0, 0.5, 1)[col]; y0 = -h * (1, 0.5, 0)[row]
        pad = 0.9 * k
        rot = math.radians(e.get_rotation())
        ix, iy = e.dxf.insert[0], e.dxf.insert[1]
        pts = []
        pv = 0.55 * k
        for (px, py) in ((x0 - pad, y0 - pv), (x0 + w + pad, y0 - pv), (x0 + w + pad, y0 + h + pv), (x0 - pad, y0 + h + pv)):
            pts.append((ix + px*math.cos(rot) - py*math.sin(rot), iy + px*math.sin(rot) + py*math.cos(rot)))
        msp.add_lwpolyline(pts, close=True, dxfattribs={'layer': 'DIMENSIONS'})


def margins(spec, vid, no_gdt=False):
    m = {'left': 4.0, 'right': 4.0, 'above': 4.0, 'below': 4.0}
    for dm in spec['dims']:
        if dm['view'] != vid: continue
        if dm['kind'] == 'linear' and dm.get('side') == 'inside':
            sd = inside_text_side(spec, dm)
            t = (dm.get('text') or '<>').replace('<>', '0' * (spec['decimals'] + 4))
            gr = gdt_rows(spec, dm)
            if sd in ('left', 'right'): need = 6 + max([text_w(t)] + [r_['w'] for r_ in gr]) + 3
            else: need = 6 + TXT + sum(r_['h'] for r_ in gr) + 3
            m[sd] = max(m[sd], need)
        elif dm['kind'] == 'linear':
            m[dm['side']] = max(m[dm['side']], dm.get('offset', DIM_FIRST) + TXT + 3)
        elif dm['kind'] in ('hole', 'diameter', 'leader'):
            sd = callout_side(spec, dm)
            base = dm.get('dist', 6) + (dm.get('offset', DIM_FIRST) + DIM_STEP if sd in stacked_sides(spec, vid) else 0)
            rows = callout_rows(spec, dm)
            need = base + (3 + 1 + max(r_['w'] for r_ in rows) if sd in ('left', 'right') else sum(r_['h'] for r_ in rows) + 2)
            m[sd] = max(m[sd], need + 2)
    if spec.get('kind') in ('assembly', 'exploded') and any(b_.get('view') == vid and b_.get('anchor') for b_ in spec.get('balloons', [])):
        extra_ = max([b_.get('dist', 0) for b_ in spec['balloons'] if b_.get('view') == vid] + [0])
        for sd_ in m: m[sd_] = max(m[sd_], BALLOON_OFF + 2 * BALLOON_R + 2 + extra_)
    if not no_gdt:
        base = margins(spec, vid, no_gdt=True)
        for fk, (pv, sd, seg) in spec.get('_gdt_place', {}).items():
            if pv != vid: continue
            for it in face_items(spec).get(fk, {}).get('items', []):
                if it[0] == 'datum': size = gdt.DBOX + 2.4
                else: size = (gdt.fcf_width(spec, it[1], fmt, text_w) if sd in ('left', 'right') else gdt.HF)
                m[sd] = max(m[sd], base[sd] + 1 + size + 2)
    v = next(x for x in spec['views'] if x['id'] == vid)
    if v['type'] in ('section', 'detail') or (v['type'] == 'iso' and (spec.get('_iso_f', 1) != 1 or spec.get('kind') == 'exploded')): m['below'] += 7.5
    return m


def layout(spec, gb, s, area=None):
    """Return dict vid -> (x0, y0) paper position of geometry bbox lower-left and total (w, h)."""
    third = spec['projection'] == 'third'
    ids = {v['id'] for v in spec['views']}
    fac = {v['id']: (v.get('factor', 1) if v['type'] == 'detail' else spec.get('_iso_f', 1) if v['type'] == 'iso' else 1)
           for v in spec['views']}
    size = {vid: ((gb[vid][2]-gb[vid][0])*s*fac[vid], (gb[vid][3]-gb[vid][1])*s*fac[vid]) for vid in gb}
    M = {vid: margins(spec, vid) for vid in gb}
    # grid cells: (col,row)
    cells = {'front': (0, 0), 'top': (0, 1), 'right': (1, 0), 'iso': (1, 1)} if third else \
            {'front': (1, 1), 'top': (1, 0), 'right': (0, 1), 'iso': (0, 0)}
    cols, rows = {}, {}
    for vid, (c, r) in cells.items():
        cols.setdefault(c, [0.0, 0.0, 0.0]); rows.setdefault(r, [0.0, 0.0, 0.0])
        if vid not in ids or vid == 'iso': continue
        w, h = size[vid]; m = M[vid]
        cols[c] = [max(cols[c][0], m['left']), max(cols[c][1], w), max(cols[c][2], m['right'])]
        rows[r] = [max(rows[r][0], m['below']), max(rows[r][1], h), max(rows[r][2], m['above'])]
    if 'iso' in ids:   # iso uses its whole cell (incl. neighbours' dim margins); grow the cell only if needed
        c, r = cells['iso']; m = M['iso']; w, h = size['iso']
        if spec.get('_brk') and (fac['iso'] != 1 or spec.get('kind') == 'exploded'):   # label under a slim iso: room for it
            w = max(w, _wlen('ISOMETRIC VIEW  SCALE 1:100') * 3.0 * 0.62)
        exw = m['left'] + w + m['right'] - sum(cols[c]); exh = m['below'] + h + m['above'] - sum(rows[r])
        if exw > 0: cols[c][2 if third else 0] += exw
        if exh > 0: rows[r][2 if third else 0] += exh
    gap = 6.0
    colx, x = {}, 0.0
    for c in sorted(cols):
        if sum(cols[c]) == 0: colx[c] = x; continue
        colx[c] = x; x += sum(cols[c]) + gap
    rowy, y = {}, 0.0
    for r in sorted(rows):
        if sum(rows[r]) == 0: rowy[r] = y; continue
        rowy[r] = y; y += sum(rows[r]) + gap
    pos = {}
    for vid, (c, r) in cells.items():
        if vid not in ids: continue
        Lm, G, Rm = cols[c]; B, G2, T = rows[r]
        w, h = size[vid]
        if vid == 'iso':
            m = M['iso']
            pos[vid] = (colx[c] + m['left'] + (sum(cols[c]) - m['left'] - m['right'] - w)/2,
                        rowy[r] + m['below'] + (sum(rows[r]) - m['below'] - m['above'] - h)/2)
            continue
        if vid == 'right':
            px = colx[c] + Lm if third else colx[c] + Lm + (G-w)
        else:
            px = colx[c] + Lm + (G-w)/2
        if vid == 'top':
            py = rowy[r] + B if third else rowy[r] + B + (G2-h)
        else:
            py = rowy[r] + B + (G2-h)/2
        pos[vid] = (px, py)
    W = max(x - gap, 0); H = max(y - gap, 0)
    # extra views (sections/details): to the right in a column, or below in a row - whichever suits the sheet
    extras = [v['id'] for v in spec['views'] if v['type'] in ('section', 'detail')]
    if extras and area:
        rw = sum(M[e]['left'] + size[e][0] + M[e]['right'] for e in extras) + gap*(len(extras)-1)
        rh = max(M[e]['below'] + size[e][1] + M[e]['above'] for e in extras)
        ew = max(M[e]['left'] + size[e][0] + M[e]['right'] for e in extras)
        eh = sum(M[e]['below'] + size[e][1] + M[e]['above'] for e in extras) + gap*(len(extras)-1)
        right_ratio = max((W + gap*2 + ew)/area[0], max(H, eh)/area[1])
        below_ratio = max(max(W, rw)/area[0], (H + gap*2 + rh)/area[1])
        if below_ratio < right_ratio:
            for vid in list(pos): pos[vid] = (pos[vid][0], pos[vid][1] + rh + gap*2)
            xx = 0.0
            for e in extras:
                m = M[e]
                pos[e] = (xx + m['left'], m['below'] + (rh - m['below'] - m['above'] - size[e][1])/2)
                xx += m['left'] + size[e][0] + m['right'] + gap
            return pos, (max(W, rw), H + gap*2 + rh)
    if extras:
        ew = max(M[e]['left'] + size[e][0] + M[e]['right'] for e in extras)
        eh = sum(M[e]['below'] + size[e][1] + M[e]['above'] for e in extras) + gap*(len(extras)-1)
        ex0 = W + (gap*2 if W else 0)
        Htot = max(H, eh)
        yy = Htot
        for e in extras:
            m = M[e]
            yy -= m['above'] + size[e][1]
            pos[e] = (ex0 + m['left'] + (ew - m['left']-m['right']-size[e][0])/2, yy)
            yy -= m['below'] + gap
        if H < Htot:
            for vid in cells:
                if vid in pos: pos[vid] = (pos[vid][0], pos[vid][1] + (Htot-H)/2)
        W, H = ex0 + ew, Htot
    return pos, (W, H)


BALLOON_R, BALLOON_OFF = 4.5, 9.0     # paper mm: balloon radius, gap from the view's outline box
REV_COLS = [('REV', 12), ('DESCRIPTION', 70), ('DATE', 24), ('APPROVED', 24)]
REV_ROW = 6.0
BOM_COLS = [('ITEM', 12), ('PART NO.', 38), ('DESCRIPTION', 62), ('MATERIAL', 42), ('QTY', 16)]
BOM_ROW = 6.0


def revisions(spec):
    """Revision rows; created from the title REV the first time a drawing is rendered."""
    if spec.get('revisions') is None:
        t = spec['title']
        spec['revisions'] = [{'rev': t.get('revision') or '-', 'desc': 'INITIAL RELEASE',
                              'date': t.get('date') or datetime.date.today().isoformat(), 'by': t.get('drawn_by', '')}]
    return spec['revisions']


def reserved_rects(spec, sheet):
    """Paper rectangles (x0, y0, x1, y1) that views must not cover: revision table (top right), BOM (above title block)."""
    PW, PH = SHEETS[sheet]
    out = []
    n = len(revisions(spec))
    rw = sum(w for _, w in REV_COLS)
    out.append((PW - BORDER - rw, PH - BORDER - REV_ROW * (2 + n), PW - BORDER, PH - BORDER))
    if spec.get('kind') == 'assembly':
        nb = len(spec.get('bom', []))
        out.append((PW - BORDER - TB_W, BORDER + TB_H, PW - BORDER, BORDER + TB_H + BOM_ROW * (nb + 1)))
    return out


def view_rects(spec, gb, s, pos):
    fac = {v['id']: (v.get('factor', 1) if v['type'] == 'detail' else spec.get('_iso_f', 1) if v['type'] == 'iso' else 1)
           for v in spec['views']}
    out = []
    for vid, (px, py) in pos.items():
        w = (gb[vid][2]-gb[vid][0])*s*fac[vid]; h = (gb[vid][3]-gb[vid][1])*s*fac[vid]
        m = margins(spec, vid)
        out.append((px - m['left'], py - m['below'], px + w + m['right'], py + h + m['above']))
    return out


def fit_offset(spec, sheet, rects, W, H):
    """Paper origin (ox, oy) for the view layout: centred if it clears the reserved tables, else the nearest spot that does."""
    aw, ah = view_area(sheet)
    ax0, ay0 = BORDER + 2, BORDER + TB_H + 6
    res = reserved_rects(spec, sheet)
    def ok(ox, oy):
        for (a0, b0, a1, b1) in rects:
            for (r0, s0, r1, s1) in res:
                if ox + a0 < r1 + 2 and ox + a1 > r0 - 2 and oy + b0 < s1 + 2 and oy + b1 > s0 - 2: return False
        return True
    cx, cy = ax0 + (aw - W) / 2, ay0 + (ah - H) / 2
    if ok(cx, cy): return cx, cy
    cands = []
    N = 12
    for i in range(N + 1):
        for j in range(N + 1):
            ox = ax0 + (aw - W) * i / N; oy = ay0 + (ah - H) * j / N
            cands.append((math.hypot(ox - cx, oy - cy), ox, oy))
    for _, ox, oy in sorted(cands):
        if ok(ox, oy): return ox, oy
    return None


def view_area(sheet):
    w, h = SHEETS[sheet]
    return (w - 2*BORDER - 4, h - 2*BORDER - TB_H - 8)


def choose(spec, gb):
    fam = spec.get('sheet_family') or ('ANSI' if spec['units'] == 'in' else 'ISO')
    sheets = [spec['sheet']] if spec['sheet'] not in (None, '', 'auto') else FAMILY[fam]
    if spec.get('_brk') and len(sheets) > 1: sheets = sheets + [BIG_SHEET[fam]]     # long parts may need one size up
    fixed = parse_scale(spec['scale'])
    res = []
    for sh in sheets:
        aw, ah = view_area(sh)
        has_iso = any(v['type'] == 'iso' for v in spec['views'])
        found = False
        for s in ([fixed] if fixed else SCALES):
            fis = {'assembly': [2, 1, 0.5, 0.25], 'exploded': [1]}.get(spec.get('kind'), [1, 0.5, 0.25])
            for fi in (fis if has_iso else [1]):
                spec['_iso_f'] = fi
                pos_, (W, H) = layout(spec, gb, s, (aw, ah))
                if W <= aw and H <= ah and fit_offset(spec, sh, view_rects(spec, gb, s, pos_), W, H):
                    res.append((sh, s, True, fi)); found = True; break
            if found and spec.get('kind') == 'assembly' and has_iso and res[-1][3] < 1:
                # assembly: the iso is the main view - accept a smaller ortho scale if it buys a full-size iso
                for s2 in [x for x in SCALES if x < s][:3]:
                    for fi in fis:
                        if s2 * fi <= s * res[-1][3]: break
                        spec['_iso_f'] = fi
                        pos_, (W, H) = layout(spec, gb, s2, (aw, ah))
                        if W <= aw and H <= ah and fit_offset(spec, sh, view_rects(spec, gb, s2, pos_), W, H):
                            res[-1] = (sh, s2, True, fi); break
                    if res[-1][3] >= 1: break
            if found: break
        if not found and fixed:
            res.append((sh, fixed, False, 0.5 if has_iso else 1))
    if not res:
        return sheets[-1], SCALES[-1], False, 1
    if fixed or len(sheets) == 1:
        return res[0]
    if spec.get('_brk'):   # broken view: one size up only when the usual sheets would print it really small
        fam_ = spec.get('sheet_family') or ('ANSI' if spec['units'] == 'in' else 'ISO')
        ok = [r for r in res if r[2] and r[0] != BIG_SHEET[fam_]]
        big = next((r for r in res if r[2] and r[0] == BIG_SHEET[fam_]), None)
        pick = next((r for r in ok if r[1] >= 1), None) or (max(ok, key=lambda r: r[1]) if ok else None)
        if pick and pick[1] >= res[0][1] / 2 and ok and pick is not ok[0] and ok[0][1] >= 0.5: pick = ok[0]
        if big and (pick is None or (pick[1] < 0.2 and big[1] >= 2 * pick[1])): return big
        if pick: return pick
    for r in res:          # smallest sheet that holds the part at full size or larger
        if r[1] >= 1: return r
    if len(res) > 1 and res[0][1] >= res[-1][1] / 2 and res[0][1] >= 0.5:
        return res[0]
    return res[-1]


# ------------------------------------------------------------------ DXF writing
def new_doc(spec, k):
    import ezdxf
    doc = ezdxf.new('R2013', setup=False, units=1 if spec['units'] == 'in' else 4)
    # linetypes defined in paper mm * k (drawing units), so LTSCALE stays 1 at any drawing scale
    doc.linetypes.add('HIDDEN', pattern=[4.5*k, 3.0*k, -1.5*k], description='Hidden __ __ __ __')
    doc.linetypes.add('CENTER', pattern=[18.0*k, 12.0*k, -1.5*k, 3.0*k, -1.5*k], description='Center ____ _ ____ _')
    doc.header['$LTSCALE'] = 1.0
    doc.header['$MEASUREMENT'] = 0 if spec['units'] == 'in' else 1
    L = doc.layers
    for name, color, lt, lw in [('VISIBLE', 7, 'CONTINUOUS', 50), ('HIDDEN', 8, 'HIDDEN', 25), ('DIMENSIONS', 7, 'CONTINUOUS', 18),
                                ('CENTER', 7, 'CENTER', 18), ('TITLEBLOCK', 7, 'CONTINUOUS', 25), ('NOTES', 7, 'CONTINUOUS', 18),
                                ('HATCH', 7, 'CONTINUOUS', 13), ('GDT', 7, 'CONTINUOUS', 18)]:
        if name not in L: L.add(name, color=color, linetype=lt, lineweight=lw)
    doc.styles.add('EPURE', font='arial.ttf')
    if font_info().get('fallback'): doc.styles.add('EPURE_CJK', font=font_info()['fallback'])
    return doc


def render(spec, d, cache=None, ids_overlay=True):
    """Draw the sheet. A broken view (long parts) works on render-time copies of the views and dims; the saved spec keeps
    the user's own views and dims, plus spec['resolved']['break'] describing what was shortened."""
    views0, dims0 = spec['views'], spec['dims']
    spec['views'] = [dict(v) for v in views0]; spec['dims'] = [copy.deepcopy(dm) for dm in dims0]
    try:
        return _render(spec, d, cache, ids_overlay)
    finally:
        spec['views'], spec['dims'] = views0, dims0
        spec.pop('_brk', None)
        for dm in spec['dims']: dm.pop('_tag', None); dm.pop('_pos', None)
        save_spec(d, spec)


BIG_SHEET = {'ANSI': 'ANSI_C', 'ISO': 'A2'}


def break_setup(spec, G, gb):
    """Plan a broken view; clip/map the long views; hide dims that sit in the cut, add the hole-pattern dim, restack.
    Returns {view id: ViewMap} for the views that are shortened (empty when the drawing stays whole)."""
    import breaks as BK, geom
    spec.pop('_brk', None)
    brk = BK.plan(spec, G)
    if not brk: return {}
    VM = {}
    for v in spec['views']:
        if v['type'] not in MAIN: continue          # sections / details stay whole
        vm = BK.ViewMap(brk, v['type'])
        if not vm.on: continue
        cuts = []
        g = G[v['id']]
        gc = dict(g); gc['visible'] = vm.clip(g['visible'], cuts); gc['hidden'] = vm.clip(g['hidden'], cuts)
        vm.g2, vm.cuts = gc, cuts
        gb[v['id']] = vm.mapped_bbox(gc['visible'] + gc['hidden'])
        VM[v['id']] = vm
    if not VM: return {}
    ai = 'xyz'.index(brk['axis'])
    allf = {f['id']: f for f in all_feats(spec)}
    def f_in_band(fid):
        f = allf.get(fid)
        if not f: return False
        pts = [f['c1'], f['c2']] if 'c1' in f else [f['apt']] if 'apt' in f else [f['center']]
        if f.get('axis') == brk['axis'] and 'lo' in f: return BK.in_band(brk, f['lo']) and BK.in_band(brk, f['hi'])
        return all(BK.in_band(brk, p_[ai]) for p_ in pts)
    keep, hidden = [], []
    for dm in spec['dims']:
        vm = VM.get(dm['view']); hide = False
        if vm:
            if dm['kind'] == 'linear' and dm.get('axis') == brk['axis']:
                try: hide = any(BK.in_band(brk, ref_value(spec, dm[r_], brk['axis'])) for r_ in ('a', 'b'))
                except Exception: hide = False
            elif dm['kind'] in ('hole', 'diameter', 'leader') and dm.get('feature'):
                hide = all(f_in_band(x) for x in (dm.get('group') or [dm['feature']]))
            elif dm['kind'] == 'angular': hide = vm.inband(*dm['E'])
            elif dm['kind'] == 'text': hide = vm.inband(*dm['point'])
        if hide: hidden.append(dm['id'])
        else: keep.append(dm)
    pat = brk.get('pattern')
    if pat and spec.get('kind') not in ('assembly', 'exploded'):
        hv = [dm for dm in spec['dims'] if dm['id'] in hidden and dm['kind'] == 'linear']
        vid = max(set(dm['view'] for dm in hv), key=lambda x: sum(1 for dm in hv if dm['view'] == x)) if hv else next(iter(VM))
        sides = [dm.get('side') for dm in hv if dm['view'] == vid]
        sd0 = max(set(sides), key=sides.count) if sides else 'left'
        side = {'left': 'right', 'right': 'left', 'above': 'below', 'below': 'above'}.get(sd0, 'right')
        n = len(pat['ids'])
        keep.append({'id': 'DP1', 'kind': 'linear', 'view': vid, 'axis': brk['axis'], 'a': pat['ids'][0], 'b': pat['ids'][-1],
                     'side': side, 'offset': DIM_FIRST, 'auto': True, '_break': True,
                     'text': f"{n - 1}X {fmt(spec, pat['pitch'])} (=<>)"})
    for vid in VM:          # restack what is left on each side of the shortened views
        for sd_ in ('left', 'right', 'above', 'below'):
            ds = sorted([dm for dm in keep if dm['view'] == vid and dm['kind'] == 'linear' and dm.get('side') == sd_],
                        key=lambda dm: dm.get('offset', DIM_FIRST))
            for i, dm in enumerate(ds): dm['offset'] = DIM_FIRST + i * DIM_STEP
    spec['dims'] = keep
    brk['views'] = sorted(VM); brk['hidden_dims'] = hidden
    spec['_brk'] = brk
    return VM


def end_view_detail(spec, G, gb, VM, s, d=None, cache=None):
    """Parts: the cross-section (end) view must read. Add it if missing; if it is still small on paper, add a scaled-up
    detail of it (render-time only) and move its dims there. Returns True when views changed."""
    import geom
    brk = spec.get('_brk')
    if not brk or spec.get('kind') in ('assembly', 'exploded'): return False
    et = {'x': 'right', 'y': 'front', 'z': 'top'}[brk['axis']]
    ev = next((v for v in spec['views'] if v['type'] == et), None)
    changed = False
    if ev is None:
        ev = {'id': et, 'type': et, '_auto': True}; spec['views'].append(ev); changed = True
        G[et] = view_geom(spec, d, ev, cache if cache is not None else {}); gb[et] = G[et]['bbox']
    b = gb.get(ev['id'])
    if not b: return changed
    ext = max(b[2] - b[0], b[3] - b[1])
    if ext * s >= 30 or any(v['type'] == 'detail' and v.get('parent') == ev['id'] for v in spec['views']): return changed
    fac = next((f for f in (2, 2.5, 4, 5, 8, 10, 20, 25, 50) if ext * s * f >= 45), 50)
    lab = next(c for c in 'ABCDEFGHJKLMNPRSTUVWXYZ' if c not in [v['id'] for v in spec['views']])
    dv = {'id': lab, 'type': 'detail', 'parent': ev['id'], 'center': [(b[0] + b[2]) / 2, (b[1] + b[3]) / 2],
          'radius': 0.5 * math.hypot(b[2] - b[0], b[3] - b[1]) * 1.12, 'factor': fac, '_auto': True}
    spec['views'].append(dv)
    G[lab] = detail_geom(G[ev['id']], dv); gb[lab] = G[lab]['bbox']
    for dm in spec['dims']:
        if dm['view'] == ev['id']: dm['view'] = lab
    brk['end_detail'] = {'view': lab, 'parent': ev['id'], 'factor': fac}
    return True


def _render(spec, d, cache=None, ids_overlay=True):
    import ezdxf, geom
    from ezdxf.enums import TextEntityAlignment
    if cache is None:
        cp = os.path.join(d, 'geom_cache.json')
        cache = json.load(open(cp)) if os.path.exists(cp) else {}
    G = {v['id']: view_geom(spec, d, v, cache) for v in spec['views']}
    gb = {k: g['bbox'] for k, g in G.items()}
    if spec.get('kind') == 'assembly': refresh_bom(spec)
    if spec.get('kind') == 'exploded': sync_exploded(spec, d)
    gdt_prepare(spec, G)
    VM = break_setup(spec, G, gb)
    sheet, s, fits, iso_f = choose(spec, gb)
    if VM and end_view_detail(spec, G, gb, VM, s, d, cache):
        for v in spec['views']:
            if v['id'] not in G: G[v['id']] = view_geom(spec, d, v, cache); gb[v['id']] = G[v['id']]['bbox']
        sheet, s, fits, iso_f = choose(spec, gb)
    spec['_iso_f'] = iso_f
    spec['resolved'] = {'sheet': sheet, 'scale': scale_str(s), 'fits': fits, 'iso_scale': scale_str(s*iso_f)}
    if spec.get('_brk'):
        b_ = spec['_brk']; U_ = uf(spec)
        spec['resolved']['break'] = {
            'axis': b_['axis'], 'true_length': round(b_['length'] / U_, 4), 'shown_length': round(b_['shown'] / U_, 4),
            'cut': [[round(a_ / U_, 4), round(c_ / U_, 4)] for a_, c_ in b_['bands']], 'views': b_['views'],
            'hidden_dims': b_['hidden_dims'], 'pattern': ({'holes': b_['pattern']['ids'], 'pitch': round(b_['pattern']['pitch'] / U_, 4)}
                                                          if b_.get('pattern') else None),
            'end_detail': b_.get('end_detail'), 'setting': spec.get('break') or {'on': 'auto'}}
    U = uf(spec)
    k = 1.0 / (s * U)              # paper mm -> drawing units
    pos, (W, H) = layout(spec, gb, s, view_area(sheet))
    PW, PH = SHEETS[sheet]
    aw, ah = view_area(sheet)
    off = fit_offset(spec, sheet, view_rects(spec, gb, s, pos), W, H) if W <= aw and H <= ah else None
    ox, oy = off if off else (BORDER + 2 + (aw - W)/2, BORDER + TB_H + 6 + (ah - H)/2)
    doc = new_doc(spec, k); msp = doc.modelspace()
    ds = doc.dimstyles.new('EPURE')
    pu = 1.0 / U   # paper mm -> drawing units at scale 1
    ds.dxf.dimtxt = TXT*pu; ds.dxf.dimasz = 2.5*pu; ds.dxf.dimexo = 1.0*pu; ds.dxf.dimexe = 1.5*pu
    ds.dxf.dimgap = 0.8*pu; ds.dxf.dimscale = 1.0/s; ds.dxf.dimdec = spec['decimals']; ds.dxf.dimtad = 1
    ds.dxf.dimtih = 0; ds.dxf.dimtoh = 0; ds.dxf.dimtxsty = 'EPURE'; ds.dxf.dimcen = 0; ds.dxf.dimzin = 0
    ds.dxf.dimclrd = 256; ds.dxf.dimclre = 256; ds.dxf.dimclrt = 256; ds.dxf.dimlwd = 18; ds.dxf.dimlwe = 18
    ds.dxf.dimatfit = 3; ds.dxf.dimtmove = 0; ds.dxf.dimdsep = ord('.')
    P = lambda x, y: ((ox + x)*k, (oy + y)*k)   # paper (layout coords) -> drawing

    vinfo = {}
    for v in spec['views']:
        vid = v['id']; g = G[vid]; b = g['bbox']; bm = gb[vid]; vm = VM.get(vid)
        f = v.get('factor', 1) if v['type'] == 'detail' else iso_f if v['type'] == 'iso' else 1
        px, py = pos[vid]
        def Tm(u, vv, px=px, py=py, b=bm, f=f):       # already-mapped (shortened) view coords -> drawing
            return P(px + (u-b[0])*s*f, py + (vv-b[1])*s*f)
        if vm:
            def T(u, vv, Tm=Tm, vm=vm): return Tm(*vm.map(u, vv))
        else:
            T = Tm
        vv_ = dict(v)
        if v['type'] == 'detail':
            vv_['_pframe'] = view_frame_name(next(x for x in spec['views'] if x['id'] == v['parent']))
        vinfo[vid] = dict(T=T, Tm=Tm, vm=vm, b=b, bm=bm, f=f, px=px, py=py, w=(bm[2]-bm[0])*s*f, h=(bm[3]-bm[1])*s*f, g=g, v=vv_)
        gd = vm.g2 if vm else g
        for kind, layer in (('hidden', 'HIDDEN'), ('visible', 'VISIBLE')):
            for p in gd[kind]:
                draw_prim(msp, p, T, layer, s*f*k)
        if vm:              # long-break lines at both edges of every cut
            for pl in vm.break_lines(vm.cuts, s*f):
                msp.add_lwpolyline([Tm(*q) for q in pl], dxfattribs={'layer': 'VISIBLE', 'lineweight': 25})
        if g.get('hatch'):
            h = msp.add_hatch(dxfattribs={'layer': 'HATCH'})
            # ANSI31 line spacing is 0.125 drawing units; want ~2.5 mm paper
            h.set_pattern_fill('ANSI31', scale=(2.5*k)/(3.175 if spec['units'] == 'mm' else 0.125))
            for loop in g['hatch']:
                h.paths.add_polyline_path([T(*q) for q in loop], is_closed=True)
        if v['type'] == 'section':
            add_label(msp, f"SECTION {vid}-{vid}", (px + vinfo[vid]['w']/2, py - 6.5), P, k, 3.5)
        if v['type'] == 'detail':
            add_label(msp, f"DETAIL {vid}  SCALE {scale_str(s*f)}", (px + vinfo[vid]['w']/2, py - 6.5), P, k, 3.5)
            c = T(*v['center']); msp.add_circle(c, v['radius']*s*f*k, dxfattribs={'layer': 'NOTES', 'lineweight': 18})
        if v['type'] == 'iso' and (iso_f != 1 or spec.get('kind') == 'exploded'):
            ring = 0
            if spec.get('kind') in ('assembly', 'exploded') and any(b_.get('view') == vid for b_ in spec.get('balloons', [])):
                ring = BALLOON_OFF + 2 * BALLOON_R + max([b_.get('dist', 0) for b_ in spec['balloons'] if b_.get('view') == vid] + [0])
            add_label(msp, 'EXPLODED ISOMETRIC VIEW' if spec.get('kind') == 'exploded' else f"ISOMETRIC VIEW  SCALE {scale_str(s*f)}", (px + vinfo[vid]['w']/2, py - 6.5 - ring), P, k, 3.0)
    # section cut lines / detail circles on parents
    for v in spec['views']:
        if v['type'] == 'section':
            par = v.get('parent')
            if par in vinfo: draw_cut_line(msp, spec, vinfo[par], v, s, k)
        if v['type'] == 'detail' and v['parent'] in vinfo:
            vi = vinfo[v['parent']]; c = vi['T'](*v['center'])
            msp.add_circle(c, v['radius']*s*vi['f']*k, dxfattribs={'layer': 'NOTES', 'lineweight': 18})
            ang = math.radians(45); rr = v['radius']*s*vi['f']*k + 3*k
            add_text(msp, v['id'], (c[0] + rr*math.cos(ang), c[1] + rr*math.sin(ang)), 3.5*k, 'NOTES', TextEntityAlignment.MIDDLE_CENTER)
    # centerlines
    for v in spec['views']:
        if v['type'] == 'iso': continue
        draw_centers(msp, spec, vinfo[v['id']], s, k)
    # dimensions
    place_callouts(spec, vinfo, s, k)
    errors = []
    for dm in spec['dims']:
        if dm['view'] not in vinfo:
            errors.append(f"{dm['id']}: view {dm['view']} missing"); continue
        try:
            draw_dim(msp, spec, dm, vinfo[dm['view']], s, k)
        except Exception as e:
            errors.append(f"{dm['id']}: {e}")
    draw_gdt_faces(msp, spec, vinfo, s, k)
    if spec.get('kind') == 'exploded':
        T_ = vinfo['iso']['T']
        for a_, b_ in spec.get('trails', []):
            msp.add_line(T_(*a_), T_(*b_), dxfattribs={'layer': 'CENTER', 'linetype': 'HIDDEN'})
    if spec.get('kind') in ('assembly', 'exploded'):
        draw_balloons(msp, spec, vinfo, s, k)
    # frame, title block, notes
    draw_sheet(msp, spec, sheet, s, k, d)
    out = os.path.join(d, 'drawing.dxf')
    doc.saveas(out)
    aud = doc.audit()
    spec['resolved']['audit_errors'] = len(aud.errors)
    spec['resolved']['dim_errors'] = errors
    saved_dims = spec['dims']
    spec['dims'] = [dm for dm in spec['dims'] if not dm.get('_gdt')]; spec.pop('_gdt_place', None)
    save_spec(d, spec)
    spec['dims'] = saved_dims
    write_png(doc, os.path.join(d, 'preview.png'), PW*k, PH*k, PW, PH)
    if ids_overlay:
        for dm in spec['dims']:
            if dm.get('_tag'):
                add_text(msp, dm['id'], dm['_tag'], 2.0*k, 'EPURE_IDS', TextEntityAlignment.MIDDLE_CENTER, color=6)
        for vid, vi in vinfo.items():
            if vi['v']['type'] in ('front', 'top', 'right') or vi['v']['type'] == 'section':
                fv = view_frame_name(vi['v'])
                for coll in ('holes', 'cyls', 'slots'):
                    for f in spec['features'][coll]:
                        circ = {'z': 'top', 'y': 'front', 'x': 'right'}[f['axis']]
                        if fv != circ or vi['v']['type'] == 'section': continue
                        u, vv = geom.project(f['center'], fv)
                        if vi.get('vm') and vi['vm'].inband(u, vv): continue
                        add_text(msp, f['id'], vi['T'](u, vv), 2.0*k, 'EPURE_IDS', TextEntityAlignment.MIDDLE_CENTER, color=1)
            add_text(msp, f'[{vid}]', P(vi['px'], vi['py'] + vi['h'] + 2), 2.2*k, 'EPURE_IDS', TextEntityAlignment.BOTTOM_LEFT, color=5)
        write_png(doc, os.path.join(d, 'preview_ids.png'), PW*k, PH*k, PW, PH)
    spec['dims'] = [dm for dm in spec['dims'] if not dm.get('_gdt')]
    for dm in spec['dims']: dm.pop('_tag', None); dm.pop('_pos', None)
    save_spec(d, spec)
    return spec


def write_png(doc, path, wd, hd, PW, PH, dpi=150):
    import matplotlib; matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from ezdxf.addons.drawing import RenderContext, Frontend
    from ezdxf.addons.drawing.matplotlib import MatplotlibBackend
    from ezdxf.addons.drawing.config import Configuration, BackgroundPolicy, LineweightPolicy, HatchPolicy
    fig = plt.figure(figsize=(PW/25.4, PH/25.4))
    ax = fig.add_axes([0, 0, 1, 1]); ax.set_axis_off()
    cfg = Configuration(background_policy=BackgroundPolicy.WHITE, lineweight_policy=LineweightPolicy.ABSOLUTE, lineweight_scaling=1.6,
                        min_lineweight=0.1)
    ctx = RenderContext(doc)
    Frontend(ctx, MatplotlibBackend(ax), config=cfg).draw_layout(doc.modelspace(), finalize=True)
    ax.set_xlim(0, wd); ax.set_ylim(0, hd); ax.set_aspect('equal')
    fig.set_size_inches(PW/25.4, PH/25.4); ax.set_position([0, 0, 1, 1])
    if path.endswith('.pdf'):
        fig.savefig(path, format='pdf')
    else:
        fig.savefig(path, dpi=dpi, facecolor='white')
    plt.close(fig)


# ------------------------------------------------------------------ fonts: text the main font can't show (CJK...) uses a fallback
FALLBACK_FONTS = ['NotoSansCJK-Regular.ttc', 'NotoSansCJKsc-Regular.otf', 'NotoSansCJKtc-Regular.otf', 'NotoSansCJKjp-Regular.otf',
                  'NotoSansCJKkr-Regular.otf', 'NotoSansCJK-Regular.otf', 'SourceHanSans-Regular.ttc', 'wqy-microhei.ttc',
                  'wqy-zenhei.ttc', 'DroidSansFallbackFull.ttf', 'NotoSansSC-Regular.otf', 'NotoSansTC-Regular.otf']
_FONTS = {}


def font_info():
    """{'primary': codepoints of the main font, 'fallback': font file name, 'fallback_cmap': codepoints} (cached)."""
    if 'primary' in _FONTS: return _FONTS
    _FONTS['primary'] = None
    try:
        from pathlib import Path
        from ezdxf.fonts import fonts
        fm = fonts.font_manager
        fdir = Path(HERE) / 'fonts'
        local = []
        if fdir.is_dir():          # fonts install.sh downloaded next to the code
            fm.scan_folder(fdir)
            local = sorted(p_.name for p_ in fdir.iterdir() if p_.suffix.lower() in ('.ttf', '.otf', '.ttc'))
        def cmap(name):
            try: return set(fm.get_ttf_font(name).getBestCmap())
            except Exception: return set()
        _FONTS['primary'] = cmap(fm.get_font_face('arial.ttf').filename)
        for nm in local + FALLBACK_FONTS:
            if fm.has_font(nm):
                cm = cmap(nm)
                if 0x4E00 in cm: _FONTS['fallback'], _FONTS['fallback_cmap'] = nm, cm; break
    except Exception:
        pass
    return _FONTS


def text_style(text, style='EPURE'):
    if style != 'EPURE' or str(text).isascii(): return style
    fi = font_info(); prim = fi.get('primary')
    if not prim or not fi.get('fallback'): return style
    miss = [c for c in str(text) if ord(c) not in prim and not c.isspace()]
    if miss and all(ord(c) in fi['fallback_cmap'] for c in miss): return 'EPURE_CJK'
    return style


def add_text(msp, text, at, h, layer, align, color=None, style='EPURE'):
    att = {'layer': layer, 'height': h, 'style': text_style(text, style)}
    if color: att['color'] = color
    t = msp.add_text(text, dxfattribs=att); t.set_placement(at, align=align); return t


def add_label(msp, text, at_paper, P, k, h):
    from ezdxf.enums import TextEntityAlignment
    add_text(msp, text, P(*at_paper), h*k, 'NOTES', TextEntityAlignment.MIDDLE_CENTER)


def draw_prim(msp, p, T, layer, sc):
    att = {'layer': layer}
    if p['t'] == 'line':
        msp.add_line(T(*p['p'][0]), T(*p['p'][1]), dxfattribs=att)
    elif p['t'] == 'circle':
        msp.add_circle(T(*p['c']), p['r']*sc, dxfattribs=att)
    elif p['t'] == 'arc':
        msp.add_arc(T(*p['c']), p['r']*sc, p['a0'], p['a1'], dxfattribs=att)
    else:
        msp.add_lwpolyline([T(*q) for q in p['p']], dxfattribs=att)


def draw_cut_line(msp, spec, vi, v, s, k):
    import geom
    from ezdxf.enums import TextEntityAlignment
    fv = view_frame_name(vi['v']); ua, va = geom.VIEW_AXES[fv]
    b = vi['b']; ext = 6/s
    if v['axis'] == ua:
        a, bb = (v['at'], b[1]-ext), (v['at'], b[3]+ext); arrow_dir = None
    elif v['axis'] == va:
        a, bb = (b[0]-ext, v['at']), (b[2]+ext, v['at'])
    else:
        return
    A, B = vi['T'](*a), vi['T'](*bb)
    msp.add_line(A, B, dxfattribs={'layer': 'CENTER', 'lineweight': 35})
    # view direction: the section frame viewer looks along -N; arrows point that way in parent
    N, X, Y = geom.frame(geom.SECTION_FRAME[v['axis']])
    Np, Xp, Yp = geom.frame(fv)
    dvec = (-N[0], -N[1], -N[2])
    du = sum(dvec[i]*Xp[i] for i in range(3)); dv = sum(dvec[i]*Yp[i] for i in range(3))
    L = 8*k
    for E in (A, B):
        tip = (E[0] + du*L, E[1] + dv*L)
        msp.add_line(E, tip, dxfattribs={'layer': 'NOTES', 'lineweight': 35})
        # arrow head
        ang = math.atan2(dv, du); ah = 2.5*k
        p1 = (tip[0] - ah*math.cos(ang-0.35), tip[1] - ah*math.sin(ang-0.35))
        p2 = (tip[0] - ah*math.cos(ang+0.35), tip[1] - ah*math.sin(ang+0.35))
        msp.add_solid([tip, p1, p2], dxfattribs={'layer': 'NOTES'})
        lab = (E[0] + du*L + du*4*k - dv*0, E[1] + dv*L + dv*4*k)
        add_text(msp, v['id'], lab, 3.5*k, 'NOTES', TextEntityAlignment.MIDDLE_CENTER)


def draw_centers(msp, spec, vi, s, k):
    import geom
    v = vi['v']; fv = view_frame_name(v)
    if fv not in geom.VIEW_AXES: return
    ua, va = geom.VIEW_AXES[fv]
    AX = {'x': 0, 'y': 1, 'z': 2}
    att = {'layer': 'CENTER'}
    F = spec['features']
    sect = v['type'] == 'section'
    vm = vi.get('vm'); bax = (spec.get('_brk') or {}).get('axis')
    for f in F['holes'] + F['cyls']:
        u, vv = geom.project(f['center'], fv)
        r = f['dia']/2
        if vm and f['axis'] != bax and vm.inband(u, vv): continue
        if sect:
            # only features the cutting plane passes through
            ai = AX[v['axis']]
            lo_, hi_ = (f['lo'], f['hi']) if f['axis'] == v['axis'] else (f['center'][ai] - r, f['center'][ai] + r)
            if not (lo_ - 1e-6 <= v['at'] <= hi_ + 1e-6): continue
        if v['type'] == 'detail' and math.dist((u, vv), v['center']) > v['radius']: continue
        e = 2.5/(s*vi['f'])
        th = spec.get('threads', {}).get(f['id'])
        if th and th.get('nominal_mm') and f['axis'] not in (ua, va):   # thread major dia: thin 3/4 circle
            msp.add_arc(vi['T'](u, vv), th['nominal_mm']/2*s*vi['f']*k, 0, 270, dxfattribs={'layer': 'VISIBLE', 'lineweight': 18})
        if f['axis'] not in (ua, va):   # circle view: cross
            msp.add_line(vi['T'](u-r-e, vv), vi['T'](u+r+e, vv), dxfattribs=att)
            msp.add_line(vi['T'](u, vv-r-e), vi['T'](u, vv+r+e), dxfattribs=att)
        else:
            lo, hi = f['lo'], f['hi']
            if f['axis'] == ua: msp.add_line(vi['T'](lo-e, vv), vi['T'](hi+e, vv), dxfattribs=att)
            else: msp.add_line(vi['T'](u, lo-e), vi['T'](u, hi+e), dxfattribs=att)
    for bc in F.get('bolt_circles', []):
        if bc['axis'] in (ua, va): continue
        u, vv = geom.project(bc['center'], fv)
        if vm and vm.inband(u, vv): continue
        if v['type'] == 'detail' and math.dist((u, vv), v['center']) > v['radius'] + bc['dia']/2: continue
        if sect and not (bc['lo'] - 1e-6 <= v['at'] <= bc['hi'] + 1e-6) and v['axis'] == bc['axis']: continue
        msp.add_circle(vi['T'](u, vv), bc['dia']/2*s*vi['f']*k, dxfattribs=att)
        e = 3.0/(s*vi['f'])
        msp.add_line(vi['T'](u-e, vv), vi['T'](u+e, vv), dxfattribs=att)
        msp.add_line(vi['T'](u, vv-e), vi['T'](u, vv+e), dxfattribs=att)
    for sl in F['slots']:
        if f_axis_in(sl, ua, va): continue
        if vm and any(vm.inband(*geom.project(c_, fv)) for c_ in (sl['c1'], sl['c2'])): continue
        if v['type'] == 'detail' and math.dist(geom.project(sl['center'], fv), v['center']) > v['radius']: continue
        e = 2.0/(s*vi['f']); r = sl['width']/2
        for c in (sl['c1'], sl['c2']):
            u, vv = geom.project(c, fv)
            msp.add_line(vi['T'](u-r-e, vv), vi['T'](u+r+e, vv), dxfattribs=att)
            msp.add_line(vi['T'](u, vv-r-e), vi['T'](u, vv+r+e), dxfattribs=att)
        (u1, v1), (u2, v2) = geom.project(sl['c1'], fv), geom.project(sl['c2'], fv)
        msp.add_line(vi['T'](u1, v1), vi['T'](u2, v2), dxfattribs=att)


def f_axis_in(f, ua, va): return f['axis'] in (ua, va)


def ext_origin(spec, vi, axis, ref, side, value):
    """2D point (view local mm) for extension line origin."""
    import geom
    fv = view_frame_name(vi['v'])
    ua, va = view_axes(spec, vi['v'])
    ci = 0 if axis == ua else 1
    if ref_is_feature(ref) or (isinstance(ref, str) and ',' in ref) or (isinstance(ref, str) and ref.upper() in ('MIN', 'MAX', 'CENTER')):
        p = geom.project(ref_point(spec, ref), fv) if fv else None
        if p: return list(p)
    # search geometry endpoints with coordinate == value; pick nearest the side
    g = vi['g']; b = vi['b']
    cands = []
    for kind in ('visible', 'hidden'):
        for p in g[kind]:
            pts = []
            if p['t'] in ('line', 'poly'): pts = [p['p'][0], p['p'][-1]]
            elif p['t'] == 'arc': pts = [geom.prim_points(p, 8)[0], geom.prim_points(p, 8)[-1]]
            elif p['t'] == 'circle':
                c, r = p['c'], p['r']; pts = [[c[0]-r, c[1]], [c[0]+r, c[1]], [c[0], c[1]-r], [c[0], c[1]+r]]
            for q in pts:
                if abs(q[ci]-value) < 1e-3: cands.append((kind == 'hidden', q))
    oi = 1 - ci
    if cands:
        key = {'below': lambda q: q[1][oi], 'left': lambda q: q[1][oi], 'above': lambda q: -q[1][oi], 'right': lambda q: -q[1][oi]}[side]
        vis = [c for c in cands if not c[0]] or cands
        return list(min(vis, key=key)[1])
    q = [0, 0]; q[ci] = value
    q[oi] = b[oi] if side in ('below', 'left') else b[oi+2]
    return q


def draw_dim(msp, spec, dm, vi, s, k):
    import geom
    att = {'layer': 'DIMENSIONS'}
    fv = view_frame_name(vi['v'])
    b = vi['b']
    if dm['kind'] == 'linear':
        ua, va = view_axes(spec, vi['v'])
        axis = dm['axis']
        if axis not in (ua, va): raise ValueError(f"axis {axis} not in view plane")
        horiz = axis == ua
        va_, vb_ = ref_value(spec, dm['a'], axis), ref_value(spec, dm['b'], axis)
        side = dm.get('side') or default_side(spec, dm['view'], axis)
        if side == 'inside':
            return draw_inside_dim(msp, spec, dm, vi, s, k, horiz, va_, vb_)
        else:
            o1 = ext_origin(spec, vi, axis, dm['a'], side, va_)
            o2 = ext_origin(spec, vi, axis, dm['b'], side, vb_)
            if vi['v']['type'] == 'detail':
                pass
            P1, P2 = vi['T'](*o1), vi['T'](*o2)
            off = dm.get('offset', DIM_FIRST) * k
            ll = vi['T'](b[0], b[1]); ur = vi['T'](b[2], b[3])
            base = {'below': (P1[0], ll[1]-off), 'above': (P1[0], ur[1]+off), 'left': (ll[0]-off, P1[1]), 'right': (ur[0]+off, P1[1])}[side]
        over = {}
        if vi['f'] != 1: over['dimlfac'] = 1.0/vi['f']
        txt = (dm.get('text') or '<>').replace('%%c', 'Ø')
        if vi.get('vm'):      # shortened view: report the true length, underlined (= not to scale) when it spans a cut
            meas = abs(P2[0] - P1[0]) if horiz else abs(P2[1] - P1[1])
            true_ = abs(vb_ - va_) / uf(spec)
            if meas > 1e-9 and abs(true_ - meas / vi['f']) > 1e-6 * max(1.0, true_):
                over['dimlfac'] = true_ / meas
                if '<>' in txt: txt = txt.replace('<>', '\\L<>\\l')
        if dm.get('tol'): txt = txt + ' ' + dm['tol']
        dim = msp.add_linear_dim(base=base, p1=P1, p2=P2, angle=0 if horiz else 90, dimstyle='EPURE',
                                 text=txt, override=over or None, dxfattribs=att)
        dim.render()
        bref = basic_refs(spec)
        if dm.get('basic') or any(ref_is_feature(r_) and str(r_).upper() in bref for r_ in (dm['a'], dm['b'])):
            box_dim_text(msp, dim, k)
        if horiz: dm['_tag'] = (min(P1[0], P2[0]) - 4*k, base[1])
        else: dm['_tag'] = (base[0], min(P1[1], P2[1]) - 3*k)
    elif dm['kind'] in ('hole', 'diameter', 'leader'):
        f = next((x for x in all_feats(spec) if x['id'] == dm.get('feature')), None)
        if f and f['type'] == 'fillet' and f['r'] * s * vi['f'] < MIN_RADIUS_PAPER:
            spec['resolved'].setdefault('skipped', []).append(
                f"{dm['id']} ({callout_text(spec, dm)}): radius only {f['r']*s*vi['f']:.2f} mm on paper at this scale - unreadable, not drawn")
            return
        draw_callout(msp, spec, dm, vi, s, k)
    elif dm['kind'] == 'angular':
        draw_angular(msp, spec, dm, vi, s, k)
    elif dm['kind'] == 'text':
        from ezdxf.enums import TextEntityAlignment
        u, vv = dm['point']
        add_text(msp, dm['text'], vi['T'](u, vv), TXT*k, 'DIMENSIONS', TextEntityAlignment.MIDDLE_CENTER)
    else:
        raise ValueError(f"unknown dim kind {dm['kind']}")


def draw_angular(msp, spec, dm, vi, s, k):
    """Angle between a slanted edge and a horizontal/vertical edge (or its extension) in the edge-on view."""
    att = {'layer': 'DIMENSIONS'}
    E, O, r = dm['E'], dm['O'], dm['r']
    L = math.dist(E, O)
    d = ((O[0]-E[0])/L, (O[1]-E[1])/L)
    Lp = L * s * vi['f']                        # slope length on paper
    R = max(8.0, min(22.0, 0.6 * Lp))           # arc radius, paper mm
    Rm = R / (s * vi['f'])                       # in model units
    bis = (d[0] + r[0], d[1] + r[1]); bl = math.hypot(*bis); bis = (bis[0]/bl, bis[1]/bl)
    T = vi['T']
    base = T(E[0] + bis[0]*Rm, E[1] + bis[1]*Rm)
    l1 = (T(*E), T(O[0], O[1])); l2 = (T(*E), T(E[0] + r[0]*Rm*1.2, E[1] + r[1]*Rm*1.2))
    txt = dm.get('text') or (f"{dm['value']:.2f}".rstrip('0').rstrip('.') + '°')
    if dm.get('tol'): txt += ' ' + dm['tol']
    if d[0]*r[1] - d[1]*r[0] > 0: l1, l2 = l2, l1     # ezdxf measures counter-clockwise from line1 to line2
    dim = msp.add_angular_dim_2l(base=base, line1=l1, line2=l2, text=txt, dimstyle='EPURE',
                                 override={'dimse1': 1, 'dimse2': 1, 'dimtad': 0, 'dimatfit': 3}, dxfattribs=att)
    dim.render()
    if dm.get('ext'):   # extension of the reference edge out to the arc
        g0 = T(E[0] + r[0]*1.0/(s*vi['f']), E[1] + r[1]*1.0/(s*vi['f']))
        g1 = T(E[0] + r[0]*(Rm + 2.0/(s*vi['f'])), E[1] + r[1]*(Rm + 2.0/(s*vi['f'])))
        msp.add_line(g0, g1, dxfattribs=dict(att, lineweight=18))
    dm['_tag'] = base


def callout_anchor(spec, dm, vi, s, k):
    """Return (C drawing point of feature centre, rr radius in drawing units, side)."""
    import geom
    fv = view_frame_name(vi['v'])
    F = spec['features']
    sd = callout_side(spec, dm)
    if dm.get('feature'):
        allf = all_feats(spec)
        f = next(x for x in allf if x['id'] == dm['feature'])
        pts_of = lambda x: [x['c1'], x['c2']] if 'c1' in x else [x['apt']] if 'apt' in x else [x['center']]
        if dm.get('group'):   # leader starts from the group member nearest the chosen side
            pick = {'right': lambda p: -p[0], 'left': lambda p: p[0], 'above': lambda p: -p[1], 'below': lambda p: p[1]}[sd]
            cands = []
            for x in allf:
                if x['id'] in dm['group']:
                    for c in pts_of(x):
                        if vi.get('vm') and vi['vm'].inband(*geom.project(c, fv)): continue
                        cands.append((pick(geom.project(c, fv)), c, x))
            _, c3, f = min(cands, key=lambda t: t[0])
        else:
            c3 = pts_of(f)[-1]
        r = 0 if 'apt' in f else f.get('width', f.get('dia', 0)) / 2
        u, vv = geom.project(c3, fv) if fv else (0, 0)
    else:
        (u, vv), r = dm['point'], 0
    return vi['T'](u, vv), r * s * vi['f'] * k, sd


def place_callouts(spec, vinfo, s, k):
    """Spread callout texts on each view side so they don't overlap each other or across-dims' texts.
    Sets dm['_pos'] (coordinate along the side: y for left/right, x for above/below)."""
    groups, fixed = {}, {}
    for dm in spec['dims']:
        if dm['view'] not in vinfo: continue
        vi = vinfo[dm['view']]
        if dm['kind'] in ('hole', 'diameter', 'leader'):
            C, rr, sd = callout_anchor(spec, dm, vi, s, k)
            groups.setdefault((dm['view'], sd), []).append((dm, C))
        elif dm['kind'] == 'linear' and dm.get('side') == 'inside':
            ua, va = view_axes(spec, vi['v'])
            sd = inside_text_side(spec, dm)
            if dm['axis'] == ua and sd in ('left', 'right'):
                y = vi['T'](vi['b'][0], dm['at'])[1]
                extra_ = sum(r_['h'] for r_ in gdt_rows(spec, dm)) * k
                fixed.setdefault((dm['view'], sd), []).append((y - (TXT/2 + 1.2)*k - extra_, y + (TXT/2 + 1.2)*k))
            elif dm['axis'] == va and sd in ('above', 'below'):
                x = vi['T'](dm['at'], vi['b'][1])[0]; w = text_w('0' * (spec['decimals'] + 5)) * k
                fixed.setdefault((dm['view'], sd), []).append((x - w/2 - 1.5*k, x + w/2 + 1.5*k))
    for (vid, sd), items in groups.items():
        occ = list(fixed.get((vid, sd), []))
        def free(lo_, hi_):
            return all(hi_ <= a or lo_ >= b for a, b in occ)
        if sd in ('left', 'right'):
            items.sort(key=lambda t: (round(t[1][1], 6), -t[1][0] if sd == 'right' else t[1][0]))
            for dm, C in items:
                rows = callout_rows(spec, dm); offs = row_offsets(rows)
                down = (offs[-1] + rows[-1]['h']/2 + 0.6) * k; up = max(rows[0]['h']/2, TXT/2 + 1.2) * k
                y = C[1] + 5*k
                for _ in range(200):
                    if free(y - down, y + up): break
                    y = min(b for a, b in occ if not (y + up <= a or y - down >= b)) + down + 0.01*k
                dm['_pos'] = y; occ.append((y - down, y + up))
        else:
            items.sort(key=lambda t: (round(t[1][0], 6), -t[1][1] if sd == 'above' else t[1][1]))
            for dm, C in items:
                w = (max(r_['w'] for r_ in callout_rows(spec, dm)) + 6) * k
                x = C[0] + 5*k
                for _ in range(200):
                    if free(x - 1*k, x + w): break
                    x = min(b for a, b in occ if not (x + w <= a or x - 1*k >= b)) + 1*k + 0.01*k
                dm['_pos'] = x; occ.append((x - 1*k, x + w))


def draw_callout(msp, spec, dm, vi, s, k):
    """Leader note from a hole/cylinder/slot (or a point) out past the view edge on a free side."""
    from ezdxf.enums import TextEntityAlignment as TA
    att = {'layer': 'DIMENSIONS'}
    C, rr, sd = callout_anchor(spec, dm, vi, s, k)
    bb = vi['b']; LL = vi['T'](bb[0], bb[1]); UR = vi['T'](bb[2], bb[3])
    extra = (max([o.get('offset', DIM_FIRST) for o in spec['dims'] if o['view'] == dm['view'] and o['kind'] == 'linear'
                  and o.get('side') == sd] + [0]) + DIM_STEP) * k if sd in stacked_sides(spec, dm['view']) else 0
    dist = dm.get('dist', 6) * k + extra
    pos = dm.get('_pos')
    if sd == 'right': elbow = (UR[0] + dist, pos if pos is not None else C[1] + 5*k)
    elif sd == 'left': elbow = (LL[0] - dist, pos if pos is not None else C[1] + 5*k)
    elif sd == 'above': elbow = (pos if pos is not None else C[0] + 5*k, UR[1] + dist)
    else: elbow = (pos if pos is not None else C[0] + 5*k, LL[1] - dist)
    ang = math.atan2(elbow[1] - C[1], elbow[0] - C[0])
    tip = (C[0] + rr * math.cos(ang), C[1] + rr * math.sin(ang))
    sgn = -1 if sd == 'left' else 1
    land = (elbow[0] + sgn * 3 * k, elbow[1])
    msp.add_lwpolyline([tip, elbow, land], dxfattribs=att)
    ah = 2.5 * k
    msp.add_solid([tip, (tip[0] + ah*math.cos(ang - 0.3), tip[1] + ah*math.sin(ang - 0.3)),
                   (tip[0] + ah*math.cos(ang + 0.3), tip[1] + ah*math.sin(ang + 0.3))], dxfattribs=att)
    rows = callout_rows(spec, dm); offs = row_offsets(rows)
    x0 = land[0] + sgn * 1 * k
    for i, (row, off) in enumerate(zip(rows, offs)):
        y = land[1] - off * k
        if row['t'] == 'text':
            add_text(msp, row['s'], (x0, y), TXT * k, 'DIMENSIONS', TA.MIDDLE_LEFT if sgn > 0 else TA.MIDDLE_RIGHT)
        elif row['t'] == 'fcf':
            gdt.draw_fcf(msp, spec, row['f'], x0, y, k, fmt, text_w, add_text, TA, right_align=sgn < 0)
        else:   # datum feature symbol hanging under the frame (or under the note, on a short attachment line)
            cx = x0 + sgn * gdt.DBOX / 2 * k
            top = y + row['h'] / 2 * k
            prev = rows[i-1]['t'] if i else 'text'
            if prev == 'fcf': top += 0.3 * k
            else: msp.add_line((cx - 3 * k, top), (cx + 3 * k, top), dxfattribs={'layer': 'GDT'})
            gdt.draw_datum(msp, row['l'], (cx, top), (0, -1), k, add_text, TA, stem=0.3)
    dm['_tag'] = (land[0], land[1] + 3.5 * k)


def draw_inside_dim(msp, spec, dm, vi, s, k, horiz, a, b):
    """Dimension across a feature (e.g. shaft diameter): arrows inside, line extended past the outline to the text."""
    from ezdxf.enums import TextEntityAlignment as TA
    att = {'layer': 'DIMENSIONS'}
    at = dm['at']; bb = vi['b']
    P1 = vi['T'](*([a, at] if horiz else [at, a])); P2 = vi['T'](*([b, at] if horiz else [at, b]))
    txt = (dm.get('text') or '<>').replace('%%c', 'Ø').replace('<>', fmt(spec, abs(b - a) / vi['f']))
    if dm.get('tol'): txt += ' ' + dm['tol']
    sd = inside_text_side(spec, dm)
    gap = 4 * k
    if horiz:
        y = P1[1]
        lo_, hi_ = sorted([P1[0], P2[0]])
        if sd == 'left':
            end = vi['T'](bb[0], at)[0] - gap; line = [(end, y), (hi_, y)]; tpos, al = (end - 1*k, y), TA.MIDDLE_RIGHT
        else:
            end = vi['T'](bb[2], at)[0] + gap; line = [(lo_, y), (end, y)]; tpos, al = (end + 1*k, y), TA.MIDDLE_LEFT
        arrows = [((lo_, y), 0.0), ((hi_, y), math.pi)]
    else:
        x = P1[0]
        lo_, hi_ = sorted([P1[1], P2[1]])
        if sd == 'below':
            end = vi['T'](at, bb[1])[1] - gap; line = [(x, end), (x, hi_)]; tpos, al = (x, end - 1*k), TA.TOP_CENTER
        else:
            end = vi['T'](at, bb[3])[1] + gap; line = [(x, lo_), (x, end)]; tpos, al = (x, end + 1*k), TA.BOTTOM_CENTER
        arrows = [((x, lo_), math.pi/2), ((x, hi_), -math.pi/2)]
    msp.add_line(line[0], line[1], dxfattribs=att)
    ah = 2.5 * k
    for (tx, ty), ang in arrows:    # arrow tip at outline, pointing outward (ang = direction from tip into the feature)
        msp.add_solid([(tx, ty), (tx + ah*math.cos(ang - 0.3), ty + ah*math.sin(ang - 0.3)),
                       (tx + ah*math.cos(ang + 0.3), ty + ah*math.sin(ang + 0.3))], dxfattribs=att)
    add_text(msp, txt, tpos, TXT*k, 'DIMENSIONS', al)
    gr = gdt_rows(spec, dm)
    if gr:
        if sd in ('left', 'right'):
            draw_gdt_rows(msp, spec, gr, tpos[0], tpos[1], k, 'right' if sd == 'left' else 'left')
        else:   # text centred below/above the dim line: frames continue under it
            ymid = tpos[1] - TXT/2*k if sd == 'below' else tpos[1] + TXT/2*k
            draw_gdt_rows(msp, spec, gr, tpos[0], ymid, k, 'center')
    dm['_tag'] = (tpos[0], tpos[1] + 3.5*k)


def fit_text(text, w, h):
    if not text: return h
    return min(h, w / (_wlen(text) * 0.62))


def draw_sheet(msp, spec, sheet, s, k, d):
    from ezdxf.enums import TextEntityAlignment as TA
    PW, PH = SHEETS[sheet]
    P = lambda x, y: (x*k, y*k)
    tb = {'layer': 'TITLEBLOCK'}
    msp.add_lwpolyline([P(0, 0), P(PW, 0), P(PW, PH), P(0, PH)], close=True, dxfattribs={'layer': 'TITLEBLOCK', 'lineweight': 13, 'color': 9})
    msp.add_lwpolyline([P(BORDER, BORDER), P(PW-BORDER, BORDER), P(PW-BORDER, PH-BORDER), P(BORDER, PH-BORDER)], close=True,
                       dxfattribs={'layer': 'TITLEBLOCK', 'lineweight': 50})
    x0 = PW - BORDER - TB_W; y0 = BORDER
    R = lambda i: y0 + TB_H - TB_ROW*i      # top of row i
    def box(xa, ya, xb, yb, lw=25):
        msp.add_lwpolyline([P(xa, ya), P(xb, ya), P(xb, yb), P(xa, yb)], close=True, dxfattribs={'layer': 'TITLEBLOCK', 'lineweight': lw})
    box(x0, y0, x0+TB_W, y0+TB_H, 50)
    t = spec['title']
    def cell(xa, xb, row, label, value, rows=1, vh=2.6, center=False):
        vh = min(vh, 3.0) if rows == 1 else vh
        ya = R(row+rows); yb = R(row)
        box(xa, ya, xb, yb)
        add_text(msp, label, P(xa+1, yb-1.0), 1.5*k, 'TITLEBLOCK', TA.TOP_LEFT)
        if value:
            h = fit_text(value, xb-xa-2.5, vh)
            if center:
                add_text(msp, value, P((xa+xb)/2, (ya+yb)/2 - 0.8), h*k, 'TITLEBLOCK', TA.MIDDLE_CENTER)
            else:
                add_text(msp, value, P(xa+1.5, ya+1.1), h*k, 'TITLEBLOCK', TA.BOTTOM_LEFT)
    xl = x0 + 70
    # tolerance block (rows 0-4)
    box(x0, R(5), xl, R(0))
    lines = ['UNLESS OTHERWISE SPECIFIED:', f"DIMENSIONS ARE IN {UNITS_WORD[spec['units']]}", 'TOLERANCES:'] + list(spec['tolerances'])
    if spec.get('kind') in ('assembly', 'exploded'):
        lines = ['ASSEMBLY DRAWING - NOT DIMENSIONED', 'SEE PART DRAWINGS FOR DIMENSIONS', 'AND TOLERANCES', 'DO NOT SCALE DRAWING']
    lh = min(4.6, 38.0/max(len(lines), 1))
    for i, ln in enumerate(lines):
        h = fit_text(ln, 66, min(2.2, lh*0.62))
        add_text(msp, ln, P(x0+2, R(0)-2-lh*i), h*k, 'TITLEBLOCK', TA.TOP_LEFT)
    # projection (rows 5-6)
    box(x0, R(7), xl, R(5))
    draw_projection_symbol(msp, spec['projection'], x0+5, R(7)+3.5, P, k)
    add_text(msp, f"{spec['projection'].upper()} ANGLE", P(x0+38, R(6)+1.2), 2.2*k, 'TITLEBLOCK', TA.MIDDLE_LEFT)
    add_text(msp, 'PROJECTION', P(x0+38, R(6)-2.8), 2.2*k, 'TITLEBLOCK', TA.MIDDLE_LEFT)
    xr = x0 + TB_W
    # company + logo (rows 0-1)
    logo = t.get('logo')
    if logo and os.path.exists(logo):
        box(xl, R(2), xl+25, R(0))
        try:
            from PIL import Image
            im = Image.open(logo); iw, ih = im.size
            dst = os.path.join(d, 'logo' + os.path.splitext(logo)[1].lower())
            if os.path.abspath(logo) != os.path.abspath(dst): shutil.copy(logo, dst)
            maxw, maxh = 23.0, 14.0; sc = min(maxw/iw, maxh/ih)
            w, h = iw*sc, ih*sc
            idf = msp.doc.add_image_def(filename=os.path.basename(dst), size_in_pixel=(iw, ih))
            msp.add_image(idf, insert=P(xl+1+(maxw-w)/2, R(2)+1+(maxh-h)/2), size_in_units=(w*k, h*k), rotation=0,
                          dxfattribs={'layer': 'TITLEBLOCK'})
        except Exception as e:
            print('logo failed:', e, file=sys.stderr)
        cell(xl+25, xr, 0, 'COMPANY', t.get('company', ''), rows=2, vh=5.0, center=True)
    else:
        cell(xl, xr, 0, 'COMPANY', t.get('company', ''), rows=2, vh=5.0, center=True)
    cell(xl, xr, 2, 'PART NAME', t.get('part_name', ''), vh=3.5)
    cell(xl, xl+65, 3, 'PART NO.', t.get('part_number', ''), vh=3.2)
    cell(xl+65, xr, 3, 'REV', t.get('revision', ''), vh=3.2, center=True)
    cell(xl, xl+50, 4, 'MATERIAL', t.get('material', ''))
    cell(xl+50, xr, 4, 'FINISH', t.get('finish', ''))
    cell(xl, xl+20, 5, 'QTY', str(t.get('quantity', '') or ''), center=True)
    cell(xl+20, xl+40, 5, 'UNITS', spec['units'].upper(), center=True)
    cell(xl+40, xl+70, 5, 'DRAWN BY', t.get('drawn_by', ''))
    cell(xl+70, xr, 5, 'DATE', t.get('date', ''))
    cell(xl, xl+30, 6, 'SCALE', scale_str(s), center=True)
    cell(xl+30, xl+60, 6, 'SIZE', sheet.replace('ANSI_', ''), center=True)
    sn = spec.get('sheet_no') or [1, 1]
    cell(xl+60, xr, 6, 'SHEET', f'{sn[0]} OF {sn[1]}', center=True)
    # revision table (top right, grows downward)
    revs = revisions(spec)
    rw = sum(w for _, w in REV_COLS); rx0 = PW - BORDER - rw; ry = PH - BORDER
    box(rx0, ry - REV_ROW, rx0 + rw, ry, 35)
    add_text(msp, 'REVISIONS', P(rx0 + rw/2, ry - REV_ROW/2), 2.5*k, 'TITLEBLOCK', TA.MIDDLE_CENTER)
    for ri, row in enumerate([None] + revs):
        yt = ry - REV_ROW * (ri + 1); x = rx0
        for (name, w), key in zip(REV_COLS, ('rev', 'desc', 'date', 'by')):
            box(x, yt - REV_ROW, x + w, yt)
            val = name if row is None else str(row.get(key) or '')
            if val:
                hh = fit_text(val, w - 2, 2.2 if row is None else 2.5)
                if key == 'desc' and row is not None:
                    table_text(msp, val, x, yt - REV_ROW/2, w, P, k, TA)
                else:
                    add_text(msp, val, P(x + w/2, yt - REV_ROW/2), hh*k, 'TITLEBLOCK', TA.MIDDLE_CENTER)
            x += w
    # bill of materials (assembly): header right above the title block, items stacked upward
    if spec.get('kind') == 'assembly':
        bx0 = PW - BORDER - TB_W
        rows_ = [None] + sorted(spec.get('bom', []), key=lambda b: b['item'])
        for ri, row in enumerate(rows_):
            yb = BORDER + TB_H + BOM_ROW * ri; x = bx0
            for (name, w), key in zip(BOM_COLS, ('item', 'part_number', 'description', 'material', 'qty')):
                box(x, yb, x + w, yb + BOM_ROW, 35 if row is None else 25)
                val = name if row is None else str(row.get(key, '') if row.get(key) is not None else '')
                if val:
                    hh = fit_text(val, w - 2, 2.2 if row is None else 2.5)
                    if key in ('item', 'qty') or row is None:
                        add_text(msp, val, P(x + w/2, yb + BOM_ROW/2), hh*k, 'TITLEBLOCK', TA.MIDDLE_CENTER)
                    else:
                        table_text(msp, val, x, yb + BOM_ROW/2, w, P, k, TA)
                x += w
    # notes block (bottom-left)
    notes = [n.replace('{UNITS}', UNITS_WORD[spec['units']]) for n in spec['notes']] + gdt_notes(spec)
    if notes:
        nx, ny = BORDER + 4, BORDER + TB_H - 2
        maxw = PW - 2*BORDER - TB_W - 10
        add_text(msp, 'NOTES:', P(nx, ny), 3.0*k, 'NOTES', TA.TOP_LEFT)
        y = ny - 6
        chars = max(20, int(maxw / (2.5*0.78)))
        for i, n in enumerate(notes):
            wrapped = wrap(f'{i+1}. {n}', chars)
            for j, ln in enumerate(wrapped):
                add_text(msp, ('    ' if j else '') + ln, P(nx, y), 2.5*k, 'NOTES', TA.TOP_LEFT); y -= 4.2


def table_text(msp, val, x, ymid, w, P, k, TA):
    """Left-aligned cell text: shrink to fit, or two lines when it would get too small."""
    h = min(2.5, (w - 2.4) / (len(val) * 0.9))
    if h >= 1.8:
        add_text(msp, val, P(x + 1.2, ymid), h*k, 'TITLEBLOCK', TA.MIDDLE_LEFT); return
    lines = wrap(val, max(8, (len(val) + 1) // 2 + 2))[:2]
    h = min(1.9, (w - 2.4) / (max(len(l) for l in lines) * 0.9))
    for i, ln in enumerate(lines):
        add_text(msp, ln, P(x + 1.2, ymid + (1.4 if i == 0 else -1.4)), h*k, 'TITLEBLOCK', TA.MIDDLE_LEFT)


def wrap(t, n):
    words, lines, cur = t.split(), [], ''
    for w in words:
        if len(cur) + len(w) + 1 > n and cur: lines.append(cur); cur = w
        else: cur = (cur + ' ' + w).strip()
    if cur: lines.append(cur)
    return lines


def draw_projection_symbol(msp, proj, x, y, P, k):
    """Truncated cone: trapezoid + 2 circles; height ~9mm."""
    att = {'layer': 'TITLEBLOCK', 'lineweight': 25}
    H, h, L = 9.0, 4.5, 11.0
    cy = y + H/2
    if proj == 'third':
        tx, cx = x, x + L + 4 + H/2        # trapezoid left, narrow end at right toward circles
        pts = [(tx, cy-H/2), (tx, cy+H/2), (tx+L, cy+h/2), (tx+L, cy-h/2)]
    else:
        cx, tx = x + H/2, x + H + 4        # circles left, trapezoid right with wide end toward circles
        pts = [(tx, cy-H/2), (tx, cy+H/2), (tx+L, cy+h/2), (tx+L, cy-h/2)]
    msp.add_lwpolyline([P(*p) for p in pts], close=True, dxfattribs=att)
    msp.add_circle(P(cx, cy), H/2*k, dxfattribs=att)
    msp.add_circle(P(cx, cy), h/2*k, dxfattribs=att)
    msp.add_line(P(tx-1.5, cy), P(tx+L+1.5, cy), dxfattribs={'layer': 'CENTER'})
    msp.add_line(P(cx-H/2-1.5, cy), P(cx+H/2+1.5, cy), dxfattribs={'layer': 'CENTER'})
    msp.add_line(P(cx, cy-H/2-1.5), P(cx, cy+H/2+1.5), dxfattribs={'layer': 'CENTER'})


# ------------------------------------------------------------------ commands
def cmd_make(a):
    import geom
    cfg = dict(DEFAULT_CONFIG); cfg.update(load_json(a.config))
    fields = load_json(a.fields)
    for k_, v_ in fields.items():   # per-drawing overrides of anything
        cfg[k_] = v_
    model = os.path.abspath(a.model)
    out = os.path.abspath(a.out); os.makedirs(out, exist_ok=True)
    try:
        shape, is_mesh, warns = geom.load(model, a.model_units)
    except geom.LoadError as e:
        print(json.dumps({'ok': False, 'error': str(e)})); sys.exit(2)
    _SHAPE[model] = (shape, is_mesh, warns)
    units = cfg.get('units', 'in')
    title = {f: cfg.get(f, '') for f in TITLE_FIELDS}
    if not title.get('date'): title['date'] = datetime.date.today().isoformat()
    if title.get('logo'): title['logo'] = os.path.abspath(os.path.expanduser(title['logo']))
    spec = {
        'version': 1, 'model': model, 'model_units': a.model_units, 'mesh': is_mesh,
        'units': units, 'decimals': cfg['decimals'] if cfg.get('decimals') is not None else (3 if units == 'in' else 2),
        'projection': cfg.get('projection', 'third'), 'sheet': cfg.get('sheet', 'auto'), 'scale': cfg.get('scale', 'auto'),
        'sheet_family': cfg.get('sheet_family', ''), 'hidden_lines': bool(cfg.get('hidden_lines', True)),
        'tolerances': cfg['tolerances'], 'notes': list(cfg['notes']), 'title': title,
        'views': [{'id': 'front', 'type': 'front'}, {'id': 'top', 'type': 'top'}, {'id': 'right', 'type': 'right'}]
                 + ([{'id': 'iso', 'type': 'iso'}] if cfg.get('iso_view', True) else []),
        'warnings': warns,
    }
    if is_mesh:
        if 'hidden_lines' not in load_json(a.fields) and 'hidden_lines' not in load_json(a.config): spec['hidden_lines'] = False
        spec['notes'].append('DIMENSIONS DERIVED FROM MESH MODEL - APPROXIMATE, VERIFY BEFORE MACHINING')
    spec['features'] = geom.features(shape, is_mesh)
    spec['thread_suggestions'] = thread_suggestions(spec, geom.scan_step_threads(model) if model.lower().endswith(('.step', '.stp')) else [])
    spec['threads'] = {}
    spec['gdt_suggestions'] = gdt.suggest(spec, fmt)
    spec['gdt'] = {'datums': [], 'fcfs': []}
    cache = {}
    spec['dims'] = auto_dims(spec, out, cache)
    spec = render(spec, out, cache)
    report(spec, out)


# ------------------------------------------------------------------ assemblies
def _slug(t):
    import re
    return re.sub(r'[^A-Za-z0-9]+', '-', t).strip('-').lower()[:40] or 'part'


def asm_rebase(spec, d):
    """Assembly folders can be moved or copied: re-point stored absolute paths at the folder they are in now."""
    if spec.get('kind') != 'assembly': return spec
    new = os.path.dirname(os.path.abspath(d)); old = spec.get('root') or new
    if old != new:
        fix = lambda p_: (new + p_[len(old):]) if isinstance(p_, str) and p_.startswith(old + os.sep) else p_
        spec['model'] = fix(spec['model']); spec['parts_root'] = fix(spec['parts_root'])
        for b in spec['bom']: b['component'] = fix(b['component']); b['part_dir'] = fix(b.get('part_dir'))
        for b in spec['bom']:
            pd = b.get('part_dir')
            if pd and os.path.exists(os.path.join(pd, 'spec.json')):
                ps = read_spec(pd)
                if ps.get('model', '').startswith(old + os.sep):
                    ps['model'] = fix(ps['model']); save_spec(pd, ps)
    spec['root'] = new
    return spec


def _asm_dir(d):
    d = os.path.abspath(d)
    if os.path.exists(os.path.join(d, 'assembly', 'spec.json')): return os.path.join(d, 'assembly')
    return d


def build_assembly(a, choose_only=False):
    """Scan the model, write the assembly sheet (balloons + BOM, no dims) and, unless choose_only, the part drawings."""
    import assembly as A, geom
    cfg = dict(DEFAULT_CONFIG); cfg.update(load_json(a.config))
    fields = load_json(a.fields)
    per_item = fields.pop('items', {}) if isinstance(fields.get('items'), dict) else {}
    for k_, v_ in fields.items(): cfg[k_] = v_
    choices = {}
    if getattr(a, 'items', None):
        for it in load_json(a.items).get('items', []): choices[int(it['item'])] = it
    model = os.path.abspath(a.model); root = os.path.abspath(a.out)
    adir = os.path.join(root, 'assembly'); cdir = os.path.join(adir, 'components')
    os.makedirs(cdir, exist_ok=True)
    try:
        inst, groups, structured = A.scan(model)
    except geom.LoadError as e:
        print(json.dumps({'ok': False, 'error': str(e)})); sys.exit(2)
    if len(inst) < 2:
        print(json.dumps({'ok': False, 'error': 'only one solid found - use `epure make` for single parts'})); sys.exit(2)
    comp = A.compound([i['shape'] for i in inst])
    mpath = os.path.join(adir, 'model.brep')
    from OCP.BRepTools import BRepTools
    BRepTools.Write_s(comp, mpath)
    units = cfg.get('units', 'in')
    title = {f: cfg.get(f, '') for f in TITLE_FIELDS}
    if not title.get('date'): title['date'] = datetime.date.today().isoformat()
    if title.get('logo'): title['logo'] = os.path.abspath(os.path.expanduser(title['logo']))
    asm_pn = title.get('part_number', '')
    bom = []
    for g in groups:
        n = g['item']; ch = choices.get(n, {})
        if ch and ch.get('name') and A.clean_name(ch['name']).upper() != g['name'].upper():
            print(f"warning: items file item {n} is '{ch['name']}' but the model's item {n} is '{g['name']}'", file=sys.stderr)
        fi = dict(per_item.get(str(n), {}) or per_item.get(g['name'], {}) or {}); fi.update({k_: v_ for k_, v_ in ch.items() if v_ not in (None, '')})
        hw = bool(g['hardware'])
        cpath = os.path.join(cdir, f'item-{n:02d}.step')
        A.write_step(inst[g['inst'][0]]['proto'], cpath)
        dr = fi.get('drawing', not hw)
        if isinstance(dr, str): dr = dr.lower() in ('yes', 'true', '1', 'y')
        bom.append({'item': n, 'name': g['name'], 'description': str(fi.get('description') or g['name']).upper(),
                    'part_number': fi.get('part_number') or (f'{asm_pn}-{n:02d}' if asm_pn and not hw else (g['name'] if hw else '')),
                    'material': fi.get('material', ''), 'finish': fi.get('finish', ''), 'qty': len(g['inst']),
                    'hardware': hw, 'hardware_reason': g['hardware'], 'drawing': bool(dr), 'size_mm': g['size'],
                    'component': cpath, 'part_dir': None, 'instances': [inst[i]['name'] for i in g['inst']],
                    'fields': {k_: v_ for k_, v_ in fi.items() if k_ in ('revision', 'notes', 'sheet', 'scale', 'tolerances', 'decimals')}})
    lo, hi = geom.bbox(comp, fast=True)
    import explode as X
    ex_on = bool(getattr(a, 'exploded', False) or cfg.get('exploded') or
                 (getattr(a, 'items', None) and load_json(a.items).get('exploded')))
    spec = {
        'version': 1, 'kind': 'assembly', 'model': mpath, 'source_model': model, 'model_units': 'mm', 'mesh': False,
        'units': units, 'decimals': cfg['decimals'] if cfg.get('decimals') is not None else (3 if units == 'in' else 2),
        'projection': cfg.get('projection', 'third'), 'sheet': cfg.get('sheet', 'auto'), 'scale': cfg.get('scale', 'auto'),
        'sheet_family': cfg.get('sheet_family', ''), 'hidden_lines': False,
        'tolerances': cfg['tolerances'], 'notes': list(cfg.get('assembly_notes', [])), 'title': title,
        'views': [{'id': 'front', 'type': 'front'}, {'id': 'top', 'type': 'top'}, {'id': 'iso', 'type': 'iso'}],
        'warnings': [] if structured else ['no assembly structure in the file: solids were split and named SOLID n'],
        'features': {'bbox': [lo, hi], 'holes': [], 'cyls': [], 'slots': [], 'fillets': [], 'chamfers': [], 'slopes': [],
                     'bolt_circles': [], 'levels': {'x': [], 'y': [], 'z': []}, 'feature_warnings': []},
        'dims': [], 'bom': bom, 'balloons': [], 'thread_suggestions': [], 'threads': {}, 'gdt_suggestions': [],
        'gdt': {'datums': [], 'fcfs': []}, 'parts_root': os.path.join(root, 'parts'), 'root': root,
        'config': os.path.abspath(a.config) if a.config else None,
        'instances': X.save_instances(inst, groups, adir),
        'exploded': {'on': ex_on and not choose_only, 'scale': 1.0, 'overrides': {}},
    }
    _SHAPE[mpath] = (comp, False, [])
    cache = {}
    spec['anchors'] = {}
    for v in spec['views']:
        g_ = view_geom(spec, adir, v, cache)
        spec['anchors'][v['id']] = {str(n_): q for n_, q in A.anchor_points(inst, groups, g_['visible'], v['type'], comp).items()}
    for b in bom:
        for vw in ('iso', 'front', 'top'):     # hidden in the iso (e.g. nuts under a plate): balloon it where it shows
            q = spec['anchors'][vw].get(str(b['item']))
            if q: spec['balloons'].append({'item': b['item'], 'view': vw, 'anchor': q}); break
        else: spec['warnings'].append(f"item {b['item']} {b['name']}: not visible in any view, no balloon")
    if not choose_only:
        for b in bom:
            if b['drawing']: make_part(spec, b, quiet=True)
    spec = render(spec, adir, cache)
    if not choose_only:
        if spec['exploded']['on']: build_exploded(spec, adir)
        spec = number_sheets(spec, adir)
    return spec, root


# ------------------------------------------------------------------ exploded sheet + sheet numbers
EXPLODE_OPS = {'exploded', 'explode', 'explode-scale'}
BALLOON_OPS = {'add-balloon', 'move-balloon', 'del-balloon'}


def _edir(adir): return os.path.join(os.path.dirname(os.path.abspath(adir)), 'exploded')


def build_exploded(asm, adir):
    """(Re)build <job>/exploded from the assembly spec: auto/override explode plan, moved shapes, trails, balloons."""
    import explode as X, assembly as A, geom
    edir = _edir(adir); os.makedirs(edir, exist_ok=True)
    old = read_spec(edir) if os.path.exists(os.path.join(edir, 'spec.json')) else {}
    ex = asm.setdefault('exploded', {'on': True, 'scale': 1.0, 'overrides': {}})
    insts = X.load_instances(asm, adir)
    res = X.plan(insts, asm['bom'], ex.get('scale', 1.0), ex.get('overrides', {}))
    mv = X.moved(insts, res)
    comp = A.compound([i['shape'] for i in mv])
    mpath = os.path.join(edir, 'exploded.brep')
    from OCP.BRepTools import BRepTools
    BRepTools.Write_s(comp, mpath)
    _SHAPE[mpath] = (comp, False, [])
    nth = {}
    ex['plan'] = []
    for it, r in zip(insts, res):
        nth[it['item']] = nth.get(it['item'], 0) + 1
        ex['plan'].append({'item': it['item'], 'instance': f"{it['item']}.{nth[it['item']]}", 'name': it['name'],
                           'dir': X.dir_name(r['dir'], r['dist_scaled']), 'dist_mm': round(r['dist_scaled'], 2),
                           'rides_with_item': insts[r['nb']]['item'] if r.get('nb') is not None else None,
                           'why': r['why']})
    lo, hi = geom.bbox(comp, fast=True)
    es = {k_: asm[k_] for k_ in ('version', 'model_units', 'mesh', 'units', 'decimals', 'projection', 'sheet', 'scale',
                                 'sheet_family', 'tolerances', 'title', 'bom', 'config', 'break') if k_ in asm}
    es.update({'kind': 'exploded', 'model': mpath, 'hidden_lines': False, 'notes': ['SEE SHEET 1 FOR PARTS LIST'],
               'views': [{'id': 'iso', 'type': 'iso'}], 'dims': [], 'warnings': [],
               'features': {'bbox': [lo, hi], 'holes': [], 'cyls': [], 'slots': [], 'fillets': [], 'chamfers': [], 'slopes': [],
                            'bolt_circles': [], 'levels': {'x': [], 'y': [], 'z': []}, 'feature_warnings': []},
               'thread_suggestions': [], 'threads': {}, 'gdt_suggestions': [], 'gdt': {'datums': [], 'fcfs': []},
               'balloon_off': old.get('balloon_off', []), 'sheet_no': old.get('sheet_no', [2, 2])})
    if os.path.exists(os.path.join(edir, 'geom_cache.json')): os.remove(os.path.join(edir, 'geom_cache.json'))
    cache = {}
    g_ = view_geom(es, edir, es['views'][0], cache)
    groups = {}
    for ii, it in enumerate(mv): groups.setdefault(it['item'], []).append(ii)
    anch = A.anchor_points(mv, [{'item': n, 'inst': v} for n, v in groups.items()], g_['visible'], 'iso', comp)
    es['anchors'] = {'iso': {str(n): q for n, q in anch.items()}}
    keep = {b_['item']: b_ for b_ in old.get('balloons', [])}
    es['balloons'] = []
    for b in asm['bom']:
        if b['item'] in es['balloon_off']: continue
        q = anch.get(b['item'])
        if not q: es['warnings'].append(f"item {b['item']} {b['name']}: not visible in the exploded view, no balloon"); continue
        nb = {'item': b['item'], 'view': 'iso', 'anchor': q}
        for k_ in ('angle', 'dist'):
            if keep.get(b['item'], {}).get(k_) is not None: nb[k_] = keep[b['item']][k_]
        es['balloons'].append(nb)
    es['trails'] = [[list(geom.project(a_, 'iso')), list(geom.project(b_, 'iso'))] for a_, b_ in X.trails(res)]
    save_spec(adir, asm)
    return render(es, edir, cache)


def sync_exploded(spec, d):
    """The exploded sheet shares the assembly's title block, revision history and BOM numbering."""
    adir = os.path.join(os.path.dirname(os.path.abspath(d)), 'assembly')
    spec['model'] = os.path.join(os.path.abspath(d), 'exploded.brep')
    if os.path.exists(os.path.join(adir, 'spec.json')):
        asm = read_spec(adir)
        spec['title'] = dict(asm['title']); spec['revisions'] = asm.get('revisions') or revisions(asm)
        for k_ in ('units', 'projection', 'tolerances', 'bom'): spec[k_] = asm[k_]


def sheet_dirs(asm, adir):
    out = [os.path.abspath(adir)]
    if asm.get('exploded', {}).get('on') and os.path.exists(os.path.join(_edir(adir), 'spec.json')): out.append(_edir(adir))
    for b in sorted(asm['bom'], key=lambda x: x['item']):
        if b.get('drawing') and b.get('part_dir') and os.path.exists(os.path.join(b['part_dir'], 'spec.json')):
            out.append(b['part_dir'])
    return out


def number_sheets(asm, adir):
    """Assembly = sheet 1, exploded = 2 (if on), then each part drawing; re-render any sheet whose number changed."""
    dirs = sheet_dirs(asm, adir); N = len(dirs)
    for n, dd in enumerate(dirs, 1):
        if dd == os.path.abspath(adir):
            if asm.get('sheet_no') != [n, N]: asm['sheet_no'] = [n, N]; asm = render(asm, adir)
            continue
        sp = read_spec(dd)
        if sp.get('sheet_no') != [n, N]:
            sp['sheet_no'] = [n, N]; render(sp, dd)
    return asm


def make_part(spec, b, quiet=True):
    """Normal dimensioned drawing for one BOM item (thread/GD&T suggestions stay pending)."""
    import contextlib, io
    pd = os.path.join(spec['parts_root'], f"{b['item']:02d}-{_slug(b['name'])}")
    os.makedirs(pd, exist_ok=True)
    flds = {k_: spec['title'].get(k_, '') for k_ in ('company', 'logo', 'drawn_by', 'date')}
    flds.update({'units': spec['units'], 'decimals': spec['decimals'], 'projection': spec['projection'],
                 'sheet_family': spec.get('sheet_family', ''), 'tolerances': spec['tolerances'], 'revision': '',
                 'part_name': b['description'], 'part_number': b['part_number'], 'material': b['material'],
                 'finish': b['finish'], 'quantity': b['qty']})
    flds.update(b.get('fields', {}))
    fpath = os.path.join(pd, 'fields.json')
    with open(fpath, 'w') as f: json.dump(flds, f, indent=1)
    ns = argparse.Namespace(model=b['component'], config=spec.get('config'), fields=fpath, out=pd, model_units='mm')
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf) if quiet else contextlib.nullcontext():
        cmd_make(ns)
    b['part_dir'] = pd
    return pd


def asm_report(spec, root):
    r = spec['resolved']
    parts = []
    for b in spec['bom']:
        e = {'item': b['item'], 'name': b['name'], 'qty': b['qty'], 'part_number': b['part_number'], 'hardware': b['hardware'],
             'drawing': b['drawing']}
        if b.get('part_dir') and b['drawing'] and os.path.exists(os.path.join(b['part_dir'], 'spec.json')):
            ps = read_spec(b['part_dir'])
            e.update({'dir': b['part_dir'], 'preview': os.path.join(b['part_dir'], 'preview.png'),
                      'audit_errors': ps['resolved']['audit_errors'], 'dim_errors': ps['resolved']['dim_errors'],
                      'thread_suggestions': [t['text'] for t in ps.get('thread_suggestions', []) if t['status'] == 'pending'],
                      'gdt_suggestions': [g_['id'] + ': ' + g_['text'] for g_ in ps.get('gdt_suggestions', []) if g_['status'] == 'pending']})
        parts.append(e)
    out = {'ok': True, 'root': root, 'assembly_dir': os.path.join(root, 'assembly'),
           'preview': os.path.join(root, 'assembly', 'preview.png'), 'sheet': r['sheet'], 'scale': r['scale'],
           'audit_errors': r['audit_errors'], 'warnings': spec.get('warnings', []), 'items': parts,
           'sheets': len(sheet_dirs(spec, os.path.join(root, 'assembly')))}
    ex = spec.get('exploded', {})
    edir = os.path.join(root, 'exploded')
    if ex.get('on') and os.path.exists(os.path.join(edir, 'spec.json')):
        es = read_spec(edir)
        out['exploded'] = {'dir': edir, 'preview': os.path.join(edir, 'preview.png'), 'scale': es['resolved']['scale'],
                           'sheet': es['resolved']['sheet'], 'audit_errors': es['resolved']['audit_errors'],
                           'explode_scale': ex.get('scale', 1.0), 'warnings': es.get('warnings', []), 'plan': ex.get('plan', [])}
    else:
        out['exploded'] = 'off'
    print(json.dumps(out, indent=1, ensure_ascii=False))


def cmd_assembly_scan(a):
    spec, root = build_assembly(a, choose_only=True)
    items = [{'item': b['item'], 'name': b['name'], 'qty': b['qty'], 'hardware': b['hardware'],
              'hardware_reason': b['hardware_reason'], 'drawing': b['drawing'], 'part_number': b['part_number'],
              'description': b['description'], 'material': b['material'], 'finish': b['finish'],
              'size_mm': b['size_mm']} for b in spec['bom']]
    ipath = os.path.join(root, 'items.json')
    with open(ipath, 'w') as f: json.dump({'assembly_part_number': spec['title'].get('part_number', ''), 'exploded': False, 'items': items}, f,
                                          indent=1, ensure_ascii=False)
    shutil.copy(os.path.join(root, 'assembly', 'preview.png'), os.path.join(root, 'scan_preview.png'))
    print(json.dumps({'ok': True, 'items_file': ipath, 'preview': os.path.join(root, 'scan_preview.png'),
                      'warnings': spec.get('warnings', []), 'items': items}, indent=1, ensure_ascii=False))


def cmd_assembly(a):
    spec, root = build_assembly(a)
    asm_report(spec, root)


def thread_suggestions(spec, file_threads):
    """Pending thread suggestions for plain holes whose size matches a tap drill. Never drawn until accepted."""
    import threads
    F = spec['features']
    cb_big = set()
    for big in F['holes']:
        for sm in F['holes']:
            i = 'xyz'.index(big['axis'])
            if sm is not big and sm['axis'] == big['axis'] and sm['dia'] < big['dia'] and not big['thru'] and \
               all(abs(sm['center'][j]-big['center'][j]) < 1e-3 for j in range(3) if j != i) and \
               (abs(big['lo']-sm['hi']) < 1e-3 or abs(big['hi']-sm['lo']) < 1e-3):
                cb_big.add(big['id'])
    for c in F.get('chamfers', []):     # countersunk / counterdrilled holes are screw clearance holes, not tap drills
        if c.get('sub') in ('csk', 'cdrill'): cb_big.update(c.get('holes', []))
    sets = {}
    for h in F['holes']:
        if h['id'] in cb_big: continue
        sets.setdefault((h['axis'], round(h['dia'], 3), h['thru'], round(h['depth'], 3)), []).append(h)
    out = []
    for (ax, dia, thru, depth), hs in sets.items():
        opts = threads.suggest(dia, spec['units'])
        if not opts: continue
        src = 'table'
        for o in opts:
            if any(ft.replace(' ', '').upper().startswith(o['thread'].split('x')[0].replace(' ', '').upper()) for ft in file_threads):
                o['from_file'] = True; src = 'file'
        opts.sort(key=lambda o: not o.get('from_file'))
        clear = threads.clearance_match(dia)
        conf = 'from STEP file' if src == 'file' else ('likely' if not thru else 'possible')
        ids = [h['id'] for h in hs]
        kind = 'thru' if thru else f'blind {fmt(spec, depth)} deep'
        text = (f"{','.join(ids)}: Ø{fmt(spec, dia)} {kind} hole looks like {opts[0]['thread']} {opts[0]['match']} ({conf})"
                + (f"; also matches {', '.join(o['thread'] for o in opts[1:])}" if len(opts) > 1 else '')
                + (f"; note: {clear} - could be a plain hole" if clear else ''))
        o0 = opts[0]
        tdepth = threads.default_thread_depth(depth, o0['pitch_mm'], o0['nominal_mm'], thru)
        out.append(dict(holes=ids, dia=dia, thru=thru, depth=depth, options=opts, confidence=conf, status='pending',
                        default_callout=o0['callout'], default_thread_depth=tdepth, text=text))
    return out


def report(spec, out):
    r = spec['resolved']
    print(json.dumps({'ok': True, 'dir': out, 'dxf': os.path.join(out, 'drawing.dxf'),
                      'preview': os.path.join(out, 'preview.png'), 'preview_ids': os.path.join(out, 'preview_ids.png'),
                      'sheet': r['sheet'], 'scale': r['scale'], 'fits_sheet': r['fits'],
                      'audit_errors': r['audit_errors'], 'dim_errors': r['dim_errors'],
                      'warnings': spec.get('warnings', []) + spec['features'].get('feature_warnings', []) + spec.get('dim_warnings', [])
                                  + spec.get('gdt_warnings', []),
                      'skipped': r.get('skipped', []),
                      'thread_suggestions': [t['text'] for t in spec.get('thread_suggestions', []) if t['status'] == 'pending'],
                      'threads': {k_: v_['callout'] for k_, v_ in spec.get('threads', {}).items()},
                      'gdt_suggestions': [f"{g_['id']}: " + (f"datum {g_['letter']} on {g_['target']}" if g_['type'] == 'datum'
                                          else f"[{gdt.fcf_text(spec, g_['fcf'], fmt)}] on {g_['fcf']['target']}") + ' - ' + g_['text']
                                          for g_ in spec.get('gdt_suggestions', []) if g_['status'] == 'pending'],
                      'gdt': {'datums': {d_['letter']: d_['target'] for d_ in spec.get('gdt', {}).get('datums', [])},
                              'frames': {f_['id']: f"[{gdt.fcf_text(spec, f_, fmt)}] on {f_['target']}" for f_ in spec.get('gdt', {}).get('fcfs', [])}},
                      'features': {k_: [f['id'] for f in spec['features'].get(k_, [])]
                                   for k_ in ('holes', 'cyls', 'slots', 'fillets', 'chamfers', 'bolt_circles', 'slopes')},
                      'dims': len(spec['dims'])}, indent=1, ensure_ascii=False))


def next_fcf_id(G):
    n = max([int(f['id'][1:]) for f in G.get('fcfs', []) if f.get('id', '').startswith('T')] + [0])
    return f'T{n + 1}'


def next_id(items, prefix):
    n = 0
    for it in items:
        i = it.get('id', '')
        if i.startswith(prefix) and i[len(prefix):].isdigit(): n = max(n, int(i[len(prefix):]))
    return f'{prefix}{n+1}'


def num_or_ref(x):
    try: return float(x)
    except (TypeError, ValueError): return x


ASM_BLOCKED = {'add-dim', 'add-diameter', 'redim', 'add-section', 'add-detail', 'accept-thread', 'reject-thread',
               'accept-gdt', 'reject-gdt', 'add-datum', 'add-fcf', 'dim-text', 'move-dim'}
PART_ONLY_ASM = {'bom-set', 'move-balloon', 'del-balloon', 'add-balloon'}


def cmd_edit(a):
    d = _asm_dir(a.dir); spec = read_spec(d)
    op = a.op
    if spec.get('kind') == 'exploded':
        if op in BALLOON_OPS or op == 'list':
            return edit_exploded(a, d, spec)
        d = os.path.join(os.path.dirname(d), 'assembly'); spec = read_spec(d)    # everything else lives on the assembly
    spec = asm_rebase(spec, d)
    is_asm = spec.get('kind') == 'assembly'
    if is_asm and op in ASM_BLOCKED: sys.exit(f'{op}: the assembly drawing has no dimensions; edit the part drawings under parts/')
    if not is_asm and (op in PART_ONLY_ASM or op in EXPLODE_OPS): sys.exit(f'{op} only works on an assembly directory')
    if is_asm and getattr(a, 'sheet', None) == 'exploded' and op in BALLOON_OPS:
        return edit_exploded(a, _edir(d), read_spec(_edir(d)))
    ex = spec.setdefault('exploded', {'on': False, 'scale': 1.0, 'overrides': {}}) if is_asm else {}
    dims = spec['dims']
    def getdim(i):
        for dm in dims:
            if dm['id'].upper() == i.upper(): return dm
        sys.exit(f'no dimension {i}')
    if op == 'add-dim':
        vw = next((v for v in spec['views'] if v['id'] == a.view), None)
        if not vw: sys.exit(f"no view {a.view}; views: {[v['id'] for v in spec['views']]}")
        ua_va = view_axes(spec, vw)
        if not ua_va or a.axis not in ua_va: sys.exit(f"axis {a.axis} is not in the plane of view {a.view} (has {ua_va})")
        side = a.side or default_side(spec, a.view, a.axis)
        same = [dm for dm in dims if dm['view'] == a.view and dm.get('side') == side and dm['kind'] == 'linear']
        off = a.offset if a.offset is not None else (max([dm.get('offset', 0) for dm in same] + [DIM_FIRST - DIM_STEP]) + DIM_STEP)
        dm = {'id': next_id(dims, 'D'), 'kind': 'linear', 'view': a.view, 'axis': a.axis, 'a': num_or_ref(a.frm),
              'b': num_or_ref(a.to), 'side': side, 'offset': off, 'auto': False}
        if side == 'inside': dm['at'] = float(a.at)
        if a.text: dm['text'] = a.text
        if a.tol: dm['tol'] = a.tol
        ref_value(spec, dm['a'], a.axis); ref_value(spec, dm['b'], a.axis)
        dims.append(dm); print('added', dm['id'])
    elif op == 'add-diameter':
        F = spec['features']; f = next((x for x in F['holes'] + F['cyls'] if x['id'] == a.feature.upper()), None)
        if not f: sys.exit(f'no hole/cylinder {a.feature}')
        vn = a.view or {'z': 'top', 'y': 'front', 'x': 'right'}[f['axis']]
        dm = {'id': next_id(dims, 'D'), 'kind': 'hole', 'view': vn, 'feature': f['id'], 'text': a.text or '%%c<>',
              'angle': a.angle, 'dist': 6, 'auto': False}
        dims.append(dm); print('added', dm['id'])
    elif op == 'add-leader':
        dm = {'id': next_id(dims, 'D'), 'kind': 'leader', 'view': a.view, 'text': a.text, 'angle': a.angle, 'dist': 8, 'auto': False}
        if a.feature: dm['feature'] = a.feature.upper()
        else: dm['point'] = [float(x) for x in a.point.split(',')]
        dims.append(dm); print('added', dm['id'])
    elif op == 'del-dim':
        for i in a.ids:
            dm = getdim(i); dims.remove(dm); print('deleted', dm['id'])
    elif op == 'move-dim':
        dm = getdim(a.id)
        if a.offset is not None: dm['offset'] = a.offset
        if a.delta is not None: dm['offset'] = dm.get('offset', DIM_FIRST) + a.delta
        if a.side: dm['side'] = a.side
        if a.angle is not None: dm['angle'] = a.angle
        if a.dist is not None: dm['dist'] = a.dist
        if a.at is not None: dm['at'] = a.at
        print('moved', dm['id'])
    elif op == 'dim-text':
        dm = getdim(a.id)
        if a.text is not None: dm['text'] = a.text
        if a.tol is not None: dm['tol'] = a.tol
        print('updated', dm['id'])
    elif op == 'add-note':
        spec['notes'].append(a.text); print('note', len(spec['notes']))
    elif op == 'del-note':
        spec['notes'].pop(a.n - 1); print('deleted note', a.n)
    elif op == 'set-note':
        spec['notes'][a.n - 1] = a.text
    elif op == 'set':
        for kv in a.pairs:
            key, val = kv.split('=', 1)
            if key in TITLE_FIELDS: spec['title'][key] = val
            elif key in ('sheet', 'scale', 'projection', 'units', 'sheet_family'):
                if key == 'sheet' and val not in SHEETS and val != 'auto': sys.exit(f'sheet must be one of {list(SHEETS)} or auto')
                spec[key] = val
                if key == 'projection':
                    for dm in dims:
                        if dm.get('auto') and dm['kind'] == 'linear' and dm.get('side') != 'inside' and dm['view'] in ('front', 'top', 'right'):
                            dm['side'] = default_side(spec, dm['view'], dm['axis'])
                if key == 'units' and 'decimals_fixed' not in spec:
                    spec['decimals'] = 3 if val == 'in' else 2
            elif key in ('decimals',): spec[key] = int(val)
            elif key in ('hidden_lines',): spec[key] = val.lower() in ('1', 'true', 'yes', 'on')
            elif key == 'tolerances': spec['tolerances'] = [x.strip() for x in val.split(';') if x.strip()]
            else: sys.exit(f'unknown field {key}')
    elif op == 'add-section':
        lab = a.label or next(c for c in 'ABCDEFGHJKLMNPRSTUVWXYZ' if c not in [v['id'] for v in spec['views']])
        try: at = float(a.at)
        except ValueError: at = ref_point(spec, a.at)['xyz'.index(a.axis)]
        par = a.parent or {'y': 'top', 'x': 'front', 'z': 'front'}[a.axis]
        spec['views'].append({'id': lab, 'type': 'section', 'axis': a.axis, 'at': at, 'parent': par})
        print('added section', lab)
    elif op == 'add-detail':
        import geom
        lab = a.label or next(c for c in 'ABCDEFGHJKLMNPRSTUVWXYZ' if c not in [v['id'] for v in spec['views']])
        par = next(v for v in spec['views'] if v['id'] == a.view)
        if ',' in a.at and a.at.count(',') == 1: c = [float(x) for x in a.at.split(',')]
        else: c = list(geom.project(ref_point(spec, a.at), view_frame_name(par)))
        spec['views'].append({'id': lab, 'type': 'detail', 'parent': a.view, 'center': c, 'radius': a.radius, 'factor': a.factor})
        print('added detail', lab)
    elif op == 'del-view':
        if a.id == 'front': sys.exit('front view cannot be removed')
        spec['views'] = [v for v in spec['views'] if v['id'] != a.id and v.get('parent') != a.id]
        spec['dims'] = [dm for dm in dims if dm['view'] in [v['id'] for v in spec['views']]]
    elif op == 'add-view':
        if a.id not in ('top', 'right', 'iso'): sys.exit('add-view supports top, right, iso')
        if not any(v['id'] == a.id for v in spec['views']): spec['views'].append({'id': a.id, 'type': a.id})
    elif op == 'break':
        b_ = spec.setdefault('break', {'on': 'auto'})
        if a.state: b_['on'] = {'on': True, 'off': False, 'auto': 'auto'}[a.state]
        if a.keep is not None:
            if a.keep <= 0: b_.pop('keep', None)
            else:
                b_['keep'] = a.keep * uf(spec)
                if not a.state: b_['on'] = True
        if a.axis: b_['axis'] = None if a.axis == 'auto' else a.axis
        print('break', b_)
    elif op == 'redim':
        cache = json.load(open(os.path.join(d, 'geom_cache.json')))
        manual = [dm for dm in dims if not dm.get('auto')]
        new = auto_dims(spec, d, cache)
        for dm in manual:
            dm['id'] = next_id(new, 'D'); new.append(dm)
        spec['dims'] = new
    elif op in ('accept-thread', 'reject-thread'):
        import threads
        hid = a.id.upper()
        sug = next((t for t in spec.get('thread_suggestions', []) if hid in t['holes']), None)
        if op == 'reject-thread':
            if not sug: sys.exit(f'no thread suggestion for {hid}')
            sug['status'] = 'rejected'
            for i in sug['holes']: spec.setdefault('threads', {}).pop(i, None)
            print('rejected', ','.join(sug['holes']))
        else:
            h = next((x for x in spec['features']['holes'] if x['id'] == hid), None)
            if not h: sys.exit(f'no hole {hid}')
            ids = sug['holes'] if sug else [hid]
            callout = a.callout or (sug['default_callout'] if sug else None)
            if not callout: sys.exit('no suggestion for this hole; pass --callout "M8x1.25-6H" (and --depth for blind holes)')
            opt = next((o for o in (sug['options'] if sug else []) if o['callout'] == callout), None)
            if a.depth is not None: depth = a.depth * uf(spec)
            elif h['thru']: depth = None
            elif opt: depth = threads.default_thread_depth(h['depth'], opt['pitch_mm'], opt['nominal_mm'], False)
            elif sug and callout == sug['default_callout']: depth = sug['default_thread_depth']
            else: depth = round(h['depth'] * 0.75 * 2) / 2
            nom = opt['nominal_mm'] if opt else None
            if nom is None:
                import re
                m = re.match(r'M(\d+(?:\.\d+)?)', callout)
                if m: nom = float(m.group(1))
            for i in ids:
                spec.setdefault('threads', {})[i] = {'callout': callout, 'depth': depth, 'nominal_mm': nom}
            if sug: sug['status'] = 'accepted'
            print('accepted', callout, 'on', ','.join(ids), '' if depth is None else f'thread depth {depth/uf(spec):g}')
    elif op in ('accept-gdt', 'reject-gdt'):
        gid = a.id.upper()
        sug = next((g_ for g_ in spec.get('gdt_suggestions', []) if g_['id'] == gid), None)
        if not sug: sys.exit(f'no GD&T suggestion {gid}')
        G = spec.setdefault('gdt', {'datums': [], 'fcfs': []})
        if op == 'reject-gdt':
            sug['status'] = 'rejected'
            if sug['type'] == 'datum':     # drop the letter from frames that are still pending
                for o in spec['gdt_suggestions']:
                    if o['type'] == 'fcf' and o['status'] == 'pending' and sug['letter'] in o['fcf']['datums']:
                        o['fcf']['datums'] = [L for L in o['fcf']['datums'] if L != sug['letter']]
                        o['text'] += f" (datum {sug['letter']} rejected, dropped from this frame)"
            print('rejected', gid)
        else:
            if sug['type'] == 'datum':
                G['datums'] = [x for x in G['datums'] if x['letter'] != sug['letter']] + [{'letter': sug['letter'], 'target': sug['target']}]
                print('accepted datum', sug['letter'], 'on', sug['target'])
            else:
                fc = copy.deepcopy(sug['fcf'])
                if a.value is not None: fc['value'] = a.value * uf(spec)
                fc['id'] = next_fcf_id(G); fc['from'] = gid
                G['fcfs'].append(fc)
                print('accepted', fc['id'], gdt.fcf_text(spec, fc, fmt), 'on', fc['target'])
            sug['status'] = 'accepted'
    elif op == 'add-datum':
        L = a.letter.upper()
        if len(L) != 1 or not L.isalpha() or L in 'IOQ': sys.exit('datum letter must be one letter, not I, O or Q')
        if not gdt.valid_target(spec, a.target): sys.exit(f'unknown target {a.target} (use zmin/zmax/xmin/..., z=12.5, or a feature id like H3)')
        G = spec.setdefault('gdt', {'datums': [], 'fcfs': []})
        G['datums'] = [x for x in G['datums'] if x['letter'] != L] + [{'letter': L, 'target': a.target.upper() if gdt.find_feature(spec, a.target) else a.target.lower()}]
        print('datum', L, 'on', a.target)
    elif op == 'add-fcf':
        if not gdt.valid_target(spec, a.target): sys.exit(f'unknown target {a.target} (use zmin/zmax/xmin/..., z=12.5, or a feature id like H3)')
        feat = gdt.find_feature(spec, a.target)
        dat = [x.strip().upper() for x in (a.datums or '').split(',') if x.strip()]
        if a.type in gdt.NO_DATUM and dat: sys.exit(f'{a.type} takes no datums')
        if a.type in gdt.NEEDS_DATUM and not dat: sys.exit(f'{a.type} needs --datums (e.g. A,B,C)')
        is_axis = bool(feat) and feat['type'] in ('hole', 'cyl', 'bolt_circle', 'bc')
        dia = a.dia if a.dia is not None else (is_axis and a.type in ('position', 'perpendicularity', 'parallelism', 'concentricity'))
        if a.mmc and not (feat and feat['type'] in ('hole', 'cyl', 'slot')): sys.exit('--mmc only applies to features of size (holes, bosses, slots)')
        group = None
        if feat and feat['type'] == 'hole':
            dm = next((x for x in spec['dims'] if x['kind'] == 'hole' and feat['id'] in (x.get('group') or [])), None)
            group = dm['group'] if dm else [feat['id']]
        G = spec.setdefault('gdt', {'datums': [], 'fcfs': []})
        fc = {'type': a.type, 'value': a.value * uf(spec), 'dia': bool(dia), 'mmc': bool(a.mmc), 'datums': dat,
              'target': feat['id'] if feat else a.target.lower(), 'group': group, 'id': next_fcf_id(G)}
        G['fcfs'].append(fc)
        print('added', fc['id'], gdt.fcf_text(spec, fc, fmt), 'on', fc['target'])
    elif op == 'del-gdt':
        G = spec.setdefault('gdt', {'datums': [], 'fcfs': []})
        key = a.id.upper(); n0 = len(G['datums']) + len(G['fcfs'])
        gone = [f.get('from') for f in G['fcfs'] if f['id'] == key] + \
               [g_['id'] for g_ in spec.get('gdt_suggestions', []) if g_['type'] == 'datum' and g_['letter'] == key]
        for g_ in spec.get('gdt_suggestions', []):
            if g_['id'] in gone and g_['status'] == 'accepted': g_['status'] = 'deleted'
        G['fcfs'] = [f for f in G['fcfs'] if f['id'] != key]
        G['datums'] = [x for x in G['datums'] if x['letter'] != key]
        if len(G['datums']) + len(G['fcfs']) == n0: sys.exit(f'no frame or datum {key}')
        print('deleted', key)
    elif op == 'bom-set':
        b = next((x for x in spec['bom'] if x['item'] == a.item), None)
        if not b: sys.exit(f'no BOM item {a.item}')
        kv = {}
        for pair in a.pairs:
            if '=' not in pair: sys.exit(f'expected key=value, got {pair}')
            k_, v_ = pair.split('=', 1); k_ = k_.strip().lower().replace('part_no', 'part_number')
            if k_ not in ('part_number', 'description', 'material', 'finish', 'qty', 'drawing'): sys.exit(f'unknown BOM key {k_}')
            kv[k_] = v_
        for k_, v_ in kv.items():
            if k_ == 'drawing': b['drawing'] = v_.strip().lower() in ('yes', 'y', 'true', '1')
            elif k_ == 'qty': b['qty'] = int(v_)
            elif k_ == 'description': b['description'] = v_.upper()
            else: b[k_] = v_
        if b['drawing']:
            pd = b.get('part_dir')
            if not pd or not os.path.exists(os.path.join(pd, 'spec.json')):
                make_part(spec, b); print('made part drawing', b['part_dir'])
            else:
                ps = read_spec(pd)
                ps['title'].update({'part_number': b['part_number'], 'material': b['material'], 'finish': b['finish'],
                                    'part_name': b['description'], 'quantity': b['qty']})
                render(ps, pd); print('updated part drawing', pd)
        print('item', a.item, {k_: b[k_] for k_ in ('part_number', 'description', 'material', 'finish', 'qty', 'drawing')})
    elif op in ('add-balloon', 'move-balloon', 'del-balloon'):
        if not any(x['item'] == a.item for x in spec['bom']): sys.exit(f'no BOM item {a.item}')
        bl = spec.setdefault('balloons', [])
        cur = [x for x in bl if x['item'] == a.item and (a.view is None or x['view'] == a.view)]
        if op == 'del-balloon':
            if not cur: sys.exit(f'no balloon for item {a.item}')
            spec['balloons'] = [x for x in bl if x not in cur]; print('deleted balloon', a.item)
        elif op == 'add-balloon':
            vw = a.view or 'iso'
            q = spec.get('anchors', {}).get(vw, {}).get(str(a.item))
            if not q: sys.exit(f'item {a.item} is not visible in view {vw}; try another --view (front/top/iso)')
            bl.append({'item': a.item, 'view': vw, 'anchor': q}); print('balloon', a.item, 'on', vw)
        else:
            if not cur: sys.exit(f'no balloon for item {a.item}')
            for x in cur:
                if a.angle is not None: x['angle'] = a.angle
                if a.dist is not None: x['dist'] = a.dist
                if a.reset: x.pop('angle', None); x.pop('dist', None)
            print('moved balloon', a.item)
    elif op == 'exploded':
        ex['on'] = a.state == 'on'; print('exploded sheet', a.state)
    elif op == 'explode':
        if not any(x['item'] == a.item for x in spec['bom']): sys.exit(f'no BOM item {a.item}')
        key = f'{a.item}.{a.inst}' if a.inst else str(a.item)
        ov = ex.setdefault('overrides', {})
        if a.reset:
            for k_ in [k_ for k_ in ov if k_ == str(a.item) or k_.startswith(f'{a.item}.')]: ov.pop(k_)
        else:
            o = ov.setdefault(key, {})
            if a.xdir:
                import explode as X
                try: X.parse_dir(a.xdir)
                except ValueError as e: sys.exit(str(e))
                o['dir'] = a.xdir.lower()
            if a.dist is not None: o['dist'] = a.dist
        if not ex.get('on'): ex['on'] = True; print('exploded sheet turned on')
        print('explode', key, ov.get(key, 'auto'))
    elif op == 'explode-scale':
        if a.factor <= 0: sys.exit('factor must be > 0')
        ex['scale'] = a.factor
        if not ex.get('on'): ex['on'] = True; print('exploded sheet turned on')
        print('explode scale', a.factor)
    elif op == 'add-rev':
        revs = revisions(spec)
        if any(r_['rev'] == a.rev for r_ in revs): sys.exit(f'rev {a.rev} already in the table (use set-rev)')
        revs.append({'rev': a.rev, 'desc': a.desc.upper(), 'date': a.date or datetime.date.today().isoformat(),
                     'by': a.by if a.by is not None else spec['title'].get('drawn_by', '')})
        spec['title']['revision'] = a.rev
        print('rev', a.rev, 'added; title block REV is now', a.rev)
    elif op == 'del-rev':
        revs = revisions(spec)
        if not any(r_['rev'] == a.rev for r_ in revs): sys.exit(f'no rev {a.rev}')
        if len(revs) == 1: sys.exit('cannot delete the only revision row; use set-rev')
        spec['revisions'] = [r_ for r_ in revs if r_['rev'] != a.rev]
        spec['title']['revision'] = spec['revisions'][-1]['rev']
        print('deleted rev', a.rev, '; title block REV is', spec['title']['revision'])
    elif op == 'set-rev':
        r_ = next((x for x in revisions(spec) if x['rev'] == a.rev), None)
        if not r_: sys.exit(f'no rev {a.rev}')
        if a.new_rev:
            if spec['title'].get('revision') == r_['rev']: spec['title']['revision'] = a.new_rev
            r_['rev'] = a.new_rev
        if a.desc is not None: r_['desc'] = a.desc.upper()
        if a.date is not None: r_['date'] = a.date
        if a.by is not None: r_['by'] = a.by
        print('rev row', r_)
    elif op == 'list' and is_asm:
        print(json.dumps({'bom': spec['bom'], 'balloons': spec['balloons'], 'revisions': revisions(spec), 'notes': spec['notes'],
                          'title': spec['title'], 'exploded': spec.get('exploded'), 'sheet_no': spec.get('sheet_no')},
                         indent=1, ensure_ascii=False))
        return
    elif op == 'list':
        print(json.dumps({'views': spec['views'], 'revisions': revisions(spec), 'dims': spec['dims'], 'notes': spec['notes'], 'title': spec['title'],
                          'thread_suggestions': spec.get('thread_suggestions', []), 'threads': spec.get('threads', {}),
                          'gdt_suggestions': spec.get('gdt_suggestions', []), 'gdt': spec.get('gdt', {}),
                          'features': spec['features'], 'resolved': spec.get('resolved')}, indent=1, ensure_ascii=False))
        return
    spec = render(spec, d)
    if is_asm:
        if ex.get('on'):
            if op in EXPLODE_OPS or not os.path.exists(os.path.join(_edir(d), 'spec.json')) or op in ('bom-set', 'break'):
                build_exploded(spec, d)
            else:
                render(read_spec(_edir(d)), _edir(d))
        spec = number_sheets(spec, d)
        asm_report(spec, os.path.dirname(d))
    else: report(spec, d)


def edit_exploded(a, d, spec):
    """Balloon ops on the exploded sheet (other ops are redirected to the assembly)."""
    op = a.op
    if op == 'list':
        print(json.dumps({'balloons': spec['balloons'], 'balloon_off': spec.get('balloon_off', []), 'trails': len(spec.get('trails', [])),
                          'plan': read_spec(os.path.join(os.path.dirname(d), 'assembly')).get('exploded', {}).get('plan')},
                         indent=1, ensure_ascii=False)); return
    if a.view not in (None, 'iso'): sys.exit('the exploded sheet has only the iso view')
    if not any(x['item'] == a.item for x in spec['bom']): sys.exit(f'no BOM item {a.item}')
    bl = spec.setdefault('balloons', []); off = spec.setdefault('balloon_off', [])
    cur = [x for x in bl if x['item'] == a.item]
    if op == 'del-balloon':
        if not cur: sys.exit(f'no balloon for item {a.item}')
        spec['balloons'] = [x for x in bl if x['item'] != a.item]
        if a.item not in off: off.append(a.item)
    elif op == 'add-balloon':
        q = spec.get('anchors', {}).get('iso', {}).get(str(a.item))
        if not q: sys.exit(f'item {a.item} is not visible in the exploded view')
        if a.item in off: off.remove(a.item)
        if not cur: bl.append({'item': a.item, 'view': 'iso', 'anchor': q})
    else:
        if not cur: sys.exit(f'no balloon for item {a.item}')
        for x in cur:
            if a.angle is not None: x['angle'] = a.angle
            if a.dist is not None: x['dist'] = a.dist
            if a.reset: x.pop('angle', None); x.pop('dist', None)
    print(op, a.item, 'on the exploded sheet')
    es = render(spec, d)
    print(json.dumps({'ok': True, 'preview': os.path.join(d, 'preview.png'), 'audit_errors': es['resolved']['audit_errors'],
                      'balloons': es['balloons']}, indent=1))


def cmd_render(a):
    d = _asm_dir(a.dir); spec = render(asm_rebase(read_spec(d), d), d)
    if spec.get('kind') == 'assembly':
        if spec.get('exploded', {}).get('on'):
            if os.path.exists(os.path.join(_edir(d), 'spec.json')): render(read_spec(_edir(d)), _edir(d))
            else: build_exploded(spec, d)
        asm_report(number_sheets(spec, d), os.path.dirname(d))
    else: report(spec, d)


def cmd_pdf(a):
    d = _asm_dir(a.dir)
    if read_spec(d).get('kind') == 'assembly':
        return asm_pdf(a, d)
    pdf_one(d, a.out)


def asm_pdf(a, d):
    """Assembly: one PDF per sheet plus a combined PDF (assembly sheet first, then each part drawing by item)."""
    import contextlib, io, subprocess
    spec = asm_rebase(read_spec(d), d); root = os.path.dirname(d)
    pdfs = [pdf_one(dd, None, quiet=True) for dd in sheet_dirs(spec, d)]
    pn = spec['title'].get('part_number') or 'assembly'
    out = a.out or os.path.join(root, f'{_slug(pn)}-drawings.pdf')
    try:
        from pypdf import PdfWriter
        w = PdfWriter()
        for p_ in pdfs: w.append(p_)
        with open(out, 'wb') as f: w.write(f)
    except ImportError:
        subprocess.run(['pdfunite'] + pdfs + [out], check=True)
    print(json.dumps({'ok': True, 'combined_pdf': out, 'pdfs': pdfs, 'pages': len(pdfs)}, indent=1))


def pdf_one(d, out=None, quiet=False):
    import ezdxf
    spec = read_spec(d)
    doc = ezdxf.readfile(os.path.join(d, 'drawing.dxf'))
    sheet = spec['resolved']['sheet']; s = parse_scale(spec['resolved']['scale']); k = 1/(s*uf(spec))
    PW, PH = SHEETS[sheet]
    out = out or os.path.join(d, 'drawing.pdf')
    write_png(doc, out, PW*k, PH*k, PW, PH)
    if not quiet: print(json.dumps({'ok': True, 'pdf': out, 'sheet': sheet, 'scale': spec['resolved']['scale']}))
    return out


def main():
    if sys.argv[1:2] == ['photo']:     # photo mode: part spec -> STEP (photospec.py); the drawing step is unchanged
        import photospec
        return photospec.main(sys.argv[2:], sys.modules[__name__])
    ap = argparse.ArgumentParser(prog='epure')
    sp = ap.add_subparsers(dest='cmd', required=True)
    m = sp.add_parser('make'); m.add_argument('model'); m.add_argument('--config'); m.add_argument('--fields')
    m.add_argument('--out', required=True); m.add_argument('--model-units', default='mm', choices=['mm', 'in'],
                                                            help='units of STL/OBJ/BREP files (STEP/IGES carry their own)')
    m.set_defaults(fn=cmd_make)
    r = sp.add_parser('render'); r.add_argument('dir'); r.set_defaults(fn=cmd_render)
    for nm, fn in (('assembly-scan', cmd_assembly_scan), ('assembly', cmd_assembly)):
        m = sp.add_parser(nm); m.add_argument('model'); m.add_argument('--config'); m.add_argument('--fields')
        m.add_argument('--out', required=True)
        if nm == 'assembly':
            m.add_argument('--items', help='items.json from assembly-scan, edited with the user\'s choices')
            m.add_argument('--exploded', action='store_true', help='also draw the exploded-view sheet (sheet 2)')
        m.set_defaults(fn=fn)
    sp.add_parser('photo', help='photo mode: epure photo check|build|mark|scale ... (see README)')
    p = sp.add_parser('pdf'); p.add_argument('dir'); p.add_argument('--out'); p.set_defaults(fn=cmd_pdf)
    e = sp.add_parser('edit'); e.add_argument('dir'); es = e.add_subparsers(dest='op', required=True)
    x = es.add_parser('add-dim'); x.add_argument('--view', required=True); x.add_argument('--axis', required=True, choices=list('xyz'))
    x.add_argument('--from', dest='frm', required=True); x.add_argument('--to', required=True)
    x.add_argument('--side', choices=['below', 'above', 'left', 'right', 'inside']); x.add_argument('--offset', type=float)
    x.add_argument('--at', type=float); x.add_argument('--text'); x.add_argument('--tol')
    x = es.add_parser('add-diameter'); x.add_argument('--feature', required=True); x.add_argument('--view'); x.add_argument('--text')
    x.add_argument('--angle', type=float, default=45)
    x = es.add_parser('add-leader'); x.add_argument('--view', required=True); x.add_argument('--text', required=True)
    x.add_argument('--feature'); x.add_argument('--point'); x.add_argument('--angle', type=float, default=45)
    x = es.add_parser('del-dim'); x.add_argument('ids', nargs='+')
    x = es.add_parser('move-dim'); x.add_argument('id'); x.add_argument('--offset', type=float); x.add_argument('--delta', type=float)
    x.add_argument('--side', choices=['below', 'above', 'left', 'right', 'inside']); x.add_argument('--angle', type=float)
    x.add_argument('--dist', type=float); x.add_argument('--at', type=float)
    x = es.add_parser('dim-text'); x.add_argument('id'); x.add_argument('--text'); x.add_argument('--tol')
    x = es.add_parser('add-note'); x.add_argument('text')
    x = es.add_parser('del-note'); x.add_argument('n', type=int)
    x = es.add_parser('set-note'); x.add_argument('n', type=int); x.add_argument('text')
    x = es.add_parser('set'); x.add_argument('pairs', nargs='+')
    x = es.add_parser('add-section'); x.add_argument('--axis', required=True, choices=list('xyz')); x.add_argument('--at', required=True)
    x.add_argument('--label'); x.add_argument('--parent')
    x = es.add_parser('add-detail'); x.add_argument('--view', required=True); x.add_argument('--at', required=True)
    x.add_argument('--radius', type=float, required=True); x.add_argument('--factor', type=float, default=2); x.add_argument('--label')
    x = es.add_parser('del-view'); x.add_argument('id')
    x = es.add_parser('add-view'); x.add_argument('id')
    x = es.add_parser('accept-thread'); x.add_argument('id'); x.add_argument('--callout'); x.add_argument('--depth', type=float,
                                                                                                    help='thread depth in drawing units')
    x = es.add_parser('reject-thread'); x.add_argument('id')
    x = es.add_parser('accept-gdt'); x.add_argument('id'); x.add_argument('--value', type=float, help='tolerance in drawing units')
    x = es.add_parser('reject-gdt'); x.add_argument('id')
    x = es.add_parser('add-datum'); x.add_argument('target'); x.add_argument('letter')
    x = es.add_parser('add-fcf'); x.add_argument('target')
    x.add_argument('--type', required=True, choices=gdt.TYPES); x.add_argument('--value', type=float, required=True)
    x.add_argument('--datums'); x.add_argument('--mmc', action='store_true')
    x.add_argument('--dia', dest='dia', action='store_true', default=None); x.add_argument('--no-dia', dest='dia', action='store_false')
    x = es.add_parser('del-gdt'); x.add_argument('id', help='frame id T# or datum letter')
    x = es.add_parser('add-rev'); x.add_argument('--rev', required=True); x.add_argument('--desc', required=True)
    x.add_argument('--by'); x.add_argument('--date')
    x = es.add_parser('del-rev'); x.add_argument('rev')
    x = es.add_parser('set-rev'); x.add_argument('rev'); x.add_argument('--new-rev', dest='new_rev'); x.add_argument('--desc')
    x.add_argument('--by'); x.add_argument('--date')
    x = es.add_parser('bom-set'); x.add_argument('item', type=int); x.add_argument('pairs', nargs='+', help='key=value')
    x = es.add_parser('exploded'); x.add_argument('state', choices=['on', 'off'])
    x = es.add_parser('explode'); x.add_argument('item', type=int); x.add_argument('--dir', dest='xdir', help='x|y|z|-x|-y|-z')
    x.add_argument('--dist', type=float, help='model mm (not scaled by explode-scale)')
    x.add_argument('--inst', type=int, help='only this copy of the item (1-based, see list plan)'); x.add_argument('--reset', action='store_true')
    x = es.add_parser('explode-scale'); x.add_argument('factor', type=float)
    for nm in ('add-balloon', 'move-balloon', 'del-balloon'):
        x = es.add_parser(nm); x.add_argument('item', type=int); x.add_argument('--view')
        x.add_argument('--sheet', choices=['assembly', 'exploded'], default='assembly')
        if nm == 'move-balloon':
            x.add_argument('--angle', type=float, help='direction from the view centre, degrees (0 = right, 90 = up)')
            x.add_argument('--dist', type=float, help='extra distance out, paper mm'); x.add_argument('--reset', action='store_true')
    x = es.add_parser('break', help='broken view for long parts: on|off|auto, --keep shown length, --axis')
    x.add_argument('state', nargs='?', choices=['on', 'off', 'auto'])
    x.add_argument('--keep', type=float, help='length to show along the long axis, drawing units (0 = automatic)')
    x.add_argument('--axis', choices=['x', 'y', 'z', 'auto'])
    es.add_parser('redim'); es.add_parser('list')
    e.set_defaults(fn=cmd_edit)
    a = ap.parse_args(); a.fn(a)


if __name__ == '__main__':
    main()
