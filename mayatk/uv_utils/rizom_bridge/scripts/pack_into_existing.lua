-- Pack the SELECTED shells into the FREE space of an existing UV layout.
-- Every other island sent -- the rest of the layout -- stays exactly where it
-- is and is packed around as occupied space; the packed islands take the
-- layout's texel density, so they join it rather than being fitted to fill it.
--
-- The host sends the whole layout (tentacle's Pack op: every mesh sharing the
-- selection's materials) and tags the faces of the objects / shells passed as
-- select_objects= with a throwaway material, which the selection token below
-- renders as a Lua list. The rest is templates/pack_block.lua's shell subset
-- (the probe-verified fixed-island recipe) with its match-density variant:
--   * density: every packed island ends at the texel density of the fixed
--     islands in the target tile -- sqrt(sum UV area / sum 3D area) over them,
--     or over all of them when the tile holds none (probed 2026-09-27 on
--     2020.1: new islands arriving at a half and a quarter of the layout's
--     density both land on it to 5 digits);
--   * no fit: a subset that fits at that density is final; one that does not
--     is shrunk uniformly to the largest scale that does (inside the tile
--     margin), never grown -- so a full layout lowers the new shells' density
--     instead of spilling them out of the tile.
-- Keep Stacked groups the SUBSET only (see keep_stacked_block.lua), so a new
-- island landing on a fixed one is never welded to it.
--
-- Rebuilt 2026-09-27. The first version selected the new objects' island
-- GROUPS by name and was gated to RizomUV >= 2022.2: on 2020.1 that selection
-- is a silent no-op with or without List=true, and the pack after it moved
-- nothing (0 of 19 islands; new islands left overlapping fixed ones). The
-- material tag works on 2020.1, so the gate is gone.
--
-- The Pre-scale / Layout Scale / Tile Coverage / Translate knobs do nothing
-- here (density is matched, the fixed islands pin the layout scale, the free
-- space is the whole target tile, and the subset always moves), so the marker
-- below hides them from the panel.
-- @ignores: SCALING_MODE, LAYOUT_SCALING_MODE, UV_AREA, PACK_TRANSLATE
--
-- Host-side export scope (read by the bridge slots before launch; echoed here so the
-- panel exposes the Scope combo): scope=__SCOPE__

PACK_SUBSET = __PACK_SELECT_NAMES__
PACK_MATCH_DENSITY = true

__KEEP_STACKED_BLOCK__
__PACK_BLOCK__
