# !/usr/bin/python
# coding=utf-8
"""Deprecated: the Export button's module is :mod:`.export_preview` -- its
option menu became a live preview of the export (2026-10-11)."""

import pythontk as ptk

ptk.Deprecation.attributes(
    globals(),
    {
        "ExportMenuMixin": (
            "mayatk.anim_utils.shots.shot_manifest.export_preview.ExportPreviewMixin"
        ),
    },
    remove_in="0.25.0",
    since="2026-10-11",
)
