# -*- coding: utf-8 -*-
"""配色调色板的对比度检查：WCAG 对比 + 相邻档位的色差（含色弱模拟）。

    python tools\\contrast_check.py

只看数值，不改任何东西。要改颜色请回主程序顶部 BAND_COLORS_DEFAULT。
"""

import sys

BG = "#1b1f27"
BG_SOFT = "#232935"
IDLE = "#2a3140"          # 没亮的那几段
MUTED = "#8b97a8"
DIM = "#7f8ba0"
ACCENT = "#4d6bfe"
ACCENT_TEXT = "#7d93ff"

BANDS = [
    ("第 1 档 青", "#3ABFE0"),
    ("第 2 档 绿", "#46C46E"),
    ("第 3 档 柠檬黄", "#FFE45C"),
    ("第 4 档 橙", "#FF8420"),
    ("第 5 档 大红", "#E5484D"),
    ("第 5 档金额色", "#f2555a"),
]

# Machado et al. 2009，severity 1.0，作用于线性 RGB
CVD = {
    "绿色弱 deuteranopia": [
        [0.367322, 0.860646, -0.227968],
        [0.280085, 0.672501, 0.047413],
        [-0.011820, 0.042940, 0.968881],
    ],
    "红色弱 protanopia": [
        [0.152286, 1.052583, -0.204868],
        [0.114503, 0.786281, 0.099216],
        [-0.003882, -0.048116, 1.051998],
    ],
}


def hex_rgb(value):
    value = value.lstrip("#")
    return tuple(int(value[i:i + 2], 16) for i in (0, 2, 4))


