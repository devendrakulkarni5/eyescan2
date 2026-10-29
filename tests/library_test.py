"""Library of scanned folders: summary rows, offline detection, remove, db_clear. Usage: library_test.py (no photos needed)."""
import os
import sys
import tempfile

# ISOLATED: a throw-away data folder, so the real database / settings are never touched
os.environ["LOCALAPPDATA"] = os.path.join(tempfile.mkdtemp(prefix="eyescan_test_lib_"))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))


def main():
    import bridge
    import keeper_library
    assert "eyescan_test_" in str(bridge.db_file()), "test must use the temp database"
    b = bridge.Bridge()
    here = tempfile.mkdtemp(prefix="eyescan_lib_photos_")
    gone = os.path.join(here, "does-not-exist")

    lib = keeper_library.Library(bridge.db_file())
    lib.note(here, 120, 7, [os.path.join(here, "1.jpg")])
    lib.note(gone, 5, 0, [])
    lib.note(here, 130, 8, [os.path.join(here, "1.jpg")])          # same folder again: replaced, not duplicated
    lib.close()

    b._settings.data["recent"] = [here, r"Z:\old\recent-only"]     # a folder scanned before the library existed
    rows = b.library()
    by = {os.path.normcase(r["folder"]): r for r in rows}
    assert len(rows) == 3, rows
    r = by[os.path.normcase(here)]
    assert (r["total"], r["flagged"], r["exists"]) == (130, 8, True), r
    assert by[os.path.normcase(gone)]["exists"] is False
    old = by[os.path.normcase(r"Z:\old\recent-only")]
    assert old["total"] is None and old["exists"] is False
    assert os.path.join(here, "1.jpg") in b._lib_paths            # the image server may serve the card thumbnail

    assert b.folder_ok(here) and not b.folder_ok(gone)
    b.save_tabs([here, gone])
    assert b.get_settings()["tabs"] == [here, gone]

    assert b.library_remove(gone)["ok"]
    assert os.path.normcase(gone) not in {os.path.normcase(x["folder"]) for x in b.library()}

    b.db_clear("all")
    assert [x["folder"] for x in b.library()] == [here] or all(x["total"] is None for x in b.library()), b.library()
    print("library_test: all passed")


main()
