"""Boolon Isles (SC2_BOOLI1): Boolon's true platform topology as REAL ISLANDS
in open water, connected by causeway bridges. v1 (lobby v1).

Why: Harbor (flat plates on Treallach) "looks like a different map" (user,
10 Aug). This build keeps Boolon's IDENTITY - the interlocking central complex
+ symmetric satellite ring, isolation between areas, bridge connections - but
renders it with real terrain art (the only thing the engine lets us paint).

Blueprint (measured from SC2_MP_104's nav mask, erosion r=12):
- 10 island footprints = deck-mask components: central complex (~447,511) +
  E (764,510) + spawn satellites NW/SW/N/S/NE/SE + inner-west pair.
- Bridges: for island pairs whose original deck mask carries a neck between
  them (>=50% deck along the closest-boundary line, gap < 90 cells), build a
  Dune Rift causeway: 14-wide flat deck at WL+8 with 1.2/cell aprons.
- Sea: everything else pushed to <= WL-18 (no drowned-donor ghost terrain).
- Islands: donor terrain RAISED under each footprint (Iskellian pattern -
  keeps real ground art): lift = (WL+5) - min(donor) inside the footprint,
  plateau-capped at WL+9, 10-cell edge taper down to the seabed (natural
  beaches; hover lands there, ships stay off via depth).
Donor: Treallach SC2_MP_302 (WL=56; the machinery proven by Strait + Harbor).
Nav: land pair on islands+bridges (recomputed from final dry+gentle ground),
amphib triple stock semantics (open water + land) with wet-aware anti-boarding
walls on bridge decks only (island edges are tapered beaches - no walls
needed). waterDepth: per-cell height rule + block-accurate island/bridge
dry pass (Harbor v6 lesson - no halo). Props sunk. Boolon's luas; marker y
recomputed per final ground. Minimap synthetic; previews regenerated.
Gates: r=3 routes between all 6 spawns (over bridges), bridge-only isolation
pairs, mesh/collision/marker/wd/island-unity, versioned lobby name.
"""
import math
import zipfile, io, os, re, struct, shutil
from collections import deque, defaultdict
import sc2maps as sm

G = 1024
DONOR = "SC2_MP_302"
SRC_LAYOUT = "SC2_MP_104"
ID_NEW = "SC2_BOOLI1"
NAME = "[6] Boolon Isles (3v3) v2"
OUT = os.path.join(sm.GAMEDATA, "_boolon_isles.scd")
WL = 56.0; WR = int(WL * 128)
ISLAND_TOP = WL + 9.0
ISLAND_MIN = WL + 5.0
SEA_MAX = WL - 18.0
TAPER = 10
BRIDGE_W = 18
BRIDGE_Y = WL + 8.0
APRON = 24; APRON_SLOPE = 1.2
ERODE_R = 12
MIN_ISLAND = 1500
SPAWNS = {1: (250, 703), 2: (248, 314), 3: (750, 732), 4: (754, 282), 5: (548, 816), 6: (550, 202)}

# ---- load ----
mz = zipfile.ZipFile(os.path.join(sm.GAMEDATA, "maps.scd"))
donor = {n.split("maps/" + DONOR + "/")[-1]: mz.read(n) for n in mz.namelist()
         if n.startswith(f"maps/{DONOR}/")}
mz.close()
uz = zipfile.ZipFile(os.path.join(sm.GAMEDATA, "uncompiled_lua.scd"))
lua104 = {n.split("/")[-1]: uz.read(n).decode("utf-8", "replace") for n in uz.namelist()
          if n.startswith(f"uncompiled/maps/{SRC_LAYOUT}/")}
uz.close()
t104 = sm.Terrain(SRC_LAYOUT)
deck104 = bytearray(1 if t104.costs_payload[t104.layers[0][2] + i] != 255 else 0 for i in range(G*G))
td = sm.Terrain(DONOR)
layers = td.layers
assert len(layers) == 5
w = td.HW
H0 = td.H
print("loaded", flush=True)

# ---- blueprint: island footprints from eroded deck mask ----
def erode_m(m, r):
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

core = erode_m(deck104, ERODE_R)
label = [0]*(G*G)
islands = {}
nid = 0
for i in range(G*G):
    if core[i] and not label[i]:
        nid += 1
        q = deque([i]); label[i] = nid; cells = [i]
        while q:
            j = q.popleft()
            x, z = j % G, j // G
            for dx, dz in ((1,0),(-1,0),(0,1),(0,-1)):
                nx, nz = x+dx, z+dz
                if 0 <= nx < G and 0 <= nz < G:
                    k = nz*G+nx
                    if core[k] and not label[k]:
                        label[k] = nid; q.append(k); cells.append(k)
        islands[nid] = cells
islands = {pid: c for pid, c in islands.items() if len(c) >= MIN_ISLAND}
# dilate each island 3 cells: the r=12 cores keep sub-7-cell waists (the east
# island's split the r=3 interior); +3 widens every waist while keeping the
# silhouette; accidental merges would trip the bridges-cut isolation gate
for pid in list(islands):
    cells = set(islands[pid])
    frontier = set(cells)
    for _ in range(3):
        nxt = set()
        for i in frontier:
            x, z = i % G, i // G
            for dx, dz in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                nx, nz = x + dx, z + dz
                if 0 <= nx < G and 0 <= nz < G:
                    j = nz*G + nx
                    if j not in cells:
                        nxt.add(j)
        cells |= nxt
        frontier = nxt
    islands[pid] = list(cells)
