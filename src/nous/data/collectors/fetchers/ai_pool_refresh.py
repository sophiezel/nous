#!/usr/bin/env python3
"""
AI产业链观察池 — 每日收盘后自动刷新
数据源: 新浪 stock_zh_a_spot
输出: ~/wiki/finance/concepts/AI产业链-观察池.md

执行时机: 每个交易日 15:30 (收盘后)
"""

import json, sys, os
from datetime import datetime
from collections import OrderedDict

# ============================================================
# 观察池定义 (固定结构，不随刷新变化)
# ============================================================
POOL = OrderedDict([
    ("上游-存储芯片", [
        ("001309", "德明利", "鳄鱼派核心"),
        ("301308", "江波龙", "鳄鱼派核心"),
        ("300042", "朗科科技", "鳄鱼派-低位"),
        ("002213", "大为股份", "鳄鱼派-低位"),
        ("603986", "兆易创新", "标准池"),
    ]),
    ("上游-CPU/GPU", [
        ("000066", "中国长城", "鳄鱼派"),
        ("688256", "寒武纪", "标准池"),
        ("688041", "海光信息", "标准池"),
        ("300474", "景嘉微", "标准池"),
    ]),
    ("上游-封测/制造", [
        ("688981", "中芯国际", "标准池"),
        ("002049", "紫光国微", "标准池"),
        ("688008", "澜起科技", "标准池"),
        ("600584", "长电科技", "标准池"),
    ]),
    ("中游-光模块", [
        ("300394", "天孚通信", "标准池-龙头"),
        ("300502", "新易盛", "标准池"),
        ("300308", "中际旭创", "标准池"),
        ("002281", "光迅科技", "标准池"),
        ("300570", "太辰光", "标准池"),
    ]),
    ("中游-光通信", [
        ("000925", "众合科技", "鳄鱼派-新开"),
        ("600345", "长江通信", "鳄鱼派-波段"),
    ]),
    ("中游-服务器/算力", [
        ("601138", "工业富联", "标准池"),
        ("000977", "浪潮信息", "标准池"),
        ("603019", "中科曙光", "标准池"),
        ("300383", "光环新网", "标准池"),
    ]),
    ("中游-算力租赁", [
        ("603985", "恒润股份", "鳄鱼派-观望"),
        ("300895", "铜牛信息", "鳄鱼派-观望"),
    ]),
    ("中游-PCB", [
        ("002916", "深南电路", "标准池"),
        ("300476", "胜宏科技", "标准池"),
        ("603386", "骏亚科技", "模拟盘持仓"),
        ("002463", "沪电股份", "标准池"),
    ]),
    ("中游-液冷/散热", [
        ("002837", "英维克", "标准池"),
        ("300684", "中石科技", "标准池"),
    ]),
    ("下游-电源配套", [
        ("002364", "中恒电气", "鳄鱼派-新开"),
    ]),
    ("下游-AI应用/软件", [
        ("300058", "蓝色光标", "鳄鱼派-新开"),
        ("600986", "浙文互联", "鳄鱼派-观察"),
        ("002230", "科大讯飞", "标准池"),
        ("688111", "金山办公", "标准池"),
        ("300624", "万兴科技", "标准池"),
        ("300033", "同花顺", "标准池"),
    ]),
    ("下游-机器人", [
        ("300124", "汇川技术", "标准池"),
        ("688017", "绿的谐波", "标准池"),
        ("603728", "鸣志电器", "标准池"),
    ]),
])

# 板块备注（固定）
NOTES = {
    "上游-存储芯片": "鳄鱼派观点: 存储芯片明天难追（昨天分歧→今天一致），持股为主。",
    "上游-CPU/GPU": "鳄鱼派: 中国长城明天必须新高中阳，否则减仓。",
    "中游-光模块": "龙头天孚涨停，新易盛+6%，易中天整体强势。光迅高开低走破板。",
    "中游-光通信": "鳄鱼派: 众合明天10:30前不涨停就止盈（3板概率不小）。长江格局持有。",
    "中游-服务器/算力": "服务器板块全面爆发：工业富联涨停，光环+7%，浪潮+5.8%。",
    "中游-算力租赁": "鳄鱼派: 短期没芯片强，考虑走但没结束，观望。",
    "中游-PCB": "⚠️ 骏亚逆势下跌，和深南(+5.67%)、沪电(+3.59%)形成分化。PCB板块在扩散但骏亚不是资金首选。",
    "下游-电源配套": "鳄鱼派: 今天新开，波段格局，明天冲高先锁利润垫。",
    "下游-AI应用/软件": "下游整体涨幅最低，AI应用板块尚在低位。鳄鱼派: 浙文今天拉升不适合追，蓝色光标新开。",
}

OUTPUT_PATH = os.path.expanduser("~/wiki/finance/concepts/AI产业链-观察池.md")


