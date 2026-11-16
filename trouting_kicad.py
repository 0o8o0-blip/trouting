#!/usr/bin/env python3
"""The KiCad half of Trouting: the only part that needs KiCad's Python (pcbnew).
The router itself runs outside KiCad, on the JSON this writes.

    python3 trouting_kicad.py dump  board.kicad_pcb board.json [--rip-up]
    python3 trouting_kicad.py apply board.kicad_pcb routes.json out.kicad_pcb [--rip-up]

dump:  everything the router needs, in mm, absolute board coordinates:
       copper layers, design rules per net (from the net classes), every pad
       (outline polygon per copper layer, drill), NPTH holes, the board outline,
       keepouts, pours, existing tracks/vias, and the footprints (position,
       rotation, side, courtyard) for the placement suggestions.
       --rip-up leaves out tracks/vias that aren't locked (they get rerouted).
apply: adds the routed tracks and vias, refills the pours, saves to out.
       --rip-up first removes the unlocked tracks/vias, as dump left them out.
"""
import json
import sys

import pcbnew

EPS = pcbnew.FromMM(0.002)      # polygon approximation error for round shapes


def mm(v):
    return round(v / 1e6, 5)


def pt(p):
    return [mm(p.x), mm(p.y)]


def polys(polyset):
    """A SHAPE_POLY_SET as a list of outlines (holes dropped: pads have none)."""
    return [[pt(o.CPoint(k)) for k in range(o.PointCount())]
            for o in (polyset.Outline(i) for i in range(polyset.OutlineCount()))]


def copper_layers(board):
    return [l for l in board.GetEnabledLayers().CuStack()]


def layer_names(board, ids):
    return [board.GetLayerName(l) for l in ids]


def net_rules(board):
    """Per net: clearance, track width, via diameter/drill (mm), from its net class."""
    ds = board.GetDesignSettings()
    out = {}
    ns = ds.m_NetSettings
    for code, net in board.GetNetsByNetcode().items():
        if code <= 0:
            continue
        nc = ns.GetEffectiveNetClass(net.GetNetname())   # KiCad 9 (net.GetNetClass() isn't wrapped)
        def get(name, fallback):
            try:
                v = getattr(nc, name)()
                return mm(v) if v and v > 0 else fallback
            except Exception:
                return fallback
        out[net.GetNetname()] = {
            "class": nc.GetName(),
            "clearance": get("GetClearance", mm(ds.m_MinClearance) or 0.2),
            "width": get("GetTrackWidth", mm(ds.GetCurrentTrackWidth()) or 0.25),
            "via_d": get("GetViaDiameter", mm(ds.GetCurrentViaSize()) or 0.6),
            "via_drill": get("GetViaDrill", mm(ds.GetCurrentViaDrill()) or 0.3),
        }
    return out


def is_unlocked_copper(t):
    return not t.IsLocked()


