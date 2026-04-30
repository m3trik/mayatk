# !/usr/bin/python
# coding=utf-8
import os
from typing import Dict, Optional
import pythontk as ptk

try:
    import maya.cmds as cmds
except ImportError:
    cmds = None
# From this package:
from mayatk.mat_utils._mat_utils import MatUtils
from mayatk.mat_utils.mat_snapshot import MatSnapshot
from mayatk.env_utils._env_utils import EnvUtils
from mayatk.mat_utils.render_opacity.attribute_mode import OpacityAttributeMode


class OpacityMaterialMode(ptk.LoggingMixin):
    """
    Implements the 'material' mode for RenderOpacity.

    This mode acts as a "Visual Attribute Mode":
    1. Adds the standard 'opacity' attribute to each object (via AttributeMode).
    2. Ensures each object has a unique material (to allow independent fading).
    3. Connects Object.opacity -> Material.opacity for viewport feedback.
    """

    FADE_ATTRS = ("base_colorR", "base_colorG", "base_colorB", "opacity")
    """StingrayPBS attributes keyframed in ``"material"`` mode."""

    FADE_SUFFIX = "_Fade"
    """Suffix appended to material names in ``"material"`` mode."""

    @classmethod
    def get_stingray_mats(cls, objects: Optional[list] = None) -> list:
        """Return unique StingrayPBS materials assigned to *objects*."""
        return MatUtils.get_mats(objects, mat_type="StingrayPBS", as_strings=True)

    @classmethod
    def ensure_transparent_graph(cls, mat) -> bool:
        """Load Standard_Transparent.sfx onto a StingrayPBS node if needed."""
        if cmds.attributeQuery("use_opacity_map", node=mat, exists=True):
            return True

        EnvUtils.load_plugin("shaderFXPlugin")
        maya_path = EnvUtils.get_env_info("install_path")
        graph = os.path.join(
            maya_path,
            "presets",
            "ShaderFX",
            "Scenes",
            "StingrayPBS",
            "Standard_Transparent.sfx",
        )
        if os.path.exists(graph):
            cmds.shaderfx(sfxnode=mat.split('|')[-1].split(':')[-1], loadGraph=graph)
            cls.logger.info(f"Loaded Standard_Transparent.sfx onto {mat.split('|')[-1].split(':')[-1]}")
            return True
        else:
            cls.logger.warning(f"Transparent graph not found: {graph}")
            return False

    @classmethod
    def create(
        cls,
        objects,
    ) -> Dict[str, Dict]:
        """
        Expose StingrayPBS transparency (load graph).
        Automatically handles material duplication if shared by unselected objects.
        """
        objects = cmds.ls(objects)
        if not objects:
            cls.logger.warning("No objects selected.")
            return {}

        results = {}
        mat_map = {}

        # 1. Group objects by material
        for obj in objects:
            try:
                mats = MatUtils.get_mats([obj], mat_type="StingrayPBS", as_strings=True)
                for m in mats:
                    mat_map.setdefault(m, []).append(str(obj))
            except Exception:
                continue

        if not mat_map:
            cls.logger.warning("No StingrayPBS materials found on selection.")
            return {}

        # 2. Process each material
        for mat, targets in mat_map.items():
            # Snapshot texture paths and scalar values BEFORE graph swap
            # (loadGraph destroys all external connections and resets attributes).
            original_mat_name = mat.split('|')[-1].split(':')[-1]
            snapshot = MatSnapshot.capture(original_mat_name, objects=targets)

            final_mat = mat

            # Check for external sharing
            sgs = cmds.listConnections(mat, type="shadingEngine")
            if sgs:
                sg = sgs[0]
                members = cmds.sets(sg, q=True) or []
                # Flatten components/multi-attrs to plain strings
                members = cmds.ls(members, flatten=True) or []

                # Robustly identify transforms (handle shapes, faces, etc.)
                member_transforms = set()
                for m in members:
                    # Strip component suffix (.f[0], .vtx[3], …) to get the node
                    node = str(m).split(".")[0]
                    # ``cmds.objectType(node) == 'shape'`` only matches the
                    # generic 'shape' type — concrete shapes return 'mesh',
                    # 'nurbsSurface', etc.  Use ``isAType='shape'`` for
                    # the inheritance-aware check.
                    if cmds.objectType(node, isAType='shape'):
                        node = (cmds.listRelatives(node, parent=True, fullPath=True) or [None])[0]
                    if node:
                        # Normalize to long path for set comparison.
                        long_paths = cmds.ls(node, long=True) or [node]
                        member_transforms.add(long_paths[0])

                target_transforms = set(cmds.ls(targets, long=True) or [])

                # If the material is used by objects NOT in our target list, duplicate it.
                if not member_transforms.issubset(target_transforms):

                    # Prevent recursive naming (Mat_Fade_Fade)
                    base_name = mat.split('|')[-1].split(':')[-1]
                    if base_name.endswith(cls.FADE_SUFFIX):
                        # Use existing name logic or strip?
                        # If we strip, we might clash with original if it exists.
                        # Ideally we just want a unique name that ends in ONE suffix.
                        pass

                    new_mat_name = f"{base_name}{cls.FADE_SUFFIX}"
                    # If it already ends with suffix, don't append another one mechanically
                    # UNLESS we are splitting a split.
                    # But usually, if we are splitting a split, maybe we just want unique auto-increment by Maya?
                    # cmds.duplicate handles uniqueness.
                    # We just shouldn't add the suffix if it's already there?
                    if base_name.endswith(cls.FADE_SUFFIX):
                        new_mat_name = (
                            base_name  # Duplicate will auto-increment (Mat_Fade1)
                        )

                    final_mat = cmds.duplicate(mat, name=new_mat_name)[0]
                    new_sg = cmds.sets(
                        renderable=True,
                        noSurfaceShader=True,
                        empty=True,
                        name=f"{sg.split('|')[-1].split(':')[-1]}_Copy",
                    )
                    cmds.connectAttr(f"{final_mat}.outColor", f"{new_sg}.surfaceShader")

                    # Assign to our targets. ``cmds.sets(forceElement=...)``
                    # expects members as positional args, not a list literal.
                    for t in targets:
                        cmds.sets(t, edit=True, forceElement=new_sg)
                    cls.logger.info(
                        f"Duplicated {mat.split('|')[-1].split(':')[-1]} -> {final_mat.split('|')[-1].split(':')[-1]} to isolate selection."
                    )
                else:
                    # Material is effectively isolated to our selection (or subset of selection).
                    # No need to duplicate.
                    pass

            # Vital Step: Ensure the graph is loaded on the base material BEFORE we potentially duplicate it further.
            # This ensures duplicates work correctly as transparent materials without needing independent reloading.
            if cls.ensure_transparent_graph(final_mat):
                cls._expose_attributes(final_mat)
                # Restore textures and scalar values lost by loadGraph / duplicate.
                MatSnapshot.restore(
                    final_mat.split('|')[-1].split(':')[-1], snapshot, source_mat_name=original_mat_name
                )
                cls.logger.info(f"Enabled transparency graph on: {final_mat.split('|')[-1].split(':')[-1]}")

            # 3. Establish Proxy Connections (Transform.opacity -> Material.opacity)
            # This requires 1-to-1 mapping if we want independent control.

            current_mat = final_mat
            for i, target_obj in enumerate(targets):
                # 3a. Ensure Attribute Exists on Transform
                OpacityAttributeMode.create([target_obj])

                # 3b. Ensure Unique Material for this object (if part of a group > 1)
                if len(targets) > 1:
                    if i == 0:
                        # First one keeps the 'final_mat' we prepared
                        my_mat = final_mat
                    else:
                        # Others get a fresh duplicate
                        # Duplicate material and SG
                        base_name = final_mat.split('|')[-1].split(':')[-1]
                        # Avoid _Fade_Fade buildup
                        if base_name.endswith(cls.FADE_SUFFIX):
                            new_name = base_name  # Auto-increment
                        else:
                            new_name = f"{base_name}{cls.FADE_SUFFIX}"

                        my_mat = cmds.duplicate(final_mat, name=new_name)[0]
                        orig_sg = cmds.listConnections(final_mat, type="shadingEngine")[0]
                        new_sg = cmds.sets(
                            renderable=True,
                            noSurfaceShader=True,
                            empty=True,
                            name=f"{orig_sg.split('|')[-1].split(':')[-1]}_Copy",
                        )
                        cmds.connectAttr(f"{my_mat}.outColor", f"{new_sg}.surfaceShader")
                        cmds.sets(target_obj, edit=True, forceElement=new_sg)
                        # Restore textures on the duplicate (cmds.duplicate drops connections)
                        MatSnapshot.restore(
                            my_mat.split('|')[-1].split(':')[-1], snapshot, source_mat_name=original_mat_name
                        )
                else:
                    my_mat = final_mat

                # 3c. Connect Transform.opacity -> Material.opacity
                if cmds.attributeQuery("opacity", node=my_mat, exists=True):
                    # Break existing input if any (e.g. static value or old connection)
                    inputs = cmds.listConnections(
                        f"{my_mat}.opacity",
                        source=True,
                        destination=False,
                        plugs=True,
                    ) or []
                    if inputs:
                        cmds.disconnectAttr(inputs[0], f"{my_mat}.opacity")

                    cmds.connectAttr(
                        f"{target_obj}.opacity", f"{my_mat}.opacity", force=True
                    )
                    cls.logger.info(
                        f"Connected {target_obj}.opacity -> {my_mat}.opacity"
                    )

                # 3d. Ensure 'use_opacity_map' toggle is active so the
                #     opacity value actually takes effect in the viewport.
                if cmds.attributeQuery("use_opacity_map", node=my_mat, exists=True):
                    try:
                        plug = f"{my_mat}.use_opacity_map"
                        if not cmds.getAttr(plug, lock=True):
                            cmds.setAttr(plug, 1.0)
                    except Exception:
                        pass

            results[final_mat.split('|')[-1].split(':')[-1]] = {"status": "configured"}

        # Force Channel Box refresh if UI is active (without
        # changing the user's selection — that is a UI concern).
        if not cmds.about(batch=True):
            try:
                cmds.channelBox("mainChannelBox", edit=True, update=True)
            except Exception:
                pass

        return results

    @classmethod
    def _expose_attributes(cls, mat):
        """Ensure standard opacity attributes are keyable."""
        for attr_name in ["opacity", "use_opacity_map"]:
            if cmds.attributeQuery(attr_name, node=mat, exists=True):
                try:
                    plug = f"{mat}.{attr_name}"
                    # Force unlock if locked (ShaderFX sometimes locks inputs)
                    if cmds.getAttr(plug, lock=True):
                        cmds.setAttr(plug, lock=False)
                    # Ensure it is keyable for the artist
                    cmds.setAttr(plug, keyable=True)
                except Exception as e:
                    cls.logger.warning(
                        f"Failed to expose attribute '{attr_name}' on {mat}: {e}"
                    )

    @classmethod
    def ensure_connections(cls, objects) -> None:
        """Re-establish ``Transform.opacity → Material.opacity`` proxy
        connections that were lost (e.g. after a duplicate operation).

        Only attempts reconnection when the object has the ``opacity``
        attribute and is assigned a StingrayPBS material that also
        exposes ``opacity``.
        """
        for obj in cmds.ls(objects):
            if not cmds.attributeQuery(OpacityAttributeMode.ATTR_NAME, node=obj, exists=True):
                continue

            mats = cls.get_stingray_mats([obj])
            for mat in mats:
                if not cmds.attributeQuery("opacity", node=mat, exists=True):
                    continue
                # Already connected from this transform → skip
                if cmds.isConnected(
                    f"{obj}.{OpacityAttributeMode.ATTR_NAME}", mat.opacity
                ):
                    continue
                # If the material opacity is already driven by another
                # object, skip to avoid stealing the connection.
                existing = mat.opacity.inputs(plugs=True)
                if existing:
                    continue
                cmds.connectAttr(
                    f"{obj}.{OpacityAttributeMode.ATTR_NAME}",
                    mat.opacity,
                    force=True,
                )
                cls.logger.info(f"Reconnected {obj}.opacity -> {mat}.opacity")

    @classmethod
    def remove(cls, objects):
        """Remove material-mode artifacts from *objects*.

        - Disconnects ``Transform.opacity`` → ``Material.opacity`` proxy.
        - Reassigns objects from ``_Fade`` duplicates back to originals.
        - Deletes orphaned ``_Fade`` materials and their shading groups.

        .. note:: The Standard_Transparent graph is **not** reverted.
           Reverting a ShaderFX graph risks data-loss and shader instability.
        """
        import re

        for obj in cmds.ls(objects):
            obj = str(obj)
            mats = cls.get_stingray_mats([obj])
            mats = [str(m) for m in mats]

            for mat in mats:
                opacity_plug = f"{mat}.opacity"
                # 1. Disconnect proxy: Transform.opacity → Material.opacity
                if cmds.attributeQuery(
                    OpacityAttributeMode.ATTR_NAME, node=obj, exists=True
                ) and cmds.attributeQuery("opacity", node=mat, exists=True):
                    src_plug = f"{obj}.{OpacityAttributeMode.ATTR_NAME}"
                    if cmds.isConnected(src_plug, opacity_plug):
                        cmds.disconnectAttr(src_plug, opacity_plug)
                        try:
                            if not cmds.getAttr(opacity_plug, lock=True):
                                cmds.setAttr(opacity_plug, 1.0)
                        except Exception:
                            pass
                        cls.logger.info(f"Disconnected {obj}.opacity -> {mat}.opacity")

                # 2. If _Fade duplicate, reassign to original & clean up
                mat_name = mat.split('|')[-1].split(':')[-1]
                match = re.match(
                    r"^(.+?)" + re.escape(cls.FADE_SUFFIX) + r"\d*$",
                    mat_name,
                )
                if match:
                    original_name = match.group(1)
                    if cmds.objExists(original_name):
                        orig_mat = original_name
                        orig_sgs = cmds.listConnections(orig_mat, type="shadingEngine") or []
                        if orig_sgs:
                            cmds.sets(obj, edit=True, forceElement=orig_sgs[0])
                            cls.logger.info(
                                f"Reassigned {obj} to original material '{original_name}'"
                            )

                    # Delete orphaned _Fade SG + material
                    fade_sgs = cmds.listConnections(mat, type="shadingEngine") or []
                    for sg in fade_sgs:
                        if not (cmds.sets(sg, q=True) or []):
                            cmds.delete(sg)
                            cls.logger.info(f"Deleted orphaned SG: {sg}")
                    if not cmds.listConnections(mat, type="shadingEngine"):
                        cmds.delete(mat)
                        cls.logger.info(f"Deleted orphaned material: {mat_name}")
