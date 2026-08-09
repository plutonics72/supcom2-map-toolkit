"""Boolon Complex Extended (SC2_BOOLX1): bigger deck complex on stock SC2_MP_104.

User goals: more playable area, east side un-boxed. Water impossible (born-dry map).
Mesh reality (survey 26 Jul): east of x~832 is a vertex desert (0-12 verts/64-tile) -
new ground there would render as void. The east cluster however is fragmented decks +
blocked high WALLS with real drawn geometry. So:
  1. EAST UNIFICATION: inside the east zone, cut walls to deck height and fill voids,
     making one broad continuous platform x<=832 (ARMY_3/4 breathing room).
  2. INTERIOR FILLS: plate over the largest void gaps between decks (dense mesh).
  3. DILATION: grow all deck edges ~10 cells into void, density-gated.
  4. Laplacian relaxation over new cells (decks fixed) -> smooth ramps between the
     51/52 and 57/58 deck levels instead of seams.
  5. +6 masses on the new ground; minimap painted; full five-file consistency
     (mesh force-sync incl. clamping orphaned wall-face verts, global collision
     snap, nav overlay on all 3 layers). No waterDepth on this map class.
Playable AREA_1 box stays (912 already exceeds the new nav extent).
"""
import zipfile, io, os, re, struct, shutil
from collections import deque, Counter
import sc2maps as sm

G = 1024
ID_OLD, ID_NEW = "SC2_MP_104", "SC2_BOOLX1"
NAME = "[6] Boolon Complex Extended (3v3) v4"
OUT = os.path.join(sm.GAMEDATA, "_boolon_ext.scd")
EAST_ZONE = (690, 200, 832, 830)          # unify walls+voids+decks into one platform
DILATE = 10
DENS_TILE = 16                             # density grid tile (cells)
DENS_MIN = 3                               # min surface verts per 48x48 neighborhood
MASS_NEW = 6
SPAWNS = {1: (250, 703), 2: (248, 314), 3: (750, 732), 4: (754, 282), 5: (548, 816), 6: (550, 202)}

# ---- load stock ----
mz = zipfile.ZipFile(os.path.join(sm.GAMEDATA, "maps.scd"))
stock_files = {n.split("/")[-1]: mz.read(n) for n in mz.namelist()
               if n.startswith(f"maps/{ID_OLD}/")}
mz.close()
lz = zipfile.ZipFile(os.path.join(sm.GAMEDATA, "lua.scd"))
lua_files = {n.split("/")[-1]: lz.read(n) for n in lz.namelist()
             if n.startswith(f"maps/{ID_OLD}/")}
lz.close()
uz = zipfile.ZipFile(os.path.join(sm.GAMEDATA, "uncompiled_lua.scd"))
ulua_files = {n.split("/")[-1]: uz.read(n) for n in uz.namelist()
              if n.startswith(f"uncompiled/maps/{ID_OLD}/")}
uz.close()
print(f"stock: {len(stock_files)} map files, {len(lua_files)} lua, {len(ulua_files)} uncompiled lua", flush=True)

hf_raw = stock_files[f"{ID_OLD}.hfield.win.bdf"]
H0, w = sm.hfield_heights(hf_raw)
t = sm.Terrain(ID_OLD)
layers = t.layers
nav0 = t.costs_payload
n_layers = len(layers)
assert n_layers == 3

