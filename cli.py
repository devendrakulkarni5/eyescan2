#!/usr/bin/env python3
"""
EyeScan 2 - command line. app.py hands any CLI option to main() here.

    python app.py --scan "D:\\Photos" --threshold 0.5 --csv report.csv
    python app.py --scan "D:\\Photos" --move
    python app.py --scan "D:\\Photos" --copy --out "Closed Eyes"
"""

from __future__ import annotations

import argparse
import sys
import traceback
from pathlib import Path

import bridge  # noqa: F401  (puts the engine folder on sys.path)
import eyescan_core as core

GUI = -1     # main() result meaning "open the GUI"


# --------------------------------------------------------------------------- #
#  CLI
# --------------------------------------------------------------------------- #

def _human(n: float) -> str:
    n = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024.0
    return f"{n:.1f} GB"


def cli(args: argparse.Namespace) -> int:
    folder = Path(args.scan).expanduser().resolve()
    if not folder.is_dir():
        print(f"Not a folder: {folder}", file=sys.stderr)
        return 2

    model = core.ensure_model()
    engine = core.EyeEngine(
        model,
        threshold=args.threshold,
        require_both=True,                  # both eyes are always checked
        min_face_area=args.min_face,
        robust=not args.fast,
        deep=args.deep,
        detect_max_dim=args.max_dim,
        keep_down=not args.no_keep_down,
        detect_animals=not args.no_animals,
    )
    core.BLUR_SHARP_MIN = args.blur_cut
    skip = (args.out, args.blur_out, args.dup_out, "Contact Sheets")
    files = list(core.iter_images(folder, args.recursive, skip_dirs=skip))
    total = len(files)

    cache = None
    if not args.no_cache:
        import eyescan_db

        cache = eyescan_db.ResultCache()
        cache = cache if cache.ok else None
    print(f"Scanning {total} image(s) in {folder} (threshold={args.threshold}"
          f"{', saved results ' + ('ignored' if args.rescan else 'used') if cache else ''})")

    overrides = core.Overrides()
    flagged: list[core.ImageResult] = []
    everything: list[core.ImageResult] = []
    reused = 0
    for i, (p, res, cached) in enumerate(
            core.scan_files(engine, files, cache, force=args.rescan,
                            workers=args.workers), 1):
        overrides.apply(res)
        res.reclassify(args.threshold, True, not args.no_keep_down)
        everything.append(res)
        reused += cached
        mark = "CLOSED" if res.is_flagged else res.verdict
        if res.is_flagged:
            flagged.append(res)
        print(f"  [{i:>4}/{total}] {mark:<8} {p.name}" + ("  (saved)" if cached else ""))
    if cache is not None:
        cache.close()
    engine.close()

    print(f"\n{len(flagged)} of {total} flagged with closed eyes"
          + (f"  ({reused} reused from saved results)." if reused else "."))
    for r in flagged:
        print(f"  - {r.path}  (max blink {r.max_blink:.2f}"
              f"{', needs a look' if r.needs_check else ''}"
              f"{', PROTECTED' if r.protected else ''})")

    n_groups = 0
    if args.dups != "off":
        n_groups = core.find_duplicates(everything, core.DUP_LEVELS[args.dups])
    extras = [r for r in everything if r.is_duplicate]
    if n_groups:
        print(f"{n_groups} duplicate group(s): {len(extras)} extra cop"
              f"{'y' if len(extras) == 1 else 'ies'} "
              f"({_human(sum(r.file_size for r in extras))} could be freed).")
    try:
        from eyescan_people import cluster_people
        who = cluster_people(everything)
    except Exception:  # noqa: BLE001 - recognition is optional
        who = []
    if who:
        print(f"{len(who)} people recognised (most photographed: person 1 in "
              f"{who[0]['photos']} photo(s)).")
    n_better = sum(1 for r in everything if r.rescue is not None)
    if n_better:
        print(f"{n_better} flagged photo(s) have a better frame in the same burst.")
    unclear = [r for r in everything if r.unclear]
    if unclear:
        print(f"{len(unclear)} photo(s) with an unclear face "
              f"({sum(1 for r in unclear if r.misfocused)} misfocused, "
              f"{sum(1 for r in unclear if r.blurry)} blurry).")

    if args.csv:
        core.write_csv(args.csv, everything)
        print(f"CSV written: {args.csv}")

    n_down = sum(1 for r in everything if r.verdict == "down")
    if n_down:
        print(f"{n_down} photo(s) look down (kept, not flagged; "
              "use --no-keep-down to flag them).")

    if args.sheet:
        import eyescan_report as report

        pages = report.make_contact_sheets(
            everything, args.sheet, title=f"EyeScan - {folder.name}",
            mode=args.sheet_mode, ratio=args.sheet_ratio,
            long_side=args.sheet_long, only=args.sheet_only)
        print(f"{len(pages)} contact sheet page(s) written to {args.sheet}")

    n_err = sum(1 for r in everything if r.verdict == "error")
    if n_err:
        print(f"{n_err} file(s) could not be read (see CSV / verdict 'error').")

    # bulk actions never touch protected photos
    def sortable(rs):
        ok = [r for r in rs if not r.protected]
        if len(ok) != len(rs):
            print(f"  ({len(rs) - len(ok)} protected photo(s) skipped)")
        return ok

    mode = "move" if args.move else "copy"
    jobs = []
    if args.move or args.copy:
        jobs.append((flagged, args.out))
    if args.sort_blurry:
        jobs.append((unclear, args.blur_out))
    if args.sort_dups:
        jobs.append((extras, args.dup_out))
    for items, name in jobs:
        items = sortable(items)
        size = sum(r.file_size for r in items)
        outcome = core.sort_results(items, folder, name,
                                    mode=mode if (args.move or args.copy) else "move",
                                    recursive=args.recursive, only_flagged=False)
        print(f"{'Copied' if mode == 'copy' and (args.move or args.copy) else 'Moved'} "
              f"{outcome.moved} file(s) ({_human(size)}) into {folder / name}")

    if args.delete or args.delete_dups:
        if not args.yes:
            print("--delete / --delete-dups need --yes to confirm "
                  "(files go to the Recycle Bin).", file=sys.stderr)
            return 2
        victims = sortable((flagged if args.delete else []) + (extras if args.delete_dups else []))
        size = sum(r.file_size for r in victims)
        done, failed = core.recycle([r.path for r in victims])
        print(f"Moved {done} file(s) ({_human(size)}) to the Recycle Bin"
              + (f"; {len(failed)} failed." if failed else "."))
    return 0