def dump(board, path, rip_up):
    cu = copper_layers(board)
    names = layer_names(board, cu)
    ds = board.GetDesignSettings()
    d = {
        "layers": names,
        "rules": {
            "min_clearance": mm(ds.m_MinClearance),
            "min_track": mm(ds.m_TrackMinWidth),
            "min_via_d": mm(ds.m_ViasMinSize),
            "min_via_drill": mm(ds.m_MinThroughDrill),
            "hole_clearance": mm(ds.m_HoleClearance),
            "hole_to_hole": mm(ds.m_HoleToHoleMin),
            "edge_clearance": mm(ds.m_CopperEdgeClearance),
        },
        "nets": net_rules(board),
        "pads": [], "holes": [], "keepouts": [], "pours": [],
        "tracks": [], "vias": [], "footprints": [], "copper_shapes": [],
    }

    for f in board.GetFootprints():
        ref = f.GetReference()
        cy = f.GetCourtyard(pcbnew.B_CrtYd if f.IsFlipped() else pcbnew.F_CrtYd)
        bb = f.GetBoundingBox(False, False)
        d["footprints"].append({
            "ref": ref, "at": pt(f.GetPosition()), "rot": round(f.GetOrientationDegrees(), 3),
            "side": "B" if f.IsFlipped() else "F", "locked": f.IsLocked(),
            "courtyard": polys(cy) if cy.OutlineCount() else [],
            "bbox": [mm(bb.GetLeft()), mm(bb.GetTop()), mm(bb.GetRight()), mm(bb.GetBottom())],
        })
        for p in f.Pads():
            if p.GetAttribute() == pcbnew.PAD_ATTRIB_NPTH:
                dr = p.GetDrillSize()
                d["holes"].append({"at": pt(p.GetPosition()), "r": mm(max(dr.x, dr.y)) / 2})
                continue
            e = {"ref": ref, "num": p.GetNumber(), "net": p.GetNetname(),
                 "at": pt(p.GetPosition()), "shapes": {}}
            for l, n in zip(cu, names):
                if p.IsOnLayer(l) and p.FlashLayer(l):
                    ps = pcbnew.SHAPE_POLY_SET()
                    p.TransformShapeToPolygon(ps, l, 0, EPS, pcbnew.ERROR_INSIDE)
                    e["shapes"][n] = polys(ps)
            if not e["shapes"]:
                continue
            if p.HasHole():
                dr = p.GetDrillSize()
                e["drill_r"] = mm(max(dr.x, dr.y)) / 2
            d["pads"].append(e)
        for g in f.GraphicalItems():            # copper drawn in footprints (antennas, coils, logos)
            if isinstance(g, pcbnew.PCB_SHAPE) and g.IsOnCopperLayer() and g.GetLayer() in cu:
                ps = pcbnew.SHAPE_POLY_SET()
                g.TransformShapeToPolygon(ps, g.GetLayer(), 0, EPS, pcbnew.ERROR_INSIDE)
                d["copper_shapes"].append({"layer": board.GetLayerName(g.GetLayer()),
                                           "net": g.GetNetname() if hasattr(g, "GetNetname") else "",
                                           "polys": polys(ps)})
    for g in board.GetDrawings():
        if isinstance(g, pcbnew.PCB_SHAPE) and g.IsOnCopperLayer() and g.GetLayer() in cu:
            ps = pcbnew.SHAPE_POLY_SET()
            g.TransformShapeToPolygon(ps, g.GetLayer(), 0, EPS, pcbnew.ERROR_INSIDE)
            d["copper_shapes"].append({"layer": board.GetLayerName(g.GetLayer()),
                                       "net": g.GetNetname(), "polys": polys(ps)})

    zones = list(board.Zones())
    for f in board.GetFootprints():
        zones += list(f.Zones())
    for z in zones:
        zl = [n for l, n in zip(cu, names) if z.GetLayerSet().Contains(l)]
        if z.GetIsRuleArea():
            d["keepouts"].append({"name": z.GetZoneName(), "layers": zl,
                                  "tracks": z.GetDoNotAllowTracks(), "vias": z.GetDoNotAllowVias(),
                                  "pads": z.GetDoNotAllowPads(), "pours": z.GetDoNotAllowZoneFills() if hasattr(z, "GetDoNotAllowZoneFills") else z.GetDoNotAllowCopperPour(),
                                  "polys": polys(z.Outline())})
        elif zl:
            d["pours"].append({"net": z.GetNetname(), "layers": zl, "priority": z.GetAssignedPriority(),
                               "polys": polys(z.Outline())})

    for t in board.GetTracks():
        if rip_up and is_unlocked_copper(t):
            continue
        if isinstance(t, pcbnew.PCB_VIA):
            top, bot = t.TopLayer(), t.BottomLayer()
            d["vias"].append({"net": t.GetNetname(), "at": pt(t.GetPosition()),
                              "d": mm(t.GetWidth(top)), "drill": mm(t.GetDrillValue()),
                              "layers": [board.GetLayerName(top), board.GetLayerName(bot)],
                              "locked": t.IsLocked()})
        else:
            e = {"net": t.GetNetname(), "layer": board.GetLayerName(t.GetLayer()),
                 "a": pt(t.GetStart()), "b": pt(t.GetEnd()), "w": mm(t.GetWidth()),
                 "locked": t.IsLocked()}
            if isinstance(t, pcbnew.PCB_ARC):
                e["mid"] = pt(t.GetMid())
            d["tracks"].append(e)

    outline = pcbnew.SHAPE_POLY_SET()
    ok = board.GetBoardPolygonOutlines(outline)
    d["outline"] = polys(outline) if ok and outline.OutlineCount() else []
    d["outline_holes"] = []
    for i in range(outline.OutlineCount()):
        for h in range(outline.HoleCount(i)):
            o = outline.Hole(i, h)
            d["outline_holes"].append([pt(o.CPoint(k)) for k in range(o.PointCount())])
    if not d["outline"]:
        bb = board.ComputeBoundingBox(False)
        d["outline"] = [[[mm(bb.GetLeft()), mm(bb.GetTop())], [mm(bb.GetRight()), mm(bb.GetTop())],
                         [mm(bb.GetRight()), mm(bb.GetBottom())], [mm(bb.GetLeft()), mm(bb.GetBottom())]]]
        d["outline_missing"] = True

    # stable order, so a run repeats exactly
    d["pads"].sort(key=lambda e: (e["ref"], e["num"], e["at"]))
    d["footprints"].sort(key=lambda e: e["ref"])
    with open(path, "w") as fh:
        json.dump(d, fh)
    print("dumped %d copper layers, %d footprints, %d pads, %d nets, %d tracks, %d vias%s"
          % (len(names), len(d["footprints"]), len(d["pads"]), len(d["nets"]),
             len(d["tracks"]), len(d["vias"]), " (NO board outline, using bounding box)" if d.get("outline_missing") else ""))


