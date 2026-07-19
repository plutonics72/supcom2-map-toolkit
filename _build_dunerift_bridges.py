"""Dune Rift bridge variants:
  1: '[6] Dune Rift - One Bridge (3v3)'  (bridge A central)      -> _dune_rift_bridge1.scd
  2: '[6] Dune Rift - Two Bridges (3v3)' (A + B southern flank)  -> _dune_rift_bridge2.scd
Both include: flattened pads under all mass markers (fixes 16 dead spots) and
removal of invisible water-walking (land layers blocked over water; the
bridge decks become the only land routes). Naval/hover layers untouched except
water-only layers blocked on the new dry decks."""
import sys, os, io, re, struct, zipfile
from collections import deque
sys.path.insert(0, r"C:\Users\Chris\Documents\supcom2-map-toolkit")
os.chdir(r"C:\Users\Chris\Documents\supcom2-map-toolkit")
import sc2maps as sm

WL = 34.0; WR = int(WL * 128)
BRIDGE_A = (498, 560, 692, 612)      # x0, z0, x1, z1
BRIDGE_B = (438, 762, 604, 808)
SRC = os.path.join(sm.GAMEDATA, "_dune_rift_3v3.scd")

zf = zipfile.ZipFile(SRC); names = zf.namelist()
base_entries = {n: zf.read(n) for n in names}; zf.close()
def ent(suffix): return [n for n in names if n.endswith(suffix)]
hf_base = base_entries[ent(".hfield.win.bdf")[0]]
H0, w = sm.hfield_heights(hf_base)
G = w - 1

