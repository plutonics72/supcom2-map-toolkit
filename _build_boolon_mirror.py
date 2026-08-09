"""Boolon Harbor (SC2_BOOLW1): Boolon's platform layout on REAL WATER (donor Boras).

Part 2 of the Boolon plan: the void becomes sea. Donor SC2_MP_305 (Boras Naval
Test Range) won the survey: 74% of Boolon's deck footprint lies on well-tessellated
donor mesh; WL=237; 5 layers + waterDepth + water sheet.

Recipe:
- hfield: Boolon nav-mask cells ("decks", incl. walkway strips) -> flat 247 (WL+10);
  everything else -> min(donor, 217) (WL-20 navigable sea; donor deeps keep depth).
- mesh: deck cells FLATTEN-snap the donor surface band to the plate (donor relief
  must not poke through); sea cells rigid-translate by local delta (v4 lesson);
  skip the water sheet (|y-237|<1.2) and skirt (y<5).
- collision: global snap to new hfield.
- nav: land pair = decks only; amphib triple = open everywhere (stock semantics;
  sub/ship depth gating comes from waterDepth at runtime).
- waterDepth: decks -> decode-verified dry block; sea -> donor water block sampled
  by nearest depth (stock water alphas are heterogeneous; sampling sidesteps it).
  Path retargeted SC2_MP_305 -> SC2_BOOLW1 (10==10); shipped under both names.
- props: ALL sunk (donor trees/rocks would float over new sea / poke decks).
- luas: Boolon's scenario/save/script under the new id; every marker y -> 247.
- minimap synthetic (sea/deck blocks) + previews regenerated from it.
Gates: land erosion r=3/r=5 all-spawn routes, naval pool connectivity report,
deck-surface mesh gate, collision gate, marker placement.
"""
import zipfile, io, os, re, struct, shutil
from collections import deque, Counter
import sc2maps as sm

G = 1024
DONOR = "SC2_MP_302"
SRC_LAYOUT = "SC2_MP_104"
ID_NEW = "SC2_BOOLW1"
NAME = "[6] Boolon Harbor (3v3) v4"
OUT = os.path.join(sm.GAMEDATA, "_boolon_harbor.scd")
WL = 56.0; WR = int(WL * 128)            # Treallach water level (proven by the Strait build)
DECK_Y = 66.0; SEA_Y = 41.0
SPAWNS = {1: (250, 703), 2: (248, 314), 3: (750, 732), 4: (754, 282), 5: (548, 816), 6: (550, 202)}

# ---- load donor + layout ----
mz = zipfile.ZipFile(os.path.join(sm.GAMEDATA, "maps.scd"))
donor = {n.split("maps/" + DONOR + "/")[-1]: mz.read(n) for n in mz.namelist()
         if n.startswith(f"maps/{DONOR}/")}
mz.close()
uz = zipfile.ZipFile(os.path.join(sm.GAMEDATA, "uncompiled_lua.scd"))
lua104 = {n.split("/")[-1]: uz.read(n).decode("utf-8", "replace") for n in uz.namelist()
          if n.startswith(f"uncompiled/maps/{SRC_LAYOUT}/")}
uz.close()
t104 = sm.Terrain(SRC_LAYOUT)
deck = bytearray(1 if t104.costs_payload[t104.layers[0][2] + i] != 255 else 0 for i in range(G*G))
# dilate 2 cells: widens Boolon's narrow walkways into proper causeways (stock
# strips fail r=5 clearance; +2 keeps the layout while giving units room)
for _d in range(2):
    grown_ = bytearray(deck)
    for z in range(1, G-1):
        b = z*G
        for x in range(1, G-1):
            if not deck[b+x] and (deck[b+x-1] or deck[b+x+1] or deck[b-G+x] or deck[b+G+x]):
                grown_[b+x] = 1
    deck = grown_
td = sm.Terrain(DONOR)
layers = td.layers
assert len(layers) == 5
w = td.HW
H0 = td.H
print(f"donor loaded; deck cells {sum(deck)}", flush=True)

# ---- hfield ----
hf_raw = donor[DONOR + ".hfield.win.bdf"]
hp = bytearray(sm.read_bdf_payload(hf_raw))
_, _, _, _, hd = struct.unpack_from("<5I", hp, 0)
Hn = list(H0)
DECK_R = int(DECK_Y * 128); SEA_R = int(SEA_Y * 128)
for z in range(G):
    for x in range(G):
        i = z*G + x
        r = DECK_R if deck[i] else min(H0[z*w + x], SEA_R)
        if Hn[z*w + x] != r:
            Hn[z*w + x] = r
            struct.pack_into("<H", hp, hd + 2*(z*w + x), r)
