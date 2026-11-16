import json, sys, time
sys.path.insert(0, '/home/tommy/claude/tracer')
from trouting.board import Board
from trouting.router import Router, Options
from trouting.render import render

name = sys.argv[1]
o = Options()
if len(sys.argv) > 2:
    o.max_rounds = int(sys.argv[2])
t = time.time()
b = Board(json.load(open(f'/home/tommy/claude/tracer/test/{name}.json')))
print(f'board {time.time()-t:.1f}s')
r = Router(b, o)
print(f'router setup {time.time()-t:.1f}s, {len(r.nets)} nets to route, classes {r.tw} {r.vd}')
r.run()
out = r.result()
print(f'done {time.time()-t:.1f}s: {len(out["tracks"])} tracks, {len(out["vias"])} vias, {len(out["unrouted"])} unrouted')
for u in out['unrouted']:
    print('  ', u)
json.dump(out, open(f'/home/tommy/claude/tracer/test/out/{name}-routes.json', 'w'))
render(r, f'/home/tommy/claude/tracer/test/out/{name}-routed.png')
# what the router believes the plane net pours will fill (for comparing with KiCad's)
import numpy as np
pred = {}
for n in r.plane_mask:
    r.stamp(n, -1)
    pl = r.plane_mask[n][0]
    pred[f'{b.net_names[n]}|{b.layers[pl]}'] = r.main_island(n)
    for (m, l), mask in r.side_mask.items():
        if m == n:
            lab, group, _ = r.pour_pieces(n, l, mask)
            pred[f'{b.net_names[n]}|{b.layers[l]}'] = lab > 0
    r.stamp(n, +1)
np.savez_compressed(f'/home/tommy/claude/tracer/test/out/{name}-pred.npz', ox=b.ox, oy=b.oy, res=b.res, W=b.W, H=b.H, **pred)
