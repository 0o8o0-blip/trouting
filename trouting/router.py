"""Negotiated-congestion routing of a whole board.

Every net is routed each round as if it had the board to itself, except that
copper another net already uses costs extra, and so do places that were
fought over in earlier rounds. The extra rises every round; nets that have a
way around take it, and the net that really needs a spot keeps it. Rounds
only reroute the nets still in a fight. What's left fighting at the end is
taken out and reported, with where it fought (the crowded spots the
placement suggestions work from).

Plane nets (a pour over >= 25 % of a layer) aren't routed pad to pad: each
pad not already in the pour gets a short track and a via into it.
"""
import math
import time

import numpy as np
import shapely
from scipy import ndimage

from .board import MARGIN
from .core import Grid


def grow(mask, r):
    """Cells within r cells (Euclidean) of mask. (ndimage's binary_dilation steps
    in a diamond, which falls 30 % short on the diagonals.)"""
    if r <= 0:
        return mask.copy()
    return ndimage.distance_transform_edt(~mask) <= r


def shrink(mask, r):
    """Cells of mask at least r cells (Euclidean) inside it (more than r from its edge)."""
    if r <= 0:
        return mask.copy()
    m = np.pad(mask, 1)                   # outside the array counts as outside
    return (ndimage.distance_transform_edt(m) > r)[1:-1, 1:-1]


class Options:
    res = 0.05
    via_cost = 15.0          # cells (x res mm) a via is worth in track length
    plane_layer_cost = 2.5   # tracks on a plane layer cut the plane
    pres0 = 0.5              # first round's penalty per other net in the way
    pres_mult = 1.6
    pres_max = 1000.0
    hist_add = 0.3
    max_rounds = 60
    near = 3.0               # mm the first search looks beyond its ends
    neighbour = 1.5          # mm: nets this close to a fight are rerouted with it
    log = print


