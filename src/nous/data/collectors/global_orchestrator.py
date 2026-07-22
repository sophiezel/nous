#!/usr/bin/env python3
"""
全局采集协调器 — GlobalCollectorOrchestrator

状态机驱动所有采集任务:
  IDLE → PRE_MARKET(08:00-09:25) → MARKET_OPEN(09:25) → INTRADAY(09:30-15:00)
       → POST_CLOSE(15:00-18:00) → NIGHT_RECONCILE(20:00/22:00/00:00) → IDLE

每个状态:
  - 知道该跑哪些采集器
  - 按依赖顺序串行/并行执行
  - 追踪进度(完成/失败/重试)
  - 失败自动重调度
  - 全局锁: 同一状态不重复执行

用法:
    python -m src.collectors.global_orchestrator              # 持续运行
    python -m src.collectors.global_orchestrator --once       # 单次执行当前状态
    python -m src.collectors.global_orchestrator --state PRE_MARKET  # 指定状态
"""

import sys
import os
import time
import json
import signal
import traceback
from pathlib import Path
from datetime import datetime, date, time as dtime
from dataclasses import dataclass, field
from typing import Callable, Optional
from enum import Enum

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent.parent))

# 状态文件
STATE_FILE = Path.home() / ".hermes" / "cache" / "orchestrator_state.json"

# ═══ 状态机 ═══

class OrchestratorState(Enum):
    IDLE = "IDLE"
    PRE_MARKET = "PRE_MARKET"          # 08:00-09:25
    MARKET_OPEN = "MARKET_OPEN"        # 09:25 触发
    INTRADAY = "INTRADAY"              # 09:30-15:00
    POST_CLOSE = "POST_CLOSE"          # 15:00-18:00
    NIGHT_RECONCILE_20 = "NIGHT_20"    # 20:00
    NIGHT_RECONCILE_22 = "NIGHT_22"    # 22:00
    NIGHT_RECONCILE_00 = "NIGHT_00"    # 00:00


# 状态转换规则: (当前状态, 时间条件, 下一状态, 是否自动)
STATE_TRANSITIONS = [
    (OrchestratorState.PRE_MARKET,      dtime(9, 25),  OrchestratorState.MARKET_OPEN,    True),
    (OrchestratorState.MARKET_OPEN,     dtime(9, 30),  OrchestratorState.INTRADAY,       True),
    (OrchestratorState.INTRADAY,        dtime(15, 0),  OrchestratorState.POST_CLOSE,      True),
    (OrchestratorState.POST_CLOSE,      dtime(20, 0),  OrchestratorState.NIGHT_RECONCILE_20, True),
    (OrchestratorState.NIGHT_RECONCILE_20, dtime(22, 0), OrchestratorState.NIGHT_RECONCILE_22, True),
    (OrchestratorState.NIGHT_RECONCILE_22, dtime(0, 0),  OrchestratorState.NIGHT_RECONCILE_00, True),
    (OrchestratorState.NIGHT_RECONCILE_00, dtime(2, 0),  OrchestratorState.IDLE,          True),
]

# 时间触发: 从IDLE到PRE_MARKET
PRE_MARKET_START = dtime(8, 0)


@dataclass
class CollectorTask:
    """单个采集任务定义"""
    name: str
    fn: Callable
    timeout_seconds: int = 120
    retry_max: int = 2
    depends_on: list[str] = field(default_factory=list)  # 依赖的前置任务名
    critical: bool = True  # 失败是否阻塞后续


@dataclass
class TaskResult:
    name: str
    success: bool
    duration_seconds: float
    error: Optional[str] = None
    retries: int = 0


# ═══ 各状态的采集任务图 ═══

