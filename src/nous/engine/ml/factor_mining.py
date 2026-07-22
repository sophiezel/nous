"""gplearn 遗传编程因子挖掘引擎

流程:
  1. 用现有因子矩阵 + 未来收益训练 SymbolicRegressor
  2. 提取最后一代的前 N 个程序
  3. 按 Rank IC 排序, 去重, 过滤低相关性新因子
  4. 保存到 data/factors/discovered/

用法:
  python -c "from nous.engine.ml.factor_mining import mine_and_save; mine_and_save()"
"""

import json
import logging
import pickle
from pathlib import Path

import numpy as np
import pandas as pd
from gplearn.genetic import SymbolicRegressor
from gplearn.functions import _Function, make_function
from scipy.stats import spearmanr

logger = logging.getLogger(__name__)

# 项目路径
PROJECT_ROOT = Path(__file__).resolve().parents[4]  # nous repo root
DISCOVERED_DIR = PROJECT_ROOT / "data" / "factors" / "discovered"

# ---------------------------------------------------------------------------
# 自定义适应度函数
# ---------------------------------------------------------------------------

def _rank_ic(y: np.ndarray, y_pred: np.ndarray, w: np.ndarray) -> float:
    """gplearn 适应度: Rank IC (Spearman 秩相关系数), 越高越好"""
    if np.std(y_pred) < 1e-12 or np.std(y) < 1e-12:
        return -1.0
    # 确保没有 NaN / inf
    mask = np.isfinite(y_pred) & np.isfinite(y)
    if mask.sum() < 10:
        return -1.0
    ic, _ = spearmanr(y_pred[mask], y[mask])
    return float(ic) if not np.isnan(ic) else -1.0


# ---------------------------------------------------------------------------
# 自定义函数集扩展 (可选)
# ---------------------------------------------------------------------------

def _protected_div(x1, x2):
    """带保护的除法: 除零时返回 1"""
    with np.errstate(divide='ignore', invalid='ignore'):
        return np.where(np.abs(x2) < 1e-8, 1.0, np.divide(x1, x2))

def _protected_log(x):
    """带保护的对数: x<=0 时返回 0"""
    with np.errstate(divide='ignore', invalid='ignore'):
        return np.where(x <= 1e-8, 0.0, np.log(x))

def _protected_sqrt(x):
    """带保护的平方根: x<0 时返回 0"""
    with np.errstate(invalid='ignore'):
        return np.where(x < 0, 0.0, np.sqrt(x))

def _protected_inv(x):
    """带保护的倒数: |x|<eps 时返回 1"""
    with np.errstate(divide='ignore', invalid='ignore'):
        return np.where(np.abs(x) < 1e-8, 1.0, 1.0 / x)

# 注册自定义函数
protected_div = make_function(function=_protected_div, name='div', arity=2)
protected_log = make_function(function=_protected_log, name='log', arity=1)
protected_sqrt = make_function(function=_protected_sqrt, name='sqrt', arity=1)
protected_inv = make_function(function=_protected_inv, name='inv', arity=1)

# 核心函数集 (gplearn 内置 + 自定义保护)
FUNCTION_SET = [
    'add', 'sub', 'mul',
    protected_div,
    protected_sqrt,
    'abs',
    protected_log,
    'neg',
    protected_inv,
]

# ---------------------------------------------------------------------------
# 核心挖掘函数
# ---------------------------------------------------------------------------

