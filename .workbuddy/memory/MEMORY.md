# MaaKOF_Fate 项目长期笔记

## 项目结构（勿破坏）
- 真实 agent 代码在 `assets/agent/`（main.py / register.py / arena_actions.py / combat_actions.py / combat_recognition.py / farm_actions.py）。**没有根 `agent/` 目录**（曾有副本，已合并删除，避免双副本漂移）。`tools/install.py` 把 assets/agent 拷到包根 agent/，与之一致。
- `assets/interface.json` 的 `agent.child_args` 必须是官方相对路径 `"./agent/main.py"`（CWD=interface.json 所在目录=assets/），**不要改绝对路径**。
- 根 `main.py` = 嵌入模式入口（sys.path 指向 assets/agent），与 assets/agent/main.py（AgentServer 入口）不是一回事。
- 我的 venv：`C:/Users/91425/.workbuddy/binaries/python/envs/default/Scripts/python.exe`（有 opencv-python + jsonschema，无 maafw）；用户系统 py3.12 有 maafw==5.5.0。

## Agent 分发（2026-10-21 定的自包含方案）
- agent 硬依赖 `maafw`（`maa` 包）+ opencv，版本须与宿主一致（本项目 maafw==5.5.0 ↔ MFA v2.5.7）。不打包解释器/不装依赖 → agent 80ms 内导入崩溃 → `Agent process exited!` ×3。
- 现状：`tools/bundle_python.py` 产出 `deps/agent_python/`（embeddable py3.12.10 + maafw + opencv-headless，已 gitignore）；`install.py` 把它拷到 `install/libs/python/`；interface.json `agent.child_exec = "./libs/python/python.exe"`。
- 发版：`python tools/install.py <版本> win x86_64`。重复打包前要先把 `install/libs/python` 移走（否则会卡在覆盖确认）。

## 规范（改完必过）
- **agent 里所有 print 必须 `flush=True`**（宿主管道拉起，stdout 块缓冲会攒成 40s 一个时间戳，崩溃还丢缓冲）。
- **interface.json 改完必过 `deps/tools/interface.schema.json`**（jsonschema，additionalProperties=false）。踩过：option 不支持 `default_check`（只有 taskItem 支持）；长说明放 `description`，别堆在 label 括号里。
- **每个 CustomAction 节点的 `next` 必须带兜底**：next 列表评估时长上限 = 当前节点 timeout，全不命中就 node timeout → 整任务 failed，而 MFA 外壳仍打「任务已全部完成」。任何「点一下要等新页面」的节点，next 必须有自恢复路径（如 `Arena_MasterMatch.next = [WaitPick, MasterPage]`）。
- 排错看 `debug/on_error/<Node>_<时间>.png` = 节点超时那一刻的截图。

## agent 内识别的性能铁律（v1.2.10 实测）
- **`context.run_recognition` 一次 IPC 400~800ms**，禁止进战斗高频循环。现场症状：开局站桩 1.34s（自检 780ms + 首轮检查 500ms）、战斗中每 4s 空档 0.55~0.67s（Fight_Lose 走 IPC）。正常循环间隔仅 156ms。
- cv2 本地同模板同阈值耗时（1280 基准）：团队页 1.5ms、主城全屏 20ms、`_main_page_hit` 47ms、误点 4ms、全屏结算 24ms。凡是「模板+阈值」能表达的识别一律本地做。
- `_hit_any` 分发：`_RESULT_TPLS={Fight_Lose:lose_text, Fight_Win:win_text}` 走本地；`Fight_Substitute` 这种 Custom recognition 不传 gray 仍走 IPC。
- `next_result_check` 初值必须是 `now + result_interval`，不能是 0（否则进循环第一件事就查结算，白等半秒）。
- **`_load_tpl(rel)` 根目录是 `_ARENA_DIR`（image/arena）**：写 `lose_text` 而不是 `arena/lose_text`；只有子目录才带前缀（`wrong/wrong_shop`）。写错只打印「缺少模板」并静默返回 None。

## 战斗：结算防误点 + 误点退出（v1.2.7 / v1.2.10 定稿，勿改回旧版）
- 盲点节奏是用户实测调好的（`result_interval=4.0`），**不要在盲点循环里加额外检测**（用户明确否决过）。结算靠事后识别：每 4s 本地匹配 `lose_text`/`win_text`，命中即 break。
- **复核失败绝不能恢复盲点**：`_escape_misnav` 2s 复核失败 = 画面已被残留点击带走 = 人已在竞技场页/主城，恢复盲点就是乱按（现场商城→排行榜→竞技场→至尊赛→主城→装备）。现在失败一律 `return True` 收手，交给流水线认页面。
- **每轮循环最前面**先本地判三个「该收手」界面：大师团页（`master_title` ROI [380,20,560,90] ≥0.9 且任一卡位有勾）、主城（`main_arena_btn.png` 全屏 ≥0.85；主城 1.0 / 大师团页 0.489 / 选人页 0.516 / 战斗 0.496）、竞技场主页。命中立即 break，不等 4s 节流。
- 主城兜底：pipeline `Arena_HomePage`（≥0.7）→ `Arena_Enter_1v1`；大师任务在 interface 覆盖成 `Arena_Enter_Master`（否则会误进 1v1）。
- `Arena_UnknownBack` 是 Custom `ArenaSafeBack`：已在竞技场页/大师团页/主城就不点，只有真子页面才点红箭头（原先无条件 Click 是「返回键多按退到主城」的元凶）。
- `Arena_MainPage` 用正向识别（`arena_title`），**不能用 `Arena_LeaveSettle` 做兜底**（反向识别，战斗超时也命中 → 中途乱导航）。

