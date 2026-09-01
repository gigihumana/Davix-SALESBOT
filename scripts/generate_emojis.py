#!/usr/bin/env python3
"""Generate the complete Davix animated emoji set.

The artwork is intentionally vector-like and rendered at 4x before being
downsampled. That keeps the silhouettes readable at Discord's 32px display
size while retaining a polished 128px source. The output is deterministic so
the shipped assets can be reproduced and reviewed instead of being opaque
binary files.

Development dependency: Pillow >= 10
"""

from __future__ import annotations

import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


ROOT = Path(__file__).resolve().parents[1]
OUTPUT_DIR = ROOT / "assets" / "emojis"
CANVAS = 128
SCALE = 4
SIZE = CANVAS * SCALE
FRAME_COUNT = 12
FRAME_DURATION_MS = 90
MAX_EMOJI_BYTES = 256 * 1024

EXPECTED_NAMES = {
    "dvcard",
    "dvcart",
    "dvcheck",
    "dvclock",
    "dvcross",
    "dvcrypto",
    "dvgift",
    "dvload",
    "dvmoney",
    "dvpix",
    "dvshield",
    "dvstar",
    "dvwarn",
}

WHITE = (255, 255, 255, 255)
INK = (18, 25, 48, 255)


def s(value: float) -> int:
    return round(value * SCALE)


def xy(points: list[tuple[float, float]]) -> list[tuple[int, int]]:
    return [(s(x), s(y)) for x, y in points]


def wave(progress: float, phase: float = 0.0) -> float:
    return math.sin(2 * math.pi * (progress + phase))


def lerp_color(a: tuple[int, int, int], b: tuple[int, int, int], t: float) -> tuple[int, int, int, int]:
    return tuple(round(a[i] + (b[i] - a[i]) * t) for i in range(3)) + (255,)


def rounded_line(
    draw: ImageDraw.ImageDraw,
    points: list[tuple[float, float]],
    fill: tuple[int, int, int, int],
    width: float,
) -> None:
    scaled = xy(points)
    line_width = s(width)
    draw.line(scaled, fill=fill, width=line_width, joint="curve")
    radius = line_width // 2
    for px, py in (scaled[0], scaled[-1]):
        draw.ellipse((px - radius, py - radius, px + radius, py + radius), fill=fill)


def font_pixels(size: int) -> ImageFont.FreeTypeFont:
    candidates = (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf",
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    )
    for candidate in candidates:
        if Path(candidate).exists():
            return ImageFont.truetype(candidate, size)
    raise RuntimeError("A bold TrueType font is required to generate the emojis")


def font(size: int) -> ImageFont.FreeTypeFont:
    return font_pixels(s(size))


