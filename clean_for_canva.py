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
  - Remove ALL original text with a text-only redaction, then re-insert it:
      * Latin runs are grouped by baseline (origin-y), size and colour, so
        fractions (4 over 5), superscripts and subscripts keep their own
        baseline and size instead of being flattened into one merged line.
        Plain Latin stays editable (Helvetica / Helvetica-Bold, shrink-to-fit).
      * Any run containing Devanagari, a rotated run, or symbols outside
        WinAnsi (Greek, math signs the base-14 fonts lack) is pixel-copied
        from the rendered source page: the exact original glyphs become a
        transparent high-res PNG. Canva cannot shatter or overlap an image,
        and no font file (Mangal etc.) is ever needed, on any OS.
  - Math alphanumeric unicode (U+1D400-U+1D7FF) is normalised to ASCII and
    the rupee sign to "Rs", as before.

Requires: pymupdf, Pillow.
"""

import io
import os

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


def should_remove_drawing(d: dict, remove_ink: bool, remove_marks: bool) -> bool:
    if remove_marks and (is_gray_watermark(d) or is_e1_watermark(d)):
        return True
    if remove_ink:
        if is_pen_stroke(d):
            return True
        if is_colored_ink(d.get("color")) and len(d.get("items", [])) >= 2 \
                and 1.3 <= (d.get("width") or 0) <= 1.7:
            return True
        if is_filled_ink_letterform(d):
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


def plan_text(page):
    latin_jobs = []
    raster_jobs = []
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

    for li, ln in enumerate(all_lines):
        other = tuple({c for j, cols in enumerate(line_colors) if j != li
                       for c in cols})
        spans = ln["spans"]
        raw_text = "".join(s["text"] for s in spans)
        if not raw_text.strip():
            continue
        rect = line_rects[li]
        rotated = ln["dir"] != (1, 0)
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
    return latin_jobs, raster_jobs


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


def _drawing_sig(d):
    r = d["rect"]
    return (str(d.get("color")), str(d.get("fill")), str(d.get("width")),
            round(r.x0, 1), round(r.y0, 1), round(r.x1, 1), round(r.y1, 1),
            len(d.get("items", [])))


def clean_pdf(input_path: str, output_path: str,
              remove_ink: bool = True, remove_marks: bool = True):
    src_doc = pymupdf.open(input_path)
    out_doc = pymupdf.open(input_path)
    classifier = ImageClassifier(src_doc)
    stats = {"pages": len(src_doc), "removed": 0, "removed_images": 0,
             "latin_lines": 0, "hindi_rasters": 0, "images_kept": 0}

    for pno in range(len(src_doc)):
        src_page = src_doc[pno]
        page = out_doc[pno]

        latin_jobs, raster_rects = plan_text(src_page)
        raster_jobs = []
        for rect, fg, other in raster_rects:
            # Generous pad, especially at the top: Devanagari glyphs
            # (shirorekha, anusvara dots, above-line matras) render above
            # the span bbox, and a tight clip slices them off.
            pad = pymupdf.Rect(rect.x0 - 2.5, rect.y0 - 4.0,
                               rect.x1 + 2.5, rect.y1 + 3.0)
            rendered = render_region_transparent(src_page, pad, fg_colors=fg,
                                                 other_colors=other)
            if rendered:
                raster_jobs.append(rendered)

        drawings = src_page.get_drawings()
        bad_drawings = [d for d in drawings
                        if should_remove_drawing(d, remove_ink, remove_marks)]
        kept_drawings = [d for d in drawings
                         if not should_remove_drawing(d, remove_ink, remove_marks)]

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
            for d in missing:
                fill = d.get("fill")
                if fill == (1.0, 1.0, 1.0) and \
                        d["rect"].get_area() > 0.5 * page_area:
                    continue
                redraw_path(page, d)
                stats["redrawn"] = stats.get("redrawn", 0) + 1

        for xref in bad_images:
            try:
                page.delete_image(xref)
                stats["removed_images"] += 1
            except Exception:
                pass

        page.add_redact_annot(page.rect, fill=False, cross_out=False)
        page.apply_redactions(
            images=pymupdf.PDF_REDACT_IMAGE_NONE,
            graphics=pymupdf.PDF_REDACT_LINE_ART_NONE,
            text=pymupdf.PDF_REDACT_TEXT_REMOVE)

        for origin, text, fontname, size, color in latin_jobs:
            try:
                page.insert_text(origin, text, fontsize=size,
                                 fontname=fontname, color=color)
                stats["latin_lines"] += 1
            except Exception:
                pass
        for rect, png in raster_jobs:
            try:
                page.insert_image(rect, stream=png)
                stats["hindi_rasters"] += 1
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
    args = ap.parse_args()
    out = args.output or os.path.join(
        os.path.dirname(os.path.abspath(args.input)),
        v2_name(os.path.basename(args.input)))
    st = clean_pdf(args.input, out,
                   remove_ink=not args.keep_ink,
                   remove_marks=not args.keep_marks)
    print(f"Saved: {out}")
    print(st)
