-- Keep stacked islands stacked through the pack. Substituted into pack.lua
-- and optimize.lua via the KEEP_STACKED_BLOCK include token (expanded by
-- Parameters.expand_includes, like pack_block.lua). The token is spelled here
-- without its surrounding double underscores on purpose: the expander itself
-- is comment-safe (it only substitutes a line whose SOLE non-whitespace
-- content is the token), but test_rizom_construction's unresolved-placeholder
-- guard scans the whole constructed script, comments included.
--
-- "Stacked" here = islands that overlap AND share a centre -- the two ways a
-- host stacks shells (identical shells rotated onto each other: same
-- footprint; different shells stacked on a common centre). Islands that
-- merely overlap (an unpacked layout, a half-overlap) are NOT stacked and
-- must still be unstacked and packed -- grouping any overlap welded whole
-- layouts into one frozen clump (user report 2026-08-16).
--
-- Mechanism (all probed on 2020.1, test/rizom_headless_probe.py):
--  1. ZomDeform CenterMode="MultiCOG" scales every island about its OWN
--     centre by 1/1000: islands sharing a centre still coincide, islands
--     that only overlapped no longer touch (their centres differ).
--  2. DefineGroupsByOverlapness gathers what still overlaps -- the stacks --
--     into island groups, and Pack.Stacked=true puts each group in Rizom's
--     "Group Stack" mode: the packer moves the group as ONE rigid unit and
--     never transforms its members relative to each other (without the
--     property the group is repacked internally into a grid). Texel density
--     of the stack matches the rest of the layout.
--  3. The inverse deform (x1000, same per-island centres) restores every
--     island exactly (round-trip error 1e-14 measured); the pack then moves
--     the groups anyway.
-- Measured: exact stack kept (0.0000 drift), a wide+tall pair sharing a
-- centre kept together, a half-overlap and a 30%-offset twin unstacked.
--
-- Scope: WorkingSet=Visible, i.e. everything that was sent. Pack-only
-- opt-in, NOT part of pack_block.lua: the unwrap_*.lua presets reach the
-- pack with freshly flattened islands whose placement is meaningless. Left
-- out of pack_into_existing.lua too -- new islands landing centred on a
-- locked island of the existing layout would be welded to it.
--
-- Rizom's own Stack Similar (ZomTopoCopy Mode="Stack") is the natural
-- Rizom-side alternative but access-violates 2020.1 headless (probed on
-- quad, cube and cylinder meshes) -- stack on the host side first, then
-- send with this on.

if __PACK_KEEP_STACKED__ then
    ZomSelect({PrimType="Island", Select=true, ResetBefore=true, All=true})
    ZomDeform({
        PrimType="Island",
        WorkingSet="Visible&Selected",
        CenterMode="MultiCOG",
        Transform={0.001, 0, 0, 0, 0.001, 0, 0, 0, 1},
    })
    ZomIslandGroups({
        Mode="DefineGroupsByOverlapness",
        WorkingSet="Visible",
        MergingPolicyString="A_ADD|AIB_ADD_A_VALUE_B|B_CLONE",
        AutoDelete=true,
        Properties={Pack={Stacked=true}},
    })
    ZomDeform({
        PrimType="Island",
        WorkingSet="Visible&Selected",
        CenterMode="MultiCOG",
        Transform={1000, 0, 0, 0, 1000, 0, 0, 0, 1},
    })
end
