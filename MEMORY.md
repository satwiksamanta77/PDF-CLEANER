# CANVA-SSC — Project Memory (detailed)

## 1. Project Overview
- Root: `C:\Users\akasa\Projects\CANVA-SSC`
- Goal: Clean scanned Hindi/English SSC question-bank PDFs so they upload to Canva's PDF editor **without overlapping/broken text**, rebuilding each page from separated text/images/drawings.
- Core library: `clean_for_canva.py`
- Web UI: `app.py` (stdlib `http.server`, port 8765, `PDF_DIR`/`OUT_DIR`)
- Launcher: `start.bat`
- Inputs: `PDFS TO CLEAN\` (18 PDFs, ~21 MB)
- Outputs: `OUTPUT\` (V2 files, gitignored, regenerated)
- Repo: https://github.com/satwiksamanta77/PDF-CLEANER.git (main)
- Archived (local only, gitignored): `_old_unused\` (debug scripts, previews, scratchpad)

## 2. Original User Problem (initial prompt)
User uploads SSC PDFs to https://www.canva.com/pdf-editor/ to solve questions by removing answers, but **text overlaps** when Canva opens them. Asked for a Python script so every cleaned upload opens cleanly. Pasted Gemini Pro's suggested script:
- Gemini suggested deleting PDF annotations (`page.annots()` / `page.delete_annot`) for digital ink.
- Fallback: delete everything that isn't black (colored drawings).
User clarification: "WHENEVER I WILL UPLOAD IT SHOULD OPEN CLEANLY AUTOMATICALLY" — pre-clean the PDF so Canva always renders it cleanly.

## 3. Why Gemini's Approach Failed (root cause)
- `annots=0` on every page — handwriting is **flattened into normal vector drawings**, not annotations.
- Latin text is word-by-word positioned spans using `CambriaMath`/`Identity-H` fonts; Canva cannot map those glyphs → characters shift/collide.
- Devanagari (`Mangal`, `Identity-H`) gets **shattered into per-glyph fragments** by Canva → big overlap mess.
- Helvetica runs wider than Calibri → text ran off right edge (page width ~544.2).
- `₹` (U+20B9) and math unicode (`𝟐`, `𝒙` U+1D499 etc.) break width measurement/rendering.
- Gray vector "coaching center" watermark + cyan "e1" brand fill present on pages.

## 4. First Working Solution (cleaned.pdf)
Produced `pdf_debug/cleaned.pdf` (36 pages, 0 text overflows, ink/watermark stripped). Tradeoff accepted by user:
- English text stays **editable**: merged full lines, `hebo` (Helvetica-Bold, base14, NOT embedded), shrink-to-fit 0.97, `limit_x = page_w − 8`.
- Hindi/Devanagari rendered as **high-res (4x) transparent PNG per line** (Canva-stable; cannot retype inside, but zero overlap).
- `₹` → `Rs`; Mathematical Alphanumeric Symbols (U+1D400–U+1D7FF) mapped to ASCII via digit map + `_MATH_LETTER_RUNS`.
- Watermark removal: `is_gray_watermark` (gray fill 0.40–0.60, ≥50 items) + `is_e1_watermark` (cyan fill rf<0.15, 0.55<gf<0.85, 0.80<bf<1.0).
- Ink removal: `is_colored_ink` (red/green/blue strokes) + `is_likely_black_handwriting` (≥4 items or ≥2 beziers on black strokes) + large black frames.
- Kept: underlines, borders, question circles, yellow box, decorative art.

User then asked "A E1 SHOWING SEE" → removed all cyan "e1" fills; page 7 and title page became clean.
User then: "GOOD BUT EVEN IF DONT EVEN THE AUTONOTES NO ISSUES JUST MAKE SURE IT SHOULD NOT CAUSE THE OVERLAPPING ISSUE WHEN UPLOAD TO CANVA THATS IT IS THAT POSSIBLE AND WHAT WILL YOU DO JUST ANSWER THAT TO ME DONT CHANGE ANYHTING NOW" → answered: possible and already done; English editable, Hindi image.
## 5. Second Phase — User Complaints After Canva Testing
User tested `02 AP, GP, HP Class 02 2023-09-18 V2 (2).pdf PRO.pdf` in Canva:
1. "TEXTS ARE NOT REALLY WRITTEN CLEANLY … THINGS BROKEN".
2. **Original question figures/diagrams being removed** — Geometry `06. Geometry Class 6 2023-01-03 V2.pdf`: "THE FIGURE WAS NOT AUTONOTE IT WAS ALREADY THERE YOU ARE REMOVING QUESTION DIAGRAMS TOO. I TOLD TO YOU THE MARKER ONE ONLY WHICH DRAWL MANUALLY". → Only remove manually-drawn markers; never remove original printed figures/content.
3. User attached PDFs directly but the model **cannot read PDF input** → must render pages to PNG with PyMuPDF and view those. Renders already made in `_preview\cmp\`.

## 6. Diagnosed Root Causes of New Complaints
- **Text "broken" in Canva**: baseline/superscript flattened (`ar2`, `r (n-1)`), fractions collapsed, `−`→`.`, `∞`→`·`, `⋯` issues; all-bold; base14 `hebo` glyph gaps; mangled Devanagari rasters (no shaping).
- **Geometry figure missing**: `deduped_images()` drops any image whose rect is ≥60% inside a kept image's rect. Full-page watermark `x6` (1283x850, rect≈(38,0,499,306)) swallows content images: geo figure `x36` (569x624, rect=(78,16,228,180)), stats histogram `x77` (972x589, rect=(201,95,544,302)).
- **Mensuration p4 handwriting survives**: black FILLED letterforms (color=None, fill near-black/red, items ~8–15, rect area < ~600pt², h ≤ ~30) are not caught by stroke heuristic.
- **Black handwriting heuristic over-aggressive**: `is_likely_black_handwriting` (≥4 items or ≥2 beziers on black strokes) can delete original diagram strokes → tighten with bbox limits (h<~40, w<~120).
- **Purple design fills must be kept**: keep purple (0.439,0.188,0.627) fills; purple at x > 0.58·page_w (printed ⑨ circle) must be kept.

## 7. Image Classification Findings (debug_imgclass.py)
Universal per-page watermark/background images across all PDFs:
- `x6`: e1 logo full-page + smask.
- `x8`/`x11`: near-full-page halves, fully transparent (vis_ratio=0).
- `x13`/`x15`: coaching-center band, aspect ~4.32/4.33, duplicate pair at nearly same rect.
Content images (figure/histogram) and ink-overlay images must be distinguished by pixel content:
- stats `x81` (1371x649) red pen, `x73` (949x441) red → ink overlays; drop when `remove_ink`.
- Drop watermark images when `remove_marks`; drop fully-white/fully-transparent bg images always.
- Use content-stream `Do` order for insertion z-order.

## 8. Web App Phase (user request)
User: "OKAY GOOD NOW MAKE A .BAT FILE WHEN USER CLICK IT WILL OPEN A WEBSITE LOCALHOST WHERE WE CAN SELECT REMOVE THESE MARKER TEXTS OR NOT IF SELECT LIKE CURRENT ONE ALL REMOVE IF NOT THEN IT WILL NOT REMOVE THAT JUST FIX THE CANVA ISSUE AND GIVE THE DOWNLOAD PDF BUTTON WHICH I CAN DOWNLOAD WHICH WILL BE DOWNLOAD WITH SAME NAME WITH JUST V2 IN IT AND I AM GIVING FEW MORE PDFS TO YOU SO THAT YOU CAN TRY AND NO BUGS SHOULD BE THERE AND MAKE AND CLEAN THE PROJECT FOLDER FILES MAKE IT LEANED VERSION PLEASE PDF IMAGE ALL COMING ALL FIX AND THE PDFS TO CLEAN ARE HERE- C:\Users\akasa\Projects\CANVA-SSC\PDFS TO CLEAN"

Built:
- `app.py`: stdlib web server, port 8765, `PDF_DIR`/`OUT_DIR`, endpoints `/`, `/api/list`, `/api/clean`, `/api/download`.
- `start.bat`: launcher (double-click).
- CLI options `--keep-ink`/`--keep-marks` in `clean_for_canva.py`.
- `v2_name()`, `clean_pdf()` with `remove_ink`/`remove_marks`.
- `test_all.py`: batch test all 18 PDFs both modes.
- Project folder cleaned (lean deliverable).

Issues found during testing:
1. Statistics histogram image missing.
2. Mensuration black handwritten solution not removed.
3. Surds layout acceptable.
Root cause of histogram: `deduped_images()` too aggressive (content images inside x6 rect).
Mensuration handwriting: baked into an image (likely img0), not vector strokes; `should_skip_drawing` kept=0 black strokes on that page. Later clarified: handwriting is filled vector letterforms (hundreds of small black/red filled paths), not strokes.

## 9. Devanagari Raster Issue (planned fix)
Current: `render_devanagari_line()` uses `insert_text` + `mangalb.ttf` → render shows MANGLED output ("यिद"/"िथ"/"क्याहै" — no shaping); PIL `raqm=False`.
Planned: replace with **pixel-copy of original line region from source page**: clip pixmap, luminance-threshold to transparent for white-bg lines; opaque for colored-bg (title bars). Pad ~2-3pt.

## 10. Math Fidelity Issue (planned fix)
Current V2 loses: superscripts flattened (`ar2`, `r (n-1)`), fractions collapsed, `−`→`.`, `∞`→`·`, `⋯` issues.
Planned: baseline-clustered runs (group spans by origin y within ~2pt + size + boldness, NOT whole-line merge); embed `C:\Windows\Fonts\arialbd.ttf`/`arial.ttf` instead of base14 `hebo`; preserve bold/regular flags; add normalize mappings for chars Arial lacks.

## 11. Handwriting Detection Issues (planned fix)
- Black stroke heuristic: ≥4 items or ≥2 beziers on black strokes can delete diagram strokes → tighten with bbox (h<~40, w<~120).
- Black FILLED letterforms (Mensuration): color=None, fill near-black/red, items ~8-15, rect area < ~600pt², h ≤ ~30 → add filled-path rule.
- Handwriting-colored text spans (green/red notes): font-based detection (span dump not yet analyzed) — pending.
- Keep purple design fills (0.439,0.188,0.627); purple at x > 0.58·page_w (printed ⑨ circle) kept.

## 12. Known Bugs (ACTIVE, not yet fixed)
1. `deduped_images()` drops content images inside full-page watermark rect (Geometry figure, Stats histogram).
2. Text "broken" in Canva: baseline/superscript flattening, all-bold, base14 hebo glyph gaps, mangled Devanagari rasters.
3. Black handwriting heuristic can delete diagram strokes; needs bbox limits.
4. Black FILLED letterform handwriting (Mensuration) not removed.
5. Need content-aware image classification (watermark / ink-overlay / white-bg / content) via smask-aware pixel stats; respect `remove_marks`/`remove_ink`; Do order.

## 13. Planned Fixes (not yet written)
1. Content-aware `select_images()`: drop only same-aspect (±5%) + ≥60% rect-overlap twins; classify images; respect flags; Do order.
2. Text rebuild: baseline-clustered runs (preserve super/subscripts & fractions), embed Arial/Arial-Bold (fix −, ∞, ⋯; add normalize mappings), preserve bold/regular.
3. Devanagari: pixel-copy original line region from source page (pad ~2-3pt, luminance threshold for white bg).
4. Tighten black-stroke heuristic with bbox limits; add black/red filled-letterform removal.
5. Handwriting-colored text spans: font-based detection (pending span dump).

## 14. Status
- App/UI, CLI (`--keep-ink`/`--keep-marks`), folder cleaned, all 18 PDFs tested OK both modes, endpoints verified, previews generated.
- Diagnosis complete; fixes designed but not yet written.
- User re-test in Canva pending.

## 15. Relevant Files & Line Numbers
- `clean_for_canva.py`: core library.
  - `normalize_text()` (line 45)
  - `is_likely_black_handwriting()` (line 103)
  - `should_skip_drawing()` (line 114)
  - `deduped_images()` (line 164, bug)
  - `fit_size()` (line 183)
  - `merge_line()` (line 195)
  - `render_devanagari_line()` (line 210)
  - `rebuild_page_text()` (line 266)
  - `clean_pdf()` (line 302)
  - `v2_name()`
- `app.py`: web server, port 8765, `PDF_DIR`/`OUT_DIR`.
- `start.bat`: launcher.
- `PDFS TO CLEAN\`: 18 inputs incl. `06. Geometry Class 6 2023-01-03.pdf`, `02 AP, GP, HP Class 02 2023-09-18.pdf`, `02. Statistics class 02 2024-10-03.pdf`, `03. Mensuration 3D Class 3 2023-01-25.pdf`.
- `OUTPUT\`: V2 outputs (stale — regenerate after fixes).
- `_preview\cmp\`: `geo_p0_orig/v2.png`, `apgp_p0_orig/v2.png` (plus `orig_stats_p5.png`, `orig_mens_p4.png`).
- Debug scripts to delete later: `debug_imgpix.py`, `debug_allimgs.py`, `debug_imgclass.py`, `debug_diag2.py`, `debug_geo.py`, `debug_render_cmp.py`, `debug_mens.py`, `debug_mens2.py`, `debug_stats_imgs.py`, `debug_missing.py`, `test_all.py`.
- `_preview\stats_x6/x8/x77/x81.png`, `mens_img0.*`: extracted images for inspection.

## 16. Web App / CLI Contract
- `start.bat` → runs `python app.py` → opens `http://127.0.0.1:8765`.
- UI lets user select PDF, toggle remove markers (handwriting, "coaching center" watermark, cyan "e1" logo) vs only fix Canva overlap.
- Download button saves `same filename + " V2"` to `OUTPUT/`.
- CLI: `python clean_for_canva.py "file.pdf"` (optional `--keep-ink`/`--keep-marks`).
- If toggles off: still fixes Canva overlap (merged lines, Hindi raster) but keeps markers.

