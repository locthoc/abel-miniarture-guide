"""
Generate a dummy foamex miniature project:
  models/house.glb      assembled 3D model (one node per part, named by part id)
  parts.json            part list + 2D outlines + sheet placements
  sheets/sheet-<t>mm.svg 1:1 cutting sheets (A4 landscape), one per thickness

All dimensions in millimetres. The GLB is written in metres (glTF standard).

In the real pipeline this file is replaced by a Blender export script that
reads each flat object's outline + thickness. The output format stays the same.
"""
import json, math, os
import numpy as np
import trimesh
from shapely.geometry import Polygon, box
from shapely.ops import unary_union
from shapely import affinity

ROOT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "examples", "dummy-house")
PROJECT = "Dummy House"
SHEET_W, SHEET_H = 297.0, 210.0   # A4 landscape
MARGIN, GAP = 10.0, 6.0

# ---------- transforms ----------
def T(x, y, z):
    m = np.eye(4); m[:3, 3] = [x, y, z]; return m

def Rx(deg):
    a = math.radians(deg); c, s = math.cos(a), math.sin(a)
    return np.array([[1, 0, 0, 0], [0, c, -s, 0], [0, s, c, 0], [0, 0, 0, 1]])

def Ry(deg):
    a = math.radians(deg); c, s = math.cos(a), math.sin(a)
    return np.array([[c, 0, s, 0], [0, 1, 0, 0], [-s, 0, c, 0], [0, 0, 0, 1]])

def frame(origin, ex, ey):
    ex = np.array(ex, float); ey = np.array(ey, float)
    ex /= np.linalg.norm(ex); ey /= np.linalg.norm(ey)
    ez = np.cross(ex, ey)
    m = np.eye(4); m[:3, 0], m[:3, 1], m[:3, 2], m[:3, 3] = ex, ey, ez, origin
    return m

# ---------- part shapes (local 2D, mm) ----------
# Footprint 120 x 80, base 5 mm, walls 3 mm, roof 2 mm.
WALL_H = 57.0          # front/back walls stop under the roof slope
GABLE_EAVE, GABLE_PEAK = 60.0, 90.0
SIDE_DEPTH = 80 - 2 * 3  # sides sit between front and back walls

def rect(x, y, w, h): return box(x, y, x + w, y + h)

front = rect(0, 0, 120, WALL_H).difference(unary_union([
    rect(48, 0, 24, 40),           # door (notch from bottom edge)
    rect(14, 28, 20, 18), rect(86, 28, 20, 18),
]))
back = rect(0, 0, 120, WALL_H).difference(unary_union([
    rect(14, 28, 20, 18), rect(86, 28, 20, 18), rect(52, 28, 16, 18),
]))
gable = Polygon([(0, 0), (SIDE_DEPTH, 0), (SIDE_DEPTH, GABLE_EAVE),
                 (SIDE_DEPTH / 2, GABLE_PEAK), (0, GABLE_EAVE)])
side = gable.difference(rect(SIDE_DEPTH / 2 - 9, 26, 18, 18))

slope_run, slope_rise = SIDE_DEPTH / 2, GABLE_PEAK - GABLE_EAVE
slope_len = math.hypot(slope_run, slope_rise)
ROOF_LEN = round(slope_len + 8)    # 8 mm eave overhang
roof = rect(0, 0, 130, ROOF_LEN)
awning = rect(0, 0, 34, 14)
base = rect(0, 0, 120, 80)

# ---------- placement in 3D (world mm, Y up, front = +Z) ----------
ridge = np.array([0.0, 5 + GABLE_PEAK, 0.0])
d_front = np.array([0, -slope_rise, slope_run]) / slope_len   # down the front slope
d_back = np.array([0, -slope_rise, -slope_run]) / slope_len

parts = [
    # id, name, thickness, shape, world matrix
    ("base",     "Base plate",     5, base,   T(-60, 5, -40) @ Rx(90)),
    ("front",    "Front wall",     3, front,  T(-60, 5, 37)),
    ("back",     "Back wall",      3, back,   T(-60, 5, -40)),
    ("side_l",   "Side wall L",    3, side,   T(-60, 5, 37) @ Ry(90)),
    ("side_r",   "Side wall R",    3, side,   T(57, 5, 37) @ Ry(90)),
    ("roof_f",   "Roof front",     2, roof,   frame(ridge + d_front * ROOF_LEN + [-65, 0, 0], [1, 0, 0], -d_front)),
    ("roof_b",   "Roof back",      2, roof,   frame(ridge + d_back * ROOF_LEN + [65, 0, 0], [-1, 0, 0], -d_back)),
    ("awning",   "Door awning",    2, awning, T(-17, 52, 40) @ Rx(90)),
]

# ---------- build GLB ----------
COLORS = {2: [0.93, 0.93, 0.90, 1], 3: [0.97, 0.97, 0.97, 1], 5: [0.86, 0.87, 0.89, 1]}
scene = trimesh.Scene()
S = np.diag([0.001, 0.001, 0.001, 1])  # mm -> m
for pid, name, t, shape, M in parts:
    mesh = trimesh.creation.extrude_polygon(shape, t)
    mesh.visual = trimesh.visual.ColorVisuals(mesh, face_colors=(np.array(COLORS[t]) * 255).astype(np.uint8))
    scene.add_geometry(mesh, node_name=pid, geom_name=pid, transform=S @ M)
