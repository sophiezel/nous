#!/usr/bin/env python3
"""
荐股报告解析器 v2 — 兼容v1(05-18)和v2(05-20+)两种格式

v2: TOP3详细个股(### 🥇/🥈/🥉) + #4-10汇总表 + 港股TOP5表
v1: 按周期分池(短线池/中线池/长线池/港股)
"""
from __future__ import annotations

import re
import json
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent.parent))
from nous.data.storage import get_db

REPORT_DIR = Path.home() / "wiki" / "finance" / "reports"

CYCLE_TAGS = {
    "🟢短线": "short", "短线": "short",
    "🟡中线": "mid", "中线": "mid",
    "🔵长线": "long", "长线": "long",
}

# ── V2: Individual picks (### 🥇/🥈/🥉) ──────────────

def _parse_v2_top3(text: str, market: str) -> list[dict]:
    """Parse TOP 3 individual pick sections like ### 🥇 长盛轴承 (300718) — 评分 9.0 | 🟢短线"""
    picks = []
    # Match: ### 🥇 Name (symbol) — 评分 X.X | cycle_tag
    pat = r'###\s*[🥇🥈🥉]\s*(.+?)\s*\((\d{5,6})\)\s*[—\-]\s*评分\s*([\d.]+)\s*\|\s*(.+)'
    for m in re.finditer(pat, text):
        name = m.group(1).strip()
        symbol = m.group(2)
        score = float(m.group(3))
        cycle_raw = m.group(4).strip()
        
        cycle = "short"
        for tag, cyc in CYCLE_TAGS.items():
            if tag in cycle_raw:
                cycle = cyc
                break
        
        # Extract PE/RSI from detail table below the heading
        section_start = m.end()
        section_end = text.find('###', section_start)
        if section_end == -1:
            section_end = text.find('---', section_start)
        if section_end == -1:
            section_end = len(text)
        section = text[section_start:section_end]
        
        pe = _extract_field(section, r'PE.*?([\d.]+)')
        rsi = _extract_field(section, r'RSI\(14\)\s*[|]*\s*([\d.]+)')
        vr = _extract_field(section, r'量比.*?([\d.]+)x')
        
        picks.append({
            'market': market, 'cycle': cycle,
            'symbol': symbol, 'name': name,
            'score': score, 'pe': pe, 'rsi': rsi, 'volume_ratio': vr,
        })
    return picks

def _extract_field(text: str, pattern: str) -> float | None:
    """Extract a numeric field from detail text."""
    m = re.search(pattern, text)
    if m:
        try:
            return float(m.group(1))
        except ValueError:
            pass
    return None

# ── V2: Summary table (#4-10 A股 / #1-5 港股) ────────

def _parse_v2_summary_table(text: str, market: str) -> list[dict]:
    """Parse the summary pipe table for picks #4-10 (A) or #1-5 (HK)."""
    picks = []
    
    if market == 'A':
        # A股 summary: after the TOP3 individual sections, find #4-10 table
        # Look for the last pipe table before 港股 section
        pass  # Handled below
    
    # Find all pipe tables in the text
    lines = text.split('\n')
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        # Detect table header
        if '|' in line and ('代码' in line or '名称' in line):
            # Collect table rows until non-pipe line
            rows = []
            j = i + 1
            while j < len(lines):
                row = lines[j].strip()
                if not row.startswith('|'):
                    break
                if '---' not in row and row.count('|') >= 3:
                    rows.append(row)
                j += 1
            
            # Parse rows
            header = line
            for row in rows:
                pick = _parse_v2_row(row, header, market)
                if pick:
                    picks.append(pick)
            
            i = j
            continue
        i += 1
    
    return picks

def _parse_v2_row(row: str, header: str, market: str) -> dict | None:
    """Parse a single pipe-delimited row from v2 format."""
    cells = [c.strip() for c in row.split('|')[1:-1]]
    if len(cells) < 4:
        return None
    
    # Skip first column if it's a row number (#)
    if cells and re.match(r'^\d{1,2}$', cells[0]):
        cells = cells[1:]  # drop # column
    
    symbol = ""
    name = ""
    score = 0.0
    cycle = "short"
    pe = None
    rsi = None
    volume_ratio = None
    
    for c in cells:
        c = c.strip()
        # Symbol: 5-6 digits
        if re.match(r'^\d{5,6}$', c):
            symbol = c
            continue
        # Name: Chinese chars, possibly with trailing '*'
        name_match = re.match(r'^([\u4e00-\u9fff·]{2,8})\*?$', c)
        if name_match and not name:
            name = name_match.group(1)
            continue
        # Volume ratio (x suffix) — process BEFORE numeric check
        vr_match = re.match(r'([\d.]+)x', c.replace('🔥','').replace('⚠️',''))
        if vr_match:
            volume_ratio = float(vr_match.group(1))
            continue
        # Score: first float 1-10
        try:
            v = float(c)
            if 0.5 <= v <= 10 and not score:
                score = v
                continue
        except ValueError:
            pass
        # RSI (strip annotations)
        rsi_clean = re.sub(r'[⚠️🔥超卖超买]', '', c).strip()
        try:
            v = float(rsi_clean)
            if 20 < v < 100 and not rsi:
                rsi = v
                continue
        except ValueError:
            pass
        # Cycle tag
        for tag, cyc in CYCLE_TAGS.items():
            if tag in c:
                cycle = cyc
                break
        # PE (A股 table: after name, before ROE)
        if not pe:
            pe_clean = re.sub(r'[⚠️~]', '', c)
            try:
                v = float(pe_clean)
                if 1 < v < 1000:
                    pe = v
            except ValueError:
                pass
    
    if symbol and len(symbol) in (5, 6):
        return {
            'market': market, 'cycle': cycle,
            'symbol': symbol, 'name': name or '',
            'score': score, 'pe': pe, 'rsi': rsi, 'volume_ratio': volume_ratio,
        }
    return None

