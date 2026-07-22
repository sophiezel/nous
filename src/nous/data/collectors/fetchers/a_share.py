"""A股数据获取：新浪行情 + akshare 日线
双源模式: Sina 主源 + 腾讯第二源（按需交叉验证）
"""
import re
import time
import json
from pathlib import Path
from datetime import date, timedelta
from typing import Optional

import akshare as ak
import pandas as pd

from nous.data.collectors.fetchers.base import clean_session, fetch_json, fetch_text
from nous.data import storage


# ── 股票列表 ──────────────────────────────────

def fetch_all_symbols() -> pd.DataFrame:
    """
    获取全量 A 股股票列表（代码 + 名称）。
    数据源：akshare stock_zh_a_spot（新浪分页，69页，约1分钟）
    返回 DataFrame: [symbol, name]
    """
    print("  获取A股列表（新浪分页，预计 ~60s）...")
    df = ak.stock_zh_a_spot()
    # stock_zh_a_spot 字段: 代码, 名称
    # 代码格式如 'sh600519'
    df = df.rename(columns={"代码": "raw_symbol", "名称": "name"})
    df["symbol"] = df["raw_symbol"].str[2:]  # 去掉 sh/sz 前缀
    df["market"] = "a"
    print(f"  获取到 {len(df)} 只A股")
    return df[["symbol", "name", "market"]]


def update_stock_list(cfg: dict):
    """增量更新股票列表到 SQLite"""
    existing = {s["symbol"] for s in storage.list_symbols("a")}
    df = fetch_all_symbols()
    new_stocks = [(r["symbol"], r["name"], r["market"]) for _, r in df.iterrows()
                  if r["symbol"] not in existing]
    if new_stocks:
        storage.upsert_stocks(new_stocks)
        print(f"  新增 {len(new_stocks)} 只股票")
    else:
        print("  股票列表已是最新")


# ── 日线数据 ──────────────────────────────────

def _resolve_prefix(symbol: str) -> str:
    """根据股票代码返回市场前缀 (sh/sz/bj)"""
    if symbol.startswith(("92", "83", "87")):
        return "bj"
    elif symbol.startswith(("6", "9")):
        return "sh"
    else:
        return "sz"


def _normalize_daily_df(df: pd.DataFrame, days: int = 120) -> pd.DataFrame:
    """将 akshare 返回的 DataFrame 标准化为统一列名格式。

    处理中英文列名，缺失列补 None。
    返回: [trade_date, open, high, low, close, volume, amount]
    """
    if df is None or df.empty:
        return pd.DataFrame(columns=[
            "trade_date", "open", "high", "low", "close", "volume", "amount"
        ])
    col_map = {"日期": "trade_date", "date": "trade_date",
               "开盘": "open", "open": "open",
               "收盘": "close", "close": "close",
               "最高": "high", "high": "high",
               "最低": "low", "low": "low",
               "成交量": "volume", "volume": "volume",
               "成交额": "amount", "amount": "amount"}
    rename = {k: v for k, v in col_map.items() if k in df.columns}
    if rename:
        df = df.rename(columns=rename)
    std_cols = ["trade_date", "open", "high", "low", "close", "volume", "amount"]
    for c in std_cols:
        if c not in df.columns:
            df[c] = None
    df["trade_date"] = pd.to_datetime(df["trade_date"]).dt.date
    df = df[std_cols]
    return df.tail(days)


def fetch_daily(symbol: str, days: int = 120, use_polars: bool = False):
    """
    获取单只股票日线（前复权）。
    数据源：akshare stock_zh_a_daily（新浪日线，含全量历史）
    返回 DataFrame: [trade_date, open, high, low, close, volume, amount]
    内置 10s 超时保护 + 数据源健康追踪。

    当 use_polars=True 时返回 pl.DataFrame（Polars），否则返回 pd.DataFrame。
    """
    prefix = _resolve_prefix(symbol)
    full_symbol = f"{prefix}{symbol}"
    import signal
    old_handler = signal.getsignal(signal.SIGALRM)
    def timeout_handler(signum, frame):
        raise TimeoutError(f"fetch_daily({symbol}) 超时10s")
    signal.signal(signal.SIGALRM, timeout_handler)
    signal.alarm(10)

    t_start = time.time()
    try:
        df = ak.stock_zh_a_daily(symbol=full_symbol, adjust="qfq")
        latency = (time.time() - t_start) * 1000
        # 健康追踪: 记录成功
        try:
            from nous.data.quality import health_tracker
            health_tracker.record_success("sina", latency_ms=latency)
        except ImportError:
            pass

        if use_polars:
            from nous.data.etl.polars_pipeline import daily_to_polars
            pl_df = daily_to_polars(df).tail(days)
            return pl_df
        else:
            return _normalize_daily_df(df, days)
    except Exception as e:
        latency = (time.time() - t_start) * 1000
        # 健康追踪: 记录失败
        try:
            from nous.data.quality import health_tracker
            health_tracker.record_failure("sina", error=str(e)[:200], latency_ms=latency)
        except ImportError:
            pass
        raise
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, old_handler)