def apply(board, path, out, rip_up):
    r = json.load(open(path))
    nets = {n.GetNetname(): n for n in board.GetNetsByNetcode().values()}
    lid = {board.GetLayerName(l): l for l in copper_layers(board)}
    if rip_up:
        for t in list(board.GetTracks()):
            if is_unlocked_copper(t):
                board.Delete(t)      # Delete, not Remove: KiCad 9 crashes later on removed items

    def P(xy):
        return pcbnew.VECTOR2I(pcbnew.FromMM(xy[0]), pcbnew.FromMM(xy[1]))
    for t in r["tracks"]:
        s = pcbnew.PCB_TRACK(board)
        s.SetStart(P(t["a"])); s.SetEnd(P(t["b"])); s.SetWidth(pcbnew.FromMM(t["w"]))
        s.SetLayer(lid[t["layer"]]); s.SetNet(nets[t["net"]])
        board.Add(s)
    for v in r["vias"]:
        s = pcbnew.PCB_VIA(board)
        s.SetPosition(P(v["at"])); s.SetWidth(pcbnew.FromMM(v["d"])); s.SetDrill(pcbnew.FromMM(v["drill"]))
        a, b = v.get("layers", [None, None])
        s.SetLayerPair(lid.get(a, pcbnew.F_Cu), lid.get(b, pcbnew.B_Cu))
        s.SetNet(nets[v["net"]])
        board.Add(s)
    if len(board.Zones()):
        filler = pcbnew.ZONE_FILLER(board)       # keep it referenced while it fills (a temporary crashes)
        filler.Fill(board.Zones())
    board.Save(out)
    print("applied %d tracks, %d vias -> %s" % (len(r["tracks"]), len(r["vias"]), out), flush=True)


if __name__ == "__main__":
    import os
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    rip = "--rip-up" in sys.argv
    b = pcbnew.LoadBoard(args[1])
    if args[0] == "dump":
        dump(b, args[2], rip)
    elif args[0] == "apply":
        apply(b, args[2], args[3], rip)
    else:
        sys.exit(__doc__)
    # KiCad's bindings can crash while Python tears them down at exit; the work is
    # saved by now, so leave without the teardown.
    sys.stdout.flush()
    os._exit(0)
