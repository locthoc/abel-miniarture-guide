# ABEL Miniature Guide · v0.6.3 beta

A static web viewer for foamex (PVC foam board) miniatures. The start screen lists every model;
each model opens a **3D assembly view** and **2D cutting sheets**, with selection linked between them.

## What's new in v0.6.3 beta
- **Clean corner angles**: after snapping to even millimetres, free slanted edges turn to the nearest multiple of 15°
  (if within 3°), so corners come out exactly 45 / 90 / 135… Straight edges keep their even ends; slanted edge
  lengths are shown rounded to even. Joined slanted edges stay as modelled (so plates still meet) and get a note.
- Tutorial House rebuilt with even dimensions and a 45° roof (152 × 100 mm, 76 mm walls, ridge at 126 mm)
- Accent colour is a redder burgundy (oxblood); start-page cards show "last opened on <date, time>"

## What's new in v0.6.22 beta
- 5-CRAFTING replaced with 5-c-final2
- **Even millimetres everywhere**: every plate corner snaps to a 2 mm grid in 3D, so sizes and the cut itself are
  2, 4, 6… mm. Butt-joint shortening rounds up (5 mm → 6 mm) so plates are a hair loose, never too tight.
  Diagonal edges can't be exactly even by length; their numbers are rounded to even.
- **Tighter sheets**: pieces sit 3 mm apart, with name and size printed inside each piece
- **Pin spacing**: each pin joint gets a measurement of the gap between the two plates it bridges (not its length)

## What's new in v0.6.2 beta
- **Label density** slider (with Measurements on): how much of a measurement may be hidden behind plates
  before it fades away, 1 % (fewest, cleanest) to 50 % (most labels). Remembered per browser.
- **Split alerts**: when a plate is bigger than a 40 × 60 cm sheet and gets split into panels, the viewer shows a
  banner (click a name to select that plate), the parts list tags it "split · N panels", and the start page card
  shows "⚠ N split".

## What's new in v0.6.1
- 5-CRAFTING updated to the final model (5-c-final1). Converter fixes: zero-area triangles left over from
  Blender are ignored on plates; plates in the same plane side by side, or touching only a small part of an
  edge, no longer count as a joint (the floor stays full size).
- Fonts: **Barlow** for the interface, **Barlow Condensed** for measurements, numbers and sheet text
- A measurement only hides when **half or more** of it is behind plates; a thin part crossing it no longer hides it

## What's new in v0.6
- **⛶ Fullscreen** button on both the 3D view and the blueprint (Esc or ✕ to leave)
- The 3D world is 15 % darker (measurements keep their brightness)
- A measurement that is blocked by a plate, even a little, hides right away (0.15 s fade) and comes back the same way
- Accent colour is now burgundy red everywhere (UI, selection, sheet hatching)
- Tutorial step 7 text updated; conversion notes are no longer shown in the viewer (still in `parts.json`)

## What's new in v0.5.79
- Start page sorted by **most recently opened** (remembered per browser), then newest conversion. Nothing is pinned.
- Thumbnails: a text card for the Tutorial House, a render for 5-CRAFTING (`projects/<id>/thumb.jpg|png|svg`).
- 5-c renamed **5-CRAFTING**; old `#5-c` links redirect (`meta.json` → `"aliases": ["5-c"]`).
- All miters are 45°: no bevel numbers anywhere. The Tutorial House roof is now untagged (butt-jointed).
- Sheets: only "Sheet N / M" at the top, no legend, no check bar, no "THIS SIDE UP"; the board shows as a light 20 % white panel.
- Blueprint pane: SVG and Print buttons removed (send sheets from `projects/<id>/sheets/` yourself); navigation hint added.
- Top bar: Reset view removed; **▶ Replay tutorial** in orange. The tour says "Skip tutorial".

## What's new in v0.5.78
- New light map (grey edges, no black). Measurements never go below **30 %**: inside the spotlight as before,
  outside it the map's grey drives opacity from 85 % down to 30 % at the darkest grey.
- Labels blur up to **4 px** (at 30 %). **Lines only fade, never blur**; the Line blur button is gone.
- 3D header: Measurements + Text on the left, View Pre-vis angle on the right.

## What's new in v0.5.77
- **Tutorial House**: a proper example model (150 × 100 × 120 mm, 5 mm foamex, mitered walls and roof with
  real 48° / 66° roof bevels, butt-jointed base, door, windows, figure, pin joints, pre-vis camera).
  Pinned first on the start screen. Rebuild it with `python tools/make_tutorial.py`.
- **1-minute guided tour** (7 steps) that opens the first time someone opens the Tutorial House (remembered per
  browser). Next / Back / Skip tour, Esc to close, **Replay tour** button in the top bar.
  Steps live in `projects/tutorial/tour.json`; any project can have its own `tour.json`.
- `projects/<id>/meta.json` (optional): `{"pinned": true, "label": "Tutorial", "description": "..."}` for the start screen.
- New, rounder spotlight map.
- Sheets: upright plates are drawn the right way up (walls, gables, doors at the bottom), and "THIS SIDE UP"
  is placed where it fits, clear of holes, hatching and angle labels.

