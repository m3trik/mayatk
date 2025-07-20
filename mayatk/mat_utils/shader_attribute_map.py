# !/usr/bin/python
# coding=utf-8
from typing import Optional, Dict
from collections import namedtuple


from typing import Optional, Tuple, Dict, Any
from collections import namedtuple

# Each slot is: (attribute_name, output_plug)
# Example: ("TEX_roughness_map", "outColorR")
ShaderAttrSlot = Optional[Tuple[str, str]]

ShaderAttrs = namedtuple(
    "ShaderAttrs",
    [
        "baseColor",  # Albedo/Diffuse/Base Color
        "emission",  # Emissive/Glow
        "specular",  # Specular/F0
        "roughness",  # Roughness/Gloss/Eccentricity
        "metallic",  # Metalness/Metallic
        "opacity",  # Opacity/Transparency
        "normal",  # Normal/Bump
        "ambientOcclusion",  # Ambient Occlusion
    ],
)


class ShaderAttributeMap:
    """
    Central mapping of logical texture/material channels to per-shader attribute/plug pairs.
    Extend by adding new shader types or logical channels as needed.
    """

    SHADER_TYPES = [
        "lambert",
        "blinn",
        "aiStandardSurface",
        "standardSurface",
        "StingrayPBS",
        "openPBRSurface",
    ]

    SHADER_ATTRS: Dict[str, ShaderAttrs] = {
        "lambert": ShaderAttrs(
            baseColor=("color", "outColor"),
            emission=("incandescence", "outColor"),
            specular=None,
            roughness=None,
            metallic=None,
            opacity=("transparency", "outColor"),
            normal=None,
            ambientOcclusion=None,
        ),
        "blinn": ShaderAttrs(
            baseColor=("color", "outColor"),
            emission=None,
            specular=("specularColor", "outColor"),
            roughness=("eccentricity", "outColorR"),  # expects .outColorR from file
            metallic=None,
            opacity=("transparency", "outColor"),
            normal=("normalCamera", "outColor"),  # <-- ADD THIS
            ambientOcclusion=None,
        ),
        "aiStandardSurface": ShaderAttrs(
            baseColor=("baseColor", "outColor"),
            emission=("emissionColor", "outColor"),
            specular=("specularColor", "outColor"),
            roughness=("specularRoughness", "outAlpha"),
            metallic=("metalness", "outAlpha"),
            opacity=("opacity", "outAlpha"),
            normal=(
                "normalCamera",
                "outColor",
            ),  # connect to aiNormalMap.outValue, not file.outColor
            ambientOcclusion=None,
        ),
        "standardSurface": ShaderAttrs(
            baseColor=("baseColor", "outColor"),
            emission=("emissionColor", "outColor"),
            specular=("specularColor", "outColor"),
            roughness=("specularRoughness", "outAlpha"),
            metallic=("metalness", "outAlpha"),
            opacity=("opacity", "outAlpha"),
            normal=("normalCamera", "outColor"),
            ambientOcclusion=None,
        ),
        "StingrayPBS": ShaderAttrs(
            baseColor=("TEX_color_map", "outColor"),
            emission=("TEX_emissive_map", "outColor"),
            specular=None,
            roughness=("TEX_roughness_map", "outColorR"),
            metallic=("TEX_metallic_map", "outColorR"),
            opacity=("TEX_opacity_map", "outAlpha"),
            normal=("TEX_normal_map", "outColor"),
            ambientOcclusion=("TEX_ao_map", "outColor"),
        ),
        "openPBRSurface": ShaderAttrs(
            baseColor=("baseColor", "outColor"),
            emission=("emissionColor", "outColor"),
            specular=("specularColor", "outColor"),
            roughness=("specularRoughness", "outAlpha"),
            metallic=("metalness", "outAlpha"),
            opacity=("opacity", "outAlpha"),
            normal=("normalCamera", "outColor"),
            ambientOcclusion=("ambientOcclusion", "outColor"),
        ),
    }

    @classmethod
    def logical_channels(cls) -> Tuple[str, ...]:
        """Returns the logical channel names as a tuple."""
        return ShaderAttrs._fields

    @classmethod
    def get_attr(cls, shader_type: str, logical: str) -> Optional[Tuple[str, str]]:
        """Return (attribute, plug) tuple for shader type and logical channel, or None."""
        attrs = cls.SHADER_ATTRS.get(shader_type)
        if attrs is None or logical not in cls.logical_channels():
            return None
        return getattr(attrs, logical)

    @classmethod
    def get_mapping(
        cls, src_type: str, dst_type: str
    ) -> Tuple[Tuple[str, str, str], ...]:
        """
        Returns a tuple of (src_attr, src_plug, dst_attr) for each logical channel present in both shader types.
        """
        src_attrs = cls.SHADER_ATTRS.get(src_type)
        dst_attrs = cls.SHADER_ATTRS.get(dst_type)
        if not src_attrs or not dst_attrs:
            return tuple()
        pairs = []
        for logical in cls.logical_channels():
            src_info = getattr(src_attrs, logical)
            dst_info = getattr(dst_attrs, logical)
            if src_info and dst_info:
                src_attr, src_plug = src_info
                dst_attr, _ = dst_info  # (dest plug is for future-proofing if needed)
                pairs.append((src_attr, src_plug, dst_attr))
        return tuple(pairs)

    @classmethod
    def add_shader_type(cls, shader_type: str, attrs: ShaderAttrs) -> None:
        """Add a new shader type mapping."""
        cls.SHADER_ATTRS[shader_type] = attrs

    @classmethod
    def update_attr(
        cls, shader_type: str, logical: str, value: Optional[Tuple[str, str]]
    ) -> None:
        """Update a logical channel mapping for a shader type."""
        attrs = cls.SHADER_ATTRS.get(shader_type)
        if not attrs or logical not in cls.logical_channels():
            return
        cls.SHADER_ATTRS[shader_type] = attrs._replace(**{logical: value})

    @classmethod
    def as_dict(cls) -> Dict[str, Dict[str, Any]]:
        """Returns a dict of dicts for all shader mappings."""
        return {
            stype: dict(attrs._asdict()) for stype, attrs in cls.SHADER_ATTRS.items()
        }


# --------------------------------------------------------------------------------------------

if __name__ == "__main__":
    pass

# --------------------------------------------------------------------------------------------
# Notes
# --------------------------------------------------------------------------------------------