## 17. Next Move
1. Analyze apgp p0 span dump (font/color/size) to find handwriting-text signature; then rewrite `clean_for_canva.py`: content-aware image dedup + image classification + draw-order insertion + baseline-clustered embedded-Arial runs + Devanagari pixel-copy + tightened/filled-path ink heuristics.
2. Re-run `test_all.py` (all 18 PDFs, both modes), visually verify `geo` (figure present), `apgp` (clean text/superscripts/fractions/Hindi), `stats p5` (histogram present, red-ink images removed), `mens p4` (handwriting removed, purple design kept).
3. Have user re-test in Canva; then delete debug scripts (`debug_*.py`, `test_all.py`, `_preview/`) — final deliverable = `clean_for_canva.py`, `app.py`, `start.bat`, `PDFS TO CLEAN/`, `OUTPUT/`.

## 18. Conversation History (key user prompts & assistant actions)
- Prompt 1: Canva overlap issue; wants script. → Assistant: Gemini's annot approach fails (annots=0); handwriting is flattened vectors.
- Prompt 2: "COUCHING CENTRE TEXT IS SHOWING THERE AND, THE QUESTIONS ARE I MEAN RIGHT SIDE SEE LITTLE PART NOT SHOWING FIX, Based on SSC CGL 2018 T-2 THIS SHOW CLEANLY TOO CORRECTLY AND I UPLOADED IN CANVA AND SEE WHAT IT IS CAUSING SO MANY OVERLAPPING HAPPENED" → Assistant: fixed watermark, right-side clipping, Canva overlap; rewrote script (Latin editable shrink-to-fit, Devanagari raster, watermark removed).
- Prompt 3: "A E1 SHOWING SEE" → Assistant: removed cyan e1 fills.
- Prompt 4: "GOOD BUT EVEN IF DONT EVEN THE AUTONOTES NO ISSUES JUST MAKE SURE IT SHOULD NOT CAUSE THE OVERLAPPING ISSUE WHEN UPLOAD TO CANVA THATS IT IS THAT POSSIBLE AND WHAT WILL YOU DO JUST ANSWER THAT TO ME DONT CHANGE ANYHTING NOW" → Assistant: confirmed possible/already done.
- Prompt 5: "TEXTS ARE NOT REALLY WRITTEN CLEANLY … THINGS BROKEN" (apgp) + "THE FIGURE WAS NOT AUTONOTE IT WAS ALREADY THERE YOU ARE REMOVING QUESTION DIAGRAMS TOO. I TOLD TO YOU THE MARKER ONE ONLY WHICH DRAWL MANUALLY" (Geometry) → Assistant: diagnosed text-broken (baseline/superscript/all-bold/glyph gaps) and figure removal (deduped_images containment bug).
- Prompt 6: "OKAY GOOD NOW MAKE A .BAT FILE WHEN USER CLICK IT WILL OPEN A WEBSITE LOCALHOST WHERE WE CAN SELECT REMOVE THESE MARKER TEXTS OR NOT ... AND I AM GIVING FEW MORE PDFS TO YOU ... PDFS TO CLEAN ARE HERE" → Assistant: built app.py/start.bat, cleaned folder, tested 18 PDFs; found histogram missing + Mensuration handwriting surviving; diagnosed dedup aggressiveness + filled letterforms.
- Prompt 7: "Continue if you have next steps, or stop and ask for clarification..." → Assistant: continued scanning images across all pages.


