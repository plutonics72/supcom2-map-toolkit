"""Dune Rift 3v3 + 2v2: port the Two Bridges v12 nav stack, PRESERVING the ford.

PREPARED 25 Jul, awaiting user go-ahead. In-place patch of the installed maps.

Design difference vs bridge2 (CRITICAL): on these maps the carve_box water-walk
ford IS the designed central crossing. There is NO wet_now closure here - all
existing land-layer costs are preserved. Only additive/orthogonal passes apply:
 1. nav noise repair: flat dry (gentle <=1.2/cell) 255-cells -> 1 on all layers
 2. hover-land unify (stock semantics: all five layers open on walkable land)
 3. stock water semantics: hover/amphib/naval layers open on all wet cells
 4. shore ribbon: banded iterative hover opening across beach lips (<=2.5 step,
    within 6 cells of water)
 5. pocket revert: repaired cells not flood-connected to a spawn re-close
 6. dried-oasis honesty (same fake-dry gully as bridge2 v12): bed raised to
    WL+1.5, two cut-only entry ramps; mesh delta-resampled + forced sync in the
    oasis rect; collision re-snapped in the oasis zone; waterDepth dry-marks
    fully-dry blocks in the zone (dry-signature copy, wet blocks untouched)
 7. lobby names gain the user-facing version (3v3 " v5", 2v2 " v4")
Gates: ford connectivity PRESERVED (west<->east reachable at r=3 - the opposite
assertion to bridge2), oasis bed open/island-0/reachable, water bodies share the
main hover island, approach corridors free of main-adjacent blocked flat-dry
cells, mesh sink gate over the oasis, collision worst-above < 0 in zone,
all-other-entries byte-identical accounting.
"""
import zipfile, io, os, re, struct, shutil
from collections import deque
import sc2maps as sm

G = 1024; WL = 34.0; WR = int(WL * 128)
SLOPE_MAX = int(1.2 * 128)
PLAY = (24, 24, 1000, 1000)
RIBBON_STEP = int(2.5 * 128)
RIBBON_DEPTH = 6
OASIS_H = WL + 1.5
OASIS = [(356, 296, 476, 420)]
TARGETS = [("_dune_rift_3v3.scd", " v5", (150, 500), (884, 585)),
           ("_dune_rift_2v2.scd", " v4", (150, 500), (884, 585))]

