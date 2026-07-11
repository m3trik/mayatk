-- Hard-surface auto-unwrap pipeline.
-- Welds existing seams (optional), detects sharp dihedral edges (modeled
-- creases), handles, and pipes as seams, then cuts, unfolds, and packs.
--
-- Use this for mechanical / architectural / hard-edge meshes whose seams
-- already exist in the topology as crisp angles. For smooth sculpted /
-- organic meshes use unwrap_organic.lua instead.
--
-- Auto.* key is SharpEdges (NOT HardEdge) -- RizomUV silently drops any
-- unknown key inside Auto={...}, so a typo here makes the cutter no-op.
-- Verify any Lua change against a live run:
-- test/rizom_headless_probe.py (2020.1 access-violates on
-- fields it doesn't know).

-- 0. Weld First (default on): weld ALL existing seams so the auto-seam
--    re-cuts from a clean surface. Off = keep the incoming seams and only
--    add the newly detected cuts on top.
if __WELD_SEAMS__ then
    ZomSelect({PrimType="Edge", WorkingSet="Visible&UnLocked", Select=true, All=true, ResetBefore=true})
    ZomWeld({PrimType="Edge", WorkingSet="Visible&UnLocked"})
end

-- 1. Auto-detect seams. SharpEdges.AngleMin = dihedral threshold in degrees.
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
        QuadLoopCutter=true,
        StretchLimiter=true,
        Quality=0.25,
        StoreCoordsUVW=true,
        FlatteningMode=0,
        FlatteningUnfoldParams={
            StopIfZeroMix=true,
            BorderIntersections=true,
            TriangleFlips=true,
        },
    },
})

-- 2. Cut along the detected seams.
ZomCut({PrimType="Edge", WorkingSet="Visible&UnLocked"})

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
