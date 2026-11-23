"""Route a dumped board (JSON from trouting_kicad.dump) and write the result as
JSON: what the KiCad plugin runs, in a process of its own.

    python3 -m trouting.job board.json result.json [--tries N] [--jobs N] [--no-suggest]

Progress goes to stdout, a line at a time. result.json holds the routes
({"tracks", "vias", "unrouted"}) and, when not everything routed, the checked
placement suggestions.
"""
import argparse
import json
import os
import time

from . import cli


def watch_stdin():
    """Exit when stdin reaches its end: the plugin that started this process holds the
    other end, and the system closes it when KiCad exits, crashed or not. The workers
    watch this process in turn (parallel._watch_parent), so they stop too."""
    import sys
    import threading

    def watch():
        try:                                       # the raw fd: sys.stdin's buffer takes a
            fd = sys.stdin.fileno()                # lock that Python's exit would wait on
            while os.read(fd, 4096):
                pass
        except Exception:
            pass
        os._exit(1)
    threading.Thread(target=watch, daemon=True).start()


def main(argv=None):
    ap = argparse.ArgumentParser(prog="trouting.job")
    ap.add_argument("board_json")
    ap.add_argument("result_json")
    ap.add_argument("--tries", type=int, default=len(cli.SETUPS))
    ap.add_argument("--jobs", type=int, default=os.cpu_count() or 4)
    ap.add_argument("--no-suggest", action="store_true")
    ap.add_argument("--watch-stdin", action="store_true",
                    help="stop when stdin closes: the plugin holds it open, so KiCad gone = stop")
    a = ap.parse_args(argv)
    if a.watch_stdin:
        watch_stdin()
    log = lambda m: print(m, flush=True)

    t0 = time.time()
    best = cli.route_portfolio(a.board_json, a.tries, a.jobs, log)
    b = cli._BOARD
    res = {"out": best["out"], "setup": best["setup"], "hot": best["hot"], "suggestions": []}
    log("best: %d unrouted, %d vias (%.0fs)" % (best["unrouted"], best["vias"], time.time() - t0))
    if best["out"]["unrouted"] and not a.no_suggest:
        from .suggest import suggest
        log("looking for parts to move or rotate")
        planes = {b.net_names[n] for n in b.planes}
        sugg = suggest(b.d, planes, best["out"]["unrouted"], best["hot_all"], best["setup"],
                       cli.fit_jobs(a.jobs, a.jobs, log), log, res=b.res, budget=max(60.0, 2.0 * best["secs"]))
        res["suggestions"] = [{"text": g["text"], "unrouted": g["unrouted"], "vias": g["vias"],
                               "ref": g["move"][1], "rot": g["move"][2], "dx": g["move"][3], "dy": g["move"][4]}
                              for g in sugg[:6]]
    with open(a.result_json, "w", encoding="utf-8") as fh:
        json.dump(res, fh)
    log("done (%.0fs)" % (time.time() - t0))


if __name__ == "__main__":
    main()
