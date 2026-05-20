-- Repack existing UV islands into the 0-1 tile.
-- Use when seams are already cut and unfolded; this only redistributes shells.

ZomSelect({PrimType="Island", Select=true, ResetBefore=true})

-- Group every island under RootGroup and distribute across tiles.
-- MergingPolicy=8322 is the canonical bitmask used by RizomUV's reference
-- bridges (e.g. Cinema4D plugin) to auto-merge mirrored / stacked islands.
ZomIslandGroups({
    Mode="DistributeInTilesEvenly",
    MergingPolicy=8322,
    GroupPath="RootGroup",
})

ZomPack({
    ProcessTileSelection=false,
    RecursionDepth=__RECURSION_DEPTH__,
    RootGroup="RootGroup",
    Scaling={Mode=__SCALING_MODE__, Mix=__SCALING_MIX__},
    Rotate={
        Step=__ROTATE_STEP__,
        Enable=__PACK_ROTATE_ENABLE__,
    },
    Translate=__PACK_TRANSLATE__,
    LayoutScalingMode=__LAYOUT_SCALING_MODE__,
    MaxMutations=__PACK_MAX_MUTATIONS__,
    Resolution=__PACK_RESOLUTION__,
})
