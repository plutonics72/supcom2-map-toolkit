"""Dune Rift nav overhaul:
BASE _dune_rift_3v3.scd: mass pads (54) + wide basin ramps (nav opened on ramps only).
BRIDGE2 _dune_rift_bridge2.scd (rebuilt from fixed base): bridges A+B with widened
B-east merge, land-layer walkability RE-DERIVED from actual terrain (dry+gentle),
water-only layers blocked on decks, erosion-verified (r=3) connectivity for all
routes, waterDepth regenerated + retargeted (fixes foam ghosts), props cleared."""
import sys, os, io, re, struct, zipfile, shutil
from collections import deque
sys.path.insert(0, r"C:\Users\Chris\Documents\supcom2-map-toolkit")
os.chdir(r"C:\Users\Chris\Documents\supcom2-map-toolkit")
import sc2maps as sm

WL = 34.0; WR = int(WL * 128)
G = 1024
BRIDGE_A = (430, 560, 692, 612)
BRIDGE_B = (438, 762, 680, 815)          # east end extended 604 -> 680, band widened
RAMPS = [  # (x0, z0, x1, z1): graded cut, height lerped along x between edge means
    (318, 430, 408, 500),                # west plateau -> basin (wide, near spawn 2)
    (330, 250, 390, 310),                # west plateau -> NE basin shore (near spawn 1)
    (630, 470, 730, 545),                # east highland -> basin (near spawns 4/5)
]
BASE = os.path.join(sm.GAMEDATA, "_dune_rift_3v3.scd")

zf = zipfile.ZipFile(BASE); names = zf.namelist()
entries = {n: zf.read(n) for n in names}; zf.close()
def E(suffix): return [n for n in names if n.endswith(suffix)][0]
hf0_raw = entries[E(".hfield.win.bdf")]
H0, w = sm.hfield_heights(hf0_raw)

t_stock = sm.Terrain("SC2_CA_I01")
layers = t_stock.layers
stock = t_stock.costs_payload
wet_i = 100*G + 900          # (x=900, z=100) NE sea, inside baked pocket
dry_i = 230*G + 150
land_layers = [li for li, r_ in enumerate(layers) if stock[r_[2] + wet_i] == 255]
wateronly = [li for li, r_ in enumerate(layers)
             if stock[r_[2] + wet_i] != 255 and stock[r_[2] + dry_i] == 255]
print(f"land layers={land_layers}, water-only={wateronly}", flush=True)
assert land_layers and wateronly, "layer typing failed - samples outside baked pocket"

sav0 = entries[E("_save.lua")].decode("utf-8", "replace")
mass = {}
for nm, a, b, c in re.findall(r"\['(Mass \d+)'\].*?VECTOR3\(\s*([\d.eE+-]+)\s*,\s*([\d.eE+-]+)\s*,\s*([\d.eE+-]+)\s*\)", sav0, re.S):
    mass.setdefault(nm, (float(a), float(c)))

def apply_pads_and_ramps(H_list, hp, hd):
    def setH(x, z, yv):
        r_ = max(0, min(65535, int(yv * 128)))
        H_list[z*w + x] = r_; struct.pack_into("<H", hp, hd + 2*(z*w + x), r_)
    ramp_cells = []
    for (x0, z0, x1, z1) in RAMPS:
        vW = [H_list[z*w + x] for z in range(z0, z1) for x in range(x0-8, x0) if H_list[z*w + x] > WR + 128]
        vE = [H_list[z*w + x] for z in range(z0, z1) for x in range(x1, x1+8) if H_list[z*w + x] > WR + 128]
        assert vW and vE, f"ramp ({x0},{z0}) endpoint has no dry bank"
        hW = sum(vW) / len(vW) / 128.0
        hE = sum(vE) / len(vE) / 128.0
        for x in range(x0, x1+1):
            t = (x - x0) / (x1 - x0)
            dv = max(WL + 3.5, hW * (1 - t) + hE * t)
            for z in range(z0, z1+1):
                setH(x, z, dv)
                ramp_cells.append((x, z))
        print(f"  ramp x[{x0},{x1}] z[{z0},{z1}]: {hW:.1f} -> {hE:.1f}", flush=True)
    for nm, (mx, mz) in mass.items():
        xi, zi = int(round(mx)), int(round(mz))
        cells = [(x, z) for z in range(zi-5, zi+6) for x in range(xi-5, xi+6)]
        mean = max(WL + 2, sum(H_list[z*w + x] for x, z in cells) / len(cells) / 128.0)
        for x, z in cells:
            setH(x, z, mean)
    print(f"  pads levelled under {len(mass)} masses", flush=True)
    return ramp_cells

