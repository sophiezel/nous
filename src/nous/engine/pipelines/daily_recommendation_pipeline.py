"""统一每日荐股Pipeline编排器 v1

流程:
  1. 覆盖率检查 (stock_daily新鲜度)
  2. Stage 1: 粗筛 (coarse_filter 四池)  
  3. Stage 2: 因子计算 + 模型预测 (复用已有infra)
  4. Stage 3: 精准过滤 (soul L2 + 风控)
  5. 写入 recommendation_pool 结构化表
  6. 生成 daily_summary.json (供LLM报告生成)
"""

import sys
import time
import json
import sqlite3
from datetime import date, datetime
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from nous.engine.pipelines.coarse_filter import (
    coarse_filter_a_long, coarse_filter_a_short,
    coarse_filter_hk_long, coarse_filter_hk_short
)
from nous.data.storage import get_db

from nous.core.db import _resolve_path
DB_PATH = Path(_resolve_path("screener.db"))
REPORT_DIR = Path.home() / "wiki" / "finance" / "reports" / "summary"
REPORT_DIR.mkdir(parents=True, exist_ok=True)


# ══════════════════════════════════════════════
# 覆盖率门禁
# ══════════════════════════════════════════════

def check_readiness() -> dict:
    """Pipeline前置检查 — 对接 sla_registry / data_assert ConsumerContract."""
    status = {
        "ready": True,
        "issues": [],
        "block_short": False,
        "daily_lag_days": 0,
        "degraded": [],
        "assert_ok": True,
    }
    try:
        from nous.data.quality.data_assert import run_assert
        from nous.data.quality.trading_calendar import trading_day_lag, previous_trading_day

        report = run_assert(consumer="recommend", include_integrity=True)
        status["assert_ok"] = report.p0_ok
        status["degraded"] = list(report.degraded)
        status["last_trade_date"] = report.last_trade_date

        for c in report.checks:
            if c.key == "stock_daily_a":
                status["daily_lag_days"] = c.lag_trading_days or 0
                if (c.lag_trading_days or 0) > 1:
                    status["block_short"] = True
                    status["issues"].append(
                        f"A股日线交易日滞后{c.lag_trading_days}天>1 → 短线池拒绝写入"
                    )
            if not c.ok:
                tag = "DEGRADED" if c.soft_fail else c.priority
                status["issues"].append(f"[{tag}] {c.label}: {c.detail}")

        # P0 failure → hard not ready (forbid silent run on bad bars)
        if not report.p0_ok:
            status["ready"] = False
            status["issues"].append("P0 鲜度/完整性断言失败 → 拒跑荐股")
        elif not report.p1_ok:
            # P1 → allow with degradation (coarse-only / no short)
            status["block_short"] = True
            status["degraded"].append("p1_assert")
            status["issues"].append("P1 断言失败 → 降级运行(拦短池)")

        if "models_lgb" in report.degraded or any(
            (not c.ok and c.key == "models_lgb") for c in report.checks
        ):
            status["degraded"].append("ml_models")
            status["issues"].append("无可用/过期模型 → DEGRADED coarse-only")

    except Exception as e:
        # Fallback to legacy light checks if assert import/DB fails hard
        status["issues"].append(f"assert异常回退: {e}")
        try:
            import sqlite3
            from datetime import date as _date

            conn = sqlite3.connect(str(DB_PATH))
            conn.execute("PRAGMA busy_timeout=5000")
            row = conn.execute("SELECT MAX(trade_date) FROM stock_daily").fetchone()
            if row and row[0]:
                lag = (_date.today() - _date.fromisoformat(row[0])).days
                status["daily_lag_days"] = lag
                if lag > 1:
                    status["block_short"] = True
                    status["issues"].append(f"A股日线滞后{lag}天>1天 → 短线池拒绝写入")
            conn.close()
        except Exception as e2:
            status["ready"] = False
            status["issues"].append(f"日线不可读: {e2}")

    return status


