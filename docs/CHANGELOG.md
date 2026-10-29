# Changelog — EyeScan 2

(v1 history: `CHANGELOG-v1.md`.)

## 2.2.0
- Lucide icon set bundled offline (`ui/vendor/lucide.js`, 2,275 icons, ISC licence; rebuild with `tools/build_icons.py`).
  Rail, toolbar, viewer, dialogs, tiles and buttons use it; no network access anywhere in the UI.
- People: quality-weighted clustering with exemplar faces and average-linkage merging (`eyescan_people.cluster_people`),
  low-quality faces never create new "people"; sort (most photos / name / closed eyes / unnamed first),
  search, multi-select **Merge selected**, **Possible duplicates** with similarity and "Not the same",
  merge with an optional name (saved merges are recognised in later scans), right-click on the People list.
- Names are matched on saved exemplar faces (not one centroid) and conservatively (score >= 0.52 and a 0.05 margin over
  the runner-up) - on real shoots SFace scores two different people ~0.47, so automatic names must be strict.
- Close folder (File, Ctrl+W, x button), redesigned Stop button, right-click menu for people, window/taskbar icon fix.

## 2.1.0
- Database settings tab: shows the SQLite file, lets you move it (and switch back), and clear specific data
  (results, this folder, missing files, names, tags, thumbnails, everything) with two-click confirm.
- People recognised by default; name people (face-signature matched on later scans) and tag animals; names are
  searchable and shown on boxes, rail and inspector. Stored in the same database.
- Window remembers size, position, screen and maximized state (falls back to the primary screen).
- Grid shapes: original proportions (justified rows), 3:2, 2:3, square.
- Detection resolutions are standard sizes (720p ... 4K, default 1600).
- Stopped scans show Resume / Rescan; Scan -> Resume scan.
- Speed: thumbnails/previews via a local image server, geometry fetched on demand (summaries ~60% smaller),
  incremental grid updates, rail no longer rebuilt every render.
- Detail overlay: full outline with forehead, jaw, chin, cheeks, brows, nose, lips (engine schema v9: needs a re-scan).
- Preview boxes and labels get thin/small when zoomed; 1:1 actual-size button; sharper image loads when zoomed.
- New app icon (from the brand logo folder); light/dark variants follow the theme.

## 2.0.0
- New web UI (pywebview / WebView2) on the unchanged v1 engine: menu bar, command palette (Ctrl+K),
  filter rail, grid/list, inspector, Review window, context menu, multi-select incl. rubber-band,
  Ctrl+wheel thumbnail size, preview wheel-zoom / drag-pan / rotate / zoom-to-face.
- Settings sidebar dialog with **all** v1 settings: sensitivity, lid gap, blur cut, misfocus ratio,
  smallest face, detection resolution, thorough/deep, animals, people, duplicates, workers, GPU device
  + "Test GPUs", box thickness/label size/colours, auto face zoom + fill, theme, overlay.
- Sort bulk groups or exactly the selected photos; undo last sort; bulk delete to the Recycle Bin (+ RAW).
- CSV export, contact sheets, People view + People list in the rail, recent folders in File menu.
- CLI (`app.py --scan ...`, `--doctor`) routed to `cli.py`; PyInstaller spec, Inno Setup installer,
  self-tests (215), headless bridge test, jsdom UI smoke test.
- Fixed: grid tiles collapsing into thin strips (WebView2 `aspect-ratio` + `content-visibility`).
