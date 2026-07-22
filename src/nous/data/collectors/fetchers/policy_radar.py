#!/usr/bin/env python3
"""
政策雷达系统 — 多源宏观/政策数据采集引擎

四层采集：
  L1: 宏观指标（akshare：CPI/PPI/PMI/LPR/M2/GDP/SHIBOR/融资融券）
  L2: 政策事件（国常会/央行/部委/政治局 via 定向页面抓取 + web 搜索）
  L3: 板块政策（按13个板块关键词定向搜索）
  L4: 权威解读（2025eyp + 新华网 + 第一财经 + 21世纪 + 财新 + 经济观察报）

输出：~/wiki/finance/raw/policy/YYYY-MM-DD.json
       ~/wiki/finance/raw/policy/YYYY-MM-DD.md（人类可读摘要）

用法：
  ~/.hermes/hermes-agent/venv/bin/python3 fetchers/policy_radar.py
  ~/.hermes/hermes-agent/venv/bin/python3 fetchers/policy_radar.py --quick  # 仅宏观指标
"""

import json
import os
import re
import sys
import time
import hashlib
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional
from dataclasses import dataclass, field, asdict

import requests
import akshare as ak
import pandas as pd

# ── 常量 ───────────────────────────────────────────

WIKI_RAW = os.path.expanduser("~/wiki/finance/raw/policy")
os.makedirs(WIKI_RAW, exist_ok=True)

TODAY = date.today().isoformat()
TODAY_COMPACT = date.today().strftime("%Y%m%d")
YESTERDAY = (date.today() - timedelta(days=1)).isoformat()

# 13个监控板块及其政策关键词
SECTOR_KEYWORDS = {
    "银行": ["银行", "LPR", "降准", "存款利率", "净息差", "资本充足率"],
    "房地产": ["房地产", "房贷", "限购", "保障房", "城中村", "首付", "三道红线"],
    "新能源": ["新能源", "光伏", "风电", "储能", "氢能", "可再生能源", "补贴退坡"],
    "新能源汽车": ["新能源汽车", "电动车", "充电桩", "购置税", "以旧换新", "锂电池"],
    "半导体": ["芯片", "半导体", "集成电路", "光刻机", "大基金", "EDA", "先进封装"],
    "AI/数字经济": ["人工智能", "AI", "算力", "大模型", "数据要素", "数字经济", "东数西算"],
    "医药": ["医药", "创新药", "集采", "DRG", "医保", "医疗器械", "生物制药"],
    "消费": ["消费", "内需", "以旧换新", "家电", "汽车", "餐饮", "旅游"],
    "基建": ["基建", "专项债", "REITs", "水利", "交通", "新基建", "城市更新"],
    "能源": ["煤炭", "石油", "天然气", "电力", "电价", "碳中和", "碳达峰"],
    "农业": ["农业", "粮食", "种子", "耕地", "乡村振兴", "生猪", "化肥"],
    "外贸": ["出口", "关税", "贸易战", "RCEP", "一带一路", "跨境电商", "汇率"],
    "金融监管": ["证监会", "IPO", "再融资", "减持", "量化", "融券", "券商并表"],
}

# ── 新浪财经 Feed API ────────────────────────────

# 新浪财经各频道的 Feed API LID
# 通过 https://feed.mix.sina.com.cn/api/roll/get 访问
SINA_FEED_CHANNELS = {
    "宏观": {"pageid": 155, "lid": 1686},
    "政策": {"pageid": 155, "lid": 1689},
    "产业": {"pageid": 155, "lid": 1690},
    "证券": {"pageid": 155, "lid": 1691},
    "地产": {"pageid": 155, "lid": 1692},
    "科技": {"pageid": 155, "lid": 1693},
    "消费": {"pageid": 155, "lid": 1695},
}

