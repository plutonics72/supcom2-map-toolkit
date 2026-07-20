"""Dune Rift - Two Bridges v7: v6 minus hover walls - all five layers open on causeways (hover tanks were blocked; only the land-layer ACU crossed).

Fixes (user report 19 Jul): units disappearing/reappearing mid-map (render mesh vs
hfield mismatch), land turrets misbehaving + bridges uncrossable (stale collision
mesh: huge triangles spanning my earlier 46-vert clamp zones), residual middle nav
issues. Changes:
- WIDER causeways built as exact lerp PLANES (cap+fill: crisp rectangular deck =
  man-made look): A z 552-620 (69 wide), B z 754-823 (70 wide)
- graded aprons <=1.2/cell for 40 cells beyond deck ends
- render mesh: delta resample + FORCED vert sync to hfield inside all edit rects
  (skips the water-sheet plane) -> no more units under the drawn surface
- collision2: GLOBAL vert snap to hf-0.3 (faithful coarse terrain; kills every
  buried-ridge wall incl. triangle spans; turret fire consistent with ground)
- nav: legacy overlay from base costs (close water, open decks+aprons, naval walls)
- props: band props relocated to deck edges as railing rows (regular spacing) for
  the constructed-bridge read; leftovers sunk
"""
import zipfile, io, os, re, struct, shutil
from collections import deque
import sc2maps as sm

G = 1024; WL = 34.0; WR = int(WL * 128)
BR_A = (430, 552, 692, 620)
BR_B = (438, 754, 680, 823)
APRON = 40; APRON_SLOPE = 1.2
RAIL_STEP = 16
BASE = os.path.join(sm.GAMEDATA, "_dune_rift_3v3.scd")
OUT = os.path.join(sm.GAMEDATA, "_dune_rift_bridge2.scd")
NAME = "[6] Dune Rift - Two Bridges (3v3)"

zb = zipfile.ZipFile(BASE); bnames = zb.namelist()
base = {n: zb.read(n) for n in bnames}; zb.close()
def bent(suf): return [n for n in bnames if n.endswith(suf)]
hf_raw = base[bent(".hfield.win.bdf")[0]]
Hb, w = sm.hfield_heights(hf_raw)

t_stock = sm.Terrain("SC2_CA_I01")
layers = t_stock.layers
stock = t_stock.costs_payload
land_layers = [li for li, r in enumerate(layers) if stock[r[2] + 100*G + 900] == 255]
wateronly = [li for li, r in enumerate(layers)
             if stock[r[2] + 100*G + 900] != 255 and stock[r[2] + 230*G + 150] == 255]
print(f"land={land_layers} wateronly={wateronly}", flush=True)

# ---- terrain: capped deck planes + aprons ----
hp = bytearray(sm.read_bdf_payload(hf_raw))
_, _, _, _, hd = struct.unpack_from("<5I", hp, 0)
H = list(Hb)
def setH(x, z, yv):
    r_ = max(0, min(65535, int(yv * 128)))
    H[z*w + x] = r_; struct.pack_into("<H", hp, hd + 2*(z*w + x), r_)

deck_plane = {}
for (x0, z0, x1, z1) in (BR_A, BR_B):
    vW = [Hb[z*w + x] for z in range(z0, z1) for x in range(x0-8, x0) if Hb[z*w + x] > WR + 128]
    vE = [Hb[z*w + x] for z in range(z0, z1) for x in range(x1, x1+8) if Hb[z*w + x] > WR + 128]
    assert vW and vE
    hW = sum(vW)/len(vW)/128.0; hE = sum(vE)/len(vE)/128.0
    hW = max(hW, WL + 8.0); hE = max(hE, WL + 8.0)   # engine dislikes near-waterline decks
    for x in range(x0, x1+1):
        t = (x - x0) / (x1 - x0)
        deck = hW * (1 - t) + hE * t
        deck_plane[(x0, x)] = deck
        for z in range(z0, z1+1):
            setH(x, z, deck)                       # cap AND fill: exact plane
    for xe, dirn, hEnd in ((x0, -1, hW), (x1, +1, hE)):
        for d in range(1, APRON+1):
            x = xe + dirn * d
            if not (0 <= x < G): break
            tgt = hEnd - APRON_SLOPE * d
            if tgt <= WL + 3: break
            for z in range(z0, z1+1):
                if H[z*w + x] / 128.0 < tgt:
                    setH(x, z, tgt)
    print(f"deck x[{x0},{x1}] z[{z0},{z1}]: plane {hW:.1f} -> {hE:.1f} (+aprons)", flush=True)
