# coding=utf-8
"""CSV mapping resolver — interprets JSON mapping files.

A mapping file is a ``.json`` file that declaratively specifies how CSV columns
map to :class:`BuilderStep` fields and how derived values (e.g. audio objects)
are resolved. See :mod:`._mapping` for the file format and the full docstring.

Package facade: the implementation lives in :mod:`._mapping` (kept out of
``__init__`` per the package convention). The public :class:`Mapping` and
:class:`MappingSpec` classes are re-exported here so ``from ...mapping import
Mapping`` works; call the resolver methods as ``Mapping.resolve`` /
``Mapping.discover`` / ``MappingSpec.format_markdown`` etc. Patch a class
method at ``...mapping._mapping.Mapping.<name>`` when a test seam is needed.
"""

from mayatk.anim_utils.shots.shot_manifest.mapping._mapping import (  # noqa: F401
    DEFAULT_DIR,
    Mapping,
)
from mayatk.anim_utils.shots.shot_manifest.mapping._spec import (  # noqa: F401
    MappingSpec,
    AUDIO_METHODS,
)

__all__ = [
    "DEFAULT_DIR",
    "Mapping",
    "MappingSpec",
    "AUDIO_METHODS",
]
