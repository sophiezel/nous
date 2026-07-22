#!/usr/bin/env python3
"""
看门狗进程 — 监控采集进程心跳, 自动重启僵死/泄漏进程
启动方式: python3 -m src.collectors.watchdog
"""

from __future__ import annotations

import os
import sys
import time
import signal
import subprocess
from pathlib import Path

# 尝试导入 psutil (内存监控)
try:
    import psutil
    HAS_PSUTIL = True
except ImportError:
    HAS_PSUTIL = False

# 心跳目录
HEARTBEAT_DIR = Path.home() / ".hermes" / "cache" / "heartbeats"
HEARTBEAT_DIR.mkdir(parents=True, exist_ok=True)

# 日志目录
LOG_DIR = Path.home() / ".hermes" / "logs" / "watchdog"
LOG_DIR.mkdir(parents=True, exist_ok=True)

# ═══ 受监控进程定义 ═══
# 每个进程: {name, script_module, heartbeat_max_age_sec, max_memory_mb, start_delay_sec}
# Phase 1: 盘前盘后采集 (pool_builder + 盘后采集器)
# Phase 2: 盘中实时采集 (minute_collector + northbound + sim)
PROCESSES = [
    {
        "name": "pool_builder",
        "module": "src.collectors.pool_builder",
        "heartbeat_max_age": 900,   # 09:25运行一次
        "max_memory_mb": 200,
        "start_delay": 0,
    },
    {
        "name": "minute_collector",
        "module": "src.collectors.minute_collector",
        "heartbeat_max_age": 90,    # 每60s更新心跳
        "max_memory_mb": 500,
        "start_delay": 5,
    },
    {
        "name": "northbound_collector",
        "module": "src.collectors.northbound_collector",
        "heartbeat_max_age": 360,   # 每5min更新
        "max_memory_mb": 300,
        "start_delay": 10,
    },
    {
        "name": "sim_executor",
        "module": "src.collectors.sim_executor",
        "heartbeat_max_age": 120,   # 每15s检查，4个slot时间点执行
        "max_memory_mb": 300,
        "start_delay": 15,
    },
    {
        "name": "sim_pnl_tracker",
        "module": "src.collectors.sim_pnl_tracker",
        "heartbeat_max_age": 90,    # 每60s更新
        "max_memory_mb": 400,
        "start_delay": 20,
    },
    {
        "name": "sentiment_dashboard",
        "module": "src.collectors.sentiment_dashboard",
        "heartbeat_max_age": 3600,  # 盘后运行一次
        "max_memory_mb": 300,
        "start_delay": 25,
    },
    {
        "name": "futures_basis",
        "module": "src.collectors.futures_basis",
        "heartbeat_max_age": 3600,  # 盘后运行一次
        "max_memory_mb": 300,
        "start_delay": 30,
    },
    {
        "name": "hsgt_stock_collector",
        "module": "src.collectors.hsgt_stock_collector",
        "heartbeat_max_age": 3600,  # 每小时运行一次
        "max_memory_mb": 300,
        "start_delay": 35,
    },
]

running: dict[str, subprocess.Popen] = {}
restart_counts: dict[str, int] = {}
MAX_RESTARTS = 5


def write_log(msg: str):
    """写入看门狗日志"""
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    try:
        log_file = LOG_DIR / f"watchdog_{time.strftime('%Y%m%d')}.log"
        with open(log_file, "a") as f:
            f.write(line + "\n")
    except Exception:
        pass


