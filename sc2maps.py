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

import os, zipfile, zlib, struct, shutil, math
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
    """Set every cell in `component` to cost=1/island=0 on ALL layers; update island-0
    bbox; return the rebuilt costs.win.bdf bytes. Leaves non-component cells (water,
    cliffs) untouched so amphibious/naval layers keep their water access."""
    payload = bytearray(terrain.costs_payload)
    cells = [i for i in range(GRID*GRID) if component[i]]
    minx = min(i % GRID for i in cells); maxx = max(i % GRID for i in cells)
    minz = min(i // GRID for i in cells); maxz = max(i // GRID for i in cells)
    for (_lid, _cA, oA, _cB, oB, _cS, oS) in terrain.layers:
        for i in cells:
            payload[oA + i] = 1
            payload[oB + i] = 0
        struct.pack_into("<4I", payload, oS, minx, minz, maxx, maxz)
    return rebuild_bdf(terrain.raw["costs.win.bdf"], payload), payload

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

def make_save(markers_blob, n_armies):
    return ("--[[ generated by sc2maps ]]--\nScenario = {\n    Props = {\n    },\n"
            "    Areas = {\n        ['AREA_1'] = {\n"
            "            ['rectangle'] = RECTANGLE( 40, 40, 984, 984 ),\n        },\n    },\n"
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
# Packaging + install
# ---------------------------------------------------------------------------
def package_remix(out_scd, scenario_id, save_lua, scenario_lua, minimap_dds=None):
    """REMIX deploy: scenario points at a stock terrain; ship only lua (+minimap)."""
    _write_scd(out_scd, scenario_id, save_lua, scenario_lua,
               extra_bin={f"{scenario_id}.minimap.win.dds": minimap_dds} if minimap_dds else {})

def package_patched(out_scd, terrain, terrain_id, save_lua, scenario_lua,
                    patched_costs, minimap_dds=None, ship_lighting=True):
    """PATCH deploy: copy the full terrain bdf set under terrain_id, swap in the
    patched costs, ship lua. terrain_id must equal the scenario id used in scenario_lua."""
    files = {}
    for k in ("hfield.win.bdf", "collision2.win.bdf", "terrain.win.bdf", "info.win.bdf",
              "mapobjs.win.bdf", "decals.win.bdf", "waterDepth.dds"):
        if terrain.raw[k]:
            files[f"{terrain_id}.{k}"] = terrain.raw[k]
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
}
