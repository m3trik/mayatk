# !/usr/bin/python
# coding=utf-8
"""Scene auto-instancer prototype."""
from __future__ import annotations

from typing import Dict, List, Optional, Sequence

try:
    import pymel.core as pm
except ImportError as error:
    print(__file__, error)
import pythontk as ptk

# from mayatk.core_utils.instance_separator import (
#     InstanceGroup,
#     InstanceSeparator,
# )
from mayatk.xform_utils.matrices import Matrices

RELOAD_COUNTER = globals().get("RELOAD_COUNTER", 0) + 1
print(f"MAYATK: Loaded AutoInstancer module (reload #{RELOAD_COUNTER})")


class AutoInstancer(ptk.LoggingMixin):
    """Prototype workflow for converting matching meshes into instances."""

    def __init__(
        self,
        tolerance: float = 0.98,
        require_same_material: bool = True,
        verbose: bool = True,
    ) -> None:
        super().__init__()
        self.tolerance = tolerance
        self.require_same_material = require_same_material
        self.verbose = verbose

    # ----------------------------------------------------------------------------------
    # Public API
    # ----------------------------------------------------------------------------------
    def run(
        self,
        nodes: Optional[Sequence[pm.nodetypes.Transform]] = None,
    ) -> List[pm.nodetypes.Transform]:
        """Entry point for discovering and instancing matching meshes.

        Args:
            nodes: Transforms to process. If None, uses current selection.

        Returns:
            List of all created instance transforms.
        """
        separator = InstanceSeparator(
            tolerance=self.tolerance,
            require_same_material=self.require_same_material,
            rebuild_instances=False,
            verbose=False,
        )
        result = separator.separate(nodes)
        groups = result.groups
        report: List[Dict[str, object]] = []
        all_instances: List[pm.nodetypes.Transform] = []

        for group in groups:
            if not group.members:
                # Unique object with no duplicates; skip
                continue

            created = self._convert_group_to_instances(group)
            all_instances.extend(created)
            report.append(
                {
                    "prototype": group.prototype.transform,
                    "instance_count": len(created)
                    - 1,  # Don't count the prototype itself
                    "instances": created,
                }
            )

        if self.verbose:
            self._log_report(report, result.payload_count)

        return all_instances

    def _convert_group_to_instances(
        self, group: InstanceGroup
    ) -> List[pm.nodetypes.Transform]:
        """Convert all members of a group to instances of the prototype.

        Creates real Maya instances by deleting duplicates and instancing the prototype.
        Returns ALL objects including the prototype source.
        """
        if not group.members:
            return []

        prototype_transform = group.prototype.transform
        instances = []

        # For each duplicate, create an instance and match its transform
        for member in group.members:
            target = member.transform
            target_name = target.name()
            target_parent = member.parent

            # Create instance of prototype
            new_instance = pm.instance(prototype_transform)[0]

            # Match transform from the original target
            Matrices.bake_world_matrix_to_transform(new_instance, member.matrix)

            # Restore hierarchy
            if target_parent:
                try:
                    pm.parent(new_instance, target_parent, absolute=True)
                except RuntimeError:
                    pass

            # Restore visibility
            new_instance.visibility.set(member.visibility)

            # Delete original and rename instance to match
            pm.delete(target)
            new_instance.rename(target_name)

            instances.append(new_instance)

        # Return prototype + all created instances
        return [prototype_transform] + instances

    def _log_report(self, report: List[Dict[str, object]], payload_count: int) -> None:
        total_instances = sum(entry["instance_count"] for entry in report)
        self.logger.info(
            "AutoInstancer processed %s payloads and created %s instances across %s groups",
            payload_count,
            total_instances,
            len(report),
        )
        for entry in report:
            prototype = entry["prototype"]
            count = entry["instance_count"]
            self.logger.info(" - %s → %s instances", prototype, count)


__all__ = ["AutoInstancer", "RELOAD_COUNTER"]