## 19. V3 Rewrite (2026-10-24) — all section 12 bugs FIXED and verified

`clean_for_canva.py` was rewritten (same public API: `clean_pdf(input, output, remove_ink=True, remove_marks=True)`, `v2_name()`, CLI flags unchanged; `app.py` untouched and re-verified end-to-end).

**Why the old approach could never work:** the source PDFs' extracted Hindi Unicode is already broken (Identity-H), so re-typing Hindi in ANY font reproduces garbage; and rebuilding pages from parts destroyed images (SMask dropped → black rectangles) and flattened fractions/superscripts. The user's screenshot (Mensuration 3D Q15 cylinder) showed all three failures at once.

**V3 strategy — clone + surgical edits, never rebuild a page:**
- Open the source twice; the output copy keeps every printed image/drawing byte-for-byte (real SMask, real z-order).
- Drawings are removed with redaction annotations + `apply_redactions(graphics=REMOVE_IF_TOUCHED)`. Kept drawings that TOUCHED removes collaterally (question-box frame) are detected by signature and re-drawn with `redraw_path()` — in original document order, passing color/fill explicitly (`color=None` = no stroke; omitting it adds a phantom black border).
- Pen detection: the pen tool always writes at **1.5001pt** → ink = stroke width 1.3–1.7, fill None. Printed strokes are 0.14/1.0 (design) or 2.25+ (diagram outlines like triangle sides). Single-segment exceptions: `re` outlines and long vertical lines are printed (frames/dividers); a long single stroke is ink only if its colour is saturated (teacher underlines). Filled red/black letterforms (≥20 items, small rect) are ink. Grey "coaching center" (≥50 items) and cyan "e1" fills are watermarks.
- Images are classified per-xref by pixel stats sampled at a stride from the ORIGINAL pixels (resizing blends thin pen strokes into the background and hides their pure colour): SMask max ≤10 → watermark/ghost; a single tight pen hue ≥0.85 of visible pixels (or a sparse saturated overlay) → ink image. Content images (triangle figure, histogram, spheres, pie chart) are kept exactly.
- Text: all original text is removed with one text-only redaction, then re-inserted from a plan built on the untouched source.
  - Visual lines are split into x-segments (gap > 25pt) so a table and the question text sharing a baseline are never planned as one line (as one raster they pixel-copied pen marks inside the table).
  - Lines are grouped into printed ROWS by vertical overlap. In multi-line rows (fractions, super/subscripts) every span is re-typed at its exact origin, shrunk to its own original width. In plain lines, spans merge into clusters (split by baseline/size/colour/x-gap > 12pt so option columns stay separate), fit inside the cluster's own footprint.
  - Devanagari lines, rotated lines, and non-WinAnsi clusters are **pixel-copied** from the rendered source at 4x with a background-distance alpha ramp (bg estimated from border pixels, excluding the line's own colours; other lines' colours are zeroed to remove slivers). Clip pad is generous at the top (−4pt) because shirorekha/anusvara render above the span bbox.

