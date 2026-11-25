"""Exploded view: per-instance explode directions (auto + user overrides), moved shapes and trail lines."""
import math, os

AX = {'x': (1, 0, 0), 'y': (0, 1, 0), 'z': (0, 0, 1)}


def parse_dir(t):
    t = t.strip().lower(); sg = -1 if t.startswith('-') else 1
    a = AX.get(t.lstrip('+-'))
    if not a: raise ValueError(f'direction must be x, y, z, -x, -y or -z (got {t})')
    return tuple(sg * c for c in a)


def dir_name(d, dist=1):
    if not dist: return 'stays'
    i = max(range(3), key=lambda j: abs(d[j]))
    if abs(abs(d[i]) - 1) < 1e-6: return ('-' if d[i] < 0 else '') + 'xyz'[i]
    return '[%.2f, %.2f, %.2f]' % tuple(d)


def save_instances(inst, groups, adir):
    """Placed copy of every instance -> adir/instances/inst-NNN.brep; returns [{file, item, name}] (file relative)."""
    from OCP.BRepTools import BRepTools
    os.makedirs(os.path.join(adir, 'instances'), exist_ok=True)
    item_of = {ii: g['item'] for g in groups for ii in g['inst']}
    out = []
    for ii, it in enumerate(inst):
        rel = os.path.join('instances', f'inst-{ii + 1:03d}.brep')
        BRepTools.Write_s(it['shape'], os.path.join(adir, rel))
        out.append({'file': rel, 'item': item_of[ii], 'name': it['name']})
    return out


def load_instances(spec, adir):
    from OCP.BRepTools import BRepTools
    from OCP.BRep import BRep_Builder
    from OCP.TopoDS import TopoDS_Shape
    out = []
    for r in spec['instances']:
        s = TopoDS_Shape(); BRepTools.Read_s(s, os.path.join(adir, r['file']), BRep_Builder())
        out.append(dict(r, shape=s))
    return out


def _props(shape):
    from OCP.GProp import GProp_GProps
    from OCP.BRepGProp import BRepGProp
    from OCP.Bnd import Bnd_Box
    from OCP.BRepBndLib import BRepBndLib
    g = GProp_GProps(); BRepGProp.VolumeProperties_s(shape, g)
    c = g.CentreOfMass(); pp = g.PrincipalProperties()
    m = pp.Moments()
    axes = [pp.FirstAxisOfInertia(), pp.SecondAxisOfInertia(), pp.ThirdAxisOfInertia()]
    b = Bnd_Box(); BRepBndLib.Add_s(shape, b); x0, y0, z0, x1, y1, z1 = b.Get()
    return dict(c=(c.X(), c.Y(), c.Z()), vol=g.Mass(), mom=list(m), axes=[(a.X(), a.Y(), a.Z()) for a in axes],
                lo=(x0, y0, z0), hi=(x1, y1, z1))


def _dot(a, b): return sum(x * y for x, y in zip(a, b))


def _extent(p, d):
    """Half-extent and centre of the bbox along unit direction d."""
    corners = [(x, y, z) for x in (p['lo'][0], p['hi'][0]) for y in (p['lo'][1], p['hi'][1]) for z in (p['lo'][2], p['hi'][2])]
    pr = [_dot(q, d) for q in corners]
    return (max(pr) - min(pr)) / 2, (max(pr) + min(pr)) / 2, min(pr), max(pr)


def _snap(a):
    i = max(range(3), key=lambda j: abs(a[j]))
    if abs(a[i]) > math.cos(math.radians(5)): return tuple((1.0 if j == i else 0.0) * (1 if a[i] > 0 else -1) for j in range(3))
    L = math.sqrt(_dot(a, a)); return tuple(x / L for x in a)


def _touch(p, q, tol=0.5):
    return all(p['lo'][i] - tol <= q['hi'][i] and q['lo'][i] - tol <= p['hi'][i] for i in range(3))