# ── V1 Parser ─────────────────────────────────────────

def _parse_v1_section(text: str, section_name: str, market: str, cycle: str) -> list[dict]:
    """Parse a named section from v1 format."""
    pat = rf'{section_name}'
    m = re.search(pat, text)
    if not m:
        return []
    
    start = m.end()
    rest = text[start:]
    m_next = re.search(r'\n(?:###|##|🇭🇰|---\n##)', rest)
    end = m_next.start() if m_next else len(rest)
    block = rest[:end]
    
    picks = []
    lines = block.split('\n')
    in_table = False
    
    for line in lines:
        line = line.strip()
        if not line.startswith('|'):
            continue
        if '---' in line:
            in_table = True
            continue
        if not in_table:
            if '代码' in line:
                in_table = True
            continue
        
        cells = [c.strip() for c in line.split('|')[1:-1]]
        if len(cells) < 4:
            continue
        
        symbol = ""
        name = ""
        score = 0.0
        
        for c in cells:
            c = c.strip()
            if re.match(r'^\d{5,6}$', c):
                symbol = c
                break
        
        if not symbol:
            # Try second column
            for c in cells:
                if re.match(r'^\d{5,6}$', c):
                    symbol = c
                    break
        
        if not symbol:
            continue
        
        # Name: first Chinese text cell
        for c in cells:
            if re.match(r'^[\u4e00-\u9fff·]{2,8}$', c):
                name = c
                break
        
        # Score: first float 1-10
        for c in cells:
            try:
                v = float(c)
                if 0.5 <= v <= 10:
                    score = v
                    break
            except ValueError:
                pass
        
        picks.append({
            'market': market, 'cycle': cycle,
            'symbol': symbol, 'name': name or '',
            'score': score, 'pe': None, 'rsi': None, 'volume_ratio': None,
        })
    
    return picks


# ── Public API ─────────────────────────────────────────

def parse_report(report_path: str = None, md_text: str = None) -> list[dict]:
    """Parse recommendation report → picks list."""
    if md_text is None:
        md_text = Path(report_path).read_text()
    
    # Detect format
    if 'A股 TOP' in md_text or '港股 TOP' in md_text:
        # v2 format
        a_block = _extract_to_boundary(md_text, 'A股 TOP', '港股 TOP')
        hk_block = _extract_to_boundary(md_text, '港股 TOP', r'\n##\s+六')
        
        picks = []
        # TOP3 individual picks (A股 only)
        if a_block:
            picks += _parse_v2_top3(a_block, 'A')
        # A股 summary table (from A股 block only)
        if a_block:
            picks += _parse_v2_summary_table(a_block, 'A')
        # 港股 table (from HK block only)
        if hk_block:
            picks += _parse_v2_summary_table(hk_block, 'HK')
    else:
        # v1 format
        picks = []
        picks += _parse_v1_section(md_text, '短线池', 'A', 'short')
        picks += _parse_v1_section(md_text, '中线池', 'A', 'mid')
        picks += _parse_v1_section(md_text, '长线池', 'A', 'long')
        picks += _parse_v1_section(md_text, '港股', 'HK', 'short')
    
    # Dedup by (symbol, market)
    seen = set()
    unique = []
    for p in picks:
        key = (p['symbol'], p['market'])
        if key not in seen:
            seen.add(key)
            unique.append(p)
    
    return unique

def _extract_to_boundary(text: str, start_pat: str, end_pat: str) -> str:
    """Extract text between two patterns."""
    m = re.search(start_pat, text)
    if not m:
        return ""
    start = m.start()
    rest = text[start:]
    m_end = re.search(end_pat, rest)
    end = m_end.start() if m_end else len(rest)
    return rest[:end]

def detect_format(md_text: str) -> str:
    if 'A股 TOP' in md_text:
        return 'v2'
    if '短线池' in md_text:
        return 'v1'
    return 'v1'

