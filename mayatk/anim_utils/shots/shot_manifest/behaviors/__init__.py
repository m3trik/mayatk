# coding=utf-8
"""Behaviors — load and apply YAML keying recipes.

A behavior template defines attribute keyframe patterns (e.g. fade-in,
fade-out) anchored to a time range's start or end.  Shared across all
tools in the ``shots`` subpackage.

Package facade: the implementation lives in :mod:`._behaviors` (kept out of
``__init__`` per the package convention). The public API is re-exported here, so
``from ...behaviors import X`` keeps working and ``mock.patch`` of
``...behaviors.X`` still takes effect for callers that read the name off this
package (the lazy ``from ...behaviors import X`` other modules do at call time —
e.g. ``compute_duration``).

To intercept an *intra-module* call — one ``_behaviors`` function calling another
(``apply_behavior`` → ``apply_audio_clip``, ``verify_behavior`` →
``_verify_audio_clip``) — patch ``...behaviors._behaviors.<name>`` instead, where
the call is actually resolved; patching the re-export here would not affect it.
"""
from mayatk.anim_utils.shots.shot_manifest.behaviors._behaviors import (  # noqa: F401
    load_behavior,
    list_behaviors,
    resolve_keys,
    apply_behavior,
    verify_behavior,
    apply_audio_clip,
    compute_duration,
    apply_to_shots,
    templates,
)
from mayatk.anim_utils.shots.shot_manifest.behaviors._spec import (  # noqa: F401
    BehaviorSpec,
    format_markdown,
)

__all__ = [
    "load_behavior",
    "list_behaviors",
    "resolve_keys",
    "apply_behavior",
    "verify_behavior",
    "apply_audio_clip",
    "compute_duration",
    "apply_to_shots",
    "templates",
    "format_markdown",
    "BehaviorSpec",
]
