"""统一注册自定义动作/识别 —— 两种运行模式共用同一份代码：

- 嵌入模式（main.py，官方文档的"方案三"）：register_all(resource)
- Agent 模式（MaaPiCli/通用 UI，官方文档的"方案二"）：register_all(AgentServer)

新增动作/识别时只改这里一处，两个入口自动生效。
"""

import cv2

# —— 降 CPU 峰值（v1.2.18，不改任何判定）——
# OpenCV 默认按物理核数开线程池（本机实测 cv2.getNumThreads() = 32）。而本工程所有
# 图像操作都是「1280x720 图上的小 ROI 匹配」，单次 0.5~15ms，多线程的唤醒/汇合开销
# 比计算本身还大（实测全屏 85x85 匹配：32 线程 15.3ms，1 线程 14.3ms，单线程反而更快）。
# 更关键的是：模拟器 + 游戏 + MAA 同时跑时，32 线程会在每个 matchTemplate 上抢核，
# 让整机更卡。统一压到 1 线程：判定结果完全不变，占用曲线更平。
cv2.setNumThreads(1)


# 省资源的 MaaFramework 运行选项（v1.2.18）
# 为什么需要代码兜底：`config/` 不入库，新装/从仓库直接跑的环境里没有
# config/maa_option.json，MaaFramework 会用自带默认值 logging=true —— 它把每一帧的
# TRC/DBG 行（带完整 JSON）写进 debug/maa.log，现场实测 **5 分钟 155MB / 158284 行**，
# 磁盘一直写、CPU 一直拼字符串，这是「开多点东西就卡」最直接的一条。
_MAA_OPTION = {
    "draw_quality": 85,
    "logging": False,       # 关掉 debug/maa.log（排查时手动改回 true）
    "save_draw": False,
    "save_on_error": True,  # 出错仍然存图，排查靠 debug/on_error/
    "stdout_level": 2,
}


def ensure_maa_option(path: str = "config/maa_option.json") -> None:
    """只在文件不存在时补一份省资源默认值；用户手动改过的一律不动。

    路径相对 interface.json 所在目录（agent 模式和嵌入模式的 cwd 都是它）。
    """
    import json
    import os

    if os.path.exists(path):
        return
    try:
        d = os.path.dirname(path)
        if d:
            os.makedirs(d, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(_MAA_OPTION, f, ensure_ascii=False, indent=4)
            f.write("\n")
        print(f"[register] 已生成 {path}（logging=false，想抓 maa.log 就把它改回 true）",
              flush=True)
    except OSError as e:
        print(f"[register] 写 {path} 失败（不影响运行）: {e}", flush=True)

from combat_actions import (
    CombatFighter,
    CombatMove,
    CombatNormalAttack,
    CombatPressKey,
    ArenaSafeBack,
)
from combat_recognition import CombatAlwaysHit, SkillReadyBright
from farm_actions import FarmTap
from arena_actions import ArenaPick, MasterPick, MasterTeamFix

# (注册名, 实例)
CUSTOM_ACTIONS = [
    ("CombatFighter", CombatFighter()),
    ("CombatNormalAttack", CombatNormalAttack()),
    ("CombatMove", CombatMove()),
    ("CombatPressKey", CombatPressKey()),
    ("FarmTap", FarmTap()),  # 你的流程动作在这里加
    ("ArenaPick", ArenaPick()),
    ("MasterPick", MasterPick()),  # 大师竞技场选人（先禁用后选用）
    ("MasterTeamFix", MasterTeamFix()),  # 大师团页：补回被盲点误点下阵的角色
    ("ArenaSafeBack", ArenaSafeBack()),  # 兜底返回：已离开战斗就不点返回
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
