# Changelog

All notable changes to EyeScan.

## [Unreleased]

- **New main-window layout - left icon rail + selection action bar** (Concept A of
  `docs/UI-CONCEPT-A.md`). `eyescan_widgets.IconRail` adds a 58 px section rail
  (Library / Filters / People / Duplicates / Animals / Settings) with live count badges;
  a section only *sets* the same filter the list would (`App._on_rail_icon`), so the
  filter list is unchanged beside it. A selection-aware `ActionBar` under the grid
  surfaces **Protect / Move to folder / Sheets / Delete** - the same verbs the menus
  use, so the confirm dialog, the protect-skip and the undo manifest are untouched - and
  shows "N selected · size". Status bar moved from grid row 2 to row 3. Covered by
  `guitest.py` (rail sections, badges, action-bar enable/count).

- **Small faces in wide shots:** a seated subject in a courtyard (face ~1-2 % of the frame) was
  invisible at the 1536 px detection size even with tiles. `EyeEngine._rescue_small_faces`
  now takes every *person* box the object detector finds that has no face yet and searches
  its top 42 % on the full-resolution pixels (then the usual refine + BlazeFace check).
  Fixed 7 of 9 reported photos in `Shruti Marathe - 15-10-2026`; the other two are pure
  profiles (no mesh possible, no eyes to judge). Cache key gained `s1`.
- **No window jump:** `eyescan_widgets.Toplevel` hides every dialog on creation and shows it
  (already sized and centred by `fit_window`) from a 1 ms timer - `update_idletasks()` doesn't
  fire timers, so there is no top-left flash. Dialogs that grab input retry until visible.
- **Zoom to the face by default** when a photo is selected (preview and review window);
  Settings -> Appearance: on/off and *how much of the view the face fills* (30-90 %).
- **Box / Mesh / Off + Labels + Eyes** buttons float in the corner of the preview and the
  review window (switchable in Settings).
- **Tooltips on every button** with name, shortcut and what it does (`widgets.TIPS`,
  `auto_tips`; ambiguous labels such as the two "Reset" buttons get their own).

- **Six new features (cache schema 8):**
  1. **Best of burst** - `core.pick_score()` ranks the frames of a duplicate group: every
     open eye (-100 per closed face), no wink (-25), not looking away (-10), natural smile
     (+6), no cut-off face (-6), clear face (+sharpness/4, -20 if unclear); resolution only
     breaks ties; protected photos always win. `dup_score` / `dup_why` say why a frame won
     or lost. Filter: *Best of burst*.
  2. **People** - SFace face-recognition model (`model/face_recognition_sface_2021dec.onnx`,
     OpenCV Zoo, Apache-2.0, 38 MB) through `eyescan_gpu.OnnxModel`: **this is the model
     that runs on the GPU** (DirectML; measured 17.7 ms/face on the GeForce 840M vs 54.6 on
     the CPU). Scan workers cut aligned 112x112 crops (`eyescan_people.align_crop`, ArcFace
     template from the mesh landmarks); the main process embeds them, so there is one GPU
     session. Vectors are cached as int8/base64. `cluster_people()` = leader clustering at
     cosine >= 0.42 then merge of centres >= 0.50 (4 people / 6 clusters on 169 portraits
     from 4 subjects). Sidebar *Who is in the photos* (combinable), *People* window.
     Settings -> Detection -> *Recognise people*.
  3. **Blink rescue** - `core.blink_rescue()`: for a photo flagged closed, finds a frame of
     the same duplicate group where those same people (matched by face recognition, else by
     position) are open, without new blinks. Chip/tag *better frame*, review window
     button / **B**, context menu *Select the better frame*, CSV `better_frame`.
  5. **Looking away** - `FaceResult.off_camera` = head turn + eye gaze in the same
     direction. Verified on real portraits: positive `yaw` = head turned to image-left
     (so head direction = `-yaw`), positive `gaze_h` = eyes to image-right; a head turned one
     way with the eyes swung back to the lens is NOT away (that is the usual portrait
     pose). `AWAY_SCORE` 26 deg; the model reports ~+0.30 for "at the lens" (`GAZE_NEUTRAL`).
  7. **Can't judge** - `eye_occlusion()` = dark share of each eye window relative to the
     cheek brightness (visible eyes <= 0.6, sunglasses ~1.0; `HIDDEN_DARK` 0.72). Both eyes
     covered -> face state `hidden`, never flagged closed (the blendshapes are guesses
     there); a photo with only hidden faces gets verdict `hidden`. Manual overrides win.
  9. **Face cut off** - `FaceResult.cut_off`: share of the face outside / touching the frame
     edge (MediaPipe extrapolates the mesh past the edge, verified by cropping real photos).
  New filters: *Can't judge*, *Looking away*, *Face cut off*, *Eyes hidden*, *Best of
  burst*, *Better frame available*; CSV columns `looking_away_faces`, `eyes_hidden_faces`,
  `cut_off_faces`, `people_ids`, `burst_score`, `better_frame`.

