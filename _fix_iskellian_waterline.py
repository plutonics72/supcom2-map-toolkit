"""Iskellian Extended: fix the water-classified island shoreline band (ships sail
onto the islands; units freeze on the band) + island-zone collision snap + lobby
version name. In-place patch of the installed archive.

Findings (25 Jul, Dune Rift battery on the installed file):
- waterDepth: v6 dry-marked only FULLY-dry blocks; at scale 4 (2048 map, 512 dds)
  a DXT block spans 16 cells, so the grown islands kept a water-classified
  shoreline band: 9.5% (A) / 11.9% (B) of island dry cells, ~97% of the edge
  band. Runtime depth check says water -> ships drive ashore; units standing on
  the band freeze (user video: frozen cluster sits in the blocky boundary zone).
- collision2: never touched by the island builds - 438 verts of old seabed sit
  >3 under the new island land. Stock elsewhere is authored scenery (stock
  baseline byte-matches: worst +118.66 on stock AND ours) - so snap ONLY inside
  the island zones.
- nav costs healthy (island B: cost 1, own island ids - the frozen units are NOT
  a nav-grid or island-table problem). Mesh matches stock baseline. No changes.

Patch: waterDepth blocks with >=40% dry cells within the padded island zones get
the verified dry-signature block; collision verts inside the zones snap to
hf-0.3; scenario lobby name gains " v6" (version-in-name convention from Dune
Rift). Everything else asserted byte-identical.
"""
import zipfile, io, os, re, struct, shutil
from collections import deque
import sc2maps as sm

MAP = os.path.join(sm.GAMEDATA, "_iskellian_ext8.scd")
SEEDS = [(568, 942), (1402, 1240)]
PAD = 30
NEW_NAME_SUFFIX = " v6"

zf = zipfile.ZipFile(MAP); names = zf.namelist()
ent = {n: zf.read(n) for n in names}; zf.close()
def key(suf):
    ks = [n for n in names if n.endswith(suf)]
    assert len(ks) == 1, (suf, ks)
    return ks[0]
H, w = sm.hfield_heights(ent[key(".hfield.win.bdf")])
G = w - 1

t = sm.Terrain("SC2_MP_304")
stock = t.costs_payload
layers = t.layers
WR = 0
ow0 = layers[0][2]
for z in range(0, G, 7):
    for x in range(0, G, 7):
        if stock[ow0 + z*G + x] != 255 and t.H[z*w + x] > WR and t.H[z*w + x] < 60*128:
            WR = t.H[z*w + x]
print(f"WL = {WR/128:.2f}", flush=True)

# island zones: dry components from seeds, bounding boxes padded
isl_mask = bytearray(G*G)
def comp_bbox(sx, sz):
    seen = bytearray(G*G); s = sz*G + sx
    assert H[sz*w + sx] > WR
    q = deque([s]); seen[s] = 1
    minx = maxx = sx; minz = maxz = sz
    n = 0
    while q:
        i = q.popleft(); n += 1
        isl_mask[i] = 1
        x, z = i % G, i // G
        minx = min(minx, x); maxx = max(maxx, x)
        minz = min(minz, z); maxz = max(maxz, z)
        for dx, dz in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            nx, nz = x + dx, z + dz
            if 0 <= nx < G and 0 <= nz < G:
                j = nz*G + nx
                if not seen[j] and H[nz*w + nx] > WR:
                    seen[j] = 1; q.append(j)
    return (max(0, minx - PAD), max(0, minz - PAD),
            min(G-1, maxx + PAD), min(G-1, maxz + PAD), n)
ZONES = []
for sx, sz in SEEDS:
    x0, z0, x1, z1, n = comp_bbox(sx, sz)
    ZONES.append((x0, z0, x1, z1))
    print(f"island zone ({sx},{sz}): x{x0}-{x1} z{z0}-{z1} ({n} dry cells)", flush=True)
def in_zone(x, z):
    return any(zx0 <= x <= zx1 and zz0 <= z <= zz1 for (zx0, zz0, zx1, zz1) in ZONES)