hf_new = sm.rebuild_bdf(hf_raw, bytes(hp))

RECTS = [(BR_A[0]-APRON, BR_A[1], BR_A[2]+APRON, BR_A[3]),
         (BR_B[0]-APRON, BR_B[1], BR_B[2]+APRON, BR_B[3])]
def in_rect(x, z):
    for (rx0, rz0, rx1, rz1) in RECTS:
        if rx0 <= x <= rx1 and rz0 <= z <= rz1: return True
    return False

# ---- render mesh: delta resample + FORCED sync in rects ----
terr_new, mv = sm.resample_mesh_heights(base[bent(".terrain.win.bdf")[0]], hf_raw, hf_new,
                                        bvh_min_y=20.0, bvh_max_y=100.0)
payload, blob_off, nv, ni = sm.locate_mesh_blob(terr_new)
pb = bytearray(payload)
vstart = blob_off + 20
forced = 0
for i in range(nv):
    off = vstart + 32*i
    x, y, z = struct.unpack_from("<3f", pb, off)
    if 0 <= x < G and 0 <= z < G and in_rect(x, z):
        if y < 2.0:                                 # underplane: leave
            continue
        if abs(y - WL) < 0.8:                       # water sheet: leave
            continue
        gy = sm.hf_sample(H, w, x, z) - 0.15        # surface record hugs new ground
        if abs(y - gy) > 0.3:
            struct.pack_into("<3f", pb, off, x, gy, z)
            forced += 1
terr_new = sm.rebuild_bdf(terr_new, bytes(pb))
print(f"mesh: {mv} delta-tracked, {forced} force-synced in rects", flush=True)

# ---- collision: global snap to ground ----
col_raw = base[bent(".collision2.win.bdf")[0]]
cp = bytearray(sm.read_bdf_payload(col_raw))
ver, cnv, cvoff, cni, cioff, cxoff = struct.unpack_from("<6I", cp, 0)
snapped = 0
for i in range(cnv):
    off = cvoff + 12*i
    x, y, z = struct.unpack_from("<3f", cp, off)
    if 0 <= x < G and 0 <= z < G:
        gy = sm.hf_sample(H, w, x, z) - 0.3
        if abs(y - gy) > 0.05:
            struct.pack_into("<3f", cp, off, x, gy, z)
            snapped += 1
col_new = sm.rebuild_bdf(col_raw, bytes(cp))
print(f"collision: {snapped}/{cnv} verts snapped to ground", flush=True)

# ---- nav: legacy overlay ----
pay = bytearray(sm.read_bdf_payload(base[bent(".costs.win.bdf")[0]]))
closed = opened = walled = 0
for z in range(G):
    b25 = z*w; b24 = z*G
    for x in range(G):
        i = b24 + x
        wet_now = H[b25 + x] <= WR
        wet_b = Hb[b25 + x] <= WR
        if wet_now:
            for li in land_layers:
                if pay[layers[li][2] + i] != 255:
                    pay[layers[li][2] + i] = 255; closed += 1
        elif wet_b or in_rect(x, z):
            for li in land_layers:
                pay[layers[li][2] + i] = 1
            if in_rect(x, z):
                for li in wateronly:           # hover/amphib classes cross the causeway too
                    pay[layers[li][2] + i] = 1
                walled += 1
            opened += 1
print(f"nav: closed {closed}, opened {opened}, hover-opened {walled}; islands...", flush=True)
sm._recompute_islands(pay, layers)
costs_new = sm.rebuild_bdf(base[bent(".costs.win.bdf")[0]], pay)

# ---- verification ----
o = layers[land_layers[0]][2]
m0 = bytearray(1 if pay[o+i] != 255 else 0 for i in range(G*G))
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
def reach(m, a, b):
    s, t2 = snapc(m, *a), snapc(m, *b)
    return s is not None and t2 is not None and flood(m, s)[t2]
allok = True
for r in (3, 5):
    er = erode(m0, r)
    for nm2, a, b in [("spawn2->spawn5", (150,500), (884,585)), ("spawn2->basin", (150,500), (470,470)),
                      ("spawn5->basin", (884,585), (470,470)), ("spawn1->basin", (150,180), (470,470)),
                      ("spawn6->spawn3", (884,870), (150,820))]:
        ok = reach(er, a, b); allok &= ok
        print(f"[r={r}] {nm2}: {'OK' if ok else 'FAIL'}", flush=True)