def _build_task_graphs():
    """构建每个状态的采集任务依赖图"""
    from nous.data.collectors.multi_source_collectors import (
        collect_margin, collect_lhb, collect_northbound,
        collect_a_indices, collect_hxc, collect_futures
    )
    today = date.today().strftime('%Y-%m-%d')

    graphs = {}

    # PRE_MARKET: 盘前数据
    graphs[OrchestratorState.PRE_MARKET] = [
        CollectorTask('global_index', lambda: collect_hxc(today), timeout_seconds=30, critical=False),
        CollectorTask('margin', lambda: collect_margin(today), timeout_seconds=60),
        CollectorTask('lhb', lambda: collect_lhb(today), timeout_seconds=60),
        CollectorTask('premarket_macro', lambda: collect_a_indices(today), timeout_seconds=60),
    ]

    # MARKET_OPEN: 开盘触发
    graphs[OrchestratorState.MARKET_OPEN] = [
        CollectorTask('open_snapshot', lambda: collect_a_indices(today), timeout_seconds=30),
    ]

    # INTRADAY: 盘中持续 (不在此执行，由minute_collector等独立进程负责)
    graphs[OrchestratorState.INTRADAY] = []

    # POST_CLOSE: 收盘后
    graphs[OrchestratorState.POST_CLOSE] = [
        CollectorTask('closing_indices', lambda: collect_a_indices(today), timeout_seconds=60),
        CollectorTask('closing_futures', lambda: collect_futures(today), timeout_seconds=60),
        CollectorTask('northbound', lambda: collect_northbound(today), timeout_seconds=60),
        CollectorTask('lhb_update', lambda: collect_lhb(today), timeout_seconds=60),
    ]

    # NIGHT_RECONCILE: 全量对账
    for night_state in [OrchestratorState.NIGHT_RECONCILE_20,
                        OrchestratorState.NIGHT_RECONCILE_22,
                        OrchestratorState.NIGHT_RECONCILE_00]:
        is_full = (night_state == OrchestratorState.NIGHT_RECONCILE_20)
        graphs[night_state] = [
            CollectorTask(f'reconcile_margin_{night_state.value}',
                         lambda: collect_margin(today), timeout_seconds=60,
                         critical=is_full),
            CollectorTask(f'reconcile_nb_{night_state.value}',
                         lambda: collect_northbound(today), timeout_seconds=60,
                         critical=is_full),
            CollectorTask(f'reconcile_lhb_{night_state.value}',
                         lambda: collect_lhb(today), timeout_seconds=60,
                         critical=is_full),
        ]

    return graphs


TASK_GRAPHS = _build_task_graphs()


# ═══ 协调器核心 ═══

