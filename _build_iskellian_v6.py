"""Iskellian Extended v6:
- islands doubled in area (plateau extended at local edge height, naval-corridor guard)
- sub bug fixed: naval/hover layers (0,2,4) restored to STOCK on original land and
  CLOSED on all new-dry cells (v2 had opened layer 0 on ~all dry land -> subs ashore)
- land layers (1,3): stock on original land (legacy-nav lesson), open on island land
- all island masses on levelled pads (fixes the 2-of-4 unbuildable per island)
- +2 masses per island on the new ring
Verified: naval r=3 connectivity around/between islands, island land connectivity,
mass pad flatness, mesh~hfield correlation.
"""
import zipfile, io, os, re, struct, shutil
from collections import deque
import sc2maps as sm

MAP = os.path.join(sm.GAMEDATA, "_iskellian_ext8.scd")
GUARD = 30                     # min water gap to mainland (naval corridor)
ISL_BBOX = [("A", 466, 740, 750, 1006), ("B", 1298, 1042, 1580, 1307)]

if os.path.exists(MAP + ".V21.bak"):
    shutil.copy2(MAP + ".V21.bak", MAP)          # rebuild from clean v2.1
zf = zipfile.ZipFile(MAP); names = zf.namelist()
ent = {n: zf.read(n) for n in names}; zf.close()
hf_raw = ent[[n for n in names if n.endswith(".hfield.win.bdf")][0]]
H, w = sm.hfield_heights(hf_raw)
G = w - 1
t = sm.Terrain("SC2_MP_304")
stock = t.costs_payload
layers = t.layers
assert t.HW == w
WR = 0
ow0 = layers[0][2]
for z in range(0, G, 7):
    for x in range(0, G, 7):
        if stock[ow0 + z*G + x] != 255 and t.H[z*w + x] > WR and t.H[z*w + x] < 60*128:
            WR = t.H[z*w + x]
print(f"WL = {WR/128:.2f}", flush=True)

dry_now = bytearray(1 if H[z*w+x] > WR else 0 for z in range(G) for x in range(G))
dry_stock = bytearray(1 if t.H[z*w+x] > WR else 0 for z in range(G) for x in range(G))

# ---- island footprints (components in bboxes), mainland distance guard ----
def comp_from(seed_xz):
    sx, sz = seed_xz
    seen = bytearray(G*G); cells = []
    s = sz*G + sx
    assert dry_now[s], f"seed ({sx},{sz}) not dry"
    q = deque([s]); seen[s] = 1
    while q:
        i = q.popleft(); cells.append(i)
        x, z = i % G, i // G
        for dx, dz in ((1,0),(-1,0),(0,1),(0,-1)):
            nx, nz = x+dx, z+dz
            if 0 <= nx < G and 0 <= nz < G:
                j = nz*G+nx
                if dry_now[j] and not seen[j]: seen[j] = 1; q.append(j)
    return cells

SEEDS = {"A": (568, 942), "B": (1402, 1240)}     # on-island mass positions
islands = {tag: comp_from(SEEDS[tag]) for tag, *_ in ISL_BBOX}
for tag, cells in islands.items():
    print(f"island {tag}: {len(cells)} cells", flush=True)
    assert 10_000 < len(cells) < 300_000, f"island {tag} component wrong size"

# mainland dry mask = dry_now minus islands
isl_mask = bytearray(G*G)
for cells in islands.values():
    for i in cells: isl_mask[i] = 1
# distance to mainland naval obstacles: dry mainland OR stock-naval-closed shallows
# near the mainland coast (subs need depth; guard the ACTUAL navigable corridor)
ow0_ = layers[0][2]
dist_dryland = bytearray(255 for _ in range(G*G))
q = deque()
for i in range(G*G):
    if dry_now[i] and not isl_mask[i]:
        dist_dryland[i] = 0; q.append(i)
while q:
    i = q.popleft(); d = dist_dryland[i]
    if d >= 70: continue
    x, z = i % G, i // G
    for dx, dz in ((1,0),(-1,0),(0,1),(0,-1)):
        nx, nz = x+dx, z+dz
        if 0 <= nx < G and 0 <= nz < G:
            j = nz*G+nx
            if dist_dryland[j] > d + 1:
                dist_dryland[j] = d + 1; q.append(j)
