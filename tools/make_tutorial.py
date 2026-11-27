"""
Build the tutorial house as a Blender-style .glb (flat planes = outer surfaces, normals outward),
then convert it with convert_glb.py into projects/tutorial/.

    python tools/make_tutorial.py

House: 152 x 100 mm footprint, 76 mm walls, 45° gable roof, ridge at 126 mm.
- walls tagged "mitered" (all 45°); roof untagged, so it butts against the walls, gables and itself
- base untagged: butt joint, the converter shortens it to sit inside the walls
- door + windows (cutouts), gables (non-90° corners)
- a human figure (1:25, ~70 mm), two pin joints, a 40x60 frame (ignored), a pre-vis camera
"""
import json, math, os, struct, subprocess, sys
import numpy as np
import trimesh
from shapely.geometry import Polygon, box

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_GLB = os.path.join(ROOT, 'tools', 'tutorial-house.glb')
MM = 0.001  # Blender units are metres


def plane(name, origin, u, v, poly):
    """Flat face in 3D: 2D polygon (mm) on axes u, v from origin; normal = u x v (outward)."""
    verts2, faces = trimesh.creation.triangulate_polygon(poly, engine='earcut')
    o, u, v = np.array(origin, float), np.array(u, float), np.array(v, float)
    verts = np.array([o + x * u + y * v for x, y in verts2]) * MM
    m = trimesh.Trimesh(verts, faces, process=False)
    n = np.cross(u, v)
    if (m.face_normals @ n).mean() < 0:
        m.faces = m.faces[:, ::-1]
    return name, m


W, D, H, PEAK = 152.0, 100.0, 76.0, 126.0  # even mm everywhere; 45° roof pitch (rise 50 over run 50)
hw, hd = W / 2, D / 2
L = math.hypot(PEAK - H, hd)  # roof slope length

parts = []
# front wall (+z) with a door and two windows
front = box(-hw, 0, hw, H).difference(box(-16, 8, 16, 66)).difference(box(-58, 30, -34, 52)).difference(box(34, 30, 58, 52))
parts.append(plane('wall_front_mitered', (0, 0, hd), (1, 0, 0), (0, 1, 0), front))
# back wall (-z) with one window
back = box(-hw, 0, hw, H).difference(box(-12, 30, 12, 52))
parts.append(plane('wall_back_mitered', (0, 0, -hd), (-1, 0, 0), (0, 1, 0), back))
# gable walls (+x / -x): pentagon with a small window
gable = Polygon([(-hd, 0), (hd, 0), (hd, H), (0, PEAK), (-hd, H)]).difference(box(-10, 35, 10, 55))
parts.append(plane('gable_right_mitered', (hw, 0, 0), (0, 0, -1), (0, 1, 0), gable))
parts.append(plane('gable_left_mitered', (-hw, 0, 0), (0, 0, 1), (0, 1, 0), gable))
# roof panels: eave -> ridge, no overhang so every edge meets a wall
roof = box(-hw, 0, hw, L)
dv = np.array([0, PEAK - H, -hd]) / L
parts.append(plane('roof_front', (0, H, hd), (1, 0, 0), dv, roof))
dv2 = np.array([0, PEAK - H, hd]) / L
parts.append(plane('roof_back', (0, H, -hd), (-1, 0, 0), dv2, roof))
# base: untagged, outer face is the underside
parts.append(plane('base', (0, 0, 0), (1, 0, 0), (0, 0, 1), box(-hw, -hd, hw, hd)))
# 40x60 frame: Blender-only, must be skipped
parts.append(plane('40x60', (0, 0, 0), (1, 0, 0), (0, 0, -1), box(260, -200, 660, 400)))

scene = trimesh.Scene()
for name, m in parts:
    scene.add_geometry(m, node_name=name, geom_name=name)

# human figure ~70 mm (1:25), standing in front of the door
UPRIGHT = trimesh.transformations.rotation_matrix(math.pi / 2, (1, 0, 0))  # trimesh cylinders run along Z; stand them up
def ycyl(r, h, sections): c = trimesh.creation.cylinder(radius=r, height=h, sections=sections); c.apply_transform(UPRIGHT); return c
fig = []
for x in (-4.5, 4.5):  # legs
    c = ycyl(3.2, 34, 12); c.apply_translation((x, 17, 0)); fig.append(c)
t = ycyl(8, 26, 14); t.apply_scale((1, 1, 0.6)); t.apply_translation((0, 47, 0)); fig.append(t)
for x in (-11, 11):  # arms
    a = ycyl(2.4, 24, 10); a.apply_translation((x, 46, 0)); fig.append(a)
h = trimesh.creation.icosphere(subdivisions=2, radius=6.5); h.apply_translation((0, 66, 0)); fig.append(h)
figure = trimesh.util.concatenate(fig); figure.apply_translation((-38, 0, hd + 28)); figure.apply_scale(MM)
scene.add_geometry(figure, node_name='human figure', geom_name='human figure')

