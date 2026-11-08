"""Resize the existing Rahhal logo into opaque PWA icons without cropping.

Run from the repository with its existing Pillow dependency. No new artwork,
text, or upscaling is introduced, and the original logo is never overwritten.
"""
from pathlib import Path

from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "static/images/logo.png"
DESTINATION = ROOT / "static/pwa"
BACKGROUND = (247, 243, 234, 255)
ICONS = (
    ("icon-192.png", 192, 0.88),
    ("icon-512.png", 512, 0.88),
    # All original content fits within the central maskable safe circle:
    # even the corners of a 60%-wide square are inside its 40%-radius circle.
    ("icon-maskable-512.png", 512, 0.60),
    ("apple-touch-icon.png", 180, 0.88),
)


def main():
    DESTINATION.mkdir(parents=True, exist_ok=True)
    with Image.open(SOURCE) as image:
        logo = image.convert("RGBA")
        for filename, size, proportion in ICONS:
            resized = logo.copy()
            bound = int(size * proportion)
            resized.thumbnail((bound, bound), Image.Resampling.LANCZOS)
            icon = Image.new("RGBA", (size, size), BACKGROUND)
            icon.alpha_composite(
                resized,
                ((size - resized.width) // 2, (size - resized.height) // 2),
            )
            path = DESTINATION / filename
            icon.convert("RGB").save(path, format="PNG", optimize=True)
            print(f"Created {filename}: {size}x{size} ({path.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
