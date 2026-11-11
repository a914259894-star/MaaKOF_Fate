"""单入口页面路由器（v1.2.30）。

== 为什么重写 ==
旧 arena.json 是「每个节点各自写一串 next」：同一批候选节点被复制了十几份，
而且 next 里混着**点击**节点 —— `Arena_Enter_1v1` 只用 main_arena_btn、阈值
0.7；`Arena_Enter_Master` 三个模板全部 0.7；`Arena_MasterMatch` 干脆是完全
盲点 (1135,634)。只要某个宽泛模板在别的页面上误命中，就会在**错误的页面**
点一下。现场表现就是「1v1 路上误点其他界面好几次」。

现在改成星形结构：
    Arena_Enter_1v1 / Arena_Enter_Master（入口，只选模式）
                    ↓
              Arena_Router（唯一判别点）
      截一张图 → 按固定优先级判页 → 命中才进那一页的流程
      流程跑完 next 回到 Router → 再判一次 → 再走一次

判别优先级（用户 2026-11-06 指定：战斗最优先、未知界面等待）：
    战斗 > KOF段位提升 > 胜负结算 > 出手顺序 > 选人 > 大师团页 > 竞技场主页 > 主城 > 未知

判不出来（未知页）就**挂起等待**，绝不盲点：加载页 / VS 入场 / 一穿三 / 至尊赛播报
这些都算未知页，一直等到画面变成可识别页面为止，期间一次都不点。

等待**不是卡死** —— Router 每轮（`Arena_UnknownWait.post_delay` = 0.25s）都会重新
截屏重新判，页面一变立刻路由出去。唯一需要的兜底是「万一永远认不出来」：

    · 连续未知满 `unknown_back_s`（默认 5s）→ 交 ArenaSafeBack 点一次返回，
      然后立刻回 Router 重判；还是认不出就再等 5s 再点一次。
      这就是「连续 n 秒一次都认不出，才点一次返回」。
    · 想只在「真看到返回箭头」时才点（更保守），把 `unknown_need_arrow` 打开；
      此时 `unknown_back_hard_s` 是它的硬上限，默认也是 5s。

== 为什么判据全部自己算 ==
`context.run_recognition` 一次 IPC 400~800ms（见 MEMORY 的性能铁律），而 Router
每轮都要跑。整套判据在 1280 基准下实测约 25~45ms（都限定了 ROI，只有 pick /
settle / main_arena_btn 需要全屏），比一次 IPC 还快一个数量级。

== 为什么路由目标必须是 DirectHit 节点 ==
override_next 只改「候选列表」，下一轮仍要重新识别一次才执行。目标若是
TemplateMatch 节点，重新截图时画面已变（转场/淡入）就会识别失败；该节点不
执行、它的 next 又没人管 → 整个 task 直接结束，而外壳只会打一行
「任务已全部完成」。所以这里路由到的每个节点都是 DirectHit + Custom，
动作内部先复核页面，页面不符就什么都不做、直接回 Router。
"""

from __future__ import annotations

import json
import os
import time

import cv2

from maa.context import Context
from maa.custom_action import CustomAction

from arena_actions import ArenaPick, _ARENA_DIR, imread_cn

_TPL: dict[str, "cv2.Mat | None"] = {}


def _tpl(rel: str):
    """加载 image/arena 下的灰度模板（带缓存）。缺模板时只打一行，返回 None。"""
    if rel not in _TPL:
        t = imread_cn(os.path.join(_ARENA_DIR, rel + ".png"), cv2.IMREAD_GRAYSCALE)
        if t is None:
            print(f"[Router] 缺少模板 {rel}.png", flush=True)
        _TPL[rel] = t
    return _TPL[rel]