def _get_market_regime(as_of_date: str = None) -> tuple[str, dict]:
    """从index_daily判定当前市场体制
    
    Args:
        as_of_date: 回测模式下的截止日期(YYYY-MM-DD), None=今天
    
    Returns: (regime_str, extra_factors)
      extra_factors包含: rsi14, daily_change, is_bull_trap
    """
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("PRAGMA busy_timeout=5000")
    extra = {}
    try:
        where = f"AND trade_date <= '{as_of_date}'" if as_of_date else ""
        rows = conn.execute(
            f"SELECT close FROM index_daily WHERE symbol='IDX_000001' {where} ORDER BY trade_date DESC LIMIT 15"
        ).fetchall()
        if len(rows) >= 14:
            closes = [r[0] for r in rows if r[0]]
            if closes:
                # 单日涨跌
                daily_change = (closes[0] - closes[1]) / closes[1] * 100 if len(closes) >= 2 else 0
                extra['daily_change'] = round(daily_change, 2)
                # RSI14
                gains, losses = 0, 0
                for i in range(1, min(14, len(closes))):
                    diff = closes[i-1] - closes[i]
                    if diff > 0: gains += diff
                    else: losses += abs(diff)
                avg_gain = gains / 13
                avg_loss = losses / 13
                rsi = 100 - 100 / (1 + avg_gain / avg_loss) if avg_loss > 0 else 100
                extra['rsi14'] = round(rsi, 1)
                
                if daily_change < -2:
                    regime = "BEAR"
                    # BULL_TRAP检测: 熊市中RSI<30+单日暴跌>3% → 可能反弹
                    if rsi < 30 and daily_change < -3:
                        extra['is_bull_trap'] = True
                        print(f"[regime] BEAR+Trap: RSI14={rsi:.0f}, 日跌{daily_change:.1f}%")
                elif daily_change > 1:
                    regime = "BULL"
                else:
                    regime = "SIDEWAYS"
                return (regime, extra)
        return ("SIDEWAYS", extra)
    finally:
        conn.close()


