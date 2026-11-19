"""Trouting on PCBWorld, step 1 (run in PCBWorld's checkout and conda env): the boards each D3
eval set routes, numbered as PCBWorld numbers them (its own selection, as for KRT), as job
lists for route.py and as --boards-list files for PCBWorld's scoring.

    python /path/to/trouting/tools/pcbworld/export.py OUT_DIR
writes OUT_DIR/<set>-jobs.json and OUT_DIR/<set>-boards.txt for d3a, d3b, d3c."""
import json
import os
import sys

sys.path.insert(0, os.getcwd())
from eval.rollout.rule_based import select_boards_with_inputs  # noqa: E402

out = sys.argv[1]
os.makedirs(out, exist_ok=True)
for tier, diff in (("d3a", "easy"), ("d3b", "medium"), ("d3c", "hard")):
    _, valid, missing = select_boards_with_inputs("pcbench", "krt", grid=None, split="test", limit=None,
                                                  sample_substrings=None, difficulty=diff, all_corpus=False)
    jobs = [{"idx": i, "board_id": b.board_id, "raw_pcb": str(b.raw_pcb), "pro": str(b.pro)}
            for i, b in enumerate(valid)]
    with open(os.path.join(out, tier + "-jobs.json"), "w") as fh:
        json.dump(jobs, fh, indent=1)
    # the scoring names a board by its folder when the file is processed_v9_guide_v3.kicad_pcb
    with open(os.path.join(out, tier + "-boards.txt"), "w") as fh:
        fh.write("".join(str(b.raw_pcb).replace("_unrouted.kicad_pcb", ".kicad_pcb") + "\n" for b in valid))
    print(tier, len(valid), "boards", ("(%d missing inputs)" % len(missing)) if missing else "")