hf_new = sm.rebuild_bdf(hf_raw, bytes(hp))
print("hfield: decks flat 247, sea <=217", flush=True)

# ---- mesh ----
terr_raw = donor[DONOR + ".terrain.win.bdf"]
terr_new, mv = sm.resample_mesh_heights(terr_raw, hf_raw, hf_new, tol=6.0, min_r=0.5,
                                        bvh_min_y=0.0, bvh_max_y=360.0)
p_orig, b_orig, nv0, _ = sm.locate_mesh_blob(terr_raw)
p2, b2, nv2, _ = sm.locate_mesh_blob(terr_new)
assert nv2 == nv0
pb = bytearray(p2)
flattened = translated = 0
for i in range(nv2):
    off = b2 + 20 + 32*i
    ox, oy, oz = struct.unpack_from("<3f", p_orig, b_orig + 20 + 32*i)
    if not (0 <= ox < G and 0 <= oz < G): continue
    if oy < 5.0: continue                            # skirt/pit
    if abs(oy - WL) < 1.0: continue                  # water sheet (stretched separately)
    oh = sm.hf_sample(H0, w, ox, oz)
    if oy > oh + 8.0: continue                       # tall structures: leave standing
                                                     # (translating them shears - their
                                                     # footprints span different deltas)
    xi, zi = int(ox), int(oz)
    on_deck = deck[zi*G + xi] or deck[zi*G + min(G-1, xi+1)] or deck[min(G-1, zi+1)*G + xi]
    x, y, z = struct.unpack_from("<3f", pb, off)
    want = (DECK_Y - 0.15) if on_deck else (oy + (sm.hf_sample(Hn, w, ox, oz) - oh))
    if abs(y - want) > 0.05:
        struct.pack_into("<3f", pb, off, x, want, z)
        if on_deck: flattened += 1
        else: translated += 1
stretched = 0
terr_new = sm.rebuild_bdf(terr_new, bytes(pb))
print(f"mesh: {mv} delta-tracked, {flattened} deck-flattened, {translated} sea-translated, {stretched} sheet-stretched", flush=True)
terr_new = sm.retarget_waterdepth_path(terr_new, DONOR, ID_NEW)
print("waterDepth path retargeted", flush=True)

# ---- collision ----
col_raw = donor[DONOR + ".collision2.win.bdf"]
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

# ---- nav ----
pay = bytearray(sm.read_bdf_payload(donor[DONOR + ".costs.win.bdf"]))
# type layers: land pair = closed on donor deep water
wet_ref = None; dry_ref = None
for z in range(0, G, 11):
    for x in range(0, G, 11):
        i = z*G + x
        if (wet_ref is None and H0[z*w+x] < WR - 15*128
                and any(td.costs_payload[layers[li][2] + i] != 255 for li in range(5))):
            wet_ref = i                       # deep water INSIDE the baked nav region
        if dry_ref is None and H0[z*w+x] > WR + 15*128:
            dry_ref = i
land_L = [li for li in range(5) if td.costs_payload[layers[li][2] + wet_ref] == 255]
amph_L = [li for li in range(5) if li not in land_L]
print(f"land layers {land_L}, amphib-class {amph_L}", flush=True)
for i in range(G*G):
    for li in land_L:
        pay[layers[li][2] + i] = 1 if deck[i] else 255
    for li in amph_L:
        pay[layers[li][2] + i] = 1
sm._recompute_islands(pay, layers)
costs_new = sm.rebuild_bdf(donor[DONOR + ".costs.win.bdf"], pay)
print("nav rebuilt + islands", flush=True)

# ---- waterDepth: proven full-mip writer (built Treallach Strait) ----
t_wd = sm.Terrain(DONOR)
t_wd.set_hfield(hf_new)
def is_water(x, z):
    xi = min(G-1, max(0, int(x))); zi = min(G-1, max(0, int(z)))
    return not deck[zi*G + xi]