def run_doctor(dest: str | None) -> int:
    """Report the runtime environment (useful when the .exe misbehaves)."""
    import platform

    lines: list[str] = []

    def add(key, val):
        lines.append(f"{key}: {val}")

    add("EyeScan", core.__version__)
    add("copyright", core.CREDIT_LINE)
    add("frozen", getattr(sys, "frozen", False))
    add("python", sys.version.replace("\n", " "))
    add("platform", platform.platform())
    add("app_dir", core.app_dir())
    add("bundled_dir", core.bundled_dir())
    for name in ("mediapipe", "PIL", "numpy", "webview", "pillow_heif"):
        try:
            mod = __import__(name)
            add(name, getattr(mod, "__version__", "?"))
        except Exception as exc:  # noqa: BLE001
            add(name, f"MISSING ({exc})")
    add("model", core.find_model() or "NOT FOUND")
    add("validator model", core.find_model(core.BLAZE_FILENAME, 50_000) or "NOT FOUND")
    add("animal model", core.find_model(core.ANIMAL_FILENAME, 1_000_000) or "NOT FOUND")
    add("face-recognition model", core.find_model("face_recognition_sface_2021dec.onnx",
                                                  5_000_000) or "NOT FOUND")
    add("heif", core.heif_available())
    try:
        import eyescan_gpu as gpu

        info = gpu.choice()
        add("onnxruntime", info.get("onnxruntime") or "MISSING")
        add("gpu adapters", "; ".join(
            f"{a['index']}: {a['name']} ({a['vendor']}, {a['vram_mb']} MB, "
            f"{'discrete' if a['discrete'] else 'integrated'})"
            for a in info.get("adapters") or []) or "none found")
        add("gpu benchmark (ms/batch)", info.get("ms"))
        add("gpu", gpu.describe(info))
        from eyescan_people import FaceEmbedder
        emb = FaceEmbedder()
        add("face recognition runs on", f"{emb.device_name} ({emb.provider})")
    except Exception as exc:  # noqa: BLE001
        add("gpu", f"check failed ({exc})")
    add("scan processes (auto, 100 photos)", core.auto_workers(100))

    text = "\n".join(lines)
    print(text)
    if dest is not None:
        target = Path(dest) if dest else core.app_dir() / "eyescan_diagnostics.txt"
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text, encoding="utf-8")
        except Exception as exc:  # noqa: BLE001
            print(f"(could not write {target}: {exc})")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Find photos with closed eyes.")
    ap.add_argument("--version", action="version",
                    version=f"EyeScan {core.__version__}" + chr(10) + core.CREDIT_LINE)
    ap.add_argument("--scan", help="folder to scan (omit to open the window)")
    ap.add_argument("--threshold", type=float, default=core.DEFAULT_THRESHOLD,
                    help="blink score >= this counts as closed (default 0.5)")
    ap.add_argument("--require-both", action="store_true",
                    help="(ignored: both eyes are always checked)")
    ap.add_argument("--min-face", type=float, default=core.DEFAULT_MIN_FACE_AREA,
                    help="ignore faces smaller than this fraction of the image")
    ap.add_argument("--fast", action="store_true",
                    help="disable the tiled face search (faster, may miss "
                         "faces that are small in the frame)")
    ap.add_argument("--no-gpu", action="store_true",
                    help="never use the GPU (DirectML) for ONNX models")
    ap.add_argument("--workers", type=int, default=0,
                    help="scan worker processes (0 = automatic, 1 = single process)")
    ap.add_argument("--deep", action="store_true",
                    help="add a 3x3 tile pass (slower; many small faces)")
    ap.add_argument("--max-dim", type=int, default=core.DEFAULT_DETECT_MAX_DIM,
                    help="detection resolution cap for the long side")
    ap.add_argument("--recursive", action="store_true", help="include sub-folders")
    ap.add_argument("--csv", help="write a CSV report of every scanned file")
    ap.add_argument("--move", action="store_true", help="move flagged files")
    ap.add_argument("--copy", action="store_true", help="copy flagged files")
    ap.add_argument("--out", default="Closed Eyes", help="output sub-folder name")
    ap.add_argument("--no-keep-down", action="store_true",
                    help="flag looking-down photos as closed (default: keep them)")
    ap.add_argument("--no-cache", action="store_true",
                    help="don't read or write the saved-results database")
    ap.add_argument("--rescan", action="store_true",
                    help="ignore saved results and analyse every photo again")
    ap.add_argument("--dups", choices=("off", "strict", "normal", "loose"),
                    default="normal", help="duplicate detection level (default normal)")
    ap.add_argument("--blur-cut", type=float, default=core.BLUR_SHARP_MIN,
                    help="face focus score below this is 'unclear' (default %(default)s)")
    ap.add_argument("--sort-blurry", action="store_true",
                    help="move blurry / misfocused photos into --blur-out")
    ap.add_argument("--blur-out", default="Blurry", help="folder for --sort-blurry")
    ap.add_argument("--sort-dups", action="store_true",
                    help="move duplicate extra copies into --dup-out (keeps the best)")
    ap.add_argument("--dup-out", default="Duplicates", help="folder for --sort-dups")
    ap.add_argument("--delete-dups", action="store_true",
                    help="send duplicate extra copies to the Recycle Bin (needs --yes)")
    ap.add_argument("--no-animals", action="store_true",
                    help="skip animal / body detection (a little faster)")
    ap.add_argument("--sheet", metavar="DIR",
                    help="write high-resolution JPG contact sheets to DIR")
    ap.add_argument("--sheet-mode", choices=("photos", "faces"), default="photos")
    ap.add_argument("--sheet-ratio", choices=("3:2", "4:3", "16:9", "5:4"),
                    default="3:2")
    ap.add_argument("--sheet-long", type=int, default=6000,
                    help="long side in px (min 4000, default 6000)")
    ap.add_argument("--sheet-only", choices=("flagged", "attention", "unclear", "extras", "all"),
                    default="attention",
                    help="which photos go on the sheets (default: closed + looking down)")
    ap.add_argument("--delete", action="store_true",
                    help="send flagged photos to the Recycle Bin (needs --yes)")
    ap.add_argument("--yes", action="store_true", help="confirm --delete")
    ap.add_argument("--doctor", nargs="?", const="", default=None,
                    help="print runtime diagnostics (optionally to a file) and exit")
    args = ap.parse_args()
    if args.no_gpu:
        import eyescan_gpu

        eyescan_gpu.set_mode(eyescan_gpu.CPU)

    if args.doctor is not None:
        return run_doctor(args.doctor or None)

    if args.scan:
        try:
            return cli(args)
        except KeyboardInterrupt:
            return 130

    return GUI      # no CLI action requested: the caller opens the window


def log_crash(text: str) -> Path | None:
    """Append a traceback to crash.log in the EyeScan user-data folder."""
    import time

    try:
        path = core.user_data_dir() / "crash.log"
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(f"\n===== {time.strftime('%Y-%m-%d %H:%M:%S')}  "
                     f"EyeScan {core.__version__} =====\n{text}\n")
        return path
    except Exception:  # noqa: BLE001
        return None