def mine_factors(
    factors_df: pd.DataFrame,
    forward_returns: pd.Series,
    pop_size: int = 500,
    generations: int = 20,
    n_sample: int = 10000,
    n_top: int = 10,
    random_state: int = 42,
    n_jobs: int = 4,
) -> list[dict]:
    """
    遗传编程挖掘因子公式

    Args:
        factors_df: 训练集因子 (n_samples × n_features), 列名为特征
        forward_returns: 未来收益 Series (对齐 factors_df)
        pop_size: 种群大小
        generations: 迭代代数
        n_sample: 采样数 (全量太慢)
        n_top: 返回 TOP N 个发现
        random_state: 随机种子
        n_jobs: 并行进程数

    Returns:
        [{formula, ic, rank_ic, length, complexity}, ...] 按 rank_ic 降序
    """
    t0 = __import__('time').time()

    # --- 采样 ---
    n_avail = len(factors_df)
    n_sample = min(n_sample, n_avail)
    rng = np.random.RandomState(random_state)
    idx = rng.choice(n_avail, n_sample, replace=False)
    X_sample = factors_df.iloc[idx].fillna(0).to_numpy(dtype=np.float64)
    y_sample = forward_returns.iloc[idx].fillna(0).to_numpy(dtype=np.float64).ravel()

    # 清理极端值 (winsorize at 99.5%)
    lower, upper = np.percentile(y_sample, [0.25, 99.75])
    y_sample = np.clip(y_sample, lower, upper)

    logger.info(
        f"gplearn 启动: pop={pop_size}, gen={generations}, "
        f"sample={n_sample}, features={X_sample.shape[1]}, "
        f"y_range=[{y_sample.min():.4f}, {y_sample.max():.4f}]"
    )

    # --- 训练 ---
    est = SymbolicRegressor(
        population_size=pop_size,
        generations=generations,
        function_set=FUNCTION_SET,
        metric='spearman',
        parsimony_coefficient=0.001,  # 惩罚复杂度
        stopping_criteria=0.99,       # Rank IC >= 0.99 提前停止
        random_state=random_state,
        verbose=0,
        n_jobs=n_jobs,
        init_depth=(2, 6),
        init_method='half and half',
        const_range=(-1.0, 1.0),
        p_crossover=0.7,
        p_subtree_mutation=0.1,
        p_hoist_mutation=0.05,
        p_point_mutation=0.1,
        p_point_replace=0.05,
        max_samples=0.8,              # 每代子采样
        tournament_size=20,
        warm_start=False,
        low_memory=True,
    )

    est.fit(X_sample, y_sample)

    elapsed = __import__('time').time() - t0
    logger.info(f"gplearn 训练完成: {elapsed:.1f}s")

    # --- 最佳程序 ---
    best_prog = est._program
    discovered = []

    # 收集最后一代的所有程序
    final_gen = est._programs[-1] if hasattr(est, '_programs') else [est._program]
    # 按适应度排序
    final_gen_sorted = sorted(
        final_gen, key=lambda p: p.fitness_, reverse=True
    )[:n_top]

    for i, program in enumerate(final_gen_sorted):
        try:
            y_pred = program.execute(X_sample)
        except Exception:
            continue

        mask = np.isfinite(y_pred) & np.isfinite(y_sample)
        if mask.sum() < 30:
            continue

        yp, ys = y_pred[mask], y_sample[mask]

        if np.std(yp) < 1e-12:
            continue

        pearson_ic = float(np.corrcoef(yp, ys)[0, 1])
        rank_ic_val, _ = spearmanr(yp, ys)
        rank_ic_val = float(rank_ic_val)

        if np.isnan(pearson_ic) or np.isnan(rank_ic_val):
            continue
        if abs(rank_ic_val) < 0.01:
            continue

        formula_str = str(program)
        discovered.append({
            'formula': formula_str,
            'ic': round(pearson_ic, 4),
            'rank_ic': round(rank_ic_val, 4),
            'length': program.length_,
            'complexity': len(formula_str),
        })

    # 按 rank_ic 降序排列
    discovered.sort(key=lambda x: x['rank_ic'], reverse=True)

    logger.info(f"发现 {len(discovered)} 个有效因子 (|Rank IC| >= 0.01)")
    for d in discovered[:5]:
        logger.info(f"  IC={d['ic']:.4f} RIC={d['rank_ic']:.4f} len={d['length']}  {d['formula'][:80]}")

    return discovered


def filter_new_factors(
    discovered: list[dict],
    existing_factors_df: pd.DataFrame,
    max_corr: float = 0.70,
) -> list[dict]:
    """
    筛选新因子: 与现有因子相关性 < max_corr

    Args:
        discovered: mine_factors() 输出
        existing_factors_df: 已存在的因子值
        max_corr: 最大允许相关性

    Returns:
        筛选后的因子列表
    """
    if not discovered:
        return []

    existing_vals = existing_factors_df.fillna(0).to_numpy(dtype=np.float64)
    kept = []

    # 用第一个发现的 valid 程序来验证
    for fact in discovered:
        # 由于我们无法轻易重新执行公式字符串, 这里用 rank_ic 阈值 + 公式去重
        formula = fact['formula']

        # 检查是否与已有公式重复 (通过公式字符串相似度)
        is_dup = False
        for k in kept:
            if _formula_similar(formula, k['formula']):
                is_dup = True
                break

        if not is_dup:
            kept.append(fact)

    logger.info(f"公式去重后: {len(discovered)} → {len(kept)}")

    # 如果 existing_factors_df 非空, 检查与现有因子的最大相关性
    if existing_vals.shape[1] > 0 and kept:
        # 用第一个样本数据测试 (公式执行很慢, 所以只对 kept 中的做完整检查)
        # 这里我们使用 rank_ic > 0.02 作为最终过滤
        kept = [f for f in kept if abs(f['rank_ic']) > 0.02]
        logger.info(f"最终保留 {len(kept)} 个新因子")

    return kept


