"""Headless self-test for EyeScan. Run: python selftest.py"""
from __future__ import annotations

import re
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'engine'))

import eyescan_core as core  # noqa: E402
from eyescan_core import (  # noqa: E402
    DEFAULT_DETECT_MAX_DIM,
    DEFAULT_MIN_FACE_AREA,
    EyeEngine,
    FaceResult,
    ImageResult,
    _area,
    _box_of,
    _iou,
    apply_threshold,
    iter_images,
    read_manifest,
    sort_results,
    undo_move,
    write_manifest,
)

PASS = 0
FAIL = 0


def check(name: str, cond: bool, extra: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  PASS  {name}")
    else:
        FAIL += 1
        print(f"  FAIL  {name}  {extra}")


# -- fakes that mimic MediaPipe's result objects ---------------------------- #

class _Cat:
    def __init__(self, name, score):
        self.category_name = name
        self.score = score


class _LM:
    def __init__(self, x, y):
        self.x = x
        self.y = y


class _Out:
    def __init__(self, faces, blinks):
        self.face_landmarks = faces
        self.face_blendshapes = blinks


def _face(x0=0.2, y0=0.2, x1=0.7, y1=0.8):
    return [_LM(x0, y0), _LM(x1, y1)]


def _blink(left, right):
    return [_Cat("eyeBlinkLeft", left), _Cat("eyeBlinkRight", right)]


class _Stub:
    def __init__(self, out):
        self._out = out

    def detect(self, _img):
        return self._out


def make_engine(out, threshold=0.5, require_both=False):
    eng = EyeEngine.__new__(EyeEngine)
    eng.threshold = threshold
    eng.require_both = require_both
    eng.min_face_area = DEFAULT_MIN_FACE_AREA
    eng.robust = False
    eng.deep = False
    eng.refine = False
    eng.rotate_fallback = False
    eng.detect_max_dim = DEFAULT_DETECT_MAX_DIM
    eng._detector = _Stub(out)
    eng._tile_detector = _Stub(out)
    return eng


def png(path, size=(200, 200)):
    from PIL import Image
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, (128, 128, 128)).save(path)


