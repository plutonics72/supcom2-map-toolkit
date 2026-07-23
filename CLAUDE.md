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

## Current shipped state (23 Jul 2026)

All maps live in the Drive folder with hashes; internal build-script versions
differ from the user-facing versions in `READ ME - Install.txt` (that file wins).

| Map (lobby name) | File | Map id | Status |
|---|---|---|---|
| [6] Dune Rift (3v3, FFA) v4 | `_dune_rift_3v3.scd` | `SC2_DUNE6` | good (23 Jul mesh sink-sync + first collision snap) |
| [6] Dune Rift - Two Bridges (3v3) v6 | `_dune_rift_bridge2.scd` | `SC2_DUNEB2` | good (user-confirmed 23 Jul: "a lot better") |
| [4] Dune Rift (2v2) v3 | `_dune_rift_2v2.scd` | — | good (23 Jul sync); still lacks the 3v3 mass-pad/ramp fixes |
| [4] Treallach Strait (2v2) | `_treallach_strait.scd` | `SC2_TRST01` | good |
| [8] Iskellian Extended (4v4) v5 | `_iskellian_ext8.scd` | `SC2_ISKEX3` | good (user-confirmed; islands 2.5×, 6 masses each) |
| Frost Crater / Ashen Basin (3v3) | `_frost_crater_3v3.scd` / `_ashen_basin_3v3.scd` | — | good (re-skins) |
| Crucible / Crossfire Atoll / The Maw | `_*_by_chris.scd` | — | user-made, untouched |

Latest-generation build scripts (each is self-contained, reads the game files +
prior installed maps, verifies, installs):

- `_build_dunerift_bridges_v8.py` — Two Bridges, current. v7's full pipeline
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

- **Two Bridges turret no-fire (OPEN, differential pending)**: turrets at the
  middle crossing don't fire at in-range targets. Installed collision verified
  CLEAN — this is NOT the stale-collision class. Leading suspects: deck-edge
  LOS shadow (a deck turret can't sight low water-level targets past the deck
  lip — candidate fix: bevel the deck z-edges) vs. the palm railing props
  eating shots along the span (candidate fix: thin/relocate rails). Waiting on
  play-test detail: turret position (deck vs shore) × target position (on the
  bridge vs on the water beside it).
- **Dune Rift 2v2**: port the 3v3 v3 fixes (54 mass pads, basin ramps) — same
  script pattern, different file. Low risk, user-visible win.
- **Boras Naval Test Range (MP_305)**: the best untouched canvas — 6-player
  watered skirmish map, ideal for a true 3v3 navy map (Treallach is the only
  watered base shipped so far). Full five-file pipeline applies.
- **Cosmetics not cracked** (accepted gaps): baked per-vertex normals (raised
  ground keeps old shading), per-region texture painting (re-skins are
  whole-set), skybox/environment, water on born-dry maps.
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
