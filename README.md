# Trouting

Tommys routing: an autorouter for KiCad boards. It routes every net at once and
lets contested spots get more expensive each round (negotiated congestion), so
nets that have another way move away and the one that needs the spot keeps it.
Plane nets (a pour over a quarter of a layer or more, usually GND) get a short
track and a via into the pour per pad instead of being routed pad to pad.

Nets wider than the Default class (power) may narrow to it where the full
width doesn't fit, between fine-pitch pins say; the command lists where it did.
What the rounds leave unjoined gets a rip-up-and-retry pass at the end.

    trouting board.kicad_pcb                  # route in place (keeps board.kicad_pcb.trouting-bak)
    trouting board.kicad_pcb -o routed.kicad_pcb

Results on the ten test boards, each with its own rules (`test/bench.sh`;
KiCad DRC of the result against the designer's own board):

| board | nets | unconnected | new DRC violations | time |
|---|---|---|---|---|
| `esp32` | 63 | 0 | 4 starved thermals, 4 isolated copper (a +3V3 pour) | 33 s |
| `fob` | 64 | 0 (from 256) | 4 starved thermals | 2 min |
| `gateway` | 63 | 0 | 3 starved thermals | 2 min |
| `h2` | 54 | 0 | none | 3 min |
| `han_esp` | 23 | 0 | none | 22 s |
| `han_ttl` | 3 | 0 | none | 3 s |
| `mbus` | 13 | 0 | none | 6 s |
| `pool_v2` | 22 | 0 | 2 starved thermals | 29 s |
| `pool_zb` | 31 | 0 | none | 40 s |
| `water` | 35 | 0 | none | 62 s |

## Parts

- `trouting_kicad.py` – the only part that needs KiCad's Python (pcbnew):
  `dump` reads a board into JSON, `apply` writes routed tracks and vias back
  and refills the pours.
- `trouting/board.py` – the board as a grid: exact distances to fixed copper,
  the board edge, keepouts and holes; the terminals each net must join.
- `trouting/core.c` – the A* search and the occupancy bookkeeping (compiled
  with gcc on first use).
- `trouting/router.py` – the rounds, plane stitching, repair and the result.
- `trouting/render.py` – a PNG of the result.

## Test boards

`test/boards/` holds fixed copies of the boards used as benchmarks. Judge a
change on all of them, not on one:

| board | what it tests |
|---|---|
| `esp32` | KiCad 5 file, ESP32 module, signal-net pours |
| `fob` | dense 2-layer, GND pour on the back, coil and antenna keepouts |
| `gateway` | 2-layer, GND both sides, QFNs and an ESP module, custom rules (.kicad_dru) |
| `h2` | ESP32-H2-DevKit-Lipo: 2-layer, hand-routed fully by its designer (so routable), many signal-net pours |
| `han_esp`, `han_ttl` | KiCad 5 files, NPTH pads with copper rings |
| `mbus` | an old KiCad 4 file |
| `pool_v2`, `pool_zb` | small 2-layer sensor boards, GND both sides |
| `water` | 2-layer with locked tracks kept from the designer |

    test/bench.sh              # route them all with the trouting command; table in test/out/bench/summary.txt
    test/bench.sh h2 fob       # just these
    test/run.sh fob via_cost=8.0   # one setup, in-process (PNG, predicted pours), then KiCad's DRC

KiCad is expected as the snap (`snap run --shell kicad`, `/snap/bin/kicad.kicad-cli`).
