-- Optimise + repack an EXISTING UV layout.
-- Relaxes stretch / angle distortion across already-cut islands and
-- repacks them. Distinct from pack.lua (which doesn't optimize first)
-- and unwrap_hard/organic.lua (which re-cut seams from scratch).
--
-- Layout-scale invariants are hardcoded (Scaling.Mode=0, Mix=true,
-- LayoutScalingMode=0); the pre-scale / layout-scale / mix-scale
-- widgets auto-hide for this preset because those placeholders aren't
-- substituted here.
--
-- 2020.1 constraint: the ZomIslandGroups + ZomPack signatures below
-- mirror pack.lua exactly. Do NOT switch to
-- ZomIslandGroups({Mode="DistributeTilesContent", FreezeIslands=true,
-- UseTileLocks=true, UseIslandLocks=true, ...}) or add AuxGroup /
-- WorkingSet to ZomPack -- those fields exist in newer Rizom (and the
-- Titus 3ds Max bridge uses them) but access-violate on 2020.1. Raise
-- a version gate in parameters.py first if the layout-preserve
-- variant is brought back.

-- 1. Optimise across all visible / flat / unlocked islands.
-- Canonical ZomOptimize signature, matching SideFX Labs and the Titus
-- 3ds Max RizomUV bridge.
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
    KeepMetric=true,
    PinMapName="Pin",
})

-- 2. Group islands. Matches pack.lua / unwrap_*.lua so behaviour is
-- consistent across presets (and known-safe on 2020.1).
ZomIslandGroups({
    Mode="DistributeInTilesEvenly",
    MergingPolicy=8322,
    GroupPath="RootGroup",
})

-- 3. Pack with preserve-scale invariants hardcoded:
--   Scaling.Mode=0 -- no pre-scale of incoming islands
--   Scaling.Mix=true -- mix incoming scale with computed
--   LayoutScalingMode=0 -- don't rescale the packed layout to fit tile
-- Rotate/translate/resolution/mutations stay user-tunable.
ZomPack({
    ProcessTileSelection=false,
    RecursionDepth=__RECURSION_DEPTH__,
    RootGroup="RootGroup",
    Scaling={Mode=0, Mix=true},
    Rotate={
        Step=__ROTATE_STEP__,
        Enable=__PACK_ROTATE_ENABLE__,
    },
    Translate=__PACK_TRANSLATE__,
    LayoutScalingMode=0,
    MaxMutations=__PACK_MAX_MUTATIONS__,
    Resolution=__PACK_RESOLUTION__,
})
