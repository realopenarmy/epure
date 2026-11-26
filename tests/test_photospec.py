"""Photo-mode spec -> solid tests. Run: venv/bin/python -m unittest discover -s tests -v"""
import json, math, os, subprocess, sys, tempfile, unittest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
import photospec as ps   # noqa: E402
import geom              # noqa: E402

PI = math.pi


def feats(solid):
    return geom.features(solid.wrapped, False)


def plate(features, **body):
    b = {'type': 'plate', 'length': 120, 'width': 80, 'thickness': 10}; b.update(body)
    return {'units': 'mm', 'body': b, 'features': features}


class Prismatic(unittest.TestCase):
    def test_plain_plate_size_and_volume(self):
        s, est = ps.build(plate([]))
        bb = ps.bbox(s)
        self.assertEqual((round(bb.xlen, 6), round(bb.ylen, 6), round(bb.zlen, 6)), (120, 80, 10))
        self.assertAlmostEqual(s.Volume(), 120 * 80 * 10, places=3)
        self.assertEqual(est, [])

    def test_hole_pattern_positions_found_by_engine(self):
        s, _ = ps.build(plate([{'type': 'hole_pattern', 'face': 'top', 'x': 10, 'y': 10, 'nx': 2, 'ny': 2,
                                'dx': 100, 'dy': 60, 'dia': 8.5}]))
        H = feats(s)['holes']
        self.assertEqual(len(H), 4)
        self.assertEqual(sorted((round(h['center'][0], 4), round(h['center'][1], 4)) for h in H),
                         [(10, 10), (10, 70), (110, 10), (110, 70)])
        self.assertTrue(all(h['thru'] and abs(h['dia'] - 8.5) < 1e-6 for h in H))
        self.assertAlmostEqual(s.Volume(), 96000 - 4 * PI * 4.25 ** 2 * 10, places=2)

    def test_blind_hole_depth_and_side_face(self):
        s, _ = ps.build(plate([{'type': 'hole', 'face': 'front', 'x': 60, 'z': 5, 'dia': 5, 'depth': 20}]))
        h = feats(s)['holes'][0]
        self.assertEqual(h['axis'], 'y'); self.assertFalse(h['thru'])
        self.assertAlmostEqual(h['depth'], 20, places=4)
        self.assertAlmostEqual(h['center'][0], 60, places=4); self.assertAlmostEqual(h['center'][2], 5, places=4)

    def test_counterbore_and_countersink(self):
        s, _ = ps.build(plate([{'type': 'hole', 'x': 30, 'y': 40, 'dia': 6.6, 'cbore_dia': 11, 'cbore_depth': 6.5},
                               {'type': 'hole', 'x': 90, 'y': 40, 'dia': 6.6, 'csk_dia': 12, 'csk_angle': 90}]))
        F = feats(s)
        dias = sorted(round(h['dia'], 3) for h in F['holes'])
        self.assertIn(6.6, dias)
        self.assertTrue(any(c.get('sub') == 'csk' for c in F.get('chamfers', [])))
        exp = 96000 - PI * 3.3 ** 2 * 10 - PI * (5.5 ** 2 - 3.3 ** 2) * 6.5
        hcsk = (6 - 3.3) / math.tan(math.radians(45))
        exp -= PI * 3.3 ** 2 * 10 + PI * hcsk / 3 * (6 ** 2 + 6 * 3.3 + 3.3 ** 2) - PI * 3.3 ** 2 * hcsk
        self.assertAlmostEqual(s.Volume(), exp, delta=1.0)

    def test_bolt_circle(self):
        s, _ = ps.build(plate([{'type': 'bolt_circle', 'cx': 60, 'cy': 40, 'bc_dia': 50, 'count': 6, 'dia': 5}]))
        F = feats(s)
        self.assertEqual(len(F['holes']), 6)
        self.assertEqual(len(F['bolt_circles']), 1)
        self.assertAlmostEqual(F['bolt_circles'][0]['dia'], 50, places=3)

    def test_slot_pocket_step(self):
        s, _ = ps.build(plate([{'type': 'slot', 'x1': 45, 'y1': 70, 'x2': 75, 'y2': 70, 'width': 6},
                               {'type': 'pocket', 'x': 60, 'y': 35, 'length': 40, 'width': 20, 'depth': 4},
                               {'type': 'step', 'face': 'top', 'edge': '+x', 'size': 15, 'depth': 3}]))
        S = feats(s)['slots'][0]
        self.assertAlmostEqual(S['width'], 6, places=4); self.assertAlmostEqual(S['length'], 30, places=4)
        slot_v = (30 * 6 + PI * 9) * 10
        self.assertAlmostEqual(s.Volume(), 96000 - slot_v - 40 * 20 * 4 - 15 * 80 * 3, places=2)

    def test_pocket_corner_radius_and_edge_fillet(self):
        s, _ = ps.build(plate([{'type': 'fillet', 'edges': 'vertical', 'radius': 6},
                               {'type': 'pocket', 'x': 60, 'y': 40, 'length': 50, 'width': 30, 'depth': 4, 'corner_radius': 5}]))
        corner = (4 - PI) * 6 ** 2 * 10
        pocket = (50 * 30 - (4 - PI) * 25) * 4
        self.assertAlmostEqual(s.Volume(), 96000 - corner - pocket, places=1)
        rs = sorted({round(f['r'], 3) for f in feats(s)['fillets']})
        self.assertEqual(rs, [5.0, 6.0])

    def test_chamfer_top_edges(self):
        s, _ = ps.build(plate([{'type': 'chamfer', 'edges': 'top', 'size': 1}], length=50, width=40))
        self.assertAlmostEqual(s.Volume(), 50 * 40 * 10 - 0.5 * (2 * 50 + 2 * 40) + 4 / 3, delta=0.01)   # mitred corners overlap

    def test_inches(self):
        s, _ = ps.build({'units': 'in', 'body': {'type': 'plate', 'length': 4, 'width': 2, 'thickness': 0.25},
                         'features': [{'type': 'hole', 'x': 1, 'y': 1, 'dia': 0.25}]})
        bb = ps.bbox(s)
        self.assertAlmostEqual(bb.xlen, 101.6, places=4)
        h = feats(s)['holes'][0]
        self.assertAlmostEqual(h['dia'], 6.35, places=4); self.assertAlmostEqual(h['center'][0], 25.4, places=4)


