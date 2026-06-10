"""Built-in lightmap quality presets (read-only JSON).

Loaded by :meth:`mayatk.LightmapBaker.preset_store` as the built-in tier. This
is a package (not a bare data dir) so the JSON ships in the wheel: setuptools'
``packages.find`` only discovers dirs with an ``__init__.py``, and ``*.json``
package-data is collected per discovered package.
"""