# ── 腾讯源日线（第二源）───────────────────────

def fetch_tx_daily(symbol: str, days: int = 1) -> pd.DataFrame:
    """
    获取腾讯源 A 股日线（前复权）。
    数据源：akshare stock_zh_a_hist_tx（腾讯证券）
    返回 DataFrame: [trade_date, open, high, low, close, volume, amount]
    - volume 列填 0（腾讯源不提供成交量，保留列名兼容）
    - 集成 rate_limiter 'tencent' 桶 (rate=5, capacity=5)
    - 内置 10s SIGALRM 超时保护
    - 包含数据源健康追踪

    Args:
        symbol: 股票代码（纯数字，如 '000001'）
        days: 返回最近 N 条记录

    Returns:
        标准化日线 DataFrame
    """
    prefix = _resolve_prefix(symbol)
    full_symbol = f"{prefix}{symbol}"

    # 令牌桶限流
    from nous.data.collectors.rate_limiter import acquire_with_multiplier
    acquire_with_multiplier('tencent', 1, timeout=10)

    # 计算合理的起止日期（days * 2 个日历日 + 10天缓冲，避免全量拉取超时）
    calendar_days = max(10, int(days * 2.0) + 10)
    start_date = (date.today() - timedelta(days=calendar_days)).strftime("%Y%m%d")
    end_date = date.today().strftime("%Y%m%d")

    import signal
    old_handler = signal.getsignal(signal.SIGALRM)
    def timeout_handler(signum, frame):
        raise TimeoutError(f"fetch_tx_daily({symbol}) 超时10s")
    signal.signal(signal.SIGALRM, timeout_handler)
    signal.alarm(10)

    t_start = time.time()
    try:
        df = ak.stock_zh_a_hist_tx(
            symbol=full_symbol,
            start_date=start_date,
            end_date=end_date,
            adjust="qfq",
        )
        latency = (time.time() - t_start) * 1000
        # 健康追踪: 记录成功
        try:
            from nous.data.quality import health_tracker
            health_tracker.record_success("tencent", latency_ms=latency)
        except ImportError:
            pass

        # 列映射: 腾讯源返回 date/open/close/high/low/amount（缺 volume）
        col_map = {"date": "trade_date", "open": "open", "close": "close",
                   "high": "high", "low": "low", "amount": "amount"}
        rename = {k: v for k, v in col_map.items() if k in df.columns}
        if rename:
            df = df.rename(columns=rename)
        std_cols = ["trade_date", "open", "high", "low", "close", "volume", "amount"]
        for c in std_cols:
            if c not in df.columns:
                df[c] = None
        # volume 腾讯不提供，填 0
        df["volume"] = 0
        df["trade_date"] = pd.to_datetime(df["trade_date"]).dt.date
        df = df[std_cols]
        return df.tail(days)
    except Exception as e:
        latency = (time.time() - t_start) * 1000
        # 健康追踪: 记录失败
        try:
            from nous.data.quality import health_tracker
            health_tracker.record_failure("tencent", error=str(e)[:200], latency_ms=latency)
        except ImportError:
            pass
        raise
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, old_handler)


# ── 双源交叉验证 ──────────────────────────────