for tag, block in [("A", BR_B), ("B", BR_A)]:
    m3 = bytearray(m0)
    for z in range(block[1]-4, block[3]+5):
        for x in range(block[0]-4-APRON, block[2]+5+APRON):
            if 0 <= x < G: m3[z*G+x] = 0
    ok = reach(erode(m3, 3), (150,500), (884,585)); allok &= ok
    print(f"[r=3] via {tag} only: {'OK' if ok else 'FAIL'}", flush=True)
m2b = bytearray(m0)
for (x0, z0, x1, z1) in (BR_A, BR_B):
    for z in range(z0-4, z1+5):
        for x in range(x0-4-APRON, x1+5+APRON):
            if 0 <= x < G: m2b[z*G+x] = 0
only = not reach(m2b, (150,500), (884,585)); allok &= only
print(f"bridges only land route: {only}", flush=True)
for (x0, z0, x1, z1, tg) in [(430, 552, 692, 620, "A"), (438, 754, 680, 823, "B")]:
    zc = (z0+z1)//2
    for li in range(5):
        o5 = layers[li][2]
        n_closed = sum(1 for x in range(x0, x1+1) if pay[o5 + zc*G + x] == 255)
        print(f"deck {tg} layer {li}: {n_closed} closed cells on centerline", flush=True)
        allok &= n_closed == 0
assert allok, "nav verification failed"

# mesh/hfield sync check inside rects
p2, bo2, nv2, _ = sm.locate_mesh_blob(terr_new)
assert nv2 == nv
v2 = bo2 + 20
worst = 0.0
for i in range(nv):
    x, y, z = struct.unpack_from("<3f", p2, v2 + 32*i)
    if 0 <= x < G and 0 <= z < G and in_rect(x, z) and y >= 2.0 and abs(y - WL) >= 0.8:
        d = y - sm.hf_sample(H, w, x, z)
        if d > worst: worst = d
print(f"mesh worst ABOVE new ground in rects: {worst:.2f}", flush=True)
assert worst < 0.3
# collision check
cp2 = sm.read_bdf_payload(col_new)
worst_c = -99.0
for i in range(cnv):
    x, y, z = struct.unpack_from("<3f", cp2, cvoff + 12*i)
    if 0 <= x < G and 0 <= z < G:
        d = y - sm.hf_sample(H, w, x, z)
        if d > worst_c: worst_c = d
print(f"collision worst above-ground: {worst_c:.2f}", flush=True)
assert worst_c < 0.0

# ---- props: railings from band props ----
mo_raw = base[bent(".mapobjs.win.bdf")[0]]
inventory = []
def collect(k, x, y, z, path):
    inventory.append((k, x, y, z, bytes(path)))
    return None
sm.edit_props(mo_raw, collect)
rails = []
for (x0, z0, x1, z1) in (BR_A, BR_B):
    for x in range(x0 + 12, x1 - 8, RAIL_STEP):
        t = (x - x0) / (x1 - x0)
        for zz in (z0 + 3, z1 - 3):
            rails.append((x, zz))
in_band = [k for (k, x, y, z, p) in inventory
           if any(rx0-6 <= x <= rx1+6 and rz0-6 <= z <= rz1+6 for (rx0, rz0, rx1, rz1) in RECTS)]
def prio(rec):
    k, x, y, z, p = rec
    pl = p.lower()
    score = 0 if b"rock" in pl else (1 if (b"palm" in pl or b"tree" in pl or b"bush" in pl) else 2)
    dmin = min((x - rx)**2 + (z - rz)**2 for rx, rz in rails)
    return (0 if k in in_band else 1, score, dmin)
pool = sorted(inventory, key=prio)
assign = {}
for slot, rec in zip(rails, pool):
    assign[rec[0]] = slot
in_band_set = set(in_band)
def apply(k, x, y, z, path):
    if k in assign:
        rx, rz = assign[k]
        return (float(rx), sm.hf_sample(H, w, rx, rz), float(rz))
    if k in in_band_set:
        return (x, -100.0, z)
    return None
mo_new, moved = sm.edit_props(mo_raw, apply)
print(f"props: {len(rails)} railing slots filled, {moved} records edited", flush=True)

