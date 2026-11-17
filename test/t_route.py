import json, sys, time
sys.path.insert(0, '/home/tommy/claude/tracer')
from trouting.board import Board
from trouting.router import Router, Options
from trouting.render import render

name = sys.argv[1]
o = Options()
tag = name
for a in sys.argv[2:]:                       # rounds, or option=value (res=0.025 plane_layer_cost=1.5 ...)
    if '=' in a:
        k, v = a.split('=')
        setattr(o, k, type(getattr(o, k))(v))
        tag += '-' + a
    else:
        o.max_rounds = int(a)
t = time.time()
b = Board(json.load(open(f'/home/tommy/claude/tracer/test/boards/{name}.json')), res=o.res)
print(f'board {time.time()-t:.1f}s')
r = Router(b, o)
print(f'router setup {time.time()-t:.1f}s, {len(r.nets)} nets to route, classes {r.tcls} {r.vcls}')
r.run()
out = r.result()
print(f'done {time.time()-t:.1f}s: {len(out["tracks"])} tracks, {len(out["vias"])} vias, {len(out["unrouted"])} unrouted')
for u in out['unrouted']:
    print('  ', u)
json.dump(out, open(f'/home/tommy/claude/tracer/test/out/{tag}-routes.json', 'w'))
render(r, f'/home/tommy/claude/tracer/test/out/{tag}-routed.png')
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
np.savez_compressed(f'/home/tommy/claude/tracer/test/out/{tag}-pred.npz', ox=b.ox, oy=b.oy, res=b.res, W=b.W, H=b.H, **pred)
