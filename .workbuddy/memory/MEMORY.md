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

## 协作偏好
- base pipeline 参数保持 MAAFramework 规范默认值（如 quick_pick:true），个性化由 interface.json 选项 override 覆盖，不要改 base 默认。
- MaaDebugger v1.17 是裸流水线调试器：不渲染 select/input 选项，用 Pipeline Override JSON 注参。**它完全不会读 interface.json、也不会拉起 agent**（源码 grep 无 interface/child_exec）——Agent Identifier 的 CONNECT 只是创建带该 identifier 的监听端并等 5s（`set_timeout(5000)`），必须先在终端手动跑 `assets> libs\python\python.exe agent\main.py <identifier>`（identifier 用输入框里那个，持久保存在 STORAGE），agent 存活期间点 CONNECT 才能连上。
