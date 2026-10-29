"""EyeScan core - detection engine, folder scanning, sorting, settings.

No UI code lives here, so the same core can back the Tkinter GUI, the CLI,
or a future HTML build.

Detection strategy (v1.2):
  MediaPipe's FaceLandmarker resizes the *whole* image down to a small model
  input, so a face that occupies only a small fraction of a large photo can
  fall below what it can see. To fix that we:
    1. run the detector on a downscaled copy of the image (fast),
    2. also run it on overlapping tiles (2x2, optionally 3x3) so each face is
       larger relative to the detector input,
    3. merge the results and drop duplicates (IoU),
    4. re-run the detector on a padded, full-resolution crop of each face to get
       accurate landmarks / blendshapes (blink scores).
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

# --------------------------------------------------------------------------- #
#  Constants
# --------------------------------------------------------------------------- #

IMAGE_EXTS = {
    ".jpg", ".jpeg", ".jpe", ".png", ".bmp", ".webp",
    ".tif", ".tiff", ".gif", ".heic", ".heif",
    # camera RAW (decoded by rawpy/LibRaw): Canon, Nikon, Sony, Adobe DNG and the rest
    ".cr2", ".cr3", ".crw", ".nef", ".nrw", ".arw", ".srf", ".sr2", ".dng",
    ".raf", ".orf", ".rw2", ".pef", ".srw", ".3fr", ".erf", ".kdc", ".mrw",
    ".raw", ".x3f",
}

MODEL_FILENAME = "face_landmarker.task"
MODEL_URL = (
    "https://storage.googleapis.com/mediapipe-models/face_landmarker/"
    "face_landmarker/float16/1/face_landmarker.task"
)

__version__ = "2.3.0"

# Credits / copyright - single source of truth (About box, status bar, CLI,
# installer and exe properties all read from here or mirror it).
APP_NAME = "Keeper"
AUTHOR = "Devendra Kulkarni"
BRAND = "DEeV.in"
WEBSITE = "https://www.deev.in"
COPYRIGHT_YEAR = 2026
COPYRIGHT = f"© {COPYRIGHT_YEAR} {AUTHOR} - {BRAND} (www.deev.in)"
RIGHTS = "All rights reserved."
CREDIT_LINE = f"{COPYRIGHT}. {RIGHTS}"

# Camera RAW formats that may sit next to a JPG as a companion file.
RAW_EXTS = (".cr2", ".cr3", ".crw", ".dng", ".nef", ".nrw", ".arw", ".srf", ".sr2",
            ".raf", ".orf", ".rw2", ".pef", ".srw", ".3fr", ".erf", ".kdc", ".mrw",
            ".raw", ".x3f")
SCHEMA_VERSION = 11   # bump when cached measurements change shape

BLAZE_FILENAME = "blaze_face_short_range.tflite"
BLAZE_URL = (
    "https://storage.googleapis.com/mediapipe-models/face_detector/"
    "blaze_face_short_range/float16/1/blaze_face_short_range.tflite"
)
ANIMAL_FILENAME = "efficientdet_lite0.tflite"
ANIMAL_URL = (
    "https://storage.googleapis.com/mediapipe-models/object_detector/"
    "efficientdet_lite0/float16/1/efficientdet_lite0.tflite"
)
ANIMAL_LABELS = ("bird", "cat", "dog", "horse", "sheep", "cow", "elephant",
                 "bear", "zebra", "giraffe")
ANIMAL_CONF = 0.45              # score needed to report an animal
ANIMAL_CONF_WITH_PEOPLE = 0.70  # stricter when a person is in frame (bags/clothes read as 'dog')
PERSON_CONF = 0.50              # score needed to count a body as a person

# "Looking down" (kept, not flagged): the blink score says closed, but the lids
# are still visibly apart, the gaze blendshape points down and (optionally) the
# head is pitched down. Calibrated on a real shoot where lowered-but-shut lids
# have EAR < ~0.09 and real downward glances sit at 0.11 - 0.20.
LOOK_DOWN_MIN = 0.45            # mean eyeLookDown blendshape
EAR_DOWN = 0.13                 # lids at least this far apart ... (user-tunable)
EAR_DOWN_PITCHED = 0.11         # ... or this far when the head is tipped down
# (0.11 labelled laughing / squinting faces as "looking down": on 146 real picks it
#  gave 43 'down' vs 23 at 0.13 and 13 at 0.15; 0.13 is the balanced default)
PITCH_DOWN = 15.0               # degrees of downward head pitch

# Per-photo focus, judged on the *face* only.
FACE_MIN_PX = 80                # faces smaller than this (px) aren't judged for focus
BLUR_SHARP_MIN = 7.0            # face sharpness ratio below this = unclear face
MISFOCUS_RATIO = 1.8            # unclear face + background this many x crisper = misfocus
MISFOCUS_BG_MIN = 10.0          # ...and the background must itself be crisp
SHARP_SIGMA = 1.2               # blur used for the fine/coarse ratio

# Duplicates: dHash Hamming distance (of 256 bits) at each sensitivity.
DUP_LEVELS = {"strict": 18, "normal": 30, "loose": 42}

SMILE_MIN = 0.50                # mean mouthSmile blendshape
MOUTH_OPEN_MIN = 0.25           # jawOpen blendshape
TEETH_OPEN_MIN = 0.12           # smiling with the jaw at least this open = teeth showing
FUNNY_MIN = 0.50                # tongueOut / cheekPuff / mouthPucker blendshape = funny face
BINDI_MIN = 0.0010              # red forehead blob, share of face-width^2 (a bindi is ~0.002-0.005)
BINDI_SAT = 0.68                # ...saturation and red dominance, as in the portrait prototype
BINDI_RED = 40

VALIDATE_CONF = 0.72            # BlazeFace score needed to confirm a face
# Calibrated on 487 detections viewed by eye: hands, belly, a foot, a wood carving
# and a wall score 0.53-0.71; 99.8% of real faces score >= 0.72 (median 0.92).
ANIMAL_FACE_OVERLAP = 0.60      # a "face" this far inside an animal box is its face
VALIDATE_PAD = 0.40             # crop padding around the face for validation

# --- eyes that cannot be judged / gaze / framing (heuristics, tunable) ---------- #
HIDDEN_DARK = 0.72              # fraction of the eye window that is dark = covered
HIDDEN_RATIO = 0.55             # a pixel is "dark" below this x the cheek brightness
# Main subjects only: closed eyes on people behind / out of focus are tagged, not flagged.
MAIN_ONLY = True                # user-tunable (Settings)
BG_REL_AREA = 0.35              # face smaller than this share of the biggest face = background
BG_REL_AREA_SOFT = 0.75         # ...or smaller than this AND clearly softer than the sharpest face
BG_REL_SHARP = 0.50
BG_MIN_PX = 48                  # face narrower than this (px) beside a bigger face: eyes unreadable
# Glasses: the frame leaves strong edges in a ring around each eye; with glasses on, "closed"
# must be confirmed by the pixels (no dark iris disc between brighter sclera / skin).
GLASSES_MIN = 0.10              # share of the ring that is a strong edge, both eyes
GLASSES_EDGE = 0.22             # gradient / cheek brightness that counts as a strong edge
IRIS_VISIBLE = 0.30             # iris-vs-surround contrast at/above this = an open eye
IRIS_UNSURE = 0.15              # between this and IRIS_VISIBLE the pixels can't settle it
AWAY_SCORE = 26.0              # degrees off-camera (head turn + eye gaze) = looking away
GAZE_DEG = 34.0                 # eye-in-head angle at a full eyeLook blendshape
GAZE_NEUTRAL = 0.30             # eyeLook offset the model reports for "at the lens"
UP_DEG = 20.0                   # ... vertical
CUT_MARGIN = 0.004              # face this close to the frame edge counts as touching it
CUT_MIN = 0.08                  # ...and this much of its extent must be outside/touching

DEFAULT_THRESHOLD = 0.50
DEFAULT_MIN_FACE_AREA = 0.002   # ignore faces smaller than 0.2% of the image

DEFAULT_DETECT_MAX_DIM = 1536   # detection runs on a copy at most this big
REFINE_MAX_DIM = 3072           # decode size; face crops are refined from this
TILE_OVERLAP = 0.15             # overlap between detection tiles
DEDUPE_IOU = 0.35               # two detections are the same face above this
DEDUPE_CONTAIN = 0.80           # ...or when this much of the smaller is inside
MIN_TILE_DIM = 640             # don't bother tiling small images

MANIFEST_NAME = "_eyescan_manifest.json"

# Eye-aspect-ratio (EAR) cross-check. The blink blendshape is the primary
# signal; EAR (lid opening / eye width, measured on the mesh) is an independent
# geometric signal used only to settle borderline cases: it vetoes a weak
# "closed" when the lids are clearly apart (e.g. looking down, which inflates
# the blendshape) and rescues a near-miss when the lids are clearly shut.
EAR_OPEN = 0.26           # mean EAR above this = lids clearly apart
EAR_SHUT = 0.12           # mean EAR below this = lids clearly together
EAR_VETO_MARGIN = 0.20    # only veto when blink < threshold + this
EAR_RESCUE_MARGIN = 0.15  # only rescue when blink >= threshold - this

# (corner, corner, [(upper, lower), ...]) per eye, MediaPipe mesh indices.
EAR_EYES = (
    (33, 133, ((160, 144), (159, 145), (158, 153))),
    (362, 263, ((385, 380), (386, 374), (387, 373))),
)

VERDICTS = ("closed", "down", "open", "hidden", "no-face", "error", "unsupported")

VERDICT_LABEL = {
    "closed": "EYES CLOSED",
    "down": "looking down",
    "open": "eyes open",
    "hidden": "can't judge",
    "no-face": "no face",
    "error": "error",
    "unsupported": "skipped",
}

# MediaPipe face-mesh indices for the eye region (both eyes).
EYE_LANDMARKS = (33, 133, 159, 145, 153, 144, 362, 263, 386, 374, 380, 373)

# Index loops used to draw the detection overlay.
FACE_OVAL_LOOP = (
    10, 338, 297, 332, 284, 251, 389, 356, 454, 323, 361, 288,
    397, 365, 379, 378, 400, 377, 152, 148, 176, 149, 150, 136,
    172, 58, 132, 93, 234, 127, 162, 21, 54, 103, 67, 109,
)
EYE_LEFT_LOOP = (33, 7, 163, 144, 145, 153, 154, 155, 133, 173,
                 157, 158, 159, 160, 161, 246)
EYE_RIGHT_LOOP = (263, 249, 390, 373, 374, 380, 381, 382, 362, 398,
                  384, 385, 386, 387, 388, 466)
# Detailed face regions (EyeScan 2), from the 478 FaceLandmarker points. Cheeks verified on real
# photos; cheek_b mirrors cheek_a. Forehead / full oval are extended along the head's "up" axis
# (see docs/PORTRAIT_EDITING_PLAN.md: FaceMesh's oval stops part-way up the forehead).
REGION_IDX = {
    "jaw": (234, 93, 132, 58, 172, 136, 150, 149, 176, 148, 152, 377, 400, 378, 379, 365,
            397, 288, 361, 323, 454),
    "chin": (149, 176, 148, 152, 377, 400, 378, 431, 18, 211),
    "cheek_a": (116, 117, 118, 119, 120, 142, 203, 206, 216, 207, 187, 147, 123),
    "cheek_b": (345, 346, 347, 348, 349, 371, 423, 426, 436, 427, 411, 376, 352),
    "brow_a": (46, 53, 52, 65, 55, 107, 66, 105, 63, 70),
    "brow_b": (276, 283, 282, 295, 285, 336, 296, 334, 293, 300),
    "nose_bridge": (168, 6, 197, 195, 5, 4, 1),
    "nose_base": (129, 64, 98, 97, 2, 326, 327, 294, 358),
    "lips": (61, 146, 91, 181, 84, 17, 314, 405, 321, 375, 291, 409, 270, 269, 267, 0, 37, 39, 40, 185),
}
FOREHEAD_TOP = (21, 54, 103, 67, 109, 10, 338, 297, 332, 284, 251)
FOREHEAD_BROW = (276, 283, 282, 295, 285, 55, 65, 52, 53, 46)
IRIS_RIGHT_IDX = (468, 469, 470, 471, 472)
IRIS_LEFT_IDX = (473, 474, 475, 476, 477)


def face_regions(pts, W: int, H: int) -> dict:
    """Named polygons / polylines (normalized 0..1) for the face outline, forehead, jaw, chin,
    cheeks, brows, nose and lips. Empty when the landmark set is incomplete."""
    from math import hypot
    if len(pts) < 468:
        return {}
    px = lambda i: (pts[i][0] * W, pts[i][1] * H)   # noqa: E731
    top, chin = px(10), px(152)
    fh = hypot(top[0] - chin[0], top[1] - chin[1]) or 1.0
    ux, uy = (top[0] - chin[0]) / fh, (top[1] - chin[1]) / fh      # head "up"
    oval = [px(i) for i in FACE_OVAL_LOOP]
    cx = sum(p[0] for p in oval) / len(oval)
    cy = sum(p[1] for p in oval) / len(oval)
    full = []
    for (x, y) in oval:
        dx, dy = x - cx, y - cy
        n = hypot(dx, dy) or 1.0
        w = max(0.0, (dx * ux + dy * uy) / n)
        ext = fh * 0.16 * w * w               # forehead: grows most at the crown, fades at the temples
        full.append((x + ux * ext, y + uy * ext))
    ext_of = {i: full[k] for k, i in enumerate(FACE_OVAL_LOOP)}
    norm = lambda seq: [(x / W, y / H) for x, y in seq]               # noqa: E731
    out = {"oval_full": norm(full)}
    out["forehead"] = norm([ext_of[i] for i in FOREHEAD_TOP] + [px(i) for i in FOREHEAD_BROW])
    for name, idx in REGION_IDX.items():
        out[name] = _pick(pts, idx)
    return {k: [tuple(p) for p in v] for k, v in out.items()}


def _head_pose(matrix) -> dict[str, float]:
    """Head pitch / yaw / roll (degrees) from MediaPipe's 4x4 face matrix.

    Sign convention (checked on real photos): pitch > 0 = head tipped *down* (verified),
    yaw / roll signs are unverified - use magnitudes only.
    """
    import numpy as np
    from math import asin, atan2, degrees

    m = np.asarray(matrix, dtype=float).reshape(4, 4)[:3, :3]
    scale = np.linalg.norm(m, axis=0)
    if np.any(scale < 1e-9):
        return {}
    r = m / scale
    pitch = degrees(asin(max(-1.0, min(1.0, r[2, 1]))))
    yaw = degrees(atan2(r[2, 0], r[2, 2]))
    roll = degrees(atan2(r[0, 1], r[1, 1]))
    return {"_pitch": pitch, "_yaw": yaw, "_roll": roll}


def _pick(points, idx):
    return [points[i] for i in idx if 0 <= i < len(points)]


# --------------------------------------------------------------------------- #
#  Geometry helpers
# --------------------------------------------------------------------------- #

def _box_of(points) -> tuple[float, float, float, float]:
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    return (min(xs), min(ys), max(xs), max(ys))


def _area(box: tuple[float, float, float, float]) -> float:
    return max(0.0, box[2] - box[0]) * max(0.0, box[3] - box[1])


def _lap_var(g, mask=None):
    lap = (4 * g[1:-1, 1:-1] - g[:-2, 1:-1] - g[2:, 1:-1]
           - g[1:-1, :-2] - g[1:-1, 2:])
    if mask is not None:
        m = mask[1:-1, 1:-1]
        if int(m.sum()) < 200:
            return None
        lap = lap[m]
    return float(lap.var())


def _sharpness_ratio(gray_img, mask=None) -> float | None:
    """Fine-to-coarse detail ratio: texture-independent focus measure.

    Laplacian variance of the region divided by that of a slightly blurred copy.
    A sharp region loses most of its fine detail when blurred (ratio 10-30); a
    region that is already out of focus barely changes (ratio 1-6). Unlike a raw
    sharpness score this does not depend on how textured the subject is, so smooth
    skin and a patterned saree are directly comparable.
    """
    import numpy as np
    from PIL import ImageFilter

    g = np.asarray(gray_img, dtype=np.float32)
    gb = np.asarray(gray_img.filter(ImageFilter.GaussianBlur(SHARP_SIGMA)),
                    dtype=np.float32)
    if g.shape[0] < 5 or g.shape[1] < 5:
        return None
    a, b = _lap_var(g, mask), _lap_var(gb, mask)
    if a is None or b is None:
        return None
    return float((a + 1e-3) / (b + 1e-3))


def face_sharpness(rgb, box) -> float | None:
    """Focus ratio of a face crop (higher = sharper; see ``_sharpness_ratio``).

    ``None`` when the face is too small to judge (< FACE_MIN_PX).
    """
    from PIL import Image

    h, w = rgb.shape[:2]
    x0, y0 = max(0, int(box[0] * w)), max(0, int(box[1] * h))
    x1, y1 = min(w, int(box[2] * w)), min(h, int(box[3] * h))
    if x1 - x0 < FACE_MIN_PX or y1 - y0 < FACE_MIN_PX:
        return None
    crop = Image.fromarray(rgb[y0:y1, x0:x1]).convert("L")
    crop.thumbnail((256, 256), Image.LANCZOS)      # big faces compare with small
    return _sharpness_ratio(crop)


EAR_FUSE_FULL = 0.25    # EAR at/above this contributes no "closed" evidence


def eye_closed(blink: float, ear: float | None, threshold: float) -> bool:
    """One eye: blink score fused with the lid gap (EAR).

    The blink blendshape is unreliable on the far eye of a turned head (it can
    read 0.1 while the lids are visibly shut) and inflated when looking down;
    the lid gap is geometry. Evidence = max(blink, 1 - EAR/0.25), with a veto
    when the lids are clearly apart. Calibrated on ~430 real photos: catches
    turned / laughing faces the blendshape alone misses, with no extra false
    flags on open-eyed photos.
    """
    if ear is None:
        return blink >= threshold
    if ear > EAR_OPEN and blink < threshold + EAR_VETO_MARGIN:
        return False                 # lids clearly apart: blink score is inflated
    s_ear = max(0.0, min(1.0, 1.0 - ear / EAR_FUSE_FULL))
    return max(blink, s_ear) >= threshold


def image_hash(rgb) -> str:
    """256-bit difference hash (hex) used to find near-duplicate photos."""
    import numpy as np
    from PIL import Image

    im = Image.fromarray(rgb).convert("L").resize((17, 16), Image.BOX
                                                  if min(rgb.shape[:2]) > 34
                                                  else Image.BILINEAR)
    a = np.asarray(im, dtype=np.int16)
    return np.packbits((a[:, 1:] > a[:, :-1]).flatten()).tobytes().hex()


def background_sharpness(rgb, boxes) -> float | None:
    """Focus ratio of everything *outside* the faces (see ``_sharpness_ratio``)."""
    import numpy as np
    from PIL import Image

    im = Image.fromarray(rgb).convert("L")
    im.thumbnail((1024, 1024), Image.LANCZOS)
    w, h = im.size
    if h < 8 or w < 8:
        return None
    mask = np.ones((h, w), dtype=bool)
    for b in boxes:
        bw, bh = b[2] - b[0], b[3] - b[1]
        x0, x1 = int(max(0, b[0] - bw * .25) * w), int(min(1, b[2] + bw * .25) * w)
        y0, y1 = int(max(0, b[1] - bh * .25) * h), int(min(1, b[3] + bh * .25) * h)
        mask[y0:y1, x0:x1] = False
    return _sharpness_ratio(im, mask)


def core_area(box) -> float:
    return _area(box)


def _contained_in(inner, outer) -> float:
    """Fraction of ``inner`` that lies inside ``outer``."""
    ix = max(0.0, min(inner[2], outer[2]) - max(inner[0], outer[0]))
    iy = max(0.0, min(inner[3], outer[3]) - max(inner[1], outer[1]))
    a = _area(inner)
    return (ix * iy) / a if a > 0 else 0.0


def _contained(a, b) -> float:
    """Fraction of the smaller box that lies inside the other."""
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    small = min(_area(a), _area(b))
    return (ix * iy) / small if small > 0 else 0.0


def eye_aspect_ratios(points, w: int, h: int):
    """(left, right) eye-aspect-ratios from mesh points, or (None, None).

    ``points`` are normalized; ``w``/``h`` restore the pixel aspect ratio.
    """
    from math import hypot

    if not points or len(points) <= 387 or w <= 0 or h <= 0:
        return None, None
    out = []
    for c0, c1, pairs in EAR_EYES:
        width = hypot((points[c0][0] - points[c1][0]) * w,
                      (points[c0][1] - points[c1][1]) * h)
        if width <= 1e-6:
            out.append(None)
            continue
        vert = sum(hypot((points[u][0] - points[lo][0]) * w,
                         (points[u][1] - points[lo][1]) * h)
                   for u, lo in pairs) / len(pairs)
        out.append(vert / width)
    return out[0], out[1]


_CHEEKS = (50, 280, 205, 425)         # mesh points on the cheeks (skin reference)


def _luma(a):
    return a[..., 0] * 0.299 + a[..., 1] * 0.587 + a[..., 2] * 0.114


def eye_occlusion(rgb, points, w: int, h: int):
    """(left, right) share of each eye window that is dark, or (None, None).

    A visible eye window holds skin, white, lashes and an iris - a minority of it is
    dark. Sunglasses, a fringe or a hand over the eye make (nearly) all of it dark /
    featureless. Judged against the cheek brightness so exposure doesn't matter.
    """
    import numpy as np

    if not points or len(points) <= 425 or w <= 0 or h <= 0:
        return None, None

    def window(idx, grow=0.45):
        xs = [points[i][0] * w for i in idx]
        ys = [points[i][1] * h for i in idx]
        cx, cy = (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2
        half = max(max(xs) - min(xs), 4.0) * (0.5 + grow)
        return (int(max(0, cx - half)), int(max(0, cy - half * 0.8)),
                int(min(w, cx + half)), int(min(h, cy + half * 0.8)))

    def patch(i, r):
        x, y = int(points[i][0] * w), int(points[i][1] * h)
        r = max(int(r), 2)
        return rgb[max(0, y - r):min(h, y + r), max(0, x - r):min(w, x + r)]

    face_w = abs(points[454][0] - points[234][0]) * w if len(points) > 454 else 0
    skin = [_luma(patch(i, face_w * 0.04).astype(np.float32)) for i in _CHEEKS]
    skin = [s for s in skin if s.size]
    if not skin:
        return None, None
    ref = float(np.median(np.concatenate([s.ravel() for s in skin])))
    if ref < 25:                       # the cheeks themselves are in deep shadow
        return None, None
    out = []
    for loop in (EYE_LEFT_LOOP, EYE_RIGHT_LOOP):
        x0, y0, x1, y1 = window(loop)
        if x1 - x0 < 4 or y1 - y0 < 3:
            out.append(None)
            continue
        lum = _luma(rgb[y0:y1, x0:x1].astype(np.float32))
        out.append(float((lum < ref * HIDDEN_RATIO).mean()))
    return out[0], out[1]


def _cheek_ref(rgb, points, w: int, h: int):
    """Median cheek brightness (the skin reference), or None when unusable."""
    import numpy as np

    if not points or len(points) <= 454 or w <= 0 or h <= 0:
        return None
    face_w = abs(points[454][0] - points[234][0]) * w
    r = max(int(face_w * 0.04), 2)
    vals = []
    for i in _CHEEKS:
        x, y = int(points[i][0] * w), int(points[i][1] * h)
        p = rgb[max(0, y - r):min(h, y + r), max(0, x - r):min(w, x + r)]
        if p.size:
            vals.append(_luma(p.astype(np.float32)).ravel())
    if not vals:
        return None
    ref = float(np.median(np.concatenate(vals)))
    return ref if ref >= 25 else None


def eye_glasses_iris(rgb, points, w: int, h: int):
    """((iris_left, iris_right), glasses): pixel evidence for spectacles / open eyes.

    ``iris_*`` = contrast of the dark iris disc against the sclera / skin beside it, in
    cheek-brightness units: ~0.5+ on an open eye, ~0 on a shut lid (a lash or liner line
    doesn't move the median). ``glasses`` = the weaker eye's share of strong edges in a
    ring round the eye (a frame rim); 0 on bare skin. Any part is ``None`` if unmeasurable.
    """
    import numpy as np

    none = ((None, None), None)
    ref = _cheek_ref(rgb, points, w, h)
    if ref is None or len(points) <= 477:
        return none
    iris, rims = [], []
    for loop, idx in ((EYE_LEFT_LOOP, IRIS_RIGHT_IDX), (EYE_RIGHT_LOOP, IRIS_LEFT_IDX)):
        xs = [points[i][0] * w for i in loop]
        ys = [points[i][1] * h for i in loop]
        ex0, ex1, ey0, ey1 = min(xs), max(xs), min(ys), max(ys)
        ew = max(ex1 - ex0, 4.0)
        # --- iris contrast
        cx, cy = points[idx[0]][0] * w, points[idx[0]][1] * h
        rad = float(np.mean([np.hypot(points[i][0] * w - cx, points[i][1] * h - cy)
                             for i in idx[1:]]))
        val = None
        if rad >= 2.0:
            x0, x1 = int(max(0, cx - 2.2 * rad)), int(min(w, cx + 2.2 * rad))
            y0, y1 = int(max(0, cy - 1.0 * rad)), int(min(h, cy + 1.0 * rad))
            if x1 - x0 >= 6 and y1 - y0 >= 3:
                lum = _luma(rgb[y0:y1, x0:x1].astype(np.float32))
                yy, xx = np.mgrid[y0:y1, x0:x1]
                dx, dy = np.abs(xx - cx), np.abs(yy - cy)
                disc = lum[(dx * dx + dy * dy) <= (0.6 * rad) ** 2]
                side = lum[(dx >= 1.3 * rad) & (dy <= 0.5 * rad)]
                if disc.size and side.size:
                    val = float((side.mean() - np.median(disc)) / ref)
        iris.append(val)
        # --- frame rim: strong edges in a ring just outside the lids
        gx0, gx1 = int(max(0, ex0 - 0.5 * ew)), int(min(w, ex1 + 0.5 * ew))
        gy0, gy1 = int(max(0, ey0 - 0.45 * ew)), int(min(h, ey1 + 0.45 * ew))
        if gx1 - gx0 >= 8 and gy1 - gy0 >= 6:
            g = _luma(rgb[gy0:gy1, gx0:gx1].astype(np.float32))
            mag = np.hypot(np.diff(g, axis=1)[:-1, :], np.diff(g, axis=0)[:, :-1]) / ref
            yy, xx = np.mgrid[gy0:gy1 - 1, gx0:gx1 - 1]
            ring = ~((xx >= ex0 - 0.08 * ew) & (xx <= ex1 + 0.08 * ew)
                     & (yy >= ey0 - 0.08 * ew) & (yy <= ey1 + 0.08 * ew))
            if ring.sum() >= 20:
                rims.append(float((mag[ring] >= GLASSES_EDGE).mean()))
    glasses = min(rims) if len(rims) == 2 else None
    return (iris[0], iris[1]), glasses


def bindi_score(rgb, points, w: int, h: int):
    """Size of the biggest saturated red blob on the forehead midline, in face-width^2.

    The zone runs from the point between the brows (9) up to the hairline (10), so it
    follows head roll. Red / magenta only (sat > BINDI_SAT, R > G + BINDI_RED): pimples
    are less saturated, hair and brows are dark. ``None`` when unmeasurable.
    """
    import numpy as np

    if not points or len(points) <= 454 or w <= 0 or h <= 0:
        return None
    face_w = abs(points[454][0] - points[234][0]) * w
    if face_w < 40:
        return None
    a = np.array([points[9][0] * w, points[9][1] * h])
    b = np.array([points[10][0] * w, points[10][1] * h])
    rad = 0.07 * face_w
    x0, x1 = int(max(0, min(a[0], b[0]) - rad)), int(min(w, max(a[0], b[0]) + rad))
    y0, y1 = int(max(0, min(a[1], b[1]) - rad)), int(min(h, max(a[1], b[1]) + rad))
    if x1 - x0 < 4 or y1 - y0 < 4:
        return None
    yy, xx = np.mgrid[y0:y1, x0:x1]
    ab = b - a
    t = np.clip(((xx - a[0]) * ab[0] + (yy - a[1]) * ab[1]) / max(float(ab @ ab), 1e-6), 0.0, 1.0)
    near = np.hypot(xx - (a[0] + t * ab[0]), yy - (a[1] + t * ab[1])) <= rad
    px = rgb[y0:y1, x0:x1].astype(np.int16)
    r, g, bl = px[..., 0], px[..., 1], px[..., 2]
    mx = px.max(axis=2).astype(np.float32)
    sat = (mx - px.min(axis=2)) / np.maximum(mx, 1.0)
    hit = (near & (sat > BINDI_SAT) & (r > g + BINDI_RED) & (r >= bl)).astype(np.uint8)
    if not hit.any():
        return 0.0
    import cv2
    n, _, stats, _ = cv2.connectedComponentsWithStats(hit, connectivity=8)
    return float(stats[1:, cv2.CC_STAT_AREA].max()) / (face_w * face_w)


def eye_gaze(scores) -> tuple[float, float]:
    """(horizontal, vertical) gaze from the eyeLook blendshapes, each -1..1.

    Horizontal is positive when the subject looks to *their* left. Vertical is positive
    when looking up.
    """
    def g(n):
        return float(scores.get(n, 0.0))

    left = (g("eyeLookOutLeft") + g("eyeLookInRight")) / 2.0
    right = (g("eyeLookInLeft") + g("eyeLookOutRight")) / 2.0
    up = (g("eyeLookUpLeft") + g("eyeLookUpRight")) / 2.0
    down = (g("eyeLookDownLeft") + g("eyeLookDownRight")) / 2.0
    return left - right, up - down



def _iou(a, b) -> float:
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    union = _area(a) + _area(b) - inter
    return inter / union if union > 0 else 0.0


# --------------------------------------------------------------------------- #
#  Paths / model
# --------------------------------------------------------------------------- #

def app_dir() -> Path:
    """Directory the app lives in (works frozen by PyInstaller too)."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def bundled_dir() -> Path:
    """Read-only resources extracted by PyInstaller, else the app dir."""
    base = getattr(sys, "_MEIPASS", None)
    return Path(base) if base else app_dir()


def user_data_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA") or str(Path.home())
    d = Path(base) / "EyeScan"
    d.mkdir(parents=True, exist_ok=True)
    return d


def find_model(name: str = MODEL_FILENAME, min_size: int = 100_000) -> Path | None:
    for c in (
        bundled_dir() / "model" / name,
        app_dir() / "model" / name,
        app_dir() / name,
        user_data_dir() / name,
    ):
        if c.is_file() and c.stat().st_size > min_size:
            return c
    return None


def ensure_model(log=print, name: str = MODEL_FILENAME, url: str = MODEL_URL,
                 min_size: int = 100_000) -> Path:
    """Return the model path, downloading it on first run if necessary."""
    found = find_model(name, min_size)
    if found:
        return found
    dest = user_data_dir() / name
    log(f"Model not found - downloading to {dest} ...")
    import urllib.request
    tmp = dest.with_suffix(".part")
    try:
        with urllib.request.urlopen(url, timeout=30) as resp, \
                open(tmp, "wb") as fh:
            shutil.copyfileobj(resp, fh)
        if tmp.stat().st_size < min_size:
            raise OSError("downloaded model is too small / corrupt")
        tmp.replace(dest)
    finally:
        tmp.unlink(missing_ok=True)
    log(f"Model downloaded ({dest.stat().st_size // 1024} KB).")
    return dest


# Optional HEIC/HEIF support (iPhone photos).
try:  # pragma: no cover - optional dependency
    import pillow_heif  # type: ignore

    pillow_heif.register_heif_opener()
    _HEIF_OK = True
except Exception:  # noqa: BLE001
    _HEIF_OK = False


def _embed_available() -> bool:
    try:
        import onnxruntime  # noqa: F401
        from eyescan_people import EMBED_FILENAME, EMBED_MIN_BYTES
        return find_model(EMBED_FILENAME, EMBED_MIN_BYTES) is not None
    except Exception:  # noqa: BLE001
        return False


def heif_available() -> bool:
    return _HEIF_OK


def raw_available() -> bool:
    try:
        import rawpy  # noqa: F401
        return True
    except Exception:  # noqa: BLE001
        return False


# --------------------------------------------------------------------------- #
#  Data model
# --------------------------------------------------------------------------- #

@dataclass
class FaceResult:
    blink_left: float
    blink_right: float
    closed: bool
    box: tuple[float, float, float, float]        # normalized x0, y0, x1, y1
    eye_box: tuple[float, float, float, float] | None = None
    small: bool = False                           # below min_face_area
    outline: list[tuple[float, float]] = field(default_factory=list)
    eyes: list[list[tuple[float, float]]] = field(default_factory=list)
    iris: list[tuple[float, float]] = field(default_factory=list)
    regions: dict = field(default_factory=dict)   # name -> [(x, y)...]  (see face_regions)
    ear_left: float | None = None                 # eye-aspect-ratio cross-check
    ear_right: float | None = None
    face_score: float | None = None               # BlazeFace confirmation (0..1)
    look_down: float = 0.0                        # mean eyeLookDown blendshape
    pitch: float | None = None                    # head pitch, degrees (+ = down)
    yaw: float | None = None
    roll: float | None = None
    smile: float = 0.0
    mouth_open: float = 0.0
    sharp: float | None = None                    # face sharpness score
    override: str | None = None                   # user: open|closed|ignore
    state: str = "open"                           # closed|down|open|ignored
    squint: float = 0.0
    sure: bool = False                            # closed with strong evidence
    wink: bool = False                            # exactly one eye closed
    gaze_h: float = 0.0                           # eye gaze, +1 = full look to subject's left
    gaze_v: float = 0.0                           # +1 = full look up
    occ_left: float | None = None                 # dark share of each eye window
    occ_right: float | None = None
    iris_left: float | None = None                # iris-disc contrast (pixels), see eye_glasses_iris
    iris_right: float | None = None
    glasses: float | None = None                  # frame-edge share round the eyes
    funny: float = 0.0                            # max tongueOut / cheekPuff / mouthPucker
    bindi: float | None = None                    # red forehead blob size (face-width^2)
    bg_closed: bool = False                       # transient: background face that would be closed
    emb: object = field(default=None, repr=False)     # face-recognition vector (128 floats)
    crop: bytes | None = field(default=None, repr=False)  # transient: aligned 112x112 RGB
    person: int | None = field(default=None, repr=False)  # cluster number (not cached)

    @property
    def blink(self) -> float:
        return (self.blink_left + self.blink_right) / 2.0

    @property
    def ear(self) -> float | None:
        if self.ear_left is None or self.ear_right is None:
            return None
        return (self.ear_left + self.ear_right) / 2.0

    @property
    def smiling(self) -> bool:
        return self.smile >= SMILE_MIN

    @property
    def teeth_showing(self) -> bool:
        return self.smiling and self.mouth_open >= TEETH_OPEN_MIN

    @property
    def funny_face(self) -> bool:
        return self.funny >= FUNNY_MIN

    @property
    def has_bindi(self) -> bool:
        return self.bindi is not None and self.bindi >= BINDI_MIN

    @property
    def eyes_hidden(self) -> bool:
        """Both eyes are covered (sunglasses, hair, a hand): closed/open can't be told."""
        l, r = self.occ_left, self.occ_right
        return l is not None and r is not None and min(l, r) >= HIDDEN_DARK

    @property
    def off_camera(self) -> float:
        """Degrees between where this person looks and the camera.

        Head turn and eye gaze are added in the same direction: the head turned to
        image-right is ``-yaw`` (verified on real portraits) and the eyes look to image-right
        when ``gaze_h`` is positive, so a head turned one way with the eyes swung back to
        the lens cancels out (the usual "over the shoulder" portrait is NOT looking away).
        """
        from math import hypot
        head = -(self.yaw or 0.0)
        eyes = (self.gaze_h - GAZE_NEUTRAL) * GAZE_DEG
        pitch = (self.pitch or 0.0) * 0.8
        return hypot(head + eyes, pitch - self.gaze_v * UP_DEG)

    @property
    def looks_away(self) -> bool:
        return (self.state == "open" and self.off_camera >= AWAY_SCORE
                and not self.eyes_hidden)

    @property
    def cut_off(self) -> float:
        """Share of the face that is outside or touching the frame edge (0 = whole)."""
        x0, y0, x1, y1 = self.box
        w, h = max(x1 - x0, 1e-6), max(y1 - y0, 1e-6)
        out = max(max(0.0, -x0) / w, max(0.0, x1 - 1.0) / w,
                  max(0.0, -y0) / h, max(0.0, y1 - 1.0) / h)
        if min(x0, y0, 1.0 - x1, 1.0 - y1) <= CUT_MARGIN:
            out = max(out, CUT_MIN)           # touching the edge counts, however tightly
        return min(1.0, out)

    @property
    def is_cut_off(self) -> bool:
        return self.cut_off >= CUT_MIN

    @property
    def mouth_is_open(self) -> bool:
        return self.mouth_open >= MOUTH_OPEN_MIN

    def looks_down(self) -> bool:
        """Lids visibly apart while the gaze / head points down."""
        ear = self.ear
        if ear is None or self.look_down < LOOK_DOWN_MIN:
            return False
        pitched = self.pitch is not None and self.pitch >= PITCH_DOWN
        return ear > (EAR_DOWN_PITCHED if pitched else EAR_DOWN)

    def eye_states(self, threshold: float) -> tuple[bool, bool]:
        """(left closed, right closed), each cross-checked against its own EAR."""
        return (eye_closed(self.blink_left, self.ear_left, threshold),
                eye_closed(self.blink_right, self.ear_right, threshold))

    def _is_sure(self, threshold: float) -> bool:
        strong = min(self.blink_left, self.blink_right) >= max(threshold + 0.12, 0.55)
        ear = self.ear
        return strong and (ear is None or ear <= 0.14)

    @property
    def has_glasses(self) -> bool:
        return self.glasses is not None and self.glasses >= GLASSES_MIN

    def _glasses_states(self, left: bool, right: bool) -> tuple[bool, bool, bool]:
        """Settle a "closed" eye behind spectacles with the pixels.

        Returns (left closed, right closed, unsure). An eye whose iris disc is clearly
        visible is open; one whose disc is absent stays closed; in between the pixels
        can't tell, and if no eye is clearly open the face is "can't judge".
        """
        kind = []
        for closed, c in ((left, self.iris_left), (right, self.iris_right)):
            if not closed or c is None or c < IRIS_UNSURE:
                kind.append("c" if closed else "o")
            else:
                kind.append("o" if c >= IRIS_VISIBLE else "u")
        if "o" not in kind and "u" in kind:
            return left, right, True
        return kind[0] == "c", kind[1] == "c", False

    def classify(self, threshold: float, require_both: bool = True,
                 keep_down: bool = True, background: bool = False) -> str:
        """Set and return ``state``: closed | down | open | ignored | hidden | background.

        ``require_both`` (the default and what the app always uses) means each
        eye is judged on its own and BOTH must be closed; one closed eye is a
        wink and is reported as such, never flagged. ``background``: a person behind
        / out of focus beside the main subject - not judged, only noted (``bg_closed``).
        """
        left, right = self.eye_states(threshold)
        self.wink = left != right
        self.bg_closed = False
        if background and self.override in (None, ""):
            self.bg_closed = left and right
            self.wink = False
            self.state, self.closed, self.sure = "background", False, False
            return "background"
        glasses_used = False
        if self.has_glasses and self.override in (None, ""):
            left, right, unsure = self._glasses_states(left, right)
            glasses_used = True
            self.wink = left != right and not unsure
            if unsure:
                self.wink = False
                self.state, self.closed, self.sure = "hidden", False, False
                return "hidden"
        if self.eyes_hidden and self.override in (None, ""):
            # sunglasses / hair / a hand: the blendshapes are guesses, don't flag
            self.wink = False
            self.state, self.closed, self.sure = "hidden", False, False
            return "hidden"
        if self.override == "ignore":
            state = "ignored"
        elif self.override == "open":
            state = "open"
        elif self.override == "closed":
            state = "closed"
        else:
            raw = (left and right) if (require_both or glasses_used) \
                else self.is_closed(threshold, False)
            if raw:
                state = "down" if (keep_down and self.looks_down()) else "closed"
            else:
                state = "open"
        self.state = state
        self.closed = state == "closed"
        self.sure = state == "closed" and self._is_sure(threshold)
        return state

    def is_closed(self, threshold: float, require_both: bool = True) -> bool:
        if require_both:
            left, right = self.eye_states(threshold)
            return left and right
        score = self.blink
        closed = score >= threshold
        if self.ear_left is None or self.ear_right is None:
            return closed
        ear = (self.ear_left + self.ear_right) / 2.0
        if closed and ear > EAR_OPEN and score < threshold + EAR_VETO_MARGIN:
            return False
        if not closed and ear < EAR_SHUT and score >= threshold - EAR_RESCUE_MARGIN:
            return True
        return closed


@dataclass
class AnimalResult:
    label: str
    score: float
    box: tuple[float, float, float, float]        # normalized x0, y0, x1, y1


@dataclass
class ImageResult:
    path: Path
    verdict: str = "open"      # closed | down | open | no-face | error | unsupported
    faces: list[FaceResult] = field(default_factory=list)
    error: str | None = None
    n_people: int = 0                  # confirmed faces, including tiny ones
    n_bodies: int = 0                  # people found by the object detector
    animals: list[AnimalResult] = field(default_factory=list)
    sharpness: float | None = None     # sharpness of the prominent face(s)
    bg_sharp: float | None = None      # sharpness of everything but the faces
    override: str | None = None        # user: keep | flag
    protected: bool = False            # user: skip in bulk sort / delete
    file_size: int = 0
    width: int = 0
    height: int = 0
    phash: str = ""
    dup_group: int | None = None       # duplicate group id (None = unique)
    dup_best: bool = False             # the frame to keep in its group
    dup_dist: int = 0                  # distance to the best frame
    dup_exact: bool = False
    dup_score: float = 0.0             # "best of burst" score of this frame
    dup_why: str = ""                  # why the winner won, or why this frame lost
    rescue: object = None              # ImageResult: a better frame with these eyes open
    rescue_for: int = 0                # how many closed faces that frame fixes
    from_cache: bool = False

    @property
    def n_closed(self) -> int:
        return sum(1 for f in self.faces if f.state == "closed")

    @property
    def n_down(self) -> int:
        return sum(1 for f in self.faces if f.state == "down")

    @property
    def max_blink(self) -> float:
        return max((f.blink for f in self.faces), default=0.0)

    @property
    def min_ear(self) -> float | None:
        ears = [f.ear for f in self.faces if f.ear is not None]
        return min(ears) if ears else None

    @property
    def is_flagged(self) -> bool:
        return self.verdict == "closed"

    @property
    def people(self) -> int:
        return max(self.n_people, self.n_bodies)

    @property
    def group(self) -> str:
        """none | solo | pair | group (3+ people)."""
        n = self.people
        return "none" if n == 0 else "solo" if n == 1 else "pair" if n == 2 else "group"

    @property
    def has_animal(self) -> bool:
        return bool(self.animals)

    @property
    def focus(self) -> str:
        """sharp | blurry | misfocused | n/a - judged on the face only."""
        sh = self.sharpness
        if sh is None:
            return "n/a"
        if sh >= BLUR_SHARP_MIN:
            return "sharp"
        # the face is unclear: did focus land on the background instead?
        if self.bg_sharp and self.bg_sharp >= max(MISFOCUS_BG_MIN,
                                                  MISFOCUS_RATIO * sh):
            return "misfocused"
        return "blurry"

    @property
    def blurry(self) -> bool:
        return self.focus == "blurry"

    @property
    def misfocused(self) -> bool:
        return self.focus == "misfocused"

    @property
    def unclear(self) -> bool:
        return self.focus in ("blurry", "misfocused")

    @property
    def needs_check(self) -> bool:
        """Flagged closed, but without strong evidence - worth a quick look."""
        closed = [f for f in self.faces if f.state == "closed"]
        return self.verdict == "closed" and bool(closed) and not any(
            f.sure for f in closed) and self.override != "flag"

    @property
    def n_hidden(self) -> int:
        return sum(1 for f in self.faces if f.state == "hidden")

    @property
    def n_away(self) -> int:
        return sum(1 for f in self.faces if f.looks_away)

    @property
    def n_cut(self) -> int:
        return sum(1 for f in self.faces if f.is_cut_off)

    @property
    def has_wink(self) -> bool:
        return any(f.wink and f.state == "open" for f in self.faces)

    @property
    def is_duplicate(self) -> bool:
        return self.dup_group is not None and not self.dup_best

    def tags(self) -> list[str]:
        out: list[str] = []
        if self.group != "none":
            out.append(f"{self.group} ({self.people})" if self.group == "group"
                       else self.group)
        for lab in sorted({a.label for a in self.animals}):
            out.append(lab)
        if any(f.smiling for f in self.faces):
            out.append("smiling")
        if any(f.teeth_showing for f in self.faces):
            out.append("teeth")
        if any(f.mouth_is_open for f in self.faces):
            out.append("mouth open")
        if any(f.funny_face for f in self.faces):
            out.append("funny face")
        if any(f.has_bindi for f in self.faces):
            out.append("bindi")
        if self.has_wink:
            out.append("wink")
        if self.n_hidden:
            out.append("eyes hidden")
        if any(f.bg_closed for f in self.faces):
            out.append("background closed")
        if any(f.has_glasses for f in self.faces):
            out.append("glasses")
        if self.n_away:
            out.append("looking away")
        if self.n_cut:
            out.append("face cut off")
        if self.rescue is not None:
            out.append("better frame")
        if self.unclear:
            out.append(self.focus)
        if self.dup_group is not None:
            out.append("duplicate" if self.is_duplicate else "best of group")
        if self.protected:
            out.append("protected")
        return out

    def _background_faces(self) -> set:
        """ids of faces that are clearly not the subject: much smaller than the biggest
        face, or smaller and far softer than the sharpest, or too narrow to read.
        Faces the user overrode or ignored are never background."""
        cand = [f for f in self.faces if f.override != "ignore"]
        if len(cand) < 2:
            return set()
        big = max(core_area(f.box) for f in cand)
        top = max((f.sharp for f in cand if f.sharp is not None), default=None)
        out = set()
        for f in cand:
            if f.override in ("open", "closed"):
                continue
            a = core_area(f.box)
            if a >= big:
                continue
            narrow = self.width > 0 and (f.box[2] - f.box[0]) * self.width < BG_MIN_PX
            soft = (f.sharp is not None and top and f.sharp < BG_REL_SHARP * top
                    and a < BG_REL_AREA_SOFT * big)
            if a < BG_REL_AREA * big or soft or narrow:
                out.add(id(f))
        return out

    def merge_duplicate_faces(self) -> int:
        """Drop faces that are the same face detected twice (one person is in a photo once).

        Two faces are one when their eyes (nearly) coincide, or their boxes overlap well and their centres
        are close. The better-scored, larger detection is kept. Returns how many were removed."""
        faces = self.faces
        if len(faces) < 2:
            return 0
        W, H = (self.width or 1000), (self.height or 1000)

        def geo(f):
            loops = [e for e in (f.eyes or []) if e]
            if len(loops) >= 2:
                cs = [(sum(p[0] for p in e) / len(e) * W, sum(p[1] for p in e) / len(e) * H) for e in loops[:2]]
                eye = ((cs[0][0] + cs[1][0]) / 2, (cs[0][1] + cs[1][1]) / 2)
                gap = ((cs[0][0] - cs[1][0]) ** 2 + (cs[0][1] - cs[1][1]) ** 2) ** 0.5
            else:
                eye, gap = None, 0.0
            bw = (f.box[2] - f.box[0]) * W
            c = ((f.box[0] + f.box[2]) / 2 * W, (f.box[1] + f.box[3]) / 2 * H)
            return eye, max(gap, 0.3 * bw), c, bw

        def same(a, b):
            (ea, ga, ca, wa), (eb, gb, cb, wb) = geo(a), geo(b)
            if ea is not None and eb is not None:
                d = ((ea[0] - eb[0]) ** 2 + (ea[1] - eb[1]) ** 2) ** 0.5
                if d < 0.6 * max(ga, gb):
                    return True
            dist = ((ca[0] - cb[0]) ** 2 + (ca[1] - cb[1]) ** 2) ** 0.5
            return _iou(a.box, b.box) >= 0.25 and dist < 0.3 * max(wa, wb)

        def rank(f):
            return _area(f.box) * (f.face_score if f.face_score is not None else 0.85)

        kept: list = []
        for f in sorted(faces, key=rank, reverse=True):
            if not any(same(f, k) for k in kept):
                kept.append(f)
        removed = len(faces) - len(kept)
        if removed:
            order = {id(f): i for i, f in enumerate(faces)}
            self.faces = sorted(kept, key=lambda f: order[id(f)])
            self.n_people = max(0, self.n_people - removed)
        return removed

    def reclassify(self, threshold: float, require_both: bool = True,
                   keep_down: bool = True) -> None:
        """Re-apply thresholds / overrides using the cached face measurements."""
        if self.verdict in ("error", "unsupported"):
            return
        bg = self._background_faces() if MAIN_ONLY else set()
        for f in self.faces:
            f.classify(threshold, require_both, keep_down, background=id(f) in bg)
        live = [f for f in self.faces if f.state not in ("ignored", "background")]
        if self.override == "flag":
            self.verdict = "closed"
        elif self.override == "keep" and live:
            self.verdict = "open"
        elif not live:
            self.verdict = "no-face"
        elif any(f.state == "closed" for f in live):
            self.verdict = "closed"
        elif any(f.state == "down" for f in live):
            self.verdict = "down"
        elif all(f.state == "hidden" for f in live):
            self.verdict = "hidden"          # nobody's eyes can be seen
        else:
            self.verdict = "open"


def apply_threshold(results, threshold: float, require_both: bool = True,
                    keep_down: bool = True) -> None:
    for r in results:
        r.reclassify(threshold, require_both, keep_down)
    try:
        blink_rescue(results)
    except Exception:  # noqa: BLE001 - a suggestion, never a reason to fail
        pass


# --------------------------------------------------------------------------- #
#  Worker processes (parallel scanning)
# --------------------------------------------------------------------------- #

class _PoolUnavailable(Exception):
    """The process pool could not start; the caller falls back to one process."""


_WORKER_ENGINE = None


def _worker_init(spec: dict) -> None:
    global _WORKER_ENGINE
    _WORKER_ENGINE = EyeEngine(**spec)


def _worker_analyze(path):
    return _WORKER_ENGINE.analyze(Path(path))


POOL_MIN_PHOTOS = 40      # below this the ~4 s pool start-up outweighs the gain


def auto_workers(n_photos: int) -> int:
    """Worker processes for a batch: leave one core for the UI, cap by memory use
    (each worker holds its own copy of the models, ~300 MB)."""
    cores = os.cpu_count() or 1
    if n_photos < POOL_MIN_PHOTOS or cores < 4:
        return 1
    return max(1, min(4, cores - 1))


# --------------------------------------------------------------------------- #
#  Engine
# --------------------------------------------------------------------------- #

class EyeEngine:
    """MediaPipe FaceLandmarker with tiled detection + crop refinement."""

    # class-level defaults so lightweight/stubbed engines stay valid
    refine_max_dim = REFINE_MAX_DIM
    rotate_fallback = True
    require_both = True
    _validator = None            # BlazeFace; None = no independent check
    _objdet = None               # EfficientDet (animals / bodies)
    keep_down = True
    detect_animals = True
    embed_faces = False
    animal_error: str | None = None

    def __init__(
        self,
        model_path: str | Path,
        threshold: float = DEFAULT_THRESHOLD,
        require_both: bool = True,
        num_faces: int = 10,
        min_face_area: float = DEFAULT_MIN_FACE_AREA,
        robust: bool = True,
        deep: bool = False,
        refine: bool = True,
        detect_max_dim: int = DEFAULT_DETECT_MAX_DIM,
        refine_max_dim: int = REFINE_MAX_DIM,
        rotate_fallback: bool = True,
        validate: bool = True,
        detect_animals: bool = True,
        keep_down: bool = True,
        detect_confidence: float = 0.25,
        tile_confidence: float = 0.30,
        embed_faces: bool = True,
    ) -> None:
        from mediapipe.tasks.python import BaseOptions
        from mediapipe.tasks.python.vision import (
            FaceLandmarker,
            FaceLandmarkerOptions,
            RunningMode,
        )

        self._model_path = str(model_path)
        self._num_faces = num_faces
        self._validate = validate
        self._detect_conf = (detect_confidence, tile_confidence)
        self.threshold = threshold
        self.require_both = require_both
        self.min_face_area = min_face_area
        self.robust = robust          # always run the 2x2 tiled pass
        self.deep = deep              # also run a 3x3 pass
        self.refine = refine          # re-detect each face on a hi-res crop
        self.detect_max_dim = detect_max_dim
        self.refine_max_dim = max(refine_max_dim, detect_max_dim)
        self.rotate_fallback = rotate_fallback  # retry sideways if no face found
        self.keep_down = keep_down          # "looking down" is kept, not flagged
        self.detect_animals = detect_animals
        # face recognition: workers cut out aligned face crops, the main process embeds
        # them (on the GPU when there is one). Off when the model file is missing.
        self.embed_faces = bool(embed_faces) and _embed_available()

        def make(conf: float):
            return FaceLandmarker.create_from_options(
                FaceLandmarkerOptions(
                    base_options=BaseOptions(model_asset_path=str(model_path)),
                    running_mode=RunningMode.IMAGE,
                    num_faces=num_faces,
                    min_face_detection_confidence=conf,
                    min_face_presence_confidence=conf,
                    output_face_blendshapes=True,
                    output_facial_transformation_matrixes=True,
                )
            )

        # A sensitive detector for the whole image, and a stricter one for the
        # tiles: tiles make real faces larger (so high-confidence), while
        # textured background regions that only *look* like faces are rejected.
        self._detector = make(detect_confidence)
        self._tile_detector = make(tile_confidence)

        # Independent second opinion. FaceLandmarker will happily fit a mesh to
        # a foot, a hand or a patterned fabric; BlazeFace (a different network)
        # almost never agrees, so a face it can't confirm is discarded.
        self.validator_error: str | None = None
        if validate:
            try:
                from mediapipe.tasks.python.vision import (
                    FaceDetector, FaceDetectorOptions)
                bpath = ensure_model(lambda _m: None, BLAZE_FILENAME,
                                     BLAZE_URL, 50_000)
                self._validator = FaceDetector.create_from_options(
                    FaceDetectorOptions(
                        base_options=BaseOptions(model_asset_path=str(bpath)),
                        min_detection_confidence=0.2))
            except Exception as exc:  # noqa: BLE001
                self.validator_error = str(exc)   # degrade gracefully

        # Animals (and a cheap body count for group tagging).
        if detect_animals:
            try:
                from mediapipe.tasks.python.vision import (
                    ObjectDetector, ObjectDetectorOptions)
                apath = ensure_model(lambda _m: None, ANIMAL_FILENAME,
                                     ANIMAL_URL, 1_000_000)
                self._objdet = ObjectDetector.create_from_options(
                    ObjectDetectorOptions(
                        base_options=BaseOptions(model_asset_path=str(apath)),
                        running_mode=RunningMode.IMAGE,
                        max_results=25, score_threshold=0.30))
            except Exception as exc:  # noqa: BLE001
                self.animal_error = str(exc)

    def close(self) -> None:
        for det in (getattr(self, "_detector", None),
                    getattr(self, "_tile_detector", None),
                    getattr(self, "_validator", None),
                    getattr(self, "_objdet", None)):
            try:
                det.close()
            except Exception:  # noqa: BLE001
                pass

    def cache_key(self) -> str:
        """Everything that changes the *measurements* (not the verdict)."""
        return (f"v{SCHEMA_VERSION}|r{int(bool(self.robust))}|d{int(bool(self.deep))}"
                f"|a{int(bool(self.detect_animals and self._objdet is not None))}"
                f"|b{int(self._validator is not None)}|m{self.min_face_area}"
                f"|x{self.detect_max_dim}|s1|e{int(bool(self.embed_faces))}")

    # -- classification ---------------------------------------------------- #

    def classify(self, blink_left: float, blink_right: float,
                 ear_left: float | None = None,
                 ear_right: float | None = None) -> bool:
        return FaceResult(blink_left, blink_right, False, (0, 0, 1, 1),
                          ear_left=ear_left, ear_right=ear_right
                          ).is_closed(self.threshold, self.require_both)

    @staticmethod
    def _eye_box(points):
        idx = EYE_LANDMARKS
        if len(points) <= max(idx):
            return None
        xs = [points[i][0] for i in idx]
        ys = [points[i][1] for i in idx]
        x0, y0, x1, y1 = min(xs), min(ys), max(xs), max(ys)
        px = (x1 - x0) * 0.06
        py = (y1 - y0) * 0.70
        return (max(0.0, x0 - px), max(0.0, y0 - py),
                min(1.0, x1 + px), min(1.0, y1 + py))

    # -- low level detection ----------------------------------------------- #

    def _raw_detect(self, region, ox: int, oy: int, W: int, H: int, detector=None):
        """Detect in ``region`` (offset ox,oy within a W x H frame).

        Returns a list of ``(points, scores)`` where ``points`` are (x, y)
        normalized to the full frame.
        """
        import numpy as np
        import mediapipe as mp

        det = detector if detector is not None else self._detector
        out = det.detect(
            mp.Image(image_format=mp.ImageFormat.SRGB,
                     data=np.ascontiguousarray(region))
        )
        landmarks = getattr(out, "face_landmarks", None) or []
        blendshapes = getattr(out, "face_blendshapes", None) or []
        matrices = getattr(out, "facial_transformation_matrixes", None) or []
        rh, rw = region.shape[:2]
        faces = []
        for i, fl in enumerate(landmarks):
            pts = [((ox + p.x * rw) / W, (oy + p.y * rh) / H) for p in fl]
            scores: dict[str, float] = {}
            if i < len(blendshapes):
                for cat in blendshapes[i]:
                    scores[cat.category_name] = cat.score
            if i < len(matrices):
                try:
                    scores.update(_head_pose(matrices[i]))
                except Exception:  # noqa: BLE001 - pose is optional
                    pass
            faces.append((pts, scores))
        return faces

    def _detection_image(self, rgb):
        """A downscaled copy used for detection (normalized coords unchanged)."""
        import numpy as np
        from PIL import Image

        h, w = rgb.shape[:2]
        m = self.detect_max_dim
        if not m or max(h, w) <= m:
            return rgb
        s = m / max(h, w)
        img = Image.fromarray(rgb).resize(
            (max(1, int(w * s)), max(1, int(h * s))), Image.BILINEAR)
        return np.asarray(img)

    def _tiled(self, det, grid: int):
        h, w = det.shape[:2]
        tw, th = w // grid, h // grid
        if tw < 32 or th < 32:
            return []
        ox, oy = int(tw * TILE_OVERLAP), int(th * TILE_OVERLAP)
        faces = []
        for gy in range(grid):
            for gx in range(grid):
                x0 = max(0, gx * tw - ox)
                y0 = max(0, gy * th - oy)
                x1 = min(w, (gx + 1) * tw + ox)
                y1 = min(h, (gy + 1) * th + oy)
                faces += self._raw_detect(det[y0:y1, x0:x1], x0, y0, w, h,
                                          detector=self._tile_detector)
        return faces

    @staticmethod
    def _dedupe(faces, thr: float = DEDUPE_IOU):
        kept = []
        for pts, scores in sorted(
            faces, key=lambda f: _area(_box_of(f[0])), reverse=True
        ):
            box = _box_of(pts)
            if all(_iou(box, _box_of(k[0])) < thr
                   and _contained(box, _box_of(k[0])) < DEDUPE_CONTAIN
                   for k in kept):
                kept.append((pts, scores))
        return kept

    def _refine(self, rgb, pts, scores):
        """Re-detect a face on a padded full-resolution crop for better scores."""
        if not self.refine:
            return pts, scores
        h, w = rgb.shape[:2]
        box = _box_of(pts)
        bw, bh = box[2] - box[0], box[3] - box[1]
        if bw <= 0 or bh <= 0:
            return pts, scores
        mx, my = bw * 0.6, bh * 0.8
        x0 = max(0, int((box[0] - mx) * w))
        y0 = max(0, int((box[1] - my) * h))
        x1 = min(w, int((box[2] + mx) * w))
        y1 = min(h, int((box[3] + my) * h))
        if x1 - x0 < 24 or y1 - y0 < 24:
            return pts, scores
        found = self._raw_detect(rgb[y0:y1, x0:x1], x0, y0, w, h,
                                 detector=self._tile_detector)
        if not found:
            return pts, scores
        best = max(found, key=lambda f: _iou(_box_of(f[0]), box))
        return best if _iou(_box_of(best[0]), box) > 0.2 else (pts, scores)

    def _objects(self, det):
        """(animals, n_bodies) from the object detector on the detection image."""
        if self._objdet is None:
            return [], 0
        import numpy as np
        import mediapipe as mp

        h, w = det.shape[:2]
        out = self._objdet.detect(mp.Image(
            image_format=mp.ImageFormat.SRGB, data=np.ascontiguousarray(det)))
        animals: list[AnimalResult] = []
        bodies = 0
        self._body_boxes = []
        for d in getattr(out, "detections", None) or []:
            if not d.categories:
                continue
            cat = d.categories[0]
            name = (cat.category_name or "").lower()
            bb = d.bounding_box
            box = (max(0.0, bb.origin_x / w), max(0.0, bb.origin_y / h),
                   min(1.0, (bb.origin_x + bb.width) / w),
                   min(1.0, (bb.origin_y + bb.height) / h))
            if name in ANIMAL_LABELS and cat.score >= ANIMAL_CONF:
                animals.append(AnimalResult(name, float(cat.score), box))
            elif name == "person" and cat.score >= PERSON_CONF:
                bodies += 1
                self._body_boxes.append(box)
        return animals, bodies

    def _rescue_small_faces(self, rgb, faces):
        """Faces too small for the whole-image passes, found through the person boxes.

        In a wide shot (a seated subject, a dog, a courtyard) the face can be 1-2 % of
        the frame - invisible at detection size. The object detector still finds the
        *person*, so look for a face in the top of each person box that has none yet,
        on the full-resolution pixels.
        """
        boxes = getattr(self, "_body_boxes", None)
        if not boxes:
            return faces
        H, W = rgb.shape[:2]
        found = []
        for bx in sorted(boxes, key=lambda b: -_area(b))[:4]:
            bw, bh = bx[2] - bx[0], bx[3] - bx[1]
            if bh < 0.06 or bw <= 0:
                continue
            has = any(bx[0] <= (b[0] + b[2]) / 2 <= bx[2] and bx[1] <= (b[1] + b[3]) / 2
                      <= bx[1] + bh * 0.6
                      for b in (_box_of(p) for p, _s in faces))
            if has:
                continue
            x0, x1 = max(0.0, bx[0] - bw * 0.15), min(1.0, bx[2] + bw * 0.15)
            y0, y1 = max(0.0, bx[1] - bh * 0.08), min(1.0, bx[1] + bh * 0.42)
            px0, py0, px1, py1 = int(x0 * W), int(y0 * H), int(x1 * W), int(y1 * H)
            if px1 - px0 < 48 or py1 - py0 < 48:
                continue
            found += self._raw_detect(rgb[py0:py1, px0:px1], px0, py0, W, H,
                                      detector=self._tile_detector)
        if not found:
            return faces
        found = self._dedupe(found)
        found = [self._refine(rgb, pts, sc) for pts, sc in found]
        found = self._validated(rgb, found)
        return self._dedupe(list(faces) + found)

    def _confirm(self, img, pts):
        """BlazeFace score for the face at ``pts`` (normalized to ``img``).

        Returns None when there is no validator / the crop is too small to
        judge (the face is then kept as-is), else the best score, 0.0 if
        BlazeFace sees no face in the crop.
        """
        if self._validator is None:
            return None
        import numpy as np
        import mediapipe as mp

        h, w = img.shape[:2]
        box = _box_of(pts)
        bw, bh = box[2] - box[0], box[3] - box[1]
        x0 = max(0, int((box[0] - bw * VALIDATE_PAD) * w))
        y0 = max(0, int((box[1] - bh * VALIDATE_PAD) * h))
        x1 = min(w, int((box[2] + bw * VALIDATE_PAD) * w))
        y1 = min(h, int((box[3] + bh * VALIDATE_PAD) * h))
        if x1 - x0 < 24 or y1 - y0 < 24:
            return None
        crop = np.ascontiguousarray(img[y0:y1, x0:x1])
        out = self._validator.detect(
            mp.Image(image_format=mp.ImageFormat.SRGB, data=crop))
        scores = [d.categories[0].score for d in (out.detections or [])
                  if d.categories]
        return max(scores, default=0.0)

    def _validated(self, img, faces):
        """Drop faces BlazeFace can't confirm; tag the rest with its score."""
        kept = []
        for pts, sc in faces:
            conf = self._confirm(img, pts)
            if conf is not None and conf < VALIDATE_CONF:
                continue
            sc = dict(sc)
            sc["_face_score"] = conf
            kept.append((pts, sc))
        return kept

    def detect_faces(self, rgb, det=None):
        """Full pipeline -> list of (points, scores) normalized to the image."""
        if det is None:
            det = self._detection_image(rgb)
        h, w = det.shape[:2]
        faces = self._raw_detect(det, 0, 0, w, h)
        if self.robust and max(w, h) >= MIN_TILE_DIM:
            faces += self._tiled(det, 2)
            if self.deep:
                faces += self._tiled(det, 3)
        faces = self._dedupe(faces)
        faces = [self._refine(rgb, pts, sc) for pts, sc in faces]
        faces = self._validated(rgb, faces)
        if not faces and self.rotate_fallback:
            faces = self._detect_rotated(det)
        return faces

    def _detect_rotated(self, det):
        """Last resort for photos with sideways faces: try 90 and 270 degrees.

        Uses the strict detector to keep false positives down, and maps the
        landmarks back into the upright image's coordinates.
        """
        import numpy as np

        for k, back in ((1, lambda x, y: (1.0 - y, x)),    # rot90 CCW
                        (3, lambda x, y: (y, 1.0 - x))):   # rot90 CW
            rot = np.ascontiguousarray(np.rot90(det, k))
            rh, rw = rot.shape[:2]
            found = self._validated(rot, self._raw_detect(
                rot, 0, 0, rw, rh, detector=self._tile_detector))
            if found:
                return [([back(x, y) for x, y in pts], sc) for pts, sc in found]
        return []

    # -- public ------------------------------------------------------------ #

    def load(self, path: Path):
        """Decode ``path`` -> ``(final_result_or_None, rgb_or_None)``.

        A non-None result means the file can't be analysed (already final).
        Safe to call from a worker thread (no MediaPipe involved).
        """
        res = ImageResult(path=path)
        ext = path.suffix.lower()
        if ext not in IMAGE_EXTS:
            res.verdict = "unsupported"
            res.error = f"unsupported extension {ext}"
            return res, None
        try:
            rgb = load_rgb_array(path, self.refine_max_dim if self.refine
                                 else self.detect_max_dim)
        except Exception as exc:  # noqa: BLE001
            res.verdict = "error"
            res.error = f"cannot read image: {exc}"
            return res, None
        if rgb is None or rgb.size == 0:
            res.verdict = "error"
            res.error = "empty image"
            return res, None
        return None, rgb

    def worker_spec(self) -> dict:
        """Constructor arguments that rebuild this engine in a worker process."""
        return dict(
            model_path=self._model_path, threshold=self.threshold,
            require_both=self.require_both, num_faces=self._num_faces,
            min_face_area=self.min_face_area, robust=self.robust, deep=self.deep,
            refine=self.refine, detect_max_dim=self.detect_max_dim,
            refine_max_dim=self.refine_max_dim, rotate_fallback=self.rotate_fallback,
            validate=self._validate, detect_animals=self.detect_animals,
            keep_down=self.keep_down, detect_confidence=self._detect_conf[0],
            tile_confidence=self._detect_conf[1], embed_faces=self.embed_faces)

    def analyze_many(self, paths, stop=None, prefetch: int = 2, workers: int = 1):
        """Yield ``(path, ImageResult)`` in input order.

        ``workers`` > 1 analyses photos in that many worker processes, each with
        its own models (decode + resize + inference all run in parallel; the
        GIL and MediaPipe's per-instance state make threads useless here). With
        one worker, only the slow image decode is overlapped on helper threads
        and detection stays on the calling thread. ``stop`` is an optional
        ``threading.Event``-like object checked between images.
        """
        paths = list(paths)
        if workers > 1 and len(paths) >= 2 * workers:
            done = 0
            try:
                for res in self._analyze_pool(paths, stop, workers):
                    yield paths[done], res
                    done += 1
                return
            except _PoolUnavailable:
                paths = paths[done:]          # carry on serially with the rest
        yield from self._analyze_serial(paths, stop, prefetch)

    def _analyze_pool(self, paths, stop, workers: int):
        import multiprocessing as mp
        from concurrent.futures.process import BrokenProcessPool

        window = workers * 2
        started = 0
        try:
            pool = ProcessPoolExecutor(
                max_workers=workers, mp_context=mp.get_context("spawn"),
                initializer=_worker_init, initargs=(self.worker_spec(),))
        except Exception as exc:  # noqa: BLE001
            raise _PoolUnavailable(str(exc)) from exc
        with pool:
            pending: dict[int, object] = {}
            nxt = 0
            for i in range(len(paths)):
                while nxt < len(paths) and nxt < i + window:
                    pending[nxt] = pool.submit(_worker_analyze, paths[nxt])
                    nxt += 1
                if stop is not None and stop.is_set():
                    for f in pending.values():
                        f.cancel()
                    return
                try:
                    res = pending.pop(i).result()
                except BrokenProcessPool as exc:
                    if started == 0:
                        raise _PoolUnavailable(str(exc)) from exc
                    res = ImageResult(path=paths[i], verdict="error",
                                      error="worker process died")
                except Exception as exc:  # noqa: BLE001
                    res = ImageResult(path=paths[i], verdict="error", error=str(exc))
                started += 1
                yield res

    def _analyze_serial(self, paths, stop, prefetch: int):
        with ThreadPoolExecutor(max_workers=max(1, prefetch)) as pool:
            pending = {}
            nxt = 0
            for i in range(len(paths)):
                while nxt < len(paths) and nxt <= i + prefetch:
                    pending[nxt] = pool.submit(self.load, paths[nxt])
                    nxt += 1
                if stop is not None and stop.is_set():
                    for f in pending.values():
                        f.cancel()
                    return
                try:
                    early, rgb = pending.pop(i).result()
                    res = early if early is not None else self.analyze(
                        paths[i], rgb=rgb)
                except Exception as exc:  # noqa: BLE001
                    res = ImageResult(path=paths[i], verdict="error",
                                      error=str(exc))
                yield paths[i], res

    def analyze(self, path: Path, rgb=None) -> ImageResult:
        res = ImageResult(path=path)
        if rgb is None:
            early, rgb = self.load(path)
            if early is not None:
                return early

        try:
            det = self._detection_image(rgb)
            faces = self.detect_faces(rgb, det)
            if self.detect_animals and self._objdet is not None:
                res.animals, res.n_bodies = self._objects(det)
                faces = self._rescue_small_faces(rgb, faces)
        except Exception as exc:  # noqa: BLE001
            res.verdict = "error"
            res.error = f"detector failed: {exc}"
            return res

        if res.animals and faces:
            res.animals = [a for a in res.animals if a.score >= ANIMAL_CONF_WITH_PEOPLE]
        if res.animals and faces:
            # BlazeFace happily confirms a dog's face; it is not a person's
            faces = [f for f in faces if not any(
                a.score >= 0.5 and _contained_in(_box_of(f[0]), a.box) >= ANIMAL_FACE_OVERLAP
                for a in res.animals)]

        res.n_people = len(faces)
        H, W = rgb.shape[:2]
        try:
            res.file_size = path.stat().st_size
        except OSError:
            pass
        res.width, res.height = getattr(rgb, "_orig_size", None) or (W, H)
        try:
            res.phash = image_hash(rgb)
        except Exception:  # noqa: BLE001 - hashing is optional
            res.phash = ""
        biggest = 0.0
        for pts, scores in faces:
            box = _box_of(pts)
            area = _area(box)
            small = area < self.min_face_area
            if small:
                continue  # too small to judge reliably
            left = float(scores.get("eyeBlinkLeft", 0.0))
            right = float(scores.get("eyeBlinkRight", 0.0))
            iris = []
            for idx in (IRIS_RIGHT_IDX, IRIS_LEFT_IDX):
                sub = _pick(pts, idx)
                if sub:
                    iris.append((sum(p[0] for p in sub) / len(sub),
                                 sum(p[1] for p in sub) / len(sub)))
            ear_l, ear_r = eye_aspect_ratios(pts, W, H)
            gaze_h, gaze_v = eye_gaze(scores)
            try:
                occ_l, occ_r = eye_occlusion(rgb, pts, W, H)
            except Exception:  # noqa: BLE001 - optional measurement
                occ_l = occ_r = None
            try:
                (iris_l, iris_r), glasses = eye_glasses_iris(rgb, pts, W, H)
            except Exception:  # noqa: BLE001 - optional measurement
                iris_l = iris_r = glasses = None
            try:
                bindi = bindi_score(rgb, pts, W, H)
            except Exception:  # noqa: BLE001 - optional measurement
                bindi = None
            crop = None
            if self.embed_faces:
                try:
                    from eyescan_people import align_crop
                    crop = align_crop(rgb, pts, scores.get("_yaw"))
                except Exception:  # noqa: BLE001
                    crop = None

            def sc(*names):
                vals = [float(scores[n]) for n in names if n in scores]
                return sum(vals) / len(vals) if vals else 0.0

            fr = FaceResult(
                left, right, False, box,
                eye_box=self._eye_box(pts), small=small,
                outline=_pick(pts, FACE_OVAL_LOOP),
                regions=face_regions(pts, W, H),
                eyes=[_pick(pts, EYE_LEFT_LOOP), _pick(pts, EYE_RIGHT_LOOP)],
                iris=iris, ear_left=ear_l, ear_right=ear_r,
                face_score=scores.get("_face_score"),
                look_down=sc("eyeLookDownLeft", "eyeLookDownRight"),
                pitch=scores.get("_pitch"), yaw=scores.get("_yaw"),
                roll=scores.get("_roll"),
                smile=sc("mouthSmileLeft", "mouthSmileRight"),
                mouth_open=sc("jawOpen"), squint=sc("eyeSquintLeft", "eyeSquintRight"),
                sharp=(face_sharpness(rgb, box)
                       if min((box[2] - box[0]) * W, (box[3] - box[1]) * H) >= FACE_MIN_PX
                       else None),
                gaze_h=gaze_h, gaze_v=gaze_v, occ_left=occ_l, occ_right=occ_r,
                iris_left=iris_l, iris_right=iris_r, glasses=glasses, bindi=bindi,
                funny=max(sc("tongueOut"), sc("cheekPuff"), sc("mouthPucker")),
                crop=crop)
            res.faces.append(fr)
            biggest = max(biggest, area)

        # focus is judged on the prominent faces: the softest of those that are
        # at least half as large as the biggest face
        prom = [f for f in res.faces if f.sharp is not None
                and core_area(f.box) >= 0.5 * biggest]
        if prom:
            res.sharpness = min(f.sharp for f in prom)
            try:
                res.bg_sharp = background_sharpness(rgb, [f.box for f in res.faces])
            except Exception:  # noqa: BLE001
                res.bg_sharp = None

        res.verdict = "open"
        res.merge_duplicate_faces()
        res.reclassify(self.threshold, self.require_both, self.keep_down)
        return res


# --------------------------------------------------------------------------- #
#  Image loading helpers
# --------------------------------------------------------------------------- #

_RAW_FLIP = {3: 180, 5: 90, 6: 270}      # LibRaw flip code -> PIL rotate() degrees (CCW)


def _load_raw(path: Path, max_dim: int | None):
    """Decode a camera RAW (Canon CR2/CR3, Nikon NEF, Sony ARW, DNG ...) to an upright PIL image.

    The full-size JPEG that cameras embed is used when it is big enough for ``max_dim`` (fast,
    and it carries the camera's own colour rendering); otherwise the sensor data is developed
    with LibRaw (half size, camera white balance), which is still much quicker than a full demosaic.
    """
    import io

    import rawpy
    from PIL import Image

    with rawpy.imread(str(path)) as raw:
        sz = raw.sizes
        flip = sz.flip
        upright = (sz.iheight, sz.iwidth) if flip in (5, 6) else (sz.iwidth, sz.iheight)
        im = None
        try:
            th = raw.extract_thumb()
            if th.format == rawpy.ThumbFormat.JPEG:
                t = Image.open(io.BytesIO(th.data))
                if max(t.size) >= min(max_dim or 2048, 2048) and max(t.size) * 2 >= max(upright):
                    # embedded previews are not always rotated; trust EXIF if present, else LibRaw's flip
                    exif_rot = t.getexif().get(0x0112, 1) != 1
                    if exif_rot:
                        from PIL import ImageOps
                        im = ImageOps.exif_transpose(t).convert("RGB")
                    else:
                        im = t.convert("RGB")
                        if (im.width > im.height) != (upright[0] > upright[1]) and flip in _RAW_FLIP:
                            im = im.rotate(_RAW_FLIP[flip], expand=True)
            elif th.format == rawpy.ThumbFormat.BITMAP:
                im = Image.fromarray(th.data)
        except Exception:  # noqa: BLE001 - no/unsupported preview: develop the sensor data
            im = None
        if im is None:
            small = bool(max_dim) and max_dim * 2 <= max(upright)
            arr = raw.postprocess(use_camera_wb=True, half_size=small, no_auto_bright=False,
                                  output_bps=8)           # applies the flip itself
            im = Image.fromarray(arr)
    im._orig_size = upright
    return im


def load_image(path: str | Path, max_dim: int | None = None):
    """Decode an image to an EXIF-corrected PIL RGB image.

    ``max_dim`` keeps the long side at or below that size. For JPEGs this uses
    ``Image.draft`` so the decoder itself produces a smaller image, which is
    far faster than decoding 20+ megapixels and resizing afterwards.
    """
    from PIL import Image, ImageOps

    Image.MAX_IMAGE_PIXELS = 600_000_000   # allow big panoramas; still bounded
    if Path(path).suffix.lower() in RAW_EXTS:
        im = _load_raw(Path(path), max_dim)
        orig = im._orig_size
        if max_dim and max(im.size) > max_dim:
            s = max_dim / max(im.size)
            im = im.resize((max(1, int(im.width * s)), max(1, int(im.height * s))),
                           Image.BILINEAR)
        im._orig_size = orig
        return im
    im = Image.open(path)
    orig = im.size
    try:
        if im.getexif().get(0x0112, 1) in (5, 6, 7, 8):   # rotated by EXIF
            orig = (orig[1], orig[0])
    except Exception:  # noqa: BLE001
        pass
    if max_dim:
        try:
            # draft() only shrinks while BOTH sides stay >= the request, so ask for
            # the target with the photo's own aspect ratio (a square request never
            # let a 3:2 photo be reduced and forced a full 26 MP decode)
            k = int(max_dim) / max(im.size)
            im.draft("RGB", (max(1, int(im.width * k)), max(1, int(im.height * k))))
        except Exception:  # noqa: BLE001 - not a JPEG / unsupported
            pass
    im = ImageOps.exif_transpose(im)
    im = im.convert("RGB")
    if max_dim and max(im.size) > max_dim:
        s = max_dim / max(im.size)
        im = im.resize((max(1, int(im.width * s)), max(1, int(im.height * s))),
                       Image.BILINEAR)
    im._orig_size = orig          # after the resize (which returns a new image)
    return im


class _SizedArray:
    """Marker: ndarray subclass carrying the photo's true pixel size."""


def load_rgb_array(path: str | Path, max_dim: int | None = None):
    """Decode an image to an EXIF-corrected RGB numpy array.

    The array's ``_orig_size`` attribute holds the photo's full size in pixels
    (the array itself is capped at ``max_dim``).
    """
    import numpy as np

    class _Arr(np.ndarray):
        _orig_size = None

    im = load_image(path, max_dim)
    arr = np.asarray(im, dtype=np.uint8).view(_Arr)
    arr._orig_size = getattr(im, "_orig_size", None) or im.size
    return arr


def fit_image(img, box_w: int, box_h: int):
    """Return a copy of ``img`` scaled to fit inside box (aspect preserved)."""
    copy = img.copy()
    copy.thumbnail((max(1, box_w), max(1, box_h)))
    return copy


# --------------------------------------------------------------------------- #
#  Folder scanning / sorting
# --------------------------------------------------------------------------- #

def iter_images(folder: Path, recursive: bool, skip_dirs=()):
    """Yield image files in ``folder`` in a stable order.

    ``skip_dirs`` are folder *names* never descended into (e.g. the output
    folder, so already-sorted photos aren't scanned again).
    """
    skip = {str(n).lower() for n in skip_dirs if n} | {"$recycle.bin"}

    def wanted(names):
        """Image names in one folder; a RAW with a same-name JPG/PNG/... beside it is the
        companion of that photo (not a second photo), so only the non-RAW one is scanned."""
        names = [n for n in names if Path(n).suffix.lower() in IMAGE_EXTS]
        plain = {Path(n).stem.lower() for n in names if Path(n).suffix.lower() not in RAW_EXTS}
        return [n for n in names
                if Path(n).suffix.lower() not in RAW_EXTS or Path(n).stem.lower() not in plain]

    if not recursive:
        files = sorted(p.name for p in folder.iterdir() if p.is_file())
        for name in wanted(files):
            yield folder / name
        return
    for dirpath, dirnames, filenames in os.walk(folder):
        dirnames[:] = sorted(d for d in dirnames if d.lower() not in skip)
        for name in wanted(sorted(filenames)):
            yield Path(dirpath) / name


def recycle(paths) -> tuple[int, list[str]]:
    """Send files to the Windows Recycle Bin (recoverable).

    Returns ``(deleted_count, failed_paths)``. Never deletes permanently: if the
    Recycle Bin can't be used the file is left alone and reported as failed.
    """
    paths = [str(Path(p)) for p in paths]
    if not paths:
        return 0, []
    if sys.platform != "win32":
        try:
            from send2trash import send2trash  # type: ignore

            ok, bad = 0, []
            for p in paths:
                try:
                    send2trash(p)
                    ok += 1
                except Exception:  # noqa: BLE001
                    bad.append(p)
            return ok, bad
        except Exception:  # noqa: BLE001
            return 0, paths

    import ctypes
    from ctypes import wintypes

    class SHFILEOPSTRUCTW(ctypes.Structure):
        _fields_ = [("hwnd", wintypes.HWND), ("wFunc", wintypes.UINT),
                    ("pFrom", wintypes.LPCWSTR), ("pTo", wintypes.LPCWSTR),
                    ("fFlags", ctypes.c_ushort),
                    ("fAnyOperationsAborted", wintypes.BOOL),
                    ("hNameMappings", ctypes.c_void_p),
                    ("lpszProgressTitle", wintypes.LPCWSTR)]

    FO_DELETE, FOF_SILENT, FOF_NOCONFIRMATION = 3, 0x4, 0x10
    FOF_ALLOWUNDO, FOF_NOERRORUI = 0x40, 0x400
    ok, bad = 0, []
    for p in paths:       # one at a time so a failure is attributable
        if not os.path.exists(p):
            bad.append(p)
            continue
        op = SHFILEOPSTRUCTW()
        op.wFunc = FO_DELETE
        op.pFrom = os.path.abspath(p) + "\0"      # double-NUL terminated
        op.fFlags = (FOF_ALLOWUNDO | FOF_NOCONFIRMATION | FOF_SILENT
                     | FOF_NOERRORUI)
        rc = ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op))
        if rc == 0 and not op.fAnyOperationsAborted and not os.path.exists(p):
            ok += 1
        else:
            bad.append(p)
    return ok, bad