- **Parallel scanning:** `EyeEngine.analyze_many(workers=N)` runs photos in N spawned
  worker processes (own models each). `core.auto_workers()` picks 1 for < 40 photos or
  < 4 cores, else `min(4, cores-1)`. Output is identical to the single-process path
  (verified on 89 photos). Settings -> Detection -> Performance, CLI `--workers`.
  Measured on a 4-thread laptop: ~11-20 % faster (pool start-up ~4 s); expect more on
  8+ cores. `eyescan.py` calls `multiprocessing.freeze_support()` for the frozen exe.
- **GPU layer (`eyescan_gpu.py`):** ONNX Runtime + **DirectML** for any DX12 GPU, CPU
  fallback at load and at run time. Settings -> Detection -> Performance -> *Processing
  device*: **Automatic** (default) / each installed GPU / **CPU only**. Automatic walks the
  adapters (read from DXGI, same order as DirectML's `device_id`) by priority - discrete
  NVIDIA, discrete AMD, other discrete, then integrated - and takes the first that a
  small benchmark shows >= 1.25x faster than the CPU, else the CPU. (On hybrid laptops
  device 0 is the Intel iGPU, which measured *slower than the CPU*.) A named GPU is
  honoured even if slower; a removed one becomes Automatic. Benchmark cached in settings
  (`gpu_bench`, re-run when adapters / ORT change). Used by ONNX models only - none ship
  yet (face recognition will be first). `--doctor` lists adapters, `--no-gpu` = CPU only.
- **3D logo + sharper icons:** the header showed a drawn flat "eye" that didn't match the
  brand. `assets/make_logo.py` now renders the six-colour iris ring as a polished 3D mark
  (round-section tubes with spherical caps lit per pixel - diffuse, specular, rim -, a
  glass lens, a bevelled glass tile, soft shadows) -> `icon.png` (512), `logo_header.png`
  (56, 3 rings, main window) and `logo_header_2x.png` (112, About / splash). The original
  flat art is kept in `docs/brand/`. `assets/make_icon.py` builds `EyeScan.ico` from the
  same renderer with fewer / thicker tubes at small sizes (4 rings >= 96 px, 2 at 48-64,
  1 at 16-40, even 60 deg sectors so every colour survives) and adds the 20 and 40 px
  sizes Windows asks for at 125 % scaling. Re-run both after changing the design.
  Explorer caches icons: rebuild the exe and clear the icon cache (or rename the exe).
- **Windows stay on screen:** `eyescan_theme.fit_window` / `work_area` size and centre every
  dialog inside the usable area (taskbar excluded) of the monitor the app is on; the main
  window's minimum size is clamped too. Settings pages scroll (`VScrolled`, scrollbar only
  when needed), the Settings / Confirm button rows can no longer be pushed off-screen.
  Covered by a 1280x680 check in `guitest.py`.
- **Findings (do not re-investigate):** MediaPipe's Python wheel is built with "GPU
  processing disabled" (`Delegate.GPU` fails), and model inference is only ~20 % of
  scan time; Pillow decode + resize dominate. A looser JPEG `draft` was 14 % faster
  but flipped 2 of 40 borderline verdicts (EAR 0.129 vs 0.138; focus), so it was
  reverted. Resampling filters feed the calibrated sharpness ratios - don't swap them.

