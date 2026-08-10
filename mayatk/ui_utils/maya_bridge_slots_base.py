# !/usr/bin/python
# coding=utf-8
"""Maya-flavored :class:`BridgeSlotsBase` -- adds Maya-side defaults.

The DCC-agnostic base lives upstream in :mod:`uitk.bridge.slots`
(re-exported through :mod:`uitk.bridge`). This thin subclass injects
the one piece every Maya bridge needs: a sensible Output Dir fallback
sourced from :class:`mayatk.env_utils.EnvUtils` (scene dir, then
workspace) when the user leaves the field blank.

The Marmoset, Substance, Rizom, Blender, and Unity bridge slots all
subclass this instead of inheriting from ``BridgeSlotsBase`` directly,
so the fallback lives in one place (Unity opts back out by overriding
``default_output_dir`` to return ``""`` — a Maya scene dir isn't a
Unity project).
"""
from __future__ import annotations

from uitk.bridge import BridgeSlotsBase

from mayatk.env_utils._env_utils import EnvUtils


class MayaBridgeSlotsBase(BridgeSlotsBase):
    """Adds a Maya-flavored ``default_output_dir`` + Scope resolution to
    :class:`BridgeSlotsBase`."""

    def default_output_dir(self) -> str:
        """Scene-dir then workspace fallback for an empty Output Dir field."""
        return EnvUtils.default_artifact_dir()

    # ------------------------------------------------------------------ scope
    def resolve_scope_objects(self, scope: str):
        """Objects to export for the chosen ``SCOPE`` param.

        Mesh shapes/transforms are returned as-is; the engines'
        :class:`mayatk.env_utils.handoff_export.MayaExportMixin` coerces them to
        transform nodes. ``"selected"`` is the default AND the fallback for any
        unknown value -- an unrecognised scope must never silently widen a send
        to the whole scene.

        Lives on the shared base so every Maya bridge (Blender / Unity /
        Marmoset / Substance / Rizom) resolves scope identically; the spec that
        drives it is :meth:`uitk.bridge.Parameters.scope_spec`, shared with
        blendertk's mirror (``BlenderBridgeSlotsBase.resolve_scope_objects``).
        """
        import maya.cmds as cmds

        if scope == "all":
            # Prefer the bridge's whole-scene hook (``MayaExportMixin
            # ._scene_objects``: DAG roots minus startup cameras) -- the same
            # set ``save_as`` ships. A mesh-only query here silently flattens
            # the scene graph on the far side: group/locator transforms never
            # reach the exporter, so every child re-roots (live report on the
            # Blender mirror). Bridges without the hook (RPC bakers) keep the
            # renderable-geometry set -- for them "the scene" IS its meshes.
            # getattr, not self.bridge: the fallback below is documented for slots
            # whose bridge has no whole-scene hook, and a panel that has not built
            # its bridge yet has no ``.bridge`` AT ALL -- reaching for it directly
            # turns that case into an AttributeError instead of the fallback.
            bridge = getattr(self, "bridge", None)
            scene = bridge._scene_objects() if bridge is not None else None
            if scene is not None:
                return scene
            return cmds.ls(type="mesh", noIntermediate=True, long=True) or []
        if scope == "visible":
            from mayatk.display_utils._display_utils import DisplayUtils

            # inherit_parent_visibility=True is what actually walks the
            # transform chain and drops hidden geometry (without it the helper
            # returns every renderable shape regardless of visibility).
            shapes = (
                DisplayUtils.get_visible_geometry(
                    shapes=True, inherit_parent_visibility=True
                )
                or []
            )
            # Expand each shape to ALL its parent paths, not the first: an
            # instanced shape is one node worn by many transforms, and the
            # engines' shape->transform coercion keeps only the first parent --
            # which would silently drop every instance sibling from the export
            # set (the same trap as NodeUtils.list_transforms' shape dedup).
            # Each path is visibility-checked on its own: one sibling being
            # visible must not smuggle a hidden one into the set.
            def _path_visible(path: str) -> bool:
                node = str(path)
                while node and node != "|":
                    try:
                        if not cmds.getAttr(f"{node}.visibility"):
                            return False
                    except Exception:  # noqa: BLE001 -- no visibility attr
                        pass
                    node = node.rsplit("|", 1)[0]
                return True

            out: list = []
            for shape in shapes:
                for parent in (
                    cmds.listRelatives(str(shape), allParents=True, fullPath=True)
                    or [str(shape)]
                ):
                    if parent not in out and _path_visible(parent):
                        out.append(parent)
            return out
        return cmds.ls(selection=True, long=True) or []
