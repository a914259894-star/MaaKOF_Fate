# -*- coding: utf-8 -*-
"""裁出 1P/2P 的勾候选区域，输出预览图 + 二值化预览。"""
import cv2

P1 = r"C:\Users\91425\.workbuddy\clipboard-images\clipboard-2026-10-21T10-32-43-945Z-db02d52c.png"  # 1P 175x177
P2 = r"C:\Users\91425\.workbuddy\clipboard-images\clipboard-2026-10-21T10-32-43-942Z-38d48fe7.png"  # 2P 169x172
OUT = r"D:\Projects\MaaKOF_Fate\MaaKOF_Fate\.workbuddy\tmp"

# 候选区域 (x, y, w, h)，2560 基准像素
crops = {
    "p1_check": (P1, (53, 64, 78, 62)),
    "p2_check": (P2, (52, 72, 78, 68)),
    "p2_wide":  (P2, (40, 60, 110, 90)),
}
for name, (p, (x, y, w, h)) in crops.items():
    im = cv2.imread(p, cv2.IMREAD_GRAYSCALE)
    crop = im[y:y+h, x:x+w]
    cv2.imwrite(fr"{OUT}\{name}.png", crop)
    _, bw = cv2.threshold(crop, 200, 255, cv2.THRESH_BINARY)
    cv2.imwrite(fr"{OUT}\{name}_bw.png", bw)
    print(name, crop.shape)
