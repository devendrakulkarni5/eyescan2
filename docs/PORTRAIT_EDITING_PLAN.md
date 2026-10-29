# Portrait Editing — plan & ideas

Status: **proposal + HTML prototype** (`docs/prototype/portrait_editor.html`). Nothing in the app changes yet.

## Goal

After EyeScan has found the keepers (eyes open, sharp, best of burst), let the user
**retouch portraits** — one photo at a time, or a whole selection in bulk — and write the
results to a **new folder**. Originals are never touched.

## Feature list

### Global (auto, per face, bulk-friendly)
| Feature | How it works |
|---|---|
| Skin smoothing | Edge-preserving smoothing (guided/bilateral filter) on the skin mask only, with a "keep texture" slider that adds fine detail back (poor man's frequency separation) |
| Frequency separation | Split into low (colour/tone) + high (texture); smooth low, leave high → no plastic look |
| **Auto-heal** (face-detected) | Landmarks give a face mask minus eyes/brows/lips/nostrils. Inside it, find small dark or red blobs vs. the local skin mean, keep only compact blemish-sized ones (rejects hair, wrinkles, folds), fill from clean neighbouring skin. See "Auto-heal & auto dodge/burn" below |
| **Auto dodge & burn** (face-detected) | *Even out*: lift areas darker than their surroundings, calm hot spots. *Contour*: landmark-guided light — dodge forehead centre, nose bridge, cheek apples, chin, under-eyes; burn cheek hollows, under-chin, nose sides and the face edge |
| Under-eye lift | Brighten + de-saturate a soft band under each eye (landmarks) |
| Eye enhance | Brighten sclera, add contrast to iris, mild sharpen on eye region |
| Teeth whitening | **Teeth only**: inner-lip landmarks (mouth opening, shrunk away from the lips) × a tooth-colour gate (bright, not red, not saturated) so lips, gums, tongue, the dark mouth interior and eyes are never touched; then lower yellow + lift L. The Whiten brush uses the same mask |
| Eye / mouth protection | Eyes, brows and lips (grown ~2.5 % of face width) are masked out of skin smoothing and glow ("Protect eyes, brows & mouth", on by default); heal / even-out / contour already respect it. Shown in pink under "Show detections" |
| Skin tone even-out | Reduce redness/blotches in chroma channels (Lab a/b) inside skin mask |
| Soft glow | Orton-style screen blend of a blurred copy, skin only |
| Warmth / exposure | Tiny tone controls so a preset can stand alone |
| Face-aware sharpen | Unsharp on eyes/lips/hair, never on skin |

### Manual brushes (single-photo editor)
Soften · Dodge · Burn · Heal (texture from source, colour from destination) · Whiten · **Guard** (click a bindi/mole/mark to exempt it from every auto step).
Later: Clone, Liquify (jaw/nose — opt-in, off by default), Red-eye, Stray-hair remover.

### Safety rails (important for portraits)
- "Natural / Beauty / Glam" presets; strengths capped so faces never look waxy.
- Always write to a **new folder** (`<folder>\Edited`); filename `name_edited.jpg`; collisions get `_2`.
- Keep EXIF + ICC profile; JPEG q95, 4:4:4.
- Recipe JSON sidecar (`name_edited.recipe.json`) → re-run later at different strength.
- Before/After hold-to-compare in every dialog.

## Open-source building blocks

| Need | Option | Notes |
|---|---|---|
| Face landmarks (eyes, lips, oval) | **MediaPipe FaceLandmarker** | Already in EyeScan. 478 pts → skin, eye, lip, teeth masks. In-browser: `@mediapipe/tasks-vision` from jsDelivr |
| Person/skin segmentation (in browser) | **MediaPipe ImageSegmenter, selfie multiclass** | In the prototype now: hair / body-skin / face-skin / clothes / accessories |
| Better skin/hair/teeth masks | **BiSeNet face-parsing** (ONNX) | 19 classes (skin, brows, eyes, lips, teeth, hair, neck, cloth). Run through `eyescan_gpu.OnnxModel` (DirectML) |
| Edge-preserving filters | numpy guided filter / `cv2.bilateralFilter` / `cv2.edgePreservingFilter` | `cv2` is already shipped because MediaPipe imports it (see AGENTS.md) |
| Heal / inpaint | `cv2.inpaint` (Telea), `cv2.seamlessClone` (Poisson) | Good for spots; large objects → LaMa ONNX |
| Large-object removal (optional) | **LaMa** ONNX | ~200 MB, GPU recommended — optional download like the other models |
| Face restoration (optional) | **CodeFormer / GFPGAN** ONNX | For soft/low-res faces; fidelity slider; off by default |
| Colour science | scikit-image / OpenCV Lab conversions | Chroma-only redness fix |
| Browser prototype extras | JSZip (cdnjs) for ZIP output, File System Access API for "new folder" | Prototype only |

## Architecture in EyeScan

```
eyescan_retouch.py     # pure numpy/cv2: Recipe dataclass, apply(img, recipe, faces) -> img
eyescan_parsing.py     # BiSeNet face-parsing via OnnxModel (cached masks per photo)
eyescan_gui.py         # RetouchWindow (ZoomCanvas + tool rail + sliders), bulk dialog
selftest.py            # recipe round-trip, mask sanity, "amount 0 == identity", bulk naming
```

- **Recipe = JSON** (global sliders + optional strokes list). Single editor edits one;
  bulk applies one recipe to N photos. The HTML prototype already works this way
  (strokes are replayed on top of the global result, so sliders stay live and undo is a pop).
- **Radii are relative** (`radius × width/1000`) so a recipe looks the same on a 12 MP and a 45 MP file.
- **Bulk path** reuses the `scan_files` worker-process pattern: workers decode → retouch → encode;
  main process only reports progress. Faces/landmarks come from the existing result cache,
  so retouching a scanned folder needs **no second detection pass**.
- **Output**: `core.write_retouched(...)` creates `Edited\`, writes a `_eyescan_retouch.json`
  manifest (like the sort manifest) → one-click **undo = delete the written files**.
- Bump `SCHEMA_VERSION` only if cached face data changes (it shouldn't; masks are derived).
- Per-face strength: scale smoothing by face size (tiny faces get less) and skip faces flagged
  `hidden` / `cut_off`.

## Auto-heal & auto dodge/burn (face-detected)

Both run **per detected face** from the 478 FaceLandmarker points EyeScan already caches
(in the browser prototype: `@mediapipe/tasks-vision` from jsDelivr, model from Google's model bucket).
No face found → these steps are skipped and reported ("N photos had no face").

**Masks (from landmarks)**
- `region` = the landmark oval **grown** (≈ 1.3× wider for ears, 1.5× higher for forehead, + a neck ellipse), widely feathered (~5 % of face width). Inside the oval it counts fully; outside only skin-coloured pixels count, so hair/background are untouched and the effect fades out instead of stopping at an edge. (The raw FaceMesh oval stops at the hairline/cheeks, which left a visible seam.) The prototype's "Show detected faces" tints this region.
- **Full forehead**: FaceMesh's oval stops part-way up the forehead, so the face outline is extended along the head's "up" axis: with the AI mask it walks up from each top point until hair/non-skin starts (so bangs and high hairlines are respected); without it, a default +16 % of face height. Cheeks, jaw and chin get the same treatment outward (up to 10 % of face width sideways, 7 % of face height at the chin) so the outline sits on the real skin edge, not the raw landmark line. The extension is smoothed along the outline. The review overlay draws this extended outline.
- **Edge-to-edge face (AI mask)**: with segmentation, the face is the whole connected *face-skin* area that touches the landmark face (cheek to cheek, hairline to chin), united with the landmark oval minus hair; the oval's own edge is no longer used. The overlay draws this real edge as a green line. Moles/marks are restricted to this face area (necklace beads on the neck and jewellery are not marks); nose wings get the same keep-out as the nose tip.
- **Body skin**: all skin-coloured pixels *connected* to the face (neck, chest, shoulders, arms, hands) are added, feathered, so heal / even-out run on the whole visible skin and tone doesn't change abruptly at the jaw. Contour (face shaping) stays on the face only. Hair highlights that pass the colour test are a known false positive → face-parsing replaces this in Phase 3.
- **Colour safety**: light changes are applied as a *gain* on all channels (not added, not "toward white"), so they never desaturate or tint skin.
- `exclude` = eyes, brows, lips (grown ~5 % of face width) + nostril discs.
- `skin weight = face × (1 − exclude)` gates every auto step, so eyes and lips are never touched.

**Auto-heal**
1. Inside the skin weight, compute local mean luminance and redness (a normalised-convolution, so eyes/lips don't contaminate it; radius ≈ 3 % of face width).
2. Score = darker-than-neighbours + 0.8 × redder-than-neighbours; threshold set by the *Heal sensitivity* slider.
3. Connected components → keep only blemish-sized (0.002–0.4 % of face width², scale-free), compact (aspect ≤ 2.6, fill ≥ 0.4) blobs. Lines (hair, wrinkles, folds) and big areas (dark circles, shadows) are rejected.
4. Dilate + feather each blob, fill it from the clean skin around it, blend by *Auto-heal* strength.
5. Python version: same logic with `cv2.inpaint` / `cv2.seamlessClone` and a pore-texture add-back; list of healed spots is written to the recipe sidecar for review/undo.

**Auto dodge & burn**
- *Even out*: `ref` = skin-weighted luminance at ~22 % of face width, `local` at ~3 %; lift where `ref − local > 0` (shadows, dark circles, nasolabial), calm half as much where brighter (shine). Capped at ±25 levels.
- *Contour*: a signed light map made of Gaussian blobs placed relative to landmarks and rotated with head roll — positive = dodge, negative = burn — minus an edge band (face oval vs. its blurred self) so the face edge recedes slightly. Applied multiplicatively (burn) / toward white (dodge) at low strength.
- Both are slider-driven (0–100) and preset-driven (Natural ≈ 60 / 35 / 25, Glam ≈ 90 / 65 / 60), so they work identically in single and bulk modes.

### Protecting deliberate marks (bindi, tilak, sindoor, moles, gems)

Auto-heal must never "fix" a bindi. Healing is not the only risk: skin smoothing, glow and dodge/burn would also fade or blur a mark, so protection is a **do-not-touch map** that every auto step respects.

| Mark | How it is recognised |
|---|---|
| **Bindi / kumkum / tilak** | *Midline zone* from landmarks 9 → 10 (between the brows up past the hairline), rotated with head roll. Inside it: a saturated red / magenta / purple blob (sat > 0.68, R > G+40) or a clearly dark/maroon compact dot → protected |
| **Sindoor** (line in the hair parting) | Same zone, saturated-colour rule, *any* shape up to ~3 % of face-width² (a line, unlike a blemish). Detected by absolute colour because the neighbourhood there is hair, not skin |
| **Moles / beauty marks / kajal dots** | Strong or weak blob that is **dark but not red** (mean darkness ≥ 12–20, red part < 60 % of it). Pimples are red-dominant, moles are not. Option *Keep moles* (default on) |
| **Gems, stick-on stones, nose-pin stones, other strong marks** | Very high contrast compact blob outside the zone (score > 60, well above the heal threshold) → *Protect other strong marks* |
| **Anything else** | **Guard tool**: click to protect a circle, click again to remove; stored per photo (normalised coordinates) in the recipe sidecar |

How it is enforced: marks are grown by ~1 % of face width and feathered into a `guard` map. `guard` (1) removes the area from heal candidates **and** from the heal fill source (so the bindi is never sampled either), (2) is subtracted from smoothing, glow, even-out and contour strength, (3) is drawn in review as labelled cyan rings (bindi / sindoor / mole / mark / user) so a bulk run can be checked at a glance.

Defaults protect everything; toggles let someone who *does* want a mole healed turn that off. Nose pins and the nostril area were already excluded by the landmark mask. Pimples (red, low-contrast) are still healed.

**Known limits**: a skin-coloured or very faint bindi (e.g. a sandalwood tilak) may not be detected → use the Guard tool or lower the *Heal sensitivity*; a large, very red pimple in the forehead zone can be mistaken for a bindi (it is then simply left alone — safe failure); forehead stick-on patterns wider than ~3 % of face-width² are not recognised automatically.

**Known limits** (general): freckles/moles are dark compact blobs too — a *Keep freckles* guard (skip blobs smaller than N px, or ask per photo) is needed; beards and heavy makeup raise false positives; very small faces (< ~80 px) are skipped; side profiles give weaker landmarks. Always show the healed-spots overlay in review before bulk-saving.

## AI skin segmentation (in the prototype now)

MediaPipe's *selfie multiclass* segmenter (free, ~16 MB, same CDN/runtime as the landmarks) labels every pixel **hair / body-skin / face-skin / clothes / accessories**. It replaces the colour-range skin test everywhere:
- **Skin map** = body-skin + face-skin; **non-skin** = hair + clothes + accessories (jewellery, earrings, necklaces).
- **Edge refinement**: the model works at 256 px, so its masks are upsampled and snapped to real photo edges with a guided filter (guide = luminance, radius ≈ 0.4 % of width). Result: hair strands, earrings and the jaw line get clean mask edges instead of a blurry 256 px blob.
- Used by: skin-only smoothing/glow, the face/body retouch region (hair strands and jewellery over the face drop out), the "must be surrounded by skin" test for moles/marks, and the connected-skin region (neck, chest, arms, hands).
- Fallback: if the model can't load (offline), everything falls back to the colour-range logic; the status bar says *AI skin mask* or *colour skin mask*, and a checkbox lets you A/B them.
- Python app: same job with BiSeNet face-parsing (19 classes: adds brows, lips, teeth, eyes, ears, neck) through `OnnxModel`; it also gives exact masks for teeth whitening and eye enhance.

## Studio-grade quality bar (what "complements Photoshop" means)

The HTML prototype is a **proof of concept in 8-bit sRGB**. To be genuinely studio-grade and to hand off cleanly to Photoshop/Capture One/Lightroom, the production tools need:

| Area | Industry expectation | Plan |
|---|---|---|
| **Bit depth & colour** | 16-bit pipeline, wide-gamut (AdobeRGB/ProPhoto/Display-P3), ICC profile preserved and embedded | Python engine in float32 numpy; read/write ICC via Pillow `ImageCms`; 16-bit TIFF/PNG export, JPEG q95 4:4:4 |
| **Linear-light maths** | Smoothing, glow, dodge & burn done in linear light so highlights don't band or shift hue | Convert to linear for filtering/D&B; keep gain-based D&B (already hue-safe) |
| **True frequency separation** | Gaussian low/high split, high layer in Linear Light, adjustable radius | Replace the guided-filter shortcut with an optional real FS mode (low = Gaussian, high = original − low), exposed as "Pro mode" |
| **Non-destructive & layered** | Retouching as layers/masks, not baked pixels | Recipe JSON (done) **+ layered PSD export**: Original · FS Low · FS High (Linear Light) · D&B (50 % grey, Soft Light) · Heal layer · Skin/Protect masks (`psd-tools` in Python, `ag-psd` in the browser). Open in Photoshop and keep editing |
| **Masks you can trust** | Hair, skin, teeth, eyes, lips masks, with refined edges | BiSeNet parsing + guided-filter refinement (prototype shows the approach); export masks as PSD channels |
| **Natural results** | No plastic skin; texture preserved; strength per area | Texture-keep slider (done), per-region strengths (skin / under-eye / forehead), presets capped |
| **Colour accuracy checks** | Skin-tone safe adjustments; no tint | Gain-only light edits (done); skin-tone vector-scope check in review |
| **Review tools** | 100 % view, before/after split, overlays, clipping warnings, histogram | Hold-to-compare (done), overlays (done); add split view + histogram + highlight/shadow clipping |
| **Pro input** | RAW/DNG/HEIC, tethered folders, pressure-sensitive pen | Python app already reads HEIC; RAW via `rawpy`; Pointer Events pressure in the browser brush |
| **Batch at scale** | Resumable, deterministic, sidecars, no overwrite | Existing EyeScan worker/manifest/undo pattern; full-resolution, per-photo sidecar |

Positioning: EyeScan does the **fast, consistent 80 %** (cull → protect marks → auto retouch → bulk) and hands off a **layered PSD / 16-bit TIFF** for the last 20 % in Photoshop — it doesn't try to replace it.

## Prototype status (tested in a real browser)

Tested end-to-end in Chromium with real portraits: face + AI segmentation load, presets, every slider (typed values, reset, double-click), all six tools with real mouse input (soften, dodge, heal with source pick, guard, undo), overlays, JPG export, bulk ZIP, phone layout. Console stayed free of errors.

**Shoot-folder test (16 spread-out 26 MP photos from `Navratri Shoot - 03-10-2026\Pradnya` and `\Sanika`):** no script errors; faces 104–415 px wide at the 1920 px working size; healed spots 1–14 per photo; bindi/mole/nose-pin preserved, ≤ 2 bindi and ≤ 2 moles per face. 5 of 16 had no face found by the landmarker alone (full-body shots) → added a **small-face fallback**: crop around each AI face-skin blob, zoom to 640 px, run the landmarker, map back. 4 of those 5 are now found; the last is a seated full-body shot in sunglasses with a ~60 px face, correctly reported as "no face found" (EyeScan skips faces < 80 px too). For such shots use the 3840 px working size or the Python app at full resolution.

Bugs found and fixed during that pass: crash when loading a photo (`exc` undefined), empty number box zeroing a slider, keyboard shortcuts dying after clicking a switch, stale export right after a slider move, "Updating…" stuck without a photo, Guard circles not undoable, preset row wrapping, over-eager mole detection (pores/shading), bottom-bar wrapping.

Performance work: two-speed updates (560 px preview + full-quality rebuild on pause), cached face/skin/smoothing data, half-resolution guided filters and blemish detection, low-res blurs for smooth masks, lazy brush/overlay data. Slider-only changes rebuild ~3× faster than at the start; plain JS per-pixel work on a 2.5 MP image is still the limit, so **Web Worker / WASM processing** is the next step for the web build and the Python app does this in worker processes anyway.

## Roadmap

1. **Phase 1 – engine + bulk (≈ 1 week)**: `eyescan_retouch` with smoothing, even-tone, teeth/eye
   enhance, glow; presets; bulk dialog with output folder + preview of 3 sample photos; tests.
2. **Phase 2 – single-photo editor**: RetouchWindow with brushes (soften/dodge/burn/heal/whiten),
   zoom-to-face, undo stack, hold-to-compare, save recipe.
3. **Phase 3 – smart masks**: BiSeNet parsing (hair/skin/teeth precision), auto blemish detection.
4. **Phase 4 – optional heavy models**: LaMa object removal, CodeFormer face restoration.
5. **Phase 5 – web/mac**: the HTML prototype can become the macOS/web front end (see `PLATFORMS.md`).

## Risks / decisions to make

- **Look**: over-smoothing is the #1 complaint → default preset "Natural", strength cap, texture slider on by default.
- **Performance**: guided filter on 24 MP in numpy ≈ 1–2 s/photo; fine for bulk, editor works on a ≤ 2000 px proxy and renders full-res on save.
- **Ethics/consent**: no body/face reshaping in bulk, liquify only in the manual editor.
- **Model size**: keep BiSeNet (~50 MB) optional-download; fall back to the YCbCr skin mask + landmarks.
- **Skin-tone fairness**: the prototype's colour-range mask is only a placeholder — it is weaker on very dark/very light skin. Landmarks + face parsing replace it in Phase 3. Test on diverse shoots before shipping.

## Prototype

`docs/prototype/portrait_editor.html` — single file, photos never leave the device. Open it by double-click
(Chrome/Edge); the face model is fetched once from a CDN, so the **auto** features need internet the first time.
Demonstrates **auto-heal and auto dodge/burn on detected faces** (sliders + "Show detected faces & healed spots"), and: skin-only guided-filter smoothing with texture keep, glow/tone, skin-mask view,
brushes (soften, dodge, burn, heal with Alt+click source, whiten), stroke undo, hold-to-compare,
export, preset buttons, recipe JSON copy, and a **bulk run to a new "Edited" folder**
(File System Access API in Chrome/Edge; ZIP fallback via JSZip from cdnjs).
