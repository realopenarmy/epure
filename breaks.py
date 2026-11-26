"""Épure broken views: long, thin parts and assemblies are drawn shortened.

A plan picks the long model axis and one or two bands [a, b] (model mm along that axis) to cut out: either a plain
stretch with no geometry in it, or the middle of an evenly spaced hole pattern (first and last hole stay visible).
Each view whose plane contains the axis gets a ViewMap: geometry inside a band is clipped away and everything past it
slides back so the two ends sit a small gap apart, with break lines drawn on both sides. Dimensions keep their true
values (epure.draw_dim scales the measured length back with dimlfac)."""
import math

RATIO = 8.0            # long/cross extent above which `break auto` shortens the views
MAX_BANDS = 2


def _vec(ax): return [(1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)]['xyz'.index(ax)]


def _turn_points(pts):
    """Endpoints plus the points where a polyline changes direction (straight runs add no events)."""
    if len(pts) <= 2: return list(pts)
    out = [pts[0]]
    for a, b, c in zip(pts, pts[1:], pts[2:]):
        u = (b[0] - a[0], b[1] - a[1]); v = (c[0] - b[0], c[1] - b[1])
        lu, lv = math.hypot(*u), math.hypot(*v)
        if lu < 1e-12 or lv < 1e-12: continue
        if abs(u[0] * v[1] - u[1] * v[0]) / (lu * lv) > 0.03: out.append(b)
    out.append(pts[-1])
    return out


def _events(spec, G, ax):
    """Sorted coordinates along the axis where the drawing changes (edges end, holes, steps...)."""
    import geom
    A = _vec(ax); ai = 'xyz'.index(ax)
    ev = []
    for v in spec['views']:
        if v['type'] not in ('front', 'top', 'right') or v['id'] not in G: continue
        d = geom.project(A, v['type'])
        idx = 0 if abs(abs(d[0]) - 1) < 1e-6 else 1 if abs(abs(d[1]) - 1) < 1e-6 else None
        if idx is None: continue
        sg = d[idx]
        for kind in ('visible', 'hidden'):
            for p in G[v['id']][kind]:
                if p['t'] in ('line', 'poly'): pts = _turn_points(p['p'])
                elif p['t'] == 'circle':
                    c = p['c']; r = p['r']; pts = [[c[0] - r, c[1] - r], [c[0] + r, c[1] + r]]
                else: pts = geom.prim_points(p, 16)
                ev += [q[idx] * sg for q in pts]
    F = spec.get('features') or {}
    for f in F.get('holes', []) + F.get('cyls', []):
        if f['axis'] == ax: ev += [f['lo'], f['hi']]
        else: ev += [f['center'][ai] - f['dia'] / 2, f['center'][ai] + f['dia'] / 2]
    for sl in F.get('slots', []):
        for c in (sl['c1'], sl['c2']): ev += [c[ai] - sl['width'] / 2, c[ai] + sl['width'] / 2]
    ev.sort()
    out = []
    for e in ev:
        if not out or e - out[-1] > 1e-4: out.append(e)
    return out


def _patterns(spec, ax):
    """Evenly spaced rows of identical holes along the axis: [{'ids', 'centers', 'pitch'}] (4+ holes)."""
    ai = 'xyz'.index(ax)
    rows = {}
    for f in (spec.get('features') or {}).get('holes', []):
        if f['axis'] == ax: continue
        key = (f['axis'], round(f['dia'], 3)) + tuple(round(f['center'][i], 2) for i in range(3) if i != ai)
        rows.setdefault(key, []).append(f)
    out = []
    for fs in rows.values():
        if len(fs) < 4: continue
        fs.sort(key=lambda f: f['center'][ai])
        cs = [f['center'][ai] for f in fs]
        p = (cs[-1] - cs[0]) / (len(cs) - 1)
        if p > 1e-6 and all(abs((cs[i + 1] - cs[i]) - p) < max(1e-3, 1e-4 * p) for i in range(len(cs) - 1)):
            out.append({'ids': [f['id'] for f in fs], 'centers': cs, 'pitch': p})
    return out


