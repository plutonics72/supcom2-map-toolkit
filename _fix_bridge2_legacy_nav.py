"""Two Bridges: replace the map-wide re-derived nav with LEGACY-based nav.

Root cause (user videos, 19 Jul 2026): re-deriving walkability from terrain slope
(dry+gentle) across the WHOLE map replaced CA_I01's clean baked nav with a noisy
mask - maze-like edges through every rugged area -> convoluted paths, commander
stalls. The base map (legacy nav) plays fine. Fix: start from the base v3 costs
(legacy + ramp cells), close land layers over water, open deck/apron cells,
wall water-only layers under the causeways. Pathing baseline == base map.
Verified: all r=3 eroded routes OK, per-bridge proofs OK, bridges-only OK,
corridors 25/48; land layers byte-identical to base outside water+deck bands.
"""
import zipfile, io, os, shutil
from collections import deque
import sc2maps as sm

G = 1024; WL = 34.0; WR = int(WL * 128)
BRIDGES = [(430, 560, 692, 612), (438, 762, 680, 815)]
APRON = 30

t_stock = sm.Terrain("SC2_CA_I01")
layers = t_stock.layers
stock = t_stock.costs_payload
land_layers = [li for li, r in enumerate(layers) if stock[r[2] + 100*G + 900] == 255]
wateronly = [li for li, r in enumerate(layers)
             if stock[r[2] + 100*G + 900] != 255 and stock[r[2] + 230*G + 150] == 255]
print(f"land={land_layers} wateronly={wateronly}", flush=True)

base_p = os.path.join(sm.GAMEDATA, "_dune_rift_3v3.scd")
b2_p = os.path.join(sm.GAMEDATA, "_dune_rift_bridge2.scd")
zb = zipfile.ZipFile(base_p)
Hb, w = sm.hfield_heights(zb.read([n for n in zb.namelist() if n.endswith(".hfield.win.bdf")][0]))
costs_base_raw = zb.read([n for n in zb.namelist() if n.endswith(".costs.win.bdf")][0])
zb.close()
z2 = zipfile.ZipFile(b2_p); names2 = z2.namelist()
ent2 = {n: z2.read(n) for n in names2}; z2.close()
H2, w2 = sm.hfield_heights(ent2[[n for n in names2 if n.endswith(".hfield.win.bdf")][0]])
assert w == w2

pay = bytearray(sm.read_bdf_payload(costs_base_raw))
in_band = bytearray(G*G)
for (x0, z0, x1, z1) in BRIDGES:
    for z in range(z0, z1+1):
        for x in range(max(0, x0-APRON), min(G, x1+APRON+1)):
            in_band[z*G+x] = 1
closed = opened = walled = 0
for z in range(G):
    b25 = z*w; b24 = z*G
    for x in range(G):
        i = b24 + x
        wet2 = H2[b25 + x] <= WR
        wetb = Hb[b25 + x] <= WR
        if wet2:
            for li in land_layers:
                if pay[layers[li][2] + i] != 255:
                    pay[layers[li][2] + i] = 255; closed += 1
        elif wetb or in_band[i]:
            for li in land_layers:
                pay[layers[li][2] + i] = 1
            opened += 1
            if wetb:
                for li in wateronly:
                    pay[layers[li][2] + i] = 255
                walled += 1
print(f"water closed: {closed}; deck/apron opened: {opened}; naval walls: {walled}", flush=True)
sm._recompute_islands(pay, layers)

# ---- erosion r=3 verification ----
o = layers[land_layers[0]][2]
m0 = bytearray(1 if pay[o+i] != 255 else 0 for i in range(G*G))

def erode(m, r):
    out = bytearray(m)
    for z in range(G):
        b = z*G; row = m[b:b+G]
        for x in range(G):
            if row[x] and 0 in row[max(0, x-r):min(G, x+r+1)]: out[b+x] = 0
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
        for dx, dz in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            nx, nz = x+dx, z+dz
            if 0 <= nx < G and 0 <= nz < G and not seen[nz*G+nx] and m[nz*G+nx]:
                seen[nz*G+nx] = 1; q.append(nz*G+nx)
    return seen

def snap(m, px, pz):
    for rr in range(0, 40, 2):
        for dz in range(-rr, rr+1, 2):
            for dx in range(-rr, rr+1, 2):
                if 0 <= px+dx < G and 0 <= pz+dz < G and m[(pz+dz)*G+px+dx]:
                    return (pz+dz)*G+px+dx
    return None

def reach(m, a, b):
    s, t = snap(m, *a), snap(m, *b)
    return s is not None and t is not None and flood(m, s)[t]

er = erode(m0, 3)
allok = True
for nm, a, b in [("spawn2->spawn5 (both bridges)", (150, 500), (884, 585)),
                 ("spawn2->basin", (150, 500), (470, 470)),
                 ("spawn5->basin", (884, 585), (470, 470)),
                 ("spawn1->basin", (150, 180), (470, 470)),
                 ("spawn6->spawn3", (884, 870), (150, 820))]:
    ok = reach(er, a, b); allok &= ok
    print(f"[r=3] {nm}: {'OK' if ok else 'FAIL'}", flush=True)
for tag, keep, block in [("A", BRIDGES[0], BRIDGES[1]), ("B", BRIDGES[1], BRIDGES[0])]:
    m3 = bytearray(m0)
    for z in range(block[1]-4, block[3]+5):
        for x in range(block[0]-4, block[2]+5):
            m3[z*G+x] = 0
    ok = reach(erode(m3, 3), (150, 500), (884, 585)); allok &= ok
    print(f"[r=3] cross-rift via {tag} only: {'OK' if ok else 'FAIL'}", flush=True)
m2b = bytearray(m0)
for (x0, z0, x1, z1) in BRIDGES:
    for z in range(z0-4, z1+5):
        for x in range(x0-4-APRON, x1+5+APRON):
            if 0 <= x < G: m2b[z*G+x] = 0
only = not reach(m2b, (150, 500), (884, 585))
print(f"bridges are only land route: {only}", flush=True)
allok &= only
for (x0, z0, x1, z1, tag) in [(430, 560, 692, 612, "A"), (438, 762, 680, 815, "B")]:
    minw = 999; minx = None
    for x in range(x0, x1+1):
        best = run = 0
        for z in range(z0-6, z1+7):
            if er[z*G+x]: run += 1; best = max(best, run)
            else: run = 0
        if best < minw: minw, minx = best, x
    print(f"deck {tag} min eroded corridor: {minw} (x={minx})", flush=True)
    allok &= minw >= 14
assert allok, "verification failed"

costs_new = sm.rebuild_bdf(ent2[[n for n in names2 if n.endswith(".costs.win.bdf")][0]], pay)
shutil.copy2(b2_p, b2_p + ".PRE_LEGACYNAV.bak")
out = dict(ent2)
out[[n for n in names2 if n.endswith(".costs.win.bdf")][0]] = costs_new
buf = io.BytesIO(); zo = zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED)
for n, d in out.items(): zo.writestr(n, d)
zo.close()
open(b2_p, "wb").write(buf.getvalue())
print(f"INSTALLED ({os.path.getsize(b2_p):,} B)", flush=True)
