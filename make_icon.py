# -*- coding: utf-8 -*-
"""生成「交流电」App 图标：深青圆角底板 + 两条相互交织的正弦波。

忠实还原原设计的“双波交叉编织”结构：两条反相正弦波在中线反复交叉，
一条亮青、一条深蓝，形成一串叶片状交织图案。矢量级超采样后缩放。
    py make_icon.py
"""

import math
import os

from PIL import Image, ImageDraw

HERE = os.path.dirname(os.path.abspath(__file__))
SS = 4
OUT = 1024
W = OUT * SS

TILE_TOP = (14, 156, 147)     # #0e9c93
TILE_BOTTOM = (11, 111, 104)  # #0b6f68
CYAN = (103, 255, 244)        # #67fff4  亮青（主）
BLUE = (33, 130, 190)         # #2182be  深蓝（副，还原原图第二色）
WHITE = (238, 255, 253)       # 备选副色


def rounded_mask(size, radius):
    m = Image.new("L", (size, size), 0)
    ImageDraw.Draw(m).rounded_rectangle([0, 0, size - 1, size - 1], radius=radius, fill=255)
    return m


def gradient_tile(size, top, bottom):
    img = Image.new("RGB", (size, size), top)
    px = img.load()
    for y in range(size):
        t = y / (size - 1)
        c = tuple(round(top[k] + (bottom[k] - top[k]) * t) for k in range(3))
        for x in range(size):
            px[x, y] = c
    return img


def wave_pts(x0, x1, mid, amp, cycles, sign, steps=2400):
    pts = []
    for i in range(steps + 1):
        x = x0 + (x1 - x0) * i / steps
        ph = 2 * math.pi * cycles * (x - x0) / (x1 - x0)
        pts.append((x, mid + sign * amp * math.sin(ph)))
    return pts


def stamp(draw, pts, color, r):
    for x, y in pts:
        draw.ellipse([x - r, y - r, x + r, y + r], fill=color)


def build(color_a, color_b, out_name, save_ico=False):
    base = Image.new("RGBA", (W, W), (0, 0, 0, 0))
    tile = gradient_tile(W, TILE_TOP, TILE_BOTTOM).convert("RGBA")
    tile.putalpha(rounded_mask(W, int(W * 0.20)))
    base.alpha_composite(tile)

    draw = ImageDraw.Draw(base)
    mid = W * 0.5
    amp = W * 0.24
    x0, x1 = W * 0.12, W * 0.88
    cycles = 2.5
    r = W * 0.028

    # 先画副色波（反相），再画主色亮青波，交叉处亮青在上
    stamp(draw, wave_pts(x0, x1, mid, amp, cycles, +1), color_b, r)
    stamp(draw, wave_pts(x0, x1, mid, amp, cycles, -1), color_a, r)

    icon = base.resize((OUT, OUT), Image.LANCZOS)
    icon.save(os.path.join(HERE, out_name))
    print("[完成] 预览:", out_name)
    if save_ico:
        ico = os.path.join(HERE, "icon.ico")
        icon.save(ico, sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
        print("[完成] 图标:", ico)


if __name__ == "__main__":
    # 主版本：亮青 + 白（深青底上对比更强，小尺寸更清楚）
    build(CYAN, WHITE, "icon_preview_1024.png", save_ico=True)
