"""
EyeScan - GPU acceleration for ONNX models (ONNX Runtime + DirectML).

DirectML runs on any DirectX 12 GPU on Windows 10/11 (NVIDIA, AMD, Intel, Arm),
with no CUDA / driver SDK to install. Everything here degrades to the CPU:

  * onnxruntime not installed            -> OnnxModel raises, callers skip the feature
  * DirectML provider missing / no DX12  -> CPU session
  * no GPU is faster than the CPU        -> CPU session
  * the GPU fails while running a model  -> that model silently switches to CPU

Which device runs a model is the user's choice (Settings): "automatic" (default),
one named GPU, or "CPU only". Automatic walks the adapters in priority order -
discrete NVIDIA, discrete AMD, other discrete, then integrated - and takes the first
one that a small benchmark shows to be clearly faster than the CPU. That check
matters: hybrid laptops list the Intel iGPU as DirectML device 0 and the real GPU
as device 1, and the iGPU is often *slower than the CPU*. The benchmark is cached in
the settings and re-run only when the adapters or ONNX Runtime change.

The MediaPipe models (FaceLandmarker, BlazeFace, EfficientDet) are TFLite and
cannot use DirectML; models loaded through ``OnnxModel`` can.
"""

from __future__ import annotations

import threading
import time

GPU_MIN_SPEEDUP = 1.25               # a GPU must beat the CPU by this much to be used


# --------------------------------------------------------------------------- #
#  Minimal ONNX writer (so the benchmark needs no `onnx` package)
# --------------------------------------------------------------------------- #

def _varint(n: int) -> bytes:
    n &= (1 << 64) - 1
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        if n:
            out.append(b | 0x80)
        else:
            out.append(b)
            return bytes(out)


def _key(field: int, wire: int) -> bytes:
    return _varint((field << 3) | wire)


def _vint(field: int, n: int) -> bytes:
    return _key(field, 0) + _varint(n)


def _msg(field: int, payload: bytes) -> bytes:
    return _key(field, 2) + _varint(len(payload)) + payload


def _str(field: int, s: str) -> bytes:
    return _msg(field, s.encode())


def _node(op: str, ins, outs, ints: dict | None = None) -> bytes:
    b = b"".join(_str(1, i) for i in ins) + b"".join(_str(2, o) for o in outs)
    b += _str(4, op)
    for name, vals in (ints or {}).items():            # AttributeProto INTS
        b += _msg(5, _str(1, name) + _vint(20, 7)
                  + b"".join(_vint(8, v) for v in vals))
    return b


def _tensor(name: str, arr) -> bytes:
    import numpy as np

    arr = np.ascontiguousarray(arr, dtype=np.float32)
    return (b"".join(_vint(1, d) for d in arr.shape) + _vint(2, 1)
            + _str(8, name) + _msg(9, arr.tobytes()))


def _value_info(name: str, shape=None) -> bytes:
    t = _vint(1, 1)                                     # elem_type FLOAT
    if shape is not None:
        t += _msg(2, b"".join(_msg(1, _vint(1, d)) for d in shape))
    return _str(1, name) + _msg(2, _msg(1, t))


def build_probe_model(batch: int = 8, size: int = 96) -> bytes:
    """A small random-weight conv net (~0.2 GFLOP): a stand-in for a face model."""
    import numpy as np

    rng = np.random.default_rng(7)
    stages = [(3, 16, 2), (16, 64, 2), (64, 64, 1)]
    nodes, inits, cur = [], [], "x"
    for i, (cin, cout, stride) in enumerate(stages):
        w = (rng.standard_normal((cout, cin, 3, 3)) * 0.05).astype(np.float32)
        inits += [_tensor(f"w{i}", w), _tensor(f"b{i}", np.zeros(cout, np.float32))]
        nodes.append(_node("Conv", [cur, f"w{i}", f"b{i}"], [f"c{i}"],
                           {"pads": [1, 1, 1, 1], "strides": [stride, stride]}))
        nodes.append(_node("Relu", [f"c{i}"], [f"r{i}"]))
        cur = f"r{i}"
    nodes.append(_node("GlobalAveragePool", [cur], ["y"]))
    graph = (b"".join(_msg(1, n) for n in nodes) + _str(2, "probe")
             + b"".join(_msg(5, t) for t in inits)
             + _msg(11, _value_info("x", [batch, 3, size, size]))
             + _msg(12, _value_info("y")))
    opset = _msg(8, _vint(2, 13))
    return _vint(1, 8) + _str(2, "eyescan") + _msg(7, graph) + opset