def start_process(proc_def: dict) -> subprocess.Popen | None:
    """启动一个采集进程"""
    name = proc_def["name"]
    module = proc_def["module"]
    pid_file = HEARTBEAT_DIR / f"{name}.pid"

    # 清除旧心跳
    hb_file = HEARTBEAT_DIR / f"{name}.heartbeat"
    if hb_file.exists():
        hb_file.unlink()

    try:
        proc = subprocess.Popen(
            [sys.executable, "-m", module],
            cwd=os.path.expanduser("~/code/stock-screener"),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        running[name] = proc
        pid_file.write_text(str(proc.pid))
        write_log(f"Started {name} (pid={proc.pid})")
        return proc
    except Exception as e:
        write_log(f"Failed to start {name}: {e}")
        return None


def check_heartbeat(name: str, max_age: int) -> tuple[bool, str]:
    """检查心跳"""
    hb_file = HEARTBEAT_DIR / f"{name}.heartbeat"
    if not hb_file.exists():
        return False, "no heartbeat file"
    try:
        last_hb = float(hb_file.read_text().strip())
        age = time.time() - last_hb
        if age > max_age:
            return False, f"stale ({age:.0f}s > {max_age}s)"
        return True, f"ok ({age:.0f}s)"
    except Exception:
        return False, "unreadable"


def check_memory(name: str, max_mb: int) -> tuple[bool, str]:
    """检查内存使用"""
    if not HAS_PSUTIL:
        return True, "psutil not installed"
    proc = running.get(name)
    if not proc or proc.poll() is not None:
        return True, "not running"
    try:
        p = psutil.Process(proc.pid)
        mem_mb = p.memory_info().rss / 1024 / 1024
        if mem_mb > max_mb:
            return False, f"OOM: {mem_mb:.0f}MB > {max_mb}MB"
        return True, f"{mem_mb:.0f}MB"
    except psutil.NoSuchProcess:
        return True, "gone"


def kill_process(name: str):
    """安全终止进程"""
    proc = running.pop(name, None)
    if proc is None:
        return
    try:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=3)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass
    write_log(f"Killed {name} (pid={proc.pid})")


def restart_process(proc_def: dict, reason: str):
    """重启进程"""
    name = proc_def["name"]
    if restart_counts.get(name, 0) >= MAX_RESTARTS:
        write_log(f"FATAL: {name} restarted {MAX_RESTARTS} times, giving up")
        return

    kill_process(name)
    time.sleep(2)  # 冷却
    restart_counts[name] = restart_counts.get(name, 0) + 1
    write_log(f"Restarting {name} (attempt {restart_counts[name]}/{MAX_RESTARTS}): {reason}")
    start_process(proc_def)


def watchdog_loop(shutdown_at: str = "15:05"):
    """主循环"""
    write_log("=== Watchdog started ===")

    # 启动所有进程(带延迟)
    for proc_def in PROCESSES:
        delay = proc_def.get("start_delay", 0)
        if delay > 0:
            time.sleep(delay)
        start_process(proc_def)

    write_log(f"All {len(PROCESSES)} processes started, monitoring ...")

    loop_count = 0
    while True:
        time.sleep(30)
        loop_count += 1

        # 收盘退出检查
        now = time.strftime("%H:%M")
        if now >= shutdown_at:
            write_log(f"Shutdown time {shutdown_at} reached, stopping all ...")
            for name in list(running.keys()):
                kill_process(name)
            write_log("=== Watchdog stopped ===")
            sys.exit(0)

        # 每30s检查所有进程
        for proc_def in PROCESSES:
            name = proc_def["name"]

            # 1. 检查进程是否存活
            proc = running.get(name)
            if proc is None:
                restart_process(proc_def, "not in running list")
                continue
            if proc.poll() is not None:
                exit_code = proc.returncode
                restart_process(proc_def, f"exited with code {exit_code}")
                continue

            # 2. 检查心跳
            hb_ok, hb_msg = check_heartbeat(name, proc_def["heartbeat_max_age"])
            if not hb_ok:
                restart_process(proc_def, hb_msg)
                continue

            # 3. 检查内存
            mem_ok, mem_msg = check_memory(name, proc_def["max_memory_mb"])
            if not mem_ok:
                restart_process(proc_def, mem_msg)
                continue

        # 每5分钟输出状态
        if loop_count % 10 == 0:
            status_parts = []
            for proc_def in PROCESSES:
                name = proc_def["name"]
                proc = running.get(name)
                if proc and proc.poll() is None:
                    hb_ok, hb_msg = check_heartbeat(name, proc_def["heartbeat_max_age"])
                    mem_ok, mem_msg = check_memory(name, proc_def["max_memory_mb"])
                    status_parts.append(f"{name}:{hb_msg}:{mem_msg}")
                else:
                    status_parts.append(f"{name}:DEAD")
            write_log(f"Status: {' | '.join(status_parts)}")


if __name__ == "__main__":
    # 注册信号处理
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    signal.signal(signal.SIGINT, lambda *_: sys.exit(0))
    watchdog_loop()
