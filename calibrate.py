"""技能按钮"亮暗"阈值校准脚本。

用法：
    python calibrate.py --shot shot_ready.png     # 就绪态截图（技能可放）
    python calibrate.py --shot shot_dark.png      # 冷却态截图（技能不可放）

自动读取 combat.json 里所有用 SkillReadyBright 的节点，打印每个节点 roi 的
亮度/饱和度值。对比两态数值，取中间值填进对应节点的 "threshold"。

roi 以 1280 为基准，脚本会按截图实际宽度自动缩放。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import cv2
import numpy as np

_HERE = Path(__file__).parent
# 兼容两种布局：官方模板 agent/ 在项目根；本工程 agent/ 在 assets/ 下
sys.path.insert(0, str(_HERE / "agent"))
sys.path.insert(0, str(_HERE / "assets" / "agent"))
sys.path.insert(0, str(_HERE))

from combat_recognition import _metric

DEFAULT_JSON = "assets/resource/pipeline/combat.json"


def parse_args():
    ap = argparse.ArgumentParser(description="SkillReadyBright 阈值校准")
    ap.add_argument("--shot", required=True, help="截图路径（就绪态/冷却态各截一张）")
    ap.add_argument("--json", default=DEFAULT_JSON, help=f"pipeline 文件（默认 {DEFAULT_JSON}）")
    ap.add_argument("--channel", default=None, help="只看某个通道（v/s/gray/min_vs），默认按节点配置")
    ap.add_argument("--draw", action="store_true",
                    help="坐标核对模式：把 BUTTONS 点击点（红点）和所有节点 roi（绿框）画到截图上，"
                         "输出 *_checked.png 后退出")
    return ap.parse_args()


def draw_check(args):
    """坐标核对：BUTTONS 红点 + JOYSTICK 蓝圈 + pipeline 全部 roi 绿框。"""
    import os

    from combat_actions import BUTTONS, JOYSTICK, JOYSTICK_RADIUS

    img = cv2.imread(args.shot)
    if img is None:
        print(f"读不到图片: {args.shot}")
        sys.exit(1)
    scale = img.shape[1] / 1280.0

    data = json.load(open(args.json, encoding="utf-8"))
    for name, node in data.items():
        if not isinstance(node, dict):
            continue
        roi = node.get("roi")
        if not roi or len(roi) != 4:
            continue
        x, y, w, h = [int(v * scale) for v in roi]
        cv2.rectangle(img, (x, y), (x + w, y + h), (0, 255, 0), 2)
        cv2.putText(img, name, (x, max(14, y - 5)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 1, cv2.LINE_AA)

    for key, (bx, by) in BUTTONS.items():
        cx, cy = int(bx * scale), int(by * scale)
        cv2.circle(img, (cx, cy), 6, (0, 0, 255), -1)
        cv2.putText(img, key, (cx + 8, cy + 4),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 0, 255), 1, cv2.LINE_AA)

    jx, jy = JOYSTICK
    cv2.circle(img, (int(jx * scale), int(jy * scale)),
               int(JOYSTICK_RADIUS * scale), (255, 128, 0), 2)

    root, ext = os.path.splitext(args.shot)
    out = root + "_checked" + (ext or ".png")
    cv2.imwrite(out, img)
    print(f"已保存: {out}")
    print("红点=点击坐标（应落在按钮正中），绿框=识别 roi，蓝圈=摇杆活动范围。")
    print("若红点全部偏向按钮右上/左下同侧 → 你的四元组是中心坐标而非左上角，告知助手重算。")


def main():
    args = parse_args()

    if args.draw:
        draw_check(args)
        return

    nodes = {}
    data = json.load(open(args.json, encoding="utf-8"))
    for name, node in data.items():
        if name.startswith("//") or not isinstance(node, dict):
            continue
        if node.get("recognition") != "Custom":
            continue
        if node.get("custom_recognition") != "SkillReadyBright":
            continue
        param = node.get("custom_recognition_param") or {}
        nodes[name] = {
            "roi": tuple(node.get("roi", (0, 0, 0, 0))),
            "mode": param.get("mode", "median"),
            "v_thresh": float(param.get("v_thresh", 160)),
        }

    if not nodes:
        print("combat.json 里没有使用 SkillReadyBright 的节点。")
        return

    img = cv2.imread(args.shot)
    if img is None:
        print(f"读不到图片: {args.shot}")
        sys.exit(1)

    scale = img.shape[1] / 1280.0
    print(f"截图 {img.shape[1]}x{img.shape[0]}，缩放系数 x{scale:.3f}（以 1280 为基准）\n")

    print(f"{'节点':<16}{'roi(1280基准)':<22}{'medianV':>9}{'ratio':>8}{'medianS':>9}{'gray':>8}")
    print("-" * 76)
    for name, cfg in nodes.items():
        x, y, w, h = cfg["roi"]
        x1, y1 = int(x * scale), int(y * scale)
        x2, y2 = int((x + w) * scale), int((y + h) * scale)
        x1, y1 = max(0, x1), max(0, y1)
        x2, y2 = min(img.shape[1], x2), min(img.shape[0], y2)
        patch = img[y1:y2, x1:x2]
        if patch.size == 0:
            print(f"{name:<16} roi 越界，跳过")
            continue
        med_v = _metric(patch, "v", "median")
        ratio = _metric(patch, "v", "ratio", v_thresh=cfg["v_thresh"])
        med_s = _metric(patch, "s", "median")
        gray = _metric(patch, "gray", "median")
        print(
            f"{name:<16}{f'{x},{y},{w},{h}':<22}"
            f"{med_v:>9.1f}{ratio:>8.2f}{med_s:>9.1f}{gray:>8.1f}"
        )

    print("\n怎么填阈值：就绪态数值 A、冷却态数值 B，threshold 取 (A+B)/2，")
    print("想防误报取 B + 0.7*(A-B)（更接近就绪值）。")
    print("- mode=median 的节点对比 medianV（或 gray/medianS 列，取决于你用的 channel）；")
    print("- mode=ratio  的节点对比 ratio 列。")
    print("确认两态差异太小的话，说明该 roi 对亮暗不敏感，换 roi 位置/尺寸或 v_thresh。")


if __name__ == "__main__":
    main()