def _write_heartbeat(status_str: str, elapsed: float, picks: int, degraded_pools: list = None):
    """写入健康检查心跳"""
    try:
        conn = sqlite3.connect(str(DB_PATH))
        conn.execute("PRAGMA busy_timeout=3000")
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS pipeline_heartbeat (
                check_time TEXT, check_type TEXT, status TEXT, detail TEXT
            );
        """)
        detail = f"{picks} picks, {elapsed:.1f}s"
        if degraded_pools:
            detail += f", DEGRADED: {','.join(degraded_pools)}"
        conn.execute(
            "INSERT INTO pipeline_heartbeat VALUES (datetime('now','localtime'),?,?,?)",
            ("pipeline_recommend", status_str, detail)
        )
        conn.commit()
        conn.close()
    except Exception:
        pass  # 心跳失败不影响主流程


def _write_model_registry(pool_type: str, model_path: str):
    """记录模型使用情况"""
    if not model_path:
        return
    try:
        conn = sqlite3.connect(str(DB_PATH))
        conn.execute("PRAGMA busy_timeout=3000")
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS pipeline_model_log (
                used_date TEXT, pool_type TEXT, model_path TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)
        conn.execute(
            "INSERT INTO pipeline_model_log VALUES (?,?,?,datetime('now','localtime'))",
            (date.today().isoformat(), pool_type, model_path)
        )
        conn.commit()
        conn.close()
    except Exception:
        pass


# ══════════════════════════════════════════════
# Stage 3: 精准过滤
# ══════════════════════════════════════════════

def precision_filter(symbols: list[str], market: str, period: str,
                     max_positions: int = 15, as_of_date: str = None) -> list[dict]:
    """Stage 3: 最终约束过滤
    
    1. K0状态检查 
    2. 行业集中度检查 (同行业≤2只)
    3. 宏观因子标注(A_long)
    4. HK PE合理性检查 (PE>500标记⚠️)
    5. HK_short T+0因子(日内振幅/竞价偏离)
    
    Args:
        as_of_date: 回测模式截止日期(YYYY-MM-DD), None=今天
                    所有stock_daily查询约束为 trade_date <= as_of_date
    """
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("PRAGMA busy_timeout=5000")
    conn.row_factory = sqlite3.Row
    
    # 回测模式: 构造WHERE子句限制数据截止日期；历史 as_of 走窄窗分区 UNION
    from nous.data.storage.daily_bars import (
        approx_start_for_lookback,
        daily_relation_sql,
    )

    date_clause = f"AND trade_date <= '{as_of_date}'" if as_of_date else ""
    date_clause_15 = f"WHERE symbol = ? AND trade_date <= '{as_of_date}'" if as_of_date else "WHERE symbol = ?"
    if as_of_date:
        # ATR/近2日只需短窗；禁止 start=None 扫 2009→as_of 全历史 UNION
        _daily = daily_relation_sql(
            approx_start_for_lookback(as_of_date, 40),
            as_of_date,
            conn=conn,
        )
    else:
        _daily = "stock_daily"  # live recommend: hot table freshness
    
    macro_factors = {}
    if market == 'A' and period == 'long':
        macro_factors = _read_macro_factors(conn)
    
    industry_count = {}
    picks = []
    
    for sym in symbols[:max_positions * 3]:
        if len(picks) >= max_positions:
            break
        
        row = conn.execute(
            "SELECT sb.name, sf.pe, sf.pb, sf.roe, sf.total_mv "
            "FROM stock_basic sb LEFT JOIN stock_fundamental sf ON sb.symbol = sf.symbol "
            "WHERE sb.symbol=?",
            (sym,)
        ).fetchone()
        if not row:
            continue

        # Quarantine skip
        try:
            from nous.data.quality.quarantine import is_quarantined
            if is_quarantined(conn, sym, as_of_date):
                continue
        except Exception:
            pass

        # Cross-validate close vs Sina (live only; skip in replay)
        db_close_preview = None
        try:
            d0 = conn.execute(
                f"SELECT close FROM {_daily} WHERE symbol=? {date_clause} ORDER BY trade_date DESC LIMIT 1",
                (sym,),
            ).fetchone()
            if d0:
                db_close_preview = d0[0] if not isinstance(d0, sqlite3.Row) else d0["close"]
        except Exception:
            pass

        pe_warning_cv = ""
        if not as_of_date and db_close_preview and market == "A":
            try:
                from nous.data.collectors.sim_executor import fetch_sina_price
                from nous.data.quality.validators import cross_validate_close
                from nous.data.quality.quarantine import quarantine_symbols
                sina_px, _stale = fetch_sina_price(sym)
                if sina_px and sina_px > 0:
                    cv = cross_validate_close(sym, float(db_close_preview), float(sina_px))
                    if cv.severity == "error":
                        quarantine_symbols(
                            conn, [sym], reason=cv.reason, severity="error",
                            as_of=date.today().isoformat(),
                        )
                        print(f"  ⛔ cross_validate drop {sym}: {cv.reason}", file=sys.stderr)
                        continue
                    if cv.severity == "warning":
                        pe_warning_cv = f"⚠️交叉验证{cv.reason}"
            except Exception:
                pass
        
        # PE合理性检查 (港股PE>500视为fallback)
        pe_warning = pe_warning_cv
        if market == 'HK' and row['pe'] and row['pe'] > 500:
            pe_warning = (pe_warning + ' ' if pe_warning else '') + '⚠️PE异常'
        
        industry = _get_industry(conn, sym)
        if industry and industry != 'unknown' and industry_count.get(industry, 0) >= 2:
            continue
        industry_count[industry] = industry_count.get(industry, 0) + 1
        
        daily = conn.execute(
            f"SELECT close, volume, high, low, open FROM {_daily} WHERE symbol=? {date_clause} ORDER BY trade_date DESC LIMIT 2",
            (sym,)
        ).fetchall()
        if not daily:
            continue
        
        # T+0因子 (HK_short)
        t0_factors = {}
        if market == 'HK' and period == 'short' and len(daily) >= 2:
            # 日内振幅
            if daily[0]['high'] and daily[0]['low'] and daily[0]['close']:
                amplitude = (daily[0]['high'] - daily[0]['low']) / daily[0]['close'] * 100
                t0_factors['intraday_amplitude'] = round(amplitude, 1)
            # 竞价跳空 (today open vs yesterday close)
            if daily[0]['open'] and daily[1]['close'] and daily[1]['close'] > 0:
                gap = (daily[0]['open'] - daily[1]['close']) / daily[1]['close'] * 100
                t0_factors['auction_gap'] = round(gap, 1)
        
        pick = {
            'symbol': sym, 'name': row['name'] or sym,
            'market': market, 'period': period,
            'pe': row['pe'], 'pb': row['pb'], 'roe': row['roe'],
            'total_mv': row['total_mv'],
            'close': daily[0]['close'], 'volume': daily[0]['volume'],
            'industry': industry,
            'position_pct': round(min(0.15, 0.05 + 0.02 * (max_positions - len(picks))), 2),
        }
        
        # ATR止损价计算 (14日ATR, 2x)
        atr_rows = conn.execute(
            f"SELECT close, high, low FROM {_daily} {date_clause_15} ORDER BY trade_date DESC LIMIT 15",
            (sym,)
        ).fetchall()
        atr_prices = [r for r in atr_rows if r['close'] and r['high'] and r['low']]
        if len(atr_prices) >= 2:
            trs = []
            for j in range(1, len(atr_prices)):
                cur = atr_prices[j-1]
                prev = atr_prices[j]
                if cur['high'] and cur['low'] and cur['close'] and prev['close'] and prev['close'] > 0:
                    h, l, c = cur['high'], cur['low'], cur['close']
                    prev_c = prev['close']
                    tr = max(h-l, abs(h-prev_c), abs(l-prev_c))
                    trs.append(tr)
            if trs:
                atr14 = sum(trs) / len(trs)
                if daily[0]['close'] and daily[0]['close'] > 0:
                    pick['atr14'] = round(atr14, 2)
                    pick['atr_atr_pct'] = round(atr14 / daily[0]['close'] * 100, 1)
                    pick['stop_loss_price'] = round(daily[0]['close'] - 2 * atr14, 2)
        
        if pe_warning:
            pick['pe_warning'] = pe_warning
        if macro_factors:
            pick['macro'] = macro_factors
        if t0_factors:
            pick['t0'] = t0_factors
        
        picks.append(pick)
    
    conn.close()
    return picks


def _read_macro_factors(conn) -> dict:
    """读取宏观因子"""
    factors = {}
    try:
        row = conn.execute("SELECT 制造业指数 FROM macro_pmi ORDER BY 月份 DESC LIMIT 1").fetchone()
        if row: factors['pmi'] = round(float(row[0]), 1)
    except Exception: pass
    try:
        row = conn.execute("SELECT cpi_yoy FROM macro_cpi ORDER BY trade_date DESC LIMIT 1").fetchone()
        if row: factors['cpi'] = round(float(row[0]), 1)
    except Exception: pass
    try:
        row = conn.execute("SELECT M2同比 FROM macro_m2 ORDER BY 月份 DESC LIMIT 1").fetchone()
        if row: factors['m2'] = round(float(row[0]), 1)
    except Exception: pass
    try:
        row = conn.execute("SELECT O_N定价 FROM macro_shibor ORDER BY 日期 DESC LIMIT 1").fetchone()
        if row: factors['shibor'] = round(float(row[0]), 2)
    except Exception: pass
    return factors


def _get_industry(conn, symbol: str) -> str:
    """获取股票行业"""
    try:
        row = conn.execute(
            "SELECT industry FROM stock_industry WHERE symbol=? LIMIT 1", (symbol,)
        ).fetchone()
        return row[0] if row else 'unknown'
    except Exception:
        return 'unknown'


# ══════════════════════════════════════════════
# 写入推荐池
# ══════════════════════════════════════════════

def _pool_display_score(rank_in_pool: int, merged_score: float | None = None) -> float:
    """Per-pool display score 9.0 → ~7.0 by rank; prefer ML merged_score when present."""
    if merged_score is not None:
        # Map typical ML/coarse scores into 1–10 display band
        try:
            s = float(merged_score)
            if 0 <= s <= 10:
                return round(min(10.0, max(1.0, s)), 2)
            # If model outputs unbounded, use rank fallback
        except (TypeError, ValueError):
            pass
    return round(max(7.0, 9.0 - rank_in_pool * 0.05), 2)


def write_recommendation_pool(picks: list[dict], report_date: str = None):
    """将最终推荐写入 recommendation_pool 表（按池独立排名，消灭全局 7.0 天花板）"""
    if not report_date:
        report_date = date.today().isoformat()
    
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("PRAGMA busy_timeout = 30000")
    
    # 鳄鱼派信号评估
    try:
        from nous.engine.signals.crocodile_signals import evaluate_crocodile_signals
        croc = evaluate_crocodile_signals(conn, report_date)
    except Exception as e:
        print(f"  ⚠️ 鳄鱼派信号计算失败: {e}", file=sys.stderr)
        croc = None

    # Quarantine filter
    try:
        from nous.data.quality.quarantine import get_quarantined
        quarantined = get_quarantined(conn, report_date)
    except Exception:
        quarantined = set()
    
    # Group by (market, period) for independent ranking
    from collections import defaultdict
    pools: dict[tuple, list] = defaultdict(list)
    for p in picks:
        if p.get("symbol") in quarantined:
            print(f"  ⛔ quarantine skip {p.get('symbol')}", file=sys.stderr)
            continue
        key = (p.get("market", ""), p.get("period", ""))
        pools[key].append(p)

    inserted = 0
    for _key, pool_picks in pools.items():
        # Prefer existing ML score ordering if present
        pool_picks = sorted(
            pool_picks,
            key=lambda x: float(x.get("merged_score") or x.get("score") or 0),
            reverse=True,
        )
        for i, p in enumerate(pool_picks):
            score = _pool_display_score(
                i, p.get("merged_score") if "merged_score" in p else p.get("ml_score"),
            )
            
            # 生成鳄鱼派buy_reason
            buy_reason = _generate_buy_reason(croc, p) if croc else ''
            
            # 鳄鱼派信号调整评分
            if croc and croc['total_score'] >= 80:
                score = min(10.0, score * 1.1)
            elif croc and croc['total_score'] < 40:
                score = max(1.0, score * 0.8)
            
            try:
                conn.execute(
                    """INSERT OR REPLACE INTO recommendation_pool
                       (rec_date, symbol, name, market, cycle, score, pe, rsi, volume_ratio, buy_reason)
                       VALUES (?,?,?,?,?,?,?,?,?,?)""",
                    (report_date, p['symbol'], p.get('name', ''),
                     p.get('market', ''), p.get('period', ''),
                     score, p.get('pe'), p.get('rsi'), p.get('volume_ratio'),
                     buy_reason)
                )
                inserted += 1
            except Exception as e:
                print(f"  ⚠️ {p['symbol']}: {e}", file=sys.stderr)
    
    conn.commit()
    
    # TTL清理: 保留最近90天
    cutoff = (date.today() - __import__('datetime').timedelta(days=90)).isoformat()
    conn.execute("DELETE FROM recommendation_pool WHERE rec_date < ?", (cutoff,))
    conn.commit()
    
    conn.close()
    print(f"[pipeline] 写入 {inserted} 条到 recommendation_pool")
    return inserted


def _generate_buy_reason(croc: dict, pick: dict) -> str:
    """生成鳄鱼派口吻的买入理由"""
    reasons = []
    
    if croc:
        signals = croc.get('signals', {})
        
        # 两只脚
        tf = signals.get('two_feet', {})
        if tf.get('status') == '强共振':
            reasons.append('两只脚强共振')
        elif tf.get('status') == '弱共振':
            reasons.append('两只脚弱共振')
        elif tf.get('status') == '分化':
            reasons.append('两只脚分化')
        elif tf.get('status') == '强分化':
            reasons.append('两只脚强分化')
        
        # 主线阶段
        ml = signals.get('mainline', {})
        if ml.get('theme'):
            reasons.append(f"主线:{ml['theme']}({ml.get('stage', '?')})")
        
        # 火车头
        loco = signals.get('locomotive', {})
        if loco.get('status') == '正常带':
            reasons.append('火车头在带')
        elif loco.get('status') == '低开预警':
            reasons.append('⚠火车头低开')
        
        # 拥挤度
        crowd = signals.get('crowding', {})
        if crowd.get('level') == '预警':
            reasons.append('拥挤度预警')
        elif crowd.get('level') == '极度拥挤':
            reasons.append('⚠拥挤度极高')
        
        # 资金
        cap = signals.get('capital', {})
        if cap.get('signal') != '中性':
            reasons.append(cap['signal'])
    
    # 个股特征
    if pick.get('pe') and pick['pe'] < 20:
        reasons.append('低PE')
    if pick.get('rsi') and 30 < pick['rsi'] < 50:
        reasons.append('RSI低位')
    
    return '+'.join(reasons) if reasons else '技术面达标'


# ══════════════════════════════════════════════
# 生成 daily_summary.json
# ══════════════════════════════════════════════

def generate_daily_summary(all_picks: dict, report_date: str = None) -> dict:
    """生成市场环境 + 推荐汇总 JSON
    
    Args:
        report_date: 报告日期(YYYY-MM-DD), None=今天
    """
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("PRAGMA busy_timeout=5000")
    
    today = report_date or date.today().isoformat()
    
    # 大盘指数
    index_data = {}
    for idx_sym, name in [('IDX_000001', 'sh_index'), ('IDX_399001', 'sz_index'), ('IDX_HSI', 'hk_hsi')]:
        row = conn.execute(
            "SELECT close FROM index_daily WHERE symbol=? ORDER BY trade_date DESC LIMIT 2",
            (idx_sym,)
        ).fetchall()
        if len(row) >= 2:
            change = (row[0][0] - row[1][0]) / row[1][0] * 100
            index_data[name] = {"close": round(row[0][0], 2), "change_pct": round(change, 2)}
    
    # 市场状态判断
    regime = "UNKNOWN"
    if index_data.get('sh_index', {}).get('change_pct', 0) < -2:
        regime = "BEAR"
    elif index_data.get('sh_index', {}).get('change_pct', 0) > 1:
        regime = "BULL"
    else:
        regime = "SIDEWAYS"
    
    # 数据新鲜度
    max_daily = conn.execute("SELECT MAX(trade_date) FROM stock_daily").fetchone()[0]
    lag_days = (date.today() - date.fromisoformat(max_daily)).days if max_daily else 999
    
    conn.close()
    
    summary = {
        "date": today,
        "generated_at": datetime.now().isoformat(),
        "market": index_data,
        "market_regime": regime,
        "data_freshness": {
            "stock_daily_latest": max_daily,
            "lag_days": lag_days
        },
        "picks_summary": {
            market: {
                period: {
                    "count": len(picks),
                    "top5_symbols": [p['symbol'] for p in picks[:5]],
                    "top5_names": [p.get('name', '') for p in picks[:5]],
                }
                for period, picks in periods.items()
            }
            for market, periods in all_picks.items()
        }
    }
    
    # 写入文件 (保留最近3个版本)
    summary_path = REPORT_DIR / f"{today}.json"
    with open(summary_path, 'w') as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    # 循环版本
    for ver in range(2, 0, -1):
        old = REPORT_DIR / f"{today}_v{ver}.json"
        new = REPORT_DIR / f"{today}_v{ver+1}.json"
        if old.exists():
            old.rename(new)
    # 保存当前为v1
    import shutil
    shutil.copy(summary_path, REPORT_DIR / f"{today}_v1.json")
    print(f"[pipeline] daily_summary → {summary_path}")
    
    return summary


# ══════════════════════════════════════════════
# Stage 2: ML模型评分
# ══════════════════════════════════════════════

def stage2_ml_scoring(symbols: list, pool_type: str, top_n: int = 100) -> list:
    """Stage 2: 加载对应池的ML模型, 对coarse后的标的做因子计算+预测
    
    返回: ([(symbol, merged_score), ...], degraded: bool)
    """
    if not symbols:
        return ([], False)
    
    POOL_MODEL_MAP = {
        'A_long': 'lgb_a_long_', 'A_short': 'lgb_a_short_',
        'HK_long': 'lgb_hk_long_', 'HK_short': 'lgb_hk_short_',
    }
    model_prefix = POOL_MODEL_MAP.get(pool_type, 'lgb_')
    
    try:
        from nous.engine.ml.predict import predict_scores, load_latest_model, write_ml_scores
        from nous.engine.ml.factor_compute import compute_all_factors
        
        # 1. 因子计算(优先用已有快照, 避免全量重算)
        factors_df = None
        FACTOR_DIR = Path.home() / "code/stock-screener/data/factors"
        # HK池用hk_latest.parquet, A池用latest.parquet
        snapshot_name = "hk_latest.parquet" if pool_type.startswith("HK") else "latest.parquet"
        factor_snapshot = FACTOR_DIR / snapshot_name
        if factor_snapshot.exists():
            import pandas as pd
            try:
                factors_df = pd.read_parquet(factor_snapshot)
                # 过滤到coarse子集
                factors_df = factors_df[factors_df['symbol'].isin(symbols)]
                print(f"  [ML] 从快照加载因子({snapshot_name}): {len(factors_df)} stocks")
            except Exception:
                pass
        else:
            print(f"  [ML] ⚠️ {snapshot_name} 不存在, 回退coarse排名")
        
        if factors_df is None or factors_df.empty:
            # fallback: 无因子数据时直接用coarse排名
            degraded = True
            return ([(s, 5.0 - i*0.02) for i, s in enumerate(symbols[:top_n])], degraded)
        
        # 2. 加载模型(优先pool专用模型, 回退通用)
        model = None
        model_path = ''
        degraded = False
        try:
            model_dir = Path.home() / "code/stock-screener/data/models"
            candidates = sorted(model_dir.glob(f"{model_prefix}*.pkl"), 
                              key=lambda p: p.stat().st_mtime, reverse=True)
            if candidates:
                import joblib
                model = joblib.load(str(candidates[0]))  # 最新修改的文件
                model_path = str(candidates[0])
        except Exception:
            pass
        
        if model is None:
            try:
                model = load_latest_model()
            except Exception:
                degraded = True
                return ([(s, 5.0 - i*0.02) for i, s in enumerate(symbols[:top_n])], degraded)
        
        # 3. 预测
        top_df = predict_scores(factors_df=factors_df, model=model, top_n=top_n)
        write_ml_scores(top_df, pool_type, model_path)
        
        # 模型注册(追踪)
        try:
            _write_model_registry(pool_type, model_path)
        except Exception:
            pass
        
        # 4. 合并: coarse_rank*0.3 + ml_score_norm*0.7
        sym_to_coarse_rank = {s: i+1 for i, s in enumerate(symbols)}
        merged = []
        for _, row in top_df.iterrows():
            sym = str(row['symbol'])
            ml_norm = float(row.get('model_score_norm', 5) or 5) / 10.0  # 0-1
            coarse_rank = sym_to_coarse_rank.get(sym, len(symbols))
            coarse_norm = 1.0 - min(coarse_rank / len(symbols), 0.99)
            merged_score = coarse_norm * 0.3 + ml_norm * 0.7
            merged.append((sym, merged_score))
        
        merged.sort(key=lambda x: x[1], reverse=True)
        return (merged[:top_n], degraded)
        
    except Exception as e:
        print(f"  [ML] {pool_type} failed: {e}, fallback to coarse only")
        degraded = True
        return ([(s, 5.0 - i*0.02) for i, s in enumerate(symbols[:top_n])], degraded)


def compute_live_ic(pool_type: str = None):
    """计算实盘Rank IC: ml_scores预测排名 vs 实际收益排名
    
    从ml_scores取历史预测 → 对比stock_daily实际forward收益
    写入quant_ic_history供model-health-check消费
    """
    try:
        conn = sqlite3.connect(str(DB_PATH))
        conn.execute("PRAGMA busy_timeout=5000")
        
        where = f"WHERE pool_type='{pool_type}'" if pool_type else ""
        rows = conn.execute(f"""
            SELECT m.trade_date, m.symbol, m.model_score_norm,
                   s1.close as entry_close, s2.close as exit_close
            FROM ml_scores m
            JOIN stock_daily s1 ON m.symbol=s1.symbol AND m.trade_date=s1.trade_date
            JOIN stock_daily s2 ON m.symbol=s2.symbol 
                AND s2.trade_date = (SELECT MIN(trade_date) FROM stock_daily 
                    WHERE symbol=m.symbol AND trade_date > m.trade_date
                    LIMIT 1 OFFSET 4)
            {where}
            ORDER BY m.trade_date DESC LIMIT 500
        """).fetchall()
        
        if len(rows) < 20:
            conn.close()
            return None
        
        scores = [r[2] or 0 for r in rows]
        returns = [(r[4]-r[3])/r[3] if r[3] and r[4] and r[3] > 0 else 0 for r in rows]
        
        from scipy.stats import spearmanr
        ic, pvalue = spearmanr(scores, returns)
        ic = round(ic, 4) if not (ic is None or str(ic) == 'nan') else 0
        
        # 写入
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS quant_ic_history (
                calc_date TEXT, pool_type TEXT, live_rank_ic REAL
            );
        """)
        conn.execute("INSERT INTO quant_ic_history VALUES (?,?,?)",
            (date.today().isoformat(), pool_type or 'all', ic))
        conn.commit()
        conn.close()
        return ic
    except Exception as e:
        return None


