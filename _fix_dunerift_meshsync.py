"""Dune Rift 3v3 + 2v2: map-wide mesh sink-sync + global collision snap, in place.

Port of the v8 Two Bridges fix (user-confirmed "a lot better", 23 Jul) to the two
sibling maps, which inherit the same base terracing divergence: drawn dunes up to
+40 above the real ground on dry land (structures bury inside visual dunes, units
vanish under drawn terrain), and - unlike bridge2, which got a global snap in its
v7 build - stale collision with hundreds of verts above ground (turret fire hits
invisible old hills; diag 23 Jul: 3v3 has 751 verts up to +69 above ground).

Patch-in-place: rewrites ONLY `terrain.win.bdf` (mesh, sink-direction only - float/
draped-sea areas deliberately untouched, same rationale as bridge2 v8) and
`collision2.win.bdf` (global snap to hf-0.3). Heightfield, costs, waterDepth, Lua,
props, minimap are asserted byte-identical - gameplay layout does not change.
NOTE: the 2v2 still awaits its separate mass-pad/ramp port (open next step).
"""
import zipfile, io, os, struct, shutil
import sc2maps as sm

G = 1024; WL = 34.0; WR = int(WL * 128)          # Dune Rift family: SC2_CA_I01, water 34
TARGETS = [("_dune_rift_3v3.scd", ".V3.bak"),
           ("_dune_rift_2v2.scd", ".V2.bak")]

def patch(name, bak):
    path = os.path.join(sm.GAMEDATA, name)
    zf = zipfile.ZipFile(path); names = zf.namelist()
    data = {n: zf.read(n) for n in names}; zf.close()
    def key(suf):
        ks = [n for n in names if n.endswith(suf)]
        assert len(ks) == 1, (name, suf, ks)
        return ks[0]
    H, w = sm.hfield_heights(data[key(".hfield.win.bdf")])
    wet = sum(1 for v in H if v <= WR)
    assert wet > 20000, f"{name}: wet-cell count {wet} looks wrong for WL={WL}"

    # ---- mesh: sink-direction sync on dry cells (bridge2-v8 recipe) ----
    terr = data[key(".terrain.win.bdf")]
    payload, blob_off, nv, ni = sm.locate_mesh_blob(terr)
    pb = bytearray(payload); vstart = blob_off + 20
    sheet = lowered = 0; worst_before = 0.0
    for i in range(nv):
        off = vstart + 32*i
        x, y, z = struct.unpack_from("<3f", pb, off)
        if not (0 <= x < G and 0 <= z < G) or y < 2.0:
            continue                                # off-map / underplane
        if abs(y - WL) < 0.8:
            sheet += 1; continue                    # water sheet
        if H[int(z)*w + int(x)] <= WR:
            continue                                # wet cell: lake bed / sheet shells
        gy = sm.hf_sample(H, w, x, z) - 0.15
        d = y - gy
        if d > worst_before: worst_before = d
        if d > 0.3:
            struct.pack_into("<3f", pb, off, x, gy, z)
            lowered += 1
    # sheet count is informational only: the sheet is sparse geometry, and its lake
    # verts sit over wet cells (skipped above) - the band skip only spares the few
    # shoreline overhang verts. WL itself is validated by the wet-cell assert.
    terr_new = sm.rebuild_bdf(terr, bytes(pb))

    # ---- collision: global snap to hf-0.3 ----
    col = data[key(".collision2.win.bdf")]
    cp = bytearray(sm.read_bdf_payload(col))
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
    col_new = sm.rebuild_bdf(col, bytes(cp))
    print(f"{name}: mesh lowered {lowered} verts (worst above was +{worst_before:.2f}); "
          f"collision snapped {snapped}/{cnv}", flush=True)

    # ---- verify from rebuilt payloads ----
    p2, bo2, nv2, _ = sm.locate_mesh_blob(terr_new)
    assert nv2 == nv
    worst = 0.0
    for i in range(nv):
        x, y, z = struct.unpack_from("<3f", p2, bo2 + 20 + 32*i)
        if not (0 <= x < G and 0 <= z < G) or y < 2.0 or abs(y - WL) < 0.8:
            continue
        if H[int(z)*w + int(x)] <= WR:
            continue
        d = y - sm.hf_sample(H, w, x, z)
        if d > worst: worst = d
    cp2 = sm.read_bdf_payload(col_new)
    worst_c = -99.0
    for i in range(cnv):
        x, y, z = struct.unpack_from("<3f", cp2, cvoff + 12*i)
        if 0 <= x < G and 0 <= z < G:
            d = y - sm.hf_sample(H, w, x, z)
            if d > worst_c: worst_c = d
    print(f"{name}: mesh worst ABOVE ground now {worst:.2f}; "
          f"collision worst {worst_c:.2f}", flush=True)
    assert worst < 0.3 and worst_c < 0.0

    # ---- repackage: ONLY the two entries change ----
    tkey, ckey = key(".terrain.win.bdf"), key(".collision2.win.bdf")
    shutil.copy2(path, path + bak)
    buf = io.BytesIO(); zo = zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED)
    n_same = 0
    for n in names:
        if n == tkey: d = terr_new
        elif n == ckey: d = col_new
        else:
            d = data[n]; n_same += 1
        zo.writestr(n, d)
    zo.close()
    open(path, "wb").write(buf.getvalue())
    print(f"{name}: INSTALLED ({os.path.getsize(path):,} B; "
          f"{n_same} entries byte-identical, backup {bak})", flush=True)

for name, bak in TARGETS:
    patch(name, bak)
