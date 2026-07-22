#!/usr/bin/env python3
"""
市场情绪采集器

从 akshare 拉取全量A股行情（stock_zh_a_spot，69页分页），计算市场情绪指标。
包括涨停数、跌停数、炸板率、涨跌比、连板高度、昨日涨停溢价，
通过加权评分体系综合得出市场情绪评分与等级。

数据保存到 ~/wiki/finance/raw/sentiment/{date}.json
支持独立运行：python sentiment.py

数据源：
- 涨停/跌停/涨跌数: akshare stock_zh_a_spot() (Sina源，69页分页)
- 连板高度: screener.db stock_daily 表
- 昨日涨停溢价: 计算昨日涨停股票今天的平均涨幅

情绪评分：涨停数30% + 涨跌比25% + 炸板率20% + 连板高度15% + 涨停溢价10%
等级映射：0-30恐惧, 30-50悲观, 50-70中性, 70-85乐观, 85-100贪婪
"""

from __future__ import annotations

import json
import sys
import os
import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path
from collections import defaultdict
from typing import Optional

import akshare as ak
import pandas as pd
import numpy as np

# Ensure report_store module is importable from dashboard scripts
sys.path.insert(0, os.path.expanduser("~/code/dashboard/scripts"))


# ── 常量 ──────────────────────────────────────────────────────────────

OUTPUT_DIR = Path.home() / "wiki" / "finance" / "raw" / "sentiment"
SCREENER_DB = Path(__file__).resolve().parents[4]  # nous repo root / "data" / "screener.db"
BOARD_LOOKBACK_DAYS = 10  # 连板查询回溯天数（含当日）

# 涨跌幅限制百分比的 epsilon 容忍度（受四舍五入和精度影响）
LIMIT_EPSILON = 0.99


# ── 涨跌幅限制判断 ─────────────────────────────────────────────────

def get_limit_threshold(code: str) -> float:
    """
    根据股票代码前缀判断涨跌幅限制（百分比）。

    Rules:
    - 主板 sh60xxxx / sh其他(非68) / sz00xxxx / sz其他(非30): 10%
    - 科创板 sh68xxxx: 20%
    - 创业板 sz30xxxx: 20%
    - 北交所 bj*: 30%

    Parameters:
        code: 带前缀的代码，如 sh600519, sz300750, bj920000

    Returns:
        涨跌幅限制百分比 (10.0, 20.0, 30.0 之一)
    """
    if not code or not isinstance(code, str):
        return 10.0

    code_part = code
    if code.startswith("sh") or code.startswith("sz") or code.startswith("bj"):
        code_part = code[2:]

    # 北交所: bj 开头或 8/4/92 开头
    if code.startswith("bj"):
        return 30.0

    # 上交所
    if code.startswith("sh"):
        # 科创板 68xxxx
        if len(code_part) >= 2 and code_part[:2] == "68":
            return 20.0
        return 10.0

    # 深交所
    if code.startswith("sz"):
        # 创业板 30xxxx
        if len(code_part) >= 2 and code_part[:2] == "30":
            return 20.0
        return 10.0

    # 未知前缀，保守返回 10%
    return 10.0


def get_limit_threshold_db(symbol: str) -> float:
    """
    判断数据库中的纯数字代码的涨跌幅限制。

    DB symbols have no sh/sz/bj prefix.

    Rules:
    - 6xxxxx → 主板 10%
    - 00xxxx → 主板 10%
    - 30xxxx → 创业板 20%
    - 68xxxx → 科创板 20%
    - 8xxxxx, 4xxxxx, 92xxxx → 北交所 30%

    Parameters:
        symbol: 纯数字代码，如 600519, 300750, 920000

    Returns:
        涨跌幅限制百分比
    """
    if not symbol or not isinstance(symbol, str):
        return 10.0

    if len(symbol) < 2:
        return 10.0

    prefix = symbol[:2]
    if prefix == "68":
        return 20.0
    if prefix == "30":
        return 20.0
    if prefix in ("83", "87", "88", "89", "92", "40", "43"):
        return 30.0
    return 10.0


# ── 涨停/跌停/炸板 检测 ─────────────────────────────────────────────

def is_limit_up(row: pd.Series) -> bool:
    """
    判断是否为封板涨停：涨跌幅>=限制99%，最新价接近最高价99.8%
    """
    thresh = get_limit_threshold(row["代码"])
    return row["涨跌幅"] >= thresh * LIMIT_EPSILON and row["最新价"] >= row["最高"] * 0.998


