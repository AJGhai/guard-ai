"""Tech-pack style annotations drawn onto a product photo (no image generation).

annotate(jpeg, items, color, labels, title) returns a new JPEG: the photo on the left with each
item's region shaded and outlined, a numbered badge, and a leader line to a callout box on a
side panel with the item's title (and a short detail line). Regions are fixed bands (upper,
waist, lower_body, hem, whole), so the marked areas are approximate by design.
"""

from __future__ import annotations

import io
import textwrap
from typing import Any

from PIL import Image, ImageDraw, ImageFont

# Vertical band of the photo for each region, as fractions of height: (top, bottom).
REGION_BANDS = {
    "upper": (0.06, 0.32),
    "waist": (0.32, 0.46),
    "lower_body": (0.46, 0.82),
    "hem": (0.82, 0.97),
    "whole": (0.03, 0.97),
}
PANEL_RATIO = 0.62      # side panel width as a fraction of the photo width
HEIGHT = 900            # output height in pixels


def _font(size: int) -> ImageFont.ImageFont:
    try:
        return ImageFont.load_default(size=size)
    except TypeError:  # very old Pillow
        return ImageFont.load_default()


def _rgb(hex_color: str) -> tuple[int, int, int]:
    h = hex_color.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def annotate(
    jpeg: bytes,
    items: list[dict[str, Any]],
    color: str = "#00d395",
    labels: list[str] | None = None,
    title: str = "",
    detail_key: str = "fixes",
) -> bytes:
    photo = Image.open(io.BytesIO(jpeg)).convert("RGB")
    scale = HEIGHT / photo.height
    photo = photo.resize((max(1, int(photo.width * scale)), HEIGHT))
    pw, ph = photo.size
    panel = int(pw * PANEL_RATIO) + 160
    canvas = Image.new("RGB", (pw + panel, ph), (255, 255, 255))
    canvas.paste(photo, (0, 0))

    rgb = _rgb(color)
    overlay = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    od = ImageDraw.Draw(overlay)
    f_title, f_label, f_badge, f_small = _font(26), _font(20), _font(20), _font(16)
    labels = labels or [str(i) for i in range(1, len(items) + 1)]
    inset = int(pw * 0.06)

    # Each item's marked area and the y of its badge; callouts follow the same top-to-bottom order.
    marks = []
    per_region: dict[str, int] = {}
    for item, label in zip(items, labels):
        region = item.get("region") if item.get("region") in REGION_BANDS else "whole"
        top, bottom = REGION_BANDS[region]
        box = (inset, int(top * ph), pw - inset, int(bottom * ph))
        badge_y = box[1] + 30 if region == "whole" else (box[1] + box[3]) // 2
        k = per_region.get(region, 0)          # several marks in one region: step them apart
        per_region[region] = k + 1
        badge_y += 46 * k
        marks.append({"item": item, "label": label, "region": region, "box": box, "y": badge_y})
    marks.sort(key=lambda m: m["y"])

    for m in marks:
        if m["region"] == "whole":
            od.rectangle(m["box"], outline=(*rgb, 255), width=3)
        else:
            od.rectangle(m["box"], fill=(*rgb, 52), outline=(*rgb, 255), width=4)
    canvas = Image.alpha_composite(canvas.convert("RGBA"), overlay).convert("RGB")
    d = ImageDraw.Draw(canvas)
    if title:
        d.text((pw + 24, 18), title, fill=(30, 30, 30), font=f_title)

    # Callouts: as close to their badge as possible, never overlapping, in the same order.
    next_top = 64
    for m in marks:
        item = m["item"]
        lines = textwrap.wrap(str(item.get("title", "")), 30)[:2]
        detail = textwrap.wrap(str(item.get(detail_key, "")), 40)[:3]
        ch = 34 + 24 * len(lines) + 20 * len(detail)
        cy = max(next_top, min(m["y"] - ch // 2, ph - ch - 30))
        next_top = cy + ch + 14
        cx = pw + 24
        d.rounded_rectangle((cx, cy, pw + panel - 24, cy + ch), radius=10, outline=rgb, width=3, fill=(250, 250, 250))
        d.ellipse((cx + 10, cy + 10, cx + 38, cy + 38), fill=rgb)
        d.text((cx + 24, cy + 24), m["label"], fill=(255, 255, 255), font=f_badge, anchor="mm")
        for i, line in enumerate(lines):
            d.text((cx + 50, cy + 12 + 24 * i), line, fill=(20, 20, 20), font=f_label)
        for i, line in enumerate(detail):
            d.text((cx + 50, cy + 16 + 24 * len(lines) + 20 * i), line, fill=(90, 90, 90), font=f_small)
        # leader: badge -> right edge of the photo -> callout
        bx, by = m["box"][0] + 26, m["y"]
        d.line([(bx, by), (pw + 6, by), (cx, cy + 24)], fill=rgb, width=3)
        d.ellipse((bx - 18, by - 18, bx + 18, by + 18), fill=rgb, outline=(255, 255, 255), width=3)
        d.text((bx, by), m["label"], fill=(255, 255, 255), font=f_badge, anchor="mm")
    d.text((10, ph - 22), "Marked areas are approximate", fill=(120, 120, 120), font=f_small)

    out = io.BytesIO()
    canvas.save(out, format="JPEG", quality=88)
    return out.getvalue()