class Bracket(unittest.TestCase):
    def test_square_bracket_volume(self):
        s, _ = ps.build({'body': {'type': 'l_bracket', 'length': 80, 'base': 50, 'height': 60, 'thickness': 5}})
        self.assertAlmostEqual(s.Volume(), 80 * (50 * 5 + 55 * 5), places=3)

    def test_bent_bracket_volume_and_faces(self):
        R, T = 5, 5
        s, _ = ps.build({'body': {'type': 'l_bracket', 'length': 80, 'base': 50, 'height': 60, 'thickness': T, 'bend_radius': R},
                         'features': [{'type': 'hole', 'face': 'base', 'x': 40, 'y': 35, 'dia': 6.6},
                                      {'type': 'hole', 'face': 'upright', 'x': 40, 'z': 40, 'dia': 6.6}]})
        square = 80 * (50 * T + (60 - T) * T)
        inner_add = (R ** 2 - PI * R ** 2 / 4) * 80          # fillet adds material in the inside corner
        outer_cut = ((R + T) ** 2 - PI * (R + T) ** 2 / 4) * 80
        holes = 2 * PI * 3.3 ** 2 * T
        self.assertAlmostEqual(s.Volume(), square + inner_add - outer_cut - holes, places=1)
        H = feats(s)['holes']
        self.assertEqual(sorted(h['axis'] for h in H), ['y', 'z'])

    def test_bracket_bad_face(self):
        with self.assertRaises(ps.SpecError):
            ps.build({'body': {'type': 'l_bracket', 'length': 80, 'base': 50, 'height': 60, 'thickness': 5},
                      'features': [{'type': 'hole', 'face': 'top', 'x': 1, 'y': 1, 'dia': 3}]})


