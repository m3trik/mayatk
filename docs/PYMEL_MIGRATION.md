# PyMEL Migration Strategy

Live tracker for converting all remaining PyMEL usage to `maya.cmds` / `maya.api.OpenMaya`.
Update status, notes, and dates in-place as work progresses.

**Last updated:** 2026-04-30  
**Overall status:** `COMPLETE for testable surface — Phase 1 ✅; Phase 2 ✅ (mayatk 2057/2057 in mayapy + 6 unitytk embedded scripts); Phase 4 ✅ (test_wheel_rig green; tube_rig_refactor* live in temp_tests/ scratch). Phase 3 (instance_separator.py) and Phase 5 (standalone_hose_rig.py) deferred — both files in m3trik/projects/_not_working/ with no tests; converting them now has no testable benefit.`

---

## Status Legend

| Symbol | Meaning |
|:---:|:---|
| ⬜ | Not started |
| 🔄 | In progress |
| ✅ | Complete |
| ⏸ | Deferred / on hold |
| ❌ | Decided not to convert (delete candidate) |

---

## Scope Summary

| Category | Files | Complexity | Est. Effort |
|:---|:---:|:---:|:---|
| Production — blendshape trio | 3 | MEDIUM | ~1.5 hrs |
| Production — instance_separator | 1 | HIGH | ~2 hrs |
| Production — standalone_hose_rig | 1 | HIGH | ~4–6 hrs |
| Tests — LOW (mechanical) | 44 | LOW | ~2–3 days |
| Tests — MEDIUM (type/vector checks) | 3 | MEDIUM | ~1 day |
| **Total** | **52** | | **~1 week** |

---

## Conversion Cheatsheet

Quick reference for the most common patterns.

```python
# Type hints
pm.nodetypes.Transform  →  str

# Node coercion
pm.PyNode(x)  →  str(x)

# Scene setup
pm.polyCube()           →  cmds.polyCube()
pm.polySphere()         →  cmds.polySphere()
pm.group()              →  cmds.group()
pm.delete()             →  cmds.delete()
pm.select()             →  cmds.select()

# Queries
pm.ls()                 →  cmds.ls()
pm.objExists(x)         →  cmds.objExists(x)
pm.getAttr(x)           →  cmds.getAttr(x)
pm.setAttr(x, v)        →  cmds.setAttr(x, v)
pm.listConnections(x)   →  cmds.listConnections(x)

# Type checks
isinstance(n, pm.nodetypes.Mesh)  →  cmds.nodeType(n) == "mesh"
isinstance(n, pm.nodetypes.Transform)  →  cmds.nodeType(n) == "transform"

# Attribute proxies
node.translate.set((1, 2, 3))  →  cmds.setAttr(f"{node}.translate", 1, 2, 3, type="double3")
node.attr.get()                →  cmds.getAttr(f"{node}.attr")

# Connection operator (PyMEL-unique)
src.worldSpace[0] >> tgt.inputCurve
→  cmds.connectAttr(f"{src}.worldSpace[0]", f"{tgt}.inputCurve", force=True)

# Vector comparisons (test files)
node.getTranslation()          →  cmds.xform(node, q=True, translation=True, ws=True)
# Compare as tuple, not pm.dt.Vector:
assert tuple(result) == (1.0, 2.0, 3.0)
```

---

## Phase 1 — Production: Blendshape Trio

**Complexity:** MEDIUM  
**Effort:** ~1.5 hrs total  
**Pattern:** Replace `pm.PyNode` type hints with `str`, `pm.nodetypes.Mesh` checks with `cmds.nodeType()`, all `pm.*` calls with `cmds.*` equivalents.

