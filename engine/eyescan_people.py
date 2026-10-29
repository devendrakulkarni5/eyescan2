"""
EyeScan - who is in the photo.

Face recognition with the SFace network (OpenCV Zoo, Apache-2.0) through
``eyescan_gpu.OnnxModel``, so it runs on the GPU (DirectML) when there is one and
on the CPU otherwise. This is the one model in the scan that uses the GPU.

* ``align_crop``     - 112x112 aligned face from the mesh landmarks (done in the scan
                       workers, so only a 37 KB crop travels to the main process)
* ``FaceEmbedder``   - crop -> 128-d unit vector
* ``cluster_people`` - group faces into people across all photos
* ``match_faces``    - which face in photo B is the same person as a face in photo A

Everything is optional: with no model, no onnxruntime or no usable face the rest of
EyeScan works exactly as before.
"""

from __future__ import annotations

import base64

EMBED_FILENAME = "face_recognition_sface_2021dec.onnx"
EMBED_MIN_BYTES = 5_000_000
EMBED_SIZE = 112

# ArcFace 5-point template for a 112 x 112 crop:
# image-left eye, image-right eye, nose tip, image-left / image-right mouth corner
TEMPLATE = ((38.2946, 51.6963), (73.5318, 51.5014), (56.0252, 71.7366),
            (41.5493, 92.3655), (70.7299, 92.2041))
# mesh landmarks: eye-corner pairs (outer, inner) of each eye, nose tip, mouth corners
_EYE_A = (33, 133)       # the eye that appears on the image-left
_EYE_B = (263, 362)
_NOSE, _MOUTH_A, _MOUTH_B = 1, 61, 291

MIN_FACE_PX = 48         # smaller faces are too coarse to recognise
MAX_YAW = 50.0           # profile faces don't embed reliably
SAME_PERSON = 0.42       # cosine similarity: same person at or above (SFace paper: 0.363)
MERGE_PERSON = 0.50      # clusters whose centres are this close are merged
MATCH_MIN = 0.36         # photo-to-photo match of one person inside a burst
FLIP_TTA = True          # embed the face and its mirror image and average: steadier on turned faces


def available(path=None) -> bool:
    from eyescan_core import find_model
    return bool(path or find_model(EMBED_FILENAME, EMBED_MIN_BYTES))


def _similarity(src, dst):
    """Least-squares similarity transform (scale, rotation, shift) src -> dst."""
    import numpy as np
    src = np.asarray(src, float)
    dst = np.asarray(dst, float)
    ms, md = src.mean(0), dst.mean(0)
    s, d = src - ms, dst - md
    var = (s ** 2).sum()
    if var < 1e-9:
        return None
    u, sig, vt = np.linalg.svd(d.T @ s)
    sign = np.eye(2)
    if np.linalg.det(u) * np.linalg.det(vt) < 0:
        sign[1, 1] = -1
    r = u @ sign @ vt
    scale = (sig * np.diag(sign)).sum() / var
    t = md - scale * r @ ms
    return scale * r, t


def align_crop(rgb, pts, yaw=None) -> bytes | None:
    """The face at normalized mesh ``pts`` as a 112x112 RGB crop (raw bytes) or None."""
    import numpy as np
    from PIL import Image

    if not pts or len(pts) <= 363:
        return None
    if yaw is not None and abs(yaw) > MAX_YAW:
        return None
    H, W = rgb.shape[:2]

    def at(i):
        return np.array([pts[i][0] * W, pts[i][1] * H])

    def mid(pair):
        return (at(pair[0]) + at(pair[1])) / 2.0

    src = [mid(_EYE_A), mid(_EYE_B), at(_NOSE), at(_MOUTH_A), at(_MOUTH_B)]
    if src[0][0] > src[1][0]:                     # mirrored mesh: keep image-left first
        src[0], src[1], src[3], src[4] = src[1], src[0], src[4], src[3]
    eye_dist = float(np.hypot(*(src[1] - src[0])))
    if eye_dist < MIN_FACE_PX * 0.32:
        return None
    fit = _similarity(src, TEMPLATE)
    if fit is None:
        return None
    m, t = fit
    inv = np.linalg.inv(m)
    off = -inv @ t
    # work on a padded window only - the photo itself can be 20+ MB
    xs = [p[0] for p in src]
    ys = [p[1] for p in src]
    pad = eye_dist * 2.2
    x0, y0 = int(max(0, min(xs) - pad)), int(max(0, min(ys) - pad))
    x1, y1 = int(min(W, max(xs) + pad)), int(min(H, max(ys) + pad))
    if x1 - x0 < 8 or y1 - y0 < 8:
        return None
    win = Image.fromarray(np.ascontiguousarray(rgb[y0:y1, x0:x1]))
    off = off - np.array([x0, y0]) @ np.eye(2)
    # PIL wants the output->input map: x_in = a*x + b*y + c ...
    coeffs = (inv[0, 0], inv[0, 1], off[0] , inv[1, 0], inv[1, 1], off[1])
    out = win.transform((EMBED_SIZE, EMBED_SIZE), Image.AFFINE, coeffs,
                        resample=Image.BILINEAR)
    return out.tobytes()