def raw_companions(path) -> list[Path]:
    """RAW files that share ``path``'s folder and name (IMG_1.JPG -> IMG_1.CR2/.DNG/...)."""
    path = Path(path)
    if path.suffix.lower() in RAW_EXTS:
        return []
    found: list[Path] = []
    try:
        for p in path.parent.iterdir():
            if (p.stem.lower() == path.stem.lower() and p.suffix.lower() in RAW_EXTS
                    and p.is_file()):
                found.append(p)
    except OSError:
        pass
    return sorted(found)


def unique_path(path: Path) -> Path:
    if not path.exists():
        return path
    parent, stem, suffix = path.parent, path.stem, path.suffix
    n = 1
    while True:
        cand = parent / f"{stem} ({n}){suffix}"
        if not cand.exists():
            return cand
        n += 1


@dataclass
class SortOutcome:
    moved: int = 0
    failed: int = 0
    manifest_path: Path | None = None
    moves: list[tuple[str, str]] = field(default_factory=list)


def sort_results(
    results,
    root: Path,
    out_name: str,
    mode: str,                      # "move" | "copy"
    recursive: bool,
    log=print,
    only_flagged: bool = True,
) -> SortOutcome:
    """Move/copy images into root/out_name (flagged only unless told otherwise)."""
    flagged = [r for r in results if r.is_flagged or not only_flagged]
    dest_root = root / out_name
    out = SortOutcome()
    for r in flagged:
        rel = r.path.relative_to(root) if root in r.path.parents else Path(r.path.name)
        sub = rel.parent if (recursive and str(rel.parent) != ".") else Path()
        dest_dir = dest_root / sub
        dest_dir.mkdir(parents=True, exist_ok=True)
        target = unique_path(dest_dir / r.path.name)
        try:
            if mode == "move":
                shutil.move(str(r.path), str(target))
            else:
                shutil.copy2(str(r.path), str(target))
            out.moved += 1
            if mode == "move":
                out.moves.append((str(r.path), str(target)))
        except Exception as exc:  # noqa: BLE001
            out.failed += 1
            log(f"  ! {r.path.name}: {exc}")

    if mode == "move" and out.moves:
        out.manifest_path = write_manifest(dest_root, out.moves)
    return out


