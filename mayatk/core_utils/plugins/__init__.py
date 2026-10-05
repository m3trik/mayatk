# !/usr/bin/python
# coding=utf-8
"""The Maya plug-ins mayatk loads, through one door (``Plugins``): by name only,
so GUI Maya never stops at its untrusted-plug-in prompt.

All classes are lazy-loaded via mayatk root package.
Import from mayatk directly: from mayatk import Plugins
"""

# Lazy-loaded via parent package - no explicit imports needed