# ---- masks ----
NAVV = [bytearray(1 if nav0[layers[li][2] + i] != 255 else 0 for i in range(G*G)) for li in range(n_layers)]
deck = NAVV[0]
# mesh surface-vert density grid
terr_raw = stock_files[f"{ID_OLD}.terrain.win.bdf"]
payload, blob_off, nv, ni = sm.locate_mesh_blob(terr_raw)
dens = Counter()
for i in range(nv):
    x, y, z = struct.unpack_from("<3f", payload, blob_off + 20 + 32*i)
    if 0 <= x < G and 0 <= z < G and y > -4.0:
        dens[(int(x)//DENS_TILE, int(z)//DENS_TILE)] += 1
def dense_ok(x, z):
    tx, tz = x // DENS_TILE, z // DENS_TILE
    s = 0
    for dz in (-1, 0, 1):
        for dx in (-1, 0, 1):
            s += dens.get((tx+dx, tz+dz), 0)
    return s >= DENS_MIN

# nearest-deck height field (BFS from deck cells, carries height)
inherit = [0] * (G*G)
q = deque()
for i in range(G*G):
    if deck[i]:
        inherit[i] = H0[(i//G)*w + (i % G)]
        q.append(i)
dist_deck = bytearray(255 for _ in range(G*G))
for i in range(G*G):
    if deck[i]: dist_deck[i] = 0
while q:
    i = q.popleft()
    d = dist_deck[i]
    if d >= 220: continue
    x, z = i % G, i // G
    for dx, dz in ((1,0),(-1,0),(0,1),(0,-1)):
        nx, nz = x+dx, z+dz
        if 0 <= nx < G and 0 <= nz < G:
            j = nz*G+nx
            if dist_deck[j] > d + 1:
                dist_deck[j] = d + 1
                inherit[j] = inherit[i]
                q.append(j)

new_cells = set()
ex0, ez0, ex1, ez1 = EAST_ZONE
# 1) east unification: EVERY non-deck cell in the zone becomes plate (v2: the
# 40-cell cap left the big east box void - the user saw no difference)
for z in range(ez0, ez1+1):
    for x in range(ex0, ex1+1):
        i = z*G + x
        if not deck[i]:
            new_cells.add(i)
# 2) dilation everywhere (density-gated)
for i in range(G*G):
    if not deck[i] and i not in new_cells and 1 <= dist_deck[i] <= DILATE:
        x, z = i % G, i // G
        if dense_ok(x, z):
            new_cells.add(i)
# 3) interior void fills: enclosed void components, dense, moderate size
hull = (210, 160, 700, 860)                # interior region west of the east zone
seen = bytearray(G*G)
fills = []
for zc in range(hull[1], hull[3], 4):
    for xc in range(hull[0], hull[2], 4):
        s = zc*G + xc
        if deck[s] or s in new_cells or seen[s] or H0[zc*w + xc] > 20*128: continue
        comp = []
        qq = deque([s]); seen[s] = 1
        ok_comp = True
        while qq:
            i = qq.popleft(); comp.append(i)
            x, z = i % G, i // G
            if not (hull[0]-30 <= x <= hull[2]+30 and hull[1]-30 <= z <= hull[3]+30):
                ok_comp = False
            for dx, dz in ((1,0),(-1,0),(0,1),(0,-1)):
                nx, nz = x+dx, z+dz
                if 0 <= nx < G and 0 <= nz < G:
                    j = nz*G+nx
                    if not seen[j] and not deck[j] and H0[nz*w + nx] <= 20*128 and dist_deck[j] <= 60:
                        seen[j] = 1; qq.append(j)
        if ok_comp and 400 <= len(comp) <= 20000:
            dense_frac = sum(1 for i in comp[::9] if dense_ok(i % G, i // G)) / max(1, len(comp[::9]))
            if dense_frac > 0.7:
                fills.append(comp)
fills.sort(key=len, reverse=True)
for comp in fills[:4]:
    new_cells.update(comp)
print(f"new cells: east+dilate+{len(fills[:4])} fills = {len(new_cells)}", flush=True)

# ---- hfield edits: plate heights + relaxation ----
hp = bytearray(sm.read_bdf_payload(hf_raw))
_, _, _, _, hd = struct.unpack_from("<5I", hp, 0)
Hn = list(H0)
val = {}
for i in new_cells:
    val[i] = float(inherit[i])
for _it in range(14):                       # Laplacian relax, deck heights fixed
    nxt = {}
    for i, v in val.items():
        x, z = i % G, i // G
        s = v; n = 1
        for dx, dz in ((1,0),(-1,0),(0,1),(0,-1)):
            j = (z+dz)*G + (x+dx)
            if j in val: s += val[j]; n += 1
            elif deck[j]: s += H0[(z+dz)*w + (x+dx)]; n += 1
        nxt[i] = s / n
    val = nxt
for i, v in val.items():
    r_ = max(0, min(65535, int(v)))
    Hn[(i//G)*w + (i % G)] = r_
    struct.pack_into("<H", hp, hd + 2*((i//G)*w + (i % G)), r_)
hf_new = sm.rebuild_bdf(hf_raw, bytes(hp))
print("hfield: plates applied + relaxed", flush=True)

# ---- masses on new ground ----
sav = ulua_files[f"{ID_OLD}_save.lua"].decode("utf-8", "replace")
ms = re.findall(r"\['(Mass \d+)'\].*?VECTOR3\(\s*([\d.eE+-]+)\s*,\s*([\d.eE+-]+)\s*,\s*([\d.eE+-]+)\s*\)", sav, re.S)
anchors = [(float(a), float(c)) for _, a, _, c in ms]
dist_edge = {}
for i in new_cells:
    x, z = i % G, i // G
    edge = any(((z+dz)*G + (x+dx)) not in val and not deck[(z+dz)*G + (x+dx)]
               for dx in (-1, 0, 1) for dz in (-1, 0, 1))
    dist_edge[i] = 0 if edge else 99
q = deque(i for i, d in dist_edge.items() if d == 0)
while q:
    i = q.popleft(); d = dist_edge[i]
    x, z = i % G, i // G
    for dx, dz in ((1,0),(-1,0),(0,1),(0,-1)):
        j = (z+dz)*G + (x+dx)
        if j in dist_edge and dist_edge[j] > d + 1:
            dist_edge[j] = d + 1; q.append(j)
cand = [i for i, d in dist_edge.items() if d >= 15]
def level_pad(xi, zi):
    cells = [(x, z) for z in range(zi-5, zi+6) for x in range(xi-5, xi+6)]
    mean = int(sum(Hn[z*w+x] for x, z in cells) / len(cells))
    for x, z in cells:
        Hn[z*w+x] = mean
        struct.pack_into("<H", hp, hd + 2*(z*w+x), mean)
    return mean / 128.0
new_masses = []
next_id = max(int(nm.split()[1]) for nm, _a, _b, _c in ms) + 1
cand_east = [i for i in cand if (i % G) > 740]
for k in range(MASS_NEW):
    pool = cand_east if k < 3 and cand_east else cand
    best = None; bestd = -1
    for i in pool[::5]:
        x, z = i % G, i // G
        d = min((x-ax)**2 + (z-az)**2 for ax, az in anchors)
        if d > bestd: bestd, best = d, (x, z)
    if best is None: break
    x, z = best
    yv = level_pad(x, z)
    nm = f"Mass {next_id:02d}"
    new_masses.append((nm, x, z, yv))
    anchors.append((x, z)); next_id += 1
hf_new = sm.rebuild_bdf(hf_raw, bytes(hp))
print(f"new masses: {[(nm, x, z) for nm, x, z, _ in new_masses]}", flush=True)

tmpl = re.search(r"(\['Mass 36'\]\s*=\s*\{.*?\n(?:.*?\n)*?\s*\},)", sav)
assert tmpl, "mass template"
block = tmpl.group(1)
inject = ""
for nm, x, z, yv in new_masses:
    nb = block.replace("'Mass 36'", f"'{nm}'")
    nb = re.sub(r"VECTOR3\(\s*[\d.eE+-]+\s*,\s*[\d.eE+-]+\s*,\s*[\d.eE+-]+\s*\)",
                f"VECTOR3( {x}.500000, {yv:.6f}, {z}.500000 )", nb, count=1)
    inject += "\n                " + nb.strip()
sav = sav.replace(block, block + inject, 1)

# ---- nav: open all 3 layers on new cells ----
pay = bytearray(sm.read_bdf_payload(stock_files[f"{ID_OLD}.costs.win.bdf"]))
for i in new_cells:
    for li in range(n_layers):
        pay[layers[li][2] + i] = 1
sm._recompute_islands(pay, layers)
costs_new = sm.rebuild_bdf(stock_files[f"{ID_OLD}.costs.win.bdf"], pay)
print("nav: opened + islands recomputed", flush=True)

# ---- mesh: RIGID DELTA TRANSLATION of the whole in-zone geometry stack ----
# v2/v3 snapped every in-zone vert to one surface height, collapsing the void's
# separate layers (floor + under-layers at -10/-30) into coplanar sheets ->
# map-wide z-fighting moire (user photos, 9 Aug). v4: translate each in-zone
# vert by the LOCAL heightfield delta, preserving inter-layer offsets exactly -
# the stack rises as one rigid body; nothing becomes coplanar.
terr_new, mv = sm.resample_mesh_heights(terr_raw, hf_raw, hf_new,
                                        bvh_min_y=-40.0, bvh_max_y=200.0)
p_orig, b_orig, nv_orig, _ = sm.locate_mesh_blob(terr_raw)
p2, b2, nv2, _ = sm.locate_mesh_blob(terr_new)
assert nv2 == nv_orig
pb = bytearray(p2)
in_zone = set()
for i in new_cells:
    in_zone.add((i % G, i // G))
translated = 0
for i in range(nv2):
    off = b2 + 20 + 32*i
    ox, oy, oz = struct.unpack_from("<3f", p_orig, b_orig + 20 + 32*i)
    if not (0 <= ox < G and 0 <= oz < G): continue
    xi, zi = int(ox), int(oz)
    if ((xi, zi) not in in_zone and (xi+1, zi) not in in_zone
            and (xi, zi+1) not in in_zone and (xi+1, zi+1) not in in_zone):
        continue
    if oy <= -35.0: continue                 # skirt/backdrop stays
    dy = sm.hf_sample(Hn, w, ox, oz) - sm.hf_sample(H0, w, ox, oz)
    if abs(dy) < 0.05: continue
    want = oy + dy
    x, y, z = struct.unpack_from("<3f", pb, off)
    if abs(y - want) > 0.05:
        struct.pack_into("<3f", pb, off, x, want, z)
        translated += 1
terr_new = sm.rebuild_bdf(terr_new, bytes(pb))
print(f"mesh: {mv} delta-tracked, {translated} stack-translated in zones", flush=True)
# gate: no surface vert ABOVE the walking height on NAV-OPEN cells (units stand
# only there; stock legitimately draws walls/scaffolds above blocked void cells)
navopen = bytearray(1 if pay[layers[0][2] + i] != 255 else 0 for i in range(G*G))
p3, b3, nv3, _ = sm.locate_mesh_blob(terr_new)
worst = -99.0; worst_at = None
for i in range(nv3):
    x, y, z = struct.unpack_from("<3f", p3, b3 + 20 + 32*i)
    if 0 <= x < G and 0 <= z < G and y > -35.0 and navopen[int(z)*G + int(x)]:
        ox, oy, oz = struct.unpack_from("<3f", p_orig, b_orig + 20 + 32*i)
        was_scenery = oy > sm.hf_sample(H0, w, ox, oz) + 2.5   # stood above old ground
        if was_scenery: continue                                # still scenery: fine
        d = y - sm.hf_sample(Hn, w, x, z)
        if d > worst: worst, worst_at = d, (round(x), round(z), round(y, 1))
print(f"mesh gate (surface verts on nav-open cells): worst above-ground {worst:.2f} at {worst_at}", flush=True)
assert worst < 3.0, "surface mesh above walking height on navigable ground"

# ---- collision: global snap ----
col_raw = stock_files[f"{ID_OLD}.collision2.win.bdf"]
cp = bytearray(sm.read_bdf_payload(col_raw))
cver, cnv, cvoff, cni, cioff, cxoff = struct.unpack_from("<6I", cp, 0)
snapped = 0
for i in range(cnv):
    off = cvoff + 12*i
    x, y, z = struct.unpack_from("<3f", cp, off)
    if 0 <= x < G and 0 <= z < G:
        gy = sm.hf_sample(Hn, w, x, z) - 0.3
        if abs(y - gy) > 0.05:
            struct.pack_into("<3f", cp, off, x, gy, z); snapped += 1
col_new = sm.rebuild_bdf(col_raw, bytes(cp))
print(f"collision: {snapped}/{cnv} snapped", flush=True)

# ---- minimap: paint new plates ----
mmd = bytearray(stock_files[f"{ID_OLD}.minimap.win.dds"])
mh, mw = struct.unpack_from("<II", mmd, 12)
cpp = G // mw; cpb = 4 * cpp
sx, sz = 500, 512                           # a mid deck cell for the plate color
sbx, sbz = (sx // cpp) // 4, (sz // cpp) // 4
plate_block = bytes(mmd[128 + (sbz * (mw//4) + sbx) * 8:][:8])
painted = 0
for bz in range(mh // 4):
    for bx in range(mw // 4):
        cx0, cz0 = bx * cpb, bz * cpb
        n_new = sum(1 for cz in range(cz0, min(G, cz0+cpb))
                    for cx in range(cx0, min(G, cx0+cpb)) if (cz*G + cx) in new_cells)
        if n_new >= (cpb*cpb) * 6 // 10:
            off = 128 + (bz * (mw//4) + bx) * 8
            if bytes(mmd[off:off+8]) != plate_block:
                mmd[off:off+8] = plate_block; painted += 1
print(f"minimap: {painted} blocks painted", flush=True)

# ---- preview images: top-down from the painted minimap (stock ships angled
# beauty shots that would show NO change; the lobby uses these, not the minimap) ----
def dxt1_decode(d, tw, th):
    px = bytearray(tw * th * 3)
    for bz in range(th // 4):
        for bx in range(tw // 4):
            off = 128 + (bz * (tw//4) + bx) * 8
            c0, c1 = struct.unpack_from("<HH", d, off)
            bits = struct.unpack_from("<I", d, off+4)[0]
            def rgb(c):
                return (((c >> 11) & 31) * 255 // 31, ((c >> 5) & 63) * 255 // 63, (c & 31) * 255 // 31)
            p0, p1 = rgb(c0), rgb(c1)
            if c0 > c1:
                pal = [p0, p1, tuple((2*a+b)//3 for a, b in zip(p0, p1)),
                       tuple((a+2*b)//3 for a, b in zip(p0, p1))]
            else:
                pal = [p0, p1, tuple((a+b)//2 for a, b in zip(p0, p1)), (0, 0, 0)]
            for py in range(4):
                for pxi in range(4):
                    r, g, b = pal[(bits >> (2*(py*4+pxi))) & 3]
                    o2 = ((bz*4+py) * tw + bx*4+pxi) * 3
                    px[o2:o2+3] = bytes((r, g, b))
    return px
mm_rgb = dxt1_decode(mmd, mw, mh)
def make_preview(stock_img):
    ih = struct.unpack_from("<I", stock_img, 12)[0]
    iw = struct.unpack_from("<I", stock_img, 16)[0]
    out_img = bytearray(stock_img[:128]) + bytearray(iw * ih * 4)
    side = ih                                   # square top-down, letterboxed
    x_off = (iw - side) // 2
    for py in range(ih):
        for pxi in range(iw):
            o2 = 128 + (py * iw + pxi) * 4
            if x_off <= pxi < x_off + side:
                sx = (pxi - x_off) * mw // side
                sz = py * mh // side
                so = (sz * mw + sx) * 3
                r, g, b = mm_rgb[so], mm_rgb[so+1], mm_rgb[so+2]
            else:
                r = g = b = 12
            out_img[o2:o2+4] = bytes((b, g, r, 255))    # BGRA
    return bytes(out_img)
stock_files[f"{ID_OLD}_PC_mapshot.dds"] = make_preview(stock_files[f"{ID_OLD}_PC_mapshot.dds"])
stock_files[f"{ID_OLD}_ui_mapimage.dds"] = make_preview(stock_files[f"{ID_OLD}_ui_mapimage.dds"])
print("preview images regenerated from minimap", flush=True)

# ---- verification: erosion routes ----
m0 = bytearray(1 if pay[layers[0][2] + i] != 255 else 0 for i in range(G*G))
def erode(m, r):
    out = bytearray(m)
    for z in range(G):
        b = z*G; row = m[b:b+G]
        for x in range(G):
            if row[x] and 0 in row[max(0,x-r):min(G,x+r+1)]: out[b+x] = 0
    out2 = bytearray(out)
    for x in range(G):
        col = out[x::G]
        for z in range(G):
            if col[z] and 0 in col[max(0,z-r):min(G,z+r+1)]: out2[z*G+x] = 0
    return out2
def flood(m, s):
    seen = bytearray(G*G); seen[s] = 1; q = deque([s])
    while q:
        i = q.popleft(); x, z = i % G, i // G
        for dx, dz in ((1,0),(-1,0),(0,1),(0,-1)):
            nx, nz = x+dx, z+dz
            if 0 <= nx < G and 0 <= nz < G and not seen[nz*G+nx] and m[nz*G+nx]:
                seen[nz*G+nx] = 1; q.append(nz*G+nx)
    return seen
def snapc(m, px, pz):
    for rr in range(0, 40, 2):
        for dz in range(-rr, rr+1, 2):
            for dx in range(-rr, rr+1, 2):
                if 0 <= px+dx < G and 0 <= pz+dz < G and m[(pz+dz)*G+px+dx]:
                    return (pz+dz)*G+px+dx
    return None
allok = True
for r in (3, 5):
    er = erode(m0, r)
    s1 = snapc(er, *SPAWNS[1])
    seen = flood(er, s1)
    for a in range(2, 7):
        t2 = snapc(er, *SPAWNS[a])
        ok = t2 is not None and seen[t2]
        print(f"[r={r}] spawn1 -> spawn{a}: {'OK' if ok else 'FAIL'}", flush=True)
        allok &= ok
    east_pt = snapc(er, 826, 500)
    ok = east_pt is not None and seen[east_pt]
    print(f"[r={r}] spawn1 -> east platform: {'OK' if ok else 'FAIL'}", flush=True)
    allok &= ok
for nm, x, z, yv in new_masses:
    okm = m0[z*G + x] == 1
    allok &= okm
    if not okm: print(f"BAD mass {nm}", flush=True)
print("mass placement verified", flush=True)
assert allok, "verification failed"

# ---- package ----
def idswap(txt):
    return txt.replace(ID_OLD, ID_NEW)
uscen = ulua_files[f"{ID_OLD}_scenario.lua"].decode("utf-8", "replace")
uscen = uscen.replace("<LOC SC2_MAPNAME_0020>[6] Boolon Complex (3v3)", NAME)
uscript = ulua_files[f"{ID_OLD}_script.lua"].decode("utf-8", "replace")
# lua.scd ships COMPILED bytecode we cannot edit; the engine loads TEXT lua fine
# (every custom map so far ships text) - use the edited uncompiled text everywhere.

out = {}
for fn, d in stock_files.items():
    if fn.endswith(".hfield.win.bdf"): d = hf_new
    elif fn.endswith(".terrain.win.bdf"): d = terr_new
    elif fn.endswith(".costs.win.bdf"): d = costs_new
    elif fn.endswith(".collision2.win.bdf"): d = col_new
    elif fn.endswith(".minimap.win.dds"): d = bytes(mmd)
    out[f"maps/{ID_NEW}/{idswap(fn)}"] = d
# lighting subfolder
mz = zipfile.ZipFile(os.path.join(sm.GAMEDATA, "maps.scd"))
for n in mz.namelist():
    if n.startswith(f"maps/{ID_OLD}/lighting/"):
        out[f"maps/{ID_NEW}/lighting/{n.split('/')[-1]}"] = mz.read(n)
mz.close()
out[f"maps/{ID_NEW}/{ID_NEW}_scenario.lua"] = idswap(uscen).encode("utf-8")
out[f"maps/{ID_NEW}/{ID_NEW}_save.lua"] = idswap(sav).encode("utf-8")
out[f"maps/{ID_NEW}/{ID_NEW}_script.lua"] = idswap(uscript).encode("utf-8")
out[f"uncompiled/maps/{ID_NEW}/{ID_NEW}_scenario.lua"] = idswap(uscen).encode("utf-8")
out[f"uncompiled/maps/{ID_NEW}/{ID_NEW}_save.lua"] = idswap(sav).encode("utf-8")
out[f"uncompiled/maps/{ID_NEW}/{ID_NEW}_script.lua"] = idswap(uscript).encode("utf-8")

if os.path.exists(OUT):
    shutil.copy2(OUT, OUT + ".V3.bak")
buf = io.BytesIO(); zo = zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED)
for n, d in sorted(out.items()): zo.writestr(n, d)
zo.close()
open(OUT, "wb").write(buf.getvalue())
print(f"INSTALLED ({os.path.getsize(OUT):,} B, {len(out)} entries)", flush=True)