def is_limit_down(row: pd.Series) -> bool:
    """
    判断是否为跌停：涨跌幅<=-限制99%，最新价接近最低价100.2%
    """
    thresh = get_limit_threshold(row["代码"])
    return row["涨跌幅"] <= -thresh * LIMIT_EPSILON and row["最新价"] <= row["最低"] * 1.002


def is_limit_up_busted(row: pd.Series) -> bool:
    """
    判断是否为炸板：最高触及涨停价但未封住
    """
    thresh = get_limit_threshold(row["代码"])
    prev_close = row["昨收"]
    high_price = row["最高"]

    limit_price = prev_close * (1.0 + thresh / 100.0)
    touched_limit = high_price >= limit_price * 0.99

    return touched_limit and not is_limit_up(row)


def _detect_db_limit_up(row) -> bool:
    """
    根据数据库 OHLC 数据判断某交易日是否涨停。

    条件：
    1. 当日涨幅 >= 限制的 95%（考虑DB精度）
    2. 收盘价接近最高价（99% 以上）

    参数:
        row: 包含 symbol, close, high, prev_close 字段的行

    返回:
        True 表示该日该股票涨停
    """
    prev_close = row.get("prev_close")
    close_val = row.get("close")
    high_val = row.get("high")

    # 检查必要字段
    if prev_close is None or close_val is None or high_val is None:
        return False
    if pd.isna(prev_close) or pd.isna(close_val) or pd.isna(high_val):
        return False
    if prev_close == 0:
        return False

    pct_change = (close_val / prev_close - 1.0) * 100.0
    symbol = str(row.get("symbol", ""))
    thresh = get_limit_threshold_db(symbol)

    return pct_change >= thresh * 0.95 and close_val >= high_val * 0.99


def _detect_db_limit_up_yesterday(row) -> bool:
    """
    判断某条记录的上一个交易日是否为涨停（用于昨日涨停溢价计算）。

    检查 prev_close 相对于 prev_prev_close 的涨幅是否达到涨停标准。

    参数:
        row: 包含 prev_close, prev_prev_close, symbol 字段的行

    返回:
        True 表示昨日该股票涨停
    """
    prev_close = row.get("prev_close")
    prev_prev_close = row.get("prev_prev_close")

    if prev_prev_close is None or prev_close is None:
        return False
    if pd.isna(prev_prev_close) or pd.isna(prev_close):
        return False
    if prev_prev_close == 0:
        return False

    pct_change = (prev_close / prev_prev_close - 1.0) * 100.0
    symbol = str(row.get("symbol", ""))
    thresh = get_limit_threshold_db(symbol)

    return pct_change >= thresh * 0.95


# ── 情绪评分函数 ─────────────────────────────────────────────────────

def calc_limit_up_score(limit_up_count: int) -> int:
    """涨停数评分（权重30%）：>200=100, >100=70, >50=40, else=20"""
    if limit_up_count > 200:
        return 100
    if limit_up_count > 100:
        return 70
    if limit_up_count > 50:
        return 40
    return 20


def calc_adv_decl_score(ratio: float) -> int:
    """涨跌比评分（权重25%）：>2=100, >1=70, >0.5=40, else=20"""
    if ratio > 2:
        return 100
    if ratio > 1:
        return 70
    if ratio > 0.5:
        return 40
    return 20


def calc_bust_score(bust_ratio: float) -> int:
    """炸板率评分（权重20%，越低越好）：<20%=100, <30%=80, <40%=60, else=20"""
    if bust_ratio < 0.20:
        return 100
    if bust_ratio < 0.30:
        return 80
    if bust_ratio < 0.40:
        return 60
    return 20


def calc_board_score(max_board: int) -> int:
    """连板高度评分（权重15%）：>5=100, >3=70, >1=40, else=20"""
    if max_board > 5:
        return 100
    if max_board > 3:
        return 70
    if max_board > 1:
        return 40
    return 20


def calc_premium_score(premium: float) -> int:
    """涨停溢价评分（权重10%）：>5%=100, >2%=70, >0%=50, else=20"""
    if premium > 5:
        return 100
    if premium > 2:
        return 70
    if premium > 0:
        return 50
    return 20


def map_sentiment_label(score: float) -> str:
    """
    综合评分映射到情绪等级。

    [0,30)恐惧 [30,50)悲观 [50,70)中性 [70,85)乐观 [85,100]贪婪
    """
    if score < 30:
        return "恐惧"
    if score < 50:
        return "悲观"
    if score < 70:
        return "中性"
    if score < 85:
        return "乐观"
    return "贪婪"


