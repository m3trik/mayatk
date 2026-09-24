-- Pack the SELECTED objects' islands into the EMPTY space of an existing
-- UV layout. Everything else sharing their material stays put and is
-- treated as occupied space; the new islands keep the scale they arrive
-- with, so texel density matches the layout they join. Needs RizomUV
-- 2022.2 or newer.
--
-- The unselected islands are the locked "forbidden area" (official Pack
-- semantics for WorkingSet Visible&Selected); Scaling.Mode=0 +
-- LayoutScalingMode=0 is what preserves the incoming scale.
--
-- The bridge renders the selection token below as a Lua table of exported
-- island-group names for the objects passed as select_objects= (tentacle's
-- Pack op derives that set from the selection, and sends every mesh
-- sharing the selection's materials so Rizom sees the whole layout).
--
-- Requires RizomUV >= 2022.2, and likely misbehaves there too: on 2020.1
-- island-group name selection is a silent no-op, with or without the
-- List=true the call below omits (probed 2026-09-23), and ZomPack with an
-- EMPTY selection packs every island. The WorkingSet field itself IS
-- honored on 2020.1 given an island selection -- templates/pack_block.lua's
-- shell subset (a material tag selects the islands) is the probe-verified
-- recipe to rebuild this on. Version-gated meanwhile: hidden from the panel
-- combo and refused by the bridge below the gate.
-- @min_rizom: 2022.2

--
-- Host-side export scope (read by the bridge slots before launch; echoed here so the
-- panel exposes the Scope combo): scope=__SCOPE__

ZomSelect({
    PrimType="IslandGroup",
    IslandGroupMode="Group",
    Names=__PACK_SELECT_NAMES__,
    Select=true,
    ResetBefore=true,
})

ZomPack({
    WorkingSet="Visible&Selected",
    ProcessTileSelection=false,
    RecursionDepth=1,
    RootGroup="RootGroup",
    Scaling={Mode=0, Mix=false},
    Rotate={
        Step=__ROTATE_STEP__,
        Enable=__PACK_ROTATE_ENABLE__,
    },
    Translate=true,
    LayoutScalingMode=0,
    MaxMutations=__PACK_MAX_MUTATIONS__,
    Resolution=__PACK_RESOLUTION__,
})
