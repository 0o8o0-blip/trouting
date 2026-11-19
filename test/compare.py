"""Trouting's routing next to the person's, board by board:

    python3 test/compare.py test/public test/out/public

For each board in DIR that has a result in OUT: its copper layers and pads, the
vias and track length the person used (the board as it was) and Trouting's (the
result), and what the bench log says (unrouted, DRC unconnected, new violations).
Reads the .kicad_pcb files as text: no KiCad needed.
"""
import math
import os
import re
import sys

NUM = r"(-?[\d.]+)"


def stats(path):
    s = open(path, encoding="utf-8", errors="replace").read()
    length = 0.0
    for m in re.finditer(r"\(segment\s+\(start " + NUM + " " + NUM + r"\)\s*\(end " + NUM + " " + NUM + r"\)", s):
        x1, y1, x2, y2 = map(float, m.groups())
        length += math.hypot(x2 - x1, y2 - y1)
    for m in re.finditer(r"\(arc\s+\(start " + NUM + " " + NUM + r"\)\s*\(mid " + NUM + " " + NUM +
                         r"\)\s*\(end " + NUM + " " + NUM + r"\)", s):     # two chords: close enough
        x1, y1, xm, ym, x2, y2 = map(float, m.groups())
        length += math.hypot(xm - x1, ym - y1) + math.hypot(x2 - xm, y2 - ym)
    layers = re.search(r"\(layers(.*?)\n\s*\)", s, re.S)
    cu = len(set(re.findall(r'"([^"]*\.Cu)"', layers.group(1)))) if layers else 0
    return {"vias": len(re.findall(r"\(via[\s)]", s)), "mm": length, "pads": s.count("(pad "), "cu": cu}


def log_facts(path):
    if not os.path.exists(path):
        return {}
    s = open(path, encoding="utf-8", errors="replace").read()
    unr = 0 if "all connections routed" in s else (re.findall(r"not routed \((\d+)\)", s) or ["?"])[-1]
    unc = (re.findall(r"DRC: (\d+) unconnected", s) or ["?"])[-1]
    new = [l.strip()[len("new: "):] for l in s.splitlines() if l.strip().startswith("new:")]
    return {"unrouted": unr, "unconnected": unc, "new": "; ".join(new), "error": "Traceback" in s}


def main(src, out):
    rows = []
    for f in sorted(os.listdir(src)):
        if not f.endswith(".kicad_pcb"):
            continue
        b = f[:-len(".kicad_pcb")]
        res = os.path.join(out, f)
        h = stats(os.path.join(src, f))
        t = stats(res) if os.path.exists(res) else None
        rows.append((b, h, t, log_facts(os.path.join(out, b + ".log"))))
    print("%-20s %3s %5s | %6s %8s | %6s %8s | %8s %11s  %s" % ("board", "cu", "pads", "vias", "mm", "vias", "mm",
                                                                 "unrouted", "unconnected", "new DRC"))
    print("%-20s %3s %5s | %15s | %15s |" % ("", "", "", "by hand", "Trouting"))
    for b, h, t, lg in rows:
        tv, tm = ("%6d" % t["vias"], "%8.0f" % t["mm"]) if t else ("     -", "       -")
        print("%-20s %3d %5d | %6d %8.0f | %s %s | %8s %11s  %s" % (
            b, h["cu"], h["pads"], h["vias"], h["mm"], tv, tm, lg.get("unrouted", "-"),
            lg.get("unconnected", "-"), "ERROR, see log" if lg.get("error") else lg.get("new", "")))
    done = [r for r in rows if r[2]]
    full = [r for r in done if r[3].get("unconnected") == "0"]
    print("\n%d boards, %d with a result, %d with 0 unconnected pads by KiCad's DRC" % (len(rows), len(done), len(full)))


if __name__ == "__main__":
    main(*sys.argv[1:3])
