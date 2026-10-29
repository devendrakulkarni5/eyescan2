# EyeScan — project context

## Purpose

Sort through a folder of photos and pull out the ones where someone has their
eyes closed (blinking, mid-laugh, etc.), so you don't have to open them one by
one. It can just report, or physically move/copy the bad shots into a
sub-folder.

## How detection works

1. Each image is opened with Pillow (JPEGs decode at a reduced size via
   `Image.draft`), EXIF-orientation corrected, and capped to ~1536 px on the
   long side for detection.
2. **Face search** (this is what makes it robust):
   - a sensitive pass over the whole image (detection confidence 0.40),
   - a strict pass over **four overlapping tiles** (confidence 0.60), and
     optionally a 3×3 grid ("Deep"),
   - results are merged and **de-duplicated** (IoU), then each face is
     **refined** by re-running the detector on a padded crop for accurate
     landmarks.
3. MediaPipe's **FaceLandmarker** returns the face mesh plus a **blendshape**
   vector per face.
4. Two blendshape channels matter: `eyeBlinkLeft` and `eyeBlinkRight`, each
   0.0 (wide open) → 1.0 (fully closed).
5. A face counts as *closed* when the blink score is ≥ the threshold
   (default **0.50**). With *Require BOTH eyes closed* the lower of the two
   scores is used, which ignores winks.
6. Faces smaller than 0.2% of the image are ignored (too small to judge).
7. Verdict per image:
   - `closed`  — at least one kept face is closed → **flagged**
   - `open`    — faces found, none closed
   - `no-face` — nothing detected
   - `error`   — unreadable / undecodable file
   - `skipped` — not an image extension

### Why the tiles?

MediaPipe's detector resizes the **whole** image to a small model input. On a
26 MP photo a face that is only ~15% of the frame ends up smaller than the
detector can see, and the photo is wrongly reported as *no face*. Running the
detector on overlapping tiles makes each face larger relative to the input, so
those faces are found. The stricter tile confidence keeps face-shaped
background regions (e.g. a hand and bangle in `IMG_9203`) from becoming false
positives.

## Why blendshapes, not an "eye cascade"

Older tools (Haar `haarcascade_eye`) just report whether an eye-shaped
rectangle was found, which misfires on glasses, glasses glare, and partial
profiles. Blendshapes are regression outputs from a face mesh model and give a
continuous, much more reliable blink signal.

## Threshold guidance

- `0.50` — balanced default.
- `0.35–0.45` — cast a wider net (more closed eyes, a few false positives).
- `0.60–0.70` — only blatant closed eyes.

Threshold is data-set dependent; do one scan, skim the CSV / verdicts, then
adjust. The GUI slider makes this quick.

## Output / sorting

Flagged files go to `<scanned folder>\Closed Eyes\` (name configurable).
- **Copy** keeps originals (safe default choice).
- **Move** relocates them.
- Recursive scans preserve the source sub-folder structure under the output.
- Existing files are never overwritten — a ` (1)`, ` (2)` … suffix is added.

## Limits & assumptions

- Needs a reasonably clear view of the eyes. Sunglasses, hands on the face,
  or extreme side profiles can be misjudged.
- No face → not flagged (the tool finds *closed eyes*, not "bad photos").
- HEIC works only if `pillow-heif` is installed (it is, by default).
- Everything runs locally; no photo ever leaves the machine.

## The interface

- **Thumbnail grid** or sortable **list** (toggle), with verdict-coloured
  borders and a letterboxed square thumbnail so portrait and landscape photos
  sit side by side neatly.
- **Filter tabs** with live counts (All / closed / open / no-face / other).
- **Preview pane** with a zoom/pan/rotate canvas and a **face-mesh overlay**
  (face outline, eye loops, iris rings, per-face `#n CLOSED/open score` labels,
  red/green by verdict, plus a legend). Overlay style is switchable between
  **Mesh**, **Box** and **Off** and is remembered; thumbnails also mark where
  the face is.
- **Review window** — a keyboard-driven culling pass over the current filter.
- **Live sensitivity** — moving the slider re-scores existing results from the
  cached blink values, so there is no re-scan.

## Safe workflow

- Report / Copy / Move. Copy keeps originals; Move relocates them.
- A **dry-run dialog** lists exactly what will be written/moved and where.
- **Undo move** replays `_eyescan_manifest.json`, stored in the output folder.
- Settings (folder, threshold, action, output name, theme, view, geometry) are
  persisted in `%LOCALAPPDATA%\EyeScan\settings.json`; recent folders too.

## Reuse

`EyeEngine` in `eyescan_core.py` is independent of the UI, so the same core can
back a Chrome/HTML build, a menu-driven `.bat`, or a scheduled task. The GUI
(`eyescan_gui.py`) only consumes `ImageResult` objects. See `docs/ROADMAP.md`.
