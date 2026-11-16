"""The board as the router sees it: a grid of cells per copper layer, built
from the JSON trouting_kicad.py dumps.

Static copper becomes three arrays per layer (see core.c): d1/n1 = distance to
the nearest copper and its net, d2 = distance to the nearest copper of any
other net. Distances are exact (to the real polygons, at each cell centre)
and already have the item's own clearance taken off, so the router's test is
just "distance >= half my width".
"""
import math

import numpy as np
import shapely
from shapely.geometry import LineString, MultiPolygon, Point, Polygon
from shapely.ops import unary_union

BIG = 1e6
NO_NET = -1        # copper of no net, board edge, keepouts, holes: in everyone's way
MARGIN = 0.01      # mm on every clearance, for the grid's straight lines between cell centres


def poly_of(rings):
    ps = [Polygon(r) for r in rings if len(r) >= 3]
    ps = [p if p.is_valid else p.buffer(0) for p in ps]
    return unary_union(ps) if ps else Polygon()


class Board:
    def __init__(self, d, res=0.05):
        self.d = d
        self.res = res
        self.layers = d["layers"]
        self.L = len(self.layers)
        self.li = {n: i for i, n in enumerate(self.layers)}
        rules = d["rules"]

        # nets: index, rules
        self.net_names = sorted(d["nets"])
        self.ni = {n: i for i, n in enumerate(self.net_names)}
        self.net_rule = [d["nets"][n] for n in self.net_names]
        clrs = [r["clearance"] for r in self.net_rule] or [0.2]
        self.c_ref = min(clrs)
        self.edge_clr = rules.get("edge_clearance") or 0.3
        self.hole_clr = rules.get("hole_clearance") or 0.25

        # outline (with cut-outs)
        outer = poly_of(d["outline"])
        holes = poly_of(d.get("outline_holes", []))
        self.outline = outer.difference(holes) if not holes.is_empty else outer
        x0, y0, x1, y1 = self.outline.bounds
        # cell centres on round coordinates (multiples of res), where most pads are
        self.ox = (math.floor(x0 / res) - 1.5) * res
        self.oy = (math.floor(y0 / res) - 1.5) * res
        self.W = int(math.ceil((x1 - x0) / res)) + 3
        self.H = int(math.ceil((y1 - y0) / res)) + 3
        self.WH = self.W * self.H

        self.items = []          # (layer index, geometry, net index, item clearance)
        self.collect_items()
        self.build_static()
        self.find_planes()
        self.build_terminals()

    # ---- coordinates ----
    def cx(self, x):            # cell x -> mm
        return self.ox + (x + 0.5) * self.res

    def cy(self, y):
        return self.oy + (y + 0.5) * self.res

    def to_cell(self, xm, ym):
        return int(round((xm - self.ox) / self.res - 0.5)), int(round((ym - self.oy) / self.res - 0.5))

    def node_xy(self, v):
        l, xy = divmod(int(v), self.WH)
        y, x = divmod(xy, self.W)
        return l, x, y

    def patch(self, geom, grow):
        """Cell index ranges and centre coordinates covering geom's bbox + grow."""
        bx0, by0, bx1, by1 = geom.bounds
        xa = max(0, int((bx0 - grow - self.ox) / self.res))
        xb = min(self.W - 1, int((bx1 + grow - self.ox) / self.res) + 1)
        ya = max(0, int((by0 - grow - self.oy) / self.res))
        yb = min(self.H - 1, int((by1 + grow - self.oy) / self.res) + 1)
        xs = self.ox + (np.arange(xa, xb + 1) + 0.5) * self.res
        ys = self.oy + (np.arange(ya, yb + 1) + 0.5) * self.res
        X, Y = np.meshgrid(xs, ys)
        idx = (np.arange(ya, yb + 1)[:, None] * self.W + np.arange(xa, xb + 1)[None, :])
        return idx.ravel(), X.ravel(), Y.ravel()

    def cells_in(self, geom, layer, shrink=0.0):
        """Node ids of the cells whose centre is inside geom (optionally shrunk)."""
        g = geom.buffer(-shrink) if shrink > 0 else geom
        if g.is_empty:
            return np.zeros(0, np.int32)
        idx, X, Y = self.patch(g, self.res)
        m = shapely.contains_xy(g, X, Y)
        return (layer * self.WH + idx[m]).astype(np.int32)

    # ---- static copper ----
    def net_clr(self, n):
        return self.net_rule[n]["clearance"] if n >= 0 else self.c_ref

    def collect_items(self):
        d = self.d
        for p in d["pads"]:
            n = self.ni.get(p["net"], NO_NET) if p["net"] else NO_NET
            for ln, rings in p["shapes"].items():
                if ln in self.li:
                    self.items.append((self.li[ln], poly_of(rings), n, self.net_clr(n)))
        for s in d["copper_shapes"]:
            n = self.ni.get(s["net"], NO_NET) if s["net"] else NO_NET
            if s["layer"] in self.li:
                self.items.append((self.li[s["layer"]], poly_of(s["polys"]), n, self.net_clr(n)))
        for t in d["tracks"]:          # what dump kept: locked (or all, without --rip-up)
            n = self.ni.get(t["net"], NO_NET)
            if t["layer"] in self.li:
                g = LineString([t["a"], t["b"]]).buffer(t["w"] / 2)
                self.items.append((self.li[t["layer"]], g, n, self.net_clr(n)))
        for v in d["vias"]:
            n = self.ni.get(v["net"], NO_NET)
            g = Point(v["at"]).buffer(v["d"] / 2)
            for l in range(self.L):
                self.items.append((l, g, n, self.net_clr(n)))
        for h in d["holes"]:
            g = Point(h["at"]).buffer(h["r"])
            for l in range(self.L):
                self.items.append((l, g, NO_NET, self.hole_clr))
        for k in d["keepouts"]:
            if k["tracks"]:
                g = poly_of(k["polys"])
                for ln in k["layers"]:
                    if ln in self.li:
                        self.items.append((self.li[ln], g, NO_NET, 0.0))

    def build_static(self):
        L, WH = self.L, self.WH
        self.d1 = np.full(L * WH, BIG, np.float32)
        self.d2 = np.full(L * WH, BIG, np.float32)
        self.n1 = np.full(L * WH, -2, np.int32)
        wmax = max([r["width"] for r in self.net_rule] + [0.25])
        vmax = max([r["via_d"] for r in self.net_rule] + [0.6])
        cmax = max([r["clearance"] for r in self.net_rule] + [self.edge_clr, self.hole_clr])
        self.rmax = max(wmax, vmax) / 2 + cmax + 0.1
        for l, g, n, c in self.items:
            if g.is_empty:
                continue
            idx, X, Y = self.patch(g, self.rmax)
            dist = shapely.distance(g, shapely.points(X, Y)) - c
            self.merge(l * WH + idx, dist.astype(np.float32), n)

        # board edge: outside is blocked outright, near the edge keeps edge clearance
        allidx, X, Y = self.patch(self.outline, self.res)
        inside = shapely.contains_xy(self.outline, X, Y)
        band = self.outline.boundary.buffer(self.rmax)
        shapely.prepare(band)
        near = inside & shapely.contains_xy(band, X, Y)
        dist = np.full(len(X), -BIG, np.float32)
        dist[inside] = BIG
        dist[near] = shapely.distance(self.outline.boundary, shapely.points(X[near], Y[near])) - self.edge_clr
        sel = dist < BIG
        for l in range(L):
            self.merge(l * WH + allidx[sel], dist[sel], NO_NET)
        self.inside = np.zeros(WH, bool)
        self.inside[allidx[inside]] = True

        # where vias may not go
        self.via_block = np.zeros(L * WH, np.uint8)
        for k in self.d["keepouts"]:
            if k["vias"]:
                g = poly_of(k["polys"]).buffer(vmax / 2)
                for ln in k["layers"]:
                    if ln in self.li:
                        self.via_block[self.cells_in(g, self.li[ln])] = 1

    def merge(self, idx, dn, n):
        d1, d2, n1 = self.d1[idx], self.d2[idx], self.n1[idx]
        same = n1 == n
        closer = ~same & (dn < d1)
        other = ~same & ~closer
        # same net as the nearest: it may only get nearer
        d1 = np.where(same, np.minimum(d1, dn), d1)
        # a new nearest, of another net: the old nearest becomes the nearest other
        d2 = np.where(closer, d1, d2)
        n1 = np.where(closer, n, n1)
        d1 = np.where(closer, dn, d1)
        # farther, of another net than the nearest
        d2 = np.where(other, np.minimum(d2, dn), d2)
        self.d1[idx], self.d2[idx], self.n1[idx] = d1, d2, n1

    # ---- pours that act as planes ----
    def find_planes(self):
        """A net whose pour covers >= 25 % of the board on a layer gets that layer
        as its plane: its pads there are connected by the pour, its other pads
        get a short track and a via down to it."""
        area = self.outline.area
        best = {}
        for p in self.d["pours"]:
            n = self.ni.get(p["net"])
            if n is None:
                continue
            g = poly_of(p["polys"]).intersection(self.outline)
            for ln in p["layers"]:
                if ln not in self.li:
                    continue
                l = self.li[ln]
                region = g
                for k in self.d["keepouts"]:
                    if k.get("pours") and ln in k["layers"]:
                        region = region.difference(poly_of(k["polys"]))
                a = region.area
                if a >= 0.25 * area:
                    # bigger wins; within 10 %, the lower layer (planes are usually at the back)
                    key = (round(a / (0.1 * area)), l)
                    if n not in best or key > best[n][0]:
                        best[n] = (key, l, region)
        self.planes = {n: (l, region) for n, (_, l, region) in best.items()}
        self.plane_layers = {l for l, _ in self.planes.values()}

    # ---- what has to be connected ----
    def build_terminals(self):
        """Per net, groups of copper already joined (pads, locked tracks and
        vias that touch). Each group is one terminal the router connects."""
        d = self.d
        per = {}
        for p in d["pads"]:
            if not p["net"] or p["net"] not in self.ni:
                continue
            n = self.ni[p["net"]]
            geoms = {self.li[ln]: poly_of(r) for ln, r in p["shapes"].items() if ln in self.li}
            per.setdefault(n, []).append({"kind": "pad", "name": "%s.%s" % (p["ref"], p["num"]),
                                          "at": p["at"], "geoms": geoms, "pth": "drill_r" in p})
        for t in d["tracks"]:
            if t["net"] in self.ni and t["layer"] in self.li:
                n = self.ni[t["net"]]
                g = LineString([t["a"], t["b"]]).buffer(t["w"] / 2)
                per.setdefault(n, []).append({"kind": "track", "name": "track", "at": t["a"],
                                              "geoms": {self.li[t["layer"]]: g}})
        for v in d["vias"]:
            if v["net"] in self.ni:
                n = self.ni[v["net"]]
                g = Point(v["at"]).buffer(v["d"] / 2)
                per.setdefault(n, []).append({"kind": "via", "name": "via", "at": v["at"],
                                              "geoms": {l: g for l in range(self.L)}})
        self.terminals = {}
        for n, items in per.items():
            parent = list(range(len(items)))

            def find(i):
                while parent[i] != i:
                    parent[i] = parent[parent[i]]
                    i = parent[i]
                return i
            for i in range(len(items)):
                for j in range(i + 1, len(items)):
                    a, b = items[i]["geoms"], items[j]["geoms"]
                    if any(l in b and a[l].intersects(b[l]) for l in a):
                        parent[find(i)] = find(j)
            groups = {}
            for i, it in enumerate(items):
                groups.setdefault(find(i), []).append(it)
            self.terminals[n] = [self.make_terminal(n, g) for g in groups.values()]

    def make_terminal(self, n, items):
        r = self.net_rule[n]
        w = r["width"]
        cells, own, novia = [], [], []
        for it in items:
            for l, g in it["geoms"].items():
                cells.append(self.cells_in(g, l))
                own.append(self.cells_in(g, l, shrink=w / 2))
                if it["kind"] == "pad":
                    novia.append(self.cells_in(g, l))
        cat = lambda a: np.unique(np.concatenate(a)).astype(np.int32) if a else np.zeros(0, np.int32)
        t = {"net": n, "items": items, "cells": cat(cells), "own": cat(own), "novia": cat(novia),
             "names": [it["name"] for it in items if it["kind"] == "pad"],
             "at": np.mean([it["at"] for it in items], axis=0)}
        if n in self.planes:
            pl, region = self.planes[n]
            t["on_plane"] = any(pl in it["geoms"] and it["geoms"][pl].intersects(region) for it in items)
        return t
