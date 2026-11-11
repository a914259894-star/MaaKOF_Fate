# MaaKOF_Fate 笔记

## 结构 / 环境
- agent 代码在 `assets/agent/`（**没有根 agent/**）；`interface.json` 的 `agent.child_args` 必须是 `./agent/main.py`（CWD=assets/）。根 `main.py` 是嵌入模式入口。
- venv：`~/.workbuddy/binaries/python/envs/default/Scripts/python.exe`（cv2+jsonschema，无 maafw）；用户 py3.12 有 maafw==5.5.0。便携 py 由 `tools/bundle_python.py` 产到 `deps/agent_python`，`install.py` 拷进 `install/libs/python`。
- 发版 `python tools/install.py <ver> win x86_64`；**打包前先移走 install/libs/python**（否则卡在覆盖确认）。覆盖包：interface.json + agent/*.py + 改过的 pipeline/模板。
- **GUI 实时画面 v1.2.26 起默认关闭**：`tools/config_seed.py` 把 `UI.LiveView.EnableLiveView=false`、`UseSeparateScreenshotTasker=false` 合并进发布包 `config/config.json`，`install.py` 末尾调用。CI 固定 `MFAA_VERSION: v2.5.7`（全局键与 `Instance.default.*` 同一个 config.json）；maaCQDiamond 用 latest（v2.16.x，已拆 `config/instances/*.json`）。配置键在 `libs/MFAAvalonia.Core.dll`（237KB 的 `MFAAvalonia.dll` 只是壳），搜 .NET 字符串要 **UTF-16LE**，ASCII grep 搜不到。种子只对**新解压的目录**生效，旧目录要用新版得解压新目录或退出 GUI 后手动 merge 自己的 config.json（GUI 退出时会整份回写，跑着改会被冲掉）。

## 规范（改完必跑）
- agent 所有 print 带 `flush=True`。
- `interface.json` 过 schema（`deps/tools/interface.schema.json`）。**option.pipeline_override 整块替换**——改 arena.json 的 param 必须同步改 option。
- **CustomAction 节点的 next 必须带兜底**：next 评估上限=节点 timeout，全不命中 → task failed，外壳仍打「任务已全部完成」。
- 改完 pipeline 跑 `debug/_test_pipeline_refs.py`（引用+注册校验）。

## 识别性能铁律
- `context.run_recognition` 一次 IPC **400~800ms**，禁止进战斗高频循环；模板+阈值一律本地 cv2（1.5~47ms）。
- `_load_tpl(rel)` 根是 `image/arena`：写 `lose_text` 而非 `arena/lose_text`；子目录才带前缀（`wrong/wrong_rank`）。
- 首轮结算检查初值 = now + result_interval（不能 0）。

## 战斗
- 盲点节奏 `result_interval=4.0` 是用户调好的，别在循环里加检测。
- 每轮最前先本地判：大师团页（`master_title` ROI[380,20,560,90]≥0.9 且有卡位勾）/ 主城（`main_arena_btn` 全屏 ≥0.85）/ 竞技场页；命中即 break。
- `_escape_misnav` 复核失败一律 `return True` 收手，**绝不恢复盲点**。
- `Arena_UnknownBack`=Custom `ArenaSafeBack`：在竞技场/大师团/主城不点，只有真子页面才点红箭头（`_find_red_back`）。
- `Arena_MainPage` 用正向 `arena_title`；**不能拿 `Arena_LeaveSettle`（inverse）做兜底**。
- v1.2.20 起出招节奏：技能**只在就绪时点**（`_ready_keys`），普攻每轮 1 次、周期 `min_round_ms=150`，挨打连点替身，敌血见底后静默。想回旧行为把 `ready_only` 改 false。
- 就绪判定（**v1.2.29 起：`mode:"bc"` + `V≥110 且 S≥55` 占比 ≥0.30 + roi 42x42**）：四个角色技能 skill1/2/3/ult 参数完全一致；秘卷保持旧的 27x28 / 150/70 / 0.30（图标全角色共用，一直正常）；替身仍 ratio 0.1（受击闪光语义不同）。**旧口径 150/70/0.15 是按"图标整颗都亮紫"的角色标的**，换到图标偏暗的角色（蓝色立绘，亮部只有 110~140）三技能实测 0.08~0.22 正卡在阈值上 → 整局不放（v1.2.29 修的就是这个）。**判据贴边时先怀疑 ROI 太小**：同一帧纵向挪 ±6px，27~35px 的 ROI 指标能从 0.22 摆到 0.03；放大到 42x42 后同一段摆动只有 0.40~0.60。白字 S≈0，仍被饱和度门槛挡在门外。
- **ratio（只看亮不亮）会被「CD 白色大数字」骗反**（v1.2.22 教训）：白字 V=255、S≈0，占满 27px 的 ROI 能把 ratio 顶到 1.00，比就绪态（0.67）还高。秘卷就踩了这个坑（阈值 0.85 → 永不点）。改用 `mode:"bc"`（V≥150 且 S≥70 占比，阈值 0.30）：就绪 0.51~0.55 / 冷却与白字 0.00~0.19。**v9 录像 195 战斗帧交叉验证：bc 与旧 ratio 仅 8 帧不同，全是 ratio=1.00/bc<0.05 的白字帧 → bc 不漏真就绪，只纠正白点。** **新角色/新按钮一律用 bc，不要用 ratio。**
- **`ready_miss_s=10` 兜底救不了「单个技能漏放」**：它判「连续 10s 一个技能都没就绪」，只要别的技能就绪就清零计时。所以单个技能判据失效会静默持续整局（v1.2.24 的一技能就是这样，用户观察到的"根本不点"）。排查时不能拿"没触发兜底"当判据正常的证据。
- **战斗画面阈值 v1.2.25 起 0.65（原 0.8）**：`Arena_WaitBattle.threshold` / `battle_threshold` / `arena_actions._confirmed_away` 三处同步。依据：实机 maa.log 120 条 `Arena_WaitBattle` 记录里 **0.30~0.72 是空档**（加载页 ≤0.298、候选框恒为 2；血条刚出现 0.724~0.788、候选框 34+；稳定 0.859~0.953）。血条刚出现时被开场发光压着上不到 0.8，旧阈值让每局白等 5.6~7.3s。**排查「等待/识别慢」先看候选框数量**：2 个 = 屏幕上没纹理（加载/黑屏），30+ = 目标已出现。
- **KO 静默 v1.2.23 起默认关闭（`ko_quiet_enable=false`）**：用户要求「不到结算就不停」。它只是 v1.2.20 加的省空点击优化，停手真正依据是每轮都在判的 `_settle_hit`/`_arena_page_hit`/`_team_page_hit`/`_home_page_hit`/`_rank_up_hit`。判定逻辑（`_ko_step`：血条连续消失满 2.0s + 本回合见过 ≤12px + 未见空血时 4s 兜底）保留，开关改 true 可恢复。**「连续 N 轮」在 150~250ms 的循环里等于 0.45~0.75s，会被大招特效骗**——v1.2.22 因此在敌人剩 33/85/57px 时误停手 1.3~1.7s（实机日志 14 次见底里 3 次误判）。`ko_quiet_max_s=20` 备用。`battle_hp1/2` 只用于开打自检/WaitBattle/选人确证，**不参与出招**。
- 判据测的是「亮不亮」不是「有没有数字」：CD 只变灰无秒数的角色 → V≈90~140 够不到 160 → 判冷却，**比有秒数更安全**（23 个模拟样本全对）。弱点在反向：大号白字几乎填满 27~35px 的 ROI，亮占比可到 0.98（skill2 阈值 0.35）→ 白点一次，但不漏放技能。
- **兜底必须按秒计时**（v1.2.21 教训）：`ready_miss_s=10`。一轮只有 150~250ms，原来按「4 轮」= 0.6~1.0s，而录像实测有**连续 6 秒全技能 CD**（93~99s）→ 每局必误触发、P0 自我关闭。**任何「连续 N 次/轮」的阈值都要检查循环周期是否可变。**
- 更本质的判据测过但没换：只测按钮外环 r∈[13,23] 的亮度（s1 冷 0.00/就 0.61，s2 0.06/0.45，ult 0.16/0.71）。分离度看着更好，但 **skill2 的金色外圈在冷却时也很亮**、亮场景背景会污染 r>30 的采样，且 sec 冷却态带蓝色光环 → 不可靠，v1.2.29 明确放弃外环方案。
- 血条测量 `_hp_widths`（0.37ms）：只切顶部 y18~44 做 HSV 黄绿 35~75。己方左对齐 115→448、敌方右对齐 831→1164，满血各 333px，1px≈0.3% 血。
- **KO 静默必须同时认页面**（settle/arena/team/rankup/home）：只盯血条会在最后一回合 KO 后硬等 12s，把结算页 2.33s 的防误点窗口整段错过。

## 选人
- 大师 15 格 = 6×2 + 第3行左3：COLS=[90,202,313,478,590,701] ROWS=[19,128,237] CELL=99。角色模板 `roles/c{栏}r{行}k{列}.png` 78×78 **直接在 99 格内滑动**（禁止 resize）。阈值 ≥0.86 或（≥0.82 且比次佳高 0.12）；**不能为遮挡降到 0.72**（别的角色最高 0.787）。
- 策略：禁 2 选 3，按输入顺序凑够即停，全试完才从第 1 格兜底。
- 点后轮询等标记，不能固定 sleep；点前先查已有标记防 toggle。
- **1v1 选完人点「确定」必须走模板，不能盲点**（v1.2.28）：按钮要选满 3 人才淡入，盲点 `(622,638)` 永远点在下方 66px 的空面板上且不报错 → 每局白等 30~90s 让游戏倒计时锁定。现在 `pick_confirm.png`（116×43）全屏定位（实测 0.9875 @ 1280 基准 (621,571)），点后 1s 复核 `pick_wait`/`pick_recent` 锚点还在就补点（≤3 次）；连续两轮看不到锚点=已被游戏接管，直接放行。**`order_confirm` 在该页只有 0.44，不会被误判成顺序页而跳过。**
- **阶段切换不能靠点「确定」**：等新阶段锚点（大师选用 = `master_pick_tip` ROI[520,645,240,70] ≥0.75）。禁用 20s + 选用 30s → `pick_phase_timeout=45`、`ban_min_wait=22`、`Arena_MasterPick.timeout=240s`。`_wait_pick_phase` 三态：True=提示 / False=`_confirmed_away` / None=超时。
- 遗留：`_cell_taken` 不认 2P 蓝角标。

## 大师团页
- 卡位 x=126/262/400/537/674、y=640；点击 `(cx,682)`。
- **掉人判据 = 角色卡整张消失**（`master_card_ssr` 在 `(cx-100,cy-56,200,70)` ≥0.85），**不是「没勾」**。
- 「匹配战斗」响应区 **y≥634**，target=`[1135,634,80,35]`（框大菱形会点到拳头区）。

## 页面路由（v1.2.30 起：单入口星形，取代旧 next 链）
- **唯一判别点 = `Arena_Router` / `Arena_Router_Master`**（DirectHit + Custom `ArenaRouter`，代码在 `assets/agent/page_router.py`）。入口 `Arena_Enter_1v1` / `Arena_Enter_Master` 只是 DoNothing 转接。
- 判页优先级（代码里顺序固定，`_test_nav_chain` 会校验）：**战斗 > KOF段位提升 > 胜负结算 > 出手顺序(order) > 选人 > 大师团 > 竞技场主页 > 主城 > 未知**。order 判据 v1.2.35 加，见下「道馆挑战」节。
- Router 用 `override_next(argv.node_name, [目标])` 精确跳转，并 `override_pipeline({目标:{"next":[自己]}})` 改回路。**1v1/大师是两个 Router 节点，不写回路会串模式。**
- **路由目标必须全是 DirectHit**：override_next 后下一轮还要重新识别，目标若是 TemplateMatch，转场丢帧就识别失败 → 节点不执行、next 空 → 整个 task 结束且外壳打「任务已全部完成」。所以 SettleOk/RankUp/MasterMatch 都改成 DirectHit + 自定义动作（先复核页面才点）。
- **任何 next 里不许放 Click 节点**（战斗内 Fight_Loop→技能键除外）——这就是 v1.2.27「1v1 误点其他界面」的根因：`Arena_Enter_1v1` 只看 main_arena_btn 且阈值 0.7 就点、`Arena_MasterMatch` 是纯盲点 (1135,634)。
- **未知页 = 纯等待**（v1.2.32 定稿）：加载页 / VS 入场 / 一穿三 / 至尊赛播报都算未知页，每 **0.25s** 重判一次，变成可识别页面立刻路由，期间**一次都不点**。兜底 = **连续 `unknown_back_s=5` 秒**一次都认不出才点一次返回，点完回 Router 重新数 5s。
  - 0.25s 必须两处一起改才生效：`Arena_UnknownWait.post_delay=250` **且** Router `rate_limit=200`，否则 rate_limit 把轮询兜成 0.3s+。
  - **v1.2.31 的教训（别再犯）**：中间试过「0.5s + 必须看到红箭头才点」，录像验证确实不误点，但用户要的语义是「等」不是「提前返回」——他说的「500ms」指的是**轮询间隔**，不是触发返回的秒数。含糊需求先复述两种解读让他选，别直接改。
  - **箭头判据若要启用必须用严格版 `page_router._red_back_xy`（y<56、x≥1040）**，不能用 `CombatFighter._find_red_back`（y<95）：后者会把战斗 HUD y=60~86 的红色元素、主城图标、加载页装饰认成箭头。录像 36 帧实测真箭头（竞技场主页/至尊赛/装备页，y=28~33）全保留、过渡帧误命中 5→0。默认 `unknown_need_arrow=false`。
  - 已知边界：加载页最长 5~7s，5s 兜底可能落在加载末尾点一次右上角。现场若看到，把 `unknown_back_s` 调到 8。
  - Router 截图用 `_grab_both`：一次 IPC 同时给灰度和彩色（找箭头要红色），不额外截图。
- **大师团页必须再分一步**（`_team_step`）：卡位全有卡 → `Arena_MasterMatch`，缺卡 → `Arena_TeamFix`。不分就是 `Router→TeamFix→Router→TeamFix` 死循环，永远不点匹配。
- **卡位判据 Router 用 0.72、TeamFix 用 0.85**：现场帧第 3 卡位只有 0.776（头像挡了半个 SSR 标签）但队伍是满的。宁可判满员去匹配，也不要判缺人（会点下阵）。
- **`match_cooldown_s=20`**：点完「匹配战斗」页面不变（匹配中），不拦的话 Router 每 300ms 判一次 team，会把刚发起的匹配连点几十下。
- **选人页判据必须有 `master_pick_tip`**（底部「温馨提示：请选择格斗家」，ROI[520,645,240,70]，阈值 0.8）。名字有历史误导，它不是大师专属；列表滚动过的选人页只有它能认（pick_recent/pick_wait 掉到 0.58/0.77）。
- 判据都走本地 cv2（ROI 化），一轮 25~45ms；日志 `[Router] 页面=X → Y` 只在变化时打 + 10s 心跳。
- `Arena_RankUp`：胜利后「KOF段位提升」全屏弹窗，**顺序=结算页→点确认→它，不顶替结算页**。`rank_up_title` ROI[300,60,720,130] ≥0.8（弹窗 1.0/其它 ≤0.248）→ 点确认 (639,611)。确认键不在盲点 → CombatFighter `_rank_up_hit` 命中即 break。
- 旧的 `Arena_RankPage` / `Arena_StuckRecover` 两个兜底节点已不在 pipeline 里（v1.2.17 回退时去掉，v1.2.30 由 Router 的「未知页等待」接手）。

## 道馆挑战（v1.2.33 新增，v1.2.34 修正三处，与竞技场同构）
- 代码 `assets/agent/gym_actions.py`，pipeline `assets/resource/pipeline/gym.json`（9 节点），任务 `gym_challenge`，无限循环。
- **判页优先级 fight > result > order > pick > wait > page > home > unknown**；未知页挂起轮询 **0.25s**、**`unknown_back_s=20`**。
- 入口 = 主城右上鸟居 **(1153, 99)**；道馆挂牌 1280 坐标：樱花 `[177,373]` / 觉醒 `[361,109]` / 克隆 `[533,333]` / 暴走 `[773,181]`（**程序找白色四字文本块算出来的，肉眼猜连错 4 次**）。选择做成 option「道馆选择」。
- **选人页有三个状态，别只认两个**：<3 人「温馨提示：请选择格斗家(X/3)」(`gym_picktip`) / **满 3 未确定 = 红色「确定」按钮** (`gym_confirm`，1280 下 (560,605)-(720,653)，点击中心 **(640,629)**，全片 128 帧只在 24.0s 命中) / 已确定「等待对方确定中」(`gym_pickwait`)。**满 3 不会自动锁定，必须点「确定」**——v1.2.33 误判成自动锁定，点完 3 格后傻等超时，再重进把角色 toggle 掉。
- **立绘列中心 x = 96 + 152k、y = 185**（从勾环位置 552/856/1008 反推；旧的 105/257/409 偏 9px）。点每格前先查该列金色勾环 `gym_check`（73x73，阈值 0.88，实测勾 0.97~1.00 / 未勾 ≤0.07），有勾跳过 —— 防 toggle。
- **选人点「确定」后必经「点击格斗家确定顺序」页**（v1.2.35 补）：右上角橙字，10 秒倒计时，**道馆 / 1v1 / 大师是同一张 UI**，判据直接复用 `order_confirm`（45x300，ROI `[0,0,1280,160]`，道馆帧 0.981 / 其余页 ≤0.579）。**只等不点**：倒计时归零游戏按默认顺序（从左到右）自动确定，点立绘反而改出场顺序。道馆 → `Gym_Wait`，竞技场 → `Arena_UnknownWait`（并清零未知计时）。**判定必须排在 pick 之前**：pick 会点立绘；误判成 order 只是多等一轮（选人页拿 order_confirm 仅 0.49）。
- 实测时序（每 0.5s 切全片）：`确定` 1.0s → `等待对方确定中` 2.5s → 转场 1.5s → **顺序页 8.0s** → 战斗准备/加载 8.0s → 开打。所以 `unknown_back_s=20`（最长未知只剩 8s，留一倍余量）；v1.2.33 的 25 卡边界、v1.2.34 的 45 是还没认顺序页时算出来的。
- **战斗节点别指错**：必须是 `Gym_Fight`，不能是竞技场 `Fight_Once`（后者无 `gym_result_threshold`，打完一局认不出道馆结算页 → 空点到 900s）。v1.2.33 在这踩过。
- **战斗就是复用同一份 `CombatFighter`**，参数逐项一致（测试 [8] 组比对，只允许多 `gym_result_*`）。不能直接共用节点的唯一原因：道馆结算页拿竞技场的 `win_text`/`lose_text` 实测只有 **0.185 / 0.127**，认不出 → 需要「同逻辑 + 换退出判据」的独立节点。
- 结算页是「点击空白处关闭」，无确定按钮。**关闭后的残余帧会判成战斗**：点完空白只等 800ms，1~2s 后血条还在淡出 → Router 判 fight 打空气。修法：`_wait_vanish()` 轮询等 `gym_close` 消失（实测 1.5~2.2s，上限 4s）+ `tail_s 0.8`。
- **「点完页面不变」必须加 cooldown**：`GymPage.cooldown_s=40`（匹配中）、`GymHome`/`GymPick` 各自也有。cooldown 分支日志用 `_cd_log` 节流（同 key 5s 一行）。
- 战斗 3v3 多回合，**每回合弹 WINNER 但战斗继续**，WINNER 不是结束信号。技能 ROI 与 1v1 像素一致。
- `debug/_test_v1233.py` 199 项（含 GymPick 三态 + 真帧 hi0024.0/hi0025.5/hi0033.0 回放 + 路由表逐条校验目标节点存在 + 三模式同帧判 order 一致）。真帧存 `debug/_gym/hi*.png`（21 帧人工标注期望页）。

## 工具
- base pipeline 用默认值，个性化走 option。
- MaaDebugger 不读 interface.json、不拉 agent；先起 agent 再 CONNECT。
- git 推送：代理 7897（Clash）没开时直连必超时，先确认端口在听；报 `schannel: failed to receive handshake` 是 Windows TLS 后端问题（curl 同代理 200 即证明网络正常），用 `git -c http.sslBackend=openssl push` 解决。**repo 里固定写了 `http.proxy=http://127.0.0.1:7897`，Clash 一关就报 `Failed to connect to github.com:443 over proxy`；此时 `git -c http.proxy= -c https.proxy= -c http.sslBackend=openssl push` 直连可通。** 发版前跑 `validate_schema.py --schema-dir deps/tools ...`（默认 tools/schema 不存在）。
- **v1.2.18 起 `logging: false`（默认不写 `debug/maa.log`）**：要 maa.log 排查时先把安装目录 `config/maa_option.json` 的 `logging` 改回 `true` 复现一次，看完改回来。`save_on_error` 一直开着。
- 排错看 `debug/on_error/<Node>_<时间>.png`。
