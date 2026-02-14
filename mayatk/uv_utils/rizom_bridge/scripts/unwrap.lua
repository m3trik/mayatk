-- Auto-unwrap for hard-surface objects.
-- Detects hard edges, cuts seams, unfolds, optimises, and packs.

-- 1. Clear existing seams
ZomSelect({PrimType="Edge", Select=true, ResetBefore=true})
ZomClear({PrimType="Edge"})

-- 2. Auto-detect hard edges as seams
ZomSelect({
    PrimType="Edge",
    Select=true,
    ResetBefore=true,
    Auto={
        HardEdge=true,
        HandleCutter=true,
        PipesCutter=true,
        Skeleton={},
    },
    FilterAngle=30,
})

-- 3. Cut seams
ZomCut({PrimType="Edge"})

-- 4. Unfold
ZomUnfold({
    PrimType="Edge",
    MinAngle=1e-005,
    Mix=1,
    Iterations=3,
    PreIterations=10,
    StopIfOutOFDomain=false,
    RoomSpace=0.01,
    PinMapName="Pin",
    ProcessNonFlats=true,
    ProcessSelection=true,
    ProcessAllIfNoneSelected=true,
    ProcessJustCut=true,
    BorderIntersections=true,
    TriangleFlips=true,
})

-- 5. Optimise
ZomSelect({PrimType="Island", Select=true, ResetBefore=true})
ZomOptimize({
    PrimType="Island",
    OptimizeStretch=true,
    OptimizeAngle=true,
    MinAngle=5,
    MaxIterations=50,
})

-- 6. Group and pack
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
