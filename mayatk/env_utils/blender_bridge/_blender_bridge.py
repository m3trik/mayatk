# !/usr/bin/python
# coding=utf-8
"""Blender bridge engine -- export the Maya selection and run a chosen import template in Blender.

The Maya half of the Maya<->Blender object hand-off (``mtk.BlenderBridge`` <-> ``btk.MayaBridge``).
A thin :class:`pythontk.ScriptLaunchBridge` subclass: the shared ``send()`` skeleton (resolve ->
preflight -> produce payload -> deliver), the template discovery / ``BRIDGE_MODES`` / ``__KEY__``
substitution machinery, and the render-script-then-launch-a-fresh-app deliverer all live upstream in
:mod:`pythontk.core_utils.app_handoff`. The Maya-side selection + FBX export come from
:class:`mayatk.env_utils.handoff_export.MayaExportMixin` (shared with the Unity bridge). This file
owns only the Blender-specific bits, declared as a :class:`pythontk.ScriptLaunchSpec` dataclass
(executable discovery + the ``--python`` launch args) plus the parameter bindings.

Picking a different template is the "dynamic script selection". One ``import`` recipe ships -- a
single options-driven script whose ``CLEAR_SCENE`` / ``FRAME_VIEW`` booleans cover what used to be
three near-identical templates -- and any extra ``templates/*.py`` the user drops in is discovered
the same way. Co-located with its panel
(``blender_bridge_slots.BlenderBridgeSlots`` + ``blender_bridge.ui``) under ``env_utils``;
discovered by :class:`mayatk.ui_utils.MayaUiHandler`. ``import maya.cmds`` is deferred so resolving
the package surface never needs a running Maya.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pythontk as ptk
from pythontk.core_utils import script_template as _templates
from pythontk.core_utils.script_template import SEND_TO

from mayatk.env_utils.handoff_export import MayaExportMixin


_PKG_DIR = Path(__file__).resolve().parent
_TEMPLATE_DIR = _PKG_DIR / "templates"


# Declarative Blender hand-off config (target discovery + the ``--python`` launch args). Blender
# runs the rendered template on startup, detached, as an interactive GUI (NOT ``--background``): it
# opens for the artist and Maya returns control immediately. A FRESH instance every time
# (session-safety rule).
_SPEC = ptk.ScriptLaunchSpec(
    # ``$BLENDER_EXE`` / ``$BLENDER`` -> ``AppLauncher.find_app`` -> a scan of
    # ``Program Files\\Blender Foundation\\Blender *`` (highest version wins).
    app=ptk.AppSpec(
        name="Blender",
        env_vars=("BLENDER_EXE", "BLENDER"),
        app_names=("blender",),
        scan_globs=(r"{program_files}\Blender Foundation\Blender *\blender.exe",),
        not_found_msg=(
            "Blender executable not found. Install Blender or set $BLENDER_EXE / "
            "BlenderBridge.blender_path."
        ),
    ),
    template_dir=_TEMPLATE_DIR,
    launch_args=lambda script_path: ["--python", script_path],
    payload_prefix="mtk_to_blender",
)


# Module-level template discovery -- kept so the slots (and tests) can list templates without a
# live engine. Thin wrappers over the shared :mod:`pythontk.core_utils.script_template` helpers.
def list_templates() -> List[Path]:
    """User-visible templates in ``templates/`` (skips underscore-prefixed)."""
    return _templates.list_templates(_TEMPLATE_DIR, ".py")


def template_modes(template_path: Path) -> Tuple[str, ...]:
    """Modes a template declares via ``BRIDGE_MODES``; ``("send_to",)`` fallback."""
    return _templates.template_modes(template_path, (SEND_TO,))


def list_template_modes() -> List[Tuple[str, str]]:
    """``[(stem, mode), ...]`` for every (template, mode) pairing."""
    return _templates.list_template_modes(_TEMPLATE_DIR, ".py", (SEND_TO,))


class BlenderBridge(MayaExportMixin, ptk.ScriptLaunchBridge):
    """Export the Maya selection and run a chosen Blender import template.

    Named after its target app (``BlenderBridge``), mirroring ``MarmosetBridge``; the Blender-side
    counterpart is ``blendertk.MayaBridge``. All Blender-specific config is the :data:`_SPEC`
    dataclass; this class adds only the Maya parameter bindings.
    """

    spec = _SPEC

    def __init__(self, blender_path: Optional[str] = None):
        super().__init__(app_path=blender_path)

    # Back-compat alias: existing callers / tests use ``.blender_path``.
    @property
    def blender_path(self) -> Optional[str]:
        return self.app_path

    @blender_path.setter
    def blender_path(self, value: Optional[str]) -> None:
        self.app_path = value

    # ------------------------------------------------------------------ parameter bindings
    def params_defaults(self) -> Dict[str, Any]:
        from mayatk.env_utils.blender_bridge import parameters as _params

        return _params.defaults()

    def render_context(self, params: Dict[str, Any]) -> Dict[str, str]:
        from mayatk.env_utils.blender_bridge import parameters as _params

        return _params.render_context(params)


# -----------------------------------------------------------------------------

if __name__ == "__main__":
    bridge = BlenderBridge()
    try:
        import maya.cmds as cmds

        sel = cmds.ls(selection=True) or []
    except ModuleNotFoundError:
        sel = []
    # bridge.send(sel)                                  # default: import template
    # bridge.send(sel, template="import_and_frame")
    # bridge.send(sel, params={"INCLUDE_MATERIALS": False})
    bridge.send(sel)
