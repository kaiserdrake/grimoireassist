"""Generate samples/terms_test.mp4: a looping fake game clip for the copyable-terms feature.

Run:  .venv\\Scripts\\python.exe tools\\make_terms_video.py
Play: run_test_video.bat   (builds the clip first if it is missing)

Scenes (1920x1080, 30 fps, ~60 s, loops in the app):
  1. Field dialogue  - typed-out lines with speaker headers (dialogue region)
  2. Map screen      - place labels + a button-hint bar (labels region)
  3. Inventory menu  - items with counters + a prose description (labels region)
  4. Battle dialogue - monster name banner at the top + two more lines

Regions to draw with F9 (frame coordinates):
  dialogue   x=60   y=770  w=1800 h=270
  labels     x=980  y=90   w=900  h=640
  monster_1  x=560  y=20   w=800  h=90
"""
from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

W, H, FPS = 1920, 1080, 30
OUT = Path(__file__).resolve().parent.parent / "samples" / "terms_test.mp4"
FONT = "C:/Windows/Fonts/arial.ttf"
FONT_B = "C:/Windows/Fonts/arialbd.ttf"

DIALOGUE_BOX = (60, 770, 1860, 1040)
LABELS_BOX = (980, 90, 1880, 730)


def font(size, bold=False):
    return ImageFont.truetype(FONT_B if bold else FONT, size)


F_TEXT, F_HEAD, F_LABEL, F_SMALL, F_BANNER = (
    font(40), font(32, True), font(34), font(26), font(46, True))


# ── backdrops ──────────────────────────────────────────────────────────────────
def field_backdrop() -> Image.Image:
    img = Image.new("RGB", (W, H), (58, 92, 70))
    d = ImageDraw.Draw(img)
    d.rectangle((0, 0, W, 420), fill=(120, 165, 205))           # sky
    d.polygon([(0, 420), (500, 230), (900, 420)], fill=(80, 105, 95))
    d.polygon([(700, 420), (1250, 180), (1800, 420)], fill=(70, 95, 88))
    for x, y in ((300, 600), (1300, 650), (1600, 520)):
        d.ellipse((x - 90, y - 40, x + 90, y + 40), fill=(45, 75, 55))
    return img


def map_backdrop() -> Image.Image:
    img = Image.new("RGB", (W, H), (22, 34, 48))
    d = ImageDraw.Draw(img)
    d.rectangle((40, 40, W - 40, H - 100), fill=(40, 70, 60), outline=(150, 140, 100), width=4)
    d.ellipse((1050, 150, 1450, 400), fill=(40, 90, 130))      # lake
    d.polygon([(1500, 450), (1800, 300), (1850, 650), (1550, 700)], fill=(90, 110, 70))
    d.rectangle((1100, 480, 1350, 650), fill=(110, 95, 70))     # village
    # Place labels, all inside the labels region.
    for (x, y), name in (((1080, 110), "Flooded Forest"), ((1500, 260), "Tower of Dawn"),
                         ((1110, 420), "Kamura Village"), ((1540, 600), "Windward Plains")):
        _label(d, x, y, name)
    # Button-hint bar at the bottom (UI furniture, should NOT become terms).
    d.rectangle((0, H - 80, W, H), fill=(15, 15, 20))
    for x, hint in ((60, "Related Tips"), (420, "Place Marker"), (780, "Find Self")):
        d.text((x, H - 58), hint, font=F_SMALL, fill=(220, 220, 220))
    d.text((60, 60), "MAP", font=F_BANNER, fill=(230, 220, 180))
    return img


