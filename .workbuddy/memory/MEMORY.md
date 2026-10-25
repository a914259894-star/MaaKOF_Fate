# MaaKOF_Fate 笔记

## 结构 / 环境
- agent 代码在 `assets/agent/`（**没有根 agent/**）；`interface.json` 的 `agent.child_args` 必须是 `./agent/main.py`（CWD=assets/）。根 `main.py` 是嵌入模式入口。
- venv：`~/.workbuddy/binaries/python/envs/default/Scripts/python.exe`（cv2+jsonschema，无 maafw）；用户 py3.12 有 maafw==5.5.0。便携 py 由 `tools/bundle_python.py` 产到 `deps/agent_python`，`install.py` 拷进 `install/libs/python`。
- 发版 `python tools/install.py <ver> win x86_64`；**打包前先移走 install/libs/python**（否则卡在覆盖确认）。覆盖包：interface.json + agent/*.py + 改过的 pipeline/模板。

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

## 选人
- 大师 15 格 = 6×2 + 第3行左3：COLS=[90,202,313,478,590,701] ROWS=[19,128,237] CELL=99。角色模板 `roles/c{栏}r{行}k{列}.png` 78×78 **直接在 99 格内滑动**（禁止 resize）。阈值 ≥0.86 或（≥0.82 且比次佳高 0.12）；**不能为遮挡降到 0.72**（别的角色最高 0.787）。
- 策略：禁 2 选 3，按输入顺序凑够即停，全试完才从第 1 格兜底。
- 点后轮询等标记，不能固定 sleep；点前先查已有标记防 toggle。
- **阶段切换不能靠点「确定」**：等新阶段锚点（大师选用 = `master_pick_tip` ROI[520,645,240,70] ≥0.75）。禁用 20s + 选用 30s → `pick_phase_timeout=45`、`ban_min_wait=22`、`Arena_MasterPick.timeout=240s`。`_wait_pick_phase` 三态：True=提示 / False=`_confirmed_away` / None=超时。
- 遗留：`_cell_taken` 不认 2P 蓝角标。

## 大师团页
- 卡位 x=126/262/400/537/674、y=640；点击 `(cx,682)`。
- **掉人判据 = 角色卡整张消失**（`master_card_ssr` 在 `(cx-100,cy-56,200,70)` ≥0.85），**不是「没勾」**。
- 「匹配战斗」响应区 **y≥634**，target=`[1135,634,80,35]`（框大菱形会点到拳头区）。

## 误落子页面 / 选人卡住（v1.2.13）
- `Arena_RankPage`：wrong_rank ROI[0,0,340,110] ≥0.8 → Custom `ArenaRankBack` 点红箭头回主城；连 3 次进不去 `return False`。
- `Arena_StuckRecover`：Custom `ArenaStuckBack` 静默观察 15s，见战斗/竞技场/大师团/主城/结算即放行；**只在 pick_wait 落 ROI[340,320,240,140] ≥0.85** 才点返回。**1P 必须限定 ROI，全屏找会在战斗 HUD 命中**。
- 两者已挂进 1v1 / 大师两条导航链的全部 next。
- `Arena_RankUp`：胜利后「KOF段位提升」全屏弹窗，**顺序=结算页→点确认→它，不顶替结算页**。`rank_up_title` ROI[300,60,720,130] ≥0.8（弹窗 1.0/其它 ≤0.248）→ 点确认 (639,611)。确认键不在盲点 → CombatFighter `_rank_up_hit` 命中即 break；挂 Fight_Once/SettleOk/LeaveSettle/UnknownBack 首位（Fight_Win/Lose 不需要）。

## 工具
- base pipeline 用默认值，个性化走 option。
- MaaDebugger 不读 interface.json、不拉 agent；先起 agent 再 CONNECT。
- git 推送代理 7897/58867 时好时坏，失败换端口或直连。
- 排错看 `debug/on_error/<Node>_<时间>.png`。
