-- Shared group + pack + placement recipe. Substituted into pack.lua and the
-- unwrap_*.lua presets via the PACK-BLOCK include token (expanded by
-- Parameters.expand_includes BEFORE version-stripping + param substitution,
-- so its tokens and inline @rizom_line gates participate in both). The
-- literal double-underscored token is never spelled in a comment -- the
-- expander is a blind string replace and would inject the block there too.
--
-- Used by pack.lua, optimize.lua and the unwrap_*.lua presets. optimize.lua
-- hand-rolled a divergent copy of the group + pack pair until 2026-08-17;
-- that copy silently no-opped for want of an island selection, so its
-- hardcoded Scaling.Mode=0 / LayoutScalingMode=0 invariants never reached the
-- packer -- see optimize.lua's header for the measurements.
--
-- Comments here must not spell the double-underscored placeholder tokens
-- (StrUtils.replace_delimited is blind to comments) -- describe fields by
-- their Lua name instead.
--
-- Probe every change: test/rizom_headless_probe.py. Field safety on 2020.1
-- (probed): MarginSize + SpacingSize safe; PaddingSize + MapResolution
-- access-violate (gated >= 2022 below). Resolution (distinct from the
-- crashing MapResolution) is safe there and is sent -- omitting it was why a
-- pack needed a second send to fill the tile; see MIN_VERSIONS for the
-- measurements. MaxMutations / Scaling.Mix / Rotate.Enable stay gated: each
-- measured as no help (or no effect at all) on 2020.1.
--
-- MarginSize / SpacingSize / PaddingSize are NOT user knobs -- their tokens
-- are computed by Parameters.derived_values from the same padding rule the
-- DCC-side pack operation uses (see DERIVED_KEYS in parameters.py), so a
-- Rizom round-trip and an in-DCC repack agree on the gutter.
--
-- RecursionDepth is fixed at 1 (pack the tiles' content), not a knob: the
-- bridge's hierarchy is always RootGroup > one tile > islands (plus the rigid
-- keep-stacked groups), so there is no nested group for a deeper recursion to
-- pack first -- depths 1, 2 and 5 saved byte-identical layouts through the
-- real Maya bridge, with and without keep-stacked groups (probed 2026-09-23).
--
-- Rotation: Rizom pre-orients every island (Rotate.Mode, default 2 = upright
-- along its minimal bounding box) BEFORE the Rotate.Step search, so rectangular
-- shells arrive axis-aligned whatever the step is. Turning rotation off
-- therefore needs BOTH halves: Mode=0 (no pre-orient) and Step=0 (no search)
-- -- measured: 30-degree strips stay at 30 degrees only with both. Rotation on
-- sends the step alone, leaving Rizom's default pre-orient in charge.

-- Shell subset: which islands this pack may move. nil for a plain pack (every
-- island packs). A preset opts in by setting the PACK_SUBSET global BEFORE this
-- block (pack.lua does; the unwrap presets must not, since they re-cut every
-- island). When set it is a list of material names: the host tags the faces of
-- the shells to pack with that material on its export copy, ZomSelect's
-- Materials filter turns that into the island selection, and every other
-- island stays FIXED -- untouched, and packed around as occupied space.
-- Probed 2026-09-23 on 2020.1: the Materials filter selects exactly the tagged
-- islands, and with WorkingSet Visible&Selected, LayoutScalingMode 0 and a
-- membership-only tile filing (FreezeIslands) the fixed islands keep their UVs
-- to 0.000000 while nothing is packed over them. Each of the three is
-- load-bearing: any LayoutScalingMode but 0 rescales the fixed islands with the
-- rest, and a filing without FreezeIslands re-centres every island in its tile.
local subset = PACK_SUBSET

-- Placement: the target UDIM tile, and the fraction of it the layout occupies
-- (anchored bottom-left). Probe-verified on 2020.1: ZomDeform takes a row-major
-- 3x3 UV transform {su,0,tu, 0,sv,tv, 0,0,1} and WorkingSet="Visible" needs no
-- selection.
local udim = __TARGET_UDIM__
local area = __UV_AREA__
local tile_u = (udim - 1001) % 10
local tile_v = math.floor((udim - 1001) / 10)
local su = (area == 1 or area == 3) and 0.5 or 1.0
local sv = (area == 2 or area == 3) and 0.5 or 1.0
local rotate = __PACK_ROTATE_ENABLE__

