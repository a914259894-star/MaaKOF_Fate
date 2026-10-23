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
        "max_drags": 2,                  // 预留（当前逻辑不再逐角色回拖）
        "last_drags": 8,                 // 拖到末页的安全上限（实际以"拖到拖不动"为准，自动收敛到最少次数）
        // 仅支持首页(栏1~3)与末页(栏8~10)：栏4~7 不允许（UI 已 restrict）。
        // 选人顺序：先选首页 3 人（不拖）→ 一次性拖到末页（拖到拖不动为止）→ 选末页 3 人。
        // 全程只向右拖、不回拖；拖拽用"页面是否还动"判定到位，避免固定次数导致未到底。
        "swipe": {"from": [829, 230], "to": [300, 230], "ms": 350, "settle_ms": 250},
        // 翻页再提速：单次拖拽更长(529px)、更快(350ms)、间隔更短(250ms)；
        // 拖到末页用 stuck 检测收敛到最少次数，与选人倒计时拉开更大余量。
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
        swipe = param.get("swipe", {"from": [829, 230], "to": [300, 230],
                                    "ms": 350, "settle_ms": 250})
        last_drags = int(param.get("last_drags", 8))

        # —— 仅支持首页(栏1~3)与末页(栏8~10)；栏4~7 不允许 ——
        bad = [p for p in picks if 4 <= p[0] <= 7]
        if bad:
            print(f"[ArenaPick] 仅支持首页(栏1~3)与末页(栏8~10)，"
                  f"栏4~7 不允许：{bad}（请在 UI 改成首页或末页栏位）")
            return False
        # 三组栏位可以「全首页」「全末页」或「混合」，按栏位自动决定要不要翻页。
        # v1.1.10 曾强制要求首页+末页各 ≥1 个，导致用户配 8,2,2;9,3,1;9,3,3
        # （全末页）时一进选人页就 return False → 整任务停止，已修正。
        front = [p for p in picks if p[0] <= 3]
        back = [p for p in picks if p[0] >= 8]
        print(f"[ArenaPick] 选人栏位 = {picks} → 首页{[p[0] for p in front]} / 末页{[p[0] for p in back]}")

        done, failed = 0, []

        def _take(pick):
            nonlocal done
            if self._pick_one(ctrl, pick, done + 1, roi, threshold, param):
                done += 1
            else:
                failed.append(pick)

        # 1) 首页角色（当前就在首页，不拖）
        for pick in front:
            _take(pick)

        # 2) 有末页角色才翻页：一次性拖到末页（拖到拖不动为止）
        if back:
            n_drags = self._drag_to_end(ctrl, swipe, max_drags=last_drags)
            print(f"[ArenaPick] 已拖到末页（实际拖拽 {n_drags} 次）")
            for pick in back:
                _take(pick)

        # 单个角色没找到不再直接整任务失败（v1.1.10 之前那样会让任务一停到底），
        # 至少选上 1 个就继续点确定，剩下的交给游戏倒计时自动锁定。
        if done == 0:
            print(f"[ArenaPick] [X] 3 个角色一个都没选上：{failed}")
            return False
        if failed:
            print(f"[ArenaPick] [!] {len(failed)} 个角色未选上 {failed}，"
                  f"已选 {done}/3，继续点确定（倒计时归零游戏会自动锁定阵容）")
        return True

    def _pick_one(self, ctrl, pick, idx, roi, threshold, param) -> bool:
        """选单个角色：找模板 → 点击 → 等勾出现。失败返回 False。"""
        col, row, k = pick
        if not (1 <= col <= 10 and 1 <= row <= 3 and 1 <= k <= 3):
            print(f"[ArenaPick] 栏仅 1~10、行/列 1~3，收到 {pick}")
            return False
        name = f"c{col}r{row}k{k}"
        tpl = self._load_template(name)
        if tpl is None:
            print(f"[ArenaPick] 找不到模板 roles/{name}.png")
            return False
        hit = self._find_role(ctrl, tpl, roi, threshold, tag=name,
                              retries=param.get("find_retries", 3),
                              retry_gap=float(param.get("find_retry_gap", 0.8)))
        if hit and self._click_until_check(
                ctrl, hit[0], hit[1], idx, param, tag=name):
            print(f"[ArenaPick] [OK] 已选 {name}")
            return True
        print(f"[ArenaPick] {name} 未找到/未选上")
        return False

    def _drag_to_end(self, ctrl, swipe, max_drags: int = 8) -> int:
        """向右拖到末页：每次拖后比对选人区域是否还变化，连两次不变即到位。

        用"页面是否还动"判定，避免固定次数没拖到底；同时受 max_drags 兜底，
        实际拖拽次数收敛到最小需要值（单次拖拽已加长，远少于旧 10 次）。
        """
        prev = self._page_signature(ctrl)
        stuck = 0
        n = 0
        while n < max_drags:
            self._do_swipe(ctrl, swipe)
            n += 1
            now = self._page_signature(ctrl)
            if prev is not None and now is not None and self._sig_similar(prev, now):
                stuck += 1
                if stuck >= 2:
                    print(f"[ArenaPick] 选人区域连续 {stuck} 次无变化，已到末页（第 {n} 次拖拽）")
                    return n
            else:
                stuck = 0
            prev = now
        print(f"[ArenaPick] 达到拖拽上限 {max_drags}，按末页处理")
        return n

    @classmethod
    def _page_signature(cls, ctrl):
        """选人区域下采样灰度签名，用于判断翻页后画面是否还变化。"""
        gray = cls._grab_1280(ctrl)
        if gray is None:
            return None
        y0, y1 = 60, 370          # 选人网格主区域（避开顶部标题/底部按钮）
        x0, x1 = 0, 1280
        H, W = gray.shape[:2]
        y0, y1 = max(0, y0), min(H, y1)
        x0, x1 = max(0, x0), min(W, x1)
        if x1 - x0 < 8 or y1 - y0 < 8:
            return None
        return cv2.resize(gray[y0:y1, x0:x1], (64, 24), interpolation=cv2.INTER_AREA)

    @staticmethod
    def _sig_similar(a, b, thresh: float = 6.0) -> bool:
        """两张下采样灰度签名的平均绝对差（0~255）是否小于阈值（判定页面已不动）。"""
        if a.shape != b.shape:
            return False
        return float(cv2.absdiff(a, b).mean()) < thresh

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