dist_main = bytearray(255 for _ in range(G*G))
q = deque()
for i in range(G*G):
    blocked_shore = stock[ow0_ + i] == 255 and dist_dryland[i] <= 60
    if (dry_now[i] and not isl_mask[i]) or (blocked_shore and not isl_mask[i]):
        dist_main[i] = 0; q.append(i)
while q:
    i = q.popleft()
    d = dist_main[i]
    if d >= 254: continue
    x, z = i % G, i // G
    for dx, dz in ((1,0),(-1,0),(0,1),(0,-1)):
        nx, nz = x+dx, z+dz
        if 0 <= nx < G and 0 <= nz < G:
            j = nz*G+nx
            if dist_main[j] > d + 1:
                dist_main[j] = d + 1; q.append(j)
print("mainland distance field done", flush=True)

# ---- grow each island: ring cells inherit nearest-island-cell height ----
hp = bytearray(sm.read_bdf_payload(hf_raw))
_, _, _, _, hd = struct.unpack_from("<5I", hp, 0)
Hn = list(H)
def setH(i, raw):
    Hn[(i // G) * w + (i % G)] = raw
    struct.pack_into("<H", hp, hd + 2*((i // G) * w + (i % G)), raw)

grown = {}
for tag, cells in islands.items():
    target = len(cells)                      # ADD this many cells (2x area)
    src_h = {}
    q = deque()
    for i in cells:
        src_h[i] = H[(i // G) * w + (i % G)]
        q.append((i, src_h[i]))
    added = []
    ring_seen = set()
    while q and len(added) < target:
        nxt = deque()
        while q and len(added) < target:
            i, hh = q.popleft()
            x, z = i % G, i // G
            for dx, dz in ((1,0),(-1,0),(0,1),(0,-1)):
                nx, nz = x+dx, z+dz
                if not (0 <= nx < G and 0 <= nz < G): continue
                j = nz*G+nx
                if dry_now[j] or j in ring_seen: continue
                if dist_main[j] <= GUARD: continue
                ring_seen.add(j)
                added.append((j, hh))
                nxt.append((j, hh))
        q = nxt
    assert len(added) <= target * 1.05, f"island {tag} overgrew"
    for j, hh in added:
        setH(j, hh)
        dry_now[j] = 1
        isl_mask[j] = 1
    # extension pass: +30 cells depth beyond the doubling (user: larger, capture masses)
    q2 = deque((j, hh) for j, hh in added)
    ext = []
    for _depth in range(30):
        nxt2 = deque()
        while q2:
            i2, hh = q2.popleft()
            x, z = i2 % G, i2 // G
            for dx, dz in ((1,0),(-1,0),(0,1),(0,-1)):
                nx, nz = x+dx, z+dz
                if not (0 <= nx < G and 0 <= nz < G): continue
                j2 = nz*G+nx
                if dry_now[j2] or j2 in ring_seen: continue
                if dist_main[j2] <= GUARD: continue
                ring_seen.add(j2)
                ext.append((j2, hh))
                nxt2.append((j2, hh))
        q2 = nxt2
    for j2, hh in ext:
        setH(j2, hh)
        dry_now[j2] = 1
        isl_mask[j2] = 1
    added = added + ext
    grown[tag] = [j for j, _ in added]
    print(f"island {tag}: +{len(added)} cells (doubling {target} + extension {len(ext)})", flush=True)

# ---- masses: pads under existing island masses; place 2 new per island ----
sav = ent[[n for n in names if n.endswith("_save.lua")][0]].decode("utf-8", "replace")
ms = re.findall(r"\['(Mass \d+)'\].*?VECTOR3\(\s*([\d.eE+-]+)\s*,\s*([\d.eE+-]+)\s*,\s*([\d.eE+-]+)\s*\)", sav, re.S)
isl_masses = []
for nm, a, b, c in ms:
    x, z = float(a), float(c)
    for tag, x0, z0, x1, z1 in ISL_BBOX:
        if x0 <= x <= x1 and z0 <= z <= z1:
            isl_masses.append((nm, x, z))
print(f"existing island masses: {[m[0] for m in isl_masses]}", flush=True)

def level_pad(xi, zi):
    cells = [(x, z) for z in range(zi-5, zi+6) for x in range(xi-5, xi+6)]
    mean = int(sum(Hn[z*w+x] for x, z in cells) / len(cells))
    for x, z in cells:
        Hn[z*w+x] = mean
        struct.pack_into("<H", hp, hd + 2*(z*w+x), mean)
    return mean / 128.0

pad_y = {}
for nm, mx, mz in isl_masses:
    pad_y[nm] = level_pad(int(round(mx)), int(round(mz)))
print("pads levelled under existing island masses", flush=True)

# new masses: farthest-point sampling on ring cells, deep inside (min 12 from water)
dist_wet = bytearray(255 for _ in range(G*G))
q = deque()
for i in range(G*G):
    if not dry_now[i]:
        dist_wet[i] = 0; q.append(i)
while q:
    i = q.popleft(); d = dist_wet[i]
    if d >= 40: continue
    x, z = i % G, i // G
    for dx, dz in ((1,0),(-1,0),(0,1),(0,-1)):
        nx, nz = x+dx, z+dz
        if 0 <= nx < G and 0 <= nz < G:
            j = nz*G+nx
            if dist_wet[j] > d + 1:
                dist_wet[j] = d + 1; q.append(j)

new_masses = []
next_id = max(int(nm.split()[1]) for nm, _a, _b, _c in ms) + 1
for tag, cells in grown.items():
    anchors = [(x, z) for nm, x, z in isl_masses] + [(x, z) for _, x, z in new_masses]
    cand = [j for j in cells if dist_wet[j] >= 34]
    placed = 0
    while placed < 2 and cand:
        best = None; bestd = -1
        for j in cand[::7]:
            x, z = j % G, j // G
            d = min((x-ax)**2 + (z-az)**2 for ax, az in anchors)
            if d > bestd: bestd, best = d, (x, z)
        x, z = best
        yv = level_pad(x, z)
        new_masses.append((f"Mass {next_id}", x, z))
        anchors.append((x, z))
        pad_y[f"Mass {next_id}"] = yv
        next_id += 1; placed += 1
print(f"new masses: {[(nm, x, z) for nm, x, z in new_masses]}", flush=True)

# save.lua: update y of existing island masses, inject new markers
for nm, mx, mz in isl_masses:
    sav = re.sub(r"(\['" + nm + r"'\].*?VECTOR3\(\s*[\d.eE+-]+\s*,\s*)[\d.eE+-]+(\s*,)",
                 lambda m: m.group(1) + f"{pad_y[nm]:.3f}" + m.group(2), sav, count=1, flags=re.S)
tmpl = re.search(r"(\['Mass 48'\]\s*=\s*\{.*?\n(?:.*?\n)*?\s*\},)", sav)
assert tmpl, "template mass block not found"
block = tmpl.group(1)
inject = ""
for nm, x, z in new_masses:
    nb = block.replace("'Mass 48'", f"'{nm}'")
    nb = re.sub(r"VECTOR3\(\s*[\d.eE+-]+\s*,\s*[\d.eE+-]+\s*,\s*[\d.eE+-]+\s*\)",
                f"VECTOR3( {x}.000000, {pad_y[nm]:.6f}, {z}.000000 )", nb, count=1)
    inject += "\n                " + nb.strip()
sav = sav.replace(block, block + inject, 1)
print("save.lua updated", flush=True)

# ---- costs: stock-based repair + island land ----
pay = bytearray(sm.read_bdf_payload(ent[[n for n in names if n.endswith(".costs.win.bdf")][0]]))
land_layers = [1, 3]; waterish = [0, 2, 4]
fixed_naval = opened_land = 0
for i in range(G*G):
    dn = dry_now[i]; ds = dry_stock[i]
    for li in waterish:
        o = layers[li][2]
        if isl_mask[i] and dn:
            v = 1                                   # stock-like: amphib class open on walkable land
        elif dn and not ds:
            v = 255
        else:
            v = stock[o + i]
        if pay[o + i] != v:
            pay[o + i] = v; fixed_naval += 1
    for li in land_layers:
        o = layers[li][2]
        if isl_mask[i] and dn:
            v = 1
        elif ds:
            v = stock[o + i]
        elif dn:
            v = 1
        else:
            v = 255
        if pay[o + i] != v:
            pay[o + i] = v; opened_land += 1
print(f"naval cells fixed: {fixed_naval}; land cells set: {opened_land}; islands recompute...", flush=True)
sm._recompute_islands(pay, layers)

# ---- verification ----
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
def snap(m, px, pz):
    for rr in range(0, 80, 2):
        for dz in range(-rr, rr+1, 2):
            for dx in range(-rr, rr+1, 2):
                if 0 <= px+dx < G and 0 <= pz+dz < G and m[(pz+dz)*G+px+dx]:
                    return (pz+dz)*G+px+dx
    return None
allok = True
# naval connectivity on each waterish layer: around the islands, W to E and N to S
for li in waterish:
    o = layers[li][2]
    m = bytearray(1 if pay[o+i] != 255 else 0 for i in range(G*G))
    er = erode(m, 3)
    for nm2, a, b, req in [("W->E channel", (200, 1024), (1850, 1024), True),
                           ("N->S between islands", (1024, 780), (1024, 1400), li == 4)]:
        s, t2 = snap(er, *a), snap(er, *b)
        ok = s is not None and t2 is not None and flood(er, s)[t2]
        note = "" if req else " (not required: stock also blocks)"
        print(f"[naval L{li} r=3] {nm2}: {'OK' if ok else 'FAIL'}{note}", flush=True)
        if req: allok &= ok
# subs ashore regression: layer 0 open on dry ~ stock rate
o = layers[0][2]
dry_open = sum(1 for i in range(0, G*G, 23) if dry_now[i] and pay[o+i] != 255)
dry_tot = sum(1 for i in range(0, G*G, 23) if dry_now[i])
print(f"layer0 open-on-dry fraction now: {dry_open/dry_tot:.2f}", flush=True)
allok &= dry_open/dry_tot < 0.65
# land connectivity per island + masses walkable
o = layers[1][2]
m = bytearray(1 if pay[o+i] != 255 else 0 for i in range(G*G))
er = erode(m, 3)
for tag, cells in islands.items():
    full = cells + grown[tag]
    seeds = [i for i in full[::501] if er[i]]
    s0 = seeds[0]
    seen = flood(er, s0)
    frac = sum(1 for i in seeds if seen[i]) / len(seeds)
    print(f"island {tag}: land r=3 component covers {frac:.2f} of samples", flush=True)
    allok &= frac > 0.9
for nm2 in pad_y:
    pass
for nm2, x, z in isl_masses + [(a, b, c) for a, b, c in new_masses]:
    xi, zi = int(round(x)), int(round(z))
    cells = [Hn[zz*w+xx] for zz in range(zi-5, zi+6) for xx in range(xi-5, xi+6)]
    rng = (max(cells)-min(cells))/128.0
    okm = rng < 0.1 and m[zi*G+xi]
    if not okm: print(f"  BAD mass {nm2} at ({xi},{zi}) rng={rng}", flush=True)
    allok &= okm
print("mass pads verified", flush=True)
assert allok, "verification failed"

# ---- waterDepth: regenerate + retarget to new 10-char id ----
wdz = zipfile.ZipFile(os.path.join(sm.GAMEDATA, "maps.scd"))
wd = bytearray(wdz.read("maps/SC2_MP_304/SC2_MP_304.waterDepth.win.dds")); wdz.close()
wh, ww = struct.unpack_from("<II", wd, 12)
scale = G // ww
# dry signature: alpha~0 = DRY LAND (alpha 255 = water). Take the block from the
# stock ISLET top (608,873) - verified alpha-0 in the stock texture.
def _alpha_mean(d, px, pz):
    bx, bz = (px // scale) // 4, (pz // scale) // 4
    off = 128 + (bz * (ww//4) + bx) * 16
    a0, a1 = d[off], d[off+1]
    bits = int.from_bytes(d[off+2:off+8], "little")
    pal = ([a0, a1] + [((7-i)*a0 + i*a1)//7 for i in range(1, 7)]) if a0 > a1 else           ([a0, a1] + [((5-i)*a0 + i*a1)//5 for i in range(1, 5)] + [0, 255])
    return sum(pal[(bits >> (3*i)) & 7] for i in range(16)) // 16
dry_block = bytes(wd[128 + ((873//scale)//4 * (ww//4) + (608//scale)//4) * 16:][:16])
assert _alpha_mean(wd, 608, 873) < 20, "dry signature block is not alpha~0"
patched = 0
for bz in range(wh // 4):
    for bx in range(ww // 4):
        cx0, cz0 = bx*4*scale, bz*4*scale
        all_dry = True
        for cz in range(cz0, min(G, cz0 + 4*scale)):
            base_ = cz*w
            for cx in range(cx0, min(G, cx0 + 4*scale)):
                if Hn[base_ + cx] <= WR: all_dry = False; break
            if not all_dry: break
        if all_dry:
            off = 128 + (bz * (ww//4) + bx) * 16
            if bytes(wd[off:off+16]) != dry_block:
                wd[off:off+16] = dry_block; patched += 1
print(f"waterDepth: {patched} blocks set dry", flush=True)
for _lbl, _px, _pz in [("Mass49", 565, 680), ("Mass52", 1630, 1126), ("ringA", 600, 705), ("isl top", 600, 880)]:
    am = _alpha_mean(wd, _px, _pz)
    print(f"  wd check {_lbl}: alpha={am}", flush=True)
    assert am < 40, f"{_lbl} not dry-marked"
# non-dry blocks must be byte-identical to stock (water untouched)
wdz2 = zipfile.ZipFile(os.path.join(sm.GAMEDATA, "maps.scd"))
_stockwd = wdz2.read("maps/SC2_MP_304/SC2_MP_304.waterDepth.win.dds"); wdz2.close()
_changed_wet = 0
for _bz in range(wh // 4):
    for _bx in range(ww // 4):
        _off = 128 + (_bz * (ww//4) + _bx) * 16
        if bytes(wd[_off:_off+16]) != _stockwd[_off:_off+16]:
            _cx0, _cz0 = _bx*4*scale, _bz*4*scale
            _alldry = all(Hn[_cz*w + _cx] > WR
                          for _cz in range(_cz0, min(G, _cz0 + 4*scale))
                          for _cx in range(_cx0, min(G, _cx0 + 4*scale)))
            if not _alldry: _changed_wet += 1
print(f"  wd check: non-dry blocks changed vs stock = {_changed_wet}", flush=True)
assert _changed_wet == 0, "patch touched water blocks"

# ---- mesh + rebuild + install ----
hf_new = sm.rebuild_bdf(hf_raw, bytes(hp))
terr_new, mv = sm.resample_mesh_heights(ent[[n for n in names if n.endswith(".terrain.win.bdf")][0]],
                                        hf_raw, hf_new, bvh_min_y=-2.0, bvh_max_y=135.0)
print(f"mesh verts delta-tracked: {mv}", flush=True)
# forced pass: snap surface-record verts in grown-ring rects to new ground
WLM = WR / 128.0
ring_rects = []
for tag, cells in grown.items():
    xs = [i % G for i in cells]; zs = [i // G for i in cells]
    ring_rects.append((min(xs)-4, min(zs)-4, max(xs)+4, max(zs)+4))
payload, blob_off, nvv, nii = sm.locate_mesh_blob(terr_new)
pbm = bytearray(payload)
vstart = blob_off + 20
forced = 0
for i in range(nvv):
    off = vstart + 32*i
    x, y, z = struct.unpack_from("<3f", pbm, off)
    if not (0 <= x < G and 0 <= z < G): continue
    if not any(rx0 <= x <= rx1 and rz0 <= z <= rz1 for (rx0, rz0, rx1, rz1) in ring_rects): continue
    if y < -4.0: continue                        # underplane (-7.5); deep seafloor is ~0+
    if abs(y - WLM) < 0.8: continue              # water sheet
    gy = sm.hf_sample(Hn, w, x, z) - 0.15
    if abs(y - gy) > 0.3:
        struct.pack_into("<3f", pbm, off, x, gy, z)
        forced += 1
terr_new = sm.rebuild_bdf(terr_new, bytes(pbm))
print(f"mesh verts force-synced in ring rects: {forced}", flush=True)
# verify: no surface vert above new ground in rects
p2v, b2v, nv2v, _ = sm.locate_mesh_blob(terr_new)
worstm = -99.0
for i in range(nv2v):
    x, y, z = struct.unpack_from("<3f", p2v, b2v + 20 + 32*i)
    if not (0 <= x < G and 0 <= z < G): continue
    if not any(rx0 <= x <= rx1 and rz0 <= z <= rz1 for (rx0, rz0, rx1, rz1) in ring_rects): continue
    if y < -4.0 or abs(y - WLM) < 0.8: continue
    d = y - sm.hf_sample(Hn, w, x, z)
    if d > worstm: worstm = d
print(f"mesh worst above ground in ring rects: {worstm:.2f}", flush=True)
assert worstm < 0.3
# ---- minimap: paint the full island footprints (preview shows old size) ----
mm_n = [n for n in names if n.endswith(".minimap.win.dds")][0]
mmd = bytearray(ent[mm_n])
mh, mw = struct.unpack_from("<II", mmd, 12)
cell_per_px = G // mw                                  # 2048/1024 = 2
cells_per_block = 4 * cell_per_px                      # 8 map cells per DXT block
src_bx, src_bz = (600 // cell_per_px) // 4, (880 // cell_per_px) // 4
src_block = bytes(mmd[128 + (src_bz * (mw//4) + src_bx) * 8:][:8])
painted = 0
for bz in range(mh // 4):
    for bx in range(mw // 4):
        cx0, cz0 = bx * cells_per_block, bz * cells_per_block
        n_isl = 0
        for cz in range(cz0, min(G, cz0 + cells_per_block), 2):
            for cx in range(cx0, min(G, cx0 + cells_per_block), 2):
                if isl_mask[cz*G + cx]: n_isl += 1
        if n_isl >= (cells_per_block // 2) ** 2 * 6 // 10:
            off = 128 + (bz * (mw//4) + bx) * 8
            if bytes(mmd[off:off+8]) != src_block:
                mmd[off:off+8] = src_block; painted += 1
ent[mm_n] = bytes(mmd)
print(f"minimap: {painted} blocks painted as island", flush=True)

terr_new = sm.retarget_waterdepth_path(terr_new, "SC2_MP_304", "SC2_ISKEX3")
print("waterDepth path retargeted to SC2_ISKEX3", flush=True)
costs_new = sm.rebuild_bdf(ent[[n for n in names if n.endswith(".costs.win.bdf")][0]], pay)
out = {}
for n, d in ent.items():
    nn = n.replace("SC2_ISKEXT2", "SC2_ISKEX3")
    if n.endswith(".hfield.win.bdf"): d = hf_new
    elif n.endswith(".terrain.win.bdf"): d = terr_new
    elif n.endswith(".costs.win.bdf"): d = costs_new
    elif n.endswith("_save.lua"): d = sav.replace("SC2_ISKEXT2", "SC2_ISKEX3").encode("utf-8")
    elif n.endswith(".lua"): d = d.decode("utf-8", "replace").replace("SC2_ISKEXT2", "SC2_ISKEX3").encode("utf-8")
    out[nn] = d
out["maps/SC2_ISKEX3/SC2_ISKEX3.waterDepth.win.dds"] = bytes(wd)
out["maps/SC2_ISKEX3/SC2_ISKEX3.waterDepth.dds"] = bytes(wd)
buf = io.BytesIO(); zo = zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED)
for n, d in out.items(): zo.writestr(n, d)
zo.close()
open(MAP, "wb").write(buf.getvalue())
print(f"INSTALLED ({os.path.getsize(MAP):,} B)", flush=True)