# ── 数据获取 ─────────────────────────────────────────────────────────

def fetch_spot_data() -> pd.DataFrame:
    """
    拉取全量A股实时行情数据。

    使用 akshare.stock_zh_a_spot() 从 Sina 源获取，
    内部自动分69页拉取，带 tqdm 进度条。

    DataFrame 字段：
        代码, 名称, 最新价, 涨跌额, 涨跌幅, 买入, 卖出,
        昨收, 今开, 最高, 最低, 成交量, 成交额, 时间戳

    返回:
        包含全部A股（约5500只）实时行情的 DataFrame
    """
    print("[sentiment] 拉取全量A股行情...")
    df = ak.stock_zh_a_spot()
    print(f"[sentiment] 获取到 {len(df)} 只股票")
    return df


# ── 连板高度查询（从 screener.db） ──────────────────────────────────

def query_board_height_and_premium() -> tuple[int, float]:
    """
    从 screener.db 查询最高连板高度和昨日涨停溢价。

    连板高度计算方式：
    1. 获取最近 BOARD_LOOKBACK_DAYS 天的日线数据
    2. 对每只股票，计算每日涨幅并判断是否涨停
    3. 统计每只股票的连续涨停天数（含当日）
    4. 返回全局最大值

    昨日涨停溢价计算方式：
    1. 找出昨日涨停的股票
    2. 计算这些股票今日的平均涨幅
    3. 返回均值（异常值会被截断）

    返回:
        (max_board_height, avg_limit_up_premium)
        - max_board_height: 最高连板数（至少 1）
        - avg_limit_up_premium: 昨日涨停股今日平均涨幅(%)
    """
    try:
        today_str = date.today().isoformat()
        yesterday_str = (date.today() - timedelta(days=1)).isoformat()
        lookback_start = (date.today() - timedelta(days=BOARD_LOOKBACK_DAYS)).isoformat()

        if not SCREENER_DB.exists():
            print(f"[sentiment] 数据库不存在: {SCREENER_DB}", file=sys.stderr)
            return 1, 0.0

        conn = sqlite3.connect(str(SCREENER_DB))

        # 使用 LAG 窗口函数获取前一交易日收盘价
        # 以及前两日收盘价（用于判断昨日是否涨停）
        query = """
        WITH daily_lag AS (
            SELECT
                symbol,
                trade_date,
                close,
                high,
                open,
                LAG(close) OVER (
                    PARTITION BY symbol
                    ORDER BY trade_date
                ) AS prev_close,
                LAG(close, 2) OVER (
                    PARTITION BY symbol
                    ORDER BY trade_date
                ) AS prev_prev_close
            FROM stock_daily
            WHERE trade_date >= ? AND trade_date <= ?
        )
        SELECT *
        FROM daily_lag
        WHERE prev_close IS NOT NULL
        ORDER BY symbol, trade_date
        """
        df = pd.read_sql(query, conn, params=[lookback_start, today_str])
        conn.close()

        if df.empty:
            print("[sentiment] 数据库无数据，连板高度和溢价返回默认值")
            return 1, 0.0

        # ── 连板高度计算 ──
        # 标记每个交易日是否为涨停
        df["is_limit_up"] = df.apply(_detect_db_limit_up, axis=1)

        # 按股票分组，排序后找出最长连续涨停
        # 只统计包含今日涨停的连续序列
        max_streak = 0
        for symbol, group in df.groupby("symbol"):
            group = group.sort_values("trade_date")
            current_streak = 0
            has_today = False
            for _, row in group.iterrows():
                if row["is_limit_up"]:
                    current_streak += 1
                    if row["trade_date"] == today_str:
                        has_today = True
                else:
                    current_streak = 0
                if has_today and current_streak > max_streak:
                    max_streak = current_streak

        if max_streak == 0:
            max_streak = 1  # 至少1板

        # ── 昨日涨停溢价计算 ──
        df["was_limit_up_yesterday"] = df.apply(_detect_db_limit_up_yesterday, axis=1)

        # 昨日涨停且昨日有数据
        yesterday_limit = df[
            (df["trade_date"] == yesterday_str) & (df["was_limit_up_yesterday"] == True)
        ]

        # 今日有数据的股票
        today_data = df[df["trade_date"] == today_str]

        avg_premium = 0.0
        if not yesterday_limit.empty and not today_data.empty:
            merged = yesterday_limit.merge(
                today_data[["symbol", "close", "prev_close"]],
                on="symbol",
                suffixes=("_yest", "_today"),
            )
            if not merged.empty:
                merged["pct_change"] = (
                    (merged["close_today"] / merged["prev_close_today"] - 1.0) * 100.0
                )
                pct_changes = merged["pct_change"]
                # 过滤异常值（超过±30%可能是数据问题）
                pct_changes = pct_changes[
                    (pct_changes > -20.0) & (pct_changes < 30.0)
                ]
                if not pct_changes.empty:
                    avg_premium = float(pct_changes.mean())

        return max_streak, round(avg_premium, 2)

    except sqlite3.OperationalError as e:
        print(f"[sentiment] 数据库查询错误: {e}", file=sys.stderr)
        return 1, 0.0
    except Exception as e:
        print(f"[sentiment] 连板查询失败: {e}", file=sys.stderr)
        return 1, 0.0


