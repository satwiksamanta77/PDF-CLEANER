#!/usr/bin/env python3
"""Clean a PDF so Canva's PDF-to-editable import stays clean.

V3 strategy (clone + surgical edits, never rebuild a page from scratch):

  - Start from a full copy of the source document so every printed image
    (geometry figures, histograms, diagrams) and every printed drawing is
    preserved byte-for-byte, with its real transparency (SMask) and z-order.
  - Remove only what the toggles ask for, precisely:
      * handwriting strokes: pen-width (>= 1.4pt) stroke drawings, in any pen
        colour, plus pure red / black filled letterforms with many path items
      * watermarks: grey "coaching center" vector (fill 0.40-0.60, >= 50 items),
        cyan "e1" vector fills, and the faint universal watermark images
        (SMask max <= 10, present on every page)
      * pen images: images whose visible pixels are >= 85% one tight pen hue
        (red / green / blue pen overlays and handwritten number badges)
  - V6 (2026-10-24): NEVER re-emit text. The original PDFs upload to
    Canva perfectly; every Canva break came from replacing the original
    text (real embedded fonts, TJ positioning) with synthetic Helvetica
    textboxes, which Canva's importer merges and re-flows (fractions
    unstack, tight lines overlap, word spaces collapse). So text is now
    left exactly as the producer wrote it, and only unwanted pieces are
    removed:
      * Hindi: delete ONLY Hindi BT blocks from the content stream
        (Devanagari font, or a non-Latin block on a dropped Hindi
        baseline). Latin text on the same page is never touched, so it
        keeps its original fonts, sizes, colours and TJ positioning.
        Rect redaction is not used for text: line bboxes overlap
        neighbouring lines, so it deleted Latin fragments on tight
        pages.
      * Hindi is optional: remove_hindi=True (the default) redacts the
        Hindi rects; remove_hindi=False leaves Hindi text untouched (it
        renders correctly as-is, no rasterisation needed any more).
      * Options move up over the removed Hindi blocks, and question text
        goes red / option text black, by editing ONLY the BT text blocks
        in the content stream (shift the block's Tm/TD position, replace
        its rg/RG colour ops). Every other stream byte is preserved, so
        the surviving text is operator-identical to the original.
  - Math alphanumeric unicode (U+1D400-U+1D7FF) is normalised to ASCII and
    the rupee sign to "Rs", as before.

Requires: pymupdf, Pillow.
"""

import io
import os
import re

import pymupdf

try:
    from PIL import Image
except ImportError:
    Image = None

MATH_DIGIT_MAP = {}
for i in range(10):
    MATH_DIGIT_MAP[0x1D7CE + i] = chr(ord("0") + i)
    MATH_DIGIT_MAP[0x1D7D8 + i] = chr(ord("0") + i)
    MATH_DIGIT_MAP[0x1D7E2 + i] = chr(ord("0") + i)
    MATH_DIGIT_MAP[0x1D7EC + i] = chr(ord("0") + i)
    MATH_DIGIT_MAP[0x1D7F6 + i] = chr(ord("0") + i)

_MATH_LETTER_RUNS = [
    (0x1D400, "A"), (0x1D434, "A"), (0x1D468, "A"), (0x1D49C, "A"),
    (0x1D4D0, "A"), (0x1D504, "A"), (0x1D538, "A"), (0x1D56C, "A"),
    (0x1D5A0, "A"), (0x1D5D4, "A"), (0x1D608, "A"), (0x1D63C, "A"),
    (0x1D670, "A"),
]


def _math_letter(cp: int):
    for base, first in _MATH_LETTER_RUNS:
        off = cp - base
        if 0 <= off < 52:
            if off < 26:
                return chr(ord(first) + off)
            return chr(ord(first.lower()) + off - 26)
    return None


def normalize_text(s: str) -> str:
    out = []
    for c in s:
        cp = ord(c)
        if cp in MATH_DIGIT_MAP:
            out.append(MATH_DIGIT_MAP[cp])
        elif 0x1D400 <= cp <= 0x1D7FF:
            rep = _math_letter(cp)
            out.append(rep if rep else c)
        elif cp == 0x20B9:
            out.append("Rs")
        elif cp == 0x2212:
            out.append("-")
        else:
            out.append(c)
    return "".join(out)


def has_devanagari(s: str) -> bool:
    return any(0x0900 <= ord(c) <= 0x097F for c in s)


_WINANSI_EXTRA = set("¡¢£¤¥¦§¨©ª«¬®¯°±²³´µ¶·¸¹º»¼½¾¿"
                     "ÀÁÂÃÄÅÆÇÈÉÊËÌÍÎÏÐÑÒÓÔÕÖ×ØÙÚÛÜÝÞß"
                     "àáâãäåæçèéêëìíîïðñòóôõö÷øùúûüýþÿ"
                     "‘’“”–—…•€™")


def is_winansi_safe(s: str) -> bool:
    for c in s:
        if 32 <= ord(c) < 127 or c in _WINANSI_EXTRA or c in "\t":
            continue
        return False
    return True


def int_to_rgb(c: int):
    return ((c >> 16 & 255) / 255.0, (c >> 8 & 255) / 255.0, (c & 255) / 255.0)


def _saturated(color) -> bool:
    return color is not None and max(color) - min(color) > 0.5 and max(color) >= 0.8


def is_pen_stroke(d: dict) -> bool:
    if d.get("color") is None or d.get("fill") is not None:
        return False
    # The pen tool in these decks always writes at 1.5001pt. Printed
    # strokes are thinner (0.14 frame, 1.0 design lines) or heavier
    # (2.25+ diagram outlines, e.g. triangle sides) - both are content.
    if not (1.3 <= (d.get("width") or 0) <= 1.7):
        return False
    items = d.get("items", [])
    if len(items) >= 2:
        return True
    if not items:
        return False
    if items[0][0] == "re":
        return False  # printed frame / shape outline
    r = d["rect"]
    if max(r.width, r.height) > 45:
        # One long straight stroke. Vertical ones are printed dividers;
        # long dark ones are printed diagram bases/axes. The teacher's
        # long underlines are saturated pen colours.
        if r.width <= 2:
            return False
        return _saturated(d["color"])
    return True


def is_colored_ink(color) -> bool:
    if color is None:
        return False
    r, g, b = color[0], color[1], color[2]
    if r > 0.7 and g < 0.3 and b < 0.3:
        return True
    if g > 0.5 and r < 0.3 and 0.2 < b < 0.55 and g > b:
        return True
    if b > 0.6 and 0.3 < g < 0.9 and r < 0.3:
        return True
    return False


def is_gray_watermark(d: dict) -> bool:
    fill = d.get("fill")
    if fill is None:
        return False
    r, g, b = fill[0], fill[1], fill[2]
    grayish = abs(r - g) < 0.03 and abs(g - b) < 0.03 and 0.40 <= r <= 0.60
    return grayish and len(d.get("items", [])) >= 50


def is_e1_watermark(d: dict) -> bool:
    fill = d.get("fill")
    if fill is None:
        return False
    rf, gf, bf = fill[0], fill[1], fill[2]
    return rf < 0.15 and 0.55 < gf < 0.85 and 0.80 < bf < 1.0


def is_filled_ink_letterform(d: dict) -> bool:
    fill = d.get("fill")
    if fill is None or d.get("color") is not None:
        return False
    if len(d.get("items", [])) < 20:
        return False
    r, g, b = fill[0], fill[1], fill[2]
    pure_red = r > 0.9 and g < 0.1 and b < 0.1
    pure_black = r < 0.1 and g < 0.1 and b < 0.1
    if not (pure_red or pure_black):
        return False
    rect = d.get("rect")
    if rect is not None and (rect.height > 40 or rect.width > 70):
        return False
    return True


def _ink_is_candidate(d: dict) -> bool:
    """The ink branch of should_remove_drawing, as a predicate."""
    if is_pen_stroke(d):
        return True
    if is_colored_ink(d.get("color")) and len(d.get("items", [])) >= 2 \
            and 1.3 <= (d.get("width") or 0) <= 1.7:
        return True
    if is_filled_ink_letterform(d):
        return True
    return False


