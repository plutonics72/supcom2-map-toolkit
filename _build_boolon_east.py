"""Boolon Complex Extended v7 (SC2_BOOLX1): EAST STRETCH of stock SC2_MP_104.

User (9 Aug): "just the land areas for the 2 most right players extended to the
right and the bridge that connects them doubled in width."

Method: a monotonic 1-D horizontal WARP of world x, applied identically to every
spatial file, so the five-file stack stays consistent BY CONSTRUCTION:
  f(x) = x                          x <= 740         west of the east decks: untouched
       = 740 + 2*(x-740)           740 <= x <= 832   east decks + their N-S bridge: 2x wider
       = 924 + (x-832)*100/192     832 <= x <= 1024  abyss wall beyond the decks: compressed
       = x                          x >= 1024        skirt/backdrop outside the map
Nav east edge 819 -> 898 (inside AREA_1's 912, so no playable-area patch); the
bridge between ARMY_3 and ARMY_4 (x 744-771, z 440-590) goes 28 -> 56 cells wide;
both east players' decks roughly double in E-W extent.

Why a warp and not plates: east of x~832 nothing walkable is DRAWN (the abyss
wall mesh drops to y -2300, 0-6 verts per 32-tile) - raised ground there renders
as a hole, and plates over void inherit the void's look (v1-v5 lesson: "invisible
land"). Stretching the decks' OWN dense geometry keeps their real material,
lighting and props.

Warped with f: hfield (heights + the per-cell type grid that follows them),
costs (all 3 layers, islands recomputed), terrain mesh (vertex x, every BVH node
AABB x-bound - exact under a monotone map - and record bbox centres), collision2
(vertex x + its BVH AABBs, then y snapped to the heightfield), mapobjs (prop
positions + the binary marker table), save.lua / scenario.lua markers + spawns,
minimap (DXT1 block-column remap) + regenerated lobby previews.
NOT warped: lighting/mask*.dds and pcloud (UV-space lightmaps indexed by the
per-vertex attrs - they follow the vertices automatically), decals (empty), info.
"""
import zipfile, io, os, re, struct, shutil, array
from collections import deque
import sc2maps as sm

G = 1024
ID_OLD, ID_NEW = "SC2_MP_104", "SC2_BOOLX1"
NAME = "[6] Boolon Complex Extended (3v3) v7"     # v6 = the laptop's retired uniform-slab plate build
OUT = os.path.join(sm.GAMEDATA, "_boolon_ext.scd")
X0, X1, X1N, XE = 740.0, 832.0, 924.0, 1024.0
S = (X1N - X0) / (X1 - X0)                 # 2.0
C = (XE - X1N) / (XE - X1)                 # 100/192

def f(x):
    if x <= X0: return x
    if x <= X1: return X0 + (x - X0) * S
    if x <= XE: return X1N + (x - X1) * C
    return x
def g(x):                                   # inverse of f
    if x <= X0: return x
    if x <= X1N: return X0 + (x - X0) / S
    if x <= XE: return X1 + (x - X1N) / C
    return x
assert abs(f(g(800.0)) - 800.0) < 1e-9 and abs(f(g(1000.0)) - 1000.0) < 1e-9
CELL_SRC = [max(0, min(G - 1, int(g(x + 0.5)))) for x in range(G)]   # dest cell -> src cell

# ---- load stock ----
mz = zipfile.ZipFile(os.path.join(sm.GAMEDATA, "maps.scd"))
stock_files = {n.split("/")[-1]: mz.read(n) for n in mz.namelist()
               if n.startswith(f"maps/{ID_OLD}/") and "/lighting/" not in n}
lighting = {n.split("/")[-1]: mz.read(n) for n in mz.namelist()
            if n.startswith(f"maps/{ID_OLD}/lighting/")}
mz.close()
uz = zipfile.ZipFile(os.path.join(sm.GAMEDATA, "uncompiled_lua.scd"))
ulua_files = {n.split("/")[-1]: uz.read(n) for n in uz.namelist()
              if n.startswith(f"uncompiled/maps/{ID_OLD}/")}
uz.close()
print(f"stock: {len(stock_files)} map files, {len(lighting)} lighting, {len(ulua_files)} lua", flush=True)

t = sm.Terrain(ID_OLD)
layers = t.layers
nav0 = t.costs_payload
n_layers = len(layers)
assert n_layers == 3
deck0 = bytearray(1 if nav0[layers[0][2] + i] != 255 else 0 for i in range(G*G))