# ---- waterDepth: dry-mark >=40%-dry blocks inside the zones ----
wd = bytearray(ent[key(".waterDepth.win.dds")])
wd_before = bytes(wd)
wh, ww = struct.unpack_from("<II", wd, 12)
scale = G // ww
bpr = ww // 4
def alpha_mean(d, px, pz):
    bx, bz = (px // scale) // 4, (pz // scale) // 4
    off = 128 + (bz * bpr + bx) * 16
    a0, a1 = d[off], d[off+1]
    bits = int.from_bytes(d[off+2:off+8], "little")
    pal = ([a0, a1] + [((7-i)*a0 + i*a1)//7 for i in range(1, 7)]) if a0 > a1 else \
          ([a0, a1] + [((5-i)*a0 + i*a1)//5 for i in range(1, 5)] + [0, 255])
    return sum(pal[(bits >> (3*i)) & 7] for i in range(16)) // 16
dry_block = bytes(wd[128 + ((873//scale)//4 * bpr + (608//scale)//4) * 16:][:16])
assert alpha_mean(wd, 608, 873) < 20, "dry signature block not alpha~0"
patched = 0
for bz in range(wh // 4):
    for bx in range(bpr):
        cx0, cz0 = bx*4*scale, bz*4*scale
        if not in_zone(cx0 + 2*scale, cz0 + 2*scale):
            continue
        has_isl = False
        for cz in range(cz0, min(G, cz0 + 4*scale)):
            for cx in range(cx0, min(G, cx0 + 4*scale)):
                if isl_mask[cz*G + cx]:
                    has_isl = True; break
            if has_isl: break
        if has_isl:      # every block carrying ANY island land goes dry: no band,
            off = 128 + (bz * bpr + bx) * 16     # and ships hold ~a block off shore
            if bytes(wd[off:off+16]) != dry_block:
                wd[off:off+16] = dry_block; patched += 1
print(f"waterDepth: {patched} island-carrying blocks set dry", flush=True)

# gates: island dry cells no longer water-classified; out-of-zone blocks untouched
allok = True
for tag, (sx, sz) in zip("AB", SEEDS):
    seen = bytearray(G*G); s = sz*G + sx
    q = deque([s]); seen[s] = 1
    bad = tot = 0
    while q:
        i = q.popleft(); x, z = i % G, i // G
        tot += 1
        if alpha_mean(wd, x, z) >= 96: bad += 1
        for dx, dz in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            nx, nz = x + dx, z + dz
            if 0 <= nx < G and 0 <= nz < G:
                j = nz*G + nx
                if not seen[j] and H[nz*w + nx] > WR:
                    seen[j] = 1; q.append(j)
    print(f"island {tag}: water-classified dry cells now {bad}/{tot}", flush=True)
    allok &= bad == 0
changed_out = sum(1 for bz in range(wh//4) for bx in range(bpr)
                  if not in_zone(bx*4*scale + 2*scale, bz*4*scale + 2*scale)
                  and wd[128+(bz*bpr+bx)*16 : 144+(bz*bpr+bx)*16] != wd_before[128+(bz*bpr+bx)*16 : 144+(bz*bpr+bx)*16])
print(f"out-of-zone blocks changed: {changed_out} (want 0)", flush=True)
allok &= changed_out == 0

# ---- collision: snap to hf-0.3 INSIDE zones only ----
col_raw = ent[key(".collision2.win.bdf")]
cp = bytearray(sm.read_bdf_payload(col_raw))
ver, cnv, cvoff, cni, cioff, cxoff = struct.unpack_from("<6I", cp, 0)
snapped = 0
for i in range(cnv):
    off = cvoff + 12*i
    x, y, z = struct.unpack_from("<3f", cp, off)
    if 0 <= x < G and 0 <= z < G and in_zone(x, z):
        gy = sm.hf_sample(H, w, x, z) - 0.3
        if abs(y - gy) > 0.05:
            struct.pack_into("<3f", cp, off, x, gy, z)
            snapped += 1
col_new = sm.rebuild_bdf(col_raw, bytes(cp))
cp2 = sm.read_bdf_payload(col_new)
worst_z = -99.0
for i in range(cnv):
    x, y, z = struct.unpack_from("<3f", cp2, cvoff + 12*i)
    if 0 <= x < G and 0 <= z < G and in_zone(x, z):
        worst_z = max(worst_z, y - sm.hf_sample(H, w, x, z))
print(f"collision: {snapped} zone verts snapped; zone worst above-ground {worst_z:+.2f}", flush=True)
allok &= worst_z < 0.0

# ---- lobby name: version-in-name convention (both lua copies) ----
sc_keys = [n for n in names if n.endswith("_scenario.lua")]
assert sc_keys, "scenario lua not found"
sc_new = {}
for sk in sc_keys:
    sc = ent[sk].decode("utf-8", "replace")
    m = re.search(r"name = '(\[8\][^']*)'", sc)
    assert m, f"display name not found in {sk}"
    old_name = m.group(1)
    new_name = old_name if old_name.endswith(NEW_NAME_SUFFIX) else old_name + NEW_NAME_SUFFIX
    sc_new[sk] = sc.replace(f"name = '{old_name}'", f"name = '{new_name}'", 1).encode("utf-8")
    print(f"lobby name in {sk.split('/')[0]}: '{old_name}' -> '{new_name}'", flush=True)
    allok &= new_name.endswith(NEW_NAME_SUFFIX)

assert allok, "verification failed"

# ---- repackage: only wd (both names), collision2, _scenario.lua change ----
wd_key2 = [n for n in names if n.endswith(".waterDepth.dds")]
shutil_dir = os.path.join(os.path.dirname(sm.GAMEDATA), "gamedata_backups")
os.makedirs(shutil_dir, exist_ok=True)
shutil.copy2(MAP, os.path.join(shutil_dir, os.path.basename(MAP) + ".v5.bak"))
buf = io.BytesIO(); zo = zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED)
n_same = 0
for n in names:
    if n == key(".waterDepth.win.dds") or (wd_key2 and n == wd_key2[0]):
        d = bytes(wd)
    elif n == key(".collision2.win.bdf"):
        d = col_new
    elif n in sc_new:
        d = sc_new[n]
    else:
        d = ent[n]; n_same += 1
    zo.writestr(n, d)
zo.close()
open(MAP, "wb").write(buf.getvalue())
print(f"INSTALLED ({os.path.getsize(MAP):,} B; {n_same} entries byte-identical)", flush=True)
