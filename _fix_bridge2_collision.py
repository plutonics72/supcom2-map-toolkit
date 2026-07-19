"""Lower buried collision-mesh verts under the Dune Rift causeways and ramps.

THE MISSING THIRD MOVEMENT GATE (found 19 Jul 2026): collision2.win.bdf holds a
coarse static collision mesh of the ORIGINAL terrain (CA_I01: 1175 verts). Raising
a deck over old HIGH ground leaves the old ridge triangles poking through the new
surface = invisible wall the engine steers units against — while costs.win.bdf and
hfield-based analysis both say "walkable" (every offline nav proof passes). This
was Bridge B's "not navigable" (old channel-bank ridge y~66 inside the y~56 deck,
east half) and, retroactively, v1's "cannot cross the ridge at its end". Bridge A
never suffered it because the old ground under its deck (lake bed, y 34-45) lies
BELOW the deck.

Format (Robotronic, GPG forums): payload = u32[version=1, numVerts, vertOffset=24,
numIndices, indexOffset, extraOffset]; verts = 3 floats (world x,y,z); then index
soup; then a BVH-ish "extra" tree (untouched — lowering-only vertex edits keep the
stale tree safe: broad-phase may still hit, narrow-phase misses).

Rule of thumb for any future deck/ramp: costs + hfield + collision2 must agree.
"""
import zipfile, struct, io, os, shutil
import sc2maps as sm

b2 = os.path.join(sm.GAMEDATA, "_dune_rift_bridge2.scd")
zf = zipfile.ZipFile(b2); names = zf.namelist()
ent = {n: zf.read(n) for n in names}; zf.close()
col_n = [n for n in names if "collision2" in n][0]
hf_n = [n for n in names if n.endswith(".hfield.win.bdf")][0]
H, w = sm.hfield_heights(ent[hf_n])
raw = ent[col_n]
pay = bytearray(sm.read_bdf_payload(raw))
ver, nv, voff, ni, ioff, xoff = struct.unpack_from("<6I", pay, 0)
assert ver == 1 and voff == 24

ZONES = [  # x0, z0, x1, z1: deck bands (+aprons/margin) and ramp rectangles
    (395, 750, 745, 825),      # bridge B band + aprons
    (395, 550, 727, 622),      # bridge A band + aprons
    (315, 425, 412, 505),      # ramp W1
    (327, 245, 395, 315),      # ramp W2
    (625, 465, 735, 550),      # ramp E1
]
changed = 0
for i in range(nv):
    off = voff + 12*i
    x, y, z = struct.unpack_from("<3f", pay, off)
    for (x0, z0, x1, z1) in ZONES:
        if x0 <= x <= x1 and z0 <= z <= z1:
            if 0 <= x < 1024 and 0 <= z < 1024:
                ground = sm.hf_sample(H, w, x, z)
                if y > ground - 0.2:               # lower ONLY, never raise
                    struct.pack_into("<3f", pay, off, x, ground - 0.5, z)
                    changed += 1
            break
print(f"collision verts lowered: {changed} of {nv}")

new_col = sm.rebuild_bdf(raw, bytes(pay))
shutil.copy2(b2, b2 + ".PRE_COLFIX.bak")
out = dict(ent); out[col_n] = new_col
buf = io.BytesIO(); zo = zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED)
for n, d in out.items(): zo.writestr(n, d)
zo.close()
open(b2, "wb").write(buf.getvalue())
print(f"INSTALLED ({os.path.getsize(b2):,} B)")
