from pathlib import Path

import shutil
import sys

try:
    import jsonc
except ModuleNotFoundError as e:
    raise ImportError(
        "Missing dependency 'json-with-comments' (imported as 'jsonc').\n"
        f"Install it with:\n  {sys.executable} -m pip install json-with-comments\n"
        "Or add it to your project's requirements."
    ) from e


working_dir = Path(__file__).parent.parent.resolve()
install_path = working_dir / Path("install")
version = len(sys.argv) > 1 and sys.argv[1] or "v0.0.1"

# the first parameter is self name
if sys.argv.__len__() < 4:
    print("Usage: python install.py <version> <os> <arch>")
    print("Example: python install.py v1.0.0 win x86_64")
    sys.exit(1)

os_name = sys.argv[2]
arch = sys.argv[3]


def get_dotnet_platform_tag():
    """自动检测当前平台并返回对应的dotnet平台标签"""
    if os_name == "win" and arch == "x86_64":
        platform_tag = "win-x64"
    elif os_name == "win" and arch == "aarch64":
        platform_tag = "win-arm64"
    elif os_name == "macos" and arch == "x86_64":
        platform_tag = "osx-x64"
    elif os_name == "macos" and arch == "aarch64":
        platform_tag = "osx-arm64"
    elif os_name == "linux" and arch == "x86_64":
        platform_tag = "linux-x64"
    elif os_name == "linux" and arch == "aarch64":
        platform_tag = "linux-arm64"
    else:
        print("Unsupported OS or architecture.")
        print("available parameters:")
        print("version: e.g., v1.0.0")
        print("os: [win, macos, linux, android]")
        print("arch: [aarch64, x86_64]")
        sys.exit(1)

    return platform_tag


def install_deps():
    if not (working_dir / "deps" / "bin").exists():
        print('Please download the MaaFramework to "deps" first.')
        print('请先下载 MaaFramework 到 "deps"。')
        sys.exit(1)

    if os_name == "android":
        shutil.copytree(
            working_dir / "deps" / "bin",
            install_path,
            dirs_exist_ok=True,
        )
        shutil.copytree(
            working_dir / "deps" / "share" / "MaaAgentBinary",
            install_path / "MaaAgentBinary",
            dirs_exist_ok=True,
        )
    else:
        shutil.copytree(
            working_dir / "deps" / "bin",
            install_path / "runtimes" / get_dotnet_platform_tag() / "native",
            ignore=shutil.ignore_patterns(
                "*MaaDbgControlUnit*",
                "*MaaThriftControlUnit*",
                "*MaaRpc*",
                "*MaaHttp*",
                "plugins",
                "*.node",
                "*MaaPiCli*",
            ),
            dirs_exist_ok=True,
        )
        shutil.copytree(
            working_dir / "deps" / "share" / "MaaAgentBinary",
            install_path / "libs" / "MaaAgentBinary",
            dirs_exist_ok=True,
        )
        shutil.copytree(
            working_dir / "deps" / "bin" / "plugins",
            install_path / "plugins" / get_dotnet_platform_tag(),
            dirs_exist_ok=True,
        )


def install_resource():
    # 本项目所有识别均为 TemplateMatch，不使用 OCR 模型，
    # 因此相比官方模板去掉了 configure_ocr_model() 步骤。
    shutil.copytree(
        working_dir / "assets" / "resource",
        install_path / "resource",
        dirs_exist_ok=True,
    )
    shutil.copy2(
        working_dir / "assets" / "interface.json",
        install_path,
    )

    with open(install_path / "interface.json", "r", encoding="utf-8") as f:
        interface = jsonc.load(f)

    interface["version"] = version

    with open(install_path / "interface.json", "w", encoding="utf-8") as f:
        jsonc.dump(interface, f, ensure_ascii=False, indent=4)


def install_chores():
    # README / LICENSE 缺失时跳过（不阻塞打包）
    for name in ("README.md", "LICENSE"):
        src = working_dir / name
        if src.exists():
            shutil.copy2(src, install_path)
        else:
            print(f"[install] {name} 不存在，跳过")


def install_agent():
    # 本工程约定：agent 目录在 assets/agent/ 下（非官方样板的根目录 agent/）
    shutil.copytree(
        working_dir / "assets" / "agent",
        install_path / "agent",
        dirs_exist_ok=True,
    )


def install_agent_runtime():
    """把便携 Python（含 maafw + opencv）打进发布包，使生成的 exe 自包含。

    - 源：deps/agent_python（由 tools/bundle_python.py 准备，.gitignore 已忽略）
    - 目标：install/libs/python/，interface.json 的 agent.child_exec 指向 ./libs/python/python.exe
    - 若源不存在，则尝试自动调用 bundle_python 准备（需要网络）。
    - 仅 win 平台打包：bundle_python 下载的是 Windows embeddable Python，
      在 macos/linux CI runner 上必然失败，且非 win 用户机器也跑不了 python.exe。
    """
    if os_name != "win":
        print(f"[install] os={os_name} 非 win，跳过便携 Python 打包（agent 回退用系统 python）")
        return
    src = working_dir / "deps" / "agent_python"
    if not (src / "python.exe").exists():
        print("[install] 未找到便携 Python（deps/agent_python/python.exe），尝试自动准备 ...")
        try:
            import importlib.util

            spec = importlib.util.spec_from_file_location(
                "bundle_python", str(working_dir / "tools" / "bundle_python.py")
            )
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            mod.main()
        except SystemExit:
            raise
        except Exception as e:  # noqa: BLE001
            print(f"[install] 自动准备失败：{e}")
            print("[install] 请先手动运行：python tools/bundle_python.py")
            sys.exit(1)

    dst = install_path / "libs" / "python"
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(
        src,
        dst,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".bundled_marker"),
        dirs_exist_ok=True,
    )
    print(f"[install] 便携 Python 已打入 {dst}（自包含，用户无需另行安装 Python）")


if __name__ == "__main__":
    install_deps()
    install_resource()
    install_chores()
    install_agent()
    install_agent_runtime()

    print(f"Install to {install_path} successfully.")
