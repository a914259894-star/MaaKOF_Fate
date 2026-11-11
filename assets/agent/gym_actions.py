"""道馆挑战（道馆赛）流程动作。

结构和竞技场一致 —— 单入口 Router + override_next 精确路由 —— 但判据和动作
是**独立一套**，因为道馆挑战几乎每一步都和竞技场不一样：

    · 入口在主城右上角第二排的鸟居图标，不在擂台里
    · 匹配是 PVP（对面是真人），匹配中页面完全不变
    · 选人是横排 8 格大立绘 + 底部 3 个队伍槽，选满 3 人后**必须点「确定」**，
      不点就一直停在那一页（不会自动锁定）
    · 战斗是 3v3 多回合，每回合结束都弹 WINNER，但战斗不退出
    · 整场结算写「战斗胜利 / 战斗失败」+「点击空白处关闭」，**没有确定按钮**
    · 结算后还有一段角色展示加载，然后才回道馆页

判页实测（视频帧 1920→1280，TM_CCOEFF_NORMED 取最大值）::

    模板           命中页           命中分   其它页最高分
    gym_title      道馆挑战页        1.000    0.640（战斗）
    gym_match      道馆挑战页        1.000    0.551（战斗）
    gym_vsok       匹配成功 VS       1.000    0.401
    gym_picktip    选人页(未满)      1.000    0.709（结算页）
    gym_pickwait   选人页(已确定)    1.000    0.720（结算页）
    gym_confirm    选人页(满 3 未确定) 1.000   0.6x（其余所有页）
    gym_close      整场结算页        1.000    0.488

`gym_close` 用「点击空白处关闭」而不是「战斗胜利/失败」—— 这行小字胜负两边
都有，一个模板同时覆盖赢和输；而且赢的画面在录像里没出现过，做了也没法验证。

`gym_picktip` / `gym_pickwait` 在同一行同一位置（文字互相替换），所以两者对
对方页面都有 0.7 左右的虚高，阈值统一 0.85 能分开（0.72 vs 0.97~1.00）。

选人页其实有**三个**状态，第三个「满 3 人未确定」只有 `gym_confirm`（红色确定
按钮，1280 下 (560,605)-(720,653)）认得出。全片 128 帧扫描：它只在 24.0s 那一帧
命中，其余所有页面都 < 0.85 —— 唯一的窗口，少认它就会把这一段判成未知页。

战斗为什么不能直接用竞技场的 `Fight_Once`：战斗循环本身就是同一份
`CombatFighter` 代码、同一批参数（`_test_v1233.py` 的 [8] 组逐项比对，只允许多
出 `gym_result_threshold` / `gym_result_roi` 两个键）。差别只在**退出条件** ——
竞技场靠 `win_text`/`lose_text` 判结算，而道馆结算页拿这两个模板实测只有
0.185 / 0.127，根本认不出。所以必须是独立节点：同一份逻辑 + 多一个识破道馆
结算页的判据。少了它，打完一局没人认结算，会一直空点到 duration 到期。
"""

from __future__ import annotations

import json
import time

from maa.context import Context
from maa.custom_action import CustomAction

from page_router import _grab_both, _match


PAGE_CN = {
    "fight": "战斗",
    "result": "整场结算",
    "pick": "选人页",
    "wait": "匹配/VS 展示",
    "page": "道馆挑战页",
    "home": "主城",
    "unknown": "未知页",
}


def _click(ctrl, xy, settle_ms=800, why="") -> None:
    """道馆专用的点击（日志前缀和竞技场分开，排查时一眼能分清）。"""
    print(f"[Gym] 点击{why} {int(xy[0])},{int(xy[1])}", flush=True)
    ctrl.post_click(int(xy[0]), int(xy[1])).wait()
    if settle_ms:
        time.sleep(settle_ms / 1000.0)


_CD_LOG = {}


