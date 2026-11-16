# Trouting

Tommys routing: an autorouter for KiCad boards. It routes every net at once and
lets contested spots get more expensive each round (negotiated congestion), so
nets that have another way move away and the one that needs the spot keeps it.
Plane nets (a pour over a quarter of a layer or more, usually GND) get a short
track and a via into the pour per pad instead of being routed pad to pad.

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
