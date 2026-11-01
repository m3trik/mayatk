# !/usr/bin/python
# coding=utf-8
"""Every Maya host adapter satisfies the pythontk Protocol it implements.

pythontk declares each host seam as a ``runtime_checkable`` ``<Domain>Host``
Protocol beside its model (its own ``test_host_seams`` holds that rule); this is
the Maya half: each adapter mayatk binds, built with no Maya (``maya.cmds`` from
this dir's conftest), is an instance of its Protocol, so a member a Protocol
gains is a member every adapter has. The roster is checked against the seams
pythontk exports: a new seam needs its row here. Added: 2026-11-01
"""

import importlib
import unittest

import pythontk as ptk


def _adapter(module, name):
    return lambda: getattr(importlib.import_module(module), name)()


#: ``(Protocol, what builds the adapter)`` for every seam mayatk implements.
ADAPTERS = (
    ("ShotsHost", _adapter("mayatk.anim_utils.shots._shots", "MayaShotsHost")),
    (
        "ShotTransferHost",
        lambda: _adapter("mayatk.anim_utils.shots._shots", "MayaShotsHost")().transfer(),
    ),
    ("KeyStashHost", _adapter("mayatk.anim_utils.key_stash._key_stash", "MayaKeyStashHost")),
    (
        "SidecarHost",
        _adapter("mayatk.env_utils.hierarchy_sync.scene_data_sidecar", "MayaSidecarHost"),
    ),
    ("WorkspaceMapHost", _adapter("mayatk.env_utils.workspace_map", "MayaWorkspaceMapHost")),
    (
        "ProbeHost",
        _adapter("mayatk.light_utils.lightmap_baker._probe_placement", "MayaProbeHost"),
    ),
    (
        "ProbeMesh",
        _adapter("mayatk.light_utils.lightmap_baker._probe_placement", "MayaProbeHost"),
    ),
    ("SequenceHost", _adapter("mayatk.anim_utils.playblast_exporter", "MayaPlayblastHost")),
    ("EmissiveGroupsHost", _adapter("mayatk.mat_utils.emissive_groups", "MayaEmissiveGroups")),
    ("CollectHost", _adapter("mayatk.mat_utils.texture_path_editor", "TexturePathEditor")),
    (
        "TransferHost",
        _adapter("mayatk.uv_utils.texture_combine._transfer_host", "MayaTransferHost"),
    ),
)


class TestHostAdapters(unittest.TestCase):
    def test_every_adapter_satisfies_its_protocol(self):
        for protocol, build in ADAPTERS:
            with self.subTest(protocol=protocol):
                seam = getattr(ptk, protocol, None)
                if seam is None:  # a pythontk older than the seam
                    self.skipTest(f"this pythontk exports no {protocol}")
                self.assertIsInstance(build(), seam)

    def test_every_seam_pythontk_exports_has_a_row(self):
        seams = {
            name
            for name in ptk.__all__
            if name.endswith("Host")
            and not name.startswith("Pure")
            and getattr(getattr(ptk, name), "_is_protocol", False)
        }
        self.assertEqual(sorted(seams - {p for p, _build in ADAPTERS}), [])


if __name__ == "__main__":
    unittest.main()
