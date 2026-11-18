"""Draw Trouting's icon, a trout, at the sizes KiCad uses.

    python3 tools/make_icons.py          # plugin/icon.png (24 px), plugin/icon-64.png, docs/trout.png (256 px, the README's)

The trout's red side stripe is a track with a 45° bend, and its eye is a via.
Needs pycairo. Drawn on a 64-unit square and scaled, so every size matches.
"""
import math
import os
import sys

import cairo

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

BACK = (0.33, 0.46, 0.24)          # olive-green back
BELLY = (0.93, 0.89, 0.78)         # pale belly
TRACK = (0.82, 0.22, 0.20)         # front copper red, as in the PNG renders
LINE = (0.16, 0.22, 0.12)          # outline
SPOT = (0.14, 0.18, 0.10)


def body(c):
    c.move_to(59, 31)                                  # snout
    c.curve_to(54, 21, 41, 16, 29, 18)                 # head and back
    c.curve_to(21, 19, 15, 24, 11, 28)                 # back to the tail's root
    c.line_to(3, 18)                                   # upper lobe of the tail
    c.curve_to(6, 26, 6, 38, 3, 46)                    # the tail's forked edge
    c.line_to(11, 36)                                  # lower lobe back to the root
    c.curve_to(17, 41, 26, 46, 37, 45)                 # belly
    c.curve_to(49, 44, 56, 39, 59, 33)                 # chin
    c.close_path()


def fins(c):
    c.move_to(25, 19)                                  # dorsal
    c.curve_to(27, 14, 31, 11, 36, 11)
    c.curve_to(35, 13, 35, 15, 37, 17.5)
    c.close_path()
    c.move_to(30, 45.5)                                # anal
    c.curve_to(29, 49, 27, 51, 24, 52)
    c.curve_to(25, 49, 24, 46, 23, 44.5)
    c.close_path()


def draw(size, path):
    s = cairo.ImageSurface(cairo.FORMAT_ARGB32, size, size)
    c = cairo.Context(s)
    c.scale(size / 64.0, size / 64.0)
    px = 64.0 / size                                   # one pixel, in drawing units
    line = max(1.6, 1.1 * px)                          # an outline at least ~1 px wide

    fins(c)
    c.set_source_rgb(*BACK)
    c.fill_preserve()
    c.set_source_rgb(*LINE)
    c.set_line_width(line)
    c.set_line_join(cairo.LINE_JOIN_ROUND)
    c.stroke()

    body(c)
    g = cairo.LinearGradient(0, 17, 0, 46)             # dark back fading to a pale belly
    g.add_color_stop_rgb(0.0, *BACK)
    g.add_color_stop_rgb(0.45, *BACK)
    g.add_color_stop_rgb(0.75, *BELLY)
    g.add_color_stop_rgb(1.0, *BELLY)
    c.set_source(g)
    c.fill_preserve()
    c.save()
    c.clip()                                           # stripe and spots stay inside the body

    if size >= 48:                                     # spots only where they show
        c.set_source_rgb(*SPOT)
        for x, y in ((20, 25), (27, 22.5), (34, 21.5), (41, 22), (24, 27), (31, 26)):
            c.arc(x, y, 1.1, 0, 2 * math.pi)
            c.fill()

    c.set_source_rgb(*TRACK)                           # the stripe: a track with a 45° bend
    c.set_line_width(5.0)
    c.set_line_cap(cairo.LINE_CAP_ROUND)
    c.set_line_join(cairo.LINE_JOIN_ROUND)
    c.move_to(8, 33)
    c.line_to(30, 33)
    c.line_to(34, 30)
    c.line_to(46, 30)
    c.stroke()
    c.restore()

    body(c)
    c.set_source_rgb(*LINE)
    c.set_line_width(line)
    c.set_line_join(cairo.LINE_JOIN_ROUND)
    c.stroke()

    c.set_source_rgb(*LINE)                            # the eye: a via, where the track ends
    c.arc(49, 29, 4.6, 0, 2 * math.pi)
    c.fill()
    small = size < 48                                  # bolder eye where pixels are few
    c.set_source_rgb(1, 1, 1)
    c.arc(49, 29, 3.4 if small else 3.2, 0, 2 * math.pi)
    c.fill()
    c.set_source_rgb(*LINE)
    c.arc(49, 29, 1.9 if small else 1.5, 0, 2 * math.pi)
    c.fill()

    s.write_to_png(path)
    print("wrote", path)


if __name__ == "__main__":
    out = sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "plugin")
    draw(24, os.path.join(out, "icon.png"))
    draw(64, os.path.join(out, "icon-64.png"))
    if len(sys.argv) == 1:
        draw(256, os.path.join(ROOT, "docs", "trout.png"))
