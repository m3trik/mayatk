-- Optimise + repack existing UV layouts.
-- Use when seams are already cut: relaxes stretch / angle distortion, then packs.
-- Distinct from pack.lua (which just repacks) and unwrap.lua (which re-cuts seams).

-- 1. Optimise across all visible / flat / unlocked islands.
-- Canonical ZomOptimize signature, matching SideFX Labs and the 3ds Max
-- RizomUV bridge. PrimType="Edge" is correct here -- this is an edge-based
-- relaxation that operates on each island's interior triangles.
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

-- 2. Group + pack. Keep the recipe in sync with pack.lua.
ZomIslandGroups({
    Mode="DistributeInTilesEvenly",
    MergingPolicy=8322,
    GroupPath="RootGroup",
})
ZomPack({
    ProcessTileSelection=false,
    RecursionDepth=__RECURSION_DEPTH__,
    RootGroup="RootGroup",
    Scaling={Mode=__SCALING_MODE__},
    Rotate={Step=__ROTATE_STEP__},
    Translate=true,
    LayoutScalingMode=__LAYOUT_SCALING_MODE__,
})