local function pack(scaling_mode, layout_mode)
    ZomPack({
        WorkingSet=subset and "Visible&Selected" or nil,
        ProcessTileSelection=false,
        RecursionDepth=1,
        RootGroup="RootGroup",
        Scaling={
            Mode=scaling_mode,
            Mix=__SCALING_MIX__,
        },
        Rotate={
            Step=rotate and __ROTATE_STEP__ or 0,
            Mode=(not rotate) and 0 or nil,
            Enable=rotate, -- @min_rizom_line: 2022.0
        },
        Translate=__PACK_TRANSLATE__,
        LayoutScalingMode=layout_mode,
        MaxMutations=__PACK_MAX_MUTATIONS__,
        Resolution=__PACK_RESOLUTION__,
        MarginSize=__PACK_MARGIN__,
        SpacingSize=__PACK_SPACING__, -- @max_rizom_line: 2021.9999
        PaddingSize=__PACK_SPACING__, -- @min_rizom_line: 2022.0
    })
end

if not subset then
    -- Group every island under RootGroup and distribute across tiles.
    -- MergingPolicy=8322 is only the properties-tree merge policy (the
    -- documented default "A_ADD|AIB_ADD_A_VALUE_B|B_CLONE" as a bitmask, the
    -- value RizomUV's reference bridges emit) -- it does NOT merge or stack
    -- islands. Keeping stacked islands together is templates/keep_stacked_block.lua.
    ZomIslandGroups({
        Mode="DistributeInTilesEvenly",
        MergingPolicy=8322,
        GroupPath="RootGroup",
    })
    pack(__SCALING_MODE__, __LAYOUT_SCALING_MODE__)
    -- Identity placement (UDIM 1001, full tile) skips the call.
    if tile_u ~= 0 or tile_v ~= 0 or su < 1.0 or sv < 1.0 then
        ZomDeform({
            PrimType="Island",
            WorkingSet="Visible",
            Transform={su, 0, tile_u, 0, sv, tile_v, 0, 0, 1},
        })
    end
else
    ZomSelect({PrimType="Island", Materials=subset, Select=true, ResetBefore=true})
    -- The packer only moves a keep-stacked group whose GROUP is selected --
    -- selected islands alone left a stack where the gather below put it, on
    -- top of fixed islands (measured). keep_stacked_block.lua groups only the
    -- subset in this mode, so every regular group (tiles excluded) is ours.
    -- Selected only when one exists: no other path needs the call.
    local function has_groups(group)
        for _, child in pairs(group.Children or {}) do
            if not child.IsTile or has_groups(child) then
                return true
            end
        end
        return false
    end
    if has_groups(ZomGet("Lib.Mesh.RootGroup")) then
        ZomSelect({PrimType="IslandGroup", IslandGroupMode="Group", Select=true, All=true})
    end

    -- Bounds of the subset, read back through ZomGet("Lib.Mesh.Islands") --
    -- one of the two probed-safe paths (indexing below it, Islands.0, crashes
    -- 2020.1). nil when nothing is selected.
    local function extent()
        local umin, umax, vmin, vmax = math.huge, -math.huge, math.huge, -math.huge
        for _, island in pairs(ZomGet("Lib.Mesh.Islands")) do
            if island.TopoStable and island.TopoStable.Selected then
                local b = island.BBoxUV
                umin = math.min(umin, b[1])
                umax = math.max(umax, b[2])
                vmin = math.min(vmin, b[3])
                vmax = math.max(vmax, b[4])
            end
        end
        if umax < umin then
            return nil
        end
        return umin, umax, vmin, vmax
    end

    -- A tag that reached no island packs NOTHING: with an empty selection
    -- ZomPack packs every island, fixed ones included -- the one outcome the
    -- subset exists to rule out. The host then reports the UVs as unchanged.
    local umin, umax, vmin, vmax = extent()
    if umin then
        -- Pack IN the target tile, so the fixed islands already there are what
        -- the subset packs around: shift everything onto 0-1 first and back
        -- after (fixed islands net zero). Placing after the pack, as the plain
        -- path does, would pack around the wrong tile's islands. Tile Coverage
        -- is not applied: the packer only fills a whole tile, and fitting the
        -- subset into a part of one measured as a thin strip along its bottom.
        local function shift(du, dv)
            if du ~= 0 or dv ~= 0 then
                ZomDeform({
                    PrimType="Island",
                    WorkingSet="Visible",
                    Transform={1, 0, du, 0, 1, dv, 0, 0, 1},
                })
            end
        end
        shift(-tile_u, -tile_v)
        umin, umax, vmin, vmax = umin - tile_u, umax - tile_u, vmin - tile_v, vmax - tile_v

        -- The packer only places islands filed under the tile. Filing by bounds
        -- centre with FreezeIslands is membership-only (nothing moves), and
        -- files the fixed islands where they lie: inside the tile they are
        -- obstacles, beside it they stay out of the packer's way.
        -- (DistributeInTilesEvenly files EVERY island under the tile, and fixed
        -- islands lying beside it then squeezed the subset into a strip --
        -- measured.)
        local function file_by_position()
            ZomIslandGroups({
                Mode="DistributeInTilesByBBox",
                MergingPolicy=8322,
                GroupPath="RootGroup",
                FreezeIslands=true,
            })
        end

        -- Scale the subset by f, each island about its own centre, and pull
        -- the centres into the tile before re-filing: a pack that overflows the
        -- tile leaves the overflow filed outside it, where the next pack no
        -- longer sees it. The pull is one scale k about a common point; the
        -- per-island 1/k undoes it on the islands' sizes, folded into f.
        local function gather(f)
            local k = 0.5 / math.max(umax - umin, vmax - vmin, 1e-9)
            ZomDeform({
                PrimType="Island",
                WorkingSet="Visible&Selected",
                Transform={k, 0, 0.25 - umin * k, 0, k, 0.25 - vmin * k, 0, 0, 1},
            })
            ZomDeform({
                PrimType="Island",
                WorkingSet="Visible&Selected",
                CenterMode="MultiCOG",
                Transform={f / k, 0, 0, 0, f / k, 0, 0, 0, 1},
            })
            file_by_position()
        end

        -- Pack, then read the subset's bounds back (they feed the next
        -- gather). Returns its extent over the tile, from the tile origin
        -- where the packer anchors: <= 1 fits.
        local function pack_and_measure(scaling_mode)
            pack(scaling_mode, 0)
            umin, umax, vmin, vmax = extent()
            return math.max(umax - math.min(umin, 0), vmax - math.min(vmin, 0))
        end

        if __PACK_TRANSLATE__ then
            -- With the fixed islands pinned, LayoutScalingMode must stay 0, and
            -- the packer then keeps the subset at its pre-scaled size, growing
            -- its box past the tile when that is too big. So fit it here:
            -- bisect for the largest scale whose pack still lands in the tile.
            local scale = 1.0
            local function pack_at(s)
                gather(s / scale)
                scale = s
                return pack_and_measure(0) -- Scaling.Mode 0: keep the size set
            end

            -- The Pre-scale knob applies once, here; every refit keeps sizes.
            gather(1.0)
            local over = pack_and_measure(__SCALING_MODE__)
            local lo, hi
            if over <= 1.0 then lo = 1.0 else hi = 1.0 end
            for _ = 1, 8 do -- bracket the best scale
                if lo and hi then break end
                local guess = math.min(math.max(scale / math.max(over, 1e-6), scale / 16), scale * 16)
                if lo then guess = math.max(guess, lo * 1.05) else guess = math.min(guess, hi * 0.95) end
                over = pack_at(guess)
                if over <= 1.0 then lo = guess else hi = guess end
            end
            if lo and hi then
                for _ = 1, 6 do -- bisect to ~1%
                    local mid = math.sqrt(lo * hi)
                    over = pack_at(mid)
                    if over <= 1.0 then lo = mid else hi = mid end
                end
            end
            for _ = 1, 3 do -- finish on a pack that fits
                if over <= 1.0 or not lo then break end
                over = pack_at(lo)
                if over > 1.0 then lo = lo * 0.97 end
            end
        else
            -- Translate off: nothing is moved, so the subset is only pre-scaled
            -- / rotated where it lies (islands outside the tile are left as
            -- they are).
            file_by_position()
            pack(__SCALING_MODE__, 0)
        end

        shift(tile_u, tile_v)
    end
end