## [1.0.1] — 2026-10-17

Versioning restarted at 1.0.1 (the earlier 1.x numbers were internal builds).

- **Credits & copyright:** (c) 2026 Devendra Kulkarni - DEeV.in (www.deev.in), All
  rights reserved. Constants live in `eyescan_core` (`CREDIT_LINE`, `WEBSITE`...).
  New About dialog (logo, clickable link), footer credit in the status bar, window
  icon, `--version` / `--doctor` banner, exe file properties (`version_info.txt`),
  installer publisher/copyright/licence page, `LICENSE.txt`. Icon in `assets\`.
  Contact sheets deliberately carry no credit.
- **Delete also removes matching RAW files** (CR2, CR3, DNG, NEF, ARW, ... same
  folder + same name) via a toggle in the confirm dialog; they go to the Recycle Bin.
- **Fix:** Review window showed no photo until zoomed - the first fit ran before the
  canvas had a size; it now re-fits on the first real size.
- **Fix:** animals need score >= 0.70 when a person is in the photo (IMG_1071 false dog).

## [1.6.0] — 2026-10-17

### Time-savers for hundreds of photos
- **Saved results (SQLite, `%LOCALAPPDATA%\EyeScan\eyescan.db`):** every photo's
  measurements are stored by path + size + modified-time + engine settings. A
  re-scan only analyses new / changed photos, a stopped scan resumes, and verdicts
  are recomputed from the current sensitivity (moving the slider never re-scans).
  *Scan* uses saved results; *Full re-scan* (Ctrl+F5) and *Re-scan selected* ignore
  them. Settings > Saved data shows size / count and can remove entries for
  missing files, forget the current folder, or clear everything (the file is
  VACUUMed, so it really shrinks). ~3.6 KB per photo.
- **Duplicates / near-duplicates (toggle):** 256-bit difference hash + grouping.
  Strict / Normal / Loose (18 / 30 / 42 bits - calibrated on real bursts: <=20 =
  identical frames, 25-36 = same pose, small movement). The "best" frame is kept
  (open eyes, clear face, highest resolution, protected first); the rest are *extra
  copies* with their total size shown. Filter, sort by group, select extras, sort
  or delete them (CLI: `--dups`, `--sort-dups`, `--delete-dups`).
- **Blurry / misfocused, judged on the face only:** new texture-independent focus
  score (detail lost when the face is slightly blurred; smooth skin and patterned
  clothes compare fairly). Calibrated by eye on 150 real faces: crisp = 10-30,
  clearly blurred < 6. *Misfocused* = unclear face while the background is >=
  1.8x crisper (focus landed elsewhere); otherwise *blurry*. Faces under 80 px are
  not judged. Filter + *Sort blurry / misfocused* (CLI `--sort-blurry`).
- **Protect (pose / required photo):** right-click > Protect (or P in Review).
  Protected photos stay flagged if they are closed, but bulk sort / delete always
  skip them (CLI too). Explicit deletes ask first.
- **Sizes everywhere:** delete / move / copy confirm dialogs show photo count,
  total size and space freed; the status bar shows the selection size; CSV has size.

### Detection accuracy
- **Both eyes, always.** The "Both eyes" option is gone: each eye is judged on its
  own (blink score fused with its lid gap) and BOTH must be closed. One closed eye
  is a **wink** (tagged, never flagged).
- **Turned / laughing faces:** the blink blendshape of the far eye reads ~0.1 on a
  turned head even when its lids are shut. EAR is now fused in: evidence =
  max(blink, 1 - EAR/0.25). On 146 earlier-labelled photos this finds 27 more
  closed-eye faces; on 250 other photos it adds only 6 flags, all of which are
  genuinely lowered / shut eyes.
- **Confidence:** "Needs a quick look" queue = closed but without strong evidence on
  both eyes; strong ones are *sure*.
- **BlazeFace validation is stricter (0.72)** and the detector runs more
  sensitively (0.25 / 0.30), which finds small and tilted faces in full-body poses
  that were missed. Cutoff chosen by viewing all 487 detections: hands, belly, a
  bare foot, a wood carving and a wall score 0.53-0.71; 99.8% of real faces score
  >= 0.72 (median 0.92).
- **Animal faces are not people:** a face inside a detected animal box (>= 60%) is
  dropped (BlazeFace confirms dog faces).
- **Looking-down lid gap is tunable** (Settings, default 0.13). 0.11 labelled
  laughing / squinting faces as "looking down" (43 of 146 picks vs 23 at 0.13).
- Photo dimensions are recorded correctly (a resize used to drop them).

### UI / UX - "Nature's Vibration" design system
Styled after MysticDigit / DowsingChartMaker: sage / coral / sun / ocean / lavender
palette, flat surfaces, 1 px hairlines, pill chips, uppercase micro-labels, Poppins /
Quicksand when installed. Custom ttkbootstrap themes (essence-light / essence-dark).
- Header with logo, folder, Scan / Stop, Review, Delete, Sheets, Settings, theme.
- **Filter rail** with live count pills: review queue (closed, needs a look, looking
  down, open, no face), focus, duplicates, people, content (animals, smiling,
  winking, protected). Eyes-closed sensitivity + toggles live under it.
- **Selection like Explorer / FastStone:** click, Ctrl-toggle, Shift-range,
  **rubber-band drag** (Ctrl adds, auto-scrolls), Ctrl+A / D / I, arrows with
  Shift-extend and Ctrl-focus, Home / End / PgUp / PgDn, Ctrl+Space, Ctrl+wheel =
  thumbnail size, F2 rename, Ctrl+C copies the files to the clipboard, Select menu
  (all eyes-closed, extra copies, blurry).
- **Viewer:** arrow keys move to the previous / next photo; **Space zooms to the
  face** (flagged face first) and again zooms out; Tab = next face. **Boxes are the
  default overlay; animals get their own box and label.**
- **Settings dialog** (Ctrl+,): detection, focus & duplicates, sorting folders,
  appearance (**box thickness, label size, per-category colours**, eye boxes),
  saved data.
- Inspector: per-face table, corrections, Protect, tags, focus / duplicate info.

**Final check on 427 real photos** (Bhagyashree, Navratri picks + review, Shruti,
Pooja, Buzzo; 474 faces): of the 146 photos an earlier script labelled closed-eyes,
118 are now attention-worthy (94 flagged closed + 24 looking-down kept; 8 have no
detectable face) vs 71 with the old rule; the other 98 photos flag 10 closed + 2
looking-down. Focus: 349 sharp, 28 misfocused, 1 blurry, 49 not judged (no / tiny
face). Duplicates (all 427): strict 6 groups / normal 11 / loose 24. Cache:
~1.9 KB per photo. These labels are weak, so treat them as sanity checks.

selftest: 139 tests. guitest: live scan + cache hit, selection incl. rubber-band,
keyboard (real Space / arrow events), zoom-to-face, filters, protect, delete,
settings, review.

## [1.5.0] — 2026-10-16

### New detection features
- **Looking down vs. closed:** head pitch (from the face matrix), the
  `eyeLookDown` gaze blendshape and the lid gap (EAR) are combined. A face whose
  blink score says "closed" but whose lids are still visibly apart while the
  gaze/head points down gets state `down` and the photo verdict **looking down** -
  it is kept, not flagged (toggle: *Keep looking-down photos*, `--no-keep-down`).
  Calibrated on a real shoot: lowered-but-shut lids have EAR < ~0.09, real
  downward glances 0.11-0.20. These are heuristics - use *Mark as...* to correct.
- **People count & group tags** (solo / pair / group 3+) from confirmed faces plus
  the object detector's body count.
- **Animals** (bird, cat, dog, horse, sheep, cow, elephant, bear, zebra, giraffe)
  via EfficientDet-Lite0 (7 MB, bundled). Verified on real dog photos.
- **Smile / mouth-open / sharpness** per face; "Blurry" tag (scale-normalised
  Laplacian variance; real photos 90-520, mildly blurred 3-13, cutoff 40).
- **Corrections that persist:** per-photo (keep / flag) and per-face
  (open / closed / not a face) in `%LOCALAPPDATA%\EyeScan\overrides.json`.

### Contact sheets
`eyescan_report.py`: paginated JPG sheets, long side >= 4000 px (default 6000 x
4000, 3:2; also 4:3, 16:9, 5:4, portrait), quality 95, 4:4:4, 300 dpi, dark or
light. *Photos* mode (whole frame) or *Faces* mode (crop with the eye contours
outlined, for checking eyes in group shots). GUI: Sheets button / Ctrl+Shift+S;
CLI: `--sheet DIR --sheet-mode faces --sheet-ratio 3:2 --sheet-long 6000`.

### UI / UX overhaul
- Menu bar, toolbar, filter sidebar (status / people / content with live counts),
  Settings tab, three-pane layout, status bar with ETA.
- Virtualised thumbnail grid with **multi-select** (click, Ctrl, Shift, arrows,
  Ctrl+A), thumbnail-size slider, sorting, search, detail list view.
- **Right-click context menu**: review, open, show in folder, copy path / name,
  mark as keep / flag / per-face, move / copy to output folder, **delete
  (Recycle Bin)**, remove from list, properties.
- **Delete** to the Recycle Bin only (never permanent), behind a confirm dialog
  whose default button is Cancel; also *Delete all flagged*.
- Inspector: per-face table (state, blink, EAR, pitch, gaze, sharpness) and
  correction buttons. Review window: K keep, F flag, M move, Del delete.
- Fixed: tile text colour used the theme *background* (names were invisible).

### Other
- CSV export shared by GUI and CLI with the new columns; CLI `--delete --yes`.
- `sort_results(..., only_flagged=False)` for sorting a selection.
- `--doctor` reports all three models. Tests: selftest 98, guitest incl. a live
  scan, context menu, overrides and delete.

## [1.4.0] — 2026-10-15

### Audit: accuracy, robustness, build

**Detection accuracy**
- Photos are now decoded at up to 3072 px (was 1536) so the per-face refinement
  crop really is higher-resolution; tiled detection still runs on a 1536 copy.
- New eye-aspect-ratio (EAR) cross-check from the mesh. The blink blendshape
  stays primary; EAR only settles borderline cases (vetoes a weak "closed" when
  the lids are clearly apart, e.g. looking down; rescues a near-miss when they
  are clearly shut). Constants: `EAR_*` in `eyescan_core.py`. **Not yet tuned on
  a labelled photo set** - adjust if you see systematic misses.
- De-duplication also merges a box mostly *inside* a larger one (tile-cut faces).
- Rotation fallback: images with no face are retried at 90 / 270 degrees.

- **Face validation (fixes feet detected as closed faces):** every face found by
  FaceLandmarker is confirmed by an independent detector (BlazeFace,
  `model/blaze_face_short_range.tflite`, 230 KB, bundled) run on the face crop;
  unconfirmed faces are dropped. Verified on `IMG_9190.JPG` (bare foot, blink
  0.52/0.86) - no longer detected; recall unchanged on a 146-photo closed-eyes set.

**Bugs fixed**
- Move-sorting twice overwrote `_eyescan_manifest.json`, losing the earlier
  undo data; manifests now merge. Partial undo keeps only failed entries.
- Recursive scans re-scanned the output folder (sorted photos came back as
  duplicates); it is now skipped.
- Manifest/settings writes are atomic; model download has a timeout, size check
  and cleanup.
- CLI CSV now covers every file (including errors), not just flagged ones.
- Scan loop no longer dies on one bad file; unexpected failures are reported.

**Performance**: image decode is overlapped with detection (`analyze_many`).

**Build**: `build_exe.bat` now uses `EyeScan.spec`, runs the self-tests first,
smoke-tests the built exe (`--doctor`), falls back to any `python` if the
`py -3.13` launcher is missing. UPX disabled (can corrupt DLLs / trigger AV).
Do not exclude `matplotlib` from the spec: mediapipe imports it at load.

**Crash diagnostics**: unhandled errors (main, Tk callbacks, worker threads,
native faults) are written to `%LOCALAPPDATA%\EyeScan\crash.log` /
`native_crash.log`, and the crash dialog shows the traceback. Scan options are
snapshotted on the main thread before the worker starts. guitest.py now
runs a live scan. `installer.iss` + `build_installer.bat` make a Setup.exe.

selftest.py: 57 tests.

## [1.3.0] — 2026-10-07

### Robust face-detection overlay

The preview and Review window no longer draw plain rectangles. They now render
the actual MediaPipe face mesh:

- **face oval contour**, drawn as a smooth closed outline (red = eyes closed,
  green = open);
- **both eye loops** with a translucent red tint over closed eyes;
- **iris rings**;
- a **label chip** per face — `#1  CLOSED  0.81` — so multiple faces are
  unambiguous;