class FaceEmbedder:
    """SFace on the GPU (or CPU). One instance per process; ``embed`` is thread-safe."""

    def __init__(self, model_path=None, prefer_gpu: bool | None = None) -> None:
        from eyescan_core import find_model
        from eyescan_gpu import OnnxModel
        path = model_path or find_model(EMBED_FILENAME, EMBED_MIN_BYTES)
        if path is None:
            raise FileNotFoundError(EMBED_FILENAME)
        self.model = OnnxModel(path, prefer_gpu)
        self._in = self.model.input_names[0]
        self.count = 0

    @property
    def provider(self) -> str:
        return self.model.provider

    @property
    def device_name(self) -> str:
        if not self.model.on_gpu:
            return "CPU"
        from eyescan_gpu import adapters
        dev = self.model.device
        for a in adapters():
            if a["index"] == dev:
                return a["name"]
        return "GPU"

    def embed(self, crop: bytes):
        """128-d unit vector for a 112x112 RGB crop, or None."""
        import numpy as np
        if not crop or len(crop) != EMBED_SIZE * EMBED_SIZE * 3:
            return None
        x = np.frombuffer(crop, np.uint8).reshape(EMBED_SIZE, EMBED_SIZE, 3)
        views = [x, x[:, ::-1]] if FLIP_TTA else [x]
        total = None
        for img in views:
            a = np.ascontiguousarray(img.transpose(2, 0, 1)[None]).astype(np.float32)   # RGB, 0..255, NCHW
            v = np.asarray(self.model.run({self._in: a})[0], np.float32).reshape(-1)
            n = float(np.linalg.norm(v))
            if n > 1e-6:
                total = v / n if total is None else total + v / n
        self.count += 1
        if total is None:
            return None
        n = float(np.linalg.norm(total))
        return total / n if n > 1e-6 else None

    def embed_result(self, res) -> int:
        """Embed every face of an ImageResult that carries a crop; returns how many."""
        n = 0
        for f in res.faces:
            crop, f.crop = getattr(f, "crop", None), None
            if crop is None or f.emb is not None:
                continue
            try:
                f.emb = self.embed(crop)
            except Exception:  # noqa: BLE001 - one bad face never stops a scan
                f.emb = None
            n += f.emb is not None
        return n


_EMBEDDER = None
_EMBEDDER_FAILED = False


def get_embedder():
    """The shared embedder, or None when the model / runtime is unavailable."""
    global _EMBEDDER, _EMBEDDER_FAILED
    if _EMBEDDER is None and not _EMBEDDER_FAILED:
        try:
            _EMBEDDER = FaceEmbedder()
        except Exception:  # noqa: BLE001
            _EMBEDDER_FAILED = True
    return _EMBEDDER


def reset_embedder() -> None:
    """Rebuild on the next use (the user changed the processing device)."""
    global _EMBEDDER, _EMBEDDER_FAILED
    _EMBEDDER, _EMBEDDER_FAILED = None, False


# --------------------------------------------------------------------------- #
#  Cache encoding: 128 x int8 -> 172 base64 chars
# --------------------------------------------------------------------------- #

def pack(v) -> str | None:
    if v is None:
        return None
    import numpy as np
    q = np.clip(np.round(np.asarray(v, np.float32) * 200.0), -127, 127).astype(np.int8)
    return base64.b64encode(q.tobytes()).decode("ascii")


def unpack(s):
    if not s:
        return None
    import numpy as np
    try:
        q = np.frombuffer(base64.b64decode(s), np.int8).astype(np.float32)
    except Exception:  # noqa: BLE001
        return None
    n = float(np.linalg.norm(q))
    return q / n if n > 1e-6 else None


