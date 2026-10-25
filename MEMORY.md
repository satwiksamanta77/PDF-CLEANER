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

## 20. Canva fix loop (2026-10-25) — v6–v28, summary

V3 output did not open the same way in Canva's PDF editor (the user: the
original opens fine, the cleaned file does not). The loop below ran under the
user's 2026-10-25 01:59 instruction: fix everything, regenerate all, re-upload
all, check every page one by one, iterate until fully clean, then push the
updated code (OUTPUT PDFs not named in that approval).

**Architecture (v6, still current): never re-emit text.** Surviving text stays
byte-for-byte; Hindi is removed by BT-block deletion; pen/watermarks by
redaction; options shift up + question red / options black by BT-block surgery
only. (v5's full-page re-emission as synthetic Helvetica was abandoned: Canva
re-flows synthetic text; original embedded-font TJ positioning is what it
handles.)

**Fix families (all in `clean_for_canva.py` unless noted):**
- v9: path-atomic drawing shift (path moves if its bbox centre is in an option
  zone); question gap cap 6pt; space MERGE (adjacent word blocks on Q/O lines
  merge into one block with real space glyphs mid-block); recolour fixes
  (rg/RG independent, numeric sc/SC replaced, insert before first text op,
  leading space after TD1); label-anchored `is_question`; SINGLE dy per
  question; column guard (option membership/promotion require x0 >= label_x0
  − 10 — fixed DI p1 table garbling).
- v10–v16 (merge rewrite, `_merge_word_spaces`): number-free merged arrays
  (space glyphs only, gap-count rounded); cross-font merge (Identity-H math
  fragments merge with font/Tc switches, spaces borrowed from a neighbouring
  single-byte segment; advances by dict-span slicing by CID count, spans
  matched by CONTAINMENT and space-stripped; CID counting includes literal
  groups); attach-instead-of-break punctuation/number rules (lone "." decimal
  only digit-before AND digit-after; ":" before digit attaches; symbol
  prefixes attach before letters/digits, not operators; "," before digit with
  gap < 2pt attaches); safety valves (reflowed end must not exceed source end
  + 2/4pt); merges per-content-stream (outputs can have multiple streams).
- v17–v21: merge run breaks on attach boundaries with gap > 2.0; Hindi gate +
  span-level font rule; fragment rules in `_delete_hindi_blocks` (c1: Latin in
  a Devanagari x-segment; c2: pure-Latin sandwiched between Hindi neighbours,
  aggregated BY BASELINE); table-grid guard (zone = gridlines' bbox, not the
  frame; bare outlines get no zone); obstacle-aware shift cap (row top ≥6pt
  below nearest kept line/zone bottom); option-continuation shift; Hindi-formula
  math "sup" rule; `_drawing_ops` connectivity ("m" only on subpath start);
  table-content excluded from option rows.
- v22: continuation size guard (|size − label size| ≤ 1.5) — v21's continuation
  rule had claimed DI p1's pie label "T.N, 9%" and shifted it out of its
  redaction-inserted clip (clips are position-tied; shifted text outside a clip
  vanishes). The 12pt membership branch is deliberately NOT guarded (fraction
  numerators/denominators are legitimately smaller).
- v23: standalone space blocks emptied when merged over (Statistics' producer
  emits ' ' blocks between words; leaving them doubled gaps and drifted words
  into splits in Canva — the real cause of the Stats letter fragmentation).
- v24: Hindi-line Latin edge tokens (c3: a mixed line with ≥2 Devanagari chars
  is a Hindi sentence, all its Latin blocks go; c4: pure-Latin on a Hindi
  baseline; frag/sup windows start at first_deva − 2.0). Fragment checks must
  use math-aware matching: fragments extract as mathematical Unicode
  ('𝑷𝑸𝑹𝑺', '∆𝑨𝑩𝑪'), so ASCII needle searches miss them.
- v25/v27: Hindi-fraction survival (c2 looks for Hindi on BOTH sides within
  40pt; c4 requires a MATH font (CambriaMath) + Devanagari within 8pt, NO
  x-gate — an x-gate broke Geo p14, whose "∆ABC" sits left of its Hindi words).