- a small **legend** in the preview.

Three overlay styles, selectable in the main window and Review and remembered
between runs: **Mesh** (default), **Box** (the previous red/green + yellow
eye-box look), and **Off**. Rotation, zoom and pan all map the overlay
correctly.

Thumbnails now also show a neutral marker box where the detected face is.

Core additions: `FaceResult.outline`, `FaceResult.eyes`, `FaceResult.iris`
(sourced from indices defined in `eyescan_core`). Settings gained an
`overlay` key.

### Verified
- `selftest.py` — **41/41** (adds outline / eye-loop / iris population tests).
- `guitest.py` — GUI SMOKE OK with the new controls.
- Overlay rendered through the real GUI code path and visually checked in
  Mesh (closed + open), and Box styles — contours land exactly on the faces.
- Frozen exe: scan OK, GUI launches and stays alive.

## [1.2.0] — 2026-10-07

### Robust face detection (fix: "no face" when a face *is* present)

MediaPipe's FaceLandmarker resizes the **whole** image down to a small model
input, so on a 26 MP photo a face that occupies ~15% of the frame shrank below
what it could detect — reported as *no face* (e.g. `IMG_9214`, `IMG_9194`,
`IMG_9224`–`IMG_9228`).

The engine now:
- runs the detector on the image **and on overlapping 2×2 tiles** (an optional
  3×3 "Deep" pass too), then merges the results and drops duplicates by IoU;
