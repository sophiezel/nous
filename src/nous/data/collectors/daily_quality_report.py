#!/usr/bin/env python3
"""
每日数据质量报告生成器 — daily_quality_report.py

从provenance_log + source_reliability + db统计生成可读markdown报告

用法:
    python -m src.collectors.daily_quality_report                    # 生成并打印
    python -m src.collectors.daily_quality_report --date 2026-05-17  # 指定日期
    python -m src.collectors.daily_quality_report --push             # 推送到Dashboard
"""

import sys
from pathlib import Path
from datetime import date, datetime, timedelta
from collections import Counter

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent.parent))

from nous.data.storage import connect_readonly


def generate_report(target_date: str = None) -> str:
    """生成markdown格式的数据质量报告"""
    if target_date is None:
        target_date = date.today().strftime('%Y-%m-%d')

    conn = connect_readonly()
    lines = []
    lines.append(f"# 数据质量日报 — {target_date}")
    lines.append(f"生成时间: {datetime.now().strftime('%H:%M:%S')}")
    lines.append("")

    try:
        # ═══ 1. 溯源概览 ═══
        lines.append("## 一、溯源覆盖")
        prov_rows = conn.execute(
            "SELECT table_name, COUNT(*) as cnt, "
            "SUM(CASE WHEN divergence_level='S0' THEN 1 ELSE 0 END) as s0, "
            "SUM(CASE WHEN divergence_level='S1' THEN 1 ELSE 0 END) as s1, "
            "SUM(CASE WHEN divergence_level='S2' THEN 1 ELSE 0 END) as s2, "
            "ROUND(AVG(confidence), 3) as avg_conf "
            "FROM data_provenance_log WHERE effective_at = ? "
            "GROUP BY table_name ORDER BY cnt DESC",
            (target_date,)
        ).fetchall()

        if prov_rows:
            lines.append("| 数据表 | 记录数 | S0 | S1 | S2 | 均置信度 |")
            lines.append("|--------|--------|----|----|----|----------|")
            total_s2 = 0
            for r in prov_rows:
                lines.append(f"| {r['table_name']} | {r['cnt']} | {r['s0']} | {r['s1']} | {r['s2']} | {r['avg_conf']:.3f} |")
                total_s2 += r['s2']
            if total_s2 > 0:
                lines.append(f"\n⚠️ **{total_s2}条S2高分歧记录需要人工审查**")
        else:
            lines.append(f"*当日无溯源数据 — 采集可能未运行*")

        # ═══ 2. 源可靠性评分 ═══
        lines.append("\n## 二、源可靠性评分")
        sr_rows = conn.execute(
            "SELECT source_name, data_type, "
            "ROUND(CAST(alpha AS REAL)/(alpha+beta), 3) as reliability, "
            "total_attempts, total_successes, total_divergences, "
            "ROUND(avg_latency_ms) as avg_latency "
            "FROM source_reliability ORDER BY reliability DESC"
        ).fetchall()

        if sr_rows:
            lines.append("| 源 | 数据类型 | 可靠性 | 尝试 | 成功 | 分歧 | 延迟ms |")
            lines.append("|----|---------|--------|------|------|------|--------|")
            for r in sr_rows:
                flag = "🟢" if r['reliability'] > 0.9 else ("🟡" if r['reliability'] > 0.7 else "🔴")
                lines.append(f"| {flag} {r['source_name']} | {r['data_type']} | {r['reliability']:.3f} | {r['total_attempts']} | {r['total_successes']} | {r['total_divergences']} | {r['avg_latency']} |")

        # ═══ 4. 对账摘要 ═══
        lines.append("\n## 四、对账摘要")
        reconcile_dir = Path.home() / ".hermes" / "cache" / "reconcile"
        import json
        for h in [20, 22, 0]:
            fname = f"report_{target_date}_{h:02d}:00.json"
            if h == 0:
                fname = f"report_{target_date}_00:00.json"
            fpath = reconcile_dir / fname
            if fpath.exists():
                try:
                    data = json.loads(fpath.read_text())
                    auto = data.get('auto_accepted', 0)
                    disp = data.get('disputed', 0)
                    diffs = data.get('diffs', [])
                    lines.append(f"- {h}:00 对账: {len(diffs)}差异, {auto}自动采纳, {disp}争议")
                except:
                    pass

        # ═══ 5. sync_outbox状态 ═══
        lines.append("\n## 五、同步队列")
        ob_rows = conn.execute(
            "SELECT status, COUNT(*) as cnt FROM sync_outbox GROUP BY status"
        ).fetchall()
        if ob_rows:
            for r in ob_rows:
                lines.append(f"- {r['status']}: {r['cnt']}")
        else:
            lines.append("- 同步队列为空")

        # ═══ 6. SLA达标 ═══
        lines.append("\n## 六、SLA达标")
        sla = {}
        if prov_rows:
            total = sum(r['cnt'] for r in prov_rows)
            s2 = sum(r['s2'] for r in prov_rows)
            sla['溯源覆盖率'] = '✅ 100%' if total > 0 else '❌'
            sla['低分歧率(S0+S1)'] = f"✅ {(total-s2)/total*100:.1f}%" if total > 0 else 'N/A'
            sla['高分歧率(S2)'] = f"{'✅' if s2/total*100 < 1 else '❌'} {s2/total*100:.1f}%" if total > 0 else 'N/A'
        if sr_rows:
            avg_rel = sum(r['reliability'] for r in sr_rows) / len(sr_rows)
            sla['源平均可靠性'] = f"{'✅' if avg_rel > 0.85 else '⚠️'} {avg_rel:.3f}"
        if v2_rows and v2_rows[0]['total'] > 0:
            sla['源覆盖度≥2'] = f"{'✅' if dual_pct >= 50 else '⚠️'} {dual_pct:.1f}%"

        for k, v in sla.items():
            lines.append(f"- {k}: {v}")

    except Exception as e:
        lines.append(f"\n❌ 报告生成异常: {e}")
    finally:
        conn.close()

    return "\n".join(lines)


def save_and_push(report: str, target_date: str, push: bool = False):
    """保存报告并可选推送到Dashboard"""
    out_dir = Path.home() / ".hermes" / "cache" / "reports"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"quality_report_{target_date}.md"
    out_path.write_text(report)
    print(f"报告已保存: {out_path}")

    if push:
        try:
            from nous.data.collectors.multi_source import write_to_outbox
            write_to_outbox('quality_report', target_date, {
                'content': report,
                'generated_at': datetime.now().isoformat(),
            })
            print("已推送到sync_outbox")
        except Exception as e:
            print(f"推送失败: {e}")


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='Daily Data Quality Report')
    parser.add_argument('--date', type=str, help='Target date (YYYY-MM-DD)')
    parser.add_argument('--push', action='store_true', help='Push to Dashboard')
    args = parser.parse_args()

    target = args.date or date.today().strftime('%Y-%m-%d')
    report = generate_report(target)
    print(report)
    save_and_push(report, target, push=args.push)
