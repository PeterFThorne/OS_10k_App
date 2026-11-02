"""Builds os10k_icon.ico / .png - navy grid (OS Point Tool theme) with an
orange-framed 2x2 map tile block. Run once: python make_icon.py"""
from PIL import Image, ImageDraw

NAVY, GRID, ORANGE = (31, 42, 68), (58, 76, 112), (242, 140, 40)
S = 1024
img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
d = ImageDraw.Draw(img)
d.rounded_rectangle([0, 0, S - 1, S - 1], radius=190, fill=NAVY)
for k in range(128, S, 128):
    d.line([k, 40, k, S - 40], fill=GRID, width=10)
    d.line([40, k, S - 40, k], fill=GRID, width=10)

# 2x2 tile block with simplified map content
x0, y0, x1, y1 = 232, 232, 792, 792
m = (x0 + x1) // 2
full = img.copy()
d = ImageDraw.Draw(full)
d.rectangle([x0, y0, x1, y1], fill=(252, 251, 247))
d.polygon([(x0, y0), (m - 40, y0), (x0 + 150, m - 30), (x0, m + 40)], fill=(204, 232, 196))   # woodland
d.line([(x0, y1 - 120), (m, m + 60), (x1, y0 + 170)], fill=(240, 127, 146), width=46)          # A road
d.line([(x0 + 70, y1), (m + 40, m + 130), (x1, m + 60)], fill=(70, 180, 220), width=22)       # river
for bx, by in ((m + 70, m + 150), (m + 150, m + 120), (m + 110, m + 210)):                    # buildings
    d.rectangle([bx, by, bx + 52, by + 52], fill=(245, 190, 140))
img.paste(full.crop((x0, y0, x1, y1)), (x0, y0))
d = ImageDraw.Draw(img)
d.line([m, y0, m, y1], fill=NAVY, width=12)
d.line([x0, m, x1, m], fill=NAVY, width=12)
d.rectangle([x0, y0, x1, y1], outline=ORANGE, width=44)

img.save("os10k_icon.png")
img.save("os10k_icon.ico", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
print("icon written")
