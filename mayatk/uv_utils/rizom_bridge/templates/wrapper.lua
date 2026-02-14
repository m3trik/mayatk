-- RizomUV wrapper: Load → [user script] → Save → Quit
-- Placeholders injected by RizomUVBridge._construct_full_script
-- __EXPORT_PATH__  : POSIX path to the FBX/OBJ file
-- __FBX_FLAG__     : ", FBX=true" when the file is FBX, "" otherwise
-- __USER_SCRIPT__  : The preset or custom Lua body inserted here

ZomLoad({File={Path="__EXPORT_PATH__", ImportGroups=true, XYZ=true__FBX_FLAG__}, NormalizeUVW=true})

__USER_SCRIPT__

ZomSave({File={Path="__EXPORT_PATH__", UVWProps=true__FBX_FLAG__}, __UpdateUIObjFileName=true})
ZomQuit()