# --------------------------------------------------------------------------- #
#  Adapters (DXGI) - the same list and order DirectML's ``device_id`` indexes
# --------------------------------------------------------------------------- #

VENDORS = {0x10DE: "NVIDIA", 0x1002: "AMD", 0x1022: "AMD", 0x8086: "Intel",
           0x5143: "Qualcomm", 0x1414: "Microsoft"}
VENDOR_ORDER = {"NVIDIA": 0, "AMD": 1, "Intel": 2}      # then everything else
DISCRETE_MIN_MB = 512                                   # dedicated VRAM of a discrete GPU

AUTO = "auto"          # best GPU by priority, else the CPU (the default)
CPU = "cpu"            # never use a GPU
# any other mode is an adapter name, e.g. "NVIDIA GeForce 840M"


def adapters() -> list[dict]:
    """Hardware display adapters in DirectML device order.

    Each is ``{"index", "name", "vendor", "vram_mb", "discrete", "rank"}``; ``rank``
    is the automatic priority (lower = tried first): discrete NVIDIA, discrete AMD,
    other discrete, then integrated GPUs. Empty when DXGI is unavailable.
    """
    out: list[dict] = []
    try:
        import ctypes
        from ctypes import POINTER, WINFUNCTYPE, Structure, byref, c_size_t, c_uint, c_void_p

        class GUID(Structure):
            _fields_ = [("a", ctypes.c_ulong), ("b", ctypes.c_ushort), ("c", ctypes.c_ushort),
                        ("d", ctypes.c_ubyte * 8)]

        class LUID(Structure):
            _fields_ = [("lo", ctypes.c_ulong), ("hi", ctypes.c_long)]

        class DESC1(Structure):
            _fields_ = [("Description", ctypes.c_wchar * 128), ("VendorId", c_uint),
                        ("DeviceId", c_uint), ("SubSysId", c_uint), ("Revision", c_uint),
                        ("Dedicated", c_size_t), ("DedicatedSys", c_size_t),
                        ("Shared", c_size_t), ("Luid", LUID), ("Flags", c_uint)]

        def vcall(obj, idx, restype, *argtypes):         # COM vtable method
            table = ctypes.cast(ctypes.cast(obj, POINTER(c_void_p))[0], POINTER(c_void_p))
            return WINFUNCTYPE(restype, c_void_p, *argtypes)(table[idx])

        iid = GUID(0x770AAE78, 0xF26F, 0x4DBA, (ctypes.c_ubyte * 8)(
            0xA8, 0x29, 0x25, 0x3C, 0x83, 0xD1, 0xB3, 0x87))      # IDXGIFactory1
        factory = c_void_p()
        if ctypes.windll.dxgi.CreateDXGIFactory1(byref(iid), byref(factory)) != 0:
            return []
        try:
            i = 0
            while True:
                ad = c_void_p()
                if vcall(factory, 12, ctypes.c_long, c_uint, POINTER(c_void_p))(
                        factory, i, byref(ad)) != 0:
                    break
                i += 1
                try:
                    d = DESC1()
                    vcall(ad, 10, ctypes.c_long, POINTER(DESC1))(ad, byref(d))
                finally:
                    vcall(ad, 2, ctypes.c_ulong)(ad)         # Release
                if d.Flags & 2:                              # DXGI_ADAPTER_FLAG_SOFTWARE
                    continue
                vendor = VENDORS.get(d.VendorId, "Other")
                mb = int(d.Dedicated // (1024 * 1024))
                discrete = mb >= DISCRETE_MIN_MB
                out.append({"index": len(out), "name": d.Description.strip(), "vendor": vendor,
                            "vram_mb": mb, "discrete": discrete,
                            "rank": (0 if discrete else 10) + VENDOR_ORDER.get(vendor, 3)})
        finally:
            vcall(factory, 2, ctypes.c_ulong)(factory)
    except Exception:  # noqa: BLE001 - not Windows / no DXGI
        return out
    return out


def gpu_names() -> list[str]:
    return [a["name"] for a in adapters()]


# --------------------------------------------------------------------------- #
#  Runtime + sessions
# --------------------------------------------------------------------------- #

def _ort():
    try:
        import onnxruntime as ort  # type: ignore
        ort.set_default_logger_severity(3)      # errors only: no console spam
        return ort
    except Exception:  # noqa: BLE001 - not installed / DLL problem
        return None


def providers_available() -> list[str]:
    ort = _ort()
    return list(ort.get_available_providers()) if ort else []


def directml_available() -> bool:
    return "DmlExecutionProvider" in providers_available()


def _make_session(model, device: int | None):
    """InferenceSession on DirectML ``device`` (None = CPU); raises if it can't."""
    ort = _ort()
    if device is None:
        return ort.InferenceSession(model, providers=["CPUExecutionProvider"])
    so = ort.SessionOptions()
    so.enable_mem_pattern = False                        # required by DirectML
    so.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
    sess = ort.InferenceSession(
        model, so, providers=[("DmlExecutionProvider", {"device_id": device}),
                              "CPUExecutionProvider"])
    if sess.get_providers()[0] != "DmlExecutionProvider":
        raise RuntimeError("DirectML provider did not load")
    return sess


def _time_model(data: bytes, device: int | None, x, seconds: float = 0.4):
    """-> (ms per batch, output) for one adapter, or None if it can't run it."""
    try:
        sess = _make_session(data, device)
        out = sess.run(None, {"x": x})[0]               # warm-up / compile
        n, t = 0, time.perf_counter()
        while time.perf_counter() - t < seconds or n < 3:
            sess.run(None, {"x": x})
            n += 1
        return (time.perf_counter() - t) / n * 1000.0, out
    except Exception:  # noqa: BLE001
        return None


def benchmark() -> dict:
    """Time the CPU and every DirectML adapter. Independent of the user's choice.

    ``ms`` maps "cpu" / "0" / "1"... -> ms for a batch of 8 (missing = that adapter
    failed or gave wrong results). ``adapters`` is the DXGI list it was measured on.
    """
    import numpy as np

    ads = adapters()
    res: dict = {"onnxruntime": None, "directml": False, "adapters": ads,
                 "ms": {}, "error": None}
    ort = _ort()
    if ort is None:
        res["error"] = "onnxruntime is not installed"
        return res
    res["onnxruntime"] = ort.__version__
    res["directml"] = "DmlExecutionProvider" in ort.get_available_providers()
    data = build_probe_model()
    x = np.random.default_rng(0).standard_normal((8, 3, 96, 96)).astype(np.float32)
    cpu = _time_model(data, None, x)
    if cpu is None:
        res["error"] = "ONNX Runtime cannot run models on this PC"
        return res
    res["ms"]["cpu"] = round(cpu[0], 2)
    if not res["directml"]:
        return res
    for a in ads:
        got = _time_model(data, a["index"], x)
        if got is None:
            continue
        if not np.allclose(cpu[1], got[1], atol=1e-2):
            res["error"] = f"{a['name']} gave different results - ignored"
            continue
        res["ms"][str(a["index"])] = round(got[0], 2)
    return res


def select(bench: dict, mode: str = AUTO) -> int | None:
    """DirectML adapter index to use for ``mode`` (None = the CPU).

    * ``cpu``    : never a GPU.
    * adapter name: that GPU if it works - the user's explicit choice, even when
      the benchmark says the CPU is faster. If it's gone or failed, falls to auto.
    * ``auto``   : adapters in priority order (discrete NVIDIA, discrete AMD, other
      discrete, then integrated); the first that works AND beats the CPU by
      ``GPU_MIN_SPEEDUP`` wins, else the CPU. (An iGPU slower than the CPU is skipped.)
    """
    ms = bench.get("ms", {})
    if mode == CPU or not bench.get("directml") or "cpu" not in ms:
        return None
    ads = bench.get("adapters", [])
    if mode not in (AUTO, ""):
        for a in ads:
            if a["name"] == mode and str(a["index"]) in ms:
                return a["index"]
    for a in sorted(ads, key=lambda a: (a["rank"], a["index"])):
        t = ms.get(str(a["index"]))
        if t is not None and t * GPU_MIN_SPEEDUP <= ms["cpu"]:
            return a["index"]
    return None


# --------------------------------------------------------------------------- #
#  Global state: the user's choice + the cached benchmark
# --------------------------------------------------------------------------- #

_mode = AUTO
_bench: dict | None = None
_lock = threading.RLock()


def set_mode(mode: str | None) -> None:
    """``auto`` (default), ``cpu``, or an adapter name from ``adapters()``."""
    global _mode
    _mode = mode or AUTO


def get_mode() -> str:
    return _mode


def set_enabled(on: bool) -> None:
    """Convenience: False = CPU only, True = automatic."""
    set_mode(AUTO if on else CPU)


def is_enabled() -> bool:
    return _mode != CPU


def set_bench(bench: dict | None) -> None:
    """Install a benchmark (None forgets it, so the next use re-measures)."""
    global _bench
    with _lock:
        _bench = bench


def bench(cached: dict | None = None) -> dict:
    """The adapter benchmark, measured once per process.

    A ``cached`` result (from the settings) is reused only while the adapters and the
    ONNX Runtime version are unchanged - a new GPU or driver swap re-measures.
    """
    global _bench
    with _lock:
        if _bench is None:
            ort = _ort()
            ads = adapters()
            if (cached and cached.get("ms") and cached.get("adapters") == ads
                    and cached.get("onnxruntime") == getattr(ort, "__version__", None)):
                _bench = cached
            else:
                _bench = benchmark()
        return _bench


def choice(cached: dict | None = None) -> dict:
    """The benchmark plus the decision for the current mode: ``device`` is the
    DirectML adapter index or None (CPU)."""
    b = bench(cached)
    return dict(b, device=select(b, _mode), mode=_mode)


def current_device(prefer_gpu: bool | None = None) -> int | None:
    """DirectML adapter to use right now, or None for the CPU."""
    if prefer_gpu is False or _mode == CPU:
        return None
    return choice()["device"]


class OnnxModel:
    """An ONNX model that runs on the GPU when it can and on the CPU when it can't.

    ``model`` is a path or the serialized bytes. ``run`` is thread-safe. If the GPU
    raises while running, the model is rebuilt on the CPU and the call is retried,
    so a driver hiccup never aborts a scan.
    """

    def __init__(self, model, prefer_gpu: bool | None = None) -> None:
        if _ort() is None:
            raise RuntimeError("onnxruntime is not installed")
        self._model = bytes(model) if isinstance(model, (bytes, bytearray)) else str(model)
        wanted = current_device(prefer_gpu)
        self.device = wanted
        try:
            self.session = _make_session(self._model, wanted)
        except Exception:  # noqa: BLE001 - the GPU refused this model: CPU instead
            self.device = None
            self.session = _make_session(self._model, None)
        self.fell_back = wanted is not None and self.device is None

    @property
    def on_gpu(self) -> bool:
        return self.device is not None

    @property
    def provider(self) -> str:
        return "DirectML" if self.on_gpu else "CPU"

    @property
    def input_names(self) -> list[str]:
        return [i.name for i in self.session.get_inputs()]

    def run(self, feeds: dict, outputs=None):
        try:
            return self.session.run(outputs, feeds)
        except Exception:  # noqa: BLE001
            if not self.on_gpu:
                raise
            with _lock:
                if self.on_gpu:          # the first thread to fail does the switch
                    self.session = _make_session(self._model, None)
                    self.device = None
                    self.fell_back = True
            return self.session.run(outputs, feeds)


# --------------------------------------------------------------------------- #
#  Text for the UI / diagnostics
# --------------------------------------------------------------------------- #

AUTO_LABEL = "Automatic - best GPU, otherwise the CPU"
CPU_LABEL = "CPU only"


def device_options(ads: list[dict] | None = None) -> list[tuple[str, str]]:
    """``[(label, mode)]`` for the settings combo: automatic, each GPU, CPU only."""
    ads = adapters() if ads is None else ads
    opts = [(AUTO_LABEL, AUTO)]
    for a in sorted(ads, key=lambda a: (a["rank"], a["index"])):
        kind = "discrete" if a["discrete"] else "integrated"
        opts.append((f"GPU: {a['name']} ({kind})", a["name"]))
    opts.append((CPU_LABEL, CPU))
    return opts


def describe(info: dict | None = None) -> str:
    """One readable line saying what will run where."""
    i = info or choice()
    if not i.get("onnxruntime"):
        return "GPU acceleration unavailable (ONNX Runtime not installed) - using the CPU"
    ms, dev = i.get("ms", {}), i.get("device")
    names = {a["index"]: a["name"] for a in i.get("adapters", [])}
    if dev is not None:
        t = ms.get(str(dev))
        speed = f", {ms['cpu'] / t:.1f}x the CPU speed" if t and ms.get("cpu") else ""
        return f"Using {names.get(dev, f'GPU {dev}')} via DirectML{speed}"
    if i.get("mode") == CPU:
        return "Using the CPU (CPU only is selected)"
    if not i.get("directml"):
        return "No DirectML support found - using the CPU"
    if not names:
        return "No GPU found - using the CPU"
    return ("Using the CPU - no GPU is faster here"
            + (f" [{i['error']}]" if i.get("error") else ""))
