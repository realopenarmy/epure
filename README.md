# Épure

Épure is a headless Linux CLI that turns a STEP, IGES, BREP, or STL model into a dimensioned 2D shop drawing: an editable DXF, a PNG preview, and a PDF. It can edit that drawing, suggest threads and GD&T, and produce assembly sheets with a BOM, balloons, an exploded view, and a revision table. Geometry comes from CadQuery/OCP; DXF, PNG, and PDF come from ezdxf and matplotlib.

## Quick start

```bash
git clone https://github.com/realopenarmy/epure /workspace/epure && bash /workspace/epure/install.sh
```

`install.sh` is safe to re-run. It creates `venv/` beside itself and the `epure` wrapper. Both locate their own directory, so the same install works if you clone the repo somewhere other than `/workspace/epure`.

```bash
/workspace/epure/epure make /workspace/epure/samples/models/plate.step \
  --config /workspace/epure/samples/defaults.json \
  --fields /workspace/epure/samples/plate.json \
  --out /workspace/epure/jobs/plate
/workspace/epure/epure pdf /workspace/epure/jobs/plate
```

That writes `jobs/plate/drawing.dxf`, `preview.png`, and `drawing.pdf`.

Committed showcase previews (regenerate the full set with `bash samples/run_samples.sh`):

- [Plate after edits](samples/plate_edit/preview.png) · [PDF](samples/plate_edit/drawing.pdf)
- [Flange with GD&T](samples/flange_gdt/preview.png) · [PDF](samples/flange_gdt/drawing.pdf)
- [Shaft-support assembly](samples/shaft_support/assembly/preview.png) · [PDF](samples/shaft_support/assembly/drawing.pdf)
- [Exploded view](samples/shaft_support/exploded/preview.png) · [PDF](samples/shaft_support/exploded/drawing.pdf)
- [Combined set, 5 pages](samples/shaft_support/ep-5000-drawings.pdf)

# Épure: 3D CAD model to 2D shop drawing

Épure turns a 3D model into a dimensioned shop drawing. It writes an editable **DXF**, a **PNG preview**, and, once the drawing is approved, a **PDF**. Every drawing is driven by `spec.json`. To make a chat edit, the bot runs an `edit` op, which updates the spec and redraws everything in about 2–5 s.

It runs headless on the box with CadQuery/OCP (OpenCascade hidden-line removal) and ezdxf (DXF writing, plus matplotlib to render PNG and PDF). FreeCAD is **not** used: TechDraw needs a GUI-capable build, while OCP installs from pip and is all we need.

## Install (fresh box)
```bash
bash /workspace/epure/install.sh      # idempotent; makes ./venv and the ./epure wrapper
```
Pinned versions (Python 3.13.5, works on 3.10–3.13): `cadquery==2.8.0 cadquery-ocp==7.9.3.1.1 ezdxf==1.4.5 matplotlib==3.11.2 numpy==2.5.5 pillow==13.0.0 trimesh==5.2.2`.
apt (only if missing): `fonts-dejavu-core fonts-liberation libgl1 libglu1-mesa libxrender1 poppler-utils python3-venv` (and `python3.X-venv` for the interpreter in use).
Run with `/workspace/epure/epure ...` (wrapper) or `/workspace/epure/venv/bin/python /workspace/epure/epure.py ...`.
Do not put outputs in folders named build/dist/out/target (the box wipes them). Use e.g. `/workspace/epure/jobs/<part>/`.

## Inputs
| Format | Status |
|---|---|
| STEP/STP | Best. Exact geometry, units read from the file (converted to mm internally). |
| IGES/IGS | Works if it contains solids. Surface-only IGES gets a warning, and thru-hole detection may be wrong. |
| BREP | Works. Has no units, so mm is assumed; use `--model-units in` if not. |
| STL / OBJ | Works, but **approximate**: facets, no hole or slot recognition, and hidden lines are off by default. A note is added automatically. Assumes mm (`--model-units in`). |
| .f3d .sldprt .ipt .prt .catpart .x_t .3dm .fcstd .dwg ... | **Rejected** with exit 2 and `{"ok":false,"error":"... export STEP ..."}`. Ask the user to export STEP. |

Model orientation: the front view looks along +Y (the viewer is at -Y), the top view looks down -Z, and the right view looks from +X. If the part comes out in an odd orientation, ask the user to re-export it with Z up, or accept the result.

