"""Agent 模式入口（官方文档的"方案二"，标准模板写法）。

由 MaaPiCli / 通用 UI 通过 assets/interface.json 里的 agent 配置拉起：
    "agent": { "child_exec": "python", "child_args": ["./agent/main.py"] }
进程 cwd 为 interface.json 所在目录（assets/）。

调试方式：
    - MaaDebugger / MaaPiCli 跑任务时，会自动拉起本进程并传入 socket_id；
    - 也可以手动联调：先运行本文件拿 socket_id 参数说明，再用调试器连接。
"""

import sys

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
