"""Crop Power BI Desktop window captures to the report canvas (drops ribbon, panes and the signed-in account name)."""

import sys
from pathlib import Path

from PIL import Image

BOX = (0.0325, 0.298, 0.6075, 0.8125)  # left, top, right, bottom as fractions of a maximised Desktop window

for p in sorted(Path(sys.argv[1] if len(sys.argv) > 1 else "powerbi/screenshots").glob("*.png")):
    im = Image.open(p)
    w, h = im.size
    if w < 2000:  # already cropped
        continue
    im.crop((int(w * BOX[0]), int(h * BOX[1]), int(w * BOX[2]), int(h * BOX[3]))).save(p, optimize=True)
    print(f"cropped {p.name}")