def _atomic_write(path: Path, text: str) -> None:
    """Write via a temp file + rename so a crash can't leave a torn file."""
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def write_manifest(dest_root: Path, moves: list[tuple[str, str]]) -> Path:
    dest_root.mkdir(parents=True, exist_ok=True)
    import time

    mp = dest_root / MANIFEST_NAME
    old: list[tuple[str, str]] = []
    if mp.exists():       # keep earlier moves so they stay undoable
        try:
            old = read_manifest(mp)
        except Exception:  # noqa: BLE001
            old = []
    seen = {b for _, b in moves}
    merged = [(a, b) for a, b in old if b not in seen] + list(moves)
    data = {
        "time": time.strftime("%Y-%m-%d %H:%M:%S"),
        "moves": [{"from": a, "to": b} for a, b in merged],
    }
    _atomic_write(mp, json.dumps(data, indent=2))
    return mp


def read_manifest(manifest_path: Path) -> list[tuple[str, str]]:
    data = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    return [(m["from"], m["to"]) for m in data.get("moves", [])]


def undo_move(manifest_path: Path, log=print) -> tuple[int, int]:
    """Move files back to where the manifest says they came from."""
    pairs = read_manifest(manifest_path)
    ok = fail = 0
    remaining = []
    for src, dst in pairs:
        try:
            if not Path(dst).exists():
                fail += 1
                continue
            src_p = Path(src)
            src_p.parent.mkdir(parents=True, exist_ok=True)
            if src_p.exists():
                src_p = unique_path(src_p)
            shutil.move(dst, str(src_p))
            ok += 1
        except Exception as exc:  # noqa: BLE001
            fail += 1
            remaining.append((src, dst))
            log(f"  ! undo {dst}: {exc}")
    if Path(manifest_path).exists():
        try:
            if remaining:        # keep only entries that genuinely failed
                _atomic_write(Path(manifest_path), json.dumps(
                    {"moves": [{"from": a, "to": b} for a, b in remaining]},
                    indent=2))
            else:
                Path(manifest_path).unlink()
        except OSError:
            pass
    return ok, fail