- uses **two confidence levels** — a sensitive full-image pass (0.40) and a
  **strict tiled pass (0.60)**. Tiles make real faces large (high confidence)
  while face-shaped background/hands are rejected. (A false positive on a hand
  in `IMG_9203` was traced and eliminated this way.);
- **refines every detection** by re-running the detector on a padded,
  full-quality crop, giving accurate landmarks and blink scores;
- reports each face with its bounding box, an eye-region box, and scores.

### Performance
- Detection resolution is capped (default 1536) and JPEGs are decoded with
  `Image.draft`, so a 26 MP photo went from **~930 ms to ~430 ms** with
  identical results.
- Thumbnails decode at 512 px and the preview at 2800 px, so the grid and
  pan/zoom stay responsive on large photos.

### Options
- GUI: **Robust** (tiled search, on by default) and **Deep** (3×3, off)
  toggles.
- CLI: `--fast` (disable tiling), `--deep`, `--max-dim`.
- `min_face_area` default lowered to 0.002.

### Verified
- `selftest.py` — **38/38** (adds `_box_of` / `_area` / `_iou` / dedupe /
  eye-box coverage).
- The seven reported images now each detect exactly **1 face** with a correct
  verdict; boxes visually confirmed to land on the faces; the hand
  false-positive is gone.
