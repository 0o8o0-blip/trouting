"""The plugin's routing without its dialogs, in KiCad's Python:

    snap run --shell kicad -c "python3 test/t_plugin.py BOARD.kicad_pcb OUT.kicad_pcb"

Routes the board as the plugin would; if it doesn't route fully, makes the
best suggested move (as "Make this move and route again" does), routes again,
and saves the result for KiCad's DRC.
"""
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.environ.get("TROUTING_PLUGIN_DIR") or os.path.join(ROOT, "plugin"))
import pcbnew  # noqa: E402
import trouting_plugin as tp  # noqa: E402


def poll(lines):
    for l in lines:
        if not l.startswith("  setup"):
            print("    | " + l, flush=True)
    return True


board = pcbnew.LoadBoard(sys.argv[1])
py = tp.find_python()
print("python:", py, " packages there:", tp.deps_ok(py), flush=True)
t = time.time()
res, new = tp.route_board(board, py, poll=poll)
if res is None:
    print("FAILED:\n" + "\n".join(new[-20:]))
    os._exit(1)
print(tp.summary(res), "(%.0fs)" % (time.time() - t), flush=True)
for s in res["suggestions"]:
    print("  suggestion:", s["text"], s["unrouted"], "unrouted")
if res["suggestions"]:
    pick = res["suggestions"][0]
    for it in new:
        board.Delete(it)
    tp.move_part(board, pick)
    print("made the move: " + pick["text"], flush=True)
    res, new = tp.route_board(board, py, suggest=False, poll=poll)
    print(tp.summary(res), flush=True)
board.Save(sys.argv[2])
print("saved", sys.argv[2], flush=True)
sys.stdout.flush()
os._exit(0)
