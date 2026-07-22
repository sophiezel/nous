#!/usr/bin/env python3
"""Clash Verge 代理控制器

用法:
  python3 clash_ctl.py status     # 查看当前模式
  python3 clash_ctl.py direct     # 直连（关代理）
  python3 clash_ctl.py rule       # 规则模式（开代理）
  python3 clash_ctl.py global     # 全局模式
"""

import json
import subprocess
import sys

SOCK = "/tmp/verge/verge-mihomo.sock"
BASE = "http://localhost"


def _api(path: str, method: str = "GET", data: dict = None) -> str:
    cmd = ["curl", "-s", "--unix-socket", SOCK, f"{BASE}{path}"]
    if method == "PATCH" and data:
        cmd += ["-X", "PATCH", "-H", "Content-Type: application/json",
                "-d", json.dumps(data)]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
    return r.stdout


def set_mode(mode: str) -> str:
    """mode: rule / global / direct"""
    return _api("/configs", "PATCH", {"mode": mode})


def get_mode() -> str:
    try:
        return json.loads(_api("/configs")).get("mode", "unknown")
    except Exception:
        return "unknown"


def main():
    if len(sys.argv) < 2:
        print(f"当前模式: {get_mode()}")
        print("用法: clash_ctl.py [status|direct|rule|global]")
        return

    cmd = sys.argv[1]
    if cmd == "status":
        print(f"当前模式: {get_mode()}")
    elif cmd in ("direct", "rule", "global"):
        set_mode(cmd)
        print(f"已切换: {cmd}")
    else:
        print(f"未知命令: {cmd}")


if __name__ == "__main__":
    main()