def gradient_badge(
    top: tuple[int, int, int],
    bottom: tuple[int, int, int],
    progress: float,
    *,
    pulse: float = 0.0,
) -> Image.Image:
    image = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    breathing = pulse * wave(progress)
    inset = 8 - breathing
    bounds = (s(inset), s(inset), s(CANVAS - inset), s(CANVAS - inset))

    mask = Image.new("L", image.size, 0)
    ImageDraw.Draw(mask).ellipse(bounds, fill=255)
    gradient = Image.new("RGBA", image.size)
    gradient_draw = ImageDraw.Draw(gradient)
    for y in range(bounds[1], bounds[3] + 1):
        amount = (y - bounds[1]) / max(1, bounds[3] - bounds[1])
        gradient_draw.line((bounds[0], y, bounds[2], y), fill=lerp_color(top, bottom, amount))
    image.alpha_composite(Image.composite(gradient, Image.new("RGBA", image.size), mask))

    accents = Image.new("RGBA", image.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(accents)
    draw.ellipse(bounds, outline=(255, 255, 255, 70), width=s(2))
    draw.arc(bounds, 205, 310, fill=(255, 255, 255, 115), width=s(3))
    draw.arc(bounds, 20, 105, fill=(9, 16, 42, 65), width=s(2))
    draw.ellipse((s(27), s(22), s(45), s(29)), fill=(255, 255, 255, 35))
    image.alpha_composite(accents)
    return image


def transform_layer(
    layer: Image.Image,
    *,
    scale_x: float = 1.0,
    scale_y: float = 1.0,
    angle: float = 0.0,
    dx: float = 0.0,
    dy: float = 0.0,
) -> Image.Image:
    width = max(1, round(SIZE * scale_x))
    height = max(1, round(SIZE * scale_y))
    transformed = layer.resize((width, height), Image.Resampling.BICUBIC)
    centered = Image.new("RGBA", layer.size, (0, 0, 0, 0))
    centered.alpha_composite(transformed, ((SIZE - width) // 2, (SIZE - height) // 2))
    if angle:
        centered = centered.rotate(angle, Image.Resampling.BICUBIC, center=(SIZE // 2, SIZE // 2))
    if dx or dy:
        shifted = Image.new("RGBA", layer.size, (0, 0, 0, 0))
        shifted.alpha_composite(centered, (s(dx), s(dy)))
        centered = shifted
    return centered


def sparkle(draw: ImageDraw.ImageDraw, x: float, y: float, radius: float, alpha: int = 255) -> None:
    color = (255, 255, 255, alpha)
    rounded_line(draw, [(x - radius, y), (x + radius, y)], color, max(1.2, radius * 0.34))
    rounded_line(draw, [(x, y - radius), (x, y + radius)], color, max(1.2, radius * 0.34))


def card_frame(progress: float) -> Image.Image:
    base = gradient_badge((100, 93, 255), (44, 88, 224), progress, pulse=0.7)
    layer = Image.new("RGBA", base.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    draw.rounded_rectangle((s(28), s(39), s(100), s(88)), radius=s(9), fill=WHITE)
    draw.rounded_rectangle((s(33), s(44), s(95), s(83)), radius=s(6), fill=(239, 245, 255, 255))
    draw.rounded_rectangle((s(33), s(52), s(95), s(62)), radius=s(2), fill=(45, 64, 139, 255))
    draw.rounded_rectangle((s(39), s(68), s(53), s(78)), radius=s(2), fill=(255, 195, 66, 255))
    rounded_line(draw, [(61, 72), (83, 72)], (80, 105, 188, 255), 3)
    rounded_line(draw, [(61, 78), (75, 78)], (133, 153, 213, 255), 2.5)
    shimmer_x = 27 + 78 * progress
    rounded_line(draw, [(shimmer_x - 5, 42), (shimmer_x + 5, 85)], (255, 255, 255, 105), 4)
    base.alpha_composite(transform_layer(layer, angle=2.0 * wave(progress), dy=1.2 * wave(progress, 0.25)))
    return base


def cart_frame(progress: float) -> Image.Image:
    base = gradient_badge((52, 211, 235), (47, 92, 222), progress, pulse=0.5)
    layer = Image.new("RGBA", base.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    rounded_line(draw, [(26, 38), (36, 38), (44, 75), (91, 75)], WHITE, 6)
    draw.rounded_rectangle((s(39), s(46), s(95), s(71)), radius=s(5), outline=WHITE, width=s(5))
    for x, color, delay in ((52, (255, 209, 79, 255), 0.0), (68, (255, 255, 255, 255), 0.15), (83, (139, 255, 225, 255), 0.3)):
        lift = max(0.0, wave(progress, delay)) * 2.5
        draw.rounded_rectangle((s(x - 5), s(52 - lift), s(x + 5), s(65 - lift)), radius=s(2), fill=color)
    for x in (52, 84):
        draw.ellipse((s(x - 7), s(79), s(x + 7), s(93)), fill=WHITE)
        draw.ellipse((s(x - 2.5), s(83.5), s(x + 2.5), s(88.5)), fill=(44, 79, 180, 255))
    sparkle(draw, 99, 35, 4.5, round(130 + 125 * max(0, wave(progress))))
    base.alpha_composite(transform_layer(layer, dy=1.5 * wave(progress)))
    return base


def check_frame(progress: float) -> Image.Image:
    base = gradient_badge((85, 239, 168), (18, 174, 115), progress, pulse=1.0)
    layer = Image.new("RGBA", base.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    draw.ellipse((s(30), s(30), s(98), s(98)), fill=(255, 255, 255, 24), outline=(255, 255, 255, 80), width=s(2))
    rounded_line(draw, [(38, 65), (56, 82), (92, 43)], WHITE, 10)
    shine = (progress * 1.5) % 1.0
    sparkle(draw, 40 + 53 * shine, 38 + 38 * (1 - shine), 3.7, round(80 + 175 * abs(wave(progress))))
    base.alpha_composite(transform_layer(layer, scale_x=1 + 0.025 * wave(progress), scale_y=1 + 0.025 * wave(progress)))
    return base


def clock_frame(progress: float) -> Image.Image:
    base = gradient_badge((255, 204, 72), (246, 142, 35), progress, pulse=0.4)
    layer = Image.new("RGBA", base.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    draw.ellipse((s(31), s(31), s(97), s(97)), fill=(255, 255, 255, 235), outline=INK, width=s(4))
    for index in range(12):
        angle = 2 * math.pi * index / 12
        inner = 27 if index % 3 == 0 else 29
        outer = 31
        rounded_line(
            draw,
            [(64 + inner * math.sin(angle), 64 - inner * math.cos(angle)), (64 + outer * math.sin(angle), 64 - outer * math.cos(angle))],
            INK,
            2.0 if index % 3 == 0 else 1.2,
        )
    minute_angle = 2 * math.pi * progress
    hour_angle = minute_angle / 4 - math.pi / 4
    rounded_line(draw, [(64, 64), (64 + 23 * math.sin(minute_angle), 64 - 23 * math.cos(minute_angle))], INK, 4)
    rounded_line(draw, [(64, 64), (64 + 15 * math.sin(hour_angle), 64 - 15 * math.cos(hour_angle))], INK, 5)
    draw.ellipse((s(59), s(59), s(69), s(69)), fill=(242, 123, 40, 255), outline=WHITE, width=s(2))
    base.alpha_composite(layer)
    return base


def cross_frame(progress: float) -> Image.Image:
    base = gradient_badge((255, 100, 116), (211, 47, 78), progress, pulse=0.8)
    layer = Image.new("RGBA", base.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    draw.ellipse((s(31), s(31), s(97), s(97)), fill=(255, 255, 255, 25), outline=(255, 255, 255, 75), width=s(2))
    rounded_line(draw, [(43, 43), (85, 85)], WHITE, 11)
    rounded_line(draw, [(85, 43), (43, 85)], WHITE, 11)
    base.alpha_composite(transform_layer(layer, angle=3.0 * wave(progress)))
    return base


def crypto_frame(progress: float) -> Image.Image:
    base = gradient_badge((149, 98, 255), (82, 56, 202), progress, pulse=0.5)
    coin = Image.new("RGBA", base.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(coin)
    draw.ellipse((s(33), s(30), s(95), s(98)), fill=(255, 199, 58, 255), outline=(255, 241, 172, 255), width=s(4))
    draw.ellipse((s(39), s(36), s(89), s(92)), outline=(191, 115, 22, 210), width=s(2))
    label_font = font(43)
    bbox = draw.textbbox((0, 0), "B", font=label_font)
    text_x = s(64) - (bbox[2] - bbox[0]) // 2
    text_y = s(61) - (bbox[3] - bbox[1]) // 2 - bbox[1]
    draw.text((text_x, text_y), "B", font=label_font, fill=(106, 67, 176, 255))
    rounded_line(draw, [(56, 39), (56, 88)], (106, 67, 176, 255), 2.2)
    rounded_line(draw, [(63, 38), (63, 89)], (106, 67, 176, 255), 2.2)
    flip = 0.58 + 0.42 * abs(math.cos(2 * math.pi * progress))
    base.alpha_composite(transform_layer(coin, scale_x=flip, dy=1.2 * wave(progress)))
    overlay = Image.new("RGBA", base.size, (0, 0, 0, 0))
    sparkle(ImageDraw.Draw(overlay), 97, 34, 4.5, round(90 + 165 * max(0, wave(progress))))
    base.alpha_composite(overlay)
    return base


def gift_frame(progress: float) -> Image.Image:
    base = gradient_badge((255, 94, 181), (184, 47, 173), progress, pulse=0.7)
    layer = Image.new("RGBA", base.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    draw.rounded_rectangle((s(34), s(58), s(94), s(94)), radius=s(6), fill=WHITE)
    draw.rounded_rectangle((s(29), s(50), s(99), s(65)), radius=s(5), fill=(255, 238, 250, 255))
    draw.rectangle((s(58), s(50), s(70), s(94)), fill=(255, 105, 177, 255))
    bow_lift = 2.5 * max(0.0, wave(progress))
    draw.ellipse((s(39), s(34 - bow_lift), s(64), s(55 - bow_lift)), fill=(255, 225, 245, 255), outline=WHITE, width=s(3))
    draw.ellipse((s(64), s(34 - bow_lift), s(89), s(55 - bow_lift)), fill=(255, 225, 245, 255), outline=WHITE, width=s(3))
    draw.ellipse((s(58), s(43 - bow_lift), s(70), s(56 - bow_lift)), fill=(255, 105, 177, 255))
    sparkle(draw, 100, 40, 4, round(95 + 160 * max(0, wave(progress, 0.2))))
    base.alpha_composite(transform_layer(layer, dy=1.3 * wave(progress, 0.5)))
    return base


def load_frame(progress: float) -> Image.Image:
    base = gradient_badge((41, 209, 199), (41, 89, 190), progress, pulse=0.3)
    layer = Image.new("RGBA", base.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    segment_count = FRAME_COUNT
    active = round(progress * segment_count) % segment_count
    for index in range(segment_count):
        angle = 2 * math.pi * index / segment_count
        distance = (index - active) % segment_count
        alpha = max(55, 255 - distance * 24)
        inner, outer = 30, 42
        rounded_line(
            draw,
            [(64 + inner * math.sin(angle), 64 - inner * math.cos(angle)), (64 + outer * math.sin(angle), 64 - outer * math.cos(angle))],
            (255, 255, 255, alpha),
            6,
        )
    draw.ellipse((s(48), s(48), s(80), s(80)), fill=(255, 255, 255, 28), outline=(255, 255, 255, 70), width=s(2))
    base.alpha_composite(layer)
    return base


def money_frame(progress: float) -> Image.Image:
    base = gradient_badge((73, 231, 150), (12, 151, 113), progress, pulse=0.6)
    layer = Image.new("RGBA", base.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    draw.rounded_rectangle((s(29), s(45), s(96), s(87)), radius=s(6), fill=(217, 255, 231, 255), outline=WHITE, width=s(3))
    draw.ellipse((s(51), s(50), s(78), s(82)), fill=(255, 255, 255, 150))
    draw.ellipse((s(35), s(51), s(42), s(58)), fill=(42, 173, 111, 255))
    draw.ellipse((s(83), s(74), s(90), s(81)), fill=(42, 173, 111, 255))
    label_font = font(30)
    bbox = draw.textbbox((0, 0), "$", font=label_font)
    draw.text((s(64) - (bbox[2] - bbox[0]) // 2, s(64) - (bbox[3] - bbox[1]) // 2 - bbox[1]), "$", font=label_font, fill=(15, 110, 83, 255))
    shine_x = 31 + 62 * progress
    rounded_line(draw, [(shine_x - 4, 48), (shine_x + 4, 84)], (255, 255, 255, 120), 3)
    base.alpha_composite(transform_layer(layer, angle=1.8 * wave(progress), dy=1.1 * wave(progress)))
    return base


def pix_frame(progress: float) -> Image.Image:
    base = gradient_badge((49, 220, 187), (20, 142, 139), progress, pulse=0.9)
    layer = Image.new("RGBA", base.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    paths = (
        [(43, 53), (56, 40), (64, 40), (78, 54)],
        [(75, 43), (88, 56), (88, 65), (75, 78)],
        [(85, 75), (72, 88), (63, 88), (50, 75)],
        [(53, 85), (40, 72), (40, 63), (53, 50)],
    )
    for index, path in enumerate(paths):
        glow = 210 + round(45 * max(0, wave(progress, index / 4)))
        rounded_line(draw, path, (255, 255, 255, glow), 7)
    draw.ellipse((s(59), s(59), s(69), s(69)), fill=(255, 255, 255, 255))
    base.alpha_composite(transform_layer(layer, scale_x=1 + 0.02 * wave(progress), scale_y=1 + 0.02 * wave(progress)))
    return base


def shield_frame(progress: float) -> Image.Image:
    base = gradient_badge((91, 111, 255), (47, 61, 185), progress, pulse=0.5)
    layer = Image.new("RGBA", base.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    shield = xy([(64, 27), (94, 39), (91, 69), (80, 88), (64, 100), (48, 88), (37, 69), (34, 39)])
    draw.polygon(shield, fill=(238, 247, 255, 255), outline=WHITE)
    inner = xy([(64, 36), (85, 44), (82, 67), (74, 81), (64, 89), (54, 81), (46, 67), (43, 44)])
    draw.polygon(inner, fill=(55, 73, 191, 255))
    draw.rounded_rectangle((s(51), s(57), s(77), s(77)), radius=s(4), fill=WHITE)
    draw.arc((s(54), s(43), s(74), s(65)), 180, 360, fill=WHITE, width=s(5))
    draw.ellipse((s(61), s(63), s(67), s(69)), fill=(55, 73, 191, 255))
    rounded_line(draw, [(64, 68), (64, 73)], (55, 73, 191, 255), 3)
    scan_y = 42 + 42 * progress
    rounded_line(draw, [(46, scan_y), (82, scan_y)], (101, 244, 244, 135), 2.5)
    base.alpha_composite(layer)
    return base


def star_points(cx: float, cy: float, outer: float, inner: float, rotation: float = -math.pi / 2) -> list[tuple[float, float]]:
    points = []
    for index in range(10):
        radius = outer if index % 2 == 0 else inner
        angle = rotation + index * math.pi / 5
        points.append((cx + radius * math.cos(angle), cy + radius * math.sin(angle)))
    return points


def star_frame(progress: float) -> Image.Image:
    base = gradient_badge((255, 213, 75), (241, 132, 28), progress, pulse=0.8)
    layer = Image.new("RGBA", base.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    draw.polygon(xy(star_points(64, 64, 33, 15)), fill=WHITE)
    sparkle(draw, 96, 36, 4.5, round(80 + 175 * max(0, wave(progress))))
    sparkle(draw, 33, 86, 3.2, round(80 + 175 * max(0, wave(progress, 0.5))))
    base.alpha_composite(transform_layer(layer, angle=7 * wave(progress), scale_x=1 + 0.025 * wave(progress), scale_y=1 + 0.025 * wave(progress)))
    return base


def warn_frame(progress: float) -> Image.Image:
    base = gradient_badge((255, 208, 65), (239, 121, 31), progress, pulse=1.0)
    layer = Image.new("RGBA", base.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    triangle = xy([(64, 28), (101, 94), (27, 94)])
    draw.polygon(triangle, fill=WHITE)
    inner = xy([(64, 39), (91, 87), (37, 87)])
    draw.polygon(inner, fill=(255, 199, 49, 255))
    exclamation_scale = 1 + 0.06 * wave(progress)
    rounded_line(draw, [(64, 52), (64, 72)], INK, 7 * exclamation_scale)
    draw.ellipse((s(59.5), s(78), s(68.5), s(87)), fill=INK)
    base.alpha_composite(transform_layer(layer, dy=1.2 * wave(progress)))
    return base


GENERATORS = {
    "dvcard": card_frame,
    "dvcart": cart_frame,
    "dvcheck": check_frame,
    "dvclock": clock_frame,
    "dvcross": cross_frame,
    "dvcrypto": crypto_frame,
    "dvgift": gift_frame,
    "dvload": load_frame,
    "dvmoney": money_frame,
    "dvpix": pix_frame,
    "dvshield": shield_frame,
    "dvstar": star_frame,
    "dvwarn": warn_frame,
}


def gif_frame(image: Image.Image) -> Image.Image:
    rgba = image.resize((CANVAS, CANVAS), Image.Resampling.LANCZOS)
    alpha = rgba.getchannel("A")
    rgb = Image.new("RGB", rgba.size, (0, 0, 0))
    rgb.paste(rgba, mask=alpha)
    quantized = rgb.quantize(colors=255, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.FLOYDSTEINBERG)
    shifted = quantized.point(lambda value: value + 1)
    palette = [0, 0, 0] + (quantized.getpalette() or [])[:765]
    palette.extend([0] * (768 - len(palette)))
    shifted.putpalette(palette)
    transparent = alpha.point(lambda value: 255 if value < 24 else 0)
    shifted.paste(0, mask=transparent)
    shifted.info["transparency"] = 0
    return shifted


def save_gif(name: str, generator) -> Path:
    frames = [gif_frame(generator(index / FRAME_COUNT)) for index in range(FRAME_COUNT)]
    destination = OUTPUT_DIR / f"{name}.gif"
    frames[0].save(
        destination,
        save_all=True,
        append_images=frames[1:],
        duration=FRAME_DURATION_MS,
        loop=0,
        disposal=2,
        transparency=0,
        optimize=True,
    )
    return destination


def validate(path: Path) -> None:
    if path.stat().st_size >= MAX_EMOJI_BYTES:
        raise ValueError(f"{path.name} exceeds Discord's 256KB limit ({path.stat().st_size} bytes)")
    with Image.open(path) as image:
        if image.size != (CANVAS, CANVAS):
            raise ValueError(f"{path.name} is {image.size}, expected 128x128")
        if image.n_frames != FRAME_COUNT:
            raise ValueError(f"{path.name} has {image.n_frames} frames, expected {FRAME_COUNT}")
        if image.info.get("loop") != 0:
            raise ValueError(f"{path.name} does not loop forever")
        signatures = set()
        for frame_index in range(image.n_frames):
            image.seek(frame_index)
            signatures.add(image.convert("RGBA").tobytes())
            if image.info.get("duration") != FRAME_DURATION_MS:
                raise ValueError(f"{path.name} frame {frame_index} has an invalid duration")
        if len(signatures) < FRAME_COUNT // 2:
            raise ValueError(f"{path.name} animation contains too many duplicate frames")


def save_preview(paths: list[Path]) -> Path:
    columns = 4
    tile_width, tile_height = 150, 145
    rows = math.ceil(len(paths) / columns)
    preview = Image.new("RGBA", (columns * tile_width, rows * tile_height), (36, 39, 46, 255))
    draw = ImageDraw.Draw(preview)
    label_font = font_pixels(14)
    for index, path in enumerate(paths):
        column, row = index % columns, index // columns
        left, top = column * tile_width, row * tile_height
        draw.rounded_rectangle(
            (left + 7, top + 7, left + tile_width - 7, top + tile_height - 7),
            radius=16,
            fill=(49, 51, 56, 255),
            outline=(73, 76, 86, 255),
            width=1,
        )
        with Image.open(path) as emoji:
            emoji.seek(emoji.n_frames // 2)
            frame = emoji.convert("RGBA").resize((96, 96), Image.Resampling.LANCZOS)
        preview.alpha_composite(frame, (left + 27, top + 13))
        label = path.stem
        bounds = draw.textbbox((0, 0), label, font=label_font)
        label_x = left + (tile_width - (bounds[2] - bounds[0])) // 2
        draw.text((label_x, top + 113), label, font=label_font, fill=(238, 239, 242, 255))

    destination = ROOT / "assets" / "emoji-preview.png"
    preview.convert("RGB").save(destination, optimize=True)
    return destination


def main() -> None:
    if set(GENERATORS) != EXPECTED_NAMES:
        raise RuntimeError("Generator registry does not match the required emoji manifest")
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    generated = []
    for name, generator in GENERATORS.items():
        destination = save_gif(name, generator)
        validate(destination)
        generated.append(destination)
        print(f"{destination.relative_to(ROOT)}  {destination.stat().st_size / 1024:.1f}KB")

    preview = save_preview(generated)
    print(f"{preview.relative_to(ROOT)}  {preview.stat().st_size / 1024:.1f}KB")

    disk_names = {path.stem for path in OUTPUT_DIR.glob("*.gif")}
    if disk_names != EXPECTED_NAMES:
        raise RuntimeError(f"Emoji directory mismatch: expected {sorted(EXPECTED_NAMES)}, found {sorted(disk_names)}")
    print(f"Generated and validated {len(generated)} animated emojis.")


if __name__ == "__main__":
    main()
