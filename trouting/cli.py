"""trouting: an autorouter for KiCad boards.

examples:
  trouting board.kicad_pcb                      route in place (keeps board.kicad_pcb.trouting-bak)
  trouting board.kicad_pcb -o routed.kicad_pcb  write the result somewhere else
  trouting board.kicad_pcb --keep-tracks        keep the existing tracks, route the rest
  trouting board.kicad_pcb --png routed.png     also draw the result

Close the board in KiCad first (or reload it after). Several router setups run
at once and the best result is kept: fewest unrouted connections, then fewest
vias, then the shortest copper. KiCad's DRC runs on the result, and any
violation that wasn't on the board before is listed.
"""
import argparse
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time

from trouting import keep_awake

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
KICAD_SIDE = os.path.join(HERE, "trouting_kicad.py")

# the portfolio: (via cost, plane layer cost); cheap vias first, they won most tests
SETUPS = [(8.0, 2.5), (8.0, 1.5), (11.0, 2.5), (6.0, 2.5), (10.0, 1.5), (13.0, 2.5),
          (7.0, 1.5), (15.0, 2.5), (9.0, 2.0), (12.0, 1.5), (5.0, 2.0), (8.0, 1.0)]


# ---- KiCad: its Python (snap, system or Windows install) and kicad-cli ----
def installed_kicad():
    """KiCad's own Python and its kicad-cli, on Windows (Program Files) and macOS
    (KiCad.app): $KICAD_PYTHON, else the newest KiCad found. (None, None): none."""
    import glob
    found = []
    if sys.platform == "darwin":
        for apps in ("/Applications", os.path.expanduser("~/Applications")):
            found += glob.glob(os.path.join(apps, "KiCad*", "KiCad.app", "Contents", "Frameworks",
                                            "Python.framework", "Versions", "*", "bin", "python3"))
    else:
        for base in {os.environ.get("ProgramFiles", r"C:\Program Files"), os.environ.get("ProgramW6432", ""),
                     os.path.expandvars(r"%LOCALAPPDATA%\Programs")}:
            if base:
                found += glob.glob(os.path.join(base, "KiCad", "*", "bin", "python.exe"))

    def home(p):        # the folder named for the version: ...\KiCad\9.0 or /Applications/KiCad (9)
        return p.split(".app" + os.sep)[0].rsplit(os.sep, 1)[0] if ".app" + os.sep in p else \
            os.path.dirname(os.path.dirname(p))

    def version(p):
        return tuple(int(x) for x in re.findall(r"\d+", os.path.basename(home(p)))) or (0,)

    exe = os.environ.get("KICAD_PYTHON") or (max(found, key=version) if found else None)
    if not exe:
        return None, None
    near = os.path.join(os.path.dirname(exe), "kicad-cli.exe")                # Windows: beside python.exe
    if ".app" + os.sep in exe:                                                 # macOS: in KiCad.app
        near = os.path.join(exe.split(".app" + os.sep)[0] + ".app", "Contents", "MacOS", "kicad-cli")
    return exe, near if os.path.exists(near) else shutil.which("kicad-cli")


def kicad():
    """How to run a script under KiCad's Python, a work dir it can see, and kicad-cli."""
    try:
        subprocess.run([sys.executable, "-c", "import pcbnew"], check=True, capture_output=True)
        py = [sys.executable]
        work = tempfile.mkdtemp(prefix="trouting-")
        cli = shutil.which("kicad-cli") or shutil.which(os.path.join(os.path.dirname(sys.executable), "kicad-cli")) \
            or installed_kicad()[1]              # macOS: KiCad's Python is in KiCad.app, kicad-cli elsewhere in it
    except subprocess.CalledProcessError:
        if os.name == "nt" or sys.platform == "darwin":
            exe, cli = installed_kicad()
            if not exe:
                where = ("its python.exe (in KiCad's bin folder)" if os.name == "nt" else
                         "the python3 inside KiCad.app (Contents/Frameworks/Python.framework/Versions/*/bin)")
                sys.exit("trouting: can't find KiCad's Python (pcbnew). Install KiCad, or set KICAD_PYTHON to "
                         + where)
            return [exe], tempfile.mkdtemp(prefix="trouting-"), cli
        if not shutil.which("snap") or not os.path.exists("/snap/bin/kicad"):
            sys.exit("trouting: can't find KiCad's Python (pcbnew), neither installed nor as the snap")
        py = None                                   # through snap run
        # the snap sees only its own common dir and non-hidden home dirs
        base = os.path.expanduser("~/snap/kicad/common/trouting")
        os.makedirs(base, exist_ok=True)
        work = tempfile.mkdtemp(prefix="run-", dir=base)
        cli = "/snap/bin/kicad.kicad-cli"
    return py, work, cli


