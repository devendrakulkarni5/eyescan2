# Keeper (formerly EyeScan 2) — project context (for AI agents & developers)

Windows desktop app (display name **Keeper**, renamed from EyeScan 2; photographer-oriented) that scans a folder of photos and finds / sorts the ones where a person
has their **eyes closed**, plus duplicates, blur/misfocus, winks, people and animals. Successor to
`S:\HTML\EyeScan` (v1, Tkinter), which stays as the reference. All offline.

**Rename note:** user-facing name, exe (`Keeper.exe`), installer (`Keeper-Setup-<ver>.exe`), spec and icon are Keeper. Internals deliberately keep the old names so existing installs/data keep working: data folder `%LOCALAPPDATA%\EyeScan2` (+ `EyeScan` for shared settings), `eyescan2.db`, `eyescan_*` engine modules, installer `AppId`, `GITHUB_REPO`.

## Architecture

```
ui/ (HTML/CSS/JS, no build step)  <-- pywebview js_api -->  bridge.py (Bridge)  -->  engine/ (EyeScan v1 engine, unchanged)
app.py = entry (window) · cli.py = command line (any --option routes here) · Keeper.spec = PyInstaller
```

- `engine/` is a **copy** of v1's `eyescan_core/db/gpu/people/report` + `model/`. Calibration lives
  there (see v1 `AGENTS.md`, `docs/PROJECT.md`). Fix engine bugs in one place and re-copy; don't fork logic.
- `bridge.py` — every public method is callable from JS (`pywebview.api.*`). **All state is private
  (`_name`)**: pywebview walks public attributes and recurses into `_window` otherwise (crash + error spam).
  Results travel as plain JSON via `summarize()`; progress as `_emit(kind, ...)` -> `window.onEvent`.
- Settings: `core.Settings` (`%LOCALAPPDATA%\EyeScan\settings.json`, shared with v1) plus
  `EXTRA_DEFAULTS` (blur_cut, misfocus_ratio, min_face, max_dim) in `%LOCALAPPDATA%\EyeScan2\extras.json`.
  `_apply_globals()` pushes blur/misfocus/EAR_DOWN into `core` and the GPU mode into `eyescan_gpu`.
- **Database** = one SQLite file `eyescan2.db` (default `%LOCALAPPDATA%\EyeScan2`, movable from Settings -> Database;
  the pointer lives in `location.json` because the DB can't store its own location). Tables: `results` (scan cache,
  `eyescan_db`), `persons` + `tags` (`engine/eyescan_names.py`). People names are bound to a face-embedding
  **centroid** (cluster numbers change every scan) and matched after each scan; animals are per-photo tags.
  `db_clear(what)` never touches photos. v1's `eyescan.db` is not shared (cache keys differ).
- **Images** reach the UI through a token-protected `127.0.0.1` HTTP server (`_ImageServer`), not base64 over the
  JS bridge. Only photos in `_results` are served. Summaries exclude geometry; `geometry(path)` is fetched on demand
  (mesh / detail overlay). Keep payloads small — `evaluate_js` is synchronous.
- **Face regions** (`core.face_regions`, schema v9): oval + forehead extension, jaw, chin, cheeks, brows, nose, lips
  from the 478 landmarks. Cheek indices were verified visually; re-check with a render if you touch them.
- **Window memory**: `app.py WindowMemory` -> `window.json` (x, y, size, maximized, screen); off-screen positions are
  moved to the primary screen after start.
- Detection resolutions are the standard list in `bridge.RESOLUTIONS` (default 1600); old values snap to the nearest.
- **Icons**: Lucide via `ui/vendor/lucide.js` (`ico(name)`; short legacy names are aliased in `ICON_ALIAS`). Never
  hot-link an icon font/CDN: the app must run offline. Regenerate with `tools/build_icons.py` (needs `npm pack lucide-static`).