def _cd_log(key, msg, every_s=5.0) -> None:
    """cooldown 分支的日志节流。

    Router 每 0.25 秒重判一次页面，而「点完匹配/选完人」这类 cooldown 长达
    25~40 秒 —— 不节流的话每轮都打一行，一次匹配能刷上百行把真正的日志埋掉。
    """
    now = time.time()
    if now - _CD_LOG.get(key, 0.0) >= every_s:
        _CD_LOG[key] = now
        print(msg, flush=True)


class GymRouter(CustomAction):
    """道馆流程的唯一判别点：截一张图 → 判页 → override_next 精确路由。

    custom_action_param（全部可省略）::

        {
            "fight_roi": [88,14,1104,38], "fight_threshold": 0.65,
            "result_roi": [500,590,280,100], "result_threshold": 0.85,
            "pick_roi": [520,600,260,100],  "pick_threshold": 0.85,
            "vsok_roi": [470,545,340,120],  "vsok_threshold": 0.85,
            "page_roi": [0,0,260,90],       "page_threshold": 0.85,
            "home_roi": [0,0,0,0],          "home_threshold": 0.85,
            "unknown_back_s": 45.0
        }

    判页顺序（不能随便调，理由见各分支注释）：
        战斗 > 整场结算 > 选人 > 匹配成功 > 道馆页 > 主城 > 未知
    """

    DEFAULTS = {
        "fight_roi": [88, 14, 1104, 38],
        "fight_threshold": 0.65,
        "result_roi": [500, 590, 280, 100],
        "result_threshold": 0.85,
        "pick_roi": [520, 600, 260, 100],
        "pick_threshold": 0.85,
        "vsok_roi": [470, 545, 340, 120],
        "vsok_threshold": 0.85,
        "page_roi": [0, 0, 260, 90],
        "page_threshold": 0.85,
        "home_roi": [0, 0, 0, 0],
        "home_threshold": 0.85,
        # 实测：点完「确定」→ 开打之间有连续 **25 秒** 认不出的画面
        # （VS 登场 15 秒倒数 → 战斗准备中 + 资源加载 → ROUND 1）。竞技场那边
        # 是 5 秒兜底，这里必须放宽到 45，否则会在 VS 中途点返回把对局退掉。
        "unknown_back_s": 45.0,
    }

    def run(self, context: Context, argv: CustomAction.RunArg) -> bool:
        param = dict(self.DEFAULTS)
        raw = argv.custom_action_param
        if raw:
            try:
                param.update(json.loads(raw))
            except ValueError:
                print(f"[Gym] 参数不是合法 JSON，用默认值: {raw!r}", flush=True)
        node = argv.node_name or "Gym_Router"
        ctrl = context.tasker.controller

        gray, color = _grab_both(ctrl)
        if gray is None:
            print("[Gym] 截图失败，本轮按未知页等待", flush=True)
            self._route(context, node, "Gym_Wait")
            return True

        kind = self._detect(gray, param)
        if kind == "unknown":
            target = self._unknown_step(param)
        else:
            self._unknown_since = None
            target = {
                # 必须是 Gym_Fight（不是竞技场的 Fight_Once）：只有 Gym_Fight 带
                # gym_result_threshold，战斗模块才会认道馆的「点击空白处关闭」结算页。
                # 走 Fight_Once 的话打完一局没人认结算，会一直空点满 duration（15 分钟）。
                "fight": "Gym_Fight",
                "result": "Gym_Result",
                "pick": "Gym_Pick",
                "page": "Gym_Page",
                "home": "Gym_Home",
                "wait": "Gym_Wait",
            }[kind]

        self._log(kind, target)
        self._route(context, node, target)
        return True

    # ------------------------------------------------------------------ 判页

    def _detect(self, gray, p) -> str:
        """按优先级逐页判别。顺序不能随便调（理由写在每条分支上）。"""
        # 1) 战斗最优先：3v3 多回合，任何误判都会导致停手/乱点。
        #    判据与竞技场共用 battle_hp1/2（HUD 完全一致，实测 0.96~1.00）。
        if (_match(gray, "battle_hp1", p["fight_roi"])[0] >= p["fight_threshold"]
                or _match(gray, "battle_hp2", p["fight_roi"])[0] >= p["fight_threshold"]):
            return "fight"

        # 2) 整场结算：底部「点击空白处关闭」。必须在选人之类之前 ——
        #    结算页背景是战斗画面，血条已消失，但页面上还有角色立绘，容易和
        #    选人页的立绘混淆。
        if _match(gray, "gym_close", p["result_roi"])[0] >= p["result_threshold"]:
            return "result"

        # 3) 选人页：底部提示条 / 「确定」按钮。三种状态都要认 ——
        #    未满 3 人是「请选择格斗家(X/3)」；满 3 人提示消失、原地长出红色
        #    「确定」；点过确定变成「等待对方确定中」。少认一个，那一段就会被
        #    判成未知页，25 秒后点返回把刚匹配到的对局退掉。
        if (_match(gray, "gym_picktip", p["pick_roi"])[0] >= p["pick_threshold"]
                or _match(gray, "gym_pickwait", p["pick_roi"])[0] >= p["pick_threshold"]
                or _match(gray, "gym_confirm", p["pick_roi"])[0] >= p["pick_threshold"]):
            return "pick"

        # 4) 匹配成功 VS：只等待，不点。放道馆页之前 —— 这个页面的背景是
        #    双方立绘，左上角已经没有道馆 logo，两个判据不会互相干扰。
        if _match(gray, "gym_vsok", p["vsok_roi"])[0] >= p["vsok_threshold"]:
            return "wait"

        # 5) 道馆挑战页：左上角「道馆挑战 KING OF FIGHTERS」横幅。
        #    实测道馆页 1.000 / 战斗 0.640 / 结算 0.557，阈值 0.85 余量 0.21。
        if _match(gray, "gym_title", p["page_roi"])[0] >= p["page_threshold"]:
            return "page"

        # 6) 主城：只能全屏找（图标位置随 UI 版本变），和竞技场同一个阈值。
        if _match(gray, "main_arena_btn", p["home_roi"])[0] >= p["home_threshold"]:
            return "home"

        return "unknown"

    def _unknown_step(self, p) -> str:
        """未知页：纯等待，一次都不点；连续够久才交给安全返回。

        道馆流程里落进未知页的都是正常过渡：匹配中、VS 倒计时（15~16 秒）、
        「战斗准备中」加载、结算后的角色展示。所以这里**必须**等到
        unknown_back_s（默认 25 秒），否则会在 VS 展示中途点返回，把刚匹配到的
        对局直接退掉。
        """
        now = time.time()
        if getattr(self, "_unknown_since", None) is None:
            self._unknown_since = now
            print("[Gym] 进入未知页，挂起等待（不做任何点击）", flush=True)
        waited = now - self._unknown_since
        limit = float(p.get("unknown_back_s", 25.0))
        if waited >= limit:
            print(f"[Gym] 未知页已持续 {waited:.0f}s（>{limit:.0f}s），交给安全返回",
                  flush=True)
            self._unknown_since = None
            return "Gym_Back"
        return "Gym_Wait"

    # ------------------------------------------------------------------ 路由

    def _route(self, context: Context, node: str, target: str) -> None:
        """让 node 的下一跳只走 target，并把 target 的回路改回 node。"""
        context.override_next(node, [target])
        if target == node:
            return
        if not context.override_pipeline({target: {"next": [node]}}):
            print(f"[Gym] 警告：改写 {target}.next 失败", flush=True)

    def _log(self, kind: str, target: str) -> None:
        now = time.time()
        changed = (getattr(self, "_last_kind", None) != kind
                   or getattr(self, "_last_target", None) != target)
        if changed or now - getattr(self, "_last_log", 0.0) >= 10.0:
            print(f"[Gym] 页面={PAGE_CN.get(kind, kind)} → {target}", flush=True)
            self._last_kind, self._last_target, self._last_log = kind, target, now


