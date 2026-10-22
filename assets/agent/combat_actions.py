"""战斗中的自定义动作（Custom Action）。

坐标以 1280x720 为基准（MFATools 实测：模拟器 2560x1440 / 640DPI，
按 interface.json 的 display_short_side: 720 自动缩放，坐标无需再换算）。
MaaFramework 在识别/点击时会按 设备宽/1280 自动缩放，16:9 设备通用；
非 16:9 设备请自行校准。

!! 换设备 / 换模拟器 / 换角色 UI 后，先用 `python main.py --shot shot.png` 截图，
!! 重新核对本文件顶部坐标和 pipeline 里的 roi。
"""

from __future__ import annotations

import json
import math
import os
import time

import cv2

from maa.context import Context
from maa.custom_action import CustomAction

from arena_actions import _ARENA_DIR  # .../resource/image/arena（复用路径定位）

# ================== 战斗 UI 固定坐标（1280x720 基准，MFATools 实测 2026-09） ==================
JOYSTICK = (167, 531)        # 摇杆中心
JOYSTICK_RADIUS = 78         # 摇杆拖动半径；按住移动时用 ~0.7 倍偏移，避免越出判定区

BUTTONS = {
    "normal": (1137, 632),   # 普攻（右下）        roi [1122,617,30,30]
    "skill1": (981, 643),    # 一技能（最下）      roi [964,626,35,34]
    "skill2": (1045, 511),   # 二技能              roi [1030,495,30,33]
    "skill3": (1168, 447),   # 三技能              roi [1155,433,27,29]
    "ult":    (1212, 325),   # 大招                roi [1198,310,28,31]
    "burst":  (1057, 319),   # 爆气                roi [1041,301,32,36]
    "secret": (1226, 229),   # 秘笈（最上）        roi [1213,216,27,28]
    "roll":   (1232, 556),   # 翻滚                roi [1220,544,25,25]
    "substitute": (1232, 556),  # 替身（= 翻滚按钮的受击发光态，同一点位）
}

CONTACT_MOVE = 0   # 摇杆专用触点（手指0）
CONTACT_ATK = 1    # 普攻/技能专用触点（手指1）


def _check(st, what: str) -> None:
    """控制器操作失败时打印到 agent 终端（正常时静默）。"""
    if getattr(st, "succeeded", None) is False:
        print(f"[combat] 控制器操作失败: {what}", flush=True)


def _shot(ctrl) -> bool:
    """截图并检查状态。截图失败时 cached_image 是旧帧，识别会全错，必须打出来。"""
    st = ctrl.post_screencap().wait()
    if getattr(st, "succeeded", None) is False:
        print("[combat] 截图失败（cached_image 是旧帧！）", flush=True)
        return False
    return True


def _tap(ctrl, key: str, hold_ms: int = 60, contact: int = CONTACT_ATK) -> None:
    """在指定按钮上做一次完整的 按下 -> 停顿 -> 抬起。

    !! 严禁在别处对触点发多余的 touch_up：maatouch 对“未按下触点的
    !! touch_up”零容忍，daemon 会断连，之后所有注入静默失效（识别不受影响）。
    """
    x, y = BUTTONS[key]
    _check(ctrl.post_touch_down(x, y, contact=contact).wait(), f"touch_down {key}")
    try:
        time.sleep(hold_ms / 1000)
    finally:
        _check(ctrl.post_touch_up(contact=contact).wait(), f"touch_up {key}")


def _joystick_point(direction) -> tuple[int, int]:
    """摇杆中心 + 方向向量 * 0.7R，得到按住时的落点。"""
    dx, dy = direction
    norm = math.hypot(dx, dy) or 1.0
    return (
        int(JOYSTICK[0] + dx / norm * JOYSTICK_RADIUS * 0.7),
        int(JOYSTICK[1] + dy / norm * JOYSTICK_RADIUS * 0.7),
    )