- **People**: `cluster_people` (quality-weighted, exemplars, average linkage) -> bridge `people()` caches clusters until the next
  scan so session merges survive; `merge_people(ids, name)` saves a named merge with exemplars. Automatic naming uses
  `NAME_MATCH` (0.52) + `MATCH_MARGIN`; do not lower them without re-testing (different people score ~0.47).
  Tests that touch names must use an isolated `LOCALAPPDATA` (see `tests/people_merge_test.py`) - never the real database.
- UI state is the `S` object in `ui/app.js`; menus = `MENUS` array; actions = `ACT` map + `run(name)`
  (menus, shortcuts, palette and context menu all go through `run`). Dialogs use `dialog({...})`.
- `ui/app.js` is `"use strict"` and uses `.hidden` classes — never `display:none` inline.

## Gotchas

- Grid tiles are squares via `height:0; padding-bottom:100%` — `aspect-ratio` + `content-visibility`
  collapsed rows in WebView2. Don't "simplify" back.
- Preview viewer (`applyView`) positions `.in` absolutely with explicit px (needed for 90° rotation).
- `scan_files(workers>1)` spawns processes: `multiprocessing.freeze_support()` must stay in `app.py`.
- Bump `core.SCHEMA_VERSION` (engine) if a cached measurement changes meaning.
- Never open a browser to verify (user rule). UI is verified headless with jsdom (`tests/ui_smoke.js`)
  and by the user manually.
- Frozen exe is `--windowed` (no console): verify with `Keeper.exe --doctor <scratch file>`
  (**overwrites** that file — never pass a photo). `--scan ... --csv <file>` works in the exe too.
- Do not exclude `matplotlib` / `cv2` / `sounddevice` from the spec: mediapipe imports them.

## Commands

```bat
Run-Keeper.bat                                   :: run from source
.venv\Scripts\python.exe tests\selftest.py         :: 215 engine tests
.venv\Scripts\python.exe tests\window_memory_test.py :: window geometry persistence
.venv\Scripts\python.exe tests\bridge_names_test.py <folder-of-copies>   :: image server, names, db move/clear
.venv\Scripts\python.exe tests\bridge_test.py <folder-of-copies>   :: headless scan + tools
cd tests && npm i jsdom && node ui_smoke.js        :: UI smoke test (no browser)
build_exe.bat  ->  dist\EyeScan2\EyeScan2.exe      :: build_installer.bat -> Inno Setup installer
.venv\Scripts\python.exe app.py --scan "D:\Photos" --csv report.csv
```

## Releasing an update (AI agents: do this when the user says "release / push an update")

Installed apps check the latest GitHub Release of the **public** repo `bridge.GITHUB_REPO`
(`devendrakulkarni5/eyescan2-releases`; the source repo is private so it can't be used) via `Bridge.check_update`.
One command does everything (bump `APP_VERSION` + `installer.iss`, selftest, PyInstaller, `--doctor`, Inno Setup, `gh release create`),
and rolls the version back if anything fails:

```bat
release.bat --check                                 :: verify Inno Setup, gh login, releases repo (changes nothing)
release.bat patch --notes "What changed"            :: or minor | major | 2.3.0 ; add --no-publish to build only
```

Manual route (same result): edit `APP_VERSION` + `installer.iss` `AppVersion`, run `build_exe.bat`, `build_installer.bat`,
then `publish_release.bat "notes"` (= `release.py --publish-only`).

Steps for the agent: 1) run `--check`, fix/ask about anything missing (`winget install GitHub.cli`, `gh auth login`,
create the public releases repo); 2) add a `## X.Y.Z` section to `docs/CHANGELOG.md` (or pass `--notes`);
3) run `release.bat <patch|minor|major>` (takes several minutes: run in background); 4) report the release URL.
Choose patch for fixes, minor for features, major only if the user says so. Never edit versions by hand.

## Mac plan

Same UI; pywebview uses WKWebView. Needs: platform-dirs helper (replace `%LOCALAPPDATA%`), `send2trash`
(already used via `core.recycle`), HEIC/RAW (`pillow_heif`, `rawpy`), CoreML provider in `eyescan_gpu`,
codesign + notarize. Build on a macOS runner (can't cross-build). See `docs/PLATFORMS.md`.
