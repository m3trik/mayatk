# mayatk — API Changes

_Diff vs prior baseline. Generated 2026-05-27._

## Removed (14)

- `mat_utils/texture_path_editor.py::TexturePathEditorSlots.lbl010` — was `(self)`
- `mat_utils/texture_path_editor.py::TexturePathEditorSlots.lbl013` — was `(self)`
- `mat_utils/texture_path_editor.py::TexturePathEditorSlots.lbl014` — was `(self)`
- `mat_utils/texture_path_editor.py::TexturePathEditorSlots.lbl015` — was `(self)`
- `mat_utils/texture_path_editor.py::TexturePathEditorSlots.lbl_find_copy` — was `(self)`
- `mat_utils/texture_path_editor.py::TexturePathEditorSlots.remap_to_relative` — was `(self, selection=None)`
- `mat_utils/texture_path_editor.py::TexturePathEditorSlots.resolve_missing_by_fuzzy` — was `(self)`
- `mat_utils/texture_path_editor.py::TexturePathEditorSlots.resolve_missing_by_stem` — was `(self)`
- `mat_utils/texture_path_editor.py::TexturePathEditorSlots.resolve_missing_by_texture` — was `(self)`
- `mat_utils/texture_path_editor.py::TexturePathEditorSlots.row_find_and_copy_texture` — was `(self, selection=None)`
- `mat_utils/texture_path_editor.py::TexturePathEditorSlots.row_resolve_by_fuzzy` — was `(self, selection=None)`
- `mat_utils/texture_path_editor.py::TexturePathEditorSlots.row_resolve_by_stem` — was `(self, selection=None)`
- `mat_utils/texture_path_editor.py::TexturePathEditorSlots.row_resolve_by_texture` — was `(self, selection=None)`
- `mat_utils/texture_path_editor.py::TexturePathEditorSlots.row_set_texture_directory` — was `(self, selection=None)`

## Added (12)

- `mat_utils/texture_path_editor.py::TexturePathEditorSlots.reload_scene_textures(self)`
- `mat_utils/texture_path_editor.py::TexturePathEditorSlots.select_absolute_paths(self)`
- `mat_utils/texture_path_editor.py::TexturePathEditorSlots.select_broken_paths(self)`
- `mat_utils/texture_path_editor.py::TexturePathEditorSlots.select_textures_for_objects(self)`
- `mat_utils/texture_path_editor.py::TexturePathEditorSlots.tb_find_and_copy_textures(self, widget=None)`
- `mat_utils/texture_path_editor.py::TexturePathEditorSlots.tb_find_and_copy_textures_init(self, widget)`
- `mat_utils/texture_path_editor.py::TexturePathEditorSlots.tb_normalize_paths(self, widget=None)`
- `mat_utils/texture_path_editor.py::TexturePathEditorSlots.tb_normalize_paths_init(self, widget)`
- `mat_utils/texture_path_editor.py::TexturePathEditorSlots.tb_resolve_missing_textures(self, widget=None)`
- `mat_utils/texture_path_editor.py::TexturePathEditorSlots.tb_resolve_missing_textures_init(self, widget)`
- `mat_utils/texture_path_editor.py::TexturePathEditorSlots.tb_set_texture_directory(self, widget=None)`
- `mat_utils/texture_path_editor.py::TexturePathEditorSlots.tb_set_texture_directory_init(self, widget)`

## Signature changed (2)

- `xform_utils/_xform_utils.py::XformUtils.clear_stored_transforms`
  - was: `(objects, prefix='original')`
  - now: `(objects, prefix='original') -> List[str]`
- `xform_utils/_xform_utils.py::XformUtils.store_transforms`
  - was: `(objects, prefix='original', accumulate=True, traverse=False)`
  - now: `(objects, prefix='original', accumulate=True, traverse=False, channels=None)`