# ── 行情分析计算 ─────────────────────────────────────────────────────

def analyze_spot_data(df: pd.DataFrame) -> dict:
    """
    分析全量A股行情，计算各项市场指标。

    分析内容：
    1. 涨停/跌停统计（含炸板）
    2. 上涨/下跌/平盘家数
    3. 炸板率和涨跌比率
    4. 涨停封板率

    参数:
        df: stock_zh_a_spot 返回的 DataFrame

    返回:
        dict 包含 limit_up_count, limit_down_count, limit_up_busted,
             bust_ratio, up_count, down_count, adv_decl_ratio
    """
    # 涨停检测（含不同板块涨跌幅限制判断）
    limit_up_mask = df.apply(is_limit_up, axis=1)
    limit_down_mask = df.apply(is_limit_down, axis=1)
    busted_mask = df.apply(is_limit_up_busted, axis=1)

    limit_up_count = int(limit_up_mask.sum())
    limit_down_count = int(limit_down_mask.sum())
    limit_up_busted = int(busted_mask.sum())

    # 涨跌统计
    up_count = int((df["涨跌幅"] > 0).sum())
    down_count = int((df["涨跌幅"] < 0).sum())
    flat_count = int((df["涨跌幅"] == 0).sum())

    # 炸板率 = 炸板数 / (涨停数 + 炸板数)
    total_touched = limit_up_count + limit_up_busted
    bust_ratio = round(limit_up_busted / total_touched, 4) if total_touched > 0 else 0.0

    # 涨跌比
    adv_decl_ratio = round(up_count / down_count, 4) if down_count > 0 else round(float(up_count), 4)

    return {
        "limit_up_count": limit_up_count,
        "limit_down_count": limit_down_count,
        "limit_up_busted": limit_up_busted,
        "bust_ratio": bust_ratio,
        "advancer_count": up_count,
        "decliner_count": down_count,
        "adv_decl_ratio": adv_decl_ratio,
    }


# ── 综合评分 ─────────────────────────────────────────────────────────

def compute_sentiment_score(
    limit_up_count: int,
    adv_decl_ratio: float,
    bust_ratio: float,
    max_board_height: int,
    avg_premium: float,
) -> tuple[float, str]:
    """
    加权计算综合情绪评分。

    权重：涨停数30% + 涨跌比25% + 炸板率20% + 连板高度15% + 涨停溢价10%
    """
    scores = {
        "limit_up": calc_limit_up_score(limit_up_count) * 0.30,
        "adv_decl": calc_adv_decl_score(adv_decl_ratio) * 0.25,
        "bust": calc_bust_score(bust_ratio) * 0.20,
        "board": calc_board_score(max_board_height) * 0.15,
        "premium": calc_premium_score(avg_premium) * 0.10,
    }

    total_score = round(sum(scores.values()), 2)
    label = map_sentiment_label(total_score)

    return total_score, label


# ── 保存与输出 ───────────────────────────────────────────────────────

def save_result(result: dict):
    """
    将结果保存为 JSON 文件。

    File path: {OUTPUT_DIR}/{date}.json
    目录不存在会自动创建。JSON 使用 UTF-8 编码和 indent=2
    格式化，便于人工阅读和版本管理。

    Parameters:
        result: 包含所有情绪指标的字典
    """
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    filepath = OUTPUT_DIR / f"{result['date']}.json"
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print(f"\n✅ 已保存: {filepath}")


