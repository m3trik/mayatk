-- Organic auto-unwrap pipeline.
-- Segments smooth sculpted / scanned / character meshes into quasi-
-- developable patches (Rizom's Mosaic segmentation). Dihedral-angle seam
-- detection is useless on smooth surfaces -- there are no crisp angles to
-- find -- so this preset drives island creation from Developability
-- (flattenability) instead. HandleCutter opens holes / handles into
-- disks, PipesCutter cuts tubes / limbs along their axis, and
-- StretchLimiter breaks up any remaining shell that would distort wildly
-- when flattened.
--
-- QuadLoopCutter is OFF -- organic topology often lacks clean quad loops,
-- and forcing loop-based cuts produces ragged seams on sculpts.
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

-- 1. Mosaic segmentation. Developability: lower = fewer, larger islands
--    (more distortion); higher = more, flatter islands (more seams).
ZomSelect({
    PrimType="Edge",
    WorkingSet="Visible&UnLocked",
    IslandGroupMode="Group",
    Select=true,
    ResetBefore=true,
    ProtectMapName="Protect",
    FilterIslandVisible=true,
    Auto={
        QuasiDevelopable={
            Developability=__DEVELOPABILITY__,
            IslandPolyNBMin=1,
            FitCones=false,
            Straighten=true,
        },
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