class GlobalCollectorOrchestrator:
    """全局采集协调器"""

    def __init__(self):
        self.state = self._load_state()
        self.state_file = STATE_FILE
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        self._save_state()

    def _load_state(self) -> OrchestratorState:
        try:
            if self.state_file.exists():
                data = json.loads(self.state_file.read_text())
                return OrchestratorState(data.get('state', 'IDLE'))
        except Exception:
            pass
        return self._current_time_state()

    def _current_time_state(self) -> OrchestratorState:
        """根据当前时间判断应该处于哪个状态"""
        now = datetime.now().time()
        weekday = datetime.now().weekday()

        # 周末: IDLE
        if weekday in (5, 6):
            return OrchestratorState.IDLE

        if now < dtime(8, 0):
            # 凌晨→检查是否在夜间对账窗口
            if now >= dtime(0, 0) and now < dtime(2, 0):
                return OrchestratorState.NIGHT_RECONCILE_00
            return OrchestratorState.IDLE
        elif now < dtime(9, 25):
            return OrchestratorState.PRE_MARKET
        elif now < dtime(9, 30):
            return OrchestratorState.MARKET_OPEN
        elif now < dtime(15, 0):
            return OrchestratorState.INTRADAY
        elif now < dtime(20, 0):
            return OrchestratorState.POST_CLOSE
        elif now < dtime(22, 0):
            return OrchestratorState.NIGHT_RECONCILE_20
        elif now < dtime(23, 59):
            return OrchestratorState.NIGHT_RECONCILE_22
        else:
            return OrchestratorState.NIGHT_RECONCILE_00

    def _save_state(self):
        self.state_file.write_text(json.dumps({
            'state': self.state.value,
            'updated_at': datetime.now().isoformat(),
        }))

    def _check_transition(self) -> bool:
        """检查是否应该状态迁移"""
        now = datetime.now().time()
        time_state = self._current_time_state()

        if time_state != self.state:
            print(f"[orchestrator] State transition: {self.state.value} → {time_state.value}")
            self.state = time_state
            self._save_state()
            return True
        return False

    def run_tasks(self, tasks: list[CollectorTask]) -> list[TaskResult]:
        """按依赖顺序执行任务列表"""
        results = []
        completed = set()

        # 拓扑排序执行
        remaining = list(tasks)
        while remaining:
            # 找所有依赖已满足的任务
            ready = [t for t in remaining
                    if all(dep in completed for dep in t.depends_on)]
            if not ready:
                # 死锁: 有循环依赖
                stuck = [t.name for t in remaining]
                print(f"[orchestrator] Deadlock detected: {stuck}")
                break

            for task in ready:
                result = self._run_single_task(task)
                results.append(result)
                if result.success:
                    completed.add(task.name)
                remaining.remove(task)

        return results

    def _run_single_task(self, task: CollectorTask) -> TaskResult:
        """执行单个任务(含重试)"""
        t0 = time.time()
        last_error = None

        for attempt in range(task.retry_max + 1):
            try:
                print(f"  [{task.name}] running (attempt {attempt+1}/{task.retry_max+1})...")
                task.fn()
                elapsed = time.time() - t0
                print(f"  [{task.name}] ✅ done ({elapsed:.1f}s)")
                return TaskResult(task.name, True, elapsed)

            except Exception as e:
                last_error = str(e)
                if attempt < task.retry_max:
                    wait = 2 ** attempt
                    print(f"  [{task.name}] ❌ attempt {attempt+1} failed: {e}, "
                          f"retry in {wait}s...")
                    time.sleep(wait)
                else:
                    elapsed = time.time() - t0
                    print(f"  [{task.name}] ❌ ALL {task.retry_max+1} attempts failed: {e}")

        return TaskResult(task.name, False, time.time() - t0,
                         error=last_error, retries=task.retry_max)

    def execute_state(self):
        """执行当前状态的所有任务"""
        tasks = TASK_GRAPHS.get(self.state, [])
        if not tasks:
            print(f"[orchestrator] State {self.state.value}: no tasks (idle/continuous)")
            return []

        print(f"\n{'='*50}")
        print(f"[orchestrator] STATE: {self.state.value}")
        print(f"[orchestrator] Tasks: {[t.name for t in tasks]}")
        print(f"{'='*50}")

        results = self.run_tasks(tasks)

        # 汇总
        succeeded = sum(1 for r in results if r.success)
        failed = sum(1 for r in results if not r.success)
        total_time = sum(r.duration_seconds for r in results)
        print(f"\n[orchestrator] Summary: {succeeded}/{len(results)} succeeded, "
              f"{failed} failed, {total_time:.1f}s total")
        for r in results:
            if not r.success:
                print(f"  ❌ {r.name}: {r.error}")

        return results

    def run_loop(self):
        """主循环 — 持续运行直到收盘"""
        print(f"[orchestrator] Starting at {datetime.now().isoformat()}")
        print(f"[orchestrator] Initial state: {self.state.value}")

        executed_states = set()

        while True:
            # 检查状态迁移
            if self._check_transition():
                executed_states = set()  # 新状态，重置

            # 如果当前状态未执行过，执行它
            if self.state != OrchestratorState.IDLE and self.state != OrchestratorState.INTRADAY:
                if self.state not in executed_states:
                    self.execute_state()
                    executed_states.add(self.state)

            # 回到IDLE → 退出循环
            if self.state == OrchestratorState.IDLE and executed_states:
                print("[orchestrator] Day cycle complete. Exiting.")
                break

            time.sleep(30)  # 30秒轮询

    def run_once(self, state_override: str = None):
        """单次执行指定状态(用于手动触发或cron)"""
        if state_override:
            self.state = OrchestratorState(state_override)
        else:
            self.state = self._current_time_state()

        print(f"[orchestrator] ONCE: state={self.state.value}")
        return self.execute_state()


# ═══ CLI入口 ═══

if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='Global Collector Orchestrator')
    parser.add_argument('--once', action='store_true', help='Run current state once and exit')
    parser.add_argument('--state', type=str, help='Override state (PRE_MARKET/INTRADAY/etc.)')
    parser.add_argument('--loop', action='store_true', help='Run full day loop')
    args = parser.parse_args()

    orch = GlobalCollectorOrchestrator()

    # 优雅退出
    def graceful_exit(signum, frame):
        print(f"\n[orchestrator] Received signal {signum}, exiting.")
        orch._save_state()
        sys.exit(0)

    signal.signal(signal.SIGTERM, graceful_exit)
    signal.signal(signal.SIGINT, graceful_exit)

    if args.state:
        results = orch.run_once(state_override=args.state)
    elif args.once:
        results = orch.run_once()
    elif args.loop:
        orch.run_loop()
    else:
        # 默认: 单次
        results = orch.run_once()

    # 退出码
    failed = sum(1 for r in (results or []) if not r.success)
    sys.exit(1 if failed > 0 else 0)
