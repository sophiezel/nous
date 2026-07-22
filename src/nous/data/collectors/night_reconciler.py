#!/usr/bin/env python3
"""
夜间对账引擎 — night_reconciler.py

三时段对账策略:
  20:00  全量复采 → diff 15:00/17:30的数据 → 生成差异报告
  22:00  差异单点复采 (不复采全量, 只复采20:00差异项)
  00:00  最后一轮差异复采 → 三时间点数据全部存 provenance_log

差异处理:
  - S0(无分歧<0.01%): 自动采纳新值
  - S1(低分歧0.01-1%): 采纳新值+记录
  - S2(高分歧>1%): 标记为disputed+告警+保留旧值

用法:
    python -m src.collectors.night_reconciler                     # 自动判断当前时段
    python -m src.collectors.night_reconciler --hour 20           # 20:00全量
    python -m src.collectors.night_reconciler --hour 22 --diff-only  # 仅复采差异
"""

import sys
import json
import time
from pathlib import Path
from datetime import datetime, date, timedelta
from dataclasses import dataclass, field
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent.parent))

from nous.data.collectors.multi_source_collectors import (
    collect_margin, collect_lhb, collect_northbound,
    collect_a_indices, collect_hxc, collect_futures
)
from nous.data.collectors.multi_source import (
    update_source_reliability, get_source_weight,
    DIVERGENCE_S0, DIVERGENCE_S1, DIVERGENCE_S2,
)
from nous.data.storage import get_db

# 对账数据存储
RECONCILE_DIR = Path.home() / ".hermes" / "cache" / "reconcile"
RECONCILE_DIR.mkdir(parents=True, exist_ok=True)


@dataclass
class DiffItem:
    """单条差异记录"""
    data_line: str       # 'margin' / 'a_indices' / 'northbound'
    field: str           # 'close' / 'total_net_buy'
    record_key: str      # '上证指数'
    old_value: float
    new_value: float
    old_time: str        # '15:00' / '17:30'
    old_sources: str     # 'sina' / 'em'
    diff_pct: float
    level: str           # S0/S1/S2
    action: str          # 'auto_accept' / 'auto_accept_logged' / 'disputed'


@dataclass
class ReconcileReport:
    """对账报告"""
    timestamp: str
    hour: int            # 20/22/0
    total_lines: int     # 总对账数据线数
    full_reconcile: bool # 是否全量
    diffs: list[DiffItem] = field(default_factory=list)
    auto_accepted: int = 0
    disputed: int = 0
    notes: list[str] = field(default_factory=list)


# ═══ 快照管理 ═══

def save_snapshot(data: dict, label: str):
    """保存数据快照到磁盘"""
    filepath = RECONCILE_DIR / f"snapshot_{label}.json"
    filepath.write_text(json.dumps({
        'timestamp': datetime.now().isoformat(),
        'label': label,
        'data': data,
    }, default=str, ensure_ascii=False, indent=2))
    return filepath


def load_snapshot(label: str) -> Optional[dict]:
    """加载快照"""
    filepath = RECONCILE_DIR / f"snapshot_{label}.json"
    if filepath.exists():
        data = json.loads(filepath.read_text())
        return data.get('data', {})
    return None


def load_all_snapshots() -> dict[str, dict]:
    """加载今天所有快照"""
    today = date.today().strftime('%Y-%m-%d')
    snapshots = {}
    for f in sorted(RECONCILE_DIR.glob("snapshot_*.json")):
        try:
            data = json.loads(f.read_text())
            label = data.get('label', f.stem)
            # 检查snapshot日期
            ts = data.get('timestamp', '')
            if today in ts or not ts:
                snapshots[label] = data.get('data', {})
        except Exception:
            pass
    return snapshots


# ═══ 数据采集 ═══

