"""Placement suggestions: when a board doesn't route as placed, which footprint
to move or rotate, and how far.

Every suggestion is checked by routing the board with the change made; only
the ones that leave fewer connections unrouted are reported.

1. Candidates: footprints near where routing failed (the pads left unjoined and
   the spots nets fought over last), not locked.
2. Moves: rotations by 90, 180 and 270 degrees, and shifts of 0.5 to 2 mm in
   eight directions. A move must keep the courtyard clear of the others' (no
   more overlap than it had) and keep the part as far inside the board as it
   was. A connector that hangs over the edge can then only slide along it.
3. Screening: the ratsnest (each net's shortest tree over its pads) gets
   shorter and crosses itself less when a placement is easier to route. The
   moves are ranked by that, and the best few per footprint are routed.
4. Routing: those boards are routed like the original, in parallel.
"""
import copy
import math
import multiprocessing as mp
import time

import numpy as np
from shapely.geometry import Polygon
from shapely.ops import unary_union

from .board import poly_of

ROTATIONS = (90, 180, 270)
STEPS = (0.5, 1.0, 2.0)                      # mm
DIRS = [(1, 0), (-1, 0), (0, 1), (0, -1), (1, 1), (1, -1), (-1, 1), (-1, -1)]
CROSS_MM = 3.0                               # a ratsnest crossing counts as this much extra length


# ---- moving a footprint in the dumped board ----
def _mover(at, rot, dx, dy):
    """Point map for a footprint at `at` turned by rot degrees (KiCad's sense:
    counter-clockwise on screen, y down) then shifted by (dx, dy)."""
    cx, cy = at
    c, s = math.cos(math.radians(rot)), math.sin(math.radians(rot))

    def f(p):
        x, y = p[0] - cx, p[1] - cy
        return [round(cx + x * c + y * s + dx, 5), round(cy - x * s + y * c + dy, 5)]
    return f


def _rings(rings, f):
    return [[f(p) for p in r] for r in rings]


def moved(d, ref, rot=0, dx=0.0, dy=0.0):
    """A copy of board dict d with footprint `ref` turned by rot degrees about
    its origin and shifted by (dx, dy) mm: its pads, holes, courtyard, copper
    and keepouts with it."""
    fp = next(f for f in d["footprints"] if f["ref"] == ref)
    f = _mover(fp["at"], rot, dx, dy)
    e = dict(d)                                         # only the touched lists are copied
    for key in ("footprints", "pads", "holes", "copper_shapes", "keepouts", "pours"):
        e[key] = list(d.get(key, []))
    for i, x in enumerate(e["footprints"]):
        if x["ref"] == ref:
            x = copy.deepcopy(x)
            x["at"] = f(x["at"])
            x["rot"] = (x["rot"] + rot) % 360
            x["courtyard"] = _rings(x["courtyard"], f)
            x0, y0, x1, y1 = x["bbox"]
            pts = [f(p) for p in ((x0, y0), (x1, y0), (x1, y1), (x0, y1))]
            x["bbox"] = [min(p[0] for p in pts), min(p[1] for p in pts), max(p[0] for p in pts), max(p[1] for p in pts)]
            e["footprints"][i] = x
    for i, x in enumerate(e["pads"]):
        if x["ref"] == ref:
            x = copy.deepcopy(x)
            x["at"] = f(x["at"])
            x["rot"] = (x.get("rot", 0.0) + rot) % 360
            x["shapes"] = {l: _rings(r, f) for l, r in x["shapes"].items()}
            e["pads"][i] = x
    for i, x in enumerate(e["holes"]):
        if x.get("ref") == ref:
            e["holes"][i] = dict(x, at=f(x["at"]))
    for key, poly_key in (("copper_shapes", "polys"), ("keepouts", "polys"), ("pours", "polys")):
        for i, x in enumerate(e[key]):
            if x.get("ref") == ref:
                e[key][i] = dict(x, **{poly_key: _rings(x[poly_key], f)})
    return e


def describe(fp, rot, dx, dy):
    if rot:
        way = {90: "90° counter-clockwise", 180: "180°", 270: "90° clockwise"}[rot]
        to = (fp["rot"] + rot + 180) % 360 - 180            # as KiCad shows it, -180..180
        return "rotate %s %s (to %g°)" % (fp["ref"], way, to)
    dist = math.hypot(dx, dy)
    names = {(1, 0): "right", (-1, 0): "left", (0, 1): "down", (0, -1): "up",
             (1, 1): "right and down", (1, -1): "right and up", (-1, 1): "left and down", (-1, -1): "left and up"}
    way = names[(int(np.sign(dx)), int(np.sign(dy)))]
    x, y = fp["at"][0] + dx, fp["at"][1] + dy
    return "move %s %.1f mm %s (to %.2f, %.2f)" % (fp["ref"], dist, way, x, y)