class Turned(unittest.TestCase):
    def test_stepped_shaft(self):
        s, _ = ps.build({'body': {'type': 'turned', 'segments': [{'dia': 30, 'length': 40}, {'dia': 20, 'length': 50}]},
                         'features': [{'type': 'cross_hole', 'z': 70, 'dia': 5, 'direction': 'y'}]})
        F = feats(s)
        self.assertEqual(sorted(round(c['dia'], 3) for c in F['cyls']), [20.0, 30.0])
        exp = PI * 15 ** 2 * 40 + PI * 10 ** 2 * 50
        self.assertLess(s.Volume(), exp)
        self.assertTrue(any(h['axis'] == 'y' and abs(h['dia'] - 5) < 1e-6 for h in F['holes']))

    def test_bushing_bore_chamfer_groove(self):
        s, _ = ps.build({'body': {'type': 'turned', 'segments': [{'dia': 40, 'length': 30}]},
                         'features': [{'type': 'bore', 'dia': 20, 'depth': 'thru'},
                                      {'type': 'groove', 'z': 10, 'width': 2, 'dia': 36},
                                      {'type': 'chamfer', 'at': 'end', 'size': 1}]})
        exp = PI * (20 ** 2 - 10 ** 2) * 30 - PI * (20 ** 2 - 18 ** 2) * 2
        cham = PI * 0.5 * (2 * 20 - 2 / 3)       # 1x1 triangle swept around r=20 (centroid at r = 20 - 1/3)
        self.assertAlmostEqual(s.Volume(), exp - cham, delta=0.5)
        H = feats(s)['holes']
        self.assertTrue(any(abs(h['dia'] - 20) < 1e-6 and h['thru'] for h in H))

    def test_shoulder_chamfer_and_fillet(self):
        body = {'type': 'turned', 'segments': [{'dia': 30, 'length': 40}, {'dia': 20, 'length': 50}]}
        s, _ = ps.build({'body': body, 'features': [{'type': 'fillet', 'at': 'steps', 'radius': 1}]})
        self.assertEqual([round(f['r'], 3) for f in feats(s)['fillets']], [1.0])
        plain = PI * 15 ** 2 * 40 + PI * 10 ** 2 * 50
        self.assertGreater(s.Volume(), plain)            # an inside fillet adds material
        s, _ = ps.build({'body': body, 'features': [{'type': 'chamfer', 'at': 'steps', 'size': 1}]})
        self.assertLess(s.Volume(), plain)               # an outer chamfer removes it
        self.assertEqual(len(feats(s)['chamfers']), 1)

    def test_blind_bore_from_end(self):
        s, _ = ps.build({'body': {'type': 'turned', 'segments': [{'dia': 20, 'length': 50}]},
                         'features': [{'type': 'bore', 'dia': 8, 'depth': 12, 'from': 'end'}]})
        self.assertAlmostEqual(s.Volume(), PI * 100 * 50 - PI * 16 * 12, places=2)


BOTTLE = os.path.join(HERE, 'samples', 'photo_bottle.json')