def update_mass_y(sav, H_list):
    for nm, (mx, mz) in mass.items():
        yv = H_list[int(round(mz))*w + int(round(mx))] / 128.0
        sav = re.sub(r"(\['" + nm + r"'\].*?VECTOR3\(\s*[\d.eE+-]+\s*,\s*)[\d.eE+-]+(\s*,)",
                     lambda m: m.group(1) + f"{yv:.3f}" + m.group(2), sav, count=1, flags=re.S)
    return sav

def repack(ent_dict, path):
    buf = io.BytesIO(); zo = zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED)
    for n, d in ent_dict.items(): zo.writestr(n, d)
    zo.close(); open(path, "wb").write(buf.getvalue())

# ================= BASE MAP: pads + ramps, legacy nav + ramp cells opened ========
print("=== BASE _dune_rift_3v3 ===", flush=True)
bak = BASE.replace(".scd", "_PRE_NAVFIX.scd.bak")
if os.path.exists(bak):
    shutil.copy2(bak, BASE)                      # rebuild from clean pre-navfix state
    zf = zipfile.ZipFile(BASE); entries = {n: zf.read(n) for n in zf.namelist()}; zf.close()
    hf0_raw = entries[E(".hfield.win.bdf")]
    H0, w = sm.hfield_heights(hf0_raw)
else:
    shutil.copy2(BASE, bak)
hp = bytearray(sm.read_bdf_payload(hf0_raw))
_, _, _, _, hd = struct.unpack_from("<5I", hp, 0)
Hb = list(H0)
ramp_cells = apply_pads_and_ramps(Hb, hp, hd)
hf_base_new = sm.rebuild_bdf(hf0_raw, bytes(hp))
pay = bytearray(sm.read_bdf_payload(entries[E(".costs.win.bdf")]))
opened = 0
for (x, z) in ramp_cells:
    i = z*G + x
    for li in land_layers:
        if pay[layers[li][2] + i] == 255:
            pay[layers[li][2] + i] = 1; opened += 1
print(f"  nav: {opened} ramp cells opened; islands recompute...", flush=True)
sm._recompute_islands(pay, layers)
costs_base_new = sm.rebuild_bdf(entries[E(".costs.win.bdf")], pay)
terr_base_new, mv = sm.resample_mesh_heights(entries[E(".terrain.win.bdf")], hf0_raw, hf_base_new,
                                             bvh_min_y=20.0, bvh_max_y=100.0)
print(f"  mesh: {mv} verts", flush=True)
be = dict(entries)
be[E(".hfield.win.bdf")] = hf_base_new
be[E(".costs.win.bdf")] = costs_base_new
be[E(".terrain.win.bdf")] = terr_base_new
for n in list(be):
    if n.endswith("_save.lua"): be[n] = update_mass_y(be[n].decode("utf-8", "replace"), Hb).encode("utf-8")
repack(be, BASE)
print(f"  BASE installed ({os.path.getsize(BASE):,} B)", flush=True)

# ================= BRIDGE2: from fixed base + bridges + rederived nav ============
print("=== BRIDGE2 rebuild ===", flush=True)
hp2 = bytearray(sm.read_bdf_payload(hf_base_new))
H2 = list(Hb)
def setH2(x, z, yv):
    r_ = max(0, min(65535, int(yv * 128)))
    H2[z*w + x] = r_; struct.pack_into("<H", hp2, hd + 2*(z*w + x), r_)
