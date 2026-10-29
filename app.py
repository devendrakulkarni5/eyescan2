"""Keeper - desktop entry point (Windows, WebView2 via pywebview).

No arguments opens the window. Any CLI option (``--scan``, ``--doctor``, ...) runs the
command-line tool in ``cli.py`` instead, so ``Keeper.exe --scan "D:\\Photos"`` works.

The window remembers its size, position, the screen it was on and whether it was maximized
(``window.json`` in the app data folder). If that screen is gone the window opens centred on the
primary screen instead of off-screen.
"""
from __future__ import annotations

import json
import multiprocessing
import sys
import threading
from pathlib import Path

DEFAULT = {"x": None, "y": None, "width": 1360, "height": 860, "maximized": False, "screen": 0}


class WindowMemory:
    """Persisted window geometry. Bounds are only tracked while the window is not maximized."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.data = dict(DEFAULT)
        try:
            raw = json.loads(path.read_text("utf-8"))
            self.data.update({k: raw[k] for k in DEFAULT if k in raw})
        except Exception:  # noqa: BLE001
            pass
        self.window = None
        self._timer: threading.Timer | None = None

    # -- native helpers (WinForms host) ------------------------------------ #
    def _maximized(self) -> bool:
        try:
            from System import Func
            form = self.window.native
            return int(form.Invoke(Func[int](lambda: int(form.WindowState)))) == 2
        except Exception:  # noqa: BLE001
            return False

    def _screen_index(self) -> int:
        try:
            import webview
            w = self.window
            cx, cy = w.x + w.width // 2, w.y + w.height // 2
            for i, s in enumerate(webview.screens):
                if s.x <= cx < s.x + s.width and s.y <= cy < s.y + s.height:
                    return i
        except Exception:  # noqa: BLE001
            pass
        return int(self.data.get("screen", 0))

    # -- tracking ---------------------------------------------------------- #
    def changed(self, *_args) -> None:
        """Moved / resized: remember the bounds (debounced) unless the window is maximized."""
        if self._timer is not None:
            self._timer.cancel()
        self._timer = threading.Timer(0.6, self.save)
        self._timer.daemon = True
        self._timer.start()

    def save(self, *_args) -> None:
        w = self.window
        if w is None:
            return
        try:
            maxed = self._maximized()
            self.data["maximized"] = maxed
            if not maxed and w.width >= 400 and w.height >= 300:
                self.data.update(x=int(w.x), y=int(w.y), width=int(w.width), height=int(w.height))
            self.data["screen"] = self._screen_index()
            self.path.write_text(json.dumps(self.data), "utf-8")
        except Exception:  # noqa: BLE001 - never block closing the window
            pass

    # -- restoring --------------------------------------------------------- #
    def place_on_screen(self) -> None:
        """Runs after the window exists: undo a saved position whose screen is no longer there."""
        try:
            import webview
            w, screens = self.window, webview.screens
            cx, cy = w.x + w.width // 2, w.y + w.height // 2
            if not any(s.x <= cx < s.x + s.width and s.y <= cy < s.y + s.height for s in screens):
                p = screens[0]
                w.move(p.x + max(0, (p.width - w.width) // 2), p.y + max(0, (p.height - w.height) // 2))
            if self.data.get("maximized"):
                w.maximize()
        except Exception:  # noqa: BLE001
            pass


APP_ID = "DEeV.Keeper"


def set_app_id() -> None:
    """Own taskbar identity: without it the taskbar shows pythonw.exe's icon and groups with Python."""
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_ID)
    except Exception:  # noqa: BLE001
        pass


def set_window_icon(title: str, ico: Path) -> bool:
    """WM_SETICON (small + big) on the window, found by title. Works for source runs too."""
    try:
        import ctypes
        import time
        u = ctypes.windll.user32
        u.FindWindowW.restype = ctypes.c_void_p
        u.LoadImageW.restype = ctypes.c_void_p
        u.LoadImageW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_uint, ctypes.c_int, ctypes.c_int, ctypes.c_uint]
        u.SendMessageW.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_void_p, ctypes.c_void_p]
        hwnd = None
        for _ in range(60):
            hwnd = u.FindWindowW(None, title)
            if hwnd:
                break
            time.sleep(0.25)
        if not hwnd:
            return False
        small, big = u.GetSystemMetrics(49), u.GetSystemMetrics(11)      # SM_CXSMICON, SM_CXICON
        hs = u.LoadImageW(None, str(ico), 1, small, small, 0x10)         # IMAGE_ICON, LR_LOADFROMFILE
        hb = u.LoadImageW(None, str(ico), 1, big, big, 0x10)
        if hs:
            u.SendMessageW(hwnd, 0x80, 0, hs)                             # WM_SETICON, ICON_SMALL
        if hb:
            u.SendMessageW(hwnd, 0x80, 1, hb)                             # ICON_BIG
        return bool(hs or hb)
    except Exception:  # noqa: BLE001
        return False


def set_title_icon(window, ico: Path, memory: WindowMemory) -> None:
    """Give the window the Keeper icon and restore its screen. Never fatal."""
    from bridge import APP_NAME
    set_window_icon(APP_NAME, ico)
    memory.place_on_screen()


def main() -> int:
    import webview

    from bridge import APP_NAME, Bridge, data_dir, resource_dir

    set_app_id()
    ui = resource_dir() / "ui"
    bridge = Bridge()
    mem = WindowMemory(data_dir() / "window.json")
    d = mem.data
    kw = {}
    if d["x"] is not None and d["y"] is not None:
        kw = {"x": int(d["x"]), "y": int(d["y"])}
    bridge._window = webview.create_window(
        APP_NAME, (ui / "index.html").as_uri(), js_api=bridge, width=int(d["width"]),
        height=int(d["height"]), min_size=(960, 620), background_color="#0F172A",
        text_select=False, **kw)
    mem.window = bridge._window
    bridge._on_exit = mem.save
    bridge._window.events.moved += mem.changed
    bridge._window.events.resized += mem.changed
    bridge._window.events.closing += mem.save
    webview.start(set_title_icon, (bridge._window, ui / "assets" / "Keeper.ico", mem),
                  debug="--debug" in sys.argv)
    return 0


if __name__ == "__main__":
    multiprocessing.freeze_support()    # scan workers re-import this module
    if any(a.startswith("--") and a != "--debug" for a in sys.argv[1:]):
        import cli
        code = cli.main()
        if code != cli.GUI:
            raise SystemExit(code)
    try:
        raise SystemExit(main())
    except Exception:  # noqa: BLE001 - leave a trace; the exe has no console
        import traceback
        import cli
        cli.log_crash(traceback.format_exc())
        raise SystemExit(1)
