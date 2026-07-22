#!/usr/bin/env python3
"""TRL推荐桥接 v2 — theme_scores/theme_auto_pools → recommendation_pool(engine='TRL')

v2修复:
  P0-1: 存储theme到expected_return字段, HTML按theme分组
  P0-2: 评分增强(真实PE从F3推荐池获取 + ret_5d基于实际交易日 + 量价区分)
"""
import json
import sys
import sqlite3
from pathlib import Path
from datetime import date

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
sys.path.insert(0, str(Path.home() / ".hermes/scripts"))

from nous.core.db import _resolve_path

DB_PATH = Path(_resolve_path("screener.db"))


def get_industry_stocks(db_conn, industry_name: str) -> list:
    """从stock_industry获取行业内所有个股(处理bj/sh/sz前缀)"""
    rows = db_conn.execute("""
        SELECT si.symbol, COALESCE(sb.name, si.symbol)
        FROM stock_industry si
        LEFT JOIN stock_basic sb ON 
            sb.symbol = si.symbol 
            OR sb.symbol = REPLACE(REPLACE(REPLACE(si.symbol, 'bj', ''), 'sh', ''), 'sz', '')
        WHERE si.industry_name = ?
    """, (industry_name,)).fetchall()
    return [(r[0], r[1] or r[0]) for r in rows]


def get_industry_stocks_l2(db_conn, l2_name: str) -> list:
    """从stock_industry_multilevel获取L2行业内所有个股"""
    rows = db_conn.execute("""
        SELECT DISTINCT sim.symbol, COALESCE(sb.name, sim.symbol)
        FROM stock_industry_multilevel sim
        LEFT JOIN stock_basic sb ON sb.symbol = sim.symbol
        WHERE sim.is_current = 1 AND sim.industry_l2 = ?
    """, (l2_name,)).fetchall()
    return [(r[0], r[1] or r[0]) for r in rows]


def get_hk_sector_stocks(db_conn, sector_name: str) -> list:
    keyword_map = {
        "港股-医药": ["药","医","生物","医疗"],
        "港股-科技": ["科技","软件","云","芯片","半导体","智能","数字","信息","通信",
                      "腾讯","阿里","百度","京东","网易","快手","哔哩","美团",
                      "小米","联想","舜宇","创科","电子"],
        "港股-汽车": ["汽车","车","理想","蔚来","小鹏","比亚迪","吉利"],
        "港股-地产": ["地产","房地产","物业","新鸿基","长实","恒基","新世界"],
        "港股-消费": ["消费","零售","食品","饮料","餐饮","啤酒","乳业",
                      "安踏","李宁","蒙牛","农夫山泉","海底捞","百胜",
                      "周大福","万洲","啤酒"],
        "港股-金融": ["银行","保险","金融","证券","基金","汇丰","恒生",
                      "招商银行","工商银行","建设银行","中国银行","农业银行",
                      "友邦","港交所"],
    }
    keywords = keyword_map.get(sector_name, [sector_name.replace("港股-","")])
    rows = db_conn.execute("SELECT symbol, name FROM stock_basic WHERE market = 'hk'").fetchall()
    result = []
    for symbol, name in rows:
        for kw in keywords:
            if kw in (name or ""):
                result.append((symbol, name or symbol))
                break
    return result


