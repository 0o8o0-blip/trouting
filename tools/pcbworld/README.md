# Trouting on PCBWorld

How the [PCBWorld numbers](../../docs/benchmarks.md) were made, so anyone can run them again.
PCBWorld picks the boards and scores the results with its own code; Trouting only routes.

1. **PCBWorld.** On Linux x86_64, follow its README: `git clone --recursive
   https://github.com/LGAI-Research/PCBWorld.git`, `bash tools/setup/setup_all.sh` (a conda env
   and its KiCad 9.0.8 engine), then `bash tools/quickstart/prepare_pcbench.sh` (all boards:
   the eval sets are numbered across the whole corpus). In its checkout, with its env active:

       export PYTHONPATH=build_rl/pcbnew/python/rl:. PCBWORLD_DATA_ROOT=$PWD/var/datasets
       export PCBENCH_PCB_ROOT=$PCBWORLD_DATA_ROOT/pcbench/exacad_sorted
       export PCBENCH_DSN_ROOT=$PCBWORLD_DATA_ROOT/pcbench/exacad_sorted_dsn

2. **The boards.** `python <trouting>/tools/pcbworld/export.py OUT` writes, for d3a, d3b and
   d3c, a job list (`OUT/<set>-jobs.json`: each board's unrouted file and project file, numbered
   as PCBWorld numbers them) and a board list for the scoring (`OUT/<set>-boards.txt`).

3. **Routing.** On a machine with Trouting and KiCad 9 (KiCad 10 writes files PCBWorld's KiCad 9
   engine can't read): `python3 tools/pcbworld/route.py OUT/d3a-jobs.json RESULTS/d3a`. Each board
   is routed from scratch with `--no-suggest` (no part moves), 30 minutes at most, and
   `RESULTS/d3a/results.jsonl` gets its status and times. If the paths differ there, copy the
   boards over and rewrite `raw_pcb`/`pro` in the job list.

4. **Scoring.** Back in PCBWorld's checkout, with `RESULTS/d3a` copied there:

       python <trouting>/tools/pcbworld/assemble.py d3a RESULTS/d3a var/results/trouting_d3a
       python -c "from pathlib import Path; from eval.pipeline import flatten_rollout_artifacts; flatten_rollout_artifacts(Path('var/results/trouting_d3a'))"
       python -m eval.pipeline --skip-rollout --output-dir var/results/trouting_d3a \
           --boards-list OUT/d3a-boards.txt --reward-config drc_dense_promoted --check-angle 45 \
           --selection-method final_potential --rollout-mode parallel --n-envs 8 --stages eval,aggregate

   `assemble.py` builds the result rows with PCBWorld's own `_build_row`, as its KRT and
   Freerouting runs do. The numbers are in `var/results/trouting_d3a/per_boards_summary.csv`
   (`clean_pass_avg`, `routability_avg`, `potential_gain_avg`, ...).

As a check of the setup, PCBWorld's own KRT run scores the same way:
`python eval/rollout/rule_based.py --baseline krt --dataset pcbench --difficulty easy --split test
--output-dir var/results/krt_d3a` (after `pip install -e methods/baselines/rule_based/krt`), then
steps 4's flatten and scoring on that dir.
