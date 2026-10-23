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

from arena_actions import _ARENA_DIR, imread_cn  # .../image/arena（复用路径定位 + 中文路径安全读图）

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
        "result_nodes": ["Fight_Lose"],
                                       // 只查失败结算文字（胜利结算盲点自点掉，不查 win）
        "wrong_threshold": 0.85,       // 误点界面标题模板阈值（实测交叉最高 0.82）
        "main_threshold": 0.8,         // 竞技场主页面（arena_title）阈值
        "back_xy": [1244, 28],         // 右上角红色返回按钮（至尊赛/排行榜/商城共用）
        "user_close_xy": [843, 109],   // 玩家详情弹窗 X 关闭
        "wrong_settle_ms": 1200,       // 每次退出点击后的等待
        "wrong_max_layers": 12,        // 嵌套误点最多连退层数
        // —— 未知界面兜底（2026-10-23 v1.2.2，装备页等未收录界面） ——
        "stall_checks": 5,             // 连续几次检查画面纹丝不动才算卡死（5×4s=20s）
        "stall_diff": 1.5,             // 32x18 签名平均绝对差阈值（装备页实测 0~0.5）
        "stall_max": 4,                // 最多点几次返回，用满后不再兜底
        "settle_threshold": 0.8        // 结算元素阈值（命中则不点返回）
    }

    退出设计（2026-10-22 定稿）：盲点速度快，可能误点进竞技场的子界面且多层嵌套。
    每 result_interval 秒检查一次当前画面：
      ① 玩家详情弹窗 → 点 X 关闭（回排行榜）
      ② 排行榜 / 至尊赛 / 商城 → 点右上返回
      ①②循环处理嵌套（最多 wrong_max_layers 层）；
      ③ 全部未命中且识别到竞技场主页面（arena_title）→ 收手 break，交还流水线进下一轮；
      ④ 无误点且非主页面 → 检查失败结算文字（只查 lose），命中 break 交还 Fight_Lose
        → Arena_SettleOk 点确认（失败结算画面盲点点不掉，v1.1.3 实测卡死 900s 的教训；
        胜利结算盲点自己会点掉回主界面，不查 win）；
      ⑤ 画面连续 stall_checks 次几乎不动、又不是上面任何一种（如装备页）→ 未知界面
        兜底：点右上红色返回箭头自救（v1.2.2，最多 stall_max 次）；
      ⑥ 都未命中 → 还在战斗，继续盲点。
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
        # 胜负结算检测（2026-10-22 定稿：只查失败）：失败结算画面盲点点不掉（确认键
        # 不在盲点坐标上），必须检测兜底 break 交给 Fight_Lose → Arena_SettleOk；
        # 胜利结算盲点自己会点掉回主界面（由主界面判定收口），不查 win——查询窗口期
        # 盲点误点结算按钮反而是历史问题源头。
        result_nodes = list(param.get("result_nodes", ["Fight_Lose"]))
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
        # 未知界面兜底状态（2026-10-23 v1.2.2）：装备页等未收录界面识别不到时，
        # 靠「画面长时间纹丝不动」判定卡死，点右上返回自救。
        stall = {"sig": None, "cnt": 0, "fired": 0}

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
                    # ④ 未知界面兜底：既不是已知误点界面/主界面/结算，画面又长期不动
                    #    （如装备页）→ 点右上返回自救，避免盲点空转 900s。
                    if self._unknown_escape(ctrl, img, gray, param, stall):
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
            tpl = imread_cn(path, cv2.IMREAD_GRAYSCALE)
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
        """竞技场主界面判定：1v1 竞技区主页（arena_title）或大师竞技页（master_title）。

        大师竞技场打完一局会回到「大师竞技」页面（不是 1v1 的竞技区主页），
        两个页面都算「已离开战斗」，任一命中即收手。
        """
        roi = (0, 0, gray.shape[1], gray.shape[0])
        for rel in ("arena_title", "master_title"):
            tpl = cls._load_tpl(rel)
            if tpl is None:
                continue
            if cls._match_score(gray, tpl, roi) >= threshold:
                return True
        return False

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
                    # 二次确认：break 与 pipeline 接管之间有 3s+ 空窗，且盲点 burst 的
                    # 残留点击可能此刻才被宿主执行、落在刚出现的主界面上（v1.1.6 实测
                    # 点进装备页 → pipeline 识别不到主界面 → NextList.Failed 死循环卡死）。
                    # 等 2s 让残留点击执行完、转场动画结束，再复核一次。
                    if layer > 0:
                        print("[CombatFighter] 误点界面已全部退出", flush=True)
                    print("[CombatFighter] 检测到竞技场主界面，等待 2s 复核（防残留点击/转场误判）", flush=True)
                    time.sleep(2.0)
                    if not _shot(ctrl):
                        return False
                    img2 = ctrl.cached_image
                    if img2 is None:
                        return False
                    if cls._main_page_hit(cls._to_gray_1280(img2), main_th):
                        print("[CombatFighter] 复核通过（已离开战斗），停止出招进下一轮", flush=True)
                        return True
                    # 复核失败：画面被残留点击切走（如装备页），继续循环当误点处理
                    print("[CombatFighter] 复核失败（画面已被切走），继续处理", flush=True)
                    gray = cls._to_gray_1280(img2)
                    continue
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

    # ================== 未知界面兜底（2026-10-23 v1.2.2） ==================
    #
    # 来历：v1.2.1 现场（log-20261023 15:13:47~15:15:58）战斗结束后残留盲点把画面
    # 点到「装备页」，而 _WRONG_DEFS 只收录 玩家详情/排行榜/至尊赛/商城 四种，
    # _main_page_hit 也不命中 → _escape_misnav 返回 False → 恢复盲点 → 空转 2 分钟。
    # 现在补一层通用兜底：连续 stall_checks 次检查画面签名几乎不变（装备页是静态页，
    # 实测均值 121.8~122.7 波动 ±0.5；战斗画面每 10s 均值动辄差 10+），且没有结算
    # 文字 → 认为卡在未收录界面，点右上红色返回箭头自救。
    @classmethod
    def _unknown_escape(cls, ctrl, img, gray, param, stall) -> bool:
        """未知界面兜底。返回 True 仅在需要结束战斗循环时（目前不主动结束）。"""
        diff_th = float(param.get("stall_diff", 1.5))
        need = max(2, int(param.get("stall_checks", 5)))
        limit = max(1, int(param.get("stall_max", 4)))
        settle = float(param.get("wrong_settle_ms", 1200)) / 1000
        settle_th = float(param.get("settle_threshold", 0.8))
        back_xy = param.get("back_xy", [1244, 28])

        sig = cv2.resize(gray, (32, 18), interpolation=cv2.INTER_AREA)
        prev = stall.get("sig")
        if prev is None:
            stall["cnt"] = 0
        elif float(cv2.absdiff(sig, prev).mean()) < diff_th:
            stall["cnt"] = int(stall.get("cnt", 0)) + 1
        else:
            stall["cnt"] = 0
        stall["sig"] = sig

        if int(stall.get("cnt", 0)) < need:
            return False
        if int(stall.get("fired", 0)) >= limit:
            if not stall.get("exhausted"):
                print(f"[CombatFighter] 未知界面兜底已用满 {limit} 次，停止兜底（回到正常循环）",
                      flush=True)
                stall["exhausted"] = True
            stall["cnt"] = 0
            return False
        # 触发前再做一次 0.8s 后的确认：检查间隔是 4s，若战斗待机动画周期恰好是
        # 4s 的整数分之一，固定间隔采样会出现「相位对齐」的假静止。0.8s 与 4s 非整除，
        # 真实运动的画面这一步必然露馅；装备页这种死页面依旧纹丝不动。
        time.sleep(0.8)
        if not _shot(ctrl):
            return False
        img2 = ctrl.cached_image
        if img2 is None or not getattr(img2, "size", 0):
            return False
        gray2 = cls._to_gray_1280(img2)
        sig2 = cv2.resize(gray2, (32, 18), interpolation=cv2.INTER_AREA)
        if float(cv2.absdiff(sig2, sig).mean()) >= diff_th:
            stall["cnt"] = 0
            stall["sig"] = sig2
            return False
        # 结算画面（胜/负文字、确定键）不点返回，交给上层流水线处理
        if cls._settle_visible(gray2, settle_th):
            stall["cnt"] = 0
            stall["sig"] = sig2
            return False

        stall["cnt"] = 0
        stall["sig"] = sig2
        stall["fired"] = int(stall.get("fired", 0)) + 1
        pos = cls._find_red_back(img2) or (int(back_xy[0]), int(back_xy[1]))
        print(f"[CombatFighter] 画面静止 {need} 次仍未识别到已知界面（疑似装备页等），"
              f"点返回 {pos} 兜底（第 {stall['fired']}/{limit} 次）", flush=True)
        ctrl.post_click(int(pos[0]), int(pos[1]))
        time.sleep(settle)
        return False

    @classmethod
    def _settle_visible(cls, gray, threshold: float) -> bool:
        """画面里是否已有胜负结算元素（win/lose 文字或确定键）。"""
        roi = (0, 0, gray.shape[1], gray.shape[0])
        for rel in ("win_text", "lose_text", "settle_confirm"):
            tpl = cls._load_tpl(rel)
            if tpl is None:
                continue
            if cls._match_score(gray, tpl, roi) >= threshold:
                return True
        return False

    @staticmethod
    def _find_red_back(img):
        """在右上角 (x>=900, y<95) 找红色返回箭头，返回 1280 基准中心坐标或 None。

        各子界面（竞技场页/装备页/至尊赛/商城）的返回键都是同一个红色箭头，
        但横坐标不完全一致（装备页 ≈(1110,33)、至尊赛/商城 ≈(1244,28)），
        所以用红色掩码取「最靠右的红色块」而不是写死坐标。
        """
        try:
            bgr = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR) if img.ndim == 2 else img
            h, w = bgr.shape[:2]
            if w != 1280:
                bgr = cv2.resize(bgr, (1280, max(1, int(round(h * 1280.0 / w)))),
                                 interpolation=cv2.INTER_AREA)
            roi = bgr[0:95, 900:1280]
            if roi.size == 0:
                return None
            b = roi[:, :, 0].astype("int16")
            g = roi[:, :, 1].astype("int16")
            r = roi[:, :, 2].astype("int16")
            red = (r - cv2.max(g, b)).clip(0, 255).astype("uint8")
            mask = ((red > 55).astype("uint8")) * 255
            mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE,
                                    cv2.getStructuringElement(cv2.MORPH_RECT, (5, 5)))
            cnts, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            best = None
            for c in cnts:
                x, y, cw, ch = cv2.boundingRect(c)
                if cw * ch < 250 or cw < 14 or ch < 10 or cw > 130 or ch > 95:
                    continue
                cx = x + cw / 2.0 + 900.0
                if best is None or cx > best[0]:
                    best = (cx, y + ch / 2.0)
            if best is None:
                return None
            return int(round(best[0])), int(round(best[1]))
        except Exception:
            return None

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
