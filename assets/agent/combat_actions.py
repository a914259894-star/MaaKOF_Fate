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
import numpy as np

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


# ================== 技能识别本地化（v1.2.18，纯性能优化，判定口径不变） ==================
# 背景：CombatFighter 每轮都要判「替身是否发光」，原来走 context.run_recognition，
# 一次就是 AgentClient 的跨进程往返（现场 maa.log 里 `handle_context_run_recognition`
# 的 `leave, Xms`：P50 14ms / P90 46ms / 最大 121ms，另外还要算 agent 侧 socket 收发
# 和跨进程线程唤醒）。而 SkillReadyBright 干的事只是「取 ROI → 算亮度占比 → 比阈值」，
# 纯 numpy 实测 0.016ms。同一个口径在进程内直接算，等于白捡一次 IPC。
#
# 参数不写死：启动时从 resource/pipeline/combat.json 读 SkillReadyBright 节点的 roi
# 和 custom_recognition_param。这样以后改 combat.json 的 roi/阈值，本地实现自动跟着变，
# 不会出现「pipeline 改了、agent 里那份没改」的漂移。读不到就整体回落 IPC（行为不变）。

_RECO_CFG: dict | None = None


def _reco_cfg_path() -> str:
    # _ARENA_DIR = .../resource/image/arena → .../resource/pipeline/combat.json
    return os.path.normpath(os.path.join(_ARENA_DIR, "..", "..", "pipeline", "combat.json"))


def _load_reco_cfg() -> dict:
    """{节点名: {roi, channel, mode, threshold, v_thresh, confirm}}，只读一次。"""
    global _RECO_CFG
    if _RECO_CFG is not None:
        return _RECO_CFG
    cfg: dict = {}
    try:
        with open(_reco_cfg_path(), encoding="utf-8") as f:
            nodes = json.load(f)
        for name, node in nodes.items():
            if not isinstance(node, dict):
                continue
            if node.get("custom_recognition") != "SkillReadyBright":
                continue
            p = node.get("custom_recognition_param") or {}
            if isinstance(p, str):
                p = json.loads(p)
            roi = node.get("roi") or [0, 0, 0, 0]
            if len(roi) != 4 or int(roi[2]) <= 0 or int(roi[3]) <= 0:
                continue
            cfg[name] = {
                "roi": tuple(int(v) for v in roi),
                "channel": str(p.get("channel", "v")).lower(),
                "mode": str(p.get("mode", "median")).lower(),
                "threshold": float(p.get("threshold", 128)),
                "v_thresh": float(p.get("v_thresh", 160)),
                "confirm": max(1, int(p.get("confirm", 1))),
            }
        print(f"[CombatFighter] 技能识别本地化：{len(cfg)} 个节点 {sorted(cfg)}"
              "（口径取自 pipeline/combat.json）", flush=True)
    except Exception as e:  # 文件缺失 / JSON 坏 / 路径不对 → 回落 IPC
        print(f"[CombatFighter] 读取 pipeline 技能参数失败（{e}），技能识别回落 IPC", flush=True)
        cfg = {}
    _RECO_CFG = cfg
    return cfg