## 大师竞技场·选人（v1.2.1 ~ v1.2.11）
- 15 格 = 6列×2行 + 第3行左 3 格；1280 基准 COLS=[90,202,313,478,590,701] ROWS=[19,128,237] CELL=99。禁用标 ban_stamp（红掩码）/ 选用白勾 master_check。
- 角色模板 `roles/c{栏}r{行}k{列}.png`（78×78）**直接在 99×99 格里滑动匹配**（0.96~0.99），禁止裁剪后 resize（v1.2.0 因此误判「角色池不重叠」）。双判据：≥0.86，或 ≥0.82 且比次佳高 ≥0.12。**阈值不能为兜遮挡降到 0.72**：别的角色最高 0.787，与 2P 遮挡的正确角色 0.784 无法区分 —— 宁可漏选走备选也不能点错人。
- 策略：禁用只要 2 个、选用只要 3 个；按输入顺序，凑够即停，多填的是备选；全试完才从第 1 格兜底。
- **点完不能固定 sleep 判标记**（UI 延迟 0.5~0.9s，误判→再点→toggle 取消，每格点两次实际 0 选中）：点前先查已有标记，点后轮询 `mark_timeout`/`mark_poll` 等标记出现，真等不到才补点。
- **阶段切换不能靠点「确定」推进**：禁用点确定只是我方声明完成，游戏要等对手确认（1.7~17.7s 空转，会耗掉前几个输入甚至拖到倒计时归零）。正解：不点确定，等新阶段锚点出现 —— 选用阶段 = 底部橙字「温馨提示：请选择格斗家」`arena/master_pick_tip.png` ROI [520,645,240,70] ≥0.75（正例 0.986~1.000 / 反例 ≤0.359）。
- **「阶段过渡」≠「页面离开」（v1.2.11）**：过渡期 1P 标签会短暂消失 0.5~1.5s，单次 `_left_pick_page` 必然误报 → 现场禁用后 6s 就判离开、整个选用阶段被跳过（0 选用直接进战斗）。现在 `_wait_pick_phase` 三态：True=提示命中 / False=**确证**离开（`_confirmed_away` 看见 battle_bar|battle_btn|arena_title|main_arena_btn|master_title）/ None=超时（1P 不在→直接选，不点确定；1P 还在→点确定走旧路径）。`_left_pick_page(confirm_gap=0.5)` 带二次复核，`_p1_score()` 模板缺失时返回 1.0（保守当还在）。
- 选人页锚点 `pick_wait`(1P) ROI [340,320,240,140] ≥0.8。真实帧标定：禁用阶段 `debug/master_frames/f_0010.jpg`（tip 0.252）、选用阶段 `f_0027.jpg`（tip 0.987）。
- **阶段时长（用户 2026-10-24 补充）：禁用 20s、选用 30s 倒计时**。所以 `pick_phase_timeout=45`（>20s+切换动画+0.4s 轮询），`ban_min_wait=22` 强制下限（**22s 内绝不点确定**：禁用里点等于白点，若已进选用则等于提前结束选人），`Arena_MasterPick.timeout=240s`；等待每 5s 打进度。**改这些必须同步改 interface.json「大师选人栏位」option 里那块 `custom_action_param`**（整块替换，只改 arena.json 会被覆盖回旧值）。
- 遗留：`_cell_taken` 不认 2P 角标（f_0010 格(1,1) 有蓝「2P」= 对手已占），兜底会去点它，待补模板。

## 大师团页（v1.2.6 / v1.2.8 / v1.2.9）
- 页面 = 左侧 tab「大师竞技场/大师团」+ 底部角色行 + 右下「匹配战斗」。`master_title` ROI [380,20,560,90] 0.997（战斗 HUD 0.166 / 选人页 0.235）。**这页没有能跳走的入口**，但盲点坐标压在 `normal`(1137,632)=匹配战斗、`skill1`(981,643)=一键上阵 上，会点掉角色。
- 卡位中心 x=126/262/400/537/674（间距 137）、y=640；点击点 `(cx,682)`（避开正中半径 22 的勾）。
- **掉人判据（v1.2.9 用户纠正，勿再用「没勾」）**：底部行是**角色列表**，白勾只表示该角色在上阵队伍里，**有角色没勾是正常状态**；真掉人 = 角色卡整张消失（位子空了）。判据 `arena/master_card_ssr.png`（62×36 卡片左上 SSR 标签）在 `(cx-100,cy-56,200,70)` 匹配，有卡 0.92~1.00 / 空白 0.20~0.65 → 阈值 0.85。5 格都有卡就 0 点击直接匹配（现场帧：卡 OOOOO、勾 OOO-O → 0 点击是对的）；空卡位才点 `(cx,682)` 等卡出现，点不动记 `_TEAM_STUCK` 180s。`max_drags` 默认 0（不自动拖角色行）。
- **「匹配战斗」target 不能框整个菱形**：原 `[1121,575,92,92]` 会点到菱形上部拳头图标区（不响应）→ WaitPick 20s 不命中 → 节点超时 → 队列静默结束。现场 4 次点击 y=640/635/635 成功、y=587 失败。**响应区 y≥634**，安全 target = `[1135,634,80,35]`，timeout 30s。

## 协作偏好 / 工具
- base pipeline 参数保持 MaaFramework 默认值（如 `quick_pick:true`），个性化用 interface.json option override，不改 base 默认。
- MaaDebugger v1.17 是裸流水线调试器：不渲染 select/input，用 Pipeline Override JSON 注参；**不读 interface.json、不拉起 agent**。要连 agent 得先手动 `assets> libs\python\python.exe agent\main.py <identifier>`，再点 CONNECT（只等 5s）。
- git 推送代理时好时坏（127.0.0.1:7897 / 58867），失败就换端口或直连重试。