class Bottle(unittest.TestCase):
    """Tapers, a rounded shoulder by z, a keyring tab, and the simplified note."""
    def test_taper_is_a_frustum(self):
        s, _ = ps.build({'body': {'type': 'turned', 'segments': [{'dia_start': 30, 'dia_end': 20, 'length': 12}]}})
        self.assertAlmostEqual(s.Volume(), PI * 12 / 3 * (15 ** 2 + 15 * 10 + 10 ** 2), places=2)
        self.assertEqual(len(s.Solids()), 1)

    def test_taper_needs_both_ends(self):
        with self.assertRaises(ps.SpecError):
            ps.build({'body': {'type': 'turned', 'segments': [{'dia_start': 30, 'length': 12}]}})

    def test_shoulder_fillet_by_z_adds_a_blend_and_leader(self):
        body = {'type': 'turned', 'segments': [{'dia': 31, 'length': 48}, {'dia_start': 31, 'dia_end': 23, 'length': 12}]}
        plain, _ = ps.build({'body': body})
        spec = {'body': body, 'features': [{'type': 'fillet', 'z': 48, 'radius': 8}]}
        s, _ = ps.build(spec)
        self.assertLess(s.Volume(), plain.Volume())       # rounding a convex shoulder removes material
        self.assertAlmostEqual(ps.bbox(s).zlen, 60, places=4)
        (pt, r, est), = ps.fillet_leaders(spec, s)
        self.assertEqual((r, est), (8, False)); self.assertTrue(40 < pt[2] < 56 and 14 < pt[0] < 15.5)

    def test_tab_with_hole(self):
        spec = {'body': {'type': 'turned', 'segments': [{'dia': 19, 'length': 10}]},
                'features': [{'type': 'tab', 'width': 8, 'thickness': 3, 'height': 7, 'hole_dia': 3}]}
        s, _ = ps.build(spec)
        self.assertAlmostEqual(ps.bbox(s).zlen, 17, places=4)
        tab = 8 * 3 * 3 + PI * 16 / 2 * 3 - PI * 1.5 ** 2 * 3   # 3 mm of box above the end + half disc - hole
        self.assertAlmostEqual(s.Volume(), PI * 9.5 ** 2 * 10 + tab, places=1)
        self.assertTrue(any(h['axis'] == 'y' and abs(h['dia'] - 3) < 1e-6 for h in feats(s)['holes']))
        with self.assertRaises(ps.SpecError):
            ps.build({'body': spec['body'], 'features': [{'type': 'tab', 'width': 8, 'thickness': 3, 'height': 3}]})

    def test_sample_bottle_all_estimated(self):
        spec = ps.load(BOTTLE)
        s, est = ps.build(spec)
        bb = ps.bbox(s)
        self.assertEqual((round(bb.xlen, 3), round(bb.zlen, 3)), (31.0, 80.0))
        self.assertTrue(ps.all_estimated(spec))
        self.assertIn(80.0, [e['mm'] for e in est if e.get('derived')])     # overall height adds up estimates
        self.assertEqual(ps.simplified_note(spec['simplified']), 'SIMPLIFIED FROM PHOTOS - LETTERING, LOGO AND RIBS NOT SHOWN')
        self.assertFalse(ps.all_estimated(plate([{'type': 'hole', 'x': 1, 'y': 1, 'dia': {'value': 3, 'est': True}}])))

    def test_bottle_cli_drawing(self):
        E = os.path.join(HERE, 'epure')
        run = lambda *a: json.loads(subprocess.run([E, *a], check=True, capture_output=True, text=True).stdout)
        with tempfile.TemporaryDirectory() as t:
            b = run('photo', 'build', BOTTLE, '--out', os.path.join(t, 'model'))
            self.assertTrue(b['all_estimated']); self.assertTrue(b['simplified_note'])
            m = run('make', b['step'], '--out', os.path.join(t, 'drw'))
            self.assertEqual(m['audit_errors'], 0)
            k = run('photo', 'mark', os.path.join(t, 'drw'), '--spec', BOTTLE)
            k2 = run('photo', 'mark', os.path.join(t, 'drw'), '--spec', BOTTLE)          # idempotent
            self.assertEqual(k2['audit_errors'], 0)
            with open(os.path.join(t, 'drw', 'spec.json')) as f: sp = json.load(f)
            self.assertTrue(all('≈' in (d.get('text') or '') for d in sp['dims']))
            self.assertEqual(sum(1 for d in sp['dims'] if d.get('photo')), 1)        # the R8 shoulder leader, once
            self.assertEqual([n for n in sp['notes'] if n.startswith('SIMPLIFIED')], [k['simplified_note']])
            self.assertEqual(sum(1 for n in sp['notes'] if 'ESTIMATED' in n), 1)


STOOL = os.path.join(HERE, 'samples', 'photo_stool.json')


def radius_at(solid, z):
    """Outer radius of a revolved solid at height z (from a thin slice)."""
    import cadquery as cq
    sl = solid.intersect(cq.Solid.makeBox(1e4, 1e4, 0.002, cq.Vector(-5e3, -5e3, z - 0.001)))
    return ps.bbox(sl).xmax