# ══════════════════════════════════════════════
# 主编排函数
# ══════════════════════════════════════════════

def run_pipeline(dry_run: bool = False, force: bool = False, replay_date: str = None) -> dict:
    """执行完整每日荐股Pipeline
    
    Args:
        dry_run: True时不写入DB
        force: True时强制重新运行(忽略今日已有推荐)
        replay_date: 回测模式指定日期(YYYY-MM-DD), None=今天
    
    Returns:
        dict with all_picks and summary
    """
    t0 = time.time()
    is_replay = replay_date is not None
    if is_replay:
        report_date = replay_date
    else:
        # 16:00后运行 → 为次日生成; 16:00前 → 为今日生成
        now = datetime.now()
        if now.hour >= 16:
            # 使用交易日历获取下一交易日
            try:
                from nous.core.utils.trading_calendar import next_trading_day
                report_date = next_trading_day(date.today()).isoformat()
            except Exception:
                report_date = (date.today() + __import__('datetime').timedelta(days=1)).isoformat()
        else:
            report_date = date.today().isoformat()
    
    # 幂等保护: 该日已有推荐且非强制模式 → skip
    if not force and not dry_run:
        try:
            conn = sqlite3.connect(str(DB_PATH))
            cnt = conn.execute(
                "SELECT COUNT(*) FROM recommendation_pool WHERE rec_date=?",
                (report_date,)).fetchone()[0]
            conn.close()
            if cnt > 0:
                print(f"[pipeline] {report_date} 已运行({cnt}条推荐), 跳过 (--force强制重新运行)")
                return {"status": "skipped", "reason": f"already_run: {cnt} picks"}
        except Exception:
            pass
    
    print(f"[pipeline] {report_date} 开始执行{' [回测模式]' if is_replay else ''}...")
    
    # 0. 就绪检查
    status = check_readiness()
    if not status["ready"]:
        print(f"[pipeline] ❌ 未就绪: {status['issues']}")
        return {"status": "not_ready", "issues": status["issues"]}
    
    for issue in status.get("issues", []):
        print(f"[pipeline] ⚠️ {issue}")
    
    # 市场体制判定 — BEAR市场跳过短线池
    regime, regime_factors = _get_market_regime(as_of_date=replay_date if is_replay else None)
    print(f"[pipeline] 市场体制: {regime}")
    
    SKIP_POOLS = []
    REDUCE_MAX = {}
    if status.get("block_short") and not is_replay:
        SKIP_POOLS.extend([('A', 'short'), ('HK', 'short')])
        print("[pipeline] ⛔ 日线滞后>1天: 跳过短线池写入")
    if regime == "BEAR":
        # BULL_TRAP: 熊市超跌反弹 → 允许1-2只short推荐
        if regime_factors.get('is_bull_trap') and not status.get("block_short"):
            REDUCE_MAX = {('A','short'): 2, ('HK','short'): 1, ('A','long'): 5, ('HK','long'): 3}
            print(f"[pipeline] ⚡ BULL_TRAP: RSI={regime_factors.get('rsi14')}, 开通短线A:2/HK:1")
        else:
            for key in [('A','short'), ('HK','short')]:
                if key not in SKIP_POOLS:
                    SKIP_POOLS.append(key)
            REDUCE_MAX = {('A','long'): 5, ('HK','long'): 3}
            print(f"[pipeline] ⚠️ BEAR市场: 跳过短线池, 长线缩减至A:{REDUCE_MAX[('A','long')]}只/HK:{REDUCE_MAX[('HK','long')]}只")
    
    all_picks = {}
    degraded_pools = []  # 追踪降级池
    
    def _pool_step(market, period, coarse_fn, default_max):
        key = (market, period)
        if key in SKIP_POOLS:
            print(f"[pipeline] {market}_{period}: ⛔ SKIP (regime={regime})")
            return
        max_p = REDUCE_MAX.get(key, default_max)
        pool_type = f"{market}_{period}"
        symbols = coarse_fn(top_n=800 if market=='A' else 300, as_of_date=replay_date if is_replay else None)
        # Stage 2: ML评分
        print(f"[pipeline] {pool_type}: coarse→{len(symbols)} symbols, ML scoring...")
        if not symbols:
            print(f"[pipeline]   {pool_type}: ⛔ 无候选标的, 跳过")
            return
        scored, degraded = stage2_ml_scoring(symbols, pool_type, top_n=min(len(symbols), 100))
        if degraded:
            degraded_pools.append(pool_type)
            print(f"[pipeline]   {pool_type}: ⚠️ ML降级 (纯coarse评分)")
        score_map = {s: sc for s, sc in scored}
        top_symbols = [s for s, _ in scored[:max_p*3]]  # 取3倍候选进precision
        picks = precision_filter(top_symbols, market, period, max_positions=max_p,
                                as_of_date=replay_date if is_replay else None)
        for p in picks:
            if p["symbol"] in score_map:
                p["merged_score"] = score_map[p["symbol"]]
        all_picks.setdefault(market, {})[period] = picks
        print(f"[pipeline]   {pool_type}: {len(picks)} picks")
    
    # Stage 1+2+3: 四池执行
    _pool_step('A', 'long', coarse_filter_a_long, 15)
    _pool_step('A', 'short', coarse_filter_a_short, 15)
    _pool_step('HK', 'long', coarse_filter_hk_long, 10)
    _pool_step('HK', 'short', coarse_filter_hk_short, 10)
    
    # 汇总所有picks
    all_flat = []
    for market, periods in all_picks.items():
        for period, picks in periods.items():
            for p in picks:
                p['market'] = market
                p['period'] = period
                all_flat.append(p)
    
    # 写入DB
    if not dry_run:
        write_recommendation_pool(all_flat, report_date)
    
    # 生成summary
    summary = generate_daily_summary(all_picks, report_date)
    
    elapsed = time.time() - t0
    total_picks = sum(len(picks) for periods in all_picks.values() for picks in periods.values())
    if not is_replay:
        status_str = "degraded" if degraded_pools else "ok"
        _write_heartbeat(status_str, elapsed, total_picks, degraded_pools)
    
    # 实盘IC追踪(回测模式跳过)
    if not is_replay:
        for pool in ['A_long', 'A_short', 'HK_long', 'HK_short']:
            ic = compute_live_ic(pool)
            if ic:
                print(f"[pipeline] {pool} live IC: {ic:.4f}")
    
    print(f"[pipeline] ✅ 完成: {total_picks} picks, {elapsed:.2f}s")
    
    
    # TRL轨: 主线推荐 (非回测模式)
    if not is_replay:
        try:
            # L2扫描: 申万二级行业三层共振
            import importlib.util
            spec = importlib.util.spec_from_file_location(
                "theme_detector_v4",
                str(Path.home() / ".hermes/scripts/theme_detector_v4.py")
            )
            td_v4 = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(td_v4)
            print("[pipeline] L2扫描开始...")
            td_v4.detect_l2(db_conn)
            print("[pipeline] L2扫描完成")
        except Exception as e:
            print(f"[pipeline] ⚠️ L2扫描异常: {e}", file=sys.stderr)
        
        try:
            from nous.engine.pipelines.trl_recommender import run_trl_track
            trl_picks = run_trl_track(report_date, dry_run=dry_run)
            if trl_picks:
                print(f"[pipeline] 🐉 TRL: {len(trl_picks)} picks")
        except Exception as e:
            print(f"[pipeline] ⚠️ TRL轨异常: {e}", file=sys.stderr)
    return {"status": "ok", "all_picks": all_picks, "summary": summary, "elapsed": elapsed}


# ══════════════════════════════════════════════
# CLI
# ══════════════════════════════════════════════

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="每日荐股统一Pipeline")
    parser.add_argument("--dry-run", action="store_true", help="不写入DB")
    parser.add_argument("--force", action="store_true", help="忽略幂等检查, 强制重新运行")
    parser.add_argument("--replay", type=str, default=None, help="回测模式: 指定日期YYYY-MM-DD")
    args = parser.parse_args()
    
    result = run_pipeline(dry_run=args.dry_run, force=args.force, replay_date=args.replay)
    if result["status"] == "ok":
        print(f"\nPipeline成功: {result['elapsed']:.1f}s")
    else:
        print(f"\nPipeline失败: {result.get('issues', [])}")
        sys.exit(1)
