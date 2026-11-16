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

Results on the test boards, with their own rules (KiCad DRC after routing):

| board | unconnected | new DRC violations | time |
|---|---|---|---|
| `fob` | 0 (from 256) | 12 starved thermals | ~1 min |
| `h2` | 0 | none | ~4 min |
| `mbus` | 0 | none | 5 s |

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

`test/` holds fixed copies of three boards used as benchmarks:

| board | what it tests |
|---|---|
| `fob` | dense 2-layer, GND pour on the back, coil and antenna keepouts |
| `h2` | ESP32-H2-DevKit-Lipo: 2-layer, hand-routed fully by its designer (so routable), many signal-net pours |
| `mbus` | an old KiCad 4 file |

    test/run.sh fob        # route, write test/out/fob-routed.kicad_pcb, run KiCad's DRC

KiCad is expected as the snap (`snap run --shell kicad`, `/snap/bin/kicad.kicad-cli`).
