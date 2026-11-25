"""Generate sample parts (STEP + one STL) for testing Épure."""
import cadquery as cq, os
d = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'samples', 'models'); os.makedirs(d, exist_ok=True)
# 1. plate 120x80x10 with 4 corner holes and a 60x30x4 pocket
plate = (cq.Workplane('XY').box(120, 80, 10).translate((60, 40, 5))
         .faces('>Z').workplane(centerOption='CenterOfBoundBox')
         .rect(100, 60, forConstruction=True).vertices().hole(8.5)
         .faces('>Z').workplane(centerOption='CenterOfBoundBox').rect(60, 30).cutBlind(-4))
cq.exporters.export(plate, f'{d}/plate.step')
cq.exporters.export(plate, f'{d}/plate.stl', tolerance=0.05, angularTolerance=0.2)
# 2. stepped shaft along Z: dia 30 x 40, dia 20 x 50, dia 12 x 25, with 1mm chamfer at end and a cross hole dia 5
shaft = (cq.Workplane('XY').circle(15).extrude(40)
         .faces('>Z').workplane().circle(10).extrude(50)
         .faces('>Z').workplane().circle(6).extrude(25)
         .faces('>Z').edges().chamfer(1))
cross = cq.Workplane('YZ').workplane(offset=-20).center(0, 70).circle(2.5).extrude(40)
shaft = shaft.cut(cross)
cq.exporters.export(shaft, f'{d}/shaft.step')
# 3. L-bracket: base 100x60x8, upright 100x8x50, two slots in base, 2 holes in upright
br = (cq.Workplane('XY').box(100, 60, 8).translate((50, 30, 4))
      .union(cq.Workplane('XY').box(100, 8, 50).translate((50, 4, 25))))
slots = (cq.Workplane('XY').pushPoints([(25, 38), (75, 38)]).slot2D(24, 8, 0).extrude(8))
br = br.cut(slots)
holes = cq.Workplane('XZ').pushPoints([(20, 36), (80, 36)]).circle(3.25).extrude(-8)
br = br.cut(holes)
cq.exporters.export(br, f'{d}/bracket.step')
from OCP.IGESControl import IGESControl_Writer
w = IGESControl_Writer('MM', 1); w.AddShape(br.val().wrapped); w.ComputeModel(); w.Write(f'{d}/bracket.iges')
from OCP.BRepTools import BRepTools
BRepTools.Write_s(br.val().wrapped, f'{d}/bracket.brep')
print('samples in', d)

# 4. flange: Ø150 x 15 disk + Ø70 hub to 40 tall, Ø40 bore chamfered 1.5x45 both ends,
#    6x Ø8.5 on Ø110 BC, R3 hub fillet, R2 outer edge round
fl = (cq.Workplane('XY').circle(75).extrude(15)
      .faces('>Z').workplane().circle(35).extrude(25))
def circ_edges(wp, r, z):
    es = [e for e in wp.edges().vals() if e.geomType() == 'CIRCLE' and abs(e.radius() - r) < 1e-3 and abs(e.Center().z - z) < 1e-3]
    return wp.newObject(es)
fl = fl.faces('>Z').workplane().hole(40)
fl = fl.faces('<Z').workplane(centerOption='CenterOfBoundBox').polarArray(55, 0, 360, 6).hole(8.5)
fl = circ_edges(fl, 35, 15).fillet(3)      # hub-to-disk fillet (torus)
fl = circ_edges(fl, 20, 40).chamfer(1.5)   # bore chamfer top
fl = circ_edges(fl, 20, 0).chamfer(1.5)    # bore chamfer bottom
fl = circ_edges(fl, 75, 15).fillet(2)      # outer top edge round (torus)
cq.exporters.export(fl, f'{d}/flange.step')
# 5. tapped block 100x60x25: 2x M8 tap drill Ø6.8 blind 18 deep, 2x M6 tap drill Ø5.0 thru,
#    1x45 chamfers on the 4 vertical edges, R5 is NOT used here
tb = cq.Workplane('XY').box(100, 60, 25).translate((50, 30, 12.5)).edges('|Z').chamfer(1)
tb = tb.faces('>Z').workplane(origin=(0, 0, 25)).pushPoints([(20, 30), (80, 30)]).hole(6.8, 18)
tb = tb.faces('>Z').workplane(origin=(0, 0, 25)).pushPoints([(50, 15), (50, 45)]).hole(5.0)
cq.exporters.export(tb, f'{d}/tapped_block.step')
# 6. fillet plate: 90x60x12 with R8 corners, 3 holes unequally spaced on a circle (fallback test)
fp = cq.Workplane('XY').box(90, 60, 12).translate((45, 30, 6)).edges('|Z').fillet(8)
import math as _m
pts = [(45 + 20*_m.cos(_m.radians(a)), 30 + 20*_m.sin(_m.radians(a))) for a in (90, 200, 320)]
fp = fp.faces('>Z').workplane(origin=(0, 0, 12)).pushPoints(pts).hole(5.5)
cq.exporters.export(fp, f'{d}/fillet_plate.step')
# 7. countersunk plate 120x70x10: 4x Ø6.6 thru CSK Ø12 x 90° (top), 2x Ø5.5 thru CSK Ø10.4 x 90° both ends,
#    1x Ø6.6 thru with counterdrill Ø11 x 4 deep x 118°
cp = cq.Workplane('XY').box(120, 70, 10).translate((60, 35, 5))
cp = cp.faces('>Z').workplane(origin=(0, 0, 10)).pushPoints([(15, 15), (105, 15), (15, 55), (105, 55)]).cskHole(6.6, 12, 90)
cp = cp.faces('>Z').workplane(origin=(0, 0, 10)).pushPoints([(40, 35), (80, 35)]).cskHole(5.5, 10.4, 90)
_V = cq.Vector
for x in (40, 80):   # bottom countersinks: cone apex upward from z=0
    cp = cp.cut(cq.Workplane().add(cq.Solid.makeCone(5.2, 0, 5.2, _V(x, 35, 0), _V(0, 0, 1))))
cp = cp.faces('>Z').workplane(origin=(0, 0, 10)).pushPoints([(60, 35)]).hole(6.6)
cp = cp.cut(cq.Workplane().add(cq.Solid.makeCylinder(5.5, 4, _V(60, 35, 6), _V(0, 0, 1))))
_h = 2.2 / _m.tan(_m.radians(59))
cp = cp.cut(cq.Workplane().add(cq.Solid.makeCone(3.3, 5.5, _h, _V(60, 35, 6 - _h), _V(0, 0, 1))))
cq.exporters.export(cp, f'{d}/csk_plate.step')
# 8. wedge block: 80 (x) x 40 (y) x 50 (z), top flat to x=30, then a 30° slope down to x=80; Ø12 thru (z) at (15, 20)
_t = _m.tan(_m.radians(30))
wb = (cq.Workplane('XZ').polyline([(0, 0), (80, 0), (80, 50 - 50*_t), (30, 50), (0, 50)]).close().extrude(-40))
wb = wb.cut(cq.Workplane().add(cq.Solid.makeCylinder(6, 50, _V(15, 20, 0), _V(0, 0, 1))))
cq.exporters.export(wb, f'{d}/wedge.step')
print('new samples ok')
