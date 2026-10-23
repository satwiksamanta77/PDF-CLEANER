# CANVA-SSC — Project Memory

## Overview
Local Python tool that cleans PDFs so they upload to Canva's PDF editor **without overlapping/broken text**. User picks a PDF in a local web UI, chooses what to remove (handwriting markers, watermarks, or only the Canva-overlap fix), and downloads the result as `same filename + " V2"` into `OUTPUT/`.

- Root: `C:\Users\akasa\Projects\CANVA-SSC`
- Core library: `clean_for_canva.py`
- Web UI: `app.py` (stdlib `http.server`, port 8765)
- Launcher: `start.bat`
- Inputs: `PDFS TO CLEAN\` (18 PDFs)
- Outputs: `OUTPUT\` (gitignored, regenerated)

## Objective
1. Fix Canva overlap: merge word-by-word spans into full lines, use `hebo`/embedded Arial, shrink-to-fit (page width ~544.2).
2. Devanagari (Hindi) must NOT be shattered by Canva: render as 4x transparent PNG per line.
3. Normalize math unicode digits / `₹` -> `Rs`.
4. Optionally remove markers: colored handwriting (red/green/blue/black scrawl), gray `coaching center` watermark, cyan `e1` brand fills.
5. Never remove original printed question figures/diagrams (only manually-drawn markers).

## Root Causes (confirmed)
- Gemini's annotation-removal script did NOT work: `annots=0` everywhere.
- Handwriting is flattened as **vector drawings** (red/blue/green/black strokes + filled letterforms), not annotations.
- Canva overlap caused by: word-by-word positioned spans + `CambriaMath`/`Identity-H` fonts; Devanagari shattered; Helvetica wider than Calibri; `₹`/math-unicode width issues.

## Architecture / Key Functions (`clean_for_canva.py`)
- `normalize_text()` — math digit/letter map + `₹`->`Rs`.
- `is_colored_ink()`, `is_gray_watermark()`, `is_e1_watermark()`, `is_likely_black_handwriting()` — classifiers.
- `should_skip_drawing()` — removal decision.
- `deduped_images()` (line ~164) — **bug**: drops content images inside full-page watermark rect.
- `merge_line()`, `fit_size()`, `render_devanagari_line()`, `place_raster()`.
- `rebuild_page_text()` (line ~266) — text rebuild.
- `clean_pdf()`, `v2_name()`.

## Known Bugs (ACTIVE, not yet fixed)
1. `deduped_images()` drops any image whose rect is inside a kept image's rect. Full-page watermark `x6` (1283x850) swallows content images: Geometry figure `x36`, Statistics histogram `x77`.
2. Text "broken" in Canva: baseline/superscript flattened, all-bold, base14 `hebo` glyph gaps (`−`, `∞`, `⋯`), mangled Devanagari rasters (no shaping).
3. Black handwriting heuristic can delete diagram strokes; needs bbox limits (h<~40, w<~120).
4. Black FILLED letterform handwriting (Mensuration) not removed (stroke heuristic misses fills).
5. Need content-aware image classification (watermark / ink-overlay / white-bg / content) via smask-aware pixel stats, respect `remove_marks`/`remove_ink`, use content-stream Do order.

## Planned Fixes (not yet written)
1. Content-aware `select_images()`: drop only same-aspect (±5%) + >=60% rect-overlap twins; classify images; respect flags; Do order.
2. Text rebuild: baseline-clustered runs (preserve superscripts/fractions), embed Arial/Arial-Bold, preserve bold/regular.
3. Devanagari: pixel-copy original line region from source page (pad ~2-3pt, luminance threshold for white bg).
4. Tighten black-stroke heuristic with bbox limits; add black/red filled-letterform removal.
5. Handwriting-colored text spans: font-based detection (pending span dump).

## Status
- App/UI, CLI (`--keep-ink`/`--keep-marks`), folder cleaned, all 18 PDFs tested OK both modes, endpoints verified, previews generated.
- Diagnosis complete; fixes designed but not yet written.
- User re-test in Canva pending.

## Relevant Files
- `clean_for_canva.py` — core library.
- `app.py` — web server, `PDF_DIR`/`OUT_DIR`.
- `start.bat` — launcher.
- `PDFS TO CLEAN\` — 18 inputs.
- `OUTPUT\` — V2 outputs (stale; regenerate after fixes).
- `_preview\cmp\` — comparison renders (archived).
- Debug scripts archived under `_old_unused\`.
