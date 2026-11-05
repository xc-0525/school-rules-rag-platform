#!/usr/bin/env python
"""启动本机 Neo4j（Windows 原生安装版）。

为什么不用 bin\\neo4j.bat：当前 shell 环境里同时存在 `PATH` 与 `Path` 两个键，
PowerShell 的 Environment 字典大小写不敏感，会抛"已添加了具有相同键的项"。
这里用最小化、无重复键的环境变量启动，绕开该问题。

用法：
    python scripts/start_neo4j.py            # 前台运行，Ctrl+C 停止
    python scripts/start_neo4j.py --check    # 只检查 7687 是否已监听
"""

from __future__ import annotations

import argparse
import os
import socket
import subprocess
import sys
from pathlib import Path

NEO4J_HOME = Path(os.getenv("NEO4J_HOME", r"G:\neo4j"))
JAVA_HOME = Path(os.getenv("NEO4J_JAVA_HOME", r"G:\DevEnv\Java-JDK17\jdk-17.0.1.12-hotspot"))


def port_open(host: str = "127.0.0.1", port: int = 7687) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.5)
        return sock.connect_ex((host, port)) == 0


def main() -> int:
    parser = argparse.ArgumentParser(description="启动本机 Neo4j")
    parser.add_argument("--check", action="store_true", help="只检查端口，不启动")
    parser.add_argument("--status", action="store_true", help="打印路径配置")
    args = parser.parse_args()

    if args.status or args.check:
        print(f"NEO4J_HOME={NEO4J_HOME}  exist={NEO4J_HOME.is_dir()}")
        print(f"JAVA_HOME={JAVA_HOME}  exist={JAVA_HOME.is_dir()}")
        print(f"bolt 7687 listening={port_open()}")
        return 0 if port_open() else 1

    if not (NEO4J_HOME / "bin" / "neo4j.bat").is_file():
        print(f"错误：找不到 {NEO4J_HOME / 'bin' / 'neo4j.bat'}")
        return 1
    if not JAVA_HOME.is_dir():
        print(f"错误：JAVA_HOME 不存在 {JAVA_HOME}")
        return 1
    if port_open():
        print("Neo4j 已在运行（7687 监听中），无需重复启动")
        return 0

    # 最小干净环境：只保留运行必需的变量，避开 PATH/Path 重复键
    # neo4j.bat 内部会调 powershell.exe，所以 WindowsPowerShell 目录必须在 PATH 里
    windows = os.environ.get("SystemRoot", r"C:\Windows")
    env = {
        "SystemRoot": windows,
        "PATH": ";".join(
            [
                f"{JAVA_HOME}\\bin",
                f"{windows}\\System32",
                f"{windows}",
                f"{windows}\\System32\\WindowsPowerShell\\v1.0",
            ]
        ),
        "JAVA_HOME": str(JAVA_HOME),
        "NEO4J_HOME": str(NEO4J_HOME),
        "TEMP": os.environ.get("TEMP", r"C:\Windows\Temp"),
        "TMP": os.environ.get("TMP", r"C:\Windows\Temp"),
    }
    print(f"启动 Neo4j（{NEO4J_HOME}），等待 7687 监听… Ctrl+C 停止")
    try:
        return subprocess.call(
            ["cmd", "/c", "neo4j.bat", "console"], cwd=str(NEO4J_HOME / "bin"), env=env
        )
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())
