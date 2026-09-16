"""入口（嵌入模式，官方文档的"方案三"）—— 官方模板（MaaPracticeBoilerplate）专用版。

与 maa-fighter/main.py 的唯一区别：
    sys.path 指向 agent/（官方模板的 agent 目录在项目根，不在 assets/ 下）。

用法：
    python main.py                                   # 自动找设备，跑默认入口（Fight_Once）
    python main.py --address 127.0.0.1:16384         # 指定模拟器/设备地址
    python main.py --entry Fight_Once                # 只打一场（跳过进本流程）
    python main.py --entry Farm_Enter                # 完整刷本循环（farm.json 就绪后）
    python main.py --shot shot.png                   # 只截一张图，用于校准坐标/截模板

Agent 模式（方案二）：由 assets/interface.json 的 agent 段拉起 agent/main.py，
无需本文件；自定义动作/识别两模式共用（agent/register.py）。

依赖：pip install maafw opencv-python
注意：用到 OCR 节点前，先把 OCR 模型放到 assets/resource/model/ocr/ 下
     （det.onnx / rec.onnx / keys.txt，ppocr_v6 下载地址见模板 docs/zh_cn/develop/how_to_develop.md），
      否则 OCR 节点运行时才会报模型缺失。
"""

from __future__ import annotations

import argparse
import os
import sys

import cv2

# 自定义模块在 agent/ 下（官方模板布局：项目根/agent/）
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "agent"))

from maa.controller import AdbController
from maa.resource import Resource
from maa.tasker import Tasker
from maa.toolkit import Toolkit

from register import register_all  # 战斗 + 你的流程的动作/识别统一注册

# 完整刷本流程就绪后，把默认入口改成你的入口节点
DEFAULT_ENTRY = "Fight_Once"

# 接线方式二选一（详见 examples/farm.sample.json 顶部说明）：
#   a) 直接改 combat.json 里 Fight_Win / Fight_Lose 的 next；
#   b) 不动 combat.json，在 post_task 时用 pipeline_override 覆盖：
#      tasker.post_task("Farm_Enter", pipeline_override={
#          "Fight_Win":  {"next": ["Farm_Settle"]},
#          "Fight_Lose": {"next": ["Farm_Retry"]},
#      })


def parse_args():
    ap = argparse.ArgumentParser(description="格斗手游自动刷本（MaaFramework 示例）")
    ap.add_argument("--adb", default=None, help="adb 可执行文件路径，默认用 Toolkit 自动发现的")
    ap.add_argument("--address", default=None, help="设备地址，如 127.0.0.1:16384；默认取第一个发现的设备")
    ap.add_argument("--entry", default=DEFAULT_ENTRY, help=f"任务入口节点名（默认 {DEFAULT_ENTRY}）")
    ap.add_argument("--shot", default=None, help="只截一张图保存到该路径（用于校准坐标/截模板），然后退出")
    return ap.parse_args()


def find_device(args):
    devices = Toolkit.find_adb_devices()
    if not devices:
        print("未发现 ADB 设备。请确认模拟器/手机已连接并开启了 ADB 调试。")
        sys.exit(1)

    if args.address:
        for d in devices:
            if d.address == args.address:
                return d
        print(f"未找到地址为 {args.address} 的设备，已发现的设备：{[d.address for d in devices]}")
        sys.exit(1)

    return devices[0]


def main():
    args = parse_args()
    Toolkit.init_option("./debug")

    device = find_device(args)
    adb_path = args.adb or device.adb_path
    print(f"连接设备: {device.address} (adb: {adb_path})")

    controller = AdbController(
        adb_path=adb_path,
        address=device.address,
        screencap_methods=device.screencap_methods,
        input_methods=device.input_methods,
        config=device.config,
    )
    controller.post_connection().wait()
    if not controller.connected:
        print("连接失败，请检查设备地址 / adb 状态。")
        sys.exit(1)

    # 校准模式：只截图
    if args.shot:
        img = controller.post_screencap().wait().get()
        cv2.imwrite(args.shot, img)
        print(f"截图已保存: {args.shot}（尺寸 {img.shape[1]}x{img.shape[0]}）")
        return

    resource = Resource()
    register_all(resource)  # 自定义动作/识别统一注册（必须在 post_bundle 之前）

    if not resource.post_bundle("./assets/resource").wait().succeeded:
        print("资源加载失败，请检查 assets/resource/ 目录（image/ 目录必须存在）。")
        sys.exit(1)

    tasker = Tasker()
    tasker.bind(resource, controller)
    if not tasker.inited:
        print("Tasker 初始化失败。")
        sys.exit(1)

    print(f"开始任务: {args.entry}")
    # 若用 pipeline_override 接线（方式 b），把注释打开并把节点名换成你的：
    # job = tasker.post_task(args.entry, pipeline_override={
    #     "Fight_Win":  {"next": ["Farm_Settle"]},
    #     "Fight_Lose": {"next": ["Farm_Retry"]},
    # })
    job = tasker.post_task(args.entry)
    job.wait()
    print(f"任务结束: {'成功' if job.succeeded else '失败'}")


if __name__ == "__main__":
    main()