- v28: figure-gate panel rule (`_ink_figure_rects`). The slide template paints
  two blue e1 panels (161-240x77-180, 259-368x54-178) on every page; a printed
  figure drawn ON a panel (Mens 3D p16's cylinder: ink-only bbox 52x75) was
  dropped by the 80x80 gate. An ink group overlapping a panel by ≥30% of its
  bbox area now qualifies under 80x80, BUT only if its long paths are black —
  coloured working on a panel (Mens 3D p22's red scratch triangle, 60% panel
  overlap) stays removed. Large coloured figures still pass the main gate.
  (An earlier v28 attempt — transitive fill expansion — resurrected the p22
  triangle via the full-page white background fill and was reverted.)
  Do NOT narrow `is_e1_watermark`: the blue panels repeat identically on all
  510 pages and are template background, not figure content; removing them is
  by-design and accepted.

**Verification rules learned (do not regress):**
- MuPDF extraction does NOT predict Canva's visual rendering (merged lines
  extract first-word-only yet pass in Canva). Never chase extraction.
- Canva's text-extraction panel collapses visibly-present spaces; judge
  spacing only visually at 200%+ zoom, with original comparison for any FAIL.
- Sweep sets must be uniquely named and frozen per run (never reuse a suffix);
  browser checks must run singly with the user present (parallel and
  user-away launches die on upload-approval timeouts).
- Most join/collision flags from checkers are Canva-side tightness or misreads
  and must be arbitrated against local renders/dict/stream before any code
  change. Real defects found this way: Stats fragmentation (v23), Geo Latin
  remnants (v24), M2D Hindi fractions (v25/v27), M3 p16 figure loss (v28).

**State at v28 (2026-10-25 evening):** regen 18/18 to `_work/v28all/`; audit
CLEAN (gap WORSE 0, shift MOVED 0, colour 1 known Mens 3D-4 p5 footer '³'
artifact, space MISSes 4 stale V2 only). SWEEP27 verdicts: DI CLEAN (6/7 pass,
p5 fail refuted); Mens 3D 3/7 pass on SWEEP27 with p10/p12/p22 flags refuted
and p16 figure loss fixed in v28. SWEEP28 set frozen (`_work/sweep28/`,
BRIEF.md). Still open: Canva re-check of M3 p16 (v28) + Stats/Geo/M2D
re-checks + wave-2 batch-2 full files, then the approved GitHub push.
Known minor gaps: M3 p16's small cone sketch still missing (no safe rule —
any cluster distance catching it at 32pt also keeps Geo p14's working at
11pt); Geo p24 header "Apollonius theorem ():" empty parens (cosmetic,
user not yet answered); Stats p18 table headers render red by rule.

## 21. Upload option (2026-10-25)

`app.py` now accepts PDF uploads, not just selecting from the folder:
- UI: "⬆ Upload PDF" button (file picker, accept=.pdf) beside the select in
  card 1, plus drag-and-drop onto the "Choose PDF" card. After upload the
  list refreshes and the new file is auto-selected; status shown inline
  (.upmsg ok/err).
- New endpoint `POST /api/upload?name=<filename>` (raw PDF bytes as the
  request body — avoids multipart parsing): basename + Windows-forbidden-char
  sanitization, .pdf extension required, `%PDF` magic check in first 1024
  bytes, 200 MB cap, auto-rename on collision (`name (1).pdf`) so existing
  folder files are never overwritten.
