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

    # ---- sculpt the heightfield first (so nav + placement use the NEW terrain) ----
    if spec.get("sculpt"):
        t.set_hfield(sm.reshape_hfield(t.raw["hfield.win.bdf"], spec["sculpt"]))
        log(f"sculpted heightfield: {len(spec['sculpt'])} features")

    # ---- re-skin the ground textures (changes the LOOK; elevation/nav untouched) ----
    if spec.get("reskin"):
        t.raw["terrain.win.bdf"] = sm.reskin_terrain(t.raw["terrain.win.bdf"], spec["reskin"])
        log(f"re-skinned {len(spec['reskin'])//2} ground layers")

    # ---- flatten buildable pads/plains (undulating dunes -> flat clearings; rest stays rough) ----
    if spec.get("flatten"):
        t.set_hfield(sm.flatten_regions(t.raw["hfield.win.bdf"], spec["flatten"]))
        log(f"flattened {len(spec['flatten'])} buildable region(s)")

    # ---- smooth ALL gentle land for broad buildability (cliffs + water preserved) ----
    if spec.get("smooth"):
        t.set_hfield(sm.flatten_gentle(t.raw["hfield.win.bdf"], t, **spec["smooth"]))
        log("smoothed gentle terrain for buildability (cliffs/water preserved)")

    # ---- ruggedize the map-edge band into an impassable, visibly-rocky boundary ----
    if spec.get("rugged_edges"):
        t.set_hfield(sm.ruggedize_edges(t.raw["hfield.win.bdf"], t, **spec["rugged_edges"]))
        log("ruggedized map-edge band (impassable rocky boundary)")

    # land layer for placement/verify; override for maps whose auto-detect is fooled
    # (e.g. a lake map with no waterDepth.dds, where the true land-only layer must be named).
    landL = spec.get("land_layer", t.land_layer())

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
        nav = lambda x, z: t.navok(x, z, layer=landL)
        incomp = nav

    def snap(x, z, r=20):
        if nav(x, z): return (round(x), round(z))
        cs = [(fx, fz) for fx in range(round(x)-r, round(x)+r+1, 2)
              for fz in range(round(z)-r, round(z)+r+1, 2)
              if 0 < fx < t.grid - 1 and 0 < fz < t.grid - 1 and nav(fx, fz)]
        return min(cs, key=lambda p: _dist(p, (x, z))) if cs else (round(x), round(z))

    # A mass extractor needs a flat, clear FOOTPRINT, not just a navigable cell — a
    # point near a cliff is navigable but unbuildable. Calibrated to the game's own
    # mass deposits, which sit on very flat ground (relief <= ~0.4, max adjacent step
    # <= ~0.2 over a 9x9 footprint). buildable() requires similar flatness and probes one
    # cell beyond the footprint, so a cliff edge that would clip the extractor is caught.
    def _relief(x, z, r):
        hs = [t.y(x+dx, z+dz) for dx in range(-r, r+1) for dz in range(-r, r+1)]
        return max(hs) - min(hs)
    def _max_step(x, z, r):
        m = 0.0
        for dx in range(-r, r+1):
            for dz in range(-r, r+1):
                c = t.y(x+dx, z+dz)
                m = max(m, abs(c - t.y(x+dx+1, z+dz)), abs(c - t.y(x+dx, z+dz+1)))
        return m
    def buildable(x, z, relief_max=0.5, step_max=0.25):
        x, z = round(x), round(z)
        if not (6 < x < t.grid - 7 and 6 < z < t.grid - 7) or not t.dry(x, z):
            return False
        if not all(nav(x+dx, z+dz) for dx in range(-2, 3) for dz in range(-2, 3)):
            return False
        return _relief(x, z, 4) < relief_max and _max_step(x, z, 4) < step_max
    _placed = []          # every reserved point (spawns + mass), for spacing
    MIN_SEP = 12          # minimum cells between any two mass points (no overlap)
    def _clear(x, z, sep=MIN_SEP):
        return all(_dist((x, z), p) >= sep for p in _placed)
    def _buffer_ok(x, z, buf=6):
        # room around the point for defensive structures: a clear, gentle apron so the
        # point isn't jammed against a cliff/water/map edge.
        cells = [(x+dx, z+dz) for dx in range(-buf, buf+1, 2) for dz in range(-buf, buf+1, 2)]
        if not all(nav(cx, cz) for cx, cz in cells):
            return False
        hs = [t.y(cx, cz) for cx, cz in cells]
        return max(hs) - min(hs) < 1.6

    def place(x, z):
        """Nearest flat, buildable cell to (x,z) that keeps MIN_SEP from other mass points.
        Searches outward ring by ring (campaign-strict first, then a relaxed fallback)."""
        ox, oz = round(x), round(z)
        for relief_max, step_max, rmax in ((0.5, 0.25, 40), (0.9, 0.4, 64)):
            for rad in range(0, rmax + 1, 2):
                if rad == 0:
                    ring = [(ox, oz)]
                else:
                    ring = ([(ox+dx, oz+dz) for dx in range(-rad, rad+1, 2) for dz in (-rad, rad)]
                          + [(ox+dx, oz+dz) for dz in range(-rad+2, rad, 2) for dx in (-rad, rad)])
                cs = [(cx, cz) for cx, cz in ring
                      if _clear(cx, cz) and buildable(cx, cz, relief_max, step_max)]
                if cs:
                    pick = min(cs, key=lambda p: _dist(p, (ox, oz)))
                    _placed.append(pick); return pick
        fb = snap(ox, oz); _placed.append(fb); return fb

    def place_square(sx, sz, d0=14):
        """Four base-mass corners that stay an AXIS-ALIGNED SQUARE, are all buildable, and
        each have a clear apron (room for defensive structures, not jammed against a cliff).
        Searches square center (near the spawn) and half-size for a flat placement."""
        for relief_max, step_max in ((0.5, 0.25), (0.9, 0.4)):
            for rad in range(0, 26, 2):
                if rad == 0:
                    centers = [(sx, sz)]
                else:
                    centers = ([(sx+ox, sz+oz) for ox in range(-rad, rad+1, 2) for oz in (-rad, rad)]
                             + [(sx+ox, sz+oz) for oz in range(-rad+2, rad, 2) for ox in (-rad, rad)])
                for cx, cz in centers:
                    for d in (d0, d0+2, d0-2, d0+4, d0-4):
                        if not (9 <= d <= 22):
                            continue
                        corners = [(cx-d, cz-d), (cx+d, cz-d), (cx-d, cz+d), (cx+d, cz+d)]
                        if all(_clear(c[0], c[1]) and buildable(c[0], c[1], relief_max, step_max)
                               and _buffer_ok(c[0], c[1]) for c in corners):
                            _placed.extend(corners)
                            return corners
        # fallback (rare — cliffy base): independent placement, may not be a perfect square
        return [place(sx+dx, sz+dz) for dx, dz in ((-d0,-d0),(d0,-d0),(-d0,d0),(d0,d0))]

    # ---- placeable cells (navigable, dry, flat) ----
    _hi = t.grid - 71                      # was hardcoded 953 (=1024-71); generalize for 512-grid maps
    placeable = [(cx, cz) for cx in range(72, _hi, 6) for cz in range(72, _hi, 6)
                 if nav(cx, cz) and t.dry(cx, cz) and _spread(t, cx, cz) < 2.5]
    log(f"placeable cells: {len(placeable)}")

    # ---- spawns: snap each anchor into the navigable area ----
    spawns = {a: snap(*xz) for a, xz in spec["anchors"].items()}
    armies = sorted(spawns)
    _placed.extend(spawns.values())   # reserve spawn cells so mass points keep clear
    if len(armies) > 1:
        log("min spawn separation: %.0f" % min(_dist(spawns[i], spawns[j])
            for i in armies for j in armies if i < j))

    # ---- base mass: clusters around each spawn ----
    eco = spec.get("economy", {})
    nbase = eco.get("base_mass", 4)
    if nbase == 4:   # keep the 4 base-mass points a clean square per spawn
        base_mass = {a: place_square(spawns[a][0], spawns[a][1]) for a in armies}
    else:
        base_off = [(-14,-14),(14,-14),(-14,14),(14,14),(0,-18),(0,18),(-18,0),(18,0)][:nbase]
        base_mass = {a: [place(spawns[a][0]+dx, spawns[a][1]+dz) for dx, dz in base_off] for a in armies}

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
    exp_mass = [place(cx+dx, cz+dz) for (cx, cz) in sites for dx, dz in site_off]

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
    elif spec.get("ship_terrain"):
        # ship the (modified, e.g. reskinned) terrain under the new id but KEEP the original
        # navmesh -- re-deriving nav on a watered skirmish map would drop its naval/water layers.
        if spec.get("naval_extend"):
            # the hfield was EXTENDED (new beaches raised from water): open them on the LAND
            # layers so units can use the new land, while keeping the naval/water layers intact.
            ne = spec["naval_extend"] if isinstance(spec["naval_extend"], dict) else {}
            patched_costs, payload, _added = sm.open_extended_land(
                t, ne.get("max_slope", 6.0), ne.get("water_margin", 4.0), ne.get("water_level"))
            log(f"naval-extend: opened {_added} new beach cells on the land layers (naval kept)")
        else:
            patched_costs, payload = t.raw["costs.win.bdf"], t.costs_payload
    else:
        patched_costs, payload = None, t.costs_payload

    # ---- VERIFY before shipping ----
    allpts = [spawns[a] for a in armies] + [p for v in base_mass.values() for p in v] + exp_mass
    def navok(x, z):
        idx = round(z)*sm.GRID + round(x)
        if patch:
            return all(payload[r[2]+idx] == 1 for r in t.layers)
        return payload[t.layers[landL][2]+idx] != 255
    bad = [p for p in allpts if not navok(*p)]
    assert not bad, f"VERIFY FAILED — blocked positions: {bad[:8]}"
    if spec.get("teams_connected", True):
        for a in armies[1:]:
            assert sm.reachable(payload, t.layers, spawns[armies[0]], spawns[a], landL), \
                f"VERIFY FAILED — ARMY_{a} not reachable from ARMY_{armies[0]} by land"
        log(f"VERIFIED: all {len(allpts)} positions navigable; all spawns mutually reachable")
    else:
        # naval map: teams sit on water-separated landmasses, so only require each TEAM's own
        # spawns to be mutually land-reachable (cross-team contact is by sea/air).
        for team in spec.get("teams", [armies]):
            for a in team[1:]:
                assert sm.reachable(payload, t.layers, spawns[team[0]], spawns[a], landL), \
                    f"VERIFY FAILED — ARMY_{a} not reachable from teammate ARMY_{team[0]} by land"
        log(f"VERIFIED: all {len(allpts)} navigable; intra-team reachable (teams water-separated)")

    # ---- assemble lua ----
    spawns_xyz = {a: (spawns[a][0] + 0.5, t.y(*spawns[a]), spawns[a][1] + 0.5) for a in armies}
    terrain_id = sid if (patch or spec.get("ship_terrain")) else t.id
    save_lua = sm.make_save("".join(mk), len(armies), spec.get("playable_area", (40, 40, 984, 984)))
    scenario_lua = sm.make_scenario(sid, terrain_id, spec["name"], spawns_xyz,
                                    norush=spec.get("norush", 70.0),
                                    reverb=sm.TERRAINS.get(t.id, {}).get("reverb", t.id))

    # ---- minimap ----
    mm = None
    _pal = {"desert": sm.desert_palette, "snow": getattr(sm, "snow_palette", None),
            "dark": getattr(sm, "dark_palette", None)}.get(spec.get("minimap"))
    if _pal:
        mm = sm.write_minimap_dds(t, _pal(t))

    # ---- package + install ----
    out = os.path.join(WS, spec["out"])
    if patch or spec.get("ship_terrain"):
        # ship the full terrain set under the new id (patched nav, or the ORIGINAL nav when
        # ship_terrain); default: neutralize moving campaign scenery props (e.g. "Mine Crawlers")
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
        # Smooth ALL the gentle desert so it's broadly BUILDABLE (dunes were walkable but too
        # undulating to place structures on). The water rift + rocky cliffs are preserved as the
        # only — and visually obvious — no-build areas.
        smooth=dict(keep_slope=6.0, radius=7, passes=2, water_margin=4.0),
        # The flat desert runs to the map edge on every side (only the NE is the water rift), so
        # open the playable boundary out from the default 40-cell inset to expose it symmetrically.
        playable_area=(8, 8, 1016, 1016),
        economy=dict(base_mass=4, sites=10, per_site=3), norush=70,
        patch=dict(max_slope=6, water_margin=4, seed=(160,500),
                   causeways=[(360, 701, 470, 561)]),   # ford through the central islets
        minimap="desert",
        strip_props="moving",   # neutralize the desert's roaming Mine Crawlers (-> static)
    ),
    # 2v2 version of Dune Rift — 4 spawns (two west shore vs two east shore) across the central
    # causeway. Same terrain/patch as the 3v3; fewer spawns + expansions.
    "dune_rift_2v2": dict(
        terrain="SC2_CA_I01", scenario_id="SC2_DUNE4", name="[4] Dune Rift (2v2)",
        out="_dune_rift_2v2.scd",
        anchors={1:(150,230), 2:(170,815), 3:(864,400), 4:(820,840)},
        teams=[[1,2],[3,4]],
        economy=dict(base_mass=4, sites=8, per_site=3), norush=70,
        # Level the gentle desert flat for broad buildability (water rift + cliffs preserved) —
        # same fix as the 3v3.
        smooth=dict(keep_slope=6.0, radius=7, passes=2, water_margin=4.0),
        playable_area=(8, 8, 1016, 1016),   # open the boundary to the map edge (same as the 3v3)
        patch=dict(max_slope=6, water_margin=4, seed=(160,500),
                   causeways=[(360, 701, 470, 561)]),
        minimap="desert",
        strip_props="moving",
    ),
    # REMIX 3v3: a real crater LAKE (Boras) — 6 bases ring a central water lake. The map
    # has no waterDepth.dds, so the auto land-layer detect is fooled -> name L1 explicitly.
    "crater_lake_3v3": dict(
        terrain="SC2_MP_305", scenario_id="SC2_BORAS6", name="[6] Crater Lake (3v3, FFA)",
        out="_crater_lake_3v3.scd",
        anchors={1:(406,198), 2:(730,264), 3:(836,576), 4:(618,824), 5:(294,760), 6:(188,448)},
        teams=[[1,2,3],[4,5,6]], land_layer=1,
        economy=dict(base_mass=4, sites=8, per_site=2), norush=70,
        patch=None,
    ),
    # SCULPTED 3v3: reshape Emerald Crater's flat floor into a NEW battlefield — a central
    # walkable dome (high ground) ringed by six cover mesas, six bases between them. Genuinely
    # new-looking (sculpted, not a reskin); green; dry (water can't go on a green map).
    "emerald_hollow_3v3": dict(
        terrain="SC2_MP_007", scenario_id="SC2_HOLLOW6", name="[6] Emerald Hollow (3v3, FFA)",
        out="_emerald_hollow_3v3.scd",
        anchors={1:(512,152), 2:(824,332), 3:(200,332), 4:(824,692), 5:(512,872), 6:(200,692)},
        teams=[[1,2,3],[4,5,6]],
        sculpt=[("cone", 512, 512, 125, 60, "raise"),     # tall central MOUNTAIN (walkable high ground)
                ("disc", 762, 512, 42, 46, "raise"),       # six tall flat-top MESAS (sharp obstacles)
                ("disc", 637, 296, 42, 46, "raise"),
                ("disc", 387, 296, 42, 46, "raise"),
                ("disc", 262, 512, 42, 46, "raise"),
                ("disc", 387, 728, 42, 46, "raise"),
                ("disc", 637, 728, 42, 46, "raise"),
                ("rect", 486, 230, 538, 300, 38, "raise"), # short tall ridges flanking the centre
                ("rect", 486, 724, 538, 794, 38, "raise")],
        patch=dict(max_slope=6, water_margin=0, seed=(512, 512)),
        economy=dict(base_mass=4, sites=9, per_site=3), norush=70,
    ),
    # ---- "steep plateaus + ramps + carved valley lanes" variants (green, dry, MP_007) ----
    # Built with sm.plateau(): flat-topped, steep-walled highground + gentle WALKABLE ramps.
    # All seed the nav patch on the floor so every plateau stays reachable up its ramp(s).
    #
    # V1 "Citadel": bases on the floor; a dramatic two-tier central plateau (4 ramps) to fight
    # over, ringed by six steep mesas that pinch the open floor into radial valley lanes.
    "citadel_3v3": dict(
        terrain="SC2_MP_007", scenario_id="SC2_CITADEL6", name="[6] Citadel (3v3, FFA)",
        out="_citadel_3v3.scd",
        anchors={1:(512,160), 2:(820,336), 3:(204,336), 4:(820,688), 5:(512,864), 6:(204,688)},
        teams=[[1,2,3],[4,5,6]],
        sculpt=(
            sm.plateau(512, 512, 84, 56, floor_y=10, ramps=['+x','-x','+z','-z'],
                       ramp_w=34, ramp_len=54, tiers=4, tier_w=11, tier_drop=11)   # 4-tier central citadel
            + sm.plateau(660, 226, 28, 36, floor_y=10, tiers=2, tier_w=9, tier_drop=13)  # 6 terraced ring mesas
            + sm.plateau(364, 226, 28, 36, floor_y=10, tiers=2, tier_w=9, tier_drop=13)
            + sm.plateau(864, 512, 28, 36, floor_y=10, tiers=2, tier_w=9, tier_drop=13)
            + sm.plateau(160, 512, 28, 36, floor_y=10, tiers=2, tier_w=9, tier_drop=13)
            + sm.plateau(660, 798, 28, 36, floor_y=10, tiers=2, tier_w=9, tier_drop=13)
            + sm.plateau(364, 798, 28, 36, floor_y=10, tiers=2, tier_w=9, tier_drop=13)
        ),
        patch=dict(max_slope=6, water_margin=0, seed=(512, 300)),
        economy=dict(base_mass=4, sites=9, per_site=3), norush=70,
    ),
    # V2 "Highlands": every base sits on its OWN steep plateau with a ramp down into a big central
    # arena (the carved valley floor) — holding the ramps and the low ground is the whole game.
    "highlands_3v3": dict(
        terrain="SC2_MP_007", scenario_id="SC2_HIGHLAND6", name="[6] Highlands (3v3, FFA)",
        out="_highlands_3v3.scd",
        anchors={1:(512,170), 2:(816,340), 3:(208,340), 4:(816,684), 5:(512,854), 6:(208,684)},
        teams=[[1,2,3],[4,5,6]],
        sculpt=(
            sm.plateau(512, 170, 50, 44, floor_y=8, ramps=['+z'], ramp_w=32, ramp_len=46, tiers=3, tier_w=8, tier_drop=11)
            + sm.plateau(816, 340, 50, 44, floor_y=8, ramps=['-x'], ramp_w=32, ramp_len=46, tiers=3, tier_w=8, tier_drop=11)
            + sm.plateau(208, 340, 50, 44, floor_y=8, ramps=['+x'], ramp_w=32, ramp_len=46, tiers=3, tier_w=8, tier_drop=11)
            + sm.plateau(816, 684, 50, 44, floor_y=8, ramps=['-x'], ramp_w=32, ramp_len=46, tiers=3, tier_w=8, tier_drop=11)
            + sm.plateau(512, 854, 50, 44, floor_y=8, ramps=['-z'], ramp_w=32, ramp_len=46, tiers=3, tier_w=8, tier_drop=11)
            + sm.plateau(208, 684, 50, 44, floor_y=8, ramps=['+x'], ramp_w=32, ramp_len=46, tiers=3, tier_w=8, tier_drop=11)
            + [("cone", 512, 512, 72, 22, "raise")]   # gentle central rise (walkable; keeps arena one island)
        ),
        patch=dict(max_slope=6, water_margin=0, seed=(512, 512)),
        economy=dict(base_mass=4, sites=9, per_site=3), norush=70,
    ),
    # V3 "Rift": two large steep plateaus (NE + SW) with ramps, split by a diagonal central valley
    # rift; the six bases ring the floor. The plateaus are positioned to never cover a spawn.
    "rift_3v3": dict(
        terrain="SC2_MP_007", scenario_id="SC2_RIFT6", name="[6] Rift (3v3, FFA)",
        out="_rift_3v3.scd",
        anchors={1:(512,160), 2:(824,336), 3:(204,336), 4:(824,688), 5:(512,864), 6:(204,688)},
        teams=[[1,2,3],[4,5,6]],
        sculpt=(
            sm.plateau(672, 300, 108, 42, floor_y=10, ramps=['-x','+z'], ramp_w=36, ramp_len=54, tiers=3, tier_w=14, tier_drop=11)
            + sm.plateau(352, 724, 108, 42, floor_y=10, ramps=['+x','-z'], ramp_w=36, ramp_len=54, tiers=3, tier_w=14, tier_drop=11)
        ),
        patch=dict(max_slope=6, water_margin=0, seed=(512, 512)),
        economy=dict(base_mass=4, sites=9, per_site=3), norush=70,
    ),
    # SCULPTED on a DIFFERENT base terrain — orange-red Martian desert (MP_202) — for a genuinely
    # NEW look (different palette/rim from green Emerald Crater). Bases sit on the flat desert floor
    # (y49) so the mass pads never straddle a step (no float); the drama is a 4-tier central citadel
    # plateau ringed by six terraced cover mesas, all raised out of the floor.
    "scorched_mesa_3v3": dict(
        terrain="SC2_MP_202", scenario_id="SC2_SCORCH6", name="[6] Scorched Mesa (3v3, FFA)",
        out="_scorched_mesa_3v3.scd",
        anchors={1:(505,190), 2:(736,306), 3:(844,598), 4:(505,822), 5:(204,639), 6:(194,387)},
        teams=[[1,2,3],[4,5,6]],
        sculpt=(
            sm.plateau(505, 506, 84, 88, floor_y=49, ramps=['+x','-x','+z','-z'],
                       ramp_w=34, ramp_len=56, tiers=4, tier_w=12, tier_drop=10)        # central citadel
            + sm.plateau(405, 333, 30, 74, floor_y=49, tiers=2, tier_w=10, tier_drop=12)  # six terraced cover mesas
            + sm.plateau(305, 506, 30, 74, floor_y=49, tiers=2, tier_w=10, tier_drop=12)
            + sm.plateau(405, 679, 30, 74, floor_y=49, tiers=2, tier_w=10, tier_drop=12)
            + sm.plateau(605, 679, 30, 74, floor_y=49, tiers=2, tier_w=10, tier_drop=12)
            + sm.plateau(705, 506, 30, 74, floor_y=49, tiers=2, tier_w=10, tier_drop=12)
            + sm.plateau(605, 333, 30, 74, floor_y=49, tiers=2, tier_w=10, tier_drop=12)
        ),
        patch=dict(max_slope=6, water_margin=0, seed=(505, 300)),
        economy=dict(base_mass=4, sites=9, per_site=3), norush=70,
    ),
    # RE-SKINNED open map: keep Emerald Crater's OPEN BOWL shape but repaint the ground to a dark
    # natural-rock biome (MP_301) for a genuinely new ashen/scorched LOOK — proving the texture
    # re-skin (reskin_terrain). No sculpt (stays open), props stripped barren.
    "ashen_basin_3v3": dict(
        terrain="SC2_MP_007", scenario_id="SC2_ASHEN6", name="[6] Ashen Basin (3v3, FFA)",
        out="_ashen_basin_3v3.scd",
        anchors={1:(511,152), 2:(823,331), 3:(200,332), 4:(512,872), 5:(823,692), 6:(200,692)},
        teams=[[1,2,3],[4,5,6]],
        reskin=sm.reskin_map("MP_007", "MP_301", "sc2_mp_007_", "sc2_mp_301_",
            [("grass01","ground01"), ("ground01","ground02"), ("rock01","hill01"),
             ("rock02","crystal"), ("sand01","ground02"), ("benthal01","ground01")]),
        patch=dict(max_slope=6, water_margin=0, seed=(512, 512)),
        economy=dict(base_mass=4, sites=9, per_site=3), norush=70,
        strip_props="all", minimap="dark",
    ),
    # RE-SKINNED open map #2: the same open bowl repainted to SNOW (MP_305 snow/floor/hill) for a
    # white ice-field look. No sculpt (open), props stripped.
    "frost_crater_3v3": dict(
        terrain="SC2_MP_007", scenario_id="SC2_FROST6", name="[6] Frost Crater (3v3, FFA)",
        out="_frost_crater_3v3.scd",
        anchors={1:(511,152), 2:(823,331), 3:(200,332), 4:(512,872), 5:(823,692), 6:(200,692)},
        teams=[[1,2,3],[4,5,6]],
        reskin=sm.reskin_map("MP_007", "MP_305", "sc2_mp_007_", "sc2_mp_305_",
            [("grass01","snow01"), ("ground01","floor01"), ("rock01","hill"),
             ("rock02","floor02"), ("sand01","snow01"), ("benthal01","floor03")]),
        patch=dict(max_slope=6, water_margin=0, seed=(512, 512)),
        economy=dict(base_mass=4, sites=9, per_site=3), norush=70,
        strip_props="all", minimap="snow",
    ),
    # REMIX 3v3: dry open green crater (Emerald Crater terrain), 6 spawns ringing the floor.
    "emerald_crater_3v3": dict(
        terrain="SC2_MP_007", scenario_id="SC2_EMERALD6", name="[6] Emerald Crater (3v3, FFA)",
        out="_emerald_crater_3v3.scd",
        anchors={1:(511,152), 2:(823,331), 3:(200,332), 4:(512,872), 5:(823,692), 6:(200,692)},
        teams=[[1,2,3],[4,5,6]],
        economy=dict(base_mass=4, sites=9, per_site=3), norush=70,
        patch=None,
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
