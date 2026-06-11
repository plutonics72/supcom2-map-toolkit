"""
make_map — build a complete SC2 custom map from a single spec dict, using sc2maps.

    from make_map import build_map, SPECS
    build_map(SPECS["dune_rift_3v3"])          # build + verify + install

A spec is a dict:
    terrain      stock terrain id to build on (see sc2maps.TERRAINS)
    scenario_id  new map id (PATCH maps: this is also the shipped terrain id)
    name         lobby name, e.g. "[6] Dune Rift (3v3, FFA)"
    out          .scd basename, e.g. "_dune_rift_3v3_by_chris.scd"
    anchors      {army_index: (x, z)} desired spawn locations (snapped to navigable)
    teams        [[1,2,3],[4,5,6]] army groupings (for the lobby FFA list order only)
    economy      dict(base_mass=4, sites=10, per_site=3)
    norush       no-rush radius (default 70)
    patch        None  -> REMIX (terrain already navigable; ship lua only), or
                 dict(max_slope=6, water_margin=4, seed=(x,z),
                      causeways=[(x0,x1,z0,z1), ...])  -> PATCH the navmesh
    minimap      "donor" (copy stock) or "desert" (generated) (default "donor")
    strip_props  PATCH only: "moving" (default — neutralize ambient moving props like
                 campaign "Mine Crawlers"), "all", a tuple of name substrings, or False

build_map loads the terrain, (optionally) patches the navmesh so the play area is
one connected navigable island, lays out spawns + economy ONLY on navigable ground,
verifies every position is navigable and all spawns are mutually reachable, then
packages + installs. It refuses to ship a map that fails verification.
"""
import os, math
import sc2maps as sm

# Built .scd files are written here (the script's own folder by default).
WS = os.environ.get("SC2_MAP_OUT", os.path.dirname(os.path.abspath(__file__)))

def _dist(a, b):
    return math.hypot(a[0]-b[0], a[1]-b[1])

def _spread(t, x, z, r=10):
    vs = [t.y(x+dx, z+dz) for dx in range(-r, r+1, 3) for dz in range(-r, r+1, 3)]
    return max(vs) - min(vs)

