# !/usr/bin/python
# coding=utf-8
"""Per-object event trigger attributes for game-engine export.

Stamps a keyable **enum** attribute onto transforms so artists can pick
event names from a dropdown and key them on the timeline.  Before export,
``bake_manifest()`` serialises the keyed timeline into a static string
attribute that any game engine can parse.

The ``category`` parameter prefixes attribute names, allowing multiple
independent event channels on the same object::

    EventTriggers.create(objs, category="audio", events=["Footstep", "Jump"])
    # -> audio_trigger (enum: None|Footstep|Jump)

    EventTriggers.create(objs, category="vfx", events=["Sparks", "Smoke"])
    # -> vfx_trigger (enum: None|Sparks|Smoke)

    # Before FBX export:
    EventTriggers.bake_manifest(objs, category="audio")
    # -> audio_manifest (string: "12:Footstep,24:Jump")

Pipeline
--------
1. **Maya authoring**: Enum attribute with named events -- artists key
   directly from the channel box dropdown.
2. **Pre-export bake**: ``bake_manifest()`` reads enum keyframes and
   writes a ``{cat}_manifest`` string: ``"frame:event,frame:event,..."``
3. **Engine import**: Parse the manifest string and inject native
   animation events (e.g. Unity ``AnimationEvent``).
4. **Engine runtime**: Standard event callback -- no per-frame polling.
"""
from typing import Dict, List, Optional, Tuple
import pythontk as ptk

try:
    import pymel.core as pm
except ImportError:
    pass

from mayatk.core_utils._core_utils import CoreUtils
from mayatk.node_utils.attributes._attributes import Attributes, AttributeTemplate