def fetch_daily_dual(symbol: str, days: int = 120, use_polars: bool = False):
    """
    双源交叉验证日线获取。

    策略:
      1. 先尝试 Sina 主源（约 70% 的情况下腾讯可跳过，降低开销）
      2. 若 Sina 成功且返回非空，则仅用 Sina 数据（单源模式，无分歧）
      3. 若 Sina 失败或为空，则触发腾讯源作为降级
      4. 使用 src.collectors.multi_source.median_consensus 合并双源

    Args:
        symbol: 股票代码（纯数字）
        days: 返回最近 N 条记录
        use_polars: 是否返回 Polars DataFrame

    Returns:
        pd.DataFrame 或 pl.DataFrame，列 [trade_date, open, high, low, close, volume, amount]
    """
    import signal as _sig
    _old_handler = _sig.getsignal(_sig.SIGALRM)
    try:
        _sig.alarm(10)

        # ── 第一步: 尝试 Sina ──
        sina_df = None
        sina_ok = False
        try:
            raw_sina = ak.stock_zh_a_daily(
                symbol=f"{_resolve_prefix(symbol)}{symbol}",
                adjust="qfq",
            )
            sina_df = _normalize_daily_df(raw_sina, days)
            sina_ok = sina_df is not None and not sina_df.empty
            try:
                from nous.data.quality import health_tracker
                health_tracker.record_success("sina")
            except ImportError:
                pass
        except Exception as e:
            try:
                from nous.data.quality import health_tracker
                health_tracker.record_failure("sina", error=str(e)[:200])
            except ImportError:
                pass
            sina_ok = False

        # ── Sina 成功且有数据 → 直接返回（跳过腾讯，降低 70% 开销）──
        if sina_ok:
            if use_polars:
                from nous.data.etl.polars_pipeline import daily_to_polars
                return daily_to_polars(sina_df).tail(days)
            return sina_df

        # ── 第二步: Sina 失败 → 按需激活腾讯源 ──
        tencent_df = None
        tencent_ok = False
        try:
            # 腾讯源按需请求，计算合理日期范围
            tx_cal_days = max(10, int(days * 2.0) + 10)
            tx_start = (date.today() - timedelta(days=tx_cal_days)).strftime("%Y%m%d")
            tx_end = date.today().strftime("%Y%m%d")
            raw_tx = ak.stock_zh_a_hist_tx(
                symbol=f"{_resolve_prefix(symbol)}{symbol}",
                start_date=tx_start,
                end_date=tx_end,
                adjust="qfq",
            )
            if raw_tx is not None and not raw_tx.empty:
                col_map = {"date": "trade_date", "open": "open", "close": "close",
                           "high": "high", "low": "low", "amount": "amount"}
                rename = {k: v for k, v in col_map.items() if k in raw_tx.columns}
                if rename:
                    raw_tx = raw_tx.rename(columns=rename)
                std_cols = ["trade_date", "open", "high", "low", "close", "volume", "amount"]
                for c in std_cols:
                    if c not in raw_tx.columns:
                        raw_tx[c] = None
                raw_tx["volume"] = 0
                raw_tx["trade_date"] = pd.to_datetime(raw_tx["trade_date"]).dt.date
                tencent_df = raw_tx[std_cols].tail(days)
                tencent_ok = not tencent_df.empty
                try:
                    from nous.data.quality import health_tracker
                    health_tracker.record_success("tencent")
                except ImportError:
                    pass
        except Exception as e:
            try:
                from nous.data.quality import health_tracker
                health_tracker.record_failure("tencent", error=str(e)[:200])
            except ImportError:
                pass
            tencent_ok = False

        if tencent_ok:
            if use_polars:
                from nous.data.etl.polars_pipeline import daily_to_polars
                return daily_to_polars(tencent_df).tail(days)
            return tencent_df

        # ── 双源都失败 ──
        raise RuntimeError(f"双源均失败: Sina={'失败' if not sina_ok else '空数据'}, "
                           f"腾讯={'失败' if not tencent_ok else '空数据'}")

    finally:
        _sig.alarm(0)
        _sig.signal(_sig.SIGALRM, _old_handler)


# ── 东方财富 EM 日线（第三源，当前不可用）────────

def fetch_em_daily(symbol: str, days: int = 1) -> pd.DataFrame:
    """获取 EM 源 A 股日线（第二源，当前不可用）。

    调研结果：东方财富 EM 的日线 K 线数据通过
    push2his.eastmoney.com 提供，该子域在当前代理环境下被拦截。
    datacenter 子域不提供 K 线数据。

    Raises:
        RuntimeError: 始终抛出，提示 EM 日线源当前不可用。

    返回格式（预期，与 fetch_daily 一致）：
        [trade_date, open, high, low, close, volume, amount]
    """
    from nous.data.collectors.rate_limiter import acquire_with_multiplier
    acquire_with_multiplier('em_datacenter', 1, timeout=5)

    # ── 方案 A: akshare stock_zh_a_hist (push2his, 被拦截) ──
    try:
        if symbol.startswith(("92", "83", "87")):
            full_symbol = symbol  # 北交所直接用代码
        elif symbol.startswith(("6", "9")):
            full_symbol = f"sh{symbol}"
        else:
            full_symbol = f"sz{symbol}"
        df = ak.stock_zh_a_hist(symbol=full_symbol, period="daily",
                                adjust="qfq", timeout=10)
        if df is not None and not df.empty:
            col_map = {"日期": "trade_date", "开盘": "open", "收盘": "close",
                       "最高": "high", "最低": "low",
                       "成交量": "volume", "成交额": "amount"}
            rename = {k: v for k, v in col_map.items() if k in df.columns}
            if rename:
                df = df.rename(columns=rename)
            std_cols = ["trade_date", "open", "high", "low", "close", "volume", "amount"]
            for c in std_cols:
                if c not in df.columns:
                    df[c] = None
            df["trade_date"] = pd.to_datetime(df["trade_date"]).dt.date
            df = df[std_cols]
            return df.tail(days)
    except Exception:
        pass  # 降级到方案 B

    # ── 方案 B: EM datacenter 直连（无可用日线报表） ──
    raise RuntimeError(
        "EM日线源当前不可用：push2his.eastmoney.com 被代理拦截，"
        "datacenter 子域无日线K线报表。"
        "请检查代理规则或使用 Sina 源 (fetch_daily)。"
    )