class Curves(unittest.TestCase):
    """Curved revolved profiles: arcs (bulge), straight revolve points, and smooth splines."""
    def test_revolve_straight_points_is_a_frustum(self):
        s, _ = ps.build({'body': {'type': 'revolve', 'smooth': False,
                                  'profile': [{'z': 0, 'dia': 30}, {'z': 12, 'dia': 20}]}})
        self.assertAlmostEqual(s.Volume(), PI * 12 / 3 * (15 ** 2 + 150 + 10 ** 2), places=2)

    def test_spline_passes_through_the_stations(self):
        prof = [{'z': 0, 'dia': 60}, {'z': 30, 'dia': 80}, {'z': 70, 'dia': 40}, {'z': 100, 'dia': 70}]
        s, _ = ps.build({'body': {'type': 'revolve', 'profile': prof}})
        for p_ in prof[1:-1]:
            self.assertAlmostEqual(radius_at(s, p_['z']), p_['dia'] / 2, delta=0.05)
        self.assertAlmostEqual(ps.bbox(s).zlen, 100, places=3)

    def test_corner_breaks_the_spline(self):
        prof = [{'z': 0, 'dia': 200}, {'z': 65, 'dia': 255}, {'z': 190, 'dia': 95, 'corner': True},
                {'z': 330, 'dia': 250}, {'z': 420, 'dia': 290}]
        s, _ = ps.build({'body': {'type': 'revolve', 'profile': prof}})
        self.assertAlmostEqual(radius_at(s, 190), 47.5, delta=0.05)
        self.assertEqual(len(s.Solids()), 1)
        self.assertTrue(any(e.geomType() == 'CIRCLE' and abs(e.Center().z - 190) < 1e-3 for e in s.Edges()))

    def test_bulge_arc_convex_and_concave(self):
        seg = lambda b: {'body': {'type': 'turned', 'segments': [{'dia': 40, 'length': 40, 'bulge': b}]}}
        cyl = PI * 20 ** 2 * 40
        out, _ = ps.build(seg(5)); inn, _ = ps.build(seg(-5))
        self.assertAlmostEqual(radius_at(out, 20), 25, places=3); self.assertAlmostEqual(radius_at(inn, 20), 15, places=3)
        self.assertGreater(out.Volume(), cyl); self.assertLess(inn.Volume(), cyl)
        with self.assertRaises(ps.SpecError): ps.build(seg(-25))

    def test_profile_errors(self):
        bad = lambda prof, msg: self.assertRaisesRegex(ps.SpecError, msg, ps.build, {'body': {'type': 'revolve', 'profile': prof}})
        bad([{'z': 0, 'dia': 10}], 'at least 2')
        bad([{'z': 5, 'dia': 10}, {'z': 10, 'dia': 10}], 'z = 0')
        bad([{'z': 0, 'dia': 10}, {'z': 10, 'dia': 10}, {'z': 5, 'dia': 10}], 'go up')
        bad([{'z': 0, 'dia': 100}, {'z': 1, 'dia': 2}, {'z': 2, 'dia': 100}, {'z': 50, 'dia': 100}], 'dips through the axis')

    def test_stations_and_curved(self):
        spec = ps.load(STOOL)
        self.assertTrue(ps.is_curved(spec)); self.assertTrue(ps.all_estimated(spec))
        self.assertEqual([(z, d) for z, d, _ in ps.stations(spec)], [(0, 200), (65, 255), (190, 95), (330, 250), (420, 290)])
        self.assertFalse(ps.is_curved(ps.load(BOTTLE)))
        st = ps.stations({'body': {'type': 'turned', 'segments': [{'dia': 40, 'length': 40, 'bulge': 5}]}})
        self.assertEqual([(z, d) for z, d, _ in st], [(0, 40), (20, 50), (40, 40)])

    def test_stool_cli_drawing(self):
        E = os.path.join(HERE, 'epure')
        run = lambda *a: json.loads(subprocess.run([E, *a], check=True, capture_output=True, text=True).stdout)
        with tempfile.TemporaryDirectory() as t:
            b = run('photo', 'build', STOOL, '--out', os.path.join(t, 'model'))
            self.assertEqual([round(v, 2) for v in b['size_mm']], [290.0, 290.0, 420.0])
            self.assertEqual(run('make', b['step'], '--out', os.path.join(t, 'drw'))['audit_errors'], 0)
            run('photo', 'mark', os.path.join(t, 'drw'), '--spec', STOOL)
            k = run('photo', 'mark', os.path.join(t, 'drw'), '--spec', STOOL)
            self.assertEqual(k['audit_errors'], 0); self.assertEqual(k['curves_note'], ps.CURVES_NOTE)
            with open(os.path.join(t, 'drw', 'spec.json')) as f: sp = json.load(f)
            ph = [d for d in sp['dims'] if d.get('photo')]
            self.assertEqual(sorted(round(d['b'] * 2) for d in ph if d['axis'] == 'x'), [95, 200, 250, 255, 290])
            self.assertEqual(sorted(round(d['b']) for d in ph if d['axis'] == 'z'), [65, 190, 330])
            self.assertTrue(all('≈' in (d.get('text') or '') for d in sp['dims']))
            self.assertEqual(sp['notes'][-3:], [ps.ESTIMATE_NOTE, ps.CURVES_NOTE, k['simplified_note']])