# 权威媒体信源（用于头条/独家报道分类）
AUTHORITATIVE_SOURCES = {
    "新华网": {
        "url": "https://www.news.cn/fortune/",
        "rss": "",
        "type": "official",
        "media_keys": ["新华网", "新华社", "Xinhua"],
    },
    "第一财经": {
        "url": "https://www.yicai.com/",
        "rss": "",
        "type": "professional",
        "media_keys": ["第一财经", "Yicai"],
    },
    "21世纪经济报道": {
        "url": "https://www.21jingji.com/",
        "rss": "",
        "type": "professional",
        "media_keys": ["21世纪经济报道", "21世纪"],
    },
    "经济观察报": {
        "url": "http://www.eeo.com.cn/",
        "rss": "",
        "type": "professional",
        "media_keys": ["经济观察报", "经济观察网"],
    },
    "财新网": {
        "url": "https://www.caixin.com/",
        "rss": "",
        "type": "professional",
        "media_keys": ["财新", "Caixin"],
    },
    "证券时报": {
        "url": "https://www.stcn.com/",
        "rss": "",
        "type": "official",
        "media_keys": ["证券时报"],
    },
    "上海证券报": {
        "url": "https://www.cnstock.com/",
        "rss": "",
        "type": "official",
        "media_keys": ["上海证券报"],
    },
    "央行": {
        "url": "http://www.pbc.gov.cn/",
        "type": "government",
        "media_keys": ["央行", "中国人民银行", "人民银行"],
    },
    "发改委": {
        "url": "https://www.ndrc.gov.cn/",
        "type": "government",
        "media_keys": ["发改委", "国家发展改革委"],
    },
    "工信部": {
        "url": "https://www.miit.gov.cn/",
        "type": "government",
        "media_keys": ["工信部", "工业和信息化部"],
    },
    "住建部": {
        "url": "https://www.mohurd.gov.cn/",
        "type": "government",
        "media_keys": ["住建部", "住房城乡建设部"],
    },
    "证监会": {
        "url": "http://www.csrc.gov.cn/",
        "type": "government",
        "media_keys": ["证监会", "中国证监会"],
    },
}

# 政府/部委政策发布页（Sina 频道之外的直接抓取目标）
GOV_POLICY_URLS = {
    "央行公开市场": "http://www.pbc.gov.cn/zhengcehuobisi/125207/125213/125431/index.html",
    "央行货币政策": "http://www.pbc.gov.cn/zhengcehuobisi/125207/125227/125957/index.html",
    "国常会": "http://www.gov.cn/zhengce/zuixin.htm",
    "发改委": "https://www.ndrc.gov.cn/xwdt/xwfb/",
    "工信部": "https://www.miit.gov.cn/xwdt/gxdt/sjdt/",
}


# ── 数据模型 ───────────────────────────────────────

@dataclass
class MacroIndicator:
    """宏观指标"""
    name: str
    value: str
    unit: str
    date: str
    prev_value: str = ""
    change: str = ""  # 环比/同比变化
    forecast: str = ""
    source: str = ""
    impact: str = ""  # bullish / bearish / neutral

@dataclass
class PolicyEvent:
    """政策事件"""
    title: str
    source: str  # 信源
    source_type: str  # government / official / professional
    date: str
    summary: str = ""
    sectors: list[str] = field(default_factory=list)  # 影响板块
    impact_level: str = ""  # high / medium / low
    url: str = ""
    content_hash: str = ""

@dataclass
class SectorPolicy:
    """板块政策"""
    sector: str
    events: list[PolicyEvent] = field(default_factory=list)
    sentiment: str = "neutral"  # supportive / tightening / neutral
    key_changes: list[str] = field(default_factory=list)

@dataclass
class PolicyRadarReport:
    """政策雷达日报"""
    date: str
    generated_at: str
    # L1: 宏观指标
    macro_indicators: list[MacroIndicator] = field(default_factory=list)
    macro_sentiment: str = "neutral"  # expansion / contraction / neutral
    # L2: 政策事件
    policy_events: list[PolicyEvent] = field(default_factory=list)
    top_events: list[PolicyEvent] = field(default_factory=list)  # 高影响事件
    # L3: 板块政策
    sector_policies: dict[str, SectorPolicy] = field(default_factory=dict)
    # L4: 权威解读
    interpretations: list[dict] = field(default_factory=list)
    # 元数据
    sources_used: list[str] = field(default_factory=list)
    fetch_errors: list[str] = field(default_factory=list)


