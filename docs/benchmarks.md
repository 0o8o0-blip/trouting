# Benchmarks

## PCBWorld

[PCBWorld](https://github.com/LGAI-Research/PCBWorld) (Song et al., LG AI Research,
[arXiv:2607.05915](https://arxiv.org/abs/2607.05915)) is a public benchmark for PCB routing on
KiCad. Its D3 sets are real open-source boards from [PCBench](https://github.com/PCBench/PCBench),
converted to KiCad 9, each kept only if its original routing is fully connected and free of DRC
errors. Every router gets the same unrouted board and is scored by the same pipeline, with KiCad's
own DRC against the board's own design rules.

Trouting 0.4.2 routed all 119 boards of the three D3 eval sets, once, without moving any part
(`--no-suggest`), and PCBWorld v1.2.2 scored them with the paper's settings
(`--reward-config drc_dense_promoted --check-angle 45 --selection-method final_potential`).

**Clean pass** (CP, the paper's headline) is the share of boards that come back fully connected
*and* with zero DRC errors. **Rout.** is the share of connections routed.

| Set | Boards | Trouting | Freerouting | KiCadRoutingTools | Best RL agent (PPO) | GPT-5.4 agent |
|---|---|---|---|---|---|---|
| D3-A | 99 (6–31 pads) | **1.00** (99/99) | 0.80 | 0.74 | 0.94 | 0.65 |
| D3-B | 10 | **1.00** (10/10) | 0.78 | 0.20 | 0.45 | 0.00 |
| D3-C | 10 (largest) | **1.00** (10/10) | – | – | – | – |

Routability was 1.00 on all three sets: every connection of every board routed. The other
routers' numbers are the paper's Table 3 (Freerouting and PPO best of five tries, averaged over
four seeds; the paper reports no D3-C numbers).

**Checking the scoring.** Two cross-checks, scored with the same pipeline and settings:

| | D3-A CP | D3-A Rout. | D3-B CP | D3-B Rout. |
|---|---|---|---|---|
| KiCadRoutingTools, rerun here with PCBWorld's own script | 0.70 | 0.93 | 0.10 | 0.75 |
| KiCadRoutingTools, published | 0.74 | 0.94 | 0.20 | 0.86 |
| The boards' original human routing | 1.00 | 1.00 | 1.00 | 1.00 |

**Quality.** PCBWorld's potential gain (Pot.) combines DRC errors, track length and vias. Scored
here, it comes out 2–3 % off the paper's scale (the human routing: 23.62 against the paper's
23.16 on D3-A), so it is compared with the human routing scored here rather than across:

| Set | Trouting Pot. | Human routing Pot. | Trouting track length | Human track length | Trouting vias | Human vias |
|---|---|---|---|---|---|---|
| D3-A | 23.73 | 23.62 | 114 mm | 170 mm | 2.2 | 1.2 |
| D3-B | 66.05 | 65.77 | 400 mm | 570 mm | 12.6 | 6.7 |

Trouting uses about a third less track than the people who routed these boards, and about twice
as many vias.

**Time.** Trouting took 3.0 s per D3-A board (median 2.5 s), 5.9 s per D3-B board and 27 s per
D3-C board, counting KiCad's start, reading, routing, writing and Trouting's own DRC; the routing
alone took 0.2 s, 2.8 s and 16 s (medians), on a 24-thread Linux PC. The paper's times come from
other machines, so they are not compared here.

**What these boards are.** D3-A boards are small: 2 to 13 nets and 6 to 31 pads, on 2 to 4
layers. D3-B and D3-C have only 10 boards each. A perfect score here says Trouting handles small
and medium open-source boards cleanly; it says nothing yet about dense boards with fine-pitch
chips, where it can still fail (below).

To run it yourself: [tools/pcbworld/](../tools/pcbworld/README.md).

## 31 public, hand-routed boards

A second set: 31 open-source KiCad boards from GitHub that people routed by hand, 25 two-layer
and 6 four-layer, with 53 to 667 pads (keyboards, USB hubs, ESP32/RP2040/STM32 boards, audio,
power). Each is routed from scratch with its own rules, and checked with KiCad's DRC.

24 of the 31 end with every pad connected, 16 of them with no DRC item the board didn't have
before. The other 7 leave 23 pads unconnected in all, mostly where a fine-pitch chip's pins have
to be escaped (a QFP microcontroller on a 4-layer board) and on one board with a net tie.

## Our own boards

Ten 2-layer boards with 3 to 64 nets: all ten end with zero unconnected pads. Every change to the
router is checked on all three sets before it goes in.