def plan(insts, bom, scale=1.0, overrides=None):
    """-> list per instance: {dir, dist, base_offset, offset, auto, why}. Offsets in model mm."""
    overrides = overrides or {}
    hw = {b['item']: b['hardware'] for b in bom}
    P = [_props(i['shape']) for i in insts]
    lo = [min(p['lo'][j] for p in P) for j in range(3)]; hi = [max(p['hi'][j] for p in P) for j in range(3)]
    size = [max(hi[j] - lo[j], 1e-6) for j in range(3)]
    V = sum(p['vol'] for p in P) or 1
    C = tuple(sum(p['c'][j] * p['vol'] for p in P) / V for j in range(3))
    gap = max(8.0, 0.12 * max(size))
    parts = [i for i in range(len(insts)) if not hw.get(insts[i]['item'])]
    base = max(parts or range(len(insts)), key=lambda i: P[i]['vol'])
    nth = {}
    for i, it in enumerate(insts): nth[i] = sum(1 for j in range(i) if insts[j]['item'] == it['item']) + 1
    res = [None] * len(insts)

    def ov(i):
        o = dict(overrides.get(str(insts[i]['item']), {})); o.update(overrides.get(f"{insts[i]['item']}.{nth[i]}", {}))
        return o

    # 1) parts (not hardware): base stays; parts sitting on it lift off along the contact normal; parts that only
    #    touch already-moved parts ride along with them and go further the same way; loose parts move away from the centre
    def overlap(p, q, j): return min(p['hi'][j], q['hi'][j]) - max(p['lo'][j], q['lo'][j])
    res[base] = dict(dir=(0.0, 0.0, 1.0), dist=0.0, base_offset=(0.0, 0.0, 0.0), why='base part (stays put)', nb=None)
    todo = [i for i in parts if i != base]; done = [base]
    while todo:
        nxt = None
        for i in todo:
            hits = [j for j in done if _touch(P[i], P[j])]
            if hits: nxt = (i, hits); break
        if nxt is None:
            for i in todo:
                p = P[i]
                rel = [(p['c'][j] - C[j]) / size[j] for j in range(3)]
                j = max(range(3), key=lambda j: abs(rel[j]))
                d = (0.0, 0.0, 1.0) if abs(rel[j]) < 0.02 else tuple((1.0 if q == j else 0.0) * (1 if rel[j] > 0 else -1) for q in range(3))
                res[i] = dict(dir=d, dist=2 * _extent(p, d)[0] * 0.75 + gap, base_offset=(0.0, 0.0, 0.0),
                              why='away from the assembly centre', nb=None)
            break
        i, hits = nxt; p = P[i]
        if base in hits:
            q = P[base]
            j = min(range(3), key=lambda j: overlap(p, q, j) / size[j])
            sg = 1 if p['c'][j] - q['c'][j] >= 0 else -1
            d = tuple((1.0 if t == j else 0.0) * sg for t in range(3))
            res[i] = dict(dir=d, dist=_extent(p, d)[0] + gap, base_offset=(0.0, 0.0, 0.0),
                          why='lifts off the base part', nb=None)
        else:
            par = max(hits, key=lambda j: P[j]['vol'])
            d = res[par]['dir']
            # clear the part it fits in completely (a shaft leaves its brackets' outline), plus half a gap
            res[i] = dict(dir=d, dist=max(_extent(P[par], d)[3] - _extent(p, d)[2], 0) + gap / 2, base_offset=None,
                          why='rides with the part it fits in, then clears it', nb=par)
        todo.remove(i); done.append(i)
    # 2) hardware: along its own axis, riding along with the part it fastens
    for i in range(len(insts)):
        if res[i] is not None: continue
        p = P[i]; m = p['mom']
        k = max(range(3), key=lambda q: min(abs(m[q] - m[r]) for r in range(3) if r != q))
        a = _snap(p['axes'][k])
        half, mid, mn, mx = _extent(p, a)
        nbs = [j for j in parts if _touch(p, P[j])]
        off = _dot(p['c'], a) - mid
        if abs(off) > 0.04 * 2 * half:              # heavy end (bolt head) leads
            sg = 1 if off > 0 else -1; why = 'along its axis, head first'
        else:
            ref = (tuple(sum(P[j]['c'][q] for j in nbs) / len(nbs) for q in range(3)) if nbs else C)
            sg = 1 if _dot(p['c'], a) - _dot(ref, a) >= 0 else -1; why = 'along its axis, away from the part it sits on'
        d = tuple(sg * x for x in a)
        nb = max(nbs, key=lambda j: _extent(P[j], d)[3]) if nbs else None
        res[i] = dict(dir=d, dist=2 * half + gap, base_offset=None, why=why, nb=nb)
    # overrides + scale
    for i, r in enumerate(res):
        o = ov(i)
        r['auto'] = not o
        if 'dir' in o: r['dir'] = parse_dir(o['dir']); r['why'] = 'set by user'
        if 'dist' in o: r['dist'] = float(o['dist']); r['why'] = 'set by user'
        r['dist_scaled'] = r['dist'] * (1.0 if 'dist' in o else scale)
    def total(i, depth=0):
        r = res[i]
        if 'offset' in r: return r['offset']
        if r['base_offset'] is None:
            nb = r['nb']
            r['base_offset'] = total(nb, depth + 1) if nb is not None and depth < 20 else (0.0, 0.0, 0.0)
        r['offset'] = tuple(r['base_offset'][q] + r['dir'][q] * r['dist_scaled'] for q in range(3))
        return r['offset']
    for i in range(len(res)): total(i)
    for i, r in enumerate(res):
        r['half'] = _extent(P[i], r['dir'])[0]
        r['centre'] = P[i]['c']
    return res


def moved(insts, res):
    from OCP.gp import gp_Trsf, gp_Vec
    from OCP.BRepBuilderAPI import BRepBuilderAPI_Transform
    out = []
    for it, r in zip(insts, res):
        t = gp_Trsf(); t.SetTranslation(gp_Vec(*r['offset']))
        out.append(dict(it, shape=BRepBuilderAPI_Transform(it['shape'], t, True).Shape()))
    return out


def trails(res, gapmm=1.0):
    """3D trail segments: from where the part sits (after riding along with its mating part) to where it is drawn."""
    out = []
    for r in res:
        if r['dist_scaled'] <= 2 * r['half'] + gapmm: continue
        d = r['dir']; c0 = tuple(r['centre'][q] + r['base_offset'][q] for q in range(3))
        a = tuple(c0[q] + d[q] * (r['half'] + gapmm) for q in range(3))
        b = tuple(c0[q] + d[q] * (r['dist_scaled'] - r['half'] - gapmm) for q in range(3))
        out.append((a, b))
    return out
