"""Generate the toolbar icons for the command (dev-only, standard library).

Fusion looks for 16x16.png, 32x32.png and their @2x variants in the command's
resources folder. The icon is a plate silhouette with a masked hole, drawn
dark gray on a transparent background with 4x supersampling."""

import math
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import imgtools as T  # noqa: E402

OUT = HERE.parent / 'commands' / 'anodizeSpec' / 'resources'


def draw(size: int, dark=(60, 60, 60), mid=(160, 160, 160)):
    ss = 4
    rgb = bytearray(size * size * 3)
    alpha = bytearray(size * size)
    # Shapes in unit coordinates (0..1): rounded plate with a hole and a tab.
    r_plate = 0.09

    def in_plate(x, y):
        x0, y0, x1, y1 = 0.08, 0.20, 0.92, 0.80
        if not (x0 <= x <= x1 and y0 <= y <= y1):
            return False
        cx = min(max(x, x0 + r_plate), x1 - r_plate)
        cy = min(max(y, y0 + r_plate), y1 - r_plate)
        return math.hypot(x - cx, y - cy) <= r_plate

    def in_hole(x, y):
        return math.hypot(x - 0.33, y - 0.5) <= 0.13

    def in_mask(x, y):
        return math.hypot(x - 0.67, y - 0.5) <= 0.13

    for py in range(size):
        for px in range(size):
            cov = 0
            acc = [0, 0, 0]
            for sy in range(ss):
                for sx in range(ss):
                    x = (px + (sx + 0.5) / ss) / size
                    y = (py + (sy + 0.5) / ss) / size
                    if not in_plate(x, y) or in_hole(x, y):
                        continue
                    col = dark if in_mask(x, y) else mid
                    cov += 1
                    acc[0] += col[0]
                    acc[1] += col[1]
                    acc[2] += col[2]
            i = py * size + px
            if cov:
                rgb[i * 3:i * 3 + 3] = bytes(c // cov for c in acc)
                alpha[i] = 255 * cov // (ss * ss)
    return size, size, bytes(rgb), bytes(alpha)


OUT.mkdir(parents=True, exist_ok=True)
for name, px in (('16x16.png', 16), ('32x32.png', 32), ('16x16@2x.png', 32), ('32x32@2x.png', 64), ('64x64.png', 64)):
    w, h, rgb, alpha = draw(px)
    T.write_png(OUT / name, w, h, rgb, alpha, 0)
    print('wrote', OUT / name)
