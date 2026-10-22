"""准备一份「便携 Python」并装好 agent 运行依赖，用于打进发布包。

目的：让生成的 exe 不依赖用户自己安装 Python / maafw，下载即用。

产出目录：<repo>/deps/agent_python/   （已被 .gitignore 忽略，不会入库）
最终结构：deps/agent_python/python.exe + Lib/site-packages/{maafw, cv2, ...}

流程：
  1. 下载 Windows embeddable Python 3.12.x（轻量、纯解压，无安装器/UAC）
  2. 修正 ._pth 启用 import site
  3. 引导 pip（get-pip.py）
  4. pip install maafw==<版本> opencv-python-headless（无 GUI 依赖，避免 Qt/GTK 系统库）
  5. 导入验证 maafw + cv2
  6. 写 .bundled_marker 标记，幂等（已存在则跳过下载）

install.py 会把 deps/agent_python 整体拷到 发布包/install/libs/python/，
interface.json 的 agent.child_exec 指向 ./libs/python/python.exe。

运行：
    python tools/bundle_python.py               # 默认 Windows x86_64
环境变量覆盖：
    MAAFW_VERSION=5.5.0  PY_VERSION=3.12.10

两种模式（按【当前运行脚本】的系统自动选择）：
  - Windows 本机模式：解压 embeddable → 引导 pip → pip install → 导入验证。
  - Linux/macOS 交叉模式（CI ubuntu runner 交叉打包 win 产物时走这里）：
    Windows 的 python.exe 无法在 Linux 上执行，改为用宿主 pip
    「download --platform win_amd64」拉取 win 轮子（wheel 即 zip），
    直接解包进 embeddable 的 Lib/site-packages，并校验关键目录存在。
"""
from __future__ import annotations

import io
import os
import shutil
import subprocess
import sys
import zipfile

import urllib.request

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEPS_DIR = os.path.join(REPO_ROOT, "deps")
OUT_DIR = os.path.join(DEPS_DIR, "agent_python")

# 与宿主 MaaFramework 版本保持一致的 Python + maafw 版本
PY_VERSION = os.environ.get("PY_VERSION", "3.12.10")
MAAFW_VERSION = os.environ.get("MAAFW_VERSION", "5.5.0")
OPENCV_PKG = "opencv-python-headless"  # 无 GUI 依赖，体积更小、系统库需求更少

# Windows embeddable 下载地址（按 PY_VERSION 拼接）
EMBED_URL = f"https://www.python.org/ftp/python/{PY_VERSION}/python-{PY_VERSION}-embed-amd64.zip"
GET_PIP_URL = "https://bootstrap.pypa.io/get-pip.py"

MARKER = os.path.join(OUT_DIR, ".bundled_marker")


def log(msg: str) -> None:
    print(f"[bundle_python] {msg}")


def fetch(url: str) -> bytes:
    log(f"下载 {url}")
    req = urllib.request.Request(url, headers={"User-Agent": "MaaKOF_Fate-bundler/1.0"})
    with urllib.request.urlopen(req, timeout=180) as r:
        return r.read()


def is_windows_host() -> bool:
    return sys.platform.startswith("win")


def ensure_embeddable(with_pip: bool = True) -> None:
    if os.path.exists(MARKER):
        log("便携 Python 已存在，跳过下载/解压。如需重建请删除 deps/agent_python 目录。")
        return
    os.makedirs(OUT_DIR, exist_ok=True)
    data = fetch(EMBED_URL)
    log(f"解压 embeddable Python ({len(data) // 1024} KB) ...")
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        z.extractall(OUT_DIR)
    fix_pth()
    if not with_pip:
        return  # 交叉模式不需要在包里引导 pip（运行期用不到）
    get_pip = os.path.join(OUT_DIR, "get-pip.py")
    with open(get_pip, "wb") as f:
        f.write(fetch(GET_PIP_URL))
    py = os.path.join(OUT_DIR, "python.exe")
    log("引导 pip ...")
    subprocess.run([py, get_pip], check=True, cwd=OUT_DIR)
    os.remove(get_pip)


def fix_pth() -> None:
    """embeddable 的 ._pth 默认注释掉 import site，会导致 pip 装的包找不到。启用它。"""
    for name in os.listdir(OUT_DIR):
        if name.endswith("._pth"):
            p = os.path.join(OUT_DIR, name)
            out = []
            with open(p, encoding="utf-8") as f:
                for line in f:
                    if line.strip() == "#import site":
                        out.append("import site\n")
                    else:
                        out.append(line if line.endswith("\n") else line + "\n")
            content = "".join(out)
            if "Lib/site-packages" not in content:
                content += "Lib/site-packages\n"
            with open(p, "w", encoding="utf-8") as f:
                f.write(content)
            log(f"已修正 {name}（启用 import site）")
            return
    log("未找到 ._pth 文件，跳过修正（可能已支持 site）")