def _ready_metric(patch, channel: str, mode: str, v_thresh: float) -> float | None:
    """与 combat_recognition._metric 逐行等价（输入 BGR patch）。

    返回 None 表示 patch 形状不可用（调用方按未命中处理）。
    """
    if patch.ndim != 3 or patch.shape[2] != 3 or patch.size == 0:
        return None
    arr = patch.reshape(-1, 3).astype(np.float32)
    if mode == "ratio":
        v = arr.max(axis=1)
        return float((v >= v_thresh).mean())
    if channel == "v":
        m = arr.max(axis=1)
    elif channel == "s":
        mx, mn = arr.max(axis=1), arr.min(axis=1)
        m = np.where(mx > 0, (mx - mn) / np.maximum(mx, 1.0) * 255.0, 0.0)
    elif channel == "min_vs":
        mx, mn = arr.max(axis=1), arr.min(axis=1)
        v = mx
        s = np.where(mx > 0, (mx - mn) / np.maximum(mx, 1.0) * 255.0, 0.0)
        m = np.minimum(v, s)
    else:  # gray
        m = 0.114 * arr[:, 0] + 0.587 * arr[:, 1] + 0.299 * arr[:, 2]
    return float(m.mean()) if mode == "mean" else float(np.median(m))


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
        "settle_threshold": 0.8,       // 结算元素阈值（命中则不点返回）
        // —— 大师团页快速收手（2026-10-23 v1.2.6） ——
        "team_roi": [380, 20, 560, 90], // master_title（大师竞技横幅）所在位置
        "team_threshold": 0.9,         // 该 ROI 内命中即判定已回到大师团页
        // —— 主城兜底（2026-10-23 v1.2.7） ——
        "home_threshold": 0.85,        // 主城「KO 竞技」图标（main_arena_btn）阈值
        "home_roi": [0, 0, 0, 0]       // 0 = 全屏找（主城图标位置随 UI 版本会变）
        // —— 防误点（2026-10-28 v1.2.19，每轮都判，不占 result_interval 节流） ——
        "settle_threshold": 0.85,      // 结算页 win_text/lose_text 阈值
        "settle_roi": [410, 70, 430, 130],  // 结算页顶部文字（胜利/失败各一张）
        "arena_threshold": 0.85,       // 竞技场主页 arena_title 阈值
        "arena_roi": [0, 0, 240, 90],  // 左上角标题（ROI 化比全屏快 60 倍）
        "battle_threshold": 0.8,       // 战斗 HUD 血条段阈值（battle_hp1/2）
        "battle_roi": [88, 14, 1104, 38]    // 两条血条所在横带
    }

    退出设计（2026-10-22 定稿，2026-10-23 v1.2.7 修正）：盲点速度快，可能误点进
    竞技场的子界面且多层嵌套。每 result_interval 秒检查一次当前画面：
      ① 玩家详情弹窗 → 点 X 关闭（回排行榜）
      ② 排行榜 / 至尊赛 / 商城 → 点右上返回
      ①②循环处理嵌套（最多 wrong_max_layers 层）；
      ③ 全部未命中且识别到竞技场主页面（arena_title / 大师团页 master_title）→
        收手 break，交还流水线进下一轮；**2s 复核失败也一样收手**（v1.2.7：
        复核失败说明画面被残留点击带走了，人已经不在战斗里，绝不能恢复盲点）；
      ④ 识别到主城（main_arena_btn）→ 收手 break，交给流水线点「竞技」重进；
      ⑤ 无误点且非上述页面 → 检查失败结算文字（只查 lose），命中 break 交还 Fight_Lose
        → Arena_SettleOk 点确认（失败结算画面盲点点不掉，v1.1.3 实测卡死 900s 的教训；
        胜利结算盲点自己会点掉回主界面，不查 win）；
        v1.2.19：win/lose 已经改成主循环每轮都查（_settle_hit，2.1ms），这条 4s 节流的
        只查 lose，退化成双保险；胜负已决/一穿三那 5.7s 里的盲点仍在发，但坐标无害。
      ⑥ 画面连续 stall_checks 次几乎不动、又不是上面任何一种（如装备页）→ 未知界面
        兜底：点右上红色返回箭头自救（v1.2.2，最多 stall_max 次）；
      ⑦ 都未命中 → 还在战斗，继续盲点。

    大师团页特例（v1.2.6）：胜利后残留盲点会落到大师团页——那页 1137,632 正好是
    「匹配战斗」、981,643 正好是「一键上阵」，会盲点掉底部角色行的上阵角色。那页
    没有任何能跳走的入口，误点跑不了，所以不再对它做任何「返回/关闭」自救，只做
    一件事：每轮循环先用小 ROI 判一次（master_title + 卡位勾），命中就立刻停手，
    把「补人 + 继续匹配」交给流水线（Arena_TeamFix → Arena_MasterMatch）。
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

    # skill_cycle 的键 → pipeline 里的节点名（就绪判定要用这些节点的 roi 与阈值）
    _SKILL_NODE = {
        "skill1": "Fight_Skill1",
        "skill2": "Fight_Skill2",
        "skill3": "Fight_Skill3",
        "ult": "Fight_Ult",
        "secret": "Fight_Secret",
    }

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
        tap_hold = float(tap_cfg.get("hold_ms", 40)) / 1000
        tap_gap = float(tap_cfg.get("interval_ms", 150)) / 1000
        # 胜负结算检测（2026-10-22 定稿：只查失败）：失败结算画面盲点点不掉（确认键
        # 不在盲点坐标上），必须检测兜底 break 交给 Fight_Lose → Arena_SettleOk；
        # 胜利结算盲点自己会点掉回主界面（由主界面判定收口），不查 win——查询窗口期
        # 盲点误点结算按钮反而是历史问题源头。
        result_nodes = list(param.get("result_nodes", ["Fight_Lose"]))
        keep_moving = bool(param.get("keep_moving", False))
        move_dir = param.get("move_dir", [-1, 0])

        # ===== v1.2.20：出招节奏改造（P0 就绪轮询 / P1 普攻/轮 / P2 受击 / P3 KO 静默） =====
        # 实测依据见 debug/plan_v1.2.20_combat.md：一轮 600ms 里 300ms 花在 5 个技能的盲点上，
        # 而技能 CD 是 5~12s，约 93% 的点落在冷却里。
        ready_only = bool(param.get("ready_only", True))
        ready_miss_rounds = max(1, int(param.get("ready_miss_rounds", 4)))
        normal_per_round = max(1, int(param.get("normal_per_round", 1)))
        min_round = max(0.0, float(param.get("min_round_ms", 150)) / 1000.0)
        hp_drop_px = max(1, int(param.get("hp_drop_px", 4)))
        sub_burst_times = max(0, int(param.get("sub_burst_times", 2)))
        sub_burst_cd = max(0.0, float(param.get("sub_burst_cd_s", 1.5)))
        ko_quiet_max = max(1.0, float(param.get("ko_quiet_max_s", 12)))
        # 就绪判定只在「读到了 pipeline 参数」时启用；读不到 → 退回原来的全量盲点
        reco_cfg = _load_reco_cfg() if ready_only else {}
        ready_miss = 0           # 连续「一个技能都没就绪」的轮数（兜底用）
        hp_self_last = None      # 上一轮己方血条宽度（判掉血）
        enemy_gone = 0           # 敌方血条连续「空/测不到」的次数
        enemy_low = False        # 本轮是否见过敌方血条见底（KO 前置，防特效遮挡误判）
        ko_quiet = True          # P3 静默开关（血条判据失灵一次就关掉，本局不再用）
        sub_cd_until = 0.0       # 受击连点替身的冷却截止时刻

        ctrl = context.tasker.controller
        print(f"[CombatFighter] 开始（{duration:.0f}s 上限）", flush=True)
        deadline = time.time() + duration
        # 结算识别降频：全屏模板识别在慢环境下单次可达 1~2.4s，
        # 每轮都查会把出招拖成涓流。每 result_interval 秒查一次足够（60s 级别的战斗）。
        result_interval = float(param.get("result_interval", 4.0))
        # v1.2.10：第一次检查要推迟一个周期再跑。原来初始 0 = 进循环立刻查一轮
        # 「误点 + 结算 + 未知界面」，实测要 500ms，等于开局又白站半秒。推迟后
        # 第一件事就是出招，检查在 4s 后照常进行（漏查的窗口只有 4s，够安全）。
        next_result_check = time.time() + result_interval

        # 开打自检：确认画面真的是战斗界面（截图若失败会是旧帧，这里能暴露）。
        # v1.2.10：原来用 context.run_recognition 走一次 MaaFramework 识别，实测
        # 一次 IPC 往返 780ms（现场 21:07:07.082 → 07.880），开局白白站桩。改成和
        # 主循环相同的本地灰度匹配（约 20ms），诊断作用不变。
        # v1.2.19：判据从 battle_bar/battle_btn 换成血条段（battle_hp1/2）——
        # 旧的两张模板在战斗侧余量只有 0.049（0.849 vs 阈值 0.8），其中 battle_btn
        # 全状态 ≤0.781 从来没到过阈值，等于废模板。
        _shot(ctrl)
        img0 = ctrl.cached_image
        if img0 is not None and getattr(img0, "size", 0):
            try:
                g0 = self._to_gray_1280(img0)
                ok = self._battle_hit(g0, param)
                print(f"[CombatFighter] 开打画面=战斗界面: {ok}", flush=True)
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
                round_t0 = time.time()

                _shot(ctrl)
                img = ctrl.cached_image
                gray = None
                hp_self = hp_enemy = None
                if img is not None and getattr(img, "size", 0):
                    # v1.2.20：血条宽度（P2 判自己挨打 / P3 判敌方 KO 换场）。
                    # 只对顶部 26 行做 HSV，实测 0.3ms，不影响出招节奏。
                    hp_self, hp_enemy = self._hp_widths(img, param)
                    gray = self._to_gray_1280(img)
                    # ===== v1.2.19 防误点：结算页 / 竞技场主页，每轮都判 =====
                    # 主防线：胜负结算页一出现就停手，「确定」交给流水线
                    # （Fight_Win/Fight_Lose → Arena_SettleOk）。它把「结算页 →
                    # 加载过场 → 竞技场主页」这三段的盲点全部清零。
                    if self._settle_hit(gray, param):
                        print("[CombatFighter] 检测到胜负结算页，立即停手"
                              "（交给 Fight_Win/Fight_Lose）", flush=True)
                        break
                    # 兜底：结算页没认出来（UI 改版/淡入帧）时，盲点会把确定点掉、
                    # 一路点到竞技场主页。这一页 8 个盲点全压在功能入口上
                    # （黄金擂台 ×3、至尊赛、大神榜、竞技商店…），必须停手。
                    if self._arena_page_hit(gray, param):
                        print("[CombatFighter] 已回到竞技场主页，立即停手"
                              "（交给流水线接 1v1）", flush=True)
                        break
                    # 大师团页优先判（v1.2.6）：胜利用盲点退回大师团页时，这一页的
                    # 1137,632=匹配战斗、981,643=一键上阵，继续盲点会把底部角色行的
                    # 上阵角色点掉。判得越早越少误点，所以每轮都查（小 ROI，很便宜）。
                    if self._team_page_hit(gray, param):
                        print("[CombatFighter] 已回到大师团页（胜利后盲点退回），立即停手",
                              flush=True)
                        break
                    # 主城兜底（v1.2.7）：返回键多按了掉进主城，主城底部是装备/副本等
                    # 导航，盲点一砸就进装备页。认出来立刻收手，交给流水线重进竞技场。
                    if self._home_page_hit(gray, param):
                        print("[CombatFighter] 检测到主城界面（返回键多按了），立即停手",
                              flush=True)
                        break
                    # 段位提升弹窗（v1.2.17）：胜利结算点完「确认」后才会弹，确认键
                    # (639,611) 不在任何盲点坐标上，认出来立刻收手交给 Arena_RankUp。
                    if self._rank_up_hit(gray, param):
                        print("[CombatFighter] 检测到「KOF段位提升」弹窗，立即停手"
                              "（交给流水线点确认）", flush=True)
                        break

                # 每 10s 打一个画面指纹：数字一直完全不变 => 截图是死的旧帧
                now = time.time()
                if gray is not None and now - last_report >= 10:
                    last_report = now
                    try:
                        fp = float(gray.mean())
                    except Exception:
                        fp = -1.0
                    print(f"[CombatFighter] t={now - t0:.0f}s 画面均值={fp:.1f}", flush=True)

                # ===== P3 敌方 KO / 换场：静默等下一回合（v1.2.20） =====
                # 实测一局 97.6s 里有 15.7s 是「KO 定格 + WINNER + 黑屏」（16%），
                # 这段时间点击全是空的，而且 WINNER 画面万一有按钮会被盲点砸中。
                # 判据不用模板：敌方血条先降到 ≤8px（见底 ≤15px），之后连续 3 次测不到/仍见底
                # 就是被打空了。「先见底」这个前置很关键 —— 否则大招特效糊住血条造成的
                # 偶发测不到也会被当成 KO，白白静默。
                # 「车轮战」规则下新回合敌方满血登场（实测 831→1164），血条回到 >20px
                # 就恢复出招；静默上限 ko_quiet_max_s（实测最长换场 7.9s）。
                if hp_enemy is not None and hp_enemy > 20:
                    enemy_low = False
                    enemy_gone = 0
                elif enemy_low and (hp_enemy is None or hp_enemy <= 15):
                    enemy_gone += 1
                elif hp_enemy is not None and hp_enemy <= 15:
                    enemy_low = True
                    enemy_gone += 1
                else:
                    enemy_gone = 0
                if ko_quiet and enemy_gone >= 3:
                    print("[CombatFighter] 敌方血量见底（KO/换场），静默等下一回合", flush=True)
                    res = self._quiet_wait(ctrl, context, param, ko_quiet_max, deadline)
                    if res == "page":
                        print("[CombatFighter] 静默期间出现结算/主城等页面，停手交给流水线",
                              flush=True)
                        break
                    if res == "timeout":
                        ko_quiet = False  # 判据失灵（如血条被长期遮挡）→ 本局关掉静默
                    enemy_gone = 0
                    enemy_low = False
                    hp_self_last = None
                    next_result_check = time.time() + result_interval
                    continue

                # ===== P2 自己挨打：连点替身脱身（v1.2.20） =====
                # 实测 ROUND 2 的 95.7s 一次掉 34% 血、之后 5 秒敌方血量纹丝不动 ——
                # 被连招压着打时，一轮只点一次替身经常错过发光窗口。
                # 触发条件只看「己方血条在掉」，1.5s 冷却防连点成翻滚位移。
                if (hp_self is not None and hp_self_last is not None
                        and hp_self_last - hp_self >= hp_drop_px
                        and sub_burst_times > 0 and time.time() >= sub_cd_until):
                    sub_cd_until = time.time() + sub_burst_cd
                    print(f"[CombatFighter] 己方掉血 {hp_self_last:.0f}→{hp_self:.0f}px，"
                          f"连点替身×{sub_burst_times}", flush=True)
                    for _ in range(sub_burst_times):
                        if context.tasker.stopping or time.time() >= deadline:
                            break
                        _shot(ctrl)
                        im2 = ctrl.cached_image
                        if im2 is not None and getattr(im2, "size", 0):
                            node = self._first_hit(im2, recog_nodes, context)
                            if node:
                                k = self._key_of(node)
                                if k:
                                    _tap(ctrl, k, hold_ms=skill_hold)
                        time.sleep(0.06)
                if hp_self is not None:
                    hp_self_last = hp_self

                # 误点界面识别（降频）：到达检查时刻才跑。
                # ① 命中误点界面 => 循环执行退出（嵌套逐层退），直到回主界面才收手 break；
                # ② 无误点且非主界面 => 检查胜负结算文字（失败结算盲点点不掉，必须兜底）；
                # ③ 都未命中 => 还在战斗，继续盲点。
                if gray is not None and now >= next_result_check:
                    next_result_check = now + result_interval
                    if self._escape_misnav(ctrl, gray, param):
                        break
                    if self._hit_any(context, img, result_nodes, gray):
                        print("[CombatFighter] 检测到胜负结算，停止出招", flush=True)
                        break
                    # ④ 未知界面兜底：既不是已知误点界面/主界面/结算，画面又长期不动
                    #    （如装备页）→ 点右上返回自救，避免盲点空转 900s。
                    if self._unknown_escape(ctrl, img, gray, param, stall):
                        break

                # 替身：受击发光才点，其余技能不识别
                # v1.2.18：改走本地等价判定（_first_hit），不再每轮发一次 IPC；
                # 命中逻辑（第一个命中的节点 → 点它的按钮）与原来逐字一致。
                if img is not None and getattr(img, "size", 0) and recog_nodes:
                    node = self._first_hit(img, recog_nodes, context)
                    if node:
                        key = self._key_of(node)
                        if key:
                            _tap(ctrl, key, hold_ms=skill_hold)

                # ===== P0 技能就绪轮询（v1.2.20） =====
                # 原来每轮无条件把 5 个技能各点一次：CD 是 5~12s，一轮 600ms，
                # 约 93% 的点击落在冷却里，白耗 280ms/轮。
                # 现在改成本地判就绪只点就绪的（5 个合计 0.41ms），省下的时间
                # 直接进下一轮 → 技能 CD 好了最多 150ms 就被点上（原来最多 600ms）。
                # 兜底：连续 ready_miss_rounds 轮一个都没就绪 → 直接退回「全量盲点」
                # 并一直保持到本局结束。理由是「只盲点一轮」在阈值整体失效时反而
                # 更差（技能频率从 1.7 次/s 掉到 0.3 次/s）；退回旧行为至少不倒退。
                cycle = skill_cycle
                if reco_cfg and img is not None and getattr(img, "size", 0):
                    ready = self._ready_keys(img, reco_cfg)
                    hit = [k for k in skill_cycle if k in ready]
                    if hit:
                        cycle = hit
                        ready_miss = 0
                    else:
                        ready_miss += 1
                        if ready_miss >= ready_miss_rounds:
                            reco_cfg = {}
                            ready_miss = 0
                            print(f"[CombatFighter] 连续 {ready_miss_rounds} 轮无技能就绪，"
                                  "就绪阈值可能失效 → 本局退回全量盲点", flush=True)
                        else:
                            cycle = []   # 全在 CD：这轮不点技能，把时间让给普攻

                for key in cycle:
                    if context.tasker.stopping:
                        break
                    if key in BUTTONS:
                        _tap(ctrl, key, hold_ms=skill_hold)
                        time.sleep(skill_gap)

                # P1：每轮只点 normal_per_round 次普攻，节奏交给轮次周期（min_round_ms）。
                # 原来 30ms 间隔连点 3 次、90ms 内打完，实测只出第 1 段。
                for _ in range(normal_per_round):
                    if context.tasker.stopping:
                        break
                    _tap(ctrl, "normal", hold_ms=int(tap_hold * 1000))
                    if normal_per_round > 1:
                        time.sleep(tap_gap)

                # 轮次最短周期：保证普攻有连招窗口，又不把技能响应拖慢
                used = time.time() - round_t0
                if min_round > used:
                    time.sleep(min_round - used)
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
    def _ready_hit(cls, img, cfg) -> bool:
        """单个 SkillReadyBright 节点的本地等价判定（img 为 BGR cached_image）。

        roi 是 1280 基准；非 16:9 设备上 maa 会按实际宽度等比换算后传给
        CustomRecognition，这里做同样的换算，保证与 IPC 版看到的是同一块像素。
        """
        x, y, w, h = cfg["roi"]
        if img.ndim == 2:  # 图像通道降级（个别截图方式会给灰度图）时补成 BGR
            img = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
        H, W = img.shape[:2]
        if W != 1280:
            sc = W / 1280.0
            x = int(round(x * sc))
            y = int(round(y * sc))
            w = max(1, int(round(w * sc)))
            h = max(1, int(round(h * sc)))
        patch = img[max(0, y):min(H, y + h), max(0, x):min(W, x + w)]
        value = _ready_metric(patch, cfg["channel"], cfg["mode"], cfg["v_thresh"])
        if value is None:
            return False
        return value >= cfg["threshold"]

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

    # 大师团页底部角色行的卡位（勾徽章中心，1280 基准；间距 137）
    _TEAM_SLOTS = ((126, 640), (262, 640), (400, 640), (537, 640), (674, 640))

    @classmethod
    def _team_page_hit(cls, gray, param) -> bool:
        """是不是「大师团」上阵页（v1.2.6）。

        双重判据，避免战斗画面里偶然命中横幅：master_title 在窄 ROI
        (默认 380,20,560,90) 内 ≥ team_threshold，且底部角色行至少一个卡位
        带着白色圆形勾（上阵标记，实测已上阵 0.93~1.00 / 未上阵 0.22）。
        """
        roi = tuple(int(v) for v in param.get("team_roi", [380, 20, 560, 90]))
        th = float(param.get("team_threshold", 0.9))
        title = cls._load_tpl("master_title")
        if title is None:
            return False
        if cls._match_score(gray, title, roi) < th:
            return False
        badge = cls._load_tpl("master_team_check")
        if badge is None:
            return False
        for cx, cy in cls._TEAM_SLOTS:
            if cls._match_score(gray, badge, (cx - 40, cy - 40, 80, 80)) >= 0.7:
                return True
        return False

    @classmethod
    def _home_page_hit(cls, gray, param) -> bool:
        """主城界面判定（v1.2.7）：主城上的「KO 竞技」入口图标（main_arena_btn）。

        为什么需要它：误点退出的返回键点多了会一路退到主城。主城没有竞技场标题、
        也没有结算元素，旧代码会当成「还在战斗」继续盲点——主城底部就是装备/副本/
        招募这些导航，盲点一砸就进装备页（现场 18:17 那一串的由来）。

        认出主城 → 立刻收手，交给流水线 Arena_Enter_1v1 点「竞技」重进，形成兜底。

        只做「高阈值 + 全屏」：主城图标位置随 UI 版本会变，写死 ROI 不划算；
        阈值 0.85 比节点里的 0.7 更严，避免在竞技场页误命中（真误命中也无害：
        竞技场页有 arena_title，流水线会先走 Arena_MainPage）。
        """
        tpl = cls._load_tpl("main_arena_btn")
        if tpl is None:
            return False
        th = float(param.get("home_threshold", 0.85))
        roi = tuple(int(v) for v in param.get("home_roi", [0, 0, 0, 0]))
        if roi[2] <= 0 or roi[3] <= 0:
            roi = (0, 0, gray.shape[1], gray.shape[0])
        return cls._match_score(gray, tpl, roi) >= th

    @classmethod
    def _rank_up_hit(cls, gray, param) -> bool:
        """胜利后的「KOF段位提升」全屏弹窗（v1.2.17，移植自 v1.2.14）。

        1v1 和大师赛胜利后都可能弹：顶部「KOF段位提升」+ 徽章 + 段位名 + 红色「确认」。
        顺序是「结算页 → 点结算确认 → 这个弹窗」，它不会顶替结算页 —— 所以
        `Fight_Win` / `Fight_Lose` 不需要它，但**战斗循环必须认它**：
        CombatFighter 是靠盲点把结算页的「确认」点掉的，点完之后画面就换成这个
        弹窗，而它的确认键在 (639,611)，**不在任何盲点坐标上**（盲点全在右侧技能区
        x≈950~1220 / y≈200~630）。不认出来就会一直在弹窗上空点到超时。

        实测（1280 基准，用户 2026-10-25 截图）：本弹窗 ROI 内 1.000；
        选人页 0.10~0.16 / 结算页 0.24~0.25 / 大师团页 0.15 / 战斗 0.23。
        """
        tpl = cls._load_tpl("rank_up_title")
        if tpl is None:
            return False
        th = float(param.get("rank_up_threshold", 0.8))
        roi = tuple(int(v) for v in param.get("rank_up_roi", [300, 60, 720, 130]))
        return cls._match_score(gray, tpl, roi) >= th

    @classmethod
    def _ready_keys(cls, img, cfg) -> set:
        """本轮「技能已就绪」的键集合（img 为 BGR cached_image，cfg 来自 combat.json）。

        阈值口径：v1.2.20 实测发现原来那组阈值普遍偏紧（skill1 冷却中固定落在
        0.677，阈值 0.5 会误判；skill2 就绪只有 0.52，阈值 0.5 贴着边）。
        详见 debug/_probe_skill_ready.py 的标定表与 debug/_probe_btn_grid.png。

        冷却中的按钮中央有白色秒数、底色是暗紫（skill1 实测 0.07）；
        就绪态是整颗亮紫图标（0.68）。所以「亮像素占比」能分开，只是阈值要放对。
        """
        out: set = set()
        for key, node in cls._SKILL_NODE.items():
            c = cfg.get(node)
            if c is None:
                continue
            try:
                if cls._ready_hit(img, c):
                    out.add(key)
            except Exception:
                continue
        return out

    @classmethod
    def _hp_widths(cls, img, param):
        """战斗中双方血条宽度（px，1280 基准），测不到返回 None。

        血条是唯一「打没打中 / 有没挨打」的直接反馈，且不需要新模板。
        实测（debug/_vid4 逐秒 150 帧，敌方右对齐 / 己方左对齐，满血各 333px，
        精度 ±1px）：敌方 831→1164 单调收缩，己方 115→448 单调收缩。

        只切顶部 26 行做 HSV（整图 HSV 要 3ms，这样 0.3ms），所以逐轮跑得起。
        阈值写死黄绿区间（35~75），因为血条是黄绿色、背景 HUD 是深蓝黑、
        能量条是橙色（H≈15~25）、延迟文字是低饱和白 —— 三者都不在区间内。
        """
        roi = param.get("hp_roi", [18, 44, 80, 1210])
        self_lo, self_hi = param.get("hp_self_x", [115, 448])
        ene_lo, ene_hi = param.get("hp_enemy_x", [831, 1164])
        if img is None or not getattr(img, "size", 0):
            return None, None
        if img.ndim == 2:
            img = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
        H, W = img.shape[:2]
        sc = W / 1280.0
        y0, y1 = int(roi[0] * sc), int(roi[1] * sc)
        band = img[max(0, y0):min(H, y1), :]
        if band.size == 0 or band.shape[0] < 4:
            return None, None
        if band.shape[1] != 1280:
            band = cv2.resize(band, (1280, band.shape[0]), interpolation=cv2.INTER_AREA)
        hsv = cv2.cvtColor(band, cv2.COLOR_BGR2HSV)
        mask = cv2.inRange(hsv, np.array((35, 120, 120), np.uint8),
                           np.array((75, 255, 255), np.uint8)) > 0
        col = mask.mean(axis=0) > 0.45

        def edge(x0: int, x1: int, pick_right: bool):
            x0 = max(0, int(x0))
            x1 = min(1280, int(x1))
            seg = np.where(col[x0:x1])[0]
            if len(seg) == 0:
                return None
            return int(x0 + (seg.max() if pick_right else seg.min()))

        right = edge(self_lo, self_hi + 40, True)     # 己方：最右端
        left = edge(ene_lo - 40, ene_hi, False)       # 敌方：最左端
        hp_self = (right - self_lo) if right is not None else None
        hp_enemy = (ene_hi - left) if left is not None else None
        return hp_self, hp_enemy

    def _quiet_wait(self, ctrl, context, param, max_s: float, deadline=None) -> str:
        """P3 静默期：一方 KO / 换场时只观察不点击，等新回合。

        返回值：
          "round"   → 新回合开始（敌方血条回到 >20px），恢复出招
          "page"    → 静默期间出现了该停手的页面（结算页/竞技场主页/大师团/主城/
                      段位弹窗），调用方应当 break 交给流水线
          "timeout" → 超时（调用方据此关闭本局静默，避免血条判据失灵时整局卡住）

        「最后一回合 KO」是必须在这里认页面的原因：KO 之后 10s 左右才进结算页
        （录像 133.5 KO → 143.7 结算），如果只盯血条会一路等到 12s 超时，
        正好把 v1.2.19 那条 2.33s 的结算页防误点窗口整段错过。
        """
        t_end = time.time() + max_s
        while time.time() < t_end:
            if context.tasker.stopping:
                return "timeout"
            if deadline is not None and time.time() >= deadline:
                return "timeout"
            time.sleep(0.4)
            _shot(ctrl)
            img = ctrl.cached_image
            if img is None or not getattr(img, "size", 0):
                continue
            hs, he = self._hp_widths(img, param)
            # 新回合：敌方满血登场（实测 333px），己方血条同时可见
            if he is not None and he > 20 and hs is not None:
                print(f"[CombatFighter] 新回合开始（敌方血量 {he:.0f}px），恢复出招",
                      flush=True)
                return "round"
            try:
                gray = self._to_gray_1280(img)
            except Exception:
                continue
            if (self._settle_hit(gray, param) or self._arena_page_hit(gray, param)
                    or self._team_page_hit(gray, param)
                    or self._rank_up_hit(gray, param)
                    or self._home_page_hit(gray, param)):
                return "page"
        print(f"[CombatFighter] KO 静默超时 {max_s:.0f}s，恢复出招（防卡死）", flush=True)
        return "timeout"

    @classmethod
    def _settle_hit(cls, gray, param) -> bool:
        """胜负结算页判定（v1.2.19）—— 主防线。

        实测（全流程录屏《拳皇·命运(7).mp4》逐帧，1280 基准）：
          ROI [410,70,430,130] 内 win 模板：结算页 0.988~0.989；主城 0.221 /
          一穿三 0.166 / 战斗 0.142 / 至尊赛 0.116 / 加载过场 -0.009。
          可靠窗口 143.73（0.890）~146.06（黑屏）= 2.33s，按 600ms 一轮有 3~4 次机会；
          两个模板合计 2.1ms。

        为什么现在要查「胜利」：历史上故意不查，是怕「查询窗口期盲点点到确定键」。
        但那条假设建立在「胜利后盲点自己会把确定点掉、回到竞技场主页就安全」之上。
        现在竞技场主页右下角是「赏金擂台/黄金擂台」入口，盲点把确认点掉之后会一路
        点进活动甚至开第二把（现场录屏 148.8~151.0：竞技场主页 → 至尊赛 → 匹配成功）。
        所以改成「一认出结算页就停手」，点确定这件事交给流水线（节流 1s，比盲点准）。
        """
        th = float(param.get("settle_threshold", 0.85))
        roi = tuple(int(v) for v in param.get("settle_roi", [410, 70, 430, 130]))
        for rel in ("win_text", "lose_text"):
            tpl = cls._load_tpl(rel)
            if tpl is None:
                continue
            if cls._match_score(gray, tpl, roi) >= th:
                return True
        return False

    @classmethod
    def _arena_page_hit(cls, gray, param) -> bool:
        """竞技场主页判定（v1.2.19）—— 结算页判定失效时的兜底。

        竞技场主页的 8 个盲点坐标全压在功能入口上（skill1→至尊赛、skill2/3/roll→
        黄金擂台、normal×3→大神榜/竞技商店、ult→1V1擂台争霸、burst→3V3黑晶迷阵），
        在这一页发盲点几乎必然点进别的玩法。

        实测（ROI [0,0,240,90]）：竞技场主页 0.991~0.994；至尊赛 0.565 / VS 0.519 /
        加载过场 0.495 / 选人页 0.372 / 战斗 0.173 / 主城 0.245。阈值 0.85（余量 0.29），
        成本 0.36ms（全屏匹配 arena_title 要 21.7ms，ROI 化后快 60 倍）。

        这页在正常流程里只存在 0.56~0.66s，比一轮（600ms）略长，数学上必然覆盖到；
        而且一旦停手画面就静止，流水线 0.5s 一轮一定接得住，不存在抢窗口。
        """
        tpl = cls._load_tpl("arena_title")
        if tpl is None:
            return False
        th = float(param.get("arena_threshold", 0.85))
        roi = tuple(int(v) for v in param.get("arena_roi", [0, 0, 240, 90]))
        return cls._match_score(gray, tpl, roi) >= th

    @classmethod
    def _battle_hit(cls, gray, param) -> bool:
        """「现在确实在战斗界面」判定（v1.2.19）—— 替换余量很薄的 battle_bar/battle_btn。

        旧判据实测（44 帧全状态）：
          battle_bar 战斗 0.849~0.926 / 非战斗最高 0.632（结算页），阈值 0.8 →
            战斗侧余量只剩 0.049，UI 一改版就会漏判；
          battle_btn 全状态最高 0.781（战斗内最高 0.752），**从来没到过阈值**，
            等于一张废模板（节点是「任一命中」，所以它一直没暴露）。
        更正一条旧记录：早前写的「战斗中 0.555 / 胜负已决 0.915」是把 VS 倒计时帧
        当成了战斗帧，实测 VS 页 battle_bar 0.556、真正的战斗帧 0.915 —— 判据没反，
        只是薄。

        新判据用战斗顶部 HUD 的血条段：血条底板是不透明的，ROI 里要么是彩色填充
        （血量）要么是暗色空槽，不会透出背景场景；别的界面在同一坐标上是什么就
        是什么（蓝天空/沙漠/UI 面板），匹配不上。模板 71x24（一段血条，71px 正好是
        血条上两处暗格之间的宽度）。

        实测（44 帧全状态：主城/竞技场主页/VS/选人/战斗×11/胜负已决/一穿三/结算页/
        加载过场/至尊赛）：
          ROI [88,14,1104,38] 内两张模板取 max：战斗 0.962~1.000，
          其余全部 ≤0.552（选人页最高 0.552，加载过场 0.327，结算页 0.450）。
          阈值 0.8 → 两侧余量各 0.25/0.16；成本 0.89ms（两张一起，原来全屏两张 34ms）。

        覆盖范围：1P 或 2P 任意一侧还剩一整段血（约 HP≥25%）即命中；两侧同时见底
        才漏判 —— 此时本来也快分出胜负了，漏判只会让调用方多等一轮。
        """
        th = float(param.get("battle_threshold", 0.8))
        roi = tuple(int(v) for v in param.get("battle_roi", [88, 14, 1104, 38]))
        for rel in ("battle_hp1", "battle_hp2"):
            tpl = cls._load_tpl(rel)
            if tpl is None:
                continue
            if cls._match_score(gray, tpl, roi) >= th:
                return True
        return False

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
        """处理误点嵌套：逐层退出直到回到竞技场/主城这类「已经离开战斗」的页面。

        返回 True = 已离开战斗（调用方 break，把后续交给流水线）；
        返回 False = 还在战斗，继续盲点。

        v1.2.7 修正（现场 18:16:45~18:17:11）：
          原来主界面「2s 复核」失败后会 return False 恢复盲点，那一下是灾难起点——
          人已经在竞技场页了，恢复盲点等于在竞技场页面上乱按（商城/排行榜/至尊赛
          入口全在这一页），于是「退出→又被点进去→再退出」反复循环；多按几次返回
          就掉进主城，主城底部导航被盲点砸中 → 点进装备页 → 继续乱点。
          现在：复核失败 = 画面被残留点击带走了，说明已经离开战斗 → 一样收手，
          剩下的交给流水线（Arena_MainPage / Arena_Enter_1v1 / Arena_UnknownBack
          都会一步步认页面，比盲点可靠得多，也不会再点进别的页）。
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
                        return True
                    img2 = ctrl.cached_image
                    if img2 is None:
                        return True
                    gray2 = cls._to_gray_1280(img2)
                    if cls._main_page_hit(gray2, main_th):
                        print("[CombatFighter] 复核通过（已离开战斗），停止出招进下一轮", flush=True)
                        return True
                    # 复核失败：画面被残留点击带去了别的页 → 一样算「已离开战斗」，
                    # 坚决不恢复盲点（恢复了就是在竞技场页上乱按，会点进商城/排行榜/至尊赛）。
                    print("[CombatFighter] 复核失败（画面已被残留点击带走），"
                          "仍按已离开战斗处理：停止出招，交给流水线认页面", flush=True)
                    return True
                if cls._home_page_hit(gray, param):
                    # 主城兜底（v1.2.7）：返回键多按了几次会掉到主城，那页底部是
                    # 装备/副本等导航，盲点会直接点进装备页 → 这里认出主城就收手。
                    print("[CombatFighter] 检测到主城界面（返回键多按了），立即停手，"
                          "交给流水线点「竞技」重进", flush=True)
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
        print(f"[CombatFighter] 连退 {max_layers} 层仍未回主界面，放弃（防死循环），收手交给流水线", flush=True)
        return True

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

        # 大师团页不点返回（v1.2.6）：这页没有任何能跳走的入口，误点跑不了，
        # 点返回反而会把界面带走；补人交给 Arena_TeamFix。
        if cls._team_page_hit(gray, param):
            stall["cnt"] = 0
            return False

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

    # 结算模板本地快速匹配表（v1.2.10）：节点名 -> (模板相对路径, 阈值)
    # 为什么不再走 context.run_recognition：一次 IPC 往返实测 400ms 以上，每
    # result_interval(4s) 查一次就等于每 4 秒把出招打断 0.6s（现场 21:07 那场
    # 21:07:12/16/20/24… 每 4 秒准时停 0.55~0.67s 全是它）。本地灰度
    # TM_CCOEFF_NORMED 全屏匹配只要 24ms，判定口径与节点里的 TemplateMatch 一致。
    _RESULT_TPLS = {
        "Fight_Lose": ("lose_text", 0.8),
        "Fight_Win": ("win_text", 0.8),
    }

    @classmethod
    def _hit_any(cls, context: Context, img, nodes: list[str], gray=None) -> bool:
        """对给定节点逐个跑识别（只识别不动作），任一命中返回 True。

        已知结算节点（_RESULT_TPLS）在有灰度帧时走本地匹配，其余（如替身这种
        Custom recognition）仍走 context.run_recognition。gray=None 时全部走 IPC。
        """
        for node in nodes:
            spec = cls._RESULT_TPLS.get(node)
            if spec is not None and gray is not None and getattr(gray, "size", 0):
                tpl = cls._load_tpl(spec[0])
                if tpl is not None:
                    roi = (0, 0, gray.shape[1], gray.shape[0])
                    if cls._match_score(gray, tpl, roi) >= float(spec[1]):
                        return True
                    continue
            try:
                det = context.run_recognition(node, img)
            except Exception:
                return False
            if det and det.hit:
                return True
        return False

    @classmethod
    def _first_hit(cls, img, nodes: list[str], context=None) -> str | None:
        """按顺序返回第一个命中的节点名（都没命中返回 None）。

        与原来「for node in nodes: if _hit_any([node]): 用它并 break」完全等价，
        区别只在于：SkillReadyBright 且 confirm==1 的节点改在进程内用 numpy 算
        （等价实现见 _ready_metric），省掉每轮一次的 IPC。其它节点（或参数读不到
        文件时）仍走 context.run_recognition，行为不变。
        """
        cfgs = _load_reco_cfg()
        for node in nodes:
            cfg = cfgs.get(node)
            if cfg is not None and cfg["confirm"] <= 1:
                if cls._ready_hit(img, cfg):
                    return node
                continue
            if context is None:
                continue
            try:
                if cls._hit_any(context, img, [node]):
                    return node
            except Exception:
                continue
        return None

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


class ArenaSafeBack(CustomAction):
    """流水线兜底返回键（v1.2.7）：只在「确实还停在子页面」时才点返回。

    原来 `Arena_UnknownBack` 是 DirectHit + Click，无条件点一次右上返回——这就是
    「返回键多按了、一路退到主城」的元凶：只要 next 里没有节点命中（例如竞技场页
    正在做转场动画、arena_title 暂时没认出来），它就再点一次返回，于是
    竞技场 → 主城 → 主城底部导航（盲点顺手点进装备）……

    现在改成自定义动作：
      · 已经在竞技场页 / 大师团页 / 主城 → **不点**，直接放行，让 next 里的
        Arena_MainPage / Arena_MasterPage / Arena_HomePage 去接管；
      · 认出别的子页面（至尊赛/排行榜/商城/装备页…）→ 才点一次返回。
    点返回的位置优先用红色箭头识别（_find_red_back），识别不到才用 back_xy。

    custom_action_param：{"back_xy": [1244,28], "home_threshold": 0.85}
    """

    def run(self, context: Context, argv: CustomAction.RunArg) -> bool:
        param = json.loads(argv.custom_action_param) if argv.custom_action_param else {}
        ctrl = context.tasker.controller
        _shot(ctrl)
        img = ctrl.cached_image
        if img is None or not getattr(img, "size", 0):
            print("[SafeBack] 截图失败，跳过返回", flush=True)
            return True
        gray = CombatFighter._to_gray_1280(img)

        main_th = float(param.get("main_threshold", 0.8))
        if CombatFighter._main_page_hit(gray, main_th):
            print("[SafeBack] 已在竞技场/大师页，不点返回（交给 next 接手）", flush=True)
            return True
        if CombatFighter._home_page_hit(gray, param):
            print("[SafeBack] 已在主城，不点返回（交给 Arena_HomePage 重新进竞技场）", flush=True)
            return True

        back_xy = param.get("back_xy", [1244, 28])
        pos = CombatFighter._find_red_back(img) or (int(back_xy[0]), int(back_xy[1]))
        print(f"[SafeBack] 停在未知子页面，点返回 {pos}", flush=True)
        ctrl.post_click(int(pos[0]), int(pos[1])).wait()
        return True
