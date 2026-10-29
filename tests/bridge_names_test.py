import sys, time, os, json, urllib.request, tempfile
sys.path.insert(0, r"S:\HTML\EyeScan2")
import bridge
b = bridge.Bridge(); ev = []
b._emit = lambda kind, **p: ev.append((kind, p))
print("people default", b.get_settings()["people"], "res", b.get_settings()["max_dim"], b.about()["resolutions"])
print(b.start_scan(sys.argv[1], {"force": True, "robust": False, "recursive": False, "people": True}))
while b._scanning or not ev: time.sleep(0.5)
print([k for k, _ in ev][-4:], [p for k, p in ev if k == "error"])
fin = [p for k, p in ev if k == "final"][0]; print("final", fin)
items = [i for k, p in ev if k == "results" for i in p["items"]]
print("item keys", sorted(items[0].keys())[:6], "payload bytes/item", len(json.dumps(items[0])))
geo = b.geometry(items[0]["path"]); print("regions", sorted(geo[0]["regions"].keys()) if geo else None)
# image server
u = b.about()["img_base"] + "p=" + urllib.parse.quote(items[0]["path"]) + "&s=360"
data = urllib.request.urlopen(u).read(); print("img server bytes", len(data), data[:2])
try: urllib.request.urlopen(b.about()["img_base"] + "p=" + urllib.parse.quote(r"C:\Windows\win.ini") + "&s=360")
except Exception as e: print("outside path ->", e)
try: urllib.request.urlopen(u.replace("t=", "t=x"))
except Exception as e: print("bad token ->", e)
pp = b.people(True); print("people", pp["ok"], len(pp.get("people", [])))
if pp["ok"]:
    c = pp["people"][0]; r = b.name_person(c["id"], "Test Person"); print("named", [x["name"] for x in r["people"]][:3], r["known"])
    pp2 = b.people(True); print("auto-match after re-cluster", [x["name"] for x in pp2["people"]][:3])
a = [i for i in items if i["animals"]]; print("animal photos", len(a))
print("db_info", {k: v for k, v in b.db_info().items() if k in ("path", "results", "persons", "tags", "default")})
d = tempfile.mkdtemp(); m = b.db_move(d); print("move", m["ok"], m.get("path"), b.db_info()["persons"])
print("scan-after-move cache rows", b.db_info()["results"])
print("clear people", b.db_clear("people")["removed"], "clear missing", b.db_clear("missing")["removed"])
back = b.db_use_default(); print("back to default", back["ok"], back.get("default"))
print("clear folder", b.db_clear("folder")["removed"], "compact", b.db_clear("compact")["ok"])
