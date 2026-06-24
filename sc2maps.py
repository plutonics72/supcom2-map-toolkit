"""
sc2maps — a reusable toolkit for making Supreme Commander 2 custom maps.

SC2 shipped no map editor. This library is the distilled result of reverse-
engineering the map file formats (the BDF containers, the heightfield, the water
mask, and — the hard one — the navigation mesh inside costs.win.bdf). With it you
can: read any shipped terrain, score terrains by openness, design a skirmish
layout, OPTIONALLY patch the navmesh so campaign terrain becomes fully playable,
and package + install a working .scd — all from a single spec.

Everything here is stdlib-only (zipfile, zlib, struct) — no numpy/pandas
(pandas import is multi-minute on this machine due to Defender).

------------------------------------------------------------------------------
FILE FORMATS (all cracked; offsets verified byte-exact on 5+ maps)
------------------------------------------------------------------------------
BDF container (.hfield/.costs/.collision2/.terrain/.info/.mapobjs/.decals .win.bdf):
  "MFDB" + u32[ver=7, 2, 0, comp_size, decomp_size, n_fixups, ...fixup table...]
  then ONE zlib stream (0x78 0x9C). Robust: scan for 78 9C from offset 4,
  first that decompresses wins. Rebuild: keep header+fixups verbatim, patch
  comp_size@0x10 + decomp_size@0x14, append zlib.compress(payload, 6) (level 6
  reproduces the 78 9C header; level 9 = 78 DA, avoid), pad file to 32 bytes.

hfield payload: u32[ver=2, w=1025, h=1025, n, dataoff=28] then u16 heightmap,
  Z-MAJOR (idx = z*1025 + x), world_y = raw/128.0. Coords are world units 0..1024.

waterDepth.dds: 512x512 DXT5. A cell is water if the block's DXT5 alpha endpoints
  max(a0,a1) > 32 (a0,a1 = first two bytes of the 16-byte block).

costs.win.bdf payload (THE NAVMESH): u32[ver=2, n_layers, table_off=12], then
  n_layers * 28-byte records: (layer_id, 1048576, offA, 1048576, offB, countS, offS).
  Per layer: costA = u8[1024*1024] Z-MAJOR cost grid (1=clear, 255=BLOCKED,
  2..254=slope/obstacle penalty); islandB = u8[1024*1024] connected-region id
  (255=none); bbox table = countS u32 = 4 per island (minx,minz,maxx,maxz).
  n_layers: 3 on land-only maps, 5 on water maps. On water maps the LAND-only
  class is the layer with the fewest underwater-navigable cells (use land_layer()).
  NAVIGABILITY RULE (confirmed in-game): cell navigable iff costA != 255.
  Engine reads this PRE-BAKED grid and does NOT re-bake from markers at load —
  so patching costA/islandB and shipping the patched bdf MAKES units move.

Deployment (two modes, both proven by shipped community packs + our maps):
  REMIX  — scenario map= points at a stock, already-navigable terrain folder;
           ship only save/scenario/script(+minimap). Use on skirmish terrains.
  PATCH  — copy the full terrain bdf set under a NEW id, patch its costs.win.bdf,
           ship everything. Required for campaign terrains (tiny baked navmesh).
"""

import os, re, zipfile, zlib, struct, shutil, math
from collections import deque

# ---------------------------------------------------------------------------
# Paths — auto-detect the SupCom 2 gamedata folder (override via env SC2_GAMEDATA)
# ---------------------------------------------------------------------------
def _find_gamedata():
    env = os.environ.get("SC2_GAMEDATA")
    if env and os.path.isdir(env):
        return env
    rel = os.path.join("steamapps", "common", "Supreme Commander 2", "gamedata")
    roots = [
        r"C:\Program Files (x86)\Steam", r"C:\Program Files\Steam",
        r"D:\Steam", r"D:\SteamLibrary", r"E:\SteamLibrary", r"C:\SteamLibrary",
        os.path.expanduser("~/.steam/steam"),                       # Linux
        os.path.expanduser("~/.local/share/Steam"),                 # Linux
        os.path.expanduser("~/Library/Application Support/Steam"),  # macOS
    ]
    for r in roots:
        p = os.path.join(r, rel)
        if os.path.isdir(p):
            return p
    return os.path.join(roots[0], rel)  # default; set SC2_GAMEDATA if this is wrong

GAMEDATA = _find_gamedata()
MAPS_SCD = os.path.join(GAMEDATA, "maps.scd")
DLC_SCD = os.path.join(GAMEDATA, "z_dlc1.scd")
UNCOMPILED_SCD = os.path.join(GAMEDATA, "uncompiled_lua.scd")
GRID = 1024  # nav/cost grid is 1024x1024; hfield is 1025x1025

# The boilerplate map script (camera + music); identical across skirmish maps.
SCRIPT_LUA = """function OnPopulate()
\timport('/lua/sim/ScenarioUtilities.lua').InitializeArmies()
\timport('/lua/sim/ScenarioFramework.lua').SetPlayableArea('AREA_1')
end

function OnStart(self)
\tlocal strMusic = 'SC2/MUSIC/MP/Conditional_Music'
\tSync.PlayMusic = strMusic
end
"""

# ---------------------------------------------------------------------------
# BDF container
# ---------------------------------------------------------------------------
def find_zlib_stream(data):
    """Offset of the (first valid) zlib stream in an MFDB file."""
    o = data.find(b"\x78\x9c", 4)
    while o != -1:
        try:
            zlib.decompress(data[o:]); return o
        except Exception:
            o = data.find(b"\x78\x9c", o + 1)
    raise ValueError("no zlib stream found")

def read_bdf_payload(data):
    """Decompressed payload of an MFDB file."""
    return zlib.decompress(data[find_zlib_stream(data):])

def rebuild_bdf(orig_bytes, new_payload):
    """Rebuild an MFDB file around a modified payload (header+fixups kept verbatim,
    sizes patched, level-6 zlib, padded to 32). Payload size may change."""
    so = find_zlib_stream(orig_bytes)
    comp = zlib.compress(bytes(new_payload), 6)
    assert comp[:2] == b"\x78\x9c", "expected 78 9C zlib header"
    out = bytearray(orig_bytes[:so])
    struct.pack_into("<I", out, 0x10, len(comp))          # compressed_size
    struct.pack_into("<I", out, 0x14, len(new_payload))   # decompressed_size
    out += comp
    while len(out) % 32:
        out += b"\x00"
    return bytes(out)