def build_map(spec, verbose=True, install=True):
    t = sm.Terrain(spec["terrain"])
    sid = spec["scenario_id"]
    log = (lambda *a: print(*a)) if verbose else (lambda *a: None)
    log(f"terrain {t.id} ({sm.TERRAINS.get(t.id,{}).get('name','?')}), {t.n_layers} layers, land layer L{t.land_layer()}")

    # ---- navigability predicate (patched component, or stock) ----
    patch = spec.get("patch")
    if patch:
        mask = sm.dry_gentle_mask(t, patch.get("max_slope", 6.0), patch.get("water_margin", 4.0))
        for box in patch.get("causeways", []):
            sm.carve_box(t, mask, *box)
        seed = patch.get("seed", (512, 512))
        comp, ncomp = sm.component_of(mask, *seed)
        log(f"patched navigable component: {ncomp} cells ({100*ncomp/(sm.GRID*sm.GRID):.1f}%)")
        incomp = lambda x, z: comp[round(z)*sm.GRID + round(x)] == 1
        nav = incomp
    else:
        comp = None
        nav = lambda x, z: t.navok(x, z)
        incomp = nav

    def snap(x, z, r=20):
        if nav(x, z): return (round(x), round(z))
        cs = [(fx, fz) for fx in range(round(x)-r, round(x)+r+1, 2)
              for fz in range(round(z)-r, round(z)+r+1, 2)
              if 0 < fx < 1023 and 0 < fz < 1023 and nav(fx, fz)]
        return min(cs, key=lambda p: _dist(p, (x, z))) if cs else (round(x), round(z))

    # ---- placeable cells (navigable, dry, flat) ----
    placeable = [(cx, cz) for cx in range(72, 953, 6) for cz in range(72, 953, 6)
                 if nav(cx, cz) and t.dry(cx, cz) and _spread(t, cx, cz) < 2.5]
    log(f"placeable cells: {len(placeable)}")

    # ---- spawns: snap each anchor into the navigable area ----
    spawns = {a: snap(*xz) for a, xz in spec["anchors"].items()}
    armies = sorted(spawns)
    if len(armies) > 1:
        log("min spawn separation: %.0f" % min(_dist(spawns[i], spawns[j])
            for i in armies for j in armies if i < j))

    # ---- base mass: clusters around each spawn ----
    eco = spec.get("economy", {})
    nbase = eco.get("base_mass", 4)
    base_off = [(-14,-14),(14,-14),(-14,14),(14,14),(0,-18),(0,18),(-18,0),(18,0)][:nbase]
    base_mass = {a: [snap(spawns[a][0]+dx, spawns[a][1]+dz) for dx, dz in base_off] for a in armies}

    # ---- expansion sites: max-spread across navigable flats, away from spawns ----
    nsites = eco.get("sites", 10); per = eco.get("per_site", 3)
    cand = [p for p in placeable if all(_dist(p, spawns[a]) > 140 for a in armies)]
    sites = []
    while len(sites) < nsites and cand:
        if not sites:
            pick = max(cand, key=lambda p: min(_dist(p, spawns[a]) for a in armies))
        else:
            pick = max(cand, key=lambda p: min([_dist(p, s) for s in sites]
                       + [_dist(p, spawns[a])*0.7 for a in armies]))
        sites.append(pick); cand = [p for p in cand if _dist(p, pick) > 105]
    site_off = [(0,-10),(-9,8),(9,8),(0,12),(-12,-4),(12,-4)][:per]
    exp_mass = [snap(cx+dx, cz+dz) for (cx, cz) in sites for dx, dz in site_off]

    # ---- markers ----
    mk = []
    for a in armies:
        sx, sz = spawns[a]
        mk.append(sm.marker(f"ARMY_{a}", "Blank Marker", sx, sz, t.y(sx, sz),
                            prop="/env/common/props/markers/M_Blank_prop.bp"))
        mk.append(sm.marker(f"Base Marker 0{a}", "Base Marker", sx, sz-22, t.y(sx, sz-22), color="ff0000ff"))
        mk.append(sm.marker(f"Rally Point 0{a}", "Rally Point",
                            sx + (18 if sx < 512 else -18), sz, t.y(sx, sz), color="FF808080"))
    if patch:  # belt-and-suspenders island seeds (harmless on stock maps too)
        for i, a in enumerate(armies, 1):
            sx, sz = spawns[a]
            mk.append(sm.marker(f"gpnav Playable Island 0{i}", "gpnav Playable Island", sx, sz, t.y(sx, sz), color="ff008000"))
    n = 0
    for a in armies:
        for (px, pz) in base_mass[a]:
            n += 1; mk.append(sm.mass_marker(f"Mass {n:02d}", px, pz, t.y(px, pz)))
    for (px, pz) in exp_mass:
        n += 1; mk.append(sm.mass_marker(f"Mass {n:02d}", px, pz, t.y(px, pz)))
    for i, (cx, cz) in enumerate(sites, 1):
        mk.append(sm.marker(f"Expansion Area {i:02d}", "Expansion Area", cx, cz, t.y(cx, cz), color="ff008080"))
    for i, (cx, cz) in enumerate(sites + [spec["anchors"][armies[0]]], 1):
        mk.append(sm.marker(f"Default Path Node {i:02d}", "Default Path Node", cx, cz+4, t.y(cx, cz+4), color="ff808000"))
    log(f"total mass points: {n}")

    # ---- patch the navmesh now that we know the component ----
    if patch:
        patched_costs, payload = sm.patch_costs(t, comp)
    else:
        patched_costs, payload = None, t.costs_payload

    # ---- VERIFY before shipping ----
    allpts = [spawns[a] for a in armies] + [p for v in base_mass.values() for p in v] + exp_mass
    landL = 0 if patch else t.land_layer()
    def navok(x, z):
        idx = round(z)*sm.GRID + round(x)
        if patch:
            return all(payload[r[2]+idx] == 1 for r in t.layers)
        return payload[t.layers[landL][2]+idx] != 255
    bad = [p for p in allpts if not navok(*p)]
    assert not bad, f"VERIFY FAILED — blocked positions: {bad[:8]}"
    for a in armies[1:]:
        assert sm.reachable(payload, t.layers, spawns[armies[0]], spawns[a], landL), \
            f"VERIFY FAILED — ARMY_{a} not reachable from ARMY_{armies[0]} by land"
    log(f"VERIFIED: all {len(allpts)} positions navigable; all spawns mutually reachable")

    # ---- assemble lua ----
    spawns_xyz = {a: (spawns[a][0] + 0.5, t.y(*spawns[a]), spawns[a][1] + 0.5) for a in armies}
    terrain_id = sid if patch else t.id
    save_lua = sm.make_save("".join(mk), len(armies))
    scenario_lua = sm.make_scenario(sid, terrain_id, spec["name"], spawns_xyz,
                                    norush=spec.get("norush", 70.0),
                                    reverb=sm.TERRAINS.get(t.id, {}).get("reverb", t.id))

    # ---- minimap ----
    mm = None
    if spec.get("minimap") == "desert":
        mm = sm.write_minimap_dds(t, sm.desert_palette(t))

    # ---- package + install ----
    out = os.path.join(WS, spec["out"])
    if patch:
        # default: neutralize moving campaign scenery props (e.g. ambient "Mine Crawlers")
        sm.package_patched(out, t, sid, save_lua, scenario_lua, patched_costs, minimap_dds=mm,
                           strip_props=spec.get("strip_props", "moving"))
    else:
        sm.package_remix(out, sid, save_lua, scenario_lua, minimap_dds=mm)
    dst = sm.install(out) if install else None
    log(f"packaged{' + installed' if install else ''}: {dst or out} ({os.path.getsize(out)/1e6:.1f} MB)")
    return dict(scd=out, installed=dst, spawns=spawns, mass=n, sites=len(sites),
                navigable_pct=(100*sum(comp)/(sm.GRID*sm.GRID)) if patch else None)


