# SC2 Map Toolkit — project state & working conventions

Custom-map development for Supreme Commander 2 via reverse-engineered file formats.
Read [README.md](README.md) (capabilities + the five-file consistency stack + field
notes) and [FORMATS.md](FORMATS.md) (byte-level formats) before touching anything —
they encode weeks of hard-won engine facts. This file covers project state, the
build/deploy loop, and open work.

## Environment (new machine setup)

- Python 3.9+, **standard library only** (numpy optional; `imageio-ffmpeg` via pip
  only if you need to extract frames from play-test videos).
- Supreme Commander 2 installed via Steam. The toolkit auto-detects the install;
  override with `SC2_GAMEDATA` env var pointing at
  `...\Supreme Commander 2\gamedata`.
- This repo contains **no game assets** (see `.gitignore`: `*.scd/*.dds/*.bdf/...`
  never get committed — the repo is original tooling + docs only, MIT-licensed).
  Built maps therefore do NOT travel with the repo. The release channel is the
  **Google Drive folder `Supreme Commander 2 Maps`** (synced via Google Drive for
  desktop): grab the current `.scd` files there and copy them into `gamedata\`.
  `READ ME - Install.txt` in that folder lists every map with size + SHA-256 —
  it is the canonical user-facing version record.
- Telegram notifications (user's standing preference: message after every task /
  fetch their play-test screenshots+videos) use the project's **own bot** via
  `scripts/sc2bot.py` (stdlib-only): `send --tag done|failed|action|info
  --title "SC2 Maps" "summary"`; `fetch --wait 60` pulls play-test media into
  `_tg/` (Bot API `getUpdates`/`getFile`, ≤20 MB, ~24 h window). Config (token +
  chat id) lives OUTSIDE this public repo — resolution: `SC2BOT_CONFIG` env →
  `~\OneDrive\Personal\sc2bot\telegram.json` → `~\.config\sc2bot\telegram.json`.
  New machine or new bot: the user runs `python scripts/sc2bot.py setup` in
  their own terminal (the token never passes through chat or the repo).
  The Trade-lab repo's bot is **retired for SC2 traffic** — it belongs to the
  user's separate trading work; never send SC2 messages through it.

## Current shipped state (9 Aug 2026)

All maps live in the Drive folder with hashes; internal build-script versions
differ from the user-facing versions in `READ ME - Install.txt` (that file wins).

| Map (lobby name) | File | Map id | Status |
|---|---|---|---|
| [6] Dune Rift (3v3, FFA) v4 | `_dune_rift_3v3.scd` | `SC2_DUNE6` | good (23 Jul mesh sink-sync + first collision snap) |
| [6] Dune Rift - Two Bridges (3v3) v9 | `_dune_rift_bridge2.scd` | `SC2_DUNEB2` | released 25 Jul (nav repair + hover water fixes + oasis honesty); the LOBBY NAME carries the version — bump it every build |
| [4] Dune Rift (2v2) v3 | `_dune_rift_2v2.scd` | — | good (23 Jul sync); still lacks the 3v3 mass-pad/ramp fixes |
| [4] Treallach Strait (2v2) | `_treallach_strait.scd` | `SC2_TRST01` | good |
| [8] Iskellian Extended (4v4) v6 | `_iskellian_ext8.scd` | `SC2_ISKEX3` | released 25 Jul (ships-ashore fixed: island shorelines were water-classified at 16-cell block granularity — every island-carrying waterDepth block now dry; frozen units stood on that band; island-zone collision snap; versioned lobby name) |
| Frost Crater / Ashen Basin (3v3) | `_frost_crater_3v3.scd` / `_ashen_basin_3v3.scd` | — | good (re-skins) |
| Crucible / Crossfire Atoll / The Maw | `_*_by_chris.scd` | — | user-made, untouched |
| [6] Boolon Complex Extended (3v3) v4 | `_boolon_ext.scd` | `SC2_BOOLX1` | installed LOCALLY only (old machine, 9 Aug) — NOT on Drive; user still reports "no change" in-game; **v5 fix prepared, NOT yet run** (see next steps) |
| [6] Boolon Harbor (3v3) v4 | `_boolon_harbor.scd` | `SC2_BOOLW1` | installed LOCALLY only (old machine, 9 Aug) — NOT on Drive; in-game self-verified (real sea, deck platforms); awaiting user play-test verdict |

Latest-generation build scripts (each is self-contained, reads the game files +
prior installed maps, verifies, installs):

- `_build_dunerift_bridges_v9.py` — Two Bridges, current. v8 + nav noise repair:
  opening-only pass gives every gentle-slope dry 255-cell cost 1 on all five
  layers (penalties preserved), pocket revert re-closes repair cells not
  flood-connected to the main landmass, and two new gates: decks-removed
  disconnection (no third crossing — the old carve_box water-walk ford stays
  dead structurally via the wet_now closure) and zero main-adjacent blocked
  flat-dry cells in the approach corridors. Root cause (24 Jul videos): the
  base bake left 255 patches on flat dry desert — units threaded the gaps in
  single-file conga lines and jammed at gap mouths.
- `_build_dunerift_bridges_v8.py` — superseded by v9. v7's full pipeline
  (capped deck planes + aprons, global collision snap, legacy-nav overlay with
  ALL FIVE layers opened on decks, waterDepth regen + retarget, minimap
  causeway painting, prop railings, erosion r=3/r=5 gates) PLUS map-wide mesh
  sink-sync and a map-wide mesh gate. v7 synced/verified the mesh only inside
  the causeway rects — 177 tiles of inherited terracing divergence shipped
  (render mesh up to +40 ABOVE the heightfield: buildable ground inside drawn
  dunes, structures buried, units vanished).
- `_fix_dunerift_meshsync.py` — in-place port of the v8 fix to the 3v3 + 2v2
  (both also got their FIRST global collision snap: 751 verts up to +69 above
  ground = turret fire into invisible hills). Rewrites only terrain +
  collision2 entries; every other archive entry stays byte-identical.
- `_build_iskellian_v6.py` — Iskellian, current. Island growth with naval-corridor
  guards, stock-style layer treatment, waterDepth regen (decode-verified dry
  block; asserts water blocks stay byte-identical to stock), mass pads + inland
  placement, minimap island painting.
- `_build_boolon_ext.py` — Boolon Complex Extended (`SC2_BOOLX1` on stock
  `SC2_MP_104`, a born-dry deck-over-void map). East zone (690,200,832,830)
  unified into one platform + 10-cell dilation + 4 interior void fills
  (138k new cells), Laplacian relaxation, 6 new masses, minimap/preview
  regeneration. v4 mesh = RIGID-STACK translation (translate every in-zone
  vert by the local hfield delta, preserving inter-layer offsets — v2/v3
  collapsed the void's stacked layers to one plane and caused map-wide
  z-fighting moiré). Script currently at **v5, prepared but NOT RUN**: adds
  the appearance-attr transplant (copy 20B packed vertex attrs, bytes 12..32
  of the 32B stride, from nearest original deck-surface donor verts onto
  translated verts in the walking-surface band of new cells). Rationale: new
  ground inherits the void's baked attrs and renders as pale abyss-mist —
  functionally walkable but INVISIBLE as land; the user's "no change from
  standard" cursor probe mapped to game (689,662), ONE CELL west of the fill
  edge. The transplant was tried once as v3 but never visually isolated
  (v3 also carried the moiré bug); v5 = v4's verified geometry + transplant
  only, positions untouched.
- `_build_boolon_mirror.py` — Boolon Harbor (`SC2_BOOLW1`): Boolon's deck
  layout as flat platforms (y=66) on the Treallach (`SC2_MP_302`, WL=56)
  watered donor; full five-file pipeline incl. `write_waterdepth_dds_mips`;
  in-game verified 9 Aug (sea renders, cross-deck unit march).
- Older `_build_*`/`_fix_*` scripts are kept as history — each docstring records
  the bug its successor fixed. Prefer copying the newest as a starting point.

## Build & deploy loop

1. Edit/copy a `_build_*.py` script; run it with the game **closed**
   (`python _build_x.py`). It installs straight into `gamedata\`.
2. **Restart SC2 fully** — archives mount at launch; overwriting an installed
   `.scd` while the game runs does nothing (even quit-to-menu + reload).
3. Scripts must end with verification gates (erosion-clearance routes, mesh/
   collision/waterDepth consistency) and refuse to install on failure. Keep the
   pattern: three "all checks pass" builds still shipped in-game blockers —
   the checks are necessary, not sufficient. The user play-tests and reports;
   their odd symptom detail usually names the guilty file (see README caveats).
4. Back up the installed `.scd` (`shutil.copy2(p, p + ".BAK")`) before
   overwriting; backups stay in `gamedata\` (not in the repo).
5. Release = copy to the Drive folder + local `Documents\SC2_maps_to_share\`,
   update `READ ME - Install.txt` (description, size, SHA-256, version bump),
   Telegram the user. Multiplayer needs byte-identical files — friends must
   replace old copies or they desync.
6. Commit the build script + any toolkit/doc changes; push. Never commit
   `.scd/.dds/...` (gitignored), `_tg/` (user's personal play-test media), or
   `*.log`.

## Machine-local assets that do NOT travel with the repo

- `Documents\SC2_custom_maps\research\` — GPG-forum format docs
  (`map_formatting.html`, `bdf_tool_thread.txt`), community true-layout maps
  (`pandora.scd`, `greenland.scd`), mesh-patch experiment bins, saved stock
  waterDepth copies. Copy this folder if possible; the format docs are the only
  irreplaceable part (the rest can be re-extracted from the game).
- `gamedata\*.bak` version backups of installed maps.
- Old-machine Claude memory (engine model, in-game UI-driving tricks) — the
  durable facts are all in README.md / FORMATS.md now.

## Open next steps

- **Boolon Extended v5 — BUILT + INSTALLED on the laptop (9 Aug), awaiting
  the user's visual check**. Pre-run adversarial review caught a BLOCKER:
  the east-unification loop plated 8,186 cells INSIDE the forbidden chasm
  vertex desert (x≤741, z 480–650 overlap of EAST_ZONE) — a guard now skips
  it, and the transplant gained gates (positions byte-asserted untouched;
  recolored>1000; attrs actually changed). Run results: 131,931 new cells,
  9,392 verts re-attributed (9,186 changed, 0 no-donor skips), all r=3/r=5
  routes + east platform OK. In-game self-verification NOT possible on this
  machine (user declines screen access — do not re-request); the user checks
  visually instead: east platform x 690–832 should render as deck ground,
  not pale mist, at both zooms. Fallback if patchwork-noisy: single fixed
  donor attr (see nearest_attr note). On approval: release BOTH Boolon maps
  per the release loop. NOTE: on this machine the game may never have had
  the map before — fresh lobby entry "[6] Boolon Complex Extended (3v3) v5".
- **Boolon Harbor v4 — awaiting user play-test**. Installed on the old
  machine only. If the user approves, rebuild/install on request and release
  to Drive alongside Extended.
- **Port the full v12 nav stack to the 3v3 and 2v2**: nav noise repair,
  hover-land unify, stock water-layer opening, shore ribbon, pocket revert,
  and the dried-oasis honesty fix (the same fake-dry gully exists on both —
  the commander-reroute trap is live there). Scripts: `_build_dunerift_
  bridges_v12.py` has every pass; an in-place `_fix_*` port like
  `_fix_dunerift_meshsync.py` is the pattern.
- **Turret "not hitting" — RESOLVED as game mechanics, not map data (26 Jul)**.
  78s combat video, frame-by-frame forensics (3 overlapping analysts): the
  turrets FIRE; their shells travel and expire mid-air at the same point every
  salvo — the ordered targets sat ~2x beyond the turrets' real weapon range
  (manual attack orders are accepted with no range feedback at strategic
  zoom). Meanwhile enemy rocket artillery legitimately outranges defense
  turrets map-wide, and ground turrets on BOTH sides cannot engage aircraft
  (the user's bomber loitered 25s inside the enemy perimeter untouched and
  did all the actual damage). Earlier "turrets don't fire from the east bank"
  report was almost certainly the same asymmetry. Nothing in the five-file
  stack can or should change weapon blueprints. Genuine-bug tripwire if it
  recurs: a turret holding fire against a GROUND unit standing INSIDE its
  displayed range ring — that clip would reopen the case.
- **Dune Rift 2v2**: port the 3v3 v3 fixes (54 mass pads, basin ramps) — same
  script pattern, different file. Low risk, user-visible win.
- **Boras Naval Test Range (MP_305)**: the best untouched canvas — 6-player
  watered skirmish map, ideal for a true 3v3 navy map (Treallach is the only
  watered base shipped so far). Full five-file pipeline applies.
- **Cosmetics not cracked** (accepted gaps): baked per-vertex normals (raised
  ground keeps old shading), per-region texture painting (re-skins are
  whole-set), skybox/environment, water on born-dry maps. NEW (9 Aug evening):
  the 20B packed vertex appearance attrs are POSITION-DEPENDENT and near-unique
  per vert (4,302 distinct on MP_104's decks, modal share 2) — nearest-donor
  transplant renders as map-wide zebra striping (v5, user photos); a single
  fixed attr (v6) is the only remaining variant. If v6 also fails, born-dry
  void CANNOT be made to render as ground — extend such maps via a REAL
  terrain donor instead (the Boolon Harbor pattern).
- **Engine quirk to remember**: SC2's pathfinder gives up on very long
  cross-map orders (units stall mid-route; staged waypoints work). Not our bug,
  but it colors play-test reports.
- **Prop-based "man-made bridge" look** is minimal (rock/shrub/palm rows).
  If more polish is wanted: denser prop rows, or investigate decals
  (`decals.win.bdf`, undocumented).

## Working style that works here

- One hypothesis, one measurement, then edit — the engine punishes assumptions
  (five separate "obvious" models were wrong this project: live-vs-baked maps,
  layer roles, waterDepth alpha polarity, collision relevance, stock-nav trust).
- Verify from the **installed file**, not in-memory state; search BDF
  **payloads** (decompressed), never containers.
- When a play report contradicts analysis, the report wins. Ask which units,
  which direction, which spot — the differential is the diagnosis.