# ── 日线批量更新（双源）────────────────────────

def update_all_daily(market: str = "a", full: bool = False, cfg: Optional[dict] = None):
    """
    批量更新日线数据（双源模式）。
    - full=False: 只更新最近缺失的日线（增量）
    - full=True: 全量拉取
    - 策略：Sina 主源 + 腾讯按需激活（双源交叉验证）
    - 内置120分钟全局超时 + 心跳日志，防止中途卡死
    """
    batch_size = cfg.get("runtime", {}).get("update_batch_size", 20) if cfg else 20
    sleep_sec = cfg.get("runtime", {}).get("update_sleep_sec", 1.0) if cfg else 1.0

    symbols = [s["symbol"] for s in storage.list_symbols(market)
               if not s["symbol"].startswith(("5", "1"))]
    total = len(symbols)
    if total == 0:
        print("  股票列表为空，请先运行 update_stock_list")
        return

    import sys
    t0 = time.time()
    TIMEOUT_SEC = 7200  # 120分钟全局超时
    HEARTBEAT_EVERY = 200
    print(f"  共 {total} 只股票，批量大小={batch_size}，间隔={sleep_sec}s，超时={TIMEOUT_SEC//60}min")
    print("  数据源: Sina(主) + 腾讯(按需降级)")

    updated = 0
    skipped = 0
    failed = 0
    failed_symbols = []

    # 优先重试上次失败的标的
    retry_list_path = Path.home() / ".hermes" / "cache" / "daily_retry_list.json"
    if retry_list_path.exists():
        try:
            retry_symbols = json.loads(retry_list_path.read_text())
            if retry_symbols:
                symbols = retry_symbols + [s for s in symbols if s not in retry_symbols]
                print(f"  优先重试 {len(retry_symbols)} 只上次失败标的")
        except Exception:
            pass

    # 共享连接
    conn = storage.get_db(write=True)  # write=True → busy_timeout=30000, 防与其他cron写锁竞争
    from nous.data.etl.polars_pipeline import clean_and_store

    # ── 令牌桶速率控制 ──
    try:
        from nous.data.collectors.rate_limiter import acquire_with_multiplier
        RATE_LIMITED = True
    except ImportError:
        acquire_with_multiplier = lambda src, tokens, timeout: True
        RATE_LIMITED = False

    for i in range(0, total, batch_size):
        batch = symbols[i : i + batch_size]

        # 令牌桶: batch级速率控制（Sina 主源）
        if RATE_LIMITED:
            acquire_with_multiplier('sina', len(batch), timeout=60)

        for sym in batch:
            # 全局超时检查
            if time.time() - t0 > TIMEOUT_SEC:
                print(f"  ⚠️ 全局超时({TIMEOUT_SEC//60}min)，已处理 {i}/{total}，终止")
                print(f"  完成: 更新={updated} 跳过={skipped} 失败={failed}")
                conn.close()
                return

            try:
                # 增量模式：检查是否需要更新
                if not full:
                    latest = storage.get_latest_date(sym)
                    today = date.today()
                    if latest and latest >= today:
                        recent = storage.get_daily(sym, limit=1)
                        if recent and recent[0].get("close", 0) > 0:
                            skipped += 1
                            continue

                # 双源获取（Sina主+腾讯按需降级）
                import signal as _signal
                _old_handler = _signal.getsignal(_signal.SIGALRM)
                try:
                    _signal.alarm(10)
                    raw_df = _fetch_daily_dual_raw(sym)
                finally:
                    _signal.alarm(0)
                    _signal.signal(_signal.SIGALRM, _old_handler)

                if raw_df is not None and not raw_df.empty:
                    clean_and_store(raw_df, sym, conn)
                    updated += 1
                else:
                    failed += 1
                    failed_symbols.append(sym)
                    if failed <= 5 or failed % 500 == 0:
                        print(f"  [{sym}] 双源返回空数据")
            except Exception as e:
                failed += 1
                failed_symbols.append(sym)
                if failed <= 5 or failed % 500 == 0:
                    print(f"  [{sym}] 失败: {e}")

        # 进度汇报 + 心跳
        done = min(i + batch_size, total)
        if done % HEARTBEAT_EVERY < batch_size or updated > 0:
            elapsed = (time.time() - t0) / 60
            print(f"  [{elapsed:.0f}min] 进度: {done}/{total}  已更新={updated}  跳过={skipped}  失败={failed}")
            sys.stdout.flush()
        time.sleep(sleep_sec)

        # ── 分批提交：每处理约200只释放一次写锁，避免阻塞其他cron ──
        if done % 200 < batch_size and updated + skipped > 0:
            try:
                conn.commit()
                conn.close()
                conn = storage.get_db(write=True)
                # 同步持久化失败列表, 防止进程crash丢失
                if failed_symbols:
                    retry_list_path.parent.mkdir(parents=True, exist_ok=True)
                    retry_list_path.write_text(json.dumps(failed_symbols[:50]))
            except Exception as e:
                print(f"  ⚠️ 分批提交失败: {e}", file=sys.stderr)

    elapsed = (time.time() - t0) / 60
    print(f"  完成[{elapsed:.0f}min]: 更新={updated} 跳过={skipped} 失败={failed}")

    # 保存重试列表
    if failed_symbols:
        try:
            retry_list_path.parent.mkdir(parents=True, exist_ok=True)
            retry_list_path.write_text(json.dumps(failed_symbols[:50]))
        except Exception:
            pass
    elif retry_list_path.exists():
        retry_list_path.unlink()

    # ETL指标记录
    try:
        from nous.data.quality.etl_metrics import ETLSession
        with ETLSession("daily_update") as sess:
            sess.record_phase("total", rows=updated + skipped, failed=failed, duration_s=elapsed*60)
    except ImportError:
        pass

    conn.close()


