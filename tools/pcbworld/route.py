"""Route PCBWorld benchmark boards with Trouting (the routing half of a PCBWorld run;
PCBWorld itself picks the boards and scores the results):

    python3 tools/pcbworld/route.py jobs.json OUT_DIR [--parallel K] [--jobs N] [--limit S] [--python PY]

jobs.json: [{"idx": 0, "board_id": "...", "raw_pcb": "...", "pro": "..."}, ...] (paths
on this machine). Each board is copied to OUT_DIR/<board_id>/ as board.kicad_pcb with
its .kicad_pro beside it (Trouting reads the rules from there), routed from scratch
(--no-suggest: the parts stay where they are, as for every router in the benchmark),
and logged. OUT_DIR/results.jsonl gets one line per board: status (ok, timeout,
error), the routed board's path, the total time (KiCad's start, reading, routing,
writing, Trouting's own DRC) and the routing time alone (from "board read" to "best").
Boards already in results.jsonl are skipped, so a run can be resumed.
"""
import argparse
import concurrent.futures as cf
import json
import os
import shutil
import subprocess
import sys
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))   # the checkout
LOCK = threading.Lock()


def route(job, out, a):
    d = os.path.join(out, job["board_id"])
    os.makedirs(d, exist_ok=True)
    pcb, routed = os.path.join(d, "board.kicad_pcb"), os.path.join(d, "routed.kicad_pcb")
    shutil.copy(job["raw_pcb"], pcb)
    if job.get("pro") and os.path.exists(job["pro"]):
        shutil.copy(job["pro"], os.path.join(d, "board.kicad_pro"))
    dru = os.path.splitext(job.get("pro") or "")[0] + ".kicad_dru"
    if os.path.exists(dru):
        shutil.copy(dru, os.path.join(d, "board.kicad_dru"))
    cmd = (a.python.split() if a.python else [sys.executable]) + [
        os.path.join(ROOT, "trouting.py"), pcb, "-o", routed, "--no-suggest", "--jobs", str(a.jobs)]
    t0 = time.time()
    t_read = t_best = None
    status, err = "ok", ""
    with open(os.path.join(d, "trouting.log"), "w", encoding="utf-8") as log:
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                             encoding="utf-8", errors="replace", start_new_session=True)
        killer = threading.Timer(a.limit, lambda: os.killpg(p.pid, 9))
        killer.start()
        try:
            for line in p.stdout:
                now = time.time()
                log.write("%8.2f %s" % (now - t0, line))
                if line.startswith("board read") and t_read is None:
                    t_read = now
                elif line.startswith("best:"):
                    t_best = now
            rc = p.wait()
        finally:
            killer.cancel()
    total = time.time() - t0
    if total >= a.limit - 1:
        status, err = "timeout", "over %ds" % a.limit
    elif rc != 0 or not os.path.exists(routed):
        status, err = "error", "exit code %s" % rc
    res = {"idx": job["idx"], "board_id": job["board_id"], "status": status, "error": err,
           "routed_pcb": routed if status == "ok" else "", "total_s": round(total, 2),
           "route_s": round(t_best - t_read, 2) if t_read and t_best else None, "host": os.uname()[1]}
    with LOCK, open(os.path.join(out, "results.jsonl"), "a", encoding="utf-8") as fh:
        fh.write(json.dumps(res) + "\n")
    print("%-60s %-7s %7.1fs (routing %ss)" % (job["board_id"][:60], status, total, res["route_s"]), flush=True)
    return res


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("jobs_json")
    ap.add_argument("out")
    ap.add_argument("--parallel", type=int, default=1, help="boards at once")
    ap.add_argument("--jobs", type=int, default=12, help="Trouting's workers per board")
    ap.add_argument("--limit", type=float, default=3600, help="seconds per board")
    ap.add_argument("--python", help="the Python to run trouting.py with (default: this one)")
    a = ap.parse_args()
    jobs = json.load(open(a.jobs_json, encoding="utf-8"))
    os.makedirs(a.out, exist_ok=True)
    done = set()
    rp = os.path.join(a.out, "results.jsonl")
    if os.path.exists(rp):
        done = {json.loads(l)["board_id"] for l in open(rp, encoding="utf-8") if l.strip()}
    todo = [j for j in jobs if j["board_id"] not in done]
    print("%d boards, %d done already, %d to route, %d at once" % (len(jobs), len(done), len(todo), a.parallel), flush=True)
    with cf.ThreadPoolExecutor(a.parallel) as ex:
        list(ex.map(lambda j: route(j, a.out, a), todo))


if __name__ == "__main__":
    main()
