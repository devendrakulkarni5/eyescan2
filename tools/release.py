"""One-command release: bump version -> tests -> exe -> installer -> GitHub Release.

    .venv\\Scripts\\python.exe tools\\release.py patch            # 2.0.0 -> 2.0.1 (also: minor, major, or 2.3.0)
    .venv\\Scripts\\python.exe tools\\release.py minor --notes "Faster people matching"
    .venv\\Scripts\\python.exe tools\\release.py 2.3.0 --no-publish   # build only, upload nothing
    .venv\\Scripts\\python.exe tools\\release.py --check              # verify tools/auth, change nothing

Notes come from --notes, else the "## <version>" section of docs/CHANGELOG.md.
The installer is uploaded to the PUBLIC releases repo (bridge.GITHUB_REPO) that the app's
update check reads. If any step fails, the version bump is rolled back.
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PY = ROOT / ".venv" / "Scripts" / "python.exe"
BRIDGE = ROOT / "bridge.py"
ISS = ROOT / "installer.iss"
ENGINE = ROOT / "engine" / "eyescan_core.py"
CHANGELOG = ROOT / "docs" / "CHANGELOG.md"
RE_BRIDGE = re.compile(r'^(APP_VERSION = ")([^"]+)(")', re.M)
RE_ISS = re.compile(r'^(#define AppVersion ")([^"]+)(")', re.M)
RE_ENGINE = re.compile(r'^(__version__ = ")([^"]+)(")', re.M)
RE_REPO = re.compile(r'^GITHUB_REPO = "([^"]+)"', re.M)


def die(msg: str) -> "None":
    print(f"\n*** {msg}")
    sys.exit(1)


def step(msg: str) -> None:
    print(f"\n=== {msg}")


def run(cmd, **kw) -> subprocess.CompletedProcess:
    print("   $", " ".join(str(c) for c in cmd))
    return subprocess.run([str(c) for c in cmd], cwd=ROOT, **kw)


def must(cmd, **kw) -> None:
    if run(cmd, **kw).returncode != 0:
        raise RuntimeError(f"command failed: {' '.join(str(c) for c in cmd)}")


def current_version() -> str:
    m = RE_BRIDGE.search(BRIDGE.read_text("utf-8"))
    if not m:
        die("APP_VERSION not found in bridge.py")
    return m.group(2)


def next_version(cur: str, arg: str) -> str:
    if re.fullmatch(r"\d+\.\d+\.\d+", arg):
        return arg
    a, b, c = (int(p) for p in cur.split("."))
    if arg == "major":
        return f"{a + 1}.0.0"
    if arg == "minor":
        return f"{a}.{b + 1}.0"
    if arg == "patch":
        return f"{a}.{b}.{c + 1}"
    die(f"version must be patch, minor, major or X.Y.Z (got {arg!r})")


def vtuple(v: str):
    return tuple(int(p) for p in v.split("."))


def find_gh() -> str | None:
    for cand in (shutil.which("gh"), r"C:\Program Files\GitHub CLI\gh.exe",
                 str(Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "GitHub CLI" / "gh.exe")):
        if cand and Path(cand).exists():
            return cand
    return None


def find_iscc() -> str | None:
    for base in (os.environ.get("ProgramFiles(x86)"), os.environ.get("ProgramFiles"),
                 str(Path(os.environ.get("LOCALAPPDATA", "")) / "Programs")):
        if base and (Path(base) / "Inno Setup 6" / "ISCC.exe").exists():
            return str(Path(base) / "Inno Setup 6" / "ISCC.exe")
    return None


def releases_repo() -> str:
    m = RE_REPO.search(BRIDGE.read_text("utf-8"))
    return m.group(1) if m else die("GITHUB_REPO not found in bridge.py")


def notes_for(version: str, given: str | None) -> str:
    if given:
        return given
    if CHANGELOG.exists():
        m = re.search(rf"^## {re.escape(version)}\s*\n(.*?)(?=^## |\Z)", CHANGELOG.read_text("utf-8"), re.M | re.S)
        if m and m.group(1).strip():
            return m.group(1).strip()
    return f"EyeScan {version}"


def preflight(publish: bool) -> tuple[str | None, str | None]:
    step("Checking tools")
    if not PY.exists():
        die(".venv missing - run build_exe.bat once to create it")
    iscc = find_iscc()
    if not iscc:
        die("Inno Setup 6 not found. Install: winget install JRSoftware.InnoSetup")
    gh = find_gh()
    if publish:
        if not gh:
            die("GitHub CLI not found. Install: winget install GitHub.cli   then: gh auth login")
        if run([gh, "auth", "status"], capture_output=True).returncode != 0:
            die("GitHub CLI is not logged in. Run: gh auth login")
        if run([gh, "repo", "view", releases_repo()], capture_output=True).returncode != 0:
            die(f"Cannot see GitHub repo {releases_repo()}. Create it as a PUBLIC repo (or fix GITHUB_REPO in bridge.py).")
    print(f"   Inno Setup: {iscc}\n   GitHub CLI: {gh or '(not needed)'}\n   Releases repo: {releases_repo()}")
    return iscc, gh


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("version", nargs="?", help="patch | minor | major | X.Y.Z")
    ap.add_argument("--notes", help="release notes (default: CHANGELOG section)")
    ap.add_argument("--no-publish", action="store_true", help="build the installer but do not upload")
    ap.add_argument("--check", action="store_true", help="only verify prerequisites")
    ap.add_argument("--skip-tests", action="store_true")
    ap.add_argument("--version-only", action="store_true", help="print the current version and exit")
    ap.add_argument("--publish-only", action="store_true",
                    help="upload the installer already built by build_exe.bat + build_installer.bat (no bump/build)")
    args = ap.parse_args()

    if args.version_only:
        print(current_version())
        return
    if args.publish_only:
        iscc, gh = preflight(True)
        ver = current_version()
        setup, tag = ROOT / "installer" / f"EyeScan2-Setup-{ver}.exe", f"v{ver}"
        if not setup.exists():
            die(f"{setup.name} not found - run build_exe.bat and build_installer.bat first")
        if run([gh, "release", "view", tag, "--repo", releases_repo()], capture_output=True).returncode == 0:
            die(f"Release {tag} already exists on {releases_repo()} - bump APP_VERSION and installer.iss first")
        step(f"Publishing {tag} to {releases_repo()}")
        if run([gh, "release", "create", tag, setup, "--repo", releases_repo(), "--title", f"EyeScan {ver}",
                "--notes", notes_for(ver, args.notes)]).returncode != 0:
            die("Upload failed")
        print(f"\nDONE - https://github.com/{releases_repo()}/releases/tag/{tag}")
        return

    publish = not args.no_publish
    iscc, gh = preflight(publish)
    if args.check:
        print("\nAll good.")
        return
    if not args.version:
        ap.error("give a version: patch | minor | major | X.Y.Z")

    cur = current_version()
    new = next_version(cur, args.version)
    if vtuple(new) <= vtuple(cur) and new != cur:
        die(f"new version {new} is not higher than {cur}")
    tag = f"v{new}"
    if publish and run([gh, "release", "view", tag, "--repo", releases_repo()], capture_output=True).returncode == 0:
        die(f"Release {tag} already exists on {releases_repo()}")
    print(f"\nReleasing {cur} -> {new}")

    saved = {p: p.read_text("utf-8") for p in (BRIDGE, ISS, ENGINE)}
    try:
        step("Bumping version")
        BRIDGE.write_text(RE_BRIDGE.sub(rf"\g<1>{new}\g<3>", saved[BRIDGE], count=1), "utf-8")
        ISS.write_text(RE_ISS.sub(rf"\g<1>{new}\g<3>", saved[ISS], count=1), "utf-8")
        ENGINE.write_text(RE_ENGINE.sub(rf"\g<1>{new}\g<3>", saved[ENGINE], count=1), "utf-8")

        if not args.skip_tests:
            step("Self-tests")
            r = run([PY, "tests/selftest.py"], capture_output=True, text=True)
            if r.returncode != 0:
                print(r.stdout[-3000:], r.stderr[-1500:])
                raise RuntimeError("self-tests failed")
            print("   passed")

        step("Building EyeScan2.exe (a few minutes)")
        must([PY, "-m", "PyInstaller", "--noconfirm", "--clean", "EyeScan2.spec"])
        exe, doctor = ROOT / "dist" / "EyeScan2" / "EyeScan2.exe", ROOT / "dist" / "EyeScan2" / "doctor.txt"
        step("Verifying the built exe")
        doctor.unlink(missing_ok=True)
        run([exe, "--doctor", doctor], timeout=180)
        txt = doctor.read_text("utf-8", "replace") if doctor.exists() else ""
        if not txt or "MISSING" in txt or "NOT FOUND" in txt:
            print(txt)
            raise RuntimeError("built exe is missing a component")
        doctor.unlink(missing_ok=True)

        step("Building installer")
        setup = ROOT / "installer" / f"EyeScan2-Setup-{new}.exe"
        setup.unlink(missing_ok=True)
        must([iscc, "installer.iss"])
        if not setup.exists():
            raise RuntimeError(f"installer not produced: {setup}")
        print(f"   {setup}  ({setup.stat().st_size / 1e6:.0f} MB)")
    except BaseException as exc:  # noqa: BLE001 - roll back the bump on any failure/Ctrl+C
        for p, txt in saved.items():
            p.write_text(txt, "utf-8")
        die(f"Release aborted, version restored to {cur}: {exc}")

    if not publish:
        print(f"\nBuilt {setup} (not published). Version files now say {new}.")
        return
    step(f"Publishing {tag} to {releases_repo()}")
    r = run([gh, "release", "create", tag, setup, "--repo", releases_repo(),
             "--title", f"EyeScan {new}", "--notes", notes_for(new, args.notes)])
    if r.returncode != 0:
        die(f"Upload failed. Installer is built at {setup}; version files already say {new}. "
            f"Retry: gh release create {tag} \"{setup}\" --repo {releases_repo()}")
    print(f"\nDONE - {tag} is live: https://github.com/{releases_repo()}/releases/tag/{tag}")
    print("Installed copies will offer it on their next start (or Help -> Check for updates).")


if __name__ == "__main__":
    main()
