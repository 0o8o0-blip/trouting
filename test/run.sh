#!/bin/bash
# route a test board end to end: route, write into a copy, KiCad DRC
set -eo pipefail
cd ~/claude/tracer/test
b=$1; shift
# the router reads the board as JSON; make it when missing or older than the board
if [ ! -f $b.json ] || [ $b.kicad_pcb -nt $b.json ]; then
  timeout 120 snap run --shell kicad -c "cd $PWD && python3 ../trouting_kicad.py dump $b.kicad_pcb $b.json --rip-up" 2>&1 | grep -v "Debug\|swig/python" | tail -1
fi
mkdir -p out
python3 t_route.py $b "$@" 2>&1 | awk '/round/{n++; if(n%10==0||n<3) print; last=$0; next} {print} END{print last}'
timeout 120 snap run --shell kicad -c "cd $PWD && python3 ../trouting_kicad.py apply $b.kicad_pcb out/$b-routes.json out/$b-routed.kicad_pcb --rip-up" 2>&1 | grep -v "Debug\|swig/python" | tail -1
/snap/bin/kicad.kicad-cli pcb drc -o out/$b-drc.txt out/$b-routed.kicad_pcb >/dev/null 2>&1 || true
/snap/bin/kicad.kicad-cli pcb drc -o out/$b-drc0.txt $b.kicad_pcb >/dev/null 2>&1 || true
echo "DRC (routed vs unrouted, silk/lib left out):"
join -a1 -a2 -e0 -o 0,1.2,2.2 <(grep -E '^\[' out/$b-drc.txt | sed 's/:.*//' | sort | uniq -c | awk '{print $2,$1}' | sort) \
     <(grep -E '^\[' out/$b-drc0.txt | sed 's/:.*//' | sort | uniq -c | awk '{print $2,$1}' | sort) | grep -v 'silk\|lib_footprint' | awk '{printf "  %-24s %5s  (was %s)\n",$1,$2,$3}'
grep -E 'unconnected pads' out/$b-drc.txt