# ── L1: 宏观指标采集 ───────────────────────────────

def fetch_macro_indicators() -> list[MacroIndicator]:
    """从 akshare 采集宏观指标"""
    indicators = []
    today = date.today()

    # CPI
    try:
        df = ak.macro_china_cpi_yearly()
        # 取最后一个有效值
        df_valid = df[df["今值"].notna()]
        if len(df_valid) > 0:
            row = df_valid.iloc[-1]
        else:
            row = df.iloc[-1]
        val = row["今值"]
        if pd.isna(val):
            val = 0
        indicators.append(MacroIndicator(
            name="CPI（居民消费价格指数）",
            value=f"{float(val):.1f}",
            unit="同比%",
            date=str(row["日期"]),
            prev_value=str(row.get("前值", "")),
            forecast=str(row.get("预测值", "")),
            source="国家统计局",
            impact="bullish" if float(val) < 1.5 else "neutral" if float(val) < 3 else "bearish",
        ))
    except Exception as e:
        indicators.append(MacroIndicator(name="CPI", value="采集失败", unit="", date=TODAY, source="", impact=""))
        print(f"  ⚠️ CPI: {e}")

    # PPI
    try:
        df = ak.macro_china_ppi_yearly()
        df_valid = df[df["今值"].notna()]
        row = df_valid.iloc[-1] if len(df_valid) > 0 else df.iloc[-1]
        val = row["今值"]
        if pd.isna(val):
            val = 0
        indicators.append(MacroIndicator(
            name="PPI（工业生产者出厂价格）",
            value=f"{float(val):.1f}",
            unit="同比%",
            date=str(row["日期"]),
            prev_value=str(row.get("前值", "")),
            forecast=str(row.get("预测值", "")),
            source="国家统计局",
            impact="bullish" if float(val) > 0 else "bearish",
        ))
    except Exception as e:
        indicators.append(MacroIndicator(name="PPI", value="采集失败", unit="", date=TODAY, source="", impact=""))
        print(f"  ⚠️ PPI: {e}")

    # PMI
    try:
        df = ak.macro_china_pmi()
        row = df.iloc[-1]
        mfg_val = str(row["制造业-指数"])
        indicators.append(MacroIndicator(
            name="PMI（制造业采购经理指数）",
            value=mfg_val,
            unit="%",
            date=str(row["月份"]),
            prev_value="",
            source="国家统计局",
            impact="bullish" if float(mfg_val) > 50 else "bearish",
        ))
    except Exception as e:
        indicators.append(MacroIndicator(name="PMI", value="采集失败", unit="", date=TODAY, source="", impact=""))
        print(f"  ⚠️ PMI: {e}")

    # LPR
    try:
        df = ak.macro_china_lpr()
        row = df.iloc[-1]
        lpr1y = str(row["LPR1Y"])
        lpr5y = str(row["LPR5Y"])
        indicators.append(MacroIndicator(
            name="LPR 1年期",
            value=lpr1y,
            unit="%",
            date=str(row["TRADE_DATE"]),
            source="中国人民银行",
            impact="bullish" if len(df) > 1 and float(lpr1y) < float(df.iloc[-2]["LPR1Y"]) else "neutral",
        ))
        indicators.append(MacroIndicator(
            name="LPR 5年期",
            value=lpr5y,
            unit="%",
            date=str(row["TRADE_DATE"]),
            source="中国人民银行",
            impact="bullish" if len(df) > 1 and float(lpr5y) < float(df.iloc[-2]["LPR5Y"]) else "neutral",
        ))
    except Exception as e:
        print(f"  ⚠️ LPR: {e}")

    # M2 / 社融
    try:
        df = ak.macro_china_money_supply()
        row = df.iloc[-1]
        m2_growth = str(row["货币和准货币(M2)-同比增长"])
        indicators.append(MacroIndicator(
            name="M2（广义货币）",
            value=m2_growth,
            unit="同比%",
            date=str(row["月份"]),
            source="中国人民银行",
            impact="bullish" if float(m2_growth) > 8 else "neutral",
        ))
    except Exception as e:
        print(f"  ⚠️ M2: {e}")

    # SHIBOR
    try:
        df = ak.macro_china_shibor_all()
        row = df.iloc[-1]
        on_val = str(row["O/N-定价"])
        indicators.append(MacroIndicator(
            name="SHIBOR 隔夜",
            value=on_val,
            unit="%",
            date=str(row["日期"]),
            source="全国银行间同业拆借中心",
            impact="bullish" if float(on_val) < 1.5 else "neutral",
        ))
    except Exception as e:
        print(f"  ⚠️ SHIBOR: {e}")

    # 融资融券余额
    try:
        df = ak.macro_china_market_margin_sh()
        row = df.iloc[-1]
        margin_val = str(row["融资余额"])
        indicators.append(MacroIndicator(
            name="融资余额（上交所）",
            value=f"{float(margin_val)/1e8:.0f}亿",
            unit="",
            date=str(row["日期"]),
            source="上交所",
            impact="bullish" if len(df) > 5 and float(row["融资余额"]) > float(df.iloc[-5]["融资余额"]) else "neutral",
        ))
    except Exception as e:
        print(f"  ⚠️ 融资余额: {e}")

    # GDP
    try:
        df = ak.macro_china_gdp_yearly()
        row = df.iloc[-1]
        indicators.append(MacroIndicator(
            name="GDP",
            value=str(row["今值"]),
            unit="同比%",
            date=str(row["日期"]),
            prev_value=str(row["前值"]),
            forecast=str(row.get("预测值", "")),
            source="国家统计局",
            impact="bullish" if float(row["今值"]) >= 5 else "bearish",
        ))
    except Exception as e:
        print(f"  ⚠️ GDP: {e}")

    return indicators


