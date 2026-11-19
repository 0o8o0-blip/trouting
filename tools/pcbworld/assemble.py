"""Trouting in PCBWorld, step 3: Trouting's routed boards as a PCBWorld rollout dir, its rows built by PCBWorld's
own _build_row (as for KRT), then scored with: python -m eval.pipeline --skip-rollout ...

    python assemble.py TIER RESULTS_DIR OUT_DIR
RESULTS_DIR holds results.jsonl and <board_id>/routed.kicad_pcb (copied from where Trouting ran)."""
import json, os, sys, types
sys.path.insert(0, os.getcwd())
from pathlib import Path
from eval.rollout.rule_based import select_boards_with_inputs, _build_row, SCHEMA_VERSION
from eval import eval_utils as u
tier, res_dir, out = sys.argv[1], Path(sys.argv[2]), Path(sys.argv[3])
diff = {"d3a": "easy", "d3b": "medium", "d3c": "hard"}[tier]
_, valid, _ = select_boards_with_inputs("pcbench", "krt", grid=None, split="test", limit=None,
                                        sample_substrings=None, difficulty=diff, all_corpus=False)
results = {}
for l in open(res_dir / "results.jsonl"):
    if l.strip():
        r = json.loads(l); results[r["board_id"]] = r
art = out / "artifacts"; art.mkdir(parents=True, exist_ok=True)
rows, route_times = [], {}
for i, b in enumerate(valid):
    r = results.get(b.board_id, {"status": "error", "error": "not routed", "total_s": 0.0})
    routed = res_dir / b.board_id / "routed.kicad_pcb"
    staged = art / ("_trouting_%05d.kicad_pcb" % i)
    if r["status"] == "ok" and routed.exists():
        staged.write_bytes(routed.read_bytes())
    result = types.SimpleNamespace(status=r["status"] if routed.exists() or r["status"] != "ok" else "no_output",
                                   routed_pcb=str(staged) if staged.exists() else "", error=r.get("error", ""),
                                   elapsed_routing_s=r["total_s"])
    rows.append(_build_row(b, i, 0, result, art, True))
    route_times[b.board_id] = r.get("route_s")
u.write_csv(out / "per_rollout.csv", rows, u.PER_ROLLOUT_FIELDS)
manifest = {"schema_version": SCHEMA_VERSION, "env_kwargs": {}, "args": {
    "generator": "trouting (test/pcbworld_route.py) + assemble.py", "stage": "rollout_only", "baseline": "trouting",
    "dataset": "pcbench", "difficulty": diff, "split": "test", "seed": 0, "n_boards": len(valid),
    "boards": [b.board_id for b in valid], "timeout_s": 1800, "deterministic": True,
    "artifact_naming": "board_{board_index:05d}_rollout_0.kicad_pcb",
    "note": "per_board_rollout_time = Trouting's total time (KiCad start, reading, routing, writing, its own DRC); "
            "routing alone in route_s.json"}}
(out / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True, default=str))
(out / "route_s.json").write_text(json.dumps(route_times, indent=1))
print(tier, "rows", len(rows), "completed", sum(1 for x in rows if x["status"] == "completed"))
