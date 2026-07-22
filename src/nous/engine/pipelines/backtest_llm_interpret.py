#!/usr/bin/env python3
"""回测LLM解读层 — 消费回测结果, 追加四章叙事解读

用法:
  python backtest_llm_interpret.py --f3 f3_data.json --trl trl_data.json --output section.md
  或由 backtest_cycle.py 内部 import 调用 interpret_dual_results()
"""

import json, os, sys
from pathlib import Path
from datetime import date


def _build_interpret_prompt(f3_data: dict, trl_data: dict) -> str:
    """构造LLM解读Prompt, 只传关键统计数据不传原始日线"""
    
    f3_trades = f3_data.get("trades", [])
    f3_valid = [t for t in f3_trades if "error" not in t]
    f3_wins = sum(1 for t in f3_valid if t.get("pnl_pct", 0) > 0)
    f3_avg = sum(t["pnl_pct"] for t in f3_valid) / len(f3_valid) if f3_valid else 0
    
    trl_trades = trl_data.get("trades", [])
    trl_valid = [t for t in trl_trades if "error" not in t]
    trl_wins = sum(1 for t in trl_valid if t.get("pnl_pct", 0) > 0)
    trl_avg = sum(t["pnl_pct"] for t in trl_valid) / len(trl_valid) if trl_valid else 0
    
    # 极端案例
    trl_best = max(trl_valid, key=lambda t: t.get("pnl_pct", -999)) if trl_valid else None
    trl_worst = min(trl_valid, key=lambda t: t.get("pnl_pct", 999)) if trl_valid else None
    f3_best = max(f3_valid, key=lambda t: t.get("pnl_pct", -999)) if f3_valid else None
    f3_worst = min(f3_valid, key=lambda t: t.get("pnl_pct", 999)) if f3_valid else None
    
    interval = trl_data.get("interval", "?")
    confirmed = trl_data.get("days_with_confirmed", 0)
    total_days = trl_data.get("total_days", 0)
    themes = set()
    for t in trl_valid:
        if t.get("theme"):
            themes.add(t["theme"])
    
    prompt = f"""你是A股量化回测分析师。以下是双引擎回测的关键统计数据，请生成四个解读章节。

## 回测参数
- 区间: {interval}
- TRL主线确认天数: {confirmed}/{total_days}
- 活跃行业: {', '.join(sorted(themes)) if themes else '无'}

## F3海鹰引擎
- 有效交易: {len(f3_valid)}笔
- 胜率: {f3_wins}/{len(f3_valid)} ({f3_wins/len(f3_valid)*100:.0f}%) 如果f3_valid else "N/A"
- 平均盈亏: {f3_avg:+.1f}%
- 最佳: {f3_best['symbol']} {f3_best.get('name','')} {f3_best['pnl_pct']:+.1f}% (入场{f3_best['entry_date']}) 如果f3_best else "无"
- 最差: {f3_worst['symbol']} {f3_worst.get('name','')} {f3_worst['pnl_pct']:+.1f}% (入场{f3_worst['entry_date']}) 如果f3_worst else "无"

## TRL龙脉引擎
- 有效交易: {len(trl_valid)}笔
- 胜率: {trl_wins}/{len(trl_valid)} ({trl_wins/len(trl_valid)*100:.0f}%) 如果trl_valid else "N/A"
- 平均盈亏: {trl_avg:+.1f}%
- 最佳: {trl_best['symbol']} {trl_best.get('name','')} {trl_best['pnl_pct']:+.1f}% ({trl_best.get('theme','')}/{trl_best.get('tier','')}) 如果trl_best else "无"
- 最差: {trl_worst['symbol']} {trl_worst.get('name','')} {trl_worst['pnl_pct']:+.1f}% ({trl_worst.get('theme','')}/{trl_worst.get('tier','')}) 如果trl_worst else "无"

请用中文输出四个章节(每章3-5句话, 不要编造数据):

### 双引擎对比叙事
[对比两个引擎在此区间的表现, 解释差异的根因。市况是震荡/趋势/下跌? 哪个引擎更适合当前市况?]

### 最值得反思的3笔交易
[挑选最有教学意义的3笔: 高胜率却亏损的/低胜率却大赚的/止损后反弹的。解释为什么反直觉]

### 异常检测
[检测异常模式: 某天胜率突变? 某行业集中亏损? 主线消失后TRL表现?]

### 明日三景
[如果今天实盘跑这个策略, 列出3种可能情景及概率]
| 情景 | 概率 | 应对 |

只输出Markdown文本, 不要额外解释。"""
    
    return prompt