# ---------------------------------------------------------------------------
# Example specs — the maps we built. Copy one and tweak to make a new map.
# ---------------------------------------------------------------------------
SPECS = {
    # PATCH example: campaign desert, two shores + central causeway, west vs east.
    "dune_rift_3v3": dict(
        terrain="SC2_CA_I01", scenario_id="SC2_DUNE6", name="[6] Dune Rift (3v3, FFA)",
        out="_dune_rift_3v3.scd",
        anchors={1:(150,230), 2:(150,500), 3:(170,815), 4:(864,400), 5:(884,585), 6:(820,840)},
        teams=[[1,2,3],[4,5,6]],
        economy=dict(base_mass=4, sites=10, per_site=3), norush=70,
        patch=dict(max_slope=6, water_margin=4, seed=(160,500),
                   causeways=[(360, 701, 470, 561)]),   # ford through the central islets
        minimap="desert",
        strip_props=False,   # keep the desert's ambient Mine Crawlers (author's choice);
                             # omit this line (or set "moving") to neutralize them
    ),
    # REMIX example: stock skirmish terrain (already navigable), marker-only.
    "open_range_3v3": dict(
        terrain="SC2_MP_002", scenario_id="SC2_MP_002OR", name="[6] Open Range (3v3, FFA)",
        out="_open_range.scd",
        anchors={1:(622,398), 2:(653,547), 3:(475,366), 4:(401,625), 5:(370,476), 6:(548,657)},
        teams=[[1,2,3],[4,5,6]],
        economy=dict(base_mass=4, sites=9, per_site=3), norush=70,
        patch=None,
    ),
    # REMIX 1v1: anchors are the stock map's own (guaranteed-navigable) start positions.
    "duel_1v1": dict(
        terrain="SC2_MP_101", scenario_id="SC2_MP_101D", name="[2] Duel (1v1)",
        out="_duel_1v1.scd",
        anchors={1:(350,554), 2:(676,554)},
        teams=[[1],[2]],
        economy=dict(base_mass=4, sites=4, per_site=2), norush=55,
        patch=None,
    ),
    # REMIX 4-player free-for-all.
    "four_corners_ffa": dict(
        terrain="SC2_MP_007", scenario_id="SC2_MP_007F", name="[4] Four Corners (FFA)",
        out="_four_corners.scd",
        anchors={1:(524,190), 2:(506,822), 3:(826,512), 4:(200,498)},
        teams=[[1],[2],[3],[4]],
        economy=dict(base_mass=4, sites=6, per_site=3), norush=70,
        patch=None,
    ),
    # REMIX on a DLC desert terrain (distinct biome), 2v2.
    "etched_desert_2v2": dict(
        terrain="SC2_D1_101_1K", scenario_id="SC2_D1_101E", name="[4] Etched Desert (2v2)",
        out="_etched_desert_2v2.scd",
        anchors={1:(182,416), 2:(842,608), 3:(460,174), 4:(564,850)},
        teams=[[1,2],[3,4]],
        economy=dict(base_mass=4, sites=8, per_site=3), norush=70,
        patch=None,
    ),
}

if __name__ == "__main__":
    import sys
    name = sys.argv[1] if len(sys.argv) > 1 else "dune_rift_3v3"
    print(build_map(SPECS[name]))