def _get_macro_snapshot(conn, rec_date: str) -> str:
    """获取当日市场宏观快照"""
    parts = []
    # 大盘涨跌 (IDX_000001 = 上证指数)
    idx = conn.execute(
        "SELECT close FROM index_daily WHERE symbol='IDX_000001' AND trade_date=?",
        (rec_date,)
    ).fetchone()
    if idx:
        # 取前一交易日算涨跌幅
        prev = conn.execute(
            "SELECT close FROM index_daily WHERE symbol='IDX_000001' AND trade_date<? ORDER BY trade_date DESC LIMIT 1",
            (rec_date,)
        ).fetchone()
        if prev and prev[0] and idx[0]:
            chg = (idx[0] - prev[0]) / prev[0] * 100
            direction = "涨" if chg > 0 else ("跌" if chg < 0 else "平")
            parts.append(f"沪指{direction}{abs(chg):.1f}%")
    
    # 北向资金 (net_buy, 单位:亿元)
    nb = conn.execute(
        "SELECT net_buy FROM hsgt_daily WHERE trade_date=?",
        (rec_date,)
    ).fetchone()
    if nb and nb[0]:
        nf = nb[0]
        in_out = "流入" if nf > 0 else "流出"
        parts.append(f"北向{in_out}{abs(nf):.0f}亿")
    
    return " | ".join(parts) if parts else f"市场数据({rec_date})"


def store_recommendation_pool(picks: list[dict], rec_date: str = None):
    if rec_date is None:
        rec_date = date.today().isoformat()
    
    conn = get_db(write=True)
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS recommendation_pool (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                rec_date TEXT NOT NULL, symbol TEXT NOT NULL, name TEXT,
                market TEXT, cycle TEXT, score REAL, pe REAL,
                rsi REAL, volume_ratio REAL,
                ma_golden INTEGER DEFAULT 0, macd_golden INTEGER DEFAULT 0,
                position_suggested REAL, expected_return REAL,
                UNIQUE(rec_date, symbol)
            )
        """)
        # 构建 macro_snapshot (当日市场环境)
        macro = _get_macro_snapshot(conn, rec_date)
        
        for p in picks:
            # 构建买入理由
            parts = []
            score = p.get('score', 0) or 0
            rsi = p.get('rsi')
            pe = p.get('pe')
            vr = p.get('volume_ratio')
            
            if score >= 8:
                parts.append(f"综合评分优秀({score:.1f})")
            elif score >= 6:
                parts.append(f"综合评分良好({score:.1f})")
            else:
                parts.append(f"综合评分{score:.1f}")
            
            # RSI 解读
            if rsi and rsi < 30:
                parts.append(f"RSI超卖({rsi:.0f})")
            elif rsi and rsi > 70:
                parts.append(f"RSI强势({rsi:.0f})")
            
            # PE 估值
            if pe and 0 < pe < 20:
                parts.append(f"低估值(PE{pe:.1f})")
            elif pe and pe > 100:
                parts.append(f"高成长(PE{pe:.1f})")
            
            # 量比
            if vr and vr > 1.5:
                parts.append(f"放量({vr:.1f}x)")
            
            buy_reason = " + ".join(parts) if parts else f"因子综合评分{score:.1f}"
            
            # 因子分解
            factor_scores = {
                "total": round(score, 1),
                "momentum": round(rsi / 10, 1) if rsi else None,
                "value": round(min(10, 100 / pe), 1) if pe and pe > 0 else None,
                "volume": round(min(10, vr * 5), 1) if vr else None,
            }
            factor_json = json.dumps({k: v for k, v in factor_scores.items() if v is not None}, ensure_ascii=False)
            
            
            conn.execute("""
                INSERT OR REPLACE INTO recommendation_pool 
                (rec_date, symbol, name, market, cycle, score, pe, rsi, volume_ratio,
                 buy_reason, factor_scores, macro_snapshot)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
            """, (rec_date, p['symbol'], p['name'], p['market'], p['cycle'],
                  p['score'], p.get('pe'), p.get('rsi'), p.get('volume_ratio'),
                  buy_reason, factor_json, macro))
        conn.commit()
        return len(picks)
    finally:
        conn.close()

# ── CLI ────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('report_path', nargs='?',
                        default=str(REPORT_DIR / f"{date.today().isoformat()}.md"))
    parser.add_argument('--store', action='store_true')
    parser.add_argument('--date')
    args = parser.parse_args()
    
    picks = parse_report(args.report_path)
    print(f"Format: {detect_format(Path(args.report_path).read_text())}")
    print(f"Total picks: {len(picks)}")
    
    a_short = [p for p in picks if p['market']=='A' and p['cycle']=='short']
    a_mid = [p for p in picks if p['market']=='A' and p['cycle']=='mid']
    a_long = [p for p in picks if p['market']=='A' and p['cycle']=='long']
    hk = [p for p in picks if p['market']=='HK']
    print(f"  A股短线: {len(a_short)}  中线: {len(a_mid)}  长线: {len(a_long)}  港股: {len(hk)}")
    for p in picks:
        print(f"  {p['market']}/{p['cycle']}: {p['symbol']} {p['name']} score={p['score']} pe={p.get('pe')} rsi={p.get('rsi')}")
    
    if args.store:
        n = store_recommendation_pool(picks, args.date)
        print(f"\nStored {n} picks to recommendation_pool")
