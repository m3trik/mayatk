# !/usr/bin/python
# coding=utf-8
"""Import a Blender scene (.blend) into Maya via a headless-Blender FBX round-trip.

The pull-direction sibling of :class:`BlenderBridge` (which pushes the Maya selection
to a fresh interactive Blender), and the mayatk mirror of blendertk's
``MayaSceneImport`` / ``btk.import_maya_scene`` (name + behavior, per the ecosystem
parity rule). A pull inverts the hand-off pipeline -- the input is a *path*, the
payload is produced *Blender-side*, and the caller needs the result -- so it
deliberately does NOT subclass :class:`pythontk.ScriptLaunchBridge`; the shared pieces
are the :class:`pythontk.AppSpec` discovery (borrowed from ``_blender_bridge._SPEC``),
the ``__KEY__`` template renderer, and pythontk's blocking
:func:`~pythontk.run_script_to_artifact` runner.

Flow: render ``templates/_import_scene.py`` -> run it under
``blender --background --factory-startup`` (fresh process every time -- the ecosystem
session-safety rule; factory startup also skips the user's addons/config) -> the
script opens the .blend and exports an FBX + a texture manifest -> ``cmds.file`` (FBX
plugin) brings it in -> materials whose textures FBX cannot carry are rebuilt
natively from the manifest via the :class:`~mayatk.mat_utils.game_shader.GameShader`
engine -> temp payload removed on success, kept + logged on failure
(``TempArtifacts`` scoped policy).

``import maya.cmds`` stays deferred (inside the import methods) so this surface
resolves without a running Maya. Requires a local Blender install (no license --
unlike the reverse direction, the conversion is free and fast).
"""

from __future__ import annotations

import os
import re
import shutil
import sys
from typing import Any, Dict, List, Optional, Sequence

import pythontk as ptk
from pythontk.core_utils import script_template as _templates

from mayatk.env_utils.blender_bridge._blender_bridge import _SPEC, _TEMPLATE_DIR

_IMPORT_TEMPLATE = _TEMPLATE_DIR / "_import_scene.py"
_IMPORT_TEMPLATE_USD = _TEMPLATE_DIR / "_import_scene_usd.py"
_BAKE_TEMPLATE = _TEMPLATE_DIR / "_bake_scene.py"

# Conversion intermediates by route: "fbx" = classic material model + texture-
# manifest sidecar rebuilt via the GameShader engine; "usd" = native materials /
# instancing through each DCC's USD runtime (no manifest needed — see the
# templates' docstrings for the fidelity trade-offs).
_TEMPLATES = {"fbx": _IMPORT_TEMPLATE, "usd": _IMPORT_TEMPLATE_USD}

# Blender scene format bpy.ops.wm.open_mainfile accepts; FBX would be imported directly.
SUPPORTED_EXTENSIONS = (".blend",)

# Sources bake_scene turns into a referenceable .ma. A .blend needs the headless-Blender
# conversion first; an .fbx is already the bake's own input, so it skips that hop.
BAKE_SOURCE_EXTENSIONS = (".blend", ".fbx")

# Sidecar written beside every bake naming the scene it came from. The Reference Manager
# lists SOURCE rows but references the BAKED file, so "is this row referenced?" can only
# be answered by walking back from a reference to its origin. On disk rather than in panel
# settings so the mapping survives a session change and is shared by every panel.
BAKE_SOURCE_SUFFIX = ".source.json"

# USD sources short-circuit the whole pipeline: both DCCs speak USD natively,
# so there is no conversion (and no Blender install) involved at all.
USD_EXTENSIONS = ptk.USD_EXTENSIONS

# Child-process argv for the conversion Blender: headless, factory settings (no
# user addons/config -- deterministic AND skips any startup toolkit the user's
# Blender autoloads), then our rendered script.
_LAUNCH_ARGS = ("--background", "--factory-startup", "--python")

# Child-process env for the bake mayapy: skip the startup baggage a headless one-shot
# baker never needs. userSetup.py is the big one on pipeline machines (it can bootstrap a
# whole toolkit); the CIP/CER/CLIC analytics trio adds network round-trips. Mirror of the
# blendertk side's conversion env.
_FAST_MAYA_ENV = {
    "MAYA_SKIP_USERSETUP_PY": "1",
    "MAYA_DISABLE_CIP": "1",
    "MAYA_DISABLE_CER": "1",
    "MAYA_DISABLE_CLIC_IPM": "1",
}