# ---- what a move may do ----
def footprint_area(fp):
    g = poly_of(fp["courtyard"]) if fp["courtyard"] else Polygon()
    if g.is_empty:
        x0, y0, x1, y1 = fp["bbox"]
        g = Polygon([(x0, y0), (x1, y0), (x1, y1), (x0, y1)])
    return g


class Space:
    """The board's outline and every footprint's courtyard, to test moves against."""

    def __init__(self, d):
        self.outline = poly_of(d["outline"])
        holes = poly_of(d.get("outline_holes", []))
        if not holes.is_empty:
            self.outline = self.outline.difference(holes)
        self.side = {f["ref"]: f["side"] for f in d["footprints"]}
        self.area = {f["ref"]: footprint_area(f) for f in d["footprints"]}

    def others(self, ref):
        side = self.side[ref]
        return unary_union([g for r, g in self.area.items() if r != ref and self.side[r] == side])

    def fits(self, ref, g_new, others=None):
        g_old = self.area[ref]
        others = self.others(ref) if others is None else others
        # no more overlap with the others than before, no further off the board
        if g_new.intersection(others).area > g_old.intersection(others).area + 1e-3:
            return False
        out_old = g_old.difference(self.outline).area
        out_new = g_new.difference(self.outline).area
        if out_old < 1e-3:
            return out_new < 1e-3
        return abs(out_new - out_old) < 0.05 * out_old + 1e-3 and \
            g_new.intersection(self.outline).area >= g_old.intersection(self.outline).area - 1e-3


# ---- screening: the ratsnest ----
def ratsnest(d, skip_nets):
    """Per net, its shortest tree over its pads: [(net, (x0, y0), (x1, y1))]."""
    per = {}
    for p in d["pads"]:
        if p["net"] and p["net"] not in skip_nets:
            per.setdefault(p["net"], []).append(p["at"])
    edges = []
    for n, pts in per.items():
        P = np.unique(np.array(pts, float), axis=0)
        if len(P) < 2:
            continue
        inside = np.zeros(len(P), bool)
        inside[0] = True
        best = np.hypot(*(P - P[0]).T)
        frm = np.zeros(len(P), int)
        for _ in range(len(P) - 1):                 # Prim
            best_m = np.where(inside, np.inf, best)
            j = int(np.argmin(best_m))
            edges.append((n, P[frm[j]], P[j]))
            inside[j] = True
            dj = np.hypot(*(P - P[j]).T)
            closer = dj < best
            best = np.where(closer, dj, best)
            frm = np.where(closer, j, frm)
    return edges


def rat_score(d, skip_nets):
    """Ratsnest length plus CROSS_MM per crossing of two different nets' lines."""
    edges = ratsnest(d, skip_nets)
    if not edges:
        return 0.0
    A = np.array([e[1] for e in edges])
    B = np.array([e[2] for e in edges])
    net = np.array([hash(e[0]) for e in edges])
    length = float(np.hypot(*(B - A).T).sum())

    def orient(p, q, r):
        return np.sign((q[..., 0] - p[..., 0]) * (r[..., 1] - p[..., 1]) - (q[..., 1] - p[..., 1]) * (r[..., 0] - p[..., 0]))
    a, b = A[:, None, :], B[:, None, :]
    c, e = A[None, :, :], B[None, :, :]
    o1, o2 = orient(a, b, c), orient(a, b, e)
    o3, o4 = orient(c, e, a), orient(c, e, b)
    cross = (o1 * o2 < 0) & (o3 * o4 < 0) & (net[:, None] != net[None, :])
    return length + CROSS_MM * float(np.triu(cross, 1).sum())


# ---- the search ----
def trouble_spots(unrouted, hot):
    """Where routing failed: pads left unjoined, by name, and fight spots (mm)."""
    pads = {p for u in unrouted for p in u.get("pads", [])}
    return pads, [tuple(h) for h in hot]


def candidates(d, unrouted, hot, max_parts=8, reach=3.0):
    """Footprints worth moving, nearest the trouble first."""
    pads, spots = trouble_spots(unrouted, hot)
    refs_of_pads = {p.split(".")[0] for p in pads}
    space = Space(d)
    score = {}
    for f in d["footprints"]:
        if f["locked"]:
            continue
        g = space.area[f["ref"]]
        s = 3.0 * sum(1 for p in pads if p.split(".")[0] == f["ref"])
        for x, y in spots:
            dist = g.distance(Polygon([(x - 0.01, y - 0.01), (x + 0.01, y - 0.01), (x + 0.01, y + 0.01)]))
            if dist < reach:
                s += 1.0 - dist / reach
        if s > 0 or f["ref"] in refs_of_pads:
            score[f["ref"]] = s
    return sorted(score, key=lambda r: -score[r])[:max_parts]


