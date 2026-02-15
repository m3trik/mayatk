# !/usr/bin/python
# coding=utf-8
import os
from typing import Dict, List, Optional
import pythontk as ptk

try:
    import pymel.core as pm
    import maya.cmds as cmds
except ImportError:
    pass
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
    def get_stingray_mats(cls, objects: Optional[List] = None) -> List:
        """Return unique StingrayPBS materials assigned to *objects*."""
        return MatUtils.get_mats(objects, mat_type="StingrayPBS")

    @classmethod
    def ensure_transparent_graph(cls, mat) -> bool:
        """Load Standard_Transparent.sfx onto a StingrayPBS node if needed."""
        if mat.hasAttr("use_opacity_map"):
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
            cmds.shaderfx(sfxnode=mat.name(), loadGraph=graph)
            cls.logger.info(f"Loaded Standard_Transparent.sfx onto {mat.name()}")
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
        objects = pm.ls(objects)
        if not objects:
            cls.logger.warning("No objects selected.")
            return {}

        results = {}
        mat_map = {}

        # 1. Group objects by material
        for obj in objects:
            try:
                mats = MatUtils.get_mats([obj], mat_type="StingrayPBS")
                for m in mats:
                    mat_map.setdefault(m, []).append(obj)
            except Exception:
                continue

        if not mat_map:
            cls.logger.warning("No StingrayPBS materials found on selection.")
            return {}

        # 2. Process each material
        for mat, targets in mat_map.items():
            # Snapshot texture paths and scalar values BEFORE graph swap
            # (loadGraph destroys all external connections and resets attributes).
            original_mat_name = mat.name()
            snapshot = MatSnapshot.capture(original_mat_name, objects=targets)

            final_mat = mat

            # Check for external sharing
            sgs = mat.listConnections(type="shadingEngine")
            if sgs:
                sg = sgs[0]
                members = sg.members(flatten=True)

                # Robustly identify transforms (handle shapes, faces, etc.)
                member_transforms = set()
                for m in members:
                    node = m.node() if hasattr(m, "node") else m
                    # If it's a shape, get parent transform
                    if isinstance(node, pm.nt.Shape):
                        node = node.getParent()
                    member_transforms.add(node)

                target_transforms = set(pm.ls(targets))  # Ensure PyNodes

                # If the material is used by objects NOT in our target list, duplicate it.
                if not member_transforms.issubset(target_transforms):

                    # Prevent recursive naming (Mat_Fade_Fade)
                    base_name = mat.name()
                    if base_name.endswith(cls.FADE_SUFFIX):
                        # Use existing name logic or strip?
                        # If we strip, we might clash with original if it exists.
                        # Ideally we just want a unique name that ends in ONE suffix.
                        pass

                    new_mat_name = f"{base_name}{cls.FADE_SUFFIX}"
                    # If it already ends with suffix, don't append another one mechanically
                    # UNLESS we are splitting a split.
                    # But usually, if we are splitting a split, maybe we just want unique auto-increment by Maya?
                    # pm.duplicate handles uniqueness.
                    # We just shouldn't add the suffix if it's already there?
                    if base_name.endswith(cls.FADE_SUFFIX):
                        new_mat_name = (
                            base_name  # Duplicate will auto-increment (Mat_Fade1)
                        )

                    final_mat = pm.duplicate(mat, name=new_mat_name)[0]
                    new_sg = pm.sets(
                        renderable=True,
                        noSurfaceShader=True,
                        empty=True,
                        name=f"{sg.name()}_Copy",
                    )
                    pm.connectAttr(final_mat.outColor, new_sg.surfaceShader)

                    # Assign to our targets
                    pm.sets(new_sg, forceElement=targets)
                    cls.logger.info(
                        f"Duplicated {mat.name()} -> {final_mat.name()} to isolate selection."
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
                    final_mat.name(), snapshot, source_mat_name=original_mat_name
                )
                cls.logger.info(f"Enabled transparency graph on: {final_mat.name()}")

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
                        base_name = final_mat.name()
                        # Avoid _Fade_Fade buildup
                        if base_name.endswith(cls.FADE_SUFFIX):
                            new_name = base_name  # Auto-increment
                        else:
                            new_name = f"{base_name}{cls.FADE_SUFFIX}"

                        my_mat = pm.duplicate(final_mat, name=new_name)[0]
                        orig_sg = final_mat.listConnections(type="shadingEngine")[0]
                        new_sg = pm.sets(
                            renderable=True,
                            noSurfaceShader=True,
                            empty=True,
                            name=f"{orig_sg.name()}_Copy",
                        )
                        pm.connectAttr(my_mat.outColor, new_sg.surfaceShader)
                        pm.sets(new_sg, forceElement=target_obj)
                        # Restore textures on the duplicate (pm.duplicate drops connections)
                        MatSnapshot.restore(
                            my_mat.name(), snapshot, source_mat_name=original_mat_name
                        )
                else:
                    my_mat = final_mat

                # 3c. Connect Transform.opacity -> Material.opacity
                if my_mat.hasAttr("opacity"):
                    # Break existing input if any (e.g. static value or old connection)
                    # If connected from another object, we are breaking it (which is good, we are taking over)
                    inputs = my_mat.opacity.inputs(plugs=True)
                    if inputs:
                        pm.disconnectAttr(inputs[0], my_mat.opacity)

                    pm.connectAttr(
                        target_obj.attr("opacity"), my_mat.opacity, force=True
                    )
                    cls.logger.info(
                        f"Connected {target_obj}.opacity -> {my_mat}.opacity"
                    )

                # 3d. Ensure 'use_opacity_map' implies usage of opacity value
                if my_mat.hasAttr("use_opacity_map"):
                    # Often these graphs use a toggle. Ensure it's active.
                    # We don't connect this, just set it.
                    pass

            status = "configured"
            results[final_mat.name()] = {"status": "configured"}

        # Select the modified materials so the Channel Box displays their attributes
        configured_mats = [
            name for name, res in results.items() if res.get("status") == "configured"
        ]
        if configured_mats:
            try:
                # Select the materials to show attributes
                pm.select(configured_mats)
            except Exception:
                pass

        # Force Channel Box refresh if UI is active
        if not pm.about(batch=True):
            try:
                cmds.channelBox("mainChannelBox", edit=True, update=True)
            except Exception:
                pass

        return results

    @classmethod
    def _expose_attributes(cls, mat):
        """Ensure standard opacity attributes are keyable."""
        for attr_name in ["opacity", "use_opacity_map"]:
            if mat.hasAttr(attr_name):
                try:
                    attr = mat.attr(attr_name)
                    # Force unlock if locked (ShaderFX sometimes locks inputs)
                    if attr.isLocked():
                        attr.unlock()

                    # Ensure it is keyable for the artist
                    attr.setKeyable(True)
                except Exception as e:
                    cls.logger.warning(
                        f"Failed to expose attribute '{attr_name}' on {mat}: {e}"
                    )

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

        for obj in pm.ls(objects):
            mats = cls.get_stingray_mats([obj])

            for mat in mats:
                # 1. Disconnect proxy: Transform.opacity → Material.opacity
                if obj.hasAttr(OpacityAttributeMode.ATTR_NAME) and mat.hasAttr(
                    "opacity"
                ):
                    if pm.isConnected(
                        obj.attr(OpacityAttributeMode.ATTR_NAME), mat.opacity
                    ):
                        pm.disconnectAttr(
                            obj.attr(OpacityAttributeMode.ATTR_NAME), mat.opacity
                        )
                        try:
                            if not mat.opacity.isLocked():
                                mat.opacity.set(1.0)
                        except Exception:
                            pass
                        cls.logger.info(f"Disconnected {obj}.opacity -> {mat}.opacity")

                # 2. If _Fade duplicate, reassign to original & clean up
                mat_name = mat.name()
                match = re.match(
                    r"^(.+?)" + re.escape(cls.FADE_SUFFIX) + r"\d*$",
                    mat_name,
                )
                if match:
                    original_name = match.group(1)
                    if pm.objExists(original_name):
                        orig_mat = pm.PyNode(original_name)
                        orig_sgs = orig_mat.listConnections(type="shadingEngine")
                        if orig_sgs:
                            pm.sets(orig_sgs[0], forceElement=obj)
                            cls.logger.info(
                                f"Reassigned {obj} to original material '{original_name}'"
                            )

                    # Delete orphaned _Fade SG + material
                    fade_sgs = mat.listConnections(type="shadingEngine")
                    for sg in fade_sgs:
                        if not sg.members(flatten=True):
                            pm.delete(sg)
                            cls.logger.info(f"Deleted orphaned SG: {sg}")
                    if not mat.listConnections(type="shadingEngine"):
                        pm.delete(mat)
                        cls.logger.info(f"Deleted orphaned material: {mat_name}")
