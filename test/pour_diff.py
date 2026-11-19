"""Trouting's predicted pours next to KiCad's real fill, to find why a pour net
ends up unconnected:

    python3 test/pour_diff.py board.json VIA_COST PLANE_COST fills.json [out.png]

board.json: `trouting_kicad.py dump BOARD board.json --rip-up` of the board as it was.
VIA_COST PLANE_COST: the setup that won (the bench log's "best:" line).
fills.json: `trouting_kicad.py fills ROUTED fills.json` of Trouting's result.

For each pour net that Trouting treats as a plane, per layer: how much of the
pour Trouting expected and KiCad filled, and where they disagree. Then each of
Trouting's vias of that net: in the predicted pour or not, in KiCad's fill or
not. A via Trouting put in the pour that KiCad's fill doesn't reach is a via
that dangles.
"""
import json
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from shapely.geometry import Polygon  # noqa: E402

from trouting.board import Board  # noqa: E402
from trouting.router import Options, Router  # noqa: E402


def raster(b, polys):
    """Grid cells whose centre is inside the polygons."""
    import shapely
    xs, ys = np.meshgrid([b.cx(x) for x in range(b.W)], [b.cy(y) for y in range(b.H)])
    m = np.zeros((b.H, b.W), bool)
    for outline, holes in polys:
        if len(outline) >= 3:
            m |= shapely.contains_xy(Polygon(outline, [h for h in holes if len(h) >= 3]).buffer(0), xs, ys)
    return m


def main(board_json, via_cost, plane_cost, fills_json, png=None):
    d = json.load(open(board_json, encoding="utf-8"))
    b = Board(d)
    o = Options()
    o.via_cost, o.plane_layer_cost = float(via_cost), float(plane_cost)
    o.log = lambda *a: None
    r = Router(b, o).run()
    fills = json.load(open(fills_json, encoding="utf-8"))
    print("planes:", {b.net_names[n]: b.layers[l] for n, (l, _) in r.plane_mask.items()},
          " side pours:", sorted("%s/%s" % (b.net_names[n], b.layers[l]) for n, l in r.side_mask))
    for n, (pl, _) in r.plane_mask.items():
        name = b.net_names[n]
        r.stamp(n, -1)
        main = r.main_island(n)
        r.stamp(n, +1)
        print("\n== %s  (plane %s)" % (name, b.layers[pl]))
        for l in sorted({pl} | {l for m, l in r.side_mask if m == n}):
            real = raster(b, [(f["outline"], f["holes"]) for f in fills if f["net"] == name and f["layer"] == b.layers[l]])
            pred = main if l == pl else r.side_mask[(n, l)]
            a = b.res ** 2
            print("  %-7s predicted %7.0f mm2, KiCad filled %7.0f mm2; predicted but not filled %6.0f, filled but not predicted %6.0f"
                  % (b.layers[l], pred.sum() * a, real.sum() * a, (pred & ~real).sum() * a, (real & ~pred).sum() * a))
            if png and l == pl:
                from PIL import Image
                img = np.zeros((b.H, b.W, 3), np.uint8)
                img[pred & real] = (90, 90, 90)
                img[pred & ~real] = (255, 60, 60)        # red: Trouting counted on copper KiCad didn't pour
                img[real & ~pred] = (60, 140, 255)       # blue: KiCad poured where Trouting didn't expect
                Image.fromarray(img).save(png.replace(".png", "-%s-%s.png" % (name.strip("/").replace("/", "_"), b.layers[l])))
        real_pl = raster(b, [(f["outline"], f["holes"]) for f in fills if f["net"] == name and f["layer"] == b.layers[pl]])
        bad = []
        for p in r.paths.get(n, []):
            for v in r.vias_of(p):
                v = int(v)
                x, y = v % b.W, v // b.W
                if not real_pl[y, x]:
                    bad.append((b.cx(x), b.cy(y), bool(main[y, x])))
        print("  vias of %s not in KiCad's %s fill: %d" % (name, b.layers[pl], len(bad)))
        for x, y, inpred in bad[:15]:
            print("    (%.2f, %.2f)  %s" % (x, y, "Trouting expected pour here" if inpred else "not in the predicted pour either"))


if __name__ == "__main__":
    main(*sys.argv[1:])
