"""把 MFAAvalonia 的 GUI 偏好预置进发布包（默认关闭实时画面）。

为什么要在打包时写：

- 「实时画面」卡片会以固定频率（``UI.LiveView.RefreshRate``）对模拟器做整屏
  截图并转成位图推给 UI，与 pipeline 自己的识别截图抢同一个 ADB / MuMu
  ``external_renderer_ipc.dll`` 通道。关掉后挂机会明显更跟手。
- 开关关掉后，GUI 也不再为它单独建一个截图专用 tasker
  （``UseSeparateScreenshotTasker``），少一个与主控制器并发访问渲染器的角色。

写进全局 ``config/config.json`` 的原因：本项目用的 MFAAvalonia v2.5.7 把全局
键和实例键都放在这一个文件里，``UI.LiveView.EnableLiveView`` 与
``UI.MainWindow.Width`` 属于同一张键表，因此写在顶层即可被读取；换成更新的
GUI（把实例拆到 ``config/instances/*.json``）时，实例文件里找不到的键也会回退
到全局值，同样生效。反过来写实例文件不行——那份文件是整字典回写的，首次
运行后就会被 GUI 自己的内存状态覆盖。
"""

from __future__ import annotations

import json
from pathlib import Path

#: 需要预置的 MFAAvalonia 全局配置项
CONFIG_PREFS: dict[str, object] = {
    # 关掉主界面「实时画面」卡片和它的定时截图轮询
    "UI.LiveView.EnableLiveView": False,
    # 不再为实时画面单独建截图 tasker（避免与主控制器并发抢渲染器）
    "UseSeparateScreenshotTasker": False,
}

CONFIG_RELATIVE_PATH = Path("config") / "config.json"


def apply_config_prefs(install_path: Path | str) -> Path:
    """把 :data:`CONFIG_PREFS` 合并进 ``install_path`` 下的全局配置。

    已存在的配置只做合并，保留用户/打包时的其它键；文件损坏时当作空字典重写。
    返回写入的文件路径。
    """
    path = Path(install_path) / CONFIG_RELATIVE_PATH

    data: dict[str, object] = {}
    if path.exists():
        try:
            loaded = json.loads(path.read_text(encoding="utf-8-sig"))
            if isinstance(loaded, dict):
                data = loaded
        except (OSError, ValueError):
            data = {}

    data.update(CONFIG_PREFS)

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return path


if __name__ == "__main__":
    import sys

    target = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).parent.parent / "install"
    print(f"Applied MFA config prefs -> {apply_config_prefs(target)}")