# ============================================================================
# 大师竞技场选人（2026-10-23 按现场录屏实现）
# ============================================================================
class MasterPick(CustomAction):
    """大师竞技场选人：先禁用，再选用。

    与 1v1 选人页的差异（录屏 1920x1080 → 1280 基准实测）：
      - 网格 = 6 列 × 3 行（第 3 行右侧 3 格为空，共 15 个角色），格子 99×99；
      - 同一页面分两阶段：先是「请先选择禁用的格斗家」（倒计时 20s），
        再是「温馨提示：请选择格斗家 (x/3)」（倒计时 30s）；
      - 禁用成功标志 = 格子右下角红色「禁用」印章（ban_stamp.png，红通道二值）；
      - 选用成功标志 = 格子内白色勾（master_check.png，白掩码二值）；
      - 顺序确认页无需任何操作（倒计时结束自动进战斗），只作为选人结束的保底判断。

    栏位含义与 1v1 完全一致：(栏, 行, 列) → roles/c{栏}r{行}k{列}.png。
    大师页角色头像与 1v1 用的是同一套美术资源，78×78 模板在 99×99 格内滑动匹配
    实测 0.96~0.99，所以「精确找角色」是走得通的（v1.2.0 因裁剪 resize 失真才
    误判成角色池不同）。真找不到时才走兜底：从第 1 格开始依次点。

    custom_action_param：
    {
        "ban_picks":  "1,1,1;1,1,2;1,1,3",   // 禁用：需要 2 个，可多填作备选，000=放空
        "pick_picks": "1,1,1;1,1,2;1,1,3;...",  // 选用：需要 3 个，可多填作备选
        "ban_count": 2, "pick_count": 3,     // 每阶段要凑够的个数
        "role_threshold": 0.86,   // 角色模板命中阈值（实测命中 0.96~0.99）
        "role_threshold_low": 0.72, "role_gap": 0.12,
        // 角标/勾/徽章会把分数压到 0.7~0.8（实测 2P 角标 0.776）；
        // 分数不够高时，只要比其余格子高出 role_gap 也认（同角色一页只出现一次）。
        "ban_threshold": 0.60,    // 禁用标判定
        "check_threshold": 0.55,  // 白勾判定
        "confirm_xy": [622, 638], // 「确定」按钮（与 1v1 同位置）
        "pick_page_timeout": 60,  // 等选人页出现的秒数
        "confirm_wait": 6         // 点确定前最多等按钮出现的秒数
    }

    选人策略（用户 2026-10-23 现场确认）：
      1. 按输入顺序逐个用 1v1 角色模板扫描，凑齐 need 个（禁用 2 / 选用 3）立即停；
      2. 输入栏位都试完仍凑不齐，才「从第 1 格开始依次点」兜底补齐；
      3. 多出来的输入位是备选，不会被强行点掉。
    """

    # 1280 基准网格（从 1920x1080 录屏帧缩放后边缘检测标定）
    # 布局固定为 6 列 × 2 行 + 第 3 行 3 格 = 15 个角色（第 3 行右侧 3 格是背景）
    COLS = (90, 202, 313, 478, 590, 701)
    ROWS = (19, 128, 237)
    CELL = 99
    ROLE_SIZE = 78           # 与 roles/*.png 同尺寸

    @classmethod
    def _cells(cls):
        """(列, 行) 遍历全部 15 个角色格（第 3 行只有左边 3 格有角色）。"""
        for ri in range(1, 4):
            n = 6 if ri < 3 else 3
            for ci in range(1, n + 1):
                yield ci, ri

    # ---------------- 主流程 ----------------
    def run(self, context: Context, argv: CustomAction.RunArg) -> bool:
        param = json.loads(argv.custom_action_param) if argv.custom_action_param else {}
        ctrl = context.tasker.controller
        print("[MasterPick] ===== 大师竞技场选人开始 =====", flush=True)

        if not self._wait_pick_page(ctrl, param):
            print("[MasterPick] [X] 等待选人页超时", flush=True)
            return False

        ok_ban = self._run_phase(ctrl, param, ban=True)
        ok_pick = self._run_phase(ctrl, param, ban=False)

        if ok_ban and ok_pick:
            print("[MasterPick] [OK] 禁用与选用均完成", flush=True)
            return True
        if self._left_pick_page(ctrl, param):
            print("[MasterPick] [OK] 已离开选人页（顺序确认/战斗），按完成放行", flush=True)
            return True
        print("[MasterPick] [X] 选人未完成，且仍在选人页", flush=True)
        return False

    def _run_phase(self, ctrl, param, ban: bool) -> bool:
        tag = "禁用" if ban else "选用"
        key = "ban_picks" if ban else "pick_picks"
        need = int(param.get("ban_count" if ban else "pick_count", 2 if ban else 3))
        refs = self._parse_refs(param.get(key))
        shown = ["000" if r is None else ",".join(str(v) for v in r) for r in refs]
        print(f"[MasterPick] --- {tag}阶段 需凑 {need} 个，输入 {shown} ---", flush=True)

        done = 0
        used = set()
        # 1) 按输入顺序找：命中就点，凑齐 need 立刻停（先输入的优先）
        for ref in refs:
            if done >= need:
                break
            if ref is None:
                print(f"[MasterPick] {tag}: 空栏位(000)，跳过", flush=True)
                continue
            pos = self._scan_role(ctrl, ref, param, used, ban)
            if pos is not None and self._click_verify(ctrl, pos, ban, param):
                used.add(pos)
                done += 1
                print(f"[MasterPick] {tag} {done}/{need} [OK] 命中栏位 "
                      f"{','.join(str(v) for v in ref)} -> 格 {pos}", flush=True)
            else:
                print(f"[MasterPick] {tag}: 栏位 {','.join(str(v) for v in ref)} "
                      f"未在本页找到/没点成功，继续下一个输入", flush=True)

        # 2) 输入都没凑齐 → 兜底：从第 1 格开始依次点，直到凑够 need
        if done < need:
            print(f"[MasterPick] {tag} 输入栏位只凑到 {done}/{need}，"
                  f"改走兜底：从第 1 格依次点（跳过已占格）", flush=True)
            while done < need:
                fb = self._fallback(ctrl, ban, param, used)
                if fb is None:
                    print(f"[MasterPick] {tag} [!] 兜底也点不出来，停在 {done}/{need}", flush=True)
                    break
                used.add(fb)
                done += 1
                print(f"[MasterPick] {tag} {done}/{need} [OK] 兜底 -> 格 {fb}", flush=True)

        self._click_confirm(ctrl, param, tag)
        print(f"[MasterPick] {tag}阶段结束：完成 {done}/{need}", flush=True)
        return done >= need

    # ---------------- 扫描 ----------------
    def _scan_role(self, ctrl, ref, param, used, ban):
        """用该栏位的 1v1 角色模板在 15 个格子里找最像的一格。

        1v1 的 roles/*.png 是 78×78 的整格头像，大师页格子是 99×99（含边框/星级），
        头像是在格子里「贴着某个偏移」放置的 —— 所以直接拿 78×78 模板在 99×99 格内
        滑动做 TM_CCOEFF_NORMED，让算法自己找对齐位置。不要再把格子裁剪后 resize 到
        78（尺寸/比例一失真，同一角色也只有 0.5~0.6，会误判成「角色池不同」）。
        实测：命中 0.985 / 0.980 / 0.989，未命中 ≤ 0.78，同角色不同 k ≤ 0.77。
        """
        col, row, k = ref
        if not (1 <= col <= 10 and 1 <= row <= 3 and 1 <= k <= 3):
            print(f"[MasterPick] 栏位仅支持 栏1~10 / 行1~3 / 列1~3，收到 {ref}", flush=True)
            return None
        tpl = self._load_rel(f"roles/c{col}r{row}k{k}")
        if tpl is None:
            print(f"[MasterPick] 缺少模板 roles/c{col}r{row}k{k}.png", flush=True)
            return None
        bgr = self._grab_bgr(ctrl)
        if bgr is None:
            print(f"[MasterPick] {ref}: 截图为空", flush=True)
            return None
        th = float(param.get("role_threshold", 0.86))
        th_low = float(param.get("role_threshold_low", 0.72))
        gap_min = float(param.get("role_gap", 0.12))
        tpls = self._scale_tpls(tpl, param)
        scores = {}
        for ci, ri in self._cells():
            if (ci, ri) in used:
                continue
            ry, rx = self.ROWS[ri - 1], self.COLS[ci - 1]
            cell = bgr[ry:ry + self.CELL, rx:rx + self.CELL]
            if cell.size == 0 or float(cell.mean()) < 25:
                continue
            if self._cell_taken(cell, ban, param):
                continue
            gray = cv2.cvtColor(cell, cv2.COLOR_BGR2GRAY)
            scores[(ci, ri)] = self._best_score(gray, tpls)
        if not scores:
            print(f"[MasterPick] 扫描 c{col}r{row}k{k}: 没有可点格子（全被禁用/已选）", flush=True)
            return None
        order = sorted(scores.items(), key=lambda kv: -kv[1])
        (best_cell, best), second = order[0], (order[1][1] if len(order) > 1 else 0.0)
        # 双判据：高分直接认；分数被角标/勾/徽章压低的（实测 2P 角标 0.776），
        # 只要它明显压过其余格子（gap ≥ 0.12）也认 —— 同一个角色一页只出现一次。
        ok = best >= th or (best >= th_low and (best - second) >= gap_min)
        print(f"[MasterPick] 扫描 c{col}r{row}k{k}: 最佳 {best:.3f} @ {best_cell}"
              f"（次佳格 {second:.3f}，差 {best - second:.3f}）→ "
              f"{'命中' if ok else '未命中'}", flush=True)
        return best_cell if ok else None

    @classmethod
    def _scale_tpls(cls, tpl, param):
        """把 78×78 模板按 ±6% 缩放几档，容忍模拟器偶发的轻微缩放差异。"""
        base = cv2.resize(tpl, (cls.ROLE_SIZE, cls.ROLE_SIZE), interpolation=cv2.INTER_AREA)
        out = [base]
        for s in param.get("role_scales", (0.94, 1.06)):
            n = int(round(cls.ROLE_SIZE * float(s)))
            n = max(48, min(cls.CELL - 4, n))
            if n != cls.ROLE_SIZE:
                out.append(cv2.resize(base, (n, n), interpolation=cv2.INTER_AREA))
        return out

    @staticmethod
    def _best_score(gray_cell, tpls) -> float:
        best = 0.0
        h, w = gray_cell.shape[:2]
        for t in tpls:
            if t.shape[0] > h or t.shape[1] > w:
                continue
            r = cv2.matchTemplate(gray_cell, t, cv2.TM_CCOEFF_NORMED)
            _, mx, _, _ = cv2.minMaxLoc(r)
            if mx > best:
                best = float(mx)
        return best


    # ---------------- 判定 ----------------
    @staticmethod
    def _red_mask(bgr):
        b, g, r = cv2.split(bgr.astype(np.int16))
        red = np.clip(r - np.maximum(g, b), 0, 255).astype(np.uint8)
        _, bw = cv2.threshold(red, 70, 255, cv2.THRESH_BINARY)
        return bw

    @staticmethod
    def _white_mask(bgr):
        b, g, r = cv2.split(bgr.astype(np.int16))
        mx = np.maximum(np.maximum(b, g), r)
        mn = np.minimum(np.minimum(b, g), r)
        return (((mx > 195) & ((mx - mn) < 45)) * 255).astype(np.uint8)

    def _ban_score(self, cell_bgr) -> float:
        tpl = self._load_rel("ban_stamp")
        if tpl is None:
            return 0.0
        m = self._red_mask(cell_bgr)
        r = cv2.matchTemplate(m, tpl, cv2.TM_CCOEFF_NORMED)
        _, mx, _, _ = cv2.minMaxLoc(r)
        return float(mx)

    def _check_score(self, cell_bgr) -> float:
        tpl = self._load_rel("master_check")
        if tpl is None:
            return 0.0
        m = self._white_mask(cell_bgr)
        r = cv2.matchTemplate(m, tpl, cv2.TM_CCOEFF_NORMED)
        _, mx, _, _ = cv2.minMaxLoc(r)
        return float(mx)

    def _cell_taken(self, cell_bgr, ban_phase: bool, param) -> bool:
        if self._ban_score(cell_bgr) >= float(param.get("ban_threshold", 0.60)):
            return True
        if not ban_phase and self._check_score(cell_bgr) >= float(param.get("check_threshold", 0.55)):
            return True
        return False

    def _cell_state(self, ctrl, ci, ri, param):
        """返回 (bgr_cell, taken, ban_hit, check_hit)；截图为空返回 (None, ...)."""
        bgr = self._grab_bgr(ctrl)
        if bgr is None:
            return None, True, False, False
        cell = bgr[self.ROWS[ri - 1]:self.ROWS[ri - 1] + self.CELL,
                   self.COLS[ci - 1]:self.COLS[ci - 1] + self.CELL]
        if cell.size == 0 or float(cell.mean()) < 25:
            return cell, True, False, False
        b = self._ban_score(cell) >= float(param.get("ban_threshold", 0.60))
        c = self._check_score(cell) >= float(param.get("check_threshold", 0.55))
        return cell, (b or c), b, c

    # ---------------- 点击 ----------------
    def _click_cell(self, ctrl, ci, ri):
        cx = self.COLS[ci - 1] + self.CELL // 2
        cy = self.ROWS[ri - 1] + self.CELL // 2
        ctrl.post_click(cx, cy).wait()
        print(f"[MasterPick] 点击格 ({ci},{ri}) @ ({cx},{cy})", flush=True)

    def _click_verify(self, ctrl, pos, ban: bool, param) -> bool:
        ci, ri = pos
        th = float(param.get("ban_threshold", 0.60) if ban else param.get("check_threshold", 0.55))
        for attempt in range(2):
            self._click_cell(ctrl, ci, ri)
            time.sleep(float(param.get("click_settle", 0.45)))
            _, _, b_hit, c_hit = self._cell_state(ctrl, ci, ri, param)
            hit = b_hit if ban else c_hit
            if hit:
                return True
            print(f"[MasterPick] 格 ({ci},{ri}) 第 {attempt + 1} 次点击未确认"
                  f"（ban={b_hit} chk={c_hit} 阈值 {th}）", flush=True)
        return False

    def _fallback(self, ctrl, ban: bool, param, used):
        """兜底：从第 1 格开始依次点，直到出现禁用标/白勾。"""
        limit = int(param.get("fallback_max", 15))
        tried = 0
        for ci, ri in self._cells():
            if (ci, ri) in used:
                continue
            cell, taken, _, _ = self._cell_state(ctrl, ci, ri, param)
            if cell is None or taken:
                continue
            if tried >= limit:
                print(f"[MasterPick] 兜底达到上限 {limit} 格", flush=True)
                return None
            tried += 1
            if self._click_verify(ctrl, (ci, ri), ban, param):
                return (ci, ri)
        return None

    # ---------------- 阶段收尾 ----------------
    def _click_confirm(self, ctrl, param, tag=""):
        """检测「确定」按钮并点击；没出现就等一会儿（选用阶段选满后才出现）。"""
        tpl = self._load_rel("pick_confirm")
        if tpl is None:
            return False
        cx, cy = param.get("confirm_xy", [622, 638])
        cx, cy = int(cx), int(cy)
        deadline = time.time() + float(param.get("confirm_wait", 6.0))
        while time.time() < deadline:
            bgr = self._grab_bgr(ctrl)
            if bgr is not None:
                gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
                x0, y0 = max(0, cx - 130), max(0, cy - 70)
                patch = gray[y0:cy + 70, x0:cx + 130]
                if patch.shape[0] >= tpl.shape[0] and patch.shape[1] >= tpl.shape[1]:
                    r = cv2.matchTemplate(patch, tpl, cv2.TM_CCOEFF_NORMED)
                    _, mx, _, loc = cv2.minMaxLoc(r)
                    if float(mx) >= 0.8:
                        ctrl.post_click(x0 + loc[0] + tpl.shape[1] // 2,
                                        y0 + loc[1] + tpl.shape[0] // 2).wait()
                        print(f"[MasterPick] {tag} 点「确定」（score {mx:.3f}）", flush=True)
                        time.sleep(float(param.get("confirm_settle", 1.2)))
                        return True
            time.sleep(0.4)
        print(f"[MasterPick] {tag} 未发现「确定」按钮（可能阶段已自动切换）", flush=True)
        return False

    # ---------------- 页面判定 ----------------
    def _wait_pick_page(self, ctrl, param) -> bool:
        tpl = self._load_rel("pick_wait")
        if tpl is None:
            return True
        th = float(param.get("pick_page_threshold", 0.8))
        deadline = time.time() + float(param.get("pick_page_timeout", 60))
        while time.time() < deadline:
            bgr = self._grab_bgr(ctrl)
            if bgr is not None:
                gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
                if gray.shape[0] >= tpl.shape[0] and gray.shape[1] >= tpl.shape[1]:
                    r = cv2.matchTemplate(gray, tpl, cv2.TM_CCOEFF_NORMED)
                    _, mx, _, _ = cv2.minMaxLoc(r)
                    if float(mx) >= th:
                        print(f"[MasterPick] 选人页已出现（1P 标签 {mx:.3f}）", flush=True)
                        return True
            time.sleep(1.0)
        return False

    def _left_pick_page(self, ctrl, param) -> bool:
        bgr = self._grab_bgr(ctrl)
        if bgr is None:
            return False
        gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
        scores = {}
        for name, th in (("pick_wait", 0.8), ("pick_confirm", 0.85)):
            tpl = self._load_rel(name)
            if tpl is None:
                continue
            if gray.shape[0] < tpl.shape[0] or gray.shape[1] < tpl.shape[1]:
                continue
            r = cv2.matchTemplate(gray, tpl, cv2.TM_CCOEFF_NORMED)
            scores[name] = float(r.max()) >= th
        if not scores:
            return False
        return not any(scores.values())

    # ---------------- 工具 ----------------
    def _grab_bgr(self, ctrl):
        ctrl.post_screencap().wait()
        img = ctrl.cached_image
        if img is None:
            return None
        if img.ndim == 2:
            img = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
        sc = img.shape[1] / 1280.0
        if abs(sc - 1.0) > 0.01:
            img = cv2.resize(img, (1280, int(img.shape[0] / sc)), interpolation=cv2.INTER_AREA)
        return img

    @staticmethod
    def _load_rel(rel: str):
        return ArenaPick._load_rel(rel)

    @staticmethod
    def _parse_refs(raw, need: int | None = None):
        """解析 "1,1,1;1,1,2;1,1,3" 或 [[1,1,1],...] → 列表，000 → None。

        不再截断到 need：输入位比 need 多出来的都是「备选」，由调用方按顺序尝试，
        凑齐 need 个就停（v1.2.0 之前截断成 need，等于把备选直接丢掉）。
        """
        items = []
        if isinstance(raw, str):
            for part in raw.split(";"):
                part = part.strip()
                if not part:
                    items.append(None)
                    continue
                if part == "000":
                    items.append(None)
                    continue
                nums = part.split(",")
                try:
                    items.append([int(x) for x in nums] if len(nums) == 3 else None)
                except ValueError:
                    items.append(None)
        elif isinstance(raw, list):
            for p in raw:
                if isinstance(p, (list, tuple)) and len(p) == 3:
                    vals = [int(x) for x in p]
                    items.append(None if all(v == 0 for v in vals) else vals)
                elif isinstance(p, str):
                    if p.strip() == "000":
                        items.append(None)
                    else:
                        nums = p.split(",")
                        try:
                            items.append([int(x) for x in nums] if len(nums) == 3 else None)
                        except ValueError:
                            items.append(None)
                else:
                    items.append(None)
        else:
            items = []
        if need is not None:
            while len(items) < need:
                items.append(None)
            items = items[:need]
        return items
