"""EyeScan report - high-resolution contact sheets (JPEG).

Pages are always at least 4000 px on the long side (default 6000 x 4000, 3:2)
saved at JPEG quality 95 / 4:4:4 / 300 dpi, so they print or zoom cleanly.

Two modes:
  * ``photos`` - the whole photograph in each tile (good for an overview);
  * ``faces``  - a crop of the most-closed face in each tile (good for checking
                 eyes, especially in group shots where faces are tiny).
"""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

import eyescan_core as core

RATIOS = {"3:2": (3, 2), "4:3": (4, 3), "16:9": (16, 9), "5:4": (5, 4)}
MIN_LONG_SIDE = 4000

THEMES = {
    "dark": dict(bg=(18, 20, 23), tile=(30, 33, 38), text=(235, 238, 241),
                 sub=(150, 158, 166), rule=(52, 57, 64)),
    "light": dict(bg=(246, 247, 249), tile=(255, 255, 255), text=(28, 32, 37),
                  sub=(96, 104, 112), rule=(214, 218, 223)),
}
STATE_COLOR = {
    "closed": (227, 93, 106),
    "down": (240, 173, 78),
    "open": (71, 159, 118),
    "no-face": (108, 117, 125),
    "error": (255, 205, 57),
}
STATE_LABEL = {"closed": "Eyes closed", "down": "Looking down", "open": "Eyes open"}
_RANK = {"closed": 0, "down": 1, "open": 2, "no-face": 3, "error": 4,
         "unsupported": 5}


def page_size(ratio: str = "3:2", long_side: int = 6000,
              portrait: bool = False) -> tuple[int, int]:
    """Pixel size of a page: long side >= 4000, exact ratio (even numbers)."""
    a, b = RATIOS.get(ratio, RATIOS["3:2"])
    if a < b:
        a, b = b, a
    long_side = max(MIN_LONG_SIDE, int(long_side))
    long_side -= long_side % 2
    short = int(round(long_side * b / a))
    short -= short % 2
    return (short, long_side) if portrait else (long_side, short)


_FONT_CACHE: dict = {}


def _font(px: int, bold: bool = False):
    key = (px, bold)
    if key in _FONT_CACHE:
        return _FONT_CACHE[key]
    names = (("segoeuib.ttf", "arialbd.ttf", "DejaVuSans-Bold.ttf") if bold
             else ("segoeui.ttf", "arial.ttf", "DejaVuSans.ttf"))
    face = None
    for n in names:
        try:
            face = ImageFont.truetype(n, max(8, px))
            break
        except Exception:  # noqa: BLE001
            continue
    _FONT_CACHE[key] = face or ImageFont.load_default()
    return _FONT_CACHE[key]


def _elide(draw, text: str, font, max_w: int) -> str:
    if draw.textlength(text, font=font) <= max_w:
        return text
    while len(text) > 4 and draw.textlength(text + "…", font=font) > max_w:
        text = text[:-1]
    return text + "…"


def _focus_face(r):
    """The face to feature: the most 'closed' one (largest as tie-break)."""
    if not r.faces:
        return None
    rank = {"closed": 0, "down": 1, "open": 2, "ignored": 3}
    return min(r.faces, key=lambda f: (rank.get(f.state, 3), -f.blink,
                                       -core._area(f.box)))


