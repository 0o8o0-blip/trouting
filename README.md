# Trouting: an autorouter for KiCad

Trouting (short for *Tommy's routing*) routes the tracks of a KiCad PCB for you. Give it a placed board with
a GND pour; it routes every net, stitches GND into the pour with vias, writes
the tracks back into the `.kicad_pcb` and runs KiCad's own DRC on the result.

![A small USB-C microcontroller board routed by Trouting: front tracks red, the GND pour on the back blue](docs/demo.png)

*The demo board in `examples/demo/`, routed by Trouting in 24 seconds: 25
nets, every pad connected, no new DRC violations. Red is the front, blue is the GND
pour on the back with the back-side tracks cut into it.*

It is made for the boards most of us actually make: dense **2-layer boards with
a ground pour**, QFNs, an ESP32 module, fine-pitch connectors.

- **All nets at once.** It doesn't route one net after another and get stuck
  on the last few. Every net is routed every round, and spots that several
  nets want get more expensive each round (negotiated congestion, the
  PathFinder algorithm from FPGA routing). The net that has another way moves;
  the one that needs the spot keeps it.
- **Understands pours.** GND isn't routed pad to pad: each pad gets a short
  track and a via into the pour. Trouting predicts how KiCad will fill the
  zones, so it knows which bits of pour will be cut off, and stitches those
  too. Thermal reliefs are checked the way KiCad's DRC checks them.
- **Your board's rules.** Clearances, track widths and via sizes come from
  the board's net classes (`.kicad_pro`) and custom rules (`.kicad_dru`).
  Power nets keep their width where it fits and narrow only where it doesn't,
  and you are told where.
- **Checked by KiCad.** After routing, KiCad's DRC runs on the result and
  Trouting reports any violation that wasn't there before.
- **Native to KiCad.** No Java and no Specctra DSN/SES export and import. It
  reads and writes the `.kicad_pcb` through KiCad's own Python module.

Status: young but working. It routes all ten real boards in its test set to zero
unconnected pads (see Results). It is developed on Linux with KiCad 9.

## Install

You need:

- **KiCad 9.** Tested with the snap (`sudo snap install kicad`). A normal
  install should also work if your system Python can `import pcbnew`.
- **Python 3.10 or newer** with numpy, scipy, shapely and Pillow.
- **gcc.** The search core is a small C file, compiled the first time it runs.

```sh
git clone https://github.com/TommyHolt/trouting.git
cd trouting
pip install -r requirements.txt
```

## Use

```sh
./trouting.py board.kicad_pcb                      # route in place, keeps board.kicad_pcb.trouting-bak
./trouting.py board.kicad_pcb -o routed.kicad_pcb  # write somewhere else
./trouting.py board.kicad_pcb --keep-tracks        # keep the tracks already there, route the rest
./trouting.py board.kicad_pcb --png routed.png     # also draw the result: front red, back blue, vias white
```

To try it on the demo board (a made-up USB-C microcontroller board, placed
but not routed):

```sh
./trouting.py examples/demo/demo.kicad_pcb -o demo-routed.kicad_pcb
./trouting.py examples/demo/stuck.kicad_pcb -o stuck-routed.kicad_pcb   # placed too tight: shows the suggestions
```

Close the board in KiCad first, or reload it after. Keep the `.kicad_pro` (and
`.kicad_dru`, if you have one) next to the `.kicad_pcb`: that is where the net
classes and rules are. Tracks you have **locked** in KiCad are kept.

It runs twelve router setups in parallel (one per core) and keeps the best: the
fewest unrouted connections, then the fewest vias, then the shortest copper.
A run looks like this:

```
reading board.kicad_pcb
  dumped 2 copper layers, 50 footprints, 70 pads, 13 nets, 0 tracks, 0 vias
board read: 1003 x 503 cells x 2 layers, 13 nets to join (0.8s)
  setup via  8.0, plane 1.0:  0 unrouted,   0 vias,    184 mm  (1 rounds, 0s)
  setup via  8.0, plane 1.5:  0 unrouted,  14 vias,    186 mm  (4 rounds, 1s)
  ...
best: via cost 8.0, plane cost 1.0
  applied 66 tracks, 0 vias
wrote board.kicad_pcb  (5s)

all connections routed
KiCad DRC: 0 unconnected pads (was 0)
  no new violations
```

### When a board can't be routed as placed

Then Trouting looks for a part to move or rotate. It takes the parts near the
connections it couldn't make and tries turning them and shifting them by 0.5
to 2 mm. A move must keep the part's courtyard clear of the other parts, its
pads clear of other copper, and the part as far inside the board as it was.
Trouting ranks the moves by how much shorter and less tangled the ratsnest
gets, then routes the whole board with each of the best ones. Only moves that
leave fewer connections unrouted are listed. On `examples/demo/stuck.kicad_pcb`
(the demo with U1 turned and C5 moved up):

```
not routed (3):
  ADC1                     U1.10
  SDA                      U1.13
  TX                       U1.11
  crowded at: (123.50, 112.30)
to make it route, move or rotate a part (each checked by routing the board with it):
  move C5 0.5 mm right and down (to 124.15, 114.15)  routes fully
  move C5 2.0 mm down (to 123.80, 115.80)            routes fully
  rotate U1 90° clockwise (to 0°)                    2 left unrouted
  move U1 1.0 mm down (to 120.00, 114.00)            2 left unrouted
```

This takes about as long again as the routing. `--no-suggest` skips it.

## Results

Trouting is tested on ten real boards, routed from scratch with each board's
own rules. They are 2-layer boards with 3 to 64 nets, in files from KiCad 4
to 9. KiCad's DRC finds **no unconnected pads** on any of them. The new DRC
warnings are a few starved thermals (GND pads, usually QFN pins, that the pour
reaches with fewer than two spokes), plus isolated copper on one board from a
pour that isn't GND (see Limitations). A board takes from a few seconds to
about three minutes on a 24-thread Linux PC.

## Limitations

What it doesn't do yet:

- **4-layer boards** are not tested yet.
- **Pours on nets other than the plane net** (a +3V3 pour, say) aren't
  modelled, so they can be left with isolated copper.
- **Differential pairs and length matching** are not supported.
- **Placement suggestions move one part at a time.** A board that needs two
  parts moved gets the move that helps most, and you run it again.

Bug reports with a board attached are the most useful thing you can send.

## How it works

| file | what it does |
|---|---|
| `trouting_kicad.py` | The only part that needs KiCad's Python (pcbnew). `dump` reads a board into JSON; `apply` writes the tracks and vias back and refills the zones. |
| `trouting/board.py` | The board as a grid at 0.05 mm: exact distance to fixed copper, board edge, keepouts and holes; the pads each net must join. |
| `trouting/core.c` | A* search and the occupancy bookkeeping, in C. |
| `trouting/router.py` | The negotiated-congestion rounds, GND stitching, the pour model, thermal checks, and rip-up and repair. |
| `trouting/cli.py` | The command: runs the setups in parallel, applies the best, runs DRC before and after. |
| `trouting/suggest.py` | Placement suggestions: moves near the trouble, screened by the ratsnest, checked by routing. |
| `trouting/parallel.py` | Forked workers and pipes (KiCad's snap doesn't allow the semaphores `multiprocessing` needs). |

## Developing

Test boards are not part of the repository, because they are other people's
designs. Put your own in `test/boards/`: the `.kicad_pcb` with its
`.kicad_pro` and `.kicad_dru` next to it. The folder is git-ignored. Judge
every change on all of them, not on one.

```sh
test/bench.sh                     # route them all; table in test/out/bench/summary.txt
test/bench.sh mine other          # just these (test/boards/mine.kicad_pcb, ...)
test/run.sh mine via_cost=8.0     # one setup, in-process (PNG of the result), then KiCad's DRC
python3 test/t_suggest.py mine scan          # turn each part, list the turns that stop it routing
python3 test/t_suggest.py mine fix U2:270    # break it so, then see what the suggestions find
```

## License

MIT. See [LICENSE](LICENSE).
