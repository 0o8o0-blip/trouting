#!/bin/bash
# route a test board end to end with one setup: route, write into a copy, KiCad DRC
#   test/run.sh h2 via_cost=8.0 plane_layer_cost=2.5
set -eo pipefail
cd ~/claude/tracer/test
b=$1; shift
T=$b; for a in "$@"; do [[ $a == *=* ]] && T="$T-$a"; done
# the router reads the board as JSON; make it when missing or older than the board
if [ ! -f boards/$b.json ] || [ boards/$b.kicad_pcb -nt boards/$b.json ]; then
  timeout 120 snap run --shell kicad -c "cd $PWD && python3 ../trouting_kicad.py dump boards/$b.kicad_pcb boards/$b.json --rip-up" 2>&1 | grep -v "Debug\|swig/python" | tail -1
fi
mkdir -p out
python3 t_route.py $b "$@" 2>&1 | awk '/^round/{n++; if(n%10==0||n<3) print; last=$0; next} {print} END{print last}'
timeout 120 snap run --shell kicad -c "cd $PWD && python3 ../trouting_kicad.py apply boards/$b.kicad_pcb out/$T-routes.json out/$T-routed.kicad_pcb --rip-up" 2>&1 | grep -v "Debug\|swig/python\|Legacy\|best-effort" | tail -1
/snap/bin/kicad.kicad-cli pcb drc -o out/$T-drc.txt out/$T-routed.kicad_pcb >/dev/null 2>&1 || true
/snap/bin/kicad.kicad-cli pcb drc -o out/$b-drc0.txt boards/$b.kicad_pcb >/dev/null 2>&1 || true
echo "DRC (routed vs designer's, silk/lib left out):"
join -a1 -a2 -e0 -o 0,1.2,2.2 <(grep -E '^\[' out/$T-drc.txt | sed 's/:.*//' | sort | uniq -c | awk '{print $2,$1}' | sort) \
     <(grep -E '^\[' out/$b-drc0.txt | sed 's/:.*//' | sort | uniq -c | awk '{print $2,$1}' | sort) | grep -v 'silk\|lib_footprint' | awk '{printf "  %-24s %5s  (was %s)\n",$1,$2,$3}'
grep -E 'unconnected pads' out/$T-drc.txt
grep -A4 '^\[starved' out/$T-drc.txt | grep -o "@([0-9. ]*mm, [0-9. ]*mm): \(PTH \)\?[Pp]ad.*" || true
