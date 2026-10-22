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
import time

from maa.context import Context
from maa.custom_action import CustomAction

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
        "result_nodes": ["Fight_Win", "Fight_Lose"],
        "normal_tap": {"hold_ms": 30, "interval_ms": 30, "times": 3},
                                       // 点完秘笈后，按此速度连点普攻
        "keep_moving": false,
        "move_dir": [-1, 0]
    }
    """

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
        result_nodes = list(param.get("result_nodes", ["Fight_Win", "Fight_Lose"]))
        tap_cfg = param.get("normal_tap", {})
        tap_hold = float(tap_cfg.get("hold_ms", 30)) / 1000
        tap_gap = float(tap_cfg.get("interval_ms", 30)) / 1000
        tap_times = max(1, int(tap_cfg.get("times", 3)))
        keep_moving = bool(param.get("keep_moving", False))
        move_dir = param.get("move_dir", [-1, 0])

        ctrl = context.tasker.controller
        print(f"[CombatFighter] 开始（{duration:.0f}s 上限）", flush=True)
        deadline = time.time() + duration

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
                if img is not None and now - last_report >= 10:
                    last_report = now
                    try:
                        fp = float(img.mean())
                    except Exception:
                        fp = -1.0
                    print(f"[CombatFighter] t={now - t0:.0f}s 画面均值={fp:.1f}", flush=True)

                if img is not None and self._hit_any(context, img, result_nodes):
                    break

                # 替身：受击发光才点，其余技能不识别
                if img is not None and recog_nodes:
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