# --------------------------------------------------------------------------- #
#  Clustering / matching
# --------------------------------------------------------------------------- #

def cosine(a, b) -> float:
    if a is None or b is None:
        return -1.0
    return float(a @ b)


def cluster_people_v1(results, same: float = SAME_PERSON, merge: float = MERGE_PERSON):
    """Give every face with an embedding a ``person`` number (1 = most photos).

    Returns ``[{"id", "faces", "photos", "best": (result, face)}]`` sorted by id.
    Leader clustering on cosine similarity, then clusters whose centres nearly
    coincide are merged (one person photographed from two angles).
    """
    import numpy as np

    items = [(r, f) for r in results for f in r.faces
             if getattr(f, "emb", None) is not None and f.state != "ignored"]
    for r in results:
        for f in r.faces:
            f.person = None
    if not items:
        return []
    X = np.stack([f.emb for _r, f in items]).astype(np.float32)
    # bigger, sharper faces first: they make the best cluster centres
    order = sorted(range(len(items)), key=lambda i: -(items[i][1].box[2] - items[i][1].box[0]))
    centers: list = []          # running sums
    counts: list[int] = []
    label = [-1] * len(items)
    for i in order:
        if centers:
            C = np.stack(centers)
            C = C / np.maximum(np.linalg.norm(C, axis=1, keepdims=True), 1e-6)
            sims = C @ X[i]
            j = int(np.argmax(sims))
            if sims[j] >= same:
                label[i] = j
                centers[j] = centers[j] + X[i]
                counts[j] += 1
                continue
        centers.append(X[i].copy())
        counts.append(1)
        label[i] = len(centers) - 1
    # merge near-identical centres
    parent = list(range(len(centers)))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    if len(centers) > 1:
        C = np.stack(centers)
        C = C / np.maximum(np.linalg.norm(C, axis=1, keepdims=True), 1e-6)
        S = C @ C.T
        for a in range(len(centers)):
            for b in range(a + 1, len(centers)):
                if S[a, b] >= merge:
                    ra, rb = find(a), find(b)
                    if ra != rb:
                        parent[rb] = ra
    groups: dict[int, list[int]] = {}
    for i, l in enumerate(label):
        groups.setdefault(find(l), []).append(i)
    # a person seen once is still a person; number by how many photos they are in
    people = []
    for members in groups.values():
        photos = {id(items[i][0]) for i in members}
        people.append((len(photos), len(members), members))
    people.sort(key=lambda t: (-t[0], -t[1]))
    out = []
    for pid, (nphotos, nfaces, members) in enumerate(people, 1):
        best = max(members, key=lambda i: ((items[i][1].sharp or 0.0),
                                           items[i][1].box[2] - items[i][1].box[0]))
        for i in members:
            items[i][1].person = pid
        out.append({"id": pid, "faces": nfaces, "photos": nphotos,
                    "best": items[best]})
    return out


# --------------------------------------------------------------------------- #
#  Quality-aware clustering (EyeScan 2)
# --------------------------------------------------------------------------- #
# v1 grew clusters from a running sum of every face, biggest first, so a blurry or turned face could drag
# a centre off, small faces became one-photo "people", and a person photographed from two angles stayed
# split. v2 weights faces by quality, compares against several exemplar faces per person (pose changes),
# merges clusters by average linkage, and attaches low-quality faces to a person only when they match
# clearly (otherwise they are left unassigned instead of inventing new people).

Q_SEED = 0.35            # faces below this quality never start or steer a person
EXEMPLARS = 8            # diverse best faces kept per person
EXEMPLAR_DUP = 0.90      # an exemplar this similar to a kept one adds nothing


def face_quality(r, f) -> float:
    """0..1: how reliable this face's embedding is (size, pose, detection, sharpness, visible eyes)."""
    w = max(f.box[2] - f.box[0], 1e-6) * (getattr(r, "width", 0) or 1000)
    h = max(f.box[3] - f.box[1], 1e-6) * (getattr(r, "height", 0) or 1000)
    size_q = min(1.0, min(w, h) / 150.0)
    pose_q = max(0.25, 1.0 - abs(f.yaw or 0.0) / 70.0)
    fs = min(1.0, max(0.3, f.face_score if f.face_score is not None else 0.85))
    sharp_q = 1.0 if f.sharp is None else min(1.0, 0.4 + f.sharp / 12.0)
    hidden = 0.75 if getattr(f, "eyes_hidden", False) else 1.0
    return size_q * pose_q * fs * sharp_q * hidden