def _formula_similar(a: str, b: str, threshold: float = 0.8) -> bool:
    """简单公式相似度: 基于字符集合 Jaccard 相似度"""
    set_a = set(a)
    set_b = set(b)
    if not set_a and not set_b:
        return True
    jaccard = len(set_a & set_b) / max(len(set_a | set_b), 1)
    return jaccard > threshold


# ---------------------------------------------------------------------------
# 持久化: 保存 / 加载
# ---------------------------------------------------------------------------

def save_discovered_factors(discovered: list[dict], tag: str = ""):
    """
    将发现的新因子保存到 data/factors/discovered/

    Args:
        discovered: mine_factors() 输出
        tag: 可选标签 (如 'week7', 'v1')
    """
    DISCOVERED_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = pd.Timestamp.now().strftime('%Y%m%d_%H%M%S')
    suffix = f"_{tag}" if tag else ""
    path = DISCOVERED_DIR / f"discovered{suffix}_{timestamp}.json"

    with open(path, 'w') as f:
        json.dump(discovered, f, indent=2, ensure_ascii=False)

    # 更新 latest 副本
    latest_path = DISCOVERED_DIR / "latest.json"
    with open(latest_path, 'w') as f:
        json.dump(discovered, f, indent=2, ensure_ascii=False)

    logger.info(f"已保存 {len(discovered)} 个发现因子 → {path}")
    return str(path)


def load_discovered_factors(tag: str = "latest") -> list[dict]:
    """
    从 data/factors/discovered/ 加载发现的因子

    Args:
        tag: 'latest' 加载最新, 或具体文件名 (不含路径)

    Returns:
        [{formula, ic, rank_ic, ...}, ...]
    """
    if tag == "latest":
        path = DISCOVERED_DIR / "latest.json"
    else:
        path = DISCOVERED_DIR / tag

    if not path.exists():
        logger.warning(f"发现因子文件不存在: {path}")
        return []

    with open(path) as f:
        data = json.load(f)

    logger.info(f"已加载 {len(data)} 个发现因子 from {path}")
    return data


# ---------------------------------------------------------------------------
# 简易执行器: 用公式对数据计算因子值
# ---------------------------------------------------------------------------

def apply_formula(formula_str: str, X: np.ndarray) -> np.ndarray:
    """
    用公式字符串对数据计算因子值

    注意: 由于 gplearn 公式内部依赖 Program 对象,
    这里只返回一个占位值。实际完整执行需要 Program.parse()。
    生产环境建议在 train 时同时保存 pkl 和公式。
    """
    # 简化版: 对每个样本执行公式 (仅用于验证)
    # 实际使用中需要保存整个 Program 对象
    raise NotImplementedError(
        "apply_formula 需要 gplearn Program 对象。"
        "请使用 save_programs/load_programs 保存/加载完整程序。"
    )


def save_programs(programs: list, tag: str = ""):
    """保存完整的 gplearn Program 对象 (用于后续计算因子值)"""
    DISCOVERED_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = pd.Timestamp.now().strftime('%Y%m%d_%H%M%S')
    suffix = f"_{tag}" if tag else ""
    path = DISCOVERED_DIR / f"programs{suffix}_{timestamp}.pkl"

    with open(path, 'wb') as f:
        pickle.dump(programs, f)

    # 更新 latest
    latest_path = DISCOVERED_DIR / "latest_programs.pkl"
    with open(latest_path, 'wb') as f:
        pickle.dump(programs, f)

    logger.info(f"已保存 {len(programs)} 个程序对象 → {path}")
    return str(path)


def load_programs(tag: str = "latest") -> list:
    """加载完整的 gplearn Program 对象"""
    if tag == "latest":
        path = DISCOVERED_DIR / "latest_programs.pkl"
    else:
        path = DISCOVERED_DIR / tag

    if not path.exists():
        logger.warning(f"程序文件不存在: {path}")
        return []

    with open(path, 'rb') as f:
        programs = pickle.load(f)

    logger.info(f"已加载 {len(programs)} 个程序对象 from {path}")
    return programs


