-- Minimal test: select all islands and pack.
-- Use this first to verify that RizomUV loads and saves correctly.

ZomSelect({PrimType="Island", Select=true, ResetBefore=true})
ZomPack({ProcessTileSelection=false, Translate=true})
