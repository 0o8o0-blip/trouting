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
