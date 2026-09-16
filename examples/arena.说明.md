# 竞技场接入与测试指南（arena.json + ArenaPick）

流程：主界面 → 点"竞技" → 选模式（1V1排位 / 大师竞技场）→（真人等匹配 / 人机直接进）
→ 选人（3 个角色）→ 点确定 → 等进入战斗页 → 结束（可接 Fight_Once 开打）。

---

## 一、接入本地项目（D:\Projects\MaaKOF_Fate\MaaKOF_Fate）

从本工程（maa-fighter/）拷贝 5 样东西，在**你的项目根**执行：

```powershell
# ① 选人动作（新文件）
cp ..\maa-fighter\assets\agent\arena_actions.py  agent\

# ② 注册清单（如果本地 register.py 没改过，直接覆盖）
cp ..\maa-fighter\assets\agent\register.py  agent\

# ③ 流水线（新文件）
cp ..\maa-fighter\assets\resource\pipeline\arena.json  assets\resource\pipeline\

# ④ 模板图（整个目录，7 张）
cp -Recurse -Force ..\maa-fighter\assets\resource\image\arena  assets\resource\image\

# ⑤ interface.json（如果本地改过，就不要覆盖，手动给 task 加两条，见下）
cp ..\maa-fighter\assets\interface.json  assets\
```

**如果 register.py / interface.json 本地改过不想覆盖**，手动加这几行：

register.py：
```python
from arena_actions import ArenaPick          # 加在 import 区

# CUSTOM_ACTIONS 列表里加一项：
    ("ArenaPick", ArenaPick()),
```

interface.json 的 task 数组里加：
```json
{ "name": "竞技场·1V1排位（到战斗页为止）", "entry": "Arena_Enter_1v1" },
{ "name": "竞技场·大师竞技场（到战斗页为止）", "entry": "Arena_Enter_Master" }
```

**接入后必须重启 agent 进程**（arena_actions.py 是新的 .py）：Ctrl+C 杀掉旧的
`python agent/main.py <id>`，重新拉起，然后网页 Agent 区重新 Connect。

---

## 二、MaaDebugger 测试（Task Entry / Pipeline Override 直接复制）

前置：模拟器正常启动；MaaDebugger 已 Adb Connect、Resource Load（`./assets/resource`）、
Agent Connect（agent 进程已拉起）。

### 测试 1：模式卡片识别（不用从主界面跑，验证模板）

**手动进到竞技区界面**（就是"竞技区"标题那个页面），然后：

- Task Entry：
```
Arena_Mode_1v1
```
- Pipeline Override：
```json
{"Arena_Mode_1v1": {"next": []}}
```
- 点 Start。预期：识别到"1V1排位模式"文字带 → 点击卡片 → 因 next 空，任务很快结束。
- 识别记录里应有 Arena_Mode_1v1 一条 ✅（点开看 draw 图，框应套住文字带）。

### 测试 2：大师卡片识别（同一界面）

- Task Entry：
```
Arena_Mode_Master
```
- Pipeline Override：
```json
{"Arena_Mode_Master": {"next": []}}
```
- 预期同上（会真的点进大师竞技场，进去后手动退出来即可）。

### 测试 3：主界面全链路（人机/真人都走到选人完成为止）

**手动退回主界面**（能看到右侧"竞技"按钮），然后：

- Task Entry：
```
Arena_Enter_1v1
```
- Pipeline Override：
```json
{"Arena_WaitBattle": {"next": []}}
```
- 预期：点竞技 → 点 1V1 → （真人会等匹配，最长 120s）→ 等选人页 → 自动点选 3 个角色
  （左下角出现头像）→ 自动点"确定" → 进入加载/战斗页后任务结束。
- picks 填 **[栏,行,列]**（1 起，栏 1~10），对应 `image/arena/roles/c{栏}r{行}k{列}.png`。
  **90 张角色模板已按 4 张全角色截图批量裁好并逐张验证**（1.png=栏1-3、2.png=栏4-6、
  3.png=栏7-9、4.png=栏10；c8/c9 双源交叉 18 对 ≥0.969；冒充矩阵 max 0.677<0.72；
  全图定位模拟 90/90）。运行逻辑：截图找目标头像 → 找到就点，找不到右拖一屏再找。
  亮暗无影响：暗色模板对亮态同角色也能 0.97+ 命中（灰度归一化匹配对亮度仿射变化免疫）。
  **补裁须知**：选人界面的格子停靠位置每次截图有 ±30px 横向漂移（3.png 实测 dx=32、
  4.png dx=30、1/2.png dx=5；行偏移恒为 dy=[8,2,6]）。裁新图必须先标定该图的 dx
  （在 1280 基准下量卡片左缘相对 x=131/516/911 的差），不能沿用旧值；
  examples/roles总览_90.png 是全部 90 张的验收图，换账号/大版本更新后重新对照。

### 测试 4：全链路 + 自动开打（接上战斗）

- Task Entry：
```
Arena_Enter_1v1
```
- Pipeline Override：
```json
{"Arena_WaitBattle": {"next": ["Fight_Once"]}}
```
- 预期：选人确认 → 进战斗页 → 直接开始自动战斗。正式排位会有结算页，
  **OCR 模型三件套必须在位**（assets/resource/model/ocr/），否则打完不会停。

### 常见问题速查

| 现象 | 原因 | 处理 |
|---|---|---|
| Arena_Mode_* 一直 ❌ | 模板没识别到 | 界面必须停在竞技区；还不行把该节点 threshold 0.7 降到 0.6 |
| 选错角色 / 找不到角色 | roles 模板裁偏或该角色不在前 max_drags 屏 | 重裁模板（贴头像方块）；识别分数看控制台，不稳把 threshold 降到 0.65 |
| 点了没选上 | 已选框确认失败重试 3 次后动作返回 False | 看 MaaDebugger 控制台 `[ArenaPick]` 日志；确认左下角已选框位置没被其他 UI 挡 |
| 点确定后没反应 | 确定按钮没出现（3 个没都选上） | 先单独测选人：Override 加 `{"Arena_Pick": {"next": []}}` |
| 匹配等太久超时 | 真人匹配 >120s | Arena_WaitPick 的 timeout 调大到 300000 |
| Stop 后再 Start 不动 | MaaDebugger 生命周期缺陷 | 重启 agent 进程 + 重新 Agent Connect + 重新 Start |

---

## 三、参数基准（1280x720，改坐标时对照 grid_*.png）

- 模式文字带模板：1v1=(491,225) 尺寸 267x44；master=(638,543) 尺寸 124x36
  （**只取文字带，不带积分数字/日期**——那些会变，全卡模板会失效）
- 选人格子区（匹配 roi）：[0, 0, 1280, 370]；翻页慢拖：(829,230)→(433,230)
  ms1200/steps10/settle1200（拖过头已无害，识别循环会重新定位）；确定按钮 (622,638)
- 左下已选框：(39,668)/(108,668)/(177,668)，饱和度 >60 判"已选"
- 全部模板已从你的真机截图裁好并归一到 1280 基准；**赛季/版本更新界面变化后重截**
  （VSCode 插件裁图后记得 ÷1.0852 缩回 1280 基准）
