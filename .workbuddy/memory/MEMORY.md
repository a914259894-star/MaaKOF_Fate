# MaaKOF_Fate 项目长期笔记

## 项目结构（本项目约定，勿破坏）
- **真实 agent 代码在 `assets/agent/`**（main.py + register.py + arena_actions.py + combat_actions.py + combat_recognition.py + farm_actions.py）。本项目约定 agent 目录在 assets/ 下（非官方样板的根目录 agent/；calibrate.py 注释亦写明此约定）。**没有根 `agent/` 目录**——历史上曾短暂出现根 agent/ 副本，已合并回 assets/agent/ 并删除，避免双副本漂移。
- `assets/interface.json` 的 agent child_args = 官方规范相对路径 `"./agent/main.py"`（协议：CWD=interface.json 所在目录 = assets/，故解析为 assets/agent/main.py）。本地 MaaDebugger 与生产发布都只加载这份。**不要改绝对路径**（发布到别的机器必失效）。
- `tools/install.py` 的 install_agent() 拷贝 **assets/agent** → 包根/agent（非样板默认的 root agent/），与本项目布局一致。
- 根 `main.py` = 嵌入模式入口（方案三），sys.path 指向 `assets/agent`；不要与 assets/agent/main.py（AgentServer 入口，方案二）混淆。
- 用户 python：系统 py3.12 装有 maafw==5.5.0（agent 用）；我的隔离 venv：`C:/Users/91425/.workbuddy/binaries/python/envs/default/Scripts/python.exe`（装了 opencv-python，用于模板制作，无 maafw）。

