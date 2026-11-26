#!/usr/bin/env bash
# Regenerates every sample drawing and exercises the edit ops. Usage: bash samples/run_samples.sh
set -euo pipefail
cd "$(dirname "$0")/.."
PY=./venv/bin/python; EP="$PY epure.py"
$PY make_samples.py >/dev/null
$EP make samples/models/plate.step   --config samples/defaults.json    --fields samples/plate.json   --out samples/plate   >/dev/null
$EP make samples/models/shaft.step   --config samples/defaults.json    --fields samples/shaft.json   --out samples/shaft   >/dev/null
$EP make samples/models/bracket.step --config samples/defaults_in.json --fields samples/bracket.json --out samples/bracket >/dev/null
$EP make samples/models/bracket.iges --config samples/defaults_logo.json --fields samples/bracket.json --out samples/bracket_first_iges >/dev/null
$EP make samples/models/bracket.brep --config samples/defaults.json --out samples/bracket_brep >/dev/null
$EP make samples/models/block.step   --config samples/defaults.json --out samples/block >/dev/null
$EP make samples/models/plate.stl    --config samples/defaults.json --fields samples/plate.json --out samples/plate_stl >/dev/null
$EP make samples/models/plate.obj    --config samples/defaults.json --out samples/plate_obj >/dev/null
$EP make samples/models/flange.step       --config samples/defaults.json --fields samples/flange.json --out samples/flange >/dev/null
$EP make samples/models/fillet_plate.step --config samples/defaults.json --out samples/fillet_plate >/dev/null
$EP make samples/models/tapped_block.step --config samples/defaults.json --fields samples/tapped.json --out samples/tapped_block >/dev/null
$EP edit samples/tapped_block accept-thread H2 >/dev/null     # M8x1.25 blind (suggested)
$EP edit samples/tapped_block accept-thread H1 >/dev/null     # M6x1 thru (suggested)
$EP make samples/models/flange.step       --config samples/defaults.json --fields samples/flange.json --out samples/flange_reject >/dev/null
$EP edit samples/flange_reject reject-thread H1 >/dev/null    # Ø8.5 are clearance holes, not M10 tap drill
$EP make samples/models/csk_plate.step   --config samples/defaults.json --out samples/csk_plate >/dev/null
$EP make samples/models/wedge.step       --config samples/defaults.json --out samples/wedge >/dev/null
# photo mode: part spec -> STEP -> the normal make -> ≈ on the estimated slot width
$EP photo build samples/photo_bracket.json --out samples/photo_bracket/model >/dev/null
$EP make samples/photo_bracket/model/model.step --config samples/defaults.json --fields samples/photo_bracket_fields.json --out samples/photo_bracket/drawing >/dev/null
$EP photo mark samples/photo_bracket/drawing --spec samples/photo_bracket.json >/dev/null
# photo mode, simplified: a keychain bottle, every size estimated from a penny, lettering and ribs left out
$EP photo build samples/photo_bottle.json --out samples/photo_bottle/model >/dev/null
$EP make samples/photo_bottle/model/model.step --config samples/defaults.json --fields samples/photo_bottle_fields.json --out samples/photo_bottle/drawing >/dev/null
$EP photo mark samples/photo_bottle/drawing --spec samples/photo_bottle.json >/dev/null
# photo mode, curved: an hourglass stool, a smooth curve through best-guess diameters, iso view dropped
$EP photo build samples/photo_stool.json --out samples/photo_stool/model >/dev/null
$EP make samples/photo_stool/model/model.step --config samples/defaults.json --fields samples/photo_stool_fields.json --out samples/photo_stool/drawing >/dev/null
$EP edit samples/photo_stool/drawing del-view iso >/dev/null
$EP photo mark samples/photo_stool/drawing --spec samples/photo_stool.json >/dev/null
# GD&T: suggestions are pending until confirmed; here every suggestion is accepted (the bot asks the user first)
$EP make samples/models/plate.step --config samples/defaults.json --fields samples/plate.json --out samples/gdt_plate >/dev/null
for g in G1 G2 G3 G4 G5; do $EP edit samples/gdt_plate accept-gdt $g >/dev/null; done      # datums A B C, flatness, position
$EP make samples/models/flange.step --config samples/defaults.json --fields samples/flange.json --out samples/flange_gdt >/dev/null
for g in G1 G2 G3 G4; do $EP edit samples/flange_gdt accept-gdt $g >/dev/null; done         # A face, B bore, flatness, perpendicularity
$EP edit samples/flange_gdt accept-gdt G5 --value 0.2 >/dev/null                             # position on the bolt circle, value changed
# edit round-trip on a copy of the plate
rm -rf samples/plate_edit; cp -r samples/plate samples/plate_edit
E="$EP edit samples/plate_edit"
$E add-section --axis y --at 40 >/dev/null                       # SECTION A-A through the pocket
$E add-dim --view A --axis z --from 6 --to 10 --side left >/dev/null   # pocket depth on the section
$E add-detail --view top --at H3 --radius 9 --factor 2 >/dev/null     # DETAIL B around hole H3
$E add-diameter --feature H3 --view B --text "%%c<> THRU" >/dev/null
$E dim-text D2 --tol "±0.05" >/dev/null
$E move-dim D9 --delta 4 >/dev/null
$E add-note "ANODIZE AFTER MACHINING" >/dev/null
$E set part_number=EP-1001-S >/dev/null
$E add-rev --rev B --desc "added section A-A and detail B" >/dev/null    # revision table row + title REV
# assembly: scan -> (bot asks the user which parts get drawings) -> assembly with the choices
$PY samples/make_assembly.py >/dev/null
rm -rf samples/shaft_support
$EP assembly-scan samples/models/shaft_support.step --config samples/defaults.json --fields samples/shaft_support.json --out samples/shaft_support >/dev/null
$EP assembly samples/models/shaft_support.step --config samples/defaults.json --fields samples/shaft_support.json \
    --items samples/shaft_support_items.json --out samples/shaft_support >/dev/null
$EP edit samples/shaft_support add-rev --rev A --desc "released for production" >/dev/null   # shared with the exploded sheet
# exploded sheet (sheet 2) is on via "exploded": true in the items file; auto explode directions, no overrides
$EP pdf samples/shaft_support >/dev/null
$EP pdf samples/plate_edit >/dev/null
for d in plate shaft bracket bracket_first_iges block plate_edit flange tapped_block fillet_plate csk_plate wedge gdt_plate flange_gdt; do $EP pdf samples/$d >/dev/null; done
$PY - <<'PY'
import ezdxf, glob, json
for f in sorted(glob.glob('samples/*/drawing.dxf') + glob.glob('samples/*/drawing/drawing.dxf') + glob.glob('samples/*/assembly/drawing.dxf') + glob.glob('samples/*/exploded/drawing.dxf') + glob.glob('samples/*/parts/*/drawing.dxf')):
    doc = ezdxf.readfile(f); a = doc.audit()
    layers = sorted(l.dxf.name for l in doc.layers)
    print(f'{f}: audit errors={len(a.errors)} fixes={len(a.fixes)} entities={len(doc.modelspace())} layers={layers}')
PY
