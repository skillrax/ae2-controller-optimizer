"""Draws packaging/icon.ico (the app logo: a controller hexagon). Needs Pillow."""

import math
from pathlib import Path

from PIL import Image, ImageDraw

S = 256
img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
d = ImageDraw.Draw(img)
c, r = S / 2, S * 0.46
hexagon = [(c + r * math.cos(math.radians(a)), c + r * math.sin(math.radians(a))) for a in range(-90, 270, 60)]
d.polygon(hexagon, fill=(55, 62, 74, 255))
d.line(hexagon + [hexagon[0]], fill=(77, 227, 255, 255), width=14, joint="curve")
# the "Y" of a cube edge, in fluix purple
mid = (c, c + S * 0.04)
for end in ((c, c + S * 0.32), (c - S * 0.24, c - S * 0.12), (c + S * 0.24, c - S * 0.12)):
    d.line([mid, end], fill=(169, 133, 239, 255), width=16)
out = Path(__file__).with_name("icon.ico")
img.save(out, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
print(f"wrote {out}")
