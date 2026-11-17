#!/bin/bash
# route every board in test/boards with the trouting command and tabulate the results
#   test/bench.sh            all boards
#   test/bench.sh h2 fob     just these
cd "$(dirname "$0")"
out=out/bench; mkdir -p $out
boards=${@:-$(ls boards/*.kicad_pcb | xargs -n1 basename | sed 's/.kicad_pcb//')}
sum=$out/summary.txt; : > $sum
for b in $boards; do
  t=$(date +%s)
  timeout 1200 python3 ../trouting.py boards/$b.kicad_pcb -o $out/$b.kicad_pcb > $out/$b.log 2>&1
  rc=$?
  secs=$(( $(date +%s) - t ))
  nets=$(grep -o '[0-9]* nets to join' $out/$b.log | cut -d' ' -f1)
  unr=$(grep -q 'all connections routed' $out/$b.log && echo 0 || grep -o 'not routed ([0-9]*)' $out/$b.log | grep -o '[0-9]*')
  setup=$(grep -c '^  setup' $out/$b.log); full=$(grep -c '^  setup.*:  0 unrouted' $out/$b.log)
  unc=$(grep -o 'DRC: [0-9]* unconnected' $out/$b.log | grep -o '[0-9]*')
  new=$(grep '  new:' $out/$b.log | sed 's/  new: //' | paste -sd, -)
  best=$(grep -A1 '^best:' $out/$b.log | tail -1 | sed 's/^ *//')
  printf "%-9s rc=%-3s %4ss nets=%-4s unrouted=%-4s setups-full=%s/%s drc-unconnected=%-4s new=[%s]\n" \
    $b $rc $secs "$nets" "${unr:-?}" $full $setup "${unc:-?}" "$new" | tee -a $sum
done
