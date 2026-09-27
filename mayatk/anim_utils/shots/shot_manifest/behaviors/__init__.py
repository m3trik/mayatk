# coding=utf-8
"""Behaviors — load and apply keying recipes.

A behavior template defines attribute keyframe patterns (e.g. fade-in,
fade-out) anchored to a time range's start or end.  Shared across all
tools in the ``shots`` subpackage.

The pure core (template discovery/loading, schema, keyframe math) lives in
``pythontk.core_utils.engines.shots.manifest.behaviors`` — JSON templates
shared with blendertk; built-ins ship with the engine and user templates go
under ``user_config_root()/shots/manifest_behaviors/``.  The Maya appliers
live in :mod:`._behaviors`.

Package facade: the public API is published here, lazily
(``lazy_exports``), so
``from ...behaviors import X`` keeps working and ``mock.patch`` of
``...behaviors.X`` still takes effect for callers that read the name off this
package (the lazy ``from ...behaviors import X`` other modules do at call time —
e.g. ``compute_duration``).

To intercept an *intra-class* call — one ``Behaviors`` method calling another
(``apply_behavior`` → ``apply_audio_clip``, ``verify_behavior`` →
``_verify_audio_clip``) — patch ``...behaviors._behaviors.<Class>.<name>``,
where the call is actually resolved.
"""

import pythontk as ptk
from pythontk.core_utils.module_resolver import lazy_exports

lazy_exports(globals(), {"_behaviors": ("Behaviors", "BehaviorSpec")})

# The pure-core staticmethods were once bound here as flat module functions;
# they are ``Behaviors`` methods (inherited from the engine class).
_BEHAVIORS = "mayatk.anim_utils.shots.shot_manifest.behaviors.Behaviors"
ptk.Deprecation.attributes(
    globals(),
    {
        "load_behavior": f"{_BEHAVIORS}.load_behavior",
        "list_behaviors": f"{_BEHAVIORS}.list_behaviors",
        "resolve_keys": f"{_BEHAVIORS}.resolve_keys",
        "templates": f"{_BEHAVIORS}.templates",
    },
    remove_in="0.22.0",
    since="2026-09-26",
)
