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

from pythontk.core_utils.module_resolver import lazy_exports

lazy_exports(globals(), {"_behaviors": ("Behaviors", "BehaviorSpec")})