def _render_image(r, mode: str, box_w: int, box_h: int):
    """Return (RGB image fitted into box, offset) with overlay drawn."""
    from PIL import ImageOps

    try:
        if mode == "faces" and r.faces:
            f = _focus_face(r)
            big = core.load_image(r.path, 3072)
            W, H = big.size
            b = f.box
            bw, bh = b[2] - b[0], b[3] - b[1]
            side = max(bw * W, bh * H) * 1.7           # a roomy square-ish crop
            cx, cy = (b[0] + b[2]) / 2 * W, (b[1] + b[3]) / 2 * H
            x0, y0 = int(cx - side / 2), int(cy - side / 2)
            img = big.crop((x0, y0, int(x0 + side), int(y0 + side)))
            col = STATE_COLOR.get(f.state, STATE_COLOR["open"])
            d = ImageDraw.Draw(img)
            # outline the eye box so the reviewer's eye goes straight there
            lw = max(2, int(side / 190))
            for eye in f.eyes:
                if len(eye) >= 3:
                    pts = [(x * W - x0, y * H - y0) for x, y in eye]
                    d.line(pts + [pts[0]], fill=col, width=lw, joint="curve")
        else:
            img = core.load_image(r.path, int(max(box_w, box_h) * 1.15))
            col = STATE_COLOR.get(r.verdict, STATE_COLOR["open"])
            d = ImageDraw.Draw(img)
            for f in r.faces:
                c = STATE_COLOR.get(f.state, STATE_COLOR["open"])
                if f.state == "ignored":
                    continue
                b = f.box
                d.rectangle([b[0] * img.width, b[1] * img.height,
                             b[2] * img.width, b[3] * img.height],
                            outline=c, width=max(2, img.width // 260))
        img.thumbnail((box_w, box_h), Image.LANCZOS)
        return img
    except Exception:  # noqa: BLE001
        return None


def _caption(r, mode: str) -> tuple[str, str]:
    f = _focus_face(r) if r.faces else None
    if f is None:
        return r.path.name, (r.error or "no face detected")
    bits = [f"blink {f.blink:.2f}"]
    if f.ear is not None:
        bits.append(f"EAR {f.ear:.2f}")
    if f.pitch is not None:
        bits.append(f"pitch {f.pitch:.0f}°")
    extra = [t for t in r.tags() if t not in ("solo", "pair")]
    line2 = "  ·  ".join(bits)
    if extra:
        line2 += "  ·  " + ", ".join(extra[:2])
    return r.path.name, line2


def make_contact_sheets(
    results,
    out_dir,
    *,
    title: str = "EyeScan contact sheet",
    subtitle: str = "",
    mode: str = "photos",           # photos | faces
    ratio: str = "3:2",
    long_side: int = 6000,
    portrait: bool = False,
    cols: int | None = None,
    rows: int | None = None,
    only: str = "flagged",          # flagged | attention | unclear | extras | all
    theme: str = "dark",
    quality: int = 95,
    prefix: str = "contact_sheet",
    progress=None,                  # callable(done, total)
    stop=None,                      # object with is_set()
) -> list[Path]:
    """Render paginated contact sheets and return the written file paths."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    th = THEMES.get(theme, THEMES["dark"])

    if only == "flagged":
        items = [r for r in results if r.verdict == "closed"]
    elif only == "attention":
        items = [r for r in results if r.verdict in ("closed", "down")]
    elif only == "unclear":
        items = [r for r in results if r.unclear]
    elif only == "extras":
        items = [r for r in results if r.is_duplicate]
    else:
        items = list(results)
    items.sort(key=lambda r: (_RANK.get(r.verdict, 9), -r.max_blink,
                              r.path.name.lower()))
    if not items:
        return []

    W, H = page_size(ratio, long_side, portrait)
    landscape = W >= H
    cols = cols or (8 if landscape else 5)
    rows = rows or (4 if landscape else 6)
    per_page = cols * rows
    pages = (len(items) + per_page - 1) // per_page

    margin = int(W * 0.018)
    head_h = int(H * (0.075 if landscape else 0.05))
    foot_h = int(H * 0.028)
    gap = int(W * 0.007)
    cell_w = (W - 2 * margin - (cols - 1) * gap) // cols
    cell_h = (H - head_h - foot_h - margin - (rows - 1) * gap) // rows
    cap_h = int(cell_h * 0.15)
    pad = int(cell_w * 0.025)
    bar = max(4, int(cell_w * 0.018))               # coloured status bar
    box_w, box_h = cell_w - 2 * pad, cell_h - cap_h - pad - bar

    f_title = _font(int(head_h * 0.42), True)
    f_sub = _font(int(head_h * 0.2))
    f_name = _font(int(cap_h * 0.36), True)
    f_meta = _font(int(cap_h * 0.29))
    f_foot = _font(int(foot_h * 0.5))

    counts = {k: sum(1 for r in results if r.verdict == k)
              for k in ("closed", "down", "open")}
    stamp = time.strftime("%d %b %Y  %H:%M")
    written: list[Path] = []
    done = 0
    total = len(items)

    with ThreadPoolExecutor(max_workers=4) as pool:
        for page in range(pages):
            if stop is not None and stop.is_set():
                break
            chunk = items[page * per_page:(page + 1) * per_page]
            futs = [pool.submit(_render_image, r, mode, box_w, box_h)
                    for r in chunk]
            sheet = Image.new("RGB", (W, H), th["bg"])
            d = ImageDraw.Draw(sheet)

            # header
            d.text((margin, margin * 0.55), title, font=f_title, fill=th["text"])
            sub = subtitle or f"{total} photo(s)  ·  {stamp}"
            d.text((margin, margin * 0.55 + head_h * 0.50), sub, font=f_sub,
                   fill=th["sub"])
            x = W - margin
            for key in ("open", "down", "closed"):
                label = f"{STATE_LABEL[key]}  {counts[key]}"
                tw = int(d.textlength(label, font=f_sub))
                x -= tw
                d.text((x, margin * 0.55 + head_h * 0.18), label, font=f_sub,
                       fill=th["text"])
                sw = int(head_h * 0.2)
                d.rectangle([x - sw - 10, margin * 0.55 + head_h * 0.2,
                             x - 10, margin * 0.55 + head_h * 0.2 + sw],
                            fill=STATE_COLOR[key])
                x -= sw + 60
            d.line([margin, head_h, W - margin, head_h], fill=th["rule"], width=2)

            # tiles
            for i, (r, fut) in enumerate(zip(chunk, futs)):
                cx = margin + (i % cols) * (cell_w + gap)
                cy = head_h + (i // cols) * (cell_h + gap) + int(margin * 0.4)
                d.rectangle([cx, cy, cx + cell_w, cy + cell_h], fill=th["tile"])
                col = STATE_COLOR.get(r.verdict, STATE_COLOR["open"])
                d.rectangle([cx, cy, cx + cell_w, cy + bar], fill=col)
                img = fut.result()
                if img is not None:
                    sheet.paste(img, (cx + (cell_w - img.width) // 2,
                                      cy + bar + pad // 2
                                      + (box_h - img.height) // 2))
                else:
                    d.text((cx + pad, cy + cell_h // 2), "unreadable",
                           font=f_meta, fill=th["sub"])
                name, meta = _caption(r, mode)
                ty = cy + cell_h - cap_h
                d.text((cx + pad, ty), _elide(d, name, f_name, cell_w - 2 * pad),
                       font=f_name, fill=th["text"])
                d.text((cx + pad, ty + cap_h * 0.50),
                       _elide(d, meta, f_meta, cell_w - 2 * pad),
                       font=f_meta, fill=th["sub"])
                done += 1
                if progress:
                    progress(done, total)

            d.text((margin, H - foot_h * 0.85),
                   f"EyeScan {core.__version__}  ·  {mode} view  ·  "
                   f"page {page + 1} of {pages}", font=f_foot, fill=th["sub"])

            path = out_dir / f"{prefix}_{mode}_{page + 1:02d}.jpg"
            sheet.save(path, "JPEG", quality=int(quality), subsampling=0,
                       optimize=True, dpi=(300, 300))
            written.append(path)
    return written
