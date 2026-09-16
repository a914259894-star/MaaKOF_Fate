# 发包指南（MaaKOF_Fate v1.0.0）

按 MaaFramework 官方方式（[MaaPracticeBoilerplate](https://github.com/MaaXYZ/MaaPracticeBoilerplate) 模板 + GitHub Actions）检查 UI 并发布。

---

## 一、UI（interface.json）检查结果

对照官方协议 [3.3-ProjectInterfaceV2](https://github.com/MaaXYZ/MaaFramework/blob/main/docs/zh_cn/3.3-ProjectInterfaceV2%E5%8D%8F%E8%AE%AE.md) 逐项检查，发现并修复 4 处问题：

| # | 问题 | 修复 |
|---|------|------|
| 1 | 文件带 `//` 注释（JSON 不允许；部分客户端用严格解析器会直接加载失败） | 重写为**严格 JSON**，说明全部移到本指南 |
| 2 | 任务列表是旧状态：竞技场任务写着「到战斗页为止」，而现在已是全链路循环 | 任务列表更新（见下） |
| 3 | 大师模式循环依赖调试器手工 Override（`Arena_LeaveSettle` 的 next 指回 Master），发包后用户没法配置 | 大师任务改用协议的 **task 级 `pipeline_override`** 内置该覆盖 |
| 4 | 缺 `title` / `welcome` 等发包后对用户有用的字段 | 补充（首次使用须知：从主界面运行、分辨率、Python 依赖等） |

**发包后的任务列表**（用户在 MFAAvalonia / MaaPiCli 里看到的）：

| 任务 | 入口 | 说明 |
|------|------|------|
| 竞技场·1v1 循环 ✅默认勾选 | `Arena_Enter_1v1` | 全链路循环直到手动停止 |
| 竞技场·大师 循环 | `Arena_Enter_Master` | 内置 `Arena_LeaveSettle.next → [WaitBattle, Mode_Master, Enter_Master]` |
| 战斗·单场挂机 | `Fight_Once` | 仅已进战斗画面时用（DirectHit 入口，勾了立刻点） |

其余字段核对无误：`interface_version: 2`、`controller`（Adb + `display_short_side: 720`，V2 协议下截图/输入方式由框架自动选择）、`resource.path: ["./resource"]`、`agent.child_exec: python + ./agent/main.py`（CWD=interface.json 所在目录）。

> 任务 `name` 是配置键（建议 ASCII），`label` 才是界面显示名——已按此规范命名。

---

## 二、本次新增的发包文件

```
MaaKOF_Fate/（项目根）
├── .github/
│   ├── workflows/install.yml      # 官方 CI：tag v* → 自动下载 MaaFW + MFAAvalonia →
│   │                              #   tools/install.py 组装 → 8 平台 zip → GitHub Release
│   └── cliff.toml                # 官方 changelog 配置（★记得把 owner/repo 改成你的）
├── agent/                         # 打包用 agent 副本（install.py 从根目录 agent/ 拷贝）
│   └── （与 assets/agent/ 完全一致的 6 个 .py）
├── tools/
│   ├── install.py                 # 官方版适配：去掉 OCR 模型步骤（本项目全模板识别，
│   │                              #   无 OCR 节点）；README/LICENSE 缺失时跳过不报错
│   └── requirements.txt           # json-with-comments
├── .gitignore                     # install/ deps/ MFA/ debug/ config/ 等不入库
├── LICENSE                        # MIT（按需自行修改）
└── RELEASE.md                       # 本文件（发包指南）
```

> **agent 双副本说明**：`agent/`（根）与 `assets/agent/` 内容必须保持一致——
> 根目录 `agent/` 供打包（install.py）和 MaaDebugger（从项目根启动）使用；
> `assets/agent/` 供 MaaPiCli 放在 assets/ 里运行时使用。
> **每次改 .py 后两处都要拷**（与改 .py 必须重启 agent 的老规矩一致）。

---

## 三、官方发包流程（GitHub Actions，推荐）

1. **建仓库**：在 GitHub 用 `Use this template` 基于 [MaaPracticeBoilerplate](https://github.com/MaaXYZ/MaaPracticeBoilerplate) 创建仓库（或直接把本地已有模板仓库推上去），然后把本工程这些文件按上面结构放入仓库并提交。

   > **从模板建仓的合并注意事项**（模板自带的这几份要用本工程的版本覆盖）：
   > - `tools/install.py` —— **建议覆盖**：官方版在 CI 里会从 `assets/MaaCommonAssets` 子模块把
   >   OCR 模型拷进发布包（即模板 README 说的「workflow 自动配置 OCR」）。本项目 20 个识别节点
   >   全部是 TemplateMatch/DirectHit/Custom，**零 OCR 节点**，发布包不需要 OCR 模型——本工程版
   >   去掉了这一步，每个平台包省 ~15MB。若保留官方版也能跑通（从模板建仓自带该子模块），
   >   只是发布包里多一份没人用的模型；但若仓库缺子模块，官方版会 `exit(1)` 直接 CI 失败。
   > - `.github/workflows/install.yml` + `.github/cliff.toml` —— 工件名 `MaaKOF_Fate-*`；
   >   cliff.toml 记得把 owner/repo 改成自己的（只影响 changelog 里的链接与 @，不影响发版成败）
   > - `assets/interface.json` —— 模板那份 agent 段是注释掉的，任务是示例任务
   >
   > **本地 OCR 模型**（当初按模板说明下载到 `assets/resource/model/ocr/` 的）：本项目已用不上
   > （结算识别等已全部改为模板匹配），本地留着无妨。注意模板 README 声称 .gitignore 会忽略该
   > 目录，但当前模板的 .gitignore 实际**没有**这行——本工程的 .gitignore 已补上。若此前已经
   > commit 过，用下面的命令移出版本库（保留本地文件）：
   > `git rm -r --cached assets/resource/model/ocr`
   >
   > 合并后 `git push` 会在分支上先触发一轮 CI 试打包（只出 artifact 不发 Release），正好当冒烟测试。

   ```bash
   git add .
   git commit -m "feat: 竞技场全链路 + 官方发包基座"
   git push origin HEAD -u
   ```

2. **改两处占位**：
   - `.github/cliff.toml` 顶部的 `owner` / `repo` 改成你的仓库；
   - 想要客户端自动更新检查的话，在 `assets/interface.json` 加 `"github": "https://github.com/<你>/<仓库名>"`。

3. **开 CI 写权限**（仅第一次）：仓库 `Settings` → `Actions` → `General` → `Workflow permissions` → `Read and write permissions` → Save。

4. **打 tag 发版**：

   ```bash
   git tag v1.0.0
   git push origin v1.0.0
   ```

   CI 会自动：下载对应平台的 MaaFramework 与 MFAAvalonia → `tools/install.py` 组装 `install/`（UI + MaaFW 运行库 + resource + interface.json + agent）→ 生成 8 个平台包（win/macos/linux/android × x64/arm64）→ 发布到 GitHub Releases 并附 changelog。

5. **分发**：普通 Windows 用户给 `MaaKOF_Fate-win-x86_64-v1.0.0.zip` 即可，解压即用（仍需电脑装有 Python 并 `pip install maafw opencv-python`——agent 是 Python 写的）。

> 版本号规范：tag 含 `alpha/beta/rc/dev` 会自动标记为预发布。

---

## 四、本地手动打包（不走 CI 的备选）

```bash
# 1. 下载 MaaFramework 对应平台包，解压到项目根 deps/
#    https://github.com/MaaXYZ/MaaFramework/releases  → MAA-win-x86_64-*.zip
#    解压后应有 deps/bin/、deps/share/MaaAgentBinary/

# 2. 组装（在项目根执行）
python -m pip install json-with-comments
python tools/install.py v1.0.0 win x86_64

# 3. 产物在 install/，压缩即得与 CI 相同结构的包（不含 MFAAvalonia UI，
#    如需 UI 请按 install.yml 中 MFA 段自行下载合入）
```

---

## 五、发包前自检清单

- [ ] `assets/interface.json` 是**严格 JSON**（`python -m json.tool assets/interface.json` 通过）
- [ ] 任务的每个 `entry`、`pipeline_override` 里的节点名都存在于 pipeline
- [ ] `agent/` 与 `assets/agent/` 六个 .py **内容一致**（本次已校验）
- [ ] MaaDebugger 跑通：Entry=`Arena_Enter_1v1` 无 Override 全链路循环 ≥2 场（含结算确认）
- [ ] 大师任务冒烟：`Arena_Enter_Master`（需已解锁）循环回大师场
- [ ] 模板齐全：`resource/image/arena/`（含 roles 90 张、结算 3 张、战斗 2 张、选人 3 张）

## 六、已知边界（写给用户的话，也留在 welcome 里）

- 仅适配 16:9；MuMu 2560x1440/640DPI 实测通过
- 快速选人 = 「近期使用」最左一列三人；换阵容需改 `Arena_Pick.quick_targets`
- 战斗为固定节奏盲点（技能1/2/3→大招→秘笈→普攻×3），仅替身走识别
- 刷本（farm）不在本包范围，骨架见 `examples/farm.sample.json`