def collect_all_data(effective_date: str = None, label: str = None) -> dict:
    """全量采集所有数据线"""
    if effective_date is None:
        effective_date = date.today().strftime('%Y-%m-%d')

    data = {}
    t0 = time.time()

    # 1. 两融
    try:
        r, _ = collect_margin(effective_date)
        if r:
            data['margin'] = {
                'sh_margin': r.get('sh_margin_balance', 0),
                'sz_margin': r.get('sz_margin_balance', 0),
                'total': r.get('margin_balance', 0),
            }
    except Exception as e:
        data['margin'] = {'error': str(e)}

    # 2. 北向
    try:
        r, _ = collect_northbound(effective_date)
        if r and isinstance(r, dict):
            data['northbound'] = {
                'total_net_buy': r.get('total_net_buy', 0),
                'sh_net_buy': r.get('sh_net_buy', 0),
                'sz_net_buy': r.get('sz_net_buy', 0),
            }
    except Exception as e:
        data['northbound'] = {'error': str(e)}

    # 3. A股指数
    try:
        r, _ = collect_a_indices(effective_date)
        if r:
            data['a_indices'] = {}
            for name, vals in r.items():
                data['a_indices'][name] = {
                    'close': vals.get('close'),
                    'sources': vals.get('sources', 1),
                    'sina_close': vals.get('sina_close'),
                    'tx_close': vals.get('tx_close'),
                }
    except Exception as e:
        data['a_indices'] = {'error': str(e)}

    # 4. 金龙指数
    try:
        r, _ = collect_hxc(effective_date)
        data['hxc'] = {'close': r.get('close') if isinstance(r, dict) else r}
    except Exception as e:
        data['hxc'] = {'error': str(e)}

    # 5. 期货
    try:
        r, _ = collect_futures(effective_date)
        if r:
            data['futures'] = {}
            for name, vals in r.items():
                data['futures'][name] = {'close': vals.get('close')}
    except Exception as e:
        data['futures'] = {'error': str(e)}

    # 6. 模拟交易对账 (DB-only, 无需外部采集)
    try:
        conn = get_db(write=False)
        sim_issues = reconcile_sim_trades(conn)
        conn.close()
        if sim_issues:
            data['sim_trades'] = {'issues': sim_issues}
    except Exception as e:
        data['sim_trades'] = {'error': str(e)}

    elapsed = time.time() - t0
    print(f"  [reconciler] Collected all data in {elapsed:.1f}s ({len(data)} lines)")

    # 保存快照
    if label:
        save_snapshot(data, label)

    return data


# ═══ Diff 引擎 ═══

