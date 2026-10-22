# -*- coding: utf-8 -*-
"""检查 1P/2P 选中截图，定位白色勾的范围。"""
import cv2
import numpy as np

P1 = r"C:\Users\91425\.workbuddy\clipboard-images\clipboard-2026-10-21T10-32-43-945Z-db02d52c.png"  # 1P
P2 = r"C:\Users\91425\.workbuddy\clipboard-images\clipboard-2026-10-21T10-32-43-942Z-38d48fe7.png"  # 2P

for tag, p in (("1P", P1), ("2P", P2)):
    im = cv2.imread(p, cv2.IMREAD_GRAYSCALE)
    print(tag, "shape=", im.shape)
    mask = (im > 235).astype(np.uint8)
    n, lab, stats, cent = cv2.connectedComponentsWithStats(mask)
    for i in range(1, n):
        x, y, w, h, a = stats[i]
        if a > 30:
            print(f"  comp{i}: bbox=({x},{y},{w},{h}) area={a}")