class GymHome(CustomAction):
    """主城 → 点右上角「道馆挑战」鸟居图标。

    图标是第二排第 6 个（更新特惠/封印大蛇/挑战豪礼/成长助礼/逃出生天/**道馆挑战**/
    招募），1280 基准 (1153, 99)。整行图标随 UI 版本会平移，所以点之前用
    `main_arena_btn` 复核「这确实是主城」，再点固定坐标 —— 和竞技场点「KO 竞技」
    是同一个套路。
    """

    DEFAULTS = {
        "roi": [0, 0, 0, 0],
        "threshold": 0.85,
        "target": [1153, 99],
        "settle_ms": 1200,
        "cooldown_s": 5.0,
    }

    def run(self, context: Context, argv: CustomAction.RunArg) -> bool:
        param = dict(self.DEFAULTS)
        raw = argv.custom_action_param
        if raw:
            try:
                param.update(json.loads(raw))
            except ValueError:
                pass
        ctrl = context.tasker.controller
        since = time.time() - getattr(GymHome, "_clicked_at", 0.0)
        if since < float(param["cooldown_s"]):
            return True

        gray, _ = _grab_both(ctrl)
        if gray is None:
            print("[Gym] 主城截图失败，本轮不点", flush=True)
            return True
        score, _ = _match(gray, "main_arena_btn", param["roi"])
        if score < float(param["threshold"]):
            print(f"[Gym] 不在主城(score={score:.2f})，本轮不点", flush=True)
            return True
        x, y = (int(v) for v in param["target"])
        _click(ctrl, (x, y), int(param["settle_ms"]),
               f"主城「道馆挑战」鸟居(score={score:.2f})")
        GymHome._clicked_at = time.time()
        return True


