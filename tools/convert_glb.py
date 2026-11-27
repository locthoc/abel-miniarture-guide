"""
Convert a Blender .glb of flat planes into a foamex viewer project.

    python tools/convert_glb.py path/to/model.glb [--out .] [--thickness 5] [--sheet 1220x2440]

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

Outputs (into --out): parts.json, models/<name>.glb (geometry only), sheets/sheet-NN.svg
"""
import argparse, json, math, os, re, string
import numpy as np
import trimesh
from shapely.geometry import Polygon, box as sbox
from shapely.ops import unary_union
from shapely import orient_polygons

TAG = re.compile(r'mit(er|re)(e?d)?', re.I)
FIGURE = re.compile(r'dummy|human|figure|ragdoll|body|person|people', re.I)
SCENERY = re.compile(r'(^|[^a-z])set([^a-z]|$)', re.I)
TOL = 0.5  # mm


def clean_name(n):
    n = TAG.sub('', n)
    n = re.sub(r'^[._\-\s]+|[._\-\s]+$', '', n)
    n = re.sub(r'[._\-\s]{2,}', '_', n)
    return n or 'plate'


def safe_id(n):
    return re.sub(r'[^A-Za-z0-9_-]', '_', n)


# ---------------------------------------------------------------- geometry helpers
def load(path):
    s = trimesh.load(path, force='scene')
    objs = []
    for node in s.graph.nodes_geometry:
        M, g = s.graph[node]
        m = s.geometry[g]
        if not isinstance(m, trimesh.Trimesh) or len(m.faces) == 0:
            continue
        v = trimesh.transform_points(m.vertices, M) * 1000.0  # m -> mm
        objs.append({'name': node, 'mesh': trimesh.Trimesh(v, m.faces.copy(), process=True)})
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
    ap.add_argument('glb')
    ap.add_argument('--out', default='.')
    ap.add_argument('--thickness', type=float, default=5.0)
    ap.add_argument('--sheet', default='1220x2440')
    ap.add_argument('--name', default=None)
    a = ap.parse_args()
    T = a.thickness
    SW, SH = map(float, a.sheet.lower().split('x'))
    project = a.name or os.path.splitext(os.path.basename(a.glb))[0]
    warnings = []

    plates, figures, scenery = [], [], []
    for o in load(a.glb):
        if FIGURE.search(o['name']):
            figures.append(o); continue
        n = plane_of(o['mesh'])
        if n is None:
            figures.append(o); continue
        if SCENERY.search(o['name']):
            scenery.append(o); continue
        o['n'] = n
        o['tag'] = bool(TAG.search(o['name']))
        o['area'] = float(o['mesh'].area)
        outline(o)
        plates.append(o)
    N = len(plates)

    def joins_of(i):
        """edge index -> neighbour plate index"""
        res = {}
        for ei, (a0, a1) in enumerate(plates[i]['edges3']):
            L = np.linalg.norm(a1 - a0)
            for j in range(N):
                if j == i:
                    continue
                ov = max((seg_overlap(a0, a1, b0, b1) for b0, b1 in plates[j]['edges3']), default=0)
                if ov > 1.0:
                    res[ei] = j
                    if ov < L - 2 * TOL:
                        warnings.append(f"{plates[i]['name']}: edge only partly meets {plates[j]['name']} "
                                        f"({ov:.0f} of {L:.0f} mm), whole edge treated as joined")
                    break
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
                    shorten[ei] = T / s
                    butt[ei] = True
        outer = offset_outline(ext, shorten) if any(shorten) else ext.copy()
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
            'w': round(float(outer[:, 0].max() - minx), 1), 'h': round(float(outer[:, 1].max() - miny), 1),
            'miters': sum(1 for x in angles if x is not None),
            'bevels': sorted({x for x in angles if x is not None}),
            'shortened': sum(butt), 'holes': len(o['holes']),
            '_outer': sh(outer), '_holes': [sh(h) for h in o['holes']], '_angles': angles,
            '_solid': solid(outer, inner, o['holes'], c, u, v, n, T),
        })
        if sum(butt):
            parts[-1]['note'] = f'shortened {T:g} mm on {sum(butt)} edge(s) (butt joint)'

    for o in figures:
        o['height'] = round(float(o['mesh'].bounds[1][1] - o['mesh'].bounds[0][1]), 1)

    # ------------------------------------------------ pieces (split oversize plates into panels)
    fs = max(SW, SH) / 75
    M_, GAP, LBL, TOP = SW * 0.02, max(8.0, SW * 0.012), fs * 3.4, fs * 5.2
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
            r = try_place(sh, pc['w'], pc['h'], label_w(pc))
            if r:
                break
        else:
            sheets.append({'shelves': [], 'y_used': M_ + TOP, 'items': []})
            si = len(sheets) - 1
            r = try_place(sheets[si], pc['w'], pc['h'], label_w(pc))
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
            for k, ang in enumerate(pc['angles']):
                if ang is None:
                    continue
                a0, a1 = outer[k], outer[(k + 1) % len(outer)]
                g.append(f'<path class="miter" d="M{a0[0]:.2f},{a0[1]:.2f} L{a1[0]:.2f},{a1[1]:.2f}"/>')
                if abs(ang - 45) > 0.5:  # only non-45 bevels get a number
                    mid = (np.array(a0) + np.array(a1)) / 2
                    inward = np.array([cx, cy]) - mid
                    inward /= (np.linalg.norm(inward) or 1)
                    tp = mid + inward * fs * 1.8
                    g.append(f'<text class="ang" x="{tp[0]:.1f}" y="{tp[1] + fs * 0.35:.1f}" text-anchor="middle">{ang:g}°</text>')
            if H > W * 1.5:
                uy = y0 + H * 0.3
                g.append(f'<text class="up" x="{cx:.1f}" y="{uy:.1f}" text-anchor="middle" transform="rotate(-90 {cx:.1f} {uy:.1f})">THIS SIDE UP</text>')
            else:
                ux = x0 + W * 0.3 if W > H * 1.5 else cx
                g.append(f'<text class="up" x="{ux:.1f}" y="{cy + fs * 0.3:.1f}" text-anchor="middle">THIS SIDE UP</text>')
            title = f'#{p["no"]} {p["name"]}' + (f' · panel {pc["label"]} of {p["pieces"]}' if pc['label'] else '')
            dim = f'{pc["w"]:.0f} × {pc["h"]:.0f} mm'
            # keep labels on the sheet: shift left if the (estimated) text width would run off the edge
            est = max(len(title) * fs * 1.1 * 0.58, len(dim) * fs * 0.9 * 0.55)
            lx = min(x0, SW - M_ - est)
            g.append(f'<text class="lbl" x="{lx:.1f}" y="{y0 + H + fs * 1.4:.1f}">{title}</text>')
            g.append(f'<text class="dim" x="{lx:.1f}" y="{y0 + H + fs * 2.7:.1f}">{dim}</text>')
            g.append('</g>')
            out.append(''.join(g))
        bar = 100 if SW >= 600 else 50
        svg = f'''<svg xmlns="http://www.w3.org/2000/svg" width="{SW:g}mm" height="{SH:g}mm" viewBox="0 0 {SW:g} {SH:g}" data-thickness="{T:g}">
<style>
.sheet{{fill:#fff;stroke:#999;stroke-width:{fs*0.08:.2f}}}
.cut{{fill:#f3f3f3;fill-rule:evenodd;stroke:#000;stroke-width:{fs*0.08:.2f}}}
.miter{{fill:none;stroke:#e8400c;stroke-width:{fs*0.3:.2f};stroke-dasharray:{fs*0.9:.1f} {fs*0.5:.1f};stroke-linecap:butt}}
text{{font-family:Helvetica,Arial,sans-serif;fill:#000}}
.lbl{{font-size:{fs*1.1:.1f}px;font-weight:bold}} .dim,.small{{font-size:{fs*0.9:.1f}px}}
.ang{{font-size:{fs*0.9:.1f}px;font-weight:bold;fill:#e8400c}} .up{{font-size:{fs*0.8:.1f}px;letter-spacing:.1em;fill:#777}}
.title{{font-size:{fs*1.6:.1f}px;font-weight:bold}}
.scale{{stroke:#000;stroke-width:{fs*0.1:.2f};fill:none}}
</style>
<rect class="sheet" x="0" y="0" width="{SW:g}" height="{SH:g}"/>
<text class="title" x="{M_:.1f}" y="{M_ + fs * 1.6:.1f}">{project} · Foamex {T:g} mm · Sheet {si}/{len(sheets)} ({SW:.0f} × {SH:.0f})</text>
<text class="small" x="{M_:.1f}" y="{M_ + fs * 2.9:.1f}">Black line = cut. Orange dashed edge = mitered (45° unless marked).</text>
<text class="small" x="{M_:.1f}" y="{M_ + fs * 4.0:.1f}">Printed side = outer face, bevel goes toward the back.</text>
<g transform="translate({SW - M_ - bar:.1f},{SH - M_:.1f})"><path class="scale" d="M0,-{fs*0.8:.1f} V0 H{bar} V-{fs*0.8:.1f}"/>
<text class="small" x="{bar}" y="-{fs:.1f}" text-anchor="end">check: {bar} mm</text></g>
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
    scene_list, fig_list = [], []
    for o in scenery:
        nid = 'scene__' + safe_id(o['name'])
        scene.add_geometry(trimesh.Trimesh(o['mesh'].vertices, o['mesh'].faces), node_name=nid, geom_name=nid, transform=S)
        scene_list.append({'id': nid, 'name': o['name']})
    for o in figures:
        nid = 'figure__' + safe_id(o['name'])
        scene.add_geometry(trimesh.Trimesh(o['mesh'].vertices, o['mesh'].faces), node_name=nid, geom_name=nid, transform=S)
        fig_list.append({'id': nid, 'name': o['name'], 'height': o['height']})
    os.makedirs(os.path.join(a.out, 'models'), exist_ok=True)
    model_file = f'models/{project}.glb'
    scene.export(os.path.join(a.out, model_file), include_normals=True)

    for p in parts:
        for k in [k for k in p if k.startswith('_')]:
            del p[k]
    data = {'project': project, 'units': 'mm', 'model': model_file, 'thickness': T,
            'sheet_size': [SW, SH], 'groups': groups_meta, 'parts': parts, 'sheets': sheet_meta,
            'scene': scene_list, 'figures': fig_list, 'warnings': warnings}
    with open(os.path.join(a.out, 'parts.json'), 'w') as f:
        json.dump(data, f, indent=1, ensure_ascii=False)

    print(f'{project}: {len(parts)} parts, {len(pieces)} pieces, {len(sheet_meta)} sheet(s), '
          f'{len(fig_list)} figure(s), {len(scene_list)} scenery')
    for w in warnings:
        print('  !', w)


if __name__ == '__main__':
    main()
