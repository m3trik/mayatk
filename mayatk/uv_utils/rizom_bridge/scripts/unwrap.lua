-- Hard-surface auto-unwrap pipeline.
-- Detects hard edges and geometric features as seams, then unfolds + packs.

-- 1. Auto-detect seams from hard edges, handles, pipes, and skeleton features.
-- ResetBefore=true wipes any prior edge selection so we start clean.
-- Note: FilterAngle is intentionally omitted -- RizomUV 2020.1 crashes
-- (access violation) when FilterAngle is paired with any Auto cutter.
-- Cutters use their own geometric heuristics; the angle filter is a
-- post-pass that can be re-introduced once the 2020.1 bug is gone.
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
})

-- 2. Cut along the detected seams.
ZomCut({PrimType="Edge"})

-- 3. Unfold each shell.
ZomUnfold({
    PrimType="Edge",
    MinAngle=__MIN_ANGLE__,
    Mix=__MIX__,
    Iterations=__ITERATIONS__,
    PreIterations=__PRE_ITERATIONS__,
    StopIfOutOFDomain=false,
    RoomSpace=__ROOM_SPACE__,
    PinMapName="Pin",
    ProcessNonFlats=true,
    ProcessSelection=true,
    ProcessAllIfNoneSelected=true,
    ProcessJustCut=true,
    BorderIntersections=true,
    TriangleFlips=true,
})

-- 4. Optimise (relax stretch / angle distortion across the unfolded shells).
-- Canonical signature, matching SideFX Labs and the 3ds Max RizomUV bridge.
-- The pre-existing PrimType="Island" + OptimizeStretch/OptimizeAngle args
-- were not real ZomOptimize parameters and crashed RizomUV 2020.1.
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

-- 5. Group + pack. Keep the recipe in sync with pack.lua.
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