def _match(gray, rel: str, roi) -> tuple[float, tuple[int, int]]:
    """在 roi 内匹配模板，返回 (最高分, 该处中心的 1280 基准坐标)。

    roi 的 w/h <= 0 时按全屏处理。ROI 比模板还小时返回 -1（不能误判成命中）。
    """
    t = _tpl(rel)
    if t is None:
        return -1.0, (0, 0)
    x, y, w, h = (int(v) for v in roi)
    H, W = gray.shape[:2]
    if w <= 0 or h <= 0:
        x, y, w, h = 0, 0, W, H
    x0, y0 = max(0, x), max(0, y)
    patch = gray[y0:min(H, y + h), x0:min(W, x + w)]
    th, tw = t.shape[:2]
    if patch.shape[0] < th or patch.shape[1] < tw:
        return -1.0, (0, 0)
    res = cv2.matchTemplate(patch, t, cv2.TM_CCOEFF_NORMED)
    _, mx, _, mloc = cv2.minMaxLoc(res)
    return float(mx), (x0 + mloc[0] + tw // 2, y0 + mloc[1] + th // 2)


def _grab_both(ctrl):
    """截一张图，同时给出 (1280 基准灰度, 1280 基准彩色)。

    为什么要彩色：未知页要靠「红色返回箭头」做守卫，灰度图取不到红色。
    不要为了彩色再截一次 —— 一次 IPC 就是 100ms 级别。

    用 `img.size` 判断空图，和 ArenaSafeBack / ArenaPick 的口径保持一致：
    `if img is None` 挡不住「0 字节的帧」，那会让 resize 抛异常。
    """
    ctrl.post_screencap().wait()
    img = ctrl.cached_image
    if img is None or not getattr(img, "size", 0):
        return None, None
    if img.ndim == 2:
        img = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
    sc = img.shape[1] / 1280.0
    if abs(sc - 1.0) > 0.01:
        img = cv2.resize(img, (1280, int(img.shape[0] / sc)), interpolation=cv2.INTER_AREA)
    return cv2.cvtColor(img, cv2.COLOR_BGR2GRAY), img


def _red_back_xy(color, x0=1040, x1=1280, y0=0, y1=56):
    """右上角「红色返回箭头」中心（1280 基准），认不到返回 None。

    和 `CombatFighter._find_red_back` 的唯一区别是 ROI：那边用 x>=900 / y<95，
    y 放到 95 是为了兼顾各种子页面，但也把战斗 HUD 里 y=60~86 的红色元素
    （血条、技能光圈、主城图标）一起捞进来了。Router 只在未知页用它，口径必须是
    **宁可漏判也不能误判**：漏判 = 继续等（无害），误判 = 在正常转场上白点一下。

    录像 36 帧实测（debug/_vid3，1200 帧抽帧）：
        真返回箭头   竞技场主页 / 至尊赛 (1242~1244, 28)、装备页 (1110, 33)
        误命中       战斗 HUD (1213~1264, 60~86)、主城 (1244, 86)、加载页 (1267, 85)
    按 y<56 收紧后：真箭头 y=28~33（余量 23px）全部保留，误命中全部切掉。
    """
    try:
        h, w = color.shape[:2]
        if w != 1280:
            color = cv2.resize(color, (1280, max(1, int(round(h * 1280.0 / w)))),
                               interpolation=cv2.INTER_AREA)
        roi = color[int(y0):int(y1), int(x0):int(x1)]
        if roi.size == 0:
            return None
        b = roi[:, :, 0].astype("int16")
        g = roi[:, :, 1].astype("int16")
        r = roi[:, :, 2].astype("int16")
        red = (r - cv2.max(g, b)).clip(0, 255).astype("uint8")
        mask = (red > 55).astype("uint8") * 255
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE,
                                cv2.getStructuringElement(cv2.MORPH_RECT, (5, 5)))
        cnts, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        best = None
        for c in cnts:
            bx, by, bw, bh = cv2.boundingRect(c)
            if bw * bh < 250 or bw < 14 or bh < 10 or bw > 130 or bh > 95:
                continue
            cx = bx + bw / 2.0 + x0
            if best is None or cx > best[0]:
                best = (cx, by + bh / 2.0)
        if best is None:
            return None
        return int(round(best[0])), int(round(best[1]))
    except Exception:
        return None


def _click(ctrl, xy, settle_ms=800, why="") -> None:
    print(f"[Router] 点击{why} {int(xy[0])},{int(xy[1])}", flush=True)
    ctrl.post_click(int(xy[0]), int(xy[1])).wait()
    if settle_ms:
        time.sleep(settle_ms / 1000.0)