CAP_A = (445, 585)                       # deck A lake stretch: cap flat (shave islet intruding into band)
for bi, (x0, z0, x1, z1) in enumerate((BRIDGE_A, BRIDGE_B)):
    vals = [H2[z*w + x] for z in range(z0, z1) for x in range(x0-16, x0-2) if H2[z*w + x] > WR + 128]
    hW = sum(vals) / len(vals) / 128.0
    vals = [H2[z*w + x] for z in range(z0, z1) for x in range(x1+2, x1+16) if H2[z*w + x] > WR + 128]
    hE = sum(vals) / len(vals) / 128.0
    for x in range(x0, x1+1):
        t = (x - x0) / (x1 - x0)
        deck = hW * (1 - t) + hE * t
        cap = bi == 0 and CAP_A[0] <= x <= CAP_A[1]
        for z in range(z0, z1+1):
            if cap or H2[z*w + x] / 128.0 < deck:
                setH2(x, z, deck)
    for xe, dirn, hEnd in ((x0, -1, hW), (x1, +1, hE)):   # graded aprons: deck meets bank at <=1.5/cell
        for d in range(1, 31):
            x = xe + dirn * d
            if not (0 <= x < G): break
            tgt = hEnd - 1.5 * d
            if tgt <= WL + 1: break
            for z in range(z0, z1+1):
                if H2[z*w + x] / 128.0 < tgt:
                    setH2(x, z, tgt)
    print(f"  bridge x[{x0},{x1}] z[{z0},{z1}]: {hW:.1f} -> {hE:.1f} (aprons graded)", flush=True)
hf_b2 = sm.rebuild_bdf(hf0_raw, bytes(hp2))
# --- land walkability re-derived: dry + gentle on CURRENT terrain ---
pay2 = bytearray(sm.read_bdf_payload(costs_base_new))
SL = int(2.2 * 128)
nmask = bytearray(G*G)
for z in range(1, G):
    b25 = z*w
    for x in range(1, G):
        c = H2[b25 + x]
        if c > WR + 100 and abs(H2[b25 + x - 1] - c) < SL and abs(H2[b25 - w + x] - c) < SL:
            nmask[z*G + x] = 1
for li in land_layers:
    o = layers[li][2]
    for i in range(G*G):
        pay2[o + i] = 1 if nmask[i] else 255
for li in wateronly:
    o = layers[li][2]
    for z in range(G):
        for x in range(G):
            i = z*G + x
            if H2[z*w + x] > WR and H0[z*w + x] <= WR:
                pay2[o + i] = 255
print(f"  land mask: {sum(nmask)} walkable cells; islands recompute...", flush=True)
sm._recompute_islands(pay2, layers)
# --- erosion-verified connectivity ---
oL = layers[land_layers[0]][2]
nav2 = bytearray(1 if pay2[oL + i] != 255 else 0 for i in range(G*G))
def erode(m, r_):
    out = bytearray(m)
    for z in range(G):
        b = z*G; row = m[b:b+G]
        for x in range(G):
            if row[x]:
                if 0 in row[max(0, x-r_):min(G, x+r_+1)]: out[b+x] = 0
    out2 = bytearray(out)
    for x in range(G):
        col = out[x::G]
        for z in range(G):
            if col[z]:
                if 0 in col[max(0, z-r_):min(G, z+r_+1)]: out2[z*G+x] = 0
    return out2
def find_open(m, x, z):
    for r_ in range(0, 40, 2):
        for dx in range(-r_, r_+1, 2):
            for dz in range(-r_, r_+1, 2):
                nx, nz = x+dx, z+dz
                if 0 <= nx < G and 0 <= nz < G and m[nz*G + nx]: return (nx, nz)
    return None
def conn(m, a, b):
    a = find_open(m, *a); b = find_open(m, *b)
    if not a or not b: return False
    seen = bytearray(G*G); s = a[1]*G + a[0]; t = b[1]*G + b[0]
    q = deque([s]); seen[s] = 1
    while q:
        i = q.popleft()
        if i == t: return True
        x, z = i % G, i // G
        for dx, dz in ((1,0),(-1,0),(0,1),(0,-1)):
            nx, nz = x+dx, z+dz
            if 0 <= nx < G and 0 <= nz < G:
                j = nz*G + nx
                if not seen[j] and m[j]: seen[j] = 1; q.append(j)
    return False