def diff_snapshots(old_data: dict, new_data: dict, old_label: str, new_label: str) -> list[DiffItem]:
    """对比两个快照, 生成差异列表"""
    diffs = []

    # 1. 两融
    if 'margin' in old_data and 'margin' in new_data:
        old_m = old_data['margin']
        new_m = new_data['margin']
        for field in ['total', 'sh_margin', 'sz_margin']:
            if field in old_m and field in new_m and old_m[field] and new_m[field]:
                ov, nv = float(old_m[field]), float(new_m[field])
                if ov != 0:
                    diff_pct = abs(nv - ov) / abs(ov) * 100
                    if diff_pct > 0.001:  # > 0.001% 记录
                        level = DIVERGENCE_S2 if diff_pct >= 1 else (DIVERGENCE_S1 if diff_pct >= 0.01 else DIVERGENCE_S0)
                        action = 'disputed' if level == DIVERGENCE_S2 else ('auto_accept_logged' if level == DIVERGENCE_S1 else 'auto_accept')
                        diffs.append(DiffItem('margin', field, '两市合计' if field == 'total' else field,
                                             ov, nv, old_label, old_label, diff_pct, level, action))

    # 2. 北向
    if 'northbound' in old_data and 'northbound' in new_data:
        old_nb = old_data['northbound']
        new_nb = new_data['northbound']
        for field in ['total_net_buy', 'sh_net_buy', 'sz_net_buy']:
            if field in old_nb and field in new_nb:
                ov, nv = float(old_nb.get(field, 0) or 0), float(new_nb.get(field, 0) or 0)
                if ov != 0 and nv != 0:
                    diff_pct = abs(nv - ov) / max(abs(ov), 1) * 100
                    if diff_pct > 0.1:  # 北向 >0.1%差异记录(金额波动大)
                        level = DIVERGENCE_S1 if diff_pct < 10 else DIVERGENCE_S2
                        action = 'disputed' if level == DIVERGENCE_S2 else 'auto_accept_logged'
                        diffs.append(DiffItem('northbound', field, field, ov, nv,
                                             old_label, old_label, diff_pct, level, action))

    # 3. A股指数
    if 'a_indices' in old_data and 'a_indices' in new_data:
        old_idx = old_data['a_indices']
        new_idx = new_data['a_indices']
        for name in old_idx:
            if name in new_idx and 'close' in old_idx[name] and 'close' in new_idx[name]:
                ov = old_idx[name]['close']
                nv = new_idx[name]['close']
                if ov and nv:
                    diff_pct = abs(nv - ov) / abs(ov) * 100
                    if diff_pct > 0.001:
                        level = DIVERGENCE_S2 if diff_pct >= 1 else (DIVERGENCE_S1 if diff_pct >= 0.01 else DIVERGENCE_S0)
                        action = 'disputed' if level == DIVERGENCE_S2 else ('auto_accept_logged' if level == DIVERGENCE_S1 else 'auto_accept')
                        diffs.append(DiffItem('a_indices', 'close', name, ov, nv,
                                             old_label, old_label, diff_pct, level, action))

    # 4. 金龙
    if 'hxc' in old_data and 'hxc' in new_data:
        ov = old_data['hxc'].get('close', 0)
        nv = new_data['hxc'].get('close', 0)
        if ov and nv and ov != 0:
            diff_pct = abs(nv - ov) / abs(ov) * 100
            if diff_pct > 0.01:
                level = DIVERGENCE_S1 if diff_pct < 1 else DIVERGENCE_S2
                action = 'disputed' if level == DIVERGENCE_S2 else 'auto_accept_logged'
                diffs.append(DiffItem('hxc', 'close', 'HXC', ov, nv,
                                     old_label, old_label, diff_pct, level, action))

    return diffs


def reconcile_sim_trades(conn) -> dict:
    """Reconcile simulated trades: plan_execution, slot_completeness, duplicates, rec_history

    Args:
        conn: sqlite3 connection (from nous.data.storage.get_db)

    Returns:
        dict with keys:
            - issues: list[str] of alert messages
            - plan_count: int, total today plans
            - unexecuted_count: int
            - duplicate_count: int
    """
    import sqlite3
    today = date.today().isoformat()
    issues = []

    # 1. Unexecuted plans
    rows = conn.execute(
        "SELECT id, symbol, action, slot FROM sim_trade_plans "
        "WHERE date(created_at) = date('now') AND executed = 0 "
        "ORDER BY id"
    ).fetchall()
    unexecuted = len(rows)
    if unexecuted:
        details = ", ".join(f"{r['symbol']}/{r['action']}/s{r['slot']}" for r in rows[:8])
        extra = " ..." if len(rows) > 8 else ""
        issues.append(f"{unexecuted} plans not executed today: {details}{extra}")

    # 2. Missing slot buys for new entries
    new_entries = conn.execute(
        "SELECT rp.symbol FROM realtime_pool rp "
        "WHERE rp.pool_source = 'recommend' AND rp.active = 1 "
        "AND rp.symbol NOT IN ("
        "  SELECT DISTINCT symbol FROM sim_position WHERE shares > 0"
        ") ORDER BY rp.symbol"
    ).fetchall()

    missing_slots = 0
    for row in new_entries:
        sym = row["symbol"]
        buys = conn.execute(
            "SELECT slot FROM sim_trades "
            "WHERE symbol = ? AND trade_date = ? AND action = 'buy'",
            (sym, today),
        ).fetchall()
        bought = {b["slot"] for b in buys}
        for slot in (1, 2, 3):
            if slot not in bought:
                issues.append(f"Symbol {sym} missing slot {slot} buy (new entry)")
                missing_slots += 1

    # 3. Duplicate trades
    dups = conn.execute(
        "SELECT symbol, slot, action, COUNT(*) as cnt FROM sim_trades "
        "WHERE trade_date = ? "
        "GROUP BY symbol, slot, action, trade_date HAVING cnt > 1",
        (today,),
    ).fetchall()
    dup_count = len(dups)
    for d in dups:
        issues.append(
            f"Duplicate trades: {d['symbol']} slot {d['slot']} {d['action']} x{d['cnt']}"
        )

    # 4. Recommendation history consistency
    active_recs = conn.execute(
        "SELECT COUNT(*) as cnt FROM recommendation_history WHERE status = 'active'"
    ).fetchone()["cnt"]
    pool_recs = conn.execute(
        "SELECT COUNT(*) as cnt FROM realtime_pool WHERE active = 1 AND pool_source = 'recommend'"
    ).fetchone()["cnt"]
    if active_recs != pool_recs:
        issues.append(
            f"Recommendation mismatch: {active_recs} active recs vs {pool_recs} pool entries"
        )

    return {
        "issues": issues,
        "plan_count": unexecuted,
        "unexecuted_count": unexecuted,
        "missing_slot_count": missing_slots,
        "duplicate_count": dup_count,
    }