def run_kicad(py, args, timeout=600):
    if py:
        cmd = py + [KICAD_SIDE] + args
    else:
        q = " ".join("'%s'" % a.replace("'", "'\\''") for a in [KICAD_SIDE] + args)
        cmd = ["snap", "run", "--shell", "kicad", "-c", "python3 " + q]
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                       timeout=timeout, env=env)
    out = [l for l in (r.stdout + r.stderr).splitlines()
           if l.strip() and not re.match(r"(\d\d:\d\d:\d\d: Debug|swig/python detected)", l)]
    if r.returncode != 0:
        sys.exit("trouting: KiCad step failed (%s):\n  %s" % (args[0], "\n  ".join(out[-10:])))
    return out


def drc(cli, pcb, work, name):
    """{violation type: count} and the number of unconnected pads, by KiCad's DRC."""
    if not cli:
        return None, None
    rpt = os.path.join(work, name + "-drc.txt")
    subprocess.run([cli, "pcb", "drc", "-o", rpt, pcb], capture_output=True)
    if not os.path.exists(rpt):
        return None, None
    with open(rpt, encoding="utf-8", errors="replace") as fh:
        txt = fh.read()
    counts = {}
    for m in re.finditer(r"^\[(\w+)\]", txt, re.M):
        counts[m.group(1)] = counts.get(m.group(1), 0) + 1
    m = re.search(r"Found (\d+) unconnected", txt)
    return counts, int(m.group(1)) if m else None


# ---- routing, several setups at once ----
_BOARD = None
_PNG_DIR = None                                  # set when --png is asked for: each setup draws its result here
DEFAULT_MINUTES = 20                             # the routing's time, all setups together (None: no limit)
_UNTIL = None                                    # when the routing has to be done (time.time()), or None