def interpret_dual_results(f3_data: dict, trl_data: dict) -> str:
    """调用LLM生成回测解读, 返回MD字符串。
    
    如果能访问DeepSeek API则调用, 否则返回备用模板。
    """
    prompt = _build_interpret_prompt(f3_data, trl_data)
    
    # 尝试直接调用DeepSeek (通过Hermes agent的LLM)
    # 如果当前环境没有API key, 返回模板解读
    api_key = os.environ.get("DEEPSEEK_API_KEY", "")
    
    if api_key:
        try:
            import requests
            resp = requests.post(
                "https://api.deepseek.com/v1/chat/completions",
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json={
                    "model": "deepseek-chat",
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0.7,
                    "max_tokens": 1500,
                },
                timeout=30
            )
            if resp.status_code == 200:
                return resp.json()["choices"][0]["message"]["content"]
        except Exception as e:
            print(f"[LLM] DeepSeek API调用失败: {e}")
    
    # 兜底: 返回模板解读
    f3_valid = [t for t in f3_data.get("trades", []) if "error" not in t]
    trl_valid = [t for t in trl_data.get("trades", []) if "error" not in t]
    
    f3_wins = sum(1 for t in f3_valid if t.get("pnl_pct", 0) > 0)
    trl_wins = sum(1 for t in trl_valid if t.get("pnl_pct", 0) > 0)
    
    f3_avg = sum(t["pnl_pct"] for t in f3_valid) / len(f3_valid) if f3_valid else 0
    trl_avg = sum(t["pnl_pct"] for t in trl_valid) / len(trl_valid) if trl_valid else 0
    
    return f"""### 双引擎对比叙事
> ⚠️ LLM API不可达, 以下为基础统计解读

此区间F3海鹰胜率{f3_wins}/{len(f3_valid)}({f3_wins/len(f3_valid)*100:.0f}%)，均盈亏{f3_avg:+.1f}%；TRL龙脉胜率{trl_wins}/{len(trl_valid)}({trl_wins/len(trl_valid)*100:.0f}%)，均盈亏{trl_avg:+.1f}%。{'TRL显著优于F3' if trl_avg > f3_avg + 2 else 'F3显著优于TRL' if f3_avg > trl_avg + 2 else '两者表现接近'}。

配置DEEPSEEK_API_KEY环境变量可启用完整LLM解读。

### 最值得反思的3笔交易
*LLM API不可达, 请检查DEEPSEEK_API_KEY配置*

### 异常检测
*LLM API不可达, 请检查DEEPSEEK_API_KEY配置*

### 明日三景
*LLM API不可达, 请检查DEEPSEEK_API_KEY配置*"""


# ── CLI ──

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="回测LLM解读生成")
    parser.add_argument("--f3", type=str, required=True, help="F3回测JSON文件路径")
    parser.add_argument("--trl", type=str, required=True, help="TRL回测JSON文件路径")
    parser.add_argument("--output", type=str, help="输出MD文件路径")
    args = parser.parse_args()
    
    with open(args.f3) as f:
        f3_data = json.load(f)
    with open(args.trl) as f:
        trl_data = json.load(f)
    
    # adapt format: f3_data might be nested
    if "trades" not in f3_data:
        f3_data = {"trades": f3_data.get("tracker", [])}
    
    text = interpret_dual_results(f3_data, trl_data)
    
    if args.output:
        Path(args.output).write_text(text)
        print(f"LLM解读已保存: {args.output}")
    else:
        print(text)
