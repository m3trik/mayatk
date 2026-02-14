-- Pack existing UVs into 0-1 space.
-- Use when islands are already cut and unfolded.

ZomSelect({PrimType="Island", Select=true, ResetBefore=true})

ZomIslandGroups({
    Mode="DistributeInTilesEvenly",
    MergingPolicy=8322,
    GroupPath="RootGroup",
})

ZomPack({
    ProcessTileSelection=false,
    RecursionDepth=2,
    RootGroup="RootGroup",
    Scaling={Mode=2},
    Rotate={Step=90},
    Translate=true,
    LayoutScalingMode=2,
    Margin=2,
    Quality=1,
})