def fetch_data():
    """从新浪拉取全市场行情，筛选观察池标的"""
    import akshare as ak

    # Sina 接口代码带 sz/sh 前缀
    code_to_prefixed = {}
    for stocks in POOL.values():
        for code, name, tag in stocks:
            if code.startswith(("0", "2", "3")):
                code_to_prefixed[f"sz{code}"] = code
            else:
                code_to_prefixed[f"sh{code}"] = code

    try:
        df = ak.stock_zh_a_spot()
        if df is None or df.empty:
            print("ERROR: stock_zh_a_spot returned empty", file=sys.stderr)
            return None
    except Exception as e:
        print(f"ERROR fetching data: {e}", file=sys.stderr)
        return None

    results = {}
    for _, row in df.iterrows():
        full_code = str(row.get("代码", ""))
        if full_code in code_to_prefixed:
            code = code_to_prefixed[full_code]
            results[code] = {
                "name": row.get("名称", ""),
                "price": float(row.get("最新价", 0) or 0),
                "pct": float(row.get("涨跌幅", 0) or 0),
                "volume": int(row.get("成交量", 0) or 0),
                "amount": float(row.get("成交额", 0) or 0) / 1e8,  # 转亿
                "high": float(row.get("最高", 0) or 0),
                "low": float(row.get("最低", 0) or 0),
                "open": float(row.get("今开", 0) or 0),
                "pre_close": float(row.get("昨收", 0) or 0),
            }
    return results


def fmt_pct(pct):
    """格式化涨跌幅"""
    if pct >= 9.9:
        return f"**+{pct:.2f}%** 🔥"
    elif pct >= 5:
        return f"**+{pct:.2f}%** 🚀"
    elif pct > 0:
        return f"+{pct:.2f}%"
    elif pct > -3:
        return f"{pct:.2f}%"
    else:
        return f"{pct:.2f}% 🔻"


def fmt_amount(amount_yi):
    """格式化成交额"""
    if amount_yi >= 100:
        return f"{amount_yi:.1f}"
    elif amount_yi >= 10:
        return f"{amount_yi:.1f}"
    else:
        return f"{amount_yi:.1f}"


def build_summary(data, pool):
    """生成梯队总结"""
    fire = []   # 涨停 >9.9%
    rocket = [] # 爆量 >5%
    up = []     # 上涨
    down = []   # 下跌

    for cat, stocks in pool.items():
        for code, name, tag in stocks:
            d = data.get(code)
            if d is None:
                continue
            pct = d["pct"]
            label = f"{name}({pct:+.1f}%)"
            if pct >= 9.9:
                fire.append(label)
            elif pct >= 5:
                rocket.append(label)
            elif pct > 0:
                up.append(label)
            else:
                down.append(label)

    lines = []
    lines.append(f"🔥 涨停 ({len(fire)}只): " + " ".join(fire[:8]))
    lines.append(f"🚀 爆量>5% ({len(rocket)}只): " + " ".join(rocket[:12]))
    lines.append(f"📈 温和上涨 ({len(up)}只): " + " ".join(up[:8]) + (" ..." if len(up) > 8 else ""))
    lines.append(f"🔻 下跌 ({len(down)}只): " + " ".join(down[:8]) + (" ..." if len(down) > 8 else ""))
    return "\n".join(lines)


def build_markdown(data, pool):
    """生成完整 markdown"""
    today = datetime.now().strftime("%Y-%m-%d")
    now = datetime.now().strftime("%Y-%m-%d %H:%M")

    lines = [
        f"# AI 产业链观察池",
        f"",
        f"> 更新: {now} | 来源: 鳄鱼派(胡文杰) + 标准池 | 自动刷新",
        f"> 覆盖: 上游芯片 → 中游硬件 → 下游应用 | 共 {sum(len(v) for v in pool.values())} 只",
        f"",
        f"---",
        f"",
    ]

    for cat, stocks in pool.items():
        lines.append(f"## {cat} ({len(stocks)}只)")
        lines.append("")
        lines.append("| 代码 | 名称 | 现价 | 涨跌 | 成交额(亿) | 来源 |")
        lines.append("|------|------|------|------|-----------|------|")

        for code, name, tag in stocks:
            d = data.get(code)
            if d is None:
                lines.append(f"| {code} | {name} | - | - | - | {tag} |")
                continue
            lines.append(
                f"| {code} | {name} | {d['price']:.2f} | {fmt_pct(d['pct'])} | {fmt_amount(d['amount'])} | {tag} |"
            )

        lines.append("")
        if cat in NOTES:
            lines.append(f"> {NOTES[cat]}")
            lines.append("")

    # 今日梯队总览
    lines.append("---")
    lines.append("")
    lines.append("## 今日梯队总览")
    lines.append("")
    lines.append("```")
    lines.append(build_summary(data, pool))
    lines.append("```")
    lines.append("")

    return "\n".join(lines)


def main():
    print("AI产业链观察池刷新...", flush=True)
    data = fetch_data()
    if data is None:
        print("获取数据失败，不更新", file=sys.stderr)
        sys.exit(1)

    found = len(data)
    total = sum(len(v) for v in POOL.values())
    print(f"获取到 {found}/{total} 只", flush=True)

    md = build_markdown(data, POOL)
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        f.write(md)

    print(f"已写入 {OUTPUT_PATH} ({len(md)} 字符)", flush=True)


if __name__ == "__main__":
    main()