- Full 28-photo folder scanned without error.
- Frozen exe: `--doctor` OK; `--scan --csv` flags the previously-missed
  frames correctly; GUI launches and stays alive.
- `guitest.py` still passes with the new toggles.

## [1.1.0] — 2026-10-07

Major UI overhaul ("modern + culling") plus a safer workflow. Split into
`eyescan_core.py` + `eyescan_gui.py` with `eyescan.py` as the entry point.

### UI
- **ttkbootstrap** themed interface with a **dark / light** toggle (falls back
  to plain ttk if the theme engine is missing).
- **Thumbnail grid** view (letterboxed, verdict-bordered) alongside the
  sortable **list** view; toggle top-right.
- **Filter tabs with live counts**: All / Eyes closed / Eyes open / No face /
  Other.
- **ZoomCanvas** preview: scroll-to-zoom at the cursor, drag-to-pan,
  double-click-to-fit, `+ − Fit 1:1`, and rotate ±90°. Handles landscape and
  portrait correctly (EXIF-corrected, aspect preserved).
- **Eye-crop zoom** panel — enlarged ear-to-ear crop per detected face, so
  borderline (~0.5) cases can be judged at a glance.
- Detected faces boxed in the preview (red = closed, green = open) with the eye
  region outlined in yellow.
