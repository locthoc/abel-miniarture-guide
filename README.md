# Foamex Miniature Viewer

A static web viewer for foamex (PVC foam board) miniatures: a **3D assembly view** and a **2D cutting blueprint**, with selection linked between them.

The repo ships with the `5-c` project (converted from Blender) and a dummy example in `examples/dummy-house/`
(open it with `?project=examples/dummy-house/parts.json`).

## Features
- 3D viewport: orbit/zoom, 1 cm ground grid, explode slider, reset view
- Blueprint: one A4 sheet per thickness, true 1:1 size in mm, with a 50 mm check bar
- Click a part in 3D, on the sheet, or in the parts list and it highlights everywhere
- Filter by thickness (ghosts the other parts in 3D)
- Download the sheet SVG, or **Print 1:1** (prints black on white)

## Run locally
The page loads files with `fetch`, so opening `index.html` directly won't work. Start a small server:

```bash
python -m http.server 8000
# open http://localhost:8000
```

## Deploy on GitHub Pages
1. Push this folder to a repo.
2. Settings → Pages → Deploy from branch → `main` / root.
3. Open `https://<user-or-org>.github.io/<repo>/`.

Pages sites are **public** even when the repo is private (unless your org has GitHub Enterprise Cloud private Pages).

## Project format
```
parts.json              list of parts + sheet files (the viewer reads this)
models/house.glb        3D model; each node name = part id
sheets/sheet-3mm.svg    cutting sheet; each piece is <g data-part="part id">
```
Load a different project with `?project=projects/kiosk/parts.json`.

## Making a new project from Blender
1. Model every foamex plate as a **flat plane** = its **outer surface** (one object per plate, real size in mm).
   Foamex grows inward, against the face normal.
2. Put `miter` / `mitre` / `mitered` anywhere in the name of plates that join with miters.
   - Tagged meets tagged: both mitered (orange dashed edge on the sheet; 45° unless a number is shown).
   - Tagged meets untagged: butt joint. The untagged plate is shortened 5 mm so it sits against the tagged one.
   - Untagged meets untagged: the smaller plate is shortened.
   - Free edges: straight cuts.
3. Figures (human dummies) can stay in: anything not flat, or named dummy/human/figure/body, is shown in 3D only.
   Add `set` to a name (e.g. `floor_set`) to show a flat object in 3D without cutting it.
4. Export **glTF Binary (.glb)**, Materials: *No export*, *Apply Modifiers* on.
5. Convert:
   ```bash
   pip install trimesh shapely mapbox_earcut
   python tools/convert_glb.py path/to/model.glb --out . --thickness 5 --sheet 1220x2440
   ```
   Plates bigger than a sheet are split into panels (A1, A2, B1…). The converter prints notes
   (flipped normals, oversize plates, odd joints); they also show under the parts list.

## Viewer tips
- 3D: left-drag rotate, right-drag pan, scroll zoom, click to select. **X-ray** sees through big plates (like a ceiling).
- Blueprint: scroll to zoom, drag to move, double-click or **Fit** to reset. ◀ ▶ or the dropdown switch sheets.

## Printing tips
- Print at **100% / Actual size**, never "Fit to page". Measure the check bar on the sheet.
- Full 1220 × 2440 sheets need a large-format printer or plotter. Otherwise use the dimensions to mark out the board.
- Foamex is PVC: cut with a knife, plotter or CNC. **Do not laser-cut it** (it releases toxic, corrosive gas).