class Estimates(unittest.TestCase):
    SPEC = {'units': 'mm', 'body': {'type': 'plate', 'length': {'value': 120, 'est': True}, 'width': 80, 'thickness': 10},
            'features': [{'type': 'hole_pattern', 'x': 10, 'y': 10, 'nx': 2, 'dx': 100, 'dia': {'value': 8.5, 'est': True}}]}

    def test_estimates_logged_once(self):
        _, est = ps.build(self.SPEC)
        self.assertEqual([(e['where'], e['kind'], e['mm']) for e in est],
                         [('body.length', 'length', 120.0), ('features[0](hole_pattern).dia', 'diameter', 8.5)])
        self.assertEqual([e['where'] for e in ps.estimates(self.SPEC)], ['body.length', 'features[0](hole_pattern).dia'])

    def test_confirmed_value_is_plain_number(self):
        spec = json.loads(json.dumps(self.SPEC)); spec['body']['length'] = 120
        spec['features'][0]['dia'] = 8.5
        self.assertEqual(ps.estimates(spec), [])


class Loading(unittest.TestCase):
    def test_yaml_and_json_match(self):
        with tempfile.TemporaryDirectory() as t:
            y = os.path.join(t, 's.yaml')
            with open(y, 'w') as f:
                f.write('units: mm\nbody: {type: plate, length: 50, width: 40, thickness: 6}\n'
                        'features:\n  - {type: hole, x: 25, y: 20, dia: {value: 5, est: true}}\n')
            spec = ps.load(y)
        self.assertEqual(spec['features'][0]['dia'], {'value': 5, 'est': True})
        s, est = ps.build(spec)
        self.assertEqual(len(est), 1)
        self.assertAlmostEqual(s.Volume(), 50 * 40 * 6 - PI * 2.5 ** 2 * 6, places=3)


class Scale(unittest.TestCase):
    def test_named_and_custom_references(self):
        self.assertAlmostEqual(ps.scale_estimate(100, 50, 'penny'), 9.525)
        self.assertAlmostEqual(ps.scale_estimate(856, 66, 'card_long'), 6.6, places=6)
        self.assertAlmostEqual(ps.scale_estimate(200, 100, ref_mm=30), 15)
        with self.assertRaises(ps.SpecError): ps.scale_estimate(100, 50, 'teacup')
        with self.assertRaises(ps.SpecError): ps.scale_estimate(0, 50, 'dime')

    def test_cli(self):
        r = subprocess.run([os.path.join(HERE, 'epure'), 'photo', 'scale', '--ref', 'aa_long', '--ref-px', '505', '--px', '66', '120'],
                           check=True, capture_output=True, text=True)
        self.assertEqual(json.loads(r.stdout)['estimates_mm'], [6.6, 12.0])


