"""WindowMemory: size/position/screen persist; bad values fall back to defaults."""
import json, sys, tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import WindowMemory, DEFAULT

class FakeWin:
    x, y, width, height = 2100, 140, 1500, 900      # e.g. on a second monitor to the right

d = Path(tempfile.mkdtemp()) / "window.json"
m = WindowMemory(d); assert m.data["width"] == DEFAULT["width"]
m.window = FakeWin(); m.save()
saved = json.loads(d.read_text())
assert (saved["x"], saved["y"], saved["width"], saved["height"]) == (2100, 140, 1500, 900), saved
m2 = WindowMemory(d); assert m2.data["x"] == 2100 and m2.data["width"] == 1500
d.write_text("{not json"); assert WindowMemory(d).data["width"] == DEFAULT["width"]
print("window memory: ok")