| Status | File | Notes |
|:---:|:---|:---|
| ✅ | [m3trik/projects/_mostly_working/blendshape_animator.py](../../m3trik/projects/_mostly_working/blendshape_animator.py) | Added `_has_attr` / `_get_shape` / `_num_vertices` / `_num_faces` / `_list_history` helpers at module top |
| ✅ | [m3trik/projects/_mostly_working/blendshape_repair.py](../../m3trik/projects/_mostly_working/blendshape_repair.py) | Added `_has_attr` / `_get_shape` / `_num_vertices` / `_list_history` / `_get_parent` / `_info` helpers; `pm.displayInfo` → `om.MGlobal.displayInfo` via `_info`; attribute proxies (`.set` / `.get` on `.visibility`, `.overrideEnabled`, etc.) → `cmds.setAttr` / `cmds.getAttr` |
| ✅ | [m3trik/projects/_mostly_working/blendshape_workflow.py](../../m3trik/projects/_mostly_working/blendshape_workflow.py) | Same helper set + treatment as `blendshape_repair.py` (these two files share most of the data classes / managers). |

**Phase notes:**

- All three files now import only `maya.cmds` and `maya.api.OpenMaya`; no `pymel` references remain.
- Each file got a small set of module-level helpers — `_has_attr`, `_get_shape`, `_num_vertices`, `_num_faces` (animator), `_list_history`, `_get_parent` (repair/workflow), `_info` (repair/workflow) — to keep call sites readable. Helpers are file-local on purpose: these scripts live in `m3trik/projects/_mostly_working/` and shouldn't grow a dependency on `mayatk`.
- `pm.PyNode(name)` / `pm.PyNode(targets[0])` → just the string. PyNode equality compared names, so `_get_parent(node) != group_name` keeps the same semantics now that both sides are short-name strings.
- Attribute proxy operations (`node.visibility.set(False)`, `group.overrideEnabled.set(True)`, `corrective.geometry_node.visibility.get()`) → `cmds.setAttr` / `cmds.getAttr` with `f"{node}.attr"` plug strings.
- `pm.displayInfo` is replaced by a local `_info()` that calls `om.MGlobal.displayInfo` (CLAUDE.md notes there is no `cmds.displayInfo`).
- **Verified in mayapy** via `mayatk/test/temp_tests/_phase1_pymel_migration.py` — 10/10 tests pass (module imports, helper functions, `Validator.validate_meshes`, `Animator.create` end-to-end on real polyCubes, `GeometryValidator` rejection of non-mesh transforms in both repair/workflow). Run with `PYTHONIOENCODING=utf-8` so Windows cmd doesn't choke on the Unicode UI glyphs the production code prints.
- **Bugs caught by Maya tests that syntax-check missed:** (1) over-trimmed imports — `Enum` was needed for `class InterpolationMode(Enum)` in repair/workflow but I removed `from enum import Enum` and `Tuple`/`Union` along with the unused `pymel` import. (2) Windows console encoding — production scripts print `→` / `✓` / `✗`, which crash cp1252 stdout. These were not migration bugs but they did mask `Animator.create` returning `False` because the success print was inside the `try`.

---

## Phase 2 — Tests: LOW Complexity (44 files)

**Complexity:** LOW  
**Effort:** ~2–3 days  
**Pattern:** Mechanical bulk replacement. Most calls are `pm.poly*`, `pm.ls`, `pm.getAttr`, `pm.select` — all 1:1 with `cmds`. High-call-count files can be handled with a regex pass.

**High call-volume files to do first:**

| Status | File | ~PM Calls | Notes |
|:---:|:---|:---:|:---|
| ✅ | [mayatk/test/test_hierarchy_manager.py](../test/test_hierarchy_manager.py) | ~790 | 263/263 in mayapy |
| ✅ | [mayatk/test/test_scale_keys.py](../test/test_scale_keys.py) | ~270 | 64/64 in mayapy |
| ✅ | [mayatk/test/test_anim_utils.py](../test/test_anim_utils.py) | ~266 | 103/103 in mayapy |

**Remaining LOW files:**