# ── L2: 政策事件采集（新浪 Feed API + 政府官网） ──

SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "application/json, text/html",
    "Accept-Language": "zh-CN,zh;q=0.9",
    "Referer": "https://finance.sina.com.cn/",
})


def fetch_sina_feed(pageid: int, lid: int, num: int = 20) -> list[dict]:
    """拉取新浪财经 Feed API"""
    url = f"https://feed.mix.sina.com.cn/api/roll/get?pageid={pageid}&lid={lid}&num={num}"
    try:
        resp = SESSION.get(url, timeout=15)
        data = resp.json()
        articles = data.get("result", {}).get("data", [])
        items = []
        for a in articles:
            items.append({
                "title": a.get("title", ""),
                "url": a.get("url", ""),
                "summary": a.get("intro", ""),
                "date": a.get("ctime", ""),
                "keywords": a.get("keywords", ""),
                "media_name": a.get("media_name", ""),
            })
        return items
    except Exception as e:
        print(f"  ⚠️ Sina feed (lid={lid}): {e}")
        return []


def fetch_policy_events() -> list[PolicyEvent]:
    """从新浪财经 Feed API 多频道采集政策事件"""
    events = []
    seen_hashes = set()

    for channel_name, cfg in SINA_FEED_CHANNELS.items():
        try:
            items = fetch_sina_feed(cfg["pageid"], cfg["lid"], num=20)
            print(f"  📡 Sina/{channel_name}: {len(items)} 条")

            for item in items:
                content_hash = hashlib.md5(
                    (item["title"] + item.get("url", "")).encode()
                ).hexdigest()[:12]

                if content_hash in seen_hashes:
                    continue
                seen_hashes.add(content_hash)

                # 匹配信源
                source_name, source_type = _match_source(
                    item.get("media_name", "")
                )

                # 匹配影响板块
                affected_sectors = _match_sectors(item["title"])

                events.append(PolicyEvent(
                    title=_clean_text(item["title"]),
                    source=source_name,
                    source_type=source_type,
                    date=_parse_sina_date(item.get("date", "")),
                    summary=item.get("summary", "")[:200],
                    sectors=affected_sectors,
                    impact_level=_assess_impact(item["title"], affected_sectors),
                    url=item.get("url", ""),
                    content_hash=content_hash,
                ))
        except Exception as e:
            print(f"  ⚠️ 频道 {channel_name}: {e}")

    print(f"  共 {len(events)} 条政策事件 ({len(seen_hashes)} 去重后)")
    return events