def print_summary(result: dict):
    """
    终端打印结果摘要。

    显示内容：
    - 情绪评分与等级
    - 涨停/跌停/炸板统计
    - 上涨/下跌/涨跌比
    - 最高连板
    - 昨日涨停溢价

    Parameters:
        result: 包含所有情绪指标的字典
    """
    bust_pct = round(result["bust_ratio"] * 100, 1)
    print(f"\n   情绪评分: {result['sentiment_score']}/100 — {result['sentiment_label']}")
    print(
        f"   涨停: {result['limit_up_count']} | 跌停: {result['limit_down_count']} "
        f"| 炸板: {result['limit_up_busted']} ({bust_pct}%)"
    )
    print(
        f"   上涨: {result['advancer_count']} | 下跌: {result['decliner_count']} "
        f"| 涨跌比: {result['adv_decl_ratio']:.2f}"
    )
    print(f"   最高连板: {result['max_consecutive_boards']} 板")
    print(f"   昨日涨停溢价: {result['avg_limit_up_premium']:.2f}%")


# ── 主流程 ───────────────────────────────────────────────────────────

def run() -> dict:
    """
    执行情绪采集主流程。

    Pipeline:
    1. fetch_spot_data() 拉取全量A股行情数据（akshare stock_zh_a_spot, 69页）
    2. analyze_spot_data() 分析行情数据（涨停/跌停/炸板/涨跌统计）
    3. query_board_height_and_premium() 查询连板高度和昨日涨停溢价
    4. compute_sentiment_score() 根据五项指标计算综合情绪评分
    5. 组装结果字典并返回

    Returns:
        dict with keys: date, limit_up_count, limit_down_count, limit_up_busted,
        bust_ratio, avg_limit_up_premium, max_consecutive_boards, advancer_count,
        decliner_count, adv_decl_ratio, sentiment_score, sentiment_label, generated_at
    """
    now = datetime.now()
    today_str = date.today().isoformat()

    # 步骤1: 拉取全量行情
    df = fetch_spot_data()

    # 步骤2: 行情分析
    analysis = analyze_spot_data(df)

    # 步骤3: 连板高度 & 昨日涨停溢价
    max_board_height, avg_premium = query_board_height_and_premium()

    # 步骤4: 综合情绪评分
    total_score, sentiment_label = compute_sentiment_score(
        limit_up_count=analysis["limit_up_count"],
        adv_decl_ratio=analysis["adv_decl_ratio"],
        bust_ratio=analysis["bust_ratio"],
        max_board_height=max_board_height,
        avg_premium=avg_premium,
    )

    # 步骤5: 组装结果
    result = {
        "date": today_str,
        "limit_up_count": analysis["limit_up_count"],
        "limit_down_count": analysis["limit_down_count"],
        "limit_up_busted": analysis["limit_up_busted"],
        "bust_ratio": analysis["bust_ratio"],
        "avg_limit_up_premium": avg_premium,
        "max_consecutive_boards": max_board_height,
        "advancer_count": analysis["advancer_count"],
        "decliner_count": analysis["decliner_count"],
        "adv_decl_ratio": analysis["adv_decl_ratio"],
        "sentiment_score": total_score,
        "sentiment_label": sentiment_label,
        "generated_at": now.strftime("%Y-%m-%d %H:%M:%S"),
    }

    return result


# ── 独立运行入口 ─────────────────────────────────────────────────────

if __name__ == "__main__":
    now = datetime.now()
    print("=" * 50)
    print(f"  市场情绪采集器 | {now.strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 50)

    result = run()
    save_result(result)
    print_summary(result)

    # 持久化到 Dashboard DB
    try:
        from report_store import store_sentiment
        total_stocks = 5514  # 近似全量A股数量
        store_sentiment(
            date=result["date"],
            score=int(result["sentiment_score"]),
            limit_up_count=result["limit_up_count"],
            limit_up_rate=round(result["limit_up_count"] / total_stocks, 4),
            details={
                "涨停家数": result["limit_up_count"],
                "跌停家数": result["limit_down_count"],
                "炸板数": result["limit_up_busted"],
                "炸板率": result["bust_ratio"],
                "上涨家数": result["advancer_count"],
                "下跌家数": result["decliner_count"],
                "涨跌比": result["adv_decl_ratio"],
                "昨日涨停溢价": result["avg_limit_up_premium"],
                "最高连板": result["max_consecutive_boards"],
                "情绪标签": result["sentiment_label"],
            },
        )
    except Exception as e:
        print(f"[report_store] persist failed (non-fatal): {e}")
