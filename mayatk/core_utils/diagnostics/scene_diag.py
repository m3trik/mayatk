# !/usr/bin/python
# coding=utf-8
"""Scene repair helpers: OCIO / color management, unknown nodes and plugins,
legacy XGen leftovers.

The scene *audit* engine lives in the sibling ``scene_audit`` module
(:class:`~mayatk.core_utils.diagnostics.scene_audit.SceneAnalyzer`), with its
data contract in ``audit_records``.
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import List, Dict, Optional, Set, Any, Tuple

try:
    import maya.cmds as cmds
except ImportError as error:  # pragma: no cover - Maya runtime specific
    print(__file__, error)
import pythontk as ptk


class SceneDiagnostics:
    """Operations for inspecting and fixing common scene issues."""

    @classmethod
    def fix_ocio(
        cls,
        dry_run: bool = False,
        verbose: bool = True,
        prefer_env_ocio: bool = True,
        prefer_aces: bool = True,
        fix_color_spaces: bool = True,
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
            fix_color_spaces: If True, automatically fix file nodes with invalid
                color space names after changing the OCIO config.

        Returns:
            dict with keys: changed(bool), previous_config(str|None), new_config(str|None),
            enabled_before(bool|None), enabled_after(bool|None), notes(list[str]).
        """

        notes: list[str] = []

        def _cm_prefs(*args, **kwargs):
            return cmds.colorManagementPrefs(*args, **kwargs)

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
                cmds.warning("OCIO repair: no valid config found; no changes made.")
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

        # If the config changed, file nodes may have invalid color space names
        # Automatically fix them to use valid names from the new config
        if fix_color_spaces and changed and not dry_run:
            color_space_result = cls.fix_missing_color_spaces(verbose=verbose)
            if color_space_result["fixed_count"] > 0:
                notes.append(
                    f"Fixed {color_space_result['fixed_count']} file node(s) with invalid color spaces"
                )

        return {
            "changed": changed,
            "previous_config": str(previous_config) if previous_config else None,
            "new_config": target_config,
            "enabled_before": enabled_before,
            "enabled_after": enabled_after,
            "notes": notes,
        }

    # Map types that contain actual color data (should use sRGB)
    # All others are non-color data and should use Raw/Linear
    COLOR_MAP_TYPES = frozenset(
        {
            "Base_Color",
            "Albedo_Transparency",
            "Diffuse",
            "Emissive",
            "Specular",
            "Subsurface_Scattering",
            "Sheen",
        }
    )

    @classmethod
    def fix_missing_color_spaces(
        cls,
        fallback_color_space: Optional[str] = None,
        fallback_raw_space: Optional[str] = None,
        auto_detect: bool = True,
        dry_run: bool = False,
        verbose: bool = True,
        scan_all: bool = True,
        force_update: bool = False,
    ) -> Dict[str, Any]:
        """Fix missing color space errors on file texture nodes.

        When opening a Maya scene, file nodes may reference color spaces that are not
        available in the current OCIO configuration. This method finds those nodes and
        reassigns them to a valid fallback color space.

        **Crash-safe strategy**: Color management is temporarily *disabled* before
        any node attributes are touched.  This prevents Maya's OCIO subsystem from
        validating (and crashing on) stale colour-space strings mid-edit.  CM is
        re-enabled only after every node has a valid value, using ``evalDeferred``
        so the DG is idle.

        Uses pythontk's TextureMapFactory.resolve_map_type for robust texture type
        detection based on comprehensive suffix matching.

        Parameters
        ----------
        fallback_color_space : str, optional
            The color space for color textures (diffuse, albedo, emissive, etc.).
            If None, auto-detects best sRGB variant from available spaces.
        fallback_raw_space : str, optional
            The color space for non-color data (normals, roughness, metallic, etc.).
            If None, auto-detects best Raw/Linear variant from available spaces.
        auto_detect : bool
            If True, attempts to detect texture type from filename using
            TextureMapFactory.resolve_map_type and assigns appropriate color space.
        dry_run : bool
            If True, only report what would be changed without modifying nodes.
        verbose : bool
            If True, print information about fixed nodes.
        scan_all : bool
            If True, scans ALL file nodes and checks if their colorSpace value
            is valid (exists in available inputSpaceNames). This catches nodes
            that missingColorSpaceNodes may miss.
        force_update : bool
            If True, re-assigns the color space even if the current value appears
            valid. This is useful for fixing "not defined in transform collection"
            warnings where the string matches but the internal reference is stale.

        Returns
        -------
        Dict[str, Any]
            Dictionary with keys:
            - fixed_count (int): Number of nodes fixed
            - fixed_nodes (List[str]): Names of fixed nodes
            - changes (List[Dict]): Details of each change
            - color_space (str): sRGB fallback used
            - raw_space (str): Raw fallback used
            - globals_fixed (bool): Whether global CM settings were fixed
        """

        result: Dict[str, Any] = {
            "fixed_count": 0,
            "fixed_nodes": [],
            "changes": [],
            "color_space": None,
            "raw_space": None,
            "globals_fixed": False,
        }

        # ------------------------------------------------------------------
        # 1. Query available colour spaces (CM must be on for this query).
        # ------------------------------------------------------------------
        available_spaces = cls._get_available_color_spaces()
        if not available_spaces:
            if verbose:
                cmds.warning("No valid color spaces available in current OCIO config.")
            return result

        available_set = set(available_spaces)
        if verbose:
            print(
                f"Available spaces ({len(available_spaces)} total): "
                f"{available_spaces[:5]}..."
            )

        # ------------------------------------------------------------------
        # 2. Collect nodes that need fixing **before** we disable CM.
        #    We query Maya's missing-nodes flag and scan for invalid values.
        # ------------------------------------------------------------------
        nodes_to_fix: Set[str] = set()

        # Method 1: Maya's built-in missing colour-space query.
        try:
            missing = cmds.colorManagementPrefs(query=True, missingColorSpaceNodes=True)
            if missing:
                for n in missing:
                    # Strip leading colons that namespaced scenes can add.
                    clean = n.lstrip(":")
                    if cmds.objExists(clean):
                        nodes_to_fix.add(clean)
                    elif cmds.objExists(n):
                        nodes_to_fix.add(n)
                if verbose:
                    print(
                        f"missingColorSpaceNodes returned {len(missing)} node(s)"
                    )
        except Exception as exc:
            if verbose:
                cmds.warning(f"missingColorSpaceNodes query failed: {exc}")

        # Method 2: Scan all file nodes for colour spaces not in the config.
        if scan_all or force_update:
            scan_found = 0
            try:
                for node_name in cmds.ls(type="file") or []:
                    if cmds.attributeQuery("colorSpace", node=node_name, exists=True):
                        cur = cmds.getAttr(f"{node_name}.colorSpace") or ""
                        if force_update or (cur and cur not in available_set):
                            nodes_to_fix.add(node_name)
                            scan_found += 1
            except Exception as exc:
                if verbose:
                    cmds.warning(f"File-node scan failed: {exc}")
            if verbose and scan_found:
                print(
                    f"Scan found {scan_found} additional node(s) "
                    f"(force_update={force_update})"
                )

        if not nodes_to_fix:
            if verbose:
                print("No nodes with missing color spaces found.")
            return result

        # ------------------------------------------------------------------
        # 3. Determine fallback colour spaces.
        # ------------------------------------------------------------------
        color_space = fallback_color_space or cls._find_best_color_space(
            available_spaces
        )
        raw_space = fallback_raw_space or cls._find_best_raw_space(available_spaces)
        result["color_space"] = color_space
        result["raw_space"] = raw_space

        if color_space not in available_set:
            cmds.warning(
                f"Color space fallback '{color_space}' not in available spaces! "
                f"Available: {available_spaces[:10]}..."
            )
        if raw_space not in available_set:
            cmds.warning(
                f"Raw space fallback '{raw_space}' not in available spaces! "
                f"Available: {available_spaces[:10]}..."
            )

        if verbose:
            tag = "(dry-run) " if dry_run else ""
            print(
                f"{tag}Found {len(nodes_to_fix)} node(s) with invalid color spaces"
            )
            print(f"{tag}Color space fallback: {color_space}")
            print(f"{tag}Raw/Data space fallback: {raw_space}")

        # ------------------------------------------------------------------
        # 4. Pre-compute the target colour space for every node (read-only).
        #    Done while CM is still enabled so colorManagementFileRules works.
        # ------------------------------------------------------------------
        # (node, current, target, detection_source)
        plan: List[Tuple[str, str, str, str]] = []

        for node_name in sorted(nodes_to_fix):
            try:
                if not cmds.objExists(node_name):
                    continue

                current_space = ""
                if cmds.attributeQuery("colorSpace", node=node_name, exists=True):
                    current_space = cmds.getAttr(f"{node_name}.colorSpace") or ""

                # Strategy 1 – Our own texture-type detection (preferred).
                # This correctly distinguishes color vs data maps (AO, Normal,
                # Roughness → Raw) which Maya's default file rules miss.
                detected = None
                detection_source = "default"
                if auto_detect:
                    detected = cls._detect_color_space_for_node_cmds(
                        node_name, color_space, raw_space
                    )
                    if detected != color_space:
                        # Our detector actively identified it as non-color data
                        detection_source = "texture-type"

                # Strategy 2 – Maya's own file-rule evaluation (fallback).
                # Only used when our detection returned the generic default
                # (i.e. it couldn't positively identify the map type).
                rule_space = None
                if detected is None or detection_source == "default":
                    if cmds.attributeQuery(
                        "fileTextureName", node=node_name, exists=True
                    ):
                        fpath = cmds.getAttr(f"{node_name}.fileTextureName") or ""
                        if fpath:
                            try:
                                rule_space = cmds.colorManagementFileRules(
                                    evaluate=fpath
                                )
                                if rule_space and rule_space in available_set:
                                    detection_source = "maya-file-rule"
                            except Exception:
                                pass

                # Resolve final target
                if detection_source == "texture-type" and detected in available_set:
                    target = detected
                elif rule_space and rule_space in available_set:
                    target = rule_space
                elif detected and detected in available_set:
                    target = detected
                else:
                    target = color_space

                if target not in available_set:
                    if verbose:
                        cmds.warning(
                            f"Cannot fix '{node_name}': target '{target}' invalid"
                        )
                    continue

                plan.append((node_name, current_space, target, detection_source))
            except Exception as exc:
                if verbose:
                    cmds.warning(f"Plan failed for '{node_name}': {exc}")

        if not plan:
            if verbose:
                print("No actionable nodes after planning.")
            return result

        # ------------------------------------------------------------------
        # 5. DISABLE colour management so setAttr on .colorSpace does NOT
        #    trigger OCIO validation (the main crash vector).
        # ------------------------------------------------------------------
        cm_was_enabled = True
        if not dry_run:
            try:
                cm_was_enabled = bool(
                    cmds.colorManagementPrefs(query=True, cmEnabled=True)
                )
                if cm_was_enabled:
                    cmds.colorManagementPrefs(edit=True, cmEnabled=False)
                    if verbose:
                        print(
                            "Temporarily disabled color management for safe edits"
                        )
            except Exception as exc:
                if verbose:
                    cmds.warning(f"Could not disable CM: {exc}")

        # ------------------------------------------------------------------
        # 6. Apply colour-space changes (CM is OFF — no OCIO crash risk).
        # ------------------------------------------------------------------
        for node_name, current_space, target_space, det_source in plan:
            try:
                change = {
                    "node": node_name,
                    "from": current_space,
                    "to": target_space,
                    "source": det_source,
                }
                result["changes"].append(change)

                if not dry_run:
                    # Disable custom rules so Maya applies OCIO defaults on re-enable
                    if cmds.attributeQuery(
                        "ignoreColorSpaceFileRules", node=node_name, exists=True
                    ):
                        try:
                            cmds.setAttr(
                                f"{node_name}.ignoreColorSpaceFileRules", False
                            )
                        except Exception:
                            pass

                    cmds.setAttr(f"{node_name}.colorSpace", target_space, type="string")

                result["fixed_nodes"].append(node_name)
                result["fixed_count"] += 1

                if verbose:
                    tag = "Would fix" if dry_run else "Fixed"
                    print(
                        f"{tag} '{node_name}': '{current_space}' -> '{target_space}'"
                        f" [{change.get('source', '?')}]"
                    )
            except Exception as exc:
                cmds.warning(f"Failed to set colorSpace on '{node_name}': {exc}")

        # ------------------------------------------------------------------
        # 7. RE-ENABLE colour management & fix globals (deferred so the DG
        #    is idle and all attribute values are already correct).
        # ------------------------------------------------------------------
        if not dry_run:
            _deferred_code = (
                "import maya.cmds as _c\n"
                "try:\n"
                "    _c.colorManagementPrefs(e=True, cmEnabled=True)\n"
                "except Exception:\n"
                "    pass\n"
                "try:\n"
                "    if _c.objExists('defaultColorMgtGlobals'):\n"
                "        _c.setAttr('defaultColorMgtGlobals.configFileEnabled', True)\n"
                "except Exception:\n"
                "    pass\n"
                "try:\n"
                "    _c.colorManagementPrefs(e=True, refresh=True)\n"
                "except Exception:\n"
                "    pass\n"
            )
            try:
                cmds.evalDeferred(_deferred_code)
            except Exception as exc:
                # If evalDeferred itself fails, try a direct re-enable as last resort.
                if verbose:
                    cmds.warning(f"evalDeferred failed ({exc}); re-enabling CM directly")
                try:
                    cmds.colorManagementPrefs(edit=True, cmEnabled=True)
                except Exception:
                    pass

            result["globals_fixed"] = True

        if verbose:
            tag = "Would fix" if dry_run else "Fixed"
            print(
                f"{tag} {result['fixed_count']} node(s) with missing color spaces."
            )

        return result

    @staticmethod
    def _unescape_fbx_ascii(name: str) -> str:
        """Decode FBX ASCII escape sequences like ``FBXASC046`` (→ ``.``).

        FBX exports replace non-alphanumeric characters with ``FBXASC###``
        where ``###`` is the zero-padded decimal ASCII code.  This helper
        restores the original characters so that texture-type detection can
        parse suffixes correctly.
        """
        return re.sub(
            r"FBXASC(\d{3})",
            lambda m: chr(int(m.group(1))),
            name,
        )

    @classmethod
    def _detect_color_space_for_node_cmds(
        cls,
        node_name: str,
        color_space: str,
        raw_space: str,
    ) -> str:
        """Detect appropriate color space based on texture filename (maya.cmds).

        Pure ``maya.cmds`` variant used during the crash-safe fix path where
        Heavy object wrappers must be avoided.

        Resolution order:
        1. ``fileTextureName`` attribute (the actual file path).
        2. FBX-unescaped ``fileTextureName``.
        3. The Maya node name itself (useful for FBX-imported nodes whose
           names embed the original filename, e.g.
           ``pasted__CargoWall_CCPanelsStickers_NORMFBXASC046jpg``).
        """

        candidates: list[str] = []

        # Primary: the actual texture path
        if cmds.attributeQuery("fileTextureName", node=node_name, exists=True):
            fpath = cmds.getAttr(f"{node_name}.fileTextureName") or ""
            if fpath:
                candidates.append(fpath)
                unescaped = cls._unescape_fbx_ascii(fpath)
                if unescaped != fpath:
                    candidates.append(unescaped)

        # Fallback: the node name itself (FBX-unescaped)
        unescaped_name = cls._unescape_fbx_ascii(node_name)
        candidates.append(unescaped_name)
        if unescaped_name != node_name:
            candidates.append(node_name)

        for candidate in candidates:
            try:
                map_type = ptk.TextureMapFactory.resolve_map_type(candidate)
                if map_type:
                    return color_space if map_type in cls.COLOR_MAP_TYPES else raw_space
            except Exception:
                continue

        return color_space

    @staticmethod
    def _find_best_color_space(available_spaces: List[str]) -> Optional[str]:
        """Find the best sRGB color space from available options.

        Returns None if no suitable color space is found (caller must handle).
        """
        if not available_spaces:
            return None

        available_set = set(available_spaces)
        available_lower = {s.lower(): s for s in available_spaces}

        # Priority order for color textures (exact matches first)
        exact_preferences = [
            "sRGB",
            "Utility - sRGB - Texture",
            "sRGB - Texture",
            "Input - Generic - sRGB - Texture",
            "sRGB texture",
            "Texture - sRGB",
            "gamma 2.2 Rec 709",
            "Rec.709 Gamma 2.2",
        ]

        for pref in exact_preferences:
            if pref in available_set:
                return pref

        # Case-insensitive exact match
        for pref in exact_preferences:
            if pref.lower() in available_lower:
                return available_lower[pref.lower()]

        # Fuzzy match: find any space containing "srgb" and "texture"
        for space in available_spaces:
            sl = space.lower()
            if "srgb" in sl and "texture" in sl:
                return space

        # Fuzzy match: find any space containing "srgb"
        for space in available_spaces:
            if "srgb" in space.lower():
                return space

        # Last resort: first available space (better than returning invalid value)
        return available_spaces[0]

    @staticmethod
    def _find_best_raw_space(available_spaces: List[str]) -> Optional[str]:
        """Find the best Raw/Linear color space from available options.

        Returns None if no suitable color space is found (caller must handle).
        """
        if not available_spaces:
            return None

        available_set = set(available_spaces)
        available_lower = {s.lower(): s for s in available_spaces}

        # Priority order for non-color data (exact matches first)
        exact_preferences = [
            "Raw",
            "Utility - Raw",
            "raw",
            "ACEScg",
            "ACES - ACEScg",
            "Linear",
            "Utility - Linear - sRGB",
            "scene-linear Rec.709-sRGB",
            "Linear sRGB",
        ]

        for pref in exact_preferences:
            if pref in available_set:
                return pref

        # Case-insensitive exact match
        for pref in exact_preferences:
            if pref.lower() in available_lower:
                return available_lower[pref.lower()]

        # Fuzzy match: prefer "raw" over "linear" (more semantically correct for data)
        for space in available_spaces:
            if "raw" in space.lower():
                return space

        # Fuzzy match: linear variants
        for space in available_spaces:
            if "linear" in space.lower():
                return space

        # Fuzzy match: ACEScg (common in ACES configs for non-color data)
        for space in available_spaces:
            if "acescg" in space.lower():
                return space

        # Last resort: first available space
        return available_spaces[0]

    @staticmethod
    def _get_available_color_spaces() -> List[str]:
        """Get a list of all available input color spaces."""

        try:
            return cmds.colorManagementPrefs(query=True, inputSpaceNames=True) or []
        except Exception:
            return []

    @staticmethod
    def fix_unknown_plugins(
        dry_run: bool = False, verbose: bool = True
    ) -> Dict[str, List[str]]:
        """
        Fixes the 'Unable to Save Scene' issue by removing unknown nodes and plugins.
        Ref: https://www.autodesk.com/support/technical/article/caas/sfdcarticles/sfdcarticles/Unable-to-Save-Scene-as-MA-file-or-Maya-crashes-when-saving-as-MB-file.html

        Args:
            dry_run (bool): If True, only lists what would be removed without taking action.
            verbose (bool): If True, logs details about the operations.

        Returns:
            dict: ``{"nodes": [...], "plugins": [...]}`` — the unknown nodes and
            plugins that were removed (or, on ``dry_run``, would be removed).
        """
        unknown_nodes: List[str] = []
        unknown_plugins: List[str] = []

        # Delete unknown nodes
        try:
            unknown_nodes = cmds.ls(type="unknown") or []
            if unknown_nodes:
                if verbose:
                    action = "Would delete" if dry_run else "Deleting"
                    print(f"{action} {len(unknown_nodes)} unknown nodes:")
                    for node in unknown_nodes:
                        print(f"  - {node}")

                if not dry_run:
                    cmds.delete(unknown_nodes)
        except Exception as e:
            cmds.warning(f"Error deleting unknown nodes: {e}")

        # Remove unknown plugins
        try:
            unknown_plugins = cmds.unknownPlugin(query=True, list=True) or []
            if unknown_plugins:
                if verbose:
                    action = "Would remove" if dry_run else "Removing"
                    print(f"{action} {len(unknown_plugins)} unknown plugins:")
                    for plugin in unknown_plugins:
                        print(f"  - {plugin}")

                if not dry_run:
                    for plugin in unknown_plugins:
                        try:
                            cmds.unknownPlugin(plugin, remove=True)
                        except Exception as e:
                            if verbose:
                                cmds.warning(
                                    f"Failed to remove unknown plugin '{plugin}': {e}"
                                )
        except Exception as e:
            cmds.warning(f"Error querying unknown plugins: {e}")

        if verbose and not unknown_nodes and not unknown_plugins:
            print("No unknown nodes or plugins found.")

        return {"nodes": unknown_nodes, "plugins": unknown_plugins}

    @staticmethod
    def remove_xgen_expressions(quiet: bool = False) -> int:
        """
        Remove legacy XGen expressions that cause 'Cannot find procedure xgmPreview' errors.

        Returns:
            int: Number of nodes deleted.
        """
        nodes_to_delete = []

        # Common XGen expression that causes issues when plugin is missing
        if cmds.objExists("xgmRefreshPreview"):
            nodes_to_delete.append("xgmRefreshPreview")

        if not nodes_to_delete:
            if not quiet:
                print("No XGen preview expressions found.")
            return 0

        count = len(nodes_to_delete)
        try:
            cmds.delete(nodes_to_delete)
            if not quiet:
                print(
                    f"Deleted {count} XGen expression nodes: {', '.join(nodes_to_delete)}"
                )
        except Exception as e:
            if not quiet:
                cmds.warning(f"Failed to delete XGen nodes: {e}")
            return 0

        return count

    @classmethod
    def cleanup_scene(cls, quiet: bool = False) -> Dict[str, Any]:
        """
        Run all scene cleanup operations:
        - Remove unknown nodes and plugins
        - Remove legacy XGen expressions

        Returns:
            dict: ``{"unknown": {"nodes": [...], "plugins": [...]},
            "xgen_removed": int}`` summarizing what was removed.
        """
        if not quiet:
            print("Starting scene cleanup...")

        unknown = cls.fix_unknown_plugins(dry_run=False, verbose=not quiet)
        xgen_removed = cls.remove_xgen_expressions(quiet=quiet)

        if not quiet:
            print("Scene cleanup complete.")

        return {"unknown": unknown, "xgen_removed": xgen_removed}
