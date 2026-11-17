"""Test placement suggestions on boards broken on purpose.

    python3 test/t_suggest.py BOARD scan [REF ...]     rotate each part (4+ pads, or these) by 90/180/270,
                                                      route each, list the ones that no longer route
    python3 test/t_suggest.py BOARD fix REF:ROT[:DX:DY] ...
                                                      break the board so, route it, then ask for suggestions

BOARD is test/boards/<name>.json or a path to a dumped .json.
"""
import json
import multiprocessing as mp
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from trouting.board import Board
from trouting.router import Options, Router
from trouting.suggest import moved, suggest

SETUP = (8.0, 2.5)
D = None


def load(name):
    p = name if name.endswith(".json") else os.path.join(ROOT, "test", "boards", name + ".json")
    return json.load(open(p))


def route(d):
    o = Options()
    o.via_cost, o.plane_layer_cost = SETUP
    o.log = lambda *a: None
    b = Board(d)
    r = Router(b, o).run()
    out = r.result()
    hot = []
    for h in r.hot:
        for v in h[:: max(1, len(h) // 20)]:
            xy = int(v) % b.WH
            hot.append((float(b.cx(xy % b.W)), float(b.cy(xy // b.W))))
    planes = {b.net_names[n] for n in b.planes}
    return out, hot, planes


def _scan_one(job):
    ref, rot = job
    t = time.time()
    out, _, _ = route(moved(D, ref, rot))
    return ref, rot, len(out["unrouted"]), time.time() - t


def main():
    global D
    name, mode = sys.argv[1], sys.argv[2]
    D = load(name)
    if mode == "scan":
        refs = sys.argv[3:] or [f["ref"] for f in D["footprints"] if not f["locked"]
                                and sum(1 for p in D["pads"] if p["ref"] == f["ref"] and p["net"]) >= 4]
        from shapely import affinity
        from trouting.suggest import Space
        sp = Space(D)
        at = {f["ref"]: f["at"] for f in D["footprints"]}
        jobs = [(r, a) for r in refs for a in (90, 180, 270)
                if sp.fits(r, affinity.rotate(sp.area[r], -a, origin=tuple(at[r])))]
        print("%d variants" % len(jobs), flush=True)
        with mp.get_context("fork").Pool(os.cpu_count()) as pool:
            for ref, rot, u, s in pool.imap_unordered(_scan_one, jobs):
                print("  %-6s rot %3d: %2d unrouted  (%.0fs)" % (ref, rot, u, s), flush=True)
        return
    d = D
    for spec in sys.argv[3:]:
        f = spec.split(":")
        ref, rot = f[0], int(f[1])
        dx, dy = (float(f[2]), float(f[3])) if len(f) > 3 else (0.0, 0.0)
        d = moved(d, ref, rot, dx, dy)
    t = time.time()
    out, hot, planes = route(d)
    print("broken board: %d unrouted %s (%.0fs)" % (len(out["unrouted"]), [u["net"] for u in out["unrouted"]], time.time() - t))
    if not out["unrouted"]:
        return
    t = time.time()
    good = suggest(d, planes, out["unrouted"], hot, SETUP, os.cpu_count(), print)
    print("suggestions (%.0fs):" % (time.time() - t))
    for g in good[:5]:
        print("  %-50s %d unrouted, %d vias" % (g["text"], g["unrouted"], g["vias"]))
    if not good:
        print("  none found")


if __name__ == "__main__":
    main()
