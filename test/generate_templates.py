import os
import sys
import shutil

try:
    from PySide2 import QtWidgets

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
except:
    pass
import pymel.core as pm
import mayatk as mtk
from mayatk.mat_utils.stingray_arnold_shader import StingrayArnoldShader
from mayatk.mat_utils.shader_templates._shader_templates import ShaderTemplates


def generate_templates():
    # Setup logging
    log_file = r"o:\Cloud\Code\_scripts\mayatk\test\generation_log.txt"

    def log(msg):
        with open(log_file, "a") as f:
            f.write(str(msg) + "\n")

    log("Starting generation...")
    log("DEBUG: Version with lambda *args fix")

    # Setup paths
    test_img_dir = r"o:\Cloud\Code\_scripts\pythontk\test\test_assets\imgtk_test"
    template_dir = (
        r"o:\Cloud\Code\_scripts\mayatk\mayatk\mat_utils\shader_templates\templates"
    )

    if not os.path.exists(template_dir):
        os.makedirs(template_dir)

    # Clear existing templates
    print("Clearing existing templates...")
    for f in os.listdir(template_dir):
        if f.endswith(".yaml"):
            os.remove(os.path.join(template_dir, f))

    # Define templates (matching StingrayArnoldShader.TEMPLATE_CONFIGS)
    # Note: We disable packing (orm_map, mask_map, etc.) to ensure templates have individual slots
    # for all maps. This makes them compatible with individual texture inputs.
    templates = [
        ("PBR Metallic Roughness", (False, False, False, False, False)),
        ("Unity URP Lit", (True, True, False, False, False)),
        ("Unity HDRP Lit", (False, False, True, False, False)),
        ("Unreal Engine", (True, False, False, True, False)),
        ("glTF 2.0", (False, False, False, True, False)),
        ("Godot", (False, False, False, False, False)),
        ("PBR Specular Glossiness", (False, True, False, False, True)),
    ]

    # Define textures to use
    # We need to map the test images to what StingrayArnoldShader expects
    # StingrayArnoldShader uses TextureMapFactory which identifies maps by name/suffix
    # The test images are named like 'im_Base_color.png', 'im_Normal_OpenGL.png' etc.

    # We'll create a temporary set of textures with standard names to ensure detection
    temp_tex_dir = os.path.join(os.path.dirname(__file__), "temp_textures")
    if not os.path.exists(temp_tex_dir):
        os.makedirs(temp_tex_dir)

    # Map test images to standard names
    texture_mapping = {
        "im_Base_color.png": "Test_Base_Color.png",
        "im_Normal_OpenGL.png": "Test_Normal.png",
        "im_Metallic.png": "Test_Metallic.png",
        "im_Roughness.png": "Test_Roughness.png",
        "im_Mixed_AO.png": "Test_AmbientOcclusion.png",
        "im_Emissive.png": "Test_Emissive.png",
        "im_Height.png": "Test_Height.png",
        # We might need Opacity if we want to test transparency packing
        # "im_Opacity.png": "Test_Opacity.png" # If available
    }

    textures = []
    for src, dst in texture_mapping.items():
        src_path = os.path.join(test_img_dir, src)
        dst_path = os.path.join(temp_tex_dir, dst)
        if os.path.exists(src_path):
            shutil.copy2(src_path, dst_path)
            textures.append(dst_path)
        else:
            print(f"Warning: Source image not found: {src_path}")

    sas = StingrayArnoldShader()

    for name, config in templates:
        log(f"Generating template: {name}")

        # Unpack config
        (
            albedo_transparency,
            metallic_smoothness,
            mask_map,
            orm_map,
            convert_specgloss,
        ) = config

        try:
            log(f"  Calling create_network for {name}...")
            # Create network
            # We use a unique name to avoid clashes
            shader_name = f"Template_{name.replace(' ', '_')}"

            # Note: StingrayArnoldShader might fail if it can't pack textures (e.g. missing alpha for albedo_transparency)
            # But for templates, we just want the graph structure.
            # However, if packing fails, the graph might be incomplete or different.
            # We should ensure we have enough textures for the packing to "succeed" or at least attempt it.
            # TextureMapFactory.prepare_maps will try to pack.

            shader_node = sas.create_network(
                textures=textures,
                name=shader_name,
                shader_type="stingray",
                create_arnold=False,  # We only want the Stingray/Standard graph for now? Or both?
                # Templates usually store the Maya shader graph.
                # If we want Arnold too, we should set True.
                # But StingrayArnoldShader creates a Stingray shader connected to an Arnold shader?
                # Let's stick to default (False) for now unless requested otherwise.
                albedo_transparency=albedo_transparency,
                metallic_smoothness=metallic_smoothness,
                mask_map=mask_map,
                orm_map=orm_map,
                convert_specgloss_to_pbr=convert_specgloss,
                cleanup_base_color=False,
                callback=lambda *args: None,  # Suppress output
            )

            if shader_node:
                # Collect all nodes to save
                # listHistory returns the node itself and upstream nodes
                history = pm.listHistory(shader_node)

                # Also check for Shading Group
                sgs = pm.listConnections(shader_node, type="shadingEngine")
                nodes_to_save = history + sgs

                # Save template
                file_path = os.path.join(template_dir, f"{name}.yaml")
                # Exclude Arnold nodes explicitly
                ShaderTemplates.save_template(
                    nodes_to_save,
                    file_path,
                    exclude_types=[
                        "aiStandardSurface",
                        "aiImage",
                        "aiNormalMap",
                        "aiSkyDomeLight",
                    ],
                )
                print(f"  Saved to {file_path}")

                # Cleanup
                pm.delete(nodes_to_save)
            else:
                print(f"  Failed to create network for {name}")

        except Exception as e:
            log(f"  Error generating {name}: {e}")
            import traceback

            log(traceback.format_exc())

    # Cleanup temp textures
    try:
        shutil.rmtree(temp_tex_dir)
    except:
        pass

    log("Template generation complete.")


if __name__ == "__main__":
    try:
        generate_templates()
    except Exception as e:
        print(f"Fatal error: {e}")
        import traceback

        traceback.print_exc()