class GymPage(CustomAction):
    """道馆挑战页 → 选道馆（可选）→ 点「匹配」。

    两个坑：

    1. **点完匹配页面不变**（匹配中还是这一页）。Router 每 0.3 秒判一次 page，
       不拦的话会把刚发起的匹配连点几十下。所以记 `_clicked_at`，cooldown 内
       只等待，不再点。
    2. **点道馆会切换右侧详情**，但点已选中的道馆不会取消。所以先点目标道馆
       （让详情切过去），再点匹配，顺序不能反。
    """

    DEFAULTS = {
        "roi": [0, 0, 260, 90],
        "threshold": 0.85,
        "dojo_xy": [177, 373],       # 樱花道馆 (266,560)@1920 → 1280
        "match_xy": [1161, 621],     # 「匹配」按钮 (1742,931)@1920 → 1280
        "dojo_settle_ms": 700,
        "match_settle_ms": 1500,
        "cooldown_s": 40.0,
    }

    def run(self, context: Context, argv: CustomAction.RunArg) -> bool:
        param = dict(self.DEFAULTS)
        raw = argv.custom_action_param
        if raw:
            try:
                param.update(json.loads(raw))
            except ValueError:
                pass
        ctrl = context.tasker.controller

        since = time.time() - getattr(GymPage, "_clicked_at", 0.0)
        if since < float(param["cooldown_s"]):
            _cd_log("page_cd",
                    f"[Gym] {since:.0f}s 前刚点过匹配，等待中（不再点）")
            return True

        gray, _ = _grab_both(ctrl)
        if gray is None:
            print("[Gym] 道馆页截图失败，本轮不点", flush=True)
            return True
        score, _ = _match(gray, "gym_title", param["roi"])
        if score < float(param["threshold"]):
            print(f"[Gym] 不在道馆页(score={score:.2f})，本轮不点", flush=True)
            return True

        dojo = param.get("dojo_xy")
        if dojo and len(dojo) >= 2 and (int(dojo[0]) or int(dojo[1])):
            _click(ctrl, (int(dojo[0]), int(dojo[1])),
                   int(param["dojo_settle_ms"]), "选道馆")
        else:
            print("[Gym] 未指定道馆，沿用页面当前选中的", flush=True)

        mx, my = (int(v) for v in param["match_xy"])
        _click(ctrl, (mx, my), int(param["match_settle_ms"]), "「匹配」")
        GymPage._clicked_at = time.time()
        return True


