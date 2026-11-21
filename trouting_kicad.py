#!/usr/bin/env python3
"""The KiCad half of Trouting: the only part that needs KiCad's Python (pcbnew).
The router itself runs outside KiCad, on the JSON this writes.

    python3 trouting_kicad.py dump  board.kicad_pcb board.json [--rip-up]
    python3 trouting_kicad.py apply board.kicad_pcb routes.json out.kicad_pcb [--rip-up]
    python3 trouting_kicad.py fills board.kicad_pcb fills.json      (debugging: KiCad's pour fill)

dump:  everything the router needs, in mm, absolute board coordinates:
       copper layers, design rules per net (from the net classes), every pad
       (outline polygon per copper layer, drill), NPTH holes, the board outline,
       keepouts, pours, existing tracks/vias, and the footprints (position,
       rotation, side, courtyard) for the placement suggestions.
       --rip-up leaves out tracks/vias that aren't locked (they get rerouted).
apply: adds the routed tracks and vias, refills the pours, saves to out.
       --rip-up first removes the unlocked tracks/vias and all teardrops, as dump
       left them out (KiCad can add teardrops again: Edit > Edit Teardrops).
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
    """A SHAPE_POLY_SET's outlines, as lists of points (their holes: holes())."""
    return [[pt(o.CPoint(k)) for k in range(o.PointCount())]
            for o in (polyset.Outline(i) for i in range(polyset.OutlineCount()))]


def holes(polyset):
    """The holes of each of polys(polyset)'s outlines (a zone drawn as a frame
    has one; read without it, it would cover the frame's middle too)."""
    return [[[pt(h.CPoint(k)) for k in range(h.PointCount())] for h in (polyset.Hole(i, j) for j in range(polyset.HoleCount(i)))]
            for i in range(polyset.OutlineCount())]


def local_clearance(item):
    """A pad's or footprint's clearance override in mm (None: none set). KiCad 9
    gives an optional, older versions an int (0: none)."""
    try:
        c = item.GetLocalClearance()
    except Exception:
        return None
    if hasattr(c, "has_value"):
        c = c.value() if c.has_value() else 0
    return mm(c) if c else None


def add_copper_drawing(board, g, cu, d, ref):
    """A drawn item on a copper layer as copper: a shape, or text (copper text is copper
    too; a track through it is a short). Hidden text isn't there."""
    kinds = tuple(getattr(pcbnew, k) for k in ("PCB_SHAPE", "PCB_TEXT", "PCB_TEXTBOX") if hasattr(pcbnew, k))
    if not isinstance(g, kinds) or not g.IsOnCopperLayer() or g.GetLayer() not in cu:
        return
    if isinstance(g, pcbnew.PCB_TEXT) and hasattr(g, "IsVisible") and not g.IsVisible():
        return
    ps = pcbnew.SHAPE_POLY_SET()
    try:
        g.TransformShapeToPolygon(ps, g.GetLayer(), 0, EPS, pcbnew.ERROR_INSIDE)
    except Exception:
        return
    if not ps.OutlineCount():
        return
    e = {"layer": board.GetLayerName(g.GetLayer()), "net": g.GetNetname() if hasattr(g, "GetNetname") else "",
         "polys": polys(ps), "holes": holes(ps)}
    if ref is not None:
        e["ref"] = ref
    d["copper_shapes"].append(e)


