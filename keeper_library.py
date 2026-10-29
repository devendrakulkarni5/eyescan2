"""Library of scanned folders: one summary row per folder, kept in the same SQLite file as the scan cache.

The cache (`results`) is keyed per photo, so it cannot answer "how many photos / closed eyes in this folder"
cheaply. This table is written at the end of every scan and drives the start-screen library and the tab strip.
It is disposable like the cache: clearing it never touches photos.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS folders (
    folder  TEXT PRIMARY KEY,
    name    TEXT NOT NULL,
    total   INTEGER NOT NULL,
    flagged INTEGER NOT NULL,
    scanned REAL    NOT NULL,
    thumbs  TEXT    NOT NULL
);
"""


def _key(folder) -> str:
    return os.path.normcase(os.path.abspath(str(folder)))


class Library:
    def __init__(self, path: Path) -> None:
        self._lock = threading.Lock()
        self._db = None
        try:
            self._db = sqlite3.connect(str(path), check_same_thread=False, timeout=10)
            self._db.execute("PRAGMA journal_mode=WAL")
            self._db.executescript(_SCHEMA)
            self._db.commit()
        except sqlite3.Error:
            self._db = None

    def note(self, folder, total: int, flagged: int, thumbs: list) -> None:
        if not self._db:
            return
        with self._lock:
            try:
                self._db.execute(
                    "INSERT OR REPLACE INTO folders(folder, name, total, flagged, scanned, thumbs) VALUES(?,?,?,?,?,?)",
                    (_key(folder), str(folder), int(total), int(flagged), time.time(), json.dumps(thumbs)))
                self._db.commit()
            except sqlite3.Error:
                pass

    def all(self) -> list[dict]:
        """Newest scan first. ``folder`` is the path as it was opened."""
        if not self._db:
            return []
        with self._lock:
            try:
                rows = self._db.execute(
                    "SELECT name, total, flagged, scanned, thumbs FROM folders ORDER BY scanned DESC").fetchall()
            except sqlite3.Error:
                return []
        out = []
        for name, total, flagged, scanned, thumbs in rows:
            try:
                th = json.loads(thumbs)
            except ValueError:
                th = []
            out.append({"folder": name, "total": total, "flagged": flagged, "scanned": scanned, "thumbs": th})
        return out

    def from_cache(self, folder) -> tuple[int, list]:
        """(photo count, 4 sample paths) for a folder scanned before the library existed, read from the scan cache."""
        if not self._db:
            return 0, []
        prefix = _key(folder).rstrip("\/") + os.sep
        with self._lock:
            try:
                rows = [r[0] for r in self._db.execute(
                    "SELECT path FROM results WHERE substr(path, 1, ?) = ? ORDER BY path", (len(prefix), prefix))]
            except sqlite3.Error:
                return 0, []
        step = max(1, len(rows) // 4)
        return len(rows), rows[::step][:4]

    def remove(self, folder) -> None:
        if not self._db:
            return
        with self._lock:
            try:
                self._db.execute("DELETE FROM folders WHERE folder=?", (_key(folder),))
                self._db.commit()
            except sqlite3.Error:
                pass

    def clear(self) -> None:
        if not self._db:
            return
        with self._lock:
            try:
                self._db.execute("DELETE FROM folders")
                self._db.commit()
            except sqlite3.Error:
                pass

    def close(self) -> None:
        if self._db:
            with self._lock:
                try:
                    self._db.close()
                except sqlite3.Error:
                    pass
                self._db = None
