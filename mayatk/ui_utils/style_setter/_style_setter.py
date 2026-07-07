# !/usr/bin/python
# coding=utf-8
"""Match Maya's scriptable viewport colors to another DCC's look.

The blendertk counterpart (``btk.StyleSetter``) reskins Blender's full widget chrome through
Blender's native ``interface_theme`` preset system. Maya has no equivalent for widget chrome —
its Qt style (``adskdarkflatui``) is a compiled native ``QStyle`` with no exported stylesheet or
theme file, so there's nothing scriptable to reskin there (see ``blendertk/docs/STRUCTURE.md``).

What Maya *does* expose scriptably is its "Colors" preferences — ``cmds.displayRGBColor``
(direct RGBA colors: viewport background, 300+ named UI colors), ``cmds.colorIndex`` (a 31-slot
indexed palette), and ``cmds.displayColor`` (maps an object/component-type name to a palette
index, separately for its dormant/active state). A "style" here is a curated JSON overlay of
those three: the viewport background and the dormant grid/wireframe colors — the visually
dominant, always-on part of Maya's look — verified empirically against a live Maya 2025 instance
(see the package ``CHANGELOG`` / ``reference_blender_dcc_style_matching`` memo).

**JSON here is NOT Maya's "native format" — Maya has no native named-preset format for colors at
all.** This is the key asymmetry with blendertk: Blender ships a genuine native theme-preset SYSTEM
(named ``.xml`` files in a preset dir that a dropdown scans, written/read by dedicated operators),
so blendertk defers to it (native XML) and gets the Preferences > Themes dropdown for free. Maya has
no counterpart — its "Colors" editor only offers Save / Reset-to-saved / Reset-to-factory, all of
which act on the user's *single* active prefs file in place (``userRGBColors2.mel``); there is no
named-preset concept, no preset dir, no selector (confirmed via ``colorPrefWnd.mel``). Maya's only
serialization is that MEL dump, and it's a flat snapshot of the active state, not a portable named
template. So a bespoke format is unavoidable, and JSON is the right choice over hand-shipping ``.mel``:
it carries the *structured* data the apply step needs (a ``rgb`` map applied via ``displayRGBColor``
plus a ``display_color`` map whose grid/edge entries are resolved to a live ``colorIndex`` slot at
apply time — a per-session lookup a static ``.mel`` command dump couldn't do without hardcoding slot
numbers). This is a documented, justified format divergence from blendertk (same ``styles/`` dir,
same ``StyleSetter`` API), not undocumented drift.

Scope boundary — deliberately narrow: a style here only ever touches the specific keys its JSON
defines (the viewport background + grid + dormant polygon-edge color for the shipped "Blender"
style). It never calls Maya's own bulk factory-reset (``rf=True``) on ``displayRGBColor`` /
``colorIndex`` / ``displayColor``, which would also blank out hundreds of unrelated colors
(node editor, hypergraph, script editor syntax, …) that have nothing to do with "the look" and
that this tool has no business touching.

``import maya.cmds`` is a module-level import (mayatk convention — these modules require a live
Maya runtime to import, unlike blendertk's DCC-optional ``bpy``). All three underlying commands
are GUI-mode only and raise in batch/mayapy (``cmds.displayRGBColor`` et al. are unavailable in
batch mode) — this module requires an interactive Maya session.
"""
import os
import glob
import json

import maya.cmds as cmds

_HERE = os.path.dirname(__file__)
# Shipped styles live under ``styles/`` — same dir name as blendertk's ``style_setter/styles/``
# (parity with the ``StyleSetter`` / ``list_styles`` / ``set_style`` vocabulary). The file *format*
# differs by necessity (bespoke ``.json`` here — Maya has no native named-preset format — vs native
# Blender theme ``.xml`` there; see the module docstring), but the location does not.
STYLES_DIR = os.path.join(_HERE, "styles")


# ---- shipped-style discovery ----------------------------------------------------------------
def list_styles():
    """Names of the shipped color styles (e.g. ``["Blender"]``)."""
    return sorted(os.path.splitext(os.path.basename(p))[0] for p in glob.glob(os.path.join(STYLES_DIR, "*.json")))


def _shipped_path(name):
    return os.path.join(STYLES_DIR, f"{name}.json")


def _load_style(name):
    path = _shipped_path(name)
    if not os.path.isfile(path):
        raise FileNotFoundError(f"No color style named {name!r} (looked in {STYLES_DIR}).")
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


# ---- apply the underlying Maya color primitives -----------------------------------------------
def _apply_rgb(name, rgba):
    cmds.displayRGBColor(name, *rgba)


def _apply_display_color(name, spec):
    for state, rgb in spec.items():
        idx = cmds.displayColor(name, query=True, **{state: True})
        cmds.colorIndex(int(idx), *rgb)


def _apply_snapshot(snapshot):
    for name, rgba in snapshot.get("rgb", {}).items():
        _apply_rgb(name, rgba)
    for name, spec in snapshot.get("display_color", {}).items():
        _apply_display_color(name, spec)


def set_style(name, persist=False):
    """Switch Maya's viewport colors to the named style — a targeted overlay of just the keys
    that style's JSON defines (see the module scope-boundary note; this never bulk-resets Maya's
    hundreds of unrelated colors).

    Parameters:
        name: A shipped style from :func:`list_styles` (e.g. ``"Blender"``).
        persist: Also write Maya's own prefs to disk (``cmds.savePrefs(colors=True)``) so the
            change survives a restart — otherwise it's live-session only.
    """
    _apply_snapshot(_load_style(name))
    if persist:
        cmds.savePrefs(colors=True)


# ---- the full template set a UI selector drives -------------------------------------------
def list_templates():
    """Ordered ``{display_name: token}`` of everything a style-selector combo offers: each shipped
    style (e.g. ``Blender``). ``token`` is what :func:`apply_template` takes.

    This is the mayatk counterpart to blendertk's :func:`list_templates`, but Maya has no *native*
    color-preset selector to mirror (its "Colors" editor offers no named-template dropdown — only
    Save/Reset), so the set is just our own shipped styles, not a host-provided list.
    """
    return {name: name for name in list_styles()}


def apply_template(name, persist=False):
    """Apply a selection from :func:`list_templates` by its token — a shipped style name, applied
    via :func:`set_style`. The uniform ``(name)`` surface lets a shared UI slot drive Maya and
    Blender identically (Blender's :func:`apply_template` takes a preset filepath instead — same
    method name + role, host-appropriate token)."""
    set_style(name, persist=persist)


class StyleSetter:
    """Public namespace for the style-setter helpers (``mtk.StyleSetter.set_style("Blender")`` …).

    Mirrors ``blendertk``'s ``StyleSetter`` at the name + behavior level (registered as just the
    class, like other mayatk tool namespaces), scoped to what Maya actually exposes scriptably —
    its "Colors" preferences — rather than widget chrome, which Maya has no equivalent for.
    """

    list_styles = staticmethod(list_styles)
    list_templates = staticmethod(list_templates)
    apply_template = staticmethod(apply_template)
    set_style = staticmethod(set_style)
