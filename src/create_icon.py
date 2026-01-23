"""Generate a microphone icon for the application shortcut."""
from PIL import Image, ImageDraw
import os

def create_microphone_image(size: int) -> Image.Image:
    """Create a microphone image at the specified size."""
    img = Image.new('RGBA', (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    cx, cy = size // 2, size // 2

    # Microphone head (rounded rectangle)
    head_width = int(size * 0.35)
    head_height = int(size * 0.45)
    head_top = int(size * 0.08)
    head_left = cx - head_width // 2
    head_right = cx + head_width // 2
    head_bottom = head_top + head_height

    # Mic head color - green to match "ready" state
    mic_color = (76, 175, 80)

    # Draw microphone head (rounded)
    radius = head_width // 2
    draw.rounded_rectangle(
        [head_left, head_top, head_right, head_bottom],
        radius=radius,
        fill=mic_color
    )

    # Microphone grille lines
    grille_color = (56, 142, 60)  # Darker green
    line_spacing = max(3, size // 12)
    line_width = max(1, size // 32)
    for y in range(head_top + radius, head_bottom - radius // 2, line_spacing):
        margin = line_width * 2
        draw.line(
            [(head_left + margin, y), (head_right - margin, y)],
            fill=grille_color,
            width=line_width
        )

    # Stand color
    stand_color = (97, 97, 97)  # Gray
    stand_width = max(2, size // 14)

    # U-shaped stand under the head
    curve_top = head_bottom - int(size * 0.05)
    curve_bottom = int(size * 0.70)
    curve_left = head_left - int(size * 0.08)
    curve_right = head_right + int(size * 0.08)

    draw.arc(
        [curve_left, curve_top, curve_right, curve_bottom + (curve_bottom - curve_top)],
        start=0, end=180,
        fill=stand_color,
        width=stand_width
    )

    # Vertical stem
    stem_top = curve_bottom
    stem_bottom = int(size * 0.85)
    draw.line(
        [(cx, stem_top), (cx, stem_bottom)],
        fill=stand_color,
        width=stand_width
    )

    # Base
    base_width = int(size * 0.35)
    base_height = max(3, size // 14)
    base_top = stem_bottom - base_height // 2
    draw.rounded_rectangle(
        [cx - base_width // 2, base_top, cx + base_width // 2, base_top + base_height],
        radius=base_height // 2,
        fill=stand_color
    )

    return img

def create_microphone_icon(output_path: str):
    """Create a microphone icon with multiple sizes for .ico file."""
    # Windows icon sizes
    sizes = [256, 128, 64, 48, 32, 16]

    # Generate images for each size
    images = [create_microphone_image(s) for s in sizes]

    # Save as ICO - the largest image first, others as append_images
    images[0].save(
        output_path,
        format='ICO',
        append_images=images[1:],
        sizes=[(s, s) for s in sizes]
    )

    # Verify file size
    file_size = os.path.getsize(output_path)
    print(f"Icon created: {output_path} ({file_size:,} bytes)")

if __name__ == "__main__":
    script_dir = os.path.dirname(os.path.abspath(__file__))
    icon_path = os.path.join(script_dir, "..", "voice-dictation.ico")
    create_microphone_icon(icon_path)
