"""Agent 模式入口（官方文档的"方案二"，标准模板写法）。

由 MaaPiCli / 通用 UI 通过 assets/interface.json 里的 agent 配置拉起：
    "agent": { "child_exec": "python", "child_args": ["./agent/main.py"] }
进程 cwd 为 interface.json 所在目录（assets/）。

调试方式：
    - MaaDebugger / MaaPiCli 跑任务时，会自动拉起本进程并传入 socket_id；
    - 也可以手动联调：先运行本文件拿 socket_id 参数说明，再用调试器连接。
"""

import os
import sys

# 便携 Python（embeddable）的 _pth 不会把脚本所在目录加入 sys.path，
# 故显式把 main.py 所在目录（assets/agent）塞进 path，保证 import register / 同目录模块能找到。
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# —— 关键修复：强制 stdout/stderr 为 UTF-8 ——
# 中文 Windows 下 Python 默认用 GBK 编码 stdout；agent 日志里打印的 ✓/✗/⚠ 等符号
# 不在 GBK 字符集，会抛 UnicodeEncodeError 并被 maafw 的 ctypes 回调"Exception ignored"吞掉，
# 导致 CustomAction 中途崩溃（例如只选完第 1 个角色就跳到战斗）。无论宿主如何启动本进程，
# 都在此处把编码重设为 UTF-8（errors=replace 保证任何字符都不会再炸）。
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError, OSError):
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

from maa.agent.agent_server import AgentServer
from maa.toolkit import Toolkit

import register


def main():
    Toolkit.init_option("./")  # cwd 是 assets/，日志/可视化结果输出到 assets/config/、debug/

    if len(sys.argv) < 2:
        print("Usage: python main.py <socket_id>")
        print("socket_id 由宿主（MaaPiCli / 通用 UI）自动传入。")
        sys.exit(1)

    socket_id = sys.argv[-1]

    register.register_all(AgentServer)  # 与嵌入模式共用同一份注册清单

    AgentServer.start_up(socket_id)
    AgentServer.join()
    AgentServer.shut_down()


if __name__ == "__main__":
    main()