# --------------------------------------------------------------------------- #
#  Duplicates
# --------------------------------------------------------------------------- #

def pick_score(r) -> float:
    """How good this frame is as the one to keep (higher = better).

    Everybody's eyes open matters most, then looking at the camera, a natural smile,
    no half-cut faces and a clear face; resolution only breaks ties.
    """
    sc = 0.0
    for f in r.faces:
        if f.state == "ignored":
            continue
        if f.state == "closed":
            sc -= 100.0
        elif f.state == "down":
            sc -= 8.0
        elif f.wink:
            sc -= 25.0
        if f.looks_away:
            sc -= 10.0
        if f.is_cut_off:
            sc -= 6.0
        if f.smiling and f.state == "open":
            sc += 6.0
        if f.mouth_is_open and not f.smiling:
            sc -= 2.0
    if r.verdict == "closed" and r.override == "flag":
        sc -= 100.0
    if r.unclear:
        sc -= 20.0
    sc += min(r.sharpness or 0.0, 40.0) / 4.0
    return sc


def _quality(r) -> tuple:
    """Higher = the better frame to keep inside a duplicate group."""
    return (2 if r.protected else 0, round(pick_score(r), 1),
            r.width * r.height, r.file_size)


def _why(r, best) -> str:
    """Short reason this frame was kept (best) or lost against ``best``."""
    if r is best:
        bits = []
        if r.faces and r.n_closed == 0:
            bits.append("all eyes open")
        if any(f.smiling for f in r.faces):
            bits.append("smiling")
        if not r.unclear and r.sharpness:
            bits.append("sharp")
        return ", ".join(bits) or "best overall"
    if r.n_closed > best.n_closed:
        return f"{r.n_closed} closed" if r.n_closed > 1 else "eyes closed"
    if r.has_wink and not best.has_wink:
        return "wink"
    if r.n_away > best.n_away:
        return "looking away"
    if r.unclear and not best.unclear:
        return r.focus
    if r.n_cut > best.n_cut:
        return "face cut off"
    sm = lambda x: sum(1 for f in x.faces if f.smiling)
    if sm(r) < sm(best):
        return "less smiling"
    return "lower overall"