# pin joints: hold the roof ridge to the gables
for i, x in enumerate((-hw + 12, hw - 12)):
    p = ycyl(1.0, 14, 12)  # 2 mm pins, like the real ones
    p.apply_translation((x, PEAK - 12, 0)); p.apply_scale(MM)
    scene.add_geometry(p, node_name='pin joint' + ('' if i == 0 else f'.{i:03d}'), geom_name=f'pin{i}')

scene.export(OUT_GLB)

# add a pre-vis camera node, the way Blender exports one (Include > Cameras)
b = open(OUT_GLB, 'rb').read()
n = struct.unpack('<I', b[12:16])[0]
j = json.loads(b[20:20 + n]); rest = b[20 + n:]
pos = np.array([230, 125, 260]) * MM; tgt = np.array([0, 55, 0]) * MM
f = (tgt - pos) / np.linalg.norm(tgt - pos); z = -f
x = np.cross([0, 1, 0], z); x /= np.linalg.norm(x); y = np.cross(z, x)
R = np.c_[x, y, z]; w = math.sqrt(1 + R[0, 0] + R[1, 1] + R[2, 2]) / 2
q = [(R[2, 1] - R[1, 2]) / (4 * w), (R[0, 2] - R[2, 0]) / (4 * w), (R[1, 0] - R[0, 1]) / (4 * w), w]
j.setdefault('cameras', []).append({'type': 'perspective', 'name': 'Camera', 'perspective': {'yfov': 0.62, 'znear': 0.001, 'aspectRatio': 1.78}})
j['nodes'].append({'name': 'Camera', 'camera': len(j['cameras']) - 1, 'translation': pos.tolist(), 'rotation': q})
j['scenes'][0]['nodes'].append(len(j['nodes']) - 1)
js = json.dumps(j).encode(); js += b' ' * ((4 - len(js) % 4) % 4)
with open(OUT_GLB, 'wb') as fo:
    fo.write(struct.pack('<III', 0x46546C67, 2, 12 + 8 + len(js) + len(rest)) + struct.pack('<II', len(js), 0x4E4F534A) + js + rest)

# convert into projects/tutorial and mark it as the pinned tutorial
subprocess.run([sys.executable, os.path.join(ROOT, 'tools', 'convert_glb.py'), OUT_GLB,
                '--id', 'tutorial', '--name', 'Tutorial House', '--projects', os.path.join(ROOT, 'projects')], check=True)
with open(os.path.join(ROOT, 'projects', 'tutorial', 'meta.json'), 'w') as fo:
    json.dump({'pinned': False, 'label': 'Tutorial',
               'description': 'Start here: a 1-minute tour of the viewer.'}, fo, indent=1)
TOUR = {
 "steps": [
  {
   "target": "#viewport",
   "action": "reset",
   "title": "The 3D view",
   "text": "Left-drag to rotate, right-drag to pan, scroll to zoom. Give it a spin, then press Next."
  },
  {
   "target": "#parts-body",
   "action": "select:wall_front_mitered",
   "title": "Pick a part",
   "text": "Click any plate: in 3D, on the cutting sheet, or in this list. It lights up in all three places."
  },
  {
   "target": "#sheet",
   "action": "sheet:1",
   "title": "The cutting sheet",
   "text": "Each sheet is a 40 × 60 cm foamex board. Orange hatching marks 45° mitered edges. Arcs show corners that aren't 90°. Scroll to zoom, drag to move."
  },
  {
   "target": ".sheet-nav",
   "action": "",
   "title": "Switching sheets",
   "text": "Use ◀ ▶ or the dropdown to move between sheets when a model needs more than one board."
  },
  {
   "target": "#viewport .explode",
   "action": "explode:0.7",
   "title": "Explode",
   "text": "Drag this slider to pull the plates apart and see how they fit together."
  },
  {
   "target": "#measure-btn",
   "action": "measure:on",
   "title": "Measurements",
   "text": "Shows the size of every plate. The ones in the middle of the view stay sharp, the rest fade. Use Text to resize them."
  },
  {
   "target": "#previs-btn",
   "action": "previs",
   "title": "Pre-vis angle",
   "text": "Jumps back to the camera angle from the emvee's mockup. That's it: replay this anytime with Replay tutorial at the top."
  }
 ]
}
with open(os.path.join(ROOT, 'projects', 'tutorial', 'tour.json'), 'w') as fo:
    json.dump(TOUR, fo, indent=1, ensure_ascii=False)
subprocess.run([sys.executable, os.path.join(ROOT, 'tools', 'convert_glb.py'), '--reindex',
                '--projects', os.path.join(ROOT, 'projects')], check=True)
