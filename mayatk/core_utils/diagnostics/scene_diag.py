# !/usr/bin/python
# coding=utf-8
"""Scene diagnostics and repair helpers."""
from __future__ import annotations

import os
from pathlib import Path

try:
    import pymel.core as pm
except ImportError as error:  # pragma: no cover - Maya runtime specific
    print(__file__, error)


class SceneDiagnostics:
    """Operations for inspecting and fixing common scene issues."""

    @staticmethod
    def fix_ocio(
        dry_run: bool = False,
        verbose: bool = True,
        prefer_env_ocio: bool = True,
        prefer_aces: bool = True,
    ) -> dict:
        """Repair Maya OCIO/Color Management preferences.

        Goals:
        - Ensure Maya color management is enabled.
        - Ensure the configured OCIO config file path points to a real, loadable config.
        - Prefer a user-provided OCIO config (OCIO env var) when valid.
        - Otherwise, discover a reasonable default OCIO config shipped with the installed Maya.

        This is intentionally conservative: it only changes config path + enable flag.
        It does not force view/display/rendering spaces, which are pipeline-specific.

        Args:
            dry_run: If True, report intended changes only.
            verbose: If True, print a summary of actions.
            prefer_env_ocio: Prefer a valid OCIO config from the OCIO environment variable.
            prefer_aces: Prefer an ACES config when multiple configs are found.

        Returns:
            dict with keys: changed(bool), previous_config(str|None), new_config(str|None),
            enabled_before(bool|None), enabled_after(bool|None), notes(list[str]).
        """

        notes: list[str] = []

        def _cm_prefs(*args, **kwargs):
            return pm.colorManagementPrefs(*args, **kwargs)

        def _try_cm_query(flag: str):
            try:
                return _cm_prefs(q=True, **{flag: True})
            except Exception:
                return None

        def _try_cm_set(**kwargs) -> bool:
            try:
                _cm_prefs(e=True, **kwargs)
                return True
            except Exception:
                return False

        def _get_maya_location() -> Path | None:
            loc = os.environ.get("MAYA_LOCATION")
            if loc:
                p = Path(loc)
                if p.exists():
                    return p
            # Fallback: try to derive from maya module path
            try:
                import maya  # type: ignore

                mp = Path(maya.__file__).resolve()
                # .../Maya2025/Python/Lib/site-packages/maya/__init__.py
                # Walk up a bit and hope to land on MAYA_LOCATION.
                for parent in mp.parents:
                    if (parent / "bin").exists() and (parent / "resources").exists():
                        return parent
            except Exception:
                return None
            return None

        def _walk_configs(root: Path, max_depth: int = 6) -> list[Path]:
            configs: list[Path] = []
            if not root.exists():
                return configs
            root = root.resolve()
            for dirpath, dirnames, filenames in os.walk(root):
                rel_depth = len(Path(dirpath).resolve().parts) - len(root.parts)
                if rel_depth > max_depth:
                    dirnames[:] = []
                    continue
                for fn in filenames:
                    if fn.lower().endswith(".ocio"):
                        configs.append(Path(dirpath) / fn)
            return configs

        def _is_valid_ocio_config(path: Path) -> bool:
            if not path.exists() or not path.is_file():
                return False
            # Try using OCIO if available
            try:
                import PyOpenColorIO as ocio  # type: ignore

                ocio.Config.CreateFromFile(str(path))
                return True
            except Exception:
                # Fallback heuristic (fast, but not authoritative)
                try:
                    head = path.read_text(encoding="utf-8", errors="ignore")[:4096]
                except Exception:
                    return False
                return (
                    "ocio_profile_version" in head
                    or "roles:" in head
                    or "displays:" in head
                )

        def _rank_config(path: Path) -> tuple:
            p = str(path).lower()
            score = 0
            if path.name.lower() == "config.ocio":
                score += 5
            if "resources" in p:
                score += 2
            if "ocio" in p:
                score += 1
            if prefer_aces and "aces" in p:
                score += 4
            # Prefer configs that are not clearly "test" or "example"
            if "example" in p or "test" in p:
                score -= 3
            return (score, len(p))

        previous_config = (
            _try_cm_query("configFilePath")
            or _try_cm_query("cmConfigFilePath")
            or _try_cm_query("ocioConfigFilePath")
            or _try_cm_query("cmConfigFile")
            or _try_cm_query("config")
        )
        if isinstance(previous_config, (list, tuple)) and previous_config:
            previous_config = previous_config[0]

        enabled_before = _try_cm_query("cmEnabled")
        if enabled_before is None:
            enabled_before = _try_cm_query("cmEnable")
        if enabled_before is None:
            enabled_before = _try_cm_query("enabled")
        if isinstance(enabled_before, (list, tuple)) and enabled_before:
            enabled_before = enabled_before[0]

        chosen: Path | None = None

        # 1) Prefer OCIO env var
        if prefer_env_ocio:
            ocio_env = os.environ.get("OCIO")
            if ocio_env:
                env_path = Path(ocio_env)
                if _is_valid_ocio_config(env_path):
                    chosen = env_path
                    notes.append(f"Using OCIO from env var: {env_path}")
                else:
                    notes.append(f"OCIO env var set but invalid/missing: {env_path}")

        # 2) If current Maya pref config is valid, keep it
        if chosen is None and previous_config:
            try:
                prev_path = Path(str(previous_config))
                if _is_valid_ocio_config(prev_path):
                    chosen = prev_path
                    notes.append(f"Current Maya OCIO config is valid: {prev_path}")
            except Exception:
                pass

        # 3) Discover shipped configs from Maya install
        if chosen is None:
            maya_loc = _get_maya_location()
            if maya_loc:
                search_roots = [
                    maya_loc / "resources" / "OCIO",
                    maya_loc / "resources" / "ColorManagement",
                    maya_loc / "resources" / "colorManagement",
                    maya_loc / "resources",
                ]
                found: list[Path] = []
                for r in search_roots:
                    found.extend(_walk_configs(r, max_depth=6))
                found = list(dict.fromkeys([p.resolve() for p in found if p.exists()]))
                found_valid = [p for p in found if _is_valid_ocio_config(p)]
                if found_valid:
                    found_valid.sort(key=_rank_config, reverse=True)
                    chosen = found_valid[0]
                    notes.append(f"Discovered Maya OCIO config: {chosen}")
                else:
                    notes.append(
                        "No valid OCIO configs discovered under MAYA_LOCATION/resources."
                    )
            else:
                notes.append(
                    "Cannot determine MAYA_LOCATION; config discovery skipped."
                )

        enabled_after = bool(enabled_before) if enabled_before is not None else None
        changed = False

        if chosen is None:
            if verbose:
                pm.warning("OCIO repair: no valid config found; no changes made.")
            return {
                "changed": False,
                "previous_config": str(previous_config) if previous_config else None,
                "new_config": None,
                "enabled_before": enabled_before,
                "enabled_after": enabled_before,
                "notes": notes,
            }

        # Apply changes
        target_config = str(chosen)

        if enabled_before is not True:
            enabled_after = True
            if dry_run:
                notes.append("Would enable color management")
            else:
                ok = (
                    _try_cm_set(cmEnabled=True)
                    or _try_cm_set(cmEnable=True)
                    or _try_cm_set(enabled=True)
                )
                if ok:
                    changed = True
                    notes.append("Enabled color management")
                else:
                    notes.append("Failed to enable color management (unsupported flag)")

        if not previous_config or str(previous_config) != target_config:
            if dry_run:
                notes.append(f"Would set OCIO config file: {target_config}")
            else:
                ok = (
                    _try_cm_set(configFilePath=target_config)
                    or _try_cm_set(cmConfigFilePath=target_config)
                    or _try_cm_set(ocioConfigFilePath=target_config)
                    or _try_cm_set(cmConfigFile=target_config)
                    or _try_cm_set(config=target_config)
                )
                if ok:
                    changed = True
                    notes.append(f"Set OCIO config file: {target_config}")
                else:
                    notes.append("Failed to set OCIO config file (unsupported flag)")

        if verbose:
            header = "OCIO repair (dry-run)" if dry_run else "OCIO repair"
            print(header)
            print(f"  Enabled: {enabled_before} -> {enabled_after}")
            print(f"  Config : {previous_config} -> {target_config}")
            for n in notes:
                print(f"  - {n}")

        return {
            "changed": changed,
            "previous_config": str(previous_config) if previous_config else None,
            "new_config": target_config,
            "enabled_before": enabled_before,
            "enabled_after": enabled_after,
            "notes": notes,
        }

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
