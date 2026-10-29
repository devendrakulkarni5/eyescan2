"""EyeScan names registry - people and animal names, in the same SQLite file as the results cache.

*People*: face-recognition clusters ("Person 3") are recomputed on every scan, so a name is bound
to the person's **face embedding centroid**, not to a cluster number. After a scan each cluster is
matched against the stored centroids and inherits the name (cosine similarity, SFace).
*Animals*: the detector has no identity model, so a name is a per-photo **tag** on the animal's box.

Tables (created next to ``results``): ``persons`` and ``tags``. Both survive "clear scan results".
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS persons (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    name     TEXT NOT NULL UNIQUE COLLATE NOCASE,
    kind     TEXT NOT NULL DEFAULT 'person',
    centroid BLOB,
    n        INTEGER NOT NULL DEFAULT 0,
    updated  REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS person_faces (
    name    TEXT NOT NULL COLLATE NOCASE,
    emb     BLOB NOT NULL,
    updated REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_person_faces_name ON person_faces(name);
CREATE TABLE IF NOT EXISTS tags (
    path    TEXT NOT NULL,
    kind    TEXT NOT NULL,
    idx     INTEGER NOT NULL,
    name    TEXT NOT NULL COLLATE NOCASE,
    box     TEXT,
    updated REAL NOT NULL,
    PRIMARY KEY (path, kind, idx)
);
CREATE INDEX IF NOT EXISTS idx_tags_name ON tags(name);
"""


def _np():
    import numpy as np
    return np