class Errors(unittest.TestCase):
    def bad(self, spec, msg):
        with self.assertRaises(ps.SpecError) as cm: ps.build(spec)
        self.assertIn(msg, str(cm.exception))

    def test_messages(self):
        self.bad({'body': {'type': 'casting'}}, 'envelope')
        self.bad({'body': {'type': 'sphere'}}, 'body.type must be')
        self.bad(plate([], thickness=None), 'missing "thickness"')
        self.bad(plate([{'type': 'hole', 'x': 1, 'y': 1}]), 'missing "dia"')
        self.bad(plate([{'type': 'knurl'}]), 'unknown feature type')
        self.bad(plate([{'type': 'hole', 'face': 'side', 'x': 1, 'y': 1, 'dia': 3}]), 'face must be')
        self.bad(plate([{'type': 'hole', 'x': 1, 'y': 1, 'dia': 'big'}]), 'expected a number')
        self.bad(plate([{'type': 'slot', 'x1': 10, 'y1': 10, 'x2': 30, 'y2': 30, 'width': 5}]), 'slots must run along')
        self.bad(plate([{'type': 'pocket', 'x': 60, 'y': 40, 'length': 130, 'width': 10, 'depth': 'thru'}]), 'pieces')
        self.bad({'units': 'cm', 'body': {'type': 'plate'}}, 'units must be')


class EndToEnd(unittest.TestCase):
    """spec -> STEP -> unchanged `epure make` -> `epure photo mark`: estimated dims get ≈ and the note."""
    def test_cli_pipeline(self):
        E = os.path.join(HERE, 'epure')
        with tempfile.TemporaryDirectory() as t:
            sp = os.path.join(t, 'spec.json')
            with open(sp, 'w') as f:
                json.dump(plate([{'type': 'hole_pattern', 'x': 10, 'y': 10, 'nx': 2, 'ny': 2, 'dx': 100, 'dy': 60,
                                  'dia': {'value': 8.5, 'est': True}}]), f)
            run = lambda *a: json.loads(subprocess.run([E, *a], check=True, capture_output=True, text=True).stdout)
            b = run('photo', 'build', sp, '--out', os.path.join(t, 'model'))
            self.assertTrue(os.path.exists(b['step'])); self.assertTrue(os.path.getsize(b['model_preview']) > 5000)
            self.assertEqual(len(b['estimates']), 1)
            m = run('make', b['step'], '--out', os.path.join(t, 'drw'))
            self.assertEqual(m['audit_errors'], 0); self.assertEqual(len(m['features']['holes']), 4)
            k = run('photo', 'mark', os.path.join(t, 'drw'), '--spec', sp)
            self.assertEqual(k['audit_errors'], 0); self.assertTrue(k['marked'])
            import ezdxf
            doc = ezdxf.readfile(os.path.join(t, 'drw', 'drawing.dxf'))
            txt = ' '.join(e.dxf.text for e in doc.modelspace().query('TEXT MTEXT') if e.dxf.hasattr('text'))
            txt += ' '.join(e.text for e in doc.modelspace().query('MTEXT'))
            for blk in doc.blocks:
                for e in blk.query('TEXT MTEXT'): txt += ' ' + (e.text if e.dxftype() == 'MTEXT' else e.dxf.text)
            self.assertIn('≈', txt)
            self.assertIn('ESTIMATED FROM PHOTOS', txt)
            r = subprocess.run([E, 'photo', 'check', sp], capture_output=True, text=True)
            self.assertFalse(json.loads(r.stdout)['ready_for_final'])
            bad = os.path.join(t, 'bad.json')
            with open(bad, 'w') as f: json.dump({'body': {'type': 'organic'}}, f)
            r = subprocess.run([E, 'photo', 'build', bad, '--out', os.path.join(t, 'x')], capture_output=True, text=True)
            self.assertEqual(r.returncode, 2); self.assertIn('envelope', json.loads(r.stdout)['error'])


if __name__ == '__main__':
    unittest.main()