def find_duplicates(results, max_dist: int = DUP_LEVELS["normal"]) -> int:
    """Group near-identical photos; returns the number of groups found.

    Sets ``dup_group`` / ``dup_best`` / ``dup_dist`` / ``dup_exact`` on every
    result (cleared first). The "best" frame of a group is the one with open
    eyes, a clear face and the highest resolution - the rest are the removable
    duplicates.
    """
    import numpy as np

    for r in results:
        r.dup_group, r.dup_best, r.dup_dist, r.dup_exact = None, False, 0, False
    items = [r for r in results if r.phash and len(r.phash) == 64]
    n = len(items)
    if n < 2:
        return 0
    H = np.frombuffer(b"".join(bytes.fromhex(r.phash) for r in items),
                      dtype=np.uint8).reshape(n, 32)
    pop = np.array([bin(i).count("1") for i in range(256)], dtype=np.uint8)
    ratio = np.array([(r.width / r.height) if r.height else 0.0 for r in items])
    parent = list(range(n))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for i in range(n - 1):
        dist = pop[H[i + 1:] ^ H[i]].sum(axis=1)
        close = np.nonzero((dist <= max_dist)
                           & (np.abs(ratio[i + 1:] - ratio[i]) < 0.02))[0]
        for j in close:
            ra, rb = find(i), find(i + 1 + int(j))
            if ra != rb:
                parent[rb] = ra
    groups: dict[int, list[int]] = {}
    for i in range(n):
        groups.setdefault(find(i), []).append(i)
    gid = 0
    for members in groups.values():
        if len(members) < 2:
            continue
        gid += 1
        best = max(members, key=lambda k: _quality(items[k]))
        for k in members:
            r = items[k]
            r.dup_group = gid
            r.dup_best = k == best
            d = int(pop[H[k] ^ H[best]].sum())
            r.dup_dist = d
            r.dup_exact = (k != best and d == 0
                           and r.file_size == items[best].file_size)
            r.dup_score = round(pick_score(r), 1)
            r.dup_why = _why(r, items[best])
    blink_rescue(results)
    return gid