er = erode(nav2, 3)
def blocked_variant(bridge):
    m = bytearray(er)
    (x0, z0, x1, z1) = bridge
    for z in range(z0-4, z1+5):
        for x in range(x0-4, x1+5):
            if 0 <= x < G and 0 <= z < G: m[z*G + x] = 0
    return m
checks = [
    ("spawn2<->spawn5 (via A, B blocked)", blocked_variant(BRIDGE_B), (150, 500), (884, 585)),
    ("spawn2<->spawn5 (via B, A blocked)", blocked_variant(BRIDGE_A), (150, 500), (884, 585)),
    ("spawn2 -> basin", er, (150, 500), (470, 470)),
    ("spawn5 -> basin", er, (884, 585), (470, 470)),
    ("spawn1 -> basin", er, (150, 230), (470, 300)),
    ("spawn6 -> spawn3 (via bridges)", er, (820, 840), (170, 815)),
]
allok = True
for lbl, m, a, b in checks:
    ok = conn(m, a, b)
    print(f"  [r=3] {lbl}: {'OK' if ok else 'FAIL'}", flush=True)
    allok = allok and ok
assert allok, "eroded connectivity failed - inspect"
costs_b2 = sm.rebuild_bdf(costs_base_new, pay2)
terr_b2, mv2 = sm.resample_mesh_heights(terr_base_new, hf_base_new, hf_b2,
                                        bvh_min_y=20.0, bvh_max_y=100.0)
print(f"  mesh: {mv2} verts", flush=True)
# waterDepth regen (CA_I01 mask is 512^2 no-mips) + retarget path in terrain
t_wd = sm.Terrain("SC2_CA_I01")
t_wd.set_hfield(hf_b2)
wd_new = sm.write_waterdepth_dds(t_wd, WL, t_wd.raw["waterDepth.dds"][:128])
terr_b2 = sm.retarget_waterdepth_path(terr_b2, "SC2_CA_I01", "SC2_DUNEB2")
def sink(k, x, y, z, path):
    for (x0, z0, x1, z1) in (BRIDGE_A, BRIDGE_B) + tuple(RAMPS):
        if x0-2 <= x <= x1+2 and z0-2 <= z <= z1+2: return (x, -100.0, z)
    return None
mo_b2, sunk = sm.edit_props(entries[E(".mapobjs.win.bdf")], sink)
print(f"  props sunk: {sunk}", flush=True)
b2 = {}
for n, d in entries.items():
    nn = n.replace("SC2_DUNE6", "SC2_DUNEB2")
    if n.endswith(".hfield.win.bdf"): d = hf_b2
    elif n.endswith(".terrain.win.bdf"): d = terr_b2
    elif n.endswith(".costs.win.bdf"): d = costs_b2
    elif n.endswith(".mapobjs.win.bdf"): d = mo_b2
    elif n.endswith(".waterDepth.dds"): d = wd_new
    elif n.endswith("_save.lua"):
        d = update_mass_y(d.decode("utf-8", "replace"), H2).replace("SC2_DUNE6", "SC2_DUNEB2").encode("utf-8")
    elif n.endswith(".lua"):
        txt = d.decode("utf-8", "replace").replace("SC2_DUNE6", "SC2_DUNEB2")
        txt = txt.replace("[6] Dune Rift (3v3, FFA)", "[6] Dune Rift - Two Bridges (3v3)")
        d = txt.encode("utf-8")
    b2[nn] = d
dst = os.path.join(sm.GAMEDATA, "_dune_rift_bridge2.scd")
repack(b2, dst)
print(f"  BRIDGE2 installed ({os.path.getsize(dst):,} B)", flush=True)
# retire One Bridge
b1 = os.path.join(sm.GAMEDATA, "_dune_rift_bridge1.scd")
if os.path.exists(b1):
    shutil.move(b1, os.path.join(os.getcwd(), "_dune_rift_bridge1_RETIRED.scd"))
    print("  One Bridge trial retired from gamedata", flush=True)
print("\nALL DONE", flush=True)
