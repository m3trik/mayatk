# !/usr/bin/python
# coding=utf-8
"""Texture Combine's Transfer mode: Maya's half of the run.

The run itself -- read the selection, resolve Auto, refuse what cannot run,
then the UV pass or the texture pass in one undo step -- is host-free and
lives once in pythontk (:class:`pythontk.TransferRun`), shared with
blendertk's twin. :class:`MayaTransferHost` hands it this library's own
operations; :meth:`TextureCombine.run_transfer
<mayatk.uv_utils.texture_combine.texture_combine.TextureCombine.run_transfer>`
builds it. It sits with Texture Combine, the tool above every domain it reads:
the lightmap pass reaches ``light_utils``, which ranks above ``uv_utils``.
"""

import pythontk as ptk

from mayatk.core_utils._core_utils import CoreUtils
from mayatk.uv_utils.texture_transfer import TextureTransfer


class MayaTransferHost(ptk.PureTransferHost):
    """What :class:`pythontk.TransferRun` asks of Maya (a
    :class:`pythontk.TransferHost`): this library's own operations, by name --
    *transfers*' (:class:`TextureTransfer` or a subclass) selection reading,
    pairing, materials and engine, the UV passes (``UvUtils``), the lightmap
    pass (``LightmapRecords``), a blank name (*combine*'s
    ``TextureCombine.default_name``) and one undo chunk: each of the Pure
    default's answers, from the scene. Twin of blendertk's
    ``BlenderTransferHost``.
    """

    def __init__(self, combine=None, transfers=None):
        self.combine = combine
        self.transfers = transfers or TextureTransfer

    def selection_sides(self, mode, stored):
        return self.transfers.selection_sides(mode, stored)

    def face_materials(self, mesh):
        return self.transfers.face_materials(mesh)

    def material_maps(self, material):
        return self.transfers.material_maps(material)

    def pair_sources(self, targets, sources):
        return self.transfers.pair_sources(targets, sources)

    def texture_transfer(self):
        return self.transfers()

    def default_name(self, nodes) -> str:
        combine = self.combine
        if combine is None:
            from mayatk.uv_utils.texture_combine.texture_combine import TextureCombine

            combine = TextureCombine(log_level="WARNING")
        return combine.default_name(nodes, fallback="transfer")

    @staticmethod
    def transfer_uvs(sources, targets):
        from mayatk.uv_utils._uv_utils import UvUtils

        return UvUtils.transfer_uvs(sources, targets, match_by_similarity=False)

    @staticmethod
    def transfer_uvs_to_similar(source, candidates, tolerance):
        from mayatk.uv_utils._uv_utils import UvUtils

        return UvUtils.transfer_uvs_to_similar(source, candidates, tolerance=tolerance)

    @staticmethod
    def has_lightmap(mesh) -> bool:
        from mayatk.light_utils.lightmap_baker.lightmap_records import LightmapRecords

        return bool(LightmapRecords.baked_objects([mesh]))

    @staticmethod
    def transfer_lightmaps(targets, source, **options):
        from mayatk.light_utils.lightmap_baker.lightmap_records import LightmapRecords

        return LightmapRecords.transfer_lightmaps(targets, source, **options)

    @staticmethod
    def undo_chunk(name):
        return CoreUtils.undo_chunk(name)
