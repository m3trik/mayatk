-- Hybrid auto-unwrap: sharp-edge seams AND Mosaic segmentation in one pass.
-- Cuts along modeled creases where the geometry has them (SharpEdges) and
-- falls back to quasi-developable segmentation where it doesn't
-- (QuasiDevelopable) -- the best default for mixed / prop meshes that are
-- part hard-surface, part smooth.
--
-- Requires RizomUV >= 2022.0: running both segmenters in one Auto block
-- access-violates 2020.1 (probed) -- so this preset is version-gated
-- (hidden from the panel combo and refused by the bridge below the gate).
-- Effect verification on a >= 2022 install is still owed -- probe it with
-- test/rizom_headless_probe.py.
-- @min_rizom: 2022.0

-- 0. Weld First (default on): weld ALL existing seams so the auto-seam
--    re-cuts from a clean surface.
if __WELD_SEAMS__ then
    ZomSelect({PrimType="Edge", WorkingSet="Visible&UnLocked", Select=true, All=true, ResetBefore=true})
    ZomWeld({PrimType="Edge", WorkingSet="Visible&UnLocked"})
end

-- 1. Detect seams via BOTH sharp dihedral angle and quasi-developable
--    segmentation, plus the usual cutters and the stretch limiter.
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
        QuasiDevelopable={
            Developability=__DEVELOPABILITY__,
            IslandPolyNBMin=1,
            FitCones=__FIT_CONES__,
            Straighten=true,
        },
        PipesCutter=true,
        HandleCutter=true,
        QuadLoopCutter=true,
        StretchLimiter=true,
        Quality=0.25,
        ReWeld={Threshold=0.5, PolyMax=20, LENGTHMax=0.1},
        BooleanUnoverlap=true,
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

-- 5. Group + pack + placement (shared recipe -- see templates/pack_block.lua).
__PACK_BLOCK__