# ---- waterDepth: retarget + regenerate (stock texture marks decks as WATER) ----
wd_zf = zipfile.ZipFile(os.path.join(sm.GAMEDATA, "maps.scd"))
wd = bytearray(wd_zf.read("maps/SC2_CA_I01/SC2_CA_I01.waterDepth.dds")); wd_zf.close()
wh, ww = struct.unpack_from("<II", wd, 12)
scale = G // ww                                     # map cells per texel
dry_block = bytes(wd[128 + ((230//scale)//4 * (ww//4) + (150//scale)//4) * 16:][:16])
patched_blocks = 0
for bz in range(wh // 4):
    for bx in range(ww // 4):
        cx0, cz0 = bx*4*scale, bz*4*scale
        tot = dry = 0
        in_r = in_rect(cx0 + 2*scale, cz0 + 2*scale)
        for cz in range(cz0, min(G, cz0 + 4*scale)):
            for cx in range(cx0, min(G, cx0 + 4*scale)):
                tot += 1
                if H[cz*w + cx] > WR: dry += 1
        mark = (dry == tot) or (in_r and dry >= tot * 4 // 10)
        if mark:
            off = 128 + (bz * (ww//4) + bx) * 16
            if bytes(wd[off:off+16]) != dry_block:
                wd[off:off+16] = dry_block
                patched_blocks += 1
print(f"waterDepth: {patched_blocks} blocks set dry", flush=True)
terr_new = sm.retarget_waterdepth_path(terr_new, "SC2_CA_I01", "SC2_DUNEB2")
print("waterDepth path retargeted to SC2_DUNEB2", flush=True)

# ---- minimap: paint the causeways (preview lacks bridges) ----
mm_key = bent(".minimap.win.dds")[0]
mmd = bytearray(base[mm_key])
mh, mw = struct.unpack_from("<II", mmd, 12)
cpp = G // mw                                          # 1024/1024 = 1
cpb = 4 * cpp                                          # 4 map cells per block
sbx, sbz = (300 // cpp) // 4, (650 // cpp) // 4        # plain sand plateau
sand_block = bytes(mmd[128 + (sbz * (mw//4) + sbx) * 8:][:8])
painted = 0
for (x0, z0, x1, z1) in (BR_A, BR_B):
    for bz in range(z0 // cpb, z1 // cpb + 1):
        for bx in range(x0 // cpb, x1 // cpb + 1):
            if bx*cpb >= x0 and (bx+1)*cpb - 1 <= x1 and bz*cpb >= z0 and (bz+1)*cpb - 1 <= z1:
                off = 128 + (bz * (mw//4) + bx) * 8
                if bytes(mmd[off:off+8]) != sand_block:
                    mmd[off:off+8] = sand_block; painted += 1
base[mm_key] = bytes(mmd)
print(f"minimap: {painted} causeway blocks painted", flush=True)

# ---- repackage under SC2_DUNEB2 ----
sav = base[bent("_save.lua")[0]].decode("utf-8", "replace")
out_entries = {}
for n, d in base.items():
    nn = n.replace("SC2_DUNE6", "SC2_DUNEB2")
    if n.endswith(".hfield.win.bdf"): d = hf_new
    elif n.endswith(".terrain.win.bdf"): d = terr_new
    elif n.endswith(".costs.win.bdf"): d = costs_new
    elif n.endswith(".collision2.win.bdf"): d = col_new
    elif n.endswith(".mapobjs.win.bdf"): d = mo_new
    elif n.endswith(".lua"):
        txt = d.decode("utf-8", "replace").replace("SC2_DUNE6", "SC2_DUNEB2")
        if n.endswith("_scenario.lua"):
            txt = txt.replace("name = '[6] Dune Rift (3v3, FFA)'", f"name = '{NAME}'")
        d = txt.encode("utf-8")
    out_entries[nn] = d
out_entries["maps/SC2_DUNEB2/SC2_DUNEB2.waterDepth.dds"] = bytes(wd)
out_entries["maps/SC2_DUNEB2/SC2_DUNEB2.waterDepth.win.dds"] = bytes(wd)
shutil.copy2(OUT, OUT + ".V4.bak")
buf = io.BytesIO(); zo = zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED)
for n, d in out_entries.items(): zo.writestr(n, d)
zo.close()
open(OUT, "wb").write(buf.getvalue())
print(f"INSTALLED ({os.path.getsize(OUT):,} B)", flush=True)
