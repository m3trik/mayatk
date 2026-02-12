# !/usr/bin/python
# coding=utf-8
import os
from typing import Dict, List, Optional
import pythontk as ptk

try:
    import pymel.core as pm
except ImportError:
    pass

# From this package:
from mayatk.core_utils._core_utils import CoreUtils
from mayatk.env_utils._env_utils import EnvUtils

# Import delegate classes
from mayatk.mat_utils.material_fade.attribute_mode import FadeAttributeMode
from mayatk.mat_utils.material_fade.material_mode import FadeMaterialMode


class MaterialFade(ptk.LoggingMixin):
    """Keyframe-based object/material fade for StingrayPBS FBX export to Unity.

    Two modes of operation:

    **mode="attribute"** (recommended for per-object control):
        Adds a custom ``Unity_Opacity`` float attribute to each object's
        transform and keyframes it.  The curve exports inside the FBX as
        a custom property.  A lightweight Unity ``AttributeBinder`` component
        reads the value at runtime and feeds it into a ``MaterialPropertyBlock``,
        so the shared material is never duplicated and GPU instancing/batching
        is preserved.

        **Unity Import Note**: By default, Unity's ModelImporter ignores animated
        custom properties. You MUST enable ``importAnimatedCustomProperties``
        in the ModelImporter Inspector (or via AssetPostprocessor) for these
        curves to appear in the AnimationClip.

    **mode="material"** (legacy, whole-material fade):
        Keys native StingrayPBS attributes (``base_color``, ``opacity``)
        directly.  Simple but **all objects sharing that material fade
        together** — there is no per-object control.  Materials are renamed
        with a ``_Fade`` suffix so a Unity ``AssetPostprocessor`` can auto-set
        them to Transparent render mode on import.

    No utility nodes (multiplyDivide, ramp, etc.) are ever created — FBX
    strips those.

    See Also:
        :meth:`generate_unity_script` — writes the C# ``AttributeBinder``
        component needed by the ``"attribute"`` mode.
    """

    # Delegated constants for convenience/docs
    ATTR_NAME = FadeAttributeMode.ATTR_NAME
    FADE_SUFFIX = FadeMaterialMode.FADE_SUFFIX
    FADE_ATTRS = FadeMaterialMode.FADE_ATTRS

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @classmethod
    @CoreUtils.undoable
    def setup(
        cls,
        objects: Optional[List] = None,
        start_frame: Optional[float] = None,
        end_frame: Optional[float] = None,
        fade_in: bool = True,
        mode: str = "attribute",
        warn_no_other_keys: bool = True,
    ) -> Dict[str, Dict]:
        """Keyframe a fade for FBX export to Unity.

        Parameters:
            objects: Objects to process. If None, uses selection.
            start_frame: Start frame. Defaults to timeline start.
            end_frame: End frame. Defaults to timeline end.
            fade_in: True = appear (0 -> 1), False = disappear (1 -> 0).
            mode: ``"attribute"`` — per-object custom property (recommended).
                  ``"material"`` — keys material node directly (all objects
                  sharing that material fade together).
            warn_no_other_keys: (attribute mode only) Warn if the object has no
                  other keys. Unity ignores clips with only custom properties.

        Returns:
            dict: ``{node_name: {"attrs_keyed": [...]}}``
        """
        if objects is None:
            objects = pm.selected()
        if not objects:
            cls.logger.warning("No objects selected.")
            return {}

        frame_range = EnvUtils.get_env_info("frame_range")
        if start_frame is None:
            start_frame = frame_range[0]
        if end_frame is None:
            end_frame = frame_range[1]

        val_start = 0.0 if fade_in else 1.0
        val_end = 1.0 if fade_in else 0.0

        if mode == "attribute":
            return FadeAttributeMode.setup(
                objects, start_frame, end_frame, val_start, val_end, warn_no_other_keys
            )
        elif mode == "material":
            return FadeMaterialMode.setup(
                objects, start_frame, end_frame, val_start, val_end
            )
        else:
            cls.logger.error(f"Unknown mode: {mode}")
            return {}

    @classmethod
    def bake(
        cls,
        objects: Optional[List] = None,
        sample_by: float = 1.0,
        optimize: bool = True,
        mode: str = "attribute",
    ) -> None:
        """Bake fade curves for clean FBX export.

        FBX only reliably writes baked keyframe data.  Run this before
        export to ensure every frame has an explicit key.

        Parameters:
            objects: Objects to process. If None, uses selection.
            sample_by: Bake sample rate in frames.
            optimize: Run :meth:`AnimUtils.optimize_keys` after baking.
            mode: ``"attribute"`` or ``"material"`` — must match the mode
                  used in :meth:`setup`.
        """
        if objects is None:
            objects = pm.selected()
        if not objects:
            cls.logger.warning("No objects selected.")
            return

        frame_range = EnvUtils.get_env_info("frame_range")

        if mode == "attribute":
            FadeAttributeMode.bake(objects, frame_range, sample_by, optimize)
        elif mode == "material":
            FadeMaterialMode.bake(objects, frame_range, sample_by, optimize)
        else:
            cls.logger.error(f"Unknown mode: {mode}")

    @classmethod
    @CoreUtils.undoable
    def remove(
        cls,
        objects: Optional[List] = None,
        mode: str = "attribute",
    ) -> None:
        """Remove fade data and restore defaults.

        Parameters:
            objects: Objects to clean. If None, uses selection.
            mode: ``"attribute"`` or ``"material"`` — must match the mode
                  used in :meth:`setup`.
        """
        if objects is None:
            objects = pm.selected()
        if not objects:
            cls.logger.warning("No objects selected.")
            return

        if mode == "attribute":
            FadeAttributeMode.remove(objects)
        elif mode == "material":
            FadeMaterialMode.remove(objects)
        else:
            cls.logger.error(f"Unknown mode: {mode}")

    # ---- Unity helper --------------------------------------------------

    @classmethod
    def generate_unity_script(cls, output_path: str) -> str:
        """Write an ``AttributeBinder.cs`` component for the ``"attribute"`` workflow.

        The generated MonoBehaviour reads the ``Unity_Opacity`` value driven
        by the Animator and applies it to the Renderer via
        ``MaterialPropertyBlock``, keeping the shared material intact and
        preserving GPU instancing / batching.

        Parameters:
            output_path: Destination file path (e.g.
                ``Assets/Scripts/AttributeBinder.cs``).

        Returns:
            str: The absolute path written.
        """
        content = """\
using UnityEngine;

/// <summary>
/// Bridges a Maya "fade" custom attribute (exported via FBX) to a
/// per-renderer alpha override using MaterialPropertyBlock.
///
/// Usage:
///   1. In Maya: MaterialFade.setup(objects, mode="attribute")
///   2. Export FBX with "Custom Properties" enabled.
///   3. In Unity: Import Model > Animation > check "Animated Custom Properties".
///   4. Attach this component to the GameObject.  The Animator will drive
///      the "fade" field automatically; this script applies it to the shader.
/// </summary>
[RequireComponent(typeof(Renderer))]
public class AttributeBinder : MonoBehaviour
{
    [Tooltip("Driven by the Animator via the FBX custom property curve.")]
    [Range(0f, 1f)]
    public float fade = 1f;

    [Tooltip("Shader color property to override alpha on (e.g. _BaseColor, _Color).")]
    public string colorProperty = "_BaseColor";

    Renderer  _rend;
    MaterialPropertyBlock _block;
    int _propID;

    void Awake()
    {
        _rend  = GetComponent<Renderer>();
        _block = new MaterialPropertyBlock();
        _propID = Shader.PropertyToID(colorProperty);
    }

    void LateUpdate()
    {
        if (_rend == null || _rend.sharedMaterial == null) return;
        if (!_rend.sharedMaterial.HasProperty(_propID)) return;

        _rend.GetPropertyBlock(_block);
        Color c = _rend.sharedMaterial.GetColor(_propID);
        c.a = fade;
        _block.SetColor(_propID, c);
        _rend.SetPropertyBlock(_block);
    }
}
"""
        os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as fh:
            fh.write(content)
        cls.logger.info(f"Generated {output_path}")
        return os.path.abspath(output_path)

    @classmethod
    def generate_postprocessor_script(cls, output_path: str) -> str:
        """Write a ``FadeCurveFixer.cs`` AssetPostprocessor for the shader-driven workflow.

        This script runs automatically on Unity import. It finds any animation curve
        named "fade" (from Maya's custom attribute) and rebinds it to drive
        ``material._BaseColor.a`` directly. This eliminates the need for
        runtime scripts like ``AttributeBinder``.

        Parameters:
            output_path: Destination file path (e.g. ``Assets/Editor/FadeCurveFixer.cs``).
                         **Note**: Must be in an ``Editor`` folder inside Unity to work.

        Returns:
            str: The absolute path written.
        """
        content = """\
using UnityEngine;
using UnityEditor;

/// <summary>
/// Automatically rewires "fade" custom properties from Maya to drive
/// the material's Alpha channel directly on import.
///
/// Usage:
///   1. In Maya: MaterialFade.setup(objects, mode="attribute")
///   2. Export FBX.
///   3. Place this script in an "Editor" folder in Unity.
///   4. On import, the "fade" curve is moved to "material._BaseColor.a".
/// </summary>
public class FadeCurveFixer : AssetPostprocessor
{
    // The source custom attribute name from Maya
    const string SOURCE_CURVE = "fade";

    // The target shader property (StingrayPBS / URP Lit standard)
    // For Built-in Standard Shader, use "material._Color.a"
    const string TARGET_PROPERTY = "material._BaseColor.a";

    void OnPostprocessModel(GameObject g)
    {
        // Optional: Auto-set strict import settings if needed
    }

    // This method is called for each AnimationClip generated during import
    void OnPostprocessAnimation(GameObject root, AnimationClip clip)
    {
        // 1. Find the custom "fade" curve
        // Note: Custom properties are usually bound to the root Transform of the animated object
        // or the specific bone. We search generally.
        
        var bindings = AnimationUtility.GetCurveBindings(clip);
        bool found = false;

        foreach (var binding in bindings)
        {
            if (binding.propertyName == SOURCE_CURVE)
            {
                // Retrieve the curve data
                AnimationCurve curve = AnimationUtility.GetEditorCurve(clip, binding);
                
                // 2. Remove the old custom property curve
                AnimationUtility.SetEditorCurve(clip, binding, null);

                // 3. Create a new binding targeting the Renderer's material
                // We assume the Animator is on the object with the Renderer, or we target the same path.
                // If the "fade" attr was on a child mesh, binding.path is preserved.
                
                // Target the SkinnedMeshRenderer or MeshRenderer at the same path
                var newBinding = EditorCurveBinding.FloatCurve(
                    binding.path, 
                    typeof(SkinnedMeshRenderer), 
                    TARGET_PROPERTY
                );

                // If it's a static MeshRenderer, try that type if the first fails?
                // Actually, SetEditorCurve doesn't validate existence, it just writes the path.
                // But we should pick the right Component type. 
                // Getting the object at path to check type is hard in PostProcessor (assets only).
                // Safest strategy: Bind to SkinnedMeshRenderer (common for characters) 
                // AND MeshRenderer (common for props). One will be valid at runtime.
                
                AnimationUtility.SetEditorCurve(clip, newBinding, curve);

                var staticBinding = EditorCurveBinding.FloatCurve(
                    binding.path,
                    typeof(MeshRenderer),
                    TARGET_PROPERTY
                );
                AnimationUtility.SetEditorCurve(clip, staticBinding, curve);

                found = true;
            }
        }

        if (found)
        {
            Debug.Log($"[FadeCurveFixer] Rewired 'fade' curve in clip '{clip.name}' to '{TARGET_PROPERTY}'");
        }
    }
}
"""
        os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as fh:
            fh.write(content)
        cls.logger.info(f"Generated {output_path}")
        return os.path.abspath(output_path)
