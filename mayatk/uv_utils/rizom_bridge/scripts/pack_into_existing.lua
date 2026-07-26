-- Pack the SELECTED objects' islands into the EMPTY space of an existing
-- UV layout. The unselected objects' islands are the locked "forbidden
-- area" (official Pack semantics for WorkingSet Visible&Selected) and do
-- not move; the new islands keep their incoming scale (Scaling.Mode=0 +
-- LayoutScalingMode=0) so texel density stays consistent with the layout
-- they join.
--
-- The bridge renders the selection token below as a Lua table of exported
-- island-group names for the objects passed as select_objects= (tentacle's
-- Pack op derives that set from the selection, and sends every mesh
-- sharing the selection's materials so Rizom sees the whole layout).
--
-- Requires RizomUV >= 2022.2: on 2020.1 island-group name selection is a
-- silent no-op and the ZomPack WorkingSet field is not honored (probed),
-- so this preset is version-gated -- hidden from the panel combo and
-- refused by the bridge below the gate. The recipe follows the official
-- RizomUVLink parameter reference; live verification on a >= 2022.2
-- install is still owed (see docs/rizom_bridge_upgrade_plan.md).
-- @min_rizom: 2022.2

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
    RecursionDepth=__RECURSION_DEPTH__,
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