def install_deps() -> None:
    py = os.path.join(OUT_DIR, "python.exe")
    if not os.path.exists(py):
        raise RuntimeError(f"未找到 {py}，embeddable 解压可能失败")

    subprocess.run([py, "-m", "pip", "install", "--upgrade", "pip"],
                   check=True, cwd=OUT_DIR)

    req = os.path.join(REPO_ROOT, "assets", "agent", "requirements.txt")
    if os.path.exists(req):
        log(f"按 assets/agent/requirements.txt 安装运行时依赖：{req}")
        subprocess.run([py, "-m", "pip", "install", "-r", req], check=True, cwd=OUT_DIR)
    else:
        subprocess.run([py, "-m", "pip", "install",
                        f"maafw=={MAAFW_VERSION}", OPENCV_PKG], check=True, cwd=OUT_DIR)

    verify = subprocess.run(
        [py, "-c", "import maa, cv2; print('maafw + cv2 导入 OK')"],
        capture_output=True, text=True, cwd=OUT_DIR,
    )
    if verify.returncode != 0:
        log("依赖验证失败：")
        log(verify.stdout)
        log(verify.stderr)
        raise RuntimeError("maafw / cv2 导入验证失败，请检查网络或版本")
    log(verify.stdout.strip())

    with open(MARKER, "w", encoding="utf-8") as f:
        f.write(f"python={PY_VERSION} maafw={MAAFW_VERSION} opencv={OPENCV_PKG}\n")


def cross_install_deps() -> None:
    """交叉模式：宿主为 Linux/macOS（如 CI ubuntu runner），目标为 Windows。

    不执行 python.exe，用宿主 pip 下载 win_amd64 轮子后直接解包进 site-packages。
    wheel 本质是 zip，运行期不需要 pip，因此包内无需引导 pip。
    """
    site_dir = os.path.join(OUT_DIR, "Lib", "site-packages")
    os.makedirs(site_dir, exist_ok=True)

    pkgs = [f"maafw=={MAAFW_VERSION}", OPENCV_PKG]
    req = os.path.join(REPO_ROOT, "assets", "agent", "requirements.txt")
    if os.path.exists(req):
        with open(req, encoding="utf-8") as f:
            pkgs = [ln.strip() for ln in f if ln.strip() and not ln.startswith("#")]

    wheels_dir = os.path.join(OUT_DIR, "_wheels")
    os.makedirs(wheels_dir, exist_ok=True)
    cmd = [sys.executable, "-m", "pip", "download",
           "--dest", wheels_dir,
           "--only-binary=:all:",
           "--platform", "win_amd64",
           "--python-version", "3.12",
           "--implementation", "cp",
           "--abi", "cp312",
           *pkgs]
    log(f"宿主 pip 下载 win_amd64 轮子：{' '.join(pkgs)}")
    subprocess.run(cmd, check=True)

    n = 0
    for fn in sorted(os.listdir(wheels_dir)):
        if not fn.endswith(".whl"):
            continue
        with zipfile.ZipFile(os.path.join(wheels_dir, fn)) as z:
            z.extractall(site_dir)
        log(f"已解包 {fn}")
        n += 1
    shutil.rmtree(wheels_dir, ignore_errors=True)
    if n == 0:
        raise RuntimeError("未下载到任何 wheel，请检查 pip download 输出")

    # 校验关键包目录存在（无法在 Linux 上 import，只做结构校验）
    missing = [d for d in ("maa", "cv2") if not os.path.isdir(os.path.join(site_dir, d))]
    if missing:
        raise RuntimeError(f"site-packages 缺少关键目录：{missing}")
    log("交叉模式完成：maa/ 与 cv2/ 均已就位（Windows 侧运行时加载）")

    with open(MARKER, "w", encoding="utf-8") as f:
        f.write(f"python={PY_VERSION} maafw={MAAFW_VERSION} opencv={OPENCV_PKG} mode=cross\n")


def main() -> None:
    os.makedirs(DEPS_DIR, exist_ok=True)
    win_host = is_windows_host()
    ensure_embeddable(with_pip=win_host)
    if win_host:
        install_deps()
    else:
        log(f"宿主为 {sys.platform}（非 Windows），走交叉模式")
        cross_install_deps()
    log(f"完成：便携 Python 位于 {OUT_DIR}")
    log("接着运行  python tools/install.py <版本> win x86_64  即可把这份 Python 打进发布包。")


if __name__ == "__main__":
    main()
