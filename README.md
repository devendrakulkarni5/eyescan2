# EyeScan 2

Windows desktop app that scans a folder of photos and finds the ones where someone has their **eyes closed**.
It also spots duplicates, blurry or misfocused shots, winks, smiles, people (and lets you name them) and animals.
Everything runs **offline** on your PC. Photos never leave it.

EyeScan 2 is the successor to the v1 Tkinter app. It keeps the proven v1 detection engine (`engine/`) behind a modern
web UI (`ui/`) hosted by pywebview (WebView2).

## Features
- Eyes-closed, looking-down and wink detection with adjustable sensitivity
- Duplicate grouping with a suggested best frame
- Blur and misfocus detection
- People recognition: cluster, name and merge people; names are remembered in later scans
- Animal boxes and tags
- Review mode, sort or copy flagged photos into folders (with undo), safe delete to the Recycle Bin
- CSV report and contact sheets
- GPU acceleration for face recognition when available (NVIDIA, AMD, others), CPU otherwise
- Built-in update check against GitHub Releases (can be turned off in Settings)

## Install (users)
Download `EyeScan2-Setup-<version>.exe` from the
[Releases](https://github.com/devendrakulkarni5/eyescan2-releases/releases) page and run it. Installing a newer
version over an old one keeps your settings and database.

## Run from source (developers)
Needs Python 3.13 on Windows.

```bat
Run-EyeScan2.bat                     :: creates .venv on first run, then starts the app
.venv\Scripts\python.exe app.py --debug
```

Command line (all v1 options work):

```bat
.venv\Scripts\python.exe app.py --scan "D:\Photos" --csv report.csv
```

## Tests
```bat
.venv\Scripts\python.exe tests\selftest.py          :: engine tests
.venv\Scripts\python.exe tests\window_memory_test.py
cd tests && npm i jsdom && node ui_smoke.js         :: UI smoke test, no browser needed
```

## Build and release
| Task | Command |
|---|---|
| Build the exe | `build_exe.bat` -> `dist\EyeScan2\EyeScan2.exe` |
| Build the installer (needs Inno Setup 6) | `build_installer.bat` |
| Publish an update (manual) | `publish_release.bat "what changed"` |
| Whole release in one go | `release.bat patch` (or `minor`, `major`, `2.3.0`) |
| Check release prerequisites | `release.bat --check` |
| Push source to GitHub | `github_push.bat` |

Releases go to the public repo `devendrakulkarni5/eyescan2-releases`; installed apps read its latest release to offer
updates. One-time setup: `winget install JRSoftware.InnoSetup GitHub.cli`, then `gh auth login`.

## Keys
Arrows / Shift / Ctrl select · Space zoom to face · Tab next face · Enter Review · K / F / P / X keep, flag, protect,
reject · Del delete · F2 rename · F5 scan · Ctrl+K commands · F1 all shortcuts.
Mouse: drag empty space = rubber-band select · Ctrl+wheel = thumbnail size · wheel / drag on preview = zoom / pan.
Reject only marks; "Move to Recycle Bin" is the single destructive action (protected photos are skipped).

## Project layout
```
app.py        window entry          cli.py      command line
bridge.py     JS <-> Python API     engine/     detection engine + models
ui/           HTML / CSS / JS       tests/      engine, bridge and UI tests
tools/        release + icon tools  docs/       changelog, platforms, roadmap
```
Developer and AI-agent notes: [AGENTS.md](AGENTS.md). History: [docs/CHANGELOG.md](docs/CHANGELOG.md).

## Licence
See [LICENSE.txt](LICENSE.txt). © Devendra Kulkarni · [DEeV.in](https://www.deev.in)