class ArenaRouter(CustomAction):
    """唯一入口：判页 → 路由。

    custom_action_param（全部可省略，默认值见 DEFAULTS）：
    {
        "mode": "1v1",            // 1v1 | master：决定竞技场主页点哪个模式按钮
        "battle_roi": [88,14,1104,38], "battle_threshold": 0.65,
        "rank_up_roi": [300,60,720,130], "rank_up_threshold": 0.8,
        "settle_roi": [410,70,430,130], "settle_threshold": 0.85,
        "pick_roi": [0,0,0,0], "pick_threshold": 0.82,       // 0 = 全屏
        "pick_tip_roi": [520,645,240,70], "pick_tip_threshold": 0.8,  // 底部「温馨提示」
        "order_roi": [0,0,1280,160], "order_threshold": 0.85, // 「点击格斗家确定顺序」页
        "team_roi": [380,20,560,90], "team_threshold": 0.9,
        "arena_roi": [0,0,240,90], "arena_threshold": 0.85,
        "home_roi": [0,0,0,0], "home_threshold": 0.85,
        "mode_1v1_xy": [621,196],  // 竞技场主页「1V1」入口（实机标定，模板只做守卫）
        "mode_master_template": "mode_master",
        "team_slots": [[126,640],...],   // 大师团页 5 个卡位（缺卡才补人）
        "team_card_threshold": 0.85,
        "match_cooldown_s": 20.0,  // 点过匹配后多久内不再点（等选人页）
        "unknown_back_s": 5.0,     // 连续未知多少秒才点一次返回（加载页等就一直等）
        "unknown_back_hard_s": 5.0, // 仅 unknown_need_arrow=true 时生效的硬上限
        "unknown_need_arrow": false, // true = 必须真看到返回箭头才点（更保守）
        "click_settle_s": 0.9      // 点完等页面反应的秒数
    }
    """

    DEFAULTS = {
        "mode": "1v1",
        "battle_roi": [88, 14, 1104, 38],
        "battle_threshold": 0.65,
        "rank_up_roi": [300, 60, 720, 130],
        "rank_up_threshold": 0.8,
        "settle_roi": [410, 70, 430, 130],
        "settle_threshold": 0.85,
        "pick_roi": [0, 0, 0, 0],
        "pick_threshold": 0.82,
        # 「点击格斗家确定顺序」页：页顶/右上橙色标题，正例 0.999、其它页 ≤0.58。
        "order_roi": [0, 0, 1280, 160],
        "order_threshold": 0.85,
        "pick_tip_roi": [520, 645, 240, 70],
        "pick_tip_threshold": 0.8,
        "team_roi": [380, 20, 560, 90],
        "team_threshold": 0.9,
        "arena_roi": [0, 0, 240, 90],
        "arena_threshold": 0.85,
        "home_roi": [0, 0, 0, 0],
        "home_threshold": 0.85,
        "mode_1v1_xy": [621, 196],
        "mode_master_template": "mode_master",
        "team_card": "master_card_ssr",
        "team_card_threshold": 0.72,
        "team_card_half_w": 100,
        "team_card_half_h": 56,
        "team_slots": [[126, 640], [262, 640], [400, 640], [537, 640], [674, 640]],
        "match_cooldown_s": 20.0,
        "unknown_back_s": 5.0,
        "unknown_back_hard_s": 5.0,
        "unknown_need_arrow": False,
        "click_settle_s": 0.9,
    }

    PAGE_CN = {
        "fight": "战斗",
        "rank_up": "KOF段位提升",
        "settle": "胜负结算",
        "pick": "选人",
        "order": "出手顺序页",
        "team": "大师团页",
        "arena": "竞技场主页",
        "home": "主城",
        "unknown": "未知页",
    }

    def run(self, context: Context, argv: CustomAction.RunArg) -> bool:
        param = dict(self.DEFAULTS)
        raw = argv.custom_action_param
        if raw:
            try:
                param.update(json.loads(raw))
            except ValueError:
                print(f"[Router] 参数不是合法 JSON，用默认值: {raw!r}", flush=True)
        mode = str(param.get("mode", "1v1")).strip().lower()
        node = argv.node_name or "Arena_Router"
        ctrl = context.tasker.controller

        gray, color = _grab_both(ctrl)
        if gray is None:
            print("[Router] 截图失败，本轮按未知页等待", flush=True)
            self._route(context, node, "Arena_UnknownWait")
            return True

        kind = self._detect(gray, param, mode)

        if kind == "unknown":
            target = self._unknown_step(color, param)
        elif kind == "arena":
            self._click_arena_entry(ctrl, gray, param, mode)
            target = node
        elif kind == "home":
            score, xy = _match(gray, "main_arena_btn", param["home_roi"])
            _click(ctrl, xy, int(float(param["click_settle_s"]) * 1000),
                   f"主城「KO竞技」入口(score={score:.2f})")
            target = node
            self._unknown_since = None
        elif kind == "team":
            # 大师团页要再分一步：队伍缺人先补人，人齐才点「匹配战斗」。
            # 不分流的话会「Router 判 team → TeamFix → 回 Router → 还是 team」死循环。
            target = self._team_step(gray, param)
            self._unknown_since = None
        else:
            target = {
                "fight": "Fight_Once",
                "rank_up": "Arena_RankUp",
                "settle": "Arena_SettleOk",
                "pick": "Arena_MasterPick" if mode == "master" else "Arena_Pick",
                # 顺序页只等不点，和未知页走同一个等待节点；区别是它会清零
                # unknown 计时，所以顺序页那 8 秒不会把兜底推上去点返回。
                "order": "Arena_UnknownWait",
            }[kind]
            self._unknown_since = None

        self._log(kind, target)
        self._route(context, node, target)
        return True

    # ------------------------------------------------------------------ 判页

    def _detect(self, gray, param, mode: str) -> str:
        """按优先级逐页判别。顺序不能随便调（理由见各条注释）。"""
        # 1) 战斗最优先：顶部血条段。实测战斗 0.962~1.000 / 其余全部 <=0.552。
        #    放在最前面，是因为战斗中出现任何误判都会直接导致停手/乱点。
        if (_match(gray, "battle_hp1", param["battle_roi"])[0] >= param["battle_threshold"]
                or _match(gray, "battle_hp2", param["battle_roi"])[0] >= param["battle_threshold"]):
            return "fight"

        # 2) KOF 段位提升弹窗：结算点完「确定」后才弹，确认键不在任何盲点上。
        if _match(gray, "rank_up_title", param["rank_up_roi"])[0] >= param["rank_up_threshold"]:
            return "rank_up"

        # 3) 胜负结算页。实测 ROI 内 0.988~0.989 / 其它页 <=0.245。
        roi, th = param["settle_roi"], param["settle_threshold"]
        if (_match(gray, "win_text", roi)[0] >= th
                or _match(gray, "lose_text", roi)[0] >= th):
            return "settle"

        # 4) 「点击格斗家确定顺序」页：选人点完「确定」之后必然经过，1v1 和
        #    大师团都有。**只等不点**（游戏 10 秒倒计时归零会按默认顺序确定），
        #    所以路由到 Arena_UnknownWait。排在选人之前是因为选人分支会点立绘，
        #    在顺序页点立绘会改掉出场顺序；而误判成 order 只是多等一轮
        #    （选人页拿 order_confirm 实测只有 0.49）。
        if _match(gray, "order_confirm", param["order_roi"])[0] >= param["order_threshold"]:
            return "order"

        # 5) 选人页。1v1 用「近期使用」标签或「等待」提示；大师选人页只有「等待」。
        if self._pick_hit(gray, param, mode):
            return "pick"

        # 6) 大师团/大师竞技页：顶部横幅。实测 0.92~1.00，阈值 0.9 有余量。
        #    放在竞技场主页之前：这两个页面的 arena_title 分数也很高（大师页是
        #    独立的 master_title），先判 master 才能正确进「匹配战斗」那条线。
        if _match(gray, "master_title", param["team_roi"])[0] >= param["team_threshold"]:
            return "team"

        # 7) 1v1 竞技场主页。ROI 化后 0.36ms，实测 0.991~0.994 / 至尊赛 0.565。
        if _match(gray, "arena_title", param["arena_roi"])[0] >= param["arena_threshold"]:
            return "arena"

        # 8) 主城：只能全屏找（图标位置随 UI 版本变），阈值 0.85 比旧节点的 0.7 严。
        if _match(gray, "main_arena_btn", param["home_roi"])[0] >= param["home_threshold"]:
            return "home"

        return "unknown"

    @staticmethod
    def _pick_hit(gray, param, mode: str) -> bool:
        """是不是选人页。

        三条判据，第一条是主力：底部橙色「温馨提示：请选择格斗家(0/3)」
        （`master_pick_tip`，ROI [520,645,240,70]）。它在 1v1 和大师选人页都有，
        而且**角色列表滚动后依然在** —— 实测滚动帧 0.884、未滚动帧 0.99，
        结算页 0.383 / 战斗帧 0.26~0.34 / 大师团页 0.23，阈值 0.8 两侧余量 0.08 / 0.42。

        为什么需要它：只靠「近期使用」标签（pick_recent）和「等待」倒计时（pick_wait）
        会漏掉**已经滚动过的选人页** —— 现场帧 `Arena_Pick_2026.09.15-19.12.09.994`
        两个都只有 0.586 / 0.772，会判成未知页白等 25 秒。注意模板名叫
        master_pick_tip 是历史原因，它本质是「选人页底部提示条」，不是大师专属。
        """
        tip_roi = param.get("pick_tip_roi", [520, 645, 240, 70])
        tip_th = float(param.get("pick_tip_threshold", 0.8))
        if _match(gray, "master_pick_tip", tip_roi)[0] >= tip_th:
            return True
        roi, th = param["pick_roi"], param["pick_threshold"]
        if mode == "master":
            # 大师选人页只有「等待」提示；1v1 的「近期使用」在这页不存在。
            return _match(gray, "pick_wait", roi)[0] >= th
        return (_match(gray, "pick_recent", roi)[0] >= th
                or _match(gray, "pick_wait", roi)[0] >= th)

    # ------------------------------------------------------------ 简单页处理

    def _click_arena_entry(self, ctrl, gray, param, mode: str) -> None:
        """竞技场主页：按任务模式点「1V1」或「大师」入口。

        两个入口都先要求模板命中（守卫），命中才点 —— 这是和旧
        `Arena_Enter_1v1`（只看 main_arena_btn、阈值 0.7 就点）最大的区别。
        """
        settle = int(float(param["click_settle_s"]) * 1000)
        if mode == "master":
            rel = str(param.get("mode_master_template", "mode_master"))
            score, xy = _match(gray, rel, (0, 0, 0, 0))
            if score >= 0.7:
                _click(ctrl, xy, settle, f"「大师竞技」入口(score={score:.2f})")
            else:
                print(f"[Router] 竞技场主页没找到「大师」入口(score={score:.2f})，本轮等待",
                      flush=True)
            return
        score, _ = _match(gray, "mode_1v1", (0, 0, 0, 0))
        if score >= 0.7:
            # 点固定坐标而不是模板中心：模板 267x44 是整条横幅，中心可能压在字上，
            # 旧节点实测 (621,196) 稳定命中。
            _click(ctrl, param["mode_1v1_xy"], settle, f"「1V1」入口(score={score:.2f})")
        else:
            print(f"[Router] 竞技场主页没找到「1V1」入口(score={score:.2f})，本轮等待",
                  flush=True)

    def _team_step(self, gray, param) -> str:
        """大师团页再分一步：缺人先补人，人齐才去点「匹配战斗」。

        判据和 MasterTeamFix 一致 —— 「卡位空了」才是真掉人（角色卡整张消失），
        不是「没勾」。实测有卡 0.92~1.00 / 空白 0.20~0.65，阈值 0.85。

        阈值这里取 0.72 而不是 TeamFix 的 0.85：现场帧 `debug/_match_click.png`
        第 3 个卡位只有 0.776（角色头像把 SSR 标签挡了一半），队伍明明是满的。
        **宁可把「有卡」判成满员（直接去匹配），也不要把满员判成缺人** ——
        后者会走进 TeamFix 把一个已上阵的角色点下阵，代价大得多。

        为什么必须在 Router 里分：Router 判页只认页面，认不出「这一页现在该补人
        还是该匹配」。不分的话 TeamFix 跑完回 Router 又判成 team → 又进 TeamFix，
        永远轮不到 Arena_MasterMatch，大师赛一局都开不起来。
        """
        card = param.get("team_card", "master_card_ssr")
        card_th = float(param.get("team_card_threshold", 0.72))
        half_w = int(param.get("team_card_half_w", 100))
        half_h = int(param.get("team_card_half_h", 56))
        slots = param.get("team_slots", [[126, 640], [262, 640], [400, 640],
                                         [537, 640], [674, 640]])
        empty = []
        for cx, cy in slots:
            score, _ = _match(gray, card, (int(cx) - half_w, int(cy) - half_h,
                                           half_w * 2, half_h * 2))
            if score < card_th:
                empty.append((int(cx), int(cy), round(score, 3)))
        if empty:
            print(f"[Router] 大师团页缺 {len(empty)} 个卡位 → Arena_TeamFix {empty}", flush=True)
            return "Arena_TeamFix"
        # 刚点过「匹配战斗」就安静等 —— 这一页在匹配成功前长得一模一样，
        # 不拦的话 Router 每 300ms 判一次 team，就会把刚发起的匹配连点几十下。
        since = time.time() - getattr(ArenaRouter, "_match_clicked_at", 0.0)
        cool = float(param.get("match_cooldown_s", 20.0))
        if since < cool:
            print(f"[Router] 刚点过匹配战斗（{since:.0f}s 前），等待选人页", flush=True)
            return "Arena_UnknownWait"
        return "Arena_MasterMatch"

    def _unknown_step(self, color, param) -> str:
        """未知页：挂起等待，直到画面变成可识别页面；只有「一直认不出」才点一次返回。

        未知页包含正常过渡：加载页、VS 入场、一穿三、至尊赛播报。它们的正确处置
        就是**等**（每 0.25s 重判一次，页面一变立刻路由），一次都不点。

        唯一的兜底是防死循环：连续未知满 `unknown_back_s`（默认 5s）就交
        ArenaSafeBack 点一次返回，然后马上回 Router 重判；还认不出就再等 5s
        再点一次。表现为「连续 n 秒一次都认不出 → 点一次返回」。

        `unknown_need_arrow=true`（默认关）是更保守的变体：5s 后必须先在本帧
        看到右上角红色返回箭头才点，看不到就一直等，直到 `unknown_back_hard_s`。
        默认不用它 —— 加载页最长 5~7s，开着会让每局在加载末尾多点一次右上角。
        """
        now = time.time()
        if getattr(self, "_unknown_since", None) is None:
            self._unknown_since = now
            print("[Router] 进入未知页，挂起等待（不做任何点击）", flush=True)
        waited = now - self._unknown_since
        limit = float(param.get("unknown_back_s", 5.0))
        hard = float(param.get("unknown_back_hard_s", limit))
        need_arrow = bool(param.get("unknown_need_arrow", False))

        if waited >= limit:
            arrow = _red_back_xy(color) if (need_arrow and color is not None) else None
            if arrow is not None or not need_arrow or waited >= hard:
                if arrow is not None:
                    why = f"右上角有返回箭头 {arrow}"
                elif not need_arrow:
                    why = f"连续 {waited:.1f}s 认不出"
                else:
                    why = f"认不出箭头且已到硬上限 {hard:.0f}s"
                print(f"[Router] 未知页{why} → Arena_UnknownBack", flush=True)
                self._unknown_since = None
                return "Arena_UnknownBack"
        return "Arena_UnknownWait"

    # ---------------------------------------------------------------- 路由

    def _route(self, context: Context, node: str, target: str) -> None:
        """让 node 的下一跳只走 target，并把 target 的回路改回 node。

        改回路是必须的：大师模式和 1v1 用的是两个 Router 节点，子节点在 json 里
        默认只写了 Arena_Router（1v1）。不覆盖的话大师赛打完一局会跑到 1v1 的
        Router 上，下一页就会被点成 1V1 入口。
        """
        context.override_next(node, [target])
        if target == node:
            return
        if not context.override_pipeline({target: {"next": [node]}}):
            print(f"[Router] 警告：改写 {target}.next 失败", flush=True)

    def _log(self, kind: str, target: str) -> None:
        """只在页面/目标变化时打一行；另外每 10s 补一次心跳便于对日志。"""
        now = time.time()
        changed = (getattr(self, "_last_kind", None) != kind
                   or getattr(self, "_last_target", None) != target)
        if changed or now - getattr(self, "_last_log", 0.0) >= 10.0:
            cn = self.PAGE_CN.get(kind, kind)
            print(f"[Router] 页面={cn} → {target}", flush=True)
            self._last_kind, self._last_target, self._last_log = kind, target, now


