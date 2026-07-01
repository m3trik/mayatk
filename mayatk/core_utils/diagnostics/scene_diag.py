# !/usr/bin/python
# coding=utf-8
"""Scene diagnostics and repair helpers."""
from __future__ import annotations

import os
import re
from pathlib import Path
from dataclasses import dataclass, field
from typing import List, Dict, Optional, Set, Any, Tuple, Callable
import math

try:
    import maya.cmds as cmds
    import maya.api.OpenMaya as om
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

    @classmethod
    def _fix_color_management_globals(cls, verbose: bool = True) -> bool:
        """Fix global color management settings in ``defaultColorMgtGlobals``.

        .. note::

           This is now called exclusively through ``evalDeferred`` inside
           ``fix_missing_color_spaces``, so it runs only after all node
           colour-space strings have been set to valid values.  Do **not**
           call this while nodes still reference invalid colour spaces —
           enabling CM forces OCIO validation and will crash Maya.

        Returns True if any changes were made.
        """

        changed = False
        gn = "defaultColorMgtGlobals"

        if not cmds.objExists(gn):
            if verbose:
                cmds.warning(f"CM globals node '{gn}' not found")
            return False

        # 1. cmEnabled
        try:
            if not cmds.colorManagementPrefs(query=True, cmEnabled=True):
                cmds.colorManagementPrefs(edit=True, cmEnabled=True)
                changed = True
                if verbose:
                    print("Enabled color management")
        except Exception as exc:
            if verbose:
                cmds.warning(f"Cannot enable CM: {exc}")
            return changed

        # 2. configFileEnabled
        try:
            if not cmds.getAttr(f"{gn}.configFileEnabled"):
                cmds.setAttr(f"{gn}.configFileEnabled", True)
                changed = True
                if verbose:
                    print(f"Set {gn}.configFileEnabled = True")
        except Exception as exc:
            if verbose:
                cmds.warning(f"Cannot set configFileEnabled: {exc}")
            return changed

        # 3. Sync config path with prefs
        try:
            cur = cmds.getAttr(f"{gn}.configFilePath")
            prefs = cmds.colorManagementPrefs(query=True, configFilePath=True)
            if cur != prefs and prefs:
                cmds.setAttr(f"{gn}.configFilePath", prefs, type="string")
                changed = True
                if verbose:
                    print(f"Updated {gn}.configFilePath to: {prefs}")
        except Exception as exc:
            if verbose:
                cmds.warning(f"Cannot update configFilePath: {exc}")

        return changed

    @staticmethod
    def _get_available_color_spaces() -> List[str]:
        """Get a list of all available input color spaces."""

        try:
            return cmds.colorManagementPrefs(query=True, inputSpaceNames=True) or []
        except Exception:
            return []

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
            unknown_nodes = cmds.ls(type="unknown")
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
            unknown_plugins = cmds.unknownPlugin(query=True, list=True)
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


@dataclass
class AuditProfile:
    """Thresholds for scene analysis."""

    max_tris: int = 20000
    max_slots: int = 4
    max_tex_res: int = 4096
    max_uvs: int = 1
    name: str = "Standard"
    texture_compression: str = "BC7"  # BC7, ASTC, None
    adaptive_tris: bool = False
    reference_diag: float = 200.0  # Size in units where max_tris applies
    min_tris: int = 500  # Floor for adaptive budget


# --------------------------------------------------------------- #
# Structured records returned by SceneAnalyzer.
# --------------------------------------------------------------- #
# Severity / kind strings are plain literals (no enum) so the
# dataclasses serialize cleanly via ``dataclasses.asdict`` for the
# machine-readable ``SceneReport.to_dict`` path. Callers can compare
# against the constants below for stable matching.
SEVERITY_LOW = "low"
SEVERITY_MEDIUM = "medium"
SEVERITY_HIGH = "high"


@dataclass
class MeshRecord:
    """Per-mesh statistics for a single shape node."""

    shape_name: str
    tris: int
    verts: int
    uv_sets: int
    has_colors: bool
    instanced: bool
    bounds_diag: float
    uv_set_names: List[str] = field(default_factory=list)
    ngons: int = 0
    non_manifold_edges: int = 0
    lamina_faces: int = 0
    vertex_bytes: int = 0


@dataclass
class MaterialRecord:
    """Per-shape material usage summary (aggregated across slots)."""

    slot_count: int
    uses_transparency: bool
    materials: List[str]
    texture_count: int = 0
    max_res: int = 0
    total_tex_size_mb: float = 0.0
    est_gpu_size_mb: float = 0.0
    unpacked_pbr: bool = False
    missing_textures: int = 0
    max_samplers: int = 0
    unique_paths_local: int = 0
    max_res_is_unique: bool = False


@dataclass
class Finding:
    """An observation about an asset (negative or risk-flagged)."""

    severity: str  # SEVERITY_LOW / SEVERITY_MEDIUM / SEVERITY_HIGH
    kind: str      # e.g. "high_poly", "vert_bloat", "ngons", "non_manifold", "extra_uv_sets"
    message: str   # human-readable summary; data lives in ``detail``
    detail: Dict[str, Any] = field(default_factory=dict)


@dataclass
class FixAction:
    """A recommended remediation step."""

    severity: str  # SEVERITY_LOW / SEVERITY_MEDIUM / SEVERITY_HIGH
    kind: str      # e.g. "decimate", "reduce_slots", "remove_uv_sets", "relink_textures"
    message: str
    target: Optional[str] = None  # transform path, material name, or texture path
    detail: Dict[str, Any] = field(default_factory=dict)


@dataclass
class BudgetDelta:
    """How far an asset exceeds the profile budget along each axis."""

    tris_over: int = 0
    slots_over: int = 0
    uvs_over: int = 0
    max_tex_res_over: int = 0

    def is_over_budget(self) -> bool:
        return any(
            (
                self.tris_over > 0,
                self.slots_over > 0,
                self.uvs_over > 0,
                self.max_tex_res_over > 0,
            )
        )

    def summary(self) -> str:
        """Pre-rendered ``"tris +N | slots +M | …"`` string used by the
        text renderer. Data layer keeps the raw numbers so callers can
        sort / threshold / serialize without re-parsing."""
        parts: List[str] = []
        if self.tris_over:
            parts.append(f"tris +{self.tris_over}")
        if self.slots_over:
            parts.append(f"slots +{self.slots_over}")
        if self.uvs_over:
            parts.append(f"uv +{self.uvs_over}")
        if self.max_tex_res_over:
            parts.append(f"maxTex +{self.max_tex_res_over}")
        return " | ".join(parts)


@dataclass
class AssetRecord:
    """Combined per-asset record produced by analyze()."""

    transform: str
    mesh: MeshRecord
    material: MaterialRecord
    score: float = 0.0
    perf_score: float = 0.0
    risk_score: float = 0.0
    findings: List[Finding] = field(default_factory=list)
    score_breakdown: Dict[str, float] = field(default_factory=dict)
    instance_count: int = 0
    tri_percent: float = 0.0
    delta: BudgetDelta = field(default_factory=BudgetDelta)
    fix_plan: List[FixAction] = field(default_factory=list)
    target_tris: int = 0


@dataclass
class ParetoEntry:
    """One row of a Pareto ranking (top contributor + cumulative %)."""

    target: str
    value: int          # raw count for the metric this list ranks (tris or slots)
    cum_percent: float  # cumulative percentage at this row


@dataclass
class TextureFile:
    """A texture file referenced by the scene, with usage stats."""

    path: str
    size_mb: float
    width: int
    height: int
    material_count: int
    materials: List[str]
    mesh_count: int
    instance_count: int


@dataclass
class MissingTexture:
    """A texture referenced by a material but not present on disk."""

    path: str
    material_count: int
    materials: List[str]


@dataclass
class SharedTexture:
    """A texture used by more than one mesh."""

    path: str
    mesh_count: int


@dataclass
class MaterialSplit:
    """A material correlated with high-slot meshes (draw-call splits)."""

    material: str
    unique_mesh_count: int
    over_budget_count: int
    avg_slots: float


@dataclass
class SlotStats:
    """Distribution stats for material slots-per-mesh."""

    avg: float
    avg_unique: float
    median: int
    p90: int
    max: int


@dataclass
class InstanceStats:
    """Mesh / instance counts."""

    unique_meshes: int
    instanced_shapes: int
    total_instances: int


@dataclass
class BudgetBuckets:
    """Histogram of overage severity per dimension."""

    # Bucket label strings are stable: "0-10%" / "10-50%" / "50%+" for
    # tris, "1-2" / "3-5" / "6+" for slots. Kept as Dict here because
    # the labels also drive UI rendering — promoting to a dataclass
    # would just duplicate them.
    tris: Dict[str, int] = field(default_factory=dict)
    slots: Dict[str, int] = field(default_factory=dict)


@dataclass
class ComplianceStats:
    """Percentage of scene over budget per dimension."""

    tris_pct: float = 0.0
    slots_pct: float = 0.0


@dataclass
class MissingTextureImpact:
    """Downstream effect of missing textures on the asset list."""

    affected_meshes: List[str] = field(default_factory=list)
    affected_materials: List[str] = field(default_factory=list)
    top_offenders: List[str] = field(default_factory=list)

    def is_empty(self) -> bool:
        return not self.affected_meshes


@dataclass
class SummaryStats:
    """High-level scene counters surfaced by the Executive Summary."""

    total_meshes: int = 0
    total_tris: int = 0
    total_verts: int = 0
    raw_total_tris: int = 0
    instance_stats: InstanceStats = field(
        default_factory=lambda: InstanceStats(0, 0, 0)
    )
    scene_health_flags: List[str] = field(default_factory=list)
    multi_slot_meshes: int = 0
    transparent_meshes: int = 0
    non_manifold_count: int = 0
    lamina_count: int = 0
    ngon_count: int = 0
    high_poly_count: int = 0
    meshes_with_transparency: int = 0
    meshes_with_extra_uvs: int = 0
    meshes_with_high_slots: int = 0


@dataclass
class BudgetStats:
    """Budget / compliance / savings figures."""

    total_target_tris: int = 0
    total_slots: int = 0
    meshes_over_tri_threshold: int = 0
    meshes_over_slot_threshold: int = 0
    total_slots_over_budget: int = 0
    savings_draw_calls_total: int = 0
    savings_tris_total: int = 0
    savings_draw_calls_budget: int = 0
    savings_tris_budget: int = 0
    slot_stats: Optional[SlotStats] = None
    compliance: ComplianceStats = field(default_factory=ComplianceStats)
    buckets: BudgetBuckets = field(default_factory=BudgetBuckets)


@dataclass
class TextureStats:
    """Texture-side aggregates."""

    total_size_mb: float = 0.0
    est_gpu_mb: float = 0.0
    est_gpu_mb_compressed: float = 0.0
    max_resolution: int = 0
    large_texture_count: int = 0
    unique_paths: int = 0
    dim_histogram: Dict[str, int] = field(default_factory=dict)
    type_breakdown: Dict[str, float] = field(default_factory=dict)
    class_estimates: Dict[str, float] = field(default_factory=dict)
    shared_4k: List[SharedTexture] = field(default_factory=list)
    single_use_4k_count: int = 0
    shared_4k_count: int = 0
    heaviest: List[TextureFile] = field(default_factory=list)


@dataclass
class PipelineStats:
    """Pipeline integrity findings (missing textures + impact)."""

    integrity_warnings: List[str] = field(default_factory=list)
    missing_project: List[MissingTexture] = field(default_factory=list)
    missing_presets: List[MissingTexture] = field(default_factory=list)
    impact: MissingTextureImpact = field(default_factory=MissingTextureImpact)


@dataclass
class OffenderLists:
    """Top-N rankings across various dimensions.

    All lists are slices of the same underlying ``assets`` collection
    re-sorted and truncated. ``by_effective_score`` replaces the
    former ``top_repeated_offenders`` / ``top_by_effective_score``
    alias pair — they were the same list under two names.
    """

    by_score: List[AssetRecord] = field(default_factory=list)
    by_tris: List[AssetRecord] = field(default_factory=list)
    by_slots: List[AssetRecord] = field(default_factory=list)
    by_max_res: List[AssetRecord] = field(default_factory=list)
    by_risk: List[AssetRecord] = field(default_factory=list)
    by_transparency: List[AssetRecord] = field(default_factory=list)
    by_effective_score: List[AssetRecord] = field(default_factory=list)
    top_materials: List[Tuple[str, int]] = field(default_factory=list)
    savings_draw_calls: List[AssetRecord] = field(default_factory=list)
    savings_tris: List[AssetRecord] = field(default_factory=list)
    pareto_tris: List[ParetoEntry] = field(default_factory=list)
    pareto_slots: List[ParetoEntry] = field(default_factory=list)
    materials_causing_splits: List[MaterialSplit] = field(default_factory=list)


@dataclass
class AnalysisManifest:
    """What was analyzed, how, and how long it took.

    Surfaces ``analyze()``-time observability: the requested section
    set, the scope, which collectors actually ran, and timings /
    counts so callers can correlate a SceneReport with the run that
    produced it.
    """

    scope: str  # "selection" | "all" | "custom"
    sections_requested: List[str] = field(default_factory=list)
    materials_collected: bool = True
    textures_collected: bool = True
    profile: AuditProfile = field(default_factory=AuditProfile)
    started_at: float = 0.0  # unix timestamp
    duration_ms: int = 0
    shape_count: int = 0
    shading_engine_count: int = 0
    file_node_count: int = 0


@dataclass
class SceneReport:
    """Top-level result of ``SceneAnalyzer.generate_report``.

    Replaces the legacy ``SceneOverview`` mega-dataclass. Groups
    related metrics into typed sub-records and exposes a
    machine-readable export via :meth:`to_dict`.
    """

    manifest: AnalysisManifest
    summary: SummaryStats = field(default_factory=SummaryStats)
    budget: BudgetStats = field(default_factory=BudgetStats)
    textures: TextureStats = field(default_factory=TextureStats)
    pipeline: PipelineStats = field(default_factory=PipelineStats)
    offenders: OffenderLists = field(default_factory=OffenderLists)
    fix_actions: List[FixAction] = field(default_factory=list)
    assets: List[AssetRecord] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        """Serialize the report to a nested plain-dict tree.

        Suitable for JSON / pickle / dashboard ingestion. Nested
        dataclasses (including AssetRecord and its mesh/material
        children) are converted recursively via
        :func:`dataclasses.asdict`. ``profile`` flattens to its own
        dict; sets become lists when present (``MissingTextureImpact``
        keeps lists internally, so no conversion required).
        """
        from dataclasses import asdict

        return asdict(self)