- Uploaded files land in `PDFS TO CLEAN\`, so `/api/clean` and
  `/api/download` work unchanged.
- Verified via scripted endpoints test: upload, duplicate auto-rename,
  .txt and fake-pdf rejection, list refresh, end-to-end clean of an uploaded
  file (34 pages OK), test artifacts removed.

## 22. v29 — the 6-defect fix round (2026-10-25, user Canva reports)

User reported (via 6 canva.link designs, 2 named): Mensuration 3D C4 "so
many problems in options and questions"; Height & Distance "issues in
options"; others: "lines over texts", "removed all options and showing
markers but no actual texts", "answer and markers not even removed".
Folder had grown to 20 PDFs (2 new: MATHS Coordinate Geometry 01, MATHS
Discount 01 — never tested before).

Diagnosis (audit script `_work/audit_r29.py` + ~90 side-by-side page
renders viewed by vision): options/questions were textually fine on most
flagged pages (MuPDF extraction artifacts; MEMORY §20 rule held), the REAL
defects were:
1. Pen annotations CHAINED to kept figures survived (per-colour failure):
   M3C4 p3 red "2.5|8" + green circles on the ball figure; H&D p14 cyan
   "√3=1.732" + 2↔1 arrows on the triangle; M3C4 p10 green "16/4√17/√11".
2. Teacher working qualified as "figure" via the old all-colour 80x80
   gate: APGP p12 purple option box (options then shifted ON TOP of it —
   "lines over texts"), APGP p27 red arrow + underbrace + 25/2 formula.
3. M3C4 p10's 46x54 square base figure REMOVED (<80x80, off-panel; every
   version until now).
4. Question-number badge images deleted by pen-image classifier (Stats p3
   "23.", Algebra p17 "(16)").
5. Stats p18 bilingual table header painted red by the question rule.
6. Surds p22: the Hindi sentence's math row ("𝟐−𝒙𝟐+𝒙−𝟑𝟐=?") survived as
   a ghost line (superscripts 4.2pt above the Hindi baseline — outside the
   ±2pt window; the Calibri "?" is not a math font).

v29 fixes in `clean_for_canva.py`:
- `_ink_figure_rects` REWRITTEN: (a) chaining is PER COLOUR — an
  annotation of another colour touching a figure no longer shares its
  exemption; (b) a colour group qualifies with ≥2 long paths AND ≥80x80
  OR ≥30% e1-panel with black long paths (v28) OR (v29b) <80 black shape
  with ≥3 long paths; (c) a group whose bbox overlaps ANY non-footer text
  line is teacher marking, never a figure (kills the purple box, answer
  circles, margin arrows; verified: NO protected figure overlaps any text
  span — M2D p13/p23, H&D p1/p14, M3C4 p2/p3/p10, Geo p25, M3C3 p16/p22);
  (d) member-split: a group chained THROUGH the text area keeps its
  distant core (the black square chained to a question-area underline via
  bridging strokes comes back after dropping the overlapping members);
  (e) small BLACK strokes within 25pt of a kept figure are its labels
  (H&D p1's angle labels); small coloured ones are marks (stay removed).
  The v29 closed-cycle test was measured EXACTLY BACKWARDS on real data
  (square=False via a stray degree-1 internal stroke; dense glyph chains
  =True) and was replaced in v29b — do not resurrect it.
- `_delete_hindi_blocks` (c5): in the frag window (first_deva−8 .. first
  opt), a block within 8pt of a DROPPED Hindi baseline, at/right of that
  Hindi line's start−20pt, is Hindi-sentence content: delete on the Hindi
  baseline (any font — catches the Calibri "?" and Geo p24's "()"), and
  above it when math-set or ≤4 units (catches the 𝟐 superscripts).
- Badge images KEPT: any small image (≤45x26) left of the question column,
  above the first option, survives regardless of pen stats (supersedes
  §19's by-design removal — questions must stay complete).
- `_surgery_page`: lines inside table grid zones keep their original
  colour (Stats p18 header).
- `_row_shift_plan(..., extra_obstacles)`: kept pen figures cap option
  shifts like text obstacles (both call sites — surgery + drawing shift —
  share one plan).
- Upload option added earlier this session (§21) untouched and re-tested.

Verification (r31): 20/20 regenerate OK; fresh audit — no MISSING option
labels, no new OVERLAPS, PEN-IMAGES = kept badges only; ~90 side-by-side
page renders visually verified clean: ALL of M3C4 (24p) and H&D (28p),
all 8 previously-defective pages, all audit-heavy pages (Geo p15/p16/p17,
Equation p11, CG-01 p14/p28, PC p23, Algebra p22, Discount p7/p22/p34),
2-3 samples of every other file incl. protected figures (M2D p13/p23,
Stats p5 histogram, DI p1 pie+table, M3C3 p16/p22/p26, Trig p5/p19,
CG-03 p10/p30, Surds2 p15, Surds03 p8, NumberSystem p5). Residuals
accepted: teacher board sketches kept as figures (M3C4 p2 sphere sketch
with "8:1" — same class as H&D p1 reference triangles; no safe rule),
occasional tiny pen specks near kept figures, M3C4 p10's green triangle
gone only because it overlapped text — a same-shaped triangle clear of
text would stay.
