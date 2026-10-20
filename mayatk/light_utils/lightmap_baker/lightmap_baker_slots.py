# !/usr/bin/python
# coding=utf-8
"""The Lightmap Baker panel: Switchboard slots for ``lightmap_baker.ui``.

A thin driver over :class:`~mayatk.LightmapBaker`, holding no bake logic and no
bake policy either: the checks a bake runs (Arnold, the authored-light upgrade,
the lights-off refusal, the unlit verdict) live on the engine's
:meth:`~mayatk.LightmapBaker.bake`, so a script bakes as safely as the panel.
``MayaUiHandler`` finds this class by name, as it finds every co-located panel.
"""

import os
from typing import Any, Callable, Dict, List, Optional, Tuple

try:
    import maya.cmds as cmds
except ImportError:
    cmds = None

import pythontk as ptk

from mayatk.mat_utils.texture_baker import TextureBaker
from mayatk.mat_utils.bake_sets import (
    LightmapBakeSet,
    LightmapExcludeSet,
    LightmapIsolateSet,
)
from mayatk.node_utils.data_nodes import DataNodes
from mayatk.light_utils.lightmap_baker.lightmap_baker import LightmapBaker


class LightmapBakerSlots(ptk.LoggingMixin, ptk.HelpMixin):
    """Switchboard slots for the ``lightmap_baker.ui`` panel.

    Composition over inheritance: a thin driver over :class:`LightmapBaker`
    (the workflow) — no bake logic lives here, and no bake policy.
    **Bake Lightmaps** (``b000``) hands the objects in Scope and the dials to
    :meth:`LightmapBaker.bake`, which checks the scene, bakes, and wires the
    result up so nothing is left to do afterward: the full PBR material and
    texture UVs are kept, lighting lands on UV2, and the engine metadata is
    stamped on the shared ``data_export`` carrier. The panel then reports what
    the bake returned. The maps survive; the engine composites.

    Nothing is reverted before a bake. An object in the scene's Exclude set
    (:class:`LightmapExcludeSet`, edited by the Exclude row) keeps any map it
    has and still lights the rest, and so does an object the bake does not
    finish. The header menu's **Revert to Source** undoes the wiring, once
    confirmed.

    The **Preset** combo (``cmb000``) is uitk's preset template
    (:meth:`PresetManager.wire_combo`) in semantic mode over
    :meth:`LightmapBaker.preset_store` -- the store
    :meth:`LightmapBaker.from_preset` reads, so a preset saved here is also a
    headless bake recipe. :meth:`_preset_fields` is the one map between its
    keys and the widgets, which stay the source of truth at bake time.
    """

    # Packing labels for the Packing combobox (cmb002). Atlas by Material
    # (index 1, the default) consolidates a material group into one shared EXR
    # + a per-object scaleOffset rect; Per-Object keeps one full-resolution map
    # each; _packing() reads it back.
    _PACKING_LABELS = ("Per-Object (one map each)", "Atlas by Material (shared map)")

    # Fixed lightmap sizes (square, px) for the Resolution combobox
    # (cmb_resolution). Power-of-two atlas sizes; every Quality preset lands on
    # one of these. _resolution() reads the selection back as an int.
    _RESOLUTIONS = (256, 512, 1024, 2048, 4096)

    # Scope labels for the Scope combobox (cmb_scope): which objects b000 bakes.
    # Selected (index 0, default) preserves the prior selection-only behavior;
    # _scope() / _scope_objects() resolve it to the mesh transforms to bake.
    # Changed (LightmapBaker.rebake_changed: what changed since the last bake,
    # and what that shades) is APPENDED: the combo persists by INDEX, so a
    # saved choice of the first three reopens as itself.
    _SCOPE_LABELS = ("Selected", "Visible", "Scene", "Changed")

    #: The panel's switches, ``{preset key: (field, default)}``. Each rides the
    #: option box of the field it QUALIFIES rather than a checkbox row of its
    #: own: the lights that light the bake are part of what Scope gathers --
    #: every scene light or the Scope's own, the environment, the hidden ones
    #: -- adaptive sampling is how the Samples are spent, denoise is what the
    #: map ships at that Resolution, Beside Material Textures redirects the
    #: Output Directory, and the reflection probe is what the bake ships beside
    #: the maps Packing lays out. So each reads as a qualifier on a control
    #: rather than a row that happens to sit nearby. A field carrying several
    #: holds them in this order (:meth:`_toggle`).
    #:
    #: The keys are the preset store's (:attr:`LightmapBaker.PRESET_BOOL_KEYS`),
    #: so :meth:`_preset_fields` builds its entries straight from here and
    #: :meth:`_wire_toggle` derives each settings key from the same name.
    _TOGGLES: Dict[str, Tuple[str, bool]] = {
        "include_scene_lights": ("cmb_scope", True),
        "include_environment": ("cmb_scope", True),
        "include_hidden_lights": ("cmb_scope", False),
        "adaptive": ("spn_samples", True),
        "denoise": ("cmb_resolution", True),
        "beside_textures": ("txt_output_dir", False),
        "reflection_probe": ("cmb002", True),
    }

    #: The tier a panel opened for the first time shows: the .ui's dial
    #: defaults are its values, so naming it costs nothing and says which
    #: tier the untouched dials are.
    _DEFAULT_PRESET = "mobile"
    #: Settings key recording that the default preset was seeded once (see
    #: :meth:`cmb000_init`).
    _PRESET_SEEDED_KEY = "lightmap_baker_preset_seeded"

    # Footer tail for a per-object bake.
    _LIGHTING_ONLY_TAIL = (
        "Maps kept; lightmap + Unity metadata stamped. Export the FBX."
    )

    def __init__(self, switchboard, log_level: str = "WARNING"):
        super().__init__()
        self.logger.setLevel(log_level)

        self.sb = switchboard
        self.ui = self.sb.loaded_ui.lightmap_baker

        # Output dir of the most recent bake (reported in the footer).
        self._last_output_dir: Optional[str] = None
        # Workflow instance, rebuilt per bake from the current dials. commit /
        # revert persist their state on the mesh, so revert works even from a
        # fresh instance / reopened scene.
        self._baker: Optional[LightmapBaker] = None
        # The Preset combo's manager, built by cmb000_init.
        self._presets = None

        # Deferred to the next tick: the switchboard builds this instance
        # mid-load, before child widgets (footer, combos) are wired onto self.ui.
        self.sb.QtCore.QTimer.singleShot(0, self._initialize_ui)

    def _initialize_ui(self) -> None:
        """Wire what reads several widgets at once, once all of them exist.

        Deferred from __init__ (QTimer) so every widget has run its ``*_init``
        and restored its session value first: the Preset combo's modified
        marker, the Adaptive Sampling gate and the Exclude count. Arnold is
        not checked here: a bake loads it on demand
        (:meth:`LightmapBaker.preflight`), and loading a renderer just because
        a panel opened would cost seconds for nothing.
        """
        if self._presets is not None:
            # Semantic presets leave this wiring to the owner
            # (``PresetManager.connect_value_widgets`` is a no-op there): any
            # edit to a setting a preset stores re-evaluates the " *" marker.
            def refresh(*_):
                self._presets.refresh_modified_state()

            for control, _read, _write in self._preset_fields().values():
                # A panel widget announces a change on the switchboard's
                # default signal for its type; the Output Directory's toggle
                # is an option-box option, not a widget, and has ``toggled``.
                signal = getattr(control, "default_signals", lambda: "toggled")()
                if control is not None and signal:
                    getattr(control, signal).connect(refresh)
            # The widgets restored their session values with signals blocked.
            self._presets.refresh_modified_state()

        # Adaptive sampling is a GPU bake's; a forced CPU bake ignores it. The
        # switch rides the Samples field's option box, so the rule greys the
        # TOGGLE rather than a row: the spinbox stays live (a CPU bake spends
        # Samples too, just the other way), and a toggle button sits out the
        # container's enabled cascade, so this rule is the only thing that
        # moves it.
        #
        # A rule is keyed by its targets' objectNames, and an option button has
        # none -- so a SECOND rule from this trigger onto another switch would
        # collide with this one's key and be dropped in silence. Gate both from
        # one call (``targets`` takes a list) rather than wiring two.
        adaptive = self._toggle("adaptive")
        if adaptive is not None:
            self.sb.enable_when(
                self.ui, adaptive.widget, "cmb_device", lambda device: device != "CPU"
            )

        # The Exclude set lives in the scene, so the count on its label must
        # follow the scene: another file opened, a Set undone.
        from mayatk.core_utils.script_job_manager import ScriptJobManager

        jobs = ScriptJobManager.instance()
        for event in ("SceneOpened", "NewSceneOpened", "Undo", "Redo"):
            try:
                jobs.subscribe(event, self._refresh_scene_sets, owner=self)
            except Exception as error:  # noqa: BLE001 -- never block the panel
                self.logger.debug(f"scriptJob {event!r} unavailable ({error})")
        jobs.connect_cleanup(self.ui, owner=self)
        self._refresh_scene_sets()

    def _refresh_scene_sets(self) -> None:
        """Re-read the scene sets onto their labels (Exclude, Isolate, Bake Set)."""
        for row in self._SET_ROWS:
            self._refresh_set_row(row)
        self._refresh_bake_set()

    # ------------------------------------------------------------------
    # Header
    # ------------------------------------------------------------------

    def header_init(self, widget) -> None:
        """Configure the header menu and help text."""
        widget.config_buttons("menu", "collapse", "hide")
        widget.menu.add(
            "QPushButton",
            setText="Revert to Source",
            setObjectName="revert_to_source",
            setToolTip="Remove the lightmap wiring from the selected objects, or "
            "from every baked object when nothing is selected. Asks first; the "
            "baked EXR files stay on disk.",
        )
        widget.menu.add(
            "QPushButton",
            setText="List Changed Objects",
            setObjectName="list_changed_objects",
            setToolTip="Select what a Changed bake would re-bake, and list each "
            "object with why (moved, deformed, a neighbour changed, the "
            "lighting changed ...) in the Script Editor. Nothing is baked.",
        )
        widget.menu.add(
            "QPushButton",
            setText="Open Sourceimages Folder",
            setObjectName="open_sourceimages",
            setToolTip="Open the folder the lightmaps are written to (the "
            "Output Directory field, or the project's sourceimages) in the file manager.",
        )
        widget.set_help_text(
            self.sb.tooltip.fmt(
                title="Lightmap Baker",
                body="Bakes the scene's lighting with Arnold into a lightmap per "
                "object (or an atlas per material) and wires it for the engine in "
                "one step. Materials and texture UVs are never changed: each mesh "
                "samples its lightmap on a second UV set, and the engine "
                "multiplies it with the albedo.",
                steps=[
                    "<b>Scope</b>: the Selected meshes, every Visible one, the "
                    "whole Scene, or what <b>Changed</b> since the last bake -- "
                    "moved, deformed, re-lit, and the objects near enough to "
                    "be shaded by it -- each written back into its own atlas "
                    "cell (the footer says what, the menu lists it). Its "
                    "buttons say what lights the bake: "
                    "<b>Include Scene Lighting</b> (the light) bakes with every "
                    "scene light, off only the Scope's own; the sphere takes the "
                    "HDRI environment in or out; the eye switches hidden lights "
                    "on for the bake. <b>Exclude</b> keeps objects from getting a map "
                    "of their own but leaves them in the render: they still cast "
                    "shadows and bounce light onto everything that bakes. Any "
                    "other mesh that is neither baked nor excluded moves, and "
                    "stays out of the render. <b>Isolate</b> is for moving parts "
                    "that still get a map (a door on its cabinet): each bakes "
                    "in a pass of its own and stays out of every other "
                    "object's render, so it leaves no shadow behind.",
                    "<b>Bake Set</b>: with <i>Save After Bake</i> on, every bake "
                    "stores the objects it acted on as a set in the scene, so "
                    "selecting it re-runs that bake. Its tag button names the "
                    "set with the naming convention's Set suffix (the Naming "
                    "panel's, <i>_SET</i> by default).",
                    "<b>Packing</b>: one atlas per material (the default), or one "
                    "map per object. Its camera button also captures the room as a "
                    "<b>Reflection Probe</b>, for the reflections a lightmap cannot "
                    "hold. <b>Processor</b>: which one Arnold renders on.",
                    "<b>Quality</b>: Resolution, Samples, GI Samples and Bounces. "
                    "Each dial carries its own switch: <b>Denoise</b> cleans a map "
                    "at the size it ships, <b>Adaptive Sampling</b> (GPU) adds "
                    "samples only where one is noisy.",
                    "<b>Output</b>: empty writes to <i>sourceimages</i>; the image "
                    "button saves each map beside its material's texture maps.",
                    "The group at the bottom runs the bake. <b>Preset</b>: pick "
                    "one, or set the dials above and save your own (the disk "
                    "icon). <b>Reset to Defaults</b> puts every setting back; "
                    "Shift+Click makes the current ones your defaults.",
                    "<b>Bake Lightmaps</b>, then export with the Scene Exporter "
                    "(or Export All): a plain Export Selection leaves out the "
                    "hidden <i>data_export</i> node the engine wiring rides on.",
                ],
                notes=[
                    "Re-baking replaces the earlier maps. <b>Revert to Source</b> "
                    "(this menu) removes the wiring; the EXR files stay on disk.",
                    "Needs Arnold (mtoa): every bake renders with it.",
                ],
            )
        )

    # ------------------------------------------------------------------
    # Preset combobox (uitk preset template, semantic mode)
    # ------------------------------------------------------------------

    def cmb000_init(self, widget) -> None:
        """Wire the Preset combo: uitk's preset template over the shared store.

        Semantic mode (``value_provider`` / ``value_applier``): a preset is the
        store's ``{key: value}`` dict, not a widget snapshot, so the file this
        panel saves is the one :meth:`LightmapBaker.from_preset` reads -- and
        the shipped tiers keep working here. The combo restores only its
        SELECTION, from the store's active pointer; every dial restores its own
        session value, so nothing is re-applied at open. That pointer owns the
        selection outright: a restored index would second-guess it against a
        list that grows whenever a preset is saved.
        """
        from uitk.managers.preset_manager import PresetManager

        store = LightmapBaker.preset_store()
        self._presets = PresetManager(
            preset_dir=str(store.user_dir),
            builtin_dir=str(store.builtin_dir) if store.builtin_dir else None,
            value_provider=self._preset_values,
            value_applier=self._apply_preset_values,
        )
        # The manager's user-facing lines (a preset that fails to load) reach
        # this panel's log rather than only the console.
        self._presets.use_logger(self.logger)
        # A pointer left on a retired tier name ("quest", now "mobile") names a
        # preset the store no longer has; follow the rename rather than open
        # on no selection.
        active = self._presets.active_preset
        if active and not self._presets.exists(active):
            current = LightmapBaker._resolve_retired_preset(active)
            if current != active:
                self._presets.active_preset = current
        # Seeded ONCE per machine: a reset (or deleting the active user
        # preset) clears the pointer, and read as "never set" it was reseeded
        # on the next open -- the reset values then showed as that preset,
        # modified ("mobile *"), which is what ``_after_reset`` exists to stop.
        settings = getattr(self.ui, "settings", None)
        seeded = bool(settings and settings.value(self._PRESET_SEEDED_KEY, False))
        if (
            not seeded
            and self._presets.active_preset is None
            and self._presets.exists(self._DEFAULT_PRESET)
        ):
            self._presets.active_preset = self._DEFAULT_PRESET
        if settings is not None and not seeded:
            settings.setValue(self._PRESET_SEEDED_KEY, True)
        widget.restore_state = False
        self._presets.wire_combo(widget, placeholder="Preset…")

    def btn_reset_defaults_init(self, widget) -> None:
        """Wire Reset to Defaults to uitk's shared reset grammar.

        Click restores the defaults, Shift+Click makes the current values the
        defaults and Ctrl+Shift+Click forgets those -- the grammar a per-field
        reset option already teaches, so the panel-wide button adds a scope
        rather than a second idiom, and brings its own tooltip and hover
        preview with it.

        The scope is every widget the window's state manages: the quality
        dials, the Scope and Packing combos, the output fields. The Exclude set
        is scene data rather than a widget value, so a reset leaves it standing.
        """
        from uitk.managers.reset_gesture import ResetGesture

        self._reset_gesture = ResetGesture(widget, on_performed=self._after_reset)

    def _after_reset(self, action: str) -> None:
        """Let go of the active preset when a reset moved the dials off it.

        The values are the defaults now, not the preset the combo still names;
        keeping the pointer would show them as that preset, modified. Saving
        the current values as the defaults (Shift+Click) moves no dial, so it
        keeps the selection.
        """
        from uitk.managers.reset_gesture import ResetGesture

        presets = getattr(self, "_presets", None)
        if presets is None or action == ResetGesture.SAVE:
            return
        presets.active_preset = None
        presets.refresh_combo()

    def _preset_fields(self) -> Dict[str, Tuple[Any, Callable[[], Any], Callable]]:
        """``{preset key: (control, read, write)}`` -- the one map between the
        preset store's keys and this panel's controls. Save reads through it
        (:meth:`_preset_values`), a load writes through it
        (:meth:`_apply_preset_values`) and each control's change re-evaluates
        the modified marker (:meth:`_initialize_ui`), so a key added here is
        saved, loaded and marked, or none of the three. The keys are
        :attr:`LightmapBaker.PRESET_INT_KEYS` / ``PRESET_BOOL_KEYS`` plus the
        panel's own ``packing``.

        The dials are widgets; every switch rides a field's option box
        (:attr:`_TOGGLES`) and is read and written through the same three-part
        shape, so neither half of the preset machinery has to know which kind
        it is holding.
        """
        ui = self.ui
        fields: Dict[str, Tuple[Any, Callable[[], Any], Callable]] = {
            "packing": (ui.cmb002, self._packing, self._set_packing),
            "resolution": (ui.cmb_resolution, self._resolution, self._set_resolution),
            "samples": (
                ui.spn_samples,
                ui.spn_samples.value,
                lambda v: ui.spn_samples.setValue(int(v)),
            ),
            "gi_samples": (
                ui.spn_gi_samples,
                ui.spn_gi_samples.value,
                lambda v: ui.spn_gi_samples.setValue(int(v)),
            ),
            "gi_depth": (
                ui.spn_bounces,
                ui.spn_bounces.value,
                lambda v: ui.spn_bounces.setValue(int(v)),
            ),
        }
        for key in self._TOGGLES:
            fields[key] = (
                self._toggle(key),
                lambda key=key: self._toggle_state(key),
                lambda value, key=key: self._set_toggle_state(key, value),
            )
        return fields

    def _preset_values(self) -> Dict[str, Any]:
        """The panel's bake settings, keyed as the preset store keys them."""
        return {
            key: read()
            for key, (_control, read, _write) in self._preset_fields().items()
        }

    def _apply_preset_values(self, data: Dict[str, Any]) -> int:
        """Write a preset's values onto the widgets; returns how many applied.

        Overlay semantics: a key the preset lacks keeps its widget's value -- a
        shipped tier stores only the quality dials, so loading one leaves the
        switches as the user set them. Unknown keys (``description``) are
        skipped. Signals are left on, so the session state records the loaded
        values: that is what the next session restores the dials from.
        """
        fields = self._preset_fields()
        applied = 0
        for key, value in data.items():
            field = fields.get(key)
            if field is None:
                continue
            _control, _read, write = field
            try:
                write(value)
            except (TypeError, ValueError) as error:
                self.logger.warning(f"Preset value {key}={value!r} skipped: {error}")
                continue
            applied += 1
        return applied

    # ------------------------------------------------------------------
    # Switches (option-box toggles)
    # ------------------------------------------------------------------

    def _wire_toggle(self, widget, key: str, icon: str, on: str, off: str, **kwargs):
        """Hang the *key* switch off *widget*'s option box as a toggle button.

        One shape for every entry in :attr:`_TOGGLES`. Off takes the neutral
        "locked" tint rather than a toggle's default error red: none of these
        switches stops the panel working, and red would read as a fault that
        isn't there. The settings key is explicit and panel-scoped -- the
        auto-derived one is the field's own objectName, and ``spn_samples`` /
        ``cmb_scope`` are names another panel in the same host would share.

        Parameters:
            widget: The field the switch rides.
            key: Its :attr:`_TOGGLES` entry; names the default and the key.
            icon: uitk icon name for the button.
            on: Tooltip while on -- what it is doing, then what a click does.
            off: Tooltip while off, the same way round.
            kwargs: Forwarded to ``set_toggle`` (e.g. ``on_toggled``,
                ``icon_off``). A field's first switch replaces what it carried;
                the next are added beside it, in :attr:`_TOGGLES` order.
        """
        kwargs.setdefault("replace", self._field_switches(key)[0] == key)
        widget.option_box.set_toggle(
            icon=icon,
            tooltip_on=on,
            tooltip_off=off,
            initial=self._TOGGLES[key][1],
            disabled_color=ptk.Palette.status()["locked"][0],
            settings_key=f"lightmap_baker_{key}",
            **kwargs,
        )

    def _field_switches(self, key: str) -> List[str]:
        """The :attr:`_TOGGLES` keys riding *key*'s field, in their order."""
        field = self._TOGGLES[key][0]
        return [k for k, (f, _default) in self._TOGGLES.items() if f == field]

    def _toggle(self, key: str):
        """The :attr:`_TOGGLES` switch *key*, or ``None`` before its field's
        ``_init`` has built it (the preset machinery reads this map while the
        panel is still loading). A field carrying several switches (Scope's
        three) holds them in :attr:`_TOGGLES` order, so each is read by its
        place among the field's switches -- options with an on/off state."""
        from uitk.widgets.optionBox.options.toggle import ToggleOption

        widget = getattr(self.ui, self._TOGGLES[key][0], None)
        shared = self._field_switches(key)
        try:
            if len(shared) == 1:
                return widget.option_box.find_option(ToggleOption)
            switches = [
                option
                for option in widget.option_box.get_options()
                if hasattr(option, "is_on")
            ]
        except Exception:  # noqa: BLE001 -- no option box on a bare widget
            return None
        index = shared.index(key)
        return switches[index] if index < len(switches) else None

    def _toggle_state(self, key: str) -> bool:
        """Whether the *key* switch is on (its shipped default until wired)."""
        toggle = self._toggle(key)
        return self._TOGGLES[key][1] if toggle is None else bool(toggle.is_on)

    def _set_toggle_state(self, key: str, value: bool) -> None:
        """Set the *key* switch (a preset load)."""
        toggle = self._toggle(key)
        if toggle is not None:
            toggle.set_on(bool(value))

    # ------------------------------------------------------------------
    # Scope, Exclude, Packing
    # ------------------------------------------------------------------

    def cmb002_init(self, widget) -> None:
        """Populate the Packing combobox (Atlas by Material is the default) and
        hang the Reflection Probe switch off it.

        One shared map per material is what an engine wants: fewer textures,
        no per-object naming collisions, and the texels spent where the
        surface area is. Per-Object is the opt-out, for a hero asset that
        earns a full map of its own. Packing is what the bake ships, and the
        probe is the one file it ships beside the maps
        (:meth:`LightmapBaker.bake_probe`).
        """
        widget.clear()
        widget.addItems(self._PACKING_LABELS)
        widget.setCurrentIndex(1)  # Atlas by Material — one shared map each
        self._wire_toggle(
            widget,
            "reflection_probe",
            icon="camera",
            on="Reflection Probe: the bake also renders the room it lit as an "
            "HDR from one point and ships it beside the maps. A lightmap holds "
            "no reflections, so a baked metal shows only what it reflects, and "
            "an object the bake leaves out is lit by nothing of the room: the "
            "WebXR preview and the GLB light both from the probe. Click to "
            "ship the maps alone.",
            off="No reflection probe: baked metals reflect the viewer's stock "
            "studio rather than this room, and objects the bake leaves out are "
            "lit by it. Click to capture the room with the bake.",
        )

    def _packing(self) -> str:
        """``"atlas"`` or ``"per_object"`` from the Packing combobox (default per_object)."""
        text = (self.ui.cmb002.currentText() or "").lower()
        return "atlas" if "atlas" in text else "per_object"

    def _set_packing(self, value: str) -> None:
        """Select the Packing row for ``"atlas"`` / ``"per_object"``."""
        self.ui.cmb002.setCurrentIndex(1 if value == "atlas" else 0)

    def _reflection_probe(self) -> bool:
        """Whether the bake captures the room as a reflection probe (the
        Packing switch)."""
        return self._toggle_state("reflection_probe")

    def cmb_scope_init(self, widget) -> None:
        """Populate the Scope combobox (Selected is the default) and hang the
        three lighting switches off it. Changed, last, is what changed since
        the last bake (:meth:`cmb_scope` says what in the footer).

        Scope is what the bake gathers, and what lights it is part of that
        (:meth:`LightmapBaker._bake_lights`): **Include Scene Lighting** --
        every scene light, or only the Scope's own -- **Include Environment**
        -- the HDRI skydome, by its own switch whatever the first says -- and
        **Include Hidden Lights** -- the hidden ones of that set switched on
        for the run. Each answers alone, so none greys another out.
        """
        widget.clear()
        widget.addItems(self._SCOPE_LABELS)
        widget.setCurrentIndex(0)  # Selected — the prior selection-only behavior
        self._wire_toggle(
            widget,
            "include_scene_lights",
            icon="light",
            on="Include Scene Lighting: every light in the scene lights the "
            "bake, wherever it sits. Click to bake with only the lights in the "
            "Scope (selected, or under what is selected).",
            off="Scope lighting only: the lights in the Scope -- selected, or "
            "under what is selected -- light the bake; every other scene light "
            "is switched off for it and back on after. The HDRI has its own "
            "button. Click to bake with every scene light.",
        )
        self._wire_toggle(
            widget,
            "include_environment",
            icon="theme",
            on="Include environment: the scene's HDRI skydome lights the bake "
            "along with its lights, in the Scope or not. Click to bake the "
            "room's own lights only.",
            off="Environment excluded: the aiSkyDomeLight is hidden for the "
            "bake and restored afterwards. An HDRI is often a backdrop or a "
            "look-dev convenience rather than the room's real lighting, and "
            "baking it in is a flat ambient lift that cannot be taken back "
            "out of the map. Click to bake it in.",
        )
        self._wire_toggle(
            widget,
            "include_hidden_lights",
            icon="eye",
            icon_off="eye_off",
            on="Include Hidden Lights: the hidden lights the bake uses are "
            "switched on for it -- under a hidden group too, without showing "
            "anything else the group hides -- and hidden again after. Click to "
            "leave hidden lights off.",
            off="Hidden lights stay off: a light hidden by its own flag or a "
            "parent group's does not light the bake. Click to switch them on "
            "for the bake.",
        )

    def _scope(self) -> str:
        """``"selected"`` (default), ``"visible"``, ``"scene"`` or ``"changed"``
        from cmb_scope."""
        return (self.ui.cmb_scope.currentText() or "Selected").split()[0].lower()

    def cmb_scope(self, index) -> None:
        """Choosing Changed says what a bake would redo, in the footer.

        The dry run (:meth:`LightmapBaker.stale_report`, built with the
        panel's dials -- :meth:`_make_baker` -- so it reads the lights the
        bake would): nothing is baked. The other scopes say nothing here.
        """
        if self._scope() != "changed":
            return
        try:
            report = self._make_baker().stale_report()
        except Exception as error:  # noqa: BLE001 -- a combo change never raises
            self.logger.warning(f"Could not read what changed: {error}")
            self.ui.footer.setText("Could not read what changed (see Script Editor).")
            return
        self.ui.footer.setText(report.summary())

    def _scope_objects(self) -> List[str]:
        """The mesh transforms the current Scope names (before the Exclude set).

        ``visible`` and ``scene`` gather across the scene so a bake needn't be
        preceded by a manual select-all; ``selected`` takes the selection, a
        group standing for the meshes under it (a bake set's members are
        often groups).

        Every scope resolves through ``TextureBaker.resolve_meshes`` -- the one
        definition of "bakeable" -- so a selection that also holds the room's
        LIGHTS (which the Blender-bridge bake genuinely needs selected, and this
        Arnold path does not) bakes the geometry instead of asking Arnold to
        render a quad_light. It also keeps the empty-scope message below honest:
        a lights-only selection reads as nothing to bake, not as a bake that
        silently produced no maps.

        The words and their precedence are :class:`pythontk.HandoffScope`'s
        (``scene`` is its synonym for ``all``; anything unknown bakes the
        selection); this supplies only the scene reads. ``changed`` is the
        panel's own word, answered first: what
        :meth:`LightmapBaker.stale_report` names -- which Bake hands
        :meth:`LightmapBaker.rebake_changed` rather than a list.
        """
        if self._scope() == "changed":
            return list(self._make_baker().stale_report().targets)

        def scene_meshes():
            # Every DAG PATH of every mesh: an instanced shape is ONE node
            # under several transforms, and a listing of nodes names it once
            # -- the production room's Scene scope reached 21 of its 61
            # visible meshes, its 40 instanced walls and props missed (a
            # group instance hides from a transform listing the same way).
            # ``or []``: an empty scene is an answer, not "can't enumerate".
            return (
                cmds.ls(
                    type="mesh", dag=True, allPaths=True, noIntermediate=True, long=True
                )
                or []
            )

        def visible_meshes():
            from mayatk.display_utils._display_utils import DisplayUtils

            return [path for path in scene_meshes() if DisplayUtils.is_visible(path)]

        pool = ptk.HandoffScope.resolve(
            self._scope(),
            selected=lambda: cmds.ls(selection=True, long=True),
            all=scene_meshes,
            visible=visible_meshes,
        )
        return TextureBaker.resolve_meshes(pool or [], descendants=True)

    def _scope_lights(self) -> List[str]:
        """The light shapes the current Scope holds -- what lights a bake with
        Include Scene Lighting off.

        The bake's targets are meshes (:meth:`_scope_objects`), so its lights
        are handed to it beside them: the lights selected or under the
        selection (:meth:`LightmapBaker._scope_lights`), every visible one, or
        every one in the scene -- the same words, read for lights.
        """
        from mayatk.display_utils._display_utils import DisplayUtils
        from mayatk.light_utils._light_utils import LightUtils

        return list(
            ptk.HandoffScope.resolve(
                self._scope(),
                selected=lambda: LightmapBaker._scope_lights(None),
                all=LightUtils.all_lights,
                visible=lambda: [
                    light
                    for light in LightUtils.all_lights()
                    if DisplayUtils.is_visible(light, consider_templated_visible=True)
                ],
            )
            or []
        )

    #: The panel's scene-set rows -- Exclude and Isolate -- by button: a label,
    #: one Set From Selection button, Select / Clear on its option box, and a
    #: live hover. One implementation (:meth:`_init_set_row` and the
    #: ``_*_set_row`` methods); a row is its set and its words.
    _SET_ROWS: Dict[str, Dict[str, Any]] = {
        "set_exclusions": dict(
            scene_set=LightmapExcludeSet,
            label="lbl_exclude",
            title="Exclude",
            verb="excluded",
            effect="they still light the rest.",
            clear_tip="Clear the Exclude set: every object in Scope bakes again. "
            "The objects themselves are untouched.",
            cleared="Exclude set cleared — every object in Scope bakes.",
        ),
        "set_isolation": dict(
            scene_set=LightmapIsolateSet,
            label="lbl_isolate",
            title="Isolate",
            verb="isolated",
            effect="each bakes alone, out of every other object's render.",
            clear_tip="Clear the Isolate set: every object bakes in one pass, "
            "each in the others' render. The objects themselves are untouched.",
            cleared="Isolate set cleared — every object bakes in one pass.",
        ),
    }

    def _init_set_row(self, widget, row: str) -> None:
        """Hang Select / Clear off a scene-set row's button, and make its hover live.

        The row mirrors the Marmoset bridge's Bake Source row: Set From
        Selection is the button, Select and Clear its option-box icons. The set
        lives in the scene, so the hover lists its CURRENT members instead of
        the ones the panel opened on; it wraps each widget's own help text.
        """
        spec = self._SET_ROWS[row]
        widget.option_box.add_action(
            callback=lambda: self._select_set_row(row),
            icon="select",
            tooltip=f"Select the {spec['verb']} objects (hidden ones included).",
            settings_key=False,
        )
        widget.option_box.add_action(
            callback=lambda: self._clear_set_row(row),
            icon="clear",
            tooltip=spec["clear_tip"],
            settings_key=False,
        )
        for target in (widget, getattr(self.ui, spec["label"])):
            help_text = target.toolTip()
            self.sb.tooltip.bind(
                target, lambda text=help_text: self._set_row_tooltip(row, text)
            )

    def _set_row_tooltip(self, row: str, help_text: str) -> str:
        """*help_text* over the meshes a row's set holds, live.

        The meshes, not the set's members: a group stored in the set reads as
        what the bake will actually act on -- the same count the label shows.
        """
        spec = self._SET_ROWS[row]
        try:
            meshes = spec["scene_set"].meshes() if cmds is not None else []
        except Exception:  # noqa: BLE001 -- a tooltip must never raise into Qt
            return help_text
        return self.sb.tooltip.stored_items(
            meshes,
            body=help_text.replace("\n", "<br>"),
            formatter=lambda n: n.rsplit("|", 1)[-1],
            noun=f"mesh(es) {spec['verb']} in this scene",
            empty_text=f"Nothing is {spec['verb']} in this scene.",
        )

    def _refresh_set_row(self, row: str) -> None:
        """Show a row's set's mesh count on its label (``Exclude (3):``).

        The count is of MESHES -- what the bake acts on -- so a group reads as
        everything under it. Runs on the panel's own edits and on scene
        open / new / undo / redo (see :meth:`_initialize_ui`).
        """
        spec = self._SET_ROWS[row]
        label = getattr(self.ui, spec["label"], None)
        if label is None or cmds is None:
            return
        try:
            count = len(spec["scene_set"].meshes())
        except Exception:  # noqa: BLE001 -- a label refresh must never raise
            count = 0
        title = spec["title"]
        label.setText(f"{title} ({count}):" if count else f"{title}:")

    def _define_set_row(self, row: str) -> None:
        """Make the selection a row's set; an empty selection clears it."""
        spec = self._SET_ROWS[row]
        members = spec["scene_set"].define()
        self._refresh_set_row(row)
        if not members:
            self.ui.footer.setText(
                f"Nothing selected — the {spec['title']} set is cleared."
            )
            return
        count = len(spec["scene_set"].meshes())
        self.ui.footer.setText(
            f"{count} mesh{'es' if count != 1 else ''} {spec['verb']}; {spec['effect']}"
            if count
            else f"{spec['title']} set stored, but it holds no meshes -- nothing "
            f"is {spec['verb']}."
        )

    def _select_set_row(self, row: str) -> None:
        """Select a row's set's members, hidden ones included."""
        spec = self._SET_ROWS[row]
        members = spec["scene_set"].members()
        self._refresh_set_row(row)
        if not members:
            self.ui.footer.setText(f"Nothing is {spec['verb']} in this scene.")
            return
        cmds.select(members, replace=True)
        self.ui.footer.setText(
            f"Selected {len(members)} {spec['verb']} object"
            f"{'s' if len(members) != 1 else ''}."
        )

    def _clear_set_row(self, row: str) -> None:
        """Delete a row's set; its objects are left untouched."""
        spec = self._SET_ROWS[row]
        if not spec["scene_set"].exists():
            self.ui.footer.setText(f"Nothing is {spec['verb']} in this scene.")
            return
        spec["scene_set"].clear()
        self._refresh_set_row(row)
        self.ui.footer.setText(spec["cleared"])

    # Exclude: no map of their own, still in the render (LightmapExcludeSet).
    def set_exclusions_init(self, widget) -> None:
        """Hang Select / Clear off the Exclude row (:meth:`_init_set_row`)."""
        self._init_set_row(widget, "set_exclusions")

    def set_exclusions(self) -> None:
        """Make the selection the Exclude set; an empty selection clears it."""
        self._define_set_row("set_exclusions")

    def select_exclusions(self) -> None:
        """Select the Exclude set's members, hidden ones included."""
        self._select_set_row("set_exclusions")

    def clear_exclusions(self) -> None:
        """Delete the Exclude set; its objects are left untouched."""
        self._clear_set_row("set_exclusions")

    def _exclusions_tooltip(self, help_text: str) -> str:
        """*help_text* over the meshes the Exclude set keeps from baking, live."""
        return self._set_row_tooltip("set_exclusions", help_text)

    # Isolate: each bakes alone, out of every other's render (LightmapIsolateSet).
    def set_isolation_init(self, widget) -> None:
        """Hang Select / Clear off the Isolate row (:meth:`_init_set_row`)."""
        self._init_set_row(widget, "set_isolation")

    def set_isolation(self) -> None:
        """Make the selection the Isolate set; an empty selection clears it."""
        self._define_set_row("set_isolation")

    def select_isolation(self) -> None:
        """Select the Isolate set's members, hidden ones included."""
        self._select_set_row("set_isolation")

    def clear_isolation(self) -> None:
        """Delete the Isolate set; its objects are left untouched."""
        self._clear_set_row("set_isolation")

    # ------------------------------------------------------------------
    # Bake Set
    # ------------------------------------------------------------------

    #: Settings keys of the Bake Set row's two switches, panel-scoped: Save
    #: After Bake (the global switch) and the naming-convention spelling.
    _BAKE_SET_SAVE_KEY = "lightmap_baker_save_bake_set"
    _BAKE_SET_CONVENTION_KEY = "lightmap_baker_bake_set_convention"

    def select_bake_set_init(self, widget) -> None:
        """Hang Save After Bake, the naming switch and Clear off the Bake Set row.

        The row mirrors the Exclude row, and its switches ride the button's
        option box as every switch on this panel does. **Save After Bake**
        (the disk) is the global one: while it is on, every bake -- in any
        scene; the state is per user -- saves the objects it acted on as the
        scene's :class:`LightmapBakeSet`. The **tag** names that set plain
        (``lightmapBaker_baked``) or by the naming convention
        (``ptk.NamingConvention``'s Set affix, ``_SET`` by default -- the one
        the Naming panel edits), renaming a set already in the scene on the
        spot. The button selects the set; Clear deletes it. Added in that
        order, which is how :meth:`_bake_set_switch` tells the two apart.
        """
        locked = ptk.Palette.status()["locked"][0]
        widget.option_box.set_toggle(
            icon="save",
            tooltip_on="Save After Bake: every bake replaces this scene's Bake Set "
            "with the objects it acted on, finished or not -- select it to "
            "re-run the bake. One setting for every scene. Click to stop.",
            tooltip_off="Bakes save no set. Click to have every bake store the "
            "objects it acted on as this scene's Bake Set.",
            initial=False,
            disabled_color=locked,
            settings_key=self._BAKE_SET_SAVE_KEY,
        )
        convention = ptk.NamingConvention.affix(LightmapBakeSet.CONVENTION_KEY)
        widget.option_box.add_toggle(
            icon="tag",
            tooltip_on="Named by the naming convention: "
            f"{LightmapBakeSet.conventional_name()} (the Naming panel's Set "
            f"suffix, {convention or 'empty'}). Click to name it plain: "
            f"{LightmapBakeSet.SET_NAME}.",
            tooltip_off=f"Named plain: {LightmapBakeSet.SET_NAME}. Click to name "
            f"it by the naming convention's Set suffix ({convention or 'empty'}).",
            initial=False,
            disabled_color=locked,
            settings_key=self._BAKE_SET_CONVENTION_KEY,
            on_toggled=self._respell_bake_set,
        )
        widget.option_box.add_action(
            callback=self.clear_bake_set,
            icon="clear",
            tooltip="Delete the scene's Bake Set. The objects themselves are untouched.",
            settings_key=False,
        )
        for target in (widget, self.ui.lbl_bake_set):
            help_text = target.toolTip()
            self.sb.tooltip.bind(
                target, lambda text=help_text: self._bake_set_tooltip(text)
            )

    def _bake_set_switch(self, index: int) -> bool:
        """The Bake Set row's switch *index* (0 Save After Bake, 1 naming), on or off.

        A switch is an option with an on/off state (``is_on``) -- the row's
        two toggles, in the order :meth:`select_bake_set_init` added them;
        its Clear action has none. ``False`` before the row is wired.
        """
        widget = getattr(self.ui, "select_bake_set", None)
        try:
            switches = [
                option
                for option in widget.option_box.get_options()
                if hasattr(option, "is_on")
            ]
        except Exception:  # noqa: BLE001 -- no option box on a bare widget
            return False
        return len(switches) > index and bool(switches[index].is_on)

    def _save_bake_set(self) -> bool:
        """Whether a bake saves its objects as the scene's Bake Set (Save After Bake)."""
        return self._bake_set_switch(0)

    def _bake_set_conventional(self) -> bool:
        """Whether the Bake Set is named by the naming convention (the row's tag switch)."""
        return self._bake_set_switch(1)

    def _respell_bake_set(self, conventional: bool) -> None:
        """Rename the scene's Bake Set to follow the tag switch, when there is one."""
        if cmds is None:
            return
        before = LightmapBakeSet.name()
        name = LightmapBakeSet.respell(bool(conventional))
        self._refresh_bake_set()
        if name and name != before:
            self.ui.footer.setText(f"Bake Set renamed: {before} -> {name}.")

    def _bake_set_tooltip(self, help_text: str) -> str:
        """*help_text* over the objects the scene's Bake Set holds, live."""
        try:
            members = LightmapBakeSet.members() if cmds is not None else []
            name = LightmapBakeSet.name() if cmds is not None else None
        except Exception:  # noqa: BLE001 -- a tooltip must never raise into Qt
            return help_text
        return self.sb.tooltip.stored_items(
            members,
            body=help_text.replace("\n", "<br>"),
            formatter=lambda n: n.rsplit("|", 1)[-1],
            noun=f"object(s) in {name}" if name else "object(s) in the Bake Set",
            empty_text="No Bake Set in this scene.",
        )

    def _refresh_bake_set(self) -> None:
        """Show the Bake Set's member count on its label (``Bake Set (151):``)."""
        label = getattr(self.ui, "lbl_bake_set", None)
        if label is None or cmds is None:
            return
        try:
            count = len(LightmapBakeSet.members())
        except Exception:  # noqa: BLE001 -- a label refresh must never raise
            count = 0
        label.setText(f"Bake Set ({count}):" if count else "Bake Set:")

    def select_bake_set(self) -> None:
        """Select the Bake Set's members, hidden ones included."""
        members = LightmapBakeSet.members()
        self._refresh_bake_set()
        if not members:
            self.ui.footer.setText("No Bake Set in this scene.")
            return
        cmds.select(members, replace=True)
        self.ui.footer.setText(
            f"Selected {len(members)} object{'s' if len(members) != 1 else ''} "
            f"of {LightmapBakeSet.name()}."
        )

    def clear_bake_set(self) -> None:
        """Delete the scene's Bake Set; its objects are left untouched."""
        if not LightmapBakeSet.exists():
            self.ui.footer.setText("No Bake Set in this scene.")
            return
        name = LightmapBakeSet.name()
        LightmapBakeSet.clear()
        self._refresh_bake_set()
        self.ui.footer.setText(f"{name} deleted; its objects are untouched.")

    def _record_bake_set(self, result) -> str:
        """Save *result*'s objects as the Bake Set when the panel asks to; the footer note.

        Every object the bake acted on -- what it baked and what it left
        unbaked (a cancel, a failed render) -- so the set re-runs the bake.
        Nothing is saved for a bake that refused or produced nothing.
        """
        if not (self._save_bake_set() and result and cmds is not None):
            return ""
        objects = sorted(set(result.maps) | set(result.unbaked))
        try:
            members = LightmapBakeSet.define(
                objects, conventional=self._bake_set_conventional()
            )
        except Exception as error:  # noqa: BLE001 -- the bake is committed already
            self.logger.warning(f"Bake Set not saved: {error}")
            return " Bake Set not saved (see Script Editor)."
        self._refresh_bake_set()
        return (
            f" Saved {len(members)} object{'s' if len(members) != 1 else ''} "
            f"as {LightmapBakeSet.name()}."
        )

    # ------------------------------------------------------------------
    # Quality
    # ------------------------------------------------------------------

    def cmb_resolution_init(self, widget) -> None:
        """Populate the Resolution combobox (value carried as item data,
        default 1024) and hang the Denoise switch off it.

        Denoise runs at the size the map SHIPS at -- the object's own map, or
        the atlas cell it is shrunk into -- which is the size this combo sets,
        so the switch belongs to it.
        """
        widget.clear()
        for r in self._RESOLUTIONS:
            widget.addItem(f"Resolution:\t{r}", r)
        widget.setCurrentIndex(self._RESOLUTIONS.index(1024))
        self._wire_toggle(
            widget,
            "denoise",
            icon="filter",
            on="Denoise: every map is cleaned at the size it ships at — the "
            "object's own map, or the atlas cell it is shrunk into. "
            "Edge-preserving, so falloff and shadow edges survive and lone "
            "fireflies are clamped. Click to ship the bake as rendered.",
            off="Not denoising. Arnold's bake has no denoiser of its own, so "
            "each map ships its sampling noise as grain. Click to denoise.",
        )

    def _resolution(self) -> int:
        """The selected lightmap resolution (px) from cmb_resolution (its item data)."""
        value = self.ui.cmb_resolution.currentData()
        return int(value) if value is not None else 1024

    def _set_resolution(self, value: int) -> None:
        """Select *value* in the Resolution combobox, snapping to the nearest fixed size."""
        nearest = min(self._RESOLUTIONS, key=lambda r: abs(r - int(value)))
        self.ui.cmb_resolution.setCurrentIndex(self._RESOLUTIONS.index(nearest))

    def spn_samples_init(self, widget) -> None:
        """Hang the Adaptive Sampling switch off the Samples field.

        Adaptive sampling does not change how MANY samples the bake may spend
        -- it decides where the Samples this field sets are spent -- so it
        qualifies this dial. A CPU bake spends them the other way and ignores
        it, which is what greys the button out (:meth:`_initialize_ui`).
        """
        self._wire_toggle(
            widget,
            "adaptive",
            icon="activity",
            on="Adaptive Sampling (GPU bakes): every texel gets Samples, and "
            "the noisy ones (shadows, contact) get more — up to Samples × GI "
            "Samples. Measured on four production floors: 73s where giving "
            "every texel the full budget took 381s, for shadow noise of 1.31% "
            "against 1.06%. Click to give every texel the full budget.",
            off="Every texel gets the full Samples × GI Samples: the cleanest "
            "map, and the slowest. Click to spend the budget adaptively.",
        )

    #: Bake-processor rows, label -> the value the baker takes. Auto is first
    #: (the default) because it is the fast choice on both renderers; the
    #: explicit rows exist for A/B-ing a suspect map against the other one.
    _DEVICES = (("Auto", "AUTO"), ("GPU", "GPU"), ("CPU", "CPU"))

    def cmb_device_init(self, widget) -> None:
        """Populate the Processor combobox (value carried as item data); default Auto."""
        widget.clear()
        for label, value in self._DEVICES:
            widget.addItem(f"Processor:\t{label}", value)
        widget.setCurrentIndex(0)  # Auto

    def _device(self) -> str:
        """The processor the bake renders on, from cmb_device (its item data)."""
        return self.ui.cmb_device.currentData() or self._DEVICES[0][1]

    def _adaptive(self) -> bool:
        """Whether a GPU bake samples adaptively (the Samples field's switch)."""
        return self._toggle_state("adaptive")

    def _include_environment(self) -> bool:
        """Whether the bake keeps the scene's environment (a Scope switch)."""
        return self._toggle_state("include_environment")

    def _include_scene_lights(self) -> bool:
        """Whether every scene light lights the bake, or only the Scope's (a Scope switch)."""
        return self._toggle_state("include_scene_lights")

    def _include_hidden_lights(self) -> bool:
        """Whether the bake switches the hidden lights on (a Scope switch)."""
        return self._toggle_state("include_hidden_lights")

    def _denoise(self) -> bool:
        """Whether the maps are denoised where they ship (the Resolution switch)."""
        return self._toggle_state("denoise")

    # ------------------------------------------------------------------
    # Output
    # ------------------------------------------------------------------

    def txt_output_dir_init(self, widget) -> None:
        """Add the folder browser and the Beside Material Textures toggle.

        No clear button: the value arrives from the browse dialog as often as
        it is typed, and a mis-click would drop a path the user picked and
        can't retype -- the field's *empty* default is one keystroke away
        anyway (see :meth:`_output_dir`).

        The toggle is a switch on the field it redirects (:attr:`_TOGGLES`):
        on, each map goes to its material's texture folder
        (:attr:`LightmapBaker.beside_textures`) and this field only takes the
        objects whose material has none.
        """
        widget.option_box.browse(
            mode="directory",
            title="Lightmap output directory",
            tooltip="Browse for the lightmap output directory…",
            start_dir=self._output_dir,
            callback=self._relativize_output_dir,
        )
        self._wire_toggle(
            widget,
            "beside_textures",
            icon="image",
            on="Beside material textures: each lightmap is saved in "
            "the folder its material's texture maps are in, named after that "
            "texture set. This field only takes the objects whose material has "
            "no texture folder inside the project (another project's is never "
            "written to). Click to save every map here instead.",
            off="Saving every lightmap to this folder. Click to save "
            "each one beside its material's texture maps instead.",
            on_toggled=self._show_output_mode,
        )
        self._show_output_mode(self._beside_textures())

    def _beside_textures(self) -> bool:
        """Whether each map is saved beside its material's texture maps."""
        return self._toggle_state("beside_textures")

    def _show_output_mode(self, beside: bool) -> None:
        """Say in the empty field where the maps will go."""
        self.ui.txt_output_dir.setPlaceholderText(
            "(blank: beside textures, else sourceimages/lightmaps)"
            if beside
            else "(blank: sourceimages/lightmaps)"
        )

    def _relativize_output_dir(self, path: str) -> None:
        """Store a browsed dir as the portable spelling of itself.

        The dialog can only hand back an absolute path; under sourceimages the
        relative one is what survives the project being moved (or a teammate's
        copy) -- see ``ptk.FileUtils.relativize_output_dir``, the exact inverse
        of the ``resolve_output_dir`` :meth:`_output_dir` reads the field with.
        """
        if not path:
            return
        self.ui.txt_output_dir.setText(
            ptk.FileUtils.relativize_output_dir(path, self._sourceimages_dir())
        )

    def _output_dir(self) -> Optional[str]:
        """The bake's output directory: the field, resolved against sourceimages.

        Empty field -> the scene project's ``sourceimages/lightmaps``
        (:attr:`LightmapBaker.OUTPUT_SUBDIR`): exactly where a scripted bake
        lands on its own, and blendertk's -- one default. A subdirectory entry
        is joined onto sourceimages (the conventional, portable home for
        material-referenced textures) so the setting survives a project move;
        a full path is taken as-is, with or without a project. The directory
        itself is created by the bake.

        ``None`` when a blank or subdirectory entry has no project to resolve
        against, rather than handing the workflow a *relative* directory
        (``os.makedirs`` would create that against the process CWD): the
        workflow's own default decides, and refuses. Mirror of blendertk's.
        """
        entry = self.ui.txt_output_dir.text()
        if (entry or "").strip():
            rooted = ptk.FileUtils.resolve_output_dir(entry, None)
            if rooted:
                return rooted
        base = self._sourceimages_dir()
        if not base:
            return None
        if not (entry or "").strip():
            return os.path.normpath(os.path.join(base, LightmapBaker.OUTPUT_SUBDIR))
        return ptk.FileUtils.resolve_output_dir(entry, base)

    def txt000_init(self, widget) -> None:
        """Add the Prefix / Suffix / Auto picker to the name-affix field."""
        widget.option_box.clear_option = True
        # Explicit key: ``txt000`` is generic enough that another panel in the
        # same host would share the auto-derived namespace.
        widget.option_box.set_affix(
            default="auto",
            settings_key="lightmap_baker_affix",
            # Fourth, custom state: take the lightmap affix from the shared
            # naming convention instead of this one field.
            convention_key="lightmap",
        )

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def _make_baker(self) -> LightmapBaker:
        """A :class:`LightmapBaker` built from the panel's dials and switches --
        the one every action builds, so the Changed dry run reads the lights,
        resolution and switches the bake will."""
        return LightmapBaker(
            resolution=self._resolution(),
            samples=self.ui.spn_samples.value(),
            gi_depth=self.ui.spn_bounces.value(),
            gi_samples=self.ui.spn_gi_samples.value(),
            device=self._device(),
            adaptive=self._adaptive(),
            include_environment=self._include_environment(),
            include_scene_lights=self._include_scene_lights(),
            include_hidden_lights=self._include_hidden_lights(),
            denoise=self._denoise(),
            beside_textures=self._beside_textures(),
            reflection_probe=self._reflection_probe(),
        )

    def b000(self) -> None:
        """Bake lightmaps for the Scope, minus the Exclude set (:meth:`LightmapBaker.bake`).

        Under Changed, re-bake what changed since the last bake and what it
        shades (:meth:`LightmapBaker.rebake_changed`), each object the way it
        was baked -- written back into its own atlas cell where it fits.
        """
        changed = self._scope() == "changed"
        scoped = [] if changed else self._scope_objects()
        if not changed and not scoped:
            self.ui.footer.setText(
                "Select one or more mesh objects to bake."
                if self._scope() == "selected"
                else f"No meshes found for scope '{self._scope()}'."
            )
            return

        self._baker = self._make_baker()

        # Where the maps land: the Output Directory field resolved against the
        # scene project's sourceimages (empty: its lightmaps folder; see
        # _output_dir -- resolved HERE, so the workflow is never handed a
        # relative directory). With Beside Material Textures it takes only the
        # objects whose material has no texture folder.
        src = self._output_dir()
        if src is None:
            self.ui.footer.setText(
                "No project to bake into — save the scene or set a Maya project."
            )
            return
        # Name the output <object><affix> per the field (e.g. "<object>_Lightmap"),
        # following the texture-set convention; the shader inherits the name.
        # An empty field falls back to the placeholder default (the .ui's
        # single source for it), so a cleared field never bakes affix-less
        # files that could collide with source texture names.
        field = self.ui.txt000
        affix = field.text().strip() or field.placeholderText()
        prefix, suffix = field.option_box.resolve_affix(affix, default="suffix")

        # Indeterminate marquee + per-object text in OUR footer. Deliberately not a
        # determinate 0..100% bar: a single Arnold bake is one opaque blocking call
        # with no sub-progress, so a percentage would sit at 0 and jump -- which is
        # exactly what mtoa's own popup does. The text still reports object i / N,
        # which is the part that tells the artist the run is alive and how far in.
        def progress(done, total, name):
            return update(
                None,
                f"Baking {name}…  ({min(done + 1, total)}/{total})"
                if done < total
                else f"Baked {total} object{'s' if total != 1 else ''}.",
            )

        with self.ui.footer.progress(text="Baking lightmaps…") as update:
            if changed:
                # Each the way it was baked (its recorded packing), so the
                # Packing row does not apply; the dials do.
                result = self._baker.rebake_changed(
                    output_dir=src, prefix=prefix, suffix=suffix, on_progress=progress
                )
            else:
                result = self._baker.bake(
                    # The Scope's lights ride beside its meshes: what lights
                    # the bake with Include Scene Lighting off (the bake's
                    # targets stay the meshes).
                    scoped
                    + [light for light in self._scope_lights() if light not in scoped],
                    packing=self._packing(),
                    output_dir=src,
                    prefix=prefix,
                    suffix=suffix,
                    on_progress=progress,
                )
        self.ui.footer.setText(
            self._bake_report(result) + self._record_bake_set(result)
        )

    def _bake_report(self, result) -> str:
        """The footer line for a :class:`LightmapBakeResult`.

        A refusal says why; an empty bake says where to look; a bake says how
        many objects it baked and where to, then what it left alone and
        whether its level looks wrong.
        """
        if result.refused:
            self._last_output_dir = None
            return result.refused
        if not result:
            self._last_output_dir = None
            return "Bake produced no output (see Script Editor)."
        self._last_output_dir = os.path.dirname(next(iter(result.maps.values())))
        folders = result.folders
        where = (
            self._last_output_dir
            if len(folders) == 1
            else f"{len(folders)} folders (beside their textures)"
        )
        if result.in_place or result.repacked or self._packing() == "atlas":
            n = len(result.files)
            tail = (
                f"Consolidated into {n} atlas map{'s' if n != 1 else ''}; each "
                "object samples its own atlas rect at engine time. Export the FBX."
            )
        else:
            tail = self._LIGHTING_ONLY_TAIL
        count = len(result.maps)
        notes = [f"Baked {count} object{'s' if count != 1 else ''} → {where}. {tail}"]
        if result.in_place:
            n = len(result.in_place)
            notes.append(
                f" {n} written back into {'its' if n == 1 else 'their'} atlas "
                f"cell{'s' if n != 1 else ''} in place; every other object's "
                "texels kept."
            )
        if result.repacked:
            notes.append(
                " Re-packed whole: "
                + "; ".join(f"{name} ({why})" for name, why in result.repacked.items())
                + "."
            )
        if result.excluded:
            notes.append(f" {len(result.excluded)} excluded.")
        if result.hidden:
            notes.append(f" {len(result.hidden)} hidden or templated, not baked.")
        if result.retired:
            n = len(result.retired)
            notes.append(
                f" Moved {n} superseded map{'s' if n != 1 else ''} nothing reads "
                "any more to the Recycle Bin (or a temp folder kept 30 days)."
            )
        if result.unbaked:
            notes.append(
                f" {len(result.unbaked)} not baked (cancelled or failed); "
                "they keep the lightmap they had."
            )
        if result.verdict:
            notes.append(f"  WARNING: {result.verdict}")
        return "".join(notes)

    # ------------------------------------------------------------------
    # Header-menu actions
    # ------------------------------------------------------------------

    def revert_to_source(self) -> None:
        """Take the lightmaps off the selected objects (or every baked one), once confirmed.

        A header-menu item one row from the panel's other actions, and with
        nothing selected it reaches every baked object in the scene -- so it
        says exactly what it will do and waits for OK. What it removes is only
        the wiring: the materials and texture UVs were never changed, and the
        EXR files stay on disk.
        """
        if self._baker is None:
            self._baker = LightmapBaker()
        # Faces or a shape name their mesh (the bake's own rule,
        # resolve_meshes): read as transforms only, a face selection became
        # "nothing selected", and the dialog offered to revert EVERY baked object.
        raw = cmds.ls(selection=True, long=True) or []
        selection = TextureBaker.resolve_meshes(raw, descendants=True) if raw else None
        targets = self._baker.baked_objects(selection) if selection != [] else []
        if not targets:
            self.ui.footer.setText(
                "None of the selected objects has a lightmap."
                if raw
                else "No baked objects to revert."
            )
            return
        count = len(targets)
        whose = "selected" if selection else "baked"
        confirmed = self.sb.confirm(
            f"<b>Revert to Source</b> &mdash; {count} {whose} "
            f"object{'s' if count != 1 else ''}"
            + ("" if selection else " (nothing is selected, so: all of them)")
            + "<br><br>Removes their lightmap wiring: each object's lightmap "
            "record and its entry in the scene's lightmap export data. Their "
            "materials and texture UVs were never changed, and the baked EXR "
            "files stay on disk.<br><br>An export will carry no lightmap for "
            "them until they are baked again. One Undo restores the wiring.",
            yes="Ok",
            no="Cancel",
        )
        if not confirmed:
            self.ui.footer.setText("Revert to Source cancelled.")
            return
        reverted = self._baker.revert(selection)
        if reverted:
            self.ui.footer.setText(
                f"Reverted {count} object{'s' if count != 1 else ''} to source "
                "(lightmap wiring removed; the EXR files stay on disk)."
            )
        else:
            self.ui.footer.setText("No baked objects to revert.")

    def list_changed_objects(self) -> None:
        """Select what a Changed bake would re-bake, and list each with why.

        The dry run (:meth:`LightmapBaker.stale_report`, the panel's dials):
        one line per object in the Script Editor -- its own change, or the
        neighbour whose change reaches it -- then the lighting, the atlases
        that re-pack whole, what cannot bake and what has no baseline. The
        footer carries the one-line summary. Nothing is baked.
        """
        try:
            report = self._make_baker().stale_report()
        except Exception as error:  # noqa: BLE001 -- a menu item never raises
            self.logger.warning(f"Could not read what changed: {error}")
            self.ui.footer.setText("Could not read what changed (see Script Editor).")
            return
        summary = report.summary()
        lines = report.lines()
        if lines:
            self.logger.log_group(summary, lines)
        if report.targets and cmds is not None:
            cmds.select(list(report.targets), replace=True)
        self.ui.footer.setText(summary)

    def open_sourceimages(self) -> None:
        """Open the bake's output folder in the file manager.

        The folder the last bake wrote to when there is one -- Beside Material
        Textures sends maps away from the Output Directory, which then showed
        nothing new -- else the Output Directory field's resolved target when
        it points somewhere that exists, else the project's sourceimages it
        resolves against: a menu item labelled "where the bakes go" that opened
        the *base* of a custom relative path would be one click short of the
        truth.
        """
        src = self._last_output_dir
        if not (src and os.path.isdir(src)):
            src = self._output_dir()
        if src and not os.path.isdir(src):  # not baked into yet
            src = self._sourceimages_dir()
        # The shared opener, not os.startfile: that exists on Windows only, and
        # the menu item raised AttributeError on a macOS or Linux Maya.
        if not ptk.FileUtils.open_explorer(src, logger=self.logger):
            self.ui.footer.setText(
                "No sourceimages directory — set a Maya project first."
            )

    @staticmethod
    def _sourceimages_dir() -> Optional[str]:
        """The scene project's sourceimages path, or None (no project / lookup
        failed): the project the scene is saved in, not merely the active one.

        Returns the path even if the folder doesn't exist yet (the bake creates it).
        """
        try:
            return DataNodes.scene_source_images_dir() or None
        except Exception:
            return None


# -----------------------------------------------------------------------------

if __name__ == "__main__":
    from mayatk.ui_utils.maya_ui_handler import MayaUiHandler

    ui = MayaUiHandler.instance().get("lightmap_baker", reload=True)
    ui.show(pos="screen", app_exec=True)
