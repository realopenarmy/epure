"""Sample assembly for Épure: base plate + 2 identical brackets + shaft + 4 bolts + 4 nuts (named components)."""
import math, os
import cadquery as cq
d = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'models')
plate = (cq.Workplane('XY').box(160, 80, 10).translate((0, 0, 5))
         .faces('>Z').workplane(origin=(0, 0, 10)).pushPoints([(-32, -12.5), (-32, 12.5), (32, -12.5), (32, 12.5)]).hole(6.6))
# L-bracket: foot 40 x 50 x 8 with 2 holes, upright 8 thick x 50 wide x 60 tall with a Ø20 bore for the shaft
br = (cq.Workplane('XZ').polyline([(0, 0), (40, 0), (40, 8), (8, 8), (8, 60), (0, 60)]).close().extrude(-50)
      .translate((0, -25, 0)))
br = br.faces('<Z').workplane(origin=(0, 0, 0)).pushPoints([(28, -25 + 25 - 12.5 + 0), (28, 12.5)]).hole(6.6)
br = br.cut(cq.Workplane().add(cq.Solid.makeCylinder(10, 8, cq.Vector(0, 0, 40), cq.Vector(1, 0, 0))))
shaft = cq.Workplane('YZ').circle(10).extrude(150).translate((-75, 0, 0))
shaft = shaft.faces('>X').edges().chamfer(1).faces('<X').edges().chamfer(1)
# hardware: hex head bolt M6x30 and hex nut M6 (simplified, no threads)
def hexagon(af, h):
    return cq.Workplane('XY').polygon(6, af / math.cos(math.radians(30))).extrude(h)
bolt = hexagon(10, 4).union(cq.Workplane('XY').circle(3).extrude(-30))
nut = hexagon(10, 5).faces('>Z').workplane().hole(6)
asm = cq.Assembly(name='SHAFT SUPPORT')
asm.add(plate, name='BASE PLATE', color=cq.Color('gray'))
asm.add(br, name='BRACKET', loc=cq.Location(cq.Vector(-60, 0, 10)))
asm.add(br, name='BRACKET_2', loc=cq.Location(cq.Vector(60, 0, 10), cq.Vector(0, 0, 1), 180))
asm.add(shaft, name='SHAFT', loc=cq.Location(cq.Vector(0, 0, 50)))
for i, (x, y) in enumerate([(-32, -12.5), (-32, 12.5), (32, -12.5), (32, 12.5)]):
    asm.add(bolt, name=f'HEX BOLT M6x30 #{i+1}', loc=cq.Location(cq.Vector(x, y, 18)))
    asm.add(nut, name=f'HEX NUT M6 #{i+1}', loc=cq.Location(cq.Vector(x, y, -5)))
asm.save(os.path.join(d, 'shaft_support.step'))
print('ok', os.path.join(d, 'shaft_support.step'))
