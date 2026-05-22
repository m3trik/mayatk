-- RizomUV one-way send wrapper: Load FBX (with user-chosen options),
-- optionally bind a color texture, then leave RizomUV open for the user.
--
-- Unlike wrapper.lua, this template intentionally omits ZomSave / ZomQuit
-- so the artist can work interactively. The bridge launches RizomUV
-- detached so Maya returns control immediately after the load runs.
--
-- The bridge substitutes placeholders into this file at runtime
-- (export path, FBX flag, geometry/group toggles, texture loads).
-- StrUtils.replace_delimited is blind to comments, so do NOT spell
-- the placeholder tokens in this header -- the substitution would
-- clobber the comment text and make the rendered script unreadable.

ZomLoad({File={Path="__EXPORT_PATH__", ImportGroups=__IMPORT_GROUPS__, XYZUVW=__LOAD_UVS__, UVWProps=__LOAD_UVW_PROPS____FBX_FLAG__}})

__TEXTURE_LOADS__
