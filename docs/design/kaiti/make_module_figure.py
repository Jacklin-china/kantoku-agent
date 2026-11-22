# -*- coding: utf-8 -*-
"""生成开题报告用「系统功能模块图」——横向树，缩放到论文正文宽度后文字仍可读。"""
from PIL import Image, ImageDraw, ImageFont

FONT = "C:/Windows/Fonts/msyh.ttc"
W, H = 800, 914
BG = (255, 255, 255)
INK = (32, 32, 32)
LINE = (110, 110, 110)
ROOT_FILL = (219, 233, 247)
MOD_FILL = (236, 241, 246)
LEAF_FILL = (252, 252, 252)

img = Image.new("RGB", (W, H), BG)
d = ImageDraw.Draw(img)

f_root = ImageFont.truetype(FONT, 23)
f_mod = ImageFont.truetype(FONT, 21)
f_leaf = ImageFont.truetype(FONT, 20)


def center_text(box, text, font, fill=INK):
    x0, y0, x1, y1 = box
    bb = d.textbbox((0, 0), text, font=font)
    tw, th = bb[2] - bb[0], bb[3] - bb[1]
    d.text((x0 + (x1 - x0 - tw) / 2 - bb[0], y0 + (y1 - y0 - th) / 2 - bb[1]), text, font=font, fill=fill)


def box(x0, y0, x1, y1, text, font, fill, radius=8, width=2):
    d.rounded_rectangle([x0, y0, x1, y1], radius=radius, outline=LINE, width=width, fill=fill)
    center_text((x0, y0, x1, y1), text, font)


modules = [
    ("创作对话与创意理解", ["会话路由与意图识别", "上下文预算整理", "结构化字段校验"]),
    ("导演方案生成", ["分镜草案生成", "专业评审与门禁", "人工确认与修订"]),
    ("生图指令与交付", ["提示词编译", "语义约束核验", "产物快照交付"]),
    ("图片工作台", ["视觉缺陷诊断", "框选与改图指令", "版本比较与回退"]),
    ("故事与镜头编排", ["故事初稿与校验", "镜头固定与顺序", "作品包导出"]),
    ("资产与任务管理", ["角色场景资产库", "任务中心与恢复", "预算与对账"]),
]

ROOT = (150, 20, 700, 80)
TRUNK_X = 66
MOD_X0, MOD_X1 = 84, 290
MOD_H = 50
LEAF_H, LEAF_GAP = 34, 6
LEAF_BLOCK = 3 * LEAF_H + 2 * LEAF_GAP      # 114
PITCH = 130
TOP0 = 120
LEAF_X0, LEAF_X1 = 334, 784

root_cx = (ROOT[0] + ROOT[2]) / 2
bus_y = 104
last_cy = TOP0 + (len(modules) - 1) * PITCH + LEAF_BLOCK / 2

# ---- 连线先画 ----
d.line([(root_cx, ROOT[3]), (root_cx, bus_y)], fill=LINE, width=2)
d.line([(TRUNK_X, bus_y), (root_cx, bus_y)], fill=LINE, width=2)
d.line([(TRUNK_X, bus_y), (TRUNK_X, last_cy)], fill=LINE, width=2)

for i in range(len(modules)):
    leaf_top = TOP0 + i * PITCH
    mod_y0 = leaf_top + (LEAF_BLOCK - MOD_H) / 2
    mod_cy = mod_y0 + MOD_H / 2
    d.line([(TRUNK_X, mod_cy), (MOD_X0, mod_cy)], fill=LINE, width=2)
    spine = MOD_X1 + 18
    d.line([(MOD_X1, mod_cy), (spine, mod_cy)], fill=LINE, width=2)
    d.line([(spine, leaf_top + LEAF_H / 2), (spine, leaf_top + LEAF_BLOCK - LEAF_H / 2)], fill=LINE, width=1)
    for j in range(3):
        ly = leaf_top + j * (LEAF_H + LEAF_GAP)
        d.line([(spine, ly + LEAF_H / 2), (LEAF_X0, ly + LEAF_H / 2)], fill=LINE, width=1)

# ---- 方块后画 ----
box(*ROOT, "基于多模态 Agent 的漫剧智能创作平台", f_root, ROOT_FILL, radius=10, width=3)

for i, (name, leaves) in enumerate(modules):
    leaf_top = TOP0 + i * PITCH
    mod_y0 = leaf_top + (LEAF_BLOCK - MOD_H) / 2
    box(MOD_X0, mod_y0, MOD_X1, mod_y0 + MOD_H, name, f_mod, MOD_FILL, radius=8, width=2)
    for j, leaf in enumerate(leaves):
        ly = leaf_top + j * (LEAF_H + LEAF_GAP)
        box(LEAF_X0, ly, LEAF_X1, ly + LEAF_H, leaf, f_leaf, LEAF_FILL, radius=6, width=1)

img.save("C:/Users/lin/Desktop/kantoku-agent/.workbuddy/module-figure.png", dpi=(200, 200))
print("saved", img.size)
