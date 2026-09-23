# !/usr/bin/python
# coding=utf-8
"""The Scene Exporter panel's task and check rows.

The declarative definitions -- ``{name: {"widget_type", "object_name",
"setChecked", ...}}`` -- from which the panel builds its widgets and the
export button reads a run back (``ptk.ExportProfile``). Presentation only:
the tooltips describe what each task and check does, and the combo tables
are the shared ``ptk.ExportProfile`` ones (labels persist by index, so a
choice is never inserted above a sentinel). The engine mixins never read
this module.
"""

from typing import Dict, Any

import pythontk as ptk

# From this package:
from mayatk.env_utils._env_utils import EnvUtils


class _TaskDefinitionsMixin:
    """The panel's rows: ``task_definitions`` / ``check_definitions``."""

    # The combo tables, shared with blendertk's panel through ExportProfile
    # (one label edit lands in both). Every combo persists by INDEX, so a
    # sentinel keeps its slot and a new choice APPENDS.
    _frame_rate_options: Dict[str, Any] = ptk.ExportProfile.frame_rate_options()
    _scene_unit_options: Dict[str, Any] = {
        k: v
        for k, v in ptk.insert_into_dict(
            EnvUtils.SCENE_UNIT_VALUES, "OFF", None
        ).items()
    }
    _texture_output_options: Dict[str, Any] = ptk.ExportProfile.TEXTURE_OUTPUT_OPTIONS
    _animation_output_options: Dict[str, Any] = (
        ptk.ExportProfile.ANIMATION_OUTPUT_OPTIONS
    )
    _optimize_textures_options: Dict[str, Any] = (
        ptk.ExportProfile.optimize_textures_options()
    )
    _texture_file_type_options: Dict[str, Any] = (
        ptk.ExportProfile.texture_file_type_options()
    )
    _export_mode_options: Dict[str, Any] = ptk.ExportProfile.EXPORT_MODE_OPTIONS
    _bake_range_options: Dict[str, Any] = ptk.ExportProfile.BAKE_RANGE_OPTIONS
    _animation_clips_options: Dict[str, Any] = ptk.ExportProfile.ANIMATION_CLIPS_OPTIONS
    _optimize_keys_options: Dict[str, Any] = ptk.ExportProfile.OPTIMIZE_KEYS_OPTIONS
    _secondary_max_size_options: Dict[str, Any] = (
        ptk.ExportProfile.SECONDARY_MAX_SIZE_OPTIONS
    )
    _uastc_rdo_options: Dict[str, Any] = ptk.ExportProfile.UASTC_RDO_OPTIONS
    _glb_key_reduction_options: Dict[str, Any] = (
        ptk.ExportProfile.GLB_KEY_REDUCTION_OPTIONS
    )
    _baked_reflections_options: Dict[str, Any] = (
        ptk.ExportProfile.BAKED_REFLECTIONS_OPTIONS
    )

    @property
    def task_definitions(self) -> Dict[str, Dict[str, Any]]:
        """Return the task definitions for the UI.

        Tooltips are built with uitk's rich-text DSL (imported lazily so this
        engine module still imports Qt-free in a headless session).  Keep the
        ``TooltipFormat.fmt`` call form and literal arguments — that is what
        ``m3trik/scripts/check_tooltips.py`` statically renders and validates.
        """
        from uitk.widgets.mixins.tooltip_mixin import TooltipFormat

        return {
            "export_visible_objects": {
                "widget_type": "ComboBox",
                "panel": "settings",
                "set_row_label": "Scope",
                "setToolTip": TooltipFormat.fmt(
                    title="Export Scope",
                    body="Which objects the export set is built from, resolved "
                    "fresh each time you export.",
                    bullets=[
                        "<b>All Scene Objects</b> — every transform and geometry "
                        "node, visible or not.",
                        "<b>All Visible Objects</b> — visible geometry only, "
                        "honoring inherited parent visibility. Templated objects "
                        "are excluded; objects with animated visibility are kept, "
                        "since their animation is baked and ships.",
                        "<b>Selected Objects Only</b> — exactly the current selection.",
                    ],
                    notes=[
                        "The data_export metadata carrier is a hidden helper node, "
                        "not geometry, so <b>Export Scene Data Node</b> is what "
                        "puts it in the set."
                    ],
                ),
                "add": self._export_mode_options,
                "value_method": "currentData",
            },
            "export_data_node": {
                "widget_type": "QCheckBox",
                "panel": "settings",
                "setText": "Export Scene Data Node",
                "setToolTip": TooltipFormat.fmt(
                    title="Export Scene Data Node",
                    body="Ship the shared <b>data_export</b> carrier node inside "
                    "the FBX, carrying whatever metadata the scene's subsystems "
                    "have stamped on it.",
                    bullets=[
                        "Shots writes <b>shot_metadata</b> (each clip with its "
                        "frame range).",
                        "Audio writes <b>audio_manifest</b>.",
                        "Any other producer's channel rides along the same way.",
                    ],
                    notes=[
                        "The carrier is hidden, so the Visible and Selected scopes "
                        "would otherwise drop it.",
                        "Refreshed from the live scene at export; no-op when there "
                        "is no metadata to carry.",
                        "A readable copy is also written beside the export as "
                        ".scene_data.json.",
                        "This ships the metadata only — it never changes the "
                        "animation. Splitting the timeline into clips is "
                        "<b>Animation Clips</b>.",
                    ],
                ),
                "setChecked": True,
            },
            "set_linear_unit": {
                "widget_type": "ComboBox",
                "panel": "settings",
                "set_row_label": "Units",
                "setToolTip": TooltipFormat.fmt(
                    title="Linear Unit",
                    body="Working linear unit Maya is switched to for the FBX "
                    "write, then switched back.",
                    notes=[
                        "The FBX plug-in stamps the file's unit from the working "
                        "unit at write time, so this is the scale the receiving "
                        "engine reads.",
                        "<b>OFF</b> writes in the scene's current unit.",
                    ],
                ),
                "add": self._scene_unit_options,
            },
            "set_workspace": {
                "widget_type": "QCheckBox",
                "panel": "settings",
                "setText": "Auto Set Workspace",
                "setToolTip": TooltipFormat.fmt(
                    title="Auto Set Workspace",
                    body="Derive the workspace from the scene path and point the "
                    "process working directory at it for the FBX write.",
                    notes=[
                        "The FBX plug-in resolves relative texture paths against "
                        "the working directory, not the workspace — without this, "
                        "embedding fails with 'The following texture(s) will not "
                        "be embedded'.",
                        "Both changes are restored after the export.",
                    ],
                ),
                "setChecked": True,
            },
            "exclude_hdr": {
                "widget_type": "QCheckBox",
                "panel": "settings",
                "setText": "Exclude HDR Environment",
                "setToolTip": TooltipFormat.fmt(
                    title="Exclude HDR Environment",
                    body="Keep the Arnold HDR environment light (aiSkyDomeLight) "
                    "out of the export set.",
                    notes=[
                        "The skydome is image-based scene lighting, not "
                        "deliverable geometry — under <b>All Scene Objects</b> it "
                        "would otherwise ride into the FBX.",
                        "No-op when the scene has no skydome.",
                    ],
                ),
                "setChecked": True,
            },
            # A mode, not a task: ``ExportRun.from_tasks`` pops it into the flag
            # that arms the POST-write pass (``TaskManager.drop_rig_apparatus``),
            # the idiom Verify The Written File rides -- the file has to exist
            # before its helpers can be dropped from it.
            "drop_rig_apparatus": {
                "widget_type": "QCheckBox",
                "panel": "settings",
                "setText": "Exclude Rig Helpers",
                "setToolTip": TooltipFormat.fmt(
                    title="Exclude Rig Helpers",
                    body="Drop the parts of a rig that only drove the animation — "
                    "controls, IK handles, up-vector locators, driver and proxy "
                    "joints, and the groups holding only those — from the written "
                    "FBX, and so from the GLB built from it.",
                    notes=[
                        "The bake has already put their motion on the joints and "
                        "meshes they drove; in the file they draw nothing and "
                        "drive nothing, yet each ships animated, and the GLB "
                        "conversion bakes every one of them at every frame.",
                        "A rig node is kept when anything still needs it: a mesh "
                        "below it, a skin bound to it, or scene data on it. A "
                        "scene's own groups and locators are never touched — "
                        "only what a rig's graph names.",
                        "The scene is not changed; only the written file is.",
                        "No effect on a USD export.",
                    ],
                ),
                "setChecked": True,
            },
            "reassign_duplicate_materials": {
                "widget_type": "QCheckBox",
                "group": "Materials",
                "setText": "Reassign Duplicate Materials",
                "setToolTip": TooltipFormat.fmt(
                    title="Reassign Duplicate Materials",
                    body="Collapse materials that are genuinely identical onto a "
                    "single keeper and reassign every object using them.",
                    bullets=[
                        "Candidates are grouped by node type and texture set, "
                        "matched on file name — so the same map loaded from two "
                        "folders still groups.",
                        "Each candidate is then verified against its keeper: "
                        "unconnected attribute values, placement and color space "
                        "per texture slot, and texture content (size plus a "
                        "partial hash) whenever the stored paths differ.",
                    ],
                    notes=[
                        "Only verified duplicates are merged — the merge deletes "
                        "what it collapses, so the verification is what makes it "
                        "safe.",
                        "Reports the same materials as <b>Check For Duplicate "
                        "Materials</b>.",
                        "Permanent scene change — not reverted after export.",
                    ],
                ),
                "setChecked": True,
            },
            "convert_to_relative_paths": {
                "widget_type": "QCheckBox",
                "group": "Materials",
                "setText": "Convert To Relative Paths",
                "setToolTip": TooltipFormat.fmt(
                    title="Convert To Relative Paths",
                    body="Rewrite the export materials' texture paths as "
                    "project-relative paths.",
                    notes=[
                        "Scoped to textures already under <b>sourceimages</b> "
                        "(subfolders included). A texture stored anywhere else "
                        "keeps its absolute path — an external reference is "
                        "usually deliberate, and this task never relocates it. "
                        "The log names any it left alone.",
                        "A relative path only resolves if the file physically "
                        "lives under sourceimages, which is why an external one "
                        "is skipped rather than rewritten: relativizing it would "
                        "point at a file that isn't there and silently break the "
                        "material on import.",
                        "The path edits persist after the export, and are "
                        "undo-anchored so Maya's undo can back them out.",
                    ],
                ),
                "setChecked": True,
            },
            "resolve_invalid_texture_paths": {
                "widget_type": "QCheckBox",
                "group": "Materials",
                "setText": "Resolve Invalid Texture Paths",
                "setToolTip": TooltipFormat.fmt(
                    title="Resolve Invalid Texture Paths",
                    body="Rebind broken texture paths by hunting for the missing "
                    "file anywhere under sourceimages, scoped to the materials "
                    "being exported. Committed lightmaps get the same hunt: a "
                    "bake marker whose recorded folder no longer holds its map "
                    "is rewritten to where the map was found, and the FBX "
                    "manifest republished.",
                    notes=[
                        "Rebinding by name is a guess — the original file is gone, "
                        "so nothing can verify content. The hunt is therefore "
                        "gated: the basename must match exactly one file. A unique "
                        "hit is rebound and logged old → new; an ambiguous name is "
                        "reported instead of guessed at.",
                        "&lt;UDIM&gt; / &lt;f&gt; names match by pattern and keep "
                        "their token.",
                        "Lightmap files are never moved — only the marker's "
                        "recorded folder changes. To gather them into the "
                        "project use Texture Path Editor ▸ Find &amp; Copy.",
                        "Permanent scene change — not reverted after export.",
                    ],
                ),
                "setChecked": True,
            },
            # -- Textures group: the Texture Output gate FIRST, then the three
            # dials it governs directly beneath it, so the gate and the gated
            # read as one block in the Tasks combo.
            "texture_write_back": {
                "widget_type": "ComboBox",
                "group": "Textures",
                "set_row_label": "Texture Output",
                "setToolTip": TooltipFormat.fmt(
                    title="Texture Output",
                    body="Whether the texture rows below — the <b>Textures</b> "
                    "template conversion and the <b>Optimize Textures</b> "
                    "pass (its size ceiling included) — modify the scene's "
                    "textures, or leave the scene as it was.",
                    bullets=[
                        "<b>Export Copies (Scene Untouched)</b> — "
                        "non-destructive: processed maps are staged for the "
                        "write (a temp folder when the deliverable embeds "
                        "its media, else <b>textures/</b> beside it), the "
                        "materials read them for the export, and the scene's "
                        "networks and paths are restored afterwards.",
                        "<b>Scene Files (In Place)</b> — permanent: the "
                        "conversion migrates the materials and the "
                        "optimization overwrites the scene's own texture "
                        "files (originals archived beside each texture in an "
                        "<b>original_textures</b> folder). Not reverted after "
                        "export.",
                    ],
                    notes=[
                        "Inert unless a template is selected or Optimize "
                        "Textures is on.",
                    ],
                ),
                "add": self._texture_output_options,
            },
            "convert_textures": {
                "widget_type": "ComboBox",
                "group": "Textures",
                # The widget keeps the objectName it had as a Settings row, so
                # every saved template key, ``cmb005_init`` and b000's reads
                # stay valid across the move into the Tasks combo.
                "object_name": "cmb005",
                "set_row_label": "Texture Template",
                "setToolTip": TooltipFormat.fmt(
                    title="Texture Template",
                    body="Convert the export's textures to a target texture "
                    "template (a pythontk map-registry workflow) before the "
                    "write — channel packing and shading model re-authored to "
                    "match what the destination engine expects.",
                    bullets=[
                        "<b>As Authored</b> (default) — send textures exactly "
                        "as the scene references them; converts nothing.",
                        "A template — materials are rebuilt through the Map "
                        "Updater, and a paired check fails the export if any "
                        "mask map still does not match.",
                    ],
                    notes=[
                        "Also drives <b>Optimize Textures</b>: the template's "
                        "per-map-type output spec supplies each map's bit "
                        "depth and container, and its size budget is what "
                        "that combo's Template Budget option enforces.",
                        "Where the rebuilt maps land — export copies or the "
                        "scene's own files — is <b>Texture Output</b>.",
                    ],
                ),
            },
            "optimize_textures": {
                "widget_type": "ComboBox",
                "group": "Textures",
                # NOT the old checkbox's objectName: a preset saved before the
                # merge carries optimize_textures (a bool) plus a separate
                # texture_max_size (an index), and letting the bool restore
                # onto this combo would keep the pass while silently dropping
                # the preset's size ceiling. A fresh name makes such a preset
                # trip the PresetManager's uncovered-keys warning instead, so
                # the user re-saves and the template is whole again. (The TASK
                # key stays optimize_textures — b000 decomposes this widget's
                # value back into the optimize_textures + texture_max_size
                # inputs the engine has always taken, so headless callers and
                # TASK_ORDER see no change.)
                "object_name": "texture_optimize",
                "set_row_label": "Optimize Textures",
                "setToolTip": TooltipFormat.fmt(
                    title="Optimize Textures",
                    body="Run the Map Converter's per-map-type optimization "
                    "pass on the textures shipping with this export — mode "
                    "and bit depth corrected per map type, the export reads "
                    "the optimized copies — with an optional longest-edge "
                    "ceiling: larger maps are downsampled, smaller ones "
                    "never grown.",
                    bullets=[
                        "<b>OFF</b> — ship every map as it is: a GLB's "
                        "embedded copies keep their own resolution too.",
                        "<b>Optimize</b> — the pass without resampling (a "
                        "template's size budget is only reported).",
                        "<b>Optimize + Max 512 … 8192</b> — the pass plus a "
                        "hard pixel ceiling, whatever the template says.",
                        "<b>Optimize + Template Budget</b> — the pass plus "
                        "the selected <b>Textures</b> template's own size "
                        "budget (e.g. glTF/URP 2048, HDRP/Unreal 4096; the "
                        "power-of-two rule is not applied). No resize with "
                        "Textures at <b>As Authored</b> or an unbudgeted "
                        "template.",
                    ],
                    notes=[
                        "With a <b>Textures</b> template selected, the "
                        "template's per-map-type output spec also drives each "
                        "map's container and bit depth (delivery containers "
                        "like KTX2 stay with the GLB half of <b>Texture File "
                        "Type</b>); at <b>As Authored</b> it is a generic "
                        "per-map-type pass and each map keeps its container.",
                        "The ceiling also caps a GLB deliverable's embedded "
                        "copies — one size policy for everything the export "
                        "ships. A plain <b>Optimize</b> names no ceiling, so a "
                        "GLB takes the web delivery ceiling, 2048 px; "
                        "<b>OFF</b> resizes nothing.",
                        "Where the optimized maps go — export copies or the "
                        "scene's own files — is <b>Texture Output</b>.",
                        "Already-optimal maps are left untouched; the paired "
                        "check names anything the pass could not optimize.",
                    ],
                ),
                # Registry-derived: the item list comes from pythontk's
                # container/format registry, so inserting a format upstream
                # shifts every index after it and a template that stored
                # "JPG" would silently start selecting its neighbour. Persist
                # the VALUE (see StateManager.restore_by); indices already on
                # disk are migrated once by _legacy_combo_index.
                "restore_by": "text",
                "add": self._optimize_textures_options,
            },
            "texture_file_type": {
                "widget_type": "ComboBox",
                "group": "Textures",
                "set_row_label": "Texture File Type",
                "setToolTip": TooltipFormat.fmt(
                    title="Texture File Type",
                    body="Container every texture shipping with this export is "
                    "written in — the maps beside (or inside) the FBX and the "
                    "images embedded in a GLB alike.",
                    bullets=[
                        "<b>Original</b> — keep each source's container; with "
                        "a <b>Textures</b> template selected, the template's "
                        "per-map-type container decides.",
                        "<b>PNG … HDR</b> — write every map as that format.",
                        "<b>KTX2</b> — GPU-compressed Basis for web/XR "
                        "runtimes (UASTC for normals/data, ETC1S for color; "
                        "lightmaps stay lossless WebP). Ships only inside a "
                        "GLB, as KTX2 alone: the smallest deliverable, but it "
                        "needs a basisu-capable viewer (three.js KTX2Loader) "
                        "— Blender, Unreal or stock Unity cannot read its "
                        "textures.",
                        "<b>KTX2 + PNG/JPEG</b> — the same KTX2 set plus a "
                        "standard copy of every map as its KHR_texture_basisu "
                        "fallback (PNG for normals, ORM and alpha; JPEG for "
                        "opaque color; lightmaps stay PNG), so the GLB also "
                        "opens in Blender, Unreal or stock Unity. The copies "
                        "cost about as much again as the KTX2 (146 MB beside "
                        "123 MB on a 4K production assembly).",
                    ],
                    notes=[
                        "Both KTX2 entries need KTX-Software's <b>toktx</b>, "
                        "offered as a managed install when it is missing.",
                        "Naming a type outranks the template's per-map-type "
                        "container, which still supplies bit depth and budget.",
                        "Each destination clamps what it cannot carry: a "
                        "scene file node and an FBX cannot read KTX2, so the "
                        "scene keeps its own container there, and a GLB falls "
                        "back to the web default (WebP) for anything glTF "
                        "cannot embed (PNG/JPEG/WebP/KTX2 are the ones it "
                        "can).",
                        "Applied by <b>Optimize Textures</b> for scene maps; "
                        "a GLB deliverable is re-encoded whether or not that "
                        "pass runs.",
                    ],
                ),
                # Registry-derived: the item list comes from pythontk's
                # container/format registry, so inserting a format upstream
                # shifts every index after it and a template that stored
                # "JPG" would silently start selecting its neighbour. Persist
                # the VALUE (see StateManager.restore_by); indices already on
                # disk are migrated once by _legacy_combo_index.
                "restore_by": "text",
                "add": self._texture_file_type_options,
            },
            "secondary_max_size": {
                "widget_type": "ComboBox",
                "group": "Textures",
                "set_row_label": "Secondary Map Size",
                "setToolTip": TooltipFormat.fmt(
                    title="Secondary Map Size",
                    body="A lower size ceiling for the GLB deliverable's packed "
                    "data maps — metallic-roughness and occlusion — under the "
                    "ceiling Optimize Textures sets. Smooth masks read the same "
                    "at half the resolution; color keeps the primary ceiling "
                    "(the perceptual detail) and so do normal maps (the surface "
                    "detail a resample visibly softens).",
                    bullets=[
                        "<b>Same As Other Maps</b> — one ceiling for every map.",
                        "<b>Max 512 … 2048</b> — the data maps' own ceiling, "
                        "never above the primary.",
                    ],
                    notes=[
                        "GLB only: the FBX and the scene's own maps are untouched.",
                        "Measured on a 4K production assembly: the eight ORM "
                        "packs were 63 MB of a 155 MB GLB; at 2K they cost a "
                        "quarter of that.",
                    ],
                ),
                "restore_by": "text",
                "add": self._secondary_max_size_options,
            },
            "uastc_rdo": {
                "widget_type": "ComboBox",
                "group": "Textures",
                "set_row_label": "KTX2 RDO",
                "setToolTip": TooltipFormat.fmt(
                    title="KTX2 UASTC RDO",
                    body="Rate-distortion optimisation for the GLB's UASTC "
                    "encodes (normal and data maps): the blocks are steered "
                    "toward what the Zstandard stage compresses, at a "
                    "controlled quality cost.",
                    bullets=[
                        "<b>OFF</b> — plain UASTC, the largest encode.",
                        "<b>Light / Standard / Strong</b> — lambda 0.5 / 1 / 2: "
                        "more bytes saved, more quality spent. Normal maps are "
                        "capped at 0.75 whatever the dial says (toktx's own "
                        "guidance).",
                    ],
                    notes=[
                        "Measured on a 4K production set: ORM packs −30% at "
                        "lambda 1 (PSNR 50/44/48 dB); normal maps −1 to −14% at "
                        "their 0.75 cap, a noisy one −45% (PSNR 46–56 dB); the "
                        "encode runs 3–4× longer.",
                        "Only with <b>Texture File Type</b> at KTX2; ETC1S "
                        "(color) has no RDO stage.",
                    ],
                ),
                "restore_by": "text",
                "add": self._uastc_rdo_options,
            },
            "baked_reflections": {
                "widget_type": "ComboBox",
                "group": "Lighting",
                "set_row_label": "Baked Reflections",
                "setToolTip": TooltipFormat.fmt(
                    title="Baked Reflections",
                    body="How strongly a lightmapped material reflects the "
                    "viewer's environment. Published in the deliverable's "
                    "lighting recipe, so any reader -- the WebXR preview "
                    "included -- lights it the way it was approved.",
                    bullets=[
                        "<b>Off (Pure Bake)</b> — the bake alone: no "
                        "reflection or gloss on a baked surface.",
                        "<b>Quarter</b> — the default: the bake keeps its "
                        "contrast, and gloss and normal maps still read.",
                        "<b>Half / Full</b> — stronger reflections; Full "
                        "lifts every dark glossy baked surface.",
                    ],
                    notes=[
                        "A lightmap already holds the surface's diffuse "
                        "light, so a baked material only ever takes the "
                        "environment's specular; this sets how much. The "
                        "viewer's environment is a bright studio, not the room "
                        "the bake lit: measured on a production room, the "
                        "darkest baked surfaces read 0.06 of display baked "
                        "alone, 0.22 at Full and 0.11 at Quarter.",
                        "Only lightmapped materials; everything else takes the "
                        "environment whole.",
                        "Decided here and carried by the deliverable (GLB and "
                        "FBX alike): the WebXR Preview's Baked Reflections row "
                        "is this row, and nothing it does is read back.",
                    ],
                ),
                "restore_by": "text",
                "add": self._baked_reflections_options,
                "setCurrentIndex": list(self._baked_reflections_options.values()).index(
                    ptk.ExportProfile.baked_reflections_default()
                ),
            },
            # -- Animation group: the Animation Output gate FIRST, then the
            # rows it governs, the same way the Textures group reads.
            "animation_write_back": {
                "widget_type": "ComboBox",
                "group": "Animation",
                "set_row_label": "Animation Output",
                "setToolTip": TooltipFormat.fmt(
                    title="Animation Output",
                    body="Whether the key-editing rows below — <b>Smart Bake</b>, "
                    "<b>Optimize Keys</b>, <b>Tie All Keyframes</b> and "
                    "<b>Snap Keys To Frame</b> — change the scene's animation, "
                    "or leave the scene as it was.",
                    bullets=[
                        "<b>Export Copies (Scene Untouched)</b> — "
                        "non-destructive: the curves are captured first, the "
                        "edits are made and written into the deliverable, and "
                        "the scene's keys are restored afterwards.",
                        "<b>Scene Keys (In Place)</b> — permanent: the "
                        "optimized, snapped, tied and baked curves stay in the "
                        "scene. Not reverted after export.",
                    ],
                    notes=[
                        "Inert unless one of those four rows is on.",
                        "Restores the CONTENT of each curve, so animation "
                        "layers, driven keys and constraints are untouched.",
                    ],
                ),
                "add": self._animation_output_options,
            },
            "flatten_sheared_chains": {
                "widget_type": "QCheckBox",
                "group": "Animation",
                "setText": "Flatten Sheared Chains",
                "setToolTip": TooltipFormat.fmt(
                    title="Flatten Sheared Chains",
                    body="Re-anchor joints whose parent-relative transform is "
                    "sheared, so the export can represent them. FBX and glTF "
                    "store animated nodes as translate/rotate/scale \u2014 "
                    "shear is silently dropped and the error compounds down "
                    "a chain.",
                    notes=[
                        "A squash/stretch chain shears with NO authored "
                        "shear: every joint carries the same non-uniform "
                        "world scale, so world matrices look clean while the "
                        "matrices BETWEEN joints skew. Measured: 47% stretch "
                        "put a chain's end 7.5 cm off in the deliverable.",
                        "Live, not baked: each flagged joint is reparented "
                        "under its nearest clean ancestor with its "
                        "offsetParentMatrix rewrapped, so the rig's drivers "
                        "keep working and worlds are preserved exactly.",
                        "The hierarchy and wiring are restored after the write.",
                        "<b>Check For Sheared Local Transforms</b> verifies "
                        "the result.",
                    ],
                ),
                "setChecked": True,
            },
            "smart_bake": {
                "widget_type": "QCheckBox",
                "group": "Animation",
                "setText": "Smart Bake",
                "setToolTip": TooltipFormat.fmt(
                    title="Smart Bake",
                    body="Bake the rig's indirect animation — constraints, driven "
                    "keys, expressions, IK, motion paths, blend shapes — down to "
                    "plain keyframes, which is all an FBX can carry.",
                    notes=[
                        "The time range is detected from the drivers themselves.",
                        "Bakes onto an override layer; whether the scene keeps it "
                        "is <b>Animation Output</b>'s call, and by default the "
                        "pre-bake state is restored after the write.",
                        "<b>Optimize Keys</b> also sets the level of the "
                        "optimization pass inside this bake — and <b>Reduce To Extremes</b> "
                        "is the level that suits its per-frame output.",
                    ],
                ),
                "setChecked": True,
            },
            "optimize_keys": {
                "widget_type": "ComboBox",
                "group": "Animation",
                # NOT the old checkbox's objectName. A template saved before
                # this merge carries optimize_keys as a BOOL, and combos
                # persist by index — restoring `true` onto this widget would
                # silently select index 1 (Static Curves Only), a level the
                # user never chose. A fresh name makes such a template trip
                # the PresetManager's uncovered-keys warning instead, so the
                # user re-saves and the template is whole again. (The TASK key
                # stays optimize_keys — the task method takes the level, and
                # a headless caller's legacy True still means what it did.)
                "object_name": "optimize_level",
                "set_row_label": "Optimize Keys",
                "setToolTip": TooltipFormat.fmt(
                    title="Optimize Keys",
                    body="Remove animation data the deliverable does not need, "
                    "at the chosen level.",
                    bullets=[
                        "<b>OFF</b> — ship every curve and key as authored.",
                        "<b>Static Curves Only</b> — delete curves whose value "
                        "never changes; every surviving curve keeps all of its "
                        "keys. The conservative rung: nothing carrying motion "
                        "is touched.",
                        "<b>Static + Flat Keys</b> — also drop the redundant "
                        "interior keys of a flat run.",
                        "<b>+ Simplify (lossy)</b> — also drop keys whose "
                        "absence changes the curve by less than the tolerance. "
                        "That is a judgement about the tolerance, so the "
                        "result is worth eyeballing.",
                        "<b>Reduce To Extremes</b> — reduce smooth "
                        "curves to their endpoints, peaks, valleys and hold "
                        "boundaries, with tangents refit to the baked motion. "
                        "The one to reach for after <b>Smart Bake</b>: a "
                        "per-frame bake has no redundant flat keys for the "
                        "other levels to find. It thins a bake, it does not "
                        "reverse one — that is Smart Bake's <b>Unbake</b>.",
                    ],
                    notes=[
                        "Stepped tangents are preserved at every level.",
                        "Also sets the level used inside <b>Smart Bake</b> — "
                        "that pass reaches the baked override-layer curves "
                        "this one cannot.",
                        "Whether the scene keeps this is <b>Animation Output</b>'s "
                        "call; by default the curves are restored after the write.",
                        "<b>Reduce To Extremes</b> rewrites tangents through the API, which "
                        "bypasses Maya's undo queue — so at <b>Animation "
                        "Output: Scene Keys (In Place)</b> it is not reversible "
                        "with Ctrl+Z. At the default it is, because the export's "
                        "own curve snapshot is restored either way.",
                    ],
                ),
                "add": self._optimize_keys_options,
                # Applied after 'add' (which lands on index 0): index 2 is
                # Static + Flat Keys, exactly what the old checked box did.
                "setCurrentIndex": 2,
            },
            "glb_key_tolerance": {
                "widget_type": "ComboBox",
                "group": "Animation",
                "set_row_label": "GLB Key Tolerance",
                "setToolTip": TooltipFormat.fmt(
                    title="GLB Key Tolerance",
                    body="The GLB half of Optimize Keys. The converter bakes a "
                    "key on every frame of every channel, so the optimisation "
                    "above never reaches the GLB; its clips are reduced to this "
                    "bound instead. Each clip keeps the keys that reproduce "
                    "every original sample within the bound under the viewer's "
                    "own interpolation (slerp for rotations); the first and "
                    "last key always stay, so no clip changes length or origin.",
                    bullets=[
                        "<b>Keep Every Key</b> — the converter's per-frame keys.",
                        "<b>Within 1e-6 … 1e-3</b> — the largest deviation any "
                        "sample may show: scene units (meters) for translation "
                        "and scale, quaternion components for rotation; 1e-4 "
                        "is 0.1 mm / 0.006°.",
                    ],
                    notes=[
                        "Rides Optimize Keys: OFF there keeps every key here. "
                        "GLB only; the FBX keeps its optimised curves.",
                        "Measured on a 4K production assembly: 2.18 M keys, "
                        "6.8% kept within 1e-4 — 23.5 MB of animation to 2.4.",
                        "Stepped channels (visibility gates, fades) lose only "
                        "repeated values, which is lossless.",
                    ],
                ),
                "restore_by": "text",
                "add": self._glb_key_reduction_options,
                # Applied after 'add' (which lands on index 0): index 2 is
                # Within 1e-4, the bound the production measurements used.
                "setCurrentIndex": 2,
            },
            "tie_all_keyframes": {
                "widget_type": "QCheckBox",
                "group": "Animation",
                "setText": "Tie All Keyframes",
                "setToolTip": TooltipFormat.fmt(
                    title="Tie All Keyframes",
                    body="Insert bookend keys at the first and last keyframe of "
                    "the whole export set, on every channel that is already "
                    "animated, so no animated channel stops short of the range.",
                    notes=[
                        "Fixes what <b>Check For Untied Keyframes</b> reports.",
                        "Tangents on the neighboring keys are frozen first, so the "
                        "inserted keys do not reshape the curve.",
                        "Whether the scene keeps this is <b>Animation Output</b>'s "
                        "call; by default the curves are restored after the write. "
                        "Kept in place, the insert bypasses Maya's undo queue — "
                        "revert with AnimUtils.untie_keyframes rather than Ctrl+Z.",
                    ],
                ),
                "setChecked": True,
            },
            "snap_keys_to_frame": {
                "widget_type": "QCheckBox",
                "group": "Animation",
                "setText": "Snap Keys To Frame",
                "setToolTip": TooltipFormat.fmt(
                    title="Snap Keys To Frame",
                    body="Round every key on the exported objects to the nearest "
                    "whole frame.",
                    notes=[
                        "Fixes what <b>Check For Floating Point Keys</b> reports — "
                        "fractional key times left behind by retiming, scaling, or "
                        "an import at a different rate.",
                        "Whether the scene keeps this is <b>Animation Output</b>'s "
                        "call; by default the curves are restored after the write.",
                    ],
                ),
                "setChecked": False,
            },
            "set_bake_animation_range": {
                "widget_type": "ComboBox",
                "group": "Animation",
                # New objectName for the same reason as optimize_level above:
                # the retired checkbox's `true` would restore as index 1 here.
                # Index 1 happens to be Auto — the right default — but that is
                # a coincidence, not a migration, and the next inserted row
                # would end it.
                "object_name": "bake_range",
                "set_row_label": "Bake Range",
                "setToolTip": TooltipFormat.fmt(
                    title="Bake Range",
                    body="Which frames the FBX bakes — overriding the range "
                    "stored in the FBX preset, whose factory value (1-48) is "
                    "not the scene's anything.",
                    bullets=[
                        "<b>OFF</b> — keep the preset's range.",
                        "<b>Auto (Shots → Keyframes)</b> — the span of the "
                        "shots declared in the <b>Shots</b> panel; a scene "
                        "with no shots falls back to the keyframe extent. "
                        "With shots authored, this is what keeps animation "
                        "outside them out of the deliverable.",
                        "<b>Keyframe Extent</b> — the first and last keyframe "
                        "of the exported objects (start floored, end ceiled).",
                        "<b>Scene Animation Range</b> — the scene's authored "
                        "range, not the playback slider. What an export "
                        "through Maya's own dialog gets by default.",
                    ],
                    notes=[
                        "Applies only when Bake Animation is enabled in the FBX "
                        "export settings; otherwise it is skipped.",
                        "Runs last, so it measures the final state of the "
                        "curves — and every mode is widened to cover the takes "
                        "<b>Animation Clips</b> declares, so no "
                        "choice here can ship metadata describing animation the "
                        "file does not contain.",
                        "A GLB rebuilds its clips by slicing the whole-timeline "
                        "stack, so this is what decides how much of the timeline "
                        "it has to slice — <b>Auto</b> is the setting that makes "
                        "a GLB cover exactly the shots.",
                        "The preset's range is restored after the write.",
                    ],
                ),
                "add": self._bake_range_options,
                # Applied after 'add' (which lands on index 0): index 1 is
                # Auto. With shots declared this reproduces what the old
                # default pair did (the split's union won); with none, the
                # keyframe extent the old checkbox measured. The one behavior
                # change is a scene WITH shots and the split switched off —
                # which now clamps to them instead of shipping everything.
                "setCurrentIndex": 1,
            },
            "apply_declared_takes": {
                "widget_type": "ComboBox",
                "group": "Animation",
                # NOT the retired checkbox's objectName, for the reason spelled
                # out on optimize_level: a template saved before this row
                # became a combo carries apply_declared_takes as a BOOL, and
                # combos persist by INDEX -- restoring `true` would select
                # index 1 (Shots Only) and silently stop shipping the sequence.
                # A fresh name trips the PresetManager's uncovered-keys warning
                # instead, so the user re-saves deliberately. (The TASK key
                # stays apply_declared_takes: the method takes the mode, and a
                # headless caller's legacy True still means what it did.)
                "object_name": "animation_clips",
                "set_row_label": "Animation Clips",
                "setToolTip": TooltipFormat.fmt(
                    title="Animation Clips",
                    body="Which animation the deliverable ships: the declared "
                    "shots as separate clips, the whole timeline as one "
                    "continuous clip, or both.",
                    bullets=[
                        "<b>Shots + Full Sequence</b> — both, the historical "
                        "shape. Nothing to choose between if the consumer is "
                        "unknown.",
                        "<b>Shots Only</b> — the shots, without the stack they "
                        "were cut from. For a player that switches clips.",
                        "<b>Full Sequence Only</b> — one continuous clip. For a "
                        "player that seeks a window inside it; each shot's frame "
                        "range still rides in <b>extras.animation_web</b>.",
                    ],
                    notes=[
                        "The two halves hold the SAME performance — the shots are "
                        "cut from the sequence — so a player that reads one never "
                        "reads the other. Measured on a production assembly: the "
                        "sequence alone was 66.5 MB, the shots 43.8 MB.",
                        "Requires shots defined in the Shots panel; with none "
                        "declared every mode ships the one continuous clip.",
                        "This is <b>not</b> what ships the shot metadata — "
                        "<b>Export Scene Data Node</b> already does that, and the "
                        "two share one refresh.",
                        "The <b>FBX</b> leg splits takes for Unity on the two "
                        "shot-bearing modes. Maya's split is lossy (a curve with "
                        "no key inside a shot contributes nothing to it), so the "
                        "<b>GLB</b> always rebuilds its clips from the "
                        "whole-timeline stack instead — which is why the sequence "
                        "is cut even when it is not shipped.",
                        "Forces Bake Animation on, and guarantees a range covering "
                        "the takes it declares; <b>Bake Range</b> then widens to "
                        "cover them, so the two cannot disagree. Both are restored "
                        "after the write.",
                    ],
                ),
                "add": self._animation_clips_options,
                # Index 2 = "Shots + Full Sequence", what the checkbox this
                # replaced did when ticked -- and it was default-on, so a
                # panel opened without a preset ships exactly what it used to.
                "setCurrentIndex": 2,
            },
            "conform_shape_names": {
                "widget_type": "QCheckBox",
                "group": "Hierarchy",
                "setText": "Fix Mangled Names",
                "setToolTip": TooltipFormat.fmt(
                    title="Fix Mangled Names",
                    body="Repair scratch and mangled names across the export set — "
                    "transforms and shapes alike — then conform each shape to "
                    "Maya's '&lt;transform&gt;Shape' convention.",
                    bullets=[
                        "Accumulated '__uninst_tmp' scratch tokens",
                        "'__RZTMP' Rizom round-trip suffixes",
                        "'FBXASC###' import escapes",
                        "Runs of three or more underscores",
                    ],
                    notes=[
                        "Clears the <b>Check For Mangled Names</b> failure.",
                        "Permanent scene change — not reverted after export.",
                    ],
                ),
                "setChecked": False,
            },
            "ignore_groups": {
                "widget_type": "QLineEdit",
                "panel": "settings",
                "set_row_label": "Ignore",
                "setPlaceholderText": "Group names to ignore (comma-separated, wildcards ok)",
                "setToolTip": TooltipFormat.fmt(
                    title="Ignore Groups",
                    body="Comma-separated name patterns of top-level groups to "
                    "drop from the export set.",
                    notes=[
                        "Example: temp, proxy",
                        "Wildcards: <b>*</b> any run of characters, <b>?</b> a "
                        "single one &mdash; <b>temp*</b> catches temp_01 and "
                        "tempRig, <b>*_proxy</b> catches hull_proxy.",
                        "A pattern with no wildcard matches that exact name.",
                        "Leave empty to skip.",
                        "Matching ignores case unless the <b>Aa</b> button beside "
                        "the field is on.",
                    ],
                ),
                "setText": "temp",
                "value_method": "text",
            },
        }

    @property
    def check_definitions(self) -> Dict[str, Dict[str, Any]]:
        """Return the check definitions for the UI.

        A failed check aborts the export, so each tooltip below leads with what
        makes it fail.  Tooltip authoring rules: see :attr:`task_definitions`.
        """
        from uitk.widgets.mixins.tooltip_mixin import TooltipFormat

        return {
            "check_referenced_objects": {
                "widget_type": "QCheckBox",
                "group": "General",
                "setText": "Check For Referenced Objects",
                "setToolTip": TooltipFormat.fmt(
                    title="Check For Referenced Objects",
                    body="Fails the export when the scene contains file references.",
                    notes=[
                        "Scans the whole scene, not just the export set.",
                        "Import the reference (or remove it) to pass.",
                    ],
                ),
                "setChecked": True,
            },
            "check_output_writable": {
                "widget_type": "QCheckBox",
                "group": "General",
                "setText": "Check Output File Is Writable",
                "setToolTip": TooltipFormat.fmt(
                    title="Check Output File Is Writable",
                    body="Fails the export when a file it is about to write is "
                    "held open by another process.",
                    notes=[
                        "Windows will not let anything replace a file while a "
                        "viewer, a preview or an engine has it open.",
                        "Runs before the first scene change, so a locked "
                        "destination costs milliseconds instead of the whole "
                        "pipeline — the write is the LAST thing an export does.",
                        "Names the process to close whenever Windows will say.",
                    ],
                ),
                "setChecked": True,
            },
            "check_geometry_lod_suffix": {
                "widget_type": "QCheckBox",
                "group": "Hierarchy & Naming",
                "setText": "Check Geometry LOD Suffix (_LODx)",
                "setToolTip": TooltipFormat.fmt(
                    title="Check Geometry LOD Suffix (_LODx)",
                    body="Lists geometry named with an LOD suffix — '_LOD' alone "
                    "or followed by digits ('_LOD1', '_LOD02'), case-insensitive.",
                    notes=[
                        "Informational only: it reports what it finds and never "
                        "fails the export."
                    ],
                ),
                "setChecked": True,
            },
            "check_duplicate_names": {
                "widget_type": "ComboBox",
                "group": "Hierarchy & Naming",
                "set_row_label": "Duplicate Names",
                "setToolTip": TooltipFormat.fmt(
                    title="Check For Duplicate Names",
                    body="Fails the export when two nodes in the export set "
                    "share a short name. The dial is how wide it looks — each "
                    "step includes the one above it.",
                    bullets=[
                        "<b>Locators</b> — attach points and sockets, which "
                        "whatever consumes them downstream matches by name.",
                        "<b>Locators &amp; Joints</b> — adds the skeleton the "
                        "FBX writes as bones; duplicate bone names break "
                        "skinning and retargeting on import.",
                        "<b>Connected &amp; Animated</b> — adds every transform "
                        "with an incoming connection on a transform or "
                        "visibility channel: constraints, keys, drivers, "
                        "expressions, IK. Their names are what the take and "
                        "metadata bindings resolve against.",
                        "<b>All Export Objects</b> — every node in the set, "
                        "plain groups included. The strictest setting: nested "
                        "groups sharing a name are legal in Maya and harmless "
                        "in the FBX, so expect noise.",
                    ],
                    notes=[
                        "Compares short names, so nodes under different parents "
                        "still collide — which is what a consumer matching them "
                        "by name downstream will see.",
                        "<b>OFF</b> disables the check.",
                    ],
                ),
                "add": self._duplicate_name_options,
                # Applied after 'add' (which lands on index 0): Locators is the
                # scope the check shipped with as a plain checkbox.
                "setCurrentIndex": 1,
            },
            "check_mangled_names": {
                "widget_type": "QCheckBox",
                "group": "Hierarchy & Naming",
                "setText": "Check For Mangled Names",
                "setToolTip": TooltipFormat.fmt(
                    title="Check For Mangled Names",
                    body="Fails the export when any node in the set — shapes "
                    "included — carries a scratch or mangled name.",
                    bullets=[
                        "Accumulated '__uninst_tmp' scratch tokens",
                        "'__RZTMP' Rizom round-trip suffixes",
                        "'FBXASC###' import escapes",
                        "Runs of three or more underscores",
                    ],
                    notes=["Repair with the <b>Fix Mangled Names</b> task."],
                ),
                "setChecked": True,
            },
            "check_root_default_transforms": {
                "widget_type": "QCheckBox",
                "group": "Hierarchy & Naming",
                "setText": "Check Root Default Transforms",
                "setToolTip": TooltipFormat.fmt(
                    title="Check Root Default Transforms",
                    body="Fails the export when a root group node is not at "
                    "identity — translate and rotate (0, 0, 0), scale (1, 1, 1).",
                    notes=[
                        "A root that was frozen reads identity but still carries "
                        "the consumed transform in its history, which an un-freeze "
                        "downstream would reinstate. Those are reported for "
                        "information and do not fail the check — as the scene "
                        "stands it really is at identity, which is what the "
                        "exporter needs."
                    ],
                ),
                "setChecked": True,
            },
            "check_sheared_local_transforms": {
                "widget_type": "QCheckBox",
                "group": "Hierarchy & Naming",
                "setText": "Check For Sheared Local Transforms",
                "setToolTip": TooltipFormat.fmt(
                    title="Check For Sheared Local Transforms",
                    body="Fails the export when a node's local matrix is "
                    "sheared. FBX and glTF store animated nodes as "
                    "translate/rotate/scale, which cannot represent shear, so "
                    "it is silently dropped.",
                    notes=[
                        "Needs no authored shear: a squash/stretch joint chain "
                        "gives every joint the same non-uniform world scale, "
                        "and the LOCAL matrix between two differently-oriented "
                        "joints is then sheared. World matrices look clean.",
                        "The residual compounds down a chain. Measured on a "
                        "wire-loom rig: 47% stretch put the last joint 7.5 cm "
                        "off; 11% stayed within 0.5 cm.",
                        "The <b>Flatten Sheared Chains</b> task re-anchors "
                        "the flagged joints automatically; otherwise reduce "
                        "the stretch at the source.",
                    ],
                ),
                "setChecked": True,
            },
            "check_hierarchy_vs_existing_fbx": {
                "widget_type": "QCheckBox",
                "group": "Hierarchy & Naming",
                "setText": "Check Hierarchy vs Existing FBX",
                "setToolTip": TooltipFormat.fmt(
                    title="Check Hierarchy vs Existing FBX",
                    body="Fails the export when the hierarchy differs from the "
                    "previous export — nodes that went missing or appeared, the "
                    "signature of an accidental change.",
                    notes=[
                        "Compares against a lightweight sidecar manifest written "
                        "beside the last export, so no FBX reimport is needed.",
                        "Version the Output Filename with a trailing counter "
                        "(<b>*_v{n:03d}</b>) so the baseline carries across "
                        "versions.",
                    ],
                ),
                "setChecked": False,
            },
            "check_hidden_geometry": {
                "widget_type": "QCheckBox",
                "group": "Geometry",
                "setText": "Check For Hidden Geometry",
                "setToolTip": TooltipFormat.fmt(
                    title="Check For Hidden Geometry",
                    body="Fails the export when geometry in the set is hidden — "
                    "by its own visibility flag or by a display layer.",
                    notes=[
                        "The FBX exporter writes hidden geometry anyway, so this "
                        "check is the only warning you get before it ships.",
                        "Objects with animated visibility are deliberately not "
                        "flagged: the Visible scope includes them on purpose and "
                        "their animation ships with them.",
                    ],
                ),
                "setChecked": True,
            },
            "check_overlapping_duplicate_mesh": {
                "widget_type": "QCheckBox",
                "group": "Geometry",
                "setText": "Check For Overlapping Duplicates",
                "setToolTip": TooltipFormat.fmt(
                    title="Check For Overlapping Duplicates",
                    body="Fails the export when two meshes occupy the same space — "
                    "typically a duplicate left sitting on top of the original.",
                    notes=[
                        "Matches on world-space bounding box, topology counts, and "
                        "sampled world-space vertex positions, so same-size "
                        "different-shape meshes are not confused for each other."
                    ],
                ),
                "setChecked": True,
            },
            "check_uv_snapshots": {
                "widget_type": "QCheckBox",
                "group": "Geometry",
                "setText": "Check For Leftover UV Snapshots",
                "setToolTip": TooltipFormat.fmt(
                    title="Check For Leftover UV Snapshots",
                    body="Fails the export when a mesh in the set still carries a "
                    "<b>_uv_snap_*</b> backup UV set an interrupted Auto Unwrap "
                    "left behind.",
                    notes=[
                        "The FBX writes it as a real UV set, and as the second "
                        "one it becomes TEXCOORD_1, the channel lightmaps read.",
                        "Reported, never removed: the next Auto Unwrap of the "
                        "mesh sweeps it.",
                    ],
                ),
                "setChecked": True,
            },
            "check_objects_below_floor": {
                # A depth is a bounded number, so it gets a spin box (same
                # rationale as the size and path budgets): the value IS how far
                # geometry may reach below the floor, and 0 reads back as
                # "OFF". NOT the old checkbox's objectName: a template saved in
                # the checkbox era carries this check as a BOOL, and a spin box
                # restoring `true` would read it as a depth of 1.0 -- a limit
                # the user never chose. A fresh name makes such a template trip
                # the PresetManager's uncovered-keys warning instead, so the
                # user re-saves and the template is whole again. (The TASK key
                # stays check_objects_below_floor -- the check takes the depth,
                # and a headless caller's legacy True still means the default.)
                "widget_type": "SpinBox",
                "object_name": "floor_depth",
                "group": "Geometry",
                "set_row_label": "Max Depth Below Floor",
                "set_limits": [0, 1000, 0.1, 2],
                "setValue": 0.5,
                "setCustomDisplayValues": {0: "OFF"},
                "setToolTip": TooltipFormat.fmt(
                    title="Max Depth Below Floor",
                    body="Fails the export when geometry reaches deeper than this "
                    "below Y=0, in scene units.",
                    notes=[
                        "The default of 0.5 lets a shallow penetration (a tire "
                        "settling into the ground) pass on its own.",
                        "Set to 0 (OFF) to disable.",
                    ],
                ),
                "value_method": "value",
            },
            "check_default_materials": {
                "widget_type": "QCheckBox",
                "group": "Materials & Paths",
                "setText": "Check For Default Materials",
                "setToolTip": TooltipFormat.fmt(
                    title="Check For Default Materials",
                    body="Fails the export when a mesh in the export set is on "
                    "Maya's fallback shader (<b>initialShadingGroup</b> / "
                    "lambert1), or on no shading group at all.",
                    notes=[
                        "Such a mesh still exports: it arrives as "
                        "'Default_Material' — untextured, and with no normal "
                        "map — so it renders wrong only in the deliverable.",
                        "Reports per SHAPE, so a per-face assignment that "
                        "leaves part of a mesh on the default is named too.",
                        "Assign a material, or drop the object from the export "
                        "set, to pass.",
                    ],
                ),
                "setChecked": True,
            },
            "check_duplicate_materials": {
                "widget_type": "QCheckBox",
                "group": "Materials & Paths",
                "setText": "Check For Duplicate Materials",
                "setToolTip": TooltipFormat.fmt(
                    title="Check For Duplicate Materials",
                    body="Fails the export when two of the export materials are "
                    "verified duplicates of each other.",
                    notes=[
                        "Same texture set, placement, color space and texture "
                        "content — near-misses like same-name-different-content "
                        "are not reported.",
                        "The <b>Reassign Duplicate Materials</b> task merges "
                        "exactly what this reports.",
                    ],
                ),
                "setChecked": True,
            },
            "check_path_length": {
                # A character budget is a bounded number, so it gets a spin box
                # (same rationale as the texture size limit): the default is
                # THIS machine's OS limit, and 0 reads back as "OFF".
                "widget_type": "SpinBox",
                "group": "Materials & Paths",
                "set_row_label": "Max Path Length",
                "set_limits": [0, 32767, 1, 0],
                "setValue": ptk.FileUtils.path_length_limit(),
                "setCustomDisplayValues": {0: "OFF"},
                "setToolTip": TooltipFormat.fmt(
                    title="Max Path Length",
                    body="Fails the export when the destination, or any texture "
                    "feeding the export materials, resolves to a path longer than "
                    "this many characters.",
                    notes=[
                        "Over-long paths fail late and opaquely — a write that "
                        "reports success but produced nothing, or a texture the "
                        "FBX plug-in silently cannot embed.",
                        "A path that fits on this machine can still break on one "
                        "without long paths enabled (260 characters).",
                        "Sidecars written beside the export are longer than the "
                        "export path itself, so leave headroom.",
                        "Set to 0 (OFF) to disable.",
                    ],
                ),
                "value_method": "value",
            },
            "check_valid_paths": {
                "widget_type": "QCheckBox",
                "group": "Materials & Paths",
                "setText": "Check For Valid Paths",
                "setToolTip": TooltipFormat.fmt(
                    title="Check For Valid Paths",
                    body="Fails the export when a texture feeding the export "
                    "materials, a committed lightmap, or a scene reference does "
                    "not resolve on disk.",
                    notes=[
                        "Resolves each path twice: the way Maya resolves it, and "
                        "the way the FBX plug-in will locate it at write time.",
                        "Catches what would otherwise surface after the export as "
                        "'The following texture(s) will not be embedded'.",
                        "Lightmaps have no file node — the bake marker records "
                        "the folder it was committed from. A map that folder no "
                        "longer holds is looked for where the GLB conversion "
                        "looks (the project's texture folders, then all of "
                        "sourceimages); found elsewhere it ships and is noted, "
                        "found nowhere it fails the export.",
                        "Textures on objects that will not ship (the HDR skydome, "
                        "file nodes orphaned by the duplicate-material cleanup) "
                        "are not reported.",
                    ],
                ),
                "setChecked": True,
            },
            "check_texture_file_size": {
                # A megabyte budget is a bounded number, so it gets a spin box:
                # steppable, no free text to typo, and 0 reads back as "OFF"
                # (the check treats a falsy limit as disabled).
                "widget_type": "SpinBox",
                "group": "Materials & Paths",
                "set_row_label": "Max Size (MB)",
                "set_limits": [0, 4096, 1, 0],
                "setValue": 16,
                "setCustomDisplayValues": {0: "OFF"},
                "setToolTip": TooltipFormat.fmt(
                    title="Max Texture File Size (MB)",
                    body="Fails the export when any texture feeding the export "
                    "materials is larger than this on disk.",
                    notes=[
                        "Catches un-downsized authoring maps — an 8K master left "
                        "wired up — that would bloat the shipped asset.",
                        "Set to 0 (OFF) to disable.",
                    ],
                ),
                "value_method": "value",
            },
            "check_framerate": {
                "widget_type": "ComboBox",
                "group": "Animation",
                "set_row_label": "Framerate",
                "setToolTip": TooltipFormat.fmt(
                    title="Scene Framerate",
                    body="Fails the export when the scene's time unit is not the "
                    "framerate selected here.",
                    notes=[
                        "Skipped when the scene has no keyframes.",
                        "<b>OFF</b> disables the check.",
                    ],
                ),
                "add": self._frame_rate_options,
            },
            "check_untied_keyframes": {
                "widget_type": "QCheckBox",
                "group": "Animation",
                "setText": "Check For Untied Keyframes",
                "setToolTip": TooltipFormat.fmt(
                    title="Check For Untied Keyframes",
                    body="Fails the export when an object has an animated channel "
                    "whose keys stop short of that object's own keyed range.",
                    notes=[
                        "The <b>Tie All Keyframes</b> task inserts the missing "
                        "bookend keys.",
                        "Set-driven-key curves are ignored — their key 'times' are "
                        "driver values, not frames.",
                    ],
                ),
                "setChecked": True,
            },
            "check_floating_point_keys": {
                "widget_type": "QCheckBox",
                "group": "Animation",
                "setText": "Check For Floating Point Keys",
                "setToolTip": TooltipFormat.fmt(
                    title="Check For Floating Point Keys",
                    body="Fails the export when a key sits on a fractional frame.",
                    notes=[
                        "The <b>Snap Keys To Frame</b> task rounds them to whole "
                        "frames."
                    ],
                ),
                "setChecked": True,
            },
            # Not a pipeline check: the pop in ``SceneExporter.perform_export``
            # turns this row into the flag that arms the POST-write pass
            # (:meth:`verify_deliverables`), the same idiom the Texture/Animation
            # Output modes ride. It lives here because it is a check in the
            # user's sense -- and because "Override Checks" should switch it off
            # with the rest -- but it never reaches the task dispatcher, and it
            # is the one entry in this map with no ``check_`` method behind it.
            "verify_deliverables": {
                "widget_type": "QCheckBox",
                "group": "Deliverable (after the write)",
                "setText": "Verify The Written File",
                "setToolTip": TooltipFormat.fmt(
                    title="Verify The Written File",
                    body="Re-opens the FBX/GLB that just shipped and runs "
                    "pythontk's file-level gates over the bytes on disk — a "
                    "truncated container, a take the FBX dropped, a NaN that "
                    "reached an accessor, a clip whose span disagrees with its "
                    "take.",
                    notes=[
                        "Reports only. The file is already written, so a failure "
                        "is logged per gate at ERROR and never unwrites the "
                        "deliverable or flips the export's verdict.",
                        "Off by default because it is the one pass that costs "
                        "time proportional to the FBX rather than the scene "
                        "(seconds and hundreds of MB of heap on a large file); "
                        "arm it for a delivery, not for every iteration.",
                        "Reads the FBX and the GLB independently, so a GLB-only "
                        "export never parses the temp FBX it is about to "
                        "discard.",
                    ],
                ),
                "setChecked": False,
            },
        }

    @property
    def definitions(self) -> Dict[str, Dict[str, Any]]:
        """Return all definitions combined for backward compatibility."""
        return {**self.task_definitions, **self.check_definitions}