# stock CA_I01 costs for layer typing
t_stock = sm.Terrain("SC2_CA_I01")
stock = t_stock.costs_payload
layers = t_stock.layers
wet_i = 900*G + 900 if H0[900*w + 900] <= WR else 100*G + 900
assert H0[(wet_i//G)*w + (wet_i % G)] <= WR
dry_i = 230*G + 150
land_layers = [li for li, r in enumerate(layers) if stock[r[2] + wet_i] == 255]
wateronly_layers = [li for li, r in enumerate(layers)
                    if stock[r[2] + wet_i] != 255 and stock[r[2] + dry_i] == 255]
print(f"layer typing: land={land_layers}, water-only={wateronly_layers} (of {len(layers)})", flush=True)

# mass markers from base save.lua
sav0 = base_entries[ent("_save.lua")[0]].decode("utf-8", "replace")
mass = {}
for nm, a, b, c in re.findall(r"\['(Mass \d+)'\].*?VECTOR3\(\s*([\d.eE+-]+)\s*,\s*([\d.eE+-]+)\s*,\s*([\d.eE+-]+)\s*\)", sav0, re.S):
    mass.setdefault(nm, (float(a), float(c)))
print(f"{len(mass)} mass markers", flush=True)

def bank_height(H, x_lo, x_hi, z0, z1):
    vals = [H[z*w + x] for z in range(z0, z1) for x in range(x_lo, x_hi)
            if H[z*w + x] > WR + 128]
    assert vals, f"no dry bank cells at x[{x_lo},{x_hi}]"
    return sum(vals) / len(vals) / 128.0

def build_variant(tag, mid, name, out, bridges):
    print(f"\n=== {name} ===", flush=True)
    hp = bytearray(sm.read_bdf_payload(hf_base))
    _, _, _, _, hd = struct.unpack_from("<5I", hp, 0)
    H = list(H0)
    def setH(x, z, yv):
        r = max(0, min(65535, int(yv * 128)))
        H[z*w + x] = r; struct.pack_into("<H", hp, hd + 2*(z*w + x), r)
    # ---- bridges: gently sloped causeway lerped between bank heights ----
    for (x0, z0, x1, z1) in bridges:
        hW = bank_height(H0, x0-16, x0-2, z0, z1)
        hE = bank_height(H0, x1+2, x1+16, z0, z1)
        for x in range(x0, x1+1):
            t = (x - x0) / (x1 - x0)
            deck = hW * (1 - t) + hE * t
            for z in range(z0, z1+1):
                edge = min(z - z0, z1 - z)          # soften band edges
                d = deck if edge >= 5 else deck - (5 - edge) * 0.0  # flat deck, nav-clean
                if H[z*w + x] / 128.0 < d:
                    setH(x, z, d)
        print(f"  bridge x[{x0},{x1}] z[{z0},{z1}]: deck {hW:.1f} -> {hE:.1f}", flush=True)
    # ---- mass pads: level 11x11 under every marker ----
    fixed = 0
    for nm, (mx, mz) in mass.items():
        xi, zi = int(round(mx)), int(round(mz))
        cells = [(x, z) for z in range(zi-5, zi+6) for x in range(xi-5, xi+6)]
        mean = sum(H[z*w + x] for x, z in cells) / len(cells) / 128.0
        if mean <= WL + 1: mean = WL + 2
        for x, z in cells:
            setH(x, z, mean)
        fixed += 1
    print(f"  pads levelled under {fixed} mass markers", flush=True)
    hf_new = sm.rebuild_bdf(hf_base, bytes(hp))
    # ---- nav: block water on land layers, block new decks on water-only layers ----
    pay = bytearray(sm.read_bdf_payload(base_entries[ent(".costs.win.bdf")[0]]))
    blocked = opened = 0
    for z in range(G):
        b25 = z*w; b24 = z*G
        for x in range(G):
            wet_now = H[b25 + x] <= WR
            wet_before = H0[b25 + x] <= WR
            i = b24 + x
            if wet_now:
                for li in land_layers:
                    if pay[layers[li][2] + i] != 255:
                        pay[layers[li][2] + i] = 255; blocked += 1
            elif wet_before:                       # new dry deck
                for li in land_layers:
                    pay[layers[li][2] + i] = 1
                for li in wateronly_layers:
                    pay[layers[li][2] + i] = 255
                opened += 1
    print(f"  nav: {blocked} water-walk cells closed, {opened} deck cells opened; recomputing islands...", flush=True)
    sm._recompute_islands(pay, layers)
    landL = land_layers[0]
    okWE = sm.reachable(pay, layers, (150, 500), (884, 585), landL)
    # prove bridges are the only routes: block band(s), expect unreachable
    pay2 = bytearray(pay)
    for (x0, z0, x1, z1) in bridges:
        for z in range(z0, z1+1):
            for x in range(x0, x1+1):
                pay2[layers[landL][2] + z*G + x] = 255
    okOnly = not sm.reachable(pay2, layers, (150, 500), (884, 585), landL)
    print(f"  reach: W<->E via bridge={okWE}; bridges-are-only-route={okOnly}", flush=True)
    assert okWE and okOnly
    costs_new = sm.rebuild_bdf(base_entries[ent(".costs.win.bdf")[0]], pay)
    # ---- mesh follows ----
    terr_new, moved = sm.resample_mesh_heights(base_entries[ent(".terrain.win.bdf")[0]],
                                               hf_base, hf_new, bvh_min_y=30.0, bvh_max_y=100.0)
    print(f"  mesh verts re-heighted: {moved}", flush=True)
    # ---- props: sink anything standing in the bridge bands (old reeds/rocks) ----
    def sink(k, x, y, z, path):
        for (x0, z0, x1, z1) in bridges:
            if x0-2 <= x <= x1+2 and z0-2 <= z <= z1+2:
                return (x, -100.0, z)
        return None
    mo_new, sunk = sm.edit_props(base_entries[ent(".mapobjs.win.bdf")[0]], sink)
    print(f"  props sunk in bridge bands: {sunk}", flush=True)
    # ---- save.lua: update mass y to pad heights ----
    sav = sav0
    for nm, (mx, mz) in mass.items():
        yv = H[int(round(mz))*w + int(round(mx))] / 128.0
        sav = re.sub(r"(\['" + nm + r"'\].*?VECTOR3\(\s*[\d.eE+-]+\s*,\s*)[\d.eE+-]+(\s*,)",
                     lambda m: m.group(1) + f"{yv:.3f}" + m.group(2), sav, count=1, flags=re.S)
    # ---- repackage under new id ----
    out_entries = {}
    for n, d in base_entries.items():
        nn = n.replace("SC2_DUNE6", mid)
        if n.endswith(".hfield.win.bdf"): d = hf_new
        elif n.endswith(".terrain.win.bdf"): d = terr_new
        elif n.endswith(".costs.win.bdf"): d = costs_new
        elif n.endswith(".mapobjs.win.bdf"): d = mo_new
        elif n.endswith("_save.lua"): d = sav.replace("SC2_DUNE6", mid).encode("utf-8")
        elif n.endswith(".lua"):
            txt = d.decode("utf-8", "replace").replace("SC2_DUNE6", mid)
            if n.endswith("_scenario.lua"):
                txt = re.sub(r"name = '[^']*'", f"name = '{name}'", txt, count=1)
            d = txt.encode("utf-8")
        out_entries[nn] = d
    buf = io.BytesIO(); zo = zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED)
    for n, d in out_entries.items(): zo.writestr(n, d)
    zo.close()
    dst = os.path.join(sm.GAMEDATA, out)
    open(dst, "wb").write(buf.getvalue())
    print(f"  INSTALLED {out} ({os.path.getsize(dst):,} bytes)", flush=True)

build_variant(1, "SC2_DUNEB1", "[6] Dune Rift - One Bridge (3v3)", "_dune_rift_bridge1.scd", [BRIDGE_A])
build_variant(2, "SC2_DUNEB2", "[6] Dune Rift - Two Bridges (3v3)", "_dune_rift_bridge2.scd", [BRIDGE_A, BRIDGE_B])
print("\nDONE - both variants installed", flush=True)
