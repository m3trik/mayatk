"""Create test images for stingray shader tests."""

import os
from PIL import Image, ImageDraw

# Create test_assets directory if it doesn't exist
script_dir = os.path.dirname(os.path.abspath(__file__))
os.makedirs(script_dir, exist_ok=True)

# Image dimensions
WIDTH, HEIGHT = 512, 512


def create_solid_image(filename, color, alpha=255):
    """Create a solid color image."""
    img = Image.new("RGBA", (WIDTH, HEIGHT), color + (alpha,))
    img.save(os.path.join(script_dir, filename))
    print(f"Created: {filename}")


def create_gradient_image(filename):
    """Create a gradient image for metallic/roughness."""
    img = Image.new("L", (WIDTH, HEIGHT))
    draw = ImageDraw.Draw(img)
    for y in range(HEIGHT):
        value = int((y / HEIGHT) * 255)
        draw.line([(0, y), (WIDTH, y)], fill=value)
    img.save(os.path.join(script_dir, filename))
    print(f"Created: {filename}")


def create_pattern_image(filename):
    """Create a checkered pattern for testing."""
    img = Image.new("RGB", (WIDTH, HEIGHT), "white")
    draw = ImageDraw.Draw(img)
    square_size = 64
    for y in range(0, HEIGHT, square_size):
        for x in range(0, WIDTH, square_size):
            if (x // square_size + y // square_size) % 2:
                draw.rectangle([x, y, x + square_size, y + square_size], fill="gray")
    img.save(os.path.join(script_dir, filename))
    print(f"Created: {filename}")


# Create test images
print("Creating test images...")

# Base color maps
create_solid_image("model_BaseColor.png", (128, 128, 128))
create_solid_image("model_Albedo.png", (128, 128, 128))
create_pattern_image("model_BaseColor.jpg")
create_pattern_image("model_BaseColor.tga")

# Opacity/transparency maps
create_solid_image("model_Opacity.png", (255, 255, 255))
create_solid_image("model_Albedo_Transparency.png", (128, 128, 128), alpha=200)

# Metallic/roughness/smoothness maps
create_gradient_image("model_Metallic.png")
create_gradient_image("model_Roughness.png")
create_gradient_image("model_Smoothness.png")
create_gradient_image("model_Metallic.jpg")
create_gradient_image("model_Roughness.jpg")
create_gradient_image("model_Smoothness.jpg")
create_gradient_image("model_Metallic.tga")
create_gradient_image("model_Roughness.tga")
create_gradient_image("model_Smoothness.tga")

# Normal maps
create_solid_image("model_Normal_OpenGL.png", (128, 128, 255))  # Blue-ish for normal

# AO map
create_solid_image("model_AO.png", (200, 200, 200))

# Composite maps
# AlbedoTransparency (with alpha)
create_solid_image("model_AlbedoTransparency.png", (128, 128, 128), alpha=200)

# MetallicSmoothness (grayscale with alpha for smoothness)
img_ms = Image.new("LA", (WIDTH, HEIGHT))
for y in range(HEIGHT):
    value = int((y / HEIGHT) * 255)
    for x in range(WIDTH):
        img_ms.putpixel((x, y), (value, 255 - value))
img_ms.save(os.path.join(script_dir, "model_MetallicSmoothness.png"))
print("Created: model_MetallicSmoothness.png")

# MaskMap (MSAO - Metallic, Smoothness, AO, Detail)
img_mask = Image.new("RGBA", (WIDTH, HEIGHT))
for y in range(HEIGHT):
    metallic = int((y / HEIGHT) * 255)
    smoothness = 255 - metallic
    ao = 200
    detail = 128
    for x in range(WIDTH):
        img_mask.putpixel((x, y), (metallic, smoothness, ao, detail))
img_mask.save(os.path.join(script_dir, "model_MaskMap.png"))
print("Created: model_MaskMap.png")

# Wood textures for additional tests
print("\nCreating wood texture variants...")
create_solid_image("wood_BaseColor.png", (139, 90, 43))  # Wood brown color
create_gradient_image("wood_Metallic.png")
create_gradient_image("wood_Roughness.png")
create_solid_image("wood_Normal_OpenGL.png", (128, 128, 255))
create_solid_image("wood_AO.png", (200, 200, 200))

print(f"\nCreated {len(os.listdir(script_dir)) - 1} test images in {script_dir}")
