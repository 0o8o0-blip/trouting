"""A PNG of the routed board: obstacles grey, front tracks red, back tracks
blue, vias white, leftover fights yellow."""
import numpy as np
from PIL import Image, ImageDraw


def render(router, path, scale=1):
    b = router.b
    W, H = b.W, b.H
    base = np.full((H, W, 3), 245, np.uint8)
    for l in range(b.L):
        d = b.d1[l * b.WH:(l + 1) * b.WH].reshape(H, W)
        base[d < 0.0] = (200, 200, 200) if l else (150, 150, 150)
    base[~b.inside.reshape(H, W)] = (40, 40, 40)
    img = Image.fromarray(base)
    if scale != 1:
        img = img.resize((W * scale, H * scale), Image.NEAREST)
    dr = ImageDraw.Draw(img)
    cols = [(220, 40, 40), (40, 90, 230), (40, 170, 60), (200, 120, 0)]
    res = b.res
    P = lambda xy: ((xy[0] - b.ox) / res * scale, (xy[1] - b.oy) / res * scale)
    out = router.result()
    for t in sorted(out["tracks"], key=lambda t: -b.li[t["layer"]]):
        dr.line([P(t["a"]), P(t["b"])], fill=cols[b.li[t["layer"]] % 4], width=max(1, int(t["w"] / res * scale)))
    for v in out["vias"]:
        x, y = P(v["at"])
        r = v["d"] / 2 / res * scale
        dr.ellipse([x - r, y - r, x + r, y + r], fill=(255, 255, 255), outline=(0, 0, 0))
    for hot in router.hot:
        for v in hot[:: max(1, len(hot) // 200)]:
            l, x, y = b.node_xy(v)
            dr.ellipse([x * scale - 6, y * scale - 6, x * scale + 6, y * scale + 6], outline=(255, 200, 0), width=3)
    img.save(path)