class ArenaSettleOk(CustomAction):
    """结算页点「确定」（v1.2.30，DirectHit 版）。

    判定与旧 `Arena_SettleOk`（TemplateMatch + Click）一致：全屏找
    settle_confirm（174x58，阈值 0.8），命中才点。改成 Custom 的唯一原因是
    Router 用 override_next 路由，目标节点必须是 DirectHit —— 见模块头注释。

    多个候选时点分数最高的那个（旧节点是 order_by: Score，语义接近）。
    """

    def run(self, context: Context, argv: CustomAction.RunArg) -> bool:
        param = json.loads(argv.custom_action_param) if argv.custom_action_param else {}
        th = float(param.get("threshold", 0.8))
        settle_ms = int(param.get("settle_ms", 1000))
        ctrl = context.tasker.controller
        gray = ArenaPick._grab_1280(ctrl)
        if gray is None:
            print("[SettleOk] 截图失败，本轮不点", flush=True)
            return True
        score, xy = _match(gray, "settle_confirm", (0, 0, 0, 0))
        if score < th:
            print(f"[SettleOk] 没找到结算「确定」(score={score:.2f}<{th:.2f})，本轮不点",
                  flush=True)
            return True
        _click(ctrl, xy, settle_ms, f"结算「确定」(score={score:.2f})")
        return True