class GymPick(CustomAction):
    """选人页 → 点前 3 格 → 点「确定」。

    三个阶段（录像 18~26s 逐帧实测）：

        0/3、1/3、2/3   底部提示「温馨提示：请选择格斗家 (X/3)」，可点立绘
        3/3 未确定      提示消失，底部中央出现红色「确定」按钮（gym_confirm）
        已确定          提示变成「等待对方确定中」（gym_pickwait），等对手

    **满 3 个不会自动锁定，必须点「确定」**。之前误以为会自动锁定，导致
    点完 3 格后傻等「等待对方确定中」等到超时，然后又重进这一页把角色 toggle 掉。

    防 toggle：点每格之前先看那一格有没有金色勾环（gym_check）。有就跳过 ——
    这样即使上一次点击没生效（转场丢帧）重跑一轮，也只补点缺的那格。
    立绘列中心实测 x = 96 + 152k（勾环在 f023.5 上量到 552/856/1008），y = 185。
    """

    DEFAULTS = {
        "slots": [[96, 185], [248, 185], [400, 185]],   # 8 列立绘中心的第 1~3 列
        "tip_roi": [520, 600, 260, 100],
        "tip_threshold": 0.85,
        "check_pad": 40,                # 勾环 73x73，ROI 取 80x80
        "check_threshold": 0.88,        # 勾环实测 0.97~1.00，未选 ≤0.07
        "step_settle_ms": 450,
        "confirm_settle_ms": 700,
        "wait_s": 8.0,
        "poll_ms": 400,
        "cooldown_s": 25.0,
    }

    def run(self, context: Context, argv: CustomAction.RunArg) -> bool:
        param = dict(self.DEFAULTS)
        raw = argv.custom_action_param
        if raw:
            try:
                param.update(json.loads(raw))
            except ValueError:
                pass
        ctrl = context.tasker.controller

        if time.time() - getattr(GymPick, "_clicked_at", 0.0) < float(param["cooldown_s"]):
            _cd_log("pick_cd", "[Gym] 刚选过一轮，本轮只等不点")
            return True

        gray, _ = _grab_both(ctrl)
        if gray is None:
            print("[Gym] 选人页截图失败，本轮不点", flush=True)
            return True

        # ① 已经点过确定，等对手 → 一次都别点
        if self._waiting(gray, param):
            _cd_log("pick_wait", "[Gym] 已确定，等对方确定中（不点）")
            return True

        # ② 3 人已满但还没确定 → 直接点确定（上次点完没等到，重进这一页）
        if self._confirm_xy(gray, param)[0] >= 0:
            self._confirm(ctrl, gray, param, "已满 3 人")
            return True

        # ③ 还在选：逐格点，已勾的跳过
        for i, xy in enumerate(param["slots"], 1):
            if self._checked(gray, param, xy):
                print(f"[Gym] 第 {i} 格已有勾，跳过（防止 toggle 掉）", flush=True)
                continue
            _click(ctrl, (int(xy[0]), int(xy[1])),
                   int(param["step_settle_ms"]), f"选第 {i} 格")
            gray, _ = _grab_both(ctrl)
            if gray is None:
                break
            if self._confirm_xy(gray, param)[0] >= 0:
                print("[Gym] 已满 3 人", flush=True)
                break

        GymPick._clicked_at = time.time()
        if gray is not None and self._confirm_xy(gray, param)[0] >= 0:
            self._confirm(ctrl, gray, param, "选完 3 格")
        else:
            print("[Gym] [X] 没看到「确定」按钮（3 人未满或画面未刷新），"
                  "交给下一轮补点", flush=True)
        return True

    def _confirm(self, ctrl, gray, param, why) -> None:
        """点「确定」并等「等待对方确定中」出现。"""
        score, xy = self._confirm_xy(gray, param)
        _click(ctrl, xy, int(param["confirm_settle_ms"]),
               f"「确定」(score={score:.2f}，{why})")
        GymPick._clicked_at = time.time()
        if self._wait_done(ctrl, param):
            print("[Gym] [OK] 已确定，等开打", flush=True)
        else:
            print(f"[Gym] [X] {param['wait_s']:.0f}s 内没等到「等待对方确定中」",
                  flush=True)

    def _waiting(self, gray, param) -> bool:
        return _match(gray, "gym_pickwait", param["tip_roi"])[0] >= float(param["tip_threshold"])

    def _confirm_xy(self, gray, param):
        """「确定」按钮位置。没出现返回 (-1, (0,0))。"""
        score, xy = _match(gray, "gym_confirm", param["tip_roi"])
        if score < float(param["tip_threshold"]):
            return -1.0, (0, 0)
        return score, xy

    def _checked(self, gray, param, xy) -> bool:
        """该列立绘是否已有金色勾环。"""
        pad = int(param["check_pad"])
        roi = [int(xy[0]) - pad, int(xy[1]) - pad, pad * 2, pad * 2]
        return _match(gray, "gym_check", roi)[0] >= float(param["check_threshold"])

    def _wait_done(self, ctrl, param) -> bool:
        deadline = time.time() + float(param["wait_s"])
        while time.time() < deadline:
            gray, _ = _grab_both(ctrl)
            if gray is not None and self._waiting(gray, param):
                return True
            time.sleep(float(param["poll_ms"]) / 1000.0)
        return False