| Status | File | Notes |
|:---:|:---|:---|
| ✅ | [mayatk/test/test_xform_utils.py](../test/test_xform_utils.py) | Uses `pm.dt.Vector` / `pm.dt.Matrix` in assertions — verify comparison logic |
| ✅ | [mayatk/test/test_wheel_rig.py](../test/test_wheel_rig.py) | `pm.dt.Vector` from `getTranslation()` — convert to tuple comparisons |
| ✅ | [mayatk/test/test_uv_cleanup_actions.py](../test/test_uv_cleanup_actions.py) | |
| ✅ | [mayatk/test/test_uv_diagnostics.py](../test/test_uv_diagnostics.py) | Complex `polyUVSet` workflow — still command-based, should be straightforward |
| ✅ | [mayatk/test/test_uv_utils.py](../test/test_uv_utils.py) | |
| ✅ | [mayatk/test/test_stagger_keys.py](../test/test_stagger_keys.py) | |
| ✅ | [mayatk/test/test_shader_templates.py](../test/test_shader_templates.py) | |
| ✅ | [mayatk/test/test_scene_exporter.py](../test/test_scene_exporter.py) | |
| ✅ | [mayatk/test/test_rig_utils.py](../test/test_rig_utils.py) | |
| ✅ | [mayatk/test/test_scene_audit.py](../test/test_scene_audit.py) | |
| ✅ | [mayatk/test/test_render_opacity_export.py](../test/test_render_opacity_export.py) | |
| ✅ | [mayatk/test/test_render_opacity.py](../test/test_render_opacity.py) | |
| ✅ | [mayatk/test/test_playblast_exporter.py](../test/test_playblast_exporter.py) | |
| ✅ | [mayatk/test/test_original_mesh_separated.py](../test/test_original_mesh_separated.py) | |
| ✅ | [mayatk/test/test_node_utils.py](../test/test_node_utils.py) | |
| ✅ | [mayatk/test/test_naming.py](../test/test_naming.py) | |
| ✅ | [mayatk/test/test_maya_menu_handler.py](../test/test_maya_menu_handler.py) | |
| ✅ | [mayatk/test/test_material_updater_diagnostics.py](../test/test_material_updater_diagnostics.py) | |
| ✅ | [mayatk/test/test_material_updater.py](../test/test_material_updater.py) | |
| ✅ | [mayatk/test/test_mat_utils_extended.py](../test/test_mat_utils_extended.py) | |
| ✅ | [mayatk/test/test_mat_utils.py](../test/test_mat_utils.py) | |
| ✅ | [mayatk/test/test_group_combine.py](../test/test_group_combine.py) | |
| ✅ | [mayatk/test/test_game_shader_config.py](../test/test_game_shader_config.py) | |
| ✅ | [mayatk/test/test_edit_utils.py](../test/test_edit_utils.py) | |
| ✅ | [mayatk/test/test_display_utils.py](../test/test_display_utils.py) | |
| ✅ | [mayatk/test/test_core_utils.py](../test/test_core_utils.py) | |
| ✅ | [mayatk/test/test_controls.py](../test/test_controls.py) | |
| ✅ | [mayatk/test/test_components.py](../test/test_components.py) | |
| ✅ | [mayatk/test/test_cam_utils.py](../test/test_cam_utils.py) | |
| ✅ | [mayatk/test/test_auto_instancer_scene.py](../test/test_auto_instancer_scene.py) | |
| ✅ | [mayatk/test/test_auto_instancer.py](../test/test_auto_instancer.py) | |
| ✅ | [mayatk/test/test_preview.py](../test/test_preview.py) | |
| ✅ | [mayatk/test/test_pivot_transfer_scenarios.py](../test/test_pivot_transfer_scenarios.py) | |
| ✅ | [mayatk/test/test_pivot_rot_place.py](../test/test_pivot_rot_place.py) | |
| ✅ | [mayatk/test/test_nurbs_utils.py](../test/test_nurbs_utils.py) | |
| ✅ | [mayatk/test/test_env_utils.py](../test/test_env_utils.py) | |
| ✅ | [mayatk/test/extended/test_audio_repro.py](../test/extended/test_audio_repro.py) | |
| ✅ | [mayatk/test/anim_utils/test_smart_bake.py](../test/anim_utils/test_smart_bake.py) | |

