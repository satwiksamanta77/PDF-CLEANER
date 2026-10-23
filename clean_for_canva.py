#!/usr/bin/env python3
"""Clean a PDF so Canva's PDF-to-editable import stays clean.

Always fixes Canva overlap:
  - Latin text: merged full lines, Helvetica-Bold, shrink-to-fit (editable)
  - Devanagari: high-res transparent PNG (Canva shatters Identity-H Devanagari)
  - Math unicode digits / ₹ normalized

Optional removals:
  - handwriting (red/green/blue pen, complex black scrawl)
  - watermarks ("coaching center" gray vector, cyan "e1" brand fills)
"""
import os
import pymupdf

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

MANGAL_BOLD = r"C:\Windows\Fonts\mangalb.ttf"
RASTER_SCALE = 4


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
        else:
            out.append(c)
    return "".join(out)


def has_devanagari(s: str) -> bool:
    return any(0x0900 <= ord(c) <= 0x097F for c in s)


def int_to_rgb(c: int):
    return ((c >> 16 & 255) / 255.0, (c >> 8 & 255) / 255.0, (c & 255) / 255.0)


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
    if d.get("color") is not None:
        return False
    fill = d.get("fill")
    if fill is None:
        return False
    r, g, b = fill[0], fill[1], fill[2]
    grayish = abs(r - g) < 0.03 and abs(g - b) < 0.03 and 0.40 <= r <= 0.60
    return grayish and len(d.get("items", [])) >= 50


def is_e1_watermark(d: dict) -> bool:
    if d.get("color") is not None:
        return False
    fill = d.get("fill")
    if fill is None:
        return False
    rf, gf, bf = fill[0], fill[1], fill[2]
    return rf < 0.15 and 0.55 < gf < 0.85 and 0.80 < bf < 1.0


def is_likely_black_handwriting(d: dict) -> bool:
    color = d.get("color")
    if color is None or max(color) > 0.25:
        return False
    if d.get("fill") is not None:
        return False
    items = d.get("items", [])
    beziers = sum(1 for it in items if it[0] == "c")
    return len(items) >= 4 or beziers >= 2


def should_skip_drawing(d: dict, page_area: float, remove_ink: bool, remove_marks: bool) -> bool:
    if remove_ink and is_colored_ink(d.get("color")):
        return True
    if remove_marks and is_gray_watermark(d):
        return True
    if remove_marks and is_e1_watermark(d):
        return True
    if remove_ink and is_likely_black_handwriting(d):
        return True
    if remove_ink:
        color, fill = d.get("color"), d.get("fill")
        if fill is None and color is not None and max(color) < 0.3:
            r = d.get("rect")
            if r and r.get_area() > 0.08 * page_area and (d.get("width") or 1) < 0.5:
                return True
        if fill is not None and color is None:
            rf, gf, bf = fill[0], fill[1], fill[2]
            if rf < 0.1 and 0.6 < gf < 0.8 and 0.85 < bf < 1.0:
                return True
    return False


def redraw_path(page, d: dict):
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
    kwargs = {}
    if d.get("color") is not None:
        kwargs["color"] = d["color"]
    if d.get("fill") is not None:
        kwargs["fill"] = d["fill"]
    if d.get("width"):
        kwargs["width"] = d["width"]
    if d.get("dashes") and d["dashes"] != "[] 0":
        kwargs["dashes"] = d["dashes"]
    try:
        shape.finish(**kwargs)
        shape.commit()
    except Exception:
        pass


def deduped_images(page):
    kept = []
    for img in page.get_images(full=True):
        xref = img[0]
        rects = page.get_image_rects(xref)
        if not rects:
            continue
        r = rects[0]
        dup = False
        for _, kr in kept:
            inter = r & kr
            if not inter.is_empty and inter.get_area() > 0.6 * min(r.get_area(), kr.get_area()):
                dup = True
                break
        if not dup:
            kept.append((xref, r))
    return kept


def fit_size(text: str, size: float, x0: float, limit_x: float) -> float:
    if not text:
        return size
    avail = limit_x - x0
    if avail <= 20:
        return size
    w = pymupdf.get_text_length(text, fontname="hebo", fontsize=size)
    if w <= avail * 0.97:
        return size
    return max(size * (avail * 0.97) / w, 6.5)


def merge_line(spans) -> str:
    parts, prev_text, prev_span = [], None, None
    for span in spans:
        text = normalize_text(span.get("text", ""))
        if not text:
            continue
        if prev_text is not None and text.strip() and prev_text.strip():
            gap = span["bbox"][0] - prev_span["bbox"][2]
            if gap > max(1.5, 0.15 * span.get("size", 12)) and not parts[-1].endswith(" ") and not text.startswith(" "):
                parts.append(" ")
        parts.append(text)
        prev_text, prev_span = text, span
    return "".join(parts)


