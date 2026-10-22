# -*- coding: utf-8 -*-
"""从 1P 选中截图裁剪勾图标 → 二值化 → 缩放到 1280x720 基准 → 保存模板并验证。

正向验证：模板应能在 1P 和 2P 截图中高分命中勾的位置。
反向验证：模板对空槽占位图（slot1~3_empty.png，已选状态之外）应低分。
"""
import os

import cv2
import numpy as np

P1 = r"C:\Users\91425\.workbuddy\clipboard-images\clipboard-2026-10-21T10-32-43-945Z-db02d52c.png"  # 1P 175x177
P2 = r"C:\Users\91425\.workbuddy\clipboard-images\clipboard-2026-10-21T10-32-43-942Z-38d48fe7.png"  # 2P 169x172
OUT_DIR = r"D:\Projects\MaaKOF_Fate\MaaKOF_Fate\assets\resource\image\arena\slots"
TMP = r"D:\Projects\MaaKOF_Fate\MaaKOF_Fate\.workbuddy\tmp"

BRIGHT = 190  # 白勾亮度阈值（截图与游戏实拍共用的二值化线）

im1 = cv2.imread(P1, cv2.IMREAD_GRAYSCALE)
im2 = cv2.imread(P2, cv2.IMREAD_GRAYSCALE)

# 勾的连通域 bbox（2560 基准）：1P comp5=(59,72,64,49)，外扩 2px
crop = im1[70:123, 57:125]
_, bw = cv2.threshold(crop, BRIGHT, 255, cv2.THRESH_BINARY)
tpl = cv2.resize(bw, (bw.shape[1] // 2, bw.shape[0] // 2), interpolation=cv2.INTER_AREA)
_, tpl = cv2.threshold(tpl, 127, 255, cv2.THRESH_BINARY)
os.makedirs(OUT_DIR, exist_ok=True)
cv2.imwrite(os.path.join(OUT_DIR, "slot_checked.png"), tpl)
cv2.imwrite(os.path.join(TMP, "slot_checked_preview.png"), tpl)
print(f"template shape={tpl.shape} nonzero={int((tpl > 0).sum())}")

def to_bw_half(im):
    _, b = cv2.threshold(im, BRIGHT, 255, cv2.THRESH_BINARY)
    return cv2.resize(b, (b.shape[1] // 2, b.shape[0] // 2), interpolation=cv2.INTER_AREA)

for tag, im in (("1P", im1), ("2P", im2)):
    b2 = to_bw_half(im)
    score = cv2.matchTemplate(b2, tpl, cv2.TM_CCOEFF_NORMED)
    _, maxv, _, maxloc = cv2.minMaxLoc(score)
    print(f"[正向] {tag}: max={maxv:.3f} @ {maxloc}")

for i in (1, 2, 3):
    e = cv2.imread(os.path.join(OUT_DIR, f"slot{i}_empty.png"), cv2.IMREAD_GRAYSCALE)
    if e is None:
        print(f"[反向] slot{i}_empty.png 不存在，跳过")
        continue
    if e.shape[0] < tpl.shape[0] or e.shape[1] < tpl.shape[1]:
        e = cv2.resize(e, (max(e.shape[1], tpl.shape[1]), max(e.shape[0], tpl.shape[0])))
    _, eb = cv2.threshold(e, BRIGHT, 255, cv2.THRESH_BINARY)
    score = cv2.matchTemplate(eb, tpl, cv2.TM_CCOEFF_NORMED)
    print(f"[反向] slot{i}_empty: max={float(score.max()):.3f}")
