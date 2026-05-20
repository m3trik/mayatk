-- RizomUV wrapper: Load -> [user script] -> Save -> Quit
--
-- The bridge substitutes three placeholders into this file at runtime:
--   * the export path on disk
--   * a nested FBX={UseUVSetNames=true} table for FBX files (empty for OBJ
--     and for Rizom versions below FBX_USE_UV_SET_NAMES_MIN_VERSION)
--   * the chosen preset, or a custom Lua body, inlined verbatim
--
-- XYZUVW + UVWProps on load = positions AND existing UVs come through, so
-- pack/optimize presets can operate on the incoming layout. XYZ-only would
-- discard UVs at load time and make pack a no-op (degenerate output UVs).
-- NormalizeUVW is unset on purpose: the Titus Batch_Make2UV_Channel
-- reference combines XYZUVW with no NormalizeUVW key, and that's the
-- only known-2020.1-safe shape for this load. Explicit NormalizeUVW=false
-- here previously coincided with an access violation on 2020.1.
--
-- Comments in this file must not contain the literal placeholder tokens
-- (double-underscored uppercase names) -- StrUtils.replace_delimited
-- substitutes them blindly, even inside Lua comments.

ZomLoad({File={Path="__EXPORT_PATH__", ImportGroups=true, XYZUVW=true, UVWProps=true__FBX_FLAG__}})

__USER_SCRIPT__

ZomSave({File={Path="__EXPORT_PATH__", UVWProps=true__FBX_FLAG__}, __UpdateUIObjFileName=true})
ZomQuit()