def _route(setup):
    from .router import Options, Router
    via_cost, plane_cost = setup
    o = Options()
    o.via_cost, o.plane_layer_cost = via_cost, plane_cost
    if _UNTIL is not None:
        # what's left of the routing's time, less a tenth for what comes after the
        # negotiation (the endgame and the clean-up), at least a minute
        o.max_secs = max(60.0, 0.9 * (_UNTIL - time.time()))
    o.log = lambda *a: None
    t = time.time()
    r = Router(_BOARD, o).run()
    out = r.result()
    length = sum(((t_["a"][0] - t_["b"][0]) ** 2 + (t_["a"][1] - t_["b"][1]) ** 2) ** 0.5 for t_ in out["tracks"])
    n_unrouted = len(out["unrouted"])
    hot = [[float(_BOARD.cx(v % _BOARD.WH % _BOARD.W)), float(_BOARD.cy(v % _BOARD.WH // _BOARD.W))]
           for h in r.hot for v in h[:1]]
    # where the fights were, more finely (for the placement suggestions)
    hot_all = [[float(_BOARD.cx(int(v) % _BOARD.WH % _BOARD.W)), float(_BOARD.cy(int(v) % _BOARD.WH // _BOARD.W))]
               for h in r.hot for v in h[:: max(1, len(h) // 20)]]
    png = None
    if _PNG_DIR:
        from .render import render
        png = os.path.join(_PNG_DIR, "setup-%g-%g.png" % setup)
        render(r, png)
    return {"setup": setup, "out": out, "unrouted": n_unrouted, "vias": len(out["vias"]), "png": png,
            "length": round(length, 1), "hot": hot, "hot_all": hot_all, "rounds": len(r.history), "secs": round(time.time() - t, 1),
            "stitches": getattr(r, "stitches", 0), "out_of_time": r.out_of_time}


def _init_route(board_json, png_dir, until=None):
    """Each worker's board (on Windows a worker is a fresh Python and reads it itself)."""
    global _BOARD, _PNG_DIR, _BOARD_FROM, _UNTIL
    _PNG_DIR, _UNTIL = png_dir, until
    if _BOARD is None or _BOARD_FROM != board_json:
        from .board import Board
        with open(board_json, encoding="utf-8") as fh:
            _BOARD = Board(json.load(fh))
        _BOARD_FROM = board_json


_BOARD_FROM = None


def route_portfolio(board_json, tries, jobs, log, png_dir=None, minutes="default"):
    """Route the board with tries setups, jobs at a time; the best result. minutes:
    the time they get all together (each stops when it's up and keeps its best
    try; "default": DEFAULT_MINUTES, None or 0: no limit)."""
    t = time.time()
    if minutes == "default":
        minutes = DEFAULT_MINUTES
    until = t + 60.0 * minutes if minutes else None
    _init_route(board_json, png_dir, until)
    log("board read: %d x %d cells x %d layers, %d nets to join (%.1fs)"
        % (_BOARD.W, _BOARD.H, _BOARD.L, sum(1 for ts in _BOARD.terminals.values() if len(ts) > 1), time.time() - t))
    setups = SETUPS[:tries]
    from .parallel import Timeout, pmap
    results = []
    deadline = [None]
    try:
        # forked workers share the board read above; spawned ones read it again
        for res in pmap(_route, setups, min(jobs, len(setups)), deadline=lambda: deadline[0],
                        init=_init_route, initargs=(board_json, png_dir, until)):
            log("  setup via %4.1f, plane %.1f: %2d unrouted, %3d vias, %6.0f mm  (%d rounds, %.0fs%s)"
                % (res["setup"] + (res["unrouted"], res["vias"], res["length"], res["rounds"], res["secs"],
                                   ", out of time" if res["out_of_time"] else "")))
            results.append(res)
            # one setup routed everything: the others get half as long again to do it with
            # fewer vias, then the best so far is taken
            if res["unrouted"] == 0 and deadline[0] is None:
                deadline[0] = time.time() + max(20.0, 0.5 * (time.time() - t))
    except Timeout as e:
        log("  (stopped the %d setups still running: a full route is in, and they had half as long again)" % e.left)
    results.sort(key=lambda r: (r["unrouted"], r["vias"], r["length"]))
    return results[0]


def main(argv=None):
    keep_awake(os.getpid())
    head, _, rest = __doc__.partition("\n\n")
    ap = argparse.ArgumentParser(prog="trouting", description="Trouting: " + head.split(": ", 1)[1], epilog=rest,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("board", help="the .kicad_pcb to route (its .kicad_pro and .kicad_dru are read from beside it)")
    ap.add_argument("-o", "--output", metavar="FILE",
                    help="write the routed board here instead of over BOARD (routing in place keeps a .trouting-bak)")
    ap.add_argument("--keep-tracks", action="store_true",
                    help="keep the tracks and vias already on the board and route only what is missing "
                         "(by default only locked tracks are kept)")
    ap.add_argument("--tries", type=int, default=len(SETUPS), metavar="N",
                    help="router setups to try, best result kept (1-%d, default %%(default)s)" % len(SETUPS))
    ap.add_argument("--jobs", type=int, default=os.cpu_count() or 4, metavar="N",
                    help="setups to run at the same time (default: one per core, %(default)s here)")
    ap.add_argument("--max-minutes", type=float, metavar="M",
                    help="time the routing gets, all setups together: when it's up, each setup "
                         "keeps its best try (default %d; 0: no limit)" % DEFAULT_MINUTES)
    ap.add_argument("--no-suggest", action="store_true",
                    help="if the board doesn't route fully, don't look for parts to move or rotate")
    ap.add_argument("--png", metavar="FILE",
                    help="also draw the routed board to this PNG: front tracks red, back blue, vias white")
    a = ap.parse_args(argv)
    log = lambda m: print(m, flush=True)

    src = os.path.abspath(a.board)
    if not os.path.exists(src):
        sys.exit("trouting: no such board: " + a.board)
    out = os.path.abspath(a.output) if a.output else src
    py, work, cli = kicad()
    t0 = time.time()
    try:
        w_src = os.path.join(work, "in.kicad_pcb")
        shutil.copy(src, w_src)
        for ext in (".kicad_pro", ".kicad_dru"):        # rules and net classes live here
            side = os.path.splitext(src)[0] + ext
            if os.path.exists(side):
                shutil.copy(side, os.path.join(work, "in" + ext))
        rip = [] if a.keep_tracks else ["--rip-up"]
        j = os.path.join(work, "board.json")
        log("reading %s" % os.path.basename(src))
        for l in run_kicad(py, ["dump", w_src, j] + rip):
            log("  " + l)
        before, before_unc = drc(cli, w_src, work, "before")

        best = route_portfolio(j, a.tries, a.jobs, log, work if a.png else None,
                               "default" if a.max_minutes is None else a.max_minutes)
        routes = os.path.join(work, "routes.json")
        with open(routes, "w", encoding="utf-8") as fh:
            json.dump(best["out"], fh)
        w_out = os.path.join(work, "out.kicad_pcb")
        log("best: via cost %.1f, plane cost %.1f" % best["setup"])
        for l in run_kicad(py, ["apply", w_src, routes, w_out] + rip):
            log("  " + l)
        after, after_unc = drc(cli, w_out, work, "after")

        if out == src:
            bak = src + ".trouting-bak"
            shutil.copy(src, bak)
            log("backup: %s" % bak)
        shutil.copy(w_out, out)
        log("wrote %s  (%.0fs)" % (out, time.time() - t0))
        if a.png and best.get("png"):
            shutil.copy(best["png"], a.png)
            log("drew %s" % a.png)

        # a board that didn't route: which part to move or rotate (each checked by routing)
        sugg = None
        if best["out"]["unrouted"] and not a.no_suggest:
            from .suggest import suggest
            log("\nlooking for parts to move or rotate:")
            planes = {_BOARD.net_names[n] for n in _BOARD.planes}
            budget = max(60.0, 2.0 * best["secs"])
            sugg = suggest(_BOARD.d, planes, best["out"]["unrouted"], best["hot_all"], best["setup"],
                           a.jobs, log, res=_BOARD.res, budget=budget)

        # report
        u = best["out"]["unrouted"]
        if u:
            log("\nnot routed (%d):" % len(u))
            for x in u:
                log("  %-24s %s" % (x["net"], ", ".join(x.get("pads", [])) or "(crowded)"))
            if best["hot"]:
                log("  crowded at: " + "; ".join("(%.2f, %.2f)" % tuple(h) for h in best["hot"][:6]))
            if sugg:
                log("to make it route, move or rotate a part (each checked by routing the board with it):")
                for g in sugg[:5]:
                    left = "routes fully" if not g["unrouted"] else "%d left unrouted" % g["unrouted"]
                    log("  %-50s %s" % (g["text"], left))
            elif sugg is not None:
                log("no single move or rotation of a nearby part made it route better")
        else:
            log("\nall connections routed")
        # where wide nets had to narrow (a designer will want to know: current)
        neck = {}
        for t_ in best["out"]["tracks"]:
            r = _BOARD.d["nets"][t_["net"]]
            if t_["w"] < r["width"] - 1e-6:
                neck.setdefault((t_["net"], t_["w"], r["width"]), [0.0])[0] += math.dist(t_["a"], t_["b"])
        if neck:
            log("narrowed where the full width didn't fit:")
            for (net, w, full), (mm,) in sorted(neck.items(), key=lambda kv: -kv[1][0]):
                log("  %-24s %5.1f mm at %.3g mm (class width %.3g)" % (net, mm, w, full))
        if after is not None:
            log("KiCad DRC: %s unconnected pads (was %s)" % (after_unc, before_unc))
            skip = ("silk", "lib_footprint", "text_", "unconnected_items")
            new = {k: v - (before or {}).get(k, 0) for k, v in after.items()
                   if v > (before or {}).get(k, 0) and not k.startswith(skip)}
            for k, v in sorted(new.items()):
                log("  new: %d x %s" % (v, k))
            if not new:
                log("  no new violations")
    finally:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    main()