class _BlenderSceneImportInternal(object):
    """Internal helpers for BlenderSceneImport."""

    @staticmethod
    def _fbx_safe_name(name: str) -> str:
        """*name* as Maya's FBX importer will spell it (illegal chars -> ``FBXASC###``).

        Blender allows ``.`` / spaces / leading digits in datablock names
        ("Material.001"); Maya's FBX plugin encodes each illegal character as
        ``FBXASC`` + its 3-digit ASCII code, a leading digit included (verified
        live against Maya 2025: ``dotted.001`` -> ``dottedFBXASC046001``,
        ``1digit`` -> ``FBXASC049digit``).
        """
        out = []
        for i, ch in enumerate(name):
            legal = (
                "a" <= ch <= "z"
                or "A" <= ch <= "Z"
                or ch == "_"
                or (ch.isdigit() and i > 0)
            )
            out.append(ch if legal else "FBXASC%03d" % ord(ch))
        return "".join(out)

    @staticmethod
    def _maya_safe_name(name: str) -> str:
        """A readable legal Maya node name for a REBUILT network (illegal -> ``_``).

        Distinct from :func:`_fbx_safe_name`: that models the importer for
        *matching*; this is the cosmetic spelling for nodes we create ourselves.
        """
        safe = re.sub(r"[^0-9A-Za-z_]", "_", name) or "rebuilt_material"
        return ("_" + safe) if safe[0].isdigit() else safe

    @staticmethod
    def _matches_fbx_name(candidate: str, want: str) -> bool:
        """True when *candidate* is *want* modulo Maya's clash-rename digit suffix."""
        if candidate == want:
            return True
        return candidate.startswith(want) and candidate[len(want) :].isdigit()


