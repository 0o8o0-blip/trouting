import json, sys, time
sys.path.insert(0, '/home/tommy/claude/tracer')
import numpy as np
from PIL import Image
from trouting.board import Board
name = sys.argv[1]
t = time.time()
b = Board(json.load(open(f'/home/tommy/claude/tracer/test/{name}.json')))
print(f'{name}: grid {b.W}x{b.H}x{b.L} built in {time.time()-t:.1f}s, rmax {b.rmax:.2f}')
print('planes', {b.net_names[n]: b.layers[l] for n, (l, _) in b.planes.items()})
nt = {b.net_names[n]: len(ts) for n, ts in b.terminals.items()}
print('nets with >=2 terminals', sum(1 for v in nt.values() if v >= 2), 'terminals total', sum(nt.values()))
for n, ts in b.terminals.items():
    if n in b.planes:
        print('plane net', b.net_names[n], 'terminals', len(ts), 'on plane', sum(t['on_plane'] for t in ts))
# a terminal with no allowed cell at all = can't be reached
bad = []
for n, ts in b.terminals.items():
    thr = b.net_rule[n]['width'] / 2 + 0.01
    for t in ts:
        c = t['cells']
        ok = np.where(b.n1[c] == n, b.d2[c], b.d1[c]) >= thr
        if not ok.any() and not len(t['own']):
            bad.append((b.net_names[n], t['names']))
print('terminals with no usable cell:', len(bad), bad[:8])
# picture: blocked for a 0.2 mm track of a net that isn't there
for l in range(b.L):
    d = b.d1[l*b.WH:(l+1)*b.WH].reshape(b.H, b.W)
    img = np.where(d < 0.11, 60, 230).astype(np.uint8)
    img[d < 0] = 0
    Image.fromarray(img).save(f'/home/tommy/claude/tracer/test/out/{name}-static-{b.layers[l]}.png')