def inventory_backdrop() -> Image.Image:
    img = field_backdrop().point(lambda v: v // 3)
    d = ImageDraw.Draw(img)
    d.text((80, 80), "Items", font=F_BANNER, fill=(230, 220, 180))
    x0, y0, x1, y1 = LABELS_BOX
    d.rectangle((x0, y0, x1, y1), fill=(25, 25, 35), outline=(120, 110, 80), width=3)
    rows = ["Mega Potion x10", "Iron Sword", "Antidote x2", "Rathalos Scale x4", "Herb 3"]
    for i, row in enumerate(rows):
        y = y0 + 30 + i * 70
        if i == 0:
            d.rectangle((x0 + 10, y - 10, x1 - 10, y + 50), fill=(60, 55, 90))
        d.text((x0 + 40, y), row, font=F_LABEL, fill=(240, 240, 240))
    d.line((x0 + 20, y0 + 400, x1 - 20, y0 + 400), fill=(90, 90, 110), width=2)
    d.text((x0 + 40, y0 + 430), "Restores a moderate amount of health.",
           font=F_SMALL, fill=(200, 200, 210))
    d.text((x0 + 40, y0 + 470), "Sold by the merchant in Kamura Village.",
           font=F_SMALL, fill=(200, 200, 210))
    return img


def battle_backdrop() -> Image.Image:
    img = field_backdrop()
    d = ImageDraw.Draw(img)
    d.ellipse((780, 250, 1140, 560), fill=(150, 50, 40))       # the "monster"
    d.rectangle((560, 20, 1360, 110), fill=(20, 20, 26))
    d.text((620, 40), "Rathalos", font=F_BANNER, fill=(255, 230, 200))
    d.text((1020, 40), "Rey Dau", font=F_BANNER, fill=(255, 230, 200))
    return img


def _label(d, x, y, text):
    w = d.textlength(text, font=F_LABEL)
    d.rectangle((x - 12, y - 8, x + w + 12, y + 46), fill=(15, 15, 20))
    d.text((x, y), text, font=F_LABEL, fill=(245, 235, 200))


# ── dialogue box ───────────────────────────────────────────────────────────────
def dialogue(base: Image.Image, header: str, text: str, shown: int, arrow: bool) -> Image.Image:
    img = base.copy()
    over = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    od = ImageDraw.Draw(over)
    od.rounded_rectangle(DIALOGUE_BOX, radius=18, fill=(10, 10, 18, 215),
                         outline=(200, 190, 150, 255), width=3)
    img = Image.alpha_composite(img.convert("RGBA"), over).convert("RGB")
    d = ImageDraw.Draw(img)
    x0, y0, x1, _y1 = DIALOGUE_BOX
    if header:
        d.text((x0 + 40, y0 + 22), header, font=F_HEAD, fill=(240, 180, 80))
    ty = y0 + (90 if header else 40)
    for i, line in enumerate(_wrap(d, text[:shown], x1 - x0 - 80)):
        d.text((x0 + 40, ty + i * 54), line, font=F_TEXT, fill=(245, 245, 245))
    if arrow:
        ax, ay = x1 - 60, DIALOGUE_BOX[3] - 45
        d.polygon([(ax, ay), (ax + 24, ay), (ax + 12, ay + 16)], fill=(245, 245, 245))
    return img


def _wrap(d, text, width):
    lines, cur = [], ""
    for word in text.split(" "):
        trial = f"{cur} {word}".strip()
        if d.textlength(trial, font=F_TEXT) > width and cur:
            lines.append(cur)
            cur = word
        else:
            cur = trial
    if cur:
        lines.append(cur)
    return lines


FIELD_LINES = [
    ("Elder Fugen", "Hunter, head to Kamura Village and speak with Hinoa before you go."),
    ("Hinoa", "A Rathalos was spotted near the Flooded Forest. Take an Iron Sword with you."),
    ("", "Rumour says the Tower of Dawn holds a relic from the old kingdom."),
    ("Minoto", "Ask Utsushi about the Windward Plains. He knows the safe paths."),
]
BATTLE_LINES = [
    ("Utsushi", "Careful! Rey Dau is circling above the Windward Plains!"),
    ("Hinoa", "Use a Mega Potion if you get hurt, then fall back to Kamura Village."),
]


def write_dialogue(out, base, lines):
    for header, text in lines:
        for n in range(0, len(text) + 1):                       # ~30 chars/sec
            out.write(_bgr(dialogue(base, header, text, n, False)))
        for f in range(FPS * 4):                                # hold, arrow blinks
            out.write(_bgr(dialogue(base, header, text, len(text), (f // 15) % 2 == 0)))
        for _ in range(FPS):                                    # box closed
            out.write(_bgr(base))


def write_still(out, img, seconds):
    frame = _bgr(img)
    for _ in range(int(FPS * seconds)):
        out.write(frame)


def _bgr(img: Image.Image) -> np.ndarray:
    return cv2.cvtColor(np.asarray(img), cv2.COLOR_RGB2BGR)


def main():
    OUT.parent.mkdir(exist_ok=True)
    out = cv2.VideoWriter(str(OUT), cv2.VideoWriter_fourcc(*"mp4v"), FPS, (W, H))
    if not out.isOpened():
        raise SystemExit("could not open the video writer")
    field, battle = field_backdrop(), battle_backdrop()
    write_dialogue(out, field, FIELD_LINES)
    write_still(out, map_backdrop(), 8)
    write_still(out, inventory_backdrop(), 8)
    write_dialogue(out, battle, BATTLE_LINES)
    out.release()
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
