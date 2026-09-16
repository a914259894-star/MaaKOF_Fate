"""竞技场选人。

两种模式（arena.json → Arena_Pick.custom_action_param.quick_pick）：
  - false（默认关闭）：只选栏 1~3，模板匹配 c{栏}r{行}k{列}.png（原先已验证可用）
  - true（快速选人）：不认 picks，直接点「近期使用」最左一列三个头像
    （= 最近一次上场的三人），再点确定
"""

from __future__ import annotations

import json
import os
import time

import cv2
import numpy as np

from maa.context import Context
from maa.custom_action import CustomAction

_TEMPLATE_CACHE: dict[str, "np.ndarray"] = {}
_ROLES_DIR = os.path.normpath(os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "resource", "image", "arena", "roles"))


class ArenaPick(CustomAction):
    """选 3 个角色后点确定。

    custom_action_param：
    {
        "quick_pick": false,             // true=点近期使用最左一列；false=模板选栏1-3
        "quick_targets": [[94, 98], [94, 206], [94, 315]],  // 1280 基准，最上到最下
        "picks": [[栏,行,列]×3],          // 仅 quick_pick=false 时使用，栏必须 1~3
        "roi": [0, 0, 1280, 370],
        "threshold": 0.72,
        "max_drags": 2,                  // 栏1-3 都在第一屏，几乎不用拖
        "swipe": {"from": [829, 230], "to": [433, 230], "ms": 1200, "settle_ms": 800},
        "confirm": [622, 638],
        "slots": [[39, 668], [108, 668], [177, 668]],
        "slot_r": 20,
        "slot_sat": 60
    }
    """

    def run(self, context: Context, argv: CustomAction.RunArg) -> bool:
        param = json.loads(argv.custom_action_param) if argv.custom_action_param else {}
        ctrl = context.tasker.controller
        confirm_xy = param.get("confirm", [622, 638])
        slots = param.get("slots", [[39, 668], [108, 668], [177, 668]])
        slot_r = int(param.get("slot_r", 20))
        slot_sat = float(param.get("slot_sat", 60))

        # 缺省当开启：旧 agent/覆盖丢了 quick_pick 时不再误走 picks 对角线
        if bool(param.get("quick_pick", True)):
            ok = self._quick(ctrl, param, slots, slot_r, slot_sat)
        else:
            ok = self._roster(ctrl, param, slots, slot_r, slot_sat)
        if not ok:
            return False
        time.sleep(0.4)
        ctrl.post_click(*confirm_xy).wait()
        return True

    def _quick(self, ctrl, param, slots, slot_r, slot_sat) -> bool:
        targets = param.get("quick_targets", [[94, 98], [94, 206], [94, 315]])
        if len(targets) != 3:
            print("[ArenaPick] quick_targets 必须是 3 个 [x,y]")
            return False
        print("[ArenaPick] 快速选人：点近期使用最左一列 3 人（上→下），点到左下框亮为止")
        for i, (x, y) in enumerate(targets, 1):
            if not self._click_until_slot(ctrl, int(x), int(y), i, slots, slot_r, slot_sat,
                                          tag=f"左列#{i}", max_tries=12):
                print(f"[ArenaPick] 左列第 {i} 个连点后左下框仍未亮")
                return False
        return True

    def _roster(self, ctrl, param, slots, slot_r, slot_sat) -> bool:
        picks = param.get("picks")
        if not picks or len(picks) != 3:
            print("[ArenaPick] picks 必须是 3 个 [栏,行,列]（栏仅 1~3）")
            return False
        picks = [[int(v) for v in p] for p in picks]
        roi = param.get("roi", [0, 0, 1280, 370])
        threshold = float(param.get("threshold", 0.72))
        max_drags = int(param.get("max_drags", 2))
        swipe = param.get("swipe", {"from": [829, 230], "to": [433, 230],
                                    "ms": 1200, "settle_ms": 800})
        print("[ArenaPick] 常规选人（仅栏 1~3）")
        for i, pick in enumerate(picks, 1):
            col, row, k = pick
            if not (1 <= col <= 3 and 1 <= row <= 3 and 1 <= k <= 3):
                print(f"[ArenaPick] 未开快速选人时栏只能 1~3，收到 {pick}")
                return False
            name = f"c{col}r{row}k{k}"
            tpl = self._load_template(name)
            if tpl is None:
                print(f"[ArenaPick] 找不到模板 roles/{name}.png")
                return False
            ok = False
            for dragged in range(max_drags + 1):
                hit = self._find_role(ctrl, tpl, roi, threshold, tag=name)
                if hit:
                    x, y = hit
                    if self._click_and_verify(ctrl, x, y, i, slots, slot_r, slot_sat, tag=name):
                        print(f"[ArenaPick] ✓ 已选 {name}")
                        ok = True
                        break
                    continue
                if dragged >= max_drags:
                    break
                self._do_swipe(ctrl, swipe)
            if not ok:
                print(f"[ArenaPick] {name} 未找到/未选上")
                return False
        return True

    @classmethod
    def _load_template(cls, name: str):
        if name in _TEMPLATE_CACHE:
            return _TEMPLATE_CACHE[name]
        path = os.path.join(_ROLES_DIR, f"{name}.png")
        tpl = cv2.imread(path, cv2.IMREAD_GRAYSCALE)
        if tpl is None:
            return None
        _TEMPLATE_CACHE[name] = tpl
        return tpl

    @staticmethod
    def _grab_1280(ctrl):
        ctrl.post_screencap().wait()
        img = ctrl.cached_image
        if img is None:
            return None
        sc = img.shape[1] / 1280.0
        if abs(sc - 1.0) > 0.01:
            img = cv2.resize(img, (1280, int(img.shape[0] / sc)), interpolation=cv2.INTER_AREA)
        if img.ndim == 3:
            return cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        return img

    @classmethod
    def _find_role(cls, ctrl, tpl, roi, threshold, tag=""):
        gray = cls._grab_1280(ctrl)
        if gray is None:
            return None
        rx, ry, rw, rh = roi
        H, W = gray.shape[:2]
        patch = gray[max(0, ry):min(H, ry + rh), max(0, rx):min(W, rx + rw)]
        th, tw = tpl.shape[:2]
        if patch.shape[0] < th or patch.shape[1] < tw:
            return None
        score = cv2.matchTemplate(patch, tpl, cv2.TM_CCOEFF_NORMED)
        _, maxv, _, maxloc = cv2.minMaxLoc(score)
        if maxv < threshold:
            if tag:
                print(f"[ArenaPick] {tag}: 未命中 {maxv:.3f} / {threshold}")
            return None
        cx = max(0, rx) + maxloc[0] + tw // 2
        cy = max(0, ry) + maxloc[1] + th // 2
        if tag:
            print(f"[ArenaPick] {tag}: 命中 {maxv:.3f} @ ({cx},{cy})")
        return (cx, cy)

    @classmethod
    def _click_until_slot(cls, ctrl, x, y, idx, slots, r, sat_thresh, tag="", max_tries=12) -> bool:
        """每个位置至少点 1 次；之后再连点直到已选框数 >= idx。

        空框误判为已选时（旧逻辑会 0 次点击直接跳过），至少会点下去。
        """
        clicked = 0
        for t in range(max_tries):
            n = cls._count_selected(ctrl, slots, r, sat_thresh)
            if clicked >= 1 and n >= idx:
                print(f"[ArenaPick] ✓ {tag} 已选框 {n}/{idx}（点了 {clicked} 次）@ ({x},{y})")
                return True
            clicked += 1
            print(f"[ArenaPick] {tag}: 已选框 {n}/{idx}，第 {clicked} 次点击 ({x},{y})")
            ctrl.post_click(int(x), int(y)).wait()
            time.sleep(0.4)
        n = cls._count_selected(ctrl, slots, r, sat_thresh)
        print(f"[ArenaPick] {tag}: 连点 {clicked} 次后已选框 {n}/{idx}")
        return n >= idx or clicked >= 1  # 点过就继续下一个，避免卡死不点后面两个

    @classmethod
    def _click_and_verify(cls, ctrl, x, y, idx, slots, r, sat_thresh, tag="") -> bool:
        return cls._click_until_slot(ctrl, x, y, idx, slots, r, sat_thresh, tag=tag, max_tries=8)

    @staticmethod
    def _do_swipe(ctrl, swipe: dict) -> None:
        f, t = swipe["from"], swipe["to"]
        ms = int(swipe.get("ms", 1200))
        settle = int(swipe.get("settle_ms", 800))
        if hasattr(ctrl, "post_swipe"):
            ctrl.post_swipe(int(f[0]), int(f[1]), int(t[0]), int(t[1]), ms).wait()
        else:
            ctrl.post_touch_down(int(f[0]), int(f[1]), contact=0).wait()
            time.sleep(ms / 1000)
            ctrl.post_touch_move(int(t[0]), int(t[1]), contact=0).wait()
            ctrl.post_touch_up(contact=0).wait()
        time.sleep(settle / 1000)

    @staticmethod
    def _count_selected(ctrl, slots, r: int, sat_thresh: float) -> int:
        st = ctrl.post_screencap().wait()
        if getattr(st, "succeeded", None) is False:
            print("[ArenaPick] 截图失败（下面读到的是旧帧！）", flush=True)
        img = ctrl.cached_image
        if img is None:
            return 0
        sc = img.shape[1] / 1280.0
        n = 0
        for cx, cy in slots:
            x1, y1 = int((cx - r) * sc), int((cy - r) * sc)
            x2, y2 = int((cx + r) * sc), int((cy + r) * sc)
            patch = img[y1:y2, x1:x2]
            if patch.size == 0:
                continue
            if patch.ndim == 3:
                patch = cv2.cvtColor(patch, cv2.COLOR_BGR2GRAY)
            # 空≈15-33，点亮≈50-70：std>40 区分（勿用饱和度，空框 sat 也有 64）
            if float(np.std(patch)) > 40:
                n += 1
        return n