def blink_rescue(results) -> int:
    """For photos flagged closed, find a frame of the same scene where they are open.

    Sets ``rescue`` (an ImageResult) and ``rescue_for`` (closed faces it fixes) on the
    flagged photo; returns how many photos have a better frame. People are matched
    between frames by face recognition when available, else by position.
    """
    from eyescan_people import match_faces

    groups: dict[int, list] = {}
    for r in results:
        r.rescue, r.rescue_for = None, 0
        if r.dup_group is not None:
            groups.setdefault(r.dup_group, []).append(r)
    found = 0
    for members in groups.values():
        for r in members:
            if r.verdict != "closed" or r.override == "flag":
                continue
            bad = {i for i, f in enumerate(r.faces) if f.state == "closed"}
            if not bad:
                continue
            best = None
            for c in members:
                if c is r or c.verdict in ("error", "unsupported", "no-face"):
                    continue
                pairs = match_faces(r, c)
                fixed = sum(1 for i in bad if i in pairs
                            and c.faces[pairs[i]].state in ("open", "hidden"))
                worse = sum(1 for j, f in enumerate(c.faces) if f.state == "closed"
                            and not (j in pairs.values() and any(
                                pairs.get(i) == j and i in bad for i in bad)))
                net = fixed - worse
                if fixed and net > 0:
                    key = (net, pick_score(c))
                    if best is None or key > best[0]:
                        best = (key, c, fixed)
            if best:
                r.rescue, r.rescue_for = best[1], best[2]
                found += 1
    return found


