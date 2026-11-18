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


def main(argv=None):
    ap = argparse.ArgumentParser(prog="trouting.job")
    ap.add_argument("board_json")
    ap.add_argument("result_json")
    ap.add_argument("--tries", type=int, default=len(cli.SETUPS))
    ap.add_argument("--jobs", type=int, default=os.cpu_count() or 4)
    ap.add_argument("--no-suggest", action="store_true")
    a = ap.parse_args(argv)
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
                       a.jobs, log, res=b.res, budget=max(60.0, 2.0 * best["secs"]))
        res["suggestions"] = [{"text": g["text"], "unrouted": g["unrouted"], "vias": g["vias"],
                               "ref": g["move"][1], "rot": g["move"][2], "dx": g["move"][3], "dy": g["move"][4]}
                              for g in sugg[:6]]
    with open(a.result_json, "w") as fh:
        json.dump(res, fh)
    log("done (%.0fs)" % (time.time() - t0))


if __name__ == "__main__":
    main()