def add_mask_opening(g, d, ref=None):
    """An opening in the solder mask that no copper pad makes (a mask-only pad or a
    drawn shape): copper of two nets in it is a solder mask bridge."""
    for ml, side in ((pcbnew.F_Mask, "F"), (pcbnew.B_Mask, "B")):
        if not g.IsOnLayer(ml):
            continue
        ps = pcbnew.SHAPE_POLY_SET()
        try:
            g.TransformShapeToPolygon(ps, ml, 0, EPS, pcbnew.ERROR_OUTSIDE)
        except Exception:
            continue
        if ps.OutlineCount():
            e = {"side": side, "polys": polys(ps), "holes": holes(ps)}
            if ref is not None:
                e["ref"] = ref                  # it moves with its footprint
            d["mask_openings"].append(e)


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
            "min_connection": mm(getattr(ds, "m_MinConn", 0)),
            "min_via_d": mm(ds.m_ViasMinSize),
            "min_via_drill": mm(ds.m_MinThroughDrill),
            "hole_clearance": mm(ds.m_HoleClearance),
            "hole_to_hole": mm(ds.m_HoleToHoleMin),
            "edge_clearance": mm(ds.m_CopperEdgeClearance),
        },
        "nets": net_rules(board),
        "pads": [], "holes": [], "keepouts": [], "pours": [],
        "tracks": [], "vias": [], "footprints": [], "copper_shapes": [], "mask_openings": [],
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
                h = {"ref": ref, "at": pt(p.GetPosition()), "r": mm(max(dr.x, dr.y)) / 2}
                c = local_clearance(p) or local_clearance(f)
                if c:    # its own clearance (a mounting hole's keep-away): the fill keeps it
                    h["clearance"] = c
                d["holes"].append(h)
                # an NPTH pad bigger than its hole has a ring of copper (of no net), which
                # DRC keeps the pad's clearance from: it goes on as a net-less pad
            npth = p.GetAttribute() == pcbnew.PAD_ATTRIB_NPTH
            e = {"ref": ref, "num": p.GetNumber(), "net": "" if npth else p.GetNetname(),
                 "at": pt(p.GetPosition()), "shapes": {},
                 # how pours join it: -1 as the zone says, 0 not, 1 thermal, 2 solid, 3 thermal if PTH
                 "zone_connection": int(p.GetLocalZoneConnection()) if hasattr(p, "GetLocalZoneConnection") else -1,
                 "rot": round(p.GetOrientationDegrees(), 3),
                 "spoke_angle": round(p.GetThermalSpokeAngleDegrees(), 3) if hasattr(p, "GetThermalSpokeAngleDegrees") else 90.0}
            try:     # pad, footprint and net class overrides, as DRC uses them
                e["clearance"] = mm(max(p.GetOwnClearance(l) for l in cu if p.IsOnLayer(l)))
            except Exception:
                pass
            for l, n in zip(cu, names):
                if p.IsOnLayer(l) and p.FlashLayer(l):
                    ps = pcbnew.SHAPE_POLY_SET()
                    p.TransformShapeToPolygon(ps, l, 0, EPS, pcbnew.ERROR_INSIDE)
                    e["shapes"][n] = polys(ps)
            if not e["shapes"]:
                add_mask_opening(p, d, ref)     # a mask-only pad
                continue
            if npth:
                # KiCad's DRC holds an NPTH pad's copper ring against tracks on every layer
                # its hole goes through, also those where the pad has no copper (inner
                # layers): keep clear of it everywhere
                ring = max(e["shapes"].values(), key=lambda r: sum(len(x) for x in r))
                for n in names:
                    e["shapes"].setdefault(n, ring)
            if p.HasHole() and not npth:
                dr = p.GetDrillSize()
                e["drill_r"] = mm(max(dr.x, dr.y)) / 2
            d["pads"].append(e)
        # copper drawn in footprints (antennas, coils, logos), and its text and fields on copper
        fields = list(f.GetFields()) if hasattr(f, "GetFields") else [f.Reference(), f.Value()]
        for g in list(f.GraphicalItems()) + fields:
            add_copper_drawing(board, g, cu, d, ref)
            if isinstance(g, pcbnew.PCB_SHAPE):
                add_mask_opening(g, d, ref)
    for g in board.GetDrawings():
        add_copper_drawing(board, g, cu, d, None)
        if isinstance(g, pcbnew.PCB_SHAPE):
            add_mask_opening(g, d)

    zones = [(z, None) for z in board.Zones()]
    for f in board.GetFootprints():
        zones += [(z, f.GetReference()) for z in f.Zones()]      # these move with their footprint
    for z, zref in zones:
        if rip_up and z.IsTeardropArea():
            continue                 # teardrops belong to the tracks being ripped up
        zl = [n for l, n in zip(cu, names) if z.GetLayerSet().Contains(l)]
        if z.GetIsRuleArea():
            d["keepouts"].append({"ref": zref, "name": z.GetZoneName(), "layers": zl,
                                  "tracks": z.GetDoNotAllowTracks(), "vias": z.GetDoNotAllowVias(),
                                  "pads": z.GetDoNotAllowPads(), "pours": z.GetDoNotAllowZoneFills() if hasattr(z, "GetDoNotAllowZoneFills") else z.GetDoNotAllowCopperPour(),
                                  "polys": polys(z.Outline()), "holes": holes(z.Outline())})
        elif zl:
            lc = z.GetLocalClearance()
            lc = lc.value() if hasattr(lc, "value") else lc      # KiCad 9: std::optional
            d["pours"].append({"ref": zref, "net": z.GetNetname(), "layers": zl, "priority": z.GetAssignedPriority(),
                               "clearance": mm(lc or 0), "min_width": mm(z.GetMinThickness()),
                               "thermal_gap": mm(z.GetThermalReliefGap()),
                               "spoke_width": mm(z.GetThermalReliefSpokeWidth()),
                               "pad_connection": int(z.GetPadConnection()),
                               "polys": polys(z.Outline()), "holes": holes(z.Outline())})

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
    if "aInferOutlineIfNecessary" in (pcbnew.BOARD.GetBoardPolygonOutlines.__doc__ or ""):
        ok = board.GetBoardPolygonOutlines(outline, False)   # KiCad 10; no outline: our own fallback below
    else:
        ok = board.GetBoardPolygonOutlines(outline)          # KiCad 9
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
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(d, fh)
    print("dumped %d copper layers, %d footprints, %d pads, %d nets, %d tracks, %d vias%s"
          % (len(names), len(d["footprints"]), len(d["pads"]), len(d["nets"]),
             len(d["tracks"]), len(d["vias"]), " (NO board outline, using bounding box)" if d.get("outline_missing") else ""))


