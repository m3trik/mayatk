# !/usr/bin/python
# coding=utf-8
"""Transform diagnostics and repair helpers."""
from __future__ import annotations

from typing import List, Optional, Sequence, Union

try:
    import maya.cmds as cmds
except ImportError as error:  # pragma: no cover - Maya runtime specific
    print(__file__, error)

from mayatk.xform_utils._xform_utils import XformUtils
from mayatk.node_utils._node_utils import NodeUtils

# Type aliases keep Maya stubs optional during static analysis
NodeLike = Union[str, object]
NodeSeq = Union[NodeLike, Sequence[NodeLike]]


class TransformDiagnostics:
    """Operations for inspecting and fixing common transform issues."""

    # Shear components at or below this magnitude are treated as orthogonal.
    SHEAR_TOLERANCE = 1e-6

    @classmethod
    def get_sheared(
        cls, objects: Optional[NodeSeq] = None, tolerance: Optional[float] = None
    ) -> List[str]:
        """Return the transforms whose axes are non-orthogonal (sheared).

        Parameters:
            objects: Transforms (or nodes resolvable to transforms) to check.
                None uses the current selection.
            tolerance: Max abs shear component treated as zero. Defaults to
                :attr:`SHEAR_TOLERANCE`.

        Returns:
            list[str]: Transforms with shear above the tolerance.
        """
        if objects is None:
            objects = cmds.ls(selection=True) or []
        tolerance = cls.SHEAR_TOLERANCE if tolerance is None else tolerance

        sheared: List[str] = []
        for obj in cmds.ls(objects, transforms=True) or []:
            shear = cmds.xform(obj, query=True, shear=True)  # [xy, xz, yz]
            if any(abs(s) > tolerance for s in shear):
                sheared.append(obj)
        return sheared

    @classmethod
    def fix_non_orthogonal_axes(
        cls,
        objects: Optional[NodeSeq] = None,
        dry_run: bool = False,
        tolerance: Optional[float] = None,
        quiet: bool = False,
    ) -> List[str]:
        """Fix non-orthogonal axes (shear) on the given objects by freezing
        their transforms. Non-orthogonal axes cause issues with FBX export.

        Instanced objects are uninstanced first (freezing an instanced shape
        would corrupt its siblings) via ``NodeUtils.uninstance``, which swaps
        in a unique shape while preserving the transform in place.

        Parameters:
            objects: Transforms to process. None uses the current selection.
            dry_run: Report (and return) what would be fixed without making changes.
            tolerance: Max abs shear component treated as zero. Defaults to
                :attr:`SHEAR_TOLERANCE`.
            quiet: Suppress console output.

        Returns:
            list[str]: The transforms fixed (or, on ``dry_run``, the transforms
            that would be fixed).
        """
        tolerance = cls.SHEAR_TOLERANCE if tolerance is None else tolerance
        sheared = cls.get_sheared(objects, tolerance)

        if dry_run:
            if not quiet:
                for obj in sheared:
                    shear = cmds.xform(obj, query=True, shear=True)
                    print(f"Dry run: would fix {obj} (Shear: {shear})")
                print("Dry run complete.")
            return sheared

        fixed: List[str] = []
        for obj in sheared:
            if not quiet:
                shear = cmds.xform(obj, query=True, shear=True)
                print(f"Fixing non-orthogonal axes on {obj} (Shear: {shear})")
            try:
                if NodeUtils.get_instances(obj):
                    if not quiet:
                        print(f"Object {obj} is an instance. Uninstancing before freezing.")
                    obj = (NodeUtils.uninstance(obj) or [obj])[0]

                # Freezing transforms (especially rotation and scale) bakes the
                # shear. connection_strategy='disconnect' force-breaks
                # connections that would otherwise prevent freezing.
                XformUtils.freeze_transforms(
                    obj, t=1, r=1, s=1, connection_strategy="disconnect", force=True
                )

                if cls.get_sheared([obj], tolerance):
                    # freeze_transforms left residual shear; bake it directly.
                    if not quiet:
                        remaining = cmds.xform(obj, query=True, shear=True)
                        print(
                            f"Warning: freeze_transforms failed to fix shear on {obj} "
                            f"(remaining: {remaining}). Attempting direct makeIdentity..."
                        )
                    cmds.makeIdentity(obj, apply=True, t=1, r=1, s=1, n=0, pn=1)

                if cls.get_sheared([obj], tolerance):
                    if not quiet:
                        cmds.warning(f"Unable to remove shear on {obj}.")
                else:
                    fixed.append(obj)
            except Exception as e:
                if not quiet:
                    cmds.warning(f"Failed to fix {obj}: {e}")

        if not quiet:
            if fixed:
                print(f"Fixed non-orthogonal axes on {len(fixed)} objects.")
            elif not sheared:
                print("No objects with non-orthogonal axes found.")
        return fixed
