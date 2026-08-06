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
--
-- Host-side export scope (read by the bridge slots before launch; echoed here so the
-- panel exposes the Scope combo): scope=__SCOPE__

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
        -- ReWeld merges over-cut micro-islands (bevel confetti); Boolean-
        -- Unoverlap adds cuts until no shells overlap. Both access-violate
        -- 2020.1 (probed) -- emitted only on >= 2022.
        ReWeld={Threshold=0.5, PolyMax=20, LENGTHMax=0.1}, -- @min_rizom_line: 2022.0
        BooleanUnoverlap=true, -- @min_rizom_line: 2022.0
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