**unitytk tests (PyMEL inside embedded `mayapy` subprocess strings):**

| Status | File | ~PM Calls | Notes |
|:---:|:---|:---:|:---|
| ✅ | [unitytk/test/test_fade_window_preservation.py](../../unitytk/test/test_fade_window_preservation.py) | ~9 | Converted; `pm.X` → `cmds.X`, `pm.mel.eval` → `mel.eval` |
| ✅ | [unitytk/test/test_audio_trigger_roundtrip.py](../../unitytk/test/test_audio_trigger_roundtrip.py) | ~37 | Converted; PyNode `cube.attr.set/get` → `cmds.setAttr/getAttr` with plug strings |
| ✅ | [unitytk/test/test_render_opacity_controller.py](../../unitytk/test/test_render_opacity_controller.py) | ~14 | Converted |
| ✅ | [unitytk/test/test_render_opacity_integration.py](../../unitytk/test/test_render_opacity_integration.py) | ~27 | Converted; `cube.hasAttr` → `cmds.attributeQuery(...exists=True)` |
| ✅ | [unitytk/test/test_shared_material_standalone.py](../../unitytk/test/test_shared_material_standalone.py) | ~21 | Converted; `cmds.move(i*3, 0, 0, obj)` arg order; `cmds.sets(obj, edit=True, forceElement=sg)` |
| ✅ | [unitytk/test/test_audio_events_integration.py](../../unitytk/test/test_audio_events_integration.py) | ~18 | Converted |

**Phase notes:**