def score_stocks_v2(db_conn, symbols: list[tuple]) -> list[dict]:
    """v2打分: PE(from F3推荐池) + ret_5d(实际交易日) + 量比 + 市值"""
    if not symbols:
        return []
    
    # 去前缀
    sym_map = {}
    for raw_sym, name in symbols:
        clean = raw_sym.replace('bj','').replace('sh','').replace('sz','')
        sym_map[clean] = (raw_sym, name)
    
    sym_list = list(sym_map.keys())
    name_map = {s: sym_map[s][1] for s in sym_list}
    
    placeholders = ','.join('?' * len(sym_list))
    
    # 1. 最新日线(含量/价)
    rows_price = db_conn.execute(f"""
        WITH latest AS (
            SELECT symbol, MAX(trade_date) as max_date
            FROM stock_daily WHERE symbol IN ({placeholders})
            GROUP BY symbol
        )
        SELECT sd.symbol, sd.close, sd.volume
        FROM stock_daily sd
        JOIN latest l ON sd.symbol = l.symbol AND sd.trade_date = l.max_date
    """, sym_list).fetchall()
    
    price_map = {r[0]: (r[1] or 0, r[2] or 0) for r in rows_price}
    
    # 2. PE: 1)stock_fundamental > 2)F3推荐池 > 3)默认50
    pe_map = {}
    # Priority 1: stock_fundamental
    pe_rows = db_conn.execute(f"""
        SELECT symbol, pe FROM stock_fundamental
        WHERE symbol IN ({placeholders}) AND pe > 0 AND pe < 500
    """, sym_list).fetchall()
    for pr in pe_rows:
        pe_map[pr[0]] = pr[1]
    
    # Priority 2: F3推荐池(补充stock_fundamental未覆盖的)
    still_missing = [s for s in sym_list if s not in pe_map]
    if still_missing:
        ph2 = ','.join('?' * len(still_missing))
        pe_rows2 = db_conn.execute(f"""
            SELECT symbol, MAX(pe) FROM recommendation_pool
            WHERE symbol IN ({ph2}) AND engine='F3'
            AND rec_date >= date('now','-10 days') AND pe > 0 AND pe < 500
            GROUP BY symbol
        """, still_missing).fetchall()
        for pr in pe_rows2:
            if pr[0] not in pe_map:
                pe_map[pr[0]] = pr[1]
    
    # 3. 5日收益(用实际交易日)
    ret_map = {}
    for sym in sym_list:
        # 取最新日期前5个交易日的价格
        r = db_conn.execute("""
            SELECT close FROM (
                SELECT close, trade_date FROM stock_daily 
                WHERE symbol=? ORDER BY trade_date DESC LIMIT 6
            ) ORDER BY trade_date ASC LIMIT 1
        """, (sym,)).fetchone()
        close_now = price_map.get(sym, (0,))[0]
        if r and r[0] > 0 and close_now > 0:
            ret_map[sym] = (close_now - r[0]) / r[0] * 100
    
    # 4. 量比: 最近日量 / 20日均量
    vol_ratio_map = {}
    for sym in sym_list:
        r = db_conn.execute("""
            SELECT AVG(volume) FROM stock_daily 
            WHERE symbol=? AND trade_date >= date(
                (SELECT MAX(trade_date) FROM stock_daily WHERE symbol=?), '-20 days'
            )
        """, (sym, sym)).fetchone()
        avg_vol = r[0] or 1
        cur_vol = price_map.get(sym, (0, 1))[1]
        if avg_vol > 0:
            vol_ratio_map[sym] = cur_vol / avg_vol
    
    # 5. 评分计算
    scored = []
    for sym in sym_list:
        close, vol = price_map.get(sym, (0, 0))
        if close <= 0:
            continue
        
        pe = pe_map.get(sym, 50)
        ret_5d = ret_map.get(sym, 0)
        vol_ratio = vol_ratio_map.get(sym, 1.0)
        
        # 估值分(0-10): PE越低越好
        if 0 < pe < 20:   pe_score = 10
        elif pe < 40:     pe_score = 8
        elif pe < 60:     pe_score = 6
        elif pe < 100:    pe_score = 4
        else:             pe_score = 2
        
        # 动量分(0-8): 5日收益
        if ret_5d > 5:       ret_score = 8
        elif ret_5d > 2:     ret_score = 6
        elif ret_5d > 0:     ret_score = 5
        elif ret_5d > -3:    ret_score = 3
        else:                ret_score = 1
        
        # 量比分(0-5): 放量>1.2好, <0.8差
        if vol_ratio > 1.5:      vol_score = 5
        elif vol_ratio > 1.2:    vol_score = 4
        elif vol_ratio > 0.8:    vol_score = 3
        else:                    vol_score = 1
        
        score = round(pe_score + ret_score + vol_score, 1)
        
        scored.append({
            'symbol': sym,
            'name': name_map.get(sym, sym),
            'score': score,
            'pe': pe,
            'ret_5d': round(ret_5d, 1),
            'vol_ratio': round(vol_ratio, 2),
            'market': 'A' if len(sym) == 6 and sym.isdigit() else 'HK',
        })
    
    scored.sort(key=lambda x: -x['score'])
    return scored


