# Packaging & platforms

## Windows (supported today)
`build_exe.bat` -> `dist\EyeScan\EyeScan.exe` (PyInstaller one-folder, `EyeScan.spec`).
`build_installer.bat` -> `installer\EyeScan-Setup-<version>.exe` (Inno Setup).
Icon, version resource and licence come from `assets\` and `LICENSE.txt`.

## macOS (.app + .dmg) - plan, not built yet
Reality check
- PyInstaller **cannot cross-build**: a Mac app must be built on a Mac (a real one,
  a rented Mac in the cloud, or a GitHub Actions `macos-*` runner - free minutes for
  public repos, paid for private).
- Output is `EyeScan.app` (PyInstaller `BUNDLE` step, `.icns` icon), wrapped into a
  `.dmg` with `create-dmg` / `hdiutil`.
- **Architectures**: Apple Silicon = arm64, Intel = x86_64. Either build one of each
  (simple, two downloads) or a `universal2` build (needs universal2 wheels for every
  dependency - check mediapipe / numpy / Pillow for the exact version you pin).
- **Older Macs**: the app can only run on macOS >= the oldest version your
  dependencies and Python support. mediapipe wheels need roughly macOS 11 (Big Sur)+;
  Python 3.13 itself is fine on 10.13+. So macOS 10.15 and older (e.g. pre-2015 Macs
  stuck on Catalina) are **not realistic**; target macOS 11+ and say so. Build on
  the *oldest* macOS you intend to support (the bundle inherits that deployment target).
- Use the python.org installer's Python (ships Tk 8.6). The Tk 8.5 that comes with
  the system Python looks wrong and misbehaves.
- **Gatekeeper**: unsigned apps show "can't be opened". Fix = Apple Developer
  Program (US$99/yr): codesign + notarize (`notarytool`) + staple. Without it users
  must right-click > Open once.

Code work needed before a Mac build (all small, none done yet)
1. Recycle Bin: `core.recycle` already falls back to `send2trash` off Windows - add
   `send2trash` to requirements (mac).
2. Data folder: `%LOCALAPPDATA%\EyeScan` -> `~/Library/Application Support/EyeScan`.
3. "Show in folder" -> `open -R <file>`; clipboard-copy-files -> not supported on Mac
   (hide that menu item).
4. Input: right-click is `<Button-2>` and wheel deltas differ on macOS; shortcuts
   should use Cmd instead of Ctrl; the menubar becomes the system menu.
5. Icon: generate `assets/EyeScan.icns` (from the 256 px PNG; a 1024 px logo is better).
6. `EyeScan.spec`: add a `BUNDLE(...)` for darwin, bundle ID e.g. `in.deev.eyescan`.
7. CI: one workflow, matrix `windows-latest` + `macos-14` (arm64) + `macos-13` (Intel),
   running `selftest.py`, building, and uploading artifacts.

Recommendation: keep Windows as the product, treat Mac as a phase 2 via GitHub Actions
(arm64 + Intel builds, macOS 11+), and only pay for notarisation if you distribute widely.