class CombatFighter(CustomAction):
    """战斗主循环。

    每轮：截图 → 结算？ → 替身识别命中则点 → 技能/大招/秘笈轮流盲点 → 点完秘笈后连点普攻。
    技能不再识别（识别太慢会把普攻饿死）；只有替身走识别。

    custom_action_param（见 combat.json 的 Fight_Once，全部可省略）：
    {
        "duration": 120,
        "interval": 0,                 // 一轮结束后的额外等待（秒）
        "skills": ["Fight_Substitute"], // 仍走识别的节点（目前只有替身）
        "skill_cycle": ["skill1", "skill2", "skill3", "ult", "secret"],
                                       // 每轮按此顺序快速盲点（BUTTONS 键名）
        "skill_tap": {"hold_ms": 30, "interval_ms": 30},
        "normal_tap": {"hold_ms": 30, "interval_ms": 30, "times": 3},
                                       // 点完秘笈后，按此速度连点普攻
        "keep_moving": false,
        "move_dir": [-1, 0],
        // —— 误点退出（2026-10-22 方案） ——
        "result_interval": 4.0,        // 误点/结算识别节流（秒）
        "result_nodes": ["Fight_Win", "Fight_Lose"],
                                       // 胜负结算文字节点（失败结算盲点点不掉，必须兜底）
        "wrong_threshold": 0.85,       // 误点界面标题模板阈值（实测交叉最高 0.82）
        "main_threshold": 0.8,         // 竞技场主页面（arena_title）阈值
        "back_xy": [1244, 28],         // 右上角红色返回按钮（至尊赛/排行榜/商城共用）
        "user_close_xy": [843, 109],   // 玩家详情弹窗 X 关闭
        "wrong_settle_ms": 1200,       // 每次退出点击后的等待
        "wrong_max_layers": 12         // 嵌套误点最多连退层数
    }

    退出设计（2026-10-22 定稿）：盲点速度快，可能误点进竞技场的子界面且多层嵌套。
    每 result_interval 秒检查一次当前画面：
      ① 玩家详情弹窗 → 点 X 关闭（回排行榜）
      ② 排行榜 / 至尊赛 / 商城 → 点右上返回
      ①②循环处理嵌套（最多 wrong_max_layers 层）；
      ③ 全部未命中且识别到竞技场主页面（arena_title）→ 收手 break，交还流水线进下一轮；
      ④ 无误点且非主页面 → 检查 win/lose 结算文字，命中 break 交还 Fight_Win/Fight_Lose
        → Arena_SettleOk 点确认（失败结算画面盲点点不掉，v1.1.3 实测卡死 900s 的教训）；
      ⑤ 都未命中 → 还在战斗，继续盲点。
    """

    # 误点界面定义（顺序即检测顺序：玩家详情必须最先——弹窗开着时背景标题仍可见）
    _WRONG_DEFS = [
        # (名称, 模板相对 _ARENA_DIR, roi(x,y,w,h), 退出动作)
        ("玩家详情", "wrong/wrong_user",   (350, 50, 620, 160), "close"),
        ("排行榜",   "wrong/wrong_rank",   (0, 0, 340, 110),   "back"),
        ("至尊赛",   "wrong/wrong_zhizun", (0, 0, 340, 110),   "back"),
        ("商城",     "wrong/wrong_shop",   (0, 0, 340, 110),   "back"),
    ]
    _TPL_CACHE: dict[str, "cv2.Mat"] = {}

    def run(self, context: Context, argv: CustomAction.RunArg) -> bool:
        param = json.loads(argv.custom_action_param) if argv.custom_action_param else {}

        duration = float(param.get("duration", 120))
        interval = float(param.get("interval", 0))
        recog_nodes = list(param.get("skills", ["Fight_Substitute"]))
        skill_cycle = list(param.get("skill_cycle",
                                     ["skill1", "skill2", "skill3", "ult", "secret"]))
        st = param.get("skill_tap", {})
        skill_hold = int(st.get("hold_ms", 30))
        skill_gap = float(st.get("interval_ms", 30)) / 1000
        tap_cfg = param.get("normal_tap", {})
        tap_hold = float(tap_cfg.get("hold_ms", 30)) / 1000
        tap_gap = float(tap_cfg.get("interval_ms", 30)) / 1000
        tap_times = max(1, int(tap_cfg.get("times", 3)))
        # 胜负结算检测（2026-10-22 晚恢复）：失败结算画面盲点点不掉（确认键不在盲点
        # 坐标上），必须有 win/lose 兜底 break 交给 Fight_Win/Fight_Lose → Arena_SettleOk。
        # 误点界面与结算画面互斥，先查误点再查结算，互不干扰。
        result_nodes = list(param.get("result_nodes", ["Fight_Win", "Fight_Lose"]))
        keep_moving = bool(param.get("keep_moving", False))
        move_dir = param.get("move_dir", [-1, 0])

        ctrl = context.tasker.controller
        print(f"[CombatFighter] 开始（{duration:.0f}s 上限）", flush=True)
        deadline = time.time() + duration
        # 结算识别降频：全屏模板识别在慢环境下单次可达 1~2.4s，
        # 每轮都查会把出招拖成涓流。每 result_interval 秒查一次足够（60s 级别的战斗）。
        result_interval = float(param.get("result_interval", 4.0))
        next_result_check = 0.0

        # 开打自检：确认画面真的是战斗界面（截图若失败会是旧帧，这里能暴露）
        _shot(ctrl)
        img0 = ctrl.cached_image
        if img0 is not None:
            try:
                det = context.run_recognition("Arena_WaitBattle", img0)
                print(f"[CombatFighter] 开打画面=战斗界面: {bool(det and det.hit)}", flush=True)
            except Exception as e:
                print(f"[CombatFighter] 开打自检异常: {e}", flush=True)
        t0 = time.time()
        last_report = 0.0
        joy_down = False

        if keep_moving:
            self._hold_joystick(ctrl, move_dir)
            joy_down = True  # 摇杆按住中，结束时必须抬起（这是唯一合法的收尾 touch_up）

        try:
            while time.time() < deadline:
                if context.tasker.stopping:
                    break

                _shot(ctrl)
                img = ctrl.cached_image

                # 每 10s 打一个画面指纹：数字一直完全不变 => 截图是死的旧帧
                now = time.time()
                if img is not None and getattr(img, "size", 0) and now - last_report >= 10:
                    last_report = now
                    try:
                        fp = float(img.mean())
                    except Exception:
                        fp = -1.0
                    print(f"[CombatFighter] t={now - t0:.0f}s 画面均值={fp:.1f}", flush=True)

                # 误点界面识别（降频）：到达检查时刻才跑。
                # ① 命中误点界面 => 循环执行退出（嵌套逐层退），直到回主界面才收手 break；
                # ② 无误点且非主界面 => 检查胜负结算文字（失败结算盲点点不掉，必须兜底）；
                # ③ 都未命中 => 还在战斗，继续盲点。
                if img is not None and getattr(img, "size", 0) and now >= next_result_check:
                    next_result_check = now + result_interval
                    gray = self._to_gray_1280(img)
                    if self._escape_misnav(ctrl, gray, param):
                        break
                    if self._hit_any(context, img, result_nodes):
                        print("[CombatFighter] 检测到胜负结算，停止出招", flush=True)
                        break

                # 替身：受击发光才点，其余技能不识别
                if img is not None and getattr(img, "size", 0) and recog_nodes:
                    for node in recog_nodes:
                        if self._hit_any(context, img, [node]):
                            key = self._key_of(node)
                            if key:
                                _tap(ctrl, key, hold_ms=skill_hold)
                            break

                for key in skill_cycle:
                    if context.tasker.stopping:
                        break
                    if key in BUTTONS:
                        _tap(ctrl, key, hold_ms=skill_hold)
                        time.sleep(skill_gap)

                for _ in range(tap_times):
                    if context.tasker.stopping:
                        break
                    _tap(ctrl, "normal", hold_ms=int(tap_hold * 1000))
                    time.sleep(tap_gap)

                if interval > 0:
                    time.sleep(interval)
        finally:
            # 只抬“确实按住中”的摇杆触点；其余触点一律由 _tap 内部配对抬起，
            # 多余的 touch_up 会杀死 maatouch（切记！）
            if joy_down:
                _check(ctrl.post_touch_up(contact=CONTACT_MOVE).wait(), "touch_up joystick")
            print("[CombatFighter] 结束", flush=True)

        return True

    # ================== 误点界面检测与退出（2026-10-22） ==================

    @staticmethod
    def _to_gray_1280(img):
        """cached_image -> 宽 1280 的灰度图（坐标/模板统一 1280 基准）。"""
        if img.ndim == 3:
            g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        else:
            g = img
        if g.shape[1] != 1280:
            sc = g.shape[1] / 1280.0
            g = cv2.resize(g, (1280, int(g.shape[0] / sc)), interpolation=cv2.INTER_AREA)
        return g

    @classmethod
    def _load_tpl(cls, rel: str):
        """加载 arena 目录下灰度模板（带缓存），失败返回 None。"""
        if rel not in cls._TPL_CACHE:
            path = os.path.join(_ARENA_DIR, *rel.split("/")) + ".png"
            tpl = cv2.imread(path, cv2.IMREAD_GRAYSCALE)
            if tpl is None:
                print(f"[CombatFighter] 缺少模板 {rel}.png", flush=True)
                cls._TPL_CACHE[rel] = None
            else:
                cls._TPL_CACHE[rel] = tpl
        return cls._TPL_CACHE[rel]

    @staticmethod
    def _match_score(gray, tpl, roi) -> float:
        """在 gray 的 roi(x,y,w,h) 内匹配模板，返回最高分（ROI 过小返回 -1）。"""
        x, y, w, h = roi
        H, W = gray.shape[:2]
        patch = gray[max(0, y):min(H, y + h), max(0, x):min(W, x + w)]
        th, tw = tpl.shape[:2]
        if patch.shape[0] < th or patch.shape[1] < tw:
            return -1.0
        return float(cv2.matchTemplate(patch, tpl, cv2.TM_CCOEFF_NORMED).max())

    @classmethod
    def _detect_wrong(cls, gray, threshold: float):
        """检测当前是否处于误点界面，返回 (名称, 动作) 或 None。

        顺序固定：玩家详情最先（弹窗开着时背景的排行榜标题仍然可见，
        若先查排行榜会在弹窗帧上以背景分数命中）。
        """
        for name, rel, roi, act in cls._WRONG_DEFS:
            tpl = cls._load_tpl(rel)
            if tpl is None:
                continue
            score = cls._match_score(gray, tpl, roi)
            if score >= threshold:
                print(f"[CombatFighter] 误点识别「{name}」score={score:.3f}", flush=True)
                return name, act
        return None

    @classmethod
    def _main_page_hit(cls, gray, threshold: float) -> bool:
        """竞技场主页面判定（arena_title 横幅，全屏匹配）。"""
        tpl = cls._load_tpl("arena_title")
        if tpl is None:
            return False
        return cls._match_score(gray, tpl, (0, 0, gray.shape[1], gray.shape[0])) >= threshold

    @classmethod
    def _escape_misnav(cls, ctrl, gray, param) -> bool:
        """处理误点嵌套：逐层退出直到回到竞技场主界面。

        返回 True = 已回主界面（调用方 break 进下一轮）；
        返回 False = 不在误点界面且不是主界面（还在战斗，继续盲点）。
        """
        wrong_th = float(param.get("wrong_threshold", 0.85))
        main_th = float(param.get("main_threshold", 0.8))
        settle = float(param.get("wrong_settle_ms", 1200)) / 1000
        max_layers = max(1, int(param.get("wrong_max_layers", 12)))
        back_xy = param.get("back_xy", [1244, 28])
        close_xy = param.get("user_close_xy", [843, 109])

        for layer in range(max_layers):
            hit = cls._detect_wrong(gray, wrong_th)
            if hit is None:
                if cls._main_page_hit(gray, main_th):
                    if layer > 0:
                        print("[CombatFighter] 误点界面已全部退出", flush=True)
                    print("[CombatFighter] 检测到竞技场主界面（已离开战斗），停止出招进下一轮", flush=True)
                    return True
                return False
            name, act = hit
            x, y = close_xy if act == "close" else back_xy
            verb = "点关闭(X)" if act == "close" else "点返回"
            print(f"[CombatFighter] 误点进入「{name}」（第 {layer + 1} 层），{verb} ({x},{y})", flush=True)
            ctrl.post_click(int(x), int(y))
            time.sleep(settle)
            if not _shot(ctrl):
                return False
            img = ctrl.cached_image
            if img is None:
                return False
            gray = cls._to_gray_1280(img)
        print(f"[CombatFighter] 连退 {max_layers} 层仍未回主界面，放弃（防死循环），继续观察", flush=True)
        return False

    @staticmethod
    def _hit_any(context: Context, img, nodes: list[str]) -> bool:
        """对给定节点逐个跑识别（只识别不动作），任一命中返回 True。"""
        for node in nodes:
            try:
                det = context.run_recognition(node, img)
            except Exception:
                return False
            if det and det.hit:
                return True
        return False

    @staticmethod
    def _key_of(node: str) -> str | None:
        """Fight_Skill1 -> skill1, Fight_Ult -> ult, Fight_Burst -> burst ..."""
        name = node.rsplit("_", 1)[-1].lower()
        return name if name in BUTTONS else None

    @staticmethod
    def _hold_joystick(ctrl, direction) -> None:
        """contact 0 按住摇杆并偏向 direction，供整场跑位用。"""
        x, y = _joystick_point(direction)
        ctrl.post_touch_down(JOYSTICK[0], JOYSTICK[1], contact=CONTACT_MOVE).wait()
        ctrl.post_touch_move(x, y, contact=CONTACT_MOVE).wait()


