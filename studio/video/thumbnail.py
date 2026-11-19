"""Create a local branded YouTube thumbnail from a licensed source image."""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


THUMBNAIL_SIZE = (1280, 720)


def _font(size: int) -> ImageFont.FreeTypeFont:
    for candidate in ("C:/Windows/Fonts/arialbd.ttf", "C:/Windows/Fonts/Impact.ttf"):
        if Path(candidate).exists():
            return ImageFont.truetype(candidate, size)
    return ImageFont.load_default()


def _first_image(image_map: dict) -> str | None:
    for value in image_map.values():
        candidates = value if isinstance(value, list) else [value]
        for candidate in candidates:
            if candidate and Path(candidate).is_file():
                return candidate
    return None


def create_thumbnail(title: str, image_map: dict, output_path: str) -> str:
    """Write a readable 1280x720 thumbnail and return its path."""
    source = _first_image(image_map)
    canvas = Image.new("RGB", THUMBNAIL_SIZE, (18, 24, 40))
    if source:
        image = Image.open(source).convert("RGB")
        scale = max(THUMBNAIL_SIZE[0] / image.width, THUMBNAIL_SIZE[1] / image.height)
        image = image.resize((int(image.width * scale), int(image.height * scale)), Image.LANCZOS)
        left = (image.width - THUMBNAIL_SIZE[0]) // 2
        top = (image.height - THUMBNAIL_SIZE[1]) // 2
        canvas.paste(image.crop((left, top, left + THUMBNAIL_SIZE[0], top + THUMBNAIL_SIZE[1])))

    overlay = Image.new("RGBA", THUMBNAIL_SIZE, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    draw.rectangle((0, 0, THUMBNAIL_SIZE[0], THUMBNAIL_SIZE[1]), fill=(0, 0, 0, 110))
    font = _font(72)
    words = title.upper().split()
    lines: list[str] = []
    line = ""
    for word in words:
        proposed = f"{line} {word}".strip()
        if draw.textbbox((0, 0), proposed, font=font)[2] > 1060 and line:
            lines.append(line)
            line = word
        else:
            line = proposed
    if line:
        lines.append(line)
    lines = lines[:3]

    text_height = sum(draw.textbbox((0, 0), line, font=font)[3] for line in lines) + 24 * (len(lines) - 1)
    y = (THUMBNAIL_SIZE[1] - text_height) // 2
    for line in lines:
        bbox = draw.textbbox((0, 0), line, font=font, stroke_width=3)
        x = (THUMBNAIL_SIZE[0] - (bbox[2] - bbox[0])) // 2
        draw.text((x, y), line, font=font, fill="white", stroke_width=3, stroke_fill="black")
        y += (bbox[3] - bbox[1]) + 24

    canvas = Image.alpha_composite(canvas.convert("RGBA"), overlay).convert("RGB")
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    canvas.save(output_path, quality=92)
    return output_path