-- Optimise + repack an EXISTING UV layout.
-- Relaxes stretch / angle distortion across already-cut islands and
-- repacks them. Distinct from pack.lua (which doesn't optimize first)
-- and unwrap_hard/organic.lua (which re-cut seams from scratch).
--
-- Structurally this IS pack.lua with an optimise step spliced in: the same
-- island-selection opener, the same optional keep-stacked partial
-- (templates/keep_stacked_block.lua) and the same shared group + pack +
-- placement partial (templates/pack_block.lua). Include tokens are written
-- WITHOUT their surrounding double underscores in prose (KEEP_STACKED_BLOCK,
-- PACK_BLOCK) -- test_uv_rizom_bridge's "no unresolved placeholders" guard
-- scans the WHOLE constructed script, comments included.
--
-- Why it stopped hand-rolling its own pack (measured 2026-08-17 through the
-- real 2020.1, three projection-UV spheres, incoming UV area 0.8100 in-tile):
--   * The old inline ZomIslandGroups + ZomPack was a NO-OP. With no island
--     selection there was nothing to group, so RootGroup stayed empty and the
--     pack packed nothing -- LayoutScalingMode 0/1/2 all saved byte-identical
--     output. The preserve-scale invariants it hardcoded (Scaling.Mode=0,
--     Mix=true, LayoutScalingMode=0) were therefore decorative.
--   * KeepMetric=true rescales the optimised result to the mesh's 3D metric,
--     so with nothing packing afterwards the preset SHIPPED an area blow-up:
--     0.8100 -> 29.0800 spanning u[0.004,5.686] v[0.004,6.835], i.e. ~6x7
--     UDIM tiles (worse through the Maya path: 84x, ~9x9 tiles).
-- With the selection + shared block restored and KeepMetric=false, the same
-- input lands at area 0.6142 inside u[0.002,0.829] v[0.002,0.998], and the
-- optimisation itself is untouched: per-face UV/3D area spread (coefficient
-- of variation) goes 0.9301 -> 0.4269, the SAME value the KeepMetric=true run
-- produces. KeepMetric was a pure global scale term, not solver quality.
--
-- Preserving the incoming layout scale is now a real, reachable choice rather
-- than a hardcoded promise: set Pre-scale to "Keep current scale" and Layout
-- Scale to "Keep positions" in the panel and the packer honours it (measured:
-- area 0.8100 -> 0.8100 with those two, vs 29.0800 if KeepMetric is re-armed).
--
-- Confirmed on the real Maya path too (headless mayapy: FBX export, island
-- groups, UV transfer back), three auto-projected spheres, same input either
-- side: 1.9238 -> 36.6722 across u[0.004,7.217] v[0.004,7.597] before,
-- 1.9238 -> 0.6251 inside u[0.002,0.968] v[0.002,0.998] after (pack.lua on
-- the same scene: 0.6652).
--
-- 2020.1 constraints, still in force:
--   * ZomSelect must come BEFORE ZomOptimize. The identical line placed AFTER
--     it access-violates (rc 0xC00000FF, nothing saved).
--   * Do NOT switch ZomIslandGroups to Mode="DistributeTilesContent" or add
--     AuxGroup / WorkingSet to ZomPack -- those fields exist in newer Rizom
--     (the Titus 3ds Max bridge uses them) but access-violate on 2020.1.
--     Raise a version gate in parameters.py first.
--
-- Host-side export scope (read by the bridge slots before launch; echoed here so the
-- panel exposes the Scope combo): scope=__SCOPE__

-- 1. Select every island, so the grouping + pack below have something to act
-- on. Without this the whole tail silently no-ops (see above).
ZomSelect({PrimType="Island", Select=true, ResetBefore=true})

-- 2. Optimise across all visible / flat / unlocked islands.
-- Canonical ZomOptimize signature, matching SideFX Labs and the Titus
-- 3ds Max RizomUV bridge -- except KeepMetric, which is false here because
-- this preset's contract is the INCOMING UV scale, not the 3D metric.
ZomOptimize({
    PrimType="Edge",
    WorkingSet="Visible&Flat&UnLocked",
    Iterations=__ITERATIONS__,
    Mix=__MIX__,
    AngleDistanceMix=1,
    RoomSpace=__ROOM_SPACE__,
    MinAngle=__MIN_ANGLE__,
    BorderIntersections=true,
    TriangleFlips=true,
    KeepMetric=false,
    PinMapName="Pin",
})

-- 3. Optionally keep islands that overlap on arrival stacked through the
--    repack (shared partial -- see templates/keep_stacked_block.lua).
__KEEP_STACKED_BLOCK__

-- 4. Group + pack + UDIM/coverage placement (shared partial -- see
--    templates/pack_block.lua). Same recipe pack.lua and the unwrap_*.lua
--    presets use, so the pack knobs stay defined in exactly one place.
__PACK_BLOCK__