def _periodic(ev, L, cross, min_cut):
    """Bands inside stretches where the geometry just repeats (rails with a hole/pocket every pitch, ladders...):
    found from the event coordinates alone, so it also works for assemblies (no feature list there)."""
    import bisect
    if len(ev) < 8: return []
    q = 0.05
    cnt = {}
    for i in range(len(ev)):
        for j in range(i + 1, len(ev)):
            d = ev[j] - ev[i]
            if d > L / 3: break
            if d < max(0.02 * L, 0.5 * cross): continue
            k = round(d / q); cnt[k] = cnt.get(k, 0) + 1
    if not cnt: return []
    best = []
    for k in sorted(cnt, key=lambda k_: -cnt[k_])[:12]:     # try the commonest spacings, keep the one that cuts most
        if cnt[k] < 6: break
        bands = _periodic_runs(ev, k * q, q, min_cut)
        if sum(b['b'] - b['a'] for b in bands) > sum(b['b'] - b['a'] for b in best): best = bands
    return best


def _periodic_runs(ev, p, q, min_cut):
    import bisect
    def has(x):
        i = bisect.bisect_left(ev, x - 2 * q)
        return i < len(ev) and ev[i] <= x + 2 * q
    per = [has(e + p) or has(e - p) for e in ev]
    stops = [ev[0]] + [e for e, f in zip(ev, per) if not f] + [ev[-1]]
    out = []
    for x0, x1 in zip(stops, stops[1:]):
        if x1 - x0 < 2 * p + min_cut: continue
        inner = [e for e in ev if x0 <= e <= x0 + p]          # cut in the widest empty spot of the first period
        gaps = [(b - a, (a + b) / 2) for a, b in zip(inner, inner[1:])]
        if not gaps: continue
        gw, a_ = max(gaps)
        if gw < 0.05 * p: continue
        n = math.floor((x1 - a_) / p)
        while n > 0:                                            # same spot, as many periods on as the run allows
            b_ = a_ + n * p
            if b_ < x1 and not any(abs(e - b_) < gw / 4 for e in ev[bisect.bisect_left(ev, b_ - gw):bisect.bisect_right(ev, b_ + gw)]):
                break
            n -= 1
        if n > 0 and n * p >= min_cut: out.append({'a': a_, 'b': a_ + n * p, 'kind': 'periodic', 'pitch': p})
    return out


