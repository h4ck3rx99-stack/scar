"""Can the local vision model point at things? Draws a fake page with known element positions and asks for the
centre of each described element. Prints the error in pixels for 0-1000 relative and absolute interpretations."""
import asyncio
import base64
import io
import json
import re
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from jarvis.brain.llm import LLM  # noqa: E402

W, H = 1280, 720
img = Image.new("RGB", (W, H), (245, 245, 245))
d = ImageDraw.Draw(img)
try:
    font = ImageFont.truetype("arial.ttf", 22)
    small = ImageFont.truetype("arial.ttf", 16)
except OSError:
    font = small = ImageFont.load_default()
items = {}
d.rectangle((0, 0, W, 60), fill=(255, 255, 255))
d.text((20, 18), "YouTube", fill=(200, 0, 0), font=font)
d.rounded_rectangle((1120, 14, 1250, 46), radius=16, outline=(30, 110, 220), width=2)
d.text((1145, 20), "Sign in", fill=(30, 110, 220), font=small)
items["the Sign in button"] = (1185, 30)
for i, (title, color) in enumerate([("Linkin Park - In The End", (60, 60, 60)), ("Lofi beats to study", (90, 40, 120)),
                                    ("Interstellar soundtrack", (20, 70, 110))]):
    x0 = 80 + i * 400
    d.rectangle((x0, 120, x0 + 360, 322), fill=color)
    d.text((x0, 335), title, fill=(10, 10, 10), font=font)
    items[["the first video", "the second video", "the third video"][i]] = (x0 + 180, 221)
d.rounded_rectangle((560, 600, 720, 650), radius=8, fill=(0, 120, 212))
d.text((595, 612), "Subscribe", fill=(255, 255, 255), font=font)
items["the blue Subscribe button"] = (640, 625)

buf = io.BytesIO()
img.save(buf, "PNG")
b64 = base64.b64encode(buf.getvalue()).decode()
PROMPT = 'Locate {what} in the screenshot and output its bbox_2d in JSON.'


async def main():
    llm = LLM()
    for what, (tx, ty) in items.items():
        messages = [{"role": "user", "content": [{"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}},
                                                 {"type": "text", "text": PROMPT.format(what=what, w=W, h=H)}]}]
        out = ""
        async for kind, data in llm.stream(messages, max_tokens=40):
            if kind == "text":
                out += data
        m = re.search(r'"bbox_2d"\s*:\s*\[\s*([\d.]+)\s*,\s*([\d.]+)\s*,\s*([\d.]+)\s*,\s*([\d.]+)', out)
        if not m:
            print(f"{what:28s} unparsable: {out!r}")
            continue
        x1, y1, x2, y2 = map(float, m.groups())
        x, y = (x1 + x2) / 2 / 1000 * W, (y1 + y2) / 2 / 1000 * H
        err = ((x - tx) ** 2 + (y - ty) ** 2) ** 0.5
        print(f"{what:28s} -> ({x:.0f},{y:.0f}) truth ({tx},{ty})  error {err:4.0f}px")
    await llm.aclose()


asyncio.run(main())