def _get_macro_snapshot_trl(conn, rec_date: str) -> str:
    """获取当日市场宏观快照 (TRL版本)"""
    parts = []
    idx = conn.execute(
        "SELECT close FROM index_daily WHERE symbol='IDX_000001' AND trade_date=?",
        (rec_date,)
    ).fetchone()
    if idx:
        prev = conn.execute(
            "SELECT close FROM index_daily WHERE symbol='IDX_000001' AND trade_date<? ORDER BY trade_date DESC LIMIT 1",
            (rec_date,)
        ).fetchone()
        if prev and prev[0] and idx[0]:
            chg = (idx[0] - prev[0]) / prev[0] * 100
            direction = "涨" if chg > 0 else ("跌" if chg < 0 else "平")
            parts.append(f"沪指{direction}{abs(chg):.1f}%")
    nb = conn.execute("SELECT net_buy FROM hsgt_daily WHERE trade_date=?", (rec_date,)).fetchone()
    if nb and nb[0]:
        nf = nb[0]
        in_out = "流入" if nf > 0 else "流出"
        parts.append(f"北向{in_out}{abs(nf):.0f}亿")
    return " | ".join(parts) if parts else f"市场数据({rec_date})"


def run_trl_track(report_date: str = None, dry_run: bool = False) -> list:
    report_date = report_date or date.today().isoformat()
    
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("PRAGMA busy_timeout=5000")
    
    # 1. 读当日主题(去重) — L2优先, 无数据则回退L1
    rows = conn.execute("""
        SELECT theme_name, category, MAX(total_score), MAX(layer_price), MAX(layer_flow), 
               MAX(layer_event), COUNT(*), MAX(stock_count)
        FROM theme_auto_pools
        WHERE scan_date = ? AND category != 'skip' AND level = 'L2'
        GROUP BY theme_name
        ORDER BY MAX(total_score) DESC
        LIMIT 8
    """, (report_date,)).fetchall()
    
    level_used = 'L2'
    if not rows:
        print("[TRL] L2无数据, 回退L1")
        rows = conn.execute("""
            SELECT theme_name, category, MAX(total_score), MAX(layer_price), MAX(layer_flow), 
                   MAX(layer_event), COUNT(*), MAX(stock_count)
            FROM theme_auto_pools
            WHERE scan_date = ? AND category != 'skip'
            GROUP BY theme_name
            ORDER BY MAX(total_score) DESC
        """, (report_date,)).fetchall()
        level_used = 'L1'
    
    if not rows:
        print("[TRL] ⚠️ theme_auto_pools无今日数据, 跳过")
        conn.close()
        return []
    
    print(f"[TRL] 读取到 {len(rows)} 个主题")
    
    # 2. 筛选候选
    candidates = []
    for r in rows:
        theme_name, category, total_score, l1, l2, l3, row_cnt, stock_cnt = r
        if category == 'skip':
            continue
        candidates.append({
            'theme': theme_name,
            'category': category,
            'total_score': total_score,
            'stock_count': stock_cnt or 0,
            'type': 'hk' if theme_name.startswith('港股-') else 'a',
        })
    
    candidates = candidates[:5]
    print(f"[TRL] {len(candidates)} 个候选主题: {[c['theme'] for c in candidates]}")
    
    if not candidates:
        conn.close()
        return []
    
    # 3. 每主题选TOP2龙头 + TOP1权重
    all_picks = []
    
    for c in candidates:
        if c['type'] == 'hk':
            stocks = get_hk_sector_stocks(conn, c['theme'])
        elif level_used == 'L2':
            stocks = get_industry_stocks_l2(conn, c['theme'])
        else:
            stocks = get_industry_stocks(conn, c['theme'])
        
        if not stocks:
            print(f"  {c['theme']}: 无个股, 跳过")
            continue
        
        scored = score_stocks_v2(conn, stocks)
        top3 = scored[:3]
        
        if not top3:
            print(f"  {c['theme']}: 无有效个股, 跳过")
            continue
        
        names = [(s['symbol'], s['name'], round(s['score'],1)) for s in top3]
        print(f"  {c['theme']} ({c['category']}, {c['total_score']:.0f}分 {c['stock_count']}只): "
              f"TOP3: {names}")
        
        for rank, s in enumerate(top3):
            tier = "🥇龙头" if rank < 2 else "🥈权重"
            cycle = "long" if c['category'] == 'confirmed' else "short"
            all_picks.append({
                'symbol': s['symbol'],
                'name': s['name'],
                'score': s['score'],
                'pe': s['pe'],
                'rsi': 50,
                'volume_ratio': s['vol_ratio'],
                'ret_5d': s['ret_5d'],
                'position_suggested': 5.0 if rank == 0 else 3.0,
                'cycle': cycle,
                'market': s['market'],
                'theme': c['theme'],
                'theme_category': c['category'],
                'theme_score': c['total_score'],
                'industry_l2': c['theme'] if level_used == 'L2' else None,
                'tier': tier,
            })
    
    # 4. 写入recommendation_pool (theme存expected_return字段, 加决策依据)
    if not dry_run and all_picks:
        conn.execute("DELETE FROM recommendation_pool WHERE rec_date=? AND engine='TRL'",
                     (report_date,))
        
        # 获取宏观快照
        macro = _get_macro_snapshot_trl(conn, report_date)
        
        for p in all_picks:
            # 构建买入理由 (TRL三层共振风格)
            s = p
            buy_parts = []
            if s.get('ret_5d', 0) > 5:
                buy_parts.append(f"5日动量{s['ret_5d']:+.1f}%")
            if s.get('pe', 50) < 30:
                buy_parts.append(f"低估值PE{s['pe']:.0f}")
            if s.get('vol_ratio', 1) > 1.5:
                buy_parts.append(f"放量{s['vol_ratio']:.1f}x")
            buy_reason = " + ".join(buy_parts) if buy_parts else f"主题共振({s.get('theme','')}, 评分{s['score']:.1f})"
            
            # TRL专属详情
            trl_detail = json.dumps({
                "tier": s.get('tier', ''),
                "theme": s.get('theme', ''),
                "theme_category": s.get('theme_category', ''),
                "theme_score": s.get('theme_score', 0),
                "stock_score": s['score'],
                "pe": s.get('pe', 50),
                "ret_5d": s.get('ret_5d', 0),
                "vol_ratio": s.get('vol_ratio', 1.0),
            }, ensure_ascii=False)
            
            conn.execute("""
                INSERT OR REPLACE INTO recommendation_pool
                (rec_date, symbol, name, market, cycle, score, pe, rsi, 
                 volume_ratio, position_suggested, engine, expected_return,
                 buy_reason, macro_snapshot, trl_detail, industry_l2)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'TRL', ?, ?, ?, ?, ?)
            """, (report_date, p['symbol'], p['name'], p['market'].upper(),
                  p['cycle'], p['score'], p['pe'], p['rsi'], p['volume_ratio'],
                  p['position_suggested'], p['theme'], buy_reason, macro, trl_detail,
                  p.get('industry_l2')))
        
        conn.commit()
        print(f"[TRL] ✅ 写入 {len(all_picks)} 条TRL推荐(带决策依据)")
    
    conn.close()
    return all_picks


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser(description="TRL推荐桥接 v2")
    p.add_argument('--date', default=None, help='日期')
    p.add_argument('--dry-run', action='store_true')
    args = p.parse_args()
    
    picks = run_trl_track(args.date, dry_run=args.dry_run)
    
    if picks:
        print(f"\n总计 {len(picks)} 条TRL推荐")
        by_theme = {}
        for p in picks:
            by_theme.setdefault(p['theme'], []).append(p)
        for theme, ps in by_theme.items():
            names = [(x['symbol'], x['name'], round(x['score'],1)) for x in ps]
            print(f"  {theme}: {names}")
    else:
        print("\n无TRL推荐")
