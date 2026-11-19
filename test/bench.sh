#!/bin/bash
# route every board in test/boards with the trouting command and tabulate the results
#   test/bench.sh            all boards
#   test/bench.sh mine other     just these (test/boards/mine.kicad_pcb, ...)
#   JOBS=N test/bench.sh         use N cores (default: half of them, at low priority)
#   BOARDS=public test/bench.sh  the boards in test/public instead (results in test/out/public)
#   PYTHON=... ARGS=--no-suggest  the Python to run it with; more trouting options
#   LIMIT=1200                   seconds per board
# Linux and macOS.
cd "$(dirname "$0")"
dir=${BOARDS:-boards}
out=out/$([ "$dir" = boards ] && echo bench || basename "$dir"); mkdir -p $out
boards=${@:-$(ls $dir/*.kicad_pcb | xargs -n1 basename | sed 's/.kicad_pcb//')}
sum=$out/summary.txt; : > $sum
cores=$(getconf _NPROCESSORS_ONLN)
jobs=${JOBS:-$(( (cores + 1) / 2 ))}
limit=${LIMIT:-1200}
py=${PYTHON:-python3}
# a time limit per board: timeout(1) on Linux; macOS has none, perl's alarm does the same
if command -v timeout > /dev/null; then lim() { timeout "$@"; }; else lim() { perl -e 'alarm shift; exec @ARGV' "$@"; }; fi
for b in $boards; do
  t=$(date +%s)
  lim $limit nice -n 19 $py ../trouting.py $dir/$b.kicad_pcb --jobs $jobs $ARGS -o $out/$b.kicad_pcb > $out/$b.log 2>&1
  rc=$?
  for x in kicad_pro kicad_dru; do [ -f $dir/$b.$x ] && cp $dir/$b.$x $out/$b.$x; done   # rules, so KiCad opens the result right
  secs=$(( $(date +%s) - t ))
  nets=$(grep -o '[0-9]* nets to join' $out/$b.log | cut -d' ' -f1)
  unr=$(grep -q 'all connections routed' $out/$b.log && echo 0 || grep -o 'not routed ([0-9]*)' $out/$b.log | grep -o '[0-9]*')
  setup=$(grep -c '^  setup' $out/$b.log); full=$(grep -c '^  setup.*:  0 unrouted' $out/$b.log)
  unc=$(grep -o 'DRC: [0-9]* unconnected' $out/$b.log | grep -o '[0-9]*')
  new=$(grep '  new:' $out/$b.log | sed 's/  new: //' | paste -sd, -)
  best=$(grep -A1 '^best:' $out/$b.log | tail -1 | sed 's/^ *//')
  printf "%-20s rc=%-3s %4ss nets=%-4s unrouted=%-4s setups-full=%s/%s drc-unconnected=%-4s new=[%s]\n" \
    $b $rc $secs "$nets" "${unr:-?}" $full $setup "${unc:-?}" "$new" | tee -a $sum
done