class CombatNormalAttack(CustomAction):
    """普攻连打（方案 B 的 Fight_NormalAttack 使用）。

    参数：{"times": 1, "hold_ms": 60, "interval_ms": 80}
    """

    def run(self, context: Context, argv: CustomAction.RunArg) -> bool:
        param = json.loads(argv.custom_action_param) if argv.custom_action_param else {}
        times = max(1, int(param.get("times", 1)))
        hold_ms = int(param.get("hold_ms", 60))
        gap_ms = int(param.get("interval_ms", 80))

        for _ in range(times):
            if context.tasker.stopping:
                break
            _tap(context.tasker.controller, "normal", hold_ms=hold_ms)
            time.sleep(gap_ms / 1000)
        return True


class CombatMove(CustomAction):
    """摇杆移动（方案 B 的 Fight_Move 使用）。

    参数：{"dir": [-1, 0], "duration_ms": 400}
    按下摇杆 -> 滑向偏移点 -> 保持 duration_ms -> 抬起。
    """

    def run(self, context: Context, argv: CustomAction.RunArg) -> bool:
        param = json.loads(argv.custom_action_param) if argv.custom_action_param else {}
        direction = param.get("dir", [-1, 0])
        hold_s = float(param.get("duration_ms", 400)) / 1000

        ctrl = context.tasker.controller
        mx, my = _joystick_point(direction)
        ctrl.post_touch_down(JOYSTICK[0], JOYSTICK[1], contact=CONTACT_MOVE).wait()
        ctrl.post_touch_move(mx, my, contact=CONTACT_MOVE).wait()
        time.sleep(hold_s)
        ctrl.post_touch_up(contact=CONTACT_MOVE).wait()
        return True


class CombatPressKey(CustomAction):
    """通用单发按键（蓄力/长按技能可用）。

    参数：{"key": "ult", "hold_ms": 800}   // hold_ms 大 = 蓄力
    """

    def run(self, context: Context, argv: CustomAction.RunArg) -> bool:
        param = json.loads(argv.custom_action_param) if argv.custom_action_param else {}
        key = param.get("key")
        if key not in BUTTONS:
            raise ValueError(f"unknown key: {key}, available: {list(BUTTONS)}")
        _tap(
            context.tasker.controller,
            key,
            hold_ms=int(param.get("hold_ms", 80)),
        )
        return True