def fills(board, path):
    """The pours' filled copper as KiCad made it (outlines with holes), per net and layer."""
    out = []
    for z in board.Zones():
        if z.GetIsRuleArea() or z.IsTeardropArea():
            continue
        for l in z.GetLayerSet().CuStack():
            fp = z.GetFilledPolysList(l)
            for i in range(fp.OutlineCount()):
                o = fp.Outline(i)
                out.append({"net": z.GetNetname(), "layer": board.GetLayerName(l),
                            "outline": [pt(o.CPoint(k)) for k in range(o.PointCount())],
                            "holes": [[pt(h.CPoint(k)) for k in range(h.PointCount())]
                                      for h in (fp.Hole(i, j) for j in range(fp.HoleCount(i)))]})
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(out, fh)
    print("%d filled pieces" % len(out))


def apply(board, path, out, rip_up):
    with open(path, encoding="utf-8") as fh:
        r = json.load(fh)
    apply_routes(board, r, rip_up)
    board.Save(out)
    print("applied %d tracks, %d vias -> %s" % (len(r["tracks"]), len(r["vias"]), out), flush=True)


def apply_routes(board, r, rip_up):
    """Put routes {"tracks", "vias"} on the board (taking the unlocked copper off
    first, with rip_up) and refill the zones. Returns the new items."""
    new = []
    nets = {n.GetNetname(): n for n in board.GetNetsByNetcode().values()}
    lid = {board.GetLayerName(l): l for l in copper_layers(board)}
    if rip_up:
        for t in list(board.GetTracks()):
            if is_unlocked_copper(t):
                board.Delete(t)      # Delete, not Remove: KiCad 9 crashes later on removed items
        # the ripped-up tracks' teardrops (zones in KiCad 9) would be left as stray copper
        for z in list(board.Zones()):
            if z.IsTeardropArea():
                board.Delete(z)

    def P(xy):
        return pcbnew.VECTOR2I(pcbnew.FromMM(xy[0]), pcbnew.FromMM(xy[1]))
    for t in r["tracks"]:
        s = pcbnew.PCB_TRACK(board)
        s.SetStart(P(t["a"])); s.SetEnd(P(t["b"])); s.SetWidth(pcbnew.FromMM(t["w"]))
        s.SetLayer(lid[t["layer"]]); s.SetNet(nets[t["net"]])
        board.Add(s)
        new.append(s)
    for v in r["vias"]:
        s = pcbnew.PCB_VIA(board)
        s.SetPosition(P(v["at"])); s.SetWidth(pcbnew.FromMM(v["d"])); s.SetDrill(pcbnew.FromMM(v["drill"]))
        a, b = v.get("layers", [None, None])
        s.SetLayerPair(lid.get(a, pcbnew.F_Cu), lid.get(b, pcbnew.B_Cu))
        s.SetNet(nets[v["net"]])
        board.Add(s)
        new.append(s)
    if len(board.Zones()):
        filler = pcbnew.ZONE_FILLER(board)       # keep it referenced while it fills (a temporary crashes)
        filler.Fill(board.Zones())
    return new


if __name__ == "__main__":
    import os
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    rip = "--rip-up" in sys.argv
    b = pcbnew.LoadBoard(args[1])
    if args[0] == "dump":
        dump(b, args[2], rip)
    elif args[0] == "fills":
        fills(b, args[2])
    elif args[0] == "apply":
        apply(b, args[2], args[3], rip)
    else:
        sys.exit(__doc__)
    # KiCad's bindings can crash while Python tears them down at exit; the work is
    # saved by now, so leave without the teardown.
    sys.stdout.flush()
    os._exit(0)