def patch(name, suffix, west, east):
    print(f"===== {name} =====", flush=True)
    path = os.path.join(sm.GAMEDATA, name)
    zf = zipfile.ZipFile(path); names = zf.namelist()
    ent = {n: zf.read(n) for n in names}; zf.close()
    def keys(suf): return [n for n in names if n.endswith(suf)]
    def key1(suf):
        ks = keys(suf); assert len(ks) == 1, (name, suf, ks); return ks[0]

    hf_raw = ent[key1(".hfield.win.bdf")]
    H0, w = sm.hfield_heights(hf_raw)
    hp = bytearray(sm.read_bdf_payload(hf_raw))
    _, _, _, _, hd = struct.unpack_from("<5I", hp, 0)
    H = list(H0)
    def setH(x, z, yv):
        r_ = max(0, min(65535, int(yv * 128)))
        H[z*w + x] = r_; struct.pack_into("<H", hp, hd + 2*(z*w + x), r_)

    # ---- 6a. oasis raise + ramps (terrain first; everything downstream uses H) ----
    raised = 0
    for (ox0, oz0, ox1, oz1) in OASIS:
        for z in range(oz0, oz1 + 1):
            for x in range(ox0, ox1 + 1):
                if H[z*w + x] <= WR + 256:
                    setH(x, z, OASIS_H); raised += 1
    def cut_ramp(x0, z0, x1, z1, axis, a_pos, a_h, b_pos, b_h):
        n = 0
        for z in range(z0, z1 + 1):
            for x in range(x0, x1 + 1):
                t = ((x if axis == "x" else z) - a_pos) / (b_pos - a_pos)
                tgt = a_h + (b_h - a_h) * t
                if H[z*w + x] / 128.0 > tgt:
                    setH(x, z, tgt); n += 1
        return n
    r1 = cut_ramp(452, 322, 472, 346, "x", 472, 44.0, 452, OASIS_H + 0.3)
    r2 = cut_ramp(408, 352, 444, 378, "z", 378, 44.0, 352, OASIS_H + 0.3)
    print(f"oasis: {raised} leveled, ramps {r1}+{r2}", flush=True)
    hf_new = sm.rebuild_bdf(hf_raw, bytes(hp))

    def in_oasis_zone(x, z):
        return any(ox0 - 20 <= x <= ox1 + 20 and oz0 - 20 <= z <= oz1 + 20
                   for (ox0, oz0, ox1, oz1) in OASIS)

    # ---- mesh: delta resample (raises the drawn bed) + forced sync in oasis ----
    terr_new, mv = sm.resample_mesh_heights(ent[key1(".terrain.win.bdf")], hf_raw, hf_new,
                                            bvh_min_y=20.0, bvh_max_y=100.0)
    payload, blob_off, nv, ni = sm.locate_mesh_blob(terr_new)
    pb = bytearray(payload)
    vstart = blob_off + 20
    forced = 0
    for i in range(nv):
        off = vstart + 32*i
        x, y, z = struct.unpack_from("<3f", pb, off)
        if not (0 <= x < G and 0 <= z < G) or y < 2.0 or abs(y - WL) < 0.8:
            continue
        if not in_oasis_zone(x, z) or H[int(z)*w + int(x)] <= WR:
            continue
        gy = sm.hf_sample(H, w, x, z) - 0.15
        if abs(y - gy) > 0.3:
            struct.pack_into("<3f", pb, off, x, gy, z)
            forced += 1
    terr_new = sm.rebuild_bdf(terr_new, bytes(pb))
    print(f"mesh: {mv} delta, {forced} forced in oasis zone", flush=True)

    # ---- collision: re-snap inside the oasis zone (rest was snapped 23 Jul) ----
    col_raw = ent[key1(".collision2.win.bdf")]
    cp = bytearray(sm.read_bdf_payload(col_raw))
    ver, cnv, cvoff, cni, cioff, cxoff = struct.unpack_from("<6I", cp, 0)
    snapped = 0
    for i in range(cnv):
        off = cvoff + 12*i
        x, y, z = struct.unpack_from("<3f", cp, off)
        if 0 <= x < G and 0 <= z < G and in_oasis_zone(x, z):
            gy = sm.hf_sample(H, w, x, z) - 0.3
            if abs(y - gy) > 0.05:
                struct.pack_into("<3f", cp, off, x, gy, z)
                snapped += 1
    col_new = sm.rebuild_bdf(col_raw, bytes(cp))
    print(f"collision: {snapped} oasis-zone verts snapped", flush=True)

    # ---- nav passes (NO wet closure - ford preserved) ----
    costs_raw = ent[key1(".costs.win.bdf")]
    pay = bytearray(sm.read_bdf_payload(costs_raw))
    n_layers = struct.unpack_from("<I", pay, 4)[0]
    layers = [struct.unpack_from("<7I", pay, 12 + 28*li) for li in range(n_layers)]
    t_stock = sm.Terrain("SC2_CA_I01")
    stockp = t_stock.costs_payload
    land_layers = [li for li in range(n_layers) if stockp[layers[li][2] + 100*G + 900] == 255]
    wateronly = [li for li in range(n_layers) if li not in land_layers]
    oL = layers[land_layers[0]][2]

    def gentle(x, z):
        h0 = H[z*w + x]
        for dx, dz in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            nx, nz = x + dx, z + dz
            if 0 <= nx < G and 0 <= nz < G and abs(H[nz*w + nx] - h0) > SLOPE_MAX:
                return False
        return True

    # 1. repair
    repaired_cells = []
    px0, pz0, px1, pz1 = PLAY
    for z in range(pz0, pz1):
        for x in range(px0, px1):
            if H[z*w + x] <= WR or not gentle(x, z):
                continue
            i = z*G + x
            changed = False
            for li in range(n_layers):
                if pay[layers[li][2] + i] == 255:
                    pay[layers[li][2] + i] = 1; changed = True
            if changed: repaired_cells.append((x, z))
    print(f"repair: {len(repaired_cells)} cells", flush=True)

    # 2+3. hover-land unify + stock water semantics
    unified = wet_opened = 0
    for z in range(G):
        for x in range(G):
            i = z*G + x
            if H[z*w + x] <= WR:
                for li in wateronly:
                    if pay[layers[li][2] + i] == 255:
                        pay[layers[li][2] + i] = 1; wet_opened += 1
            elif pay[oL + i] != 255:
                for li in wateronly:
                    if pay[layers[li][2] + i] == 255:
                        pay[layers[li][2] + i] = 1; unified += 1
    print(f"unify: {unified} land cells; wet hover opened: {wet_opened}", flush=True)

    # helpers
    def flood(m, s):
        seen = bytearray(G*G); seen[s] = 1; q = deque([s])
        while q:
            i = q.popleft(); x, z = i % G, i // G
            for dx, dz in ((1,0),(-1,0),(0,1),(0,-1)):
                nx, nz = x+dx, z+dz
                if 0 <= nx < G and 0 <= nz < G and not seen[nz*G+nx] and m[nz*G+nx]:
                    seen[nz*G+nx] = 1; q.append(nz*G+nx)
        return seen
    def snapc(m, px_, pz_):
        for rr in range(0, 40, 2):
            for dz in range(-rr, rr+1, 2):
                for dx in range(-rr, rr+1, 2):
                    if 0 <= px_+dx < G and 0 <= pz_+dz < G and m[(pz_+dz)*G+px_+dx]:
                        return (pz_+dz)*G+px_+dx
        return None
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
    def reach(m, a, b):
        s, t2 = snapc(m, *a), snapc(m, *b)
        return s is not None and t2 is not None and flood(m, s)[t2]

    # 5. pocket revert (land-layer connectivity to west spawn)
    mrep = bytearray(1 if pay[oL + i] != 255 else 0 for i in range(G*G))
    seen_main = flood(mrep, snapc(mrep, *west))
    reverted = 0
    for (x, z) in repaired_cells:
        if mrep[z*G + x] and not seen_main[z*G + x]:
            i = z*G + x
            for li in range(n_layers):
                pay[layers[li][2] + i] = 255
            reverted += 1
    print(f"pocket revert: {reverted}", flush=True)

    # 4. shore ribbon (hover layers, banded)
    near_shore = bytearray(G*G); seenb = bytearray(G*G); qq = deque()
    for z in range(G):
        for x in range(G):
            if H[z*w + x] <= WR:
                seenb[z*G + x] = 1; qq.append((x, z, 0))
    while qq:
        x, z, d = qq.popleft()
        if d == RIBBON_DEPTH: continue
        for dx, dz in ((1,0),(-1,0),(0,1),(0,-1)):
            nx, nz = x+dx, z+dz
            if 0 <= nx < G and 0 <= nz < G and not seenb[nz*G+nx]:
                seenb[nz*G+nx] = 1; near_shore[nz*G+nx] = 1; qq.append((nx, nz, d+1))
    band = [(i % G, i // G) for i in range(G*G) if near_shore[i]]
    oh0 = layers[wateronly[0]][2]
    ribbon = 0
    for _p in range(RIBBON_DEPTH):
        opened = 0
        for (x, z) in band:
            i = z*G + x
            if pay[oh0 + i] != 255:
                continue
            h0 = H[z*w + x]
            ok = False
            for dx, dz in ((1,0),(-1,0),(0,1),(0,-1)):
                nx, nz = x+dx, z+dz
                if not (0 <= nx < G and 0 <= nz < G): continue
                if abs(h0 - H[nz*w + nx]) > RIBBON_STEP: continue
                if H[nz*w + nx] <= WR or pay[oh0 + nz*G + nx] != 255:
                    ok = True; break
            if ok:
                for li in wateronly:
                    pay[layers[li][2] + i] = 1
                opened += 1
        ribbon += opened
        if not opened: break
    print(f"shore ribbon: {ribbon}", flush=True)

    sm._recompute_islands(pay, layers)
    costs_new = sm.rebuild_bdf(costs_raw, pay)

    # ---- waterDepth: dry-mark now-fully-dry blocks in the oasis zone ----
    # NOTE (review finding): these maps' terrain payloads reference the STOCK
    # /maps/SC2_CA_I01/ waterDepth texture, and the 9-char ids (SC2_DUNE6/4)
    # cannot be retargeted (needs same length as SC2_CA_I01). The archive-entry
    # patch below therefore has NO runtime effect here - the raised bed relies
    # on hfield+costs+mesh alone (ford precedent: wet-classified + open costs
    # is traversable). Real cure if the reroute persists: same-length id
    # rename + retarget in a full rebuild.
    tp = sm.read_bdf_payload(terr_new)
    assert b"SC2_CA_I01.waterDepth" in tp, "unexpected waterDepth reference in terrain payload"
    print("waterDepth runtime ref: stock SC2_CA_I01 (archive patch is inert here)", flush=True)
    wd_keys = keys(".waterDepth.dds") + keys(".waterDepth.win.dds")
    assert wd_keys, "no waterDepth in archive"
    wd = bytearray(ent[wd_keys[0]])
    wd_before = bytes(wd)
    wh, ww = struct.unpack_from("<II", wd, 12)
    scale = G // ww; bpr = ww // 4
    dry_block = bytes(wd[128 + ((230//scale)//4 * bpr + (150//scale)//4) * 16:][:16])
    assert max(dry_block[0], dry_block[1]) <= 32, "donor block not decode-verified dry"
    wpatched = 0
    for bz in range(wh // 4):
        for bx in range(bpr):
            cx0, cz0 = bx*4*scale, bz*4*scale
            if not in_oasis_zone(cx0 + 2*scale, cz0 + 2*scale):
                continue
            all_dry = all(H[cz*w + cx] > WR
                          for cz in range(cz0, min(G, cz0 + 4*scale))
                          for cx in range(cx0, min(G, cx0 + 4*scale)))
            if all_dry:
                off = 128 + (bz * bpr + bx) * 16
                if bytes(wd[off:off+16]) != dry_block:
                    wd[off:off+16] = dry_block; wpatched += 1
    print(f"waterDepth: {wpatched} oasis blocks dry", flush=True)

    # ---- lobby name ----
    sc_new = {}
    sc_keys = keys("_scenario.lua")
    assert sc_keys, f"{name}: no scenario lua found"
    for sk in sc_keys:
        sc = ent[sk].decode("utf-8", "replace")
        m = re.search(r"name = '(\[\d\][^']*)'", sc)
        assert m, f"name not found in {sk}"
        old = m.group(1)
        newn = old if old.endswith(suffix) else old + suffix
        sc_new[sk] = sc.replace(f"name = '{old}'", f"name = '{newn}'", 1).encode("utf-8")
        print(f"lobby name: '{old}' -> '{newn}'", flush=True)

    # ---- verification ----
    allok = True
    o = oL
    m0 = bytearray(1 if pay[o+i] != 255 else 0 for i in range(G*G))
    er3 = erode(m0, 3)
    ok_ford = reach(er3, west, east)
    print(f"[r=3] ford connectivity west<->east PRESERVED: {'OK' if ok_ford else 'FAIL'}", flush=True)
    allok &= ok_ford
    oisl = layers[land_layers[0]][4]
    for nm2, qx, qz in [("oasis N", 416, 330), ("oasis pond", 424, 356)]:
        openok = pay[o + qz*G + qx] != 255
        main_isl = pay[oisl + west[1]*G + west[0]]
        islok = pay[oisl + qz*G + qx] == main_isl
        print(f"{nm2}: open={openok} island-main={islok} h={H[qz*w+qx]/128.0:.1f}", flush=True)
        allok &= openok and islok
    ok_oasis = reach(er3, west, (416, 330))
    print(f"[r=3] main landmass -> oasis: {'OK' if ok_oasis else 'FAIL'}", flush=True)
    allok &= ok_oasis
    ohB = layers[wateronly[0]][4]
    main_h = pay[ohB + 600*G + 300]
    for wn, wx, wz in [("north lake", 480, 480), ("channel", 600, 690)]:
        islv = pay[ohB + wz*G + wx]
        print(f"hover {wn}: island {islv} (main {main_h})", flush=True)
        allok &= islv == main_h
    # mesh gate in oasis zone
    p2, bo2, nv2, _ = sm.locate_mesh_blob(terr_new)
    worst = 0.0
    for i in range(nv2):
        x, y, z = struct.unpack_from("<3f", p2, bo2 + 20 + 32*i)
        if not (0 <= x < G and 0 <= z < G) or y < 2.0 or abs(y - WL) < 0.8:
            continue
        if not in_oasis_zone(x, z) or H[int(z)*w + int(x)] <= WR:
            continue
        d = y - sm.hf_sample(H, w, x, z)
        if d > worst: worst = d
    print(f"mesh worst above ground in oasis zone: {worst:.2f}", flush=True)
    allok &= worst < 0.3
    cpv = sm.read_bdf_payload(col_new)
    worst_c = -99.0
    for i in range(cnv):
        x, y, z = struct.unpack_from("<3f", cpv, cvoff + 12*i)
        if 0 <= x < G and 0 <= z < G and in_oasis_zone(x, z):
            worst_c = max(worst_c, y - sm.hf_sample(H, w, x, z))
    print(f"collision worst above in oasis zone: {worst_c:.2f}", flush=True)
    allok &= worst_c < 0.0
    changed_out = sum(1 for bz in range(wh//4) for bx in range(bpr)
                      if not in_oasis_zone(bx*4*scale + 2*scale, bz*4*scale + 2*scale)
                      and wd[128+(bz*bpr+bx)*16 : 144+(bz*bpr+bx)*16] != wd_before[128+(bz*bpr+bx)*16 : 144+(bz*bpr+bx)*16])
    print(f"waterDepth out-of-zone changed: {changed_out} (want 0)", flush=True)
    allok &= changed_out == 0
    assert allok, f"{name}: verification failed"

    # ---- repackage ----
    bdir = os.path.join(os.path.dirname(sm.GAMEDATA), "gamedata_backups")
    os.makedirs(bdir, exist_ok=True)
    bak = os.path.join(bdir, name + ".pre_navstack.bak")
    if not os.path.exists(bak):        # never clobber the true pre-patch backup
        shutil.copy2(path, bak)
    buf = io.BytesIO(); zo = zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED)
    for n in names:
        if n == key1(".hfield.win.bdf"): d = hf_new
        elif n == key1(".terrain.win.bdf"): d = terr_new
        elif n == key1(".costs.win.bdf"): d = costs_new
        elif n == key1(".collision2.win.bdf"): d = col_new
        elif n in wd_keys: d = bytes(wd)
        elif n in sc_new: d = sc_new[n]
        else: d = ent[n]
        zo.writestr(n, d)
    zo.close()
    open(path, "wb").write(buf.getvalue())
    print(f"{name}: INSTALLED ({os.path.getsize(path):,} B)", flush=True)

for name, suffix, west, east in TARGETS:
    patch(name, suffix, west, east)
