"""你自己的流程（进本/结算/再挑战/领奖…）的自定义动作放这个文件。

战斗相关动作在 combat_actions.py，一般不用动。
main.py 里记得把这里的动作注册进 Resource（见 main.py 的"你的流程"段）。
"""

from __future__ import annotations

import json
import time

from maa.context import Context
from maa.custom_action import CustomAction


class FarmTap(CustomAction):
    """通用点按：你的流程里点按钮/跳过对话/领奖励都能用。

    custom_action_param:
    {
        "x": 640, "y": 360,   // 目标点（1280 基准）。省略则点识别框中心
        "hold_ms": 80          // 按住时长，长按跳过剧情可设 800+
    }
    """

    def run(self, context: Context, argv: CustomAction.RunArg) -> bool:
        param = json.loads(argv.custom_action_param) if argv.custom_action_param else {}
        x, y = param.get("x"), param.get("y")

        if (x is None or y is None) and argv.box:
            x = argv.box[0] + argv.box[2] // 2
            y = argv.box[1] + argv.box[3] // 2
        if x is None or y is None:
            return False

        hold_ms = int(param.get("hold_ms", 80))
        ctl = context.tasker.controller
        ctl.post_touch_down(int(x), int(y)).wait()
        time.sleep(hold_ms / 1000)
        ctl.post_touch_up().wait()
        return True


# 照上面的样子继续加你的动作即可，例如：
# class FarmEnterDungeon(CustomAction): ...