class BlenderSceneImport(ptk.LoggingMixin, _BlenderSceneImportInternal):
    """Engine: convert a .blend to FBX via headless Blender, then import it.

    Scriptable and synchronous; async affordances belong to the calling UI layer.
    """

    def __init__(
        self,
        blender_path: Optional[str] = None,
        log_level: str = "INFO",
        mayapy_path: Optional[str] = None,
    ):
        super().__init__()
        self.logger.setLevel(log_level)
        self._blender_path = blender_path
        # Host interpreter for the FBX -> .ma bake (see the mayapy_path property).
        self._mayapy_path = mayapy_path

    # ------------------------------------------------------------------ discovery
    @property
    def blender_path(self) -> Optional[str]:
        """The Blender executable (explicit, or discovered via the bridge's AppSpec)."""
        if not self._blender_path:
            self._blender_path = _SPEC.app.resolve()
        return self._blender_path

    @blender_path.setter
    def blender_path(self, value: Optional[str]) -> None:
        self._blender_path = value

    def require_blender(self) -> str:
        """Return :attr:`blender_path` or raise the spec's not-found error."""
        blender_exe = self.blender_path
        if not blender_exe:
            raise FileNotFoundError(_SPEC.app.not_found_message)
        return blender_exe

    # ------------------------------------------------------------------ discovery (browser API)
    @staticmethod
    def find_scenes(
        root_dir: str,
        recursive: bool = False,
        extensions: Optional[Sequence[str]] = None,
    ) -> List[str]:
        """Every importable Blender scene (``.blend``) under *root_dir* — sorted abs paths.

        The discovery half of the import: pairs with :meth:`import_scene` so a browser can
        list convertible Blender scenes with one call, using the SAME extension set the
        importer accepts. USD sources are import-capable too but are not *Blender scenes*, so
        they are intentionally excluded here. ``.blend1`` backups never match ``*.blend``.

        *extensions* narrows or widens that default — a browser listing *bakeable* rows
        passes :data:`BAKE_SOURCE_EXTENSIONS` (which adds ``.fbx``), or the subset the
        user has enabled.
        """
        if not (root_dir and os.path.isdir(root_dir)):
            return []
        inc = [f"*{ext}" for ext in (extensions or SUPPORTED_EXTENSIONS)]
        found = ptk.FileUtils.get_dir_contents(
            root_dir, content="filepath", recursive=recursive, inc_files=inc
        )
        return sorted(os.path.normpath(p) for p in found)

    # ------------------------------------------------------------------ conversion
    @staticmethod
    def _template(via: str):
        """The conversion template for *via*; raises on an unknown route."""
        try:
            return _TEMPLATES[via]
        except KeyError:
            raise ValueError(
                f"via must be one of {sorted(_TEMPLATES)}, got {via!r}"
            ) from None

    def render_script(
        self,
        src_path: str,
        out_path: str,
        *,
        via: str = "fbx",
        embed_textures: bool = False,
        include_animation: bool = True,
    ) -> str:
        """Render the Blender-side conversion script (exposed for tests/preview)."""
        context = {
            "SRC_PATH": str(src_path).replace("\\", "/"),
            "INCLUDE_ANIMATION": repr(bool(include_animation)),
        }
        if via == "usd":
            context["OUT_USD"] = str(out_path).replace("\\", "/")
            if embed_textures:
                self.logger.info(
                    "embed_textures has no USD-route equivalent (textures are "
                    "referenced on disk); ignored."
                )
        else:
            context["OUT_FBX"] = str(out_path).replace("\\", "/")
            context["EMBED_TEXTURES"] = repr(bool(embed_textures))
        return _templates.ScriptTemplate.render_template(self._template(via), context)

    def convert(
        self,
        src_path: str,
        out_path: str,
        *,
        via: str = "fbx",
        timeout: float = 600,
        **script_opts: Any,
    ) -> "ptk.ScriptRunResult":
        """Convert *src_path* to *out_path* in a fresh headless Blender (blocking)."""
        src = os.path.abspath(os.path.expanduser(os.path.expandvars(str(src_path))))
        if not os.path.isfile(src):
            raise FileNotFoundError(f"Blender scene not found: {src}")
        if not src.lower().endswith(SUPPORTED_EXTENSIONS):
            raise ValueError(
                f"Unsupported scene format: {src} (expected {SUPPORTED_EXTENSIONS})"
            )
        blender_exe = self.require_blender()
        self.logger.info(f"Converting {os.path.basename(src)} via {blender_exe} ...")
        result = self._run_script(
            blender_exe,
            self.render_script(src, out_path, via=via, **script_opts),
            artifact=out_path,
            timeout=timeout,
        )
        self.logger.info(
            f"Converted to {via.upper()} in {result.duration:.1f}s "
            f"({os.path.getsize(result.artifact) // 1024} KB)."
        )
        return result

    # Seam for tests (stub the Blender run without patching pythontk internals).
    @staticmethod
    def _run_script(app_exe, script_text, *, artifact, timeout, env=None):
        return ptk.ScriptRunner.run_script_to_artifact(
            app_exe,
            script_text,
            artifact=artifact,
            launch_args=lambda script_path: [*_LAUNCH_ARGS, script_path],
            timeout=timeout,
            env=env,
        )

    @classmethod
    def _cache_key(cls, src: str, script_opts: Dict[str, Any], via: str = "fbx") -> str:
        """Deterministic tag for the conversion cache: scene identity (path +
        mtime + size), the Blender-side options that shape the artifact, and
        the conversion template's own identity (per *via*) -- a template fix
        must invalidate stale cached payloads, or a retry after an upgrade
        replays the old bug."""
        return ptk.CachedArtifact.key(
            sorted(script_opts.items()), files=[src, cls._template(via)]
        )

    def _cached_conversion(
        self,
        src: str,
        *,
        via: str,
        use_cache: bool,
        timeout: float,
        script_opts: Dict[str, Any],
    ) -> "ptk.CachedArtifact.Result":
        """The cached FBX/USD conversion of *src*, produced on a miss.

        Shared by :meth:`import_scene` and :meth:`bake_scene`: both need the SAME
        intermediate, so a scene that was already imported bakes without a second
        Blender launch.
        """
        ext = ".usd" if via == "usd" else ".fbx"
        self._template(via)  # validate the route before any work
        return ptk.CachedArtifact("blender_to_mtk", extension=ext).get(
            self._cache_key(src, script_opts, via),
            lambda out: self.convert(src, out, via=via, timeout=timeout, **script_opts),
            sidecars=(".manifest.json",),
            use_cache=use_cache and os.path.isfile(src),
        )

    # ------------------------------------------------------------------ import
    def import_scene(
        self,
        src_path: str,
        *,
        via: str = "fbx",
        cleanup: bool = True,
        use_cache: bool = True,
        timeout: float = 600,
        fbx_options: Optional[Dict[str, Any]] = None,
        **script_opts: Any,
    ) -> List[str]:
        """Import the Blender scene at *src_path*; return the transforms created.

        Parameters:
            src_path: A ``.blend`` file — or a USD file
                (``.usd``/``.usda``/``.usdc``/``.usdz``), which short-circuits
                the round-trip entirely: Maya imports USD natively (mayaUsd),
                so no headless Blender, cache or manifest is involved
                (``via``/``cleanup``/``use_cache``/``timeout``/``fbx_options``
                are inert for USD sources).
            via: Conversion intermediate for ``.blend`` sources. ``"fbx"``
                (default) = classic material model + texture-manifest sidecar
                rebuilt through the ``GameShader`` engine. ``"usd"`` =
                ``wm.usd_export`` → mayaUsd import: materials arrive as native
                UsdPreviewSurface conversions (metallic / roughness / normal
                textures included, no manifest) and instancing survives (see
                the template docstrings).
            cleanup: Remove the intermediate artifact on success (kept on
                failure either way, with its path logged, for debugging). Not
                applied to cached payloads -- persistence is the cache's point.
            use_cache: Reuse a prior conversion of the identical scene
                (path + mtime + size + options + per-``via`` template key) --
                a cache hit skips the Blender launch entirely. Cached payloads
                live in the temp dir under the detached-policy lifecycle
                (stale-swept after ``max_age_days``). Texture edits flow
                through even on a hit: the payload references textures on
                disk (``embed_textures`` defaults off), so Maya always loads
                the current files.
            timeout: Max seconds for the Blender-side conversion.
            fbx_options: Forwarded to ``cmds.file`` for the FBX import
                (``via="fbx"`` only; the USD route imports with the native
                defaults).
            **script_opts: Blender-side knobs (``embed_textures`` /
                ``include_animation``; ``embed_textures`` is FBX-route only).
        """
        src = os.path.abspath(os.path.expanduser(os.path.expandvars(str(src_path))))
        if os.path.splitext(src)[1].lower() in USD_EXTENSIONS:
            # USD fast path: native import, no headless-Blender round-trip at all.
            from mayatk.env_utils.usd import UsdUtils

            if not os.path.isfile(src):
                raise FileNotFoundError(f"USD file not found: {src}")
            self.logger.info(
                f"USD source — importing natively (no Blender conversion): {src}"
            )
            imported = self._transforms(UsdUtils.import_scene(src))
            self.logger.info(f"Imported {len(imported)} object(s) from {src_path}.")
            return imported

        got = self._cached_conversion(
            src, via=via, use_cache=use_cache, timeout=timeout, script_opts=script_opts
        )
        out_path, tmp = got.path, got.scratch

        # Sidecar the FBX template writes for the textures FBX cannot carry
        # (metallic/roughness/ao and the packed game-engine maps). The USD
        # route needs no manifest -- its materials arrive natively.
        manifest_path = out_path + ".manifest.json"
        try:
            if via == "usd":
                from mayatk.env_utils.usd import UsdUtils

                new_nodes = UsdUtils.import_scene(out_path)
            else:
                new_nodes = self._import_fbx(out_path, fbx_options)
        except Exception:
            if tmp is not None and os.path.isfile(out_path):
                self.logger.warning(
                    f"Keeping intermediate {via.upper()} for debugging: {out_path}"
                )
            raise
        if os.path.isfile(manifest_path):
            # Structurally non-fatal: a bad sidecar must never abort an
            # import whose FBX already landed (materials just stay classic).
            try:
                self._apply_texture_manifest(manifest_path, new_nodes)
            except Exception as e:  # noqa: BLE001
                self.logger.warning(
                    f"Texture-manifest rebuild failed ({e}); keeping FBX materials."
                )
        if cleanup and tmp is not None:
            tmp.cleanup()
        imported = self._transforms(new_nodes)
        self.logger.info(f"Imported {len(imported)} object(s) from {src_path}.")
        return imported

    # ------------------------------------------------------------------ bake (FBX -> .ma)
    @property
    def mayapy_path(self) -> Optional[str]:
        """The headless ``mayapy`` used for the bake — this host's own interpreter.

        Unlike :attr:`blender_path` (a foreign app, discovered through an ``AppSpec``),
        the bake runs the SAME Maya the panel runs in, so the interpreter ships beside
        the running binary: ``sys.executable``'s ``bin`` dir, else ``MAYA_LOCATION/bin``.
        """
        if not self._mayapy_path:
            candidates = []
            if sys.executable:
                candidates.append(os.path.dirname(sys.executable))
            location = os.environ.get("MAYA_LOCATION")
            if location:
                candidates.append(os.path.join(location, "bin"))
            name = "mayapy.exe" if os.name == "nt" else "mayapy"
            for directory in candidates:
                candidate = os.path.join(directory, name)
                if os.path.isfile(candidate):
                    self._mayapy_path = candidate
                    break
            else:
                self._mayapy_path = shutil.which("mayapy")
        return self._mayapy_path

    @mayapy_path.setter
    def mayapy_path(self, value: Optional[str]) -> None:
        self._mayapy_path = value

    def require_mayapy(self) -> str:
        """Return :attr:`mayapy_path` or raise an error naming what's missing."""
        mayapy = self.mayapy_path
        if not mayapy:
            raise FileNotFoundError(
                "No mayapy interpreter found for the bake (not beside sys.executable, "
                "not under MAYA_LOCATION/bin, not on PATH). Set "
                "BlenderSceneImport.mayapy_path."
            )
        return mayapy

    def render_bake_script(self, fbx_path: str, out_path: str) -> str:
        """Render the Maya-side FBX->.ma bake script (exposed for tests/preview)."""
        return _templates.ScriptTemplate.render_template(
            _BAKE_TEMPLATE,
            {
                "SRC_FBX": str(fbx_path).replace("\\", "/"),
                "OUT_MA": str(out_path).replace("\\", "/"),
                # The child is the same Maya version, so the parent's sys.path entries
                # are valid there -- this is what makes the shared manifest replay
                # (mayatk in the child) reliable rather than best-effort.
                "EXTRA_SYS_PATH": repr(list(sys.path)),
            },
        )

    def bake(self, fbx_path: str, out_path: str, *, timeout: float = 600) -> Any:
        """Bake *fbx_path* into the .ma at *out_path* in a fresh ``mayapy`` (blocking)."""
        fbx = os.path.abspath(os.path.expanduser(os.path.expandvars(str(fbx_path))))
        if not os.path.isfile(fbx):
            raise FileNotFoundError(f"FBX not found: {fbx}")
        mayapy = self.require_mayapy()
        self.logger.info(f"Baking {os.path.basename(fbx)} to .ma via {mayapy} ...")
        env = dict(os.environ)
        env.update(_FAST_MAYA_ENV)
        result = self._run_bake_script(
            mayapy,
            self.render_bake_script(fbx, out_path),
            artifact=out_path,
            timeout=timeout,
            env=env,
        )
        self.logger.info(
            f"Baked to .ma in {result.duration:.1f}s "
            f"({os.path.getsize(result.artifact) // 1024} KB)."
        )
        return result

    # Seam for tests (stub the mayapy run without patching pythontk internals).
    @staticmethod
    def _run_bake_script(app_exe, script_text, *, artifact, timeout, env=None):
        return ptk.ScriptRunner.run_script_to_artifact(
            app_exe, script_text, artifact=artifact, timeout=timeout, env=env
        )

    def bake_scene(
        self,
        src_path: str,
        *,
        use_cache: bool = True,
        timeout: float = 600,
        **script_opts: Any,
    ) -> str:
        """Bake *src_path* to a cached ``.ma`` and return its path — the reference path.

        Maya references FBX natively, so this is not a capability gap the way the
        Blender side's is; it is deliberate **symmetry**. Both panels reference a cached
        *native* scene, so the referenced-file surface (edits, namespaces, reload,
        relocate, display overrides) behaves identically no matter which DCC the row came
        from. A ``.blend`` is converted to FBX in a headless Blender (the cached
        intermediate :meth:`import_scene` already uses), then baked to a ``.ma`` in a
        headless ``mayapy``; an ``.fbx`` source skips straight to the bake.

        Both stages are cached independently, and the bake's key includes the FBX's
        identity **and the bake template's**, so a template fix invalidates stale bakes
        (a retry after an upgrade must not replay the old bug).

        Parameters:
            src_path: A ``.blend`` / ``.fbx`` file.
            use_cache: Reuse a prior conversion + bake of the identical source.
            timeout: Max seconds for EACH headless stage.
            **script_opts: Blender-side conversion knobs (``embed_textures`` /
                ``include_animation``); inert for an ``.fbx`` source.

        Returns:
            str: Path to the cached ``.ma`` — pass it to ``cmds.file(reference=True)``.
        """
        src = os.path.abspath(os.path.expanduser(os.path.expandvars(str(src_path))))
        ext = os.path.splitext(src)[1].lower()
        if ext not in BAKE_SOURCE_EXTENSIONS:
            raise ValueError(
                f"Unsupported bake source: {src} (expected {BAKE_SOURCE_EXTENSIONS})"
            )
        if not os.path.isfile(src):
            raise FileNotFoundError(f"Scene not found: {src}")

        if ext == ".fbx":
            fbx_path, conversion = src, None
        else:
            conversion = self._cached_conversion(
                src,
                via="fbx",
                use_cache=use_cache,
                timeout=timeout,
                script_opts=script_opts,
            )
            fbx_path = conversion.path

        got = ptk.CachedArtifact("blender_bake_mtk", extension=".ma").get(
            ptk.CachedArtifact.key(files=[fbx_path, _BAKE_TEMPLATE]),
            lambda out: self.bake(fbx_path, out, timeout=timeout),
            use_cache=use_cache,
        )
        # The FBX scratch is consumed once the bake has read it; the .ma scratch is NOT
        # cleaned up -- the caller references that file, so it must outlive this call
        # (an uncached bake therefore lives under the scoped store's stale sweep).
        if conversion is not None and conversion.scratch is not None:
            conversion.scratch.cleanup()
        # Rewritten on a cache hit too: cheap, and it self-heals a sidecar lost to a
        # partial sweep (without it the panel silently forgets the row is referenced).
        self._write_bake_source(got.path, src)
        self.logger.info(f"Baked {src_path} -> {got.path}")
        return got.path

    def _write_bake_source(self, baked_path: str, src: str) -> None:
        """Record beside *baked_path* which foreign scene it was baked from."""
        import json

        try:
            with open(baked_path + BAKE_SOURCE_SUFFIX, "w", encoding="utf-8") as fh:
                json.dump({"source": os.path.abspath(src)}, fh)
        except OSError as e:  # cosmetic bookkeeping — never fail a completed bake
            self.logger.debug(f"Could not write the bake source sidecar: {e}")

    @staticmethod
    def bake_source(baked_path: str) -> Optional[str]:
        """The foreign scene *baked_path* was baked from, or None if it is not a bake.

        The inverse of :meth:`bake_scene` — lets a browser map a reference back to the
        source row the user actually sees.
        """
        import json

        try:
            with open(baked_path + BAKE_SOURCE_SUFFIX, "r", encoding="utf-8") as fh:
                return json.load(fh).get("source") or None
        except (OSError, ValueError):
            return None

    # ------------------------------------------------------------------ Maya side
    @staticmethod
    def _transforms(nodes: List[str]) -> List[str]:
        """The transform subset of *nodes* (behavior parity: blendertk's
        ``import_scene`` returns Blender objects, i.e. transform-level items)."""
        import maya.cmds as cmds

        return cmds.ls(nodes, type="transform") or []

    def _import_fbx(
        self, fbx_path: str, fbx_options: Optional[Dict[str, Any]] = None
    ) -> List[str]:
        """Import *fbx_path* into the current scene; return ALL new nodes
        (the manifest apply needs the shading engines, not just transforms).

        Deterministic baseline first: the plugin's import options are global
        and sticky (the user's last interactive FBX import shapes this one),
        and the factory default mode "merge" can RETARGET animation onto
        same-named nodes already in the scene -- reset, then pin "add". The
        import mirror of the conversion template's FBXResetExport rule;
        best-effort, so a plugin build missing a command can't block the
        import.
        """
        import maya.cmds as cmds

        from mayatk.env_utils.fbx_utils import FbxUtils

        FbxUtils.load_plugin()
        try:
            FbxUtils.reset_import()
        except RuntimeError as e:
            self.logger.debug(f"FBXResetImport unavailable: {e}")
        FbxUtils._apply_import_options({"FBXImportMode": "add"})
        options = dict(
            i=True,
            type="FBX",
            ignoreVersion=True,
            returnNewNodes=True,
        )
        options.update(fbx_options or {})
        return cmds.file(fbx_path, **options) or []

    def _apply_texture_manifest(self, manifest_path: str, new_nodes: List[str]) -> None:
        """Rebuild manifest materials natively from the conversion's sidecar.

        The FBX carries only the classic-model approximation (color / normal /
        emissive); the manifest carries each textured material's ORIGINAL image
        files, which the game-shader engine (:class:`GameShader`) wires into a
        standardSurface network -- including the packed game-engine maps FBX has
        no slot for (``Metallic_Smoothness``, ``MSAO``, ``ORM``), smoothness ->
        roughness inversion and channel splits included. Classification is by
        filename via the shared ``ptk.MapFactory`` SSoT, so conventionally named
        sets round-trip; an entry whose files classify to nothing keeps its
        FBX material (logged). Per-entry failures degrade, never abort the import.
        """
        import json

        import maya.cmds as cmds

        try:
            with open(manifest_path, "r", encoding="utf-8") as fh:
                manifest = json.load(fh)
        except Exception as e:
            self.logger.warning(
                f"Texture manifest unreadable ({e}); keeping FBX materials."
            )
            return
        if not isinstance(manifest, dict):
            self.logger.warning("Texture manifest malformed; keeping FBX materials.")
            return

        # Imported shading engines by their surface material's short name --
        # the Maya analogue of blendertk's slot scan. Restricting to NEW nodes
        # keeps a pre-existing same-named scene material out of the swap.
        sgs_by_material: Dict[str, List[str]] = {}
        for sg in cmds.ls(new_nodes, exactType="shadingEngine") or []:
            sources = cmds.listConnections(
                f"{sg}.surfaceShader", source=True, destination=False
            )
            if sources:
                short = sources[0].split("|")[-1].split(":")[-1]
                sgs_by_material.setdefault(short, []).append(sg)

        # Fallback matching only (see below): new transforms by FBX-safe short name.
        by_short: Dict[str, List[str]] = {}
        for node in cmds.ls(new_nodes, type="transform") or []:
            short = node.split("|")[-1].split(":")[-1]
            by_short.setdefault(
                _BlenderSceneImportInternal._fbx_safe_name(short), []
            ).append(node)

        entries = manifest.get("materials", [])
        # Every entry's exact FBX-spelled target. The clash-rename suffix match
        # below must never claim a name that is ANOTHER entry's exact target --
        # "M_test" (renamed by the importer) must not steal "M_test2"'s SGs.
        wants = {
            _BlenderSceneImportInternal._fbx_safe_name(e.get("fbx_material") or "")
            for e in entries
            if isinstance(e, dict)
        }
        # Nor a name that truly exists in the .blend at all: the importer only
        # renames on CLASH, so an exact .blend spelling seen among the imported
        # SGs is its own material -- an UNTEXTURED "Mat2" beside textured "Mat"
        # has no manifest entry, and without this it would read as "Mat renamed
        # to Mat2" and get repainted with Mat's rebuilt textures. Older
        # manifests lack the key; the entries-only guard above still applies.
        wants |= {
            _BlenderSceneImportInternal._fbx_safe_name(n)
            for n in manifest.get("scene_materials", [])
            if isinstance(n, str)
        }

        def target_sgs(want: str) -> Dict[str, List[str]]:
            """Shading groups for *want*: exact importer spelling first; else
            tolerate Maya's rename-on-clash digit suffix (sibling-safe)."""
            if want in sgs_by_material:
                return {want: sgs_by_material[want]}
            return {
                short: sgs
                for short, sgs in sgs_by_material.items()
                if _BlenderSceneImportInternal._matches_fbx_name(short, want)
                and short not in wants
            }

        for entry in entries:
            name = entry.get("name", "?")
            try:
                listed = entry.get("files", [])
                files = [f for f in listed if os.path.isfile(f)]
                if not files:
                    # Never silent: untextured materials with no explanation
                    # cost a debugging session (live production report on the
                    # mirror direction).
                    if listed:
                        self.logger.warning(
                            f"{name}: manifest texture file(s) missing on disk, "
                            f"e.g. {listed[0]} -- material stays untextured."
                        )
                    else:
                        self.logger.warning(
                            f"{name}: no texture paths resolved during conversion "
                            "-- the .blend's images may be packed into the file "
                            "or need relinking. Material stays untextured."
                        )
                    continue
                new_sg = self._rebuild_material(files, name)
                if new_sg is None:  # nothing classified -- keep the FBX material
                    self.logger.warning(
                        f"{name}: no texture classified by filename; keeping the "
                        "FBX-carried material."
                    )
                    continue

                # Primary: transfer members at the SHADING-GROUP level, keyed by
                # the FBX-spelled material name (the importer may rename-on-clash
                # with a digit suffix). Renderable sets are exclusive, so
                # forceElement moves per-face assignments intact -- the Maya
                # analogue of blendertk's slot-level swap.
                want = _BlenderSceneImportInternal._fbx_safe_name(
                    entry.get("fbx_material") or ""
                )
                replaced, swapped = [], 0
                if want:
                    for short, sgs in target_sgs(want).items():
                        for old_sg in sgs:
                            members = cmds.sets(old_sg, query=True) or []
                            if members:
                                cmds.sets(members, forceElement=new_sg)
                                swapped += 1
                            old_mats = (
                                cmds.listConnections(
                                    f"{old_sg}.surfaceShader",
                                    source=True,
                                    destination=False,
                                )
                                or []
                            )
                            for old in old_mats:
                                if old not in replaced:
                                    replaced.append(old)
                if swapped:
                    self._purge_orphans(replaced)
                    self.logger.info(
                        f"Rebuilt material {name} from {len(files)} "
                        f"file(s) into {swapped} shading group(s)."
                    )
                    continue

                # Fallback (importer renamed the material): whole-object assign.
                targets = [
                    node
                    for member in entry.get("objects", [])
                    for node in by_short.get(
                        _BlenderSceneImportInternal._fbx_safe_name(member), []
                    )
                ]
                if not targets:
                    self._purge_rebuilt(new_sg)  # nothing to attach it to
                    self.logger.warning(
                        f"{name}: no matching shading group or object found."
                    )
                    continue
                cmds.sets(targets, forceElement=new_sg)
                self.logger.info(
                    f"Rebuilt material {name} from {len(files)} file(s) "
                    f"on {len(targets)} object(s) (object-level fallback)."
                )
            except Exception as e:
                self.logger.warning(f"Manifest entry {name} skipped: {e}")

    # Seam for tests (stub the GameShader build without live texture prep).
    @staticmethod
    def _rebuild_material(files: List[str], name: str) -> Optional[str]:
        """Build a native shader network from *files*; return its shading group.

        ``standardSurface`` (not Stingray) so the result renders in any viewport
        without the ShaderFX plugin. The packed-map config flags are enabled per
        detected map type -- they gate each packed map as a desired OUTPUT in
        ``MapFactory``'s prep (MSAO's flag is ``mask_map`` per the registry SSoT).
        """
        import maya.cmds as cmds

        from mayatk.mat_utils.game_shader import GameShader

        flags = {
            "Metallic_Smoothness": "metallic_smoothness",
            "MSAO": "mask_map",
            "ORM": "orm_map",
            "Albedo_Transparency": "albedo_transparency",
            "Emissive": "emissive",
            "Ambient_Occlusion": "ambient_occlusion",
            "Opacity": "opacity",
        }
        kwargs: Dict[str, Any] = {"shader_type": "standard_surface"}
        for path in files:
            map_type = ptk.MapFactory.resolve_map_type(path)
            if map_type in flags:
                kwargs[flags[map_type]] = True

        # Blender datablock names ("Material.001") are not legal Maya node
        # names -- sanitize for the created network (matching elsewhere uses
        # the manifest strings, so this is cosmetic only).
        node = GameShader(log_level="WARNING").create_network(
            files, name=_BlenderSceneImportInternal._maya_safe_name(name), **kwargs
        )
        if not node:
            return None
        node = str(node)
        if cmds.nodeType(node) == "shadingEngine":
            return node
        sgs = cmds.listConnections(node, type="shadingEngine") or []
        return sgs[0] if sgs else None

    def _purge_orphans(self, materials: List[str]) -> None:
        """Remove replaced materials (their emptied shading groups and
        now-exclusive texture nodes included) once unused.

        Hygiene only -- every step is best-effort and must never break the
        import.
        """
        import maya.cmds as cmds

        for mat in materials:
            try:
                if not cmds.objExists(mat):
                    continue
                sgs = cmds.listConnections(mat, type="shadingEngine") or []
                if any(cmds.sets(sg, query=True) for sg in sgs):
                    continue  # still assigned somewhere -- keep it
                textures = [
                    n
                    for n in (cmds.listHistory(mat) or [])
                    if n != mat
                    and cmds.nodeType(n) in ("file", "place2dTexture", "bump2d")
                ]
                cmds.delete(list(set(sgs)) + [mat])
                for node in textures:
                    if cmds.objExists(node) and not cmds.listConnections(
                        node, source=False, destination=True
                    ):
                        cmds.delete(node)
            except Exception as e:  # noqa: BLE001
                self.logger.debug(f"Orphan purge skipped: {e}")

    def _purge_rebuilt(self, sg: str) -> None:
        """Delete an unattachable rebuilt network (SG + its surface material)."""
        import maya.cmds as cmds

        try:
            mats = (
                cmds.listConnections(
                    f"{sg}.surfaceShader", source=True, destination=False
                )
                or []
            )
            self._purge_orphans(mats)
            if cmds.objExists(sg):
                cmds.delete(sg)
        except Exception as e:  # noqa: BLE001
            self.logger.debug(f"Rebuilt-network purge skipped: {e}")


__all__ = ["BlenderSceneImport"]