## What's new in v0.5.7
- Measurements use a **spotlight**: `assets/spotlight-map.png` is stretched over the 3D view (white = spotlight).
  Inside it, the 6 nearest visible measurements go 100 % → 85 %; the rest 85 % → 25 % by depth.
  A soft curve takes the spotlight edge from 85 % to 25 %, and outside it goes 25 % → 0 % where the map is black.
  Lines fade the same way along their length. Swap the PNG to reshape the spotlight.
- **Blur**: below 85 % opacity, labels blur more as they fade (up to 6 px at 0 %)
- **Line blur** button (shown with measurements) to blur faded lines too, or keep them crisp

## What's new in v0.5.6
- The measurement fade only counts what you can see: measurements on screen and not hidden behind a
  plate set the nearest and farthest. It updates every frame, including while the camera is flying
  (pre-vis / reset moves). X-ray plates don't hide measurements.

## What's new in v0.5.5
- Measurements: the nearest 6 are always fully readable; the rest fade on a curve to 0 % at the farthest
- Measurement lines fade along their own length (the end nearer the camera is stronger)
- **Text** slider for measurement size (50–250 %), shown while measurements are on
- Controls guide is always there, at 40 % until you hover it (the hide button is gone)
- Going back to the pre-vis angle takes 0.8 s and orbits around instead of sliding straight

## What's new in v0.5
- **Measurements** button: dimension lines on every plate, the overall size and the figure height.
  Opacity follows depth (nearest 100 %, farthest 0 %, relative to the current view) and plates block
  measurements behind them. The selected part's measurements stay at full strength.
- **Pin joints**: objects named with pin / joint / hinge are shown grey and see-through, stay in place when
  exploding, are clickable, and are listed under *Hardware* with size and count. They are never on the sheets.
- Objects named like **40x60** (any number x number) are Blender-only frames and are skipped.

## What's new in v0.4.1
- Blender cameras: export with *Include → Cameras* on. The model opens from that camera, and a
  **View Pre-vis angle** button flies back to it (one camera per file; the button is greyed out if there is none)
- All camera moves (Camera view, Reset view) ease in and out over 0.5 s

## What's in v0.4
- Model browser start screen (`projects/projects.json`), each model at its own link: `…/#<id>`
- Explode slider inside the 3D view (right edge)
- Foamex sheets 40 × 60 cm by default; plates bigger than a sheet are split into panels (A1, A2, B1…)
- Mitered edges marked with an orange diagonal-hatch band
- Every corner that isn't 90° is labeled with an arc and its angle
- `--scale` option in the converter

## Folder layout
```
index.html, app.js, style.css
projects/projects.json          list shown on the start screen (generated)
projects/<id>/parts.json        part list, sheets, notes
projects/<id>/models/<id>.glb   3D model (geometry only)
projects/<id>/sheets/sheet-NN.svg
projects/<id>/thumb.png         optional preview image for the start screen
projects/<id>/description.txt   optional one-line description for the card
tools/convert_glb.py            Blender .glb -> project
```

## Run locally
```bash
python -m http.server 8000
# open http://localhost:8000
```
(Opening `index.html` directly won't work: the page loads files with `fetch`.)

## Deploy on GitHub Pages
Upload everything to the repo root (including `.nojekyll`), then Settings → Pages → Deploy from branch → `main` / root.
Pages sites are public.

## Adding a model from Blender
1. Model every foamex plate as a **flat plane** = its **outer surface** (one object per plate, real size).
   Foamex grows inward, against the face normal.
2. Put `miter` / `mitre` / `mitered` anywhere in the name of plates that join with miters.
   - Tagged meets tagged: mitered (orange hatch on the sheet; 45° unless a number is shown).
   - Tagged meets untagged: butt joint, the untagged plate is shortened 5 mm.
   - Untagged meets untagged: the smaller plate is shortened.
   - Free edges: straight cuts.
3. Figures (human dummies): anything not flat, or named dummy/human/figure/body, is shown in 3D only.
   Pin joints: name them with pin / joint / hinge. Scale frames named like `40x60` are ignored.
   Add `set` to a name (e.g. `floor_set`) to show a flat object in 3D without cutting it.
4. Export **glTF Binary (.glb)**, Materials: *No export*, *Apply Modifiers* on.
   Tick *Include → Cameras* to bring your camera angle(s) along (use a real Camera object).
5. Convert:
   ```bash
   pip install trimesh shapely mapbox_earcut
   python tools/convert_glb.py path/to/model.glb --scale 1 --sheet 400x600
   ```
   This creates `projects/<file name>/` and updates the start screen. Options: `--id`, `--name`,
   `--scale 0.1`, `--thickness 5`. Notes (flipped normals, oversize plates, odd joints) print in the
   terminal and show under the parts list.
6. Thumbnail: save an image as `projects/<id>/thumb.png` and run `python tools/convert_glb.py --reindex`.

## Viewer tips
- 3D: left-drag rotate, right-drag pan, scroll zoom, click to select. **X-ray** sees through big plates.
- Blueprint: scroll to zoom, drag to move, double-click or **Fit** to reset. ◀ ▶ or the dropdown switch sheets.

## Printing tips
- Print at **100% / Actual size**, never "Fit to page". Measure the check bar on the sheet.
- 40 × 60 cm sheets are bigger than A3: use a large-format printer, or mark out the board from the dimensions.
- Foamex is PVC: cut with a knife, plotter or CNC. **Do not laser-cut it** (it releases toxic, corrosive gas).