- **Review (culling) window**: large photo, keyboard-driven
  (`←/→`, `K` keep, `M` move, `R` rotate, `+/-/0`, `Esc`), verdict badge,
  eye crops, per-face scores.
- Splash overlay while the model loads.

### Workflow / safety
- **Live sensitivity** — the slider re-classifies existing results from cached
  scores with no re-scan.
- **Settings persistence** (`%LOCALAPPDATA%\EyeScan\settings.json`): folder,
  threshold, both-eyes, action, output name, theme, view, window geometry.
- **Recent-folders** dropdown.
- **Dry-run dialog** before Copy/Move listing exactly what will happen.
- **Undo move** via `_eyescan_manifest.json` written beside the moved files.
- Status bar with counts and progress.

### Other
- `--doctor` CLI command reports the runtime environment (frozen?, versions,
  model path) and can write it to a file — essential for a `--windowed` exe.
- `guitest.py` headless GUI construction smoke test.
- Core `sort_results` now returns a `SortOutcome`; added
  `reclassify`/`apply_threshold`, manifest `write/read/undo`.
- Build adds `--collect-all ttkbootstrap`.

### Verified
- `selftest.py` — **30/30** (adds re-classify + manifest/undo coverage).
- `guitest.py` — whole UI builds; grid/list, preview zoom/rotate, review
  window, theme + view toggles all exercise cleanly.
- Frozen exe: `--doctor` confirms bundled mediapipe 1.1.0, ttkbootstrap 2.2.3
  and the model; `--scan --csv` detects faces; GUI launches and stays alive.
- Still not tested against a real closed-eye photo (threshold tuning pending).

## [1.0.0] — 2026-10-07

Initial release.

- `EyeEngine` — MediaPipe FaceLandmarker blendshape-based closed-eye detection
  (`eyeBlinkLeft` / `eyeBlinkRight`), with threshold, `require_both`, and
  `min_face_area` controls.
- Tkinter GUI: folder picker, recursive option, sensitivity slider,
  report/copy/move modes, output-folder name, progress bar, results table with
  sortable columns, image preview with detected faces boxed (red = closed,
  green = open), CSV export, and "sort flagged now".
- Headless CLI: `--scan`, `--threshold`, `--require-both`, `--min-face`,
  `--recursive`, `--csv`, `--move`, `--copy`, `--out`.
- `sort_results()` preserves sub-folder structure and never overwrites.
- Model auto-download on first run; bundled into the exe.
- `selftest.py` — 19 headless assertions.
- `build_exe.bat` — PyInstaller one-folder `--windowed` build.
- `Run-EyeScan.bat` — run from source.
- Project context: `AGENTS.md`, `docs/PROJECT.md`, this file, `docs/ROADMAP.md`.
- Dev tooling: `Launch-OpenCode.bat`, `backup.bat`, `restore.bat`.

### Verified

- 19/19 self-tests pass.
- Real portrait detected (1 face, blink 0.18/0.17 → open).
- Frozen exe loads the bundled model, detects faces, writes CSV.
- GUI window constructs and stays alive.
- Not yet tested against a real closed-eye photo (threshold tuning pending).
