"""Prefect 工作流: 因子研究管线
串联因子计算 → 模型训练 → SHAP 分析

用法:
    # 直接运行 (不需要 Prefect server)
    python src/qlib_research/prefect_flows.py
    
    # 指定参数
    python src/qlib_research/prefect_flows.py --limit 500 --forward 10
    
    # 跳过因子计算 (使用已有快照)
    python src/qlib_research/prefect_flows.py --skip-factors
    
    # 启动 Prefect UI (可选)
    prefect server start --port 4200 &
"""
import sys
from pathlib import Path
from datetime import date
from prefect import flow, task

sys.path.insert(0, str(Path(__file__).resolve().parents[4]  # nous repo root))


@task(retries=2, retry_delay_seconds=30, log_prints=True)
def compute_factors(limit: int = 0):
    """计算因子并保存快照
    
    Args:
        limit: 限制股票数量 (0=全量)
    
    Returns:
        因子快照路径
    """
    from nous.engine.ml.factor_compute import compute_all_factors, save_factor_snapshot
    import sqlite3
    
    print(f"[factor_compute] 开始, limit={limit}")
    
    # 解析 limit 参数: 获取 top N 股票
    symbols = None
    if limit > 0:
        from nous.engine.ml.factor_compute import DB_PATH
        conn = sqlite3.connect(str(DB_PATH))
        latest_full = conn.execute(
            "SELECT trade_date FROM stock_daily d JOIN stock_basic b ON d.symbol=b.symbol "
            "WHERE b.market='a' GROUP BY trade_date HAVING COUNT(*) > 1000 "
            "ORDER BY trade_date DESC LIMIT 1"
        ).fetchone()
        if latest_full:
            latest_date = latest_full[0]
            top = [r[0] for r in conn.execute(
                "SELECT d.symbol FROM stock_daily d JOIN stock_basic b ON d.symbol=b.symbol "
                "WHERE b.market='a' AND d.trade_date=? ORDER BY d.amount DESC LIMIT ?", 
                (latest_date, limit)
            ).fetchall()]
            symbols = top
            print(f"[factor_compute] limit={limit}: {len(top)}只 (基准日{latest_date})")
        else:
            print(f"[factor_compute] 无法确定 limit 基准日, 使用全量")
        conn.close()
    
    df = compute_all_factors(symbols=symbols)
    path = save_factor_snapshot(df)
    print(f"[factor_compute] 完成: {path}, {len(df)}行")
    return str(path)


@task(retries=1, retry_delay_seconds=10, log_prints=True)
def train_model(forward_period: int = 5, limit: int = 0):
    """训练 6 模型集成 (LightGBM + XGBoost + CatBoost + Ridge + MLP + Voting)

    Args:
        forward_period: 预测未来 N 日收益率
        limit: 限制股票数量 (0=全量)

    Returns:
        训练结果 dict, 包含各模型 ic, rank_ic 及最佳模型信息
    """
    from nous.engine.ml.model_ensemble import run_ensemble_pipeline
    print(f"[model_ensemble] 开始, forward_period={forward_period}, limit={limit}")
    results = run_ensemble_pipeline(limit=limit, forward_period=forward_period)
    if results:
        best = max(results.items(), key=lambda x: x[1]["ic"])
        print(f"[model_ensemble] 完成: 最佳模型={best[0]}, "
              f"IC={best[1]['ic']:.4f}, Rank IC={best[1]['rank_ic']:.4f}")
        # 构建兼容 summary
        summary = {
            "model": best[0],
            "ic": best[1]["ic"],
            "rank_ic": best[1]["rank_ic"],
            "models": {k: {"ic": v["ic"], "rank_ic": v["rank_ic"]}
                       for k, v in results.items()},
        }
        return summary
    else:
        print(f"[model_ensemble] 失败: 返回 None")
        return None


@task(log_prints=True)
def analyze_shap():
    """SHAP 因子可解释性分析"""
    from nous.engine.ml.shap_analysis import run_shap_analysis
    print("[shap] 开始")
    report = run_shap_analysis()
    top5 = report.get("top_10_factors", [])[:5] if report else []
    print(f"[shap] 完成: TOP5={top5}")
    return report


@flow(name="factor-research-pipeline", log_prints=True)
def factor_research_pipeline(
    limit: int = 0,
    forward_period: int = 5,
    skip_factors: bool = False,
):
    """
    因子研究完整管线:
      factor_compute → model_train → shap_analysis
    
    Args:
        limit: 限制股票数量 (0=全量)
        forward_period: 预测周期 (天)
        skip_factors: 跳过因子计算 (使用已有快照)
    """
    today = date.today().isoformat()
    print(f"{'='*50}")
    print(f"Factor Research Pipeline — {today}")
    print(f"{'='*50}")
    
    # Stage 1: 因子计算
    if not skip_factors:
        factor_path = compute_factors(limit=limit)
    else:
        print("[factor_compute] 跳过 (使用已有快照)")
    
    # Stage 2: 模型训练 (6模型集成)
    result = train_model(forward_period=forward_period, limit=limit)
    
    # Stage 3: SHAP 分析 (仅在模型质量达标时执行)
    if result and result.get("ic", 0) > 0.01:
        analyze_shap()
    else:
        print("[shap] 跳过 (IC过低或训练失败)")
    
    print(f"{'='*50}")
    print(f"Pipeline 完成")
    print(f"{'='*50}")
    
    return result


def add_cli_args():
    """为直接运行添加命令行参数解析"""
    import argparse
    parser = argparse.ArgumentParser(
        description="Prefect 工作流: 因子研究管线",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  %(prog)s                         # 全量因子计算 + 模型训练 + SHAP
  %(prog)s --limit 500              # 仅500只股票 (快速调试)
  %(prog)s --forward 10             # 预测10日收益率
  %(prog)s --skip-factors           # 跳过因子计算 (复用已有快照)
  %(prog)s --no-shap                # 跳过 SHAP 分析
        """,
    )
    parser.add_argument(
        "--limit", type=int, default=0,
        help="限制股票数量 (0=全量, 默认: %(default)s)"
    )
    parser.add_argument(
        "--forward", type=int, default=5, dest="forward_period",
        help="预测未来 N 日收益率 (默认: %(default)s)"
    )
    parser.add_argument(
        "--skip-factors", action="store_true",
        help="跳过因子计算, 使用已有快照"
    )
    parser.add_argument(
        "--no-shap", action="store_true",
        help="跳过 SHAP 分析"
    )
    return parser


if __name__ == "__main__":
    parser = add_cli_args()
    args = parser.parse_args()
    
    print(f"🚀 启动 Prefect 工作流: factor-research-pipeline")
    print(f"   参数: limit={args.limit}, forward={args.forward_period}, "
          f"skip_factors={args.skip_factors}")
    
    result = factor_research_pipeline(
        limit=args.limit,
        forward_period=args.forward_period,
        skip_factors=args.skip_factors,
    )
    
    if result:
        print(f"\n✅ Pipeline 成功")
        print(f"   最佳模型 = {result.get('model', 'N/A')}")
        print(f"   IC       = {result.get('ic', 'N/A'):.4f}")
        print(f"   Rank IC  = {result.get('rank_ic', 'N/A'):.4f}")
        if "models" in result:
            print(f"   各模型 IC:")
            for name, r in result["models"].items():
                print(f"      {name:<10s}: IC={r['ic']:.4f}, Rank IC={r['rank_ic']:.4f}")
    else:
        print(f"\n⚠️  Pipeline 完成但训练返回空结果")