# ---------------------------------------------------------------------------
# Archive helpers
# ---------------------------------------------------------------------------
def _scd_for(map_id):
    """Which archive holds a given terrain id (maps.scd or the DLC pack)."""
    for scd in (MAPS_SCD, DLC_SCD):
        with zipfile.ZipFile(scd) as zf:
            if any(n.endswith(f"{map_id}.hfield.win.bdf") for n in zf.namelist()):
                return scd
    raise FileNotFoundError(f"terrain {map_id} not found in maps.scd or z_dlc1.scd")

def read_entry(scd, suffix):
    """Read the first archive entry whose name ends with `suffix`."""
    with zipfile.ZipFile(scd) as zf:
        names = [n for n in zf.namelist() if n.endswith(suffix)]
        if not names:
            return None
        return zf.read(names[0])

# ---------------------------------------------------------------------------
# Terrain — loads heightfield, water mask, and the navmesh for a stock map id
# ---------------------------------------------------------------------------
class Terrain:
    TERRAIN_FILES = ("hfield.win.bdf", "costs.win.bdf", "collision2.win.bdf",
                     "terrain.win.bdf", "info.win.bdf", "mapobjs.win.bdf",
                     "decals.win.bdf", "minimap.win.dds", "waterDepth.dds")

    def __init__(self, map_id):
        self.id = map_id
        self.scd = _scd_for(map_id)
        self.raw = {}
        with zipfile.ZipFile(self.scd) as zf:
            names = zf.namelist()
            for k in self.TERRAIN_FILES:
                hit = [n for n in names if n.endswith(f"{map_id}.{k}")]
                self.raw[k] = zf.read(hit[0]) if hit else None
            self.lighting = {n: zf.read(n) for n in names
                             if f"{map_id}\\lighting\\" in n or f"{map_id}/lighting/" in n}
        # heightfield
        hp = read_bdf_payload(self.raw["hfield.win.bdf"])
        _, self.HW, self.HH, _, hdat = struct.unpack_from("<5I", hp, 0)
        self.H = struct.unpack_from(f"<{self.HW*self.HH}H", hp, hdat)
        # water mask (optional)
        self._wd = self.raw["waterDepth.dds"][128:] if self.raw["waterDepth.dds"] else None
        # costs / navmesh
        self.costs_payload = bytearray(read_bdf_payload(self.raw["costs.win.bdf"]))
        _, self.n_layers, toff = struct.unpack_from("<3I", self.costs_payload, 0)
        self.layers = [struct.unpack_from("<7I", self.costs_payload, toff + 28*i)
                       for i in range(self.n_layers)]

    def set_hfield(self, hfield_bytes):
        """Swap in a (sculpted) heightfield and re-parse heights, so all queries +
        nav/placement use the NEW terrain. Pair with reshape_hfield()."""
        self.raw["hfield.win.bdf"] = hfield_bytes
        hp = read_bdf_payload(hfield_bytes)
        _, self.HW, self.HH, _, hdat = struct.unpack_from("<5I", hp, 0)
        self.H = struct.unpack_from(f"<{self.HW*self.HH}H", hp, hdat)

    # --- height / water queries (world coords) ---
    def y(self, x, z):
        return self.H[round(z) * self.HW + round(x)] / 128.0

    def hraw(self, x, z):
        return self.H[round(z) * self.HW + round(x)]

    def slope_raw(self, x, z):
        x, z = round(x), round(z)
        c = self.H[z*self.HW + x]
        return max(abs(self.H[z*self.HW+x+1]-c), abs(self.H[z*self.HW+x-1]-c),
                   abs(self.H[(z+1)*self.HW+x]-c), abs(self.H[(z-1)*self.HW+x]-c))

    def dry(self, x, z):
        if self._wd is None:
            return True
        ti = min(511, round(x) * 511 // 1024); tj = min(511, round(z) * 511 // 1024)
        o = ((tj // 4) * 128 + (ti // 4)) * 16
        return max(self._wd[o], self._wd[o + 1]) <= 32

    # --- navmesh queries ---
    def cost(self, x, z, layer):
        return self.costs_payload[self.layers[layer][2] + round(z)*GRID + round(x)]

    def navok(self, x, z, layer=None):
        """Navigable on a layer (default: the land layer)? cost != 255."""
        L = self.land_layer() if layer is None else layer
        return self.cost(x, z, L) != 255

    def land_layer(self):
        """Index of the land-only movement layer (fewest underwater-navigable cells)."""
        if self.n_layers == 3:
            return 0
        best, bi = 1e18, 0
        for li, rec in enumerate(self.layers):
            oA = rec[2]; under = 0
            for z in range(0, GRID, 4):
                for x in range(0, GRID, 4):
                    if self.costs_payload[oA + z*GRID + x] != 255 and not self.dry(x, z):
                        under += 1
            if under < best:
                best, bi = under, li
        return bi

    # --- connected navigable regions on the shipped (unpatched) terrain ---
    def nav_component(self, seed_x, seed_z, layer=None):
        L = self.land_layer() if layer is None else layer
        oA = self.layers[L][2]
        comp = bytearray(GRID*GRID); s = round(seed_z)*GRID + round(seed_x)
        if self.costs_payload[oA + s] == 255:
            return comp, 0
        q = deque([s]); comp[s] = 1; n = 1
        while q:
            i = q.popleft(); x, z = i % GRID, i // GRID
            for dx, dz in ((1,0),(-1,0),(0,1),(0,-1)):
                nx, nz = x+dx, z+dz
                if 0 <= nx < GRID and 0 <= nz < GRID:
                    j = nz*GRID+nx
                    if not comp[j] and self.costs_payload[oA+j] != 255:
                        comp[j] = 1; n += 1; q.append(j)
        return comp, n

    def openness(self, layer=None):
        """Largest connected navigable fraction of the map (0..1), on shipped data."""
        L = self.land_layer() if layer is None else layer
        oA = self.layers[L][2]; seen = bytearray(GRID*GRID); best = 0
        for s in range(0, GRID*GRID, 5):
            if self.costs_payload[oA+s] != 255 and not seen[s]:
                q = deque([s]); seen[s] = 1; n = 1
                while q:
                    i = q.popleft(); x, z = i % GRID, i // GRID
                    for dx, dz in ((1,0),(-1,0),(0,1),(0,-1)):
                        nx, nz = x+dx, z+dz
                        if 0 <= nx < GRID and 0 <= nz < GRID:
                            j = nz*GRID+nx
                            if not seen[j] and self.costs_payload[oA+j] != 255:
                                seen[j] = 1; n += 1; q.append(j)
                best = max(best, n)
        return best / (GRID*GRID)

# ---------------------------------------------------------------------------
# Navmesh patching — open dry/gentle terrain, carve causeways, verify
# ---------------------------------------------------------------------------
def dry_gentle_mask(terrain, max_slope_world=6.0, water_margin=4.0):
    """1024x1024 bytearray: 1 where the cell is dry land of walkable slope."""
    dry_raw = int((_water_level(terrain) + water_margin) * 128)
    slope_raw = int(max_slope_world * 128)
    m = bytearray(GRID*GRID)
    for z in range(1, GRID-1):
        for x in range(1, GRID-1):
            if terrain.H[z*terrain.HW + x] > dry_raw and terrain.slope_raw(x, z) < slope_raw:
                m[z*GRID + x] = 1
    return m

def carve_box(terrain, mask, x0, x1, z0, z1, min_world=10.0):
    """Force a rectangular corridor navigable (a causeway/ford), excluding void."""
    thr = int(min_world * 128); added = 0
    for z in range(z0, z1):
        for x in range(x0, x1):
            if terrain.H[z*terrain.HW + x] > thr and not mask[z*GRID + x]:
                mask[z*GRID + x] = 1; added += 1
    return added

def component_of(mask, seed_x, seed_z):
    """4-connected component of `mask` containing the seed (snaps seed to nearest set cell)."""
    comp = bytearray(GRID*GRID); sx, sz = round(seed_x), round(seed_z)
    if not mask[sz*GRID + sx]:
        for r in range(1, 60):
            done = False
            for dx in range(-r, r+1):
                for dz in (-r, r):
                    for cx, cz in ((sx+dx, sz+dz), (sx+dz, sz+dx)):
                        if 0 <= cx < GRID and 0 <= cz < GRID and mask[cz*GRID+cx]:
                            sx, sz = cx, cz; done = True; break
                    if done: break
                if done: break
            if done: break
    q = deque([sz*GRID+sx]); comp[sz*GRID+sx] = 1; n = 1
    while q:
        i = q.popleft(); x, z = i % GRID, i // GRID
        for dx, dz in ((1,0),(-1,0),(0,1),(0,-1)):
            nx, nz = x+dx, z+dz
            if 0 <= nx < GRID and 0 <= nz < GRID:
                j = nz*GRID+nx
                if mask[j] and not comp[j]:
                    comp[j] = 1; n += 1; q.append(j)
    return comp, n

def patch_costs(terrain, component):
    """Open `component` (cost=1) on every layer, then REBUILD each layer's island grid
    and bbox table so the navigation metadata is fully self-consistent. Returns the
    rebuilt costs.win.bdf bytes + the patched payload.

    Consistency matters for MULTIPLAYER: SC2 runs a lockstep simulation, and malformed
    pathfinding metadata (navigable cells with stale/leftover island ids, navigable-but-
    island-less cells, overlapping/stale bboxes) is a classic desync trigger — single-
    player tolerates it, multiplayer diverges. _recompute_islands fixes that."""
    payload = bytearray(terrain.costs_payload)
    cells = [i for i in range(GRID*GRID) if component[i]]
    for (_lid, _cA, oA, _cB, oB, _cS, oS) in terrain.layers:
        for i in cells:
            payload[oA + i] = 1                  # open the play area on every layer
    _recompute_islands(payload, terrain.layers)  # consistent island grid + bbox per layer
    return rebuild_bdf(terrain.raw["costs.win.bdf"], payload), payload

def _recompute_islands(payload, layers):
    """Rewrite each layer's island grid + bbox table to exactly match its cost grid:
    island id = connected-component label of navigable (cost!=255) cells, largest = 0.
    The bbox table can't be resized (it sits mid-payload with container fixups pointing
    past it), so components beyond the table's capacity are blocked (cost=255) — these
    are tiny unreachable patches. Result: cost!=255  <=>  island in 0..k-1, every island
    contiguous with a correct bbox, no orphans. (Slower: a BFS over each layer.)"""
    import array
    N = GRID * GRID
    for (_lid, _cA, oA, _cB, oB, cS, oS) in layers:
        cap = cS // 4
        if cap < 1:
            continue
        lbl = array.array('i', bytes(4 * N))     # 0 = unvisited/blocked
        comps = []                               # [size, x0, z0, x1, z1, temp_id]
        cid = 0
        for s in range(N):
            if payload[oA + s] != 255 and lbl[s] == 0:
                cid += 1
                q = deque([s]); lbl[s] = cid
                size = 0; x0 = x1 = s % GRID; z0 = z1 = s // GRID
                while q:
                    i = q.popleft(); size += 1
                    x = i % GRID; z = i // GRID
                    if x < x0: x0 = x
                    if x > x1: x1 = x
                    if z < z0: z0 = z
                    if z > z1: z1 = z
                    if x > 0 and payload[oA+i-1] != 255 and lbl[i-1] == 0:
                        lbl[i-1] = cid; q.append(i-1)
                    if x < GRID-1 and payload[oA+i+1] != 255 and lbl[i+1] == 0:
                        lbl[i+1] = cid; q.append(i+1)
                    if z > 0 and payload[oA+i-GRID] != 255 and lbl[i-GRID] == 0:
                        lbl[i-GRID] = cid; q.append(i-GRID)
                    if z < GRID-1 and payload[oA+i+GRID] != 255 and lbl[i+GRID] == 0:
                        lbl[i+GRID] = cid; q.append(i+GRID)
                comps.append([size, x0, z0, x1, z1, cid])
        comps.sort(key=lambda c: -c[0])
        keep = {c[5]: rank for rank, c in enumerate(comps[:cap])}
        for i in range(N):
            if payload[oA + i] == 255:
                payload[oB + i] = 255
            else:
                r = keep.get(lbl[i])
                if r is None:                    # excess tiny component -> block (consistent)
                    payload[oA + i] = 255; payload[oB + i] = 255
                else:
                    payload[oB + i] = r
        for rank in range(cap):
            if rank < len(comps):
                _, x0, z0, x1, z1, _ = comps[rank]
                struct.pack_into("<4I", payload, oS + 16*rank, x0, z0, x1, z1)
            else:
                struct.pack_into("<4I", payload, oS + 16*rank, 0, 0, 0, 0)

def reachable(payload, layers, a, b, layer=0):
    """BFS over a costs payload: can land units walk from a to b?"""
    oA = layers[layer][2]; seen = bytearray(GRID*GRID)
    s = round(a[1])*GRID + round(a[0]); t = round(b[1])*GRID + round(b[0])
    if payload[oA+s] == 255:
        return False
    q = deque([s]); seen[s] = 1
    while q:
        i = q.popleft()
        if i == t:
            return True
        x, z = i % GRID, i // GRID
        for dx, dz in ((1,0),(-1,0),(0,1),(0,-1)):
            nx, nz = x+dx, z+dz
            if 0 <= nx < GRID and 0 <= nz < GRID:
                j = nz*GRID+nx
                if not seen[j] and payload[oA+j] != 255:
                    seen[j] = 1; q.append(j)
    return seen[t] == 1

def _water_level(terrain):
    return TERRAINS.get(terrain.id, {}).get("water_y", 0.0)

# ---------------------------------------------------------------------------
# Lua generation (markers, save, scenario)
# ---------------------------------------------------------------------------
def _fmt(v):
    return f"{v:.3f}".rstrip("0").rstrip(".")

def marker(name, mtype, x, z, y, color="ff800080", prop="", extra=""):
    return (f"                ['{name}'] = {{\n{extra}"
            f"                    ['color'] = STRING( '{color}' ),\n"
            f"                    ['type'] = STRING( '{mtype}' ),\n"
            f"                    ['prop'] = STRING( '{prop}' ),\n"
            f"                    ['orientation'] = QUATERNION( 0, 0, 0, 1 ),\n"
            f"                    ['position'] = VECTOR3( {x}, {_fmt(y)}, {z} ),\n"
            f"                }},\n")

def mass_marker(name, x, z, y):
    extra = ("                    ['size'] = FLOAT( 6.0 ),\n"
             "                    ['amount'] = FLOAT( 100.0 ),\n"
             "                    ['resource'] = BOOLEAN( true ),\n")
    return marker(name, "Mass", x, z, y, color="ff008000", extra=extra)

def armies_tail(n_armies):
    """The Chains + Armies Lua blocks for n_armies (+ARMY_EXTRA), lifted from a stock
    skirmish save (MP_204 for <=4 armies, MP_206 for 5-6)."""
    src = "SC2_MP_206_save.lua" if n_armies > 4 else "SC2_MP_204_save.lua"
    txt = read_entry(UNCOMPILED_SCD, src).decode("utf8", "ignore")
    tail = txt[txt.index("    Chains = {"):]
    for a in list(range(1, n_armies+1)) + ["EXTRA"]:
        assert f"['ARMY_{a}']" in tail, f"ARMY_{a} missing in {src}"
    return tail

def make_save(markers_blob, n_armies, area=(40, 40, 984, 984)):
    """area = the playable-area rectangle (x0, z0, x1, z1) in world/cell units. The default insets
    40 cells from a 1024 map edge; widen it (e.g. (8, 8, 1016, 1016)) to expose more of the rendered
    terrain when the usable ground runs right to the edge."""
    return ("--[[ generated by sc2maps ]]--\nScenario = {\n    Props = {\n    },\n"
            "    Areas = {\n        ['AREA_1'] = {\n"
            f"            ['rectangle'] = RECTANGLE( {area[0]}, {area[1]}, {area[2]}, {area[3]} ),\n        }},\n    }},\n"
            "    MasterChain = {\n        ['_MASTERCHAIN_'] = {\n            Markers = {\n"
            + markers_blob +
            "            },\n        },\n    },\n" + armies_tail(n_armies))

def make_scenario(scenario_id, terrain_id, name, spawns, norush=70.0, reverb=None):
    """spawns: {army_index: (x, y, z)}. terrain_id is the folder whose .scmap/.bdf
    the engine loads (== scenario_id for PATCH maps, a stock id for REMIX maps)."""
    armies = sorted(spawns)
    offs = "".join(f"    norushoffsetX_ARMY_{a} = 0.000000,\n    norushoffsetY_ARMY_{a} = 0.000000,\n"
                   for a in armies)
    starts = "".join(f"                ['ARMY_{a}'] = {{ {_fmt(spawns[a][0])}, {_fmt(spawns[a][1])}, {_fmt(spawns[a][2])} }},\n"
                     for a in armies)
    alist = ",".join(f"'ARMY_{a}'" for a in armies)
    return f"""version = 3
ScenarioInfo = {{
    devname = '{scenario_id}',
    name = '{name}',
    description = '{terrain_id}',
    type = 'skirmish',
    starts = true,
    preview = '',
    reverbPreset = '{reverb or terrain_id}',
    size = {{1024, 1024}},
    map = '/maps/{terrain_id}/{terrain_id}.scmap',
    save = '/maps/{scenario_id}/{scenario_id}_save.lua',
    script = '/maps/{scenario_id}/{scenario_id}_script.lua',
    norushradius = {norush:.6f},
{offs}    StartPositions = {{
{starts}    }},
    Configurations = {{
        ['standard'] = {{
            teams = {{
                {{ name = 'FFA', armies = {{{alist},}} }},
            }},
            customprops = {{ ['ExtraArmies'] = STRING( 'ARMY_EXTRA' ), }},
        }},
    }}}}
"""

# ---------------------------------------------------------------------------
# Minimap (DXT1) from heightfield
# ---------------------------------------------------------------------------
def _pack565(r, g, b):
    return ((r >> 3) << 11) | ((g >> 2) << 5) | (b >> 3)

def write_minimap_dds(terrain, palette):
    """Build a 1024x1024 DXT1 minimap. `palette(x,z)->(r,g,b)` colors each block."""
    ref = terrain.raw["minimap.win.dds"]
    assert ref and ref[84:88] == b"DXT1", "donor minimap is not DXT1"
    blocks = bytearray()
    for bz in range(0, 1024, 4):
        for bx in range(0, 1024, 4):
            rs = gs = bs = 0
            for dz in (0, 2):
                for dx in (0, 2):
                    r, g, b = palette(bx+dx, bz+dz)
                    rs += r; gs += g; bs += b
            c = _pack565(rs//4, gs//4, bs//4)
            blocks += struct.pack("<HHI", c, c, 0)
    return ref[:128] + bytes(blocks)

def desert_palette(terrain):
    def pal(x, z):
        if not terrain.dry(x, z): return (60, 110, 160)
        v = terrain.y(x, z)
        if v < 50: return (172, 144, 96)
        if v < 130: return (210, 186, 132)
        return (168, 138, 96)
    return pal

def snow_palette(terrain):
    """White/ice minimap to match a snow re-skin (colours by height: floor->rim)."""
    def pal(x, z):
        v = terrain.y(x, z)
        if v < 30: return (210, 220, 230)
        if v < 90: return (188, 200, 214)
        return (150, 164, 182)
    return pal

def dark_palette(terrain):
    """Dark-grey minimap to match a scorched-rock / dark re-skin."""
    def pal(x, z):
        v = terrain.y(x, z)
        if v < 30: return (66, 68, 72)
        if v < 90: return (54, 56, 60)
        return (40, 42, 46)
    return pal

# ---------------------------------------------------------------------------
# Water editing — water-level field, water mask, heightfield pond carving.
#
# FINDING (in-game tested): you CANNOT add water to a map that was authored dry.
# Setting the water level + writing a waterDepth.dds + carving the heightfield on a
# dry map (e.g. Emerald Crater) renders NO water — the engine only renders water that
# was baked in by GPG's map compiler (a water surface entity the dry map lacks). These
# functions only affect maps that already have a baked water system; to get water,
# build on a terrain that already has it (see TERRANS / Terrain water-layer detection).
# Kept for reference + for tweaking already-watered maps.
# ---------------------------------------------------------------------------
WATER_LEVEL_OFFSET = 216   # byte offset of the water-level float32 in the info.win.bdf payload

def set_water_level(info_bytes, level, offset=WATER_LEVEL_OFFSET):
    """Return info.win.bdf rebuilt with the water-level float set to `level` (world units)."""
    payload = bytearray(read_bdf_payload(info_bytes))
    struct.pack_into("<f", payload, offset, float(level))
    return rebuild_bdf(info_bytes, payload)

def write_waterdepth_dds(terrain, water_level, header, is_water=None, depth_scale=40.0):
    """Build a 512x512 DXT5 waterDepth.dds. `header` = a 128-byte DXT5 DDS header from a
    watered donor map. A cell is water where is_water(x,z) is True (default: terrain below
    water_level); the DXT5 ALPHA encodes depth so Terrain.dry reads it (max(a0,a1)>32) and the
    engine renders water there. Dry blocks get alpha 0. Each 16-byte block is uniform alpha+color."""
    if is_water is None:
        is_water = lambda x, z: terrain.y(x, z) < water_level
    blocks = bytearray()
    for bj in range(128):
        for bi in range(128):
            tx = bi * 4 + 2; tz = bj * 4 + 2                       # block-centre texel (512 res)
            wx = min(1024, tx * 1024 // 511); wz = min(1024, tz * 1024 // 511)
            if is_water(wx, wz):
                a = max(33, min(255, int((water_level - terrain.y(wx, wz)) * depth_scale) + 80))
            else:
                a = 0
            blocks += bytes((a, a, 0, 0, 0, 0, 0, 0))             # DXT5 alpha: uniform a, indices 0
            blocks += struct.pack("<HHI", 0, 0, 0)                # DXT5 colour: uniform, indices 0
    return bytes(header[:128]) + bytes(blocks)

def carve_ponds(hfield_bytes, ponds, depth_to_y):
    """Lower the heightfield inside each pond disc to `depth_to_y` (world units), creating
    shallow basins. ponds = [(cx, cz, radius), ...] in world cells. Returns rebuilt hfield bytes."""
    payload = bytearray(read_bdf_payload(hfield_bytes))
    _, w, h, _, hdat = struct.unpack_from("<5I", payload, 0)
    target = int(depth_to_y * 128)
    for (cx, cz, r) in ponds:
        for dz in range(-r, r + 1):
            for dx in range(-r, r + 1):
                if dx * dx + dz * dz <= r * r:
                    x, z = cx + dx, cz + dz
                    if 0 <= x < w and 0 <= z < h:
                        idx = hdat + (z * w + x) * 2
                        if struct.unpack_from("<H", payload, idx)[0] > target:
                            struct.pack_into("<H", payload, idx, target)
    return rebuild_bdf(hfield_bytes, payload)

def reshape_hfield(hfield_bytes, ops):
    """Sculpt the heightfield (PROVEN to change the visible terrain in-game). `ops` is a list:
      ("disc", cx, cz, r, y, mode)              flat disc of radius r set to height y
      ("cone", cx, cz, r, peak_y, mode)         smooth cone: peak_y at centre, easing to edge
      ("rect", x0, z0, x1, z1, y, mode)         flat rectangle
      ("ramp", x0, z0, x1, z1, y_lo, y_hi, mode) graded slab: height interpolates linearly along
                                                the longer axis from y_lo (low-coord end) to y_hi
                                                (high-coord end). A gentle ramp (small dy / long
                                                run) stays WALKABLE; use it to connect a steep
                                                plateau top down to the floor.
    mode: "set" overwrite | "raise" only where it lifts terrain | "lower" only where it drops it.
    Flat-topped discs/rects get steep (blocked) walls = obstacles; broad cones + ramps are gentle
    (walkable) highground/access. Returns rebuilt hfield.win.bdf bytes (pair with set_hfield)."""
    payload = bytearray(read_bdf_payload(hfield_bytes))
    _, w, h, _, hd = struct.unpack_from("<5I", payload, 0)
    def get(x, z): return struct.unpack_from("<H", payload, hd+(z*w+x)*2)[0]
    def put(x, z, y, mode):
        if not (0 <= x < w and 0 <= z < h): return
        raw = max(0, min(65535, int(y*128))); cur = get(x, z)
        if mode == "raise" and raw <= cur: return
        if mode == "lower" and raw >= cur: return
        struct.pack_into("<H", payload, hd+(z*w+x)*2, raw)
    for op in ops:
        k = op[0]
        if k == "disc":
            _, cx, cz, r, y, mode = op
            for dz in range(-r, r+1):
                for dx in range(-r, r+1):
                    if dx*dx + dz*dz <= r*r: put(cx+dx, cz+dz, y, mode)
        elif k == "cone":
            _, cx, cz, r, peak, mode = op
            for dz in range(-r, r+1):
                for dx in range(-r, r+1):
                    d = (dx*dx + dz*dz) ** 0.5
                    if d <= r:
                        base = get(cx+dx, cz+dz) / 128.0
                        put(cx+dx, cz+dz, base + (peak-base)*(1 - d/r), mode)
        elif k == "rect":
            _, x0, z0, x1, z1, y, mode = op
            for z in range(max(0, z0), min(h, z1)):
                for x in range(max(0, x0), min(w, x1)):
                    put(x, z, y, mode)
        elif k == "ramp":
            _, x0, z0, x1, z1, y_lo, y_hi, mode = op
            ax0, ax1 = min(x0, x1), max(x0, x1)
            az0, az1 = min(z0, z1), max(z0, z1)
            along_x = (ax1 - ax0) >= (az1 - az0)
            lo_c = ax0 if along_x else az0
            span = max(1, (ax1 - ax0 if along_x else az1 - az0) - 1)
            for z in range(max(0, az0), min(h, az1)):
                for x in range(max(0, ax0), min(w, ax1)):
                    t = ((x if along_x else z) - lo_c) / span
                    put(x, z, y_lo + (y_hi - y_lo) * max(0.0, min(1.0, t)), mode)
    return rebuild_bdf(hfield_bytes, payload)


def plateau(cx, cz, r, top_y, floor_y=8.0, ramps=(), ramp_w=28, ramp_len=48,
            shape="disc", mode="raise", tiers=1, tier_w=10, tier_drop=12.0):
    """Return a list of reshape_hfield ops for a flat-topped plateau (STEEP, unit-blocking
    walls) at height `top_y`, with gentle WALKABLE ramps descending to `floor_y` on the named
    sides. `ramps` is any of '+x','-x','+z','-z' (the compass direction the ramp descends).
    Pair with a navmesh patch whose component seed sits on the floor: component_of then floods
    floor -> up each ramp -> plateau top, so a base placed on top stays reachable.

    tiers>1 builds a STEPPED / TERRACED plateau: `tiers` concentric rings, the flat top at
    radius `r-(tiers-1)*tier_w` and height top_y, each outer ring `tier_w` wider and `tier_drop`
    lower. Every step is a fresh steep face, and SC2 auto-textures steep faces tan/rock — so a
    terraced plateau shows several concentric tan rings even from the flattened overhead camera
    (the zoomed-out view that hides gentle relief). The ramp runs the full top_y->floor_y drop,
    starting at the inner flat-top edge, carving one gentle walkable notch through every tier."""
    rt = max(2, r - (tiers - 1) * tier_w)        # inner flat-top radius
    ops = []
    for i in range(tiers):                       # outermost(low) first, inner(high) overwrites
        ri = r - i * tier_w
        yi = top_y - (tiers - 1 - i) * tier_drop
        if ri <= 0:
            continue
        if shape == "rect":
            ops.append(("rect", cx - ri, cz - ri, cx + ri, cz + ri, yi, mode))
        else:
            ops.append(("disc", cx, cz, ri, yi, mode))
    hw = ramp_w // 2
    ov = 8  # overlap into the flat top so the ramp fuses with it (no lip)
    for d in ramps:
        if d == "+x":
            ops.append(("ramp", cx + rt - ov, cz - hw, cx + r + ramp_len, cz + hw, top_y, floor_y, "set"))
        elif d == "-x":
            ops.append(("ramp", cx - r - ramp_len, cz - hw, cx - rt + ov, cz + hw, floor_y, top_y, "set"))
        elif d == "+z":
            ops.append(("ramp", cx - hw, cz + rt - ov, cx + hw, cz + r + ramp_len, top_y, floor_y, "set"))
        elif d == "-z":
            ops.append(("ramp", cx - hw, cz - r - ramp_len, cx + hw, cz - rt + ov, floor_y, top_y, "set"))
    return ops

# ---------------------------------------------------------------------------
# Re-skin — change a map's GROUND TEXTURES (its look) without touching elevation
# ---------------------------------------------------------------------------
def reskin_terrain(terrain_bytes, mapping):
    """Re-skin the ground by repointing texture paths inside terrain.win.bdf to a DIFFERENT
    biome's textures — changes the actual visible look (grass->snow/rock/etc.), PROVEN in-game.

    mapping = {old_full_path: new_full_path}. Paths are written WITHOUT the '.win' infix (the
    engine inserts it: a map references '..._grass01_d.dds' but the file is '..._grass01_d.win.dds').
    Editing is IN PLACE — each path is overwritten within its own byte span (path + trailing nulls)
    and null-padded back to the same length — so NO internal offsets in the BDF shift. The new
    path must therefore be strictly shorter than the old path's slot (raises otherwise). Returns
    rebuilt terrain.win.bdf bytes. Pair with package_patched (which ships the modified terrain)."""
    pl = bytearray(read_bdf_payload(terrain_bytes))
    done = 0
    for m in list(re.finditer(rb"/[Tt]extures/Terrain/[!-~]+?\.dds", pl)):
        old = m.group().decode("latin1")
        if old not in mapping:
            continue
        s, e = m.start(), m.end()
        slack = 0
        while e + slack < len(pl) and pl[e + slack] == 0:
            slack += 1
        slot = (e - s) + slack
        new = mapping[old].encode("latin1")
        if len(new) >= slot:
            raise ValueError(f"reskin path too long for slot ({len(new)}>={slot}): {mapping[old]}")
        pl[s:s + slot] = new + b"\x00" * (slot - len(new))
        done += 1
    if done == 0:
        raise ValueError("reskin_terrain: no texture paths matched the mapping keys")
    return rebuild_bdf(terrain_bytes, pl)


def reskin_map(src_id, dst_id, src_prefix, dst_prefix, layers):
    """Convenience: build a reskin_terrain mapping for the diffuse(_d)+normal(_n) of each layer.
    layers = [(src_layer, dst_layer), ...]. Paths look like
      /textures/Terrain/<id>/<prefix><layer>_<d|n>.dds   (no '.win')
    e.g. reskin_map('MP_007','MP_301','sc2_mp_007_','sc2_mp_301_',[('grass01','ground01'),...])."""
    m = {}
    for sl, dl in layers:
        for ch in ("d", "n"):
            m[f"/textures/Terrain/{src_id}/{src_prefix}{sl}_{ch}.dds"] = \
                f"/textures/Terrain/{dst_id}/{dst_prefix}{dl}_{ch}.dds"
    return m


def flatten_regions(hfield_bytes, regions):
    """Flatten each region to its own MEAN height (dead-flat => BUILDABLE), leaving the rest of the
    terrain untouched so the surrounding dunes stay visibly rough/unbuildable. Use to carve buildable
    clearings out of an undulating campaign terrain (e.g. desert) without flattening the whole map.
      ("disc", cx, cz, r)          flat circular pad
      ("rect", x0, z0, x1, z1)     flat rectangular plain
    Returns rebuilt hfield.win.bdf bytes (pair with Terrain.set_hfield)."""
    pl = bytearray(read_bdf_payload(hfield_bytes))
    _, w, h, _, hd = struct.unpack_from("<5I", pl, 0)
    def gy(x, z): return struct.unpack_from("<H", pl, hd + (z*w + x)*2)[0]
    def put(x, z, v): struct.pack_into("<H", pl, hd + (z*w + x)*2, max(0, min(65535, int(v))))
    for reg in regions:
        cells = []
        if reg[0] == "disc":
            _, cx, cz, r = reg
            for dz in range(-r, r+1):
                for dx in range(-r, r+1):
                    if dx*dx + dz*dz <= r*r and 0 <= cx+dx < w and 0 <= cz+dz < h:
                        cells.append((cx+dx, cz+dz))
        else:
            _, x0, z0, x1, z1 = reg
            for z in range(max(0, z0), min(h, z1)):
                for x in range(max(0, x0), min(w, x1)):
                    cells.append((x, z))
        if not cells:
            continue
        mean = sum(gy(x, z) for x, z in cells) // len(cells)
        for x, z in cells:
            put(x, z, mean)
    return rebuild_bdf(hfield_bytes, pl)


def flatten_gentle(hfield_bytes, terrain, keep_slope=6.0, radius=7, passes=2, water_margin=4.0,
                   mode="level"):
    """Make the WHOLE gentle land broadly BUILDABLE while preserving the genuinely-impassable
    features (cells whose ORIGINAL slope exceeds keep_slope = cliffs/rift walls, and cells below
    the water line = the rift). Two modes:
      - "level"  : set every gentle-dry cell to ONE shared mean height -> dead flat -> guaranteed
                   buildable everywhere that isn't cliff/water (the dunes are modest amplitude so
                   the leveled plain is artifact-free). The reliable one.
      - "smooth" : separable box-blur of the gentle land (keeps large-scale variation but often
                   leaves residual slope above SC2's build tolerance).
    Returns rebuilt hfield.win.bdf."""
    if mode not in ("level", "smooth"):
        raise ValueError(f"flatten_gentle: unknown mode {mode!r} (use 'level' or 'smooth')")
    pl = bytearray(read_bdf_payload(hfield_bytes))
    _, w, h, _, hd = struct.unpack_from("<5I", pl, 0)
    n = w * h
    H = list(struct.unpack_from(f"<{n}H", pl, hd))
    orig = H[:]
    thr = int(keep_slope * 128)
    waterraw = int((_water_level(terrain) + water_margin) * 128)
    def gentle(x, z):
        c = orig[z * w + x]
        if c < waterraw:
            return False
        s = max(abs(orig[z * w + x + 1] - c), abs(orig[z * w + x - 1] - c),
                abs(orig[(z + 1) * w + x] - c), abs(orig[(z - 1) * w + x] - c))
        return s <= thr
    if mode == "level":
        tot = cnt = 0
        for z in range(1, h - 1):
            for x in range(1, w - 1):
                if gentle(x, z):
                    tot += orig[z * w + x]
                    cnt += 1
        mean = tot // max(cnt, 1)
        for z in range(1, h - 1):
            for x in range(1, w - 1):
                if gentle(x, z):
                    H[z * w + x] = mean
    else:
        def blur(src):
            tmp = [0] * n
            for z in range(h):
                b = z * w
                pre = [0] * (w + 1)
                for x in range(w):
                    pre[x + 1] = pre[x] + src[b + x]
                for x in range(w):
                    lo = x - radius if x - radius > 0 else 0
                    hi = x + radius if x + radius < w - 1 else w - 1
                    tmp[b + x] = (pre[hi + 1] - pre[lo]) // (hi - lo + 1)
            out = [0] * n
            for x in range(w):
                pre = [0] * (h + 1)
                for z in range(h):
                    pre[z + 1] = pre[z] + tmp[z * w + x]
                for z in range(h):
                    lo = z - radius if z - radius > 0 else 0
                    hi = z + radius if z + radius < h - 1 else h - 1
                    out[z * w + x] = (pre[hi + 1] - pre[lo]) // (hi - lo + 1)
            return out
        for _ in range(passes):
            H = blur(H)
        for z in range(1, h - 1):
            for x in range(1, w - 1):
                if not gentle(x, z):
                    H[z * w + x] = orig[z * w + x]
        for x in range(w):  # the 1-cell map-edge border can't be tested by gentle(); keep original
            H[x] = orig[x]
            H[(h - 1) * w + x] = orig[(h - 1) * w + x]
        for z in range(h):
            H[z * w] = orig[z * w]
            H[z * w + w - 1] = orig[z * w + w - 1]
    for i in range(n):
        struct.pack_into("<H", pl, hd + i * 2, H[i])
    return rebuild_bdf(hfield_bytes, pl)

# ---------------------------------------------------------------------------
# PNG (debug renders) — pure stdlib
# ---------------------------------------------------------------------------
def write_png(path, w, h, rgb_rows):
    def chunk(tag, data):
        c = struct.pack(">I", len(data)) + tag + data
        return c + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
    raw = b"".join(b"\x00" + row for row in rgb_rows)
    open(path, "wb").write(b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw, 6)) + chunk(b"IEND", b""))

# ---------------------------------------------------------------------------
# Scenery props (mapobjs.win.bdf)
# ---------------------------------------------------------------------------
# Animated/vehicle scenery props that move around (ambient detail in campaign maps).
# Copying a campaign terrain wholesale brings these along — e.g. the Illuminate desert's
# "Mine Crawler". strip_mapobjs() neutralizes them so PATCH maps don't inherit movement.
MOVING_PROP_PATTERNS = ("Crawler", "Vehicle", "Civilian", "Wander", "Ambient_", "Traffic")

def strip_mapobjs(mapobjs_bytes, mode="moving"):
    """Return a rebuilt mapobjs.win.bdf with scenery props neutralized.

    mode="moving"  — repoint every moving/vehicle prop (MOVING_PROP_PATTERNS, or a
                     custom tuple of substrings) to a STATIC prop already present in the
                     same file, so it stops moving. Structure-preserving (in-place,
                     null-terminated, no offset shifts) and crash-safe (the replacement
                     prop is one the map already loads). Static scenery (trees/rocks) kept.
    mode="all"     — set the object count to 0 (best-effort: removes all props).

    Note: verified to produce a valid file and to replace the target blueprint strings;
    the in-game visual result (prop becomes static) is the expected behavior but is not
    machine-verifiable here — test a build in a skirmish if it matters."""
    payload = bytearray(read_bdf_payload(mapobjs_bytes))
    if mode == "all":
        struct.pack_into("<I", payload, 4, 0)                 # object count -> 0
        return rebuild_bdf(mapobjs_bytes, payload)
    patterns = MOVING_PROP_PATTERNS if mode == "moving" else tuple(mode)
    is_moving = lambda p: any(pt.encode() in p for pt in patterns)
    paths = [(m.start(), m.group()) for m in re.finditer(rb"/props/[!-~]+?\.bp", payload)]
    static = sorted((p for _, p in paths if not is_moving(p)), key=len)
    donor = static[0] if static else None
    for off, path in paths:
        if not is_moving(path):
            continue
        if donor and len(donor) < len(path):
            payload[off:off+len(donor)] = donor
            payload[off+len(donor)] = 0                       # null-terminate
        else:
            payload[off] = 0                                  # blank -> prop skipped
    return rebuild_bdf(mapobjs_bytes, payload)

# ---------------------------------------------------------------------------
# Packaging + install
# ---------------------------------------------------------------------------
def package_remix(out_scd, scenario_id, save_lua, scenario_lua, minimap_dds=None):
    """REMIX deploy: scenario points at a stock terrain; ship only lua (+minimap)."""
    _write_scd(out_scd, scenario_id, save_lua, scenario_lua,
               extra_bin={f"{scenario_id}.minimap.win.dds": minimap_dds} if minimap_dds else {})

def package_patched(out_scd, terrain, terrain_id, save_lua, scenario_lua,
                    patched_costs, minimap_dds=None, ship_lighting=True, strip_props="moving"):
    """PATCH deploy: copy the full terrain bdf set under terrain_id, swap in the
    patched costs, ship lua. terrain_id must equal the scenario id used in scenario_lua.
    strip_props ("moving" default / "all" / a tuple of substrings / False) neutralizes
    campaign scenery props so the map doesn't inherit moving vehicles — see strip_mapobjs."""
    files = {}
    for k in ("hfield.win.bdf", "collision2.win.bdf", "terrain.win.bdf", "info.win.bdf",
              "mapobjs.win.bdf", "decals.win.bdf", "waterDepth.dds"):
        if terrain.raw[k]:
            files[f"{terrain_id}.{k}"] = terrain.raw[k]
    if strip_props and terrain.raw["mapobjs.win.bdf"]:
        files[f"{terrain_id}.mapobjs.win.bdf"] = strip_mapobjs(terrain.raw["mapobjs.win.bdf"], strip_props)
    files[f"{terrain_id}.costs.win.bdf"] = patched_costs
    files[f"{terrain_id}.minimap.win.dds"] = minimap_dds or terrain.raw["minimap.win.dds"]
    lighting = {}
    if ship_lighting:
        for n, b in terrain.lighting.items():
            lighting[n.replace("\\", "/").split("/")[-1]] = b
    _write_scd(out_scd, terrain_id, save_lua, scenario_lua, extra_bin=files, lighting=lighting)

def _write_scd(out_scd, map_id, save_lua, scenario_lua, extra_bin=None, lighting=None):
    if os.path.exists(out_scd):
        os.remove(out_scd)
    with zipfile.ZipFile(out_scd, "w", zipfile.ZIP_DEFLATED) as zf:
        for sub in (f"maps/{map_id}", f"uncompiled/maps/{map_id}"):
            zf.writestr(f"{sub}/{map_id}_save.lua", save_lua)
            zf.writestr(f"{sub}/{map_id}_scenario.lua", scenario_lua)
            zf.writestr(f"{sub}/{map_id}_script.lua", SCRIPT_LUA)
        for name, data in (extra_bin or {}).items():
            zf.writestr(f"maps/{map_id}/{name}", data)
        for name, data in (lighting or {}).items():
            zf.writestr(f"maps/{map_id}/lighting/{name}", data)
    return out_scd

def install(scd_path):
    dst = os.path.join(GAMEDATA, os.path.basename(scd_path))
    shutil.copy(scd_path, dst)
    return dst

def uninstall(scd_basename):
    p = os.path.join(GAMEDATA, scd_basename)
    if os.path.exists(p):
        os.remove(p); return True
    return False

def list_installed():
    return sorted(f for f in os.listdir(GAMEDATA)
                  if f.startswith("_") and f.endswith(".scd"))

# ---------------------------------------------------------------------------
# Terrain catalog — reusable facts derived during reverse-engineering.
# openness = largest connected navigable fraction on SHIPPED data (land layer).
# navmesh: "full" = playable everywhere (skirmish, REMIX-ready);
#          "pocket" = only a small baked region (campaign, needs PATCH);
#          "degenerate" = no islands baked, engine falls back to cost-only.
# ---------------------------------------------------------------------------
TERRAINS = {
    # id            name                  biome        water_y openness navmesh   notes
    "SC2_MP_002":  dict(name="Open Palms",         biome="grass",  water_y=15,  openness=1.00, navmesh="full",   players=6),
    "SC2_MP_204":  dict(name="Geothermal Borehole",biome="grass",  water_y=0,   openness=0.15, navmesh="full",   players=4),
    "SC2_MP_206":  dict(name="Van Horne Core",     biome="rock",   water_y=0,   openness=0.95, navmesh="full",   players=6),
    "SC2_MP_302":  dict(name="Treallach Island",   biome="island", water_y=56,  openness=0.82, navmesh="full",   players=4),
    "SC2_CA_I01":  dict(name="Illuminate Desert",  biome="sand",   water_y=34,  openness=0.28, navmesh="pocket", players=6,
                        notes="two shores (west ~482k cells / east ~249k) split by a river; patch + causeway needed"),
    "SC2_CA_C04":  dict(name="Cybran Highland",    biome="rock",   water_y=118, openness=0.41, navmesh="pocket", players=4,
                        notes="looks like Van Horne Core (shared art); big plateau"),
    "SC2_GC_DEMO": dict(name="Demo Crater",        biome="rock",   water_y=0,   openness=0.07, navmesh="degenerate", players=4,
                        notes="unreleased; cross+hub navigable, void basins blocked at runtime"),
    "SC2_MP_007":  dict(name="4 FFA Land",         biome="grass",  water_y=0,   openness=0.67, navmesh="full",   players=4),
    "SC2_MP_101":  dict(name="1v1 Land",           biome="grass",  water_y=0,   openness=0.09, navmesh="full",   players=2),
    "SC2_D1_101_1K": dict(name="Etched Desert (1K)", biome="sand", water_y=0,   openness=0.43, navmesh="full",   players=4),
}