def _fetch_daily_dual_raw(symbol: str) -> Optional[pd.DataFrame]:
    """update_all_daily 内部使用的双源原始获取。

    与 fetch_daily_dual 的区别：
    - 直接返回原始 DataFrame（不经过 polars 转换）
    - 由调用方负责 clean_and_store
    - 策略：先尝试 Sina，失败则降级到腾讯

    Returns:
        pd.DataFrame 或 None（双源均失败）
    """
    prefix = _resolve_prefix(symbol)

    # ── 第一步: 尝试 Sina 主源 ──
    try:
        df = ak.stock_zh_a_daily(symbol=f"{prefix}{symbol}", adjust="qfq")
        if df is not None and not df.empty:
            return df
    except Exception:
        pass

    # ── 第二步: Sina 失败 → 激活腾讯源 ──
    try:
        from nous.data.collectors.rate_limiter import acquire_with_multiplier
        acquire_with_multiplier('tencent', 1, timeout=5)

        # 腾讯源按需请求，默认取 250 个日历日（约 120 个交易日）
        tx_cal_days = 250
        tx_start = (date.today() - timedelta(days=tx_cal_days)).strftime("%Y%m%d")
        tx_end = date.today().strftime("%Y%m%d")
        df = ak.stock_zh_a_hist_tx(
            symbol=f"{prefix}{symbol}",
            start_date=tx_start,
            end_date=tx_end,
            adjust="qfq",
        )
        if df is not None and not df.empty:
            # 腾讯源缺 volume，补 0
            df["volume"] = 0
            return df
    except Exception:
        pass

    return None


# ── 基本面数据 ────────────────────────────────
# 东方财富 push2 API 提供 PE/PB/市值，但需要代理直连。
# 当前代理环境（global mode）下东方财富不可用，此函数暂时返回空。
# 待 Clash 加 DIRECT 规则后可启用。

def fetch_fundamentals_batch(symbols: list[str]) -> list[dict]:
    """
    批量获取基本面数据（PE/PB/总市值）。
    数据源：东方财富 push2 API（需要代理直连）
    当前不可用，返回空列表。
    TODO: 修复 Clash 代理后启用
    """
    return []  # placeholder


def fetch_fundamentals_single(symbol: str) -> Optional[dict]:
    """单只股票基本面（暂不可用）"""
    return None