def plan(spec, G):
    """-> dict(axis, bands, gap, length, shown, cross, pattern) or None when the views stay whole."""
    c = spec.get('break') or {}
    on = c.get('on', 'auto')
    if on in (False, 'off'): return None
    lo, hi = spec['features']['bbox']
    ext = [hi[i] - lo[i] for i in range(3)]
    ai = 'xyz'.index(c['axis']) if c.get('axis') in ('x', 'y', 'z') else max(range(3), key=lambda i: ext[i])
    ax = 'xyz'[ai]; L = ext[ai]
    cross = max(ext[i] for i in range(3) if i != ai)
    ratio = L / max(cross, 1e-9)
    if (on == 'auto' and ratio < RATIO) or ratio < 2: return None
    ev = _events(spec, G, ax)
    if len(ev) < 2: return None
    target = c.get('keep') or max(4.0 * cross, 0.08 * L)
    margin = max(0.75 * cross, 0.01 * L)          # plain material kept next to any geometry
    min_cut = max(2.0 * cross, 0.08 * L)          # not worth a break for less
    cands = []
    for pt in _patterns(spec, ax):
        cs, p = pt['centers'], pt['pitch']
        # cut just past the first/last hole's own geometry (pockets, flats...), not a whole half pitch later
        m2 = max(0.5 * cross, 0.1 * p)
        a_ = min(max([e for e in ev if cs[0] <= e < cs[0] + p / 2] + [cs[0]]) + m2, cs[0] + p / 2)
        b_ = max(min([e for e in ev if cs[-1] - p / 2 < e <= cs[-1]] + [cs[-1]]) - m2, cs[-1] - p / 2)
        if b_ - a_ < min_cut: continue
        ref = [((e - cs[0] + p / 2) % p) - p / 2 for e in ev if cs[0] - p / 2 <= e < cs[0] + p / 2]
        ok = all(any(abs(((e - cs[0] + p / 2) % p) - p / 2 - r) < 1e-3 for r in ref) for e in ev if a_ < e < b_)
        ok = ok and not any(abs(e - a_) < 0.02 * p or abs(e - b_) < 0.02 * p for e in ev)
        ok = ok and not any(a_ < e < b_ for e in (cs[0], cs[-1]))
        if ok: cands.append({'a': a_, 'b': b_, 'kind': 'pattern', 'pattern': pt})
    if spec.get('kind') in ('assembly', 'exploded'):     # parts use the hole pattern (its dims need the hole ids)
        cands += _periodic(ev, L, cross, min_cut)
    for e0, e1 in zip(ev, ev[1:]):
        if e1 - e0 - 2 * margin >= min_cut: cands.append({'a': e0 + margin, 'b': e1 - margin, 'kind': 'plain'})
    cands.sort(key=lambda x: -(x['b'] - x['a']))
    bands, removed, pattern = [], 0.0, None
    for cd in cands:
        if len(bands) >= MAX_BANDS or L - removed <= target: break
        if any(cd['a'] < b and cd['b'] > a for a, b in bands): continue
        a_, b_ = cd['a'], cd['b']
        if cd['kind'] == 'plain' and (b_ - a_) > (L - removed - target):
            w = max(L - removed - target, min_cut)
            if w > b_ - a_: continue
            m_ = (a_ + b_) / 2; a_, b_ = m_ - w / 2, m_ + w / 2
        elif c.get('keep') and cd['kind'] in ('pattern', 'periodic'):   # asked to show more: give back whole pitches
            p = cd['pattern']['pitch'] if cd['kind'] == 'pattern' else cd['pitch']
            n = math.floor(((b_ - a_) - (L - removed - target)) / p)
            n = min(max(n, 0), math.floor((b_ - a_ - min_cut) / p))
            if n > 0:
                a_ += p * (n // 2); b_ -= p * (n - n // 2)
        bands.append((a_, b_)); removed += b_ - a_
        if cd['kind'] == 'pattern': pattern = cd['pattern']
    if not bands: return None
    bands.sort()
    shown = L - removed
    gap = max(0.03 * shown, 0.02 * cross)
    return {'axis': ax, 'bands': [[a, b] for a, b in bands], 'gap': gap, 'length': L, 'shown': shown + gap * len(bands),
            'cross': cross, 'pattern': pattern, 'bbox': [lo, hi]}


def in_band(brk, c):
    return any(a < c < b for a, b in brk['bands'])


class ViewMap:
    """2D map for one view: t = coordinate along the projected axis; bands in t are cut out and later t slides back."""

    def __init__(self, brk, frame):
        import geom
        self.on = False
        A = _vec(brk['axis']); ai = 'xyz'.index(brk['axis'])
        d = geom.project(A, frame); dl = math.hypot(*d)
        if dl < 0.3: return
        N = geom.frame(frame)[0]
        an = sum(A[i] * N[i] for i in range(3))
        self.dh = (d[0] / dl, d[1] / dl); self.nh = (-self.dh[1], self.dh[0])
        # off-axis parts of the model shift t a little (oblique views): estimate the spread from the bbox corners
        lo, hi = brk['bbox']
        errs = []
        for x in (lo[0], hi[0]):
            for y in (lo[1], hi[1]):
                for z in (lo[2], hi[2]):
                    P = (x, y, z); cc = P[ai]
                    errs.append(-an * (sum(P[i] * N[i] for i in range(3)) - cc * an) / dl)
        em, E = (max(errs) + min(errs)) / 2, (max(errs) - min(errs)) / 2
        self.g = brk['gap'] * dl
        self.bands = []
        for a, b in brk['bands']:
            ta, tb = a * dl + em + E, b * dl + em - E
            if tb - ta > self.g: self.bands.append((ta, tb))
        self.on = bool(self.bands)

    def t(self, q): return q[0] * self.dh[0] + q[1] * self.dh[1]
    def w(self, q): return q[0] * self.nh[0] + q[1] * self.nh[1]

    def shift(self, t):
        s = 0.0
        for ta, tb in self.bands:       # (clipped ends sit on the band edges up to rounding)
            if t >= tb - 1e-7 * max(1.0, abs(tb)): s += (tb - ta) - self.g
            elif t > ta + 1e-7 * max(1.0, abs(ta)): s += t - ta; break
        return s

    def map(self, u, v):
        s = self.shift(self.t((u, v)))
        return (u - s * self.dh[0], v - s * self.dh[1])

    def inband(self, u, v):
        t = self.t((u, v)); return any(ta < t < tb for ta, tb in self.bands)

    def _clip_pts(self, pts, cuts):
        """Pieces of a polyline outside every band (unmapped coordinates); records the cut points."""
        bnd = sorted([x for ab in self.bands for x in ab])
        pieces, cur = [], []
        def inside(t): return any(ta < t < tb for ta, tb in self.bands)
        for p0, p1 in zip(pts, pts[1:]):
            t0, t1 = self.t(p0), self.t(p1)
            ts = [0.0] + sorted((x - t0) / (t1 - t0) for x in bnd if abs(t1 - t0) > 1e-12 and 0 < (x - t0) / (t1 - t0) < 1) + [1.0]
            for f0, f1 in zip(ts, ts[1:]):
                q0 = (p0[0] + (p1[0] - p0[0]) * f0, p0[1] + (p1[1] - p0[1]) * f0)
                q1 = (p0[0] + (p1[0] - p0[0]) * f1, p0[1] + (p1[1] - p0[1]) * f1)
                if inside((self.t(q0) + self.t(q1)) / 2):
                    if len(cur) >= 2: pieces.append(cur)
                    if cur: self._cut(cur[-1], cuts)
                    cur = []
                    continue
                if not cur:
                    cur = [list(q0)]; self._cut(q0, cuts)
                cur.append(list(q1))
        if len(cur) >= 2: pieces.append(cur)
        return pieces

    def _cut(self, q, cuts):
        t = self.t(q)
        for i, (ta, tb) in enumerate(self.bands):
            if abs(t - ta) < 1e-6 * max(1, abs(ta)): cuts.append((i, 0, self.w(q))); return
            if abs(t - tb) < 1e-6 * max(1, abs(tb)): cuts.append((i, 1, self.w(q))); return

    def clip(self, prims, cuts):
        import geom
        out = []
        for p in prims:
            if p['t'] in ('circle', 'arc'):
                tc = self.t(p['c'])
                if not any(tc + p['r'] > ta and tc - p['r'] < tb for ta, tb in self.bands):
                    out.append(p); continue
                pts = geom.prim_points(p, 96)
            else:
                pts = p['p']
            for pc in self._clip_pts(pts, cuts):
                out.append({'t': 'poly', 'p': pc} if len(pc) > 2 else {'t': 'line', 'p': pc})
        return out

    def mapped_bbox(self, prims):
        import geom
        mp = []
        for p in prims:
            q = dict(p)
            if p['t'] in ('circle', 'arc'): q['c'] = list(self.map(*p['c']))
            else: q['p'] = [list(self.map(*x)) for x in p['p']]
            mp.append(q)
        return geom.prims_bbox(mp)

    def break_lines(self, cuts, scale):
        """Long-break symbols (lists of mapped 2D points) at both edges of every band; scale = paper mm per model mm."""
        out = []
        oh, zw, amp, tol = 2.0 / scale, 2.2 / scale, 1.6 / scale, 10.0 / scale   # separate symbols only for parts far apart
        for i, (ta, tb) in enumerate(self.bands):
            for side, tt in ((0, ta), (1, tb)):
                ws = sorted(w for (j, sd, w) in cuts if j == i and sd == side)
                if not ws: continue
                groups = [[ws[0], ws[0]]]
                for w in ws[1:]:
                    if w - groups[-1][1] > tol: groups.append([w, w])
                    else: groups[-1][1] = w
                tm = tt - self.shift(tt)          # where this band edge lands once the far side slid back
                for w0, w1 in groups:
                    wm = (w0 + w1) / 2; z = min(zw, max((w1 - w0) / 3, zw / 2))
                    pts_tw = [(tm, w0 - oh), (tm, wm - z), (tm + amp, wm - z / 3), (tm - amp, wm + z / 3), (tm, wm + z), (tm, w1 + oh)]
                    out.append([(t_ * self.dh[0] + w_ * self.nh[0], t_ * self.dh[1] + w_ * self.nh[1]) for t_, w_ in pts_tw])
        return out