class SceneInfoSection:
    """Report-section identifiers used to gate analyze() work and report output.

    ``ALL`` is the canonical render order. The dependency sets drive
    ``SceneAnalyzer.analyze`` so that unchecked sections skip the
    corresponding collection phase (material caches, texture file IO).
    """

    SUMMARY = "summary"
    FIX_FIRST = "fix_first"
    PARETO = "pareto"
    OFFENDERS = "offenders"
    CATEGORIES = "categories"
    TEXTURES = "textures"
    PIPELINE = "pipeline"
    ASSUMPTIONS = "assumptions"

    ALL: Tuple[str, ...] = (
        SUMMARY,
        FIX_FIRST,
        PARETO,
        OFFENDERS,
        CATEGORIES,
        TEXTURES,
        PIPELINE,
        ASSUMPTIONS,
    )

    LABELS: Dict[str, str] = {
        SUMMARY: "Executive Summary",
        FIX_FIRST: "Fix First (High Impact)",
        PARETO: "Pareto View",
        OFFENDERS: "Top Issues by Asset",
        CATEGORIES: "Top Offenders by Category",
        TEXTURES: "Textures",
        PIPELINE: "Pipeline Integrity",
        ASSUMPTIONS: "Data Assumptions",
    }

    # Material-cache phase is needed for anything that touches slots,
    # transparency, draw-call estimates or texture aggregates.
    _NEEDS_MATERIALS: Set[str] = {
        SUMMARY,
        FIX_FIRST,
        PARETO,
        OFFENDERS,
        CATEGORIES,
        TEXTURES,
        PIPELINE,
    }

    # Texture file IO (os.path.getsize + cmds.getAttr outSize per file
    # node) is the heaviest single cost; only walk it if the sections
    # the caller asked for actually surface that data.
    _NEEDS_TEXTURES: Set[str] = {
        SUMMARY,
        FIX_FIRST,
        TEXTURES,
        PIPELINE,
    }

    @classmethod
    def normalize(cls, sections: Optional[List[str]]) -> List[str]:
        """Coerce a caller-supplied sections argument to a stable,
        de-duped list of valid keys.

        ``None`` expands to all sections in :attr:`ALL` order. Unknown
        keys are dropped silently — that matches "best effort"
        semantics and means a downstream UI can pass through whatever
        the user picked without pre-filtering.

        Caller order is preserved so an option-box exposing section
        reordering would Just Work without touching this code.
        """
        if sections is None:
            return list(cls.ALL)
        valid = set(cls.ALL)
        seen: Set[str] = set()
        out: List[str] = []
        for key in sections:
            if key in valid and key not in seen:
                out.append(key)
                seen.add(key)
        return out


