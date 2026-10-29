"""EyeScan results cache - SQLite.

Stores each photo's *measurements* (faces, blink / EAR scores, sharpness,
hash ...) keyed by file path + size + modified time + engine settings, so a
re-scan only analyses photos that are new or changed, and an interrupted scan
resumes where it stopped. Verdicts are recomputed on load from the current
sensitivity, so changing a threshold never needs a re-scan.

The cache is disposable: the app works without it, and the Settings tab can
clear it or drop entries for files that no longer exist.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from pathlib import Path

import eyescan_core as core

_SCHEMA = """
CREATE TABLE IF NOT EXISTS results (
    path    TEXT PRIMARY KEY,
    size    INTEGER NOT NULL,
    mtime   REAL    NOT NULL,
    key     TEXT    NOT NULL,
    data    TEXT    NOT NULL,
    scanned REAL    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_results_scanned ON results(scanned);
"""


class ResultCache:
    """Thread-safe (one lock) wrapper; safe to share with the scan thread."""

    COMMIT_EVERY = 25

    def __init__(self, path: Path | None = None) -> None:
        self.path = Path(path) if path else core.user_data_dir() / "eyescan.db"
        self._lock = threading.Lock()
        self._pending = 0
        self.error: str | None = None
        try:
            self._db = sqlite3.connect(str(self.path), check_same_thread=False,
                                       timeout=10)
            self._db.execute("PRAGMA journal_mode=WAL")
            self._db.execute("PRAGMA synchronous=NORMAL")
            self._db.executescript(_SCHEMA)
            self._db.commit()
        except sqlite3.Error as exc:       # unusable DB -> run without a cache
            self._db = None
            self.error = str(exc)

    @property
    def ok(self) -> bool:
        return self._db is not None

    @staticmethod
    def _k(p) -> str:
        return os.path.normcase(os.path.abspath(str(p)))

    # -- read / write ------------------------------------------------------ #

    def get(self, path, size: int, mtime: float, key: str):
        if not self._db:
            return None
        with self._lock:
            try:
                row = self._db.execute(
                    "SELECT size, mtime, key, data FROM results WHERE path=?",
                    (self._k(path),)).fetchone()
            except sqlite3.Error:
                return None
        if not row or row[0] != size or abs(row[1] - mtime) > 1e-3 or row[2] != key:
            return None
        try:
            return core.result_from_dict(path, json.loads(row[3]))
        except Exception:  # noqa: BLE001 - corrupt row: just re-scan the photo
            return None

    def put(self, path, size: int, mtime: float, key: str, result) -> None:
        if not self._db:
            return
        try:
            data = json.dumps(core.result_to_dict(result), separators=(",", ":"))
        except Exception:  # noqa: BLE001
            return
        with self._lock:
            try:
                self._db.execute(
                    "INSERT OR REPLACE INTO results VALUES (?,?,?,?,?,?)",
                    (self._k(path), size, mtime, key, data, time.time()))
                self._pending += 1
                if self._pending >= self.COMMIT_EVERY:
                    self._db.commit()
                    self._pending = 0
            except sqlite3.Error:
                pass

    def flush(self) -> None:
        if not self._db:
            return
        with self._lock:
            try:
                self._db.commit()
                self._pending = 0
            except sqlite3.Error:
                pass

    # -- housekeeping ------------------------------------------------------ #

    def stats(self) -> dict:
        """{'count', 'bytes' (file size on disk), 'oldest', 'newest'}."""
        out = {"count": 0, "bytes": 0, "oldest": None, "newest": None}
        if not self._db:
            return out
        self.flush()
        with self._lock:
            try:
                n, lo, hi = self._db.execute(
                    "SELECT COUNT(*), MIN(scanned), MAX(scanned) FROM results"
                ).fetchone()
                out.update(count=n or 0, oldest=lo, newest=hi)
            except sqlite3.Error:
                pass
        total = 0
        for suffix in ("", "-wal", "-shm"):
            try:
                total += os.path.getsize(str(self.path) + suffix)
            except OSError:
                pass
        out["bytes"] = total
        return out

    def forget_folder(self, folder) -> int:
        """Drop cached results under ``folder`` (recursive)."""
        if not self._db:
            return 0
        prefix = self._k(folder).rstrip("\\/") + os.sep
        with self._lock:
            try:
                cur = self._db.execute(
                    "DELETE FROM results WHERE substr(path, 1, ?) = ?",
                    (len(prefix), prefix))
                self._db.commit()
                return cur.rowcount
            except sqlite3.Error:
                return 0

    def prune_missing(self) -> int:
        """Remove entries whose file no longer exists. Returns rows removed."""
        if not self._db:
            return 0
        self.flush()
        with self._lock:
            try:
                paths = [r[0] for r in self._db.execute("SELECT path FROM results")]
            except sqlite3.Error:
                return 0
            gone = [(p,) for p in paths if not os.path.exists(p)]
            try:
                self._db.executemany("DELETE FROM results WHERE path=?", gone)
                self._db.commit()
            except sqlite3.Error:
                return 0
        self.vacuum()
        return len(gone)

    def clear(self) -> int:
        """Delete everything and shrink the file. Returns rows removed."""
        if not self._db:
            return 0
        with self._lock:
            try:
                n = self._db.execute("SELECT COUNT(*) FROM results").fetchone()[0]
                self._db.execute("DELETE FROM results")
                self._db.commit()
            except sqlite3.Error:
                return 0
        self.vacuum()
        return n

    def vacuum(self) -> None:
        if not self._db:
            return
        with self._lock:
            try:
                self._db.commit()
                self._db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                self._db.execute("VACUUM")
            except sqlite3.Error:
                pass

    def close(self) -> None:
        if self._db:
            self.flush()
            with self._lock:
                try:
                    self._db.close()
                except sqlite3.Error:
                    pass
            self._db = None
