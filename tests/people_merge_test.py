import sys, time, collections, tempfile, os
# ISOLATED: a throw-away data folder, so the real database / settings / names are never touched
os.environ["LOCALAPPDATA"] = os.path.join(tempfile.gettempdir(), "eyescan_test_fixed"); os.makedirs(os.environ["LOCALAPPDATA"], exist_ok=True)
sys.path.insert(0, r"S:\HTML\EyeScan2")


def main():
    import bridge
    b = bridge.Bridge(); ev = []
    b._emit = lambda k, **p: ev.append((k, p))
    assert "eyescan_test_" in str(bridge.db_file()), "test must use the temp database"
    b._names.clear("names")                           # temp DB only (asserted above)
    b.start_scan(sys.argv[1], {"force": False, "robust": False, "recursive": True, "people": True})
    while b._scanning or not ev: time.sleep(0.3)
    pay = b.people(True)
    subj = lambda r: r.path.parent.name
    comp = collections.defaultdict(collections.Counter)
    for r in b._results.values():
        for f in r.faces:
            if f.person is not None: comp[f.person][subj(r)] += 1
    print("people:", [(p["id"], dict(comp[p["id"]])) for p in pay["people"]])
    print("suggestions:", pay["suggestions"][:4])
    A = [pid for pid, c in comp.items() if c.most_common(1)[0][0] == "A"]
    print("cluster ids of subject A:", A)
    D = [pid for pid, c in comp.items() if c.most_common(1)[0][0] == "D"]
    r1 = b.merge_people(D, "")                       # unnamed: lasts until the next scan
    print("unnamed merge of D fragments:", len(pay["people"]), "->", len(r1["people"]), "people; named:", [p["name"] for p in r1["people"] if p["name"]])
    r2 = b.merge_people(A, "Anna")                   # named: saved with exemplars
    print("named merge of A fragments ->", [(p["id"], p["name"], len(p["photos"])) for p in r2["people"] if p["name"]])
    r3 = b.people(True, refresh=True)
    print("after re-cluster, Anna recognised again:", [(p["id"], p["name"], len(p["photos"])) for p in r3["people"] if p["name"]], "| total people", len(r3["people"]))
    other = [p for p in r3["people"] if not p["name"]][0]
    b.name_person(other["id"], "Zed")
    r4 = b.people(True, refresh=True)
    ids = {p["name"]: p["id"] for p in r4["people"] if p["name"]}
    r5 = b.merge_people([ids["Anna"], ids["Zed"]], "Anna")
    print("merge named Anna+Zed -> known names", sorted(set(b.known_names())), "| people", len(r5["people"]))
    tgt = r5["people"][-1]["id"]; res = b.ignore_person(tgt)
    print("ignore_person: photos updated", len(res["items"]), "| people left", len(res["payload"]["people"]))
    print("db persons", b.db_info()["persons"], "| temp data dir", os.environ["LOCALAPPDATA"])


if __name__ == "__main__":
    import multiprocessing; multiprocessing.freeze_support(); main()