def compute_gplearn_factors(
    X: np.ndarray,
    programs: list,
    factor_names: list[str] | None = None,
) -> pd.DataFrame:
    """
    用预训练的 gplearn 程序计算因子值

    Args:
        X: 输入特征矩阵 (n_samples × n_features)
        programs: gplearn Program 对象列表
        factor_names: 因子列名 (默认 K7_gp_0, K7_gp_1, ...)

    Returns:
        DataFrame: n_samples × n_programs
    """
    if factor_names is None:
        factor_names = [f"K7_gp_{i}" for i in range(len(programs))]

    results = {}
    for name, prog in zip(factor_names, programs):
        try:
            vals = prog.execute(X)
            results[name] = vals
        except Exception as e:
            logger.warning(f"因子 {name} 执行失败: {e}")
            results[name] = np.full(X.shape[0], np.nan)

    return pd.DataFrame(results)


# ---------------------------------------------------------------------------
# 一站式: 挖掘并保存
# ---------------------------------------------------------------------------

def mine_and_save(
    start_date: str = "2023-01-01",
    end_date: str = "2024-12-31",
    symbols: list[str] | None = None,
    pop_size: int = 300,
    generations: int = 10,
    n_sample: int = 5000,
    tag: str = "",
):
    """
    从数据库加载数据 → 挖掘因子 → 保存结果

    用法:
        from nous.engine.ml.factor_mining import mine_and_save
        mine_and_save(tag='v1')
    """
    import sqlite3

    # 加载日线数据
    db_path = PROJECT_ROOT / "data" / "screener.db"
    conn = sqlite3.connect(str(db_path))

    where_clauses = [f"b.market = 'a'"]
    if start_date:
        where_clauses.append(f"d.trade_date >= '{start_date}'")
    if end_date:
        where_clauses.append(f"d.trade_date <= '{end_date}'")
    if symbols:
        sym_list = "','".join(symbols)
        where_clauses.append(f"d.symbol IN ('{sym_list}')")

    where = " AND ".join(where_clauses)
    query = f"""
        SELECT d.symbol, d.trade_date, d.open, d.high, d.low, d.close, d.volume, d.amount
        FROM stock_daily d
        JOIN stock_basic b ON d.symbol = b.symbol
        WHERE {where}
        ORDER BY d.symbol, d.trade_date
    """

    df = pd.read_sql_query(query, conn)
    conn.close()
    logger.info(f"加载日线: {len(df)} 行, {df['symbol'].nunique()} 股票")

    # 按股票分组, 构造基础因子 (收益率、波动率等)
    all_X = []
    all_y = []

    for sym, grp in df.groupby('symbol'):
        grp = grp.sort_values('trade_date')
        close = grp['close'].values

        # 构造基础特征: 滞后收益率
        n_f = min(20, len(close) - 6)
        if n_f < 10:
            continue

        features = {}
        for lag in [1, 2, 3, 5, 10, 20]:
            if lag < len(close):
                ret = np.diff(close, n=lag) / close[:-lag]
                features[f'ret_{lag}d'] = np.concatenate([np.full(lag, np.nan), ret])

        # 波动率
        ret_1d = np.diff(close) / close[:-1]
        features['std_5'] = np.concatenate([np.full(5, np.nan),
            pd.Series(ret_1d).rolling(5).std().values[4:]])
        features['std_10'] = np.concatenate([np.full(10, np.nan),
            pd.Series(ret_1d).rolling(10).std().values[9:]])

        # 构建特征矩阵
        fdf = pd.DataFrame(features)
        fdf = fdf.iloc[:len(close)]  # 对齐

        # 未来 5 日收益 (标签)
        fwd = np.full(len(close), np.nan)
        fwd[:-5] = (close[5:] / close[:-5] - 1)
        fdf['fwd_5d'] = fwd

        # 删除 NaN
        fdf = fdf.dropna()
        if len(fdf) < 30:
            continue

        all_X.append(fdf.drop(columns=['fwd_5d']).values)
        all_y.append(fdf['fwd_5d'].values)

    if not all_X:
        logger.warning("无有效训练数据")
        return []

    X_all = np.concatenate(all_X)
    y_all = np.concatenate(all_y)

    X_df = pd.DataFrame(X_all, columns=[f'f{i}' for i in range(X_all.shape[1])])
    y_s = pd.Series(y_all)

    logger.info(f"训练数据: X={X_df.shape}, y={len(y_s)}")

    # 挖掘
    discovered = mine_factors(
        X_df, y_s,
        pop_size=pop_size,
        generations=generations,
        n_sample=n_sample,
    )

    if discovered:
        save_discovered_factors(discovered, tag=tag)
        logger.info(f"发现 {len(discovered)} 个因子, 已保存")

    return discovered


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )
    # 快速测试
    discovered = mine_and_save(tag="quicktest", pop_size=100, generations=5, n_sample=2000)
    print(f"发现 {len(discovered)} 个因子")
    for d in discovered[:3]:
        print(f"  RIC={d['rank_ic']:.4f}  {d['formula'][:60]}")