**Verification (2026-10-24):** all 18 PDFs × 4 toggle modes = **72/72 runs OK** (old code failed 72/72 on Linux: hardcoded `C:\Windows\Fonts\mangalb.ttf`; V3 has no platform font dependency). Visually verified page renders: Mensuration 3D p14 (the screenshot page — 4/5 fraction stacked, Hindi solid, ink gone), Geometry p0 (triangle + option fractions in columns), Statistics p5 (histogram intact, red pen overlay gone), DI p0/p3 (tables clean, pie chart intact), Height & Distance p0 (title frame kept) and p2 (all six printed triangles kept, red answers gone), AP/GP p0 (superscripts/fractions in place), Equation p0, Surds p0, Coordinate Geometry p0/p2, Mensuration 2D p0 (white card over orange band, z-order correct), Mensuration 3D Class 4 p0/p3 (spheres kept), Trigonometry/Permutation/Algebra/Coordinate notes pages (printed handwriting-style content kept when it is print; teacher pen removed). `both_off` mode preserves handwriting + watermarks while still fixing the text. Web UI smoke-tested: `/api/list`, `/api/clean`, download all work.

**Known tiny remnants (accepted):** pen strokes drawn *inside* a Hindi text line's own rectangle are pixel-copied with the line (e.g. a small red tick fragment on 1–2 pages); handwritten question-number badge images (e.g. a pen-drawn "2.") are removed with ink ON, by design.