class Registry:
    """Thread-safe wrapper (one lock, own connection)."""

    def __init__(self, path) -> None:
        self.path = Path(path)
        self._lock = threading.Lock()
        self.error: str | None = None
        try:
            self._db = sqlite3.connect(str(self.path), check_same_thread=False, timeout=10)
            self._db.execute("PRAGMA journal_mode=WAL")
            self._db.executescript(_SCHEMA)
            self._db.commit()
        except sqlite3.Error as exc:
            self._db = None
            self.error = str(exc)

    @property
    def ok(self) -> bool:
        return self._db is not None

    def close(self) -> None:
        with self._lock:
            if self._db is not None:
                try:
                    self._db.commit()
                    self._db.close()
                except sqlite3.Error:
                    pass
                self._db = None

    # -- people ------------------------------------------------------------ #

    MAX_EXEMPLARS = 32

    def persons(self) -> list[dict]:
        """[{'name', 'n', 'centroid', 'exemplars': [vectors]}] - exemplars are a person's saved diverse faces."""
        if not self._db:
            return []
        np = _np()
        with self._lock:
            rows = self._db.execute(
                "SELECT name, kind, centroid, n FROM persons ORDER BY name COLLATE NOCASE").fetchall()
            ex_rows = self._db.execute("SELECT name, emb FROM person_faces").fetchall()
        ex: dict[str, list] = {}
        for name, blob in ex_rows:
            ex.setdefault(name.lower(), []).append(np.frombuffer(blob, dtype=np.float32).copy())
        out = []
        for name, kind, blob, n in rows:
            vec = np.frombuffer(blob, dtype=np.float32).copy() if blob else None
            out.append({"name": name, "kind": kind, "n": n, "centroid": vec, "exemplars": ex.get(name.lower(), [])})
        return out

    @staticmethod
    def _diverse(vectors, cap, dup=0.93):
        """Greedy farthest-point pick so the kept faces cover different poses / looks."""
        np = _np()
        kept: list = []
        for v in vectors:
            if not kept or float(np.max(np.stack(kept) @ v)) < dup:
                kept.append(v)
        while len(kept) > cap:      # still too many: drop the most redundant one
            M = np.stack(kept) @ np.stack(kept).T
            np.fill_diagonal(M, -1)
            kept.pop(int(np.argmax(M.max(axis=1))))
        return kept

    def save_person(self, name: str, centroid, n: int, exemplars=None) -> None:
        """Create a person or merge a new sample into an existing name (weighted mean + diverse faces)."""
        name = name.strip()
        if not self._db or not name:
            return
        np = _np()
        c = np.asarray(centroid, dtype=np.float32)
        new_ex = [np.asarray(e, dtype=np.float32) for e in (exemplars or [])] or [c]
        with self._lock:
            row = self._db.execute("SELECT centroid, n FROM persons WHERE name=?", (name,)).fetchone()
            old_ex = [np.frombuffer(b, dtype=np.float32).copy() for (b,) in
                      self._db.execute("SELECT emb FROM person_faces WHERE name=?", (name,)).fetchall()]
            if row and row[0]:
                old = np.frombuffer(row[0], dtype=np.float32)
                merged = old * row[1] + c * n
                nrm = float(np.linalg.norm(merged)) or 1.0
                c, n = (merged / nrm).astype(np.float32), row[1] + n
                if not old_ex:
                    old_ex = [old.copy()]
            keep = self._diverse(old_ex + new_ex, self.MAX_EXEMPLARS)
            self._db.execute(
                "INSERT INTO persons(name, kind, centroid, n, updated) VALUES(?,?,?,?,?) "
                "ON CONFLICT(name) DO UPDATE SET centroid=excluded.centroid, n=excluded.n, updated=excluded.updated",
                (name, "person", c.tobytes(), int(n), time.time()))
            self._db.execute("DELETE FROM person_faces WHERE name=?", (name,))
            self._db.executemany("INSERT INTO person_faces VALUES (?,?,?)",
                                 [(name, e.astype(np.float32).tobytes(), time.time()) for e in keep])
            self._db.commit()

    @staticmethod
    def score(centre, exemplars, person) -> float:
        """Half centre-to-centre, half the two best exemplar matches (so another pose still matches)."""
        np = _np()
        pc = person["centroid"]
        s1 = float(np.asarray(centre) @ pc)
        if not exemplars:
            return s1
        PE = np.stack(person["exemplars"] or [pc])
        m = np.max(np.stack(exemplars) @ PE.T, axis=1)
        return 0.5 * (s1 + float(np.sort(m)[-2:].mean()))

    MATCH_MARGIN = 0.05     # the best name must beat the runner-up by this much, else it is ambiguous

    def match(self, centroid, threshold: float, exemplars=None) -> tuple[str | None, float]:
        """Best stored name for a person, or (None, best score). Conservative on purpose: a wrong
        automatic name is worse than no name, so it needs a high score AND a clear margin."""
        scored = sorted(((self.score(centroid, exemplars, p), p["name"]) for p in self.persons()
                         if p["centroid"] is not None), reverse=True)
        if not scored:
            return None, -1.0
        best, name = scored[0]
        runner = scored[1][0] if len(scored) > 1 else -1.0
        if best >= threshold and best - runner >= self.MATCH_MARGIN:
            return name, best
        return None, best

    def merge_names(self, src: str, dst: str) -> bool:
        """Fold one saved person into another (e.g. the same person saved under two names)."""
        if not self._db or src.lower() == dst.lower():
            return False
        ps = {p["name"].lower(): p for p in self.persons()}
        a, b = ps.get(src.lower()), ps.get(dst.lower())
        if a is None or b is None:
            return False
        self.save_person(dst, a["centroid"], a["n"], a["exemplars"])
        self.delete_person(src)
        with self._lock:
            self._db.execute("UPDATE tags SET name=? WHERE name=?", (dst, src))
            self._db.commit()
        return True

    def rename_person(self, old: str, new: str) -> bool:
        if not self._db or not new.strip():
            return False
        with self._lock:
            try:
                self._db.execute("UPDATE persons SET name=?, updated=? WHERE name=?", (new.strip(), time.time(), old))
                self._db.execute("UPDATE person_faces SET name=? WHERE name=?", (new.strip(), old))
                self._db.execute("UPDATE tags SET name=? WHERE name=?", (new.strip(), old))
                self._db.commit()
                return True
            except sqlite3.Error:
                return False

    def delete_person(self, name: str) -> None:
        if not self._db:
            return
        with self._lock:
            self._db.execute("DELETE FROM persons WHERE name=?", (name,))
            self._db.execute("DELETE FROM person_faces WHERE name=?", (name,))
            self._db.commit()

    # -- animal / photo tags ------------------------------------------------ #

    def set_tag(self, path: str, kind: str, idx: int, name: str, box=None) -> None:
        if not self._db:
            return
        with self._lock:
            if name.strip():
                self._db.execute(
                    "INSERT OR REPLACE INTO tags VALUES (?,?,?,?,?,?)",
                    (path, kind, int(idx), name.strip(), json.dumps(box) if box else None, time.time()))
            else:
                self._db.execute("DELETE FROM tags WHERE path=? AND kind=? AND idx=?", (path, kind, int(idx)))
            self._db.commit()

    def tag_map(self) -> dict[str, list[dict]]:
        """{path: [{'kind', 'idx', 'name'}]} for every tagged photo."""
        if not self._db:
            return {}
        with self._lock:
            rows = self._db.execute("SELECT path, kind, idx, name FROM tags").fetchall()
        out: dict[str, list[dict]] = {}
        for path, kind, idx, name in rows:
            out.setdefault(path, []).append({"kind": kind, "idx": idx, "name": name})
        return out

    def search(self, text: str) -> list[str]:
        """Photos tagged with a name containing ``text`` (animals; people are matched live)."""
        if not self._db or not text.strip():
            return []
        with self._lock:
            rows = self._db.execute("SELECT DISTINCT path FROM tags WHERE name LIKE ?",
                                    (f"%{text.strip()}%",)).fetchall()
        return [r[0] for r in rows]

    def all_names(self) -> list[str]:
        if not self._db:
            return []
        with self._lock:
            a = [r[0] for r in self._db.execute("SELECT name FROM persons")]
            b = [r[0] for r in self._db.execute("SELECT DISTINCT name FROM tags")]
        return sorted({*a, *b}, key=str.lower)

    # -- housekeeping ------------------------------------------------------- #

    def counts(self) -> dict:
        if not self._db:
            return {"persons": 0, "tags": 0}
        with self._lock:
            p = self._db.execute("SELECT COUNT(*) FROM persons").fetchone()[0]
            t = self._db.execute("SELECT COUNT(*) FROM tags").fetchone()[0]
        return {"persons": p, "tags": t}

    def clear(self, what: str) -> int:
        """what: 'people' | 'tags' | 'names' (both). Returns rows removed."""
        if not self._db:
            return 0
        n = 0
        with self._lock:
            if what in ("people", "names"):
                n += self._db.execute("DELETE FROM persons").rowcount
                self._db.execute("DELETE FROM person_faces")
            if what in ("tags", "names"):
                n += self._db.execute("DELETE FROM tags").rowcount
            self._db.commit()
        return n