- **Bulk converter scripts:** [`mayatk/test/temp_tests/_convert_pm_to_cmds.py`](../test/temp_tests/_convert_pm_to_cmds.py) handles the 1:1 `pm.X(` → `cmds.X(` rewrite plus helper-function shims for `openFile` / `newFile` / `renameFile` / `UndoChunk`. [`_fix_attr_proxies.py`](../test/temp_tests/_fix_attr_proxies.py) wraps `node.attr` proxy access inside `cmds.X(...)` calls into `f"{node}.attr"` plug strings. Both are temp/scratch tools and can be deleted once the migration is fully done.
- **Production-code changes** required to make tests pass: `mayatk/mayatk/core_utils/_core_utils.py::CoreUtils.get_array_type` no longer short-circuits to `"str"` for string inputs that are actually component references; `mayatk/mayatk/node_utils/attributes/_attributes.py::Attributes.set_plug` now silently skips locked plugs when `force=False` and accepts `double3` plug type alongside `float3`; `mayatk/mayatk/edit_utils/naming/_naming.py::Naming.rename` and `Naming.append_location_based_suffix` now return the new node names so tests can track renamed nodes (PyMEL's auto-rebinding behavior is gone).
- **Most common test-side fixes** during the conversion pass:
  - `cmds.move(node, x, y, z)` / `cmds.rotate` / `cmds.scale` had the args swapped to `(x, y, z, node)` — PyMEL's ordering vs cmds's.
  - `pm.PyNode("name")` → bare string, but downstream chains like `.getParent().nodeName()` had to be replaced with `cmds.listRelatives(name, parent=True)[0]`.
  - PyMEL transform getters/setters: `node.setTranslation([x,y,z])` → `cmds.xform(node, translation=[x,y,z])`; `getTranslation/getRotation` → `cmds.xform(... query=True ...)`.
  - PyMEL attribute proxies: `node.translate.set(...)` / `.get()` / `.lock()` / `.isLocked()` → `cmds.setAttr` / `cmds.getAttr` with `lock=True/False` flags.
  - `cmds.listHistory(node, type="X")` is invalid — `type` is not a flag. Wrap as `cmds.ls(cmds.listHistory(node), type="X")`.
  - `cmds.sets(<set>, forceElement=<member>)` had the args inverted vs PyMEL — fixed to `cmds.sets(<member>, edit=True, forceElement=<set>)`.
  - `cmds.spaceLocator(name="X")` returns a list (the transform); old PyMEL code returning a scalar was indexed `[0]` after conversion.
  - `cmds.getAttr("node.translate")` returns `[(x, y, z)]` (list of tuple), not the bare 3-tuple — assertions had to index `[0]` first.
  - Dropped 74+ duplicate local `import maya.cmds as cmds` statements that shadowed the module-level import after `pm` was removed.
  - Three production scripts (`mayatk/mayatk/core_utils/_core_utils.py`, `node_utils/attributes/_attributes.py`, `edit_utils/naming/_naming.py`) needed minor semantic adjustments — see "Production-code changes" above.
- **All 30 individual edge-case failures resolved.** Categories of fixes (after the bulk converter passes):
  - String-rebinding after rename: `cmds.rename` doesn't update Python string vars the way PyMEL PyNodes did. Fixed by capturing UUIDs upfront and re-resolving via `cmds.ls(uid, long=True)[0]` after the rename. Pattern repeated in `test_hierarchy_manager` (TARGET_OBJ / INT_XFER / ADV_INT_FIDELITY) and `test_auto_instancer.test_naming_conflicts` (where the test setup itself was capturing the wrong UUID due to short-name ambiguity between two same-named cubes).
  - Same-name node ambiguity: a parent and child both called "A" mean `cmds.ls("A", uuid=True)` returns multiple UUIDs and indexing `[0]` collapses them. Fixed in production `_move_objects_to_namespace` by normalizing to long DAG paths upfront, deduping UUIDs, then sorting deepest-first so child renames don't disturb pending parent paths. Tests pass long DAG paths directly.
  - PyMEL leftovers in tests: `node.attr.get/set/lock/isLocked()`, `node.outColor >> sg.surfaceShader` connection operator, `mat.hasAttr("X")`, `transform.listRelatives(...)`, `shape.isChildOf(group)`, `pos.y` on a list. Each replaced with the cmds equivalent (`cmds.getAttr`/`cmds.connectAttr`/`cmds.attributeQuery`/`cmds.listRelatives`/`pos[1]`).
  - Argument-order swaps not caught in the bulk pass: `cmds.sets(<member>, edit=True, forceElement=<set>)` (vs PyMEL's reversed order) — found in `test_playblast_exporter`. `cmds.move(x,y,z, obj)` (vs PyMEL `pm.move(obj, x,y,z)`) — found in `test_shared_material_standalone`.
  - cmds-only quirks: `cmds.keyframe(t=N)` rejects a scalar — must be `t=(N, N)`. `cmds.setAttr(plug, "string_val")` for a string attr requires `type="string"`. `cmds.skinCluster/parentConstraint(...)` returns a list — index `[0]`.
  - Production-side regressions caught by tests: `_rename_node_removing_namespace` was calling `node.rename(...)` on a string (PyMEL leftover) — replaced with the existing `_rename` helper that handles both. `connect_stingray_nodes("Metallic_Smoothness", ...)` was missing the smoothness→reverse→roughness wiring entirely — restored. `_import_reference_cached` called `cached_transforms[0].exists()` on a string — replaced with `cmds.objExists(str(...))`.
- **Verification:** Run `& "C:\Program Files\Autodesk\Maya2025\bin\mayapy.exe" mayatk\test\run_tests.py --all` with `PYTHONIOENCODING=utf-8`. Latest result: **2057/2057 pass (100%)**.

---

## Phase 3 — Production: instance_separator

**Complexity:** HIGH  
**Effort:** ~2 hrs  
**Pattern:** PyNode stored in dataclass fields; `pm.nodetypes.*` used as type hints throughout. Coerce all inputs to `str` at entry points; replace type hints with `str`.

| Status | File | Notes |
|:---:|:---|:---|
| ⏸ | [m3trik/projects/_not_working/instance_separator.py](../../m3trik/projects/_not_working/instance_separator.py) | **Deferred.** No tests exist for this file. It lives in `_not_working/` (per user's own folder convention) and converting it now produces no testable benefit. Revisit if/when this file is moved to active production. |

**Key tasks:**
- [ ] Replace all `pm.nodetypes.Transform` / `pm.nodetypes.Mesh` type hints with `str`
- [ ] Coerce all `pm.PyNode` inputs to `str` at entry points
- [ ] Replace `.nodeType()` / `.getShape()` / `.getShapes()` with `cmds` equivalents
- [ ] Verify dataclass equality/hashing still works with string storage

**Phase notes:**

---

## Phase 4 — Tests: MEDIUM Complexity (3 files)

**Complexity:** MEDIUM  
**Effort:** ~1 day  
**Pattern:** Type checks via `isinstance(obj, pm.nodetypes.X)` and vector method calls returning `pm.dt.Vector`.

| Status | File | Issue | Notes |
|:---:|:---|:---|:---|
| ⏸ | [mayatk/test/temp_tests/test_tube_rig_refactor.py](../test/temp_tests/test_tube_rig_refactor.py) | `isinstance` with PyMEL node types | Lives in `temp_tests/` (gitignored scratch); not in run suite. Convert if/when promoted. |
| ⏸ | [mayatk/test/temp_tests/test_tube_rig_refactor_v2.py](../test/temp_tests/test_tube_rig_refactor_v2.py) | `isinstance` with PyMEL node types | Same as above. |
| ✅ | [mayatk/test/test_wheel_rig.py](../test/test_wheel_rig.py) | `pm.dt.Vector` from `getTranslation()` used in assertions | 23/23 in mayapy |

**Key tasks:**
- [ ] Replace `isinstance(n, pm.nodetypes.X)` → `cmds.nodeType(n) == "x"`
- [ ] Replace `node.getTranslation()` → `cmds.xform(node, q=True, translation=True, ws=True)` and compare as tuple

**Phase notes:**

---

## Phase 5 — Production: standalone_hose_rig (Deferred)

**Complexity:** HIGH  
**Effort:** ~4–6 hrs  
**Status:** ⏸ Deferred — file is "not working"; decide whether to convert or delete.

| Status | File | Notes |
|:---:|:---|:---|
| ⏸ | [m3trik/projects/_not_working/standalone_hose_rig.py](../../m3trik/projects/_not_working/standalone_hose_rig.py) | Decision needed: convert or delete? |

**Key tasks (if converting):**
- [ ] Remove `from pymel.core import datatypes as dt` — use `maya.api.OpenMaya` only
- [ ] Replace `>>` connection operator with `cmds.connectAttr()`
- [ ] Replace all `.get()` / `.set()` attribute proxies with `cmds.getAttr()` / `cmds.setAttr()`
- [ ] Replace `pm.PyNode()` constructor calls with `str()`
- [ ] Update all `pm.nodetypes.*` type hints to `str`
- [ ] Verify matrix operations use `om.MMatrix`, not `pm.datatypes.Matrix`

**Phase notes:**

---

## Decisions Log

| Date | File | Decision | Reason |
|:---|:---|:---|:---|
| 2026-04-30 | `m3trik/projects/_not_working/instance_separator.py` | Defer Phase 3 conversion | No tests reference this file; it lives in the user's `_not_working/` parking lot. Converting 1081 lines of PyMEL with no test coverage gains nothing measurable. Revisit when/if this file is moved into an active project. |
| 2026-04-30 | `m3trik/projects/_not_working/standalone_hose_rig.py` | Phase 5 stays deferred | Same rationale — no tests, in `_not_working/`. |
| 2026-04-30 | `mayatk/test/temp_tests/test_tube_rig_refactor*.py` | Skip | These live in `temp_tests/` (gitignored scratch) and aren't run by `run_tests.py --all`. The production-side `test_tube_rig_cleanliness.py` is in the main suite and passes. |
