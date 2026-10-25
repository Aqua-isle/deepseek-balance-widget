# -*- coding: utf-8 -*-
"""把挂件打包成可直接分发的 Windows 程序（PyInstaller）。

    python build_exe.py             # 两种都打（默认）
    python build_exe.py --onefile   # 只打单文件 exe
    python build_exe.py --portable  # 只打免解压的文件夹版
    python build_exe.py --console   # 保留控制台，排错用

产物：
    dist\\DeepSeekBalance.exe                     单文件（推荐发给别人）
    dist\\DeepSeekBalance-portable\\             文件夹版（启动快，不受临时目录限制）

两种都**不含任何 API Key**：Key 只存在
%APPDATA%\\DeepSeekBalance\\config.json（DPAPI 加密），换台机器要重新填。
打完跑一下 python tools\\verify_build.py 复核。
"""

import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
TARGETS = {
    "onefile": ("--onefile", "DeepSeekBalance"),
    "portable": ("--onedir", "DeepSeekBalance-portable"),
}


def build(kind: str, icon: Path, console: bool) -> int:
    mode, name = TARGETS[kind]
    build_dir = HERE / "build"
    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm", "--clean",
        mode,
        "--name", name,
        f"--icon={icon}",
        "--distpath", str(HERE / "dist"),
        "--workpath", str(build_dir / f"work-{kind}"),
        "--specpath", str(build_dir),
    ]
    if not console:
        cmd.append("--windowed")
    cmd.append(str(HERE / "deepseek_balance_widget.py"))

    print(f"\n[build] {name}")
    code = subprocess.call(cmd, cwd=str(HERE))
    if code == 0:
        target = (HERE / "dist" / f"{name}.exe") if kind == "onefile" \
            else (HERE / "dist" / name)
        size = 0
        if target.is_file():
            size = target.stat().st_size
        elif target.is_dir():
            size = sum(p.stat().st_size for p in target.rglob("*") if p.is_file())
        print(f"[build] 完成: {target}  ({size / 1024 / 1024:.1f} MB)")
    else:
        print(f"[build] 失败，退出码 {code}")
    return code


def main() -> int:
    sys.path.insert(0, str(HERE))
    import deepseek_balance_widget as widget      # 复用现成的图标生成，不另存素材

    build_dir = HERE / "build"
    build_dir.mkdir(exist_ok=True)
    icon_src, _ico = widget.ensure_icon()
    icon = build_dir / "icon.ico"
    shutil.copyfile(icon_src, icon)
    print(f"[build] 图标: {icon}")

    console = "--console" in sys.argv[1:]
    wanted = []
    if "--onefile" in sys.argv[1:]:
        wanted.append("onefile")
    if "--portable" in sys.argv[1:]:
        wanted.append("portable")
    if not wanted:
        wanted = ["onefile", "portable"]

    for kind in wanted:
        code = build(kind, icon, console)
        if code != 0:
            return code
    print("\n[build] 全部完成。记得跑 python tools\\verify_build.py 复核 Key 没被打进去。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
