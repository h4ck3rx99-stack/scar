"""Does Scar find the code editor on a CCBP-style coding page, and refuse pages that aren't problems?
Draws a mock learning.ccbp.in coding-practice layout and runs the same checks code_assist uses (no clicks)."""
import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from jarvis.brain import tools_extra  # noqa: E402
from jarvis.hands import capture  # noqa: E402
from jarvis.skills.screen import ocr_image  # noqa: E402

W, H = 1920, 1080
img = Image.new("RGB", (W, H), (242, 244, 248))
d = ImageDraw.Draw(img)
try:
    f22, f18, mono = ImageFont.truetype("arial.ttf", 22), ImageFont.truetype("arial.ttf", 18), ImageFont.truetype("consola.ttf", 18)
except OSError:
    f22 = f18 = mono = ImageFont.load_default()
d.rectangle((0, 0, W, 70), fill=(255, 255, 255))
d.text((30, 22), "NxtWave  |  Coding Practice - 3", fill=(20, 40, 90), font=f22)
problem = ["First Negative Number", "", "Write a program that reads a number N, followed by N integers.",
           "Print the first negative number. If there are none, print No Negative Numbers.", "",
           "Input", "The first line of input is an integer N. The next N lines contain integers.", "",
           "Output", "The output should be a single line containing the first negative integer.", "",
           "Explanation", "In the given example, -5 is the first negative number.", "",
           "Sample Input 1", "4", "3", "-5", "2", "-8", "", "Sample Output 1", "-5"]
for i, line in enumerate(problem):
    d.text((40, 100 + i * 34), line, fill=(30, 30, 30), font=f22 if i == 0 else f18)
d.rectangle((960, 80, 1900, 140), fill=(255, 255, 255))
d.text((980, 98), "PYTHON", fill=(40, 40, 40), font=f18)
for label, x in (("Reset", 1560), ("Run", 1680), ("Submit", 1790)):
    d.rounded_rectangle((x, 90, x + 95, 130), radius=6, fill=(32, 110, 230) if label == "Submit" else (230, 235, 245))
    d.text((x + 18, 100), label, fill=(255, 255, 255) if label == "Submit" else (30, 30, 30), font=f18)
d.rectangle((960, 150, 1900, 1060), fill=(30, 30, 30))                     # dark code editor
for n in range(1, 26):
    d.text((975, 160 + (n - 1) * 30), str(n), fill=(120, 120, 120), font=mono)
d.text((1020, 160), "# Write your code here", fill=(106, 153, 85), font=mono)
EDITOR = (960, 150, 1900, 1060)


async def main():
    capture.window_image = lambda w: img
    tools_extra.__dict__.setdefault("_", None)
    w = SimpleNamespace(frame=SimpleNamespace(left=0, top=0, width=W, height=H), minimized=False, exe="opera.exe")
    text = await ocr_image(img)
    print("mock page counts as a problem:", tools_extra.looks_like_problem(text))
    privacy = ("YOUR CONNECTION IS NOT PRIVATE Attackers might be trying to steal your information from gxcorner (for "
               "example, passwords, messages, or credit cards). CERT AUTHORITY INVALID Back to safety Help me understand")
    print("privacy-error page counts as a problem:", tools_extra.looks_like_problem(privacy))
    t = tools_extra.ExtraTools()
    for attempt in range(3):
        hit = await t.vision_locate("the code editor panel (the area with line numbers where code is typed, not the "
                                    "problem statement)", w)
        if not hit:
            print("editor: not found")
            continue
        x, y, bw, bh = hit
        inside = EDITOR[0] <= x <= EDITOR[2] and EDITOR[1] <= y <= EDITOR[3]
        too_big = bw * bh > 0.8 * W * H
        print(f"editor click point ({x},{y}) box {bw}x{bh}: {'INSIDE editor' if inside else 'OUTSIDE editor'}"
              f"{' (rejected: whole window)' if too_big else ''}")


asyncio.run(main())