## Config files
**Per-person defaults** (`--config`, ask once, then keep it, e.g. `jobs/_people/<name>.json`):
```json
{"company": "ACME FABRICATION", "logo": "/abs/path/logo.png", "units": "in", "projection": "third",
 "drawn_by": "J. SMITH", "tolerances": ["X.X ±0.1", "X.XX ±0.01", "X.XXX ±0.005", "ANGLES ±0.5°"],
 "notes": ["BREAK ALL SHARP EDGES", "ALL DIMENSIONS IN {UNITS} UNLESS NOTED", "REMOVE ALL BURRS"],
 "sheet_family": "", "decimals": null, "hidden_lines": true, "iso_view": true}
```
**Per-drawing fields** (`--fields`). These may override anything above:
```json
{"part_name": "MOUNTING PLATE", "part_number": "EP-1001", "revision": "A", "material": "6061-T6",
 "finish": "CLEAR ANODIZE", "quantity": "4", "sheet": "auto", "scale": "auto"}
```
- Blank or missing fields print blank.
- `date` defaults to today.
- `units`: `in` (3 decimals, ANSI sheets) or `mm` (2 decimals, ISO sheets).
- `{UNITS}` in notes becomes INCHES or MILLIMETERS.
- Sheets: `ANSI_A ANSI_B A4 A3` (auto-pick) plus `ANSI_C A2` (explicit only).
- Auto-pick: the smallest sheet that holds the part at 1:1 or larger. Otherwise the larger sheet at the best standard scale (2:1, 1:2, 1:4, 1:5, ...).
- The iso view shrinks to 1:2 or 1:4 of the sheet scale (and is labelled) when it would otherwise force a smaller scale.

