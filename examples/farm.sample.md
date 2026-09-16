# farm.sample.json 使用说明

这个文件演示怎么把你的刷本流程和战斗流程（Fight_*）接起来，是【骨架】不是成品。

## 用法

1. 按你的游戏改各节点的识别/动作参数（现在都是占位）：
   - Farm_Enter：识别"挑战/开始"按钮 → Click
   - Farm_Settle：结算页点"领取/确定"
   - Farm_Retry：点"再次挑战/再来一次"
   - Farm_NoStamina：识别"体力不足/次数不足" → StopTask 结束任务
2. 把本文件复制为 `assets/resource/pipeline/farm.json`
3. 接线二选一：
   - a) 直接改 combat.json 里 `Fight_Win` 的 next 为 `["Farm_Settle"]`、
        `Fight_Lose` 的 next 为 `["Farm_Retry"]`；
   - b) 不改 combat.json，在 main.py 的 post_task 处用 pipeline_override 覆盖
        （main.py 里已留注释示例）。
4. 跑起来：
   - 嵌入模式：`python main.py --entry Farm_Enter`（或把 DEFAULT_ENTRY 改成 Farm_Enter）
   - Agent 模式：在 interface.json 的 task 列表加一条
     `{ "name": "刷本", "entry": "Farm_Enter" }`

## 注意

- pipeline JSON 里**不能写注释键**（如 "// 说明"），会导致解析失败
- 节点名全局唯一：farm.json 里不要用 Fight_* 前缀
- Fight_Win / Fight_Lose 是 OCR 节点，需要先放 OCR 模型（见 README 6.3）