class Router:
    def __init__(self, board, opt=None):
        self.b = b = board
        self.o = opt or Options()
        res = b.res
        rules = b.net_rule
        # shape classes: each distinct track width and via diameter
        self.tw = sorted({r["width"] for r in rules}) or [0.25]
        self.vd = sorted({r["via_d"] for r in rules}) or [0.6]
        self.K = len(self.tw) + len(self.vd)
        # a via keeps the copper clearance from its pad and the hole clearance from its
        # drill, whichever reaches further (for low-clearance nets, the drill's)
        hole_clr = b.hole_clr
        drill = {v: max([r["via_drill"] for r in rules if r["via_d"] == v] + [0.0]) for v in self.vd}
        self.via_reach = {v: max(v / 2, drill[v] / 2 + hole_clr - b.c_ref) for v in self.vd}
        half = [w / 2 for w in self.tw] + [self.via_reach[v] for v in self.vd]
        cmax = [0.0] * self.K
        for r in rules:
            for k in (self.tw.index(r["width"]), len(self.tw) + self.vd.index(r["via_d"])):
                cmax[k] = max(cmax[k], r["clearance"])
        self.per = []
        for n, r in enumerate(rules):
            c = r["clearance"]
            extra = max(0.0, c - b.c_ref)
            rt = [(r["width"] / 2 + max(c, cmax[k]) + half[k] + MARGIN) / res for k in range(self.K)]
            # (the via's drill against another net's track: its reach already counts the
            # hole clearance, so use the smaller copper term there)
            rv = [(max(r["via_d"] / 2 + max(c, cmax[k]), r["via_drill"] / 2 + hole_clr) + half[k] + MARGIN) / res
                  for k in range(self.K)]
            self.per.append({
                "kt": self.tw.index(r["width"]), "kv": len(self.tw) + self.vd.index(r["via_d"]),
                "thr": r["width"] / 2 + extra + MARGIN,
                "vthr": max(r["via_d"] / 2 + extra, r["via_drill"] / 2 + hole_clr - b.c_ref) + MARGIN,
                "rt": rt, "rv": rv,
            })
        lc = [self.o.plane_layer_cost if l in b.plane_layers else 1.0 for l in range(b.L)]
        self.grid = Grid(b, self.K, lc)

        # per net: own copper (always usable), no-via cells, plane targets
        self.nets = []
        for n, terms in sorted(b.terminals.items()):
            if n in b.planes:
                todo = [t for t in terms if not t["on_plane"]]
                if not todo:
                    continue
            elif len(terms) < 2:
                continue
            self.nets.append(n)
        self.own = {n: np.unique(np.concatenate([t["own"] for t in b.terminals[n]])) for n in self.nets}
        self.novia = {n: np.unique(np.concatenate([t["novia"] for t in b.terminals[n]])) for n in self.nets}
        # hole to hole: a net's vias keep away from its own holes (other nets' are kept
        # further away by the copper clearance already)
        h2h = b.d["rules"].get("hole_to_hole") or 0.25
        self.own_holes = {}
        for n in self.nets:
            vd = b.net_rule[n]["via_drill"]
            hs = []
            for t in b.terminals[n]:
                for it in t["items"]:
                    if it["kind"] == "via":
                        hs.append((it["at"], 0.15))
            for pd in b.d["pads"]:
                if pd["net"] == b.net_names[n] and "drill_r" in pd:
                    hs.append((pd["at"], pd["drill_r"]))
            self.own_holes[n] = np.unique(np.concatenate(
                [self.disk_cells(at, r + vd / 2 + h2h + MARGIN) for at, r in hs])) if hs else np.zeros(0, np.int32)
        self.h2h = h2h
        self.plane_mask = {}
        for n, (l, region) in b.planes.items():
            if n in self.nets:
                idx, X, Y = b.patch(region, 0)
                m = np.zeros(b.WH, bool)
                m[idx[shapely.contains_xy(region, X, Y)]] = True
                # where the pour can be at all: the region, minus other nets' fixed copper
                sl = slice(l * b.WH, (l + 1) * b.WH)
                ok = np.where(b.n1[sl] == n, b.d2[sl], b.d1[sl]) >= self.per[n]["thr"]
                self.plane_mask[n] = (l, (m & ok).reshape(b.H, b.W))
        self.pour_shrink = {}
        for n in self.plane_mask:
            for l in range(b.L):
                zc, zw = b.pour_rules(n, l)
                need = zc + zw / 2
                have = b.net_rule[n]["clearance"] + b.net_rule[n]["width"] / 2
                self.pour_shrink[(n, l)] = max(0, int(math.ceil((need - have) / b.res - 1e-6)))
        # pads a pour joins with thermal spokes: a gap ring around them, joined only by spokes
        self.therm = {}
        for n in self.plane_mask:
            for l in range(b.L):
                gap, spoke, conn = b.thermal(n, l)
                pads = []
                for t in b.terminals[n]:
                    for it in t["items"]:
                        if it["kind"] != "pad" or l not in it["geoms"]:
                            continue
                        c = it.get("zone_connection", -1)
                        c = conn if c < 0 else c
                        if c == 3:
                            c = 1 if it["pth"] else 2
                        if c in (0, 1):
                            g = it["geoms"][l]
                            ctr = np.array(it["at"], float)
                            pts = np.array(g.exterior.coords if g.geom_type == "Polygon" else
                                           [q for gg in g.geoms for q in gg.exterior.coords])
                            spokes = []
                            if c == 1:
                                for k4 in range(4):        # KiCad's four spokes, at the pad's spoke angle
                                    a = math.radians(it["spokes"] + 90 * k4)
                                    u = np.array([math.cos(a), math.sin(a)])
                                    spokes.append((u, float(((pts - ctr) @ u).max())))
                            pads.append((b.cells_in(g, l) % b.WH, ctr, spokes))
                # the fill keeps its minimum width from the gap's edge too
                zw = b.pour_rules(n, l)[1]
                self.therm[(n, l)] = (int(math.ceil((gap + zw / 2) / b.res)), max(1, int(spoke / b.res)), pads)
        self.side_mask = {}
        for (n, l), region in b.side_pours.items():
            if n in self.plane_mask:
                idx, X, Y = b.patch(region, 0)
                m = np.zeros(b.WH, bool)
                m[idx[shapely.contains_xy(region, X, Y)]] = True
                sl = slice(l * b.WH, (l + 1) * b.WH)
                ok = np.where(b.n1[sl] == n, b.d2[sl], b.d1[sl]) >= self.per[n]["thr"]
                self.side_mask[(n, l)] = (m & ok).reshape(b.H, b.W)
        # plane nets: which terminal a cell belongs to (a stub joins its own pad to the plane)
        self.term_of_cell = {}
        for n in self.plane_mask:
            m = {}
            for ti, t in enumerate(b.terminals[n]):
                for v in t["cells"]:
                    m[int(v)] = ti
            self.term_of_cell[n] = m
        self.paths = {}          # net -> list of node arrays
        self.failed = {}         # net -> list of terminals it couldn't reach
        self.stamped = set()
        self.hard = False        # the repair pass: other nets' copper is a wall

    # ---- helpers ----
    def bbox_of(self, nodes, grow_mm):
        b = self.b
        xy = nodes % b.WH
        xs, ys = xy % b.W, xy // b.W
        g = int(grow_mm / b.res)
        return [int(xs.min()) - g, int(ys.min()) - g, int(xs.max()) + g, int(ys.max()) + g]

    def in_box(self, nodes, box):
        b = self.b
        xy = nodes % b.WH
        xs, ys = xy % b.W, xy // b.W
        return nodes[(xs >= box[0]) & (xs <= box[2]) & (ys >= box[1]) & (ys <= box[3])]

    def disk_cells(self, at, r_mm):
        """xy cells (layer 0 node ids) within r_mm of a point."""
        b = self.b
        cx, cy = b.to_cell(*at)
        R = int(math.ceil(r_mm / b.res))
        ys, xs = np.mgrid[-R:R + 1, -R:R + 1]
        m = (xs * b.res) ** 2 + (ys * b.res) ** 2 < r_mm ** 2
        xs, ys = xs[m] + cx, ys[m] + cy
        k = (xs >= 0) & (xs < b.W) & (ys >= 0) & (ys < b.H)
        return (ys[k] * b.W + xs[k]).astype(np.int32)

    def novia_for(self, n, paths):
        """No-via cells for net n's next search: its pads, its holes, and near
        the vias its paths so far have (on every layer)."""
        b = self.b
        xy = [self.own_holes[n]]
        vd = b.net_rule[n]["via_drill"]
        for p in paths:
            for v in self.vias_of(p):
                l, x, y = b.node_xy(v)
                xy.append(self.disk_cells((b.cx(x), b.cy(y)), vd + self.h2h + MARGIN))
        xy = np.unique(np.concatenate(xy)) if len(xy) > 1 else xy[0]
        layers = [xy + l * b.WH for l in range(b.L)] if len(xy) else []
        return np.concatenate([self.novia[n]] + layers).astype(np.int32)

    def close_vias(self, n, path):
        """Vias of one path closer to each other than the hole-to-hole rule allows."""
        b = self.b
        vs = self.vias_of(path)
        if len(vs) < 2:
            return []
        lim = (b.net_rule[n]["via_drill"] + self.h2h + MARGIN) / b.res
        xs, ys = vs % b.W, vs // b.W
        bad = []
        for i in range(len(vs)):
            for j in range(i + 1, len(vs)):
                if math.hypot(xs[i] - xs[j], ys[i] - ys[j]) < lim:
                    bad += [int(vs[i]), int(vs[j])]
        return bad

    def full(self):
        return [0, 0, self.b.W - 1, self.b.H - 1]

    def vias_of(self, path):
        if len(path) < 2:
            return np.zeros(0, np.int32)
        WH = self.b.WH
        a, c = path[:-1], path[1:]
        m = (a % WH) == (c % WH)
        return (a[m] % WH).astype(np.int32)

    def net_nodes(self, n):
        ps = self.paths.get(n, [])
        if not ps:
            return np.zeros(0, np.int32), np.zeros(0, np.int32)
        return np.concatenate(ps).astype(np.int32), np.concatenate([self.vias_of(p) for p in ps]).astype(np.int32)

    # ---- one search ----
    def search(self, n, src, tgt, pres, grows, paths=()):
        """A* from src to tgt inside a box around tgt and the source cell nearest
        to it, grown by each of grows (mm; None = the whole board) until it works."""
        b, p = self.b, self.per[n]
        tbox = self.bbox_of(tgt, 0)
        tx, ty = (tbox[0] + tbox[2]) / 2, (tbox[1] + tbox[3]) / 2
        sxy = src % b.WH
        k = int(np.argmin((sxy % b.W - tx) ** 2 + (sxy // b.W - ty) ** 2))
        novia = self.novia_for(n, paths)
        for grow in grows:
            box = self.full() if grow is None else self.bbox_of(np.concatenate([src[k:k + 1], tgt]), grow)
            s = self.in_box(src, box)
            if not len(s):
                continue
            path = self.route_once(n, s, tgt, novia, box, tbox, pres)
            if path is not None:
                return path
        return None

    def route_once(self, n, src, tgt, novia, box, tbox, pres):
        """One A*; if the path has two of its own vias too close, forbid vias
        around them and search again."""
        b, p = self.b, self.per[n]
        for _ in range(4):
            path = self.grid.route(n, p["kt"], p["kv"], p["thr"], p["vthr"], src, tgt, self.own[n], novia,
                                   box, tbox, self.o.via_cost, pres, hard=self.hard)
            if path is None:
                return None
            bad = self.close_vias(n, path)
            if not bad:
                return path
            extra = []
            for v in bad:
                x, y = v % b.W, v // b.W
                d = self.disk_cells((b.cx(x), b.cy(y)), b.net_rule[n]["via_drill"] / 2 + MARGIN)
                extra += [d + l * b.WH for l in range(b.L)]
            novia = np.concatenate([novia] + extra).astype(np.int32)
        return None

    def route_net(self, n, pres, keep=False):
        b, o = self.b, self.o
        terms = b.terminals[n]
        paths, failed = [], []
        kept = list(self.paths.get(n, [])) if keep else []
        if n in self.plane_mask:
            pl, mask = self.plane_mask[n]
            mask = self.main_island(n)
            good = self.joined_to_plane(n, kept, mask) if keep else set()
            joined = [t["cells"] for t in terms
                      if self.in_island(t, pl, mask) or any(int(v) in good for v in t["cells"])]
            todo = [t for t in terms if not (self.in_island(t, pl, mask) or any(int(v) in good for v in t["cells"]))]
            # nearest to something joined first: then a pad can join a neighbour already joined
            # (a short track) instead of taking a via of its own
            while todo:
                jat = np.array([t["at"] for t in terms if any(np.array_equal(t["cells"], j) for j in joined)] or [[0, 0]])
                t = min(todo, key=lambda t: float(np.min(np.hypot(*(jat - t["at"]).T))))
                todo.remove(t)
                done = np.concatenate(paths + kept + joined) if paths or kept or joined else np.zeros(0, np.int32)
                for grow in (o.near, 4 * o.near, None):
                    box = self.full() if grow is None else self.bbox_of(t["cells"], grow)
                    x0, y0, x1, y1 = max(box[0], 0), max(box[1], 0), min(box[2], b.W - 1), min(box[3], b.H - 1)
                    sub = mask[y0:y1 + 1, x0:x1 + 1]
                    ys, xs = np.nonzero(sub)
                    tgt = (pl * b.WH + (ys + y0) * b.W + (xs + x0)).astype(np.int32)
                    tgt = np.concatenate([tgt, self.in_box(done, box)]) if len(done) else tgt
                    if not len(tgt):
                        continue
                    path = self.route_once(n, t["cells"], tgt, self.novia_for(n, paths),
                                           [x0, y0, x1, y1], [x0, y0, x1, y1], pres)
                    if path is not None:
                        paths.append(path)
                        joined.append(t["cells"])
                        break
                else:
                    failed.append(t)
            return paths, failed

        # Prim: grow a tree from the first terminal, always to the nearest one left
        left = list(range(1, len(terms)))
        tree_nodes = [terms[0]["cells"]]
        tree_at = [terms[0]["at"]]
        while left:
            ats = np.array(tree_at)
            best = min(left, key=lambda i: np.min(np.hypot(*(ats - terms[i]["at"]).T)))
            left.remove(best)
            t = terms[best]
            src = np.concatenate(tree_nodes)
            path = self.search(n, src, t["cells"], pres, (o.near, 4 * o.near, None), paths)
            if path is None:
                failed.append(t)
                continue
            paths.append(path)
            tree_nodes += [path, t["cells"]]
            tree_at.append(t["at"])
            # the path's points join the tree too (a later pad may be nearest to it)
            sel = path[:: max(1, len(path) // 8)]
            for v in sel:
                l, x, y = b.node_xy(v)
                tree_at.append(np.array([b.cx(x), b.cy(y)]))
        return paths, failed

    # ---- planes ----
    def pour_pieces(self, n, l, mask):
        """Net n's pour on layer l as KiCad will fill it, with every other net's
        copper as it is now (net n must not be stamped): returns (labels, group of
        each label, groups a pad's spokes reach). Pieces joined by a thermal pad's
        spokes are in one group."""
        b = self.b
        kt = self.per[n]["kt"]
        occ = self.grid.occ[kt * self.grid.N + l * b.WH: kt * self.grid.N + (l + 1) * b.WH].reshape(b.H, b.W)
        free = self.filled(n, l, mask & (occ == 0))
        clear = mask & (occ == 0)            # where a spoke may cross (no other copper)
        # (free is where the centre of the fill's minimum-width brush can be; the gap
        # ring below includes that half width, so the slivers KiCad can't fill go too)
        gap, spoke, pads = self.therm.get((n, l), (0, 1, []))
        rings = []
        for xy, ctr, spokes in pads:
            if not len(xy):
                continue
            ys, xs = xy // b.W, xy % b.W
            R = gap + 3
            y0, x0 = max(0, ys.min() - R), max(0, xs.min() - R)
            y1, x1 = min(b.H, ys.max() + R + 1), min(b.W, xs.max() + R + 1)
            loc = np.zeros((y1 - y0, x1 - x0), bool)
            loc[ys - y0, xs - x0] = True
            inner = grow(loc, gap)
            free[y0:y1, x0:x1] &= ~inner
            if spokes:
                rings.append((ctr, spokes))
        lab, k = ndimage.label(free)
        res = b.res
        sw = spoke * res

        def cell(pnt):
            x, y = b.to_cell(*pnt)
            return (x, y) if 0 <= x < b.W and 0 <= y < b.H else None

        def spoke_label(ctr, u, ext):
            """The piece a spoke from this pad edge in direction u lands in (0: none):
            the strip across the gap must be clear and land fully on one piece."""
            v = np.array([-u[1], u[0]])
            land = set()
            for t in np.linspace(-sw / 2, sw / 2, 5):
                for dist in np.arange(ext, ext + gap * res + 1e-9, res):     # across the gap
                    c = cell(ctr + u * dist + v * t)
                    if c is None or not clear[c[1], c[0]]:
                        return 0
                c = cell(ctr + u * (ext + gap * res + 1.5 * res) + v * t)  # just past it
                if c is None or not lab[c[1], c[0]]:
                    return 0
                land.add(int(lab[c[1], c[0]]))
            return land.pop() if len(land) == 1 else 0
        parent = list(range(k + 1))

        def find(i):
            while parent[i] != i:
                parent[i] = parent[parent[i]]
                i = parent[i]
            return i
        landed = []
        for ctr, spokes in rings:
            ls = [a for a in (spoke_label(ctr, u, ext) for u, ext in spokes) if a]
            landed.append(ls)
            for a in ls[1:]:
                parent[find(a)] = find(ls[0])
        group = np.array([find(i) for i in range(k + 1)])
        spoked = {int(group[a]) for ls in landed for a in ls}
        self._spoke_cache = (n, l, landed)
        return lab, group, spoked

    def main_island(self, n):
        """Where net n's plane pour joins up: the biggest group of pieces it will
        fill (net n must not be stamped)."""
        pl, mask = self.plane_mask[n]
        lab, group, _ = self.pour_pieces(n, pl, mask)
        if len(group) < 2:
            return np.zeros_like(mask)
        g = group[lab]
        sizes = np.bincount(g.ravel())
        sizes[0] = 0
        return g == int(np.argmax(sizes))

    def filled(self, n, l, free):
        """Where the pour will really be: the free cells, shrunk by what the zone's
        own clearance and minimum width take beyond the net's rules."""
        k = self.pour_shrink.get((n, l), 0)
        return shrink(free, k) if k else free

    def in_island(self, t, pl, island):
        """Is terminal t joined to the island on layer pl: its copper in it, or
        (a pad with a thermal gap) spokes' room of the island just outside the gap."""
        b = self.b
        c = t["cells"]
        c = c[(c // b.WH) == pl] % b.WH
        if not len(c):
            return False
        if island.ravel()[c].any():
            return True
        # a thermal pad: does one of its spokes land on the island?
        gap, spoke, pads = self.therm.get((t["net"], pl), (0, 1, []))
        res = b.res
        sw = spoke * res
        for it in t["items"]:
            if it["kind"] != "pad" or pl not in it["geoms"]:
                continue
            ctr = np.array(it["at"], float)
            for xy, pc, spokes in pads:
                if not np.allclose(pc, ctr):
                    continue
                for u, ext in spokes:
                    v = np.array([-u[1], u[0]])
                    ok = True
                    for tt in np.linspace(-sw / 2, sw / 2, 5):
                        x, y = b.to_cell(*(ctr + u * (ext + gap * res + 1.5 * res) + v * tt))
                        if not (0 <= x < b.W and 0 <= y < b.H and island[y, x]):
                            ok = False
                            break
                    if ok:
                        return True
        return False

    def joined_to_plane(self, n, paths, island):
        """Nodes known joined to the plane: the island, and the stubs that land on it
        (or on a stub that does)."""
        b = self.b
        pl = self.plane_mask[n][0]
        terms = b.terminals[n]
        good = set((np.nonzero(island.ravel())[0] + pl * b.WH).tolist())
        for t in terms:
            if self.in_island(t, pl, island):
                good.update(int(v) for v in t["cells"])
        pending = list(range(len(paths)))
        changed = True
        while changed and pending:
            changed = False
            for i in list(pending):
                if int(paths[i][-1]) in good:
                    good.update(int(v) for v in paths[i])
                    ti = self.term_of_cell[n].get(int(paths[i][0]))
                    if ti is not None:                  # the pad it starts from is joined now
                        good.update(int(v) for v in terms[ti]["cells"])
                    pending.remove(i)
                    changed = True
        return good

    def plane_ok(self, n):
        """Is every terminal of plane net n joined to the pour's main island?"""
        b = self.b
        pl = self.plane_mask[n][0]
        self.stamp(n, -1)
        isl = self.main_island(n).ravel()
        self.stamp(n, +1)
        paths = self.paths.get(n, [])
        good = self.joined_to_plane(n, paths, isl.reshape(b.H, b.W))
        if any(int(p[-1]) not in good for p in paths):
            return False
        for t in b.terminals[n]:
            if not self.in_island(t, pl, isl.reshape(b.H, b.W)) and not any(int(v) in good for v in t["cells"]):
                return False
        return True

    # ---- the rounds ----
    def stamp(self, n, sign):
        nodes, vias = self.net_nodes(n)
        p = self.per[n]
        self.grid.stamp(nodes, vias, p["rt"], p["rv"], sign)

    def conflicts(self, n, hadd=0.0):
        nodes, vias = self.net_nodes(n)
        p = self.per[n]
        return self.grid.conflicts(nodes, vias, p["kt"], p["kv"], hadd)

    def run(self):
        o, b = self.o, self.b
        t0 = time.time()

        def span(n):
            ats = np.array([t["at"] for t in b.terminals[n]])
            return np.ptp(ats[:, 0]) + np.ptp(ats[:, 1])
        todo = sorted(self.nets, key=span)
        pres = o.pres0
        self.history = []
        for rnd in range(1, o.max_rounds + 1):
            for n in todo:
                if n in self.stamped:
                    self.stamp(n, -1)
                    self.stamped.discard(n)
                self.paths[n], self.failed[n] = self.route_net(n, pres)
                self.stamp(n, +1)
                self.stamped.add(n)
            bad = {}
            for n in self.nets:
                c = self.conflicts(n)
                if len(c):
                    bad[n] = len(c)
            for n in self.plane_mask:
                if n not in bad and not self.plane_ok(n):
                    bad[n] = 1                       # cut off from its plane: route it again
                    o.log("  %s: a stub is cut off from the plane, again" % b.net_names[n])
            self.history.append((rnd, len(bad), sum(bad.values())))
            o.log("round %2d: %3d nets rerouted, %3d still in a fight (%d cells)  %.0fs"
                  % (rnd, len(todo), len(bad), sum(bad.values()), time.time() - t0))
            if not bad:
                break
            hot = []
            for n in bad:
                hot.append(self.conflicts(n, o.hist_add))
            # capped: beyond this, float32 path costs lose the step lengths (and at 1.6^200, overflow)
            pres = min(pres * o.pres_mult, o.pres_max)
            todo = sorted(set(bad) | self.near_nets(np.concatenate(hot), o.neighbour), key=span)
        self.settle()
        return self

    def near_nets(self, nodes, mm):
        """Nets with copper within mm of any of these nodes (any layer)."""
        b = self.b
        if not len(nodes):
            return set()
        m = np.zeros(b.WH, bool)
        m[np.unique(nodes % b.WH)] = True
        m = grow(m.reshape(b.H, b.W), max(1, int(mm / b.res))).ravel()
        out = set()
        for n in self.nets:
            nodes_n, _ = self.net_nodes(n)
            if len(nodes_n) and m[nodes_n % b.WH].any():
                out.add(n)
        return out

    def settle(self):
        """Take out the connections still in a fight, the most fought-over first,
        until nothing overlaps; then reconnect what that split, with other nets'
        copper as a wall."""
        self.hot = []            # where the leftover fights were: node ids
        while True:
            worst = None
            for n in self.nets:
                c = self.conflicts(n)
                if not len(c):
                    continue
                cs = set(int(v) for v in c)
                for i, p in enumerate(self.paths.get(n, [])):
                    k = sum(1 for v in p if int(v) in cs) + sum(1 for v in self.vias_of(p) for l in range(self.b.L)
                                                                if int(v) + l * self.b.WH in cs)
                    if k and (worst is None or k > worst[0]):
                        worst = (k, n, i, c)
            if worst is None:
                break
            _, n, i, c = worst
            self.hot.append(c)
            self.stamp(n, -1)
            del self.paths[n][i]
            self.stamp(n, +1)
        self.repair()

    def components(self, n):
        """Net n's terminals and paths grouped by what touches what: a list of
        (terminal indexes, path indexes)."""
        terms, paths = self.b.terminals[n], self.paths.get(n, [])
        T = len(terms)
        parent = list(range(T + len(paths)))

        def find(i):
            while parent[i] != i:
                parent[i] = parent[parent[i]]
                i = parent[i]
            return i
        owner = {}
        for ti, t in enumerate(terms):
            for v in t["cells"]:
                owner.setdefault(int(v), set()).add(ti)
        for pi, p in enumerate(paths):
            for v in p:
                owner.setdefault(int(v), set()).add(T + pi)
        for pi, p in enumerate(paths):
            for end in (int(p[0]), int(p[-1])):
                for o in owner.get(end, ()):
                    parent[find(o)] = find(T + pi)
        groups = {}
        for i in range(T + len(paths)):
            groups.setdefault(find(i), ([], []))
            (groups[find(i)][0] if i < T else groups[find(i)][1]).append(i if i < T else i - T)
        return [g for g in groups.values() if g[0]]

    def group_nodes(self, n, g):
        terms, paths = self.b.terminals[n], self.paths.get(n, [])
        return np.concatenate([terms[i]["cells"] for i in g[0]] + [paths[i] for i in g[1]]).astype(np.int32)

    def repair(self):
        """Join each net's pieces with walls up: a path found here can't overlap anything."""
        self.hard = True
        o = self.o
        for n in self.nets:
            self.stamp(n, -1)
            if n in self.plane_mask:
                before = len(self.paths[n])
                more, self.failed[n] = self.route_net(n, 0.0, keep=True)
                self.paths[n] = self.paths[n] + more
            else:
                self.failed[n] = []
                while True:
                    comps = self.components(n)
                    if len(comps) < 2:
                        break
                    comps.sort(key=lambda g: -len(g[0]))
                    main = self.group_nodes(n, comps[0])
                    joined = False
                    for g in comps[1:]:
                        path = self.search(n, main, self.group_nodes(n, g), 0.0, (o.near, 4 * o.near, None),
                                           self.paths[n])
                        if path is not None:
                            self.paths[n].append(path)
                            joined = True
                            break
                    if not joined:
                        for g in comps[1:]:
                            self.failed[n].append({"names": sum((self.b.terminals[n][i]["names"] for i in g[0]), [])})
                        break
            self.stamp(n, +1)
        self.hard = False
        self.stitch_islands()

    def stitch_islands(self):
        """A plane net's pour on another layer breaks into pieces around the other
        nets' tracks. A piece with none of the net's pads or tracks in it is
        connected to nothing: give it a via down to the plane."""
        b = self.b
        self.stitches = 0
        for (n, l), mask in self.side_mask.items():
            pl = self.plane_mask[n][0]
            p = self.per[n]
            self.stamp(n, -1)
            N = self.grid.N
            occ = lambda k, layer: self.grid.occ[k * N + layer * b.WH: k * N + (layer + 1) * b.WH].reshape(b.H, b.W)
            lab, group, spoked = self.pour_pieces(n, l, mask)
            k = len(group) - 1
            if k:
                island = self.main_island(n)
                # joined = one of the net's tracks or vias runs into it (a pad alone may
                # get no thermal spokes, and an island with nothing of its net is deleted)
                pads = np.concatenate([t["cells"] for t in b.terminals[n]])
                own = [pt for pt in self.paths.get(n, [])]
                own = np.concatenate(own) if own else np.zeros(0, np.int32)
                own = np.setdiff1d(own, pads)
                own = own[(own // b.WH) == l] % b.WH
                # joined: the net's tracks or vias run into it, a solidly joined pad is
                # in it, or a thermal pad's spokes reach it (the pads are all routed now)
                joined = set(group[np.unique(lab.ravel()[own])].tolist()) | spoked
                for t in b.terminals[n]:
                    c = t["cells"][(t["cells"] // b.WH) == l] % b.WH
                    joined.update(group[np.unique(lab.ravel()[c])].tolist())
                joined.discard(0)
                # where a via of this net may go: fixed copper, other nets, own holes and vias
                ok = np.ones(b.WH, bool)
                for layer in range(b.L):
                    sl = slice(layer * b.WH, (layer + 1) * b.WH)
                    ok &= np.where(b.n1[sl] == n, b.d2[sl], b.d1[sl]) >= p["vthr"]
                    ok &= b.via_block[sl] == 0
                    ok &= occ(p["kv"], layer).ravel() == 0
                nv = self.novia_for(n, self.paths.get(n, []))
                ok[nv % b.WH] = False
                ok &= island.ravel()
                ok = ok.reshape(b.H, b.W)
                for i in range(1, k + 1):
                    if group[i] != i or i in joined:
                        continue
                    piece = group[lab] == i
                    cand = piece & ok
                    if not cand.any():
                        continue
                    depth = ndimage.distance_transform_edt(piece)     # as far inside the piece as it goes
                    v = int(np.argmax(np.where(cand, depth, -1)))
                    self.paths.setdefault(n, []).append(np.array([l * b.WH + v, pl * b.WH + v], np.int32))
                    self.stitches += 1
                    # the next via keeps its distance from this one
                    ok.ravel()[self.disk_cells((b.cx(v % b.W), b.cy(v // b.W)),
                                               b.net_rule[n]["via_d"] + p["thr"] * 2) ] = False
            self.stamp(n, +1)
        if self.stitches:
            self.o.log("stitched %d pour islands to their plane" % self.stitches)

    # ---- output ----
    def segments(self, n):
        """Tracks (merged straight runs) and vias of a routed net, in mm."""
        b = self.b
        r = b.net_rule[n]
        tracks, vias = [], []
        # where a path starts or ends on another path, that one must have a segment end
        # too (KiCad counts a track end on the middle of another as dangling)
        ends = set()
        for path in self.paths.get(n, []):
            ends.add(int(path[0]))
            ends.add(int(path[-1]))
        for path in self.paths.get(n, []):
            pts = [b.node_xy(v) for v in path]
            i = 0
            while i < len(pts) - 1:
                l, x, y = pts[i]
                l2, x2, y2 = pts[i + 1]
                if (x, y) == (x2, y2):
                    vias.append((x, y))
                    i += 1
                    continue
                d = (x2 - x, y2 - y)
                j = i + 1
                while j < len(pts) - 1:
                    la, xa, ya = pts[j]
                    lb, xb, yb = pts[j + 1]
                    if lb != l or (xb - xa, yb - ya) != d or int(path[j]) in ends:
                        break
                    j += 1
                lj, xj, yj = pts[j]
                tracks.append({"net": b.net_names[n], "layer": b.layers[l],
                               "a": [round(b.cx(x), 4), round(b.cy(y), 4)],
                               "b": [round(b.cx(xj), 4), round(b.cy(yj), 4)], "w": r["width"]})
                i = j
        out_v = [{"net": b.net_names[n], "at": [round(b.cx(x), 4), round(b.cy(y), 4)],
                  "d": r["via_d"], "drill": r["via_drill"], "layers": [b.layers[0], b.layers[-1]]}
                 for x, y in sorted(set(vias))]
        return tracks, out_v

    def result(self):
        tracks, vias = [], []
        for n in self.nets:
            if n in self.stamped:
                t, v = self.segments(n)
                tracks += t
                vias += v
        unrouted = []
        for n in self.nets:
            for t in self.failed.get(n, []):
                unrouted.append({"net": self.b.net_names[n], "pads": t["names"]})
        return {"tracks": tracks, "vias": vias, "unrouted": unrouted}