def _unit(v):
    import numpy as np
    n = float(np.linalg.norm(v))
    return v / n if n > 1e-6 else v


def _pick_exemplars(idx, X, Q, k=EXEMPLARS):
    """Best-quality faces that differ from each other (frontal, turned, smiling ... all represented)."""
    import numpy as np
    chosen: list[int] = []
    for i in sorted(idx, key=lambda i: -Q[i]):
        if not chosen or float(np.max(X[chosen] @ X[i])) < EXEMPLAR_DUP:
            chosen.append(i)
        if len(chosen) >= k:
            break
    return chosen or list(idx)[:1]


def person_score(x, centre, exemplars) -> float:
    """Similarity of one face to a person: half centre, half best exemplar."""
    return 0.5 * (float(centre @ x) + float(np_max(exemplars @ x)))


def np_max(a):
    import numpy as np
    return float(np.max(a))


def link_score(ca, ea, cb, eb) -> float:
    """Average linkage between two people: the better of centre-to-centre and the mean of the three
    closest exemplar pairs (so two angles of one person still link)."""
    import numpy as np
    S = (ea @ eb.T).ravel()
    top = np.sort(S)[-3:]
    return max(float(ca @ cb), float(top.mean()))


def cluster_people(results, same: float = SAME_PERSON, merge: float = MERGE_PERSON):
    """Group faces into people. Sets ``face.person`` (1 = most photos); faces too poor to place stay None.

    Returns ``[{"id", "faces", "photos", "best": (result, face), "centroid", "exemplars", "quality"}]``.
    """
    import numpy as np

    items = [(r, f) for r in results for f in r.faces
             if getattr(f, "emb", None) is not None and f.state != "ignored"]
    for r in results:
        for f in r.faces:
            f.person = None
    if not items:
        return []
    X = np.stack([f.emb for _r, f in items]).astype(np.float32)
    Q = [face_quality(r, f) for r, f in items]
    order = sorted(range(len(items)), key=lambda i: -Q[i])

    photo = [id(r) for r, _f in items]      # two faces of one photo are two different people

    cl: list[dict] = []     # {"m": [idx], "sum": vec, "ex": [idx], "ph": {photo ids}}

    def centre(c):
        return _unit(c["sum"])

    def exm(c):
        return X[c["ex"]]

    def make(members):
        s = sum(Q[i] * X[i] for i in members)
        return {"m": list(members), "sum": s, "ex": _pick_exemplars(members, X, Q),
                "ph": {photo[i] for i in members}}

    def best_cluster(i):
        best, bs = -1, -1.0
        for j, c in enumerate(cl):
            if photo[i] in c["ph"]:
                continue
            s = person_score(X[i], centre(c), exm(c))
            if s > bs:
                best, bs = j, s
        return best, bs

    low: list[int] = []
    for i in order:
        if Q[i] < Q_SEED:
            low.append(i)
            continue
        best, bs = best_cluster(i)
        if best >= 0 and bs >= same:
            c = cl[best]
            c["m"].append(i)
            c["ph"].add(photo[i])
            c["sum"] = c["sum"] + Q[i] * X[i]
            if len(c["ex"]) < EXEMPLARS and float(np.max(X[c["ex"]] @ X[i])) < EXEMPLAR_DUP:
                c["ex"].append(i)
        else:
            cl.append({"m": [i], "sum": Q[i] * X[i].copy(), "ex": [i], "ph": {photo[i]}})

    # merge people that are the same person (average linkage), strongest link first;
    # never when they appear together in a photo (they cannot be one person)
    cache: dict = {}

    def link(a, b):
        key = (id(cl[a]), id(cl[b]))
        if key not in cache:
            cache[key] = link_score(centre(cl[a]), exm(cl[a]), centre(cl[b]), exm(cl[b]))
        return cache[key]

    while len(cl) > 1:
        best, pair = merge, None
        for a in range(len(cl)):
            for b in range(a + 1, len(cl)):
                if cl[a]["ph"] & cl[b]["ph"]:
                    continue
                s = link(a, b)
                if s >= best:
                    best, pair = s, (a, b)
        if pair is None:
            break
        a, b = pair
        cl[a] = make(cl[a]["m"] + cl[b]["m"])
        del cl[b]
        cache.clear()

    # refinement: with settled people, re-place every face (order no longer matters), one slot per
    # person per photo, and let faces too poor to seed a person join only on a clear match
    for _round in range(2):
        if not cl:
            break
        slots: dict[int, list[int]] = {}
        for i in range(len(items)):
            slots.setdefault(photo[i], []).append(i)
        members: list[list[int]] = [[] for _ in cl]
        cents = [centre(c) for c in cl]
        exs = [exm(c) for c in cl]
        for faces in slots.values():
            cand = []
            for i in faces:
                need = same if Q[i] >= Q_SEED else same + 0.04
                for j in range(len(cl)):
                    s = person_score(X[i], cents[j], exs[j])
                    if s >= need:
                        cand.append((s, i, j))
            cand.sort(reverse=True)
            done_f, done_p = set(), set()
            for s, i, j in cand:
                if i not in done_f and j not in done_p:
                    members[j].append(i)
                    done_f.add(i)
                    done_p.add(j)
            # a good face that fits nobody keeps its previous person rather than vanishing
            for i in faces:
                if i not in done_f and Q[i] >= Q_SEED:
                    for j, c in enumerate(cl):
                        if i in c["m"] and j not in done_p:
                            members[j].append(i)
                            done_p.add(j)
                            break
        moved = any(set(m) != set(c["m"]) for m, c in zip(members, cl))
        cl = [make(m) for m in members if m]
        if not moved:
            break

    people = []
    for c in cl:
        photos = {id(items[i][0]) for i in c["m"]}
        people.append((len(photos), len(c["m"]), c))
    people.sort(key=lambda t: (-t[0], -t[1]))
    out = []
    for pid, (nphotos, nfaces, c) in enumerate(people, 1):
        members = c["m"]
        best = max(members, key=lambda i: Q[i])
        for i in members:
            items[i][1].person = pid
        out.append({"id": pid, "faces": nfaces, "photos": nphotos, "best": items[best],
                    "centroid": centre(c).astype(np.float32),
                    "exemplars": [X[i].copy() for i in c["ex"]],
                    "quality": float(np.mean([Q[i] for i in members]))})
    return out


