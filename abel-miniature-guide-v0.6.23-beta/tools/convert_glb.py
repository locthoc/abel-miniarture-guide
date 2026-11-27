"""
Convert a Blender .glb of flat planes into a foamex viewer project.

    python tools/convert_glb.py path/to/model.glb [--scale 1] [--sheet 400x600] [--thickness 5] [--id name]
    python tools/convert_glb.py --reindex      # only rebuild projects/projects.json

Each model goes to projects/<id>/ and is listed in projects/projects.json (the start screen).
Put a thumbnail next to it as projects/<id>/thumb.png (or .jpg / .webp) and run --reindex.

Rules
- Every flat plane object is a foamex plate. The plane is the OUTER surface; foamex grows inward
  (against the face normal) by --thickness.
- Tagged plates (name contains miter / mitre / mitered / mitred, any case) are mitered where they meet
  another TAGGED plate: bevel = half the corner angle (45 deg for a 90 deg corner).
- Where a tagged plate meets an untagged plate, it is a butt joint: the tagged plate keeps full size,
  the untagged plate is shortened so it butts against the tagged plate's inner face.
- Where two untagged plates meet, the smaller one is shortened.
- Free edges are straight cuts.
- Non-flat objects, or names with dummy/human/figure/ragdoll/body/person, are figures (3D only).
  Names containing "set" as a word (floor_set, set-mirror) are scenery (3D only).
- Normals of a joined group of 3+ plates are oriented outward automatically.
- Plates bigger than a sheet are split into sheet-size panels (A1, A2, B1...).

Outputs (into projects/<id>/): parts.json, models/<id>.glb (geometry only), sheets/sheet-NN.svg
"""
import argparse, datetime, json, math, os, re, string, struct
import numpy as np
import trimesh
from shapely.geometry import Polygon, Point as ShPoint, box as sbox
from shapely.ops import unary_union, polylabel
from shapely import orient_polygons

TAG = re.compile(r'mit(er|re)(e?d)?', re.I)
FIGURE = re.compile(r'dummy|human|figure|ragdoll|body|person|people', re.I)
SCENERY = re.compile(r'(^|[^a-z])set([^a-z]|$)', re.I)
FRAME = re.compile(r'(^|[^0-9.])\d+(\.\d+)?\s*[x×]\s*\d+(\.\d+)?([^0-9]|$)', re.I)  # 40x60 etc: Blender-only scale frames
PIN = re.compile(r'(^|[^a-z])(pins?|joints?|hinges?)([^a-z]|$)', re.I)            # pin joints / hinges: hardware
TOL = 0.5  # mm
GRID = 2.0  # every plate corner snaps to even millimetres (2, 4, 6...): sizes and the cut itself
def even(x):  # a length shown as even mm
    return int(GRID * round(float(x) / GRID))


def clean_name(n):
    n = TAG.sub('', n)
    n = re.sub(r'^[._\-\s]+|[._\-\s]+$', '', n)
    n = re.sub(r'[._\-\s]{2,}', '_', n)
    return n or 'plate'


def safe_id(n):
    return re.sub(r'[^A-Za-z0-9_-]', '_', n)


