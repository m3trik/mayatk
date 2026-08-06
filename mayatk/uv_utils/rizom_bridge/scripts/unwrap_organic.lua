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
--
-- Host-side export scope (read by the bridge slots before launch; echoed here so the
-- panel exposes the Scope combo): scope=__SCOPE__

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
            FitCones=__FIT_CONES__,
            Straighten=true,
        },
        PipesCutter=true,
        HandleCutter=true,
        QuadLoopCutter=false,
        StretchLimiter=true,
        Quality=0.5,
        -- Add cuts until no shells overlap (skeleton-guided). Probed safe
        -- on 2020.1; a no-op when there are no overlaps, a fix when there
        -- are (Smithsonian's production organic recipe).
        SkeletonUnoverlap={SegLevel=1, FromRoot=true, Smooth=2},
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

-- Group + pack + placement (shared recipe -- see templates/pack_block.lua).
__PACK_BLOCK__
