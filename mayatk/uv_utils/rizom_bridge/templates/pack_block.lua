-- Shared group + pack + placement recipe. Substituted into pack.lua and the
-- unwrap_*.lua presets via the PACK-BLOCK include token (expanded by
-- Parameters.expand_includes BEFORE version-stripping + param substitution,
-- so its tokens and inline @rizom_line gates participate in both). The
-- literal double-underscored token is never spelled in a comment -- the
-- expander is a blind string replace and would inject the block there too.
--
-- optimize.lua intentionally does NOT use this block -- it hardcodes
-- Scaling.Mode=0 / LayoutScalingMode=0 to preserve the existing layout, and
-- its param rows auto-hide because those tokens aren't referenced there.
--
-- Comments here must not spell the double-underscored placeholder tokens
-- (StrUtils.replace_delimited is blind to comments) -- describe fields by
-- their Lua name instead.
--
-- Probe every change: test/rizom_headless_probe.py. Field safety on 2020.1
-- (probed): MarginSize + SpacingSize safe; PaddingSize + MapResolution
-- access-violate (gated >= 2022 below).

-- Group every island under RootGroup and distribute across tiles.
-- MergingPolicy=8322 is the canonical bitmask RizomUV's reference bridges
-- use to auto-merge mirrored / stacked islands.
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
    MarginSize=__PACK_MARGIN__,
    SpacingSize=__PACK_SPACING__, -- @max_rizom_line: 2021.9999
    PaddingSize=__PACK_SPACING__, -- @min_rizom_line: 2022.0
})

-- Post-pack placement: shift the packed layout into the target UDIM tile
-- and optionally compress it into a fraction of that tile (anchored
-- bottom-left). Identity placement (UDIM 1001, full tile) skips the call.
-- Probe-verified on 2020.1: ZomDeform takes a row-major 3x3 UV transform
-- {su,0,tu, 0,sv,tv, 0,0,1} and WorkingSet="Visible" needs no selection.
local udim = __TARGET_UDIM__
local area = __UV_AREA__
local tile_u = (udim - 1001) % 10
local tile_v = math.floor((udim - 1001) / 10)
local su = (area == 1 or area == 3) and 0.5 or 1.0
local sv = (area == 2 or area == 3) and 0.5 or 1.0
if tile_u ~= 0 or tile_v ~= 0 or su < 1.0 or sv < 1.0 then
    ZomDeform({
        PrimType="Island",
        WorkingSet="Visible",
        Transform={su, 0, tile_u, 0, sv, tile_v, 0, 0, 1},
    })
end
