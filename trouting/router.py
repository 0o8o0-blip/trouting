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


class Options:
    res = 0.05
    via_cost = 15.0          # cells (x res mm) a via is worth in track length
    plane_layer_cost = 2.5   # tracks on a plane layer cut the plane
    pres0 = 0.5              # first round's penalty per other net in the way
    pres_mult = 1.6
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
        half = [w / 2 for w in self.tw] + [v / 2 for v in self.vd]
        cmax = [0.0] * self.K
        for r in rules:
            for k in (self.tw.index(r["width"]), len(self.tw) + self.vd.index(r["via_d"])):
                cmax[k] = max(cmax[k], r["clearance"])
        self.per = []
        for n, r in enumerate(rules):
            c = r["clearance"]
            extra = max(0.0, c - b.c_ref)
            rt = [(r["width"] / 2 + max(c, cmax[k]) + half[k] + MARGIN) / res for k in range(self.K)]
            rv = [(r["via_d"] / 2 + max(c, cmax[k]) + half[k] + MARGIN) / res for k in range(self.K)]
            self.per.append({
                "kt": self.tw.index(r["width"]), "kv": len(self.tw) + self.vd.index(r["via_d"]),
                "thr": r["width"] / 2 + extra + MARGIN, "vthr": r["via_d"] / 2 + extra + MARGIN,
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
            for t in terms:
                if self.in_island(t, pl, mask) or any(int(v) in good for v in t["cells"]):
                    continue
                done = np.concatenate(paths + kept) if paths or kept else np.zeros(0, np.int32)
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
    def main_island(self, n):
        """The biggest connected piece the pour will fill on net n's plane layer,
        with every other net's copper as it is now (net n must not be stamped)."""
        b = self.b
        pl, mask = self.plane_mask[n]
        kt = self.per[n]["kt"]
        occ = self.grid.occ[kt * self.grid.N + pl * b.WH: kt * self.grid.N + (pl + 1) * b.WH].reshape(b.H, b.W)
        lab, k = ndimage.label(mask & (occ == 0))
        if k == 0:
            return np.zeros_like(mask)
        sizes = np.bincount(lab.ravel())
        sizes[0] = 0
        return lab == int(np.argmax(sizes))

    def in_island(self, t, pl, island):
        c = t["cells"]
        c = c[(c // self.b.WH) == pl] % self.b.WH
        return bool(len(c)) and bool(island.ravel()[c].any())

    def joined_to_plane(self, n, paths, island):
        """Nodes known joined to the plane: the island, and the stubs that land on it
        (or on a stub that does)."""
        b = self.b
        pl = self.plane_mask[n][0]
        good = set((np.nonzero(island.ravel())[0] + pl * b.WH).tolist())
        pending = list(range(len(paths)))
        changed = True
        while changed and pending:
            changed = False
            for i in list(pending):
                if int(paths[i][-1]) in good:
                    good.update(int(v) for v in paths[i])
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
            pres *= o.pres_mult
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
        m = ndimage.binary_dilation(m.reshape(b.H, b.W), iterations=max(1, int(mm / b.res))).ravel()
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