class GymResult(CustomAction):
    """整场结算 → 点空白处关闭。

    页面上只有「举报」一个按钮（右下），任何空白处都能关。点左侧中部
    (200, 467) 是因为那里离举报键和对手信息框都远，不会误触。

    **点完必须等结算层真的消失再回 Router**（实测关闭动画 1.5~2.2 秒）。
    不等的话会出一个很难查的问题：关闭动画播到一半时，底下露出的战斗画面
    还带着血条，Router 据此判成「战斗」→ 进 Gym_Fight → 战斗模块发现血条
    又没了、等 6 秒才退出，白等一轮。所以这里轮询到 `gym_close` 消失为止
    （最多 4 秒），再多等 0.8 秒让角色展示起来，然后才交回 Router。
    """

    DEFAULTS = {
        "roi": [500, 590, 280, 100],
        "threshold": 0.85,
        "target": [200, 467],
        "settle_ms": 800,
        "vanish_s": 4.0,
        "poll_ms": 300,
        "tail_s": 0.8,
        "cooldown_s": 6.0,
    }

    def run(self, context: Context, argv: CustomAction.RunArg) -> bool:
        param = dict(self.DEFAULTS)
        raw = argv.custom_action_param
        if raw:
            try:
                param.update(json.loads(raw))
            except ValueError:
                pass
        ctrl = context.tasker.controller

        if time.time() - getattr(GymResult, "_clicked_at", 0.0) < float(param["cooldown_s"]):
            return True

        gray, _ = _grab_both(ctrl)
        if gray is None:
            print("[Gym] 结算页截图失败，本轮不点", flush=True)
            return True
        score, _ = _match(gray, "gym_close", param["roi"])
        if score < float(param["threshold"]):
            print(f"[Gym] 不在结算页(score={score:.2f})，本轮不点", flush=True)
            return True
        x, y = (int(v) for v in param["target"])
        _click(ctrl, (x, y), int(param["settle_ms"]),
               f"结算页空白处关闭(score={score:.2f})")
        GymResult._clicked_at = time.time()

        if self._wait_vanish(ctrl, param):
            time.sleep(float(param["tail_s"]))
            print("[Gym] [OK] 结算层已关闭，等角色展示播完", flush=True)
        else:
            print(f"[Gym] [X] {param['vanish_s']:.0f}s 内结算层没消失，交给 Router 重判",
                  flush=True)
        return True

    def _wait_vanish(self, ctrl, param) -> bool:
        """轮询到「点击空白处关闭」这行字消失（= 结算层关掉了）。"""
        deadline = time.time() + float(param["vanish_s"])
        while time.time() < deadline:
            time.sleep(float(param["poll_ms"]) / 1000.0)
            gray, _ = _grab_both(ctrl)
            if gray is None:
                continue
            if _match(gray, "gym_close", param["roi"])[0] < float(param["threshold"]):
                return True
        return False
