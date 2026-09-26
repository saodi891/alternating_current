# -*- coding: utf-8 -*-
"""
把 voice_gui.py 打包成「交流电.exe」（单文件、无黑窗）。

用法（在本目录下）：
    py build_exe.py

想换图标：把图标命名为 icon.ico 放在本目录，会自动用上。
生成的 exe 在 dist\交流电.exe。
"""

import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
APP_NAME = "交流电"


def main():
    os.chdir(HERE)

    cmd = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--onefile",
        "--windowed",
        "--name",
        APP_NAME,
        # 强制把 PySide6 / shiboken6 完整收进来，避免运行时 No module named PySide6
        "--collect-all",
        "PySide6",
        "--collect-all",
        "shiboken6",
    ]

    icon = os.path.join(HERE, "icon.ico")
    if os.path.isfile(icon):
        cmd += ["--icon", icon]
        # 同时把 icon.ico 打进 exe，运行时用作窗口/任务栏图标
        # Windows 上 --add-data 的分隔符是分号：源;目标目录
        cmd += ["--add-data", f"{icon};."]
        print(f"[图标] 使用 {icon}")
    else:
        print("[图标] 没找到 icon.ico，用默认图标（想换就把 icon.ico 放这里再打包）")

    # 把字体 + 图标素材（assets 文件夹）一起打进 exe
    assets = os.path.join(HERE, "assets")
    if os.path.isdir(assets):
        cmd += ["--add-data", f"{assets};assets"]
        print(f"[资源] 打包 assets（字体/图标）")
    else:
        print("[资源] 没找到 assets 文件夹，界面会退回系统字体、不显示图标")

    cmd.append("voice_gui.py")

    print("[打包] 开始，稍等 1~2 分钟 ...")
    result = subprocess.run(cmd)

    exe_path = os.path.join(HERE, "dist", f"{APP_NAME}.exe")
    print()
    if result.returncode == 0 and os.path.isfile(exe_path):
        print(f"[完成] {exe_path}")
        print("       首次运行会在 exe 同目录生成 config.json 和 voices 文件夹。")
    else:
        print("[失败] 没生成 exe，往上翻 PyInstaller 的报错信息。")


if __name__ == "__main__":
    main()