# ---- hfield: heights (1025 grid, bilinear in x) + per-cell type grid (nearest) ----
hf_raw = stock_files[f"{ID_OLD}.hfield.win.bdf"]
hp = bytearray(sm.read_bdf_payload(hf_raw))
_, w, h, _, hd = struct.unpack_from("<5I", hp, 0)
assert w == G + 1 and h == G + 1
H0 = struct.unpack_from(f"<{w*h}H", hp, hd)
Hn = list(H0)
xs_i = []
for x in range(w):
    sx = g(float(x)); x0 = int(sx); fx = sx - x0
    xs_i.append((x0, min(w - 1, x0 + 1), fx))
for z in range(h):
    row = z * w
    for x in range(int(X0) + 1, w):
        x0, x1, fx = xs_i[x]
        Hn[row + x] = int(round(H0[row + x0] * (1 - fx) + H0[row + x1] * fx))
hp[hd:hd + w*h*2] = array.array("H", Hn).tobytes()
toff = hd + w*h*2 + 2                         # u16 header, then G*G bytes (2 = deck, 3 = void)
T0 = bytes(hp[toff:toff + G*G])
assert len(T0) == G*G and set(T0) <= {0, 2, 3}, "unexpected hfield type grid"
# orientation sanity: type==2 must coincide with walkable cells row-major
agree = sum(1 for i in range(0, G*G, 7) if (T0[i] == 2) == bool(deck0[i]))
agree_t = sum(1 for i in range(0, G*G, 7) if (T0[i] == 2) == bool(deck0[(i % G)*G + i // G]))
assert agree > agree_t, "type grid orientation"
for z in range(G):
    row = z * G
    src = T0[row:row + G]
    hp[toff + row + int(X0) + 1: toff + row + G] = bytes(src[CELL_SRC[x]] for x in range(int(X0) + 1, G))
hf_new = sm.rebuild_bdf(hf_raw, bytes(hp))
print(f"hfield: heights + type grid warped (type/deck agreement {agree/((G*G)//7+1):.3f})", flush=True)

# ---- costs: warp every layer's cost grid, recompute islands ----
costs_raw = stock_files[f"{ID_OLD}.costs.win.bdf"]
pay = bytearray(sm.read_bdf_payload(costs_raw))
for li in range(n_layers):
    base = layers[li][2]
    src_all = bytes(pay[base:base + G*G])
    for z in range(G):
        row = z * G
        src = src_all[row:row + G]
        pay[base + row + int(X0) + 1: base + row + G] = bytes(src[CELL_SRC[x]] for x in range(int(X0) + 1, G))
sm._recompute_islands(pay, layers)
costs_new = sm.rebuild_bdf(costs_raw, pay)
deck = bytearray(1 if pay[layers[0][2] + i] != 255 else 0 for i in range(G*G))
print("nav: 3 layers warped + islands recomputed", flush=True)

# ---- terrain mesh: vertex x, BVH AABB x-bounds, record bbox centres ----
terr_raw = stock_files[f"{ID_OLD}.terrain.win.bdf"]
p_orig, b, nv, ni = sm.locate_mesh_blob(terr_raw)
tp = bytearray(p_orig)
vb = b + 20
moved = 0
for i in range(nv):
    o = vb + 32*i
    x = struct.unpack_from("<f", tp, o)[0]
    if x > X0:
        struct.pack_into("<f", tp, o, f(x)); moved += 1
nrec = struct.unpack_from("<I", tp, 4)[0]
strtab = struct.unpack_from("<I", tp, 16)[0]
offs = sorted(struct.unpack_from("<I", tp, 20 + rr*108 + 32)[0] for rr in range(nrec))
blocks = [(offs[i], offs[i+1] if i + 1 < len(offs) else strtab) for i in range(len(offs))]
def warp_bvh(buf, blocks):
    n = 0
    for s0, s1 in blocks:
        for o in range(s0, s1 - 55, 56):
            bb = list(struct.unpack_from("<6f", buf, o))
            if (bb[0] <= bb[3] and bb[1] <= bb[4] and bb[2] <= bb[5]
                    and all(-9000 < v < 9000 for v in bb) and bb[3] > X0):
                bb[0] = f(bb[0]); bb[3] = f(bb[3])
                struct.pack_into("<6f", buf, o, *bb); n += 1
    return n
bvh_n = warp_bvh(tp, blocks)
for rr in range(nrec):
    o = 20 + rr*108 + 96
    cx = struct.unpack_from("<f", tp, o)[0]
    if cx == cx and -9000 < cx < 9000 and cx > X0:
        struct.pack_into("<f", tp, o, f(cx))
terr_new = sm.rebuild_bdf(terr_raw, bytes(tp))
print(f"mesh: {moved}/{nv} verts warped, {bvh_n} BVH nodes re-bounded", flush=True)
# gate: verts that tracked the old ground must track the new ground at their new x
# Gate: the ground under EVERY in-map vertex must be the same height after the
# warp (old heightfield at old x == new heightfield at new x) - the vertex's own
# y is untouched, so any difference is resampling error. Stock verts sit up to
# ~2 units off the heightfield (lips, scaffolds), which is why the gate compares
# ground-to-ground rather than vertex-to-ground.
worst = 0.0; worst_at = None; checked = 0
worst_void = 0.0; worst_void_at = None
for i in range(nv):
    ox, oy, oz = struct.unpack_from("<3f", p_orig, vb + 32*i)
    if 0 <= ox < G and 0 <= oz < G:
        checked += 1
        nx = struct.unpack_from("<f", tp, vb + 32*i)[0]
        d = abs(sm.hf_sample(H0, w, ox, oz) - sm.hf_sample(Hn, w, nx, oz))
        xi, zi = int(nx), int(oz)
        if 0 <= xi < G and deck[zi*G + xi]:
            if d > worst: worst, worst_at = d, (round(ox), round(oz), round(nx))
        elif d > worst_void: worst_void, worst_void_at = d, (round(ox), round(oz), round(nx))
# Cliff faces inside the COMPRESSED abyss band (x>832) lose heightfield resolution
# (a 2-cell-wide drop becomes ~1 cell; the mesh keeps it, the grid blurs it) -
# void, nothing walks or builds there. The stretch band doubles resolution, so on
# walkable ground the ground height under each vertex must be preserved.
print(f"mesh gate: {checked} in-map verts; worst ground-height change on WALKABLE ground {worst:.2f} at {worst_at}; "
      f"on void/cliff {worst_void:.2f} at {worst_void_at} (info)", flush=True)
assert worst < 0.75, "warped mesh no longer matches warped heightfield on walkable ground"

# ---- collision2: vertex x warp + y snap, BVH AABBs, record centre ----
col_raw = stock_files[f"{ID_OLD}.collision2.win.bdf"]
cp = bytearray(sm.read_bdf_payload(col_raw))
cver, cnv, cvoff, cni, cioff, cxoff = struct.unpack_from("<6I", cp, 0)
cw = snapped = 0
for i in range(cnv):
    o = cvoff + 12*i
    x, y, z = struct.unpack_from("<3f", cp, o)
    if x > X0: x = f(x); cw += 1
    if 0 <= x < G and 0 <= z < G:
        gy = sm.hf_sample(Hn, w, x, z) - 0.3
        if abs(y - gy) > 0.05: y = gy; snapped += 1
    struct.pack_into("<3f", cp, o, x, y, z)
cbvh = warp_bvh(cp, [(cxoff + 108, len(cp))])
ccx = struct.unpack_from("<f", cp, cxoff + 96)[0]
if ccx == ccx and -9000 < ccx < 9000 and ccx > X0:
    struct.pack_into("<f", cp, cxoff + 96, f(ccx))
col_new = sm.rebuild_bdf(col_raw, bytes(cp))
print(f"collision: {cw} verts warped, {snapped}/{cnv} snapped, {cbvh} BVH nodes re-bounded", flush=True)

# ---- lua: markers + spawns ----
sav = ulua_files[f"{ID_OLD}_save.lua"].decode("utf-8", "replace")
uscen = ulua_files[f"{ID_OLD}_scenario.lua"].decode("utf-8", "replace")
uscript = ulua_files[f"{ID_OLD}_script.lua"].decode("utf-8", "replace")
VEC = r"VECTOR3\(\s*([-\d.eE+]+)\s*,\s*([-\d.eE+]+)\s*,\s*([-\d.eE+]+)\s*\)"
marker_pos = [(float(a), float(b_), float(c_)) for a, b_, c_ in re.findall(VEC, sav)]
lua_moved = 0
def rep_vec(m):
    global lua_moved
    x, y, z = float(m.group(1)), float(m.group(2)), float(m.group(3))
    if x <= X0: return m.group(0)
    lua_moved += 1
    return f"VECTOR3( {f(x):.6f}, {y:.6f}, {z:.6f} )"
sav = re.sub(VEC, rep_vec, sav)
def rep_sp(m):
    x = float(m.group(2))
    if x <= X0: return m.group(0)
    return f"{m.group(1)}{{ {f(x):.6f}, {m.group(3)}, {m.group(4)} }}"
SPAWNS0 = {}
for m in re.finditer(r"\['ARMY_(\d)'\]\s*=\s*\{\s*([-\d.]+)\s*,\s*([-\d.]+)\s*,\s*([-\d.]+)\s*\}", uscen):
    SPAWNS0[int(m.group(1))] = (int(float(m.group(2))), int(float(m.group(4))))
uscen = re.sub(r"(\['ARMY_\d'\]\s*=\s*)\{\s*([-\d.]+)\s*,\s*([-\d.]+)\s*,\s*([-\d.]+)\s*\}", rep_sp, uscen)
uscen = uscen.replace("<LOC SC2_MAPNAME_0020>[6] Boolon Complex (3v3)", NAME)
SPAWNS = {}
for m in re.finditer(r"\['ARMY_(\d)'\]\s*=\s*\{\s*([-\d.]+)\s*,\s*([-\d.]+)\s*,\s*([-\d.]+)\s*\}", uscen):
    SPAWNS[int(m.group(1))] = (int(float(m.group(2))), int(float(m.group(4))))
assert len(SPAWNS) == 6
print(f"lua: {lua_moved} marker positions warped; spawns {SPAWNS}", flush=True)

# ---- mapobjs: prop positions + the binary marker table (exact-triple search) ----
mo_raw = stock_files[f"{ID_OLD}.mapobjs.win.bdf"]
mp = bytearray(sm.read_bdf_payload(mo_raw))
_ver, pcount, poff = struct.unpack_from("<3I", mp, 0)
props_moved = 0
for k in range(pcount):
    o = poff + k*44 + 16
    x = struct.unpack_from("<f", mp, o)[0]
    if x > X0:
        struct.pack_into("<f", mp, o, f(x)); props_moved += 1
mk_moved = 0
for x, y, z in marker_pos:
    if x <= X0: continue
    key = struct.pack("<3f", x, y, z)
    i = mp.find(key)
    while i >= 0:
        if i % 4 == 0 and i >= poff + pcount*44:      # marker table lives after the props
            struct.pack_into("<f", mp, i, f(x)); mk_moved += 1
        i = mp.find(key, i + 4)
mo_new = sm.rebuild_bdf(mo_raw, bytes(mp))
east_markers = sum(1 for x, _, _ in marker_pos if x > X0)
print(f"mapobjs: {props_moved} props warped, {mk_moved}/{east_markers} east markers warped in the binary table", flush=True)
assert mk_moved >= east_markers - 2, "binary marker table not found for east markers"

# ---- minimap: decode DXT1 -> resample columns through g (linear) -> re-encode ----
# (block-column duplication was tried first: it stretches 4-px blocks as 'abcd
# abcd' and streaks the stretched region - and the lobby previews inherit it)
mm_raw = stock_files[f"{ID_OLD}.minimap.win.dds"]
mh, mw = struct.unpack_from("<II", mm_raw, 12)
assert mw == G and mh == G
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
                pal = [p0, p1, tuple((2*a+b_)//3 for a, b_ in zip(p0, p1)),
                       tuple((a+2*b_)//3 for a, b_ in zip(p0, p1))]
            else:
                pal = [p0, p1, tuple((a+b_)//2 for a, b_ in zip(p0, p1)), (0, 0, 0)]
            for py in range(4):
                for pxi in range(4):
                    r, g_, b_ = pal[(bits >> (2*(py*4+pxi))) & 3]
                    o2 = ((bz*4+py) * tw + bx*4+pxi) * 3
                    px[o2:o2+3] = bytes((r, g_, b_))
    return px
mm_src = dxt1_decode(mm_raw, mw, mh)
mm_rgb = bytearray(mm_src)
cmap = []
for x in range(mw):
    sx = max(0.0, min(mw - 1.0, g(x + 0.5) - 0.5)); x0 = int(sx)
    cmap.append((x0, min(mw - 1, x0 + 1), sx - x0))
for z in range(mh):
    ro = z * mw * 3
    for x in range(int(X0), mw):
        x0, x1, fx = cmap[x]
        o0 = ro + x0*3; o1 = ro + x1*3; oo = ro + x*3
        mm_rgb[oo]   = int(mm_src[o0]   * (1-fx) + mm_src[o1]   * fx + 0.5)
        mm_rgb[oo+1] = int(mm_src[o0+1] * (1-fx) + mm_src[o1+1] * fx + 0.5)
        mm_rgb[oo+2] = int(mm_src[o0+2] * (1-fx) + mm_src[o1+2] * fx + 0.5)
def rgb565(c):
    return (((c >> 11) & 31) * 255 // 31, ((c >> 5) & 63) * 255 // 63, (c & 31) * 255 // 31)
def dxt1_encode(px, tw, th, ref, keep_below_x):
    """Simple DXT1 encoder (luminance-extreme endpoints, nearest palette index);
    blocks entirely west of keep_below_x are copied verbatim from `ref`."""
    out = bytearray(ref[:128]); bwid = tw // 4
    for bz in range(th // 4):
        for bx in range(bwid):
            if bx*4 + 3 < keep_below_x:
                o = 128 + (bz*bwid + bx) * 8; out += ref[o:o+8]; continue
            cols = []
            for py in range(4):
                for pxi in range(4):
                    o = ((bz*4+py) * tw + bx*4+pxi) * 3
                    cols.append((px[o], px[o+1], px[o+2]))
            lum = [0.3*r + 0.59*g_ + 0.11*b_ for r, g_, b_ in cols]
            hi = max(range(16), key=lambda k: lum[k]); lo = min(range(16), key=lambda k: lum[k])
            c0 = sm._pack565(*cols[hi]); c1 = sm._pack565(*cols[lo])
            if c0 == c1:
                out += struct.pack("<HHI", c0, c1, 0); continue
            if c0 < c1: c0, c1 = c1, c0
            p0, p1 = rgb565(c0), rgb565(c1)
            pal = [p0, p1, tuple((2*a+b_)//3 for a, b_ in zip(p0, p1)), tuple((a+2*b_)//3 for a, b_ in zip(p0, p1))]
            bits = 0
            for k, c in enumerate(cols):
                best = min(range(4), key=lambda j: (c[0]-pal[j][0])**2 + (c[1]-pal[j][1])**2 + (c[2]-pal[j][2])**2)
                bits |= best << (2*k)
            out += struct.pack("<HHI", c0, c1, bits)
    return bytes(out)
mmd = bytearray(dxt1_encode(mm_rgb, mw, mh, mm_raw, int(X0)))
assert len(mmd) == len(mm_raw)
print("minimap: columns resampled through the warp and re-encoded", flush=True)
def make_preview(stock_img):
    ih = struct.unpack_from("<I", stock_img, 12)[0]
    iw = struct.unpack_from("<I", stock_img, 16)[0]
    out_img = bytearray(stock_img[:128]) + bytearray(iw * ih * 4)
    side = ih
    x_off = (iw - side) // 2
    for py in range(ih):
        for pxi in range(iw):
            o2 = 128 + (py * iw + pxi) * 4
            if x_off <= pxi < x_off + side:
                sx = (pxi - x_off) * mw // side
                sz = py * mh // side
                so = (sz * mw + sx) * 3
                r, g_, b_ = mm_rgb[so], mm_rgb[so+1], mm_rgb[so+2]
            else:
                r = g_ = b_ = 12
            out_img[o2:o2+4] = bytes((b_, g_, r, 255))
    return bytes(out_img)
prev_a = make_preview(stock_files[f"{ID_OLD}_PC_mapshot.dds"])
prev_b = make_preview(stock_files[f"{ID_OLD}_ui_mapimage.dds"])
print("preview images regenerated from the warped minimap", flush=True)

# ---- verification: geometry of the change + erosion routes ----
def runs_at(m, z):
    out = []; s = None
    for x in range(G):
        if m[z*G + x] and s is None: s = x
        if not m[z*G + x] and s is not None: out.append((s, x - 1)); s = None
    return out
r0 = [r for r in runs_at(deck0, 500) if r[0] >= 700]
r1 = [r for r in runs_at(deck, 500) if r[0] >= 700]
print(f"bridge at z=500: stock runs {r0} -> new runs {r1}", flush=True)
bw0 = max(b_ - a + 1 for a, b_ in r0); bw1 = max(b_ - a + 1 for a, b_ in r1)
assert bw1 >= 2 * bw0 - 2, "bridge did not double"
east0 = max(i % G for i in range(G*G) if deck0[i]); east1 = max(i % G for i in range(G*G) if deck[i])
print(f"walkable east edge: {east0} -> {east1}; walkable cells {sum(deck0)} -> {sum(deck)}", flush=True)
assert east1 >= 890 and east1 < 912
def erode(m, r):
    out = bytearray(m)
    for z in range(G):
        b0 = z*G; row = m[b0:b0+G]
        for x in range(G):
            if row[x] and 0 in row[max(0, x-r):min(G, x+r+1)]: out[b0+x] = 0
    out2 = bytearray(out)
    for x in range(G):
        col = out[x::G]
        for z in range(G):
            if col[z] and 0 in col[max(0, z-r):min(G, z+r+1)]: out2[z*G+x] = 0
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
# Routes: r=3 clearance is REQUIRED everywhere; r=5 is a no-regression check against
# stock (the stock causeways are narrower than 11 cells, so several stock pairs
# already fail r=5 - a warp that only widens the east side cannot fix those).
def routes(m, r, sp):
    er = erode(m, r); res = {}
    s1 = snapc(er, *sp[1]); seen = flood(er, s1)
    for a in range(2, 7):
        t2 = snapc(er, *sp[a]); res[f"spawn1 -> spawn{a}"] = t2 is not None and seen[t2]
    er_e = bytearray(er)                      # ARMY_3 <-> ARMY_4 with everything west of x=700 walled off
    for z in range(G):
        er_e[z*G: z*G + 700] = bytes(700)
    s3 = snapc(er_e, *sp[3]); s4 = snapc(er_e, *sp[4])
    res["spawn3 -> spawn4 via the east bridge only"] = s3 is not None and s4 is not None and flood(er_e, s3)[s4]
    return res, er, seen
allok = True
for r in (3, 5):
    res0, _, _ = routes(deck0, r, SPAWNS0)
    res1, er, seen = routes(deck, r, SPAWNS)
    for k in res0:
        tag = "OK" if res1[k] else ("FAIL" if res0[k] else "fail (stock fails too)")
        print(f"[r={r}] {k}: {tag}", flush=True)
        if r == 3: allok &= res1[k]
        allok &= res1[k] or not res0[k]
    east_pt = snapc(er, 880, 380)
    ok = east_pt is not None and seen[east_pt]
    print(f"[r={r}] spawn1 -> new east ground (880,380): {'OK' if ok else 'FAIL'}", flush=True); allok &= ok
bad = 0
for m in re.finditer(r"\['(Mass \d+[^']*)'\]\s*=\s*\{(.*?)\n\s*\},", sav, re.S):
    pm = re.search(VEC, m.group(2))
    x, z = int(float(pm.group(1))), int(float(pm.group(3)))
    if not deck[z*G + x]: bad += 1; print(f"BAD mass {m.group(1)} at {x},{z}", flush=True)
allok &= bad == 0
print("mass markers on walkable ground verified", flush=True)
assert allok, "verification failed"

# ---- package ----
def idswap(txt): return txt.replace(ID_OLD, ID_NEW)
out = {}
for fn, d in stock_files.items():
    if fn.endswith(".hfield.win.bdf"): d = hf_new
    elif fn.endswith(".terrain.win.bdf"): d = terr_new
    elif fn.endswith(".costs.win.bdf"): d = costs_new
    elif fn.endswith(".collision2.win.bdf"): d = col_new
    elif fn.endswith(".mapobjs.win.bdf"): d = mo_new
    elif fn.endswith(".minimap.win.dds"): d = bytes(mmd)
    elif fn.endswith("_PC_mapshot.dds"): d = prev_a
    elif fn.endswith("_ui_mapimage.dds"): d = prev_b
    out[f"maps/{ID_NEW}/{idswap(fn)}"] = d
for fn, d in lighting.items():
    out[f"maps/{ID_NEW}/lighting/{fn}"] = d
for sub in ("maps", "uncompiled/maps"):
    out[f"{sub}/{ID_NEW}/{ID_NEW}_scenario.lua"] = idswap(uscen).encode("utf-8")
    out[f"{sub}/{ID_NEW}/{ID_NEW}_save.lua"] = idswap(sav).encode("utf-8")
    out[f"{sub}/{ID_NEW}/{ID_NEW}_script.lua"] = idswap(uscript).encode("utf-8")
if os.path.exists(OUT) and not os.path.exists(OUT + ".V4.bak"):   # keep the v4 plate build once
    shutil.copy2(OUT, OUT + ".V4.bak")
buf = io.BytesIO(); zo = zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED)
for n, d in sorted(out.items()): zo.writestr(n, d)
zo.close()
open(OUT, "wb").write(buf.getvalue())
print(f"INSTALLED ({os.path.getsize(OUT):,} B, {len(out)} entries)", flush=True)
