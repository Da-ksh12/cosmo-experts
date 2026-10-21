"""Where on a screenshot does a pure colour appear?   python find_color_box.py <png> <r,g,b> [tolerance]

Prints {"x0","y0","x1","y1","count"} (pixel coordinates of the screenshot, x1/y1 exclusive) of every pixel within `tolerance` of the colour, or {"count": 0}. Used by e2e/photo-analysis.mjs to find where
a controlled picture's coloured rectangle really appears on screen, independently of the viewer's own marker maths."""
import json
import sys

import numpy as np
from PIL import Image


def main() -> None:
    path, rgb = sys.argv[1], [int(v) for v in sys.argv[2].split(",")]
    tol = int(sys.argv[3]) if len(sys.argv) > 3 else 40
    a = np.asarray(Image.open(path).convert("RGB")).astype(int)
    m = (np.abs(a - np.array(rgb)).max(axis=2) <= tol)
    ys, xs = np.nonzero(m)
    if not len(xs):
        print(json.dumps({"count": 0}))
        return
    print(json.dumps({"x0": int(xs.min()), "y0": int(ys.min()), "x1": int(xs.max()) + 1, "y1": int(ys.max()) + 1, "count": int(len(xs))}))


main()
