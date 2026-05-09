-- RizomUV wrapper: Load -> [user script] -> Save -> Quit
--
-- The bridge substitutes three placeholders into this file at runtime:
--   * the export path on disk
--   * a ", FBX=true" flag for FBX files (empty string for OBJ)
--   * the chosen preset, or a custom Lua body, inlined verbatim
--
-- The wrapper deliberately does NOT call NormalizeUVW: presets that need a
-- normalised starting layout can call ZomNormalize themselves; pack-only
-- presets must preserve the incoming UV scale so existing layouts survive.
--
-- Comments in this file must not contain the literal placeholder tokens
-- (double-underscored uppercase names) -- StrUtils.replace_delimited
-- substitutes them blindly, even inside Lua comments.

ZomLoad({File={Path="__EXPORT_PATH__", ImportGroups=true, XYZ=true__FBX_FLAG__}})

__USER_SCRIPT__

ZomSave({File={Path="__EXPORT_PATH__", UVWProps=true__FBX_FLAG__}, __UpdateUIObjFileName=true})
ZomQuit()