def _ink_figure_rects(drawings, page) -> list:
    """Bboxes of hand-drawn FIGURES to exempt from ink removal.

    The pen pass removes ink strokes, but on some pages the figures
    themselves are hand-drawn in the same pen (Mens 2D p13/p23: two
    large squares in red/green ink, 107/125 vector drawings, wiped
    to a blank page by V3 and every version since). A figure is a
    large connected ink group: group ink-candidate paths by rect
    proximity (6pt), and keep groups >= 80pt in BOTH dimensions
    (annotations -- ticks, circles, written digits, margin notes --
    are small, or wide-but-short, and stay removed). On a text-free
    page (no alphabetic dict line -- only a footer number) all ink
    is the page's content, so the whole page is kept. Exemption
    applies ONLY to ink removal, never to gray/e1 watermark removal.
    """
    import re as _re
    has_alpha = False
    for blk in page.get_text("dict")["blocks"]:
        if blk.get("type") != 0:
            continue
        for ln in blk.get("lines", []):
            for s in ln.get("spans", []):
                if _re.search(r"[A-Za-z]", s["text"]):
                    has_alpha = True
                    break
    if not has_alpha:
        return [page.rect]
    cands = [d for d in drawings if _ink_is_candidate(d)]
    parent = list(range(len(cands)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    # Group by TOUCHING (2pt), not nearness: 6pt chained the teacher's
    # scattered marks across whole questions into fake "figures"
    # (Geo p14/p25 kept annotation groups of 245x261 / 434x82).
    rects = []
    for d in cands:
        r = d["rect"]
        rects.append((r.x0 - 2.0, r.y0 - 2.0, r.x1 + 2.0, r.y1 + 2.0))
    for i in range(len(cands)):
        for j in range(i + 1, len(cands)):
            a, b = rects[i], rects[j]
            if a[0] <= b[2] and b[0] <= a[2] and a[1] <= b[3] \
                    and b[1] <= a[3]:
                ri, rj = find(i), find(j)
                if ri != rj:
                    parent[ri] = rj
    groups = {}
    for i, d in enumerate(cands):
        groups.setdefault(find(i), []).append(d)
    # The slide template paints two blue panels (e1 fills) on every
    # page, and a printed figure is often drawn ON one (Mens 3D
    # p16's cylinder outlines sit on the 161-240x77-180 panel, so
    # the ink-only bbox, 52x75, understates the figure and the gate
    # dropped it). An ink group that overlaps a panel by >= 30% of
    # its own bbox area counts as a figure even when under 80x80 --
    # working merely adjacent to a panel (Mens 3D p22's scratch
    # triangle, ~2% overlap) does not. Page-background fills are
    # white, not e1, so they can never qualify a group this way.
    panels = [d["rect"] for d in drawings if is_e1_watermark(d)]
    keep = []
    for members in groups.values():
        x0 = min(m["rect"].x0 for m in members)
        y0 = min(m["rect"].y0 for m in members)
        x1 = max(m["rect"].x1 for m in members)
        y1 = max(m["rect"].y1 for m in members)
        panel_only = False
        if x1 - x0 < 80.0 or y1 - y0 < 80.0:
            garea = (x1 - x0) * (y1 - y0)
            on_panel = False
            for pr in panels:
                ix = max(0.0, min(x1, pr.x1) - max(x0, pr.x0))
                iy = max(0.0, min(y1, pr.y1) - max(y0, pr.y0))
                if garea > 0 and ix * iy / garea >= 0.30:
                    on_panel = True
                    break
            if not on_panel:
                continue
            panel_only = True
        # A figure is built from long strokes (sides, circumcircle);
        # chained annotations are short strokes. Require >= 2 long
        # paths, then keep only the long paths plus paths near them
        # (figure labels) -- the teacher's working written beside a
        # figure (Geo p14's "BA=", "CAP ~ BAC" notes below the
        # triangle) is far from every long stroke and stays removed.
        long_paths = [m for m in members
                      if max(m["rect"].width, m["rect"].height) >= 40.0]
        if len(long_paths) < 2:
            continue
        if panel_only:
            # The panel path is narrow, so it needs one more guard:
            # a printed figure's long strokes are black, while
            # coloured teacher working sitting on a panel (Mens 3D
            # p22's red scratch triangle, 60% panel overlap) is not.
            # Large coloured figures still pass the main gate above.
            black = True
            for lp in long_paths:
                c = lp.get("color") or lp.get("fill")
                if c is not None and max(c) >= 0.15:
                    black = False
                    break
            if not black:
                continue
        for m in members:
            r = m["rect"]
            if max(r.width, r.height) >= 30.0:
                keep.append(pymupdf.Rect(r))
                continue
            cx, cy = (r.x0 + r.x1) / 2, (r.y0 + r.y1) / 2
            for lp in long_paths:
                lr = lp["rect"]
                dx = 0.0 if lr.x0 <= cx <= lr.x1 else min(
                    abs(cx - lr.x0), abs(cx - lr.x1))
                dy = 0.0 if lr.y0 <= cy <= lr.y1 else min(
                    abs(cy - lr.y0), abs(cy - lr.y1))
                if dx * dx + dy * dy <= 15.0 * 15.0:
                    keep.append(pymupdf.Rect(r))
                    break
    return keep


def should_remove_drawing(d: dict, remove_ink: bool, remove_marks: bool,
                          keep_ink_rects=None) -> bool:
    if remove_marks and (is_gray_watermark(d) or is_e1_watermark(d)):
        return True
    if remove_ink and _ink_is_candidate(d):
        if keep_ink_rects:
            r = d["rect"]
            cx, cy = (r.x0 + r.x1) / 2, (r.y0 + r.y1) / 2
            if any(kr.x0 <= cx <= kr.x1 and kr.y0 <= cy <= kr.y1
                   for kr in keep_ink_rects):
                return False
        return True
    return False


class ImageClassifier:
    def __init__(self, doc):
        self.doc = doc
        self.cache = {}

    def _stats(self, xref):
        if xref in self.cache:
            return self.cache[xref]
        result = {"smask_max": 255, "pen_frac": 0.0, "white_frac": 0.0,
                  "colored_frac": 0.0, "dark_frac": 0.0, "vis_ratio": 1.0}
        try:
            info = self.doc.extract_image(xref)
            smask = info.get("smask", 0)
            if smask and Image is not None:
                s_info = self.doc.extract_image(smask)
                s_im = Image.open(io.BytesIO(s_info["image"]))
                result["smask_max"] = s_im.getextrema()[1]
            if result["smask_max"] > 10 and Image is not None and smask:
                im = Image.open(io.BytesIO(info["image"])).convert("RGB")
                s_info = self.doc.extract_image(smask)
                mask = Image.open(io.BytesIO(s_info["image"])).convert("L")
                if mask.size != im.size:
                    mask = mask.resize(im.size)
                # Sample original pixels at a stride instead of resizing:
                # resampling blends thin pen strokes into the background and
                # destroys their pure colour, which is the signal we need.
                px = list(im.getdata())
                mk = list(mask.getdata())
                step = max(1, len(px) // 200000)
                px = px[::step]
                mk = mk[::step]
                vis = [p for p, m in zip(px, mk) if m > 40]
                result["vis_ratio"] = len(vis) / max(1, len(px))
                if vis:
                    n = len(vis)

                    def frac(pred):
                        return sum(1 for p in vis if pred(p)) / n
                    red = frac(lambda p: p[0] > 200 and p[1] < 80 and p[2] < 80)
                    green = frac(lambda p: p[1] > 150 and p[0] < 80 and p[2] < 120 and p[1] > p[0] + 50)
                    blue = frac(lambda p: p[2] > 200 and p[0] < 80 and p[1] < 80)
                    cyan = frac(lambda p: p[2] > 180 and p[1] > 140 and p[0] < 90)
                    result["pen_frac"] = max(red, green, blue, cyan)
                    result["colored_frac"] = red + green + blue + cyan
                    result["dark_frac"] = frac(lambda p: p[0] < 70 and p[1] < 70 and p[2] < 70)
                    result["white_frac"] = frac(lambda p: p[0] > 200 and p[1] > 200 and p[2] > 200)
        except Exception:
            pass
        self.cache[xref] = result
        return result

    def should_remove(self, xref, remove_ink: bool, remove_marks: bool) -> bool:
        st = self._stats(xref)
        if st["smask_max"] <= 10:
            return bool(remove_marks)
        if not remove_ink or st["white_frac"] >= 0.05:
            return False
        # Single pen colour (red/green/blue/cyan overlay or number badge).
        if st["pen_frac"] >= 0.85:
            return True
        # Multi-colour pen overlay: sparse visible pixels, at least a fifth
        # of them tight pen hues, and pen hues + black pen together >= 85%.
        # Printed diagrams fail the pen-hue test (their blue is pale, their
        # black is the whole image), headings fail the sparsity test.
        if st["vis_ratio"] <= 0.22 and st["colored_frac"] >= 0.20 \
                and st["colored_frac"] + st["dark_frac"] >= 0.85:
            return True
        return False


RASTER_SCALE = 4


def render_region_transparent(src_page, rect, fg_colors=(), other_colors=()):
    if Image is None:
        return None
    rect = pymupdf.Rect(rect)
    rect = rect & src_page.rect
    if rect.is_empty or rect.width < 1 or rect.height < 1:
        return None
    pix = src_page.get_pixmap(matrix=pymupdf.Matrix(RASTER_SCALE, RASTER_SCALE),
                              clip=rect, alpha=False)
    im = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
    w, h = im.size
    border = []
    for x in range(0, w, 3):
        border.append(im.getpixel((x, 0)))
        border.append(im.getpixel((x, h - 1)))
    for y in range(0, h, 3):
        border.append(im.getpixel((0, y)))
        border.append(im.getpixel((w - 1, y)))

    def near_fg(p):
        for fg in fg_colors:
            if max(abs(p[0] - fg[0]), abs(p[1] - fg[1]), abs(p[2] - fg[2])) <= 70:
                return True
        return False

    # Background is the most common border colour that is NOT close to a
    # known foreground (text) colour. Dense text lines have red/blue glyphs
    # all along the clip border (from this line and its neighbours), and
    # picking one of those as "background" would hollow out the glyphs.
    candidates = [p for p in border if not near_fg(p)]
    if not candidates:
        candidates = border
    bg = max(set(candidates), key=candidates.count)
    # If a near-white colour is at all common on the border and the picked
    # background is saturated, the page background is white.
    whites = [p for p in candidates if p[0] >= 245 and p[1] >= 245 and p[2] >= 245]
    if len(whites) >= 0.10 * len(candidates) and not (
            bg[0] >= 245 and bg[1] >= 245 and bg[2] >= 245):
        bg = (255, 255, 255)
    def near_any(p, colors, tol=70):
        for c in colors:
            if max(abs(p[0] - c[0]), abs(p[1] - c[1]), abs(p[2] - c[2])) <= tol:
                return True
        return False

    out = Image.new("RGBA", im.size)
    new_data = []
    for p in im.getdata():
        dist = max(abs(p[0] - bg[0]), abs(p[1] - bg[1]), abs(p[2] - bg[2]))
        if dist <= 35:
            alpha = 0
        elif dist >= 120:
            alpha = 255
        else:
            alpha = int((dist - 35) * 255 / 85)
        # Drop slivers of neighbouring lines: glyphs of another line that
        # stray into this clip (line bboxes overlap by design) have the
        # neighbour's colour, not this line's. Same-colour slivers are kept:
        # they land exactly on the neighbour's own copy, so they are invisible.
        if alpha and other_colors and not near_any(p, fg_colors) \
                and near_any(p, other_colors):
            alpha = 0
        new_data.append((p[0], p[1], p[2], alpha))
    out.putdata(new_data)
    buf = io.BytesIO()
    out.save(buf, format="PNG")
    return rect, buf.getvalue()


def collect_visual_lines(page):
    spans = []
    d = page.get_text("dict")
    for block in d["blocks"]:
        if block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            line_dir = line.get("dir", (1, 0))
            for s in line.get("spans", []):
                if not s.get("text") or not s.get("bbox"):
                    continue
                spans.append({
                    "text": s["text"],
                    "size": s.get("size", 12),
                    "font": s.get("font", ""),
                    "color": s.get("color", 0),
                    "origin": s.get("origin") or (s["bbox"][0], s["bbox"][3]),
                    "bbox": s["bbox"],
                    "dir": line_dir,
                })
    spans.sort(key=lambda s: (round(s["origin"][1], 1), s["bbox"][0]))
    lines = []
    for s in spans:
        placed = False
        for ln in lines:
            if abs(s["origin"][1] - ln["origin_y"]) <= 3.0 and s["dir"] == ln["dir"]:
                ln["spans"].append(s)
                placed = True
                break
        if not placed:
            lines.append({"origin_y": s["origin"][1], "dir": s["dir"], "spans": [s]})
    return lines


def merge_cluster_text(spans):
    spans = sorted(spans, key=lambda s: s["bbox"][0])
    parts, prev_text, prev_span = [], None, None
    for span in spans:
        text = normalize_text(span["text"])
        if not text:
            continue
        if prev_text is not None and text.strip() and prev_text.strip():
            gap = span["bbox"][0] - prev_span["bbox"][2]
            if gap > max(1.5, 0.15 * span.get("size", 12)) \
                    and parts and not parts[-1].endswith(" ") \
                    and not text.startswith(" "):
                parts.append(" ")
        parts.append(text)
        prev_text, prev_span = text, span
    return "".join(parts)


def fit_size(text, fontname, size, x0, limit_x):
    avail = limit_x - x0
    if avail <= 20 or not text:
        return size
    w = pymupdf.get_text_length(text, fontname=fontname, fontsize=size)
    if w <= avail * 0.97:
        return size
    return max(size * (avail * 0.97) / w, 6.5)


def _span_fg(spans):
    return tuple({tuple(int(v * 255) for v in int_to_rgb(s["color"]))
                  for s in spans})


def plan_text(page, remove_hindi=True):
    latin_jobs = []
    raster_jobs = []
    hindi_removed = 0
    limit_x = page.rect.width - 8.0
    raw_lines = collect_visual_lines(page)
    # Split each visual line into x-segments: spans sharing a baseline but
    # sitting in different page regions (a table on the left, the question
    # text on the right) must not be planned as one line. As one raster they
    # would pixel-copy everything between them, including pen marks inside
    # the table, and as one Latin line they would merge unrelated columns.
    all_lines = []
    for ln in raw_lines:
        spans = sorted(ln["spans"], key=lambda s: s["bbox"][0])
        if ln["dir"] != (1, 0) or len(spans) == 1:
            all_lines.append(ln)
            continue
        groups = [[spans[0]]]
        for s in spans[1:]:
            if s["bbox"][0] - groups[-1][-1]["bbox"][2] > 25.0:
                groups.append([s])
            else:
                groups[-1].append(s)
        for g in groups:
            all_lines.append({"origin_y": ln["origin_y"], "dir": ln["dir"],
                              "spans": g})
    line_colors = [_span_fg(ln["spans"]) for ln in all_lines]
    # Group visual lines into printed ROWS: lines whose vertical extents
    # overlap (fractions, inline superscripts/subscripts) are satellites of
    # one another. In a multi-line row every span must keep its own exact
    # position, so those lines are re-typed span by span, not merged.
    line_rects = []
    for ln in all_lines:
        r = pymupdf.Rect(ln["spans"][0]["bbox"])
        for s in ln["spans"][1:]:
            r |= pymupdf.Rect(s["bbox"])
        line_rects.append(r)
    row_of = list(range(len(all_lines)))

    def _find(i):
        while row_of[i] != i:
            row_of[i] = row_of[row_of[i]]
            i = row_of[i]
        return i
    for i in range(len(all_lines)):
        for j in range(i + 1, len(all_lines)):
            a, b = line_rects[i], line_rects[j]
            overlap = min(a.y1, b.y1) - max(a.y0, b.y0)
            if overlap > 0.25 * min(a.height, b.height):
                row_of[_find(i)] = _find(j)
    row_sizes = {}
    for i in range(len(all_lines)):
        row_sizes[_find(i)] = row_sizes.get(_find(i), 0) + 1

    # Hindi-row satellites: a Hindi fraction's digits ("4", "5") sit on
    # their own baselines, so they look like Latin lines. When Hindi is
    # removed, drop them (nearest-line test in the main loop below), or
    # they survive as stray red digits next to the question.
    drop_rows = set()
    hindi_drop_idx = set()
    dropped_rects = []
    dropped_baselines = []
    if remove_hindi:
        for li, ln in enumerate(all_lines):
            raw = "".join(s["text"] for s in ln["spans"])
            if not has_devanagari(raw):
                continue
            kept = [s for s in ln["spans"] if not has_devanagari(s["text"])]
            n_deva = sum(1 for c in raw if 0x0900 <= ord(c) <= 0x097F)
            n_latin = sum(1 for c in raw if c.isascii() and c.isalnum())
            # Only rows whose Hindi line is itself dropped (pure Hindi
            # or Hindi-dominant); a kept Latin-dominant line keeps its
            # satellites too.
            if not kept or not "".join(s["text"] for s in kept).strip() \
                    or n_deva >= n_latin:
                drop_rows.add(_find(li))
                hindi_drop_idx.add(li)
                # v19 (DI p13): the dropped rect must cover only the
                # Devanagari spans. A visual line can share a baseline
                # with unrelated Latin content in another column (the
                # bar-chart class labels 1-10 sit on the Hindi line's
                # baseline); a full-line rect deletes those labels via
                # the baseline+rect rule. Devanagari-only x-range keeps
                # them, and pure-Hindi lines are unchanged.
                deva_rect = pymupdf.Rect()
                for s in ln["spans"]:
                    if has_devanagari(s["text"]):
                        deva_rect |= pymupdf.Rect(s["bbox"])
                dropped_rects.append(deva_rect if not deva_rect.is_empty
                                    else line_rects[li])
                dropped_baselines.append(ln["origin_y"])
    # Baselines of option-label lines ("a)", "b)", ...) on this page:
    # an option's value (e.g. a superscript "3^36") sits a few points
    # from its label and must never be mistaken for a Hindi satellite.
    option_baselines = []
    for ln in all_lines:
        joined = "".join(s["text"] for s in ln["spans"]) \
            .replace(" ", "").replace("\u00a0", "")
        if joined[:2] in ("a)", "b)", "c)", "d)",
                          "A)", "B)", "C)", "D)"):
            option_baselines.append(ln["origin_y"])

    # Devanagari x-range per dropped Hindi line, for the satellite
    # column gate in the main loop below.
    deva_xrange = {}
    for mj in hindi_drop_idx:
        xs = [s["bbox"][0] for s in all_lines[mj]["spans"]
              if has_devanagari(s["text"])]
        xe = [s["bbox"][2] for s in all_lines[mj]["spans"]
              if has_devanagari(s["text"])]
        if xs:
            deva_xrange[mj] = (min(xs), max(xe))

    for li, ln in enumerate(all_lines):
        other = tuple({c for j, cols in enumerate(line_colors) if j != li
                       for c in cols})
        spans = ln["spans"]
        raw_text = "".join(s["text"] for s in spans)
        if not raw_text.strip():
            continue
        # A footer page number ("15", "25") sitting under the Hindi block
        # shares a row with Hindi lines and looks like a satellite, but it
        # is not Hindi: never drop a digits-only line at the page foot.
        is_footer_number = raw_text.strip().isdigit() \
            and ln["origin_y"] > page.rect.height - 25.0
        # Satellite test (replaces the old row test: bbox-overlap rows
        # chain across the whole page, so every short line looked like a
        # Hindi satellite). A true satellite (a Hindi fraction digit) is
        # the NEAREST line to a dropped Hindi line, within 12pt. An
        # English superscript belongs to its English line, and an option
        # value belongs to its option label, so both are kept even when
        # they sit near the Hindi block.
        nearest_hindi = None
        nearest_other = None
        for mj, other_ln in enumerate(all_lines):
            if mj == li:
                continue
            dist = abs(other_ln["origin_y"] - ln["origin_y"])
            if mj in hindi_drop_idx:
                # v20 (DI p5 "35%", p13 "CLASS"): a satellite belongs
                # to its Hindi line's COLUMN. A short Latin line in
                # another column (chart labels, pie percentages) can
                # sit within 12pt of a Hindi baseline by coincidence;
                # without a column gate it is dropped as a satellite.
                # Only Hindi lines whose DEVANAGARI spans overlap this
                # line in x (4pt tolerance) count -- a visual line's
                # full rect can itself span columns, so the Deva
                # x-range, not the line rect, is the gate.
                dx = deva_xrange.get(mj)
                if dx is not None:
                    cr = line_rects[li]
                    if cr.x0 <= dx[1] + 4.0 and dx[0] - 4.0 <= cr.x1:
                        if nearest_hindi is None or dist < nearest_hindi:
                            nearest_hindi = dist
            else:
                if nearest_other is None or dist < nearest_other:
                    nearest_other = dist
        near_option = any(abs(ln["origin_y"] - ob) <= 12.0
                          for ob in option_baselines)
        is_satellite = not has_devanagari(raw_text) \
            and len(raw_text.strip()) <= 6 and not is_footer_number \
            and not near_option and nearest_hindi is not None \
            and nearest_hindi <= 12.0 \
            and (nearest_other is None or nearest_hindi < nearest_other)
        if is_satellite:
            hindi_removed += 1
            dropped_rects.append(line_rects[li])
            dropped_baselines.append(ln["origin_y"])
            continue
        rect = line_rects[li]
        rotated = ln["dir"] != (1, 0)
        if remove_hindi and has_devanagari(raw_text):
            kept = [s for s in spans if not has_devanagari(s["text"])]
            if not kept or not "".join(s["text"] for s in kept).strip():
                hindi_removed += 1
                continue
            # Hindi-dominant line (a Hindi sentence carrying a stray
            # Latin "cm" or digit): keeping just the Latin spans leaves
            # orphan fragments scattered on the page, so drop the line.
            # A Latin-dominant line keeps its Latin spans instead.
            n_deva = sum(1 for c in raw_text if 0x0900 <= ord(c) <= 0x097F)
            n_latin = sum(1 for c in raw_text if c.isascii() and c.isalnum())
            if n_deva >= n_latin:
                hindi_removed += 1
                continue
            if not rotated:
                # Mixed line: keep the Latin spans, drop the Hindi ones.
                spans = kept
                raw_text = "".join(s["text"] for s in spans)
            # Rotated mixed lines stay rasterised (rare); pure-Hindi
            # rotated lines were already dropped above.
        if rotated or has_devanagari(raw_text):
            raster_jobs.append((rect, _span_fg(spans), other))
            continue
        if row_sizes[_find(li)] > 1:
            # Satellite row: re-type span by span at exact origins, each
            # span shrunk to its own original width so nothing can drift
            # into a neighbouring superscript or fraction part.
            for s in spans:
                text = normalize_text(s["text"])
                if not text.strip():
                    continue
                if not is_winansi_safe(text):
                    raster_jobs.append((pymupdf.Rect(s["bbox"]),
                                        _span_fg([s]), other))
                    continue
                bold = "bold" in s["font"].lower() or "cambria" in s["font"].lower()
                fontname = "hebo" if bold else "helv"
                size = s["size"]
                bw = s["bbox"][2] - s["bbox"][0]
                if bw > 4:
                    w = pymupdf.get_text_length(text, fontname=fontname,
                                                fontsize=size)
                    if w > bw * 1.03:
                        size = max(size * bw / w, 6.0)
                latin_jobs.append((s["origin"], text, fontname, size,
                                   int_to_rgb(s["color"])))
            continue
        # Split by large x-gaps as well: options in separate columns
        # (a) 50/3   b) 15   c) 8/3   d) 44/3) share a baseline, and merging
        # across a column gap would pile every column onto the first one.
        clusters = []
        for s in sorted(spans, key=lambda s: s["bbox"][0]):
            if clusters:
                cl = clusters[-1]
                first, prev = cl[0], cl[-1]
                gap = s["bbox"][0] - prev["bbox"][2]
                if abs(s["origin"][1] - first["origin"][1]) <= 1.5 \
                        and abs(s["size"] - first["size"]) <= 1.5 \
                        and s["color"] == first["color"] and gap <= 12.0:
                    cl.append(s)
                    continue
            clusters.append([s])
        for cl in clusters:
            text = merge_cluster_text(cl)
            if not text.strip():
                continue
            if not is_winansi_safe(text):
                crect = pymupdf.Rect(cl[0]["bbox"])
                for s in cl[1:]:
                    crect |= pymupdf.Rect(s["bbox"])
                raster_jobs.append((crect, _span_fg(cl), other))
                continue
            first = next(s for s in sorted(cl, key=lambda s: s["bbox"][0])
                         if normalize_text(s["text"]).strip())
            bold = any("bold" in s["font"].lower() or "cambria" in s["font"].lower()
                       for s in cl)
            fontname = "hebo" if bold else "helv"
            size = max(s["size"] for s in cl)
            # Fit inside the cluster's own original footprint, not just the
            # page edge, so a merged line can never stretch into the next
            # column or over a neighbouring element.
            cl_right = max(s["bbox"][2] for s in cl) + 2.0
            size = fit_size(text, fontname, size, first["origin"][0],
                            min(limit_x, cl_right))
            if first["origin"][0] > limit_x:
                continue
            latin_jobs.append((first["origin"], text, fontname, size,
                               int_to_rgb(first["color"])))
    return (latin_jobs, raster_jobs, hindi_removed, dropped_rects,
            dropped_baselines)


def redraw_path(page, d: dict):
    """Re-draw one drawing (used to restore a kept drawing that a TOUCHED
    redaction removed because a pen stroke's bbox overlapped it)."""
    shape = page.new_shape()
    for it in d["items"]:
        op = it[0]
        if op == "l":
            shape.draw_line(it[1], it[2])
        elif op == "re":
            shape.draw_rect(it[1])
        elif op == "qu":
            shape.draw_quad(it[1])
        elif op == "c":
            shape.draw_bezier(it[1], it[2], it[3], it[4])
    # Pass colour/fill explicitly: omitting "color" makes Shape.finish add
    # a black stroke the original never had. color=None means no stroke,
    # fill=None means no fill, exactly as in the source drawing.
    kwargs = {"color": d.get("color"), "fill": d.get("fill")}
    if d.get("color") is not None and d.get("width"):
        kwargs["width"] = d["width"]
    if d.get("color") is not None and d.get("dashes") and d["dashes"] != "[] 0":
        kwargs["dashes"] = d["dashes"]
    try:
        shape.finish(**kwargs)
        shape.commit()
    except Exception:
        pass


def _drawing_ops(d, page_height):
    """PDF operators re-drawing one kept drawing, for restores that must
    sit UNDER the text (see the restore block: prepended to the first
    content stream). get_drawings() points are top-left origin, so y is
    flipped against the page height. Returns bytes, or None when the
    drawing uses a colour form this emitter cannot express (patterns)
    or an unknown path item -- a partial path is worse than none."""
    def num(v):
        s = f"{v:.4f}".rstrip("0").rstrip(".")
        return s if s not in ("", "-0") else "0"

    def pt(p):
        return f"{num(p.x)} {num(page_height - p.y)}"

    color, fill = d.get("color"), d.get("fill")
    for c in (color, fill):
        if c is not None and not (isinstance(c, tuple) and len(c) == 3
                                  and all(isinstance(v, float)
                                          for v in c)):
            return None
    parts = ["q"]
    if color is not None:
        parts.append(f"{num(color[0])} {num(color[1])} {num(color[2])} RG")
    if fill is not None:
        parts.append(f"{num(fill[0])} {num(fill[1])} {num(fill[2])} rg")
    if color is not None and d.get("width"):
        parts.append(f"{num(d['width'])} w")
    dashes = d.get("dashes")
    if color is not None and dashes and dashes != "[] 0" and \
            all(ch in "[]0123456789. " for ch in dashes):
        parts.append(f"{dashes} d")
    # v21: a polyline/curve chain is ONE subpath. Writing "m" before
    # every segment split it into zero-area lines that fill invisibly
    # (M3 p22 c-root: present in the stream at the right rect, absent
    # from the render; v18's Shape restore kept it connected). Emit
    # "m" only when a new subpath starts (first item, or a jump).
    prev_end = None

    def _same(p, q):
        return p is not None and abs(p.x - q.x) < 0.01 \
            and abs(p.y - q.y) < 0.01

    for it in d.get("items", []):
        op = it[0]
        if op == "l":
            if not _same(prev_end, it[1]):
                parts.append(f"{pt(it[1])} m")
            parts.append(f"{pt(it[2])} l")
            prev_end = it[2]
        elif op == "re":
            r = it[1]
            parts.append(f"{num(r.x0)} {num(page_height - r.y1)} "
                         f"{num(r.width)} {num(r.height)} re")
            prev_end = None
        elif op == "qu":
            q = it[1]
            parts.append(f"{pt(q.ul)} m {pt(q.ur)} l {pt(q.lr)} l "
                         f"{pt(q.ll)} l h")
            prev_end = None
        elif op == "c":
            if not _same(prev_end, it[1]):
                parts.append(f"{pt(it[1])} m")
            parts.append(f"{pt(it[2])} {pt(it[3])} "
                         f"{pt(it[4])} c")
            prev_end = it[4]
        else:
            return None
    if fill is not None and color is not None:
        parts.append("h B")
    elif fill is not None:
        parts.append("h f")
    elif color is not None:
        parts.append("S")
    else:
        return None
    parts.append("Q")
    return ("\n".join(parts) + "\n").encode("latin-1")


def _drawing_sig(d):
    r = d["rect"]
    return (str(d.get("color")), str(d.get("fill")), str(d.get("width")),
            round(r.x0, 1), round(r.y0, 1), round(r.x1, 1), round(r.y1, 1),
            len(d.get("items", [])))


def _split_bt_blocks(text):
    """Split a content-stream string into (is_bt_block, chunk) segments.

    BT/ET inside literal (...) or hex <...> strings are text, not
    operators, so string state is tracked while scanning. Dicts (<< >>)
    never contain BT/ET operators, so only strings need tracking.
    """
    segments = []
    n = len(text)
    pos = 0  # start of the not-yet-emitted tail
    i = 0
    while i < n:
        # Find the next whole-word BT outside strings.
        bt = -1
        j = i
        while j < n:
            ch = text[j]
            if ch == "(":
                depth = 1
                j += 1
                while j < n and depth:
                    c = text[j]
                    if c == "\\":
                        j += 2
                        continue
                    if c == "(":
                        depth += 1
                    elif c == ")":
                        depth -= 1
                    j += 1
                continue
            if ch == "<" and j + 1 < n and text[j + 1] != "<":
                k = text.find(">", j + 1)
                j = n if k < 0 else k + 1
                continue
            if text.startswith("BT", j) and \
                    (j == 0 or not (text[j - 1].isalnum()
                                    or text[j - 1] == "_")) and \
                    (j + 2 >= n or not (text[j + 2].isalnum()
                                        or text[j + 2] == "_")):
                bt = j
                break
            j += 1
        if bt < 0:
            break
        # Find the matching whole-word ET outside strings.
        et = -1
        k = bt + 2
        while k < n:
            ch = text[k]
            if ch == "(":
                depth = 1
                k += 1
                while k < n and depth:
                    c = text[k]
                    if c == "\\":
                        k += 2
                        continue
                    if c == "(":
                        depth += 1
                    elif c == ")":
                        depth -= 1
                    k += 1
                continue
            if ch == "<" and k + 1 < n and text[k + 1] != "<":
                q = text.find(">", k + 1)
                k = n if q < 0 else q + 1
                continue
            if text.startswith("ET", k) and \
                    (k == 0 or not (text[k - 1].isalnum()
                                    or text[k - 1] == "_")) and \
                    (k + 2 >= n or not (text[k + 2].isalnum()
                                        or text[k + 2] == "_")):
                et = k + 2
                break
            k += 1
        if et < 0:
            break
        segments.append((False, text[pos:bt]))
        segments.append((True, text[bt:et]))
        pos = et
        i = et
    segments.append((False, text[pos:]))
    return segments


def _dict_line_info(page):
    """One entry per text line: (baseline_topdown, is_option,
    is_footer_digits, is_question, x0, x1, y0, y1, is_label).
    is_label is true only for the option-label line itself; is_option
    also covers lines promoted by proximity (see below). Span texts are
    joined with spaces removed for the option test, since fragments
    carry no spaces. A line within 12pt of an option-label line is
    option-related (an option's value, e.g. a superscript "3^36", sits
    a few points from its label) and counts as an option, so the whole
    value gets one colour. A line counts as question text when it sits
    in the question column above the first option. The column's left
    edge is read off the page (the leftmost substantial line near the
    top) because it is NOT always x250 -- Mensuration and Geometry
    questions start near x344, Statistics near x223, Surds near x158
    -- so a fixed threshold left most files' questions blue instead
    of red. A line that shares its baseline with a line left of the
    column is a table row, not question text, so it keeps its original
    colour instead of being painted red with the question."""
    height = page.rect.height
    lines = []
    top_cands = []
    for block in page.get_text("dict")["blocks"]:
        if block.get("type") != 0:
            continue
        for ln in block.get("lines", []):
            spans = sorted(ln["spans"], key=lambda s: s["bbox"][0])
            if not spans:
                continue
            baseline = spans[0]["origin"][1]
            x0 = spans[0]["bbox"][0]
            joined = "".join(s["text"] for s in spans)
            nospace = joined.replace(" ", "").replace("\u00a0", "")
            is_label = nospace[:2] in ("a)", "b)", "c)", "d)",
                                       "A)", "B)", "C)", "D)")
            is_option = is_label
            is_footer = baseline > height - 25.0 and joined.strip().isdigit()
            size = max(s["size"] for s in spans)
            lines.append((baseline, is_option, is_footer, x0,
                          ln["bbox"][0], ln["bbox"][2],
                          ln["bbox"][1], ln["bbox"][3], is_label, size))
            # Question-column left edge candidates (see docstring).
            # Single digits near the top are the circled
            # question-number badge, not the question.
            if ln["bbox"][1] < 40.0 and len(joined.strip()) >= 2 \
                    and not joined.strip().isdigit():
                top_cands.append((ln["bbox"][1], x0))
    option_baselines = [bl for bl, is_opt, _, _, _, _, _, _, _, _ in lines
                        if is_opt]
    # (baseline, left edge) of every option label, for the
    # proximity promotion below.
    label_bl_x = [(bl, x0) for bl, _i, _f, x0, _a, _c, _d, _e, is_lab, _s
                  in lines if is_lab]
    first_opt = min(option_baselines) if option_baselines else None
    # The edge is anchored on the option labels (their leftmost
    # column starts at the question column's left edge on every file
    # checked) combined with the topmost text band. Either alone
    # fails somewhere: the top band alone misses a formula line's
    # leftmost fragment when superscripts poke above it (Surds2 p15's
    # "If" at x171 sits below the band its line's fragments occupy,
    # so the edge came out as x206 and the whole line stayed blue),
    # and labels alone fail for centred options. A top-band line far
    # from every label (a left figure title) is not the question.
    label_xs = [x0 for (_b, _i, _f, x0, _a, _c, _d, _e, is_lab, _s)
                in lines if is_lab]
    label_edge = min(label_xs) if label_xs else None
    cands = []
    if top_cands:
        top_y = min(y0 for y0, _x0 in top_cands)
        band = [x0 for y0, x0 in top_cands if y0 <= top_y + 2.0]
        if label_edge is not None:
            band = [x0 for x0 in band
                    if abs(x0 - label_edge) <= 150.0]
        if band:
            cands.append(min(band))
    if label_edge is not None:
        cands.append(label_edge)
    edge = min(cands) if cands else 250.0
    label_baselines = sorted(bl for bl, _i, _f, _x, _a, _c, _d, _e,
                             is_lab, _s in lines if is_lab)
    label_size = {bl: sz for bl, _i, _f, _x, _a, _c, _d, _e, is_lab, sz
                  in lines if is_lab}
    out = []
    for (baseline, is_option, is_footer, x0, lx0, lx1, ly0, ly1,
         is_label, size) in lines:
        # v9 DI p1: proximity was baseline-only, so left-column table
        # rows (x28) within 12pt of a right-column option label's
        # baseline counted as options (painted black, and — before the
        # _row_shift_plan x-guard — shifted onto other rows). Same
        # column guard as the shift plan: the line must start at or
        # right of its label's left edge.
        if not is_footer and not is_option and any(
                abs(baseline - lb) <= 12.0 and x0 >= lx - 10.0
                for lb, lx in label_bl_x):
            is_option = True
        if not is_footer and not is_option and first_opt is not None \
                and baseline > first_opt:
            # A wrapped option value: its continuation line sits a
            # full pitch (~17pt) below its label, past the 12pt
            # window, strictly between two label rows (Statistics p4's
            # "between 80-90" line). Lines below the last label row
            # are not continuations -- figure labels live there.
            above = [lb for lb in label_baselines if lb < baseline]
            # v21: also promote a continuation below the LAST label
            # (Statistics p4's "between 90-100": 17pt below d), so it
            # takes the option colour as well as the shift. The old
            # "strictly between two labels" rule left it blue and,
            # with the surgery gate, unshifted. A figure label within
            # 20pt below the last option, in the same column, is
            # promoted too -- it moves up slightly and turns black,
            # but stays readable, which beats stranding a real value.
            # v22: same-size guard (see _row_shift_plan): a chart
            # label below the last option (DI p1's "T.N, 9%", 10pt,
            # 15.8pt below c)) is not an option continuation (the
            # options are 12pt), so it must not promote, shift, or
            # recolour -- shifting it out of its pie clip made it
            # vanish entirely.
            if above and baseline - max(above) <= 20.0 \
                    and x0 >= edge - 10.0 \
                    and abs(size - label_size.get(max(above), size)) \
                    <= 1.5:
                is_option = True
        has_left_mate = any(
            abs(ob - baseline) <= 1.0 and ox < edge - 10.0
            for (ob, _io, _if, ox, _a, _b, _c, _d, _e, _s) in lines)
        is_question = (not is_footer and not is_option
                       and first_opt is not None
                       and x0 >= edge - 10.0 and baseline < first_opt
                       and not has_left_mate)
        out.append((baseline, is_option, is_footer, is_question,
                    lx0, lx1, ly0, ly1, is_label, size))
    return out


def _table_grid_zones(page):
    """Bounding boxes of TABLE grid areas, one per table frame.

    A frame is a single-rect stroke path (a figure outline has many
    items; a watermark fill is not a stroke), at least 80x15pt, that
    CONTAINS gridlines (internal rules dividing cells). A bare
    outline is not a table: M3 p20's question column border
    (387,-0)-(545,266) has no gridlines, so it must not guard
    anything. The zone is the bbox of the gridlines, NOT the frame:
    Stats p18's frame runs to y197 but its grid ends at y148, and
    the options (y157+) sit below the grid inside the frame -- a
    frame-wide zone would wrongly swallow them. Gridlines: a
    horizontal rule (50pt+, centre strictly inside the frame) or a
    vertical rule (30pt+, centre strictly inside)."""
    zones = []
    drawings = page.get_drawings()
    for dr in drawings:
        r = dr["rect"]
        if dr["type"] not in ("s", "fs") or len(dr["items"]) != 1 \
                or dr["items"][0][0] != "re" \
                or r.width < 80.0 or r.height < 15.0:
            continue
        fx0, fx1, fy0, fy1 = r.x0, r.x1, r.y0, r.y1
        gx0 = gx1 = gy0 = gy1 = None
        for dr2 in drawings:
            if dr2["type"] not in ("s", "fs"):
                continue
            r2 = dr2["rect"]
            cy = (r2.y0 + r2.y1) / 2
            cx = (r2.x0 + r2.x1) / 2
            is_h = r2.height <= 1.0 and r2.width >= 50.0 \
                and fy0 + 5.0 < cy < fy1 - 5.0 \
                and r2.x0 < fx1 and r2.x1 > fx0
            is_v = r2.width <= 1.0 and r2.height >= 30.0 \
                and fx0 + 5.0 < cx < fx1 - 5.0 \
                and r2.y0 < fy1 and r2.y1 > fy0
            if is_h or is_v:
                gx0 = r2.x0 if gx0 is None else min(gx0, r2.x0)
                gx1 = r2.x1 if gx1 is None else max(gx1, r2.x1)
                gy0 = r2.y0 if gy0 is None else min(gy0, r2.y0)
                gy1 = r2.y1 if gy1 is None else max(gy1, r2.y1)
        if gx0 is not None:
            zones.append((gx0, gx1, gy0, gy1))
    return zones


def _row_shift_plan(lines, blocks, height, grid_zones=None):
    """One rigid shift per QUESTION's option block, capped so the
    top row never prints over the question. Returns (line_dy, zones).

    Two v7 faults lived here. First, a BT block was shifted only when
    its baseline matched a dict line baseline within 1pt, so a
    superscript block (its own raised baseline, e.g. the "2" of r^2)
    matched no line and was left behind while its option moved up.
    Now every line belongs to its nearest option-label row (within
    12pt) and takes that row's shift. Second, the full Hindi-span shift
    pushed a row's raised parts (fraction numerators) up into the
    question's last line, printing options over the question. Now each
    row's shift is capped so the row's top stays at least 6pt below
    the question's last line; the leftover is a few points of blank,
    not an overlap. Zones for option drawings use the same capped
    row shifts, so bars and roots move exactly with their text.
    v9: the shift is ONE dy per question, not per row. Per-row caps
    let a lower row shift further than the top row (M2D p4: row 2
    shifted 11.5pt more than row 1), which compressed the gap between
    rows until row 1's denominator printed as a superscript on row
    2's value. A single dy (the most constrained row's) preserves
    every inter-row gap exactly; the uncapped remainder is blank
    page bottom, not an overlap."""
    def shift_for(bl):
        return sum(b1 - b0 for b0, b1 in blocks if b1 <= bl)

    # v21: table content must never join an option row. Stats p18's
    # table values row (y126-145, inside the grid y79-148) sat within
    # 12pt of the a)/b) labels and in the same column, so it took the
    # option shift while the table border stayed put -- and, as an
    # option member, it stopped being an obstacle, so the options
    # jumped onto the border. A line whose bbox centre is inside a
    # grid zone is table content: it neither shifts nor caps (it
    # still counts as an obstacle, because it is not in opt_block).
    def in_zone(ln):
        if not grid_zones:
            return False
        cx = (ln[4] + ln[5]) / 2
        cy = (ln[6] + ln[7]) / 2
        return any(gx0 <= cx <= gx1 and gy0 <= cy <= gy1
                   for gx0, gx1, gy0, gy1 in grid_zones)

    labels = [i for i, ln in enumerate(lines) if ln[8] and not ln[2]]
    # Group labels by the question above them: the question line with
    # the greatest bottom still above the label's baseline. Pages
    # with one question give one group; multi-question pages split.
    qlines = [ln for ln in lines if ln[3] and not ln[2]]
    groups = {}
    ungrouped = []
    for i in labels:
        bl = lines[i][0]
        above = [ln for ln in qlines if ln[7] < bl]
        if above:
            qbot = max(ln[7] for ln in above)
            groups.setdefault(qbot, []).append(i)
        else:
            ungrouped.append(i)
    label_dy = {}
    label_members = {}
    for i in labels:
        bl = lines[i][0]
        # v9 DI p1: membership was baseline-only, so LEFT-column table
        # rows (x28-234) whose baselines happened to sit within 12pt of
        # a RIGHT-column option label's baseline took the option shift
        # and printed on top of other table rows (Goa onto A.P.,
        # Bihar onto M.P.). A row member must sit in the same column:
        # its left edge at or right of the label's left edge.
        # v21: an option's CONTINUATION line (Statistics p4's
        # "between 80-90" range, a full pitch ~17pt below its label)
        # belongs to the option's row and shifts with it; the old
        # 12pt window left ranges behind while their sentences moved
        # up, printing d)'s sentence over a)'s range. Continuations
        # are below the label (never above: a line above belongs to
        # the previous row), in the same column, and never a label,
        # a question line, or a footer digit.
        members = [j for j, ln in enumerate(lines)
                   if not ln[2] and not in_zone(ln)
                   and ln[4] >= lines[i][4] - 10.0
                   and (abs(ln[0] - bl) <= 12.0
                        or (not ln[8] and not ln[3]
                            and 0.0 < ln[0] - bl <= 20.0
                            and abs(ln[9] - lines[i][9]) <= 1.5))]
        label_members[i] = members
    # v21: obstacle cap. The question cap alone lets options jump
    # onto a table or figure sitting between the question and the
    # options (Statistics p18: a)/b) printed on the table's bottom
    # border). A row's shift is also capped so its top stays 6pt
    # below the nearest kept (non-option, non-footer) line above it
    # in the same column. The cap is derived from `lines` only, which
    # is identical at both call sites, so text and drawings keep
    # one dy; the 6pt margin also clears a table border sitting a
    # few points below its last text row.
    opt_block = set(labels)
    for _members in label_members.values():
        opt_block.update(_members)

    def obstacle_cap(row_top, rx0, rx1):
        best = None
        for j, ln in enumerate(lines):
            if j in opt_block or ln[2]:
                continue
            if ln[7] < row_top and ln[4] < rx1 and ln[5] > rx0:
                # v21: a table line's obstacle bottom is the GRID's
                # bottom, not the text's. Stats p18's values text
                # ends at y130 but the table border is at y148; the
                # text-bottom cap let a)/b) shift until their tops
                # (y136) straddled the border. The zone bottom
                # (148) caps them 6pt below the border instead.
                bot = ln[7]
                if grid_zones:
                    cx = (ln[4] + ln[5]) / 2
                    cy = (ln[6] + ln[7]) / 2
                    for gx0, gx1, gy0, gy1 in grid_zones:
                        if gx0 <= cx <= gx1 and gy0 <= cy <= gy1:
                            bot = max(bot, gy1)
                best = bot if best is None else max(best, bot)
        return None if best is None else row_top - best - 6.0

    for qbot, members_labels in groups.items():
        dys = []
        for i in members_labels:
            members = label_members[i]
            row_top = min(lines[j][6] for j in members) if members \
                else lines[i][6]
            row_x0 = min(lines[j][4] for j in members) if members \
                else lines[i][4]
            row_x1 = max(lines[j][5] for j in members) if members \
                else lines[i][5]
            cap = row_top - qbot - 6.0
            oc = obstacle_cap(row_top, row_x0, row_x1)
            if oc is not None:
                cap = min(cap, oc)
            dys.append(min(shift_for(lines[i][0]), cap))
        dy = max(0.0, min(dys)) if dys else 0.0
        for i in members_labels:
            label_dy[i] = dy
    for i in ungrouped:
        members = label_members[i]
        row_top = min(lines[j][6] for j in members) if members \
            else lines[i][6]
        row_x0 = min(lines[j][4] for j in members) if members \
            else lines[i][4]
        row_x1 = max(lines[j][5] for j in members) if members \
            else lines[i][5]
        cap = shift_for(lines[i][0])
        oc = obstacle_cap(row_top, row_x0, row_x1)
        if oc is not None:
            cap = min(cap, oc)
        label_dy[i] = max(0.0, cap)
    line_dy = {}
    for j, ln in enumerate(lines):
        if ln[2] or in_zone(ln):
            continue
        best = None
        for i in labels:
            d = abs(ln[0] - lines[i][0])
            cont = (not ln[8] and not ln[3]
                    and 0.0 < ln[0] - lines[i][0] <= 20.0
                    and abs(ln[9] - lines[i][9]) <= 1.5)
            if (d <= 12.0 or cont) and ln[4] >= lines[i][4] - 10.0 and (
                    best is None or d < best[0] or
                    (d == best[0]
                     and lines[i][0] < lines[best[1]][0])):
                best = (d, i)
        if best is not None:
            line_dy[j] = label_dy[best[1]]
    zones = []
    for i in labels:
        dy = label_dy[i]
        members = label_members[i]
        if dy <= 0 or not members:
            continue
        x0 = min(lines[j][4] for j in members)
        x1 = max(lines[j][5] for j in members)
        y0 = min(lines[j][6] for j in members)
        y1 = max(lines[j][7] for j in members)
        zones.append((x0, x1, height - (y1 + 4.0), height - (y0 - 4.0),
                      dy))
    return line_dy, zones


def _merged_hindi_spans(dropped_rects):
    """Merge dropped Hindi rects into vertical spans (rects with a
    vertical gap < 30pt belong to one Hindi block)."""
    spans = []
    for r in sorted(dropped_rects, key=lambda r: r.y0):
        if spans and r.y0 - spans[-1][1] < 30.0:
            spans[-1][1] = max(spans[-1][1], r.y1)
        else:
            spans.append([r.y0, r.y1])
    return spans


def _is_math_font(font, font_bases):
    """True when a BT block's resource font is a math font (CambriaMath
    and kin): Hindi-sentence Latin is set in math fonts, figure labels
    are not. Unknown fonts (no mapping) are not math, so the (c4) gate
    stays conservative."""
    base = (font_bases or {}).get(font, "") or ""
    low = base.lower()
    return "cambria" in low or "math" in low or low.startswith("symbol")


def _delete_hindi_blocks(doc, page, dropped_rects, dropped_baselines,
                         hindi_fonts):
    """Delete Hindi BT blocks from the page's content streams, nothing
    else. A block is Hindi when its font is a Devanagari font (Mangal,
    Nirmala, ...) AND its dict line contains Devanagari -- the producer
    sets some Latin option values in Mangal, so font alone would delete
    them -- or when its baseline sits on a dropped Hindi line AND its x
    falls inside that line's rect -- the latter catches Hindi fraction
    digits set in CambriaMath and Latin digits interleaved in a Hindi
    sentence, while a Latin/math cell sitting beside Hindi on the same
    baseline (outside the Hindi rect) is kept. Baseline matching
    (tolerance 3pt, far below the ~10pt tightest line pitch) never
    touches a neighbouring Latin line, unlike rect redaction, whose
    line bboxes overlap adjacent lines and delete their fragments.
    Returns the number of blocks deleted."""
    height = page.rect.height
    deleted = 0
    # Resource name -> basefont, for the (c4) math-font gate: the
    # producer sets Hindi-sentence Latin (figure names, units,
    # fractions) in CambriaMath, while figure labels are Calibri.
    try:
        font_bases = {f[4]: f[3] for f in page.get_fonts()}
    except Exception:
        font_bases = {}
    # Dict lines (baseline, x-range, has-Devanagari, text, x-segments)
    # for the rules below: the producer sets some Latin option values
    # in Mangal (e.g. Geometry p1 option b) "15" is a Mangal,Bold hex
    # block), so font name alone must not delete a block. A
    # Devanagari-font block is Hindi only when its OWN span contains
    # Devanagari (a mixed line like the Geometry p24 header keeps its
    # Latin blocks and loses only the Devanagari one); when no span
    # matches the block, the old line-level rule is the fallback.
    # Segments split a line's spans on >25pt gaps, the same split
    # plan_text uses: a Latin cell sitting beside Hindi (a table
    # cell, a figure label) is a different segment and is kept.
    dict_lines = []
    dict_spans = []
    for blk in page.get_text("dict")["blocks"]:
        if blk.get("type") != 0:
            continue
        for ln in blk.get("lines", []):
            if not ln["spans"]:
                continue
            joined = "".join(s["text"] for s in ln["spans"])
            spans = sorted(
                ((s["bbox"][0], s["bbox"][2],
                  any(0x0900 <= ord(c) <= 0x097F for c in s["text"]))
                 for s in ln["spans"]),
                key=lambda t: t[0])
            for sx0, sx1, s_deva in spans:
                dict_spans.append((ln["spans"][0]["origin"][1],
                                   sx0, sx1, s_deva))
            segs = [[spans[0]]]
            for sp in spans[1:]:
                if sp[0] - segs[-1][-1][1] > 25.0:
                    segs.append([sp])
                else:
                    segs[-1].append(sp)
            seg_list = [(g[0][0], g[-1][1],
                         any(s[2] for s in g)) for g in segs]
            dict_lines.append((ln["spans"][0]["origin"][1],
                               ln["bbox"][0], ln["bbox"][2],
                               any(0x0900 <= ord(c) <= 0x097F
                                   for c in joined),
                               joined, seg_list))
    opt_bl = [bl for bl, x0, x1, hd, t, segs in dict_lines
              if t.replace(" ", "").replace("\u00a0", "")[:2]
              in ("a)", "b)", "c)", "d)", "A)", "B)", "C)", "D)")]
    first_opt = min(opt_bl) if opt_bl else None
    deva_bl = [bl for bl, x0, x1, hd, t, segs in dict_lines if hd]
    first_deva = min(deva_bl) if deva_bl else None
    # v21: table frames. A pure-Latin line sandwiched between Hindi
    # lines is a fragment -- unless it sits inside a table frame
    # (Statistics p18: "CLASS INTERVAL"/"FREQUENCY" are the header of
    # a bilingual table whose Hindi rows surround them, and the
    # v21: table grid zones (see _table_grid_zones). A Latin block
    # inside a grid zone is table content, not a Hindi fragment:
    # Stats p18's "CLASS INTERVAL"/"FREQUENCY" header was deleted
    # by the fragment rule (sandwiched between Hindi table rows).
    grid_zones = _table_grid_zones(page)
    # v21: font-name -> is-math-font, for the superscript rule below.
    font_math = {}
    for _xref, _ext, _typ, _base, _name, _enc in page.get_fonts():
        _b = (_base or "").lower()
        font_math[_name] = ("math" in _b or "cambria" in _b)
    for xref in page.get_contents():
        raw = doc.xref_stream(xref)
        if not raw:
            continue
        text = raw.decode("latin-1")
        out = []
        changed = False
        for is_block, seg in _split_bt_blocks(text):
            if not is_block:
                out.append(seg)
                continue
            mfont = re.search(r"/(\S+)\s+[\d.]+\s+Tf", seg)
            font = mfont.group(1) if mfont else ""
            mtm = re.search(
                r"([\d.\-]+)\s+([\d.\-]+)\s+([\d.\-]+)\s+([\d.\-]+)\s+"
                r"([\d.\-]+)\s+([\d.\-]+)\s+Tm", seg)
            mtd = re.search(r"([\d.\-]+)\s+([\d.\-]+)\s+TD", seg)
            if mtm:
                x, y_pdf = float(mtm.group(5)), float(mtm.group(6))
            elif mtd:
                x, y_pdf = float(mtd.group(1)), float(mtd.group(2))
            else:
                out.append(seg)
                continue
            baseline = height - y_pdf
            on_hindi_line = any(abs(baseline - b) < 3.0
                                for b in dropped_baselines)
            inside_hindi_rect = any(
                r.y0 - 2.0 <= baseline <= r.y1 + 2.0
                and r.x0 - 2.0 <= x <= r.x1 + 2.0
                for r in dropped_rects)
            line_deva = None
            line_idx = None
            for li, (bl, x0, x1, has_deva, t, segs) in enumerate(dict_lines):
                if abs(bl - baseline) < 2.0 and x0 - 2.0 <= x <= x1 + 2.0:
                    line_deva = has_deva
                    line_idx = li
                    break
            # The block's own span (nearest span start on its baseline)
            # decides the font rule for mixed lines, so a Latin-dominant
            # line keeps its Latin blocks and loses only Devanagari.
            span_deva = None
            best = None
            for so, sx0, sx1, s_deva in dict_spans:
                if abs(so - baseline) < 2.0 and sx0 - 2.0 <= x <= sx1 + 2.0:
                    d = abs(x - sx0)
                    if best is None or d < best:
                        best = d
                        span_deva = s_deva
            if span_deva is None:
                font_is_hindi = font in hindi_fonts and line_deva is not False
            else:
                font_is_hindi = font in hindi_fonts and span_deva
            # Hindi-translation fragments in the question area: Latin
            # blocks on a mixed Hindi line (same x-segment as the Hindi)
            # or pure-Latin lines sandwiched between two Hindi lines
            # in the same column (Geometry p7/p8/p15 stray red lines).
            # English question lines sit above the first Hindi line and
            # option/footer lines at or below the first option row, so
            # the question-area window excludes both. The window's
            # lower edge carries a 2pt tolerance: blocks on the first
            # Hindi line itself can sit a fraction BELOW first_deva
            # (the producer sets math tokens a hair lower than the
            # Hindi words on the same visual line, and first_deva
            # comes from the dict line's first span), which closed
            # the window on exactly those edge tokens (Geo p7's
            # "𝑷𝑸𝑹𝑺"/"𝑷𝑸=" at bl=83.06 vs first_deva=83.07).
            frag = False
            if line_idx is not None and first_opt is not None \
                    and first_deva is not None \
                    and first_deva - 2.0 <= baseline < first_opt:
                bl, x0, x1, has_deva, t, segs = dict_lines[line_idx]
                if has_deva:
                    for sx0, sx1, s_deva in segs:
                        if s_deva and sx0 <= x <= sx1:
                            frag = True
                            break
                    # v24: a Hindi sentence line's Latin EDGE tokens
                    # sit outside every Devanagari segment, so (c1)
                    # misses them: a figure name at the line start
                    # ("PQRS", "ABCD", "ΔABC") or a unit at its end
                    # ("(cm") survives as a lone red line between
                    # question and options (Geometry p5/p7/p8/p14).
                    # A mixed line with any real Hindi word on it is
                    # a Hindi sentence, so its Latin blocks go too.
                    # The threshold is 2 Devanagari chars, not 8: a
                    # math-heavy Hindi line carries only a couple
                    # ("∆ABC में AC=8.4cm और" has 5 in total across
                    # its split lines). A single stray Devanagari
                    # char (noise, not a word) does not trigger.
                    if not frag:
                        n_deva = sum(1 for c in t
                                     if 0x0900 <= ord(c) <= 0x097F)
                        if n_deva >= 2:
                            frag = True
                else:
                    # Neighbour baselines, aggregated: a baseline counts
                    # as Hindi if ANY dict line on it has Devanagari.
                    # The extractor splits one Hindi baseline into
                    # several dict lines (Geo p7's 116.69 baseline:
                    # Hindi segments plus a Latin "PR, QS" segment);
                    # sorting (baseline, has_deva) tuples put the Latin
                    # segment first, so above[0] wrongly read non-Hindi
                    # and the sandwiched fragment survived. The test
                    # looks for Hindi on BOTH sides within 40pt, not
                    # just at the nearest neighbour baselines: a Hindi
                    # fraction's numerator and denominator sit 8pt
                    # above/below their Hindi line, so each part's
                    # nearest neighbour is the OTHER part (non-Hindi),
                    # masking the Hindi lines beyond (M2D p19/p20:
                    # the Hindi "2/11" and "15√15/4" survived as
                    # stray fractions above the options). 40pt covers
                    # two line pitches each side, far inside the Hindi
                    # zone; the window and the x-overlap gate keep
                    # English question lines (no Hindi above them)
                    # and option lines (no Hindi below them) safe.
                    cand = [(b2, hd2) for (b2, x02, x12, hd2, t2, s2)
                            in dict_lines
                            if x02 - 2.0 <= x1 and x0 <= x12 + 2.0]
                    below_deva = any(hd2 for b2, hd2 in cand
                                     if bl - 40.0 <= b2 < bl - 2.0)
                    above_deva = any(hd2 for b2, hd2 in cand
                                     if bl + 2.0 < b2 <= bl + 40.0)
                    if below_deva and above_deva:
                        frag = True
                    # v24 (c4): a pure-Latin block ON a Hindi baseline.
                    # The extractor splits one Hindi baseline into
                    # several dict lines (Geometry p7 bl=83.1: the
                    # Hindi words are one line, the CambriaMath
                    # "𝑷𝑸𝑹𝑺" and "𝑷𝑸=" tokens are separate lines
                    # at the same baseline but non-overlapping x),
                    # so neither (c1) (own line is pure Latin) nor
                    # (c2) (neighbours in x-overlap are the English
                    # question above and the options below, not
                    # Hindi) catches them. A Latin block sharing a
                    # baseline with substantial Devanagari is part
                    # of that Hindi sentence, whatever its x. The
                    # window is 8pt, not 2pt: one visual line's blocks
                    # sit up to ~3pt apart in baseline (the redaction
                    # pass rewrites them), and a Hindi fraction's
                    # numerator/denominator sit 6-7pt above/below
                    # their Hindi line (M2D p20's "15√15/4" is the
                    # Hindi block's last line, so (c2) has no Hindi
                    # below it to sandwich against). No x-overlap
                    # gate: the Hindi words on p20's last Hindi line
                    # start at x349, 6-17pt right of the fraction
                    # (x316-343), and Geo p14's "∆ABC" (x345-381)
                    # sits left of its Hindi words (x398+) with no
                    # overlap either -- an x-gate broke p14. Instead
                    # the block must be set in a MATH font (Cambria-
                    # Math): Hindi-sentence Latin is math (figure
                    # names, units, fractions), while figure labels
                    # in the other column are Calibri, so they stay.
                    # The frag window (first_deva..first_opt)
                    # already excludes real question/option lines.
                    if not frag and _is_math_font(font, font_bases):
                        n_deva = sum(
                            1 for (b2, x02, x12, hd2, t2, s2)
                            in dict_lines
                            if abs(b2 - bl) < 8.0
                            for c in t2
                            if 0x0900 <= ord(c) <= 0x097F)
                        if n_deva >= 2:
                            frag = True
            if frag and any(gx0 <= x <= gx1
                            and gy0 <= baseline <= gy1
                            for gx0, gx1, gy0, gy1 in grid_zones):
                frag = False
            # v21: Hindi-formula math. A block is Hindi-formula
            # content (the English formula sits on its own line)
            # when it is an ORPHAN -- no dict line at its baseline,
            # because the extractor groups its glyphs into the Hindi
            # line below -- above a Hindi baseline by 3-15pt, with
            # its x inside that baseline's full x-range (math and
            # Hindi segments together: M3 p20's bl=202.8 baseline
            # splits into a math dict line plus Hindi "ka"/"maan"
            # lines, so neither a nearest-line test nor a single
            # Hindi segment's range catches the "2" superscripts),
            # and it is set in a math font (any length: the whole
            # "2+ 9v2" formula fragment) or short (<=4 units, a
            # raised superscript in any font). Both forms survived
            # every other rule -- non-Hindi font, outside the Deva
            # x-range, raised baseline. English math is safe: it
            # sits on its own dict line (not an orphan), and option
            # superscripts sit at or below the first option row
            # (outside the window). Units count literal chars plus
            # hex bytes (a 2-byte CID counts double).
            sup = False
            if first_opt is not None and first_deva is not None \
                    and first_deva - 2.0 <= baseline < first_opt:
                units = 0
                for sm in re.finditer(r"\((?:\\.|[^\\()])*\)", seg):
                    units += len(sm.group(0)) - 2
                for sm in re.finditer(r"<([0-9A-Fa-f]+)>", seg):
                    units += len(sm.group(1)) // 2
                orphan = not any(abs(bl2 - baseline) < 2.0
                                 for (bl2, x02, x12, hd2, t2, s2)
                                 in dict_lines)
                if orphan and (font_math.get(font, False)
                               or 0 < units <= 4):
                    for (bl2, x02, x12, hd2, t2, s2) in dict_lines:
                        if not hd2:
                            continue
                        if not 3.0 <= bl2 - baseline <= 15.0:
                            continue
                        xs0 = min(x0 for (b2, x0, x1, hd, t, s)
                                  in dict_lines
                                  if abs(b2 - bl2) < 2.0)
                        xs1 = max(x1 for (b2, x0, x1, hd, t, s)
                                  in dict_lines
                                  if abs(b2 - bl2) < 2.0)
                        if xs0 - 2.0 <= x <= xs1 + 2.0:
                            sup = True
                            break
            if font_is_hindi or frag or sup or \
                    (on_hindi_line and inside_hindi_rect):
                changed = True
                deleted += 1
                continue
            out.append(seg)
        if changed:
            doc.update_stream(xref, "".join(out).encode("latin-1"))
    return deleted


def _tokenize_stream(seg):
    """Split a non-BT content-stream segment into tokens:
    (kind, start, end, text), kind in number/name/string/hex/array/
    dict/op. Strings, hex, arrays and dicts are single tokens, so
    numbers inside them are never mistaken for coordinates."""
    toks = []
    i, n = 0, len(seg)
    while i < n:
        ch = seg[i]
        if ch in " \t\r\n\x00\x0c":
            i += 1
            continue
        if ch == "%":
            j = seg.find("\n", i)
            i = n if j < 0 else j + 1
            continue
        if ch == "(":
            j = i + 1
            depth = 1
            while j < n and depth:
                c = seg[j]
                if c == "\\":
                    j += 2
                    continue
                if c == "(":
                    depth += 1
                elif c == ")":
                    depth -= 1
                j += 1
            toks.append(("string", i, j, seg[i:j]))
            i = j
            continue
        if ch == "<":
            if i + 1 < n and seg[i + 1] == "<":
                j = i + 2
                depth = 1
                while j < n and depth:
                    if seg[j] == "(":
                        k = j + 1
                        d = 1
                        while k < n and d:
                            if seg[k] == "\\":
                                k += 2
                                continue
                            if seg[k] == "(":
                                d += 1
                            elif seg[k] == ")":
                                d -= 1
                            k += 1
                        j = k
                        continue
                    if seg[j] == "<" and j + 1 < n and seg[j + 1] == "<":
                        depth += 1
                        j += 2
                        continue
                    if seg[j] == ">" and j + 1 < n and seg[j + 1] == ">":
                        depth -= 1
                        j += 2
                        continue
                    j += 1
                toks.append(("dict", i, j, seg[i:j]))
                i = j
                continue
            j = seg.find(">", i)
            j = n if j < 0 else j + 1
            toks.append(("hex", i, j, seg[i:j]))
            i = j
            continue
        if ch == "[":
            j = i + 1
            depth = 1
            while j < n and depth:
                c = seg[j]
                if c == "(":
                    k = j + 1
                    d = 1
                    while k < n and d:
                        if seg[k] == "\\":
                            k += 2
                            continue
                        if seg[k] == "(":
                            d += 1
                        elif seg[k] == ")":
                            d -= 1
                        k += 1
                    j = k
                    continue
                if c == "[":
                    depth += 1
                elif c == "]":
                    depth -= 1
                j += 1
            toks.append(("array", i, j, seg[i:j]))
            i = j
            continue
        if ch == "/":
            j = i + 1
            while j < n and seg[j] not in " \t\r\n\x00\x0c[]<>(){}%/":
                j += 1
            toks.append(("name", i, j, seg[i:j]))
            i = j
            continue
        if ch.isdigit() or ch in "+-.":
            j = i
            if seg[j] in "+-":
                j += 1
            while j < n and (seg[j].isdigit() or seg[j] == "."):
                j += 1
            if j > i and seg[i:j] not in ("+", "-", ".", "+.", "-."):
                toks.append(("number", i, j, seg[i:j]))
                i = j
                continue
            toks.append(("op", i, i + 1, seg[i]))
            i += 1
            continue
        if ch.isalpha() or ch in "*'\"":
            j = i
            while j < n and (seg[j].isalpha() or seg[j] in "*'\""):
                j += 1
            toks.append(("op", i, j, seg[i:j]))
            i = j
            continue
        i += 1
    return toks


def _shift_drawings_in_segment(seg, zones):
    """Shift path ops that draw option graphics (fraction bars, root
    signs) up with their option lines. zones: (x0, x1, pdf_y0, pdf_y1,
    dy) in page coordinates, pdf_y0 <= pdf_y1. Only paths under an
    identity CTM are touched; anything inside a cm group is left alone.
    Returns (new_segment, number_of_ops_shifted).

    A path moves as a whole or not at all: it shifts when its bbox
    centre falls inside a zone, and then EVERY y operand of the path
    moves by that zone's dy. Shifting point-by-point (only the points
    inside a zone) stretched tall paths -- a root sign whose top sat
    in an option zone but whose bottom did not was pulled into a long
    vertical line down the page, and a root whose hook poked above
    its row's band never moved at all. Clip paths (ending in W/W*)
    never move, and neither do lone large rects (a full-page clip
    rect or a footer band overlaps every option zone, and shifting
    one mis-clips the page)."""
    toks = _tokenize_stream(seg)
    ctm_stack = []
    ctm_clean = True
    cur = None
    num_run = []
    edits = []
    in_path = False
    path_yops = []
    path_pts = []
    path_dirty = False
    path_lone_re = None

    def zone_for_centre(cx, cy):
        for (zx0, zx1, zy0, zy1, dy) in zones:
            if zx0 - 1.0 <= cx <= zx1 + 1.0 \
                    and zy0 - 1.0 <= cy <= zy1 + 1.0:
                return dy
        return None

    def fnum(tok):
        try:
            return float(tok[2])
        except ValueError:
            return None

    def close_path(paint):
        nonlocal in_path, path_yops, path_pts, path_dirty, path_lone_re
        if in_path and paint and not path_dirty and path_pts:
            ok = True
            if path_lone_re is not None:
                _x, _y, w, h = path_lone_re
                if w > 60.0 or h > 20.0:
                    ok = False
            if ok:
                xs = [p[0] for p in path_pts]
                ys = [p[1] for p in path_pts]
                dy = zone_for_centre((min(xs) + max(xs)) / 2.0,
                                     (min(ys) + max(ys)) / 2.0)
                if dy is not None:
                    for (s, e, v) in path_yops:
                        edits.append((s, e, f"{v + dy:.2f}"))
        in_path = False
        path_yops = []
        path_pts = []
        path_dirty = False
        path_lone_re = None

    for kind, s, e, txt in toks:
        if kind == "number":
            num_run.append((s, e, txt))
            continue
        if kind != "op":
            num_run = []
            continue
        op = txt
        nums = [fnum(t) for t in num_run]
        nums_ok = all(v is not None for v in nums)
        if op == "q":
            ctm_stack.append(ctm_clean)
        elif op == "Q":
            if ctm_stack:
                ctm_clean = ctm_stack.pop()
        elif op == "cm":
            if nums_ok and len(nums) == 6:
                ctm_clean = ctm_clean and nums == [1.0, 0.0, 0.0,
                                                   1.0, 0.0, 0.0]
                if in_path and not ctm_clean:
                    path_dirty = True
        elif op in ("m", "l") and nums_ok and len(nums) == 2:
            if op == "m" and not in_path:
                in_path = True
                path_yops = []
                path_pts = []
                path_dirty = not ctm_clean
                path_lone_re = None
            if in_path:
                path_pts.append((nums[0], nums[1]))
                path_yops.append((num_run[1][0], num_run[1][1],
                                  nums[1]))
            cur = (nums[0], nums[1])
        elif op == "c" and nums_ok and len(nums) == 6:
            if in_path:
                for k in (0, 2, 4):
                    path_pts.append((nums[k], nums[k + 1]))
                for k in (1, 3, 5):
                    path_yops.append((num_run[k][0], num_run[k][1],
                                      nums[k]))
            cur = (nums[4], nums[5])
        elif op == "v" and nums_ok and len(nums) == 4 \
                and cur is not None:
            if in_path:
                path_pts.append(cur)
                path_pts.append((nums[0], nums[1]))
                path_pts.append((nums[2], nums[3]))
                for k in (1, 3):
                    path_yops.append((num_run[k][0], num_run[k][1],
                                      nums[k]))
            cur = (nums[2], nums[3])
        elif op == "y" and nums_ok and len(nums) == 4 \
                and cur is not None:
            if in_path:
                path_pts.append((nums[0], nums[1]))
                path_pts.append((nums[2], nums[3]))
                path_pts.append(cur)
                for k in (1, 3):
                    path_yops.append((num_run[k][0], num_run[k][1],
                                      nums[k]))
            cur = (nums[2], nums[3])
        elif op == "re" and nums_ok and len(nums) == 4:
            if not in_path:
                in_path = True
                path_yops = []
                path_pts = []
                path_dirty = not ctm_clean
                path_lone_re = (nums[0], nums[1], nums[2], nums[3])
            else:
                path_lone_re = None
            path_pts.append((nums[0], nums[1]))
            path_pts.append((nums[0] + nums[2], nums[1] + nums[3]))
            path_yops.append((num_run[1][0], num_run[1][1], nums[1]))
        elif op in ("W", "W*"):
            close_path(False)
        elif op in ("S", "s", "f", "F", "f*", "B", "B*", "b", "b*"):
            close_path(True)
            cur = None
        elif op == "n":
            close_path(False)
            cur = None
        num_run = []
    if not edits:
        return seg, 0
    out = seg
    for s, e, new in sorted(edits, reverse=True):
        out = out[:s] + new + out[e:]
    return out, len(edits)


def _shift_option_drawings(doc, page, dropped_rects, remove_hindi):
    """Move option graphics (fraction bars, root signs) up by the same
    amount their option lines move. Text surgery shifts option text
    but never touches drawings, so bars/roots were left behind at the
    bottom of the page. Runs after Hindi deletion, before text surgery,
    so zones use original positions and the shift matches what surgery
    is about to apply to the text. Returns ops shifted."""
    if not remove_hindi or not dropped_rects:
        return 0
    height = page.rect.height
    spans = _merged_hindi_spans(dropped_rects)
    # Row zones (expanded a little: a fraction bar sits in the gap
    # between its numerator and denominator lines, overlapping neither
    # line's bbox, so unexpanded bands miss it), with the same capped
    # row shifts the text surgery is about to apply.
    _line_dy, zones = _row_shift_plan(_dict_line_info(page), spans,
                                      height, _table_grid_zones(page))
    if not zones:
        return 0
    shifted = 0
    for xref in page.get_contents():
        raw = doc.xref_stream(xref)
        if not raw:
            continue
        text = raw.decode("latin-1")
        out = []
        changed = False
        for is_block, seg in _split_bt_blocks(text):
            if is_block:
                out.append(seg)
                continue
            newseg, n = _shift_drawings_in_segment(seg, zones)
            if n:
                changed = True
                shifted += n
            out.append(newseg)
        if changed:
            doc.update_stream(xref, "".join(out).encode("latin-1"))
    return shifted


def _decode_literal(body):
    """Decode a PDF literal-string body (escapes, octal) to text."""
    out = []
    i = 0
    while i < len(body):
        c = body[i]
        if c == "\\" and i + 1 < len(body):
            nxt = body[i + 1]
            if nxt in "nrtbf":
                out.append({"n": "\n", "r": "\r", "t": "\t",
                            "b": "\b", "f": "\f"}[nxt])
                i += 2
                continue
            if nxt.isdigit():
                k = i + 1
                oct_digits = ""
                while k < len(body) and len(oct_digits) < 3 \
                        and body[k].isdigit():
                    oct_digits += body[k]
                    k += 1
                out.append(chr(int(oct_digits, 8) & 0xFF))
                i = k
                continue
            out.append(nxt)
            i += 2
            continue
        out.append(c)
        i += 1
    return "".join(out)


def _tj_advance(seg, font, size):
    """Advance (width) of a BT block's text, or None if it cannot be
    measured (multiple text ops, undecodable content)."""
    arrays = re.findall(r"\[((?:\\.|[^\\\]])*)\]\s*TJ", seg)
    singles = re.findall(r"\((?:\\.|[^\\()])*\)\s*Tj", seg)
    if len(arrays) + len(singles) != 1:
        return None
    try:
        if arrays:
            content = arrays[0]
            adv = 0.0
            i = 0
            while i < len(content):
                c = content[i]
                if c in " \t\r\n":
                    i += 1
                    continue
                if c == "(":
                    j = i + 1
                    depth = 1
                    while j < len(content) and depth:
                        cc = content[j]
                        if cc == "\\":
                            j += 2
                            continue
                        if cc == "(":
                            depth += 1
                        elif cc == ")":
                            depth -= 1
                        j += 1
                    adv += font.text_length(
                        _decode_literal(content[i + 1:j - 1]), size)
                    i = j
                    continue
                if c == "<":
                    j = content.find(">", i)
                    if j < 0:
                        return None
                    adv += font.text_length(
                        bytes.fromhex(content[i + 1:j]).decode("latin-1"),
                        size)
                    i = j + 1
                    continue
                if c.isdigit() or c in "+-.":
                    j = i
                    if content[j] in "+-":
                        j += 1
                    while j < len(content) and (content[j].isdigit()
                                                or content[j] == "."):
                        j += 1
                    adv -= float(content[i:j]) / 1000.0 * size
                    i = j
                    continue
                i += 1
            return adv
        m = re.match(r"\((.*)\)\s*Tj", singles[0], re.S)
        return font.text_length(_decode_literal(m.group(1)), size) \
            if m else None
    except Exception:
        return None


def _escape_literal(text):
    """Escape text for a PDF literal string (single-byte chars)."""
    out = []
    for ch in text:
        o = ord(ch)
        if ch in "\\()":
            out.append("\\" + ch)
        elif 32 <= o < 127:
            out.append(ch)
        else:
            out.append("\\%03o" % o)
    return "".join(out)


def _block_text_and_span(seg):
    """(text, kind, body_span, full_span) for a BT block with exactly
    one text op, else (None, None, None, None). Text concatenates all
    string elements of the TJ array (or the Tj string), decoded
    latin-1. body_span covers the text content (array interior or
    string body); full_span covers the whole text op."""
    arrays = list(re.finditer(r"\[((?:\\.|[^\\\]])*)\]\s*TJ", seg))
    singles = list(re.finditer(r"\((?:\\.|[^\\()])*\)\s*Tj", seg))
    if len(arrays) + len(singles) != 1:
        return None, None, None, None
    if arrays:
        m = arrays[0]
        content = m.group(1)
        parts = []
        i = 0
        while i < len(content):
            c = content[i]
            if c == "(":
                j = i + 1
                depth = 1
                while j < len(content) and depth:
                    cc = content[j]
                    if cc == "\\":
                        j += 2
                        continue
                    if cc == "(":
                        depth += 1
                    elif cc == ")":
                        depth -= 1
                    j += 1
                parts.append(_decode_literal(content[i + 1:j - 1]))
                i = j
            elif c == "<":
                j = content.find(">", i)
                if j < 0:
                    return None, None, None, None
                try:
                    parts.append(bytes.fromhex(content[i + 1:j]).decode(
                        "latin-1"))
                except Exception:
                    return None, None, None, None
                i = j + 1
            else:
                i += 1
        return "".join(parts), "TJ", (m.start(1), m.end(1)), \
            (m.start(), m.end())
    m = singles[0]
    inner = re.match(r"(\((?:\\.|[^\\()])*\))\s*Tj", m.group(0))
    if not inner:
        return None, None, None, None
    body = inner.group(1)[1:-1]
    return _decode_literal(body), "Tj", \
        (m.start() + 1, m.start() + 1 + len(body)), \
        (m.start(), m.end())


def _boundary_space(t1, t2, prev_char=None):
    """True when two adjacent fragments take a word space between
    them, False when the second attaches to the first with no space:
    number continuations ("1" + "16.8" = 116.8), attaching punctuation
    ("20" + "%", "(" + "in", "150-" + "155"). Attaching (instead of
    breaking the run) is what keeps a whole line in one merged block:
    a run broken mid-line reflows its parts against fixed-position
    neighbours and the parts collide (DI p1 "31,5000, then what"
    overlapped "What" in v10). A lone period is a decimal point only
    when the character before it is a digit ("16" + "." + "66");
    after a word it ends a sentence ("cube" + "." + "20" takes a
    space). A colon before a digit is a ratio ("1:3"); before a
    letter it takes a space."""
    if not t1 or not t2:
        return False
    a, b = t1[-1], t2[0]
    if t1 == ".":
        # A lone period is a decimal point only between two digits
        # ("16" + "." + "66"); after a digit but before a word it ends
        # a sentence ("9:5" + "." + "If" takes a space).
        if prev_char is None:
            return False
        return not (prev_char.isdigit() and b.isdigit())
    # A lone degree sign is a suffix, not a prefix: "85°" + "and"
    # takes a space; only a unit letter ("90°C") stays joined.
    # Punctuation after ° falls through to the rules below.
    if t1 == "°":
        if t2 in ("C", "F", "K"):
            return False
        if b.isalnum():
            return True
    # Symbol prefix: ΔABC, ∠B, αx, √36, πr, 90°C stay joined; a
    # symbol before an operator ("θ =") still takes a space.
    if t1 in "Δ∠αβγδεθλμπρσφωΣ√∞°" and (b.isalnum() or b in
                                        "Δ∠αβγδεθλμπρσφωΣ√∞°"):
        return False
    if a.isdigit() and (b.isdigit() or b == "."):
        return False
    if a == "." and b.isdigit():
        return not (len(t1) <= 2 or t1[-2].isdigit())
    if a == ":":
        return not b.isdigit()
    if b in "%,.)?!:;-/°":
        return False
    if a in "(-/":
        return False
    return True


def _merge_word_spaces(doc, page):
    """Merge adjacent word blocks on question/option lines into one
    text op with a real space between the words, so Canva keeps the
    word break when it re-flows the text. Word gaps on these pages are
    implicit position gaps between BT blocks (no space glyph), and
    Canva drops them when concatenating blocks ("valuein(incm)ofBD?").
    A trailing space appended to a block did not survive Canva's
    import, so the space is put in the MIDDLE of a merged block
    instead, and the following blocks are emptied. The merged array
    holds ONLY strings and space glyphs, no TJ numbers: a gap is
    rendered as one or more space glyphs (count rounded from the
    source gap), since positioned offsets inside a merged array are
    what Canva mishandles. Runs break at number continuations
    ("1" + "16.8" stays 116.8, never "1 16.8") and at attaching
    punctuation. Runs MAY span fonts: an Identity-H math fragment
    sitting between two word blocks (e.g. "year 2015 is", where the
    year is CambriaMath) is merged with font switches, the space
    glyphs written in a neighbouring single-byte font that really
    has one, and the math fragment's TJ array copied verbatim.
    Only question/option lines are touched; table and figure
    text keeps its original blocks. Returns runs merged."""
    height = page.rect.height
    lines = _dict_line_info(page)
    fonts = {}
    base_names = {}
    for f in page.get_fonts():
        base_names[f[4]] = f[3].split("+", 1)[-1]
        try:
            parts = doc.extract_font(f[0])
            fonts[f[4]] = (pymupdf.Font(fontbuffer=parts[3])
                           if parts and parts[3] else None,
                           f[5] == "Identity-H")
        except Exception:
            fonts[f[4]] = (None, f[5] == "Identity-H")
    # Dict spans, for measuring Identity-H fragments (font.text_length
    # mis-measures 2-byte CID text) and for recognising space-only
    # blocks. Baseline lies inside the span bbox vertically.
    spans = []
    try:
        for blk in page.get_text("dict")["blocks"]:
            if blk.get("type") != 0:
                continue
            for ln in blk["lines"]:
                for sp in ln["spans"]:
                    spans.append(sp)
    except Exception:
        spans = []

    def match_span(baseline, x, fname_base):
        # A block matches the span that CONTAINS its x, not one that
        # starts at it: the extractor groups adjacent same-font
        # fragments into one span ("31,5000," is one span), so
        # matching by span start paired the "31" block with the whole
        # "31,5000," span and its 50pt width, which let the merge
        # jump over the comma and "5000" blocks and collide with
        # them. Among containing spans take the latest-starting.
        best = None
        for sp in spans:
            bb = sp["bbox"]
            if sp["font"] != fname_base:
                continue
            if not (bb[1] - 2.0 <= baseline <= bb[3] + 2.0):
                continue
            if not (bb[0] - 2.0 <= x <= bb[2] + 2.0):
                continue
            if best is None or bb[0] > best[1]["bbox"][0]:
                best = (bb[0], sp)
        return best[1] if best else None

    def has_space(r):
        if r["ident"]:
            return False
        try:
            return r["font"].has_glyph(0x20) not in (0, None)
        except Exception:
            return False

    def match_line(baseline, x):
        best = None
        for li, ln in enumerate(lines):
            if abs(ln[0] - baseline) <= 18.0 \
                    and ln[4] - 10.0 <= x <= ln[5] + 10.0:
                if best is None or abs(ln[0] - baseline) < best[0]:
                    best = (abs(ln[0] - baseline), li)
        if best is None:
            return None
        ln = lines[best[1]]
        return ln if (ln[1] or ln[3]) else None

    merged = 0
    for xref in page.get_contents():
        raw = doc.xref_stream(xref)
        if not raw:
            continue
        text = raw.decode("latin-1")
        segs = _split_bt_blocks(text)
        recs = []
        all_blocks = []
        space_blocks = []
        for idx, (is_block, seg) in enumerate(segs):
            if not is_block:
                continue
            mf = re.search(r"/(\S+)\s+([\d.]+)\s+Tf", seg)
            mtm = re.search(
                r"([\d.\-]+)\s+([\d.\-]+)\s+([\d.\-]+)\s+([\d.\-]+)\s+"
                r"([\d.\-]+)\s+([\d.\-]+)\s+Tm", seg)
            mtd = re.search(r"([\d.\-]+)\s+([\d.\-]+)\s+TD", seg)
            if mtm:
                x, y_pdf = float(mtm.group(5)), float(mtm.group(6))
            elif mtd:
                x, y_pdf = float(mtd.group(1)), float(mtd.group(2))
            else:
                continue
            baseline = height - y_pdf
            all_blocks.append((baseline, x, False))
            if not mf or mf.group(1) not in fonts:
                continue
            font, ident = fonts[mf.group(1)]
            if font is None:
                continue
            size = float(mf.group(2))
            t, _kind, bspan, fspan = _block_text_and_span(seg)
            if t is None or fspan is None:
                continue
            mtc = re.search(r"([\d.\-]+)\s+Tc", seg)
            tc = float(mtc.group(1)) if mtc else 0.0
            mgs = re.search(r"/(\S+)\s+gs", seg)
            gs = mgs.group(1) if mgs else None
            fname = mf.group(1)
            if ident:
                sp = match_span(baseline, x, base_names.get(fname, ""))
                if sp is None:
                    continue
                op = seg[fspan[0]:fspan[1]]
                # Count CIDs in hex groups AND literal groups:
                # apply_redactions rewrites some hex strings as
                # literals with the same bytes ("<0D4C>" becomes
                # "(\rL)"), and counting hex only skipped the "="
                # block, shifting every later slice by one and
                # merging "4" + "cm" with no space (Geo p14).
                cid_count = sum(len(g) // 4 for g in
                                re.findall(r"<([0-9A-Fa-f]+)>", op))
                j = 0
                while j < len(op):
                    c = op[j]
                    if c == "(":
                        k = j + 1
                        depth = 1
                        while k < len(op) and depth:
                            cc = op[k]
                            if cc == "\\":
                                k += 2
                                continue
                            if cc == "(":
                                depth += 1
                            elif cc == ")":
                                depth -= 1
                            k += 1
                        cid_count += len(
                            _decode_literal(op[j + 1:k - 1])) // 2
                        j = k
                    else:
                        j += 1
                if cid_count == 0:
                    continue
                if match_line(baseline, x) is None:
                    continue
                # Text and advance are assigned after sorting, by
                # slicing the span's text per CID count in x order
                # (see below); the span groups several blocks.
                recs.append({"idx": idx, "seg": seg, "baseline": baseline,
                             "x": x, "end": x, "text": "", "font": font,
                             "size": size, "bspan": bspan, "fspan": fspan,
                             "fname": fname, "tc": tc, "gs": gs,
                             "ident": True, "op": op, "span": sp,
                             "cid_count": cid_count})
            else:
                if not t.strip():
                    all_blocks[-1] = (baseline, x, True)
                    space_blocks.append((idx, seg, bspan, baseline, x))
                    continue
                adv = _tj_advance(seg, font, size)
                if adv is None:
                    continue
                if match_line(baseline, x) is None:
                    continue
                recs.append({"idx": idx, "seg": seg, "baseline": baseline,
                             "x": x, "end": x + adv, "text": t,
                             "font": font, "size": size,
                             "bspan": bspan, "fspan": fspan, "fname": fname,
                             "tc": tc, "gs": gs, "ident": False,
                             "op": None})
        recs.sort(key=lambda r: (r["baseline"], r["x"]))
        # Slice grouped Identity-H spans per block, in x order:
        # each block's CIDs map to that many characters of the span
        # text, and font.text_length on the real Unicode slice gives
        # the true advance (it mis-measures raw CID bytes).
        span_off = {}
        span_txt = {}
        keep = []
        for r in recs:
            if not r["ident"]:
                keep.append(r)
                continue
            sp = r["span"]
            # The extractor inserts spaces between words in span
            # text ("1: 3. 12. 5%") that the CID stream does not
            # have; slicing must use the space-free text or every
            # block after the first space is mis-sliced.
            if id(sp) not in span_txt:
                span_txt[id(sp)] = sp["text"].replace(" ", "")
            st = span_txt[id(sp)]
            off = span_off.get(id(sp), 0)
            cnt = r["cid_count"]
            span_off[id(sp)] = off + cnt
            if off + cnt > len(st):
                continue
            r["text"] = st[off:off + cnt]
            if not r["text"].strip():
                for k2, (bl, bx, ws) in enumerate(all_blocks):
                    if abs(bl - r["baseline"]) <= 1.0 \
                            and abs(bx - r["x"]) <= 0.5:
                        all_blocks[k2] = (bl, bx, True)
                space_blocks.append((r["idx"], r["seg"], r["bspan"],
                                     r["baseline"], r["x"]))
                continue
            try:
                adv = r["font"].text_length(r["text"], r["size"])
            except Exception:
                adv = None
            if not adv:
                continue
            r["end"] = r["x"] + adv
            keep.append(r)
        recs = keep
        edits = {}
        i = 0
        while i < len(recs):
            run = [recs[i]]
            run_sp = []
            j = i + 1
            while j < len(recs):
                prev, cur = run[-1], recs[j]
                gap = cur["x"] - prev["end"]
                prev_char = run[-2]["text"][-1] if len(run) >= 2 else None
                sp = _boundary_space(prev["text"], cur["text"], prev_char)
                # Thousand separator with no real gap in the source
                # ("31,5000", "65,520"): attach, do not space. With a
                # real word gap it is a list separator ("3, 5") and
                # keeps its space.
                if sp and prev["text"].endswith(",") \
                        and cur["text"][:1].isdigit() and gap < 2.0:
                    sp = False
                if abs(cur["baseline"] - run[0]["baseline"]) > 2.0 \
                        or abs(cur["baseline"] - prev["baseline"]) > 1.0 \
                        or cur["gs"] != prev["gs"] \
                        or gap > 20.0 \
                        or (sp and gap < 2.0) \
                        or ((not sp) and gap < -2.0) \
                        or ((not sp) and gap > 2.0):
                    # Attaching segments the producer spaced apart
                    # (a root sign's gap, a ratio's spaced segments)
                    # compresses the line and misaligns its drawings
                    # (Geometry p5 b)/c) roots, p31 trailing roots):
                    # break the run and leave both at source positions.
                    break
                blocked = any(
                    abs(bl - prev["baseline"]) <= 1.0
                    and prev["end"] - 0.5 <= bx < cur["x"]
                    and not ws
                    for bl, bx, ws in all_blocks)
                if blocked:
                    break
                run.append(cur)
                run_sp.append(sp)
                j += 1
            if len(run) >= 2:
                first = run[0]
                if any(r["ident"] for r in run):
                    # Mixed-font run: rebuild the first block with
                    # font/Tc switches and verbatim math ops. A
                    # boundary with no single-byte side carrying a
                    # real space glyph cannot be spaced, so the run
                    # is left unmerged rather than joined.
                    src_span = run[-1]["end"] - run[0]["x"]
                    ops = []
                    cur_fname, cur_size = first["fname"], first["size"]
                    cur_tc = first["tc"]
                    ok = True
                    reflow_act = 0.0
                    sb_all = [q for q, r in enumerate(run)
                              if (not r["ident"]) and has_space(r)]
                    for k, r in enumerate(run):
                        if k and run_sp[k - 1]:
                            prev = run[k - 1]
                            # Gap-preserving spaces, as in the
                            # single-font path: one space glyph per
                            # ~space width of source gap, so a wide
                            # source gap ("In" to "Δ", 12pt) does not
                            # collapse and shift everything after
                            # it (a partial run's neighbours stay
                            # at source positions).
                            gapk = r["x"] - prev["end"]
                            sfont = None
                            if (not r["ident"]) and has_space(r):
                                sfont = r
                            elif (not prev["ident"]) and has_space(prev):
                                sfont = prev
                            elif sb_all:
                                q = min(sb_all, key=lambda q: abs(q - k))
                                sfont = run[q]
                            if sfont is None:
                                ok = False
                                break
                            try:
                                sw = sfont["font"].text_length(
                                    " ", sfont["size"])
                            except Exception:
                                sw = sfont["size"] * 0.25
                            n_sp = max(1, min(8, round(gapk / sw))) \
                                if sw > 0 else 1
                            if (sfont["fname"], sfont["size"]) != \
                                    (cur_fname, cur_size):
                                ops.append("/%s %s Tf"
                                           % (sfont["fname"],
                                              "%g" % sfont["size"]))
                                cur_fname, cur_size = \
                                    sfont["fname"], sfont["size"]
                            ops.append("(" + " " * n_sp + ") Tj")
                            reflow_act += n_sp * sw
                        reflow_act += r["end"] - r["x"]
                        if (r["fname"], r["size"]) != (cur_fname, cur_size):
                            ops.append("/%s %s Tf"
                                       % (r["fname"], "%g" % r["size"]))
                            cur_fname, cur_size = r["fname"], r["size"]
                        if r["tc"] != cur_tc:
                            ops.append("%s Tc" % ("%g" % r["tc"]))
                            cur_tc = r["tc"]
                        if r["ident"]:
                            ops.append(r["op"])
                        else:
                            ops.append("[(" + _escape_literal(r["text"])
                                       + ")] TJ")
                    if not ok:
                        i = j
                        continue
                    if reflow_act > src_span + 4.0:
                        i = j
                        continue
                    if j < len(recs) \
                            and abs(recs[j]["baseline"]
                                    - run[0]["baseline"]) <= 2.0 \
                            and run[0]["x"] + reflow_act \
                            > recs[j]["x"] + 2.0:
                        i = j
                        continue
                    fspan = first["fspan"]
                    edits[first["idx"]] = first["seg"][:fspan[0]] \
                        + " ".join(ops) + first["seg"][fspan[1]:]
                    for r in run[1:]:
                        bspan = r["bspan"]
                        edits[r["idx"]] = r["seg"][:bspan[0]] \
                            + r["seg"][bspan[1]:]
                    merged += 1
                else:
                    # Single-font run (proven path), now number-free:
                    # each gap is one or more space glyphs, rounded
                    # from the source gap, never a TJ offset.
                    try:
                        space_w = first["font"].text_length(
                            " ", first["size"])
                    except Exception:
                        space_w = first["size"] * 0.25
                    n_sps = []
                    for k in range(1, len(run)):
                        if not run_sp[k - 1]:
                            n_sps.append(0)
                            continue
                        gap = run[k]["x"] - run[k - 1]["end"]
                        n_sps.append(max(1, min(6, round(gap / space_w))))
                    src_span = run[-1]["end"] - run[0]["x"]
                    reflow = sum(r["end"] - r["x"] for r in run) \
                        + sum(n * space_w for n in n_sps)
                    if reflow > src_span + 2.0:
                        i = j
                        continue
                    if j < len(recs) \
                            and abs(recs[j]["baseline"]
                                    - run[0]["baseline"]) <= 2.0 \
                            and run[0]["x"] + reflow > recs[j]["x"] + 1.0:
                        i = j
                        continue
                    parts = ["[(" + _escape_literal(first["text"]) + ")"]
                    for k in range(1, len(run)):
                        if n_sps[k - 1]:
                            parts.append("(" + " " * n_sps[k - 1] + ")")
                        parts.append("(" + _escape_literal(run[k]["text"])
                                     + ")")
                    parts.append("] TJ")
                    fspan = first["fspan"]
                    edits[first["idx"]] = first["seg"][:fspan[0]] \
                        + " ".join(parts) + first["seg"][fspan[1]:]
                    for r in run[1:]:
                        bspan = r["bspan"]
                        edits[r["idx"]] = r["seg"][:bspan[0]] \
                            + r["seg"][bspan[1]:]
                    merged += 1
                # v23: a producer that emits standalone space blocks
                # (Statistics) leaves them behind when a run merges
                # over them: the merged TJ already carries its own
                # space glyphs, so a leftover block doubles the gap
                # -- and, as the merged line shrinks against the
                # source positions, the leftover block drifts INTO a
                # word, which Canva renders as a split ("h eight",
                # "d istribution"). Empty every space block clearly
                # inside the run's source span; blocks at the span's
                # edges are separators to neighbouring runs and stay.
                for (sidx, sseg, sbspan, sbl, sx) in space_blocks:
                    if abs(sbl - run[0]["baseline"]) <= 1.0 \
                            and run[0]["x"] + 1.0 < sx \
                            < run[-1]["end"] - 1.0:
                        edits[sidx] = sseg[:sbspan[0]] \
                            + sseg[sbspan[1]:]
            i = j
        if edits:
            out = []
            for idx, (is_block, seg) in enumerate(segs):
                out.append(edits.get(idx, seg))
            doc.update_stream(xref, "".join(out).encode("latin-1"))
    return merged


def _surgery_page(doc, page, dropped_rects, remove_hindi):
    """Shift lines below removed Hindi blocks up, recolor question red /
    options black, by editing only BT blocks in the page's content
    streams. Every other stream byte is preserved. Returns the number of
    BT blocks edited. A block that matches no dict line is left alone,
    except for the explicit-space fix below."""
    height = page.rect.height
    blocks = []
    if remove_hindi and dropped_rects:
        for r in sorted(dropped_rects, key=lambda r: r.y0):
            if blocks and r.y0 - blocks[-1][1] < 30.0:
                blocks[-1][1] = max(blocks[-1][1], r.y1)
            else:
                blocks.append([r.y0, r.y1])

    def shift_for(baseline_topdown):
        return sum(b1 - b0 for b0, b1 in blocks if b1 <= baseline_topdown)

    lines = _dict_line_info(page)
    edited = 0
    height = page.rect.height
    blocks = []
    if remove_hindi and dropped_rects:
        for r in sorted(dropped_rects, key=lambda r: r.y0):
            if blocks and r.y0 - blocks[-1][1] < 30.0:
                blocks[-1][1] = max(blocks[-1][1], r.y1)
            else:
                blocks.append([r.y0, r.y1])

    def shift_for(baseline_topdown):
        return sum(b1 - b0 for b0, b1 in blocks if b1 <= baseline_topdown)

    lines = _dict_line_info(page)
    line_dy, _zones = _row_shift_plan(lines, blocks, height,
                                      _table_grid_zones(page))
    edited = 0
    for xref in page.get_contents():
        raw = doc.xref_stream(xref)
        if not raw:
            continue
        text = raw.decode("latin-1")
        out = []
        changed = False
        for is_block, seg in _split_bt_blocks(text):
            if not is_block:
                out.append(seg)
                continue
            mtm = re.search(
                r"([\d.\-]+)\s+([\d.\-]+)\s+([\d.\-]+)\s+([\d.\-]+)\s+"
                r"([\d.\-]+)\s+([\d.\-]+)\s+Tm", seg)
            mtd = re.search(r"([\d.\-]+)\s+([\d.\-]+)\s+TD", seg)
            if mtm:
                x, y_pdf = float(mtm.group(5)), float(mtm.group(6))
                pos_fmt = "Tm"
            elif mtd:
                x, y_pdf = float(mtd.group(1)), float(mtd.group(2))
                pos_fmt = "TD"
            else:
                out.append(seg)
                continue
            baseline = height - y_pdf
            # Match the block to its dict line by nearest baseline
            # within 18pt with an overlapping x-range. Exact-baseline
            # matching (< 1pt) orphaned superscript blocks, whose
            # raised baseline sits between dict lines, and left them
            # behind when their option moved.
            match = None
            best = None
            for li, ln in enumerate(lines):
                if abs(ln[0] - baseline) <= 18.0 \
                        and ln[4] - 10.0 <= x <= ln[5] + 10.0:
                    if best is None or abs(ln[0] - baseline) < best[0]:
                        best = (abs(ln[0] - baseline), li)
            if best is not None:
                ln = lines[best[1]]
                match = (ln[1], ln[2], ln[3], best[1])
            if match is None:
                out.append(seg)
                continue
            is_opt, is_foot, is_q, li = match
            newseg = seg
            if not is_foot:
                # Shift ONLY option rows, each by its row's single
                # capped shift (see _row_shift_plan). Shifting every
                # line below the Hindi block moved figure labels
                # (pie-chart labels) and table rows away from their
                # unshifted graphics -- and shifted labels fell outside
                # their clip rect and vanished entirely. Question lines
                # sit above Hindi and need no shift; figures and tables
                # must stay aligned with their drawings, which never
                # move.
                # v21: shift by the row plan, not by the option
                # flag. _row_shift_plan already decides what moves
                # (row members + continuations); gating on is_opt
                # stranded below-last-label continuations (Stats p4's
                # "between 90-100": in the shift plan with dy=81, but
                # unpromoted by _dict_line_info, so it stayed behind
                # while its sentence moved up).
                dy = line_dy.get(li, 0.0)
                if dy:
                    if pos_fmt == "Tm":
                        m2 = re.search(
                            r"([\d.\-]+)\s+([\d.\-]+)\s+([\d.\-]+)\s+"
                            r"([\d.\-]+)\s+([\d.\-]+)\s+([\d.\-]+)\s+Tm",
                            newseg)
                        if m2 and abs(float(m2.group(1)) - 1) < 0.01 \
                                and abs(float(m2.group(2))) < 0.01 \
                                and abs(float(m2.group(3))) < 0.01 \
                                and abs(float(m2.group(4)) - 1) < 0.01:
                            newseg = newseg[:m2.start(6)] + \
                                f"{float(m2.group(6)) + dy:.2f}" + \
                                newseg[m2.end(6):]
                    else:
                        m2 = re.search(r"([\d.\-]+)\s+([\d.\-]+)\s+TD",
                                        newseg)
                        if m2:
                            newseg = newseg[:m2.start(2)] + \
                                f"{float(m2.group(2)) + dy:.2f}" + \
                                newseg[m2.end(2):]
                # Recolour ONLY question (red) and option (black) lines.
                # Table rows, figure labels and attributions keep their
                # original colour: painting them red made DI table
                # numbers red, which is not what was asked for.
                if is_opt:
                    r, g, b = 0.0, 0.0, 0.0
                elif is_q:
                    r, g, b = 1.0, 0.0, 0.0
                else:
                    r = None
                if r is not None:
                    newseg2 = re.sub(r"[\d.]+\s+[\d.]+\s+[\d.]+\s+rg\b",
                                     f"{r:g} {g:g} {b:g} rg", newseg)
                    newseg2 = re.sub(r"[\d.]+\s+[\d.]+\s+[\d.]+\s+RG\b",
                                     f"{r:g} {g:g} {b:g} RG", newseg2)
                    newseg2 = re.sub(r"(?<![\d.\w])[\d.]+\s+g\b",
                                     f"{r:g} {g:g} {b:g} rg", newseg2)
                    newseg2 = re.sub(r"(?<![\d.\w])[\d.]+\s+G\b",
                                     f"{r:g} {g:g} {b:g} RG", newseg2)
                    # Colours set through a colourspec (sc/SC) instead
                    # of rg/RG: a grey "1" set with "0.54 sc" survived
                    # as grey because no rg/RG was present to replace.
                    newseg2 = re.sub(
                        r"[\d.]+\s+[\d.]+\s+[\d.]+\s+sc\b",
                        f"{r:g} {g:g} {b:g} rg", newseg2)
                    newseg2 = re.sub(
                        r"[\d.]+\s+[\d.]+\s+[\d.]+\s+SC\b",
                        f"{r:g} {g:g} {b:g} RG", newseg2)
                    newseg2 = re.sub(r"(?<![\d.\w])[\d.]+\s+sc\b",
                                     f"{r:g} {g:g} {b:g} rg", newseg2)
                    newseg2 = re.sub(r"(?<![\d.\w])[\d.]+\s+SC\b",
                                     f"{r:g} {g:g} {b:g} RG", newseg2)
                    # Most blocks set NO colour at all: they inherit the
                    # colour of a previous block (the producer sets blue
                    # once and only re-sets it on change). Replacing
                    # nothing leaves them blue, so insert the target
                    # colour when absent. Fill and stroke are checked
                    # INDEPENDENTLY: a block that sets only its stroke
                    # colour (RG) but inherits its fill stayed grey
                    # after the stroke was replaced, because the old
                    # combined check saw the new RG and skipped the
                    # fill insert. (Checked by the exact target
                    # string, not a bare "rg", which could occur inside
                    # a text string.)
                    target_rg = f"{r:g} {g:g} {b:g} rg"
                    target_RG = f"{r:g} {g:g} {b:g} RG"
                    inserts = []
                    if target_rg not in newseg2:
                        inserts.append(target_rg)
                    if target_RG not in newseg2:
                        inserts.append(target_RG)
                    if inserts:
                        # Insert immediately before the first text op,
                        # not after BT: a colour op the block sets
                        # itself (e.g. an scn pattern colour) sits
                        # between BT and the text and would override
                        # an insert placed at BT.
                        mtxt = re.search(
                            r"\[((?:\\.|[^\\\]])*)\]\s*TJ"
                            r"|\((?:\\.|[^\\()])*\)\s*Tj", newseg2)
                        if mtxt:
                            # Leading space is essential: the text op
                            # often follows TD/Tm with no delimiter
                            # ("TD[...]"), and inserting "1 0 0 rg"
                            # directly after it makes "TD1", an
                            # unknown operator, so the fill stayed
                            # black while the stroke turned red.
                            newseg2 = newseg2[:mtxt.start()] + \
                                " " + " ".join(inserts) + " " + \
                                newseg2[mtxt.start():]
                        else:
                            newseg2 = newseg2.replace(
                                "BT", "BT " + " ".join(inserts), 1)
                    if newseg2 != newseg:
                        newseg = newseg2
                if newseg != seg:
                    changed = True
                    edited += 1
            out.append(newseg)
        if changed:
            doc.update_stream(xref, "".join(out).encode("latin-1"))
    # Word spaces: after shifting and recolouring, merge adjacent word
    # blocks on question/option lines into one text op with a real
    # space between them (see _merge_word_spaces).
    try:
        edited += _merge_word_spaces(doc, page)
    except Exception:
        pass
    return edited


def clean_pdf(input_path: str, output_path: str,
              remove_ink: bool = True, remove_marks: bool = True,
              remove_hindi: bool = True):
    src_doc = pymupdf.open(input_path)
    out_doc = pymupdf.open(input_path)
    classifier = ImageClassifier(src_doc)
    stats = {"pages": len(src_doc), "removed": 0, "removed_images": 0,
             "latin_lines": 0, "hindi_rasters": 0, "images_kept": 0,
             "hindi_removed": 0}

    for pno in range(len(src_doc)):
        src_page = src_doc[pno]
        page = out_doc[pno]

        latin_jobs, raster_rects, hindi_removed, dropped_rects, \
            dropped_baselines = plan_text(
                src_page, remove_hindi=remove_hindi)
        stats["hindi_removed"] += hindi_removed
        # Font resource names for the Hindi-deletion rule: Devanagari
        # basefonts (from spans that actually contain Devanagari).
        # Hindi fonts are identified by NAME (Mangal, Nirmala, ...), not
        # by span association. The old span-based rule promoted
        # CambriaMath to a "Hindi font" whenever a mixed span carried
        # Devanagari, and then deleted every CambriaMath block on the
        # page -- including standalone English math values such as
        # Geometry p1 option b) "15", which Canva then showed as a
        # blank. Hindi-line CambriaMath digits are still caught by the
        # baseline+rect rule in _delete_hindi_blocks, which is what
        # that rule is for.
        hindi_fonts = set()
        for f in src_page.get_fonts():
            basefont, name = f[3], f[4]
            stripped = basefont.split("+", 1)[-1] if "+" in basefont \
                else basefont
            low = stripped.lower()
            if any(k in low for k in ("mangal", "nirmala", "devanagari",
                                      "kokila", "utsaah", "aparajita")):
                hindi_fonts.add(name)
        # A page can hold Hindi in a Latin-dominant line (the Geometry
        # p24 header) with no dropped Hindi baseline at all; the
        # deletion pass must still run for it, or its Devanagari block
        # survives. Pages with no Devanagari at all keep the old skip.
        page_has_deva = False
        for blk in src_page.get_text("dict")["blocks"]:
            if blk.get("type") != 0:
                continue
            for ln in blk.get("lines", []):
                for s in ln.get("spans", []):
                    if any(0x0900 <= ord(c) <= 0x097F for c in s["text"]):
                        page_has_deva = True
                        break
        drawings = src_page.get_drawings()
        # Hand-drawn figures share the pen with annotations; exempt
        # large ink groups (and all ink on text-free pages) so figures
        # survive (see _ink_figure_rects).
        keep_ink_rects = _ink_figure_rects(drawings, src_page) \
            if remove_ink else []
        # Small marks inside a dropped Hindi row (a fraction bar, a dot)
        # belong to the Hindi text; leave them and they survive as stray
        # red dashes. Only small marks are eligible, never large art.
        def _hindi_mark(d):
            if not remove_hindi or not dropped_rects:
                return False
            r = d["rect"]
            if r.get_area() > 400:
                return False
            cx, cy = (r.x0 + r.x1) / 2, (r.y0 + r.y1) / 2
            return any(hr.x0 - 2 <= cx <= hr.x1 + 2
                       and hr.y0 - 2 <= cy <= hr.y1 + 2
                       for hr in dropped_rects)
        bad_drawings = [d for d in drawings
                        if should_remove_drawing(d, remove_ink, remove_marks,
                                                 keep_ink_rects)
                        or _hindi_mark(d)]
        kept_drawings = [d for d in drawings
                         if not should_remove_drawing(d, remove_ink,
                                                      remove_marks,
                                                      keep_ink_rects)
                         and not _hindi_mark(d)]

        bad_images = []
        seen_xref = set()
        for img in src_page.get_images(full=True):
            xref = img[0]
            if xref in seen_xref:
                continue
            seen_xref.add(xref)
            if classifier.should_remove(xref, remove_ink, remove_marks):
                bad_images.append(xref)

        if bad_drawings:
            for d in bad_drawings:
                r = pymupdf.Rect(d["rect"])
                # Perfectly horizontal / vertical pen strokes have a
                # zero-area bbox and were skipped before, leaving dashes.
                if r.width < 2.0:
                    mid = (r.x0 + r.x1) / 2
                    r.x0, r.x1 = mid - 1.0, mid + 1.0
                if r.height < 2.0:
                    mid = (r.y0 + r.y1) / 2
                    r.y0, r.y1 = mid - 1.0, mid + 1.0
                page.add_redact_annot(r, fill=False, cross_out=False)
            # TOUCHED, not COVERED: pen paths are routinely split across
            # several drawing objects whose bboxes only overlap, and COVERED
            # left about half of them behind in testing.
            page.apply_redactions(
                images=pymupdf.PDF_REDACT_IMAGE_NONE,
                graphics=pymupdf.PDF_REDACT_LINE_ART_REMOVE_IF_TOUCHED,
                text=pymupdf.PDF_REDACT_TEXT_NONE)
            stats["removed"] += len(bad_drawings)
            # Restore any kept drawing the TOUCHED pass removed because a
            # pen bbox overlapped it (the thin question-box frame is the
            # known case), except a plain white full-page background,
            # which viewers render identically without it.
            from collections import Counter
            remaining = Counter(_drawing_sig(d) for d in page.get_drawings())
            missing = []
            for d in kept_drawings:
                sig = _drawing_sig(d)
                if remaining.get(sig, 0) > 0:
                    remaining[sig] -= 1
                else:
                    missing.append(d)
            page_area = page.rect.get_area()
            # Restore in original document order: z-order is layering
            # (an orange band first, the white card on top of it), and
            # re-drawing in any other order inverts the layers.
            # v19 (DI p5/p9): a restored drawing used to be APPENDED,
            # i.e. ON TOP of all text, so any filled drawing overlapping
            # kept Latin text had to be skipped -- and the skip (single
            # item, or area > 5000) deleted pie slices and bar-chart
            # bars that a big watermark's TOUCHED rect had removed,
            # leaving charts with floating labels and no data. Instead
            # the restores are PREPENDED to the first content stream,
            # i.e. UNDER all text and drawings, exactly where the
            # source had them: slices and bars come back with their
            # labels readable on top, and backdrops sit behind their
            # labels as in the source. No overlap skip is needed any
            # more; only the full-page white background is still
            # skipped (viewers render identically without it).
            to_restore = []
            for d in missing:
                fill = d.get("fill")
                if fill == (1.0, 1.0, 1.0) and \
                        d["rect"].get_area() > 0.5 * page_area:
                    continue
                to_restore.append(d)
            ops = bytearray()
            op_count = 0
            for d in to_restore:
                b = _drawing_ops(d, page.rect.height)
                if b:
                    ops += b
                    op_count += 1
            if ops:
                contents = page.get_contents()
                if contents:
                    first = contents[0]
                    old = out_doc.xref_stream(first)
                    out_doc.update_stream(first, bytes(ops) + (old or b""))
                    stats["redrawn"] = stats.get("redrawn", 0) + op_count
                else:
                    for d in to_restore:
                        redraw_path(page, d)
                    stats["redrawn"] = stats.get("redrawn", 0) \
                        + len(to_restore)
            elif to_restore:
                for d in to_restore:
                    redraw_path(page, d)
                stats["redrawn"] = stats.get("redrawn", 0) + len(to_restore)

        for xref in bad_images:
            try:
                page.delete_image(xref)
                stats["removed_images"] += 1
            except Exception:
                pass

        # Surgical Hindi removal: delete Hindi BT blocks from the stream
        # (font / baseline rules). Rect redaction is NOT used for text:
        # line bboxes overlap neighbouring lines, so it deleted Latin
        # fragments (question last lines, page numbers) on tight pages.
        if remove_hindi and (dropped_baselines or page_has_deva):
            try:
                _delete_hindi_blocks(out_doc, page, dropped_rects,
                                     dropped_baselines, hindi_fonts)
            except Exception:
                pass
        # Move option graphics (fraction bars, root signs) up with
        # their option lines, before text surgery moves the text.
        try:
            stats["drawings_shifted"] = stats.get("drawings_shifted", 0) \
                + _shift_option_drawings(out_doc, page, dropped_rects,
                                         remove_hindi)
        except Exception:
            pass
        # Stream surgery: shift lines below the removed Hindi blocks up
        # and recolor question red / options black. Only BT text blocks
        # are edited; all other stream bytes are preserved.
        try:
            stats["latin_lines"] += _surgery_page(
                out_doc, page, dropped_rects, remove_hindi)
        except Exception:
            pass

    out_doc.save(output_path, garbage=3, deflate=True)
    out_doc.close()
    src_doc.close()
    return stats


def v2_name(filename: str) -> str:
    base, ext = os.path.splitext(filename)
    return f"{base} V2{ext}"


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("input")
    ap.add_argument("-o", "--output")
    ap.add_argument("--keep-ink", action="store_true")
    ap.add_argument("--keep-marks", action="store_true")
    ap.add_argument("--keep-hindi", action="store_true")
    args = ap.parse_args()
    out = args.output or os.path.join(
        os.path.dirname(os.path.abspath(args.input)),
        v2_name(os.path.basename(args.input)))
    st = clean_pdf(args.input, out,
                   remove_ink=not args.keep_ink,
                   remove_marks=not args.keep_marks,
                   remove_hindi=not args.keep_hindi)
    print(f"Saved: {out}")
    print(st)
