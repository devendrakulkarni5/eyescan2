"""Bridge between the Keeper engine and the web UI.

Everything the UI can do is a public method on ``Bridge`` (exposed to JavaScript by
pywebview). All state is private (leading underscore) because pywebview walks public
attributes. Heavy work runs on worker threads; progress and results reach the page
through ``_emit`` as small JSON events, batched so the UI never floods.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import shutil
import sqlite3
import subprocess
import sys
import threading
import time
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


def resource_dir() -> Path:
    """Folder holding ui/ and engine/ (the PyInstaller bundle when frozen)."""
    return Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))


ENGINE_DIR = resource_dir() / "engine"
if ENGINE_DIR.is_dir():
    sys.path.insert(0, str(ENGINE_DIR))

import eyescan_core as core  # noqa: E402
import eyescan_names  # noqa: E402
import keeper_library  # noqa: E402

APP_NAME = "Keeper"
APP_VERSION = "2.3.0"
# "owner/repo" of the GitHub repository whose latest Release (tag like v2.1.0) is the newest version
# (a separate PUBLIC repo used only for installers: the source repo is private and would 404)
GITHUB_REPO = "devendrakulkarni5/eyescan2-releases"
UPDATE_URL = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
THUMB_SIZE = 360
BATCH_SECONDS = 0.4
FINAL_CHUNK = 300
# industry-standard detection resolutions (long side, px). 1600 replaces v1's odd 1536.
RESOLUTIONS = (720, 1080, 1280, 1600, 1920, 2560, 3840)
DEFAULT_RES = 1600
BULK = {
    "closed": lambda r: r.verdict == "closed",
    "unclear": lambda r: r.unclear,
    "dups": lambda r: r.is_duplicate,
}
BULK_FOLDER = {"closed": "Closed Eyes", "unclear": "Blurry", "dups": "Duplicates"}
# settings that Keeper adds on top of core.DEFAULT_SETTINGS (kept in extras.json)
EXTRA_DEFAULTS = {
    "blur_cut": core.BLUR_SHARP_MIN, "misfocus_ratio": core.MISFOCUS_RATIO,
    "min_face": core.DEFAULT_MIN_FACE_AREA, "max_dim": DEFAULT_RES,
    "main_only": core.MAIN_ONLY,    # judge only the main subjects, not background people
    "people_sort": "photos",        # photos | name | closed | unnamed
    "grid_ratio": "original",       # square | 3:2 | 2:3 | original
    "v2_defaults": 0,               # one-time migration marker
    "after_scan_tab": "closed",     # tab to open when a scan completes (scan itself starts on All); "off" = never switch
    "tabs": [],                    # folders that were open as tabs (restored, not rescanned, on start)
    "check_updates": True,         # look for a newer version on start (the only online call)
    "overlay_parts": ["outline", "forehead", "jaw", "chin", "cheeks", "brows", "nose", "lips", "eyes"],
}
# automatic naming is stricter than clustering: SFace scores two different people ~0.47 on real shoots
NAME_MATCH = 0.52
ENGINE_KEYS = {"robust", "deep", "animals", "keep_down", "min_face", "max_dim"}


def data_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA") or str(Path.home())
    d = Path(base) / "EyeScan2"   # folder name kept from before the rename to Keeper so existing data carries over
    d.mkdir(parents=True, exist_ok=True)
    return d


def _pts(seq):
    return [[round(x, 4), round(y, 4)] for x, y in seq]


def snap_res(v) -> int:
    """Nearest standard detection resolution."""
    try:
        v = int(v)
    except (TypeError, ValueError):
        return DEFAULT_RES
    return min(RESOLUTIONS, key=lambda r: abs(r - v))


def db_location() -> Path:
    """Folder holding eyescan2.db (default: the app data folder; movable from Settings)."""
    try:
        d = json.loads((data_dir() / "location.json").read_text("utf-8")).get("dir")
        if d and Path(d).is_dir():
            return Path(d)
    except Exception:  # noqa: BLE001
        pass
    return data_dir()


def db_file() -> Path:
    return db_location() / "eyescan2.db"


def summarize(r: "core.ImageResult", pnames=None, animal_names=None) -> dict:
    """Plain-JSON view of one result without heavy geometry (see ``geometry``)."""
    pnames = pnames or {}
    faces = [{
        "box": [round(v, 4) for v in f.box],
        "state": f.state, "sure": bool(f.sure), "wink": bool(f.wink),
        "left": round(f.blink_left, 2), "right": round(f.blink_right, 2),
        "smile": round(f.smile, 2), "smiling": bool(f.smiling),
        "away": bool(f.looks_away), "hidden": bool(f.eyes_hidden),
        "glasses": bool(f.has_glasses),
        "pitch": None if f.pitch is None else round(f.pitch, 1),
        "yaw": None if f.yaw is None else round(f.yaw, 1),
        "override": f.override, "person": f.person, "pname": pnames.get(f.person),
    } for f in r.faces]
    an = animal_names or {}
    animals = [{"label": a.label, "box": [round(v, 4) for v in a.box], "name": an.get(i)}
               for i, a in enumerate(r.animals)]
    names = sorted({f["pname"] for f in faces if f["pname"]} | {a["name"] for a in animals if a["name"]})
    return {
        "path": str(r.path), "name": r.path.name, "folder": str(r.path.parent),
        "verdict": r.verdict, "flagged": bool(r.is_flagged),
        "needs_check": bool(r.needs_check), "wink": bool(r.has_wink),
        "faces": faces, "n_closed": r.n_closed, "n_down": r.n_down,
        "people": r.people, "group": r.group, "focus": r.focus,
        "unclear": bool(r.unclear), "tags": r.tags(), "names": names,
        "smiling": any(f.smiling for f in r.faces), "animals": animals,
        "teeth": any(f.teeth_showing for f in r.faces),
        "funny": any(f.funny_face for f in r.faces),
        "bindi": any(f.has_bindi for f in r.faces),
        "glasses": any(f.has_glasses for f in r.faces),
        "eyes_hidden": any(f.state == "hidden" for f in r.faces),
        "dup_group": r.dup_group, "dup_best": bool(r.dup_best), "dup_why": r.dup_why,
        "rescue": str(r.rescue.path) if r.rescue is not None else None,
        "size": r.file_size, "w": r.width, "h": r.height,
        "protected": bool(r.protected), "override": r.override,
        "blink": round(r.max_blink, 2), "error": r.error,
    }


def geometry(r: "core.ImageResult") -> list:
    """Per-face outline, eyes, iris and detailed regions (fetched only when drawn)."""
    return [{"outline": _pts(f.outline), "eyes": [_pts(e) for e in f.eyes], "iris": _pts(f.iris),
             "regions": {k: _pts(v) for k, v in (f.regions or {}).items()}} for f in r.faces]


class _ImageServer:
    """Tiny 127.0.0.1 HTTP server for thumbnails/previews: the browser loads <img> natively
    (parallel, cached) instead of shipping base64 through the JS bridge. Only scanned photos are
    served and every URL carries a per-run token."""

    def __init__(self, bridge) -> None:
        self.token = secrets.token_urlsafe(12)
        outer = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"      # keep-alive: many thumbnails over one connection

            def log_message(self, *args):  # noqa: D401 - silence
                pass

            def do_GET(self):  # noqa: N802
                q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
                path = (q.get("p") or [""])[0]
                try:
                    size = int((q.get("s") or ["360"])[0])
                except ValueError:
                    size = 360
                if (q.get("t") or [""])[0] != outer.token or (path not in bridge._results and path not in bridge._lib_paths):
                    self.send_response(403)
                    self.end_headers()
                    return
                f = bridge._thumb_file(path, size)
                if f is None:
                    self.send_response(404)
                    self.end_headers()
                    return
                data = f.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "image/jpeg")
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "private, max-age=86400")
                self.end_headers()
                self.wfile.write(data)

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.httpd.daemon_threads = True
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    @property
    def base(self) -> str:
        return f"http://127.0.0.1:{self.port}/img?t={self.token}&"


class Bridge:
    def __init__(self) -> None:
        self._window = None
        self._results: dict[str, core.ImageResult] = {}
        self._lib_paths: set[str] = set()      # library card thumbnails the image server may serve
        self._engine = None
        self._stop = threading.Event()
        self._scanning = False
        self._overrides = core.Overrides()
        self._settings = core.Settings()
        self._extras = dict(EXTRA_DEFAULTS)
        try:
            raw = json.loads((data_dir() / "extras.json").read_text("utf-8"))
            self._extras.update({k: raw[k] for k in raw if k in EXTRA_DEFAULTS})
        except Exception:  # noqa: BLE001
            pass
        self._extras["max_dim"] = snap_res(self._extras["max_dim"])
        if not self._extras.get("v2_defaults"):       # first run of Keeper
            self._settings["people"] = True           # recognise people by default
            self._extras["v2_defaults"] = 1
            self._settings.save()
            self._save_extras()
        self._apply_globals()
        self._root: Path | None = None
        self._thumb_dir = data_dir() / "thumbs"
        self._thumb_dir.mkdir(parents=True, exist_ok=True)
        self._names = eyescan_names.Registry(db_file())
        self._tags: dict = self._names.tag_map()
        self._clusters: list = []
        self._cluster_names: dict = {}
        self._on_exit = None
        self._thumb_sem = threading.Semaphore(3)
        self._img = _ImageServer(self)

    # ---- plumbing ---------------------------------------------------------
    def _emit(self, kind: str, **payload) -> None:
        if self._window is None:
            return
        msg = json.dumps({"kind": kind, **payload})
        try:
            self._window.evaluate_js(f"window.onEvent({msg})")
        except Exception:  # noqa: BLE001 - window closing mid-scan
            pass

    def _sum(self, r) -> dict:
        """Summary including person names and animal tags."""
        tags = {t["idx"]: t["name"] for t in self._tags.get(str(r.path), []) if t["kind"] == "animal"}
        return summarize(r, self._cluster_names, tags)

    def about(self) -> dict:
        return {"name": APP_NAME, "version": APP_VERSION, "author": core.AUTHOR,
                "brand": core.BRAND, "website": core.WEBSITE, "year": core.COPYRIGHT_YEAR,
                "credit": core.CREDIT_LINE, "rights": core.RIGHTS, "img_base": self._img.base,
                "resolutions": list(RESOLUTIONS)}

    def system_info(self) -> dict:
        try:
            import eyescan_gpu
            gpu = eyescan_gpu.describe()
        except Exception as exc:  # noqa: BLE001
            gpu = f"GPU info unavailable ({exc})"
        return {"gpu": gpu, "cores": os.cpu_count() or 1,
                "people_ok": core._embed_available()}

    def exit(self) -> None:
        if getattr(self, "_on_exit", None):
            self._on_exit()                 # remember the window before it goes
        if self._window is not None:
            self._window.destroy()

    def check_update(self) -> dict:
        """Compare APP_VERSION with the published version.json. Never raises: offline -> ok False."""
        def num(v):
            return tuple(int(p) for p in str(v).strip().lstrip("vV").split(".")[:4])
        try:
            import urllib.request
            req = urllib.request.Request(UPDATE_URL, headers={
                "User-Agent": f"Keeper/{APP_VERSION}", "Accept": "application/vnd.github+json"})
            with urllib.request.urlopen(req, timeout=6) as resp:
                info = json.loads(resp.read(1 << 20).decode("utf-8"))
            latest = str(info["tag_name"]).lstrip("vV")
            # prefer the installer attached to the release, else the release page
            exe = next((a.get("browser_download_url") for a in info.get("assets", [])
                        if str(a.get("name", "")).lower().endswith(".exe")), None)
            url = exe or str(info.get("html_url") or "")
            return {"ok": True, "current": APP_VERSION, "latest": latest,
                    "newer": num(latest) > num(APP_VERSION) and not info.get("prerelease"),
                    "url": url if url.startswith("https://") else core.WEBSITE,
                    "notes": str(info.get("body") or "")[:1000]}
        except Exception as exc:  # noqa: BLE001 - offline, bad JSON, DNS...
            return {"ok": False, "current": APP_VERSION, "error": str(exc)[:200]}

    def open_url(self, url: str) -> None:
        if url.startswith("https://"):
            webbrowser.open(url)

    # ---- settings & recent folders ---------------------------------------
    def _apply_globals(self) -> None:
        """Tunables the engine reads from module globals."""
        core.BLUR_SHARP_MIN = float(self._extras["blur_cut"])
        core.MISFOCUS_RATIO = float(self._extras["misfocus_ratio"])
        core.EAR_DOWN = float(self._settings["ear_down"])
        core.MAIN_ONLY = bool(self._extras["main_only"])
        try:
            import eyescan_gpu
            eyescan_gpu.set_mode(self._settings["gpu_device"] or eyescan_gpu.AUTO)
            eyescan_gpu.set_bench(None)
        except Exception:  # noqa: BLE001 - GPU support is optional
            pass

    def _save_extras(self) -> None:
        try:
            (data_dir() / "extras.json").write_text(json.dumps(self._extras), "utf-8")
        except OSError:
            pass

    def get_settings(self) -> dict:
        return {**self._settings.data, **self._extras}

    def save_settings(self, patch: dict) -> dict:
        changed = set()
        if "max_dim" in (patch or {}):
            patch = {**patch, "max_dim": snap_res(patch["max_dim"])}
        for k, v in (patch or {}).items():
            if k in EXTRA_DEFAULTS:
                if self._extras.get(k) != v:
                    changed.add(k)
                self._extras[k] = v
            elif k in core.DEFAULT_SETTINGS:
                if self._settings[k] != v:
                    changed.add(k)
                self._settings[k] = v
        self._settings.save()
        self._save_extras()
        self._apply_globals()
        if "main_only" in changed and self._results and not self._scanning:
            self._finish_analysis()      # cached measurements, no re-detection
        if changed & ENGINE_KEYS and self._engine is not None and not self._scanning:
            try:
                self._engine.close()
            except Exception:  # noqa: BLE001
                pass
            self._engine = None      # rebuilt with the new options on the next scan
        return self.get_settings()

    def gpu_adapters(self) -> list:
        try:
            import eyescan_gpu
            return eyescan_gpu.gpu_names()
        except Exception:  # noqa: BLE001
            return []

    def gpu_test(self) -> dict:
        """Benchmark the CPU against each DirectML adapter; result is saved."""
        try:
            import eyescan_gpu as gpu
            gpu.set_bench(None)
            info = gpu.choice(None)
            self._settings["gpu_bench"] = {k: v for k, v in info.items() if k not in ("device", "mode")}
            self._settings.save()
            return {"ok": True, "text": gpu.describe(info)}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "text": f"GPU check failed: {exc}"}

    def recent(self) -> list:
        out = []
        for f in self._settings.get("recent", []) or []:
            out.append({"folder": f, "exists": Path(f).is_dir()})
        return out

    def clear_recent(self) -> None:
        self._settings.data["recent"] = []
        self._settings.save()

    def pick_folder(self):
        import webview
        res = self._window.create_file_dialog(webview.FOLDER_DIALOG)
        return res[0] if res else None

    # ---- scanning ----------------------------------------------------------
    def start_scan(self, folder: str, opts: dict | None = None) -> dict:
        opts = opts or {}
        if self._scanning:
            return {"ok": False, "error": "A scan is already running."}
        root = Path(folder)
        if not root.is_dir():
            return {"ok": False, "error": "That folder doesn't exist."}
        for k in ("threshold", "recursive", "robust", "deep", "keep_down", "dup_level",
                  "dup_enabled", "use_cache", "people", "animals", "workers"):
            if k in opts:
                self._settings[k] = opts[k]
        self._settings["folder"] = str(root)
        self._settings.add_recent(str(root))
        self._settings.save()
        self._stop.clear()
        self._scanning = True
        self._root = root
        threading.Thread(target=self._scan, args=(root, bool(opts.get("force")), None),
                         daemon=True).start()
        return {"ok": True}

    def rescan(self, paths: list) -> dict:
        """Re-analyse chosen photos, ignoring their saved results."""
        if self._scanning or not self._root:
            return {"ok": False, "error": "Wait for the current scan to finish."}
        self._stop.clear()
        self._scanning = True
        threading.Thread(target=self._scan, args=(self._root, True, [Path(p) for p in paths]),
                         daemon=True).start()
        return {"ok": True}

    def stop_scan(self) -> None:
        self._stop.set()

    def close_folder(self) -> dict:
        """Stop any scan, forget the open folder and its results (photos and saved data are untouched)."""
        self._stop.set()
        t0 = time.time()
        while self._scanning and time.time() - t0 < 10:
            time.sleep(0.1)
        if self._scanning:
            return {"ok": False, "error": "The scan is still stopping. Try again in a moment."}
        self._results, self._root = {}, None
        self._clusters, self._cluster_names = [], {}
        return {"ok": True}

    def _make_engine(self):
        s = self._settings
        if self._engine is None:
            self._emit("phase", text="Loading face models…")
            self._engine = core.EyeEngine(
                core.ensure_model(lambda m: self._emit("phase", text=str(m))),
                threshold=s["threshold"], require_both=True,
                robust=bool(s["robust"]), deep=bool(s["deep"]),
                keep_down=bool(s["keep_down"]), detect_animals=bool(s["animals"]),
                min_face_area=float(self._extras["min_face"]),
                detect_max_dim=int(self._extras["max_dim"]))
        e = self._engine
        e.threshold, e.require_both = float(s["threshold"]), True
        e.keep_down, e.robust, e.deep = bool(s["keep_down"]), bool(s["robust"]), bool(s["deep"])
        e.embed_faces = bool(s["people"]) and core._embed_available()
        return e

    def _scan(self, root: Path, force: bool, only: list | None) -> None:
        cache = None
        s = self._settings
        thr = float(s["threshold"])
        self._clusters = []                  # people are regrouped after every scan
        try:
            eng = self._make_engine()
            if only is None:
                files = list(core.iter_images(
                    root, bool(s["recursive"]),
                    skip_dirs=("Closed Eyes", "Blurry", "Duplicates", "Contact Sheets")))
                self._results = {}
            else:
                files = only
            total = len(files)
            self._emit("start", total=total, folder=str(root), partial=only is not None)
            if s["use_cache"]:
                cache = self._open_cache()
            batch: list[dict] = []
            i_done = 0
            last = started = time.monotonic()
            workers = int(s["workers"]) or None
            for i, (p, res, _cached) in enumerate(
                    core.scan_files(eng, files, cache, force=force, stop=self._stop,
                                    workers=workers), 1):
                self._overrides.apply(res)
                res.reclassify(thr, True, bool(s["keep_down"]))
                self._results[str(p)] = res
                i_done = i
                batch.append(self._sum(res))
                now = time.monotonic()
                if now - last >= BATCH_SECONDS or i == total:
                    rate = i / max(now - started, 1e-3)
                    self._emit("results", items=batch, done=i, total=total,
                               eta=int((total - i) / rate) if rate else None)
                    batch, last = [], now
            if batch:
                self._emit("results", items=batch, done=total, total=total, eta=0)
            self._emit("phase", text="Finding similar shots…")
            self._finish_analysis()
            allr = list(self._results.values())
            for k in range(0, len(allr), FINAL_CHUNK):          # duplicate info changed: refresh in chunks
                self._emit("results", items=[self._sum(r) for r in allr[k:k + FINAL_CHUNK]],
                           done=total, total=total, eta=0)
            if not self._stop.is_set():
                self._note_library(root)
            self._emit("final", stopped=self._stop.is_set(), done=min(i_done, total), total=total)
        except Exception as exc:  # noqa: BLE001
            self._emit("error", message=f"Scan failed: {exc}")
        finally:
            if cache is not None:
                cache.close()
            self._scanning = False
            self._emit("idle")

    # ---- library of scanned folders ----------------------------------------
    def _note_library(self, root: Path) -> None:
        rs = sorted(self._results)
        if not rs:
            return
        step = max(1, len(rs) // 4)
        lib = keeper_library.Library(db_file())
        lib.note(root, len(rs), sum(1 for r in self._results.values() if r.is_flagged), rs[::step][:4])
        lib.close()

    def library(self) -> list:
        """Every scanned folder (newest first), plus recent folders scanned before the library existed."""
        lib = keeper_library.Library(db_file())
        rows = lib.all()
        seen = {os.path.normcase(os.path.abspath(r["folder"])) for r in rows}
        for f in self._settings.get("recent", []) or []:
            if os.path.normcase(os.path.abspath(f)) not in seen:
                rows.append({"folder": f, "total": None, "flagged": None, "scanned": None, "thumbs": []})
        for r in rows:
            r["exists"] = Path(r["folder"]).is_dir()
            if r["total"] is None:                       # scanned before the library existed: read what the cache knows
                n, r["thumbs"] = lib.from_cache(r["folder"])
                r["total"] = n or None
            self._lib_paths.update(r["thumbs"])
        lib.close()
        return rows

    def library_remove(self, folder: str) -> dict:
        """Forget a folder: its saved scan results, its library card and its recent entry. Photos are untouched."""
        if self._scanning and self._root and os.path.normcase(str(self._root)) == os.path.normcase(os.path.abspath(folder)):
            return {"ok": False, "error": "Close this folder before removing it."}
        lib = keeper_library.Library(db_file())
        lib.remove(folder)
        lib.close()
        c = self._open_cache()
        if c is not None:
            c.forget_folder(folder)
            c.close()
        key = os.path.normcase(os.path.abspath(folder))
        self._settings.data["recent"] = [x for x in self._settings.get("recent", []) or []
                                         if os.path.normcase(os.path.abspath(x)) != key]
        self._settings.save()
        return {"ok": True}

    def folder_ok(self, folder: str) -> bool:
        return Path(folder).is_dir()

    def save_tabs(self, folders: list) -> None:
        self._extras["tabs"] = [str(f) for f in (folders or [])][:20]
        self._save_extras()

    def _finish_analysis(self) -> None:
        s, rs = self._settings, list(self._results.values())
        core.apply_threshold(rs, float(s["threshold"]), True, bool(s["keep_down"]))
        if s["dup_enabled"]:
            core.find_duplicates(rs, core.DUP_LEVELS.get(s["dup_level"], 30))

    def set_threshold(self, value: float) -> list:
        """Live sensitivity slider: re-score cached values, no re-detection."""
        self._settings["threshold"] = float(value)
        self._settings.save()
        self._finish_analysis()
        return [self._sum(r) for r in self._results.values()]

    def _open_cache(self):
        try:
            import eyescan_db
            c = eyescan_db.ResultCache(db_file())
            return c if c.ok else None
        except Exception:  # noqa: BLE001
            return None

    # ---- images ------------------------------------------------------------
    def _thumb_file(self, path: str, size: int = THUMB_SIZE):
        """Cached JPEG for a photo at ``size`` px (0 = original, capped at 6000)."""
        try:
            st = os.stat(path)
            size = 6000 if size <= 0 else max(64, min(size, 6000))
            key = hashlib.sha1(f"{path}|{st.st_size}|{st.st_mtime}|{size}".encode()).hexdigest()
            f = self._thumb_dir / f"{key}.jpg"
            if not f.exists():
                with self._thumb_sem:
                    img = core.load_image(path, size)
                    img.thumbnail((size, size))
                    tmp = f.with_suffix(".tmp")
                    img.save(tmp, "JPEG", quality=90 if size >= 2400 else 82)
                    os.replace(tmp, f)
            return f
        except Exception:  # noqa: BLE001
            return None

    def thumb(self, path: str, size: int = THUMB_SIZE) -> str | None:
        """JPEG data URL (fallback when the image server is unavailable)."""
        f = self._thumb_file(path, size)
        return None if f is None else "data:image/jpeg;base64," + base64.b64encode(f.read_bytes()).decode()

    def preview(self, path: str) -> str | None:
        return self.thumb(path, 2000)

    def geometry(self, path: str) -> list:
        r = self._results.get(path)
        return geometry(r) if r is not None else []

    def face_thumb(self, path: str, box: list, size: int = 96) -> str | None:
        try:
            img = core.load_image(path, 1600)
            w, h = img.size
            x0, y0, x1, y1 = box
            pad = 0.25 * max(x1 - x0, y1 - y0)
            crop = img.crop((max(0, int((x0 - pad) * w)), max(0, int((y0 - pad) * h)),
                             min(w, int((x1 + pad) * w)), min(h, int((y1 + pad) * h))))
            crop.thumbnail((size, size))
            from io import BytesIO
            buf = BytesIO()
            crop.save(buf, "JPEG", quality=85)
            return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()
        except Exception:  # noqa: BLE001
            return None

    # ---- corrections -------------------------------------------------------
    def set_override(self, paths: list, value: str | None) -> list:
        """value: 'keep' | 'flag' | None (reset). Returns refreshed summaries."""
        out = []
        for p in paths:
            r = self._results.get(p)
            if r is None:
                continue
            self._overrides.set_image(p, value)
            if value is None:
                for i in range(len(r.faces)):
                    self._overrides.set_face(p, i, None)
            self._overrides.apply(r)
            r.reclassify(float(self._settings["threshold"]), True, bool(self._settings["keep_down"]))
            out.append(self._sum(r))
        self._overrides.save()
        return out

    def set_face_override(self, path: str, idx: int, value: str | None) -> dict | None:
        r = self._results.get(path)
        if r is None:
            return None
        self._overrides.set_face(path, int(idx), value)
        self._overrides.save()
        self._overrides.apply(r)
        r.reclassify(float(self._settings["threshold"]), True, bool(self._settings["keep_down"]))
        return self._sum(r)

    def set_protect(self, paths: list, value: bool) -> list:
        out = []
        for p in paths:
            r = self._results.get(p)
            if r is None:
                continue
            self._overrides.set_protect(p, bool(value))
            self._overrides.apply(r)
            out.append(self._sum(r))
        self._overrides.save()
        return out

    # ---- bulk tools: sort / delete / undo ---------------------------------
    def bulk_preview(self, which: str) -> dict:
        hit = [r for r in self._results.values() if BULK[which](r)]
        items = [r for r in hit if not r.protected]
        return {"paths": [str(r.path) for r in items], "count": len(items),
                "bytes": sum(r.file_size for r in items),
                "protected": len(hit) - len(items), "folder": BULK_FOLDER[which]}

    def sort_bulk(self, which: str, mode: str, out_name: str, paths: list | None = None) -> dict:
        """Sort a bulk group, or (with ``paths``) exactly the chosen photos."""
        if not self._root:
            return {"ok": False, "error": "Scan a folder first."}
        if paths:
            items = [self._results[p] for p in paths if p in self._results and not self._results[p].protected]
            out_name = out_name or self._settings["out_name"]
        else:
            items = [r for r in self._results.values() if BULK[which](r) and not r.protected]
            out_name = out_name or BULK_FOLDER[which]
        if not items:
            return {"ok": False, "error": "Nothing to sort."}
        out = core.sort_results(items, self._root, out_name, mode,
                                bool(self._settings["recursive"]), log=lambda *_: None,
                                only_flagged=False)
        if out.manifest_path:
            (data_dir() / "last_manifest.txt").write_text(str(out.manifest_path), "utf-8")
        gone = []
        if mode == "move":
            for r in items:
                if not r.path.exists():
                    gone.append(str(r.path))
                    self._results.pop(str(r.path), None)
        return {"ok": True, "moved": out.moved, "failed": out.failed, "mode": mode,
                "gone": gone, "folder": str(self._root / out_name)}

    def undo_sort(self) -> dict:
        f = data_dir() / "last_manifest.txt"
        try:
            mp = Path(f.read_text("utf-8").strip())
        except OSError:
            return {"ok": False, "error": "Nothing to undo (copies never touch your originals)."}
        if not mp.exists():
            return {"ok": False, "error": "The undo record is gone."}
        ok, fail = core.undo_move(mp, log=lambda *_: None)
        return {"ok": True, "restored": ok, "failed": fail,
                "note": "Run Scan (F5) to refresh the list."}

    def trash(self, paths: list, include_protected: bool = False) -> dict:
        """Recycle Bin (recoverable). Protected photos are skipped unless told otherwise."""
        targets, skipped, asked = [], 0, []
        for p in paths:
            r = self._results.get(p)
            if r is not None and r.protected and not include_protected:
                skipped += 1
                continue
            asked.append(p)
            targets.append(p)
            targets += [str(c) for c in core.raw_companions(Path(p))]
        done, failed = core.recycle(targets)
        gone = [p for p in asked if p not in failed]
        for p in gone:
            self._results.pop(p, None)
        return {"deleted": done, "failed": failed, "skipped": skipped, "gone": gone}

    def forget(self, paths: list) -> None:
        for p in paths:
            self._results.pop(p, None)

    # ---- exports -----------------------------------------------------------
    def export_csv(self) -> dict:
        import webview
        if not self._results:
            return {"ok": False, "error": "Nothing to export yet."}
        res = self._window.create_file_dialog(
            webview.SAVE_DIALOG, save_filename="keeper_report.csv",
            file_types=("CSV (*.csv)",))
        if not res:
            return {"ok": False, "cancelled": True}
        path = res if isinstance(res, str) else res[0]
        core.write_csv(path, list(self._results.values()))
        return {"ok": True, "path": str(path)}

    def make_sheets(self, opts: dict) -> dict:
        if not self._results or not self._root:
            return {"ok": False, "error": "Scan a folder first."}
        if self._scanning:
            return {"ok": False, "error": "Wait for the scan to finish."}
        self._settings["sheet_ratio"] = opts.get("ratio", "3:2")
        self._settings["sheet_mode"] = opts.get("mode", "photos")
        self._settings["sheet_long"] = int(opts.get("long", 6000))
        self._settings.save()
        threading.Thread(target=self._sheets, args=(opts,), daemon=True).start()
        return {"ok": True}

    def _sheets(self, opts: dict) -> None:
        try:
            import eyescan_report as report
            out = self._root / "Contact Sheets"
            pages = report.make_contact_sheets(
                list(self._results.values()), out, mode=opts.get("mode", "photos"),
                ratio=opts.get("ratio", "3:2"), long_side=int(opts.get("long", 6000)),
                only=opts.get("only", "flagged"), theme=self._settings["theme"],
                title=f"{core.APP_NAME} - {self._root.name}",
                progress=lambda d, t: self._emit("sheet_progress", done=d, total=t))
            self._emit("sheet_done", pages=[str(p) for p in pages], folder=str(out))
        except Exception as exc:  # noqa: BLE001
            self._emit("error", message=f"Contact sheets failed: {exc}")

    # ---- people & names ----------------------------------------------------
    def people(self, quiet: bool = False, refresh: bool = False) -> dict:
        """Cluster faces (quality-weighted), match clusters to saved names, return list + suggestions.

        The clustering is cached until the next scan so manual merges in this session survive."""
        if refresh or not self._clusters:
            rs = list(self._results.values())
            try:
                from eyescan_people import cluster_people
                clusters = cluster_people(rs)
            except Exception as exc:  # noqa: BLE001
                return {"ok": False, "error": f"People view unavailable ({exc})"}
            if not clusters:
                return {"ok": False, "error": "" if quiet else "No recognised faces. Turn on People in Settings and re-scan."}
            out = []
            for c in clusters:
                photos = {str(r.path) for r in rs for f in r.faces if f.person == c["id"]}
                closed = sum(1 for r in rs if r.is_flagged and any(f.person == c["id"] for f in r.faces))
                br, bf = c["best"]
                out.append({"id": c["id"], "photos": sorted(photos), "closed": closed,
                            "path": str(br.path), "box": [round(v, 4) for v in bf.box],
                            "faces": c["faces"], "_cen": c["centroid"], "_ex": c["exemplars"],
                            "quality": round(c["quality"], 2)})
            self._clusters = out
        return self._people_payload()

    def _people_payload(self) -> dict:
        from eyescan_people import suggest_merges
        self._cluster_names = {}
        for c in self._clusters:
            if c["_cen"] is not None:
                name, _sim = self._names.match(c["_cen"], NAME_MATCH, c["_ex"])
                if name:
                    self._cluster_names[c["id"]] = name
        assign = {}
        for r in self._results.values():
            if any(f.person is not None for f in r.faces):
                assign[str(r.path)] = [[f.person, self._cluster_names.get(f.person)] for f in r.faces]
        people = [{k: v for k, v in c.items() if not k.startswith("_")} | {"name": self._cluster_names.get(c["id"])}
                  for c in self._clusters]
        sug = suggest_merges([{"id": c["id"], "centroid": c["_cen"], "exemplars": c["_ex"]} for c in self._clusters])
        return {"ok": True, "people": people, "assign": assign, "known": self._names.all_names(), "suggestions": sug}

    def merge_people(self, ids: list, name: str = "") -> dict:
        """Merge several recognised people into one. With a name the merge is saved (and later scans
        recognise the person); without one it lasts until the next scan."""
        import numpy as np
        ids = [int(i) for i in ids]
        group = [c for c in self._clusters if c["id"] in ids]
        if len(group) < 2:
            return {"ok": False, "error": "Pick at least two people to merge."}
        name = (name or "").strip()
        keep = max(group, key=lambda c: len(c["photos"]))
        cen = sum(c["_cen"] * c["faces"] for c in group if c["_cen"] is not None)
        cen = (cen / (float(np.linalg.norm(cen)) or 1.0)).astype(np.float32)
        pool = [e for c in group for e in c["_ex"]]
        ex = self._names._diverse(pool, 8, 0.90) if pool else [cen]
        old_names = {self._cluster_names.get(c["id"]) for c in group} - {None}
        for r in self._results.values():
            for f in r.faces:
                if f.person in ids:
                    f.person = keep["id"]
        merged = dict(keep, photos=sorted({p for c in group for p in c["photos"]}),
                      closed=sum(c["closed"] for c in group), faces=sum(c["faces"] for c in group),
                      _cen=cen, _ex=ex)
        self._clusters = [merged if c["id"] == keep["id"] else c for c in self._clusters if c["id"] == keep["id"] or c["id"] not in ids]
        if name:
            for old in old_names:
                if old.lower() != name.lower() and any(p["name"].lower() == old.lower() for p in self._names.persons()):
                    if any(p["name"].lower() == name.lower() for p in self._names.persons()):
                        self._names.merge_names(old, name)
                    else:
                        self._names.rename_person(old, name)
            self._names.save_person(name, cen, max(1, merged["faces"]), ex)
        return self._people_payload()

    def split_person(self, cluster_id: int) -> dict:
        """Split one recognised person that is really several people: its faces are grouped again with
        much stricter thresholds. Lasts until the next scan; saved names are not touched."""
        import numpy as np
        from types import SimpleNamespace
        from eyescan_people import cluster_people, SAME_PERSON, MERGE_PERSON
        cid = int(cluster_id)
        old = next((c for c in self._clusters if c["id"] == cid), None)
        if old is None:
            return {"ok": False, "error": "That person isn't available."}
        rs = [r for r in self._results.values() if any(f.person == cid for f in r.faces)]
        proxies = [SimpleNamespace(path=r.path, width=getattr(r, "width", 0), height=getattr(r, "height", 0),
                                   faces=[f for f in r.faces if f.person == cid]) for r in rs]
        parts = cluster_people(proxies, same=SAME_PERSON + 0.13, merge=MERGE_PERSON + 0.14)
        if len(parts) < 2:
            for r in rs:
                for f in r.faces:
                    if f.person is None and getattr(f, "emb", None) is not None:
                        f.person = cid
            return {"ok": False, "error": "These faces all look like the same person to the recognition model."}
        base = max(c["id"] for c in self._clusters)
        new = []
        for k, part in enumerate(parts):
            nid = cid if k == 0 else base + k
            for pr, f in ((pr, f) for pr in proxies for f in pr.faces if f.person == part["id"]):
                f.person = -nid                      # mark, so ids from different parts cannot collide
            br, bf = part["best"]
            new.append({"id": nid, "faces": part["faces"], "path": str(br.path), "box": [round(v, 4) for v in bf.box],
                        "_cen": part["centroid"], "_ex": part["exemplars"], "quality": round(part["quality"], 2)})
        for r in rs:
            for f in r.faces:
                if f.person is not None and f.person < 0:
                    f.person = -f.person
        for c in new:
            c["photos"] = sorted({str(r.path) for r in rs for f in r.faces if f.person == c["id"]})
            c["closed"] = sum(1 for r in rs if r.is_flagged and any(f.person == c["id"] for f in r.faces))
        self._clusters = [c for c in self._clusters if c["id"] != cid] + new
        return self._people_payload()

    def name_person(self, cluster_id: int, name: str) -> dict:
        """Give a recognised person a name; saved in the database and matched in every later scan."""
        name = (name or "").strip()
        c = next((x for x in self._clusters if x["id"] == cluster_id), None)
        if c is None or c["_cen"] is None:
            return {"ok": False, "error": "That person isn't available. Scan with People turned on."}
        if not name:
            return {"ok": False, "error": "Enter a name."}
        self._names.save_person(name, c["_cen"], max(1, c["faces"]), c["_ex"])
        return self._people_payload()

    def ignore_person(self, cluster_id: int) -> dict:
        """Mark every face of a recognised person as ignored (e.g. a false 'person'); returns refreshed photos."""
        out = []
        for r in self._results.values():
            hit = False
            for i, f in enumerate(r.faces):
                if f.person == cluster_id:
                    self._overrides.set_face(str(r.path), i, "ignore")
                    hit = True
            if hit:
                self._overrides.apply(r)
                r.reclassify(float(self._settings["threshold"]), True, bool(self._settings["keep_down"]))
                out.append(self._sum(r))
        self._overrides.save()
        self._clusters = [c for c in self._clusters if c["id"] != cluster_id]
        return {"items": out, "payload": self._people_payload() if self._clusters else {"ok": True, "people": [], "assign": {}, "known": [], "suggestions": []}}

    def forget_person(self, name: str) -> dict:
        self._names.delete_person(name)
        return self._people_payload()

    def rename_person(self, old: str, new: str) -> dict:
        if not self._names.rename_person(old, new):
            return {"ok": False, "error": "Couldn't rename (is that name already used?)."}
        return self._people_payload()

    def tag_animal(self, path: str, idx: int, name: str) -> dict | None:
        r = self._results.get(path)
        if r is None or idx >= len(r.animals):
            return None
        self._names.set_tag(path, "animal", int(idx), (name or "").strip(), list(r.animals[idx].box))
        self._tags = self._names.tag_map()
        return self._sum(r)

    def known_names(self) -> list:
        return self._names.all_names()

    # ---- database: location, info, clean-up --------------------------------
    def db_info(self) -> dict:
        f = db_file()
        size = sum(os.path.getsize(str(f) + sfx) for sfx in ("", "-wal", "-shm") if os.path.exists(str(f) + sfx))
        info = {"path": str(f), "folder": str(f.parent), "bytes": size, "default": f.parent == data_dir(),
                "results": 0, "oldest": None, "newest": None, **self._names.counts(),
                "thumbs": sum(1 for _ in self._thumb_dir.glob("*.jpg")),
                "thumb_bytes": sum(x.stat().st_size for x in self._thumb_dir.glob("*.jpg"))}
        c = self._open_cache()
        if c is not None:
            st = c.stats()
            info.update(results=st["count"], oldest=st["oldest"], newest=st["newest"])
            c.close()
        return info

    def db_open_folder(self) -> None:
        if sys.platform == "win32":
            subprocess.Popen(["explorer", str(db_location())])

    def db_move(self, new_dir: str, use_existing: bool = False) -> dict:
        """Copy the database to ``new_dir`` (SQLite backup API), switch to it, remove the old file."""
        if self._scanning:
            return {"ok": False, "error": "Wait for the scan to finish."}
        dest_dir = Path(new_dir)
        if not dest_dir.is_dir():
            return {"ok": False, "error": "That folder doesn't exist."}
        src, dst = db_file(), dest_dir / "eyescan2.db"
        if dst.resolve() == src.resolve():
            return {"ok": False, "error": "The database is already in that folder."}
        if dst.exists() and not use_existing:
            return {"ok": False, "exists": True, "error": "A database already exists there."}
        try:
            self._names.close()
            if not use_existing:
                s_db, d_db = sqlite3.connect(str(src)), sqlite3.connect(str(dst))
                with d_db:
                    s_db.backup(d_db)
                s_db.close()
                d_db.close()
            (data_dir() / "location.json").write_text(json.dumps({"dir": str(dest_dir)}), "utf-8")
            if not use_existing:
                for sfx in ("", "-wal", "-shm"):
                    try:
                        os.remove(str(src) + sfx)
                    except OSError:
                        pass
        except Exception as exc:  # noqa: BLE001
            self._names = eyescan_names.Registry(db_file())
            return {"ok": False, "error": f"Couldn't move the database: {exc}"}
        self._names = eyescan_names.Registry(db_file())
        self._tags = self._names.tag_map()
        return {"ok": True, **self.db_info()}

    def db_use_default(self) -> dict:
        return self.db_move(str(data_dir())) if db_location() != data_dir() else {"ok": True, **self.db_info()}

    def db_clear(self, what: str) -> dict:
        """what: results | folder | missing | people | tags | names | thumbs | compact | all"""
        if self._scanning:
            return {"ok": False, "error": "Wait for the scan to finish."}
        n = 0
        c = self._open_cache() if what in ("results", "folder", "missing", "compact", "all") else None
        lib = keeper_library.Library(db_file())
        try:
            if what == "results" and c:
                n = c.clear()
                lib.clear()
            elif what == "folder" and c and self._root:
                n = c.forget_folder(self._root)
                lib.remove(self._root)
            elif what == "missing" and c:
                n = c.prune_missing()
            elif what == "compact" and c:
                c.vacuum()
            elif what in ("people", "tags", "names"):
                n = self._names.clear(what)
                if what != "tags":
                    self._cluster_names = {}
                if what != "people":
                    self._tags = self._names.tag_map()
            elif what == "thumbs":
                for f in self._thumb_dir.glob("*.jpg"):
                    try:
                        f.unlink()
                        n += 1
                    except OSError:
                        pass
            elif what == "all":
                lib.clear()
                n = (c.clear() if c else 0) + self._names.clear("names")
                self._cluster_names, self._tags = {}, {}
        finally:
            lib.close()
            if c is not None:
                c.close()
        return {"ok": True, "removed": n, **self.db_info()}

    # ---- file helpers ------------------------------------------------------
    def reveal(self, path: str) -> None:
        if sys.platform == "win32":
            subprocess.Popen(["explorer", f"/select,{os.path.normpath(path)}"])

    def open_file(self, path: str) -> None:
        if sys.platform == "win32":
            os.startfile(path)  # noqa: S606 - user-chosen photo

    def rename(self, path: str, new_name: str) -> dict:
        r = self._results.get(path)
        p = Path(path)
        if not new_name or any(c in new_name for c in '\\/:*?"<>|'):
            return {"ok": False, "error": "That isn't a valid file name."}
        if not Path(new_name).suffix:
            new_name += p.suffix
        target = p.with_name(new_name)
        if target.exists():
            return {"ok": False, "error": "A file with that name already exists."}
        try:
            p.rename(target)
        except OSError as exc:
            return {"ok": False, "error": f"Could not rename: {exc}"}
        if r is not None:
            self._results.pop(path, None)
            r.path = target
            self._results[str(target)] = r
            return {"ok": True, "old": path, "item": self._sum(r)}
        return {"ok": True, "old": path, "item": None}

    def copy_files(self, paths: list) -> bool:
        """Put files on the Windows clipboard so they can be pasted in Explorer."""
        if sys.platform != "win32" or not paths:
            return False
        lit = ",".join("'" + p.replace("'", "''") + "'" for p in paths)
        subprocess.run(["powershell", "-NoProfile", "-Command", f"Set-Clipboard -LiteralPath {lit}"],
                       creationflags=0x08000000, check=False)
        return True