def to_linear(channel):
    c = channel / 255.0
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def luminance(rgb):
    r, g, b = (to_linear(c) for c in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(fg, bg):
    a, b = luminance(hex_rgb(fg)), luminance(hex_rgb(bg))
    high, low = max(a, b), min(a, b)
    return (high + 0.05) / (low + 0.05)


# ---- CIELAB / ΔE2000 ----
def linear_rgb(rgb):
    return tuple(to_linear(c) for c in rgb)


def rgb_to_xyz(rgb):
    r, g, b = linear_rgb(rgb)
    return (r * 0.4124 + g * 0.3576 + b * 0.1805,
            r * 0.2126 + g * 0.7152 + b * 0.0722,
            r * 0.0193 + g * 0.1192 + b * 0.9505)


def xyz_to_lab(xyz):
    def f(t):
        return t ** (1 / 3) if t > 216 / 24389 else (841 / 108) * t + 4 / 29
    x, y, z = xyz
    fx, fy, fz = f(x / 0.95047), f(y / 1.0), f(z / 1.08883)
    return (116 * fy - 16, 500 * (fx - fy), 200 * (fy - fz))


def lab(color):
    return xyz_to_lab(rgb_to_xyz(hex_rgb(color)))


def delta_e2000(c1, c2):
    import math
    L1, a1, b1 = lab(c1)
    L2, a2, b2 = lab(c2)
    C1, C2 = math.hypot(a1, b1), math.hypot(a2, b2)
    Cb = (C1 + C2) / 2
    G = 0.5 * (1 - math.sqrt(Cb ** 7 / (Cb ** 7 + 25 ** 7))) if Cb else 0
    a1p, a2p = (1 + G) * a1, (1 + G) * a2
    C1p, C2p = math.hypot(a1p, b1), math.hypot(a2p, b2)
    h1p = math.degrees(math.atan2(b1, a1p)) % 360 if (a1p or b1) else 0
    h2p = math.degrees(math.atan2(b2, a2p)) % 360 if (a2p or b2) else 0
    dLp = L2 - L1
    dCp = C2p - C1p
    if not C1p or not C2p:
        dhp = 0.0
    else:
        dhp = h2p - h1p
        if dhp > 180:
            dhp -= 360
        elif dhp < -180:
            dhp += 360
    dHp = 2 * math.sqrt(C1p * C2p) * math.sin(math.radians(dhp / 2))
    Lbp = (L1 + L2) / 2
    Cbp = (C1p + C2p) / 2
    if not (C1p * C2p):
        hbp = h1p + h2p
    elif abs(h1p - h2p) <= 180:
        hbp = (h1p + h2p) / 2
    else:
        hbp = (h1p + h2p + (360 if h1p + h2p < 360 else -360)) / 2
    T = (1 - 0.17 * math.cos(math.radians(hbp - 30))
         + 0.24 * math.cos(math.radians(2 * hbp))
         + 0.32 * math.cos(math.radians(3 * hbp + 6))
         - 0.20 * math.cos(math.radians(4 * hbp - 63)))
    dtheta = 30 * math.exp(-(((hbp - 275) / 25) ** 2))
    Rc = 2 * math.sqrt(Cbp ** 7 / (Cbp ** 7 + 25 ** 7)) if Cbp else 0
    Sl = 1 + (0.015 * (Lbp - 50) ** 2) / math.sqrt(20 + (Lbp - 50) ** 2)
    Sc = 1 + 0.045 * Cbp
    Sh = 1 + 0.015 * Cbp * T
    Rt = -math.sin(math.radians(2 * dtheta)) * Rc
    return math.sqrt((dLp / Sl) ** 2 + (dCp / Sc) ** 2 + (dHp / Sh) ** 2
                     + Rt * (dCp / Sc) * (dHp / Sh))


def simulate(color, matrix):
    r, g, b = linear_rgb(hex_rgb(color))
    out = []
    for row in matrix:
        v = row[0] * r + row[1] * g + row[2] * b
        out.append(max(0.0, min(1.0, v)))
    # 回到 sRGB 再转回 8bit，便于和正常视觉的 ΔE 用同一套算法比
    def enc(v):
        v = 12.92 * v if v <= 0.0031308 else 1.055 * v ** (1 / 2.4) - 0.055
        return max(0, min(255, round(v * 255)))
    return "#%02x%02x%02x" % tuple(enc(v) for v in out)


def verdict(ratio, large=True):
    if ratio >= 7:
        return "AAA"
    if ratio >= 4.5:
        return "AA"
    if ratio >= 3:
        return "AA 大字号" if large else "不足（仅够大字号）"
    return "不足"


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    print("== 1. 金额/竖条颜色 在卡片底色上的对比 ==")
    print(f"{'档位':<14}{'色值':<10}{'对比度':>7}  {'WCAG':<12}")
    for name, color in BANDS:
        ratio = contrast(color, BG)
        print(f"{name:<14}{color:<10}{ratio:>7.2f}  {verdict(ratio):<12}")

    print("\n== 2. 亮着的段 vs 熄灭的段（能不能看出哪几格亮） ==")
    for name, color in BANDS:
        print(f"{name:<14}{color:<10}{contrast(color, IDLE):>7.2f}")

    print("\n== 3. 相邻档位的色差 ΔE2000（>10 明显可分，5~10 勉强，<5 容易混） ==")
    pairs = [(BANDS[i][0], BANDS[i][1], BANDS[i + 1][0], BANDS[i + 1][1])
             for i in range(len(BANDS) - 2)]      # 最后一个是同档金额色，不参与相邻比较
    header = f"{'相邻':<28}{'正常':>8}"
    for mode in CVD:
        header += f"{mode:>22}"
    print(header)
    for name_a, a, name_b, b in pairs:
        row = f"{name_a.split()[1]}→{name_b.split()[1]:<20}".ljust(28)
        row += f"{delta_e2000(a, b):>8.1f}"
        for mode, matrix in CVD.items():
            row += f"{delta_e2000(simulate(a, matrix), simulate(b, matrix)):>22.1f}"
        print(row)

    print("\n== 4. 小字与辅助元素 ==")
    for label, fg, bg in (
        ("副行 MUTED", MUTED, BG),
        ("页脚 DIM", DIM, BG),
        ("页脚运行中 ACCENT_TEXT", ACCENT_TEXT, BG),
        ("浮层 MUTED", MUTED, BG_SOFT),
        ("增值按钮 白字", "#ffffff", ACCENT),
    ):
        ratio = contrast(fg, bg)
        print(f"{label:<20}{ratio:>7.2f}  {verdict(ratio, large=False)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