wd = sm.write_waterdepth_dds_mips(t_wd, WL, is_water=is_water)
wh, ww = struct.unpack_from("<II", wd, 12)
scale = G // ww
def alpha_mean(d, bx, bz):
    off = 128 + (bz * (ww//4) + bx) * 16
    a0, a1 = d[off], d[off+1]
    bits = int.from_bytes(d[off+2:off+8], "little")
    pal = ([a0, a1] + [((7-i)*a0 + i*a1)//7 for i in range(1, 7)]) if a0 > a1 else           ([a0, a1] + [((5-i)*a0 + i*a1)//5 for i in range(1, 5)] + [0, 255])
    return sum(pal[(bits >> (3*i)) & 7] for i in range(16)) // 16
for lbl, px, pz, want_dry in [("spawn1", 250, 703, True), ("spawn4", 754, 282, True),
                              ("center", 512, 470, True), ("open sea", 60, 60, False)]:
    am = alpha_mean(wd, (px//scale)//4, (pz//scale)//4)
    print(f"  wd {lbl}: alpha={am}", flush=True)
    assert (am < 40) == want_dry, lbl
print("waterDepth rebuilt", flush=True)

# ---- props: sink all ----
def sink_all(k, x, y, z, path):
    return (x, -150.0, z)
mo_new, sunk = sm.edit_props(donor[DONOR + ".mapobjs.win.bdf"], sink_all)
print(f"props sunk: {sunk}", flush=True)

# ---- luas: Boolon layout, all marker y -> 247 ----
def fix_y(txt):
    return re.sub(r"VECTOR3\(\s*([\d.eE+-]+)\s*,\s*[\d.eE+-]+\s*,\s*([\d.eE+-]+)\s*\)",
                  lambda m: f"VECTOR3( {m.group(1)}, {DECK_Y:.6f}, {m.group(2)} )", txt)
scen = lua104[f"{SRC_LAYOUT}_scenario.lua"]
scen = scen.replace("<LOC SC2_MAPNAME_0020>[6] Boolon Complex (3v3)", NAME)
scen = fix_y(scen).replace(SRC_LAYOUT, ID_NEW)
sav = fix_y(lua104[f"{SRC_LAYOUT}_save.lua"]).replace(SRC_LAYOUT, ID_NEW)
script = lua104[f"{SRC_LAYOUT}_script.lua"].replace(SRC_LAYOUT, ID_NEW)

# ---- minimap synthetic + previews ----
mmd = bytearray(donor[DONOR + ".minimap.win.dds"])
mh, mw = struct.unpack_from("<II", mmd, 12)
cpp = G // mw; cpb = 4 * cpp
def grab_block(px, pz):
    return bytes(mmd[128 + ((pz//cpp)//4 * (mw//4) + (px//cpp)//4) * 8:][:8])
sea_px = dry_px = None
for z in range(0, G, 13):
    for x in range(0, G, 13):
        if H0[z*w+x] < WR - 20*128 and sea_px is None: sea_px = (x, z)
        if H0[z*w+x] > WR + 20*128 and dry_px is None: dry_px = (x, z)
sea_block = grab_block(*sea_px); deck_block = grab_block(*dry_px)
for bz in range(mh // 4):
    for bx in range(mw // 4):
        cx0, cz0 = bx * cpb, bz * cpb
        n_deck = sum(1 for cz in range(cz0, min(G, cz0+cpb))
                     for cx in range(cx0, min(G, cx0+cpb)) if deck[cz*G + cx])
        off = 128 + (bz * (mw//4) + bx) * 8
        mmd[off:off+8] = deck_block if n_deck >= (cpb*cpb)//2 else sea_block
def dxt1_decode(d, tw, th):
    px = bytearray(tw * th * 3)
    for bz in range(th // 4):
        for bx in range(tw // 4):
            off = 128 + (bz * (tw//4) + bx) * 8
            c0, c1 = struct.unpack_from("<HH", d, off)
            bits = struct.unpack_from("<I", d, off+4)[0]
            def rgb(c): return (((c>>11)&31)*255//31, ((c>>5)&63)*255//63, (c&31)*255//31)
            p0, p1 = rgb(c0), rgb(c1)
            pal = [p0, p1,
                   tuple((2*a+b)//3 for a, b in zip(p0, p1)) if c0 > c1 else tuple((a+b)//2 for a, b in zip(p0, p1)),
                   tuple((a+2*b)//3 for a, b in zip(p0, p1)) if c0 > c1 else (0, 0, 0)]
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
    side = ih; x_off = (iw - side) // 2
    for py in range(ih):
        for pxi in range(iw):
            o2 = 128 + (py * iw + pxi) * 4
            if x_off <= pxi < x_off + side:
                sx = (pxi - x_off) * mw // side; sz = py * mh // side
                so = (sz * mw + sx) * 3
                r, g, b = mm_rgb[so], mm_rgb[so+1], mm_rgb[so+2]
            else:
                r = g = b = 12
            out_img[o2:o2+4] = bytes((b, g, r, 255))
    return bytes(out_img)
shot_new = make_preview(donor[DONOR + "_PC_mapshot.dds"])
ui_new = make_preview(donor[DONOR + "_ui_mapimage.dds"])
print("minimap + previews built", flush=True)

# ---- verification ----
m0 = bytearray(1 if pay[layers[land_L[0]][2] + i] != 255 else 0 for i in range(G*G))
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
    s1 = snapc(er, *SPAWNS[1]); seen = flood(er, s1)
    for a in range(2, 7):
        t2 = snapc(er, *SPAWNS[a])
        ok = t2 is not None and seen[t2]
        if r == 3: allok &= ok
        print(f"[r={r}] spawn1 -> spawn{a}: {'OK' if ok else 'FAIL'}{'' if r == 3 else ' (advisory)'}", flush=True)
# naval pools: amphib layer over WATER cells only
mn = bytearray(1 if (pay[layers[amph_L[0]][2] + i] != 255 and not deck[i]
                     and Hn[(i//G)*w + (i % G)] <= WR) else 0 for i in range(G*G))
ern = erode(mn, 3)
total = sum(ern)
sN = snapc(ern, 512, 60)
if sN:
    pool = sum(flood(ern, sN))
    print(f"naval: largest pool from N = {pool*100//max(1,total)}% of eroded sea", flush=True)
# mesh gate on deck cells
p3, b3, nv3, _ = sm.locate_mesh_blob(terr_new)
worst = -99.0; worst_at = None
for i in range(nv3):
    x, y, z = struct.unpack_from("<3f", p3, b3 + 20 + 32*i)
    if 0 <= x < G and 0 <= z < G and m0[int(z)*G + int(x)]:
        ox, oy, oz = struct.unpack_from("<3f", p_orig, b_orig + 20 + 32*i)
        if oy > sm.hf_sample(H0, w, ox, oz) + 8.0: continue   # tall scenery (translated, still tall)
        d = y - DECK_Y
        if d > worst: worst, worst_at = d, (round(x), round(z), round(y, 1))
print(f"deck mesh gate: worst above plate {worst:.2f} at {worst_at}", flush=True)
allok &= worst < 4.0
assert allok, "verification failed"

# ---- package ----
out = {}
ren = {
    DONOR + ".hfield.win.bdf": hf_new, DONOR + ".terrain.win.bdf": terr_new,
    DONOR + ".costs.win.bdf": costs_new, DONOR + ".collision2.win.bdf": col_new,
    DONOR + ".mapobjs.win.bdf": mo_new, DONOR + ".minimap.win.dds": bytes(mmd),
    DONOR + ".waterDepth.dds": bytes(wd),
    DONOR + "_PC_mapshot.dds": shot_new, DONOR + "_ui_mapimage.dds": ui_new,
}
for fn, d in donor.items():
    d = ren.get(fn, d)
    out[f"maps/{ID_NEW}/{fn.replace(DONOR, ID_NEW)}"] = d
out[f"maps/{ID_NEW}/{ID_NEW}.waterDepth.win.dds"] = bytes(wd)
out[f"maps/{ID_NEW}/{ID_NEW}_scenario.lua"] = scen.encode("utf-8")
out[f"maps/{ID_NEW}/{ID_NEW}_save.lua"] = sav.encode("utf-8")
out[f"maps/{ID_NEW}/{ID_NEW}_script.lua"] = script.encode("utf-8")
out[f"uncompiled/maps/{ID_NEW}/{ID_NEW}_scenario.lua"] = scen.encode("utf-8")
out[f"uncompiled/maps/{ID_NEW}/{ID_NEW}_save.lua"] = sav.encode("utf-8")
out[f"uncompiled/maps/{ID_NEW}/{ID_NEW}_script.lua"] = script.encode("utf-8")
buf = io.BytesIO(); zo = zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED)
for n, d in sorted(out.items()): zo.writestr(n, d)
zo.close()
open(OUT, "wb").write(buf.getvalue())
print(f"INSTALLED ({os.path.getsize(OUT):,} B, {len(out)} entries)", flush=True)
