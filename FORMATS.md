# Supreme Commander 2 map file formats

Reverse-engineered reference for the files that make up an SC2 map. Offsets below
were verified byte-exact across 5+ shipped maps. All integers are little-endian.

A map lives in a `.scd` file (a renamed `.zip`) under the game's `gamedata\` folder,
containing `maps/<ID>/` and `uncompiled/maps/<ID>/`. A skirmish map needs three Lua
files (`_scenario.lua`, `_save.lua`, `_script.lua`) plus a terrain (the `*.win.bdf`
set + `.minimap.win.dds`). The scenario's `map = '/maps/<ID>/<ID>.scmap'` field tells
the engine which terrain folder to load the `.win.bdf` files from — it can point at a
**different** id than the scenario, which is how you remix a stock terrain.

## BDF container (`*.win.bdf`)

Wraps a single zlib stream with a header and a pointer-fixup table.

```
0x00  char[4]  "MFDB"
0x04  u32      version = 7
0x08  u32      = 2
0x0C  u32      = 0
0x10  u32      compressed_size     (exact length of the zlib stream)
0x14  u32      decompressed_size
0x18  u32      n_fixups
0x1C  u32[...] fixup table (byte offsets into the payload that hold pointers)
      ...      then value 8, padding to the stream
 ~    u8[]     ONE zlib stream (starts 0x78 0x9C), `compressed_size` bytes
      ...      a few zero pad bytes to EOF
```

The zlib stream does **not** start at a fixed offset across file types. Robust recipe:
scan for `78 9C` from offset 4 and take the first position that `zlib.decompress`es.

**Rewriting:** keep bytes `[0 : stream_start]` verbatim (header + fixup table),
overwrite `compressed_size@0x10` and `decompressed_size@0x14`, append
`zlib.compress(payload, level=6)` (level 6 emits the `78 9C` header that matches the
originals; level 9 emits `78 DA` — avoid), then zero-pad the file to a multiple of 32.
Payload size may change freely.

## Heightfield (`<ID>.hfield.win.bdf`)

Payload:
```
0x00  u32  version = 2
0x04  u32  width  = 1025
0x08  u32  height = 1025
0x0C  u32  n = width*height
0x10  u32  data_offset = 28
0x1C  u16[1025*1025]  heightmap, Z-MAJOR: H[z*1025 + x]
```
`world_y = raw / 128.0`. World coordinates span 0..1024 and match marker positions.

## Water mask (`<ID>.waterDepth.dds`)

512×512 **DXT5** (128-byte DDS header, then 16-byte blocks). A cell is **water** if its
block's alpha endpoints satisfy `max(block[0], block[1]) > 32`. Block for world (x,z):
`((z*511//1024)//4) * 128 + ((x*511//1024)//4)`, times 16. (Land/dry otherwise.)

## Navigation mesh (`<ID>.costs.win.bdf`) — the important one

Payload:
```
0x00  u32  version = 2
0x04  u32  n_layers       (3 on land-only maps, 5 on maps with water)
0x08  u32  table_offset = 12
0x0C  layer records, 28 bytes each:
        u32 layer_id              (0 .. n_layers-1)
        u32 count_A = 1048576     (= 1024*1024)
        u32 off_A                 (-> cost grid)
        u32 count_B = 1048576
        u32 off_B                 (-> island grid)
        u32 count_S               (u32 ELEMENTS, = 4 * num_islands)
        u32 off_S                 (-> bbox table; 0xFFFFFFFF if none)
```

Per layer:
- **cost grid** `u8[1024*1024]`, Z-MAJOR (`A[off_A + z*1024 + x]`):
  `1` = walkable, `255` = **blocked**, `2..254` = slope/obstacle penalty.
- **island grid** `u8[1024*1024]`: connected-region id (`0..k`), `255` = none.
  Equal ids (≠255) ⇒ mutually reachable.
- **bbox table** `u32[count_S]`: 4 per island — `(min_x, min_z, max_x, max_z)`.

**Navigability rule (confirmed in-game):** a cell is navigable for a layer iff its
cost ≠ 255. The engine reads this **pre-baked** grid at load and does **not** recompute
it from markers — which is exactly why editing these bytes and re-packing the file
makes units move (or stop). On water maps, the land-only class is the layer with the
fewest underwater-navigable cells (`Terrain.land_layer()`); other layers are
amphibious/hover/naval (water is navigable on those by design).

Islands are baked at map-compile time, seeded from `gpnav "Playable Island"` markers
and skirmish start positions. Campaign maps bake only the small mission region — so a
campaign terrain is "open" by slope yet mostly non-navigable until patched. A map with
**no** islands baked (e.g. the unreleased demo map) is a degenerate case: the engine
falls back to cost-only navigation and blocks bottomless void via the heightfield.

**To open terrain:** flood the dry, gently-sloped cells into one connected component,
write `cost=1` / `island=0` for those cells on every layer, set island 0's bbox to the
component extent, and rebuild. Leave water/cliff cells untouched so amphibious/naval
layers keep their access. To bridge water between two landmasses, also force a thin
corridor of cells navigable (a causeway/ford). See `sc2maps.patch_costs` / `carve_box`.

## Collision (`<ID>.collision2.win.bdf`)

A low-poly triangle-mesh physics/raycast proxy of the terrain (float32 world-space
vertices + u32 triangle indices + an AABB/BVH tree). It carries **no** navigability
information and needs no edits for a nav patch. Documented for completeness.

## Other files

- `<ID>.terrain.win.bdf` — render mesh + texture path references (absolute paths like
  `/textures/terrain/<id>/...`, which resolve from `maps.scd`, so a copied terrain reuses
  the originals' textures without shipping them).
- `<ID>.mapobjs.win.bdf` — baked props/markers (scenery, gpnav markers) as 36-byte
  records + a string table.
- `<ID>.info.win.bdf` — ambience/water parameters (the water level is in here as a float).
- `<ID>.decals.win.bdf` — ground decals (cosmetic).
- `<ID>.minimap.win.dds` — 1024×1024 DXT1 minimap. A flat-color-per-4×4-block DXT1
  (`color0 == color1`, all indices 0) is a valid, simple way to generate one.

## Lua

`_scenario.lua` (`ScenarioInfo` table: name, size, `map`/`save`/`script` paths,
`StartPositions`, team `Configurations`), `_save.lua` (`Scenario` table: `Areas`,
`MasterChain.Markers` — army Blank Markers, `Mass`, Base/Rally/Defensive/Expansion/
Default-Path-Node markers — `Chains`, and an `Armies` block), and `_script.lua`
(boilerplate `OnPopulate`/`OnStart`). The `Armies` block for N armies (+`ARMY_EXTRA`)
can be lifted verbatim from a stock skirmish save (MP_204 for ≤4, MP_206 for 5–6).
