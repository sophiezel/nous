"""预编译 SQL 模板 — DuckDB 查询引擎专用

包含:
- MA_CROSS_SQL: 用 SQL 计算 MA 金叉（替代 Python 循环）
- ROLLING_METRICS_SQL: 批量计算多只股票滚动指标
- LATEST_ALIGNED_SQL: ASOF JOIN 对齐多股收盘价
"""

# ── MA 金叉 SQL ──────────────────────────────────────
# 用窗口函数批量计算 MA5/MA20 金叉，无需 Python 逐只计算
# :symbol, :short_win, :long_win
MA_CROSS_SQL = """
WITH ma AS (
    SELECT
        symbol,
        trade_date,
        close,
        AVG(close) OVER (
            PARTITION BY symbol ORDER BY trade_date
            ROWS BETWEEN {short_win}-1 PRECEDING AND CURRENT ROW
        ) AS ma_short,
        AVG(close) OVER (
            PARTITION BY symbol ORDER BY trade_date
            ROWS BETWEEN {long_win}-1 PRECEDING AND CURRENT ROW
        ) AS ma_long
    FROM hot.stock_daily
    WHERE symbol = ?
),
crosses AS (
    SELECT
        symbol,
        trade_date,
        ma_short,
        ma_long,
        LAG(ma_short) OVER (ORDER BY trade_date) AS prev_short,
        LAG(ma_long) OVER (ORDER BY trade_date) AS prev_long
    FROM ma
)
SELECT
    symbol,
    trade_date,
    CASE
        WHEN prev_short < prev_long AND ma_short > ma_long THEN 1
        ELSE 0
    END AS golden_cross
FROM crosses
WHERE ma_short IS NOT NULL AND ma_long IS NOT NULL
ORDER BY trade_date DESC
LIMIT {lookback}
"""

# ── 批量滚动指标 SQL ─────────────────────────────────
# 在一次查询中计算多只股票的 SMA20/SMA50/RSI14 等基础滚动指标
# :symbols, :days
ROLLING_METRICS_SQL = """
WITH ranked AS (
    SELECT
        symbol,
        trade_date,
        close,
        volume,
        ROW_NUMBER() OVER (
            PARTITION BY symbol ORDER BY trade_date DESC
        ) AS rn
    FROM hot.stock_daily
    WHERE symbol IN ({placeholders})
)
SELECT
    symbol,
    trade_date,
    close,
    volume,
    AVG(close) OVER (
        PARTITION BY symbol ORDER BY trade_date
        ROWS BETWEEN 19 PRECEDING AND CURRENT ROW
    ) AS sma20,
    AVG(close) OVER (
        PARTITION BY symbol ORDER BY trade_date
        ROWS BETWEEN 49 PRECEDING AND CURRENT ROW
    ) AS sma50,
    AVG(volume) OVER (
        PARTITION BY symbol ORDER BY trade_date
        ROWS BETWEEN 4 PRECEDING AND CURRENT ROW
    ) AS vol_ma5,
    AVG(volume) OVER (
        PARTITION BY symbol ORDER BY trade_date
        ROWS BETWEEN 19 PRECEDING AND CURRENT ROW
    ) AS vol_ma20
FROM ranked
WHERE rn <= {days}
ORDER BY symbol, trade_date
"""

# ── ASOF JOIN 对齐收盘价 ────────────────────────────
# 对齐多只股票在同一交易日的收盘价，用于相对强度排序
# DuckDB >=0.6 支持 ASOF JOIN
LATEST_ALIGNED_SQL = """
WITH ranked AS (
    SELECT
        symbol,
        trade_date,
        close,
        ROW_NUMBER() OVER (
            PARTITION BY symbol ORDER BY trade_date DESC
        ) AS rn
    FROM hot.stock_daily
    WHERE symbol IN ({placeholders})
)
SELECT symbol, trade_date, close
FROM ranked
WHERE rn = 1
ORDER BY close DESC
"""

# ── 批量获取最新收盘价 ───────────────────────────────
LATEST_CLOSE_SQL = """
WITH latest AS (
    SELECT
        symbol,
        trade_date,
        close,
        ROW_NUMBER() OVER (
            PARTITION BY symbol ORDER BY trade_date DESC
        ) AS rn
    FROM hot.stock_daily
    WHERE symbol IN ({placeholders})
)
SELECT symbol, close
FROM latest
WHERE rn = 1
"""

# ── 最近 X 日涨幅排序 ────────────────────────────────
RETURN_RANK_SQL = """
WITH ranked AS (
    SELECT
        symbol,
        trade_date,
        close,
        ROW_NUMBER() OVER (
            PARTITION BY symbol ORDER BY trade_date DESC
        ) AS rn
    FROM hot.stock_daily
    WHERE symbol IN ({placeholders})
),
latest AS (SELECT * FROM ranked WHERE rn = 1),
prev AS (SELECT * FROM ranked WHERE rn = {lookback})
SELECT
    l.symbol,
    l.trade_date,
    l.close AS close_now,
    p.close AS close_before,
    (l.close - p.close) / NULLIF(p.close, 0) * 100 AS ret_pct
FROM latest l
LEFT JOIN prev p ON l.symbol = p.symbol
ORDER BY ret_pct DESC
"""