# ---------------------------------------------------------------- geometry helpers
def read_cameras(path, scale=1.0):
    """Blender cameras exported in the .glb (Include > Cameras). Returned in viewer units (metres x scale)."""
    try:
        with open(path, 'rb') as f:
            b = f.read()
        n = struct.unpack('<I', b[12:16])[0]
        j = json.loads(b[20:20 + n])
    except Exception:
        return []
    nodes, cams = j.get('nodes', []), j.get('cameras', [])
    if not cams:
        return []
    def local(nd):
        if 'matrix' in nd:
            return np.array(nd['matrix'], float).reshape(4, 4).T  # glTF is column-major
        t = nd.get('translation', [0, 0, 0]); x, y, z, w = nd.get('rotation', [0, 0, 0, 1]); sc = nd.get('scale', [1, 1, 1])
        R = np.array([[1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
                      [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
                      [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)]])
        M = np.eye(4); M[:3, :3] = R * np.array(sc); M[:3, 3] = t
        return M
    parent = {c: i for i, nd in enumerate(nodes) for c in nd.get('children', [])}
    def world(i):
        M = local(nodes[i])
        while i in parent:
            i = parent[i]; M = local(nodes[i]) @ M
        return M
    out = []
    for i, nd in enumerate(nodes):
        if 'camera' not in nd:
            continue
        cam = cams[nd['camera']]
        if cam.get('type') != 'perspective':
            continue
        W = world(i)
        fwd = -W[:3, 2] / np.linalg.norm(W[:3, 2]); up = W[:3, 1] / np.linalg.norm(W[:3, 1])
        out.append({'name': nd.get('name') or cam.get('name') or f'Camera {len(out) + 1}',
                    'position': (W[:3, 3] * scale).round(5).tolist(),
                    'forward': fwd.round(5).tolist(), 'up': up.round(5).tolist(),
                    'fov': round(math.degrees(cam['perspective'].get('yfov', 0.6)), 2)})
    return out


def in_plate(q, geo):
    """Is point q (mm) inside a plate? geo = (c, u, v, n, outer, inner, holes, T). The plate runs from the
    outer face (depth 0) to the inner face (depth T); its outline blends from outer to inner (miters)."""
    c, u, v, n, outer, inner, holes, T = geo
    d = float((c - q) @ n)
    if d < -1e-6 or d > T + 1e-6:
        return False
    w = d / T if T else 0.0
    q2 = ((q - c) @ u, (q - c) @ v)
    poly = Polygon(outer + (inner - outer) * w, holes)
    return poly.contains(ShPoint(*q2))


def pin_gap(pin, solids, step=0.05):
    """Free space between plates along a pin's axis, inside the pin's length (mm in, metres out).
    Walks along the axis, finds stretches outside every plate that have a plate at BOTH ends,
    and returns the longest one. None if the pin doesn't bridge two plates."""
    if solids is None:
        return None
    v = pin.vertices
    c = v.mean(0)
    _, _, vt = np.linalg.svd(v - c, full_matrices=False)
    ax = vt[0] / np.linalg.norm(vt[0])
    t = (v - c) @ ax
    p0, p1 = t.min(), t.max()          # the pin itself
    t0, t1 = p0 - 6.0, p1 + 6.0        # look a little past its ends: plates may have been snapped to the grid
    ts = np.arange(t0, t1 + step, step)
    pts = c + np.outer(ts, ax)
    inside = np.array([any(in_plate(q, g) for g in solids) for q in pts])
    best, i, n = None, 0, len(ts)
    while i < n:
        if inside[i]:
            i += 1; continue
        j = i
        while j + 1 < n and not inside[j + 1]:
            j += 1
        if i > 0 and j < n - 1 and ts[j] > p0 and ts[i] < p1:  # plate on both sides, and it's where the pin is
            L = ts[j] - ts[i] + step
            if best is None or L > best[0]:
                best = (L, ts[i] - step / 2, ts[j] + step / 2)
        i = j + 1
    if not best:
        return None
    L, a, b = best
    pa, pb = c + ax * a, c + ax * b
    return {'a': (pa / 1000).round(6).tolist(), 'b': (pb / 1000).round(6).tolist(), 'len': even(L)}


def load(path, scale=1.0):
    s = trimesh.load(path, force='scene')
    objs = []
    for node in s.graph.nodes_geometry:
        M, g = s.graph[node]
        m = s.geometry[g]
        if not isinstance(m, trimesh.Trimesh) or len(m.faces) == 0:
            continue
        v = trimesh.transform_points(m.vertices, M) * 1000.0 * scale  # m -> mm, then model scale
        mesh = trimesh.Trimesh(v, m.faces.copy(), process=True)
        # drop zero-area triangles (Blender leftovers after merging vertices); they break the flatness check
        keep = mesh.area_faces > 0.01  # mm²
        if len(mesh.faces) <= 64 and not keep.all() and keep.any():  # plates only; figures keep every triangle
            mesh.update_faces(keep); mesh.remove_unreferenced_vertices()
        objs.append({'name': node, 'mesh': mesh})
    return objs


def plane_of(mesh):
    n = mesh.face_normals.mean(0)
    if np.linalg.norm(n) < 1e-6:
        return None
    n /= np.linalg.norm(n)
    if np.abs((mesh.vertices - mesh.vertices.mean(0)) @ n).max() > TOL:
        return None
    if np.abs(mesh.face_normals @ n).min() < 0.99:
        return None
    return n


def frame(n, verts):
    c = verts.mean(0)
    # upright plates: sheet "up" = model up, so walls and gables read the right way round
    up = np.array([0.0, 1.0, 0.0]) - n[1] * n
    if np.linalg.norm(up) > 0.3:
        v = up / np.linalg.norm(up)
        return c, np.cross(v, n), v
    d = verts - c
    d -= np.outer(d @ n, n)
    _, _, vt = np.linalg.svd(d, full_matrices=False)
    u = vt[0] - (vt[0] @ n) * n
    u /= np.linalg.norm(u)
    return c, u, np.cross(n, u)  # 2D seen from outside: x=u, y=v, u x v = n


def outline(o):
    n = o['n']
    c, u, v = frame(n, o['mesh'].vertices)
    p2 = np.c_[(o['mesh'].vertices - c) @ u, (o['mesh'].vertices - c) @ v]
    tris = [Polygon(p2[f]) for f in o['mesh'].faces]
    poly = unary_union([t.buffer(0.01, join_style=2) for t in tris if t.area > 1e-9]).buffer(-0.01, join_style=2)
    if poly.geom_type != 'Polygon':
        poly = max(poly.geoms, key=lambda g: g.area)
    poly = orient_polygons(poly.simplify(0.05))  # exterior CCW, holes CW
    ext = np.array(poly.exterior.coords)[:-1]
    o.update(poly=poly, c=c, u=u, v=v, ext=ext,
             holes=[np.array(h.coords)[:-1] for h in poly.interiors],
             edges3=[(c + ext[k][0] * u + ext[k][1] * v, c + ext[(k + 1) % len(ext)][0] * u + ext[(k + 1) % len(ext)][1] * v)
                     for k in range(len(ext))])


def seg_overlap(a0, a1, b0, b1):
    d = a1 - a0
    L = np.linalg.norm(d)
    if L < 1e-6:
        return 0.0
    d /= L
    for q in (b0, b1):
        w = q - a0
        if np.linalg.norm(w - (w @ d) * d) > TOL:
            return 0.0
    t0, t1 = sorted([(b0 - a0) @ d, (b1 - a0) @ d])
    return max(0.0, min(L, t1) - max(0.0, t0))


ANGLE_STEP, ANGLE_SNAP = 15.0, 3.0  # clean angles are multiples of 15°; snap only edges within 3° of one


def square_up(ext, fixed):
    """Turn free slanted edges to the nearest multiple of 15° (if within 3°), so corners come out exactly
    45 / 90 / 135... Straight (axis) edges and joined edges keep their line; a turned edge pivots on the end it
    shares with a kept edge, so straight edges keep their even-millimetre ends. Returns (new outline, notes)."""
    m = len(ext)
    ang = [math.degrees(math.atan2(*(ext[(k + 1) % m] - ext[k])[::-1])) for k in range(m)]
    keeper = [k in fixed or min(abs(a % 90), 90 - abs(a % 90)) < 1e-6 for k, a in enumerate(ang)]
    lines, notes = [], []
    for k in range(m):
        a0, a1 = ext[k], ext[(k + 1) % m]
        a = ang[k]
        tgt = ANGLE_STEP * round(a / ANGLE_STEP)
        if keeper[k] or abs(a - tgt) < 1e-6:
            if k in fixed and abs(a - tgt) > 0.05 and min(abs(a % 90), 90 - abs(a % 90)) > 1e-6:
                notes.append(f'joined edge at {abs(a) % 180:.1f}° kept as is so the plates still meet')
            lines.append((a0, a)); continue
        if abs(a - tgt) > ANGLE_SNAP:
            lines.append((a0, a)); notes.append(f'keeps a {abs(a) % 180:.1f}° edge (not near a clean angle)'); continue
        if keeper[k - 1]:
            pivot = a0
        elif keeper[(k + 1) % m]:
            pivot = a1
        else:
            pivot = (a0 + a1) / 2
        lines.append((pivot, tgt))
    out = []
    for k in range(m):
        (p1, t1), (p2, t2) = lines[k - 1], lines[k]
        d1 = np.array([math.cos(math.radians(t1)), math.sin(math.radians(t1))])
        d2 = np.array([math.cos(math.radians(t2)), math.sin(math.radians(t2))])
        den = d1[0] * d2[1] - d1[1] * d2[0]
        if abs(den) < 1e-9:
            out.append(ext[k].copy()); continue
        tt = ((p2 - p1)[0] * d2[1] - (p2 - p1)[1] * d2[0]) / den
        out.append(p1 + d1 * tt)
    out = np.array(out)
    if not Polygon(out).is_valid or np.abs(out - ext).max() > 4.0:  # something odd: leave the outline alone
        return ext, ['could not square up its corners, left as modelled']
    return out, notes


def offset_outline(ext, offs):
    """Move each edge k of a CCW polygon inward by offs[k]; return new vertices."""
    m = len(ext)
    out = []
    for k in range(m):
        p_prev, p, p_next = ext[k - 1], ext[k], ext[(k + 1) % m]
        def line(a0, a1, d):
            e = (a1 - a0) / np.linalg.norm(a1 - a0)
            return a0 + np.array([-e[1], e[0]]) * d, e
        q1, e1 = line(p_prev, p, offs[k - 1])
        q2, e2 = line(p, p_next, offs[k])
        den = e1[0] * e2[1] - e1[1] * e2[0]
        if abs(den) < 1e-9:
            out.append(q2)
        else:
            t = ((q2[0] - q1[0]) * e2[1] - (q2[1] - q1[1]) * e2[0]) / den
            out.append(q1 + e1 * t)
    return np.array(out)


def solid(outer, inner, holes, c, u, v, n, T):
    V, F = [], []
    def add(pts2, depth):
        base = len(V)
        for q in pts2:
            V.append(c + q[0] * u + q[1] * v - n * depth)
        return base
    for pg, depth, flip in ((Polygon(outer, holes), 0.0, False), (Polygon(inner, holes), T, True)):
        vv, ff = trimesh.creation.triangulate_polygon(pg, engine='earcut')
        b = add(vv, depth)
        for f in ff:
            F.append([b + f[0], b + f[2], b + f[1]] if flip else [b + f[0], b + f[1], b + f[2]])
    m = len(outer)
    bo, bi = add(outer, 0.0), add(inner, T)
    for k in range(m):
        k2 = (k + 1) % m
        F += [[bo + k, bi + k, bo + k2], [bo + k2, bi + k, bi + k2]]
    for h in holes:
        ho, hi = add(h, 0.0), add(h, T)
        for k in range(len(h)):
            k2 = (k + 1) % len(h)
            F += [[ho + k, hi + k, ho + k2], [ho + k2, hi + k, hi + k2]]
    s = trimesh.Trimesh(np.array(V), np.array(F), process=True)
    trimesh.repair.fix_normals(s)
    return s


# ---------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('glb', nargs='?')
    ap.add_argument('--id', default=None, help='project id / folder name (default: file name)')
    ap.add_argument('--name', default=None, help='display name (default: id)')
    ap.add_argument('--projects', default='projects', help='projects folder')
    ap.add_argument('--out', default=None, help='write here instead of projects/<id>/ (no index update)')
    ap.add_argument('--scale', type=float, default=1.0, help='scale the whole model, e.g. 0.1')
    ap.add_argument('--thickness', type=float, default=5.0)
    ap.add_argument('--sheet', default='400x600', help='foamex sheet size in mm, WxH')
    ap.add_argument('--reindex', action='store_true', help='only rebuild projects/projects.json')
    a = ap.parse_args()
    if a.reindex and not a.glb:
        rebuild_index(a.projects); return
    if not a.glb:
        ap.error('give a .glb file (or --reindex)')
    T = a.thickness
    SW, SH = map(float, a.sheet.lower().split('x'))
    pid = safe_id(a.id or os.path.splitext(os.path.basename(a.glb))[0])
    project = a.name or pid
    a.out = a.out or os.path.join(a.projects, pid)
    os.makedirs(a.out, exist_ok=True)
    warnings = []

    plates, figures, scenery, pins, skipped = [], [], [], [], []
    for o in load(a.glb, a.scale):
        if FRAME.search(o['name']):
            skipped.append(o['name']); continue
        if PIN.search(o['name']):
            pins.append(o); continue
        if FIGURE.search(o['name']):
            figures.append(o); continue
        n = plane_of(o['mesh'])
        if n is None:
            figures.append(o); continue
        if SCENERY.search(o['name']):
            scenery.append(o); continue
        snapped = GRID * np.round(o['mesh'].vertices / GRID)
        o['mesh'] = trimesh.Trimesh(snapped, o['mesh'].faces, process=True)
        keep = o['mesh'].area_faces > 0.01
        if not keep.all() and keep.any():
            o['mesh'].update_faces(keep); o['mesh'].remove_unreferenced_vertices()
        n2 = o['mesh'].face_normals.mean(0)
        n = n2 / np.linalg.norm(n2) if np.linalg.norm(n2) > 1e-6 else n
        o['n'] = n
        o['tag'] = bool(TAG.search(o['name']))
        o['area'] = float(o['mesh'].area)
        outline(o)
        plates.append(o)
    N = len(plates)

    def joins_of(i):
        """edge index -> neighbour plate index. An edge counts as joined only if a non-parallel plate
        covers at least half of it; a plate that merely touches a long edge for a bit (a box standing at
        the floor's edge) is not a joint."""
        res = {}
        for ei, (a0, a1) in enumerate(plates[i]['edges3']):
            L = np.linalg.norm(a1 - a0)
            best = None
            for j in range(N):
                if j == i:
                    continue
                if abs(float(plates[i]['n'] @ plates[j]['n'])) > 0.985:  # same plane, side by side
                    continue
                ov = max((seg_overlap(a0, a1, b0, b1) for b0, b1 in plates[j]['edges3']), default=0)
                if ov < max(1.0, 0.5 * L):
                    continue
                key = (plates[j]['tag'], ov)
                if best is None or key > best[0]:
                    best = (key, j, ov)
            if best:
                res[ei] = best[1]
                if best[2] < L - 2 * TOL:
                    warnings.append(f"{plates[i]['name']}: edge only partly meets {plates[best[1]]['name']} "
                                    f"({best[2]:.0f} of {L:.0f} mm), whole edge treated as joined")
        return res

    joins = [joins_of(i) for i in range(N)]

    # groups (connected plates)
    group = [-1] * N
    ng = 0
    for i in range(N):
        if group[i] >= 0:
            continue
        stack, group[i] = [i], ng
        while stack:
            k = stack.pop()
            for j in joins[k].values():
                if group[j] < 0:
                    group[j] = ng; stack.append(j)
        ng += 1

    # outward normals for groups of 3+ plates
    for gi in range(ng):
        mem = [i for i in range(N) if group[i] == gi]
        if len(mem) < 3:
            continue
        gc = np.mean([plates[i]['c'] for i in mem], axis=0)
        flipped = 0
        for i in mem:
            if (plates[i]['c'] - gc) @ plates[i]['n'] < 0:
                plates[i]['n'] = -plates[i]['n']; outline(plates[i]); flipped += 1
        if flipped:
            warnings.append(f"{flipped} plate(s) in a group of {len(mem)} had normals pointing inward, flipped outward")
    joins = [joins_of(i) for i in range(N)]

    # ------------------------------------------------ decide each joined edge
    groups_meta = []
    for gi in range(ng):
        mem = [i for i in range(N) if group[i] == gi]
        groups_meta.append({'id': gi + 1, 'name': f'Group {gi + 1}' if len(mem) > 1 else clean_name(plates[mem[0]]['name'])})

    parts, scene_meshes = [], []
    no = 0
    for i, o in enumerate(plates):
        c, u, v, n, ext = o['c'], o['u'], o['v'], o['n'], o['ext']
        # clean corner angles: free slanted edges turn to the nearest multiple of 15° (joined edges must match, so stay)
        ext, sq_notes = square_up(ext, set(joins[i].keys()))
        for note in sorted(set(sq_notes)):
            warnings.append(f"{o['name']}: {note}")
        m = len(ext)
        shorten, miter, angles, butt = [0.0] * m, [0.0] * m, [None] * m, [False] * m
        for ei, j in joins[i].items():
            oj = plates[j]
            alpha = math.acos(max(-1.0, min(1.0, float(n @ oj['n']))))  # angle between outward normals
            convex = (oj['c'] - c) @ n < 0
            if o['tag'] and oj['tag']:
                if not convex or alpha < math.radians(1):
                    warnings.append(f"{o['name']}: joint with {oj['name']} is not a convex corner, cut straight")
                    continue
                miter[ei] = T * math.tan(alpha / 2)
                angles[ei] = round(90 - math.degrees(alpha) / 2, 1)
            else:
                # butt joint: who gets shortened?
                if o['tag'] and not oj['tag']:
                    me_short = False
                elif oj['tag'] and not o['tag']:
                    me_short = True
                else:  # both untagged: the smaller plate
                    me_short = o['area'] < oj['area'] or (o['area'] == oj['area'] and i > j)
                if me_short:
                    s = math.sin(alpha)
                    if s < 0.5:
                        warnings.append(f"{o['name']}: shallow joint with {oj['name']}, shortened by {T:g} mm only")
                        s = 1.0
                    shorten[ei] = GRID * math.ceil((T / s) / GRID - 1e-9)  # round UP to even mm: a hair loose, never too tight
                    butt[ei] = True
        outer = offset_outline(ext, shorten) if any(shorten) else ext.copy()
        if any(shorten):  # keep the shortened cut on even millimetres too
            m0 = ext.min(0)
            outer = m0 + GRID * np.round((outer - m0) / GRID)
        inner = offset_outline(outer, miter) if any(miter) else outer.copy()
        if not Polygon(inner).is_valid or Polygon(inner).area <= 0:
            warnings.append(f"{o['name']}: plate too small for its joints, check it")
        if any(butt) and not o['tag']:
            pass
        no += 1
        minx, miny = outer[:, 0].min(), outer[:, 1].min()
        sh = lambda pts: (np.asarray(pts) - [minx, miny])
        parts.append({
            'id': safe_id(o['name']), 'no': no, 'name': clean_name(o['name']), 'object': o['name'],
            'thickness': T, 'group': group[i] + 1, 'tagged': o['tag'],
            'w': even(outer[:, 0].max() - minx), 'h': even(outer[:, 1].max() - miny),
            'miters': sum(1 for x in angles if x is not None),
            'bevels': sorted({x for x in angles if x is not None}),
            'shortened': sum(butt), 'holes': len(o['holes']),
            '_outer': sh(outer), '_holes': [sh(h) for h in o['holes']], '_angles': angles,
            '_solid': solid(outer, inner, o['holes'], c, u, v, n, T),
            '_geo': (c, u, v, n, outer, inner, o['holes'], T),
        })
        if sum(butt):
            parts[-1]['note'] = f'shortened {T:g} mm on {sum(butt)} edge(s) (butt joint)'
        # edges for the 3D measurements (metres, like the .glb); one label per distinct length + direction
        edges, mo = [], len(outer)
        for k in range(mo):
            q0, q1 = outer[k], outer[(k + 1) % mo]
            L = float(np.linalg.norm(q1 - q0))
            if L < 0.5:
                continue
            d2 = (q1 - q0) / L
            dup = any(abs(float(d2 @ e['_d'])) > 0.999 and abs(L - e['len']) < 0.5 for e in edges)
            if dup:
                continue
            p0, p1 = c + q0[0] * u + q0[1] * v, c + q1[0] * u + q1[1] * v
            out3 = d2[1] * u - d2[0] * v  # outward, in the plate plane (outline is CCW)
            edges.append({'a': (p0 / 1000).round(6).tolist(), 'b': (p1 / 1000).round(6).tolist(),
                          'len': even(L), 'out': out3.round(5).tolist(), '_d': d2})
        for e in edges:
            del e['_d']
        parts[-1]['edges'] = edges
        parts[-1]['normal'] = n.round(5).tolist()

    for o in figures:
        o['height'] = even(o['mesh'].bounds[1][1] - o['mesh'].bounds[0][1])
    if skipped:
        warnings.append(f"Skipped {len(skipped)} Blender-only frame(s): {', '.join(skipped)}")

    # ------------------------------------------------ pieces (split oversize plates into panels)
    fs = max(SW, SH) / 75
    M_, GAP, LBL, TOP = SW * 0.02, 3.0, 0.0, fs * 2.8  # pieces sit 3 mm apart; labels are drawn inside them
    UW, UH = SW - 2 * M_, SH - 2 * M_ - TOP - LBL  # usable area for one piece

    def fits(w, h):
        return (w <= UW and h <= UH) or (h <= UW and w <= UH)

    pieces = []
    for p in parts:
        outer, holes, angles = p['_outer'], p['_holes'], p['_angles']
        miter_segs = [(outer[k], outer[(k + 1) % len(outer)], angles[k]) for k in range(len(outer)) if angles[k] is not None]
        poly = Polygon(outer, holes)
        if fits(p['w'], p['h']):
            tiles = [('', poly)]
        else:
            best = None
            for tw, th in ((UW - 1, UH - 1), (UH - 1, UW - 1)):
                nx, ny = math.ceil(p['w'] / tw), math.ceil(p['h'] / th)
                tl = []
                for r in range(ny):
                    for cc in range(nx):
                        cell = sbox(cc * tw, r * th, min((cc + 1) * tw, p['w'] + 1), min((r + 1) * th, p['h'] + 1))
                        piece = poly.intersection(cell)
                        geoms = [piece] if piece.geom_type == 'Polygon' else [g for g in getattr(piece, 'geoms', []) if g.geom_type == 'Polygon']
                        for k, g in enumerate(geoms):
                            if g.area > 1.0:
                                lab = f"{string.ascii_uppercase[(ny - 1 - r) % 26]}{cc + 1}" + (f".{k + 1}" if len(geoms) > 1 else '')
                                tl.append((lab, orient_polygons(g.simplify(0.05))))
                if best is None or len(tl) < len(best):
                    best = tl
            tiles = best
            warnings.append(f"{p['name']} ({p['w']:.0f} x {p['h']:.0f} mm) is bigger than a sheet: split into {len(tiles)} panels")
        p['pieces'] = len(tiles)
        for lab, g in tiles:
            ext = np.array(g.exterior.coords)[:-1]
            # which piece edges lie on a mitered edge of the plate
            pang = []
            for k in range(len(ext)):
                a0, a1 = ext[k], ext[(k + 1) % len(ext)]
                ang = None
                for b0, b1, ag in miter_segs:
                    if seg_overlap(np.r_[a0, 0], np.r_[a1, 0], np.r_[b0, 0], np.r_[b1, 0]) > 1.0:
                        ang = ag; break
                pang.append(ang)
            minx, miny = ext[:, 0].min(), ext[:, 1].min()
            pieces.append({'part': p, 'label': lab,
                           'outer': ext - [minx, miny],
                           'holes': [np.array(h.coords)[:-1] - [minx, miny] for h in g.interiors],
                           'angles': pang,
                           'w': float(ext[:, 0].max() - minx), 'h': float(ext[:, 1].max() - miny)})

    # ------------------------------------------------ pack pieces onto sheets
    sheets = []

    def label_w(pc):  # estimated width of the two label lines under a piece
        p = pc['part']
        title = f'#{p["no"]} {p["name"]}' + (f' · panel {pc["label"]} of {p["pieces"]}' if pc['label'] else '')
        return max(len(title) * fs * 1.1 * 0.58, (len(f'{pc["w"]:.0f} × {pc["h"]:.0f} mm')) * fs * 0.9 * 0.55)

    def try_place(sh, w, hh, lw=0.0):
        for shelf in sh['shelves']:
            y, h, xu = shelf
            for (ww, hh2, rot) in ((w, hh, False), (hh, w, True)):
                we = max(ww, lw)  # reserve room for the label too
                if hh2 + LBL <= h and xu + we <= SW - M_:
                    shelf[2] = xu + we + GAP
                    return (xu, y, rot)
        for (ww, hh2, rot) in sorted(((w, hh, False), (hh, w, True)), key=lambda t: t[1]):
            we = max(ww, min(lw, SW - 2 * M_))
            if ww <= SW - 2 * M_ and sh['y_used'] + hh2 + LBL <= SH - M_:
                y = sh['y_used']
                sh['shelves'].append([y, hh2 + LBL, M_ + we + GAP])
                sh['y_used'] = y + hh2 + LBL + GAP
                return (M_, y, rot)
        return None

    # part order keeps each box together on the first sheets; first-fit still fills gaps
    for pc in sorted(pieces, key=lambda q: (q['part']['no'], -q['w'] * q['h'])):
        for si, sh in enumerate(sheets):
            r = try_place(sh, pc['w'], pc['h'])
            if r:
                break
        else:
            sheets.append({'shelves': [], 'y_used': M_ + TOP, 'items': []})
            si = len(sheets) - 1
            r = try_place(sheets[si], pc['w'], pc['h'])
        pc['sheet'], pc['pos'] = si + 1, r
        sheets[si]['items'].append(pc)
    for p in parts:
        p['sheets'] = sorted({pc['sheet'] for pc in pieces if pc['part'] is p})
        p['sheet'] = p['sheets'][0] if p['sheets'] else None

    # ------------------------------------------------ SVG
    def path(pts):
        return 'M' + ' L'.join(f'{x:.2f},{y:.2f}' for x, y in pts) + ' Z'

    os.makedirs(os.path.join(a.out, 'sheets'), exist_ok=True)
    for f in os.listdir(os.path.join(a.out, 'sheets')):
        if f.startswith('sheet-') and f.endswith('.svg'):
            os.remove(os.path.join(a.out, 'sheets', f))
    sheet_meta = []
    for si, sh in enumerate(sheets, 1):
        out = []
        for pc in sh['items']:
            p = pc['part']
            x0, y0, rot = pc['pos']
            W, H = (pc['h'], pc['w']) if rot else (pc['w'], pc['h'])
            def tr(q):
                x, y = q
                if rot:
                    x, y = pc['h'] - y, x
                return (x0 + x, y0 + (H - y))
            outer = [tr(q) for q in pc['outer']]
            d = path(outer) + ''.join(' ' + path([tr(q) for q in h]) for h in pc['holes'])
            g = [f'<g class="part" data-part="{p["id"]}" data-piece="{pc["label"]}">', f'<path class="cut" d="{d}"/>']
            cx, cy = np.mean(outer, axis=0)
            spoly = Polygon(outer, [[tr(q) for q in h] for h in pc['holes']]).buffer(0)
            BAND = min(fs * 1.1, 0.3 * min(W, H))  # hatch band width: easy to see, not the true bevel depth
            avoid = []  # spots taken by bevel numbers / corner angles
            for k, ang in enumerate(pc['angles']):
                if ang is None:
                    continue
                a0, a1 = np.array(outer[k]), np.array(outer[(k + 1) % len(outer)])
                e = (a1 - a0) / (np.linalg.norm(a1 - a0) or 1)
                nrm = np.array([-e[1], e[0]])
                if not spoly.contains(ShPoint(*((a0 + a1) / 2 + nrm * 0.5))):
                    nrm = -nrm
                band = Polygon([a0, a1, a1 + nrm * BAND, a0 + nrm * BAND]).intersection(spoly)
                for bg in ([band] if band.geom_type == 'Polygon' else getattr(band, 'geoms', [])):
                    if bg.geom_type == 'Polygon' and bg.area > 0.01:
                        g.append(f'<path class="miter" d="{path(list(bg.exterior.coords)[:-1])}"/>')
                # no bevel numbers: miters are always shown as 45° hatching
            # corner angles for every corner that is not 90 deg (or a straight 180)
            mv = len(outer)
            for k in range(mv):
                P = np.array(outer[k]); A = np.array(outer[k - 1]); B_ = np.array(outer[(k + 1) % mv])
                u1, u2 = A - P, B_ - P
                l1, l2 = np.linalg.norm(u1), np.linalg.norm(u2)
                if l1 < 1e-6 or l2 < 1e-6:
                    continue
                u1, u2 = u1 / l1, u2 / l2
                th = math.degrees(math.acos(max(-1.0, min(1.0, float(u1 @ u2)))))
                bis = u1 + u2
                bis = bis / np.linalg.norm(bis) if np.linalg.norm(bis) > 1e-9 else np.array([-u1[1], u1[0]])
                if not spoly.contains(ShPoint(*(P + bis * min(l1, l2) * 0.05))):
                    th, bis = 360 - th, -bis
                if abs(th - 90) < 0.5 or abs(th - 180) < 0.5:
                    continue
                r = min(fs * 2.2, 0.35 * min(l1, l2))
                t1 = math.atan2(u1[1], u1[0]); t2 = math.atan2(u2[1], u2[0])
                tb = math.atan2(bis[1], bis[0])
                # sweep from u1 to u2 through the bisector
                d1 = (tb - t1 + math.pi) % (2 * math.pi) - math.pi
                sgn = 1 if d1 >= 0 else -1
                span = math.radians(th)
                pts = [P + r * np.array([math.cos(t1 + sgn * span * i / 16), math.sin(t1 + sgn * span * i / 16)]) for i in range(17)]
                g.append('<path class="arc" d="M' + ' L'.join(f'{x:.2f},{y:.2f}' for x, y in pts) + '"/>')
                lp = P + bis * (r + fs * 1.3)
                if not spoly.contains(ShPoint(*lp)):
                    lp = P + bis * r * 0.55
                g.append(f'<text class="corner" x="{lp[0]:.1f}" y="{lp[1] + fs * 0.32:.1f}" text-anchor="middle">{th:.0f}°</text>')
                avoid.append(ShPoint(*lp).buffer(fs * 1.6))
                avoid.append(ShPoint(*P).buffer(r * 1.05))
            title = f'#{p["no"]} {p["name"]}' + (f' · {pc["label"]}' if pc['label'] else '')
            dim = f'{even(pc["w"])} × {even(pc["h"])} mm'
            has_band = any(a is not None for a in pc['angles'])
            clear = spoly.buffer(-(min(BAND * 1.1, 0.25 * min(W, H)) if has_band else min(fs * 0.3, 0.15 * min(W, H))))
            if avoid:
                clear = clear.difference(unary_union(avoid))
            cands = []
            if not clear.is_empty:
                big = max(clear.geoms, key=lambda q: q.area) if clear.geom_type == 'MultiPolygon' else clear
                if big.contains(big.centroid): cands.append(big.centroid)
                try: cands.append(polylabel(big, tolerance=0.5))
                except Exception: pass
            def fits(cp, bw, bh, vert):
                bw, bh = (bh, bw) if vert else (bw, bh)
                return clear.contains(sbox(cp.x - bw / 2, cp.y - bh / 2, cp.x + bw / 2, cp.y + bh / 2))
            placed = None
            for sc in (1.0, 0.8, 0.65, 0.5, 0.4):  # two lines: name + size
                f1, f2 = fs * 1.0 * sc, fs * 0.8 * sc
                bw = max(len(title) * f1 * 0.5, len(dim) * f2 * 0.48); bh = f1 + f2 * 1.25
                for cp in cands:
                    for vert in (False, True):
                        if fits(cp, bw, bh, vert): placed = ('two', cp, vert, f1, f2); break
                    if placed: break
                if placed: break
            if not placed:  # just the number
                num = f'#{p["no"]}' + (f'·{pc["label"]}' if pc['label'] else '')
                for sc in (0.9, 0.6, 0.4):
                    f1 = fs * sc
                    for cp in cands or [ShPoint(cx, cy)]:
                        if not cands or fits(cp, len(num) * f1 * 0.52, f1, False):
                            placed = ('one', cp, False, f1, num); break
                    if placed: break
            if placed:
                kind, cp, vert, f1, f2 = placed
                rot = f' transform="rotate(-90 {cp.x:.1f} {cp.y:.1f})"' if vert else ''
                if kind == 'two':
                    g.append(f'<g{rot}><text class="lbl" x="{cp.x:.1f}" y="{cp.y - f2 * 0.15:.1f}" text-anchor="middle" style="font-size:{f1:.1f}px">{title}</text>'
                             f'<text class="dim" x="{cp.x:.1f}" y="{cp.y + f2 * 1.05:.1f}" text-anchor="middle" style="font-size:{f2:.1f}px">{dim}</text></g>')
                else:
                    g.append(f'<text class="lbl" x="{cp.x:.1f}" y="{cp.y + f1 * 0.35:.1f}" text-anchor="middle" style="font-size:{f1:.1f}px">{f2}</text>')
            g.append('</g>')
            out.append(''.join(g))
        bar = 100 if SW >= 1000 else 50
        svg = f'''<svg xmlns="http://www.w3.org/2000/svg" width="{SW:g}mm" height="{SH:g}mm" viewBox="0 0 {SW:g} {SH:g}" data-thickness="{T:g}">
<style>
.sheet{{fill:#fff;stroke:#999;stroke-width:{fs*0.08:.2f}}}
.cut{{fill:#f3f3f3;fill-rule:evenodd;stroke:#000;stroke-width:{fs*0.08:.2f}}}
.miter{{fill:url(#hatch);stroke:none}}
.hatch-line{{stroke:#8f1d3b;stroke-width:{fs*0.2:.2f}}}
.arc{{fill:none;stroke:#000;stroke-width:{fs*0.07:.2f}}}
.corner{{font-size:{fs*0.85:.1f}px;font-weight:bold}}
@import url('https://fonts.googleapis.com/css2?family=Barlow+Condensed:wght@500;600;700&amp;display=swap');
text{{font-family:'Barlow Condensed','Arial Narrow',Arial,sans-serif;fill:#000}}
.lbl{{font-size:{fs*1.1:.1f}px;font-weight:bold}} .dim,.small{{font-size:{fs*0.9:.1f}px}}
.ang{{font-size:{fs*0.9:.1f}px;font-weight:bold;fill:#8f1d3b}} .up{{font-size:{fs*0.8:.1f}px;letter-spacing:.1em;fill:#777}}
.title{{font-size:{fs*1.6:.1f}px;font-weight:bold}}
.scale{{stroke:#000;stroke-width:{fs*0.1:.2f};fill:none}}
</style>
<defs><pattern id="hatch" width="{fs*0.55:.2f}" height="{fs*0.55:.2f}" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
<line class="hatch-line" x1="0" y1="0" x2="0" y2="{fs*0.55:.2f}"/></pattern></defs>
<rect class="sheet" x="0" y="0" width="{SW:g}" height="{SH:g}"/>
<text class="title" x="{M_:.1f}" y="{M_ + fs * 1.6:.1f}">Sheet {si} / {len(sheets)}</text>
{chr(10).join(out)}
</svg>'''
        fname = f'sheets/sheet-{si:02d}.svg'
        with open(os.path.join(a.out, fname), 'w') as f:
            f.write(svg)
        sheet_meta.append({'index': si, 'file': fname, 'thickness': T, 'w': SW, 'h': SH,
                           'parts': sorted({pc['part']['id'] for pc in sh['items']})})

    # ------------------------------------------------ GLB (geometry only, in metres)
    scene = trimesh.Scene()
    S = np.diag([0.001, 0.001, 0.001, 1])
    for p in parts:
        scene.add_geometry(p['_solid'], node_name=p['id'], geom_name=p['id'], transform=S)
    scene_list, fig_list, hw_list = [], [], []
    solids_all = [p['_geo'] for p in parts] if parts else None
    for o in pins:
        nid = 'hw__' + safe_id(o['name'])
        scene.add_geometry(trimesh.Trimesh(o['mesh'].vertices, o['mesh'].faces), node_name=nid, geom_name=nid, transform=S)
        size = [even(x) for x in (o['mesh'].bounds[1] - o['mesh'].bounds[0])]
        base = re.sub(r'\.\d+$', '', o['name']).strip()
        hw = {'id': nid, 'name': o['name'], 'type': base, 'size': size}
        gap = pin_gap(o['mesh'], solids_all)
        if gap:
            hw['gap'] = gap
        hw_list.append(hw)
    for o in scenery:
        nid = 'scene__' + safe_id(o['name'])
        scene.add_geometry(trimesh.Trimesh(o['mesh'].vertices, o['mesh'].faces), node_name=nid, geom_name=nid, transform=S)
        scene_list.append({'id': nid, 'name': o['name']})
    for o in figures:
        nid = 'figure__' + safe_id(o['name'])
        scene.add_geometry(trimesh.Trimesh(o['mesh'].vertices, o['mesh'].faces), node_name=nid, geom_name=nid, transform=S)
        fig_list.append({'id': nid, 'name': o['name'], 'height': o['height']})
    os.makedirs(os.path.join(a.out, 'models'), exist_ok=True)
    model_file = f'models/{pid}.glb'
    scene.export(os.path.join(a.out, model_file), include_normals=True)

    for p in parts:
        for k in [k for k in p if k.startswith('_')]:
            del p[k]
    data = {'version': '0.4', 'id': pid, 'project': project, 'units': 'mm', 'model': model_file, 'thickness': T,
            'scale': a.scale, 'source': os.path.basename(a.glb),
            'converted': datetime.date.today().isoformat(),
            'sheet_size': [SW, SH], 'groups': groups_meta, 'parts': parts, 'sheets': sheet_meta,
            'scene': scene_list, 'figures': fig_list, 'hardware': hw_list, 'cameras': read_cameras(a.glb, a.scale), 'warnings': warnings}
    with open(os.path.join(a.out, 'parts.json'), 'w') as f:
        json.dump(data, f, indent=1, ensure_ascii=False)

    print(f'{project}: {len(parts)} parts, {len(pieces)} pieces, {len(sheet_meta)} sheet(s), '
          f'{len(fig_list)} figure(s), {len(scene_list)} scenery, {len(data["cameras"])} camera(s), {len(hw_list)} pin joint(s)')
    for w in warnings:
        print('  !', w)
    if os.path.abspath(os.path.dirname(os.path.normpath(a.out))) == os.path.abspath(a.projects):
        rebuild_index(a.projects)


def rebuild_index(root):
    """List every projects/<id>/parts.json in projects/projects.json for the start screen."""
    items = []
    for d in sorted(os.listdir(root)) if os.path.isdir(root) else []:
        pj = os.path.join(root, d, 'parts.json')
        if not os.path.isfile(pj):
            continue
        with open(pj) as f:
            data = json.load(f)
        thumb = next((f'{d}/thumb.{e}' for e in ('png', 'jpg', 'jpeg', 'webp', 'svg') if os.path.isfile(os.path.join(root, d, f'thumb.{e}'))), None)
        desc_f = os.path.join(root, d, 'description.txt')
        meta_f = os.path.join(root, d, 'meta.json')  # optional: {"pinned": true, "label": "Tutorial", "description": "..."}
        meta = json.load(open(meta_f)) if os.path.isfile(meta_f) else {}
        sheets = data.get('sheets', [])
        size = data.get('sheet_size') or ([sheets[0].get('w', 297), sheets[0].get('h', 210)] if sheets else None)
        items.append({
            'id': d, 'name': data.get('project', d),
            'parts': len(data.get('parts', [])), 'sheets': len(sheets),
            'sheet_size': size, 'thickness': data.get('thickness'),
            'scale': data.get('scale'), 'converted': data.get('converted'), 'hardware': len(data.get('hardware', [])),
            'thumb': thumb,
            'description': meta.get('description') or (open(desc_f).read().strip() if os.path.isfile(desc_f) else None),
            'pinned': bool(meta.get('pinned')), 'label': meta.get('label'), 'aliases': meta.get('aliases', []),
            'split': sum(1 for q in data.get('parts', []) if (q.get('pieces') or 1) > 1),
        })
    items.sort(key=lambda x: x.get('converted') or '', reverse=True)
    items.sort(key=lambda x: not x['pinned'])  # pinned first, newest first otherwise
    with open(os.path.join(root, 'projects.json'), 'w') as f:
        json.dump({'projects': items}, f, indent=1, ensure_ascii=False)
    print(f'projects.json: {len(items)} project(s)')


if __name__ == '__main__':
    main()