class SceneAnalyzer(ptk.LoggingMixin):
    """
    Analyzes scene objects for performance expectations in game engines.
    Focuses on Mesh and Material metrics with a scalable, bulk-collection workflow.
    """

    def __init__(self):
        super().__init__()
        self.logger.hide_logger_name(True)
        self._shading_map: Dict[str, Set[str]] = (
            {}
        )  # shape_name -> {shading_engine_names}
        self._material_map: Dict[str, str] = {}  # shading_engine -> material_node
        self._material_flags: Dict[str, Dict[str, Any]] = {}  # material_node -> {flags}
        self._global_texture_usage: Dict[str, Dict[str, Any]] = (
            {}
        )  # path -> {count, meshes, instances}
        self.scope = "selection"
        self.profile: Any = AuditProfile()
        # Populated by ``analyze`` so renderers can hide sections /
        # lines whose underlying data was deliberately skipped.
        self.collected_sections: Set[str] = set(SceneInfoSection.ALL)
        self.materials_collected: bool = True
        self.textures_collected: bool = True
        # Observability — populated by ``analyze``. Surfaced via
        # :class:`AnalysisManifest` on the SceneReport.
        self._analysis_started_at: float = 0.0
        self._analysis_duration_ms: int = 0
        self._shading_engine_count: int = 0
        self._file_node_count: int = 0

    @classmethod
    def run_audit(cls, adaptive: bool = False, verbose: bool = True) -> None:
        """
        Run a full scene audit and print the report.

        Args:
            adaptive: If True, use adaptive budgeting based on object size.
            verbose: If True, print the report to the script editor.
        """
        profile = AuditProfile(adaptive_tris=adaptive)
        if adaptive:
            profile.name = "Adaptive (Game Ready)"

        analyzer = cls()
        records = analyzer.analyze(profile=profile)
        report = analyzer.generate_report(records)

        if verbose:
            analyzer.print_report(report)


    @classmethod
    def _build_report(
        cls,
        adaptive: bool,
        objects: Optional[List[Any]] = None,
        progress_callback: Optional[Callable[[int, int, str], None]] = None,
        sections: Optional[List[str]] = None,
    ) -> Tuple["SceneAnalyzer", SceneReport]:
        """Run the analyze + generate_report pipeline once for the
        given audit settings, returning the (analyzer, report) pair.

        Shared by ``format_audit_text`` / ``format_audit_html`` so
        the heavy work only happens once per user-facing call
        regardless of which output shape they ask for. ``sections``
        is forwarded to ``analyze`` so the right collection phases
        are skipped.
        """
        profile = AuditProfile(adaptive_tris=adaptive)
        if adaptive:
            profile.name = "Adaptive (Game Ready)"

        analyzer = cls()
        records = analyzer.analyze(
            profile=profile,
            objects=objects,
            progress_callback=progress_callback,
            sections=sections,
        )
        report = analyzer.generate_report(records)
        return analyzer, report

    def _build_manifest(self, shape_count: int = 0) -> AnalysisManifest:
        """Snapshot of the most recent ``analyze`` run for the
        SceneReport. Pulls timing / counts from analyzer state and
        falls back to defaults when ``analyze`` was never called."""
        return AnalysisManifest(
            scope=self.scope,
            sections_requested=sorted(self.collected_sections),
            materials_collected=self.materials_collected,
            textures_collected=self.textures_collected,
            profile=self.profile if isinstance(self.profile, AuditProfile) else AuditProfile(),
            started_at=self._analysis_started_at,
            duration_ms=self._analysis_duration_ms,
            shape_count=shape_count,
            shading_engine_count=self._shading_engine_count,
            file_node_count=self._file_node_count,
        )

    @staticmethod
    def _capture_via_logger(
        analyzer: "SceneAnalyzer",
        formatter,
        render: Callable[[], None],
    ) -> str:
        """Run ``render()`` with the analyzer's logger redirected to
        an in-memory buffer using ``formatter``, then restore.

        Isolation: existing handlers are stashed, replaced by a
        single ``StreamHandler`` bound to a ``StringIO``, and
        propagation is disabled — otherwise the report would also
        appear in the script editor and any other handlers attached
        to this logger (e.g. tentacle's footer status log). Restored
        in ``finally`` so a raising renderer doesn't leave the logger
        in a broken state.
        """
        import io
        import logging as _logging

        buf = io.StringIO()
        handler = _logging.StreamHandler(buf)
        handler.setLevel(_logging.NOTSET)
        handler.setFormatter(formatter)

        existing_handlers = analyzer.logger.handlers[:]
        existing_propagate = analyzer.logger.propagate
        analyzer.logger.handlers = [handler]
        analyzer.logger.propagate = False
        try:
            render()
        finally:
            analyzer.logger.handlers = existing_handlers
            analyzer.logger.propagate = existing_propagate

        return buf.getvalue()

    @classmethod
    def format_audit_text(
        cls,
        adaptive: bool = False,
        objects: Optional[List[Any]] = None,
        sections: Optional[List[str]] = None,
    ) -> Dict[str, str]:
        """Run the audit and return the formatted report as a
        section-keyed dict of plain text.

        Sibling to :meth:`run_audit` — same analysis, captured into
        per-section strings instead of routed through the logger.
        Used by callers that want to display (or partially display)
        the report somewhere other than the script editor.

        Parameters:
            adaptive: Apply the Adaptive (Game Ready) profile.
            objects: Forwarded to :meth:`analyze`. ``None`` uses the
                current selection; pass an explicit list for
                whole-scene or custom-scope audits.
            sections: Iterable of ``SceneInfoSection`` keys. ``None``
                means "all sections" (prior default behavior).

        Returns:
            ``dict[str, str]`` keyed by section name (insertion order
            matches the requested ``sections``). A special
            ``"_header"`` entry contains the report title + profile
            block. Empty sections (e.g. the analyzer had nothing to
            say for "fix_first") are still present but map to an
            empty string, so callers can iterate the dict and trust
            it to mirror their request.

        HTML markup that the logger's level wrappers inject (color
        spans on NOTICE / WARNING / ERROR) is stripped via
        ``LevelAwareFormatter(strip_html=True)`` so the output is
        clean plain text.
        """
        from pythontk.core_utils.logging_mixin import LevelAwareFormatter

        selected = SceneInfoSection.normalize(sections)
        analyzer, report = cls._build_report(
            adaptive=adaptive,
            objects=objects,
            sections=selected,
        )
        formatter = LevelAwareFormatter(logger=analyzer.logger, strip_html=True)

        result: Dict[str, str] = {}
        result["_header"] = cls._capture_via_logger(
            analyzer, formatter, lambda: analyzer._render_header_section(report)
        )
        renderers = analyzer._section_renderers()
        for section in selected:
            renderer = renderers.get(section)
            if renderer is None:
                continue
            result[section] = cls._capture_via_logger(
                analyzer, formatter, lambda r=renderer: r(report)
            )
        return result

    @classmethod
    def format_audit_html(
        cls,
        adaptive: bool = False,
        objects: Optional[List[Any]] = None,
        progress_callback: Optional[Callable[[int, int, str], None]] = None,
        sections: Optional[List[str]] = None,
    ) -> Dict[str, str]:
        """Run the audit and return a section-keyed dict of HTML
        chunks suitable for concatenation into a viewer dialog.

        Preserves the logger's inline color spans (NOTICE lavender,
        WARNING pastel yellow, ERROR pastel pink, etc.) by capturing
        with a plain ``Formatter("%(message)s")`` — the level
        wrappers stay in the output — and embedding each section's
        captured text inside its own ``<pre>`` block. Per-section
        ``<pre>`` blocks render identically to one large block in a
        QTextDocument since the explicit ``margin:0`` collapses the
        block spacing.

        Parameters:
            adaptive: Apply the Adaptive (Game Ready) profile.
            objects: Forwarded to :meth:`analyze`.
            progress_callback: Forwarded to :meth:`analyze`.
            sections: Iterable of ``SceneInfoSection`` keys. ``None``
                means "all sections" (prior default behavior).

        Returns:
            ``dict[str, str]`` keyed by section name (insertion order
            matches the requested ``sections``). A special
            ``"_header"`` entry contains the ``<h2>`` title; the
            tentacle viewer joins values in iteration order.

        The body is **not** HTML-escaped because doing so would turn
        the wrapping spans into literal markup. Maya names containing
        a literal ``<`` could corrupt the rendering; same trade-off
        the existing TextEditLogHandler accepts when streaming logger
        output into a QTextEdit.
        """
        import logging as _logging

        selected = SceneInfoSection.normalize(sections)
        analyzer, report = cls._build_report(
            adaptive=adaptive,
            objects=objects,
            progress_callback=progress_callback,
            sections=selected,
        )
        formatter = _logging.Formatter("%(message)s")

        # Explicit ``font-family`` on the ``<pre>`` overrides any font
        # inherited from the outer ``RichTextFormatter.format`` wrapper (``<font>``
        # + ``<div align=...>``). ``margin:0`` keeps per-section <pre>
        # blocks visually stitched together — identical line spacing
        # to the previous single-block layout.
        pre_open = (
            "<pre style=\"font-family:'Consolas','Courier New',Monaco,monospace;"
            " color:#ddd; margin:0;\">"
        )
        pre_close = "</pre>"

        title = "Scene Audit Report — Adaptive" if adaptive else "Scene Audit Report"
        result: Dict[str, str] = {}
        result["_header"] = (
            f"<h2 style='color:#9cf; margin:0 0 6px 0;'>{title}</h2>"
        )

        renderers = analyzer._section_renderers()
        for section in selected:
            renderer = renderers.get(section)
            if renderer is None:
                continue
            captured = cls._capture_via_logger(
                analyzer, formatter, lambda r=renderer: r(report)
            )
            if not captured:
                # Section returned no content (e.g. fix_first when
                # nothing is over-budget). Keep the key for callers
                # that key off section identity, but skip the <pre>
                # so the dialog doesn't render an empty box.
                result[section] = ""
                continue
            result[section] = pre_open + captured + pre_close
        return result

    def analyze(
        self,
        objects: List[Any] = None,
        fast_mode: bool = True,
        progress_callback: Optional[Callable[[int, int, str], None]] = None,
        profile: AuditProfile = None,
        sections: Optional[List[str]] = None,
    ) -> List[AssetRecord]:
        """
        Main entry point for analysis.

        Args:
            objects: List of objects to analyze. If None, uses selection.
            fast_mode: If True, skips deep checks (not implemented yet, but reserved for future).
            progress_callback: Optional callback(current, total, message) for progress updates.
            profile: Target profile settings.
            sections: Iterable of ``SceneInfoSection`` keys controlling
                which report sections will be rendered. The analyzer
                uses the set to skip work the unselected sections don't
                need — most notably texture file IO and (when no
                section needs them) the material caches. ``None``
                means "all sections" — equivalent to the prior
                behavior.

        Returns:
            List of AssetRecord objects sorted by score (descending).
        """
        import time

        if profile is None:
            profile = AuditProfile()
        self.profile = profile
        self.scope = "selection" if objects is None else "custom"

        # Normalize via the canonical helper so a typo'd or
        # alternately-ordered list from a direct caller gets the same
        # filtering as the format_audit_* paths.
        selected_sections = set(SceneInfoSection.normalize(sections))
        needs_materials = bool(selected_sections & SceneInfoSection._NEEDS_MATERIALS)
        needs_textures = bool(selected_sections & SceneInfoSection._NEEDS_TEXTURES)
        # Recorded on the analyzer so generate_report / renderers can
        # hide texture/material lines that have no underlying data.
        self.collected_sections = selected_sections
        self.materials_collected = needs_materials
        self.textures_collected = needs_textures

        # Observability — reset per-run counters; the AnalysisManifest
        # on the eventual SceneReport reads these.
        self._analysis_started_at = time.time()
        self._shading_engine_count = 0
        self._file_node_count = 0
        _start_perf = time.perf_counter()

        # Phase A: Resolve targets — always fast (just node-name
        # normalization), so it gets a small fixed slice of the bar.
        # The Phase B/C split is computed AFTER Phase A so it can use
        # the true shape count instead of an estimate.
        PHASE_A_END = 5
        if progress_callback:
            progress_callback(0, 100, "Resolving targets...")

        # shape -> list of transform names
        shape_map = self._resolve_targets(
            objects,
            progress_callback=progress_callback,
            pct_start=0,
            pct_end=PHASE_A_END,
        )
        if not shape_map:
            self._analysis_duration_ms = int(
                (time.perf_counter() - _start_perf) * 1000
            )
            return []

        shapes = list(shape_map.keys())
        total_shapes = len(shapes)

        # Weight the Phase B/C split by item counts so the bar tracks
        # wall-clock progress instead of the old 10/10/80 layout. Phase
        # B walks ALL scene shading engines (not just the selection's)
        # and does one ``_analyze_material_node`` per SE — each
        # potentially calling ``os.path.getsize`` per file node — so
        # for selection-scope audits Phase B is usually the bottleneck.
        # Item-count weighting fixes the "bar at ~15% when the work is
        # at ~90%" symptom of the fixed split.
        phase_b_count = (
            len(cmds.ls(type="shadingEngine") or []) if needs_materials else 0
        )
        phase_b_end = self._phase_b_end(PHASE_A_END, phase_b_count, total_shapes)

        # Phase B: Bulk collect material data (skip entirely when no
        # selected section needs slot / transparency / texture data).
        if needs_materials:
            if progress_callback:
                progress_callback(
                    PHASE_A_END, 100, "Collecting material data..."
                )
            self._build_material_caches(
                shape_map,
                progress_callback=progress_callback,
                pct_start=PHASE_A_END,
                pct_end=phase_b_end,
                collect_textures=needs_textures,
            )
        else:
            # Make sure stale caches from a prior run don't leak into
            # this analyze pass.
            self._shading_map.clear()
            self._material_map.clear()
            self._material_flags.clear()
            self._global_texture_usage.clear()
            # No Phase B work happened — give its bar range back to C.
            phase_b_end = PHASE_A_END

        # Phase C: Analyze and Score
        records = []
        phase_c_span = max(1, 100 - phase_b_end)
        for i, shape in enumerate(shapes):
            if progress_callback:
                pct = phase_b_end + int((i / total_shapes) * phase_c_span)
                progress_callback(pct, 100, f"Analyzing {shape}")

            mesh_rec = self._analyze_mesh(shape)
            mat_rec = (
                self._analyze_material(shape)
                if needs_materials
                else MaterialRecord(slot_count=0, uses_transparency=False, materials=[])
            )

            # Calculate score and findings
            (
                score,
                perf_score,
                risk_score,
                findings,
                breakdown,
                delta,
                fix_plan,
                target_tris,
            ) = self._calculate_score(mesh_rec, mat_rec)

            # Get transforms and instance count
            transforms = shape_map[shape]
            instance_count = len(transforms)
            # Use the first transform as the representative name
            transform_name = transforms[0] if transforms else "Unknown"

            records.append(
                AssetRecord(
                    transform=transform_name,
                    mesh=mesh_rec,
                    material=mat_rec,
                    score=score,
                    perf_score=perf_score,
                    risk_score=risk_score,
                    findings=findings,
                    score_breakdown=breakdown,
                    instance_count=instance_count,
                    delta=delta,
                    fix_plan=fix_plan,
                    target_tris=target_tris,
                )
            )

        # Post-process: Calculate tri_percent
        total_tris = sum(r.mesh.tris * r.instance_count for r in records)
        if total_tris > 0:
            for r in records:
                r.tri_percent = ((r.mesh.tris * r.instance_count) / total_tris) * 100.0

        # Sort by score descending
        records.sort(key=lambda x: x.score, reverse=True)

        # Final tick — the per-shape loop ends at ~99% (i = N-1).
        # Without this the bar visually stalls just shy of full before
        # the context manager hides it.
        if progress_callback:
            progress_callback(100, 100, "Done")

        self._analysis_duration_ms = int((time.perf_counter() - _start_perf) * 1000)
        return records

    def generate_report(self, records: List[AssetRecord]) -> SceneReport:
        """Build a :class:`SceneReport` from per-asset records.

        The intermediate computation phases (texture aggregates,
        Pareto rankings, missing-texture impact, fix actions) are the
        same as before; the difference is in the *shape* of the
        return — typed sub-records instead of a 70-field bag.
        """
        manifest = self._build_manifest()
        if not records:
            return SceneReport(manifest=manifest)

        total_meshes = len(records)
        # Note: records are per unique shape. We must multiply by instance_count for scene totals.
        total_tris = sum(r.mesh.tris * r.instance_count for r in records)
        total_verts = sum(r.mesh.verts * r.instance_count for r in records)
        total_slots = sum(r.material.slot_count * r.instance_count for r in records)
        max_slots = max((r.material.slot_count for r in records), default=0)
        avg_slots = (
            total_slots / sum(r.instance_count for r in records)
            if total_meshes > 0
            else 0
        )

        multi_slot_meshes = sum(1 for r in records if r.material.slot_count > 1)
        meshes_over_slot_threshold = sum(
            1 for r in records if r.material.slot_count > self.profile.max_slots
        )
        meshes_over_tri_threshold = sum(
            1 for r in records if r.mesh.tris > r.target_tris
        )
        total_slots_over_budget = sum(
            max(0, r.material.slot_count - self.profile.max_slots) * r.instance_count
            for r in records
        )

        transparent_meshes = sum(1 for r in records if r.material.uses_transparency)

        non_manifold_count = sum(1 for r in records if r.mesh.non_manifold_edges > 0)
        lamina_count = sum(1 for r in records if r.mesh.lamina_faces > 0)
        ngon_count = sum(1 for r in records if r.mesh.ngons > 0)
        high_poly_count = sum(1 for r in records if r.mesh.tris > r.target_tris)

        # Texture stats
        # total_tex_size_mb is unique disk size per material.
        # Summing it across all records might double count if materials are shared across shapes.
        # But MaterialRecord is per shape.
        # We should use global texture usage for scene total.
        # But we don't have file sizes in global usage map easily available here unless we iterate.
        # Let's approximate by summing unique textures in records, but we need to be careful.
        # Actually, we can just iterate over _global_texture_usage keys and get size?
        # No, we don't have size in that map.
        # Let's use the sum of records but acknowledge it might be slightly off if materials are shared across shapes.
        # Wait, MaterialRecord calculates `total_tex_size_mb` based on unique textures for that shape.
        # If Shape A and Shape B use Mat X, they both count the textures.
        # So summing them is wrong for "Unique Scene Total".
        # We need a set of all unique paths encountered in records.
        # But we don't have paths in MaterialRecord, only stats.
        # We can't accurately calculate "Unique Scene Texture Size" from records alone without paths.
        # However, we can use `est_gpu_mb` which is per-instance (resident).
        # Actually, GPU memory is also unique (resources are shared).
        # So we really need to know the set of all textures in the scene.
        # We can rebuild it from `_material_flags` since we have `_material_map`.

        unique_paths = set()
        total_texture_mb = 0.0
        est_gpu_mb = 0.0
        est_gpu_mb_compressed = 0.0
        texture_type_breakdown = {}
        texture_class_estimates = {}  # Class -> Compressed MB
        texture_dim_histogram = {"4k+": 0, "2k": 0, "1k": 0, "512": 0, "<512": 0}
        shared_4k_textures = []
        single_use_4k_count = 0
        shared_4k_count = 0

        # Re-scan materials used by these records
        used_materials = set()
        for r in records:
            used_materials.update(r.material.materials)

        # Use global texture usage for accurate scene-wide stats
        # But filter by what's actually used in the analyzed records if scope is Selection
        # If scope is Selection, we only care about textures used by selected objects.
        # _global_texture_usage contains everything found during _build_material_caches.
        # _build_material_caches was built from shape_map (selection).
        # So _global_texture_usage should be correct for the scope.

        for path, usage_data in self._global_texture_usage.items():
            unique_paths.add(path)

        processed_paths = set()

        for mat_name in used_materials:
            flags = self._material_flags.get(mat_name, {})
            textures = flags.get("textures", [])
            for t in textures:
                path = t["path"]
                if path not in processed_paths:
                    processed_paths.add(path)

                    size_mb = t["size_mb"]
                    total_texture_mb += size_mb

                    w, h = t["res"]
                    # Uncompressed: RGBA8 (4 bytes) + Mips (1.33x)
                    est_gpu_mb += (w * h * 4 * 1.33) / (1024 * 1024)

                    # Compressed Estimate per Class
                    tex_type = t.get("type", "Other")
                    has_alpha = t.get("has_alpha", False)

                    bpp = 1.0  # Default BC7/BC5
                    if tex_type == "BaseColor":
                        bpp = 1.0 if has_alpha else 0.5  # BC7 vs BC1
                    elif tex_type == "Normal":
                        bpp = 1.0  # BC5
                    elif tex_type == "Masks":
                        bpp = 0.5  # BC1/BC4
                    elif tex_type == "Emissive":
                        bpp = 0.5  # BC1

                    comp_size = (w * h * bpp * 1.33) / (1024 * 1024)
                    est_gpu_mb_compressed += comp_size

                    texture_class_estimates[tex_type] = (
                        texture_class_estimates.get(tex_type, 0.0) + comp_size
                    )

                    texture_type_breakdown[tex_type] = (
                        texture_type_breakdown.get(tex_type, 0.0) + size_mb
                    )

                    # Histogram & 4K Analysis
                    max_dim = max(w, h)
                    if max_dim >= 4096:
                        texture_dim_histogram["4k+"] += 1
                        # Check usage
                        usage = self._global_texture_usage.get(path, {})
                        count = usage.get("count", 0) if isinstance(usage, dict) else 0
                        # Note: count in global_texture_usage is "used by X materials * objects"
                        # We want "used by X meshes" or "used by X materials"
                        # usage["meshes"] is a set of mesh names
                        mesh_count = len(usage.get("meshes", []))

                        if mesh_count > 1:
                            shared_4k_count += 1
                            shared_4k_textures.append((path, mesh_count))
                        else:
                            single_use_4k_count += 1

                    elif max_dim >= 2048:
                        texture_dim_histogram["2k"] += 1
                    elif max_dim >= 1024:
                        texture_dim_histogram["1k"] += 1
                    elif max_dim >= 512:
                        texture_dim_histogram["512"] += 1
                    else:
                        texture_dim_histogram["<512"] += 1

        # Sort shared 4K textures
        shared_4k_textures.sort(key=lambda x: x[1], reverse=True)
        shared_4k_textures = shared_4k_textures[:5]  # Top 5

        max_texture_res = max((r.material.max_res for r in records), default=0)
        large_texture_count = sum(1 for r in records if r.material.max_res > 2048)
        unique_texture_paths = len(unique_paths)

        # Top offenders (already sorted by score in analyze)
        top_offenders = records[:20]

        # Category offenders
        top_by_tris = sorted(records, key=lambda x: x.mesh.tris, reverse=True)[:10]
        top_by_slots = sorted(
            records, key=lambda x: x.material.slot_count, reverse=True
        )[:10]
        top_by_max_res = sorted(
            records, key=lambda x: x.material.max_res, reverse=True
        )[:10]
        top_by_risk = sorted(records, key=lambda x: x.risk_score, reverse=True)[:10]

        # Heaviest Textures (Files)
        heaviest_textures_list = []
        for path in processed_paths:
            # Find details again (inefficient but safe)
            # We could have cached it above.
            found = False
            for mat_name in used_materials:
                flags = self._material_flags.get(mat_name, {})
                textures = flags.get("textures", [])
                for t in textures:
                    if t["path"] == path:
                        usage = self._global_texture_usage.get(
                            path, {"count": 0, "meshes": set(), "instances": 0}
                        )
                        # Find materials using this texture
                        # We don't have a reverse map path->materials easily.
                        # But we can iterate used_materials.
                        mats_using = []
                        for m in used_materials:
                            m_flags = self._material_flags.get(m, {})
                            if any(
                                tx["path"] == path for tx in m_flags.get("textures", [])
                            ):
                                mats_using.append(m)

                        heaviest_textures_list.append(
                            (
                                path,
                                t["size_mb"],
                                t["res"],
                                len(mats_using),
                                mats_using,
                                len(usage["meshes"]),
                                usage["instances"],
                            )
                        )
                        found = True
                        break
                if found:
                    break

        # Sort by size descending
        heaviest_textures_list.sort(key=lambda x: x[1], reverse=True)

        # Transparency
        transparent_recs = [r for r in records if r.material.uses_transparency]
        top_by_transparency = sorted(
            transparent_recs, key=lambda x: x.score, reverse=True
        )[:10]

        # Top by Effective Score (was the duplicate
        # ``top_repeated_offenders`` / ``top_by_effective_score`` pair).
        top_by_effective_score = sorted(
            records, key=lambda x: x.score * max(1, x.instance_count), reverse=True
        )[:10]

        # Top Materials
        mat_usage = {}
        for r in records:
            for mat in r.material.materials:
                mat_usage[mat] = mat_usage.get(mat, 0) + max(1, r.instance_count)
        top_materials = sorted(mat_usage.items(), key=lambda x: x[1], reverse=True)[:10]

        # Top Savings
        # Draw Calls: (slots - 1) * instances
        savings_draw_calls_candidates = [
            r for r in records if r.material.slot_count > 1
        ]
        top_savings_draw_calls = sorted(
            savings_draw_calls_candidates,
            key=lambda x: (x.material.slot_count - 1) * x.instance_count,
            reverse=True,
        )[:5]
        savings_draw_calls_total = sum(
            (r.material.slot_count - 1) * r.instance_count
            for r in savings_draw_calls_candidates
        )

        # Savings to Budget (slots - budget) * instances
        savings_draw_calls_budget = sum(
            max(0, r.material.slot_count - self.profile.max_slots) * r.instance_count
            for r in records
        )

        # Tris: (tris - budget) * instances
        savings_tris_candidates = [r for r in records if r.mesh.tris > r.target_tris]
        top_savings_tris = sorted(
            savings_tris_candidates,
            key=lambda x: (x.mesh.tris - x.target_tris) * x.instance_count,
            reverse=True,
        )[:5]
        savings_tris_total = sum(
            (r.mesh.tris - r.target_tris) * r.instance_count
            for r in savings_tris_candidates
        )
        savings_tris_budget = (
            savings_tris_total  # Same logic as total since budget is the baseline
        )

        # Missing Textures
        missing_map = {}  # path -> set(mat_names)
        for mat_name in used_materials:
            flags = self._material_flags.get(mat_name, {})
            paths = flags.get("missing_paths", [])
            for p in paths:
                if p not in missing_map:
                    missing_map[p] = set()
                missing_map[p].add(mat_name)

        missing_textures_list = []
        missing_textures_project = []
        missing_textures_presets = []

        for p, mats in missing_map.items():
            entry = (p, len(mats), list(mats))
            missing_textures_list.append(entry)

            # Categorize
            lower_p = p.lower().replace("\\", "/")
            if "program files" in lower_p or "maya" in lower_p and "presets" in lower_p:
                missing_textures_presets.append(entry)
            else:
                missing_textures_project.append(entry)

        missing_textures_list.sort(key=lambda x: x[1], reverse=True)
        missing_textures_project.sort(key=lambda x: x[1], reverse=True)
        missing_textures_presets.sort(key=lambda x: x[1], reverse=True)

        # Instance Stats
        instance_stats = {
            "unique_meshes": len(records),
            "instanced_shapes": sum(1 for r in records if r.instance_count > 1),
            "total_instances": sum(r.instance_count for r in records),
        }

        # Budget Compliance Distribution
        budget_compliance_dist = {
            "tris": {"0-10%": 0, "10-50%": 0, "50%+": 0},
            "slots": {"1-2": 0, "3-5": 0, "6+": 0},
        }

        for r in records:
            # Tris
            if r.mesh.tris > r.target_tris:
                overage = (r.mesh.tris - r.target_tris) / r.target_tris
                if overage <= 0.1:
                    budget_compliance_dist["tris"]["0-10%"] += 1
                elif overage <= 0.5:
                    budget_compliance_dist["tris"]["10-50%"] += 1
                else:
                    budget_compliance_dist["tris"]["50%+"] += 1

            # Slots
            slots = r.material.slot_count
            if slots > self.profile.max_slots:
                overage = slots - self.profile.max_slots
                if overage <= 2:
                    budget_compliance_dist["slots"]["1-2"] += 1
                elif overage <= 5:
                    budget_compliance_dist["slots"]["3-5"] += 1
                else:
                    budget_compliance_dist["slots"]["6+"] += 1

        # Scene Compliance
        # For adaptive, we sum the individual targets
        total_target_tris = sum(r.target_tris * r.instance_count for r in records)
        scene_compliance = {
            "tris": (
                (total_tris / total_target_tris * 100.0)
                if total_target_tris > 0
                else 0.0
            ),
            "slots": (
                (total_slots / (total_meshes * self.profile.max_slots) * 100.0)
                if total_meshes > 0
                else 0.0
            ),
        }

        # Scene-level fix actions (high-priority "do these next"
        # items). Was ``fix_first_items: List[str]`` plus a dead
        # scene-level ``fix_plan: List[str]``; renderer only used the
        # former. Now consolidated to a single structured list.
        scene_fix_actions: List[FixAction] = []

        # 1. Top Offenders by Effective Score
        effective_offenders = sorted(
            records, key=lambda x: x.score * max(1, x.instance_count), reverse=True
        )
        for r in effective_offenders[:3]:
            if r.score > 10:
                reason_kind = "general"
                reason = "General Issues"
                if r.findings:
                    first = r.findings[0]
                    reason_kind = first.kind
                    if first.kind == "high_poly":
                        reason = "High Poly"
                    elif first.kind == "draw_call_split":
                        reason = "High Slots"
                    elif first.kind in {
                        "max_tex_dim",
                        "oversized_texture",
                        "heavy_textures",
                    }:
                        reason = "Heavy Textures"
                    else:
                        reason = first.message

                scene_fix_actions.append(
                    FixAction(
                        severity=SEVERITY_HIGH,
                        kind="fix_offender",
                        message=(
                            f"Fix {r.transform}: {reason} "
                            f"(Score {r.score:.0f} x {r.instance_count} instances)"
                        ),
                        target=r.transform,
                        detail={
                            "score": r.score,
                            "instances": r.instance_count,
                            "primary_finding": reason_kind,
                        },
                    )
                )

        # 2. Missing Textures
        if missing_textures_project:
            scene_fix_actions.append(
                FixAction(
                    severity=SEVERITY_HIGH,
                    kind="relink_textures",
                    message=f"Relink {len(missing_textures_project)} missing project textures",
                    detail={"count": len(missing_textures_project)},
                )
            )

        # 3. Slot Reduction
        if savings_draw_calls_budget > 0:
            scene_fix_actions.append(
                FixAction(
                    severity=SEVERITY_HIGH,
                    kind="reduce_slots_scene",
                    message=f"Reduce material slots by {savings_draw_calls_budget} to reach budget",
                    detail={"slots_to_reduce": savings_draw_calls_budget},
                )
            )

        # 4. High Poly
        high_poly_overage = sum(max(0, r.mesh.tris - r.target_tris) for r in records)
        if high_poly_overage > 100000:
            scene_fix_actions.append(
                FixAction(
                    severity=SEVERITY_HIGH,
                    kind="decimate_scene",
                    message=f"Decimate meshes to save {high_poly_overage:,} triangles total",
                    detail={"tris_to_save": high_poly_overage},
                )
            )

        # Pipeline Integrity
        pipeline_integrity = []
        if missing_textures_project:
            pipeline_integrity.append(
                f"{len(missing_textures_project)} missing project files"
            )
        if missing_textures_presets:
            pipeline_integrity.append(
                f"{len(missing_textures_presets)} missing preset files (low priority)"
            )

        # --- New Calculations ---
        raw_total_tris = sum(r.mesh.tris for r in records)
        raw_total_verts = sum(r.mesh.verts for r in records)
        raw_total_slots = sum(r.material.slot_count for r in records)

        # Slot Stats (Per Unique Mesh)
        slot_counts = sorted([r.material.slot_count for r in records])
        if slot_counts:
            import math

            median_slots = slot_counts[len(slot_counts) // 2]
            p90_index = int(len(slot_counts) * 0.9)
            p90_slots = slot_counts[p90_index]
        else:
            median_slots = 0
            p90_slots = 0

        slot_stats = {
            "avg": avg_slots,  # This is weighted avg in current code? No, avg_slots = total_slots / total_instances.
            # Wait, avg_slots calculation in current code:
            # avg_slots = total_slots / sum(r.instance_count)
            # This is "Avg Slots per Instance".
            # User asked for "Material Slots (per-mesh)".
            # Let's provide per-unique-mesh stats for the breakdown.
            "avg_unique": sum(slot_counts) / len(slot_counts) if slot_counts else 0,
            "median": median_slots,
            "p90": p90_slots,
            "max": max_slots,
        }

        # Pareto View (Tris) — structured ParetoEntry rows; the
        # renderer formats the display string from these.
        sorted_by_eff_tris = sorted(
            records, key=lambda r: r.mesh.tris * r.instance_count, reverse=True
        )
        pareto_tris: List[ParetoEntry] = []
        running_tris = 0
        for r in sorted_by_eff_tris[:10]:
            eff_tris = r.mesh.tris * r.instance_count
            running_tris += eff_tris
            pct = (running_tris / total_tris * 100.0) if total_tris > 0 else 0.0
            pareto_tris.append(
                ParetoEntry(target=r.transform, value=eff_tris, cum_percent=pct)
            )

        # Pareto View (Slots)
        sorted_by_eff_slots = sorted(
            records,
            key=lambda r: r.material.slot_count * r.instance_count,
            reverse=True,
        )
        pareto_slots: List[ParetoEntry] = []
        running_slots = 0
        for r in sorted_by_eff_slots[:10]:
            eff_slots = r.material.slot_count * r.instance_count
            running_slots += eff_slots
            pct = (running_slots / total_slots * 100.0) if total_slots > 0 else 0.0
            pareto_slots.append(
                ParetoEntry(target=r.transform, value=eff_slots, cum_percent=pct)
            )

        # ``pareto_texture_mb`` and ``top_wins_by_type`` removed —
        # both were computed but never rendered, and the structured
        # data they would have surfaced (heaviest textures, scene
        # savings) is already on TextureStats / BudgetStats.

        # Scene Health Flags
        scene_health_flags = []
        if total_tris > 10000000:
            scene_health_flags.append("Extreme Poly Count (>10M)")
        if total_slots > 5000:
            scene_health_flags.append("High Draw Call Count (>5k)")
        if unique_texture_paths > 500:
            scene_health_flags.append("Many Unique Textures (>500)")

        # Materials Causing Splits
        mat_mesh_counts = {}
        for r in records:
            for m in r.material.materials:
                if m not in mat_mesh_counts:
                    mat_mesh_counts[m] = {"unique": 0, "over_budget": 0, "slots": []}
                mat_mesh_counts[m]["unique"] += 1
                mat_mesh_counts[m]["slots"].append(r.material.slot_count)
                if r.material.slot_count > self.profile.max_slots:
                    mat_mesh_counts[m]["over_budget"] += 1

        materials_causing_splits: List[MaterialSplit] = []
        for m, data in mat_mesh_counts.items():
            avg_slots_for_mat = sum(data["slots"]) / len(data["slots"])
            # Filter: avg_slots >= 4 or significant over-budget meshes
            if avg_slots_for_mat >= 4 or data["over_budget"] > 5:
                materials_causing_splits.append(
                    MaterialSplit(
                        material=m,
                        unique_mesh_count=data["unique"],
                        over_budget_count=data["over_budget"],
                        avg_slots=avg_slots_for_mat,
                    )
                )

        materials_causing_splits.sort(
            key=lambda s: s.unique_mesh_count, reverse=True
        )
        materials_causing_splits = materials_causing_splits[:5]

        # Missing Texture Impact — structured record (was a
        # ``Dict[str, Any]`` with set values that bled out of the
        # dataclass type system).
        impact_materials: Set[str] = set()
        impact_meshes: Set[str] = set()
        impact_offenders: List[str] = []
        if missing_textures_list:
            missing_paths_set = {p[0] for p in missing_textures_list}
            for r in records:
                rec_missing = False
                for m in r.material.materials:
                    flags = self._material_flags.get(m, {})
                    m_missing = flags.get("missing_paths", [])
                    if any(p in missing_paths_set for p in m_missing):
                        rec_missing = True
                        impact_materials.add(m)

                if rec_missing:
                    impact_meshes.add(r.transform)
                    impact_offenders.append(r.transform)

        missing_texture_impact = MissingTextureImpact(
            affected_meshes=sorted(impact_meshes),
            affected_materials=sorted(impact_materials),
            top_offenders=impact_offenders[:5],
        )

        meshes_with_transparency = sum(
            1 for r in records if r.material.uses_transparency
        )
        meshes_with_extra_uvs = sum(1 for r in records if r.mesh.uv_sets > 1)
        meshes_with_high_slots = sum(1 for r in records if r.material.slot_count > 1)

        # ``selection_coverage`` removed — duplicated values already
        # present on InstanceStats (``total_instances`` /
        # ``unique_meshes``).

        # --- Pack the legacy positional tuples into typed records ---

        def _to_missing(entries: List[Tuple[str, int, List[str]]]) -> List[MissingTexture]:
            return [
                MissingTexture(path=p, material_count=c, materials=list(mats))
                for (p, c, mats) in entries
            ]

        missing_project_records = _to_missing(missing_textures_project)
        missing_presets_records = _to_missing(missing_textures_presets)

        shared_4k_records = [
            SharedTexture(path=p, mesh_count=c) for (p, c) in shared_4k_textures
        ]

        heaviest_records: List[TextureFile] = []
        for (
            path,
            size_mb,
            res,
            mat_count,
            mats,
            mesh_count,
            inst_count,
        ) in heaviest_textures_list:
            width, height = res if isinstance(res, tuple) else (0, 0)
            heaviest_records.append(
                TextureFile(
                    path=path,
                    size_mb=float(size_mb),
                    width=int(width),
                    height=int(height),
                    material_count=int(mat_count),
                    materials=list(mats),
                    mesh_count=int(mesh_count),
                    instance_count=int(inst_count),
                )
            )

        slot_stats_record: Optional[SlotStats]
        if slot_counts:
            slot_stats_record = SlotStats(
                avg=avg_slots,
                avg_unique=sum(slot_counts) / len(slot_counts),
                median=int(median_slots),
                p90=int(p90_slots),
                max=int(max_slots),
            )
        else:
            slot_stats_record = None

        # --- Compose typed sub-records ---

        manifest = self._build_manifest(shape_count=total_meshes)

        summary = SummaryStats(
            total_meshes=total_meshes,
            total_tris=total_tris,
            total_verts=total_verts,
            raw_total_tris=raw_total_tris,
            instance_stats=InstanceStats(**instance_stats),
            scene_health_flags=scene_health_flags,
            multi_slot_meshes=multi_slot_meshes,
            transparent_meshes=transparent_meshes,
            non_manifold_count=non_manifold_count,
            lamina_count=lamina_count,
            ngon_count=ngon_count,
            high_poly_count=high_poly_count,
            meshes_with_transparency=meshes_with_transparency,
            meshes_with_extra_uvs=meshes_with_extra_uvs,
            meshes_with_high_slots=meshes_with_high_slots,
        )

        budget = BudgetStats(
            total_target_tris=total_target_tris,
            total_slots=total_slots,
            meshes_over_tri_threshold=meshes_over_tri_threshold,
            meshes_over_slot_threshold=meshes_over_slot_threshold,
            total_slots_over_budget=total_slots_over_budget,
            savings_draw_calls_total=savings_draw_calls_total,
            savings_tris_total=savings_tris_total,
            savings_draw_calls_budget=savings_draw_calls_budget,
            savings_tris_budget=savings_tris_budget,
            slot_stats=slot_stats_record,
            compliance=ComplianceStats(
                tris_pct=scene_compliance["tris"],
                slots_pct=scene_compliance["slots"],
            ),
            buckets=BudgetBuckets(
                tris=budget_compliance_dist["tris"],
                slots=budget_compliance_dist["slots"],
            ),
        )

        textures = TextureStats(
            total_size_mb=total_texture_mb,
            est_gpu_mb=est_gpu_mb,
            est_gpu_mb_compressed=est_gpu_mb_compressed,
            max_resolution=max_texture_res,
            large_texture_count=large_texture_count,
            unique_paths=unique_texture_paths,
            dim_histogram=texture_dim_histogram,
            type_breakdown=texture_type_breakdown,
            class_estimates=texture_class_estimates,
            shared_4k=shared_4k_records,
            single_use_4k_count=single_use_4k_count,
            shared_4k_count=shared_4k_count,
            heaviest=heaviest_records,
        )

        pipeline = PipelineStats(
            integrity_warnings=pipeline_integrity,
            missing_project=missing_project_records,
            missing_presets=missing_presets_records,
            impact=missing_texture_impact,
        )

        offenders = OffenderLists(
            by_score=top_offenders,
            by_tris=top_by_tris,
            by_slots=top_by_slots,
            by_max_res=top_by_max_res,
            by_risk=top_by_risk,
            by_transparency=top_by_transparency,
            by_effective_score=top_by_effective_score,
            top_materials=top_materials,
            savings_draw_calls=top_savings_draw_calls,
            savings_tris=top_savings_tris,
            pareto_tris=pareto_tris,
            pareto_slots=pareto_slots,
            materials_causing_splits=materials_causing_splits,
        )

        return SceneReport(
            manifest=manifest,
            summary=summary,
            budget=budget,
            textures=textures,
            pipeline=pipeline,
            offenders=offenders,
            fix_actions=scene_fix_actions,
            assets=records,
        )

    @staticmethod
    def _phase_b_end(
        phase_a_end: int,
        phase_b_count: int,
        phase_c_count: int,
    ) -> int:
        """Split the post–Phase-A bar range between B and C by item
        ratio. Returns the cumulative bar position where Phase B ends
        and Phase C begins (i.e. ``pct_end`` for the
        :meth:`_build_material_caches` slice).
        """
        remaining = 100 - phase_a_end
        total_bc = phase_b_count + phase_c_count
        if total_bc <= 0:
            return phase_a_end
        phase_b_weight = int(remaining * phase_b_count / total_bc)
        return phase_a_end + phase_b_weight

    def _resolve_targets(
        self,
        objects: Optional[List[Any]],
        progress_callback: Optional[Callable[[int, int, str], None]] = None,
        pct_start: int = 0,
        pct_end: int = 10,
    ) -> Dict[str, List[str]]:
        """Resolves inputs to a map of {mesh_shape_path: [transform_paths]}.

        ``progress_callback`` (when supplied) ticks across the
        [``pct_start``, ``pct_end``) range during the normalized-input
        walk — heavy for whole-scene scopes with thousands of nodes.
        """
        if objects is None:
            objects = cmds.ls(selection=True, long=True) or []
            if not objects:
                return {}

        shape_map: Dict[str, List[str]] = {}  # shape full path -> list of transform paths

        def _shape_of(transform: str) -> Optional[str]:
            shapes = (
                cmds.listRelatives(
                    transform, shapes=True, fullPath=True, noIntermediate=True
                )
                or []
            )
            return shapes[0] if shapes else None

        def _parent_of(node: str) -> Optional[str]:
            parents = cmds.listRelatives(node, parent=True, fullPath=True) or []
            return parents[0] if parents else None

        def add_shape(shape: str, transform: str):
            try:
                if cmds.getAttr(f"{shape}.intermediateObject"):
                    return
            except Exception:
                pass
            if shape not in shape_map:
                shape_map[shape] = []
            shape_map[shape].append(transform)

        # Normalize inputs to long-name strings
        normalized: List[str] = []
        for obj in objects:
            name = str(obj)
            if not cmds.objExists(name):
                continue
            longs = cmds.ls(name, long=True) or []
            if longs:
                normalized.extend(longs)
            else:
                normalized.append(name)

        total_norm = len(normalized)
        span = max(1, pct_end - pct_start)
        for obj_idx, obj in enumerate(normalized):
            if progress_callback and total_norm:
                pct = pct_start + int((obj_idx / total_norm) * span)
                progress_callback(
                    pct, 100, f"Resolving targets ({obj_idx + 1}/{total_norm})"
                )
            try:
                node_type = cmds.nodeType(obj)
            except Exception:
                continue

            if node_type == "transform":
                s = _shape_of(obj)
                if s and cmds.objectType(s) == "mesh":
                    add_shape(s, obj)
                else:
                    # Group: collect descendant mesh shapes
                    descendants = (
                        cmds.listRelatives(
                            obj, allDescendents=True, type="mesh", fullPath=True
                        )
                        or []
                    )
                    for ds in descendants:
                        parent = _parent_of(ds)
                        if parent:
                            add_shape(ds, parent)

            elif node_type == "mesh":
                parent = _parent_of(obj)
                if parent:
                    add_shape(obj, parent)

            elif node_type == "objectSet":
                members = cmds.sets(obj, q=True) or []
                # Flatten nested sets / components down to leaf nodes
                flat = cmds.ls(members, long=True, flatten=True) or []
                for m in flat:
                    # Strip component suffix if present
                    node = m.split(".")[0]
                    if not cmds.objExists(node):
                        continue
                    nt = cmds.nodeType(node)
                    if nt == "transform":
                        s = _shape_of(node)
                        if s and cmds.objectType(s) == "mesh":
                            add_shape(s, node)
                    elif nt == "mesh":
                        parent = _parent_of(node)
                        if parent:
                            add_shape(node, parent)

        return shape_map

    def _build_material_caches(
        self,
        shape_map: Dict[str, List[str]],
        progress_callback: Optional[Callable[[int, int, str], None]] = None,
        pct_start: int = 10,
        pct_end: int = 20,
        collect_textures: bool = True,
    ):
        """
        Builds shared caches for material lookups to avoid per-object graph walks.
        Inverts the relationship: Iterates Shading Engines -> Members.

        ``progress_callback`` (when supplied) ticks per shading-engine
        across the [``pct_start``, ``pct_end``) range so the footer bar
        advances during this otherwise-static phase.

        ``collect_textures`` is forwarded to ``_analyze_material_node``
        so a sections-filtered run can skip file-IO when none of the
        requested sections depend on texture data.
        """
        self._shading_map.clear()
        self._material_map.clear()
        self._material_flags.clear()
        self._global_texture_usage.clear()

        shading_engines = cmds.ls(type="shadingEngine") or []
        # Observability — count SEs walked for the AnalysisManifest.
        self._shading_engine_count = len(shading_engines)

        # target_shapes uses full DAG paths; build a leaf-name lookup for matching SE members
        target_shapes = set(shape_map.keys())
        target_leaf_to_full = {s.split("|")[-1]: s for s in target_shapes}

        total_ses = len(shading_engines)
        span = max(1, pct_end - pct_start)
        for se_idx, se in enumerate(shading_engines):
            if progress_callback and total_ses:
                pct = pct_start + int((se_idx / total_ses) * span)
                progress_callback(
                    pct, 100, f"Collecting material data ({se_idx + 1}/{total_ses})"
                )
            members = cmds.sets(se, q=True) or []
            if not members:
                continue

            se_name = se

            # Find the surface shader
            surface_shader = (
                cmds.listConnections(
                    f"{se}.surfaceShader", source=True, destination=False
                )
                or []
            )
            mat_name = surface_shader[0] if surface_shader else "lambert1"
            self._material_map[se_name] = mat_name

            # Cache material flags if not done
            if mat_name not in self._material_flags:
                self._material_flags[mat_name] = self._analyze_material_node(
                    surface_shader[0] if surface_shader else None,
                    collect_textures=collect_textures,
                )

            mat_textures = self._material_flags[mat_name].get("textures", [])

            se_objects = set()
            se_instance_count = 0

            # Flatten components / nested sets to leaf nodes
            flat_members = cmds.ls(members, long=True, flatten=True) or []

            for member in flat_members:
                # Strip component suffix
                node = member.split(".")[0]
                if not cmds.objExists(node):
                    continue

                node_type = cmds.nodeType(node)
                if node_type == "transform":
                    shapes = (
                        cmds.listRelatives(
                            node, shapes=True, fullPath=True, noIntermediate=True
                        )
                        or []
                    )
                    if shapes:
                        node = shapes[0]
                        node_type = cmds.nodeType(node)

                if node_type != "mesh":
                    continue

                # Resolve to canonical full path so equality works against shape_map keys
                full_paths = cmds.ls(node, long=True) or []
                if not full_paths:
                    continue
                node_full = full_paths[0]
                node_leaf = node_full.split("|")[-1]
                se_objects.add(node_full)

                instances = 1
                if node_full in shape_map:
                    instances = len(shape_map[node_full])
                elif node_leaf in target_leaf_to_full:
                    instances = len(shape_map[target_leaf_to_full[node_leaf]])

                se_instance_count += instances

                if node_full in target_shapes or node_leaf in target_leaf_to_full:
                    key = (
                        node_full
                        if node_full in target_shapes
                        else target_leaf_to_full[node_leaf]
                    )
                    if key not in self._shading_map:
                        self._shading_map[key] = set()
                    self._shading_map[key].add(se_name)

            # Update global texture usage
            obj_count = len(se_objects)
            for tex in mat_textures:
                path = tex["path"]
                if path not in self._global_texture_usage:
                    self._global_texture_usage[path] = {
                        "count": 0,
                        "meshes": set(),
                        "instances": 0,
                    }

                self._global_texture_usage[path][
                    "count"
                ] += obj_count  # This is actually "used by X materials * objects" which is weird.
                # Wait, "Used by X mats" is one metric. "Used by Y meshes" is another.
                # Here we are iterating SEs. One SE = One Material (usually).
                # So for this SE, we add the objects to the set.
                self._global_texture_usage[path]["meshes"].update(se_objects)
                self._global_texture_usage[path]["instances"] += se_instance_count

    def _analyze_material_node(
        self,
        mat_node: Optional[str],
        collect_textures: bool = True,
    ) -> Dict[str, Any]:
        """Analyzes a single material node for flags (transparency, etc).

        ``collect_textures`` gates the file-node walk that does the
        heavy ``os.path.getsize`` + ``cmds.getAttr outSize`` per
        texture. Skip it when the requested report sections don't
        surface texture data — the slot/transparency/PBR flags above
        are still computed because they're effectively free.
        """
        flags = {"transparent": False, "type": "Unknown"}
        if not mat_node:
            return flags

        flags["type"] = cmds.nodeType(mat_node)

        def _has_attr(node: str, attr: str) -> bool:
            try:
                return bool(cmds.attributeQuery(attr, node=node, exists=True))
            except Exception:
                return False

        def _attr_inputs(node: str, attr: str) -> List[str]:
            try:
                return (
                    cmds.listConnections(
                        f"{node}.{attr}", source=True, destination=False
                    )
                    or []
                )
            except Exception:
                return []

        def _attr_get(node: str, attr: str):
            try:
                v = cmds.getAttr(f"{node}.{attr}")
            except Exception:
                return None
            # cmds.getAttr returns [(r,g,b)] for color3 / double3 — unwrap
            if isinstance(v, list) and len(v) == 1 and isinstance(v[0], tuple):
                return v[0]
            return v

        is_transparent = False

        transparency_attrs = ["transparency", "transmission"]

        for attr in transparency_attrs:
            if _has_attr(mat_node, attr):
                if _attr_inputs(mat_node, attr):
                    is_transparent = True
                    break
                val = _attr_get(mat_node, attr)
                if isinstance(val, (float, int)):
                    if val > 0.001:
                        is_transparent = True
                        break
                elif isinstance(val, (tuple, list)):
                    if any(c > 0.001 for c in val):
                        is_transparent = True
                        break

        if not is_transparent:
            opacity_attrs = ["opacity", "cutout_opacity"]

            for attr in opacity_attrs:
                if _has_attr(mat_node, attr):
                    if _attr_inputs(mat_node, attr):
                        is_transparent = True
                        break
                    val = _attr_get(mat_node, attr)
                    if isinstance(val, (float, int)):
                        if val < 0.999:
                            is_transparent = True
                            break
                    elif isinstance(val, (tuple, list)):
                        if any(c < 0.999 for c in val):
                            is_transparent = True
                            break

        flags["transparent"] = is_transparent

        unpacked_pbr = False
        if mat_node:
            try:
                pbr_sources = {}
                check_attrs = {
                    "metallic": ["metalness", "metallic"],
                    "roughness": ["specularRoughness", "roughness"],
                    "ao": ["ambientOcclusion", "ao"],
                }

                for key, attrs in check_attrs.items():
                    for attr in attrs:
                        if _has_attr(mat_node, attr):
                            inputs = _attr_inputs(mat_node, attr)
                            if inputs:
                                src = inputs[0]
                                if cmds.nodeType(src) == "file":
                                    pbr_sources[key] = src
                            break

                # If we have both Metallic and Roughness, check if they are different
                if "metallic" in pbr_sources and "roughness" in pbr_sources:
                    if pbr_sources["metallic"] != pbr_sources["roughness"]:
                        unpacked_pbr = True

                # If we have AO and it's different from Metallic or Roughness
                if "ao" in pbr_sources:
                    if (
                        "metallic" in pbr_sources
                        and pbr_sources["ao"] != pbr_sources["metallic"]
                    ):
                        unpacked_pbr = True
                    elif (
                        "roughness" in pbr_sources
                        and pbr_sources["ao"] != pbr_sources["roughness"]
                    ):
                        unpacked_pbr = True

            except Exception:
                pass

        flags["unpacked_pbr"] = unpacked_pbr

        # Find textures
        textures = []
        missing_count = 0
        missing_paths = []
        if mat_node and collect_textures:
            try:
                history = cmds.listHistory(mat_node) or []
                file_nodes = cmds.ls(history, type="file") or []
                # Observability — count file nodes stat'd for the manifest.
                self._file_node_count += len(file_nodes)
                for fn in file_nodes:
                    path = (
                        cmds.getAttr(f"{fn}.fileTextureName")
                        if _has_attr(fn, "fileTextureName")
                        else ""
                    )
                    if path:
                        resolved_path = cmds.workspace(expandName=path)

                        # outSize is double2; cmds returns [(w,h)]
                        out_size = cmds.getAttr(f"{fn}.outSize")
                        if (
                            isinstance(out_size, list)
                            and out_size
                            and isinstance(out_size[0], tuple)
                        ):
                            res = (out_size[0][0], out_size[0][1])
                        else:
                            res = out_size

                        size_mb = 0.0
                        if os.path.exists(resolved_path):
                            size_mb = os.path.getsize(resolved_path) / (1024 * 1024)
                        else:
                            missing_count += 1
                            missing_paths.append(resolved_path)

                        tex_type = "Unknown"
                        has_alpha = False

                        if _has_attr(fn, "outTransparency") and _attr_inputs(
                            fn, "outTransparency"
                        ):
                            has_alpha = True
                        elif _has_attr(fn, "outAlpha") and _attr_inputs(fn, "outAlpha"):
                            has_alpha = True

                        connected_channels = set()
                        try:
                            dest_plugs = (
                                cmds.listConnections(
                                    fn,
                                    source=False,
                                    destination=True,
                                    plugs=True,
                                )
                                or []
                            )
                            for dest_plug in dest_plugs:
                                dest_node, _, attr_path = dest_plug.partition(".")

                                if dest_node == mat_node:
                                    connected_channels.add(attr_path.lower())

                                elif cmds.nodeType(dest_node) in [
                                    "bump2d",
                                    "bump3d",
                                ]:
                                    bump_dests = (
                                        cmds.listConnections(
                                            dest_node,
                                            source=False,
                                            destination=True,
                                            plugs=True,
                                        )
                                        or []
                                    )
                                    for b_plug in bump_dests:
                                        if b_plug.split(".")[0] == mat_node:
                                            connected_channels.add("normal")
                                            break
                        except Exception:
                            pass

                        if connected_channels:
                            # Determine type from channels
                            # Priority: BaseColor, Normal, Emissive, then PBR/Masks
                            if any(
                                x in c
                                for c in connected_channels
                                for x in ["basecolor", "diffuse", "tex_color"]
                            ):
                                tex_type = "BaseColor"
                            elif any(
                                x in c
                                for c in connected_channels
                                for x in ["normal", "bump"]
                            ):
                                tex_type = "Normal"
                            elif any(
                                x in c
                                for c in connected_channels
                                for x in ["emiss", "emission"]
                            ):
                                tex_type = "Emissive"
                            else:
                                # Check for PBR components
                                pbr_comps = []
                                if any("rough" in c for c in connected_channels):
                                    pbr_comps.append("Roughness")
                                if any("metal" in c for c in connected_channels):
                                    pbr_comps.append("Metallic")
                                if any("spec" in c for c in connected_channels):
                                    pbr_comps.append("Specular")
                                if any(
                                    "ao" in c or "ambient" in c
                                    for c in connected_channels
                                ):
                                    pbr_comps.append("AO")
                                if any(
                                    "trans" in c or "opacity" in c
                                    for c in connected_channels
                                ):
                                    pbr_comps.append("Opacity")

                                if len(pbr_comps) > 1:
                                    # Sort for consistency
                                    pbr_comps.sort()
                                    tex_type = "Packed (" + "+".join(pbr_comps) + ")"
                                elif len(pbr_comps) == 1:
                                    tex_type = pbr_comps[0]
                                else:
                                    # Fallback to first channel name
                                    first_channel = list(connected_channels)[0]
                                    # Clean up name (e.g. "TEX_global_diffuse" -> "Global Diffuse")
                                    clean_name = (
                                        first_channel.replace("tex_", "")
                                        .replace("_map", "")
                                        .replace("_", " ")
                                        .title()
                                    )
                                    tex_type = clean_name

                        # 2. Fallback to filename
                        if tex_type == "Unknown":
                            lower_path = resolved_path.lower()
                            if any(
                                x in lower_path
                                for x in [
                                    "_bc",
                                    "_d",
                                    "_diff",
                                    "_albedo",
                                    "_color",
                                    "_basecolor",
                                ]
                            ):
                                tex_type = "BaseColor"
                            elif any(
                                x in lower_path
                                for x in ["_n", "_nrm", "_norm", "_normal"]
                            ):
                                tex_type = "Normal"
                            elif any(
                                x in lower_path
                                for x in [
                                    "_m",
                                    "_met",
                                    "_metal",
                                    "_r",
                                    "_rough",
                                    "_s",
                                    "_spec",
                                    "_orm",
                                    "_arm",
                                    "_mask",
                                ]
                            ):
                                tex_type = "Masks"
                            elif any(
                                x in lower_path for x in ["_e", "_emiss", "_emit"]
                            ):
                                tex_type = "Emissive"

                        # Fallback: Check connection if possible (not implemented here to keep it fast/simple)

                        textures.append(
                            {
                                "path": resolved_path,
                                "res": res,
                                "size_mb": size_mb,
                                "node": fn,
                                "type": tex_type,
                                "has_alpha": has_alpha,
                            }
                        )
            except Exception:
                pass

        flags["textures"] = textures
        flags["missing_textures"] = missing_count
        flags["missing_paths"] = missing_paths
        return flags

    def _analyze_mesh(self, shape: str) -> MeshRecord:
        """Fast mesh analysis."""
        counts = cmds.polyEvaluate(shape, triangle=True, vertex=True)
        bbox = cmds.polyEvaluate(shape, boundingBox=True)

        if isinstance(counts, dict):
            tris = counts.get("triangle", 0)
            verts = counts.get("vertex", 0)
        else:
            tris = 0
            verts = 0

        uv_sets = cmds.polyUVSet(shape, q=True, allUVSets=True) or []
        uv_count = len(uv_sets)
        uv_set_names = list(uv_sets)

        color_sets = cmds.polyColorSet(shape, q=True, allColorSets=True) or []
        has_colors = len(color_sets) > 0

        has_skin = False
        try:
            history = cmds.listHistory(shape) or []
            if cmds.ls(history, type="skinCluster"):
                has_skin = True
        except Exception:
            pass

        # Instanced if shape has more than one parent transform
        instanced = len(cmds.listRelatives(shape, allParents=True) or []) > 1

        # Bounds
        dx = bbox[0][1] - bbox[0][0]
        dy = bbox[1][1] - bbox[1][0]
        dz = bbox[2][1] - bbox[2][0]
        diag = math.sqrt(dx * dx + dy * dy + dz * dz)

        ngons = 0
        non_manifold_edges = 0
        lamina_faces = 0
        try:
            sel = om.MSelectionList()
            sel.add(shape)
            dag_path = sel.getDagPath(0)
            mesh_fn = om.MFnMesh(dag_path)

            vertex_counts, _ = mesh_fn.getVertices()
            ngons = sum(1 for count in vertex_counts if count > 4)

            nme = cmds.polyInfo(shape, nonManifoldEdges=True)
            if nme:
                non_manifold_edges = len(nme)

            lf = cmds.polyInfo(shape, laminaFaces=True)
            if lf:
                lamina_faces = len(lf)
        except Exception:
            pass

        # Vertex Payload Estimate
        # Pos(12) + Norm(4) + Tan(4) + UV(8*count) + Color(4) + Skin(8)
        # Baseline: 20 bytes
        v_bytes = 20 + (uv_count * 8)
        if has_colors:
            v_bytes += 4
        if has_skin:
            v_bytes += 8  # 4 weights + 4 indices (approx)

        return MeshRecord(
            shape_name=shape,
            tris=tris,
            verts=verts,
            uv_sets=uv_count,
            uv_set_names=uv_set_names,
            has_colors=has_colors,
            instanced=instanced,
            bounds_diag=diag,
            ngons=ngons,
            non_manifold_edges=non_manifold_edges,
            lamina_faces=lamina_faces,
            vertex_bytes=v_bytes,
        )

    def _analyze_material(self, shape: str) -> MaterialRecord:
        """Fast material analysis using cache."""
        assigned_ses = self._shading_map.get(shape, set())

        # If no SE found in map, it might be using default shader or not in a set?
        # If empty, try direct connection as fallback?
        # The bulk collection should have caught it if it's in a shading group.

        materials = []
        uses_transparency = False
        unpacked_pbr = False
        all_textures = []
        missing_textures = 0
        max_samplers = 0

        for se in assigned_ses:
            mat_name = self._material_map.get(se)
            if mat_name:
                materials.append(mat_name)
                flags = self._material_flags.get(mat_name, {})
                if flags.get("transparent"):
                    uses_transparency = True
                if flags.get("unpacked_pbr"):
                    unpacked_pbr = True
                if flags.get("textures"):
                    mat_textures = flags["textures"]
                    all_textures.extend(mat_textures)

                    # Calculate samplers for this specific material
                    # Dedup by path to handle same texture used multiple times in one shader
                    unique_mat_textures = {t["path"] for t in mat_textures}
                    count = len(unique_mat_textures)
                    if count > max_samplers:
                        max_samplers = count

                if flags.get("missing_textures"):
                    missing_textures += flags["missing_textures"]

        # Aggregate texture stats
        # Dedup by path to avoid double counting same texture used in multiple slots of same material
        unique_textures = {t["path"]: t for t in all_textures}.values()
        texture_count = len(unique_textures)
        max_res = 0
        max_res_is_unique = False
        total_size = 0.0
        est_gpu_size = 0.0
        unique_paths_local = 0

        for t in unique_textures:
            w, h = t["res"]
            max_dim = max(w, h)

            path = t["path"]
            usage_data = self._global_texture_usage.get(path, {})
            usage_count = (
                usage_data.get("count", 0) if isinstance(usage_data, dict) else 0
            )
            is_unique = usage_count == 1

            if is_unique:
                unique_paths_local += 1

            if max_dim > max_res:
                max_res = max_dim
                max_res_is_unique = is_unique
            elif max_dim == max_res:
                if is_unique:
                    max_res_is_unique = True

            total_size += t["size_mb"]

            # Estimate GPU size based on format
            # BaseColor: BC7 (1 byte) or BC1 (0.5 byte) if no alpha
            # Normal: BC5 (1 byte)
            # Masks: BC1 (0.5 byte) or BC4 (0.5 byte)
            # Emissive: BC1 (0.5 byte)
            # Uncompressed: RGBA8 (4 bytes)

            # Mips: 1.33x
            pixels = w * h
            mips = 1.33

            # Uncompressed baseline
            est_gpu_size += (pixels * 4 * mips) / (1024 * 1024)

            # Compressed estimate
            bpp = 1.0  # Default BC7/BC5 (8 bits per pixel = 1 byte)
            tex_type = t.get("type", "Unknown")
            has_alpha = t.get("has_alpha", False)

            if tex_type == "BaseColor":
                if has_alpha:
                    bpp = 1.0  # BC7
                else:
                    bpp = 0.5  # BC1
            elif tex_type == "Normal":
                bpp = 1.0  # BC5
            elif tex_type in [
                "Masks",
                "Roughness",
                "Metallic",
                "AO",
                "Specular",
                "Opacity",
            ]:
                bpp = 0.5  # BC1/BC4
            elif "Packed" in tex_type:
                bpp = 1.0  # Assume BC7 for packed maps to be safe, or BC1 if RGB. Let's use 1.0.
            elif tex_type == "Emissive":
                bpp = 0.5  # BC1

            # Add to compressed total (unique)
            # Note: MaterialRecord stores total for this material's textures.
            # But we are iterating unique textures for this material.
            # Wait, MaterialRecord.est_gpu_size_mb is per-material unique.
            # SceneOverview aggregates this differently.

            # We'll store the compressed size in the record too?
            # MaterialRecord doesn't have est_gpu_size_mb_compressed field yet.
            # We can repurpose or add it.
            # For now, let's just update the logic in SceneOverview using global usage.
            # But here we are in _analyze_material.

            pass  # Logic moved to SceneOverview for global stats, but we need it here for per-asset score?
            # Current score uses total_tex_size_mb (disk).
            # Let's stick to disk size for scoring for now.

        return MaterialRecord(
            slot_count=len(assigned_ses),
            uses_transparency=uses_transparency,
            materials=sorted(list(set(materials))),  # Dedup material names
            texture_count=texture_count,
            max_res=int(max_res),
            total_tex_size_mb=total_size,
            est_gpu_size_mb=est_gpu_size,
            unpacked_pbr=unpacked_pbr,
            missing_textures=missing_textures,
            max_samplers=max_samplers,
            unique_paths_local=unique_paths_local,
            max_res_is_unique=max_res_is_unique,
        )

    def _calculate_score(
        self, mesh: MeshRecord, mat: MaterialRecord
    ) -> Tuple[
        float,
        float,
        float,
        List[Finding],
        Dict[str, float],
        BudgetDelta,
        List[FixAction],
        int,
    ]:
        """Calculate 'badness' scores (perf / risk) and emit structured
        findings, fix actions, and a budget delta.

        Returns:
            ``(total_score, perf_score, risk_score, findings, breakdown,
            delta, fix_plan, target_tris)`` — all richly-typed. ``delta``
            is a :class:`BudgetDelta` (was the ``delta_summary`` string);
            ``findings`` is ``List[Finding]`` (was ``List[str]`` with
            severity baked into trailing ``[H]/[M]/[L]`` tags); ``fix_plan``
            is ``List[FixAction]`` (was ``List[str]`` of prose).
        """
        perf_score = 0.0
        risk_score = 0.0
        findings: List[Finding] = []
        breakdown: Dict[str, float] = {}
        fix_plan: List[FixAction] = []

        # Determine Target Tris (Adaptive)
        target_tris = self.profile.max_tris
        if self.profile.adaptive_tris and self.profile.reference_diag > 0:
            # Linear scaling: size / ref_size * max_tris
            # Clamped between min_tris and max_tris
            ratio = min(1.0, mesh.bounds_diag / self.profile.reference_diag)
            calculated = int(self.profile.max_tris * ratio)
            target_tris = max(self.profile.min_tris, calculated)

        delta = BudgetDelta(
            tris_over=max(0, mesh.tris - target_tris),
            slots_over=max(0, mat.slot_count - self.profile.max_slots),
            uvs_over=max(0, mesh.uv_sets - self.profile.max_uvs),
            max_tex_res_over=max(0, mat.max_res - self.profile.max_tex_res),
        )

        # --- Mesh Scoring ---

        # Tris
        if mesh.tris > target_tris:
            over = mesh.tris - target_tris
            penalty = over / 1000.0  # 1 point per 1k over
            perf_score += penalty
            findings.append(
                Finding(
                    severity=SEVERITY_HIGH,
                    kind="high_poly",
                    message=f"High Poly: {mesh.tris} tris (budget {target_tris}, +{over})",
                    detail={"tris": mesh.tris, "budget": target_tris, "over": over},
                )
            )
            breakdown["High Poly"] = penalty
            fix_plan.append(
                FixAction(
                    severity=SEVERITY_HIGH,
                    kind="decimate",
                    message=f"Reduce tris {mesh.tris:,} -> {target_tris:,} (Decimate/Retopo)",
                    detail={"from_tris": mesh.tris, "to_tris": target_tris},
                )
            )

        # Verts per tri (Bloat check)
        if mesh.tris > 0:
            ratio = mesh.verts / mesh.tris
            if ratio > 3.0:
                penalty = 10.0
                perf_score += penalty
                findings.append(
                    Finding(
                        severity=SEVERITY_HIGH,
                        kind="vert_bloat",
                        message=f"Vert Bloat: {ratio:.1f} verts/tri",
                        detail={"verts_per_tri": ratio},
                    )
                )
                breakdown["Vert Bloat"] = penalty
                fix_plan.append(
                    FixAction(
                        severity=SEVERITY_MEDIUM,
                        kind="merge_vertices",
                        message="Merge vertices / Fix hard edges to reduce vertex count",
                    )
                )

        # UV Sets
        if mesh.uv_sets > self.profile.max_uvs:
            over = mesh.uv_sets - self.profile.max_uvs
            penalty = over * 5.0
            perf_score += penalty
            uv_names_str = ", ".join(mesh.uv_set_names)
            findings.append(
                Finding(
                    severity=SEVERITY_MEDIUM,
                    kind="extra_uv_sets",
                    message=(
                        f"Extra Vertex Streams: {mesh.uv_sets} UV sets "
                        f"({uv_names_str}) (budget {self.profile.max_uvs}, +{over})"
                    ),
                    detail={
                        "uv_sets": mesh.uv_sets,
                        "uv_names": list(mesh.uv_set_names),
                        "budget": self.profile.max_uvs,
                        "over": over,
                    },
                )
            )
            breakdown["Extra UV Sets"] = penalty
            fix_plan.append(
                FixAction(
                    severity=SEVERITY_LOW,
                    kind="remove_uv_sets",
                    message=(
                        f"Remove {over} extra UV sets "
                        "(if not required by export/profile)"
                    ),
                    detail={"remove_count": over},
                )
            )

        # Vertex Bytes finding deliberately dropped — the prior code
        # appended a "Vertex Payload" finding then filtered it out in
        # the renderer. Bytes are still available on
        # ``MeshRecord.vertex_bytes`` for callers that want them.

        # Ngons (Risk, not Perf)
        if mesh.ngons > 0:
            penalty = mesh.ngons * 0.1
            risk_score += penalty

            severity = SEVERITY_MEDIUM
            if mesh.ngons > 100 or mesh.tris > target_tris:
                severity = SEVERITY_HIGH

            if mesh.tris > 0:
                ngons_per_10k = (mesh.ngons / mesh.tris) * 10000
                msg = f"N-gons: {mesh.ngons} ({ngons_per_10k:.1f} per 10k tris)"
            else:
                msg = f"N-gons: {mesh.ngons}"

            findings.append(
                Finding(
                    severity=severity,
                    kind="ngons",
                    message=msg,
                    detail={"ngons": mesh.ngons, "tris": mesh.tris},
                )
            )
            breakdown["N-gons"] = penalty
            fix_plan.append(
                FixAction(
                    severity=SEVERITY_LOW,
                    kind="triangulate_ngons",
                    message="Triangulate or Quadrangulate N-gons",
                )
            )

        # Non-manifold edges (Risk)
        if mesh.non_manifold_edges > 0:
            penalty = mesh.non_manifold_edges * 2.0
            risk_score += penalty
            findings.append(
                Finding(
                    severity=SEVERITY_HIGH,
                    kind="non_manifold",
                    message=f"Non-Manifold: {mesh.non_manifold_edges} edges",
                    detail={"non_manifold_edges": mesh.non_manifold_edges},
                )
            )
            breakdown["Non-Manifold"] = penalty
            fix_plan.append(
                FixAction(
                    severity=SEVERITY_HIGH,
                    kind="fix_non_manifold",
                    message="Cleanup non-manifold geometry",
                )
            )

        # Lamina faces (Risk)
        if mesh.lamina_faces > 0:
            penalty = mesh.lamina_faces * 2.0
            risk_score += penalty
            findings.append(
                Finding(
                    severity=SEVERITY_HIGH,
                    kind="lamina_faces",
                    message=f"Lamina Faces: {mesh.lamina_faces}",
                    detail={"lamina_faces": mesh.lamina_faces},
                )
            )
            breakdown["Lamina Faces"] = penalty
            fix_plan.append(
                FixAction(
                    severity=SEVERITY_HIGH,
                    kind="remove_lamina",
                    message="Remove lamina faces",
                )
            )

        # --- Material Scoring ---

        # Slots (Draw calls)
        unique_mat_count = len(mat.materials)
        if mat.slot_count > self.profile.max_slots:
            over = mat.slot_count - self.profile.max_slots
            penalty = over * 10.0
            perf_score += penalty

            redundancy_note = ""
            if mat.slot_count > unique_mat_count:
                redundancy_note = f" ({unique_mat_count} unique materials)"

            findings.append(
                Finding(
                    severity=SEVERITY_HIGH,
                    kind="draw_call_split",
                    message=(
                        f"Draw Call Split: {mat.slot_count} slots"
                        f"{redundancy_note} "
                        f"(budget {self.profile.max_slots}, +{over})"
                    ),
                    detail={
                        "slot_count": mat.slot_count,
                        "unique_materials": unique_mat_count,
                        "budget": self.profile.max_slots,
                        "over": over,
                    },
                )
            )
            breakdown["Draw Call Split"] = penalty

            if mat.slot_count > unique_mat_count:
                fix_plan.append(
                    FixAction(
                        severity=SEVERITY_HIGH,
                        kind="consolidate_slots",
                        message=(
                            f"Consolidate {mat.slot_count - unique_mat_count} "
                            "redundant slots (Assign same material to all faces)"
                        ),
                        detail={"redundant_slots": mat.slot_count - unique_mat_count},
                    )
                )
            else:
                fix_plan.append(
                    FixAction(
                        severity=SEVERITY_HIGH,
                        kind="reduce_slots",
                        message=(
                            f"Reduce slots {mat.slot_count} -> "
                            f"{self.profile.max_slots} (Merge materials: "
                            "Combine textures or use Vertex Colors)"
                        ),
                        detail={
                            "from_slots": mat.slot_count,
                            "to_slots": self.profile.max_slots,
                        },
                    )
                )

        # Transparency
        if mat.uses_transparency:
            penalty = 5.0
            perf_score += penalty
            findings.append(
                Finding(
                    severity=SEVERITY_MEDIUM,
                    kind="transparency",
                    message="Transparent",
                )
            )
            breakdown["Transparency"] = penalty

        # Textures
        ideal_res = (mesh.bounds_diag / 100.0) * 512

        if mat.max_res > self.profile.max_tex_res:
            over = mat.max_res - self.profile.max_tex_res
            if mat.max_res_is_unique and mat.max_res > ideal_res * 2.0:
                penalty = 10.0
                perf_score += penalty
                findings.append(
                    Finding(
                        severity=SEVERITY_MEDIUM,
                        kind="oversized_texture",
                        message=(
                            f"Oversized Texture: {mat.max_res}px "
                            f"(vs ideal {int(ideal_res)}px)"
                        ),
                        detail={
                            "res": mat.max_res,
                            "ideal_res": int(ideal_res),
                        },
                    )
                )
                breakdown["Oversized Texture"] = penalty
                fix_plan.append(
                    FixAction(
                        severity=SEVERITY_MEDIUM,
                        kind="downscale_textures",
                        message=f"Downscale textures to {int(ideal_res)}px",
                        detail={"target_res": int(ideal_res)},
                    )
                )
            else:
                findings.append(
                    Finding(
                        severity=SEVERITY_HIGH,
                        kind="max_tex_dim",
                        message=(
                            f"Max texture dimension: {mat.max_res} "
                            f"(budget {self.profile.max_tex_res}, +{over})"
                        ),
                        detail={
                            "res": mat.max_res,
                            "budget": self.profile.max_tex_res,
                            "over": over,
                        },
                    )
                )
                fix_plan.append(
                    FixAction(
                        severity=SEVERITY_HIGH,
                        kind="downscale_textures",
                        message=f"Downscale textures to {self.profile.max_tex_res}px",
                        detail={"target_res": self.profile.max_tex_res},
                    )
                )

        if mat.total_tex_size_mb > 50.0:  # 50MB soft limit per mesh
            penalty = (mat.total_tex_size_mb - 50.0) * 0.5
            perf_score += penalty
            findings.append(
                Finding(
                    severity=SEVERITY_LOW,
                    kind="heavy_textures",
                    message=f"Heavy Textures: {mat.total_tex_size_mb:.1f} MB",
                    detail={"size_mb": mat.total_tex_size_mb},
                )
            )
            breakdown["Heavy Textures"] = penalty

        # Sampler Count / Packing
        if mat.unpacked_pbr:
            penalty = 15.0
            perf_score += penalty
            findings.append(
                Finding(
                    severity=SEVERITY_MEDIUM,
                    kind="unpacked_pbr",
                    message="Unpacked PBR Maps (Inefficient)",
                )
            )
            breakdown["Unpacked PBR"] = penalty
            fix_plan.append(
                FixAction(
                    severity=SEVERITY_MEDIUM,
                    kind="pack_pbr",
                    message="Pack PBR maps (ORM/ARM)",
                )
            )

        # Max Samplers (Per-material limit, usually 16)
        if mat.max_samplers > 8:
            penalty = (mat.max_samplers - 8) * 2.0
            perf_score += penalty
            findings.append(
                Finding(
                    severity=SEVERITY_LOW,
                    kind="texture_samplers",
                    message=f"Texture Samplers: {mat.max_samplers} samplers",
                    detail={"samplers": mat.max_samplers},
                )
            )
            breakdown["Texture Samplers"] = penalty

        # Unique Files (Local Impact)
        if mat.unique_paths_local > 0:
            penalty = mat.unique_paths_local * 2.0
            perf_score += penalty
            findings.append(
                Finding(
                    severity=SEVERITY_LOW,
                    kind="unique_textures_local",
                    message=f"Unique Textures: {mat.unique_paths_local} (Local only)",
                    detail={"count": mat.unique_paths_local},
                )
            )
            breakdown["Unique Textures"] = penalty

        # Shader Complexity (Total textures)
        if mat.texture_count > 5:
            penalty = (mat.texture_count - 5) * 0.5
            perf_score += penalty
            breakdown["Shader Complexity"] = penalty

        if mat.missing_textures > 0:
            penalty = mat.missing_textures * 2.0
            risk_score += penalty
            findings.append(
                Finding(
                    severity=SEVERITY_HIGH,
                    kind="missing_textures",
                    message=f"Missing Textures: {mat.missing_textures} files",
                    detail={"count": mat.missing_textures},
                )
            )
            breakdown["Missing Textures"] = penalty
            fix_plan.append(
                FixAction(
                    severity=SEVERITY_HIGH,
                    kind="relink_textures",
                    message="Relink missing textures",
                )
            )

        total_score = perf_score + risk_score

        # Deduplicate fix plan by (kind, message) — the structured
        # form means we don't accidentally collapse two distinct
        # actions that happen to share a prefix.
        seen: Set[Tuple[str, str]] = set()
        deduped: List[FixAction] = []
        for action in fix_plan:
            key = (action.kind, action.message)
            if key in seen:
                continue
            seen.add(key)
            deduped.append(action)

        return (
            total_score,
            perf_score,
            risk_score,
            findings,
            breakdown,
            delta,
            deduped,
            target_tris,
        )

    def _group_records(
        self, records: List[AssetRecord], sort_key: Callable[[AssetRecord], float]
    ) -> List[Tuple[str, List[AssetRecord]]]:
        """Groups records by base name and sorts groups by the max value of sort_key."""
        import re

        groups = {}
        for rec in records:
            # Match base name (e.g. "cargo_details1_b" -> "cargo_details")
            base_name = re.sub(r"[\d_]+[a-zA-Z]?$", "", rec.transform)
            if not base_name:
                base_name = rec.transform

            if base_name not in groups:
                groups[base_name] = []
            groups[base_name].append(rec)

        # Sort groups by max value in group
        sorted_groups = sorted(
            groups.items(),
            key=lambda item: max(sort_key(r) for r in item[1]),
            reverse=True,
        )
        return sorted_groups

    # ------------------------------------------------------------ #
    # Section renderers — all consume :class:`SceneReport`.
    # ------------------------------------------------------------ #

    # Severity-to-confidence-tag map used by per-asset findings.
    _CONF_TAG = {
        SEVERITY_HIGH: "[H]",
        SEVERITY_MEDIUM: "[M]",
        SEVERITY_LOW: "[L]",
    }

    def print_report(
        self,
        report: SceneReport,
        sections: Optional[List[str]] = None,
    ):
        """Print the formatted scene-audit report to the logger.

        ``sections`` chooses which sections to render and in what
        order. ``None`` means "all sections" — equivalent to the
        prior behavior. The context header (title + profile lines)
        is always emitted first.
        """
        self._render_header_section(report)
        selected = (
            list(SceneInfoSection.ALL) if sections is None else list(sections)
        )
        for section in selected:
            renderer = self._section_renderers().get(section)
            if renderer is not None:
                renderer(report)

    def _section_renderers(self) -> Dict[str, Callable[[SceneReport], None]]:
        """Map a ``SceneInfoSection`` key to its renderer. Centralized
        so ``print_report`` and the per-section HTML capture path
        share a single registry."""
        return {
            SceneInfoSection.SUMMARY: self._render_summary_section,
            SceneInfoSection.FIX_FIRST: self._render_fix_first_section,
            SceneInfoSection.PARETO: self._render_pareto_section,
            SceneInfoSection.OFFENDERS: self._render_offenders_section,
            SceneInfoSection.CATEGORIES: self._render_categories_section,
            SceneInfoSection.TEXTURES: self._render_textures_section,
            SceneInfoSection.PIPELINE: self._render_pipeline_section,
            SceneInfoSection.ASSUMPTIONS: self._render_assumptions_section,
        }

    def _render_header_section(self, report: SceneReport) -> None:
        """Title + profile block. Always emitted at the top."""
        profile = report.manifest.profile
        header_lines = [
            f"Profile: {profile.name}",
            f"  - Max Tris: {profile.max_tris:,} {'(Adaptive)' if profile.adaptive_tris else ''}",
            f"  - Max Slots: {profile.max_slots}",
            f"  - Max Tex Res: {profile.max_tex_res}px",
            f"  - Max UV Sets: {profile.max_uvs}",
        ]
        self.logger.log_box("Scene Audit Report", header_lines)

    def _render_summary_section(self, report: SceneReport) -> None:
        """Executive Summary — high-level scene metrics."""
        col_width = 30
        summary = report.summary
        budget = report.budget
        textures = report.textures
        manifest = report.manifest

        self.logger.info("")
        self.logger.notice("Executive Summary")
        self.logger.log_divider()

        if summary.scene_health_flags:
            for flag in summary.scene_health_flags:
                self.logger.warning(f"  [!] {flag}")
            self.logger.log_raw("")

        self.logger.log_raw(
            f"{'Mesh Shapes':<{col_width}}: {summary.total_meshes} unique"
        )
        self.logger.log_raw(
            f"{'Instances':<{col_width}}: {summary.instance_stats.total_instances} total "
            f"({summary.instance_stats.instanced_shapes} instanced shapes)"
        )

        self.logger.log_raw(
            f"{'Triangles':<{col_width}}: {summary.total_tris:,} Effective "
            f"(Raw: {summary.raw_total_tris:,})"
        )

        # Material Slots Block — only if material data was collected.
        if manifest.materials_collected:
            if budget.slot_stats is not None:
                s = budget.slot_stats
                self.logger.log_raw(
                    f"{'Slots per mesh':<{col_width}}: avg {s.avg_unique:.1f} | "
                    f"median {s.median} | p90 {s.p90} | max {s.max}"
                )
            self.logger.log_raw(
                f"{'Effective draw calls':<{col_width}}: {budget.total_slots} (slot proxy)"
            )

        # Compressed Breakdown — only if textures were collected.
        if manifest.textures_collected:
            self.logger.log_raw(
                f"{'Est. GPU Compressed':<{col_width}}: {textures.est_gpu_mb_compressed:.1f} MB "
                "(assumed formats by map type)"
            )

            missing_count = len(report.pipeline.missing_project)
            if missing_count > 0:
                affected = len(report.pipeline.impact.affected_meshes)
                self.logger.log_raw(
                    f"{'Missing Files':<{col_width}}: {missing_count} project textures "
                    f"(affecting {affected} meshes)"
                )

    def _render_fix_first_section(self, report: SceneReport) -> None:
        """Fix First — prioritized high-impact remediation items."""
        summary = report.summary
        budget = report.budget
        scene_actions = report.fix_actions

        if not (
            scene_actions
            or summary.total_tris > budget.total_target_tris
            or budget.total_slots_over_budget > 0
        ):
            return

        self.logger.info("")
        self.logger.notice("Fix First (High Impact)")
        self.logger.log_divider()

        deltas = []
        if summary.total_tris > budget.total_target_tris:
            deltas.append(
                f"+{summary.total_tris - budget.total_target_tris:,} tris "
                f"({budget.meshes_over_tri_threshold} meshes)"
            )
        if budget.total_slots_over_budget > 0:
            deltas.append(f"+{budget.total_slots_over_budget} slots")

        if deltas:
            self.logger.log_raw(f"Over budget deltas: {' | '.join(deltas)}")
            self.logger.log_raw("")

        for action in scene_actions:
            # The scene-level Reduce / Decimate actions duplicate the
            # delta line above when deltas are printed — skip them by
            # structured kind rather than substring matching.
            if deltas and action.kind in {"reduce_slots_scene", "decimate_scene"}:
                continue
            self.logger.log_raw(f"  - {action.message}")

    def _render_pareto_section(self, report: SceneReport) -> None:
        """Pareto View — top contributors to tris / slots."""
        pareto_tris = report.offenders.pareto_tris
        pareto_slots = report.offenders.pareto_slots
        if not (pareto_tris or pareto_slots):
            return

        self.logger.info("")
        self.logger.notice("Pareto View (Top 10)")
        self.logger.log_divider()

        if pareto_tris:
            cum_total = pareto_tris[-1].cum_percent
            self.logger.log_raw(
                f"Triangles (Top 10 account for {cum_total:.1f}%):"
            )
            for entry in pareto_tris:
                self.logger.log_raw(f"  {entry.target}: {entry.value:,}")
            self.logger.log_raw("")

        if pareto_slots:
            cum_total = pareto_slots[-1].cum_percent
            self.logger.log_raw(
                f"Slots (Top 10 account for {cum_total:.1f}%):"
            )
            for entry in pareto_slots:
                self.logger.log_raw(f"  {entry.target}: {entry.value}")
            self.logger.log_raw("")

    def _render_offenders_section(self, report: SceneReport) -> None:
        """Top Issues by Asset (Base Score)."""
        by_score = report.offenders.by_score
        if not by_score:
            return
        offenders = [r for r in by_score if r.score > 0]
        if not offenders:
            return

        self.logger.info("")
        self.logger.notice("Top Issues by Asset (Base Score)")
        self.logger.log_divider()

        for i, rec in enumerate(offenders[:5], 1):
            self._print_asset_record(rec, i)

    def _render_categories_section(self, report: SceneReport) -> None:
        """Top Offenders by Category — materials correlated with slot bloat."""
        self.logger.info("")
        self.logger.notice("Top Offenders by Category")
        self.logger.log_divider()

        splits = report.offenders.materials_causing_splits
        if splits:
            headers = ["Material", "Unique Meshes", "Avg Slots", "Over-Slot"]
            sorted_mats = sorted(
                splits,
                key=lambda s: (s.over_budget_count, s.avg_slots),
                reverse=True,
            )
            data = [
                [s.material, s.unique_mesh_count, f"{s.avg_slots:.1f}", s.over_budget_count]
                for s in sorted_mats[:5]
            ]
            self.log_table(
                data,
                headers,
                title="Materials correlated with high slot meshes",
            )
            self.logger.log_raw("")

    def _render_textures_section(self, report: SceneReport) -> None:
        """Textures — histogram, 4K analysis, heaviest files."""
        if not report.manifest.textures_collected:
            return

        textures = report.textures

        self.logger.info("")
        self.logger.notice("Textures")
        self.logger.log_divider()

        if textures.dim_histogram:
            self.logger.log_raw("Dimension Histogram:")
            hist = textures.dim_histogram
            self.logger.log_raw(
                f"  4k+: {hist['4k+']} | 2k: {hist['2k']} | 1k: {hist['1k']} | "
                f"512: {hist['512']} | <512: {hist['<512']}"
            )

            if hist["4k+"] > 0:
                self.logger.log_raw(
                    f"  4K Analysis: {hist['4k+']} textures "
                    f"(Shared: {textures.shared_4k_count} | "
                    f"Single-use: {textures.single_use_4k_count})"
                )
                if textures.shared_4k:
                    headers = ["Texture Name", "Mesh Count"]
                    data = [
                        [os.path.basename(s.path), s.mesh_count]
                        for s in textures.shared_4k
                    ]
                    self.log_table(data, headers, title="Top Shared 4K Textures")
            self.logger.log_raw("")

        # Heaviest Textures: only print when the single-use 4K count
        # is high — see prior reviewer note ("Print Heaviest only
        # when single-use 4K is high (e.g., >25)").
        if textures.heaviest and textures.single_use_4k_count > 25:
            headers = ["Path", "Size (MB)", "Res", "Mats", "Meshes", "Inst"]
            data = []
            for t in textures.heaviest[:10]:
                display_path = t.path
                if len(display_path) > 50:
                    display_path = "..." + display_path[-47:]
                data.append(
                    [
                        display_path,
                        f"{t.size_mb:.1f}",
                        f"{t.width}x{t.height}",
                        t.material_count,
                        t.mesh_count,
                        t.instance_count,
                    ]
                )
            self.log_table(data, headers, title="Heaviest Textures (Files)")

    def _render_pipeline_section(self, report: SceneReport) -> None:
        """Pipeline Integrity — missing project textures + impact."""
        if not report.manifest.textures_collected:
            return
        pipeline = report.pipeline
        if not pipeline.integrity_warnings:
            return

        self.logger.info("")
        self.logger.notice("Pipeline Integrity")
        self.logger.log_divider()

        if pipeline.missing_project:
            self.logger.log_raw(
                f"Missing project files: {len(pipeline.missing_project)}"
            )

        impact = pipeline.impact
        if not impact.is_empty() and impact.top_offenders:
            self.logger.log_raw(
                f"Affected top offenders: {', '.join(impact.top_offenders)}"
            )

        if pipeline.missing_project:
            headers = ["Missing File Path", "Mats"]
            data = []
            for missing in pipeline.missing_project[:5]:
                display_path = (
                    missing.path
                    if len(missing.path) <= 60
                    else "..." + missing.path[-57:]
                )
                data.append([display_path, missing.material_count])
            self.log_table(data, headers, title="Missing Project Files")

    def _render_assumptions_section(self, report: SceneReport) -> None:
        """Data Assumptions — methodology notes."""
        self.logger.info("")
        self.logger.notice("Data Assumptions")
        self.logger.log_divider()
        self.logger.log_raw(
            "- GPU Size Est: Uncompressed RGBA8 + 33% Mips. Actual usage depends on engine compression (BC1/BC3/ASTC)."
        )
        self.logger.log_raw(
            "- Compression assumptions: BaseColor BC7/BC1, Normal BC5, Masks (AO/Rough/Metal) BC4/BC1 (varies)."
        )
        self.logger.log_raw(
            "- Unique Texture Disk Size: Sum of file sizes on disk for unique paths referenced by materials."
        )
        self.logger.log_raw(
            "- Effective Score: Base Score * Instance Count. Prioritize high effective scores."
        )

    def _print_asset_record(
        self, rec: AssetRecord, rank: int, effective: bool = False
    ):
        """Render a single asset record. Uses the structured
        ``rec.findings`` / ``rec.fix_plan`` / ``rec.delta`` — no
        substring sniffing or regex stripping needed."""
        effective_score = rec.score * max(1, rec.instance_count)

        score_display = f"Score: {rec.score:.0f}"
        if effective:
            score_display = f"Effective: {effective_score:.0f} (Base: {rec.score:.0f})"

        self.logger.warning(
            f"{rec.transform:<40} {rec.instance_count} instances | "
            f"{score_display} | Rank: #{rank}"
        )

        if rec.delta.is_over_budget():
            self.logger.log_raw(f"  Deltas: {rec.delta.summary()}")

        # Slots evidence
        if rec.material.slot_count > 1:
            mats = rec.material.materials
            limit = 3
            mat_str = ", ".join(mats[:limit])
            if len(mats) > limit:
                mat_str += "..."
            self.logger.log_raw(
                f"  Slots ({rec.material.slot_count}): {mat_str}"
            )

        # Findings — severity comes from the Finding itself.
        for finding in rec.findings:
            conf = self._CONF_TAG.get(finding.severity, "[L]")
            self.logger.log_raw(f"  - {finding.message} {conf}")

        # Fix Plan — top 3 by emission order (already deduped in
        # _calculate_score).
        if rec.fix_plan:
            self.logger.log_raw("  Fix Plan:")
            for action in rec.fix_plan[:3]:
                self.logger.log_raw(f"    > {action.message}")

        self.logger.log_raw("")  # Spacer
