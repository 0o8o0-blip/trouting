"""Compare the router's predicted pour fill (out/<b>-pred.npz) with KiCad's (out/<b>-fills.json).
Writes out/<b>-fill-<net>-<layer>.png: green both, red KiCad only, blue predicted only."""
import json, sys
import numpy as np, shapely
from shapely.geometry import Polygon
from PIL import Image
name = sys.argv[1]
P = np.load(f'/home/tommy/claude/tracer/test/out/{name}-pred.npz')
ox, oy, res, W, H = float(P['ox']), float(P['oy']), float(P['res']), int(P['W']), int(P['H'])
X, Y = np.meshgrid(ox + (np.arange(W) + 0.5) * res, oy + (np.arange(H) + 0.5) * res)
fills = json.load(open(f'/home/tommy/claude/tracer/test/out/{name}-fills.json'))
for key in P.files:
    if '|' not in key:
        continue
    net, layer = key.split('|')
    polys = [Polygon(f['outline'], f['holes']) for f in fills if f['net'] == net and f['layer'] == layer]
    real = np.zeros((H, W), bool)
    for pg in polys:
        real |= shapely.contains_xy(pg, X, Y)
    pred = P[key]
    img = np.full((H, W, 3), 255, np.uint8)
    img[real & pred] = (120, 200, 120)
    img[real & ~pred] = (230, 60, 60)
    img[~real & pred] = (60, 90, 230)
    Image.fromarray(img).save(f'/home/tommy/claude/tracer/test/out/{name}-fill-{net.strip("/")}-{layer}.png')
    print(f'{key}: KiCad filled {real.sum()} cells, predicted {pred.sum()}, predicted but not filled {(pred & ~real).sum()}')
