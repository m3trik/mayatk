# !/usr/bin/python
# coding=utf-8
"""Scene-inspection ops.

All read-only for now. Mutating ops are *safe to add* -- every op runs
through :func:`run_on_main_thread` in :mod:`..server`, so even an
``mset.importModel`` call will be marshalled onto Toolbag's Qt main
thread before it touches the scene.
"""
from ..registry import register


@register("scene.summary")
def summary():
    """High-level snapshot of the current Toolbag scene.

    Returns counts + names for objects/materials and the active scene
    file path -- enough for a client to confirm "this is the scene I
    expected" before firing a mutating op.
    """
    import mset  # noqa: PLC0415 -- lazy import keeps op modules portable.

    materials = [m.name for m in mset.getAllMaterials() or []]
    objects = []
    for o in mset.getAllObjects() or []:
        objects.append({"name": getattr(o, "name", ""),
                        "type": type(o).__name__})
    return {
        "scene_path": mset.getScenePath() or "",
        "toolbag_version": mset.getToolbagVersion(),
        "object_count": len(objects),
        "material_count": len(materials),
        "materials": materials,
        "objects": objects,
    }


@register("scene.list_materials")
def list_materials():
    """Material names in the current scene."""
    import mset  # noqa: PLC0415
    return [m.name for m in mset.getAllMaterials() or []]
