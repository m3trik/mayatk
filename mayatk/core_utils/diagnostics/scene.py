# !/usr/bin/python
# coding=utf-8
"""Scene diagnostics and repair helpers."""
from __future__ import annotations

try:
    import pymel.core as pm
except ImportError as error:  # pragma: no cover - Maya runtime specific
    print(__file__, error)


class SceneDiagnostics:
    """Operations for inspecting and fixing common scene issues."""

    @staticmethod
    def fix_unknown_plugins(dry_run=False, verbose=True):
        """
        Fixes the 'Unable to Save Scene' issue by removing unknown nodes and plugins.
        Ref: https://www.autodesk.com/support/technical/article/caas/sfdcarticles/sfdcarticles/Unable-to-Save-Scene-as-MA-file-or-Maya-crashes-when-saving-as-MB-file.html

        Args:
            dry_run (bool): If True, only lists what would be removed without taking action.
            verbose (bool): If True, logs details about the operations.
        """
        # Delete unknown nodes
        try:
            unknown_nodes = pm.ls(type="unknown")
            if unknown_nodes:
                if verbose:
                    action = "Would delete" if dry_run else "Deleting"
                    print(f"{action} {len(unknown_nodes)} unknown nodes:")
                    for node in unknown_nodes:
                        print(f"  - {node}")

                if not dry_run:
                    pm.delete(unknown_nodes)
        except Exception as e:
            pm.warning(f"Error deleting unknown nodes: {e}")

        # Remove unknown plugins
        try:
            unknown_plugins = pm.unknownPlugin(query=True, list=True)
            if unknown_plugins:
                if verbose:
                    action = "Would remove" if dry_run else "Removing"
                    print(f"{action} {len(unknown_plugins)} unknown plugins:")
                    for plugin in unknown_plugins:
                        print(f"  - {plugin}")

                if not dry_run:
                    for plugin in unknown_plugins:
                        try:
                            pm.unknownPlugin(plugin, remove=True)
                        except Exception as e:
                            if verbose:
                                pm.warning(
                                    f"Failed to remove unknown plugin '{plugin}': {e}"
                                )
        except Exception as e:
            pm.warning(f"Error querying unknown plugins: {e}")

        if (
            verbose
            and not ("unknown_nodes" in locals() and unknown_nodes)
            and not ("unknown_plugins" in locals() and unknown_plugins)
        ):
            print("No unknown nodes or plugins found.")

    @staticmethod
    def remove_xgen_expressions(quiet: bool = False) -> int:
        """
        Remove legacy XGen expressions that cause 'Cannot find procedure xgmPreview' errors.

        Returns:
            int: Number of nodes deleted.
        """
        nodes_to_delete = []

        # Common XGen expression that causes issues when plugin is missing
        if pm.objExists("xgmRefreshPreview"):
            nodes_to_delete.append("xgmRefreshPreview")

        if not nodes_to_delete:
            if not quiet:
                print("No XGen preview expressions found.")
            return 0

        count = len(nodes_to_delete)
        try:
            pm.delete(nodes_to_delete)
            if not quiet:
                print(
                    f"Deleted {count} XGen expression nodes: {', '.join(nodes_to_delete)}"
                )
        except Exception as e:
            if not quiet:
                pm.warning(f"Failed to delete XGen nodes: {e}")
            return 0

        return count

    @classmethod
    def cleanup_scene(cls, quiet: bool = False) -> None:
        """
        Run all scene cleanup operations:
        - Remove unknown nodes and plugins
        - Remove legacy XGen expressions
        """
        if not quiet:
            print("Starting scene cleanup...")

        cls.fix_unknown_plugins(dry_run=False, verbose=not quiet)
        cls.remove_xgen_expressions(quiet=quiet)

        if not quiet:
            print("Scene cleanup complete.")
