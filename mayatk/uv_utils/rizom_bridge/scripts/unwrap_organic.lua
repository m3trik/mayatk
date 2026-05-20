-- Organic auto-unwrap pipeline.
-- Tuned for smooth sculpted / scanned / character meshes where seams do
-- not align with sharp dihedral angles. Leans on HandleCutter (cuts holes
-- and handles into disks) and PipesCutter (cuts tubes / limbs along their
-- axis), plus StretchLimiter to break up shells that would otherwise
-- distort wildly when flattened.
--
-- The shared Sharp Angle knob defaults to 39 (hard-surface tuned); raise
-- it (60-90+) for organic so smooth surface noise isn't treated as a seam.
-- QuadLoopCutter is OFF -- organic topology often lacks clean quad loops,
-- and forcing loop-based cuts produces ragged seams on sculpts.

ZomSelect({
    PrimType="Edge",
    WorkingSet="Visible&UnLocked",
    IslandGroupMode="Group",
    Select=true,
    ResetBefore=true,
    ProtectMapName="Protect",
    FilterIslandVisible=true,
    Auto={
        SharpEdges={AngleMin=__SHARP_ANGLE__},
        PipesCutter=true,
        HandleCutter=true,
        QuadLoopCutter=false,
        StretchLimiter=true,
        Quality=0.5,
        StoreCoordsUVW=true,
        FlatteningMode=0,
        FlatteningUnfoldParams={
            StopIfZeroMix=true,
            BorderIntersections=true,
            TriangleFlips=true,
        },
    },
})

ZomCut({PrimType="Edge", WorkingSet="Visible&UnLocked"})

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