# --------------------------------------------------------------------------- #
#  (De)serialisation for the SQLite cache
# --------------------------------------------------------------------------- #

def _r4(v):
    if isinstance(v, float):
        return round(v, 4)
    if isinstance(v, (list, tuple)):
        return [_r4(x) for x in v]
    return v


def _pack_emb(v):
    if v is None:
        return None
    from eyescan_people import pack
    return pack(v)


def _unpack_emb(s):
    if not s:
        return None
    from eyescan_people import unpack
    return unpack(s)


def result_to_dict(r) -> dict:
    """JSON-safe snapshot of the *measurements* (verdicts are recomputed)."""
    faces = []
    for f in r.faces:
        faces.append({
            "bl": f.blink_left, "br": f.blink_right, "box": _r4(f.box),
            "eb": _r4(f.eye_box) if f.eye_box else None,
            "small": f.small, "out": _r4(f.outline), "eyes": _r4(f.eyes),
            "iris": _r4(f.iris), "rg": {k: _r4(v) for k, v in f.regions.items()}, "el": f.ear_left, "er": f.ear_right,
            "fs": f.face_score, "ld": f.look_down, "pi": f.pitch, "ya": f.yaw,
            "ro": f.roll, "sm": f.smile, "mo": f.mouth_open, "sh": f.sharp,
            "sq": f.squint, "gh": round(f.gaze_h, 3), "gv": round(f.gaze_v, 3),
            "ol": _r4(f.occ_left), "or": _r4(f.occ_right),
            "il": _r4(f.iris_left), "ir": _r4(f.iris_right), "gl": _r4(f.glasses),
            "fu": round(f.funny, 3), "bi": _r4(f.bindi),
            "em": _pack_emb(f.emb)})
    return {"v": r.verdict, "faces": faces, "err": r.error,
            "np": r.n_people, "nb": r.n_bodies,
            "an": [[a.label, a.score, _r4(a.box)] for a in r.animals],
            "sh": r.sharpness, "bg": r.bg_sharp, "fsz": r.file_size,
            "w": r.width, "h": r.height, "ph": r.phash}


