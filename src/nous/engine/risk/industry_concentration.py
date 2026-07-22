#!/usr/bin/env python3
"""industry_concentration.py — L2/L3行业集中度风控检查
用法:
  python3 industry_concentration.py                 # 检查持仓
  python3 industry_concentration.py --symbol 000001 --weight 0.05  # 预检
"""
import sqlite3, sys, argparse, json
from pathlib import Path
from collections import defaultdict

DB = Path.home() / "code/stock-screener/data/screener.db"
PORTFOLIO = Path.home() / "wiki/finance/portfolio/state.yaml"
L2_MAX = 0.30   # 单L2行业 ≤30%
L3_MAX = 0.15   # 单L3行业 ≤15%
CONCEPT_MAX = 3 # 单股 ≤3个概念板块


def get_industry(symbol: str, level: str = 'L2') -> str:
    """查股票的三级行业归属"""
    db = sqlite3.connect(str(DB))
    col = f"industry_{level.lower()}"
    r = db.execute(f"SELECT {col} FROM stock_industry_multilevel WHERE symbol=? AND is_current=1 LIMIT 1",
                   (symbol,)).fetchone()
    db.close()
    return r[0] if r else None


def get_concepts(symbol: str) -> list[str]:
    db = sqlite3.connect(str(DB))
    rows = db.execute("SELECT concept_name FROM stock_concept_map WHERE symbol=?", (symbol,)).fetchall()
    db.close()
    return [r[0] for r in rows]


def check_portfolio():
    """检查当前持仓的行业集中度"""
    if not PORTFOLIO.exists():
        return {"error": "持仓文件不存在"}
    
    import yaml
    with open(PORTFOLIO) as f:
        pf = yaml.safe_load(f)
    
    positions = pf.get('positions', [])
    if not positions:
        return {"status": "ok", "message": "无持仓"}
    
    # 聚合L2集中度
    l2_exposure = defaultdict(float)
    l3_exposure = defaultdict(float)
    concept_count = defaultdict(int)
    violations = []
    
    for pos in positions:
        sym = str(pos.get('symbol', '')).zfill(6)
        weight = float(pos.get('weight_pct', 0)) / 100
        
        l2 = get_industry(sym, 'L2')
        l3 = get_industry(sym, 'L3')
        concepts = get_concepts(sym)
        
        if l2:
            l2_exposure[l2] += weight
        if l3:
            l3_exposure[l3] += weight
        for c in concepts:
            concept_count[sym] += 1
    
    # 检查违规
    for l2_name, exp in l2_exposure.items():
        if exp > L2_MAX:
            violations.append({
                "type": "L2集中度超标",
                "industry": l2_name,
                "exposure": round(exp * 100, 1),
                "limit": round(L2_MAX * 100, 1),
                "severity": "high" if exp > L2_MAX * 1.5 else "medium",
            })
    
    for l3_name, exp in l3_exposure.items():
        if exp > L3_MAX:
            violations.append({
                "type": "L3集中度超标",
                "industry": l3_name,
                "exposure": round(exp * 100, 1),
                "limit": round(L3_MAX * 100, 1),
                "severity": "medium",
            })
    
    for sym, cnt in concept_count.items():
        if cnt > CONCEPT_MAX:
            violations.append({
                "type": "概念板块重叠",
                "symbol": sym,
                "count": cnt,
                "limit": CONCEPT_MAX,
                "severity": "low",
            })
    
    return {
        "status": "violation" if violations else "ok",
        "violations": violations,
        "l2_exposure": {k: round(v*100, 1) for k, v in sorted(l2_exposure.items(), key=lambda x: -x[1])[:5]},
        "l3_exposure": {k: round(v*100, 1) for k, v in sorted(l3_exposure.items(), key=lambda x: -x[1])[:5]},
    }


def precheck(symbol: str, weight: float):
    """预检: 如果买入该股票是否超标"""
    l2 = get_industry(symbol, 'L2')
    l3 = get_industry(symbol, 'L3')
    concepts = get_concepts(symbol)
    
    result = {"symbol": symbol, "weight": weight, "L2": l2, "L3": l3,
              "concepts": concepts, "checks": []}
    
    # 获取当前L2暴露
    if l2:
        db = sqlite3.connect(str(DB))
        current_l2 = check_portfolio().get('l2_exposure', {}).get(l2, 0)
        new_l2 = current_l2 / 100 + weight
        result["checks"].append({
            "check": f"L2 {l2}",
            "current": round(current_l2, 1),
            "after_buy": round(new_l2 * 100, 1),
            "limit": round(L2_MAX * 100, 1),
            "pass": new_l2 <= L2_MAX,
        })
    
    if concepts:
        result["checks"].append({
            "check": f"概念重叠",
            "count": len(concepts),
            "limit": CONCEPT_MAX,
            "pass": len(concepts) <= CONCEPT_MAX,
        })
    
    db.close()
    return result


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--symbol", help="预检股票代码")
    p.add_argument("--weight", type=float, default=0.05, help="仓位权重")
    args = p.parse_args()
    
    if args.symbol:
        result = precheck(args.symbol.zfill(6), args.weight)
    else:
        result = check_portfolio()
    
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