print(f"islands: {len(islands)}: " + ", ".join(
    f"P{pid}({len(c)})" for pid, c in sorted(islands.items(), key=lambda kv: -len(kv[1]))), flush=True)
assert 8 <= len(islands) <= 12, "unexpected island count"
# ---- v2: classify islands, fill the central lake, grow the home islands ----
big_id = max(islands, key=lambda p: len(islands[p]))
cent = {}
for pid, cells in islands.items():
    xs = sum(i % G for i in cells) // len(cells)
    zs = sum(i // G for i in cells) // len(cells)
    cent[pid] = (xs, zs)
owner0 = bytearray(G*G)          # 1-based compact ids for BFS labels
pid_order = sorted(islands)
pid_idx = {pid: k+1 for k, pid in enumerate(pid_order)}
for pid, cells in islands.items():
    for i in cells: owner0[i] = pid_idx[pid]
spawn_ids = set()
for a, (sx, sz) in SPAWNS.items():
    assert owner0[sz*G + sx], f"spawn {a} not on an island footprint"
    spawn_ids.add(pid_order[owner0[sz*G + sx] - 1])
stone_ids = {pid for pid in islands
             if pid not in spawn_ids and pid != big_id and 320 <= cent[pid][0] <= 390}
east_id = next(pid for pid in islands
               if pid not in spawn_ids and pid != big_id and pid not in stone_ids
               and cent[pid][0] > 700)
print(f"classified: central P{big_id}, east P{east_id}, spawns {sorted(spawn_ids)}, "
      f"stones {sorted(stone_ids)}", flush=True)

# (lake fill happens after the Voronoi fields are built - see below)

# growth guards: Voronoi labels + distance-to-Voronoi-boundary (keeps 20-cell gaps)
near_id = bytearray(owner0)
near_d = [0 if owner0[i] else 10**6 for i in range(G*G)]
q = deque(i for i in range(G*G) if owner0[i])
while q:
    i = q.popleft(); x, z = i % G, i // G
    for dx, dz in ((1,0),(-1,0),(0,1),(0,-1)):
        nx, nz = x+dx, z+dz
        if 0 <= nx < G and 0 <= nz < G:
            j = nz*G+nx
            if near_d[j] > near_d[i] + 1:
                near_d[j] = near_d[i] + 1
                near_id[j] = near_id[i]
                q.append(j)
bd = [10**6]*(G*G)
q = deque()
for z in range(G):
    for x in range(G):
        i = z*G + x
        for dx, dz in ((1,0),(0,1)):
            nx, nz = x+dx, z+dz
            if nx < G and nz < G and near_id[nz*G+nx] != near_id[i]:
                if bd[i]: bd[i] = 0; q.append(i)
                j = nz*G+nx
                if bd[j]: bd[j] = 0; q.append(j)
while q:
    i = q.popleft(); x, z = i % G, i // G
    for dx, dz in ((1,0),(-1,0),(0,1),(0,-1)):
        nx, nz = x+dx, z+dz
        if 0 <= nx < G and 0 <= nz < G:
            j = nz*G+nx
            if bd[j] > bd[i] + 1:
                bd[j] = bd[i] + 1; q.append(j)

# lake fill (needs the Voronoi fields): the measured v1 lake pocket (bbox
# x255-431 z325-466) becomes arena floor of the central island; an 8-cell moat
# stays around any OTHER island (the stepping stone keeps its ring of water)
stone_idx = {pid_idx[pid] for pid in stone_ids}
lake_cells = []
for z in range(325, 467):
    for x in range(255, 432):
        i = z*G + x
        if owner0[i]:
            continue
        # central's own region always fills; stepping-stone regions fill only
        # beyond an 8-cell moat; SPAWN-island regions never fill (their x4
        # growth expands there - filling them fused P15 with the arena)
        if near_id[i] == pid_idx[big_id] or (near_id[i] in stone_idx and near_d[i] >= 8):
            lake_cells.append(i)
islands[big_id] = list(set(islands[big_id]) | set(lake_cells))
for i in lake_cells: owner0[i] = pid_idx[big_id]
print(f"lake fill: {len(lake_cells)} cells joined the central island (arena floor)", flush=True)

FRAME = 24
def grow_island(pid, want_cells, max_r, min_bd=8):
    idx = pid_idx[pid]
    cur = set(islands[pid])
    elig = [(near_d[i], i) for i in range(G*G)
            if not owner0[i] and near_id[i] == idx and near_d[i] <= max_r and bd[i] >= min_bd
            and FRAME <= i % G < G - FRAME and FRAME <= i // G < G - FRAME]
    elig.sort()
    for _d, i in elig:
        if len(cur) >= want_cells: break
        cur.add(i)
    islands[pid] = list(cur)
    return len(cur)

v1_sizes = {pid: len(c) for pid, c in islands.items()}
for pid in sorted(spawn_ids):
    got = grow_island(pid, 4 * v1_sizes[pid], 60)
    print(f"spawn island P{pid}: {v1_sizes[pid]} -> {got} cells", flush=True)
got_e = grow_island(east_id, 4 * v1_sizes[east_id], 26)
print(f"east peninsula P{east_id}: {v1_sizes[east_id]} -> {got_e} cells", flush=True)

isl_mask = bytearray(G*G)
for cells in islands.values():
    for i in cells: isl_mask[i] = 1

# ---- blueprint: bridges from original necks ----
def boundary(cells):
    bs = []
    cset = set(cells)
    for i in cells:
        x, z = i % G, i // G
        if any((z+dz)*G + (x+dx) not in cset
               for dx, dz in ((1,0),(-1,0),(0,1),(0,-1)) if 0 <= x+dx < G and 0 <= z+dz < G):
            bs.append((x, z))
    return bs
# endpoints from the r=3-eroded cores: guarantees a 7-wide landing, never a spur
core3 = erode_m(core, 3)
islands3 = {}
for pid, cells in islands.items():
    c3 = [i for i in cells if core3[i]]
    islands3[pid] = c3 if len(c3) >= 50 else cells
bnds = {pid: boundary(c) for pid, c in islands3.items()}
bridges = []
pids = sorted(islands)
for ai in range(len(pids)):
    for bi in range(ai+1, len(pids)):
        pa, pb = pids[ai], pids[bi]
        best = None; bd = 1e18
        for (ax, az) in bnds[pa][::3]:
            for (bx, bz) in bnds[pb][::3]:
                d = (ax-bx)**2 + (az-bz)**2
                if d < bd: bd, best = d, (ax, az, bx, bz)
        if best is None or bd > 90*90:
            continue
        ax, az, bx, bz = best
        steps = max(abs(ax-bx), abs(az-bz))
        on_deck = sum(1 for s in range(steps+1)
                      if deck104[int(az + (bz-az)*s/max(1,steps))*G + int(ax + (bx-ax)*s/max(1,steps))])
        if on_deck >= steps * 35 // 100:
            bridges.append((pa, pb, ax, az, bx, bz))
print(f"evidence bridges: {len(bridges)}: " + ", ".join(f"P{a}-P{b}" for a, b, *_ in bridges), flush=True)
# spanning pass: every island must join ONE bridge-connected component (some of
# Boolon's original walkways bend, so the straight-line evidence test misses them)
parent = {pid: pid for pid in pids}
def find(p):
    while parent[p] != p:
        parent[p] = parent[parent[p]]; p = parent[p]
    return p
for (pa, pb, *_r) in bridges:
    parent[find(pa)] = find(pb)
def closest_pair(compA, compB):
    best = None; bd = 1e18
    for pa in compA:
        for pb in compB:
            for (ax, az) in bnds[pa][::4]:
                for (bx, bz) in bnds[pb][::4]:
                    d = (ax-bx)**2 + (az-bz)**2
                    if d < bd: bd, best = d, (pa, pb, ax, az, bx, bz)
    return best, bd
while True:
    comps = defaultdict(list)
    for pid in pids: comps[find(pid)].append(pid)
    groups = list(comps.values())
    if len(groups) == 1: break
    groups.sort(key=len, reverse=True)
    main, rest = groups[0], [p for g in groups[1:] for p in g]
    best_all = None; bd_all = 1e18
    for g in groups[1:]:
        cand, d = closest_pair(main, g)
        if cand and d < bd_all: bd_all, best_all = d, cand
    assert best_all, "cannot connect island graph"
    pa, pb, ax, az, bx, bz = best_all
    bridges.append((pa, pb, ax, az, bx, bz))
    parent[find(pa)] = find(pb)
    print(f"spanning bridge added: P{pa}-P{pb}", flush=True)
print(f"bridges total: {len(bridges)}: " + ", ".join(f"P{a}-P{b}" for a, b, *_ in bridges), flush=True)

# ---- hfield synthesis ----
hf_raw = donor[DONOR + ".hfield.win.bdf"]
hp = bytearray(sm.read_bdf_payload(hf_raw))
_, _, _, _, hd = struct.unpack_from("<5I", hp, 0)
Hn = list(H0)
def setH(x, z, yv):
    r_ = max(0, min(65535, int(yv * 128)))
    Hn[z*w + x] = r_
    struct.pack_into("<H", hp, hd + 2*(z*w + x), r_)
# 1) sea floor: cap everything at SEA_MAX (kills drowned-donor ghost terrain)
for z in range(G):
    for x in range(G):
        if H0[z*w + x] > int(SEA_MAX * 128):
            setH(x, z, SEA_MAX)
# 2) islands: lift donor relief under each footprint; clamp into [ISLAND_MIN, ISLAND_TOP]
dist_out = bytearray(255 for _ in range(G*G))       # distance outside islands (for taper)
q = deque()
for i in range(G*G):
    if isl_mask[i]:
        dist_out[i] = 0; q.append(i)
while q:
    i = q.popleft(); d = dist_out[i]
    if d >= TAPER + 1: continue
    x, z = i % G, i // G
    for dx, dz in ((1,0),(-1,0),(0,1),(0,-1)):
        nx, nz = x+dx, z+dz
        if 0 <= nx < G and 0 <= nz < G:
            j = nz*G+nx
            if dist_out[j] > d + 1:
                dist_out[j] = d + 1; q.append(j)
ARENA_H = ISLAND_MIN + 0.5
# central island: dist-to-coast BFS for the interior-flat arena
cent_set = set(islands[big_id])
din = {}
q = deque()
for i in cent_set:
    x, z = i % G, i // G
    if any(((z+dz)*G + (x+dx)) not in cent_set
           for dx, dz in ((1,0),(-1,0),(0,1),(0,-1)) if 0 <= x+dx < G and 0 <= z+dz < G):
        din[i] = 0; q.append(i)
while q:
    i = q.popleft(); d = din[i]
    x, z = i % G, i // G
    for dx, dz in ((1,0),(-1,0),(0,1),(0,-1)):
        j = (z+dz)*G + (x+dx)
        if j in cent_set and din.get(j, 10**6) > d + 1:
            din[j] = d + 1; q.append(j)
for pid, cells in islands.items():
    dmin = min(H0[(i//G)*w + (i % G)] for i in cells) / 128.0
    for i in cells:
        x, z = i % G, i // G
        relief = ISLAND_MIN + max(0.0, min(ISLAND_TOP - ISLAND_MIN,
                                           (H0[z*w + x] / 128.0 - dmin) * 0.35))
        if pid == big_id:
            d = din.get(i, 0)
            if d >= 18:
                yv = ARENA_H                          # the contested arena
            elif d >= 12:
                t_ = (d - 12) / 6.0
                yv = relief * (1 - t_) + ARENA_H * t_ # blend band
            else:
                yv = relief                           # coastal fringe keeps relief
        else:
            yv = relief
        setH(x, z, yv)
# taper ring: blend island edge down to the sea floor
for i in range(G*G):
    d = dist_out[i]
    if 1 <= d <= TAPER and not isl_mask[i]:
        x, z = i % G, i // G
        t_ = d / (TAPER + 1)
        edge_h = ISLAND_MIN
        target = edge_h + (SEA_MAX - edge_h) * t_
        if Hn[z*w + x] / 128.0 < target:
            setH(x, z, target)
print("islands raised + tapered", flush=True)
# 3) bridges: flat deck along each neck line + aprons onto the islands
bridge_mask = bytearray(G*G)
for (pa, pb, ax, az, bx, bz) in bridges:
    steps = max(abs(ax-bx), abs(az-bz), 1)
    dx_, dz_ = (bx-ax)/steps, (bz-az)/steps
    L = math.hypot(bx-ax, bz-az)
    px_, pz_ = (-(bz-az)/max(L,1e-9), (bx-ax)/max(L,1e-9))
    # HALF-STEP sampling in both axes: 0.5 spacing guarantees full cell coverage
    # on diagonal spans (integer stepping provably leaves hole lattices at ~45deg)
    for s2 in range(-2*APRON, 2*(steps + APRON) + 1):
        s = s2 / 2.0
        cx, cz = ax + dx_*s, az + dz_*s
        for o2 in range(-BRIDGE_W, BRIDGE_W + 1):
            o = o2 / 2.0
            x, z = math.floor(cx + px_*o), math.floor(cz + pz_*o)
            if not (0 <= x < G and 0 <= z < G): continue
            i = z*G + x
            if 0 <= s <= steps:
                if not isl_mask[i]:
                    setH(x, z, BRIDGE_Y)
                    bridge_mask[i] = 1
                elif s <= 5 or s >= steps - 5:
                    # junction plateau: flatten the island landing to deck height
                    # so the r=3 corridor never pinches at the bridge mouth
                    if Hn[z*w + x] / 128.0 < BRIDGE_Y:
                        setH(x, z, BRIDGE_Y)
            else:                              # apron: only raise low ground on islands
                dd = (-s) if s < 0 else (s - steps)
                tgt = BRIDGE_Y - APRON_SLOPE * dd
                if tgt > WL + 1 and Hn[z*w + x] / 128.0 < tgt and isl_mask[i]:
                    setH(x, z, tgt)
print("bridges built", flush=True)

# ---- mass pads: relocate old-neck masses onto land NOW and level 11x11 pads
# (BEFORE the hfield freeze so mesh/collision/waterDepth all see the pads;
# Iskellian lesson: unlevelled relief under masses = unbuildable extractors) ----
walk_approx = bytearray(1 if (isl_mask[i] or bridge_mask[i]) else 0 for i in range(G*G))
# pads must sit fully INSIDE islands: use the interior (eroded) mask for targets
pad_interior = erode_m(bytearray(isl_mask), 6)
def nearest_approx(xi, zi):
    for rr in range(0, 120, 2):
        for dz in range(-rr, rr+1, 2):
            for dx in range(-rr, rr+1, 2):
                nx, nz = xi+dx, zi+dz
                if 0 <= nx < G and 0 <= nz < G and pad_interior[nz*G + nx]:
                    return nx, nz
    return xi, zi
def level_pad(xi, zi):
    xi = min(G-6, max(5, xi)); zi = min(G-6, max(5, zi))
    cells = [(x, z) for z in range(zi-5, zi+6) for x in range(xi-5, xi+6)]
    mean = sum(Hn[z*w+x] for x, z in cells) // len(cells)
    for x, z in cells:
        Hn[z*w+x] = mean
        struct.pack_into("<H", hp, hd + 2*(z*w+x), mean)
    return xi, zi
mass_pads = []
sav_src = lua104[f"{SRC_LAYOUT}_save.lua"]
def far_from_pads(xi, zi):
    return all(max(abs(xi - px_), abs(zi - pz_)) >= 14 for _n, px_, pz_ in mass_pads)
for nm_, a_, b_, c_ in re.findall(r"\['(Mass \d+)'\].*?VECTOR3\(\s*([\d.eE+-]+)\s*,\s*([\d.eE+-]+)\s*,\s*([\d.eE+-]+)\s*\)", sav_src, re.S):
    xi, zi = int(float(a_)), int(float(c_))
    if not (0 <= xi < G and 0 <= zi < G) or not pad_interior[zi*G + xi] or not far_from_pads(xi, zi):
        found = False
        for rr in range(0, 140, 2):          # nearest interior cell clear of other pads
            for dz in range(-rr, rr+1, 2):
                for dx in range(-rr, rr+1, 2):
                    nx, nz = min(G-1, max(0, xi+dx)), min(G-1, max(0, zi+dz))
                    if pad_interior[nz*G + nx] and far_from_pads(nx, nz):
                        xi, zi = nx, nz; found = True; break
                if found: break
            if found: break
    xi, zi = level_pad(xi, zi)
    mass_pads.append((nm_, xi, zi))
print(f"mass pads: {len(mass_pads)} levelled", flush=True)

hf_new = sm.rebuild_bdf(hf_raw, bytes(hp))

# ---- mesh: delta resample + forced sync on all touched dry cells ----
terr_raw = donor[DONOR + ".terrain.win.bdf"]
terr_new, mv = sm.resample_mesh_heights(terr_raw, hf_raw, hf_new, tol=6.0, min_r=0.5,
                                        bvh_min_y=0.0, bvh_max_y=360.0)
p_orig, b_orig, nv0, _ = sm.locate_mesh_blob(terr_raw)
p2, b2, nv2, _ = sm.locate_mesh_blob(terr_new)
assert nv2 == nv0
pb = bytearray(p2)
forced = 0
for i in range(nv2):
    off = b2 + 20 + 32*i
    ox, oy, oz = struct.unpack_from("<3f", p_orig, b_orig + 20 + 32*i)
    if not (0 <= ox < G and 0 <= oz < G): continue
    if oy < 5.0: continue
    if abs(oy - WL) < 1.0: continue
    oh = sm.hf_sample(H0, w, ox, oz)
    if oy > oh + 8.0: continue                       # tall scenery stays
    xi, zi = int(ox), int(oz)
    ii = zi*G + xi
    touched = isl_mask[ii] or bridge_mask[ii] or dist_out[ii] <= TAPER or H0[zi*w + xi] > int(SEA_MAX*128)
    if not touched: continue
    gy = sm.hf_sample(Hn, w, ox, oz) - 0.15
    x, y, z = struct.unpack_from("<3f", pb, off)
    if abs(y - gy) > 0.3:
        struct.pack_into("<3f", pb, off, x, gy, z)
        forced += 1
terr_new = sm.rebuild_bdf(terr_new, bytes(pb))
print(f"mesh: {mv} delta-tracked, {forced} forced", flush=True)
terr_new = sm.retarget_waterdepth_path(terr_new, DONOR, ID_NEW)

# ---- collision: global snap ----
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
print(f"collision: {snapped}/{cnv}", flush=True)

# ---- nav: recompute from final ground ----
pay = bytearray(sm.read_bdf_payload(donor[DONOR + ".costs.win.bdf"]))
wet_ref = None
for z in range(0, G, 11):
    for x in range(0, G, 11):
        i = z*G + x
        if (wet_ref is None and H0[z*w+x] < WR - 15*128
                and any(td.costs_payload[layers[li][2] + i] != 255 for li in range(5))):
            wet_ref = i
assert wet_ref is not None
land_L = [li for li in range(5) if td.costs_payload[layers[li][2] + wet_ref] == 255]
amph_L = [li for li in range(5) if li not in land_L]
SLOPE_MAX = int(1.2 * 128)
def gentle(x, z):
    h0 = Hn[z*w + x]
    for dx, dz in ((1, 0), (-1, 0), (0, 1), (0, -1)):
        nx, nz = x + dx, z + dz
        if 0 <= nx < G and 0 <= nz < G and abs(Hn[nz*w + nx] - h0) > SLOPE_MAX:
            return False
    return True
for z in range(G):
    for x in range(G):
        i = z*G + x
        dry = Hn[z*w + x] > WR
        walk = dry and (isl_mask[i] or bridge_mask[i] or (dist_out[i] <= TAPER and gentle(x, z)))
        for li in land_L:
            pay[layers[li][2] + i] = 1 if walk else 255
        for li in amph_L:
            pay[layers[li][2] + i] = 1 if (not dry or walk or gentle(x, z)
                                           or dist_out[i] <= TAPER) else 255
# anti-boarding walls on BRIDGE decks only (islands have tapered beaches by design)
def near_wet2(x, z):
    for dz in range(-2, 3):
        for dx in range(-2, 3):
            nx, nz = x + dx, z + dz
            if 0 <= nx < G and 0 <= nz < G and Hn[nz*w + nx] <= WR:
                return True
    return False
walled = 0
for i in range(G*G):
    if bridge_mask[i]:
        x, z = i % G, i // G
        if near_wet2(x, z):
            for li in amph_L:
                pay[layers[li][2] + i] = 255
            walled += 1
print(f"nav set; bridge anti-boarding {walled} cells", flush=True)
sm._recompute_islands(pay, layers)
costs_new = sm.rebuild_bdf(donor[DONOR + ".costs.win.bdf"], pay)
walk_mask = bytearray(1 if pay[layers[land_L[0]][2] + i] != 255 else 0 for i in range(G*G))

# ---- waterDepth (Harbor v6 recipe: per-cell rule + block-accurate dry pass) ----
t_wd = sm.Terrain(DONOR)
t_wd.set_hfield(hf_new)
dry_mask = bytearray(1 if Hn[(i//G)*w + (i % G)] > WR else 0 for i in range(G*G))
def is_water(x, z):
    xi = min(G-1, max(0, int(x))); zi = min(G-1, max(0, int(z)))
    return not dry_mask[zi*G + xi]
wd = bytearray(sm.write_waterdepth_dds_mips(t_wd, WL, is_water=is_water))
forced_dry = 0
for bj in range(128):
    for bi in range(128):
        has_dry = False
        for cz in range(bj*8, bj*8 + 8):
            base_ = cz*G
            for cx in range(bi*8, bi*8 + 8):
                if dry_mask[base_ + cx]:
                    has_dry = True; break
            if has_dry: break
        if has_dry:
            off = 128 + (bj*128 + bi) * 16
            if wd[off] != 0 or wd[off+1] != 0:
                wd[off:off+16] = bytes(8) + struct.pack("<HHI", 0, 0, 0)
                forced_dry += 1
wd = bytes(wd)
print(f"waterDepth: {forced_dry} dry-carrying blocks forced dry", flush=True)

# ---- props: sink all ----
def sink_all(k, x, y, z, path):
    return (x, -150.0, z)
mo_new, sunk = sm.edit_props(donor[DONOR + ".mapobjs.win.bdf"], sink_all)
print(f"props sunk: {sunk}", flush=True)

# ---- luas: Boolon layout; markers RELOCATED to nearest walkable + y from ground ----
def nearest_walk(xi, zi):
    for rr in range(0, 120, 2):
        for dz in range(-rr, rr+1, 2):
            for dx in range(-rr, rr+1, 2):
                nx, nz = xi+dx, zi+dz
                if 0 <= nx < G and 0 <= nz < G and walk_mask[nz*G + nx]:
                    return nx, nz
    return xi, zi
def fix_y_dynamic(txt):
    def rep(m):
        x = float(m.group(1)); z = float(m.group(2))
        xi = min(G-1, max(0, int(x))); zi = min(G-1, max(0, int(z)))
        if not walk_mask[zi*G + xi]:
            xi, zi = nearest_walk(xi, zi)          # old-neck markers moved onto land
        return f"VECTOR3( {xi}.500000, {Hn[zi*w + xi]/128.0:.6f}, {zi}.500000 )"
    return re.sub(r"VECTOR3\(\s*([\d.eE+-]+)\s*,\s*[\d.eE+-]+\s*,\s*([\d.eE+-]+)\s*\)", rep, txt)
scen = lua104[f"{SRC_LAYOUT}_scenario.lua"]
assert "<LOC SC2_MAPNAME_0020>[6] Boolon Complex (3v3)" in scen
scen = scen.replace("<LOC SC2_MAPNAME_0020>[6] Boolon Complex (3v3)", NAME)
scen = fix_y_dynamic(scen).replace(SRC_LAYOUT, ID_NEW)
sav = fix_y_dynamic(lua104[f"{SRC_LAYOUT}_save.lua"]).replace(SRC_LAYOUT, ID_NEW)
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
sea_block = grab_block(*sea_px); land_block = grab_block(*dry_px)
for bz in range(mh // 4):
    for bx in range(mw // 4):
        cx0, cz0 = bx * cpb, bz * cpb
        n_dry = sum(1 for cz in range(cz0, min(G, cz0+cpb))
                    for cx in range(cx0, min(G, cx0+cpb)) if dry_mask[cz*G + cx])
        off = 128 + (bz * (mw//4) + bx) * 8
        mmd[off:off+8] = land_block if n_dry >= (cpb*cpb)//2 else sea_block
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
print("minimap + previews", flush=True)

# ---- verification ----
m0 = bytearray(1 if pay[layers[land_L[0]][2] + i] != 255 else 0 for i in range(G*G))
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
# debug: un-eroded connectivity first (disconnection vs pinch tells different stories)
s1r = snapc(m0, *SPAWNS[1]); seen_r = flood(m0, s1r)
raw_ok = [a for a in range(2, 7)
          if (lambda t3: t3 is not None and seen_r[t3])(snapc(m0, *SPAWNS[a]))]
print(f"[raw] spawn1 reaches: {raw_ok} (flood size {sum(seen_r)})", flush=True)
er = erode_m(m0, 3)
s1 = snapc(er, *SPAWNS[1]); seen = flood(er, s1)
for a in range(2, 7):
    t2 = snapc(er, *SPAWNS[a])
    ok = t2 is not None and seen[t2]
    print(f"[r=3] spawn1 -> spawn{a}: {'OK' if ok else 'FAIL'}", flush=True)
    if not ok and t2 is not None:
        comp_a = flood(er, t2)
        xs = [i % G for i in range(G*G) if comp_a[i]]
        zs = [i // G for i in range(G*G) if comp_a[i]]
        print(f"  dbg spawn{a} er-comp: {len(xs)} cells bbox x{min(xs)}-{max(xs)} z{min(zs)}-{max(zs)}", flush=True)
    allok &= ok
for (pa2, pb2, ax2, az2, bx2, bz2) in bridges:
    mx_, mz_ = (ax2+bx2)//2, (az2+bz2)//2
    print(f"  dbg bridge P{pa2}-P{pb2}: ({ax2},{az2})->({bx2},{bz2}) "
          f"er@mid={er[mz_*G+mx_]} walk@mid={m0[mz_*G+mx_]}", flush=True)
# bridges are the only land links: cutting ALL bridge cells must fragment the land
m_cut = bytearray(m0)
for i in range(G*G):
    if bridge_mask[i]: m_cut[i] = 0
s1c = snapc(m_cut, *SPAWNS[1])
seen_c = flood(m_cut, s1c)
frag = sum(1 for a in range(2, 7)
           if (lambda t3: t3 is None or not seen_c[t3])(snapc(m_cut, *SPAWNS[a])))
print(f"bridges-cut isolation: {frag}/5 other spawns unreachable (want >=4)", flush=True)
allok &= frag >= 4
# markers on walkable ground
bad_mk = 0
for a_, c_ in re.findall(r"VECTOR3\(\s*([\d.eE+-]+)\s*,\s*[\d.eE+-]+\s*,\s*([\d.eE+-]+)\s*\)", sav):
    mx, mzz = int(float(a_)), int(float(c_))
    if not (0 <= mx < G and 0 <= mzz < G) or not m0[mzz*G + mx]:
        bad_mk += 1
print(f"markers off-land: {bad_mk} (want 0)", flush=True)
allok &= bad_mk == 0
# mesh gate: dry walkable cells, no vert above walking height
p3, b3, nv3, _ = sm.locate_mesh_blob(terr_new)
worst = -99.0; worst_at = None
for i in range(nv3):
    x, y, z = struct.unpack_from("<3f", p3, b3 + 20 + 32*i)
    if 0 <= x < G and 0 <= z < G and m0[int(z)*G + int(x)]:
        if y < 5.0 or abs(y - WL) < 1.2:      # skirt / water sheet (edit-pass skips)
            continue
        ox, oy, oz = struct.unpack_from("<3f", p_orig, b_orig + 20 + 32*i)
        if oy > sm.hf_sample(H0, w, ox, oz) + 8.0: continue
        d = y - sm.hf_sample(Hn, w, x, z)
        if d > worst: worst, worst_at = d, (round(x), round(z), round(y, 1))
print(f"mesh gate: worst above ground {worst:.2f} at {worst_at}", flush=True)
allok &= worst < 4.0
# collision gate
cp2 = sm.read_bdf_payload(col_new)
worst_c = -99.0
for i in range(cnv):
    x, y, z = struct.unpack_from("<3f", cp2, cvoff + 12*i)
    if 0 <= x < G and 0 <= z < G:
        worst_c = max(worst_c, y - sm.hf_sample(Hn, w, x, z))
print(f"collision worst above: {worst_c:.2f}", flush=True)
allok &= worst_c < 0.0
# wd gate: sampled dry cells decode dry, sampled sea decodes water
wh, ww = struct.unpack_from("<II", wd, 12)
scale = G // ww
def alpha_mean(d, bx, bz):
    off = 128 + (bz * (ww//4) + bx) * 16
    a0, a1 = d[off], d[off+1]
    bits = int.from_bytes(d[off+2:off+8], "little")
    pal = ([a0, a1] + [((7-i)*a0 + i*a1)//7 for i in range(1, 7)]) if a0 > a1 else \
          ([a0, a1] + [((5-i)*a0 + i*a1)//5 for i in range(1, 5)] + [0, 255])
    return sum(pal[(bits >> (3*i)) & 7] for i in range(16)) // 16
bad_wd = 0
for i in range(0, G*G, 131):
    x, z = i % G, i // G
    a = alpha_mean(wd, (x//scale)//4, (z//scale)//4)
    if dry_mask[i] and a >= 40: bad_wd += 1
print(f"wd sampled dry cells water-classified: {bad_wd} (want 0)", flush=True)
allok &= bad_wd == 0
sea_a = alpha_mean(wd, (60//scale)//4, (60//scale)//4)
print(f"wd open sea alpha: {sea_a} (want water >=40)", flush=True)
allok &= sea_a >= 40
# v2 gates: arena size, east peninsula width, growth factors, island count
flat_arena = sum(1 for i in cent_set
                 if abs(Hn[(i//G)*w + (i % G)]/128.0 - ARENA_H) <= 0.2)
print(f"arena flat cells: {flat_arena} (want >= 45000)", flush=True)
allok &= flat_arena >= 45000
e_rows = defaultdict(int)
for i in islands[east_id]:
    e_rows[i // G] += 1
zs_sorted = sorted(e_rows)
mid_rows = zs_sorted[len(zs_sorted)//10 : -len(zs_sorted)//10 or None]
min_wid = min(e_rows[zr] for zr in mid_rows)
print(f"east peninsula min mid-row width: {min_wid} (want >= 60)", flush=True)
allok &= min_wid >= 60
for pid in sorted(spawn_ids):
    ratio = len(islands[pid]) / v1_sizes[pid]
    print(f"spawn island P{pid} growth: x{ratio:.2f} (want >= 3.8)", flush=True)
    allok &= ratio >= 3.8
n_comp = 0
seen_ic = bytearray(G*G)
for i in range(G*G):
    if isl_mask[i] and not seen_ic[i]:
        n_comp += 1
        q = deque([i]); seen_ic[i] = 1
        while q:
            j = q.popleft(); x, z = j % G, j // G
            for dx, dz in ((1,0),(-1,0),(0,1),(0,-1)):
                nx, nz = x+dx, z+dz
                if 0 <= nx < G and 0 <= nz < G and isl_mask[nz*G+nx] and not seen_ic[nz*G+nx]:
                    seen_ic[nz*G+nx] = 1; q.append(nz*G+nx)
print(f"island components: {n_comp} (want {len(islands)} - no merges)", flush=True)
if n_comp != len(islands):
    comp_of = {}
    seen_dbg = bytearray(G*G)
    cid = 0
    for i in range(G*G):
        if isl_mask[i] and not seen_dbg[i]:
            cid += 1
            qd = deque([i]); seen_dbg[i] = 1
            while qd:
                j = qd.popleft(); comp_of[j] = cid
                x, z = j % G, j // G
                for dx, dz in ((1,0),(-1,0),(0,1),(0,-1)):
                    nx, nz = x+dx, z+dz
                    if 0 <= nx < G and 0 <= nz < G and isl_mask[nz*G+nx] and not seen_dbg[nz*G+nx]:
                        seen_dbg[nz*G+nx] = 1; qd.append(nz*G+nx)
    by_comp = defaultdict(set)
    for pid, cells in islands.items():
        for i in cells[::37]:
            by_comp[comp_of.get(i, -1)].add(pid)
    for c2, pids2 in sorted(by_comp.items()):
        if len(pids2) > 1:
            print(f"  dbg MERGED component {c2}: islands {sorted(pids2)}", flush=True)
allok &= n_comp == len(islands)
# amph unity: open sea and islands share one amph island (beaches open)
offB_a = layers[amph_L[0]][4]
isl_sea_a = pay[offB_a + 60*G + 60]
isl_isl_a = pay[offB_a + SPAWNS[1][1]*G + SPAWNS[1][0]]
print(f"amph islands: sea={isl_sea_a} island={isl_isl_a} "
      f"{'OK' if isl_sea_a == isl_isl_a else 'FAIL'}", flush=True)
allok &= isl_sea_a == isl_isl_a
# bridge decks stay amph-crossable up the middle
open_mid = sum(1 for i in range(G*G) if bridge_mask[i] and pay[layers[amph_L[0]][2] + i] != 255)
tot_br = sum(1 for i in range(G*G) if bridge_mask[i])
print(f"bridge amph-open cells: {open_mid}/{tot_br}", flush=True)
allok &= tot_br > 0 and open_mid > tot_br // 3
# mass pad flatness + walkability in the FINAL data
bad_pad = 0
for nm_, xi, zi in mass_pads:
    cells = [Hn[z*w+x] for z in range(zi-5, zi+6) for x in range(xi-5, xi+6)]
    if (max(cells) - min(cells)) / 128.0 > 0.15 or not m0[zi*G + xi]:
        bad_pad += 1
        print(f"  BAD pad {nm_} at ({xi},{zi})", flush=True)
print(f"mass pads bad: {bad_pad} (want 0)", flush=True)
allok &= bad_pad == 0
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
if os.path.exists(OUT):
    bdir = os.path.join(os.path.dirname(sm.GAMEDATA), "gamedata_backups")
    os.makedirs(bdir, exist_ok=True)
    bak = os.path.join(bdir, os.path.basename(OUT) + ".bak")
    if not os.path.exists(bak):
        shutil.copy2(OUT, bak)
buf = io.BytesIO(); zo = zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED)
for n, d in sorted(out.items()): zo.writestr(n, d)
zo.close()
open(OUT, "wb").write(buf.getvalue())
print(f"INSTALLED ({os.path.getsize(OUT):,} B, {len(out)} entries)", flush=True)