def result_from_dict(path, d: dict):
    r = ImageResult(path=Path(path), verdict=d.get("v", "open"), error=d.get("err"))
    r.n_people, r.n_bodies = d.get("np", 0), d.get("nb", 0)
    r.animals = [AnimalResult(a[0], a[1], tuple(a[2])) for a in d.get("an", [])]
    r.sharpness, r.bg_sharp = d.get("sh"), d.get("bg")
    r.file_size, r.width, r.height = d.get("fsz", 0), d.get("w", 0), d.get("h", 0)
    r.phash = d.get("ph", "")
    for f in d.get("faces", []):
        r.faces.append(FaceResult(
            f["bl"], f["br"], False, tuple(f["box"]),
            eye_box=tuple(f["eb"]) if f.get("eb") else None,
            small=f.get("small", False),
            outline=[tuple(p) for p in f.get("out", [])],
            eyes=[[tuple(p) for p in e] for e in f.get("eyes", [])],
            iris=[tuple(p) for p in f.get("iris", [])],
            regions={k: [tuple(p) for p in v] for k, v in (f.get("rg") or {}).items()},
            ear_left=f.get("el"), ear_right=f.get("er"), face_score=f.get("fs"),
            look_down=f.get("ld", 0.0), pitch=f.get("pi"), yaw=f.get("ya"),
            roll=f.get("ro"), smile=f.get("sm", 0.0), mouth_open=f.get("mo", 0.0),
            sharp=f.get("sh"), squint=f.get("sq", 0.0),
            gaze_h=f.get("gh", 0.0), gaze_v=f.get("gv", 0.0),
            occ_left=f.get("ol"), occ_right=f.get("or"),
            iris_left=f.get("il"), iris_right=f.get("ir"), glasses=f.get("gl"),
            funny=f.get("fu", 0.0), bindi=f.get("bi"),
            emb=_unpack_emb(f.get("em"))))
    r.merge_duplicate_faces()               # older scans may hold the same face twice
    r.from_cache = True
    return r


def scan_files(engine, files, cache=None, force: bool = False, stop=None,
               workers: int | None = None):
    """Yield ``(path, ImageResult, from_cache)`` for every file.

    Photos whose size + modified-time + engine settings match the cache are
    returned instantly without touching the detector (this is what makes a
    re-scan, or resuming a stopped scan, fast). Everything else is analysed and
    stored. ``force`` ignores the cache (a true re-scan). ``workers`` is the
    number of worker processes (None = automatic, 1 = single process).
    """
    key = engine.cache_key()
    todo: list[tuple[Path, int, float]] = []
    for p in files:
        if stop is not None and stop.is_set():
            return
        try:
            st = p.stat()
        except OSError:
            todo.append((p, 0, 0.0))
            continue
        if cache is not None and not force:
            hit = cache.get(p, st.st_size, st.st_mtime, key)
            if hit is not None:
                yield p, hit, True
                continue
        todo.append((p, st.st_size, st.st_mtime))
    meta = {str(p): (sz, mt) for p, sz, mt in todo}
    if not workers:
        workers = auto_workers(len(todo))
    embedder = None
    if todo and getattr(engine, "embed_faces", False):
        from eyescan_people import get_embedder
        embedder = get_embedder()
    for p, res in engine.analyze_many([t[0] for t in todo], stop=stop,
                                      workers=workers):
        if embedder is not None:
            embedder.embed_result(res)
        else:
            for f in res.faces:
                f.crop = None
        if cache is not None and res.verdict not in ("error", "unsupported"):
            sz, mt = meta.get(str(p), (0, 0.0))
            cache.put(p, sz, mt, key, res)
        yield p, res, False


CSV_COLUMNS = ["file", "verdict", "faces", "people", "group", "closed_faces",
               "looking_down_faces", "max_blink", "min_ear", "head_pitch",
               "smile", "sharpness", "focus", "duplicate_group", "duplicate_best",
               "size_kb", "animals", "tags", "protected", "error",
               "looking_away_faces", "eyes_hidden_faces", "cut_off_faces", "people_ids",
               "burst_score", "better_frame"]


def write_csv(path, results) -> None:
    """Write one row per photo (shared by the GUI and the CLI)."""
    import csv

    with open(path, "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.writer(fh)
        w.writerow(CSV_COLUMNS)
        for r in results:
            main = max(r.faces, key=lambda f: _area(f.box), default=None)
            w.writerow([
                str(r.path), r.verdict, len(r.faces), r.people, r.group,
                r.n_closed, r.n_down, f"{r.max_blink:.3f}",
                "" if r.min_ear is None else f"{r.min_ear:.3f}",
                "" if not main or main.pitch is None else f"{main.pitch:.0f}",
                "" if not main else f"{main.smile:.2f}",
                "" if r.sharpness is None else f"{r.sharpness:.0f}", r.focus,
                "" if r.dup_group is None else r.dup_group,
                "" if r.dup_group is None else int(r.dup_best),
                round(r.file_size / 1024),
                " ".join(sorted({a.label for a in r.animals})),
                "; ".join(r.tags()), int(r.protected), r.error or "",
                r.n_away, r.n_hidden, r.n_cut,
                " ".join(str(f.person) for f in r.faces if f.person),
                "" if r.dup_group is None else f"{r.dup_score:.1f}",
                "" if r.rescue is None else str(r.rescue.path)])


class Overrides:
    """User corrections that survive re-scans (per photo / per face).

    ``image``: "keep" (never flag) or "flag" (always flag).
    ``faces``: {face index: "open" | "closed" | "ignore"}.
    """

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or (user_data_dir() / "overrides.json")
        self.data: dict[str, dict] = {}
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                self.data = raw
        except Exception:  # noqa: BLE001
            pass

    @staticmethod
    def _key(p) -> str:
        return os.path.normcase(os.path.abspath(str(p)))

    def save(self) -> None:
        try:
            _atomic_write(self.path, json.dumps(self.data, indent=1))
        except Exception:  # noqa: BLE001
            pass

    def set_image(self, p, value: str | None) -> None:
        e = self.data.setdefault(self._key(p), {})
        if value:
            e["image"] = value
        else:
            e.pop("image", None)
        self._prune(p)

    def set_protect(self, p, value: bool) -> None:
        e = self.data.setdefault(self._key(p), {})
        if value:
            e["protect"] = True
        else:
            e.pop("protect", None)
        self._prune(p)

    def set_face(self, p, idx: int, value: str | None) -> None:
        e = self.data.setdefault(self._key(p), {})
        faces = e.setdefault("faces", {})
        if value:
            faces[str(idx)] = value
        else:
            faces.pop(str(idx), None)
        if not faces:
            e.pop("faces", None)
        self._prune(p)

    def _prune(self, p) -> None:
        if not self.data.get(self._key(p)):
            self.data.pop(self._key(p), None)

    def apply(self, result) -> None:
        e = self.data.get(self._key(result.path), {})
        result.override = e.get("image")
        result.protected = bool(e.get("protect"))
        faces = e.get("faces", {})
        for i, f in enumerate(result.faces):
            f.override = faces.get(str(i))


# --------------------------------------------------------------------------- #
#  Settings
# --------------------------------------------------------------------------- #

DEFAULT_SETTINGS = {
    "folder": "",
    "threshold": DEFAULT_THRESHOLD,
    "require_both": False,
    "mode": "copy",
    "out_name": "Closed Eyes",
    "recursive": False,
    "robust": True,
    "deep": False,
    "keep_down": True,
    "ear_down": 0.13,
    "dup_enabled": True,
    "dup_level": "normal",
    "use_cache": True,
    "workers": 0,                 # scan worker processes: 0 = automatic
    "auto_face_zoom": True,       # zoom the preview onto the face when a photo is selected
    "face_zoom_fill": 0.6,        # ... filling this share of the view
    "overlay_bar": True,          # Box / Mesh / Off buttons on the preview
    "people": True,               # recognise people (face-embedding model, GPU when available)
    "gpu_device": "auto",         # ONNX device: "auto" (best GPU, else CPU) | "cpu" | GPU name
    "gpu_bench": {},              # cached adapter benchmark (see eyescan_gpu)
    "box_thickness": 3,
    "box_label_size": 13,
    "box_labels": True,
    "box_colors": {"closed": "#E88D78", "down": "#ECD489", "open": "#97B881",
                   "animal": "#8FB8CA", "ignored": "#9ca3af"},
    "animals": True,
    "sheet_ratio": "3:2",
    "sheet_mode": "photos",
    "sheet_long": 6000,
    "thumb_size": 160,
    "overlay": "box",
    "theme": "dark",
    "view": "grid",
    "pane_filters": True,         # left filter list visible (collapses on a narrow window)
    "pane_inspector": True,       # right inspector visible
    "pane_auto": True,            # panes auto-fit the window until the user picks a layout
    "recent": [],
    "geometry": "",
}


class Settings:
    """Tiny JSON-backed settings store in %LOCALAPPDATA%\\EyeScan."""

    MAX_RECENT = 10

    def __init__(self) -> None:
        self.path = user_data_dir() / "settings.json"
        self.data = dict(DEFAULT_SETTINGS)
        self.load()

    def load(self) -> None:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                self.data.update({k: raw[k] for k in raw if k in DEFAULT_SETTINGS
                                  or k == "recent"})
        except Exception:  # noqa: BLE001
            pass

    def save(self) -> None:
        try:
            _atomic_write(self.path, json.dumps(self.data, indent=2))
        except Exception:  # noqa: BLE001
            pass

    def __getitem__(self, key):
        return self.data.get(key, DEFAULT_SETTINGS.get(key))

    def __setitem__(self, key, value) -> None:
        self.data[key] = value

    def get(self, key, default=None):
        return self.data.get(key, default)

    def add_recent(self, folder: str) -> None:
        folder = str(folder)
        rec = [r for r in self.data.get("recent", []) if r.lower() != folder.lower()]
        rec.insert(0, folder)
        self.data["recent"] = rec[: self.MAX_RECENT]

