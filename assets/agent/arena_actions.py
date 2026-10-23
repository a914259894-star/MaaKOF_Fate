"""竞技场选人。

两种模式（arena.json → Arena_Pick.custom_action_param.quick_pick）：
  - false（默认关闭）：模板匹配 c{栏}r{行}k{列}.png，栏 1~10、行/列 1~3。
    页面按 picks 的栏位自动判定，支持混页（如 1,1,1 与 8,1,1 混合）：
    栏≤3 归第一页(不拖)、栏≥8 归最后一页(拖到尽头)、栏4~7 按比例拖；
    按「第一页→中间→最后一页」顺序逐页前进选人，全程只向右拖、不回拖。
  - true（快速选人）：不认 picks，直接点「近期使用」最左一列三个头像
    （= 最近一次上场的三人），再点确定

选人成功判定（勾判定，2026-10-21 重构）：
点中角色后，该角色所在的网格格子会出现白勾（金框 + 1P/2P 角标）。
判定 = 在「被点格子中心附近」（check_half 半径内）搜索选中勾模板
（slots/slot_checked.png，40x41 二值图，与截图统一按 check_bright 二值化）。
一个勾 = 一个角色选上，与 1P/2P 无关——我们只验证自己点过的格子，
不做「两侧都勾上」之类的对方侧判定（勾只出现在被点的格子里，
且页面会滚动，固定 ROI 必然失效）。
实测（现场帧 2026.10.21-17.45）：勾格子 0.98 / 0.89，无勾格子 0.0，
底部槽/大立绘等无关区域 ≤0.70 且与格子 ROI 不相交 → 阈值 0.7。
"""

from __future__ import annotations

import json
import os
import time

import cv2
import numpy as np

from maa.context import Context
from maa.custom_action import CustomAction

_TEMPLATE_CACHE: dict[str, "cv2.Mat"] = {}


def imread_cn(path: str, flags: int = cv2.IMREAD_GRAYSCALE):
    """cv2.imread 在 Windows 上走窄字符 API，路径含中文（如 D:\\桌面\\kof）直接
    can't open/read file，导致选人模板全部加载失败、选人节点立即失败停止。
    改用 np.fromfile + cv2.imdecode（Python 文件接口），任意路径都能读。
    读取失败返回 None（与 imread 行为一致）。"""
    try:
        return cv2.imdecode(np.fromfile(path, dtype=np.uint8), flags)
    except (OSError, ValueError):
        return None


def _find_roles_dir() -> str:
    """模板目录：agent 可能在根 agent/ 或 assets/agent/，自动适配两种布局。"""
    here = os.path.dirname(os.path.abspath(__file__))
    for rel in (os.path.join("..", "assets", "resource", "image", "arena", "roles"),   # 根 agent/
                os.path.join("..", "resource", "image", "arena", "roles")):            # assets/agent/
        d = os.path.normpath(os.path.join(here, rel))
        if os.path.isdir(d):
            return d
    return os.path.normpath(os.path.join(here, "..", "assets", "resource", "image", "arena", "roles"))


_ROLES_DIR = _find_roles_dir()
_ARENA_DIR = os.path.dirname(_ROLES_DIR)  # .../image/arena（roles 的上级，含 slots/）


