# !/usr/bin/python
# coding=utf-8
from __future__ import annotations
import logging

try:
    import pymel.core as pm
except ImportError:
    pass

from mayatk.xform_utils._xform_utils import XformUtils
from mayatk.node_utils._node_utils import NodeUtils


class TransformDiagnostics:
    @staticmethod
    def fix_non_orthogonal_axes(objects=None, dry_run=False):
        """
        Fixes non-orthogonal axes on the given objects by freezing their transforms.
        Non-orthogonal axes (shear) cause issues with FBX export.

        Args:
            objects: List of objects to process. If None, uses selection.
            dry_run: If True, only reports what would be fixed without making changes.
        """
        if objects is None:
            objects = pm.selected()

        objects = pm.ls(objects, transforms=True)

        fixed_objects = []
        for obj in objects:
            # Check for shear
            shear = pm.xform(obj, q=True, shear=True)
            # shear returns [xy, xz, yz]
            if any(abs(s) > 1e-6 for s in shear):
                print(f"Fixing non-orthogonal axes on {obj} (Shear: {shear})")

                if dry_run:
                    print(f"Dry run: Would fix {obj}")
                    continue

                try:
                    # Check if it's an instance using the same logic as XformUtils
                    if NodeUtils.get_instances(obj):
                        print(
                            f"Object {obj} is an instance. Uninstancing before freezing."
                        )
                        # Uninstance by duplicating and replacing
                        orig_name = obj.name()
                        parent = obj.getParent()
                        dup = pm.duplicate(obj)[0]
                        # Move dup to same parent
                        if parent:
                            pm.parent(dup, parent)
                        # Delete original
                        pm.delete(obj)
                        # Rename dup
                        dup.rename(orig_name)
                        obj = dup
                        print(f"Uninstanced to {obj}")

                    # Freezing transforms (especially rotation and scale) bakes the shear
                    # Use connection_strategy='disconnect' to force break connections that prevent freezing
                    XformUtils.freeze_transforms(
                        obj, t=1, r=1, s=1, connection_strategy="disconnect", force=True
                    )

                    # Verify if shear is gone
                    new_shear = pm.xform(obj, q=True, shear=True)
                    if any(abs(s) > 1e-6 for s in new_shear):
                        print(
                            f"Warning: freeze_transforms failed to fix shear on {obj}. Remaining: {new_shear}"
                        )
                        print("Attempting direct makeIdentity...")
                        try:
                            pm.makeIdentity(obj, apply=True, t=1, r=1, s=1, n=0, pn=1)
                            print("Direct makeIdentity succeeded.")
                            fixed_objects.append(obj)
                        except Exception as e:
                            print(f"Direct makeIdentity failed: {e}")
                    else:
                        fixed_objects.append(obj)

                except Exception as e:
                    print(f"Failed to fix {obj}: {e}")

        if fixed_objects:
            print(f"Fixed non-orthogonal axes on {len(fixed_objects)} objects.")
        elif dry_run:
            print("Dry run complete.")
        else:
            print("No objects with non-orthogonal axes found.")
