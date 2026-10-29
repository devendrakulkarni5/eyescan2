"""Open folders keep scanning in the background; a saved folder opens at once while another scan holds the detector.
Generates its own throw-away photos and an isolated data folder - no arguments needed."""
import os
import sys
import tempfile
import threading
import time

os.environ["LOCALAPPDATA"] = tempfile.mkdtemp(prefix="eyescan_test_sess_")      # never the real database
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))


def photos(n, tag):
    import random
    from PIL import Image
    d = tempfile.mkdtemp(prefix=f"eyescan_sess_{tag}_")
    rnd = random.Random(tag)
    for i in range(n):
        Image.frombytes("RGB", (160, 120), bytes(rnd.randrange(256) for _ in range(160 * 120 * 3))).save(os.path.join(d, f"IMG_{i}.jpg"))
    return d


def wait(cond, secs=120):
    t0 = time.time()
    while not cond():
        assert time.time() - t0 < secs, "timed out"
        time.sleep(0.1)


def main():
    import bridge
    assert "eyescan_test_" in str(bridge.db_file())
    b = bridge.Bridge()
    ev = []
    b._emit = lambda k, **p: ev.append((k, p))
    kinds = lambda folder, kind: [p for k, p in ev if k == kind and p.get("folder") == folder]
    A, C = photos(12, "a"), photos(5, "c")

    # 1. a normal scan, then close it (engine now exists, results are saved)
    assert b.start_scan(A, {"force": False, "robust": False})["ok"]
    wait(lambda: kinds(A, "idle"))
    assert len(b._results) == 12 and kinds(A, "final")
    assert b.close_folder()["ok"] and not b._sessions

    # 2. another folder holds the detector: a saved folder still opens at once
    b._scan_lock.acquire()
    ev.clear()
    assert b.start_scan(A, {})["ok"]
    wait(lambda: kinds(A, "idle"))
    assert len(b._results) == 12, len(b._results)
    assert kinds(A, "final"), "saved folder should finish without the detector"

    # 3. a folder with new photos waits its turn in the background...
    assert b.start_scan(C, {})["ok"]
    sc = b._sessions[bridge._session_key(C)]
    time.sleep(1.0)
    assert sc.scanning and not sc.results and "Waiting" in sc.phase, (sc.scanning, len(sc.results), sc.phase)

    # 4. ...while the user switches back to A (replayed from memory, not rescanned)
    ev.clear()
    assert b.start_scan(A, {})["ok"]
    wait(lambda: kinds(A, "final"))
    assert kinds(A, "start") and sum(len(p["items"]) for p in kinds(A, "results")) == 12
    assert b._active is b._sessions[bridge._session_key(A)] and not kinds(A, "results")[0]["items"] == []

    # 5. the detector frees up: C finishes in the background, A stays on screen
    b._scan_lock.release()
    wait(lambda: not sc.scanning)
    assert len(sc.results) == 5 and kinds(C, "final")
    assert len(b._results) == 12, "the visible folder must not change"

    # 6. opening C replays its finished results; a stopped scan is resumed rather than replayed
    ev.clear()
    assert b.start_scan(C, {})["ok"]
    wait(lambda: kinds(C, "idle"))
    assert sum(len(p["items"]) for p in kinds(C, "results")) == 5 and len(b._results) == 5
    b.leave_folder()
    assert b._active is None and len(b._sessions) == 2             # leaving the folder keeps its session
    assert b.drop_session(C)["ok"] and bridge._session_key(C) not in b._sessions
    lib = {os.path.normcase(r["folder"]): r for r in b.library()}
    assert lib[os.path.normcase(A)]["total"] == 12
    print("sessions_test: all passed")


main()