def full_landmarks(cx=0.40, cy=0.40, r=0.10):
    """A synthetic 478-point face mesh (iris included)."""
    import math
    pts = []
    for i in range(478):
        a = (i % 36) / 36.0 * 2 * math.pi
        pts.append(_LM(cx + r * math.cos(a), cy + r * math.sin(a)))
    return pts


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="eyescan_test_"))
    print(f"temp dir: {tmp}")
    img = tmp / "a.png"
    png(img)

    print("\n[1] classification")
    closed = make_engine(_Out([_face()], [_blink(0.92, 0.88)]))
    r = closed.analyze(img)
    check("both eyes clearly shut -> closed", r.verdict == "closed", r.verdict)
    check("max_blink reported", abs(r.max_blink - 0.90) < 0.01, str(r.max_blink))

    opened = make_engine(_Out([_face()], [_blink(0.10, 0.12)]))
    check("clearly open -> open", opened.analyze(img).verdict == "open")

    check("mean 0.51 >= 0.50 -> closed",
          make_engine(_Out([_face()], [_blink(0.40, 0.62)])).analyze(img).verdict == "closed")
    check("wink + require_both -> open",
          make_engine(_Out([_face()], [_blink(0.95, 0.20)]), require_both=True)
          .analyze(img).verdict == "open")
    check("wink, mean rule -> closed",
          make_engine(_Out([_face()], [_blink(0.95, 0.20)])).analyze(img).verdict == "closed")

    multi = make_engine(_Out([_face(0.05, 0.05, 0.35, 0.35),
                              _face(0.55, 0.55, 0.95, 0.95)],
                             [_blink(0.05, 0.05), _blink(0.8, 0.9)]))
    r = multi.analyze(img)
    check("one shut face in a group -> closed", r.verdict == "closed")
    check("closed-face count is 1", r.n_closed == 1, str(r.n_closed))

    print("\n[2] edge cases")
    check("no faces -> no-face", make_engine(_Out([], [])).analyze(img).verdict == "no-face")
    check("tiny face skipped -> no-face",
          make_engine(_Out([[_LM(0.01, 0.01), _LM(0.05, 0.05)]], [_blink(0.9, 0.9)]))
          .analyze(img).verdict == "no-face")
    bad = tmp / "note.txt"
    bad.write_text("hello")
    check("non-image skipped",
          make_engine(_Out([], [])).analyze(bad).verdict == "unsupported")
    check("missing file -> error",
          make_engine(_Out([], [])).analyze(tmp / "nope.png").verdict == "error")

    print("\n[3] re-classification from cached scores")
    res = make_engine(_Out([_face()], [_blink(0.62, 0.58)])).analyze(img)
    check("starts closed at 0.5", res.verdict == "closed")
    res.reclassify(0.70, False)
    check("reclassify to 0.70 -> open", res.verdict == "open")
    res.reclassify(0.59, True)   # min(0.62,0.58)=0.58 < 0.59 -> open
    check("require_both is stricter than the mean -> open", res.verdict == "open")
    errors = make_engine(_Out([], [])).analyze(tmp / "missing2.png")
    apply_threshold([errors], 0.1, False)
    check("error verdicts survive reclassify", errors.verdict == "error")

    print("\n[4] folder scanning")
    scan_dir = tmp / "photos"
    png(scan_dir / "one.png")
    png(scan_dir / "two.png")
    png(scan_dir / "sub" / "three.png")
    (scan_dir / "readme.txt").write_text("x")
    check("flat scan finds 2", len(list(iter_images(scan_dir, False))) == 2)
    check("recursive finds 3", len(list(iter_images(scan_dir, True))) == 3)

    print("\n[5] sort / move + undo")
    results = [
        ImageResult(path=scan_dir / "one.png", verdict="closed",
                    faces=[FaceResult(0.9, 0.9, True, (0, 0, 1, 1))]),
        ImageResult(path=scan_dir / "two.png", verdict="open"),
        ImageResult(path=scan_dir / "sub" / "three.png", verdict="closed",
                    faces=[FaceResult(0.9, 0.9, True, (0, 0, 1, 1))]),
    ]
    outcome = sort_results(results, scan_dir, "Closed Eyes", "move", recursive=True)
    check("2 files moved", outcome.moved == 2, str(outcome.moved))
    check("flagged one.png moved", (scan_dir / "Closed Eyes" / "one.png").exists())
    check("sub structure kept", (scan_dir / "Closed Eyes" / "sub" / "three.png").exists())
    check("open file untouched", (scan_dir / "two.png").exists())
    check("manifest written", outcome.manifest_path and outcome.manifest_path.exists())

    pairs = read_manifest(outcome.manifest_path)
    check("manifest lists 2 moves", len(pairs) == 2, str(len(pairs)))
    ok, fail = undo_move(outcome.manifest_path)
    check("undo restored both", ok == 2 and fail == 0, f"{ok}/{fail}")
    check("original one.png back", (scan_dir / "one.png").exists())
    check("original sub/three.png back", (scan_dir / "sub" / "three.png").exists())
    check("manifest removed after undo", not outcome.manifest_path.exists())

    copy_dir = tmp / "copy"
    png(copy_dir / "c.png")
    n = sort_results(
        [ImageResult(path=copy_dir / "c.png", verdict="closed")],
        copy_dir, "Out", "copy", recursive=False)
    check("copy keeps original", n.moved == 1 and (copy_dir / "c.png").exists()
          and (copy_dir / "Out" / "c.png").exists())

    print("\n[6] geometry / multi-tile merge")
    check("_box_of", _box_of([(0.1, 0.2), (0.5, 0.6)]) == (0.1, 0.2, 0.5, 0.6))
    check("_area", abs(_area((0, 0, 0.5, 0.4)) - 0.2) < 1e-9)
    check("_iou half overlap", abs(_iou((0, 0, 1, 1), (0, 0, 0.5, 0.5)) - 0.25) < 1e-9)
    check("_iou disjoint", _iou((0, 0, 0.2, 0.2), (0.5, 0.5, 0.9, 0.9)) == 0.0)

    big = ([(0.10, 0.10), (0.60, 0.60)], {"eyeBlinkLeft": 0.9})
    near = ([(0.11, 0.11), (0.61, 0.61)], {"eyeBlinkLeft": 0.2})   # same face
    other = ([(0.65, 0.65), (0.95, 0.95)], {"eyeBlinkLeft": 0.1})  # different
    merged = EyeEngine._dedupe([big, near, other])
    check("dedupe merges the same face found in two tiles", len(merged) == 2,
          str(len(merged)))
    check("dedupe keeps the larger detection",
          merged[0][1]["eyeBlinkLeft"] == 0.9)

    pts = [(0.5, 0.5)] * 478
    for i, x in zip((33, 133, 159, 145, 153, 144),
                    (0.30, 0.45, 0.34, 0.40, 0.32, 0.42)):
        pts[i] = (x, 0.30)
    for i, x in zip((362, 263, 386, 374, 380, 373),
                    (0.56, 0.70, 0.59, 0.65, 0.57, 0.67)):
        pts[i] = (x, 0.32)
    eb = EyeEngine._eye_box(pts)
    check("eye_box spans both eyes", bool(eb) and eb[0] < 0.30 and eb[2] > 0.70,
          str(eb))
    check("eye_box missing when landmarks are short",
          EyeEngine._eye_box([(0.5, 0.5)] * 10) is None)

    print("\n[7] overlay landmark data")
    eng = make_engine(_Out([full_landmarks()], [_blink(0.2, 0.2)]))
    face = eng.analyze(img).faces[0]
    check("face outline stored", len(face.outline) == 36, str(len(face.outline)))
    check("both eye loops stored", [len(e) for e in face.eyes] == [16, 16],
          str([len(e) for e in face.eyes]))
    check("iris centres stored", len(face.iris) == 2, str(len(face.iris)))

    print("\n[8] settings")
    from eyescan_core import Settings
    s = Settings()
    s.add_recent(str(tmp))
    s.add_recent(str(tmp))
    check("recent de-duplicated", s.get("recent").count(str(tmp)) == 1)

    print("\n[9] v1.4 accuracy / robustness")
    from eyescan_core import eye_aspect_ratios, _contained
    fr = FaceResult(0.55, 0.55, False, (0, 0, 1, 1), ear_left=0.32, ear_right=0.32)
    check("EAR veto: borderline blink but lids clearly apart -> open",
          not fr.is_closed(0.5, False))
    fr = FaceResult(0.90, 0.90, False, (0, 0, 1, 1), ear_left=0.32, ear_right=0.32)
    check("EAR never vetoes a strong blink", fr.is_closed(0.5, False))
    fr = FaceResult(0.40, 0.40, False, (0, 0, 1, 1), ear_left=0.05, ear_right=0.06)
    check("EAR rescue: near-miss blink with lids shut -> closed",
          fr.is_closed(0.5, False))
    fr = FaceResult(0.10, 0.10, False, (0, 0, 1, 1), ear_left=0.05, ear_right=0.05)
    check("EAR doesn't rescue a very low blink", not fr.is_closed(0.5, False))
    check("no EAR -> plain threshold",
          FaceResult(0.6, 0.6, False, (0, 0, 1, 1)).is_closed(0.5, False))
    check("EAR needs the full mesh",
          eye_aspect_ratios([(0.5, 0.5)] * 10, 100, 100) == (None, None))
    check("EAR degenerate eye width -> None",
          eye_aspect_ratios([(0.5, 0.5)] * 478, 100, 100)[0] is None)

    check("containment: nested box detected",
          _contained((0.3, 0.3, 0.4, 0.4), (0.2, 0.2, 0.8, 0.8)) > 0.99)
    kept = EyeEngine._dedupe([([(0.2, 0.2), (0.8, 0.8)], {}),
                              ([(0.3, 0.3), (0.4, 0.4)], {})])
    check("dedupe drops a face nested inside a bigger one", len(kept) == 1)

    # rotation fallback: the detector only "sees" a face on its 2nd call
    import numpy as np

    class _RotStub:
        def __init__(self):
            self.calls = 0

        def detect(self, _mpimg):
            self.calls += 1
            if self.calls < 2:
                return _Out([], [])
            return _Out([[_LM(0.2, 0.3), _LM(0.4, 0.5)]], [_blink(0.9, 0.9)])
    eng = make_engine(_Out([], []))
    eng.rotate_fallback = True
    eng._tile_detector = _RotStub()
    got = eng._detect_rotated(np.zeros((100, 200, 3), np.uint8))
    check("rotated fallback finds a face", len(got) == 1)
    pts = got[0][0] if got else [(0, 0)]
    # k=1 produced nothing; k=3 maps (x, y) -> (y, 1 - x)
    check("rotated landmarks mapped back",
          abs(pts[0][0] - 0.3) < 1e-9 and abs(pts[0][1] - 0.8) < 1e-9, str(pts))

    # BlazeFace validation: unconfirmed "faces" (feet, hands) are discarded
    class _Det:
        def __init__(self, score):
            self.categories = [type("C", (), {"score": score})()]

    class _Blaze:
        def __init__(self, score):
            self.score = score

        def detect(self, _img):
            return type("O", (), {"detections": [_Det(self.score)] if self.score else []})()
    big = np.full((300, 300, 3), 128, np.uint8)
    eng = make_engine(_Out([], []))
    eng._validator = _Blaze(0.0)
    check("validator rejects an unconfirmed face (the 'foot' case)",
          eng._validated(big, [([(0.2, 0.2), (0.8, 0.8)], {})]) == [])
    eng._validator = _Blaze(0.9)
    kept = eng._validated(big, [([(0.2, 0.2), (0.8, 0.8)], {})])
    check("validator keeps a confirmed face + records score",
          len(kept) == 1 and kept[0][1]["_face_score"] == 0.9)
    eng._validator = None
    check("no validator -> faces kept unchanged",
          len(eng._validated(big, [([(0.2, 0.2), (0.8, 0.8)], {})])) == 1)

    # manifest merge + undo
    mroot = tmp / "mm"
    (mroot / "src").mkdir(parents=True)
    a, b = mroot / "src" / "a.jpg", mroot / "src" / "b.jpg"
    a.write_bytes(b"x")
    b.write_bytes(b"y")
    out = mroot / "Closed"
    out.mkdir()
    ta, tb = out / "a.jpg", out / "b.jpg"
    shutil.move(str(a), str(ta))
    write_manifest(out, [(str(a), str(ta))])
    shutil.move(str(b), str(tb))
    mp = write_manifest(out, [(str(b), str(tb))])
    check("second manifest keeps the first move", len(read_manifest(mp)) == 2)
    ok, fail = undo_move(mp)
    check("both moves undone", ok == 2 and fail == 0 and a.exists() and b.exists())

    (mroot / "Closed" / "x.jpg").write_bytes(b"z")
    names = {p.name for p in iter_images(mroot, True, skip_dirs=("Closed",))}
    check("recursive scan skips output folder", names == {"a.jpg", "b.jpg"}, str(names))

    bad = mroot / "src" / "bad.png"
    bad.write_bytes(b"not an image")
    eng = make_engine(_Out([_face()], [_blink(0.9, 0.9)]))
    res = [r for _, r in eng.analyze_many([img, bad, img])]
    check("analyze_many keeps order + reports unreadable file",
          [r.verdict for r in res] == ["closed", "error", "closed"],
          str([r.verdict for r in res]))

    class _Stop:
        def is_set(self):
            return True
    check("analyze_many honours stop",
          list(eng.analyze_many([img, img], stop=_Stop())) == [])

    print("\n[10] v1.5 features")
    from eyescan_core import AnimalResult, Overrides, face_sharpness, write_csv

    def face(blink=0.7, ear=0.15, down=0.7, pitch=None, **kw):
        return FaceResult(blink, blink, False, (0.2, 0.2, 0.6, 0.6), ear_left=ear,
                          ear_right=ear, look_down=down, pitch=pitch, **kw)

    f = face()
    check("closed blink + lids apart + gaze down -> down",
          f.classify(0.5, False, True) == "down" and not f.closed)
    check("keep_down off -> flagged closed",
          face().classify(0.5, False, False) == "closed")
    check("lids shut (low EAR) stays closed even looking down",
          face(ear=0.05).classify(0.5, False, True) == "closed")
    check("head pitched down relaxes the lid gap needed",
          face(ear=0.12, pitch=20).classify(0.5, False, True) == "down")
    check("same gap without pitch -> closed",
          face(ear=0.12, pitch=2).classify(0.5, False, True) == "closed")
    check("no gaze-down signal -> closed",
          face(down=0.1).classify(0.5, False, True) == "closed")
    check("open eyes stay open", face(blink=0.1, ear=0.3, down=0.0)
          .classify(0.5, False, True) == "open")
    f = face(ear=0.03)
    f.override = "open"
    check("override open wins", f.classify(0.5, False, True) == "open")
    f.override = "ignore"
    check("override ignore -> ignored", f.classify(0.5, False, True) == "ignored")

    r = ImageResult(path=img, faces=[face(), face(blink=0.1, ear=0.3, down=0)])
    r.reclassify(0.5, False, True)
    check("verdict 'down' when only look-down faces", r.verdict == "down")
    check("down photo is not flagged", not r.is_flagged)
    r.reclassify(0.5, False, False)
    check("keep_down off -> verdict closed", r.verdict == "closed")
    r.override = "keep"
    r.reclassify(0.5, False, False)
    check("image override keep -> open", r.verdict == "open")
    r.override = "flag"
    r.reclassify(0.5, False, True)
    check("image override flag -> closed", r.verdict == "closed")
    r2 = ImageResult(path=img, faces=[face()])
    r2.faces[0].override = "ignore"
    r2.reclassify(0.5, False, True)
    check("all faces ignored -> no-face", r2.verdict == "no-face")

    g = ImageResult(path=img, n_people=1)
    check("group solo", g.group == "solo")
    g.n_people = 2
    check("group pair", g.group == "pair")
    g.n_people = 5
    check("group 3+", g.group == "group" and "group (5)" in g.tags())
    g.n_people, g.n_bodies = 1, 4
    check("bodies count toward people", g.people == 4)
    g.animals = [AnimalResult("dog", 0.9, (0, 0, 1, 1))]
    check("animal tag", "dog" in g.tags() and g.has_animal)
    check("no people -> group none", ImageResult(path=img).group == "none")

    # analyze() fills the new fields from blendshape scores
    big_png = tmp / "big.png"
    png(big_png, (400, 400))
    cats = _blink(0.8, 0.8) + [_Cat("eyeLookDownLeft", 0.7), _Cat("eyeLookDownRight", 0.7),
                               _Cat("mouthSmileLeft", 0.9), _Cat("mouthSmileRight", 0.9),
                               _Cat("jawOpen", 0.4)]
    eng = make_engine(_Out([full_landmarks()], [cats]))
    rr = eng.analyze(big_png)
    ff = rr.faces[0]
    check("analyze records gaze/smile/mouth",
          abs(ff.look_down - 0.7) < 1e-6 and ff.smiling and ff.mouth_is_open)
    check("analyze counts people; a tiny face is too small to judge for focus",
          rr.n_people == 1 and rr.sharpness is None and rr.focus == "n/a")

    # sharpness: noise is sharp, a blur is not
    from PIL import Image as _I, ImageFilter as _F
    rng = np.random.default_rng(0)
    sharp_img = _I.fromarray(rng.integers(0, 255, (400, 400, 3), dtype=np.uint8))
    blur_img = sharp_img.filter(_F.GaussianBlur(6))
    ss = face_sharpness(np.asarray(sharp_img), (0, 0, 1, 1))
    sb = face_sharpness(np.asarray(blur_img), (0, 0, 1, 1))
    check("sharpness separates sharp from blurred", ss > 20 * sb, f"{ss} vs {sb}")

    # animals from a stubbed object detector
    class _BB:
        origin_x, origin_y, width, height = 10, 20, 100, 80

    def _det(name, score):
        return type("D", (), {"categories": [type("C", (), {
            "category_name": name, "score": score})()], "bounding_box": _BB})()

    class _Obj:
        def detect(self, _i):
            return type("O", (), {"detections": [
                _det("dog", 0.8), _det("cat", 0.2), _det("person", 0.9),
                _det("person", 0.3), _det("chair", 0.99)]})()
    eng = make_engine(_Out([], []))
    eng._objdet = _Obj()
    an, bodies = eng._objects(np.zeros((200, 200, 3), np.uint8))
    check("animals: confident dog kept, weak cat / chair dropped",
          [a.label for a in an] == ["dog"] and an[0].box == (0.05, 0.1, 0.55, 0.5),
          str(an))
    check("bodies: only the confident person counted", bodies == 1)

    # overrides persist and re-apply
    ov = Overrides(tmp / "ov.json")
    ov.set_face(img, 0, "open")
    ov.set_image(img, "keep")
    ov.save()
    ov2 = Overrides(tmp / "ov.json")
    rx = ImageResult(path=img, faces=[face()])
    ov2.apply(rx)
    check("overrides round-trip", rx.override == "keep" and rx.faces[0].override == "open")
    ov2.set_face(img, 0, None)
    ov2.set_image(img, None)
    check("clearing overrides prunes the entry", ov2.data == {})

    # CSV
    csvp = tmp / "o.csv"
    write_csv(csvp, [rr, r])
    txt = csvp.read_text(encoding="utf-8-sig").splitlines()
    check("csv has header + a row per photo", len(txt) == 3 and txt[0].startswith("file,verdict"))

    # recycle bin (recoverable delete)
    victim = tmp / "delete_me.jpg"
    victim.write_bytes(b"x" * 64)
    done, failed = core.recycle([victim, tmp / "nope.jpg"])
    check("recycle sends a file to the Recycle Bin", done == 1 and not victim.exists())
    check("recycle reports a missing file as failed", len(failed) == 1)

    # contact sheets
    import eyescan_report as rep
    for ratio in ("3:2", "4:3", "16:9"):
        w_, h_ = rep.page_size(ratio, 6000)
        a_, b_ = rep.RATIOS[ratio]
        check(f"page {ratio} is exact and >= 4000px", max(w_, h_) >= 4000
              and abs(w_ / h_ - a_ / b_) < 0.002, f"{w_}x{h_}")
    check("long side below 4000 is raised to 4000", max(rep.page_size("3:2", 1200)) == 4000)
    shots = []
    for i in range(3):
        pth = tmp / f"s{i}.png"
        png(pth, (300, 450))
        shots.append(ImageResult(path=pth, verdict="closed", faces=[face(blink=0.6 + i / 10)]))
    shots[0].faces[0].classify(0.5, False, False)
    pages = rep.make_contact_sheets(shots, tmp / "sheets", title="t", only="all",
                                    mode="photos", long_side=4000)
    from PIL import Image as _PI
    im_ = _PI.open(pages[0])
    check("contact sheet written at 4000x2667+", im_.size[0] >= 4000 and len(pages) == 1,
          str(im_.size))
    pages = rep.make_contact_sheets(shots, tmp / "sheets", title="t", only="all",
                                    mode="faces", long_side=4000)
    check("faces-mode contact sheet written", len(pages) == 1 and pages[0].exists())
    check("nothing to show -> no pages",
          rep.make_contact_sheets([], tmp / "sheets2") == [])

    print("\n[11] v1.6: both eyes, focus, duplicates, protect, cache")
    from eyescan_core import (eye_closed, find_duplicates, image_hash, result_from_dict,
                              result_to_dict, scan_files)
    import eyescan_db

    # --- both eyes, each judged on its own (blink fused with the lid gap)
    check("eye: strong blink -> closed", eye_closed(0.8, 0.05, 0.5))
    check("eye: lids clearly apart vetoes a borderline blink", not eye_closed(0.55, 0.32, 0.5))
    check("eye: far eye of a turned head - blink 0.07 but lids shut (EAR 0.09) -> closed",
          eye_closed(0.07, 0.09, 0.5))
    check("eye: open eye stays open", not eye_closed(0.1, 0.30, 0.5))
    turned = FaceResult(0.62, 0.07, False, (0, 0, 1, 1), ear_left=0.23, ear_right=0.11)
    check("turned/laughing face: both eyes closed by blink+EAR -> closed",
          turned.classify(0.5, True, False) == "closed")
    wink = FaceResult(0.9, 0.05, False, (0, 0, 1, 1), ear_left=0.04, ear_right=0.30)
    check("one eye shut, other wide open -> a wink, never flagged",
          wink.classify(0.5, True, True) == "open" and wink.wink)
    rwink = ImageResult(path=img, faces=[wink])
    rwink.reclassify(0.5, True, True)
    check("wink photo is tagged 'wink', not closed",
          rwink.verdict == "open" and "wink" in rwink.tags())
    sure = FaceResult(0.9, 0.9, False, (0, 0, 1, 1), ear_left=0.03, ear_right=0.03)
    sure.classify(0.5, True, True)
    maybe = FaceResult(0.62, 0.07, False, (0, 0, 1, 1), ear_left=0.23, ear_right=0.11)
    maybe.classify(0.5, True, False)
    check("strong evidence on both eyes = sure; disagreement = needs a look",
          sure.sure and not maybe.sure)
    chk = ImageResult(path=img, faces=[maybe])
    chk.reclassify(0.5, True, False)
    check("needs_check for a borderline closed photo", chk.needs_check)
    check("engine requires both eyes by default", EyeEngine.require_both is True)

    # --- focus judged on the face only
    def withsharp(sh, bg):
        return ImageResult(path=img, sharpness=sh, bg_sharp=bg)
    check("focus: crisp face -> sharp", withsharp(18, 22).focus == "sharp")
    check("focus: soft face, soft background -> blurry", withsharp(3, 4).focus == "blurry")
    check("focus: soft face but crisp background -> misfocused", withsharp(3, 20).focus == "misfocused")
    check("focus: nothing judged when the face is too small", withsharp(None, 20).focus == "n/a")
    check("blurry / misfocused count as unclear", withsharp(3, 4).unclear and withsharp(3, 20).unclear
          and not withsharp(18, 22).unclear)
    sharp_img = _I.fromarray(rng.integers(0, 255, (300, 300, 3), dtype=np.uint8))
    ss2 = face_sharpness(np.asarray(sharp_img), (0, 0, 1, 1))
    sb2 = face_sharpness(np.asarray(sharp_img.filter(_F.GaussianBlur(5))), (0, 0, 1, 1))
    check("focus ratio: sharp >> blurred and the blurred one is below the cutoff",
          ss2 > 3 * sb2 and sb2 < core.BLUR_SHARP_MIN, f"{ss2:.1f} vs {sb2:.1f}")

    # --- duplicates
    def hres(name, h, verdict="open", sharp=10.0, size=1000, w=100, hh=150):
        r = ImageResult(path=tmp / name, verdict=verdict, phash=h, file_size=size,
                        width=w, height=hh, sharpness=sharp)
        return r
    base = "00" * 32
    one_bit = "01" + "00" * 31
    far = "ff" * 32
    a1, a2, a3, a4 = (hres("d1.jpg", base, "closed"), hres("d2.jpg", one_bit, "open", 9),
                      hres("d3.jpg", base, "open", 15), hres("d4.jpg", far))
    ngroups = find_duplicates([a1, a2, a3, a4], max_dist=10)
    check("duplicates: similar frames grouped, a different photo is not",
          ngroups == 1 and a1.dup_group == a2.dup_group == a3.dup_group and a4.dup_group is None)
    best = [x for x in (a1, a2, a3) if x.dup_best]
    check("duplicates: best frame = open eyes + sharpest", best == [a3])
    check("duplicates: extras flagged, identical copy marked exact",
          a1.is_duplicate and a2.is_duplicate and a3.dup_exact is False and a1.dup_dist == 0)
    wide = hres("d5.jpg", base, w=300, hh=100)
    check("duplicates: different aspect ratio never groups", find_duplicates([a1, wide], 10) == 0)
    a3.protected = True
    find_duplicates([a1, a2, a3], 10)
    check("duplicates: a protected frame is always the one kept", a3.dup_best)
    check("image_hash is stable and 256 bits",
          image_hash(np.asarray(sharp_img)) == image_hash(np.asarray(sharp_img))
          and len(image_hash(np.asarray(sharp_img))) == 64)

    # --- protect survives re-scans and is skipped by bulk sort helpers
    ovp = Overrides(tmp / "ovp.json")
    ovp.set_protect(img, True)
    ovp.save()
    rp = ImageResult(path=img)
    Overrides(tmp / "ovp.json").apply(rp)
    check("protect persists", rp.protected and "protected" in rp.tags())

    # --- (de)serialisation + SQLite cache
    src = ImageResult(path=img, verdict="closed", n_people=2, n_bodies=3, sharpness=12.5,
                      bg_sharp=14.0, file_size=1234, width=600, height=400, phash=base,
                      animals=[AnimalResult("dog", 0.9, (0.1, 0.1, 0.5, 0.5))],
                      faces=[FaceResult(0.8, 0.7, False, (0.1, 0.2, 0.4, 0.6),
                                        eye_box=(0.1, 0.2, 0.3, 0.3),
                                        outline=[(0.1, 0.2)] * 36, eyes=[[(0.2, 0.2)] * 16] * 2,
                                        iris=[(0.2, 0.2), (0.3, 0.2)], ear_left=0.05,
                                        ear_right=0.06, look_down=0.3, pitch=12.0,
                                        smile=0.4, sharp=11.0, squint=0.2)])
    back = result_from_dict(img, __import__("json").loads(__import__("json").dumps(
        result_to_dict(src))))
    check("result round-trips through JSON",
          back.n_people == 2 and back.animals[0].label == "dog" and back.width == 600
          and abs(back.faces[0].ear_left - 0.05) < 1e-9 and back.faces[0].pitch == 12.0
          and back.phash == base and len(back.faces[0].outline) == 36 and back.from_cache)
    cdb = eyescan_db.ResultCache(tmp / "t.db")
    check("cache opens", cdb.ok)
    cdb.put(img, 10, 5.0, "k1", src)
    hit = cdb.get(img, 10, 5.0, "k1")
    check("cache hit returns the stored measurements", hit is not None and hit.n_bodies == 3)
    check("cache miss when size, mtime or settings differ",
          cdb.get(img, 11, 5.0, "k1") is None and cdb.get(img, 10, 9.0, "k1") is None
          and cdb.get(img, 10, 5.0, "k2") is None)
    st = cdb.stats()
    check("cache stats", st["count"] == 1 and st["bytes"] > 0)

    # scan_files: first pass analyses + stores, second is served from the cache
    simg = [tmp / f"sc{i}.png" for i in range(3)]
    for p_ in simg:
        png(p_)
    eng = make_engine(_Out([_face()], [_blink(0.9, 0.9)]))
    calls = []
    orig = eng.analyze_many
    eng.analyze_many = lambda files, stop=None, **k: (calls.append(len(list(files))) or
                                                      orig(files, stop=stop))
    cdb2 = eyescan_db.ResultCache(tmp / "t2.db")
    first = list(scan_files(eng, simg, cdb2))
    second = list(scan_files(eng, simg, cdb2))
    check("scan_files: 1st pass analysed 3, none cached", len(first) == 3 and not any(c for *_, c in first))
    check("scan_files: 2nd pass served 3/3 from the cache, no analysis",
          all(c for *_, c in second) and calls == [3, 0], str(calls))
    forced = list(scan_files(eng, simg, cdb2, force=True))
    check("scan_files: force re-scans everything", not any(c for *_, c in forced))
    import os as _os
    _os.utime(simg[0], (1, 1))                     # modify one file's timestamp
    third = list(scan_files(eng, simg, cdb2))
    check("scan_files: a changed file is re-analysed, the rest reused",
          sorted(c for *_, c in third) == [False, True, True])
    simg[1].unlink()
    check("cache: prune removes entries for missing files", cdb2.prune_missing() == 1)
    n_forget = cdb2.forget_folder(tmp)
    check("cache: forget_folder drops entries under a folder", n_forget >= 1)
    cdb2.put(simg[0], 1, 1.0, "k", src)
    check("cache: clear empties it and shrinks the file", cdb2.clear() >= 1
          and cdb2.stats()["count"] == 0)
    cdb.close()
    cdb2.close()
    check("sizes: result keeps file size / dimensions from analyze",
          make_engine(_Out([_face()], [_blink(0.1, 0.1)])).analyze(big_png).file_size > 0)

    # --- false-face defences
    from eyescan_core import VALIDATE_CONF
    eng = make_engine(_Out([], []))
    eng._validator = _Blaze(0.70)
    check("validator: a 0.70 'face' (hand / belly / foot range) is rejected",
          eng._validated(big, [([(0.2, 0.2), (0.8, 0.8)], {})]) == [] and VALIDATE_CONF >= 0.7)
    eng._validator = _Blaze(0.75)
    check("validator: a 0.75 face is kept",
          len(eng._validated(big, [([(0.2, 0.2), (0.8, 0.8)], {})])) == 1)

    class _BB2:
        origin_x, origin_y, width, height = 0, 0, 200, 200      # the whole frame

    class _DogOnly:
        def detect(self, _i):
            c = type("C", (), {"category_name": "dog", "score": 0.9})()
            return type("O", (), {"detections": [type("D", (), {
                "categories": [c], "bounding_box": _BB2})()]})()
    eng = make_engine(_Out([_face(0.1, 0.1, 0.5, 0.5)], [_blink(0.9, 0.9)]))
    eng._objdet = _DogOnly()
    dres = eng.analyze(big_png)
    check("a 'face' inside a dog's box is the dog's face: dropped, dog kept",
          dres.n_people == 0 and dres.verdict == "no-face"
          and [a.label for a in dres.animals] == ["dog"])
    eng2 = make_engine(_Out([_face(0.1, 0.1, 0.5, 0.5)], [_blink(0.9, 0.9)]))
    check("without an animal the same face is kept", eng2.analyze(big_png).n_people == 1)
    check("looking-down lid gap is tunable (module constant)",
          core.EAR_DOWN == 0.13 and core.EAR_DOWN_PITCHED == 0.11)

    # --- credits / version / RAW companions
    check("engine version matches APP_VERSION",
          core.__version__ == re.search(r'^APP_VERSION = "([^"]+)"',
                                        (Path(__file__).resolve().parent.parent / "bridge.py").read_text("utf-8"), re.M).group(1))
    check("credits carry author, brand, site, year, rights",
          "Devendra Kulkarni" in core.CREDIT_LINE and "DEeV.in" in core.CREDIT_LINE
          and "www.deev.in" in core.CREDIT_LINE and "2026" in core.CREDIT_LINE
          and core.RIGHTS == "All rights reserved." and core.WEBSITE.startswith("https://"))
    rdir = Path(tempfile.mkdtemp(prefix="eyescan_raw_"))
    for n in ("A.JPG", "A.CR2", "a.dng", "A.cr3", "B.JPG", "A_other.CR2", "A.txt"):
        (rdir / n).write_bytes(b"x")
    got = sorted(p.name.lower() for p in core.raw_companions(rdir / "A.JPG"))
    check("raw_companions finds same-name CR2/DNG/CR3 only", got == ["a.cr2", "a.cr3", "a.dng"], str(got))
    check("raw_companions: none for a photo without RAW", core.raw_companions(rdir / "B.JPG") == [])
    check("raw_companions: a RAW file has no companions", core.raw_companions(rdir / "A.CR2") == [])
    shutil.rmtree(rdir, ignore_errors=True)

    # --- parallel scanning + GPU (DirectML) layer
    import os

    import eyescan_gpu as gpu

    _cores = os.cpu_count
    try:
        os.cpu_count = lambda: 8
        check("auto_workers: small batch stays single-process",
              core.auto_workers(core.POOL_MIN_PHOTOS - 1) == 1)
        check("auto_workers: big batch on 8 cores uses 4 (capped, one core spare)",
              core.auto_workers(500) == 4)
        os.cpu_count = lambda: 2
        check("auto_workers: 2-core PC never forks", core.auto_workers(500) == 1)
        os.cpu_count = lambda: 4
        check("auto_workers: 4 cores -> 3 workers", core.auto_workers(500) == 3)
    finally:
        os.cpu_count = _cores
    check("settings: GPU is automatic by default",
          core.DEFAULT_SETTINGS["gpu_device"] == "auto" and core.DEFAULT_SETTINGS["workers"] == 0)

    def ad(i, name, vendor, mb):
        discrete = mb >= gpu.DISCRETE_MIN_MB
        return {"index": i, "name": name, "vendor": vendor, "vram_mb": mb, "discrete": discrete,
                "rank": (0 if discrete else 10) + gpu.VENDOR_ORDER.get(vendor, 3)}

    ADS = [ad(0, "Intel HD", "Intel", 112), ad(1, "NVIDIA X", "NVIDIA", 4000),
           ad(2, "AMD Y", "AMD", 8000)]

    def bn(cpu=10.0, **ms):
        return {"directml": True, "adapters": ADS, "ms": {"cpu": cpu, **ms}}

    check("auto: NVIDIA outranks AMD and Intel when all are faster than the CPU",
          gpu.select(bn(**{"0": 2.0, "1": 4.0, "2": 3.0})) == 1)
    check("auto: AMD is used when the NVIDIA adapter failed",
          gpu.select(bn(**{"0": 6.0, "2": 4.0})) == 2)
    check("auto: a GPU no faster than the CPU is skipped for the next one",
          gpu.select(bn(**{"1": 9.9, "2": 4.0})) == 2)
    check("auto: an iGPU slower than the CPU is never used (hybrid laptop)",
          gpu.select(bn(**{"0": 12.0})) is None)
    check("auto: an iGPU that is clearly faster is used when it is all there is",
          gpu.select(bn(**{"0": 5.0})) == 0)
    check("cpu mode never uses a GPU", gpu.select(bn(**{"1": 1.0}), gpu.CPU) is None)
    check("named GPU is honoured even if the CPU is faster (explicit choice)",
          gpu.select(bn(**{"0": 12.0, "1": 3.0}), "Intel HD") == 0)
    check("a named GPU that is gone falls back to automatic",
          gpu.select(bn(**{"1": 3.0}), "Removed GPU") == 1)
    check("a named GPU that failed the benchmark falls back to automatic",
          gpu.select(bn(**{"1": 3.0}), "AMD Y") == 1)
    check("no DirectML -> CPU", gpu.select(dict(bn(**{"1": 1.0}), directml=False)) is None)

    opts = gpu.device_options(ADS)
    check("device options: automatic first, GPUs by priority, CPU last",
          [m for _l, m in opts] == ["auto", "NVIDIA X", "AMD Y", "Intel HD", "cpu"]
          and "discrete" in opts[1][0] and "integrated" in opts[3][0])
    check("describe(): names the adapter in use",
          "NVIDIA X" in gpu.describe(dict(bn(**{"1": 2.0}), onnxruntime="x", device=1, mode="auto")))
    check("describe(): says when CPU-only was chosen",
          "CPU only" in gpu.describe(dict(bn(**{"1": 2.0}), onnxruntime="x", device=None, mode="cpu")))
    check("describe(): explains when no GPU beats the CPU",
          "no GPU is faster" in gpu.describe(dict(bn(**{"0": 12.0}), onnxruntime="x",
                                                  device=None, mode="auto")))

    # the cached benchmark is trusted only while adapters + runtime are unchanged
    _ads, _bm = gpu.adapters, gpu.benchmark
    try:
        ort_ver = getattr(gpu._ort(), "__version__", None)
        gpu.adapters = lambda: ADS
        calls: list[int] = []
        gpu.benchmark = lambda: calls.append(1) or dict(bn(**{"1": 3.0}), onnxruntime=ort_ver)
        saved = dict(bn(**{"1": 2.0}), onnxruntime=ort_ver)
        gpu.set_bench(None)
        check("cached benchmark is reused (no re-measure)",
              gpu.bench(saved)["ms"]["1"] == 2.0 and not calls)
        gpu.set_bench(None)
        gpu.adapters = lambda: ADS[:2]                      # a GPU was removed
        check("cached benchmark for different hardware is discarded and re-measured",
              gpu.bench(saved)["ms"]["1"] == 3.0 and len(calls) == 1)
        gpu.set_bench(None)
        gpu.adapters = lambda: ADS
        gpu.set_mode("cpu")
        check("choice() reflects the selected mode",
              gpu.choice(saved)["device"] is None and gpu.current_device() is None)
        gpu.set_mode("auto")
        check("choice() auto picks the priority GPU", gpu.choice(saved)["device"] == 1)
    finally:
        gpu.adapters, gpu.benchmark = _ads, _bm
        gpu.set_mode(gpu.AUTO)
        gpu.set_bench(None)

    check("adapters(): entries have the fields the UI needs",
          all({"index", "name", "vendor", "discrete", "rank"} <= set(a) for a in gpu.adapters()))

    if gpu._ort() is not None:
        import numpy as np

        probe = gpu.build_probe_model()
        m_cpu = gpu.OnnxModel(probe, prefer_gpu=False)
        xin = np.random.default_rng(3).standard_normal((8, 3, 96, 96)).astype(np.float32)
        y_cpu = m_cpu.run({"x": xin})[0]
        check("generated ONNX probe model loads and runs on the CPU",
              m_cpu.provider == "CPU" and y_cpu.shape == (8, 64, 1, 1))
        gpu.set_mode(gpu.CPU)
        check("CPU-only mode -> OnnxModel uses the CPU", gpu.OnnxModel(probe).provider == "CPU")
        gpu.set_mode(gpu.AUTO)

        # a GPU that fails at run time must fall back to the CPU and retry
        class _Boom:
            def run(self, *_a):
                raise RuntimeError("device removed")

        m_gpu = gpu.OnnxModel(probe, prefer_gpu=False)
        m_gpu.device, m_gpu.session = 0, _Boom()
        check("GPU failure while running -> same call succeeds on the CPU",
              m_gpu.run({"x": xin})[0].shape == (8, 64, 1, 1)
              and m_gpu.provider == "CPU" and m_gpu.fell_back)

    # ------------------------------------------------------------------ #
    #  Gaze / hidden eyes / cut-off faces / best of burst / blink rescue / people
    # ------------------------------------------------------------------ #
    print("\n[looking away, hidden eyes, framing, burst, rescue, people]")
    import numpy as np
    import eyescan_people as people

    def F(**kw):
        base = dict(blink_left=0.05, blink_right=0.05, closed=False,
                    box=(0.3, 0.2, 0.5, 0.45), ear_left=0.30, ear_right=0.30,
                    sharp=20.0)
        base.update(kw)
        f = FaceResult(**base)
        f.classify(0.5)
        return f

    g_h, g_v = core.eye_gaze({"eyeLookOutLeft": 0.9, "eyeLookInRight": 0.9,
                              "eyeLookInLeft": 0.0, "eyeLookOutRight": 0.0,
                              "eyeLookUpLeft": 0.6, "eyeLookUpRight": 0.6})
    check("eye_gaze: positive = subject's left, up = +", g_h > 0.8 and g_v > 0.5, f"{g_h} {g_v}")
    check("at the lens: not looking away", not F(yaw=0.0, gaze_h=0.30).looks_away)
    check("head turned but eyes back at the lens: NOT looking away",
          not F(yaw=-27.0, gaze_h=-0.96).looks_away)
    check("eyes swung sideways: looking away", F(yaw=-10.0, gaze_h=0.96).looks_away)
    check("head turned far away: looking away", F(yaw=45.0, gaze_h=0.30).looks_away)
    check("closed eyes are never 'looking away'",
          not F(yaw=45.0, blink_left=0.9, blink_right=0.9, ear_left=0.05,
                ear_right=0.05).looks_away)
    check("whole face inside the frame: not cut off", not F(box=(0.2, 0.2, 0.5, 0.5)).is_cut_off)
    check("face running off the left edge: cut off", F(box=(-0.06, 0.2, 0.2, 0.5)).is_cut_off)
    check("face touching the top edge: cut off", F(box=(0.3, 0.0, 0.5, 0.25)).is_cut_off)
    check("cut_off is a 0..1 share", 0.0 < F(box=(-0.06, 0.2, 0.2, 0.5)).cut_off <= 1.0)

    # eye occlusion on a synthetic face: skin, open-looking eyes vs sunglasses
    pts = [(0.5, 0.5)] * 478
    pts = list(pts)

    def put(idx, x, y):
        for i in idx:
            pts[i] = (x, y)

    def loop(idx, cx, cy, hw=0.05, hh=0.02):
        for k, i in enumerate(idx):
            a = 6.2832 * k / len(idx)
            pts[i] = (cx + hw * np.cos(a), cy + hh * np.sin(a))

    loop(core.EYE_LEFT_LOOP, 0.35, 0.4)
    loop(core.EYE_RIGHT_LOOP, 0.65, 0.4)
    put((50, 205), 0.30, 0.62)
    put((280, 425), 0.70, 0.62)
    put((234,), 0.10, 0.5)
    put((454,), 0.90, 0.5)
    S = 400

    def scene(sunglasses: bool):
        img = np.full((S, S, 3), (205, 160, 135), np.uint8)          # skin
        for cx in (0.35, 0.65):
            x, y = int(cx * S), int(0.4 * S)
            if sunglasses:
                img[y - 30:y + 30, x - 45:x + 45] = (25, 25, 28)
            else:
                img[y - 6:y + 6, x - 14:x + 14] = (235, 235, 235)     # sclera
                img[y - 7:y + 7, x - 5:x + 5] = (40, 25, 20)          # iris
        return img

    ol, orr = core.eye_occlusion(scene(False), pts, S, S)
    check("visible eyes: only a minority of the eye window is dark",
          ol is not None and ol < 0.45 and orr < 0.45, f"{ol} {orr}")
    ol, orr = core.eye_occlusion(scene(True), pts, S, S)
    check("sunglasses: the eye window is (almost) all dark", ol > 0.85 and orr > 0.85,
          f"{ol} {orr}")
    hid = F(occ_left=0.95, occ_right=0.9, blink_left=0.9, blink_right=0.9,
            ear_left=0.1, ear_right=0.1)
    check("covered eyes are 'hidden', not flagged closed", hid.state == "hidden" and not hid.closed)
    check("one covered eye is not enough to hide the face",
          F(occ_left=0.95, occ_right=0.2).state == "open")
    check("a manual 'closed' override beats the hidden check",
          (lambda f: (setattr(f, "override", "closed"), f.classify(0.5), f.state)[2])(
              F(occ_left=0.95, occ_right=0.9)) == "closed")
    r_h = ImageResult(Path("h.jpg"), faces=[hid])
    r_h.reclassify(0.5)
    check("only hidden faces -> verdict 'hidden' (can't judge)", r_h.verdict == "hidden")
    r_h2 = ImageResult(Path("h2.jpg"), faces=[F(occ_left=0.95, occ_right=0.9), F()])
    r_h2.reclassify(0.5)
    check("hidden + a visible open face -> open, tagged 'eyes hidden'",
          r_h2.verdict == "open" and "eyes hidden" in r_h2.tags())
    r_h3 = ImageResult(Path("h3.jpg"), faces=[F(occ_left=0.95, occ_right=0.9),
                                              F(blink_left=0.9, blink_right=0.9,
                                                ear_left=0.05, ear_right=0.05)])
    r_h3.reclassify(0.5)
    check("hidden + a closed face -> still flagged closed", r_h3.verdict == "closed")

    # cache round-trip keeps the new measurements
    fr = F(gaze_h=0.7, gaze_v=-0.2, occ_left=0.3, occ_right=0.35)
    v = np.random.RandomState(1).randn(128).astype(np.float32)
    fr.emb = v / np.linalg.norm(v)
    back = core.result_from_dict("x.jpg", core.result_to_dict(
        ImageResult(Path("x.jpg"), faces=[fr]))).faces[0]
    check("cache keeps gaze + occlusion", abs(back.gaze_h - 0.7) < 1e-3
          and abs(back.occ_left - 0.3) < 1e-3)
    check("cache keeps the face-recognition vector (int8 packed)",
          back.emb is not None and float(back.emb @ fr.emb) > 0.995)

    # best of burst + blink rescue
    def frame(name, faces, ph="ab" * 32, sharp=20.0):
        r = ImageResult(Path(name), faces=faces, width=6000, height=4000,
                        file_size=5_000_000, phash=ph, sharpness=sharp)
        r.reclassify(0.5)
        return r

    def closed_face(box):
        return F(box=box, blink_left=0.9, blink_right=0.9, ear_left=0.05, ear_right=0.05)

    bx1, bx2 = (0.1, 0.2, 0.3, 0.45), (0.55, 0.2, 0.75, 0.45)
    a = frame("a.jpg", [F(box=bx1), closed_face(bx2)])
    b = frame("b.jpg", [F(box=bx1, smile=0.8), F(box=bx2, smile=0.8)])
    c = frame("c.jpg", [F(box=bx1), F(box=bx2, blink_left=0.9, ear_left=0.05)])   # a wink
    d = frame("d.jpg", [F(box=bx1, yaw=-40.0), F(box=bx2)])                      # looks away
    grp = [a, b, c, d]
    n = core.find_duplicates(grp, 30)
    check("burst: one group found", n == 1)
    check("burst: all eyes open + smiling frame is the winner", b.dup_best and not a.dup_best)
    check("burst: score ranks winner > away > wink > closed",
          b.dup_score > d.dup_score > a.dup_score and b.dup_score > c.dup_score)
    check("burst: the winner says why", "all eyes open" in b.dup_why and "smiling" in b.dup_why,
          b.dup_why)
    check("burst: a loser says why", a.dup_why == "eyes closed", a.dup_why)
    check("rescue: the closed frame points at the frame with that person's eyes open",
          a.rescue is b and a.rescue_for == 1, f"{a.rescue}")
    check("rescue: nothing suggested for a photo that is fine", b.rescue is None)
    check("rescue: a better-frame tag appears", "better frame" in a.tags())
    lone = frame("lone.jpg", [closed_face(bx1)], ph="cd" * 32)
    core.find_duplicates([lone, a, b], 30)
    check("rescue: none when the photo has no sibling frames", lone.rescue is None)
    a2 = frame("a2.jpg", [closed_face(bx1), F(box=bx2)])
    b2 = frame("b2.jpg", [F(box=bx1), closed_face(bx2)])
    core.find_duplicates([a2, b2], 30)
    check("rescue: swapping one blink for another is not an improvement",
          a2.rescue is None and b2.rescue is None)

    # people: alignment, packing, clustering, matching
    pts5 = [(0.5, 0.5)] * 478
    pts5 = list(pts5)
    for idx, xy in ((33, (0.30, 0.40)), (133, (0.40, 0.40)), (263, (0.70, 0.40)),
                    (362, (0.60, 0.40)), (1, (0.5, 0.55)), (61, (0.40, 0.70)),
                    (291, (0.60, 0.70))):
        pts5[idx] = xy
    img = (np.random.RandomState(0).rand(300, 300, 3) * 255).astype(np.uint8)
    crop = people.align_crop(img, pts5, 0.0)
    check("align_crop: 112x112 RGB", crop is not None and len(crop) == 112 * 112 * 3)
    check("align_crop: profile faces are skipped", people.align_crop(img, pts5, 70.0) is None)
    check("pack/unpack keeps direction", float(people.unpack(people.pack(fr.emb)) @ fr.emb) > 0.995)

    rs = np.random.RandomState(7)
    cents = [rs.randn(128) for _ in range(3)]

    def noisy(k):
        v = cents[k] + rs.randn(128) * 0.25
        return (v / np.linalg.norm(v)).astype(np.float32)

    res_p = []
    for k in (0, 1, 2, 0, 1, 0, 2, 0):
        fc = F(box=(0.2, 0.2, 0.4, 0.4))
        fc.emb = noisy(k)
        res_p.append(ImageResult(Path(f"p{len(res_p)}.jpg"), faces=[fc]))
    pc = people.cluster_people(res_p)
    labs = [r.faces[0].person for r in res_p]
    check("people: three identities -> three clusters", len(pc) == 3, f"{len(pc)}")
    check("people: same identity shares a number", labs[0] == labs[3] == labs[5] == labs[7]
          and labs[1] == labs[4] and labs[2] == labs[6])
    check("people: the most photographed person is #1", labs[0] == 1 and pc[0]["photos"] == 4)
    pr = frame("pr.jpg", [F(box=bx1), F(box=bx2)])
    pr2 = frame("pr2.jpg", [F(box=(0.57, 0.22, 0.77, 0.47)), F(box=(0.12, 0.2, 0.32, 0.45))])
    check("match_faces: falls back to position when there is no embedding",
          people.match_faces(pr, pr2) == {0: 1, 1: 0})
    pr.faces[0].emb, pr2.faces[1].emb = noisy(0), noisy(0)
    pr.faces[1].emb, pr2.faces[0].emb = noisy(1), noisy(1)
    check("match_faces: uses who the person is when it can",
          people.match_faces(pr, pr2) == {0: 1, 1: 0})

    mpath = core.find_model(people.EMBED_FILENAME, people.EMBED_MIN_BYTES)
    if mpath is not None:
        e = people.FaceEmbedder(prefer_gpu=False)
        v1 = e.embed(crop)
        v2 = e.embed(crop)
        check("face model: 128-d unit vector, stable", v1 is not None and v1.shape == (128,)
              and abs(float(v1 @ v1) - 1) < 1e-3 and float(v1 @ v2) > 0.999)
        check("face model: bad crop size -> None", e.embed(b"abc") is None)
        check("face model: provider reported", e.provider in ("CPU", "DirectML"))
    check("engine cache key tracks face recognition",
          "|e1" in core.EyeEngine.cache_key(type("E", (), dict(
              robust=True, deep=False, detect_animals=False, _objdet=None,
              _validator=None, min_face_area=0.002, detect_max_dim=1536,
              embed_faces=True))()))

    # small faces found through the person box
    class _Stub(EyeEngine):
        def __init__(self):
            pass
    st = _Stub()
    st._body_boxes = []
    check("small-face rescue: no person boxes -> nothing changes",
          st._rescue_small_faces(np.zeros((100, 100, 3), np.uint8), [("f", {})]) == [("f", {})])
    calls = []
    st._body_boxes = [(0.3, 0.2, 0.6, 0.9)]
    st._raw_detect = lambda reg, ox, oy, W, H, detector=None: calls.append((ox, oy, reg.shape)) or []
    st._tile_detector = None
    st._rescue_small_faces(np.zeros((1000, 800, 3), np.uint8), [])
    check("small-face rescue: searches the TOP of a person box that has no face",
          len(calls) == 1 and calls[0][1] < 0.2 * 1000 and calls[0][2][0] < 0.5 * 1000, str(calls))

    shutil.rmtree(tmp, ignore_errors=True)
    print(f"\n{PASS} passed, {FAIL} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
