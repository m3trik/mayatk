-- Repack existing UV islands into the target tile.
-- Use when seams are already cut and unfolded; this only redistributes shells.
--
-- The group + pack + UDIM/coverage placement recipe lives in the shared
-- templates/pack_block.lua partial (see there), so the pack knobs are
-- defined once and reused by the unwrap_*.lua presets too. The optional
-- keep-stacked step (templates/keep_stacked_block.lua) runs first: it
-- groups islands that overlap on arrival so the packer keeps them stacked.
-- Include tokens are written WITHOUT their surrounding double underscores in
-- prose (KEEP_STACKED_BLOCK, PACK_BLOCK). Not because the expander would
-- clobber a comment -- Parameters.expand_includes only expands a line whose
-- SOLE non-whitespace content is the token -- but because
-- test_rizom_construction's "no unresolved placeholders" guard scans the
-- WHOLE constructed script, comments included, and a spelled-out token reads
-- to it as a placeholder that failed to resolve.

--
-- Host-side export scope (read by the bridge slots before launch; echoed here so the
-- panel exposes the Scope combo): scope=__SCOPE__

ZomSelect({PrimType="Island", Select=true, ResetBefore=true})

__KEEP_STACKED_BLOCK__
__PACK_BLOCK__