## CLI
```bash
E=/workspace/epure/epure
$E make model.step --config person.json --fields part.json --out jobs/EP-1001   # -> drawing.dxf, preview.png, preview_ids.png, spec.json
$E edit jobs/EP-1001 <op> [args]     # change spec + redraw (prints JSON status)
$E render jobs/EP-1001               # redraw after hand-editing spec.json
$E pdf jobs/EP-1001 [--out file.pdf] # PDF from the current DXF (vector, real sheet size)
$E edit jobs/EP-1001 list            # dump views, dims, notes, title, features (ids + coordinates)
$E assembly-scan asm.step --config person.json --fields asm.json --out jobs/EP-5000              # list parts + numbered preview, writes items.json
$E assembly      asm.step --config person.json --fields asm.json --items items.json --out jobs/EP-5000  # assembly sheet + part drawings
$E assembly ... --exploded            # also draw the exploded-view sheet (or "exploded": true in items.json)
$E pdf jobs/EP-5000                  # assembly: one PDF per sheet + combined <part no>-drawings.pdf (assembly, exploded, parts)
```
Every command prints JSON: `ok, dxf, preview, preview_ids, sheet, scale, fits_sheet, audit_errors, dim_errors, warnings, features, dims`.
- Show the user **preview.png**.
- Use **preview_ids.png** (same drawing, with dimension ids D#, feature ids H#/C#/S#, and view ids in colour) when they need to say which dimension to change, or look at it yourself before editing.
- Check that `audit_errors` is 0 and `dim_errors` is empty before sending.

### Edit ops (exact)
| op | args | notes |
|---|---|---|
| `add-dim` | `--view V --axis x\|y\|z --from REF --to REF [--side below\|above\|left\|right\|inside] [--offset MM] [--at MM] [--text T] [--tol T]` | Linear dimension along a model axis. The axis must lie in the view plane. Default side/offset stacks outside the existing dims. `inside` draws across the part at `--at` (model mm along the other view axis), with text outside. |
| `add-diameter` | `--feature H#\|C# [--view V] [--text T]` | Hole/cylinder callout. Default view is the one where it shows as a circle. |
| `add-leader` | `--view V --text T (--feature ID \| --point u,v)` | Free note with a leader arrow (point in view-local model mm, see `list`). |
| `del-dim` | `D# [D# ...]` | |
| `move-dim` | `D# [--offset MM] [--delta MM] [--side S] [--dist MM] [--at MM]` | offset/delta = paper mm from the view edge (stack position). For callouts, `--side` picks the side the leader goes to and `--dist` sets how far out. |
| `dim-text` | `D# [--text T] [--tol T]` | Override text. `<>` = measured value. `%%c` = Ø. Callout tokens: `<depth> <cb> <cbdepth> <w> <l>`. Example: `--tol "±0.05"` or `--text "<> REF"`. |
| `add-note` / `del-note N` / `set-note N T` | | General notes block (1-based). |
| `set` | `key=value ...` | Title fields: `company logo part_name part_number revision material finish quantity drawn_by date`. Drawing: `sheet=A3\|ANSI_B\|auto`, `scale=1:2\|auto`, `projection=first\|third`, `units=in\|mm`, `decimals=3`, `hidden_lines=false`, `tolerances="X.X ±0.1;X.XX ±0.01"` |
| `add-section` | `--axis x\|y\|z --at MM\|REF [--label A] [--parent V]` | Full section through an axis-aligned plane. Hatched (ANSI31). The cut line with arrows and letters is drawn on the parent view. y-sections look like the front view, x like the right view, z like the top view. |
| `add-detail` | `--view V --at REF\|u,v --radius MM [--factor 2] [--label B]` | Enlarged circular detail. You can put a callout on it with `add-diameter --view B`. |
| `del-view` | `ID` | top/right/iso/section/detail (not front). Removes its dims. |
| `add-view` | `top\|right\|iso` | |
| `redim` | | Re-run auto-dimensioning. Keeps manual dims (ids renumbered). |
| `accept-thread` | `H# [--callout "M8x1.25-6H"] [--depth N]` | Confirm a thread suggestion. Applies to every hole in that suggestion. `--callout` overrides the suggested callout. `--depth` is the thread depth in drawing units (blind holes only); the default is hole depth − 3 pitches, trimmed to about max(1.5×nominal, 75% of hole depth). |
| `reject-thread` | `H#` | The holes stay plain Ø callouts (e.g. Ø8.5 clearance holes, not M10 tap drill). |
| `accept-gdt` | `G# [--value N]` | Confirm a GD&T suggestion (datum or frame). `--value` overrides the tolerance (drawing units). |
| `reject-gdt` | `G#` | Drop it. Rejecting a datum also removes that letter from frames still pending. |
| `add-datum` | `TARGET LETTER` | Manual datum feature symbol. TARGET = a face (`zmin`, `xmax`, `z=12.5` in model mm) or a feature (`H3`, `C1`, `P1`). Letters I, O, Q are not allowed. Re-using a letter moves it. |
| `add-fcf` | `TARGET --type position\|flatness\|perpendicularity\|parallelism\|profile\|runout\|concentricity --value N [--datums A,B,C] [--mmc] [--dia\|--no-dia]` | Manual feature control frame. `--value` in drawing units. Ø is automatic for axis features (holes, bosses) under position/perpendicularity/parallelism/concentricity. On a hole it covers the hole's whole callout group. |
| `del-gdt` | `T# \| LETTER` | Delete a frame (T#) or a datum (letter). |
| `add-rev` | `--rev B --desc T [--by INITIALS] [--date YYYY-MM-DD]` | New row in the revision table (description upper-cased; by = drawn_by, date = today by default). Also sets the title-block REV. |
| `del-rev` | `REV` | Removes that row (not the only one). Title REV becomes the last remaining row. |
| `set-rev` | `REV [--new-rev X] [--desc T] [--by I] [--date D]` | Fix a row. |
| `bom-set` | `ITEM key=value ...` (assembly) | Keys: `part_number description material finish qty drawing=yes\|no`. Updates the BOM and the part drawing's title block; `drawing=yes` makes the part drawing if it has none. |
| `move-balloon` | `ITEM [--angle DEG] [--dist MM] [--view V] [--reset]` (assembly) | Angle = direction from the view centre (0 right, 90 up); dist = extra paper mm outwards. |
| `add-balloon` / `del-balloon` | `ITEM [--view front\|top\|iso]` (assembly) | Extra balloon in another view / remove one. |
| `exploded` | `on\|off` (assembly) | Turn the exploded-view sheet on/off. Sheet numbers of every sheet in the set are updated. |
| `explode` | `ITEM [--dir x\|y\|z\|-x\|-y\|-z] [--dist MM] [--inst N] [--reset]` (assembly) | Fix the auto direction/distance (model mm, not scaled) for all copies of an item, or one copy (`--inst`, numbering in `list` → `exploded.plan`). Turns the sheet on. |
| `explode-scale` | `F` (assembly) | Multiply every auto distance (1.0 = default; 1.5 = more spread). |
| balloon ops on the exploded sheet | `edit <job>/exploded move-balloon 4 --angle 120` or `edit <job> move-balloon 4 --sheet exploded ...` | Same as on the assembly; settings survive re-explodes. |
| `list` | | Views, dims, features, `thread_suggestions`, `threads`, `gdt_suggestions` (with status), and accepted `gdt`. |

**REF** can be:
- a number (a coordinate on the dim axis, model mm), e.g. `6`, or `x=12.5`
- a feature id: `H1` (hole centre), `C1` (cylinder axis), `S1` (slot centre), `S1.1`/`S1.2` (slot end centres), `BC1` (bolt-circle centre), `F1` (fillet axis/centre), `K1` (chamfer), `P1` (slanted face)
- `MIN`, `MAX`, `CENTER` (bounding box)
- `x,y,z` (a raw point)

Feature coordinates are in `edit DIR list` under `features`. Auto dims use the bounding-box minimum as the datum (baseline dimensioning).

### Chat-edit recipe for the bot
1. Look at `preview_ids.png` to map the user's words ("the 55 on the left", "that hole") to ids.
2. Run one `edit` op per request. To redo a dim, `del-dim` it and then `add-dim`.
3. **Threads:** after `make`, if the JSON has `thread_suggestions` with status `pending`, show the user each `text` line (e.g. `H2,H3: Ø6.80 blind 18.00 deep hole looks like M8x1.25 tap drill (likely)`) and ask whether to call it M8x1.25-6H. Only after they answer, run `accept-thread H2 [--callout ...] [--depth ...]` or `reject-thread H2`. Never accept on your own; nothing threaded is drawn until accepted.
4. **GD&T:** after `make`, the JSON has `gdt_suggestions` (pending) as lines like `G5: [⌖ | Ø0.10 Ⓜ | A | B | C] on H1 - <plain-English reason>`. Show them to the user, ask which to keep and whether to change any value, then run `accept-gdt G# [--value ...]` or `reject-gdt G#` for each. Never accept on your own; nothing is drawn until accepted. For extra frames the user asks for, use `add-datum` / `add-fcf`.
5. Send `preview.png` back each round. When they approve, run `pdf` and send `drawing.pdf` plus `drawing.dxf` (and `logo.*` if used, since the DXF IMAGE references it by relative path).

## What it draws
- **Views:** front, top, and right, placed by the third-angle (default) or first-angle convention and aligned to each other, plus an isometric view. Sections and details go in a column to the right or a row below, whichever fits better.
- **Layers:** VISIBLE (0.5 mm), HIDDEN (dashed), CENTER (chain), DIMENSIONS, TITLEBLOCK, NOTES, HATCH. Geometry is **true size** in drawing units (in or mm), and the frame and title block are scaled by 1/scale, so measuring in AutoCAD, Fusion, or LibreCAD gives real sizes. Dimensions are real DIMENSION entities using dimstyle `EPURE` (DIMSCALE = 1/scale). Linetype patterns are pre-scaled, with LTSCALE = 1.
- **Auto dims:**
  - Overall W/H/D, plus every axis-aligned planar face level (steps, pockets, flange thickness). Each axis is placed in the view where most of those levels are visible lines.
  - Hole positions from cylindrical faces, plus grouped hole callouts (`4X Ø8.50 THRU`, `Ø5.00 x 15.00 DEEP`, counterbores as a second line `CBORE Ø11.00 x 6.00 DEEP`).
  - Slot end-centre positions plus `2X SLOT w x l`.
  - Shaft/boss diameters as across-the-part dims.
  - Centre marks and centrelines.
  - At most 7 levels per axis; the largest faces win. Hole and counterbore floors are not dimensioned as levels (the depth is in the hole callout).
  - **Fillets (F#):** cylindrical and toroidal blend faces get `R3.00`, grouped as `4X R6.00`, in the view where the blend shows as an arc. A fillet smaller than 0.5 mm on paper is skipped and listed in `skipped`.
  - **Chamfers (K#):** planar bevels → `2.00 X 45°` or `1.00 X 2.00` (unequal legs), in the view where the chamfer is edge-on; conical chamfers on shafts → `1.00 X 45°` or `1.00 X 30°`. Hole chamfers join the hole callout (`1.50 X 45° CHAMFER BOTH ENDS`). Grouped with `nX`.
  - **Bolt circles (BC#):** 3+ identical holes, equal radius and even spacing → `6X Ø8.50 THRU / EQ SP ON Ø110.00 BC` plus a centreline circle. Only the BC centre is located (plus `1ST HOLE a° FROM HORIZONTAL` when no hole sits on an axis). Uneven spacing falls back to X/Y positions with a warning.
  - **Countersinks / counterdrills:** a cone at a hole entry joins the hole callout: `4X Ø6.60 THRU / Ø12.00 X 90° CSK` (`BOTH ENDS` when both sides are countersunk). Included angles snap to 60/82/90/100/118/120° when within 1°, otherwise the measured angle is shown. A cone between a small hole and a larger blind hole is a counterdrill: `CDRILL Ø11.00 x 4.00 DEEP X 118°`. Small hole-edge cones stay `1.50 X 45° CHAMFER`.
  - **Slanted faces (P#):** a face tilted about one axis gets an angular dimension in the view where it is edge-on, measured from the nearest horizontal/vertical edge (or its extension line). The slope's start point gets a linear dim if it isn't already dimensioned, placed in the view where that point shows as an edge.
  - **GD&T (suggest, then confirm):** see below.
  - **Threads:** suggested from tap-drill sizes (metric coarse M2–M24, UNC/UNF #4–1") or found as text in the STEP file. Not drawn until `accept-thread`; then the callout reads `2X M8x1.25-6H x 13.50 DEEP / Ø6.80 DRILL x 18.00 DEEP` and a thin 3/4 circle shows the major diameter in the end view.
- **Title block** (bottom right): company + logo, part name, part no., rev, material, finish, qty, units, drawn by, date, scale, size, sheet 1 of 1, tolerance block, and the projection symbol + words. The notes block is at the bottom left.

## Revision table
Every drawing has an ASME-style REVISIONS table at the top right (REV, DESCRIPTION, DATE, APPROVED). The first row comes from the REV field (`-` when empty) as INITIAL RELEASE. `add-rev` adds rows and bumps the title REV. The table grows downwards; views are moved, or the scale/sheet changes, so nothing overlaps it. Long descriptions shrink, then wrap to 2 lines.

## Assemblies (choose first)
1. `assembly-scan` reads the STEP/IGES/BREP assembly structure (XCAF names + placements; files without structure are split into solids named SOLID n). Identical parts (same prototype, or same volume/area/inertia) are merged with a quantity. Items are numbered by size; standard hardware (bolt, screw, nut, washer, pin, ... by name, or hex/thread-like shape) goes last, `hardware: true`, `drawing: false`. It prints the items and writes `items.json` + `scan_preview.png` (the assembly sheet with balloons and BOM).
2. The bot shows the preview and asks which parts get drawings and each one's part no / material / finish, then writes those into `items.json` (match on `item`; a name mismatch is warned).
3. `assembly` writes `assembly/` (no dimensions: iso as the main view + front + top, one balloon per item placed outside the views in angle order (leaders may still cross the part on crowded views - use `move-balloon`), BOM above the title block) and `parts/NN-name/` for each item with `drawing: true` - a normal `make` drawing (qty per assembly in the title block, part no default `<asm part no>-NN`). Part thread/GD&T suggestions come back per item and still need confirming (`edit parts/NN-name accept-gdt G1`).
- Per-item fields can also come from the fields file: `{"items": {"2": {"material": "4140 STEEL"}}}`. Extra per-item keys: revision, notes, sheet, scale, tolerances, decimals.
- Parts inherit units, decimals, projection, tolerances, company, logo, drawn_by, date from the assembly.
- Editing a part's title (`edit parts/02-shaft set material=...`) flows into the BOM on the next assembly render (`render jobs/EP-5000`).
- Balloons are anchored on an edge the viewer can really see (ray test). Items hidden in the iso (e.g. nuts under a plate) get their balloon in the front or top view.
- **Exploded sheet (optional, off by default)** in `<job>/exploded/`: one large iso view of the parts pulled apart, dashed trail lines (CENTER layer) from where each part sits to where it is drawn, balloons with the same item numbers as the BOM, note `SEE SHEET 1 FOR PARTS LIST` (no second parts list). Same title block and revision history as the assembly (`add-rev` on either folder goes to the assembly).
  - Auto directions: the biggest part stays; parts touching it lift off along the contact face normal; parts that only touch moved parts (a shaft in brackets) ride with them and then clear them the same way; loose parts move away from the centre; fasteners move along their own axis (head first for bolts/screws; nuts/washers away from the part they sit on) and ride with the part they fasten. `edit <job> list` → `exploded.plan` says what each copy does and why.
- **Sheet numbers:** the set is numbered assembly = 1, exploded = 2 (when on), then each part drawing by item number, `n OF N` in every title block. Turning the exploded sheet or a part drawing on/off renumbers and redraws the others.
- Dimension ops are refused on the assembly and exploded sheets. The job folder can be moved or copied; paths are re-pointed on the next command.

## Honest limits
- **Assemblies:** one sheet for the assembly (no exploded view, no section, no sub-assembly sheets: nested sub-assemblies are flattened into parts). Hardware detection is by name, or simple shapes (hex nut, plain washer, headed bolt); anything else is a normal part until `bom-set N drawing=no`. Balloon leaders can cross geometry on crowded views. Mesh files (STL/OBJ) can't be assemblies. Part drawings use each part's own model axes, so a part may be drawn in a different orientation from how it sits in the assembly.
- **Exploded view:** straight moves along x/y/z only (no rotation, no staged/multi-step explodes); parts are not checked for collisions after moving, so overlapping parts may need `explode`/`explode-scale`. Auto picks the smallest standard scale that fits (often 1:2 on A3). Trail lines can cross other parts.
- **Revision table:** one table per drawing (the exploded sheet shares the assembly's); no zone column; the assembly and its parts have separate revision histories.
- **Mesh models:** fillets, chamfers and slopes are not detected (facets would look like dozens of chamfers).
- **Auto-dimensioning is a first draft, not drafter-grade.**
  - It is complete for prismatic parts with axis-aligned faces and holes.
  - It does **not** dimension faces tilted on two axes (compound angles; counted in a warning), chamfers on such tilted edges, or non-axis-aligned holes.
  - **GD&T:** suggestions are a starting point from geometry only (no knowledge of how the part mates). No composite frames, datum targets, modifiers other than Ⓜ on the tolerance, projected zones, or Ⓜ on datum references. Hole patterns not square to datum A get no position suggestion. Face symbols need the face seen edge-on in front/top/right. The bolt-circle Ø inside a hole note can't be boxed, so it reads `EQ SP ON Ø110.00 BC BASIC` instead. Basic boxes are drawn as a frame around the dimension text.
  - **Countersinks:** only coaxial full cones at a hole end. Counterdrill depth is to the end of the full diameter.
  - **Threads:** OCCT does not import STEP AP242 thread/hole features, so the file is only scanned for text like `M8x1.25`. Otherwise threads are guesses from the drill size. Some sizes are ambiguous (Ø8.5 is both the M10 tap drill and the M8 clearance hole; the suggestion says so). Side views show no thread lines, only the end-view 3/4 circle.
  - **Fillets:** called out only where the arc is visible as an arc. Torus corner patches (where two fillets meet) and variable-radius blends are not called out; very wide torus blends are counted in a warning.
  - **Chamfers:** only bevels on axis-aligned edges with legs ≤ 25% of the part size; compound-angle chamfers are missed.
  - **Bolt circles:** need even spacing; 4 holes on a square are dimensioned X/Y (no warning), which is also standard.
  - Baseline stacks can get busy on complex parts. Use `del-dim` and `move-dim`, or `set hidden_lines=false`.
  - Leaders can cross other geometry on dense parts. Fix with `move-dim D# --side ...`.
- **Sections:** only full sections on planes normal to X, Y, or Z. There are no offset, half, aligned, or broken-out sections, and no hidden lines inside sections.
- **Details:** clipped and redrawn as polylines, so arcs become polylines inside a detail.
- **Mesh (STL/OBJ):** overall and face-level dims work, but holes aren't recognised (no callouts or centre marks), curved surfaces show facet edges, and values are only as good as the mesh. Always ask for STEP.
- **Iso view:** sharp and silhouette edges only (no tangent edges).
- **One sheet only.** Huge parts go to 1:N scale on B/A3 unless `sheet=ANSI_C|A2` is set.
- **Rendering:** fonts render as DejaVu/Arial-substitute in PNG/PDF. DXF text uses style `EPURE` (arial.ttf), which CAD apps map to Arial.
- **Checking:** DXFs are validated with `ezdxf` audit (0 errors on all samples). They were not opened in AutoCAD or LibreCAD on the box.

## GD&T
- **Suggestions** (`gdt_suggestions` in spec.json and the JSON output, all `pending`, never drawn until accepted):
  - Datum A = the biggest outside flat face (faces within 10% count as equal; bottom first).
  - Datum B = a main through-bore square to A (≥ 20% of the part width), else the larger min-side face square to A. Datum C = the remaining min-side face (min sides, because the baseline dims start there).
  - Flatness on A (0.05 mm / 0.002" up to 150 mm, else 0.10 / 0.004"), perpendicularity Ø0.05 of the main bore to A, and position Ø0.10 (0.005") to A|B|C on each hole pattern square to A. Ⓜ (MMC) on plain thru holes; no Ⓜ on blind or tapped-looking holes.
  - Turned parts (a long main diameter, no big flat faces): datum A = the main diameter, circular runout 0.05 to A on the other coaxial diameters.
  - Each comes with a plain-English `text` to show the user.
- **Drawing:** frames are boxed on layer `GDT`, symbols drawn as lines (they show in any CAD font). Frames for holes hang under the hole callout, frames for bosses under the Ø dimension. Face datums and face frames sit on the face where it is seen edge-on (triangle + stem + boxed letter; frames with a leader arrow), outside the dimension stacks.
- With any position frame, the hole location dims (and their bolt circle) are boxed as **basic**, and notes are added: `DIMENSIONING AND TOLERANCING PER ASME Y14.5-2018`, `BOXED DIMENSIONS ARE BASIC`.

## JSON output (make / edit / render)
`audit_errors`, `dim_errors`, `skipped` (callouts left off, with reason), `warnings` (feature + dim warnings, e.g. uneven hole circle), `thread_suggestions` (pending `text` lines for the user), `threads` (accepted), `gdt_suggestions` (pending, `G#: [frame] on target - reason`), `gdt` (accepted datums and frames), plus the feature lists (`holes cyls slots fillets chamfers bolt_circles slopes`).

## Samples (`samples/`, regenerate with `bash samples/run_samples.sh`)
- `models/` holds plate.step/.stl/.obj, shaft.step, bracket.step/.iges/.brep, block.step, flange.step, tapped_block.step, fillet_plate.step, csk_plate.step, wedge.step, made by `make_samples.py`.
- `plate/` is a metric A3 1:1 plate with 4 holes and a pocket. `plate_edit/` is the same part after edits: section A-A, depth dim, detail B with callout, a tolerance, a moved dim, a note, and a rev change, plus a **PDF**.
- `shaft/` is a stepped shaft with a cross hole. `bracket/` is an inch L-bracket with slots on ANSI B. `bracket_first_iges/` is first-angle from IGES with a logo.
- `flange/` is a hub flange: 6-hole bolt circle, R3/R2 fillets, Ø40 bore chamfered both ends (A4 1:2). `flange_reject/` is the same with the M10 guess rejected.
- `tapped_block/` has M8 blind and M6 thru tapped holes (both accepted) and 4X 1 mm edge chamfers. `fillet_plate/` has 4X R8 corners and 3 unevenly spaced holes (warning, X/Y fallback).
- `csk_plate/`: 4X Ø6.6 with 90° CSK, 2X Ø5.5 countersunk both ends, 1 counterdrill. `wedge/`: 30° slope with angular dim + slope-start dim.
- `gdt_plate/`: the plate with datums A/B/C, flatness on A and position Ø0.10 Ⓜ on the 4 holes (basic dims boxed). `flange_gdt/`: flange with A = bottom face, B = bore, flatness, perpendicularity of the bore and a position Ø0.20 Ⓜ on the bolt circle.
- `shaft_support/`: assembly (base plate, shaft, 2 identical brackets, 4 bolts + 4 nuts) made by `make_assembly.py`. `scan_preview.png` + `items.json` from the scan, `assembly/` sheet 1 with balloons, BOM and rev A, `exploded/` sheet 2 (auto explode), `parts/01..03` sheets 3-5, and `ep-5000-drawings.pdf` (5 pages).
- `plate_edit/` also shows a second revision row (B).
- `block/` has a counterbore, a blind side hole, and fillets. `plate_stl/` and `plate_obj/` are mesh inputs.

## License

Épure is MIT licensed. See [LICENSE](LICENSE).
