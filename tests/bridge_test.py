import sys, time, os
sys.path.insert(0, r"S:\HTML\EyeScan2")
import bridge
b = bridge.Bridge(); ev = []
b._emit = lambda kind, **p: ev.append((kind, p))
print(b.about()["credit"]); print(b.system_info())
root = sys.argv[1]
print(b.start_scan(root, {"force": True, "robust": False, "people": False, "recursive": False}))
while b._scanning or not ev: time.sleep(0.5)
print([k for k,_ in ev][-3:], [p for k,p in ev if k=="error"])
fin = [p for k,p in ev if k=="final"][0]["items"]
f0 = fin[0]; print(f0["name"], len(f0["faces"]), "outline pts", len(f0["faces"][0]["outline"]) if f0["faces"] else 0)
print("preview", b.bulk_preview("closed"), b.bulk_preview("dups")["count"])
print("override", b.set_override([f0["path"]], "flag")[0]["flagged"], b.set_override([f0["path"]], None)[0]["flagged"])
print("protect", b.set_protect([f0["path"]], True)[0]["protected"], b.set_protect([f0["path"]], False)[0]["protected"])
print("face_thumb", (b.face_thumb(f0["path"], f0["faces"][0]["box"]) or "")[:30])
b.set_override([f0["path"]], "flag")
r = b.sort_bulk("closed", "copy", "Closed Eyes"); print("sort", {k:v for k,v in r.items() if k!="gone"})
print("undo", b.undo_sort())
r = b.rename(f0["path"], "renamed_test.jpg"); print("rename", r["ok"]); b.rename(r["item"]["path"], f0["name"])
print("sens", len(b.set_threshold(0.6)), b.get_settings()["threshold"]); b.set_threshold(0.5)
print("recent", b.recent()[:1])

# --- v2.0 additions: extras settings, selection sort, GPU test ---
s = b.save_settings({"blur_cut": 9.0, "misfocus_ratio": 2.2, "ear_down": 0.15, "min_face": 0.004, "max_dim": 2048})
import eyescan_core as core
print("extras", s["blur_cut"], core.BLUR_SHARP_MIN, core.MISFOCUS_RATIO, core.EAR_DOWN)
b.save_settings({"blur_cut": 7.0, "misfocus_ratio": 1.8, "ear_down": 0.13, "min_face": 0.002, "max_dim": 1536})
r = b.sort_bulk(None, "copy", "Picked", [fin[1]["path"]]); print("sort selection", r["ok"], r.get("moved"))
print("gpu", b.gpu_adapters(), b.gpu_test()["ok"])