# ── L3: 板块政策 ────────────────────────────────────

def organize_sector_policies(events: list[PolicyEvent]) -> dict[str, SectorPolicy]:
    """按板块归类政策事件"""
    sectors: dict[str, SectorPolicy] = {}
    for sector in SECTOR_KEYWORDS:
        sectors[sector] = SectorPolicy(sector=sector)

    for event in events:
        for sector in event.sectors:
            if sector in sectors:
                sectors[sector].events.append(event)

    # 评估板块政策情绪
    for sector, sp in sectors.items():
        if not sp.events:
            sp.sentiment = "neutral"
            continue
        supportive = sum(1 for e in sp.events if "利好" in e.title or "支持" in e.title or "鼓励" in e.title)
        tightening = sum(1 for e in sp.events if "监管" in e.title or "收紧" in e.title or "严查" in e.title)
        if supportive > tightening:
            sp.sentiment = "supportive"
        elif tightening > supportive:
            sp.sentiment = "tightening"
        else:
            sp.sentiment = "neutral"

    return sectors


# ── L4: 权威解读 ────────────────────────────────────

def fetch_2025eyp_articles() -> list[dict]:
    """从 2025eyp 拉取宏观分析文章"""
    articles = []
    try:
        resp = SESSION.get(
            "http://127.0.0.1/v1/articles/new",
            params={"page": 1, "count": 10, "columnId": 2},  # 宏观分析专栏
            headers={
                "Authorization": "Bearer ",
                "Origin": "http://h5.2025eyp.com",
                "Referer": "http://h5.2025eyp.com/",
            },
            timeout=15,
        )
        data = resp.json()
        for item in data.get("data", {}).get("articles", [])[:5]:
            articles.append({
                "title": item.get("title", ""),
                "summary": item.get("summary", ""),
                "date": item.get("createdAt", ""),
                "source": "2025eyp",
                "source_type": "professional",
            })
    except Exception as e:
        print(f"  ⚠️ 2025eyp: {e}")

    return articles


# ── 综合报告生成 ────────────────────────────────────

def generate_radar_report(
    quick: bool = False,
) -> PolicyRadarReport:
    """生成完整政策雷达日报"""
    print(f"[{datetime.now():%H:%M:%S}] 政策雷达启动...")

    report = PolicyRadarReport(
        date=TODAY,
        generated_at=datetime.now().isoformat(),
    )

    # L1: 宏观指标
    print("  L1: 宏观指标...")
    report.macro_indicators = fetch_macro_indicators()
    signals = sum(1 for m in report.macro_indicators if m.impact == "bullish") - \
              sum(1 for m in report.macro_indicators if m.impact == "bearish")
    if signals > 2:
        report.macro_sentiment = "expansion"
    elif signals < -2:
        report.macro_sentiment = "contraction"
    else:
        report.macro_sentiment = "neutral"

    if quick:
        return report

    # L2: 政策事件
    print("  L2: 政策事件...")
    report.policy_events = fetch_policy_events()
    report.top_events = [e for e in report.policy_events if e.impact_level == "high"]
    report.sources_used = list(set(e.source for e in report.policy_events))

    # L3: 板块政策
    print("  L3: 板块政策...")
    report.sector_policies = organize_sector_policies(report.policy_events)

    # L4: 权威解读
    print("  L4: 权威解读...")
    report.interpretations = fetch_2025eyp_articles()

    print(f"[{datetime.now():%H:%M:%S}] 雷达采集完成")
    print(f"  指标: {len(report.macro_indicators)} | 事件: {len(report.policy_events)} | "
          f"板块: {len(report.sector_policies)} | 解读: {len(report.interpretations)}")

    return report


