# LESSONS.md — CANVA-SSC

TRIGGER: PDF text overlaps in Canva after cleaning → RULE: Gemini's annotation-removal script fails because annots=0; handwriting is flattened vector drawings, not annotations.
TRIGGER: Canva shatters Devanagari text → RULE: render Hindi lines as 4x transparent PNG per line instead of editable text.
TRIGGER: PyMuPDF insert_text with Mangal font produces mangled Hindi (no shaping) → RULE: use pixel-copy of original line region (or insert_htmlbox + mangal) instead of insert_text.
TRIGGER: base14 hebo lacks glyphs (∠, ∥, ∞, −, ⋯) and Canva substitutes → RULE: embed Arial/Arial-Bold and add normalize mappings for chars Arial lacks.
TRIGGER: whole-line merge flattens superscripts/fractions → RULE: group spans by origin y (~2pt) + size + boldness into baseline-clustered runs.
TRIGGER: image dedup by containment → RULE: never drop a content image just because its rect is inside a full-page watermark rect; drop only same-aspect (±5%) + >=60% rect-overlap twins.
TRIGGER: stats histogram / geometry figure missing in V2 → RULE: full-page watermark image x6 swallows content images; classify images by pixel content (smask-aware), keep content images.
TRIGGER: black handwriting heuristic deletes diagram strokes → RULE: constrain candidate to bbox height<~40 and width<~120; never remove original printed figures.
TRIGGER: Mensuration handwriting survives cleaning → RULE: black/red FILLED letterforms (color=None, fill near-black/red, items ~8-15) are ink; add filled-path rule.
TRIGGER: purple design fills removed → RULE: keep purple (0.439,0.188,0.627) fills and purple at x>0.58*page_w.
TRIGGER: Canva opens text broken → RULE: use merged full lines with shrink-to-fit 0.97, limit_x = page_w-8, preserve bold/regular.
TRIGGER: ₹ and math unicode break width/rendering → RULE: map ₹->Rs and math digits/letters to ASCII.
TRIGGER: image z-order wrong → RULE: insert images in content-stream Do order.
TRIGGER: invisible watermark images (smask black, vis~0) → RULE: drop if vis<0.02; detect ink overlays by pen-color fraction of visible pixels.
TRIGGER: all-bold output → RULE: preserve bold/regular flags from source spans.
TRIGGER: Devanagari rasters mangled in Canva → RULE: pad ~2-3pt and luminance-threshold for white backgrounds; opaque for colored title bars.
TRIGGER: committing debug scripts → RULE: keep project lean; archive debug_*.py, test_all.py, _preview, scratchpad.
TRIGGER: PDF input cannot be read by model → RULE: render pages to PNG with PyMuPDF and view those.
TRIGGER: user wants original figures kept → RULE: only remove manually-drawn markers; never remove original printed figures/content.
TRIGGER: text "broken" in Canva → RULE: baseline-clustered runs, embed Arial, preserve superscripts/fractions.
TRIGGER: histogram missing → RULE: content-aware dedup; don't let full-page watermark swallow content images.
TRIGGER: Mensuration handwriting baked into image → RULE: check if handwriting is an image overlay, not just vector strokes.
TRIGGER: e1 watermark visible → RULE: remove cyan fills (rf<0.15, 0.55<gf<0.85, 0.80<bf<1.0).
TRIGGER: coaching center watermark → RULE: remove gray fills (0.40-0.60, ≥50 items).
TRIGGER: user wants lean project → RULE: archive debug scripts and previews; deliver only core files.
TRIGGER: user wants download with V2 suffix → RULE: v2_name() appends " V2" to filename.
TRIGGER: user wants toggle for marker removal → RULE: --keep-ink/--keep-marks flags and UI toggles.
TRIGGER: user wants local web UI → RULE: app.py on port 8765 with start.bat launcher.
TRIGGER: user wants all PDFs tested → RULE: test_all.py runs all 18 PDFs both modes.
TRIGGER: user wants no bugs → RULE: test thoroughly and verify visually.
TRIGGER: user wants memory files → RULE: write MEMORY.md and LESS.md capturing state/decisions.
TRIGGER: user wants git push → RULE: commit all except OUTPUT and archived; push to origin main.
TRIGGER: cleaner rebuilt pages from extracted parts → RULE: clone the page and edit surgically; extracted Hindi Unicode is broken at the source (Identity-H), so Hindi must be pixel-copied from the render, never re-typed.
TRIGGER: pen strokes vs printed diagram strokes confusion → RULE: the pen tool always writes at 1.5001pt; classify ink by the 1.3-1.7 width band. Printed strokes are 0.14/1.0 or 2.25+.
TRIGGER: triangle sides (2.26pt, 3 items, black) deleted as "black handwriting" → RULE: multi-item alone does not mean ink; check the width band first.
TRIGGER: title-slide frame (1.5pt, single 're') deleted as pen → RULE: single-segment 're' outlines and long vertical lines are printed shapes; a long single stroke is ink only in a saturated pen colour.
TRIGGER: restored drawings gained black borders / wrong layering → RULE: Shape.finish needs color=None passed explicitly for "no stroke", and collateral restorations must be re-drawn in original document (z) order, not by area.
TRIGGER: table and question text sharing a baseline merged into one raster that copied pen marks → RULE: split visual lines into x-segments (gap > 25pt) before planning.
TRIGGER: superscripts drifted after line merge → RULE: group lines into rows by vertical overlap; in multi-line rows re-type span-by-span at exact origins, each fit to its own width; plain lines fit inside their own footprint.
TRIGGER: pen-colour detection failed on downscaled images → RULE: sample original pixels at a stride; never resample before measuring (blending destroys pure pen hues).
TRIGGER: Hindi glyph tops/anusvara clipped in rasters → RULE: pad the clip −4pt at the top; Devanagari renders above its span bbox.
TRIGGER: old code crashed off Windows → RULE: no platform font paths; base-14 fonts + pixel-copy only. Dependencies are just PyMuPDF + Pillow (requirements.txt).
TRIGGER: Canva check flags a join/collision → RULE: arbitrate against local renders/dict/stream + original comparison before any code change; MuPDF extraction and Canva's text panel both collapse real spaces and do not predict Canva's visual rendering.
TRIGGER: figure drawn on a template panel dropped by the 80x80 ink gate → RULE: an ink group overlapping an e1 panel by ≥30% of its bbox qualifies under 80x80, but only when its long paths are black (coloured working on a panel stays removed); never let a page-background fill expand a figure group.
TRIGGER: sweep upload fails on approval timeout → RULE: run Canva checks singly with the user present; freeze each sweep set under a unique suffix and never reuse one.
TRIGGER: coloured annotations survive because they touch a kept pen figure → RULE: chain ink groups PER COLOUR; a touching annotation of another colour neither earns nor inherits the figure exemption.
TRIGGER: big teacher working (purple option box, margin arrows) qualifies as an 80x80 "figure" → RULE: a pen group whose bbox overlaps ANY non-footer text line is teacher marking, never a figure; real question figures never print over text (verified across all protected figures).
TRIGGER: small figure (<80x80, off-panel) dropped by the size gate → RULE: a black group with ≥3 long paths qualifies below the 80x80 gate; coloured small groups stay removed (underbrace/formula working).
TRIGGER: closed-cycle endpoint test planned as a figure discriminator → RULE: measured backwards on real data (hand-drawn square False via stray degree-1 stroke; dense glyph chains True) — never use it; use colour + path-count rules instead.
TRIGGER: black group chains through the text area and loses its distant figure → RULE: on text-overlap rejection, drop only the overlapping members, re-chain the core and re-evaluate.
TRIGGER: small black strokes near a kept figure removed → RULE: they are figure labels (H&D p1 angle labels); keep black satellites within 25pt, remove coloured ones (circle-marks).
TRIGGER: Hindi sentence's math row survives as ghost line → RULE: superscripts sit up to ~5pt above the Hindi baseline and the trailing "?" is Calibri — widen the frag window to first_deva−8 and drop the math-font requirement on the Hindi baseline itself.
TRIGGER: question-number badge image deleted as pen image → RULE: keep any small image (≤45x26) left of the question column above the first option, regardless of pen-colour stats.
TRIGGER: table header recoloured red by the question rule → RULE: lines whose centre is inside a table grid zone keep their original colour.
TRIGGER: shifted options print over a kept pen figure → RULE: kept figure rects are shift obstacles, shared by both the text-surgery and drawing-shift plans (one dy).
