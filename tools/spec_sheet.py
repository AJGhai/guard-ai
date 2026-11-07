"""One-page product spec sheet for suppliers, as a self-contained printable HTML file.

build_spec_sheet(...) returns HTML with the annotated photo (numbered pins at fixed regions),
the product spec, and the requested changes with what to ask the supplier. Open it in a browser
and print to PDF. Nothing is sent anywhere.

sample_photo() draws a simple illustration of wide-leg pants (an original drawing, no stock or
listing image), so the page works offline with no key.
"""

from __future__ import annotations

import base64
import html
import io
from datetime import date
from typing import Any

# Where a region shows on the product photo: (x%, y%). Same layout as the Product to PO page.
REGION_XY = {"upper": (50, 20), "waist": (50, 38), "lower_body": (50, 62), "hem": (50, 88), "whole": (84, 50)}
PIN_STEP = 14   # horizontal offset (%) when several pins share a region


def pin_positions(items: list[dict[str, Any]]) -> list[tuple[float, float]]:
    """One (x%, y%) per item, spreading pins that share a region so numbers stay readable."""
    counts: dict[str, int] = {}
    out = []
    for item in items:
        region = item.get("region") if item.get("region") in REGION_XY else "whole"
        x, y = REGION_XY[region]
        k = counts.get(region, 0)
        counts[region] = k + 1
        out.append((min(92.0, x + PIN_STEP * k), y))
    return out


def pinned_photo_html(src: str, items: list[dict[str, Any]], color: str, labels: list[str]) -> str:
    """An <img> with absolutely positioned numbered pins (labels[i] is pin i's text)."""
    pins = "".join(
        f'<div style="position:absolute;left:{x}%;top:{y}%;transform:translate(-50%,-50%);'
        f'background:{color};color:#04130d;border:2px solid #fff;border-radius:50%;width:28px;'
        f'height:28px;display:flex;align-items:center;justify-content:center;font:700 13px sans-serif">'
        f"{html.escape(label)}</div>"
        for (x, y), label in zip(pin_positions(items), labels)
    )
    return (
        f'<div style="position:relative;max-width:420px">'
        f'<img src="{src}" style="width:100%;border-radius:8px;display:block"/>{pins}</div>'
    )


def data_uri(jpeg: bytes) -> str:
    return "data:image/jpeg;base64," + base64.b64encode(jpeg).decode()


def build_spec_sheet(
    jpeg: bytes,
    spec: dict[str, Any],
    selected: list[dict[str, Any]],
    economics: dict[str, Any] | None = None,
    buyer: str = "Guard Apparel",
    annotated: bytes | None = None,
) -> str:
    e = html.escape
    rows = "".join(
        f"<tr><td class='n'>{i}</td><td><b>{e(s['title'])}</b><br><span class='m'>Fixes: {e(s['fixes'])}</span></td>"
        f"<td>{e(s['supplier_ask'])}</td></tr>"
        for i, s in enumerate(selected, 1)
    ) or "<tr><td colspan='3' class='m'>No changes selected.</td></tr>"
    econ = ""
    if economics:
        econ = (
            f"<p class='m'>Target landed cost (our estimate): <b>${economics['landed']:.2f}</b> per unit. "
            f"Please quote each change separately with MOQ, lead time and sample timeline.</p>"
        )
    if annotated:  # tech-pack image with marked areas and callouts (tools/annotate.py)
        photo = f'<img src="{data_uri(annotated)}" style="width:100%;border-radius:8px;display:block"/>'
    else:
        photo = pinned_photo_html(data_uri(jpeg), selected, "#00d395", [str(i) for i in range(1, len(selected) + 1)])
    return f"""<!doctype html><html><head><meta charset="utf-8"><title>Spec sheet: {e(spec['title'])}</title>
<style>
 body {{ font-family: -apple-system, Segoe UI, Helvetica, Arial, sans-serif; color:#111; margin:32px; }}
 h1 {{ font-size:22px; margin:0 0 4px; }} h2 {{ font-size:15px; margin:18px 0 6px; }}
 .m {{ color:#555; font-size:13px; }} .grid {{ display:flex; gap:28px; align-items:flex-start; }}
 table {{ border-collapse:collapse; width:100%; font-size:13px; }}
 td, th {{ border-top:1px solid #ddd; padding:8px 6px; vertical-align:top; text-align:left; }}
 td.n {{ width:26px; font-weight:700; color:#00a374; }} ul {{ margin:4px 0; padding-left:18px; font-size:13px; }}
 .tag {{ display:inline-block; font-size:11px; border:1px solid #f5a623; color:#b37100; border-radius:99px; padding:1px 8px; }}
 @media print {{ body {{ margin:12mm; }} }}
</style></head><body>
<h1>{e(spec['title'])}</h1>
<div class="m">Product spec and change request from {e(buyer)} · {date.today():%d %b %Y}
 · <span class="tag">DRAFT FOR SUPPLIER QUOTES</span></div>
<div class="grid" style="margin-top:16px">
 <div style="flex:0 0 {520 if annotated else 360}px">{photo}<div class="m" style="margin-top:6px">Numbers mark where each change applies (approximate).</div></div>
 <div style="flex:1">
  <h2>Product</h2><ul>
   <li><b>Type:</b> {e(spec['product_type'])}</li>
   <li><b>Materials:</b> {e(', '.join(spec.get('materials') or ['to confirm']))}</li>
   {''.join(f'<li>{e(f)}</li>' for f in spec.get('features') or [])}
  </ul>
  {('<p style="font-size:13px"><b>Certifications requested:</b> ' + e(', '.join(spec['certifications'])) + '</p>') if spec.get('certifications') else ''}
  <h2>Requested changes</h2>
  <table><tr><th></th><th>Change</th><th>What we ask</th></tr>{rows}</table>
  {econ}
 </div>
</div>
<p class="m" style="margin-top:20px">Prepared with an AI agent from the product photo; materials and features are as seen in the photo and must be confirmed by the supplier.</p>
</body></html>"""


def sample_photo() -> bytes:
    """A simple original illustration of light linen wide-leg pants, as a JPEG."""
    from PIL import Image, ImageDraw

    w, h = 600, 800
    img = Image.new("RGB", (w, h), (236, 232, 224))
    d = ImageDraw.Draw(img)
    linen, shade, line = (222, 210, 186), (205, 192, 166), (150, 138, 115)
    d.rectangle([180, 120, 420, 185], fill=linen, outline=line, width=3)               # waistband
    for x in range(195, 410, 18):
        d.line([x, 128, x + 6, 178], fill=shade, width=2)                                # gathers
    d.polygon([(180, 185), (300, 185), (290, 720), (110, 720)], fill=linen, outline=line)  # left leg
    d.polygon([(300, 185), (420, 185), (490, 720), (310, 720)], fill=linen, outline=line)  # right leg
    d.line([300, 185, 300, 330], fill=line, width=3)                                     # center seam
    d.line([200, 300, 165, 700], fill=shade, width=3)
    d.line([400, 300, 440, 700], fill=shade, width=3)
    d.rectangle([110, 700, 290, 720], fill=shade)
    d.rectangle([310, 700, 490, 720], fill=shade)
    d.text((24, 770), "Sample illustration", fill=(120, 110, 95))
    out = io.BytesIO()
    img.save(out, format="JPEG", quality=88)
    return out.getvalue()
