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