# ═══ 主对账流程 ═══

def run_reconcile(hour: int, effective_date: str = None, diff_only: bool = False) -> ReconcileReport:
    """
    执行对账

    Args:
        hour: 20/22/0
        effective_date: 数据日期
        diff_only: True=仅复采上一轮差异项, False=全量复采

    Returns:
        ReconcileReport
    """
    if effective_date is None:
        effective_date = date.today().strftime('%Y-%m-%d')

    label = f"{effective_date}_{hour:02d}00"
    report = ReconcileReport(
        timestamp=datetime.now().isoformat(),
        hour=hour,
        total_lines=5,
        full_reconcile=not diff_only,
    )

    if hour == 20:
        # 20:00 全量复采
        print(f"\n{'='*50}")
        print(f"[reconciler] 20:00 FULL RECONCILE — {effective_date}")
        print(f"{'='*50}")

        new_data = collect_all_data(effective_date, label=f"20h_{effective_date}")

        # 加载15:00和17:30快照
        old_15h = load_snapshot(f"POST_CLOSE_{effective_date}")
        old_17h = load_snapshot(f"hsgt_{effective_date}")

        all_diffs = []
        if old_15h:
            diffs_15 = diff_snapshots(old_15h, new_data, '15:00(收盘)', '20:00(复采)')
            all_diffs.extend(diffs_15)
            print(f"  15:00 vs 20:00: {len(diffs_15)} diffs")
        if old_17h:
            diffs_17 = diff_snapshots(old_17h, new_data, '17:30(盘后)', '20:00(复采)')
            all_diffs.extend(diffs_17)
            print(f"  17:30 vs 20:00: {len(diffs_17)} diffs")

        report.diffs = all_diffs

    else:
        # 22:00 / 00:00 — 差异单点复采
        prev_label = f"20h_{effective_date}" if hour == 22 else f"{effective_date}_22:00"
        print(f"\n[reconciler] {hour}:00 DIFF-ONLY RECONCILE")

        # 加载20:00的差异列表
        prev_report_file = RECONCILE_DIR / f"report_20h_{effective_date}.json"
        if not prev_report_file.exists():
            print(f"  No previous report at {prev_report_file}, skipping")
            return report

        prev = json.loads(prev_report_file.read_text())
        prev_diffs = prev.get('diffs', [])
        if not prev_diffs:
            print(f"  No diffs in previous report, skipping")
            return report

        # 仅复采有差异的数据线
        lines_to_recheck = set(d['data_line'] for d in prev_diffs)
        print(f"  Re-checking {len(lines_to_recheck)} data lines: {lines_to_recheck}")

        new_data = {}
        for line in lines_to_recheck:
            try:
                if line == 'margin':
                    r, _ = collect_margin(effective_date)
                    new_data['margin'] = r
                elif line == 'northbound':
                    r, _ = collect_northbound(effective_date)
                    new_data['northbound'] = r
                elif line == 'a_indices':
                    r, _ = collect_a_indices(effective_date)
                    new_data['a_indices'] = r
                elif line == 'hxc':
                    r, _ = collect_hxc(effective_date)
                    new_data['hxc'] = {'close': r.get('close') if isinstance(r, dict) else r}
            except Exception as e:
                print(f"  [{line}] re-collect failed: {e}")

        # 重新diff
        new_diffs = diff_snapshots(prev.get('new_snapshot', {}), new_data,
                                   f'{hour-2}:00', f'{hour}:00')
        report.diffs = new_diffs

    # 统计
    report.auto_accepted = sum(1 for d in report.diffs if d.action != 'disputed')
    report.disputed = sum(1 for d in report.diffs if d.action == 'disputed')

    # 打印报告
    print(f"\n[reconciler] REPORT ({hour}:00):")
    print(f"  Total diffs: {len(report.diffs)}")
    print(f"  Auto accepted: {report.auto_accepted}")
    print(f"  Disputed (need review): {report.disputed}")
    for d in report.diffs:
        flag = "🔴" if d.action == 'disputed' else ("🟡" if d.action == 'auto_accept_logged' else "🟢")
        print(f"  {flag} [{d.data_line}] {d.record_key}.{d.field}: "
              f"{d.old_value:.4f} → {d.new_value:.4f} ({d.diff_pct:.3f}%, {d.level})")

    # 模拟交易对账
    if 'sim_trades' in new_data:
        sim = new_data['sim_trades']
        if 'issues' in sim:
            issues = sim['issues'].get('issues', [])
            if issues:
                print(f"\n  [sim_trades] {len(issues)} issue(s):")
                for iss in issues:
                    print(f"    ⚠ {iss}")
            else:
                print(f"\n  [sim_trades] ✅ all clear")
        elif 'error' in sim:
            print(f"\n  [sim_trades] ❌ error: {sim['error']}")

    # 保存报告
    report_file = RECONCILE_DIR / f"report_{label}.json"
    report_data = {
        'timestamp': report.timestamp,
        'hour': hour,
        'full_reconcile': report.full_reconcile,
        'diffs': [
            {
                'data_line': d.data_line,
                'field': d.field,
                'record_key': d.record_key,
                'old_value': str(d.old_value),
                'new_value': str(d.new_value),
                'old_time': d.old_time,
                'diff_pct': d.diff_pct,
                'level': d.level,
                'action': d.action,
            }
            for d in report.diffs
        ],
        'auto_accepted': report.auto_accepted,
        'disputed': report.disputed,
    }
    report_file.write_text(json.dumps(report_data, ensure_ascii=False, indent=2))
    print(f"\n  Report saved: {report_file}")

    return report


# ═══ CLI ═══

if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='Night Reconciler')
    parser.add_argument('--hour', type=int, choices=[20, 22, 0], 
                       help='Reconcile hour (20/22/0)')
    parser.add_argument('--diff-only', action='store_true',
                       help='Only re-check divergent items (for 22:00/00:00)')
    parser.add_argument('--date', type=str, help='Effective date (YYYY-MM-DD)')
    args = parser.parse_args()

    if args.hour is None:
        # 自动判断
        now = datetime.now()
        if now.hour >= 20 and now.hour < 22:
            args.hour = 20
        elif now.hour >= 22:
            args.hour = 22
        elif now.hour < 2:
            args.hour = 0
        else:
            print("Not in reconcile window (20:00-02:00)")
            sys.exit(0)

    if args.hour != 20:
        args.diff_only = True

    report = run_reconcile(
        hour=args.hour,
        effective_date=args.date,
        diff_only=args.diff_only
    )

    if report.disputed > 0:
        sys.exit(1)  # 有争议→退出码1→cron告警