def suggest_merges(clusters, lo: float = 0.30, hi: float = MERGE_PERSON, limit: int = 30):
    """Pairs of people that might be the same person but were not merged automatically."""
    import numpy as np
    out = []
    for a in range(len(clusters)):
        for b in range(a + 1, len(clusters)):
            A, B = clusters[a], clusters[b]
            if A.get("centroid") is None or B.get("centroid") is None:
                continue
            s = link_score(A["centroid"], np.stack(A["exemplars"]), B["centroid"], np.stack(B["exemplars"]))
            if lo <= s < hi:
                out.append({"a": A["id"], "b": B["id"], "sim": round(s, 3)})
    out.sort(key=lambda t: -t["sim"])
    return out[:limit]



def match_faces(a, b, min_sim: float = MATCH_MIN):
    """Pair faces of two photos of the same scene: ``{index in a: index in b}``.

    Uses face recognition when both faces have an embedding, else the position of
    the face in the frame (a burst is shot from one spot).
    """
    pairs = []
    for i, fa in enumerate(a.faces):
        for j, fb in enumerate(b.faces):
            ea, eb = getattr(fa, "emb", None), getattr(fb, "emb", None)
            cax, cay = (fa.box[0] + fa.box[2]) / 2, (fa.box[1] + fa.box[3]) / 2
            cbx, cby = (fb.box[0] + fb.box[2]) / 2, (fb.box[1] + fb.box[3]) / 2
            size = max(fa.box[2] - fa.box[0], fb.box[2] - fb.box[0], 1e-6)
            dist = ((cax - cbx) ** 2 + (cay - cby) ** 2) ** 0.5 / size
            if ea is not None and eb is not None:
                sim = cosine(ea, eb)
                if sim < min_sim or dist > 6.0:
                    continue
                score = sim - 0.02 * dist
            else:
                if dist > 1.2:
                    continue
                score = 0.5 - 0.3 * dist
            pairs.append((score, i, j))
    pairs.sort(reverse=True)
    used_a, used_b, out = set(), set(), {}
    for _s, i, j in pairs:
        if i in used_a or j in used_b:
            continue
        out[i] = j
        used_a.add(i)
        used_b.add(j)
    return out
