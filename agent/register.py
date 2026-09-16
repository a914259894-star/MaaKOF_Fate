"""统一注册自定义动作/识别 —— 两种运行模式共用同一份代码：

- 嵌入模式（main.py，官方文档的"方案三"）：register_all(resource)
- Agent 模式（MaaPiCli/通用 UI，官方文档的"方案二"）：register_all(AgentServer)

新增动作/识别时只改这里一处，两个入口自动生效。
"""

from combat_actions import (
    CombatFighter,
    CombatMove,
    CombatNormalAttack,
    CombatPressKey,
)
from combat_recognition import CombatAlwaysHit, SkillReadyBright
from farm_actions import FarmTap
from arena_actions import ArenaPick

# (注册名, 实例)
CUSTOM_ACTIONS = [
    ("CombatFighter", CombatFighter()),
    ("CombatNormalAttack", CombatNormalAttack()),
    ("CombatMove", CombatMove()),
    ("CombatPressKey", CombatPressKey()),
    ("FarmTap", FarmTap()),  # 你的流程动作在这里加
    ("ArenaPick", ArenaPick()),
]

CUSTOM_RECOGNITIONS = [
    ("CombatAlwaysHit", CombatAlwaysHit()),
    ("SkillReadyBright", SkillReadyBright()),
]


def register_all(target) -> None:
    """target: Resource 或 AgentServer（两者都有 register_custom_* 接口）"""
    for name, obj in CUSTOM_ACTIONS:
        target.register_custom_action(name, obj)
    for name, obj in CUSTOM_RECOGNITIONS:
        target.register_custom_recognition(name, obj)