def save_report(report: PolicyRadarReport):
    """保存报告到 JSON 和 Markdown"""
    # JSON
    json_path = os.path.join(WIKI_RAW, f"{TODAY}.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(_serialize(report), f, ensure_ascii=False, indent=2)
    print(f"  JSON: {json_path}")

    # Markdown
    md = _render_markdown(report)
    md_path = os.path.join(WIKI_RAW, f"{TODAY}.md")
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(md)
    print(f"  MD: {md_path}")


# ── Markdown 渲染 ───────────────────────────────────

def _render_markdown(report: PolicyRadarReport) -> str:
    lines = []
    lines.append(f"# 政策雷达日报 — {report.date}")
    lines.append(f"> 生成: {report.generated_at} | 宏观情绪: **{_label_macro(report.macro_sentiment)}**")
    lines.append("")

    # 宏观指标
    lines.append("## 一、宏观指标速览")
    lines.append("")
    lines.append("| 指标 | 现值 | 前值 | 方向 | 解读 |")
    lines.append("|------|------|------|------|------|")
    for m in report.macro_indicators:
        impact_icon = {"bullish": "🟢", "bearish": "🔴", "neutral": "⚪", "": "⚪"}
        lines.append(
            f"| {m.name} | {m.value}{m.unit} | {m.prev_value} | "
            f"{impact_icon.get(m.impact, '⚪')} | {_interpret_indicator(m)} |"
        )
    lines.append("")

    # 高影响政策事件
    if report.top_events:
        lines.append("## 二、重大政策事件")
        lines.append("")
        for e in report.top_events[:8]:
            lines.append(f"### {e.title}")
            lines.append(f"- 信源: **{e.source}** ({e.source_type}) | 日期: {e.date}")
            if e.sectors:
                lines.append(f"- 影响板块: {', '.join(e.sectors)}")
            if e.url:
                lines.append(f"- 链接: {e.url}")
            lines.append("")

    # 板块政策情绪
    lines.append("## 三、板块政策情绪")
    lines.append("")
    active_sectors = {k: v for k, v in report.sector_policies.items() if v.events}
    if active_sectors:
        lines.append("| 板块 | 情绪 | 事件数 | 关键政策 |")
        lines.append("|------|------|--------|----------|")
        for sector, sp in active_sectors.items():
            sentiment_label = {
                "supportive": "🟢 支持性",
                "tightening": "🔴 收紧",
                "neutral": "⚪ 中性",
            }.get(sp.sentiment, "⚪ 中性")
            key = sp.events[0].title[:30] if sp.events else "-"
            lines.append(f"| {sector} | {sentiment_label} | {len(sp.events)} | {key} |")
    else:
        lines.append("> 今日无显著板块政策事件")
    lines.append("")

    # 权威解读
    if report.interpretations:
        lines.append("## 四、权威解读")
        lines.append("")
        for item in report.interpretations[:5]:
            lines.append(f"- **[{item['source']}]** {item['title']}")
            if item.get("summary"):
                lines.append(f"  > {item['summary'][:150]}")
        lines.append("")

    # 信源统计
    if report.sources_used:
        lines.append(f"## 五、信源 ({len(report.sources_used)})")
        lines.append("")
        lines.append(f"本日共使用 {len(report.sources_used)} 个信源:")
        for s in sorted(report.sources_used):
            cfg = AUTHORITATIVE_SOURCES.get(s, {})
            lines.append(f"- **{s}** ({cfg.get('type', 'unknown')})")

    return "\n".join(lines)


# ── 辅助函数 ────────────────────────────────────────

def _parse_sina_date(ctime: str) -> str:
    """解析新浪 Feed 返回的时间（Unix 时间戳或日期字符串）"""
    try:
        ts = int(ctime)
        return datetime.fromtimestamp(ts).strftime("%Y-%m-%d")
    except (ValueError, TypeError):
        return ctime[:10] if ctime else TODAY


def _clean_html(text: str) -> str:
    """去除 HTML 标签"""
    return re.sub(r"<[^>]+>", "", text).strip()


def _clean_text(text: str) -> str:
    """清洗文本"""
    text = _clean_html(text)
    text = text.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
    return text.strip()


def _match_sectors(title: str) -> list[str]:
    """匹配标题影响哪些板块"""
    matched = []
    for sector, keywords in SECTOR_KEYWORDS.items():
        for kw in keywords:
            if kw in title:
                matched.append(sector)
                break
    return list(set(matched))


def _match_source(media_name: str) -> tuple[str, str]:
    """匹配媒体名称为信源名称和类型"""
    for source_name, cfg in AUTHORITATIVE_SOURCES.items():
        for key in cfg.get("media_keys", []):
            if key in media_name:
                return (source_name, cfg["type"])
    return (media_name if media_name else "新浪财经", "media")


def _assess_impact(title: str, sectors: list[str]) -> str:
    """评估政策影响等级"""
    high_keywords = ["政治局", "国常会", "国务院", "降准", "降息", "LPR", "改革",
                     "部署", "通知", "方案", "意见", "规划"]
    for kw in high_keywords:
        if kw in title:
            return "high"
    return "medium"


def _label_macro(sentiment: str) -> str:
    return {"expansion": "🟢 扩张", "contraction": "🔴 收缩", "neutral": "⚪ 中性"}.get(sentiment, "中性")


def _interpret_indicator(m: MacroIndicator) -> str:
    """生成指标解读短句"""
    interpretations = {
        "CPI": "通胀温和" if m.impact == "bullish" else "通胀压力" if m.impact == "bearish" else "",
        "PPI": "出厂价格回升" if m.impact == "bullish" else "出厂价格承压",
        "PMI": "制造业扩张" if m.impact == "bullish" else "制造业收缩",
        "M2": "流动性充裕" if m.impact == "bullish" else "流动性中性",
        "GDP": "经济增速达标" if m.impact == "bullish" else "经济增速放缓",
    }
    for key, text in interpretations.items():
        if key in m.name:
            return text
    return ""


def _serialize(report: PolicyRadarReport) -> dict:
    """序列化报告为 JSON 兼容的 dict"""
    result = {
        "date": report.date,
        "generated_at": report.generated_at,
        "macro_sentiment": report.macro_sentiment,
        "macro_indicators": [asdict(m) for m in report.macro_indicators],
        "top_events": [],
        "sector_policies": {},
        "interpretations": report.interpretations,
        "sources_used": report.sources_used,
    }
    for e in report.top_events:
        d = asdict(e)
        d["sectors"] = e.sectors  # dataclass asdict 可能丢失 list
        result["top_events"].append(d)
    for sector, sp in report.sector_policies.items():
        result["sector_policies"][sector] = {
            "sector": sp.sector,
            "sentiment": sp.sentiment,
            "event_count": len(sp.events),
            "events": [asdict(e) for e in sp.events],
            "key_changes": sp.key_changes,
        }
    return result


# ── 主入口 ───────────────────────────────────────────

def main():
    import argparse
    parser = argparse.ArgumentParser(description="政策雷达系统")
    parser.add_argument("--quick", action="store_true", help="仅采集宏观指标")
    args = parser.parse_args()

    report = generate_radar_report(quick=args.quick)
    save_report(report)

    # 输出摘要
    bullish = sum(1 for m in report.macro_indicators if m.impact == "bullish")
    bearish = sum(1 for m in report.macro_indicators if m.impact == "bearish")
    print(f"\n  宏观信号: 🟢{bullish} 🔴{bearish} | 情绪: {_label_macro(report.macro_sentiment)}")
    if report.top_events:
        print(f"  高影响事件: {len(report.top_events)} 条")
        for e in report.top_events[:3]:
            print(f"    📌 {e.title[:50]}...")


if __name__ == "__main__":
    main()