def render_devanagari_line(spans, pad: float = 2.0):
    x0 = min(s["bbox"][0] for s in spans)
    y0 = min(s["bbox"][1] for s in spans)
    x1 = max(s["bbox"][2] for s in spans)
    y1 = max(s["bbox"][3] for s in spans)
    max_extra = 0.0
    for s in spans:
        raw = s.get("text", "")
        norm = normalize_text(raw)
        if norm == raw:
            continue
        try:
            w_raw = pymupdf.get_text_length(raw, fontname="helv", fontsize=s.get("size", 14))
            w_norm = pymupdf.get_text_length(norm, fontname="helv", fontsize=s.get("size", 14))
            max_extra = max(max_extra, w_norm - w_raw)
        except Exception:
            max_extra = max(max_extra, 12.0)
    x1 += max_extra
    rect = pymupdf.Rect(x0 - pad, y0 - pad, x1 + pad, y1 + pad)

    tmp = pymupdf.open()
    tp = tmp.new_page(width=rect.width, height=rect.height)
    for s in spans:
        text = normalize_text(s.get("text", ""))
        if not text:
            continue
        o = s.get("origin") or (s["bbox"][0], s["bbox"][3])
        origin = (o[0] - rect.x0, o[1] - rect.y0)
        tp.insert_text(
            origin, text, fontsize=s.get("size", 14),
            fontname="mangalb", fontfile=MANGAL_BOLD,
            color=int_to_rgb(s.get("color", 0)),
        )
    pix = tp.get_pixmap(matrix=pymupdf.Matrix(RASTER_SCALE, RASTER_SCALE), alpha=True)
    png = pix.tobytes("png")
    tmp.close()
    return rect, png


def place_raster(page, rect, png, page_rect):
    r = pymupdf.Rect(rect)
    hard_max = page_rect.width - 2
    if r.x1 > hard_max:
        shift = r.x1 - hard_max
        r.x0 = max(0, r.x0 - shift)
        r.x1 = hard_max
    if r.x0 < 0:
        r.x1 = min(hard_max, r.x1 - r.x0)
        r.x0 = 0
    if r.y1 > page_rect.height - 1:
        r.y1 = page_rect.height - 1
    if r.y0 < 0:
        r.y0 = 0
    page.insert_image(r, stream=png)


def rebuild_page_text(new_page, src, stats):
    page_w = src.rect.width
    limit_x = page_w - 8.0
    d = src.get_text("dict")
    latin_jobs = []

    for block in d["blocks"]:
        if block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            spans = [s for s in line.get("spans", []) if s.get("bbox")]
            if not any(s.get("text", "").strip() for s in spans):
                continue
            if has_devanagari("".join(s.get("text", "") for s in spans)):
                rect, png = render_devanagari_line(spans)
                place_raster(new_page, rect, png, src.rect)
                stats["hindi_rasters"] += 1
                continue
            merged = merge_line(spans)
            if not merged.strip():
                continue
            latin_jobs.append((merged, spans))

    for merged, spans in latin_jobs:
        first = next(s for s in spans if normalize_text(s.get("text", "")).strip())
        x0 = first["bbox"][0]
        size = max(s.get("size", 12) for s in spans if s.get("text", "").strip())
        size = fit_size(merged, size, x0, limit_x)
        origin = first.get("origin") or (x0, first["bbox"][3])
        if origin[0] > limit_x:
            continue
        color = int_to_rgb(first.get("color", 0))
        new_page.insert_text(origin, merged, fontsize=size, fontname="hebo", color=color)
        stats["latin_lines"] += 1


def clean_pdf(input_path: str, output_path: str, remove_ink: bool = True, remove_marks: bool = True):
    src_doc = pymupdf.open(input_path)
    out_doc = pymupdf.open()
    stats = {"pages": len(src_doc), "removed": 0, "latin_lines": 0,
             "hindi_rasters": 0, "images_kept": 0}

    for page in src_doc:
        new_page = out_doc.new_page(width=page.rect.width, height=page.rect.height)
        new_page.draw_rect(page.rect, color=None, fill=(1, 1, 1))

        for xref, r in deduped_images(page):
            try:
                info = src_doc.extract_image(xref)
                new_page.insert_image(r, stream=info["image"])
                stats["images_kept"] += 1
            except Exception:
                pass

        page_area = page.rect.width * page.rect.height
        for dr in page.get_drawings():
            if should_skip_drawing(dr, page_area, remove_ink, remove_marks):
                stats["removed"] += 1
                continue
            redraw_path(new_page, dr)

        rebuild_page_text(new_page, page, stats)

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
    out = args.output or os.path.join(os.path.dirname(os.path.abspath(args.input)), v2_name(os.path.basename(args.input)))
    st = clean_pdf(args.input, out, remove_ink=not args.keep_ink, remove_marks=not args.keep_marks)
    print(f"Saved: {out}")
    print(st)