## 选人判定约定（2026-10-21 深夜重构：格子勾判定）
- **勾出现的位置 = 被点角色的网格格子内**（白勾 + 金框 + 1P/2P 角标），不在屏幕底部队伍展示槽（y=630 那排只有头像和空占位图）。**一个勾 = 一个角色选上**，与 1P/2P 无关——只验证自己点过的格子，不做"两侧都勾"的对方侧判定。
- 判定实现 `_cell_has_check(ctrl,x,y,param)`：以**实际点击命中点**为中心取 check_half=60 半径窗口（页面会滚动，固定 ROI 必然失效），截图与模板统一按 check_bright=190 二值化后 TM_CCOEFF_NORMED，阈值 check_threshold=0.7。模板 `slots/slot_checked.png`（40×41 二值图）**从 720p 现场帧直接裁剪**（debug/on_error/Arena_Pick_2026.10.21-17.45.01.999.png 格子中心约 (250,178)）；实测真勾 0.98/0.89、无勾格子 0.0、无关区域（底部槽/大立绘护腕≤0.70）均在格子 ROI 之外。
- 防 toggle：`_click_until_check` 点击前先查已有勾（有则免点）；点击后连续 miss_confirm=2 次无勾才补点，click_max_tries=3。快速选人 `_quick` 同理只对无勾格补点。
- 旧方案（底部槽 slot_rois 双侧 c==2 / 空槽占位图兜底）已整体删除，slot{1,2,3}_empty.png 不再参与判定。
- 常规选人页面由栏位自动判定：栏≤3 第一页、≥8 最后一页（右→左拖 last_drags=10 次）、4~7 按比例；混页按「第一页→中间→末页」只右拖不回拖。
- 今日现场帧验证脚本模式：venv python + cv2，二值化 190 后 matchTemplate 扫 debug/on_error/*.png。

## Agent 运行依赖与分发（2026-10-21 重要坑）
- agent 运行时硬依赖 `maafw`（即 `maa` 包，main.py 顶层 `from maa.agent.agent_server import AgentServer`）+ `opencv-python`，且版本须与宿主 MaaFramework 一致（本项目 maafw==5.5.0 ↔ MFA v2.5.7 / MaaFramework v5.5.0）。
- **致命坑**：`tools/install.py` 只拷贝 agent 源码（install_agent 拷 assets/agent），**不打包 Python 解释器、也不装 maafw/opencv**。宿主按 interface.json `agent.child_exec:"python"` 用系统 PATH 里的 `python` 拉起 agent。若那台机器 `python` 没装 maafw（或 MFA 选中的 python 不是装了 maafw 的那个），进程在 ~80ms 内导入崩溃 → 日志 `Agent process exited!` ×3 → `Agent启动失败 / Failed to LinkStart agentClient!`。
- 现象证据：用户初版 exe 在 `D:\桌面\kof\` 运行时 agent 每次都 80ms 内退出，根因即 maafw 未随包分发。
- **已实施 方案B（自包含，2026-10-21）**：① 新增 `tools/bundle_python.py`——下载 Windows embeddable Python 3.12.10（py `PY_VERSION`、maafw `MAAFW_VERSION` 可环境变量覆盖），修正 `._pth` 启用 `import site`，引导 pip，装 `maafw==5.5.0` + `opencv-python-headless`（用 headless 避免 Qt/GTK 系统库），并做 `import maa, cv2` 验证，产物落在 `deps/agent_python/`（已被 .gitignore 忽略）。② `install.py` 新增 `install_agent_runtime()`：把 `deps/agent_python` 拷到 `install/libs/python/`（忽略 `.bundled_marker`/`__pycache__`）；缺失时自动调用 bundle_python 准备。③ `interface.json` 的 `agent.child_exec` 由 `"python"` 改为 `"./libs/python/python.exe"`；欢迎语更新为"已内置便携 Python，开箱即用"。④ 新增 `assets/agent/requirements.txt`（`maafw==5.5.0` + `opencv-python-headless`）。
- 验证（2026-10-21 后台实跑通过）：bundle_python.py 成功下载 embeddable Python 3.12.10 → 引导 pip → 安装 maafw==5.5.0 + opencv-python-headless==5.0.0 → 导入自检 `maafw + cv2 导入 OK`；产物 `deps/agent_python/python.exe` 复验 `import maa, cv2` 通过（cv2 5.0.0）。bundle_python.py 与 install.py 均 py_compile 通过；interface.json 仍合法 JSON。发版命令：`python tools/install.py <版本> win x86_64`，install_agent_runtime 会把 deps/agent_python 拷入 install/libs/python/。

## 战斗后结算防误点（2026-10-21 最终方案：主页面兜底检测）
- 根因：`CombatFighter`（`combat.json`→`Fight_Once`）常驻盲点技能/普攻，战斗结束后到 win/lose 识别命中有窗口期，盲点坐标（normal 1137,632 / skill1 981,643 / roll 1232,556 等）与结算确认键重叠会误点退出到竞技场主页面，之后 win_text 消失检测不到 → 盲点到 900s 超时。
- **用户明确否决的方案**（勿再做）：在盲点循环里加 settle_confirm/settle_guard 检测或缩短 result_interval——盲点节奏是用户实测调好的（result_interval=4.0），迭代内的盲点突发仍可能误点，且影响手感。
- **已落地最终方案**：盲点流程完全恢复原样；仅在原 win/lose 节流检查里**追加竞技场主页面兜底检测**： ① 新增 `arena.json` 节点 `Arena_MainPage`（TemplateMatch `arena/arena_title.png`「竞技区」横幅，阈值 0.8，DoNothing，next=[Arena_WaitBattle, Arena_Mode_1v1, Arena_Enter_1v1] 与 Arena_LeaveSettle 相同）；② `combat_actions.py` 增加 `arena_main_nodes` 参数（默认 ["Arena_MainPage"]），在 result_nodes 检查后追加 `_hit_any(main_nodes)` 命中即 break；③ `Fight_Once.next` 增加 `Arena_MainPage`（主页面时正向命中 → 直接进下一轮导航）。**注意勿用 `Arena_LeaveSettle` 做 Fight_Once.next 兜底**——它是反向识别，战斗卡死超时时也会命中导致中途乱导航；`Arena_MainPage` 是正向识别无此问题。
- 效果：无论战斗正常结算还是被误点退回主页面，CombatFighter 都能在 ≤4s 内识别主页面并收手进下一轮；若仍有迭代内误点再另行解决（用户原话："接下来如果还误点的话再看看怎么解决"）。

## 大师竞技场（2026-10-23，v1.2.1）
- 选人页 15 格 = 6列×2行 + 第3行左 3 格（`MasterPick._cells()`），1280 基准 COLS=[90,202,313,478,590,701] ROWS=[19,128,237] CELL=99。禁用标 ban_stamp（红掩码）/ 选用白勾 master_check；一局会出现 4 个禁用标（自己 2 + 对手 2）。
- **角色匹配可行**：1v1 `roles/c{栏}r{行}k{列}.png`（78×78）**直接在 99×99 格内滑动** TM_CCOEFF_NORMED 即 0.96~0.99。禁止把格子裁剪后 resize 到 78（v1.2.0 因此误判"角色池不重叠"）。角标/勾/徽章会压到 0.58~0.78，用双判据：≥0.86 或（≥0.72 且比次佳格高 ≥0.12）。
- 选人策略（用户要求）：禁用**只要 2 个**、选用**只要 3 个**；输入按顺序优先，凑够即停；多填的输入位是备选；全试完仍凑不齐才从第 1 格兜底补齐。

## 日志与 interface.json 规范（2026-10-23，v1.2.3）
- **所有 agent 里的 print 必须带 `flush=True`**：宿主用管道拉起 agent，stdout 是块缓冲（4KB~8KB），日志会攒成一坨（现场见过 40s 输出同一个时间戳），进程崩溃还会丢缓冲内容。`assets/agent/main.py` 与根 `main.py` 已 `reconfigure(line_buffering=True)` 兜底。
- **interface.json 改完必须过 `deps/tools/interface.schema.json`**（venv 已装 jsonschema）：
  `python -c "import json,jsonschema; jsonschema.validate(json.load(open('assets/interface.json',encoding='utf-8')), json.load(open('deps/tools/interface.schema.json',encoding='utf-8')))"`
  踩过：**option 不支持 `default_check`**（只有 taskItem 支持，schema additionalProperties=false）——之前「大师选人栏位」带着它，已删；选项只要被 task 的 `option` 数组引用就会显示，不需要 default_check。
- input 选项每个 `inputs[]` 支持自己的 `description`（会显示成字段旁的问号提示），长说明应放 option.description / input.description，**不要堆在 label 括号里**（会把文本框挤没）。

## 点击类 CustomAction 的三条铁律（2026-10-23，v1.2.4 现场教训）
- **点完不能固定 sleep 就判标记**：模拟器/游戏 UI 渲染有 0.5~0.9s 延迟，固定 0.45s 判定必然"失败"，再点一次会把已选取消（toggle）→ 每格两次点击、实际选中 0，整段流程被拖过倒计时。正确做法：点前先查已有标记（有则免点），点后轮询（`mark_timeout`/`mark_poll`）等标记出现，只有真等不到才补点。
- **阶段中途要有「页面还在吗」守卫**：页面被游戏接管后继续盲点会把点击丢到别的界面（回到大厅/主页/点到取消弹窗），把循环带偏。判据要用页面独有的稳定锚点（大师选人页 = 「1P」标签 @(405,363) 90×50，ROI [340,320,240,140]），不要用「确定 按钮在不在」（取消弹窗上也有），也不要全屏找通用文字（战斗 HUD 里也有 1P）。
- **每个 CustomAction 节点的 `next` 必须带兜底**：MaaFramework 里 next 列表评估的时长上限是**当前节点的 timeout**（MasterPick=180s），列表里没节点命中就一直空转，到点 node timeout → **整个任务 failed 停止**（MFA 外壳还打「任务已全部完成」，极易误判成正常结束）。所以 MasterPick/Pick 的 next 都要包含 WaitBattle + 大厅 + 竞技区卡 + 首页竞技入口 + 竞技区主页面；master 任务还要在 interface 里把 `Arena_MainPage.next` 改成先 Mode_Master，否则会被 base 的 Mode_1v1 抢走。
- 排错时看 `debug/on_error/<Node>_<时间>.png`：它是**节点超时那一刻的截图**，直接告诉你卡在哪个界面（v1.2.4 就靠它确认"卡在大师团大厅而不是战斗"）。
- **阶段切换不能靠「点确认」推进，要识别新阶段的界面标志**（2026-10-23，v1.2.5 现场教训）：大师禁用阶段点「确定」只是我方声明完成，游戏要等对手也确认才切进选用阶段，中间 1.7~17.7s 点格子全是空转 —— 白等还把前几个输入耗掉，甚至拖到倒计时归零变 0/3。正解：不点确认、让弹窗倒计时自然结束，然后用新阶段独有的锚点（大师选用阶段 = 底部橙字「温馨提示：请选择格斗家」，模板 `arena/master_pick_tip.png`，ROI [520,645,240,70]，正例 0.986~1.000 / 反例 ≤0.359）确认切过去了再动手。
- **模板匹配阈值不要为了"兜住遮挡"放到 0.72 这种低分**：实测大师页「别的角色」最高能到 0.787（`c6r2k3`→`c7r1k3`），而 2P 角标遮挡的**正确**角色是 0.784 —— 两者无法区分，只能一律不认（低分档保底 ≥0.82）。宁可漏选走备选/兜底，也不能点错人。

## 大师团「上阵」页（2026-10-23，v1.2.6）
- 页面 = 左侧 tab「大师竞技场/大师团」、底部一排角色卡、右下「匹配战斗」。识别：`master_title` 在 ROI `[380,20,560,90]` 内 0.997（战斗 HUD 0.166 / 选人页 0.235）。**这页没有能跳走的入口**，盲点跑不了，但盲点坐标正好压在 `normal`(1137,632)=匹配战斗、`skill1`(981,643)=一键上阵 上，会把底部角色行的「上阵」点掉 → 角色不够、匹配不了。
- 底部角色行卡位（白色圆形勾 = 已上阵）中心 x=**126/262/400/537/674**（间距 137）、y=**640**；模板 `arena/master_team_check.png`（46×46，已上阵 0.93~1.00 / 未上阵 0.22，阈值 0.7）。点击点用 `(cx, 682)`（避开正中半径 22 的勾）。
- 处理：① `CombatFighter` 每轮循环**最前面**判 `_team_page_hit`（标题 ROI + 任一卡位有勾，双判据）命中立即 break，别再等 4s 节流；② `_unknown_escape` 对该页**不点返回**（不需要自救）；③ `Arena_MasterPage.next → Arena_TeamFix(MasterTeamFix) → Arena_MasterMatch`。
- **掉人判据（v1.2.9 用户纠正，勿再用「没勾」）**：底部行是**角色列表**，白勾只表示该角色在上阵队伍里，**有角色没勾是正常状态**；真正掉人 = 那张角色卡整张消失（位子空了）。判据用 `arena/master_card_ssr.png`（62×36 卡片左上角 SSR 标签）在 `(cx-100, cy-56, 200, 70)` 内匹配，**有卡 0.92~1.00 / 空白 0.20~0.65 → 阈值 0.85**。卡位都有卡就一下都不点（现场 19:05:55 帧：卡 OOOOO、勾 OOO-O → 0 点击，被判"人够"是对的）；出现空卡位才点 `(cx, cy+42)` 等卡片出现，点不动就记 `_TEAM_STUCK` 180s。`max_drags` 默认 0（不再自动拖角色行）。

## 大师团「匹配战斗」按钮的点击区（2026-10-23 v1.2.8 现场踩坑）
- **target 不能框整个菱形**：`[1121,575,92,92]` 会让点击随机落到菱形上部的拳头图标区，那里不响应 → 点了没反应 → `Arena_MasterWaitPick` 20s 一个都不命中 → `Arena_MasterMatch` 节点超时 → 队列静默结束（外壳还打「任务已全部完成」，on_error 截图停在大师团页）。现场 4 次点击：y=640/635/635 成功，y=587 失败。**响应区在菱形下半（y≥634）**，安全 target = `[1135,634,80,35]`。
- **任何「点一下就要等新页面」的节点，next 必须带自恢复兜底**：`Arena_MasterMatch.next` 现在 `[Arena_MasterWaitPick, Arena_MasterPage]`，点了没反应就重走 `MasterPage→TeamFix→MasterMatch` 再点，绝不能只剩一个「等结果」节点。
- `MasterTeamFix` 的死卡位记忆：点不出勾的卡位进 `_TEAM_STUCK`（180s），期内跳过（现场第 4 格 x=537 就是这种，点多少次都不变勾；其余四格 0.92~0.99）。

## 战斗误点退出（v1.2.7 定稿，勿再改回「恢复盲点」）
- **复核失败绝不能恢复盲点**：`_escape_misnav` 的 2s 复核失败 = 画面已被残留点击带走 = 人已在竞技场页/主城，这时恢复盲点就是在竞技场页或主城底部导航上乱按（现场 18:16~18:17：商城→排行榜→竞技场页→复核失败→至尊赛→…→主城→点进装备）。现在复核失败一律 `return True` 收手，交给流水线认页面。
- **主城兜底**：主城 = `arena/main_arena_btn.png`（85×85「KO 竞技」图标）全屏匹配 ≥0.85（主城 1.0 / 大师团页 0.489 / 选人页 0.516 / 战斗 0.496，单次约 20ms）。agent 每轮快速检查 + `_escape_misnav` 都判，命中立即 break；pipeline 里 `Arena_HomePage`（≥0.7）→ `Arena_Enter_1v1`（大师任务被 interface 覆盖成 `Arena_Enter_Master`，否则从主城会误进 1v1）。
- **兜底返回不再盲点**：`Arena_UnknownBack` 已从 `DirectHit+Click` 改成 Custom `ArenaSafeBack`——已在竞技场页/大师团页/主城就不点（这正是「返回键多按了退到主城」的元凶），只有真子页面才点红色箭头。

## agent 内识别的性能铁律（v1.2.10 实测，勿违反）
- **`context.run_recognition` 一次 IPC 往返 400~800ms**（Tasker 侧调度 + 同步），只能用于低频/一次性判断；**绝不能放进战斗高频循环**。现场症状：开局站桩 1.34s（自检 780ms + 首轮检查 500ms）+ 战斗中每 4s 固定空档 0.55~0.67s（`Fight_Lose` 每 4s 查一次）。
- 同模板同阈值的 **cv2 本地灰度匹配**耗时（1280 基准）：团队页判定 1.5ms、主城全屏 20ms、`_main_page_hit` 双模板 47ms、误点 4 模板 4ms、全屏结算 24ms。凡是「模板 + 阈值」能表达的识别，一律本地做。
- `_hit_any` 已有分发：`_RESULT_TPLS = {Fight_Lose: lose_text, Fight_Win: win_text}` 走本地；其它节点（`Fight_Substitute` 这种 Custom recognition）不传 `gray` 就走 IPC。
- **`_load_tpl(rel)` 的根是 `_ARENA_DIR`（`image/arena`）**：`lose_text` 不能写成 `arena/lose_text`（子目录才带前缀，如 `wrong/wrong_shop`）。写错只打印「缺少模板」并返回 None，会静默失效。
- 首轮检查要推迟：`next_result_check = time.time() + result_interval`，**不能初值 0**（否则进循环第一件事就是查结算，白等半秒）。

## 大师选人「阶段过渡」不能当「页面离开」（v1.2.11，勿再改回单次判定）
- **禁用→选用阶段切换的过渡里 1P 标签会短暂消失**（0.5~1.5s），`_left_pick_page` 单次判定必然误报。2026-10-24 现场：禁用点完 6 秒就判「选人页已消失」→ `run()` 直接 `return True` 放行 → **整个选用阶段跳过，0 个选用直接进战斗**（用户描述「选人不动了」）。
- `_wait_pick_phase` 现在是**三态**：True=温馨提示命中、False=**确证**离开（`_confirmed_away` 看见 battle_bar/battle_btn/arena_title/main_arena_btn/master_title 之一）、None=单纯超时。超时后 1P 不在 → 直接选（不点确定，点了等于提前结束选人）；1P 还在 → 点确定走旧路径。
- `_left_pick_page(ctrl, param, confirm_gap=0.5)` 带二次复核，用于点格失败收手与选人收尾；`_p1_score()` 模板缺失时返回 1.0（保守当「还在」）。
- 选人页/阶段模板取值：`pick_wait`(1P) ROI [340,320,240,140] ≥0.8；`master_pick_tip` ROI [520,645,240,70] ≥0.75。真实帧标定：禁用阶段 `master_frames/f_0010.jpg`（tip 0.252）、选用阶段 `f_0027.jpg`（tip 0.987）。
- **遗留**：`_cell_taken` 不认 2P 角标（f_0010 的格 (1,1) 有蓝色「2P」= 对手已占），兜底会去点它，待补模板。

## 协作偏好
- base pipeline 参数保持 MAAFramework 规范默认值（如 quick_pick:true），个性化由 interface.json 选项 override 覆盖，不要改 base 默认。
- MaaDebugger v1.17 是裸流水线调试器：不渲染 select/input 选项，用 Pipeline Override JSON 注参。**它完全不会读 interface.json、也不会拉起 agent**（源码 grep 无 interface/child_exec）——Agent Identifier 的 CONNECT 只是创建带该 identifier 的监听端并等 5s（`set_timeout(5000)`），必须先在终端手动跑 `assets> libs\python\python.exe agent\main.py <identifier>`（identifier 用输入框里那个，持久保存在 STORAGE），agent 存活期间点 CONNECT 才能连上。