class ArenaPick(CustomAction):
    """选 3 个角色后点确定。

    custom_action_param：
    {
        "quick_pick": false,             // true=点近期使用最左一列；false=模板选栏1-3
        "quick_targets": [[94, 98], [94, 206], [94, 315]],  // 1280 基准，最上到最下
        "picks": "栏,行,列;栏,行,列;栏,行,列" 或 [[栏,行,列]×3],  // 仅 quick_pick=false；栏1~10/行列1~3；UI 文本框为字符串
        "roi": [0, 0, 1280, 370],
        "threshold": 0.72,
        "max_drags": 2,                  // 预留（当前逐页前进逻辑不再逐角色回拖）
        "last_drags": 10,                // 自动判定为最后一页时，从右往左拖此次数（确保停在最右端拖不动）
        // 页面由 picks 栏位自动判定，支持混页：栏≤3 归第一页(不拖)、栏≥8 归最后一页(拖到尽头)、栏4~7 按比例拖；
        // 按「第一页→中间→最后一页」顺序逐页前进选人，全程只向右拖、不回拖。
        "swipe": {"from": [829, 230], "to": [433, 230], "ms": 600, "settle_ms": 500},
        // 翻页提速：1200/800 时代 10 次拖页约 20s，会与选人倒计时竞速——
        // 倒计时归零游戏自动锁定阵容并跳到「确定出手顺序」页，剩余拖页
        // 全落在顺序页上，随后找不到角色 → 整个任务失败（2026-10-23 现场）。
        "confirm": [622, 638],
        // —— 勾判定参数 ——
        "check_half": 60,        // 勾搜索半径：以被点格子中心为圆心取 (2*half)^2 窗口（格子约 105px）
        "check_threshold": 0.7,  // 勾模板匹配阈值（实测真勾 0.98/0.89、无勾 0.0）
        "check_bright": 190,     // 白勾二值化亮度线
        "click_max_tries": 3,    // 单个角色最多点击次数（勾一直不出现时）
        "miss_confirm": 2,       // 连续 N 次检测无勾才认定未选上并补点（防检测抖动把已选角色 toggle 掉）
        "check_timeout": 5,      // 每次点击后等待勾出现的秒数
        // —— 找不到角色 / 兜底 ——
        "find_retries": 3,       // 模板未命中时重试截图次数（转场/截图抖动容错）
        "find_retry_gap": 0.8,   // 重试间隔秒
        // 「确定出手顺序」页兜底：选人中途倒计时归零游戏会自动锁定阵容并
        // 跳到该页（不会等你点确定），此时选人已由游戏接管——检测到该页
        // 直接放行让 pipeline 走 WaitBattle，不再因找不到角色而整任务失败。
        "order_threshold": 0.85, // order_confirm.png 匹配阈值（正例 0.999 / 反例 ≤0.67）
        "order_roi": [0, 0, 1280, 160]
    }
    """

    def run(self, context: Context, argv: CustomAction.RunArg) -> bool:
        param = json.loads(argv.custom_action_param) if argv.custom_action_param else {}
        ctrl = context.tasker.controller
        confirm_xy = param.get("confirm", [622, 638])

        # 缺省当开启：旧 agent/覆盖丢了 quick_pick 时不再误走 picks 对角线
        if bool(param.get("quick_pick", True)):
            ok = self._quick(ctrl, param)
        else:
            ok = self._roster(ctrl, param)
        if not ok:
            # 找不到角色/没选上：优先判断是否已被游戏接管（跳到顺序页）
            if self._order_screen(ctrl, param):
                print("[ArenaPick] 检测到「确定出手顺序」页（倒计时归零游戏已自动锁定阵容），"
                      "跳过选人放行继续", flush=True)
                return True
            return False
        if self._order_screen(ctrl, param):
            # 极端情况：选人逻辑走完但期间游戏已自动锁定跳页，确定键不能再点
            print("[ArenaPick] 确定前检测到「确定出手顺序」页（游戏已自动锁定），跳过点确定", flush=True)
            return True
        time.sleep(0.4)
        ctrl.post_click(*confirm_xy)  # 发完就走，不等回执（慢环境防阻塞）
        return True

    @classmethod
    def _order_screen(cls, ctrl, param) -> bool:
        """当前画面是否为「点击格斗家确定顺序」页。

        选人倒计时归零时游戏自动锁定阵容并跳到该页（2026-10-23 现场：
        剩余翻页落在顺序页上、随后 c8r2k2 找不到 → Arena_Pick ret=false
        → 整任务停止）。模板为页顶橙色标题文字，正例 0.999、
        选人格子页/主页面等反例 ≤0.67，0.85 余量充足。
        """
        tpl = cls._load_rel("order_confirm")
        if tpl is None:
            return False
        gray = cls._grab_1280(ctrl)
        if gray is None:
            return False
        H, W = gray.shape[:2]
        rx, ry, rw, rh = param.get("order_roi", [0, 0, 1280, 160])
        patch = gray[max(0, ry):min(H, ry + rh), max(0, rx):min(W, rx + rw)]
        if patch.shape[0] < tpl.shape[0] or patch.shape[1] < tpl.shape[1]:
            return False
        score = cv2.matchTemplate(patch, tpl, cv2.TM_CCOEFF_NORMED)
        ms = float(score.max())
        th = float(param.get("order_threshold", 0.85))
        if ms >= th:
            print(f"[ArenaPick] 顺序页判定 [OK] {ms:.3f} ≥ {th}", flush=True)
            return True
        return False

    def _quick(self, ctrl, param) -> bool:
        targets = param.get("quick_targets", [[94, 98], [94, 206], [94, 315]])
        if len(targets) != 3:
            print("[ArenaPick] quick_targets 必须是 3 个 [x,y]")
            return False
        print("[ArenaPick] 快速选人：点近期使用最左一列 3 人（上→下）")
        # 慢环境优化：agent↔宿主图像传输可能降级（空图/单次数十秒），
        # 点击一律"发完就走"不等回执；先三人各点 1 次，再限时验证补漏。
        for x, y in targets:
            ctrl.post_click(int(x), int(y))
            time.sleep(0.35)
        # 验证窗口上限：到点直接返回去点确认（游戏选人倒计时也会自动确认）
        deadline = time.time() + float(param.get("verify_timeout", 12))
        while time.time() < deadline:
            # 一个勾 = 一个角色选上：验证每个目标格是否有勾
            flags = [self._cell_has_check(ctrl, x, y, param) for x, y in targets]
            if all(flags):
                print(f"[ArenaPick] [OK] 快速选人 3 格全部有勾 {flags}")
                return True
            # 只对无勾的格子补点；有勾的绝不再点（否则 toggle 掉）
            for (x, y), ok in zip(targets, flags):
                if not ok:
                    print(f"[ArenaPick] 补点 左列 ({x},{y})（当前勾 {flags}）")
                    ctrl.post_click(int(x), int(y))
                    time.sleep(0.35)
            time.sleep(0.5)
        print("[ArenaPick] 选人结束（限时到），照常点确认；若当前不是「近期使用」快速选人页，"
              "请把 UI 选项「快速选人」设为关闭走模板选人", flush=True)
        return True

    def _roster(self, ctrl, param) -> bool:
        # picks 兼容两种来源：UI 文本框注入的字符串 "栏,行,列;栏,行,列;..."
        # 或 arena.json 直接写的数组 [[栏,行,列], ...]
        picks = self._parse_picks(param.get("picks"))
        if not picks or len(picks) != 3:
            print(f"[ArenaPick] picks 必须是 3 个 [栏,行,列]，收到：{param.get('picks')}")
            return False
        roi = param.get("roi", [0, 0, 1280, 370])
        threshold = float(param.get("threshold", 0.72))
        swipe = param.get("swipe", {"from": [829, 230], "to": [433, 230],
                                    "ms": 600, "settle_ms": 500})
        last_drags = int(param.get("last_drags", 10))

        # —— 页面自动判定（不再依赖独立「选人页面」选项），并支持混页 ——
        # 栏≤3 归第一页(不拖)、栏≥8 归最后一页(拖到尽头)、栏4~7 按比例 (col-3) 次拖；
        # 按「第一页 → 中间 → 最后一页」顺序逐页前进选人，全程只向右拖、不回拖。
        front = [p for p in picks if p[0] <= 3]
        mid = [p for p in picks if 4 <= p[0] <= 7]
        back = [p for p in picks if p[0] >= 8]
        ordered = (sorted(front, key=lambda p: p[0])
                   + sorted(mid, key=lambda p: p[0])
                   + sorted(back, key=lambda p: p[0]))

        def _target_drags(col: int) -> int:
            if col <= 3:
                return 0
            if col >= 8:
                return last_drags
            return max(0, col - 3)

        print(f"[ArenaPick] 选人栏位 = {picks} → "
              f"第一页{[p[0] for p in front]} / 中间{[p[0] for p in mid]} / 最后一页{[p[0] for p in back]}")
        current_drags = 0
        for i, pick in enumerate(ordered, 1):
            col, row, k = pick
            if not (1 <= col <= 10 and 1 <= row <= 3 and 1 <= k <= 3):
                print(f"[ArenaPick] 栏仅 1~10、行/列 1~3，收到 {pick}")
                return False
            # 逐页前进：需要更多拖拽时才向右拖，绝不回拖
            tgt = _target_drags(col)
            while current_drags < tgt:
                self._do_swipe(ctrl, swipe)
                current_drags += 1
            name = f"c{col}r{row}k{k}"
            tpl = self._load_template(name)
            if tpl is None:
                print(f"[ArenaPick] 找不到模板 roles/{name}.png")
                return False
            hit = self._find_role(ctrl, tpl, roi, threshold, tag=name,
                                  retries=param.get("find_retries", 3),
                                  retry_gap=float(param.get("find_retry_gap", 0.8)))
            if hit and self._click_until_check(
                    ctrl, hit[0], hit[1], i, param, tag=name):
                print(f"[ArenaPick] [OK] 已选 {name}")
            else:
                print(f"[ArenaPick] {name} 未找到/未选上")
                return False
        return True

    @staticmethod
    def _parse_picks(raw) -> list | None:
        """把 picks 参数统一成 [[栏,行,列], ...]。

        支持：
          - 列表：[[1,1,1],[2,1,1],[3,1,1]]（arena.json 直接写）
          - 字符串："1,1,1;2,1,1;3,1,1"（UI 文本框经 {picks} 注入）
        无法解析返回 None。
        """
        if raw is None:
            return None
        if isinstance(raw, list):
            out = []
            for p in raw:
                if not (isinstance(p, (list, tuple)) and len(p) == 3):
                    return None
                out.append([int(p[0]), int(p[1]), int(p[2])])
            return out
        if isinstance(raw, str):
            raw = raw.strip()
            if not raw:
                return None
            out = []
            for part in raw.split(";"):
                nums = part.split(",")
                if len(nums) != 3:
                    return None
                try:
                    out.append([int(x) for x in nums])
                except ValueError:
                    return None
            return out
        return None

    @classmethod
    def _load_rel(cls, rel: str):
        """加载 arena 目录下相对路径模板（如 "roles/c1r1k1"、"slots/slot_checked"）。"""
        if rel in _TEMPLATE_CACHE:
            return _TEMPLATE_CACHE[rel]
        path = os.path.join(_ARENA_DIR, *rel.split("/")) + ".png"
        tpl = imread_cn(path, cv2.IMREAD_GRAYSCALE)
        if tpl is None:
            return None
        _TEMPLATE_CACHE[rel] = tpl
        return tpl

    @classmethod
    def _load_template(cls, name: str):
        return cls._load_rel(f"roles/{name}")

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
    def _find_role(cls, ctrl, tpl, roi, threshold, tag="",
                   retries: int = 3, retry_gap: float = 0.8):
        """模板找角色；未命中时重试截图几次（转场/截图抖动容错）。"""
        retries = max(1, int(retries))
        hit = None
        for attempt in range(retries):
            gray = cls._grab_1280(ctrl)
            if gray is None:
                time.sleep(retry_gap)
                continue
            rx, ry, rw, rh = roi
            H, W = gray.shape[:2]
            patch = gray[max(0, ry):min(H, ry + rh), max(0, rx):min(W, rx + rw)]
            th, tw = tpl.shape[:2]
            if patch.shape[0] < th or patch.shape[1] < tw:
                time.sleep(retry_gap)
                continue
            score = cv2.matchTemplate(patch, tpl, cv2.TM_CCOEFF_NORMED)
            _, maxv, _, maxloc = cv2.minMaxLoc(score)
            if maxv >= threshold:
                cx = max(0, rx) + maxloc[0] + tw // 2
                cy = max(0, ry) + maxloc[1] + th // 2
                if tag:
                    print(f"[ArenaPick] {tag}: 命中 {maxv:.3f} @ ({cx},{cy})"
                          + (f"（第 {attempt + 1} 次截图）" if attempt else ""))
                return (cx, cy)
            if tag:
                print(f"[ArenaPick] {tag}: 未命中 {maxv:.3f} / {threshold}"
                      + (f"，重试 {attempt + 1}/{retries - 1}" if attempt < retries - 1 else ""))
            time.sleep(retry_gap)
        return None

    @classmethod
    def _cell_has_check(cls, ctrl, cx, cy, param) -> bool:
        """判定 (cx,cy) 附近的格子是否出现选中勾。

        点中角色后白勾出现在该角色的网格格子内（金框 + 1P/2P 角标），
        一个勾 = 该角色被选上，与 1P/2P 无关。截图与模板统一按
        check_bright 二值化后 TM_CCOEFF_NORMED。
        只在以 (cx,cy) 为中心的 check_half 窗口内搜索——页面会滚动，
        固定坐标 ROI 必然失效，必须围绕实际点击位置。
        """
        tpl = cls._load_rel("slots/slot_checked")
        if tpl is None:
            print("[ArenaPick] [!] 缺少 slots/slot_checked.png，无法做勾判定", flush=True)
            return False
        gray = cls._grab_1280(ctrl)
        if gray is None:
            print("[ArenaPick] 截图为空（图像通道降级），按无勾处理", flush=True)
            return False
        H, W = gray.shape[:2]
        half = int(param.get("check_half", 60))
        bright = int(param.get("check_bright", 190))
        th = float(param.get("check_threshold", 0.7))
        x0, x1 = max(0, int(cx) - half), min(W, int(cx) + half)
        y0, y1 = max(0, int(cy) - half), min(H, int(cy) + half)
        patch = gray[y0:y1, x0:x1]
        if patch.shape[0] < tpl.shape[0] or patch.shape[1] < tpl.shape[1]:
            return False  # 窗口越界无法验证
        _, bw = cv2.threshold(patch, bright, 255, cv2.THRESH_BINARY)
        if not bw.any():
            return False  # 全黑（无勾）
        score = cv2.matchTemplate(bw, tpl, cv2.TM_CCOEFF_NORMED)
        ms = float(score.max())
        if ms >= th:
            print(f"[ArenaPick] 勾判定 [OK] {ms:.2f} @({int(cx)},{int(cy)})")
            return True
        return False

    @classmethod
    def _click_until_check(cls, ctrl, x, y, idx, param, tag="") -> bool:
        """点 (x,y) 直到该格子出现选中勾。

        一个勾 = 一个角色选上（勾就在被点的格子内，与 1P/2P 无关）。
        防 toggle：点击后先等勾出现；连续 miss_confirm 次检测都无勾
        才认定确实没选上、才补点——避免检测抖动导致把已选角色点掉。
        若点击前该格已有勾（重复运行等），直接认定已选上、不再点击。
        """
        if cls._cell_has_check(ctrl, x, y, param):
            print(f"[ArenaPick] [OK] {tag} {idx}号位已有勾（免点）")
            return True
        max_clicks = max(1, int(param.get("click_max_tries", 3)))
        miss_need = max(1, int(param.get("miss_confirm", 2)))
        timeout = float(param.get("check_timeout", 5))
        for attempt in range(max_clicks):
            word = "点击" if attempt == 0 else f"补点第 {attempt} 次"
            print(f"[ArenaPick] {tag}: {idx}号位{word} ({x},{y})")
            ctrl.post_click(int(x), int(y)).wait()
            time.sleep(0.4)
            # 等勾出现：连续 miss_need 次无勾才判定失败
            miss = 0
            deadline = time.time() + timeout
            while time.time() < deadline:
                if cls._cell_has_check(ctrl, x, y, param):
                    print(f"[ArenaPick] [OK] {tag} {idx}号位已选上（共点 {attempt + 1} 次）")
                    return True
                miss += 1
                if miss >= miss_need:
                    break
                time.sleep(0.5)
        print(f"[ArenaPick] [X] {tag}: 点了 {max_clicks} 次仍无勾（勾判定异常或页面不对）")
        return False

    @staticmethod
    def _do_swipe(ctrl, swipe: dict) -> None:
        f, t = swipe["from"], swipe["to"]
        ms = int(swipe.get("ms", 600))
        settle = int(swipe.get("settle_ms", 500))
        if hasattr(ctrl, "post_swipe"):
            ctrl.post_swipe(int(f[0]), int(f[1]), int(t[0]), int(t[1]), ms).wait()
        else:
            ctrl.post_touch_down(int(f[0]), int(f[1]), contact=0).wait()
            time.sleep(ms / 1000)
            ctrl.post_touch_move(int(t[0]), int(t[1]), contact=0).wait()
            ctrl.post_touch_up(contact=0).wait()
        time.sleep(settle / 1000)