class EventTriggers(ptk.LoggingMixin):
    """Manages per-object event triggers for game-engine export.

    Adds a keyable ``{category}_trigger`` **enum** attribute to object
    transforms.  Enum field names define the available events (index 0
    is always ``"None"``).

    Before export, call ``bake_manifest()`` to serialise the keyed
    timeline into a ``{category}_manifest`` string attribute that
    survives FBX export as a static user property.

    The ``category`` parameter (default ``"event"``) prefixes attribute
    names, enabling multiple independent event channels on one object.

    Typical workflow::

        # Setup
        EventTriggers.create(objects, events=["Footstep", "Jump", "Land"])

        # Key events on timeline (artist can also key from channel box dropdown)
        EventTriggers.set_key(obj, event="Footstep", time=10)

        # Before export -- bake enum keys into portable manifest string
        EventTriggers.bake_manifest(objects)
        # -> event_manifest = "10:Footstep,24:Jump"

        # Add more events later (safe -- appends, no index shift)
        EventTriggers.add_events(objects, events=["Slide"])

        # Remove a specific category
        EventTriggers.remove(objects, category="vfx")
    """

    DEFAULT_CATEGORY = "event"
    """Default prefix when no category is specified."""

    DEFAULT_EVENTS = ("None",)
    """The zero-index entry is always 'None' (no event)."""

    # ------------------------------------------------------------------
    # Naming helpers
    # ------------------------------------------------------------------

    @classmethod
    def attr_names(cls, category: Optional[str] = None) -> Tuple[str, str]:
        """Return the ``(trigger_attr, manifest_attr)`` pair for a category.

        Parameters:
            category: Prefix for attribute names.

        Returns:
            Tuple of ``("{cat}_trigger", "{cat}_manifest")``.
        """
        cat = category or cls.DEFAULT_CATEGORY
        return f"{cat}_trigger", f"{cat}_manifest"

    # ------------------------------------------------------------------
    # Create / Setup
    # ------------------------------------------------------------------

    @classmethod
    @CoreUtils.undoable
    def create(
        cls,
        objects: Optional[List] = None,
        events: Optional[List[str]] = None,
        category: Optional[str] = None,
    ) -> Dict[str, Dict]:
        """Create event trigger attributes on objects.

        .. warning::
            This **destroys existing keyframes** for the given category
            because it calls ``remove()`` first.  Use ``ensure()`` for
            non-destructive creation/update.

        Creates a keyable **enum** attribute whose field names are the
        available events.  Index 0 is always ``"None"`` (no event).
        Automatically bakes the manifest string after creation.

        Parameters:
            objects: Transforms to set up.  Defaults to selection.
            events: Event names (excluding the implicit ``"None"`` at index 0).
            category: Attribute prefix (default ``"event"``).
                Use different categories for independent channels,
                e.g. ``"audio"``, ``"vfx"``, ``"gameplay"``.

        Returns:
            Per-object results dict with ``attrs_created`` and ``events``.
        """
        if objects is None:
            objects = pm.selected()
        if not objects:
            cls.logger.warning("No objects selected.")
            return {}

        trigger_attr, manifest_attr = cls.attr_names(category)

        # Clean any previous state for this category first
        cls.remove(objects, category=category)

        event_list = list(cls.DEFAULT_EVENTS) + (events or [])
        results = {}

        # Create the enum attribute
        template = AttributeTemplate(
            long_name=trigger_attr,
            attribute_type="enum",
            keyable=True,
            default_value=0,
            enum_names=event_list,
        )
        Attributes.create_attributes(objects, template)

        for obj in pm.ls(objects):
            results[obj.name()] = {
                "attrs_created": [f"{obj.name()}.{trigger_attr}"],
                "events": event_list,
                "category": category or cls.DEFAULT_CATEGORY,
            }
            cls.logger.info(
                f"Event triggers ({trigger_attr}) on {obj}: " f"{':'.join(event_list)}"
            )
        # Auto-bake manifest so it stays current.
        cls.bake_manifest(objects, category=category)
        return results

    # Legacy alias
    setup = create

    # ------------------------------------------------------------------
    # Event Management
    # ------------------------------------------------------------------

    @classmethod
    @CoreUtils.undoable
    def ensure(
        cls,
        objects: Optional[List] = None,
        events: Optional[List[str]] = None,
        category: Optional[str] = None,
    ) -> Dict[str, Dict]:
        """Create or update event triggers — the **recommended** entry-point.

        Prefer this over ``create()`` which destroys existing keyframes.

        If the trigger attribute does not yet exist on an object it is
        created with the given *events*.  If it already exists the new
        events are appended (existing keyframes are never disturbed).

        Parameters:
            objects: Transforms to set up.  Defaults to selection.
            events: Event names to add (excluding the implicit ``"None"``).
            category: Attribute prefix (default ``"event"``).

        Returns:
            Per-object results dict.
        """
        if objects is None:
            objects = pm.selected()
        if not objects:
            cls.logger.warning("No objects selected.")
            return {}

        trigger_attr, _ = cls.attr_names(category)

        needs_create = [o for o in pm.ls(objects) if not o.hasAttr(trigger_attr)]
        needs_update = [o for o in pm.ls(objects) if o.hasAttr(trigger_attr)]

        results = {}
        if needs_create:
            results.update(cls.create(needs_create, events=events, category=category))
        if needs_update and events:
            cls.add_events(needs_update, events=events, category=category)
            for obj in needs_update:
                results[obj.name()] = {
                    "events": cls.get_events(obj, category=category),
                    "category": category or cls.DEFAULT_CATEGORY,
                }

        return results

    @classmethod
    @CoreUtils.undoable
    def add_events(
        cls,
        objects: Optional[List] = None,
        events: Optional[List[str]] = None,
        category: Optional[str] = None,
    ) -> None:
        """Append new event names to existing enum fields.

        Existing keyframes are not affected because new events are
        appended (higher indices).  This avoids the index-shift problem.
        Automatically re-bakes the manifest string after modification.

        Parameters:
            objects: Transforms with existing event trigger attrs.
            events: New event names to append.
            category: Attribute prefix (default ``"event"``).
        """
        if objects is None:
            objects = pm.selected()
        if not objects or not events:
            return

        trigger_attr, _ = cls.attr_names(category)

        for obj in pm.ls(objects):
            if not obj.hasAttr(trigger_attr):
                cls.logger.warning(
                    f"{obj} has no '{trigger_attr}'. Run create() first."
                )
                continue

            # Append each new event via the centralised helper.
            for event in events:
                Attributes.add_enum_field(str(obj), trigger_attr, event)

            cls.logger.info(
                f"Updated events on {obj}.{trigger_attr}: "
                f"{Attributes.get_enum_fields(str(obj), trigger_attr)}"
            )

        # Auto-bake manifest so it stays current.
        cls.bake_manifest(objects, category=category)

    @classmethod
    def get_events(
        cls,
        obj,
        category: Optional[str] = None,
    ) -> List[str]:
        """Return the event name list from the enum fields.

        Parameters:
            obj: A single Maya transform.
            category: Attribute prefix (default ``"event"``).

        Returns:
            List of event names (index 0 is always "None").
        """
        trigger_attr, _ = cls.attr_names(category)
        obj = pm.PyNode(obj)
        if not obj.hasAttr(trigger_attr):
            return []
        return Attributes.get_enum_fields(str(obj), trigger_attr)

    # Keep old name as alias for compatibility
    get_manifest = get_events

    @classmethod
    def event_index(
        cls,
        obj,
        event_name: str,
        category: Optional[str] = None,
    ) -> int:
        """Return the integer index for an event name, or -1 if not found."""
        trigger_attr, _ = cls.attr_names(category)
        return Attributes.enum_label_to_index(str(obj), trigger_attr, event_name)

    # ------------------------------------------------------------------
    # Keyframing
    # ------------------------------------------------------------------

    @classmethod
    def set_key(
        cls,
        obj,
        event: str,
        time: Optional[float] = None,
        auto_clear: bool = True,
        category: Optional[str] = None,
    ) -> bool:
        """Set a stepped keyframe for an event trigger.

        Parameters:
            obj: Transform with event trigger attributes.
            event: Event name (must exist in the enum fields).
            time: Frame number.  If None, uses current time.
            auto_clear: If True, also keys value 0 ("None") one frame
                before the trigger so events are discrete pulses.
            category: Attribute prefix (default ``"event"``).

        Returns:
            True if the key was set, False if the event was not found.
        """
        trigger_attr, _ = cls.attr_names(category)
        obj = pm.PyNode(obj)
        idx = cls.event_index(obj, event, category=category)
        if idx < 0:
            cls.logger.warning(
                f"Event '{event}' not in events for {obj}. "
                f"Available: {cls.get_events(obj, category=category)}"
            )
            return False

        kwargs = {"attribute": trigger_attr, "value": float(idx)}
        if time is not None:
            kwargs["time"] = time
        else:
            time = pm.currentTime(query=True)

        if auto_clear and idx != 0 and time > 1:
            pm.setKeyframe(
                obj,
                attribute=trigger_attr,
                time=time - 1,
                value=0.0,
                itt="stepnext",
                ott="step",
            )

        pm.setKeyframe(obj, itt="stepnext", ott="step", **kwargs)
        cls.logger.info(f"Keyed '{event}' (idx={idx}) on {obj} at frame {time}")
        return True

    @classmethod
    def clear_key(
        cls,
        obj,
        time: Optional[float] = None,
        category: Optional[str] = None,
    ) -> None:
        """Remove the trigger keyframe at a specific time.

        Parameters:
            obj: Transform with event trigger attributes.
            time: Frame to clear.  If None, uses current time.
            category: Attribute prefix (default ``"event"``).
        """
        trigger_attr, _ = cls.attr_names(category)
        obj = pm.PyNode(obj)
        kwargs = {"attribute": trigger_attr}
        if time is not None:
            kwargs["time"] = (time, time)
        pm.cutKey(obj, **kwargs)

    # ------------------------------------------------------------------
    # Manifest Baking (pre-export)
    # ------------------------------------------------------------------

    @classmethod
    @CoreUtils.undoable
    def bake_manifest(
        cls,
        objects: Optional[List] = None,
        category: Optional[str] = None,
    ) -> Dict[str, str]:
        """Bake enum keyframes into a portable manifest string.

        Reads all keyframes on the ``{cat}_trigger`` enum attribute,
        resolves each value to its event name, and writes the result
        as a ``{cat}_manifest`` string attribute in the format::

            "12:Footstep,24:Jump,48:Land"

        Only non-zero (non-"None") events are included.  The manifest
        string survives FBX export as a static user property, which
        the engine importer can parse into native animation events.

        Call this **before FBX export**.

        Parameters:
            objects: Transforms to bake.  Defaults to selection.
            category: Attribute prefix (default ``"event"``).

        Returns:
            Dict mapping object name -> baked manifest string.
        """
        if objects is None:
            objects = pm.selected()
        if not objects:
            cls.logger.warning("No objects selected.")
            return {}

        trigger_attr, manifest_attr = cls.attr_names(category)
        results = {}

        for obj in pm.ls(objects):
            if not obj.hasAttr(trigger_attr):
                cls.logger.warning(f"{obj} has no '{trigger_attr}'. Skipping.")
                continue

            events = cls.get_events(obj, category=category)
            if not events:
                continue

            # Build index→label map for correct lookup with gapped indices.
            pairs = Attributes.parse_enum_def(str(obj), trigger_attr)
            idx_to_label = {idx: label for label, idx in pairs}

            # Get all keyframe times on the trigger attribute
            key_times = pm.keyframe(
                obj, attribute=trigger_attr, query=True, timeChange=True
            )

            entries = []
            if key_times:
                for t in sorted(key_times):
                    val = pm.keyframe(
                        obj,
                        attribute=trigger_attr,
                        query=True,
                        time=(t, t),
                        valueChange=True,
                    )
                    if val:
                        idx = int(round(val[0]))
                        label = idx_to_label.get(idx)
                        # Skip "None" (index 0) and unknown indices
                        if label and idx != 0:
                            frame = int(t) if t == int(t) else t
                            entries.append(f"{frame}:{label}")

            manifest_str = ",".join(entries) if entries else ""

            # Create or update the manifest string attribute
            if not obj.hasAttr(manifest_attr):
                pm.addAttr(obj, ln=manifest_attr, dt="string")
            obj.attr(manifest_attr).set(manifest_str)

            results[obj.name()] = manifest_str
            cls.logger.info(
                f"Baked manifest on {obj}.{manifest_attr}: "
                f"{manifest_str or '(empty)'}"
            )

        return results

    # ------------------------------------------------------------------
    # Remove / Cleanup
    # ------------------------------------------------------------------

    @classmethod
    @CoreUtils.undoable
    def remove(
        cls,
        objects: Optional[List] = None,
        category: Optional[str] = None,
    ) -> None:
        """Remove event trigger attributes and animation curves.

        Parameters:
            objects: Transforms to clean up.  Defaults to selection.
            category: Attribute prefix to remove (default ``"event"``).
                Pass ``"*"`` to remove **all** event trigger categories
                found on the objects.
        """
        if objects is None:
            objects = pm.selected()
        if not objects:
            return

        if category == "*":
            cls._remove_all_categories(objects)
            return

        trigger_attr, manifest_attr = cls.attr_names(category)

        for obj in pm.ls(objects):
            if obj.hasAttr(trigger_attr):
                curves = pm.listConnections(obj.attr(trigger_attr), type="animCurve")
                if curves:
                    pm.delete(curves)
                obj.deleteAttr(trigger_attr)

            if obj.hasAttr(manifest_attr):
                obj.deleteAttr(manifest_attr)

            cls.logger.info(f"Removed {trigger_attr}/{manifest_attr} from {obj}")

    @classmethod
    def _remove_all_categories(cls, objects) -> None:
        """Scan objects for any ``*_trigger``/``*_manifest`` pairs and remove."""
        for obj in pm.ls(objects):
            all_attrs = pm.listAttr(obj, userDefined=True) or []
            triggers = [a for a in all_attrs if a.endswith("_trigger")]
            for trigger_attr in triggers:
                cat = trigger_attr.rsplit("_trigger", 1)[0]
                manifest_attr = f"{cat}_manifest"
                if obj.hasAttr(trigger_attr):
                    curves = pm.listConnections(
                        obj.attr(trigger_attr), type="animCurve"
                    )
                    if curves:
                        pm.delete(curves)
                    obj.deleteAttr(trigger_attr)
                if obj.hasAttr(manifest_attr):
                    obj.deleteAttr(manifest_attr)
                cls.logger.info(f"Removed {trigger_attr}/{manifest_attr} from {obj}")
