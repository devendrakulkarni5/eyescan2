# Roadmap

## Done

- [x] Culling review window, thumbnail grid, zoom/pan/rotate preview (1.1)
- [x] Robust multi-tile face search; dual confidence (1.2)
- [x] Face-mesh overlay (1.3)
- [x] Accuracy audit: EAR cross-check, rotation fallback, nested-box de-dupe (1.4)
- [x] **BlazeFace validation** - rejects feet/hands/patterns detected as faces (1.4)
- [x] **Looking-down detection** (head pitch + gaze + lid gap) - kept, not flagged (1.5)
- [x] People count / solo-pair-group tags, animal detection, smile, sharpness (1.5)
- [x] Per-photo and per-face corrections that persist across scans (1.5)
- [x] Delete to Recycle Bin, right-click context menu, multi-select (1.5)
- [x] Professional UI: menus, filter sidebar, inspector, virtualised grid (1.5)
- [x] High-resolution contact sheets, >= 4000 px, 3:2 default (1.5)
- [x] Installer script (Inno Setup) (1.4)
- [x] SQLite saved results: fast re-scan, resume, clear / prune (1.6)
- [x] Duplicates / near-duplicates with best-frame pick (1.6)
- [x] Face-only blur / misfocus detection + sort (1.6)
- [x] Protect poses from bulk sort / delete; sizes + savings in dialogs (1.6)
- [x] Both-eyes logic fused with lid gap; wink tag; needs-a-look queue (1.6)
- [x] Explorer / FastStone selection (rubber-band, Shift/Ctrl, keyboard) (1.6)
- [x] Space = zoom to face; animals boxed separately; box style settings (1.6)
- [x] "Nature's Vibration" design system UI (1.6)

### Ideas not built yet
- [ ] Eye-region (not whole-face) focus score, to catch "nose sharp, eyes soft".
- [ ] Group-photo mode: "everyone's eyes open" frame picker across a burst.
- [ ] Optional ONNX runtime to shrink the ~330 MB bundle (see Architecture).

## Next: feet / body awareness (proposal - not built)

**Why:** the foot false-positive is already solved by BlazeFace validation, which
costs ~5 ms per face and adds no model. Dedicated foot detection is therefore
*not* needed for robustness. It would only be worth building as a **feature**
(tag "feet visible", count people whose face is hidden, drop face hits that sit
on a body part).

**Cheapest sound design - MediaPipe Pose Landmarker (lite):**

| | |
|---|---|
| Model | `pose_landmarker_lite.task`, ~5.5 MB, bundled like the others |
| Gives | 33 landmarks per person incl. heel + foot-index, ankles, hips |
| Cost | ~30-60 ms per image on CPU, run **once on the 1536 px detection image** (not per tile) |
| Use 1 | tag `feet` when foot landmarks are visible with decent confidence |
| Use 2 | true body count for group detection when faces are hidden / turned away |
| Use 3 | veto a face whose centre lies below the hips or on a foot landmark |
| Limit | `num_poses` > 1 degrades on crowded photos; keep it as a tag, never a verdict |

**Plan:** (1) add `PoseLandmarker` behind an `EyeEngine(detect_pose=True)` flag,
default off; (2) store `ImageResult.n_poses` and `feet: bool`; (3) add a
"Feet" tag filter + CSV column; (4) evaluate on the Navratri + Bhagyashree
shoots (feet photos are common) before enabling by default. Estimated cost:
+10-15% scan time when on.

## UI / UX ideas

- [ ] Blink-score histogram with a draggable threshold line.
- [ ] "Compare" view: both eyes side-by-side, large.
- [ ] Drag & drop a folder onto the window (needs `tkinterdnd2`).
- [ ] Burst grouping: pick the best frame (all eyes open + sharpest).

## Packaging

- [ ] Custom app icon (`.ico`) in the exe and the Tk window.
- [ ] Windows Explorer right-click -> "Scan with EyeScan".
- [ ] Code-signing to avoid SmartScreen warnings.

## Architecture (recommendation)

Keep `eyescan_core` as the engine - it is UI-free and fully tested. The
real limit is **Tkinter** (no native look, hand-rolled virtualised grid, no
drag-and-drop, weak HiDPI) and the **~300 MB bundle** (mediapipe + OpenCV).

1. **UI: PySide6 (Qt 6)** - native widgets, `QListView` model/view that
   virtualises 50k thumbnails for free, real drag & drop, proper HiDPI and
   theming, `QThreadPool` instead of the queue/`after` pump. Core stays
   unchanged; only `eyescan_gui.py` is rewritten. Ship with PyInstaller
   (works) or `pyside6-deploy` (Nuitka-based), wrapped by Inno Setup.
2. **Runtime: ONNX Runtime instead of mediapipe** (optional, bigger job) -
   YuNet/SCRFD for detection + a landmark/blendshape ONNX model. Drops the
   bundle to ~80-100 MB and removes the matplotlib/cv2 import baggage, at the
   cost of re-validating accuracy on the labelled shoots.
3. **Not recommended:** Electron/Tauri + Python sidecar (two runtimes, IPC for
   multi-MB images, larger and slower to start).
