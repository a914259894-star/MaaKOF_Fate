"""战斗中的自定义识别（Custom Recognition）。"""

from __future__ import annotations

import numpy as np

from maa.context import Context
from maa.custom_recognition import CustomRecognition


class CombatAlwaysHit(CustomRecognition):
    """永真识别器：运行即命中 roi 区域（不命中返回 None 则该节点永不通过）。

    用在"无条件执行"的节点上：
      - Fight_NormalAttack 普攻兜底（轮询到它 = 本帧没有技能可放，就平A）
      - Fight_Move         摇杆移动
    roi 由 pipeline 节点定义给出（写 roi 是为了框出有意义的手感区域，
    也方便在调试器里看到这个节点"命中了哪里"）。
    """

    def analyze(
        self,
        context: Context,
        argv: CustomRecognition.AnalyzeArg,
    ) -> CustomRecognition.AnalyzeResult:
        roi = argv.roi
        x, y, w, h = (roi.x, roi.y, roi.w, roi.h) if roi else (0, 0, 0, 0)
        if w <= 0 or h <= 0:
            x, y, w, h = 0, 0, 100, 100
        return CustomRecognition.AnalyzeResult(
            box=(int(x), int(y), int(w), int(h)),
            detail={"always": True},
        )


class SkillReadyBright(CustomRecognition):
    """亮暗判定：技能图标"就绪=亮、冷却=暗"，按 roi 小块的亮度/饱和度决定是否可放。

    custom_recognition_param（全部可省略，默认即暗化类冷却）：
    {
        "channel": "v",        // 度量通道（mode=ratio 时固定用 v 做亮度截止）：
                               //   "v"    明度（图标整体变暗 → 用这个，最常见）
                               //   "s"    饱和度（图标变灰/黑白 → 用这个）
                               //   "gray" 灰度均值（和 v 近似，速度快一点）
                               //   "min_vs" 取 min(V, S)：对"又变暗又变灰"更严
        "threshold": 128,      // 判定阈值：度量值 >= threshold 视为就绪。
                               // 用 calibrate.py 分别测就绪/冷却两态，取中间值
        "mode": "median",      // "median"：roi 亮度中位数（实心亮图标用）
                               // "mean"  ：roi 亮度均值
                               // "ratio" ：roi 内"亮像素占比"（0~1，环形/描边/镂空图标用，
                               //           threshold 相应填 0.x），亮像素 = V >= v_thresh
        "v_thresh": 160,       // mode=ratio 时的亮度截止（0~255）
        "confirm": 1           // 连续 confirm 帧命中才算就绪（防全屏闪光误报，建议 2）
    }

    说明：
    - roi 建议取图标本体（含发光描边/环形），中心 40x40 只适合"实心亮"图标，
      环形（如爆气圈）、深色底（如秘笈）图标用 mode=ratio 更稳；
    - 每个技能图标底色不同，threshold 要按节点单独校准；
    - 该识别器是无状态的纯亮度判断，比模板匹配更适合"机制多、形态变"的图标。
    """

    def __init__(self):
        super().__init__()
        self._confirm: dict[str, int] = {}

    def analyze(
        self,
        context: Context,
        argv: CustomRecognition.AnalyzeArg,
    ) -> CustomRecognition.AnalyzeResult:
        param = _loads(argv.custom_recognition_param)
        channel = str(param.get("channel", "v")).lower()
        threshold = float(param.get("threshold", 128))
        mode = str(param.get("mode", "median")).lower()
        v_thresh = float(param.get("v_thresh", 160))
        confirm = max(1, int(param.get("confirm", 1)))

        img = argv.image
        if img is None or img.size == 0:
            return CustomRecognition.AnalyzeResult(box=None, detail={})

        roi = argv.roi
        x, y, w, h = int(roi.x), int(roi.y), int(roi.w), int(roi.h)
        h_img, w_img = img.shape[:2]
        if w <= 0 or h <= 0:
            return CustomRecognition.AnalyzeResult(box=None, detail={})
        x1, y1 = max(0, x), max(0, y)
        x2, y2 = min(w_img, x + w), min(h_img, y + h)
        if x2 <= x1 or y2 <= y1:
            return CustomRecognition.AnalyzeResult(box=None, detail={})

        patch = img[y1:y2, x1:x2]  # BGR
        value = _metric(patch, channel, mode, v_thresh=v_thresh)

        box = (x, y, w, h)
        hit = value >= threshold
        if confirm > 1:
            key = argv.node_name
            streak = self._confirm.get(key, 0) + 1 if hit else 0
            self._confirm[key] = streak
            hit = streak >= confirm

        return CustomRecognition.AnalyzeResult(
            box=box if hit else None,
            detail={"channel": channel, "mode": mode, "value": round(float(value), 1)},
        )


def _metric(patch: np.ndarray, channel: str, mode: str, v_thresh: float = 160) -> float:
    """计算小块图像的亮度/饱和度度量值。

    median/mean 模式返回 0~255；ratio 模式返回亮像素占比 0~1（V >= v_thresh）。
    """
    arr = patch.reshape(-1, 3).astype(np.float32)
    if mode == "ratio":
        v = arr.max(axis=1)
        return float((v >= v_thresh).mean())
    if channel == "v":
        m = arr.max(axis=1)  # HSV 的 V = max(r,g,b)
    elif channel == "s":
        mx, mn = arr.max(axis=1), arr.min(axis=1)
        m = np.where(mx > 0, (mx - mn) / np.maximum(mx, 1.0) * 255.0, 0.0)  # HSV 的 S
    elif channel == "min_vs":
        mx, mn = arr.max(axis=1), arr.min(axis=1)
        v = mx
        s = np.where(mx > 0, (mx - mn) / np.maximum(mx, 1.0) * 255.0, 0.0)
        m = np.minimum(v, s)
    else:  # gray
        m = 0.114 * arr[:, 0] + 0.587 * arr[:, 1] + 0.299 * arr[:, 2]
    if mode == "mean":
        return float(m.mean())
    return float(np.median(m))


def _loads(s: str | None) -> dict:
    if not s:
        return {}
    import json

    try:
        return json.loads(s)
    except (TypeError, ValueError):
        return {}
