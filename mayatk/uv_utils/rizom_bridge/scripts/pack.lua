-- Repack existing UV islands into the target tile.
-- Use when seams are already cut and unfolded; this only redistributes shells.
--
-- The group + pack + UDIM/coverage placement recipe lives in the shared
-- templates/pack_block.lua partial (see there), so the pack knobs are
-- defined once and reused by the unwrap_*.lua presets too. NOTE: the
-- include token below must NOT be named in any comment -- the expander is
-- a blind string replace and would inject the block into the comment too.

--
-- Host-side export scope (read by the bridge slots before launch; echoed here so the
-- panel exposes the Scope combo): scope=__SCOPE__

ZomSelect({PrimType="Island", Select=true, ResetBefore=true})

__PACK_BLOCK__