os.makedirs(os.path.join(ROOT, "models"), exist_ok=True)
scene.export(os.path.join(ROOT, "models", "house.glb"), include_normals=True)

# ---------- shelf-pack parts onto sheets per thickness ----------
def pack(items):
    """items: list of (pid, w, h). Returns {pid: (x, y)} in sheet mm (y down)."""
    items = sorted(items, key=lambda i: -i[2])
    x, y, row_h, out = MARGIN, MARGIN + 14, 0, {}  # 14 mm reserved for title
    for pid, w, h in items:
        if x + w > SHEET_W - MARGIN:
            x, y, row_h = MARGIN, y + row_h + GAP + 8, 0
        if y + h > SHEET_H - MARGIN - 12:
            raise SystemExit(f"{pid} does not fit on one sheet; add multi-sheet support")
        out[pid] = (x, y); x += w + GAP; row_h = max(row_h, h)
    return out

def ring_path(coords, ox, oy, h):
    # local y up -> sheet y down
    pts = [f"{ox + px:.2f},{oy + h - py:.2f}" for px, py in coords]
    return "M" + " L".join(pts) + " Z"

def shape_path(shape, ox, oy):
    minx, miny, maxx, maxy = shape.bounds
    s = affinity.translate(shape, -minx, -miny)
    h = maxy - miny
    d = ring_path(s.exterior.coords, ox, oy, h)
    for hole in s.interiors:
        d += " " + ring_path(hole.coords, ox, oy, h)
    return d

part_list = []
by_t = {}
for i, (pid, name, t, shape, M) in enumerate(parts, 1):
    minx, miny, maxx, maxy = shape.bounds
    w, h = maxx - minx, maxy - miny
    part_list.append({"id": pid, "no": i, "name": name, "thickness": t,
                      "w": round(w, 1), "h": round(h, 1), "holes": len(shape.interiors)})
    by_t.setdefault(t, []).append((pid, w, h, shape, i, name))

sheets = []
os.makedirs(os.path.join(ROOT, "sheets"), exist_ok=True)
for t in sorted(by_t):
    items = by_t[t]
    pos = pack([(p, w, h) for p, w, h, *_ in items])
    g = []
    for pid, w, h, shape, no, name in items:
        x, y = pos[pid]
        # narrow parts get the dimension on a second line so labels don't collide
        dim = (f'<text class="dim" x="{x + w - 1:.1f}" y="{y + h + 5:.1f}" text-anchor="end">{w:.0f} × {h:.0f}</text>'
               if w >= 60 else
               f'<text class="dim" x="{x + 2:.1f}" y="{y + h + 9:.1f}">{w:.0f} × {h:.0f}</text>')
        g.append(
            f'<g class="part" id="part-{pid}" data-part="{pid}">'
            f'<path class="cut" d="{shape_path(shape, x, y)}"/>'
            f'<text class="lbl" x="{x + 2:.1f}" y="{y + h + 5:.1f}">#{no} {name}</text>'
            + dim + '</g>')
    fname = f"sheet-{t}mm.svg"
    svg = f'''<svg xmlns="http://www.w3.org/2000/svg" width="{SHEET_W}mm" height="{SHEET_H}mm" viewBox="0 0 {SHEET_W} {SHEET_H}" data-thickness="{t}">
<style>
.sheet{{fill:#fff;stroke:#999;stroke-width:0.3}}
.cut{{fill:#f3f3f3;fill-rule:evenodd;stroke:#000;stroke-width:0.25}}
.lbl,.dim,.title,.small{{font-family:Helvetica,Arial,sans-serif;fill:#000}}
.lbl{{font-size:3.2px;font-weight:bold}} .dim{{font-size:3px}}
.title{{font-size:5px;font-weight:bold}} .small{{font-size:3px}}
.scale{{stroke:#000;stroke-width:0.4;fill:none}}
</style>
<rect class="sheet" x="0.5" y="0.5" width="{SHEET_W - 1}" height="{SHEET_H - 1}"/>
<text class="title" x="{MARGIN}" y="{MARGIN + 5}">{PROJECT} · Foamex {t} mm · Sheet A4</text>
<text class="small" x="{MARGIN}" y="{MARGIN + 10}">Scale 1:1, print at 100% (no "fit to page"). Units: mm.</text>
<g transform="translate({SHEET_W - MARGIN - 50},{SHEET_H - MARGIN})">
<path class="scale" d="M0,-3 V0 H50 V-3 M10,0 V-1.5 M20,0 V-1.5 M30,0 V-1.5 M40,0 V-1.5"/>
<text class="small" x="0" y="-4.5">check: this bar = 50 mm</text></g>
{chr(10).join(g)}
</svg>'''
    with open(os.path.join(ROOT, "sheets", fname), "w") as f:
        f.write(svg)
    sheets.append({"thickness": t, "file": f"sheets/{fname}", "size": "A4", "parts": [p for p, *_ in items]})

with open(os.path.join(ROOT, "parts.json"), "w") as f:
    json.dump({"project": PROJECT, "units": "mm", "model": "models/house.glb",
               "parts": part_list, "sheets": sheets}, f, indent=2, ensure_ascii=False)
print("ok:", len(part_list), "parts,", len(sheets), "sheets")