def moves_for(d, ref, space):
    fp = next(f for f in d["footprints"] if f["ref"] == ref)
    g = space.area[ref]
    others = space.others(ref)
    f_at = fp["at"]
    out = []
    from shapely import affinity
    for rot in ROTATIONS:
        # shapely rotates counter-clockwise in y-up; on the board (y down) that is clockwise
        g2 = affinity.rotate(g, -rot, origin=tuple(f_at))
        if space.fits(ref, g2, others):
            out.append((rot, 0.0, 0.0))
    for step in STEPS:
        for ux, uy in DIRS:
            k = step / math.hypot(ux, uy) if ux and uy else step
            dx, dy = ux * k, uy * k
            if space.fits(ref, affinity.translate(g, dx, dy), others):
                out.append((0, round(dx, 3), round(dy, 3)))
    return out


def screen(d, refs, plane_nets, per_part=2, total=12):
    """The most promising moves of these footprints, by ratsnest score."""
    space = Space(d)
    base = rat_score(d, plane_nets)
    ranked = []
    for ref in refs:
        mine = []
        for rot, dx, dy in moves_for(d, ref, space):
            s = rat_score(moved(d, ref, rot, dx, dy), plane_nets)
            mine.append((s - base, ref, rot, dx, dy))
        mine.sort()
        ranked.append(mine)
    # the best per footprint first (so every candidate part is tried), then the best of the rest
    picked = [m for mine in ranked for m in mine[:per_part]]
    rest = sorted(m for mine in ranked for m in mine[per_part:])
    picked.sort()
    return (picked + rest)[:total]


# ---- routing the candidates ----
_JOB = {}


def _try(move):
    from .board import Board
    from .router import Options, Router
    d, setup, res = _JOB["d"], _JOB["setup"], _JOB["res"]
    _, ref, rot, dx, dy = move
    e = moved(d, ref, rot, dx, dy)
    o = Options()
    o.via_cost, o.plane_layer_cost = setup
    o.log = lambda *a: None
    t = time.time()
    r = Router(Board(e, res=res), o).run()
    out = r.result()
    return {"move": move, "unrouted": len(out["unrouted"]), "vias": len(out["vias"]),
            "secs": round(time.time() - t, 1)}


def suggest(d, plane_nets, unrouted, hot, setup, jobs, log, res=0.05, budget=None):
    """Checked placement suggestions, best first: [{"text", "unrouted", "vias", "move"}]."""
    t0 = time.time()
    refs = candidates(d, unrouted, hot)
    if not refs:
        log("  no footprint near the trouble can move (all locked?)")
        return []
    log("  footprints near the trouble: %s" % ", ".join(refs))
    moves = screen(d, refs, plane_nets, total=max(jobs, 8))
    log("  routing the board with each of %d moves (%.0fs so far)" % (len(moves), time.time() - t0))
    _JOB.update(d=d, setup=setup, res=res)
    fps = {f["ref"]: f for f in d["footprints"]}
    found = []
    ctx = mp.get_context("fork")
    with ctx.Pool(min(jobs, len(moves))) as pool:
        it = pool.imap_unordered(_try, moves)
        for _ in moves:
            try:
                r = it.next(timeout=None if budget is None else max(1.0, budget - (time.time() - t0)))
            except mp.TimeoutError:
                log("  (out of time: stopped the moves still routing)")
                pool.terminate()
                break
            _, ref, rot, dx, dy = r["move"]
            r["text"] = describe(fps[ref], rot, dx, dy)
            log("    %-46s %2d unrouted  (%.0fs)" % (r["text"], r["unrouted"], r["secs"]))
            found.append(r)
    before = len(unrouted)
    good = [r for r in found if r["unrouted"] < before]

    def size(r):                       # how big a change it is for the designer: a turn or a shift
        _, _, rot, dx, dy = r["move"]
        return 2.0 if rot else math.hypot(dx, dy)
    good.sort(key=lambda r: (r["unrouted"], r["vias"], size(r)))
    out, per = [], {}
    for r in good:                     # at most two per footprint, so other parts get a say
        ref = r["move"][1]
        if per.get(ref, 0) < 2:
            out.append(r)
            per[ref] = per.get(ref, 0) + 1
    return out