class ArenaRankUp(CustomAction):
    """「KOF段位提升」弹窗点确认（v1.2.30，DirectHit 版）。

    确认键 (639,611) 不在任何盲点坐标上，所以必须靠识别驱动。
    ROI [300,60,720,130] 内实测弹窗 1.0 / 其它页 <=0.248。
    """

    def run(self, context: Context, argv: CustomAction.RunArg) -> bool:
        param = json.loads(argv.custom_action_param) if argv.custom_action_param else {}
        th = float(param.get("threshold", 0.8))
        xy = param.get("confirm_xy", [639, 611])
        settle_ms = int(param.get("settle_ms", 1500))
        roi = param.get("roi", [300, 60, 720, 130])
        ctrl = context.tasker.controller
        gray = ArenaPick._grab_1280(ctrl)
        if gray is None:
            print("[RankUp] 截图失败，本轮不点", flush=True)
            return True
        score, _ = _match(gray, "rank_up_title", roi)
        if score < th:
            print(f"[RankUp] 弹窗不在(score={score:.2f}<{th:.2f})，本轮不点", flush=True)
            return True
        _click(ctrl, xy, settle_ms, f"段位提升「确认」(score={score:.2f})")
        return True


class ArenaMasterMatch(CustomAction):
    """大师团页点「匹配战斗」（v1.2.30，DirectHit 版）。

    旧 `Arena_MasterMatch` 是 `recognition: DirectHit` + `action: Click` 的**纯盲点**
    (1135,634,80,35) —— 只要大师团页没准备好（转场、掉人重排），它一样点，
    点到哪算哪。这里加页面守卫：master_title 在 ROI 内 >= 0.85 才点，
    否则什么都不做、回 Router 重新判。

    点击区 y>=634 是「匹配战斗」响应区，往上就是拳头的判定范围
    （见 MEMORY：框大菱形会点到拳头区）。
    """

    DEFAULTS = {
        "roi": [380, 20, 560, 90],
        "threshold": 0.85,
        "target": [1135, 634, 80, 35],
        "settle_ms": 1200,
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
        gray = ArenaPick._grab_1280(ctrl)
        if gray is None:
            print("[MasterMatch] 截图失败，本轮不点", flush=True)
            return True
        score, _ = _match(gray, "master_title", param["roi"])
        if score < float(param["threshold"]):
            print(f"[MasterMatch] 不在大师团页(score={score:.2f})，本轮不点", flush=True)
            return True
        x, y, w, h = (int(v) for v in param["target"])
        _click(ctrl, (x + w // 2, y + h // 2), int(param["settle_ms"]),
               f"「匹配战斗」(页面分 {score:.2f})")
        # 记一下点击时刻：Router 下次判到大师团页时据此安静等待，不再重复点。
        ArenaRouter._match_clicked_at = time.time()
        return True
