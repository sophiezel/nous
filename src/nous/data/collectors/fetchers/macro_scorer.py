#!/usr/bin/env python3
"""
宏观评分引擎 — 基于 akshare 宏观指标的综合评分系统

功能：
  从 akshare 拉取宏观经济指标（CPI/PPI/PMI/M2/LPR/SHIBOR/融资余额），
  计算综合评分（0-100），输出 JSON 到 ~/wiki/finance/raw/macro/macro_score.json。

评分维度：
  - CPI分：通胀水平评分（1-3% 为满分100，0-1% 为60分，负值为40分）
  - PPI分：工业品价格评分（>-2% 为60分，-2%~-5% 为40分，<-5% 为20分）
  - PMI分：制造业景气评分（>52 为100分，>50 为80分，<50 为40分）
  - 流动性分：M2 + SHIBOR + LPR 综合流动性评分（等权平均）
  - 融资情绪分：融资余额环比变化评分（基准50分，每变动1%约±32.5分）

周期判断：
  - expansion（扩张）: 总分 > 65
  - neutral（中性）: 总分 45-65
  - contraction（收缩）: 总分 < 45

风格偏好：
  - growth（成长）: CPI>0% 且 PPI>-2%
  - value（价值）: CPI>2% 或 PPI>1%
  - defensive（防御）: CPI<-0.5% 且 PPI<-5%
  - balanced（均衡）: 默认/其他情况

风险提示：
  - CPI < 0.5% → 通缩风险
  - PPI < -2% → 企业利润承压
  - PPI < -5% → 深度通缩
  - PMI < 50 → 经济收缩

独立运行：
  ~/.hermes/hermes-agent/venv/bin/python3 fetchers/macro_scorer.py

数据源：
  全部通过 akshare 使用 Sina 源获取，无需代理配置。

输出目录：
  ~/wiki/finance/raw/macro/macro_score.json
"""

from __future__ import annotations

import json
import os
import sys
import time
import traceback
from datetime import date, datetime
from pathlib import Path
from typing import Optional

import akshare as ak
import pandas as pd

# Ensure report_store module is importable from dashboard scripts
sys.path.insert(0, os.path.expanduser("~/code/dashboard/scripts"))

# ── 常量 ───────────────────────────────────────────

WIKI_RAW = os.path.expanduser("~/wiki/finance/raw/macro")
os.makedirs(WIKI_RAW, exist_ok=True)

TODAY = date.today().isoformat()
NOW = datetime.now()

OUTPUT_FILE = os.path.join(WIKI_RAW, "macro_score.json")

# 评分权重配置
SCORE_WEIGHTS = {
    "cpi": 0.15,
    "ppi": 0.15,
    "pmi": 0.25,
    "liquidity": 0.25,
    "margin": 0.20,
}

# 重试配置
FETCH_RETRIES = 2
FETCH_RETRY_DELAY = 2.0  # seconds


# ── 数据获取（带重试和异常保护）───────────────────


def _fetch_with_retry(fetch_func, *args, **kwargs):
    """通用带重试的数据获取装饰器"""
    last_error = None
    for attempt in range(1, FETCH_RETRIES + 2):
        try:
            return fetch_func(*args, **kwargs)
        except Exception as e:
            last_error = e
            if attempt <= FETCH_RETRIES:
                time.sleep(FETCH_RETRY_DELAY)
    raise last_error


def _fetch_cpi() -> tuple[float, str]:
    """
    获取 CPI 最新值及日期。

    注意：CPI 数据框按日期升序排列（最旧在前），
    且最新行可能含 NaN 值，需要过滤后取最后一个有效值。
    """
    df = ak.macro_china_cpi_yearly()
    # 过滤掉今值为 NaN 的行（最新月度数据可能尚未公布）
    df_valid = df[df["今值"].notna()]
    if len(df_valid) > 0:
        row = df_valid.iloc[-1]  # 取最后一个有效值
    else:
        row = df.iloc[-1]  # 兜底：全部无效时取最后一行
    val = float(row["今值"])
    if pd.isna(val):
        val = 0.0
    date_str = str(row["日期"])
    return val, date_str


def _fetch_ppi() -> tuple[float, str]:
    """
    获取 PPI 最新值及日期。

    CPI/PPI 数据源格式相同，处理逻辑一致。
    注意：同样需要过滤 NaN 值。
    """
    df = ak.macro_china_ppi_yearly()
    df_valid = df[df["今值"].notna()]
    if len(df_valid) > 0:
        row = df_valid.iloc[-1]
    else:
        row = df.iloc[-1]
    val = float(row["今值"])
    if pd.isna(val):
        val = 0.0
    date_str = str(row["日期"])
    return val, date_str


def _fetch_pmi() -> tuple[float, str]:
    """
    获取 PMI 制造业最新值及日期。

    注意：PMI 数据框按日期降序排列（最新在前），
    因此取 iloc[0]（索引0）而非 iloc[-1]。
    """
    df = ak.macro_china_pmi()
    # 数据框最新在前，取第一行
    row = df.iloc[0]
    val = float(str(row["制造业-指数"]))
    date_str = str(row["月份"])
    return val, date_str


def _fetch_m2() -> tuple[float, str]:
    """
    获取 M2 同比增长率及日期。

    注意：M2 数据框按日期降序排列（最新在前），
    因此取 iloc[0]（索引0）而非 iloc[-1]。
    """
    df = ak.macro_china_money_supply()
    row = df.iloc[0]
    val = float(str(row["货币和准货币(M2)-同比增长"]))
    date_str = str(row["月份"])
    return val, date_str


def _fetch_lpr() -> tuple[float, str]:
    """
    获取 LPR 1年期利率及日期。

    注意：LPR 数据框按日期升序排列（最旧在前），
    取 iloc[-1] 获取最新值。
    早期数据 LPR1Y 列可能为 NaN（LPR 改革前），
    需要确保取到有效行。
    """
    df = ak.macro_china_lpr()
    # 取最后一行的 LPR1Y 值（最早非 NaN 值）
    row = df.iloc[-1]
    val = float(str(row["LPR1Y"]))
    date_str = str(row["TRADE_DATE"])
    return val, date_str


def _fetch_shibor() -> tuple[float, str]:
    """
    获取 SHIBOR 隔夜利率及日期。

    注意：SHIBOR 数据框按日期升序排列（最旧在前），
    取 iloc[-1] 获取最新交易日的隔夜利率。
    """
    df = ak.macro_china_shibor_all()
    row = df.iloc[-1]
    val = float(str(row["O/N-定价"]))
    date_str = str(row["日期"])
    return val, date_str


def _fetch_margin() -> tuple[float, str, float]:
    """
    获取融资余额、日期及前值。

    返回 (当前融资余额, 日期字符串, 前一交易日融资余额)。
    注意：融资融券数据框按日期升序排列（最旧在前），
    iloc[-1] 为最新交易日，iloc[-2] 为前一交易日。
    """
    df = ak.macro_china_market_margin_sh()
    row = df.iloc[-1]
    val = float(str(row["融资余额"]))
    date_str = str(row["日期"])
    prev_val = float(str(df.iloc[-2]["融资余额"])) if len(df) > 1 else val
    return val, date_str, prev_val


# ── 评分逻辑 ───────────────────────────────────────


def _score_cpi(cpi: float) -> float:
    """
    CPI 评分逻辑。

    规则：
      - 1% ≤ CPI ≤ 3%：满分 100（理想通胀区间）
      - 0% ≤ CPI < 1%：60 分（偏低，接近通缩边缘）
      - CPI < 0%：40 分（通缩）
      - CPI > 3%：线性递减，每超1%减20分，最低0分（过热）
    """
    if 1.0 <= cpi <= 3.0:
        return 100.0
    elif 0.0 <= cpi < 1.0:
        return 60.0
    elif cpi < 0.0:
        return 40.0
    else:  # cpi > 3.0
        return max(0.0, 100.0 - (cpi - 3.0) * 20.0)


def _score_ppi(ppi: float) -> float:
    """
    PPI 评分逻辑。

    规则：
      - PPI > -2%：60 分（轻度通缩或正增长）
      - -5% < PPI ≤ -2%：40 分（中度通缩）
      - PPI ≤ -5%：20 分（深度通缩）
    """
    if ppi > -2.0:
        return 60.0
    elif -5.0 < ppi <= -2.0:
        return 40.0
    else:  # ppi <= -5.0
        return 20.0


def _score_pmi(pmi: float) -> float:
    """
    PMI 评分逻辑。

    规则：
      - PMI > 52：100 分（强劲扩张）
      - 50 < PMI ≤ 52：80 分（温和扩张）
      - 48 ≤ PMI ≤ 50：60 分（临界区间）
      - PMI < 48：40 分（明显收缩）
    """
    if pmi > 52.0:
        return 100.0
    elif pmi > 50.0:
        return 80.0
    elif pmi >= 48.0:
        return 60.0
    else:
        return 40.0


def _score_liquidity(m2: float, shibor: float, lpr: float) -> float:
    """
    流动性综合评分，由三个子维度等权合成。

    M2 评分（货币供给）：
      - M2 > 8%：100（充裕）
      - M2 > 6%：80（适度）
      - M2 > 4%：60（偏紧）
      - M2 ≤ 4%：40（紧缩）

    SHIBOR 隔夜评分（银行间流动性）：
      - SHIBOR < 1.5%：100（宽松）
      - SHIBOR < 2.0%：80（适度）
      - SHIBOR < 2.5%：60（偏紧）
      - SHIBOR ≥ 2.5%：40（紧张）

    LPR 1Y 评分（贷款成本）：
      - LPR < 3.0%：100（低利率环境）
      - LPR < 3.5%：80（适中）
      - LPR < 4.0%：60（偏高）
      - LPR ≥ 4.0%：40（高利率环境）
    """
    # M2 评分
    if m2 > 8.0:
        m2_score = 100.0
    elif m2 > 6.0:
        m2_score = 80.0
    elif m2 > 4.0:
        m2_score = 60.0
    else:
        m2_score = 40.0

    # SHIBOR 评分
    if shibor < 1.5:
        shibor_score = 100.0
    elif shibor < 2.0:
        shibor_score = 80.0
    elif shibor < 2.5:
        shibor_score = 60.0
    else:
        shibor_score = 40.0

    # LPR 评分
    if lpr < 3.0:
        lpr_score = 100.0
    elif lpr < 3.5:
        lpr_score = 80.0
    elif lpr < 4.0:
        lpr_score = 60.0
    else:
        lpr_score = 40.0

    # 等权平均
    return round((m2_score + shibor_score + lpr_score) / 3.0, 1)


def _score_margin(margin: float, prev_margin: float) -> float:
    """
    融资情绪评分。

    基于融资余额的环比变化率计算市场情绪：
      - 基准分：50 分
      - 调整项：变化率 × 3250（每1%变化约影响32.5分）
      - 上限：100 分（极度乐观）
      - 下限：0 分（极度悲观）

    当融资余额增加时加分（看涨情绪），减少时减分（看跌情绪）。
    """
    if prev_margin <= 0:
        return 50.0
    change_pct = (margin - prev_margin) / prev_margin
    score = 50.0 + change_pct * 3250.0
    return round(max(0.0, min(100.0, score)), 1)


def _determine_level(total_score: float) -> str:
    """
    判断宏观经济周期阶段。

    规则：
      - total > 65：expansion（扩张期，多数指标向好）
      - 45 ≤ total ≤ 65：neutral（中性期，指标分化）
      - total < 45：contraction（收缩期，多数指标偏弱）
    """
    if total_score > 65.0:
        return "expansion"
    elif total_score >= 45.0:
        return "neutral"
    else:
        return "contraction"


def _determine_style(cpi: float, ppi: float) -> str:
    """
    判断市场风格偏好，基于 CPI 和 PPI 的组合。

    逻辑：
      - CPI≤0% 且 PPI≤-3%：balanced（均衡，典型的通缩+通缩组合）
      - CPI>0% 且 PPI>-2%：growth（成长，温和通胀+工业回暖）
      - CPI>2% 或 PPI>1%：value（价值，通胀较高，价值股受益）
      - CPI<-0.5% 且 PPI<-5%：defensive（防御，深度双通缩）
      - 其他：balanced（默认均衡）
    """
    if cpi <= 0.0 and ppi <= -3.0:
        return "balanced"
    elif cpi > 0.0 and ppi > -2.0:
        return "growth"
    elif cpi > 2.0 or ppi > 1.0:
        return "value"
    elif cpi < -0.5 and ppi < -5.0:
        return "defensive"
    else:
        return "balanced"


def _recommend_position(total_score: float, level: str) -> float:
    """
    推荐仓位建议。

    基于周期阶段给出仓位建议：
      - expansion（扩张期）：95%（积极做多）
      - neutral（中性期）：70%（中性仓位）
      - contraction（收缩期）：30%（防御性低仓位）
    """
    if level == "expansion":
        return 0.95
    elif level == "neutral":
        return 0.70
    else:  # contraction
        return 0.30


def _identify_risks(cpi: float, ppi: float, pmi: float) -> list[str]:
    """
    识别当前宏观环境中的风险因素。

    触发条件：
      - CPI < 0.5%：通缩风险提示
      - PPI < -2.0%：企业利润承压提示
      - PPI < -5.0%：深度通缩提示（覆盖 -2% 的分支）
      - PMI < 50.0：经济收缩风险提示
    """
    risks = []
    if cpi < 0.5:
        risks.append(f"CPI {cpi}% 偏低，需关注通缩风险")
    if ppi < -2.0:
        risks.append(f"PPI {ppi}% 中度通缩，企业利润承压")
    elif ppi < -5.0:
        # 此分支实际上不会执行，因为 -2% 的分支已捕获
        # 保留用于逻辑完整性
        risks.append(f"PPI {ppi}% 深度通缩，需关注工业通缩风险")
    if pmi < 50.0:
        risks.append(f"PMI {pmi}% 低于荣枯线，经济收缩风险")
    return risks


def _make_summary(total_score: float, level: str, position_pct: int, style: str) -> str:
    """
    生成概述文本，用于 JSON 中的 summary 字段和终端输出。

    将英文标签映射为中文描述：
      - expansion → 扩张
      - neutral → 中性
      - contraction → 收缩
      - growth → 成长
      - balanced → 均衡
      - value → 价值
      - defensive → 防御
    """
    level_cn = {
        "expansion": "扩张",
        "neutral": "中性",
        "contraction": "收缩",
    }.get(level, level)

    style_cn_map = {
        "growth": "成长",
        "balanced": "均衡",
        "value": "价值",
        "defensive": "防御",
    }
    style_cn = style_cn_map.get(style, style)

    return f"宏观评分 {total_score}/100（{level_cn}），推荐仓位 {position_pct}%，风格偏{style_cn}"


# ── 主流程 ──────────────────────────────────────────


def fetch_and_score() -> dict:
    """
    获取宏观数据并计算综合评分。

    流程：
      1. 依次获取所有宏观指标（带异常保护）
      2. 打印原始数据行
      3. 计算各维度评分，含子维度分解
      4. 加权计算总分
      5. 判断周期阶段和风格偏好
      6. 识别风险因素
      7. 生成概述文本
      8. 构建并返回结果字典

    Returns:
        dict: 包含评分结果的字典，结构见 _build_result
    """
    # 获取所有宏观数据
    cpi, cpi_date = _fetch_cpi()
    ppi, ppi_date = _fetch_ppi()
    pmi, pmi_date_str = _fetch_pmi()
    m2, m2_date_str = _fetch_m2()
    lpr, lpr_date = _fetch_lpr()
    shibor, shibor_date = _fetch_shibor()
    margin, margin_date, margin_prev = _fetch_margin()

    # 打印原始数据（与验证输出格式一致）
    print(f"  CPI: {cpi} ({cpi_date})")
    print(f"  PPI: {ppi} ({ppi_date})")
    print(f"  PMI: {pmi} ({pmi_date_str})")
    print(f"  M2: {m2} ({m2_date_str})")
    print(f"  LPR 1Y: {lpr} ({lpr_date})")
    print(f"  SHIBOR O/N: {shibor} ({shibor_date})")
    print(f"  融资余额: {margin} ({margin_date}), 前值: {margin_prev}")

    # 计算各维度评分
    cpi_score = _score_cpi(cpi)
    ppi_score = _score_ppi(ppi)
    pmi_score = _score_pmi(pmi)
    liquidity_score = _score_liquidity(m2, shibor, lpr)
    margin_score = _score_margin(margin, margin_prev)

    # 计算综合评分（加权平均）
    total_score = round(
        cpi_score * SCORE_WEIGHTS["cpi"]
        + ppi_score * SCORE_WEIGHTS["ppi"]
        + pmi_score * SCORE_WEIGHTS["pmi"]
        + liquidity_score * SCORE_WEIGHTS["liquidity"]
        + margin_score * SCORE_WEIGHTS["margin"],
        1,
    )

    # 判断周期和风格
    level = _determine_level(total_score)
    style = _determine_style(cpi, ppi)
    position = _recommend_position(total_score, level)
    position_pct = int(round(position * 100))
    risks = _identify_risks(cpi, ppi, pmi)
    summary = _make_summary(total_score, level, position_pct, style)

    # 打印评分结果
    print(f"\n   总分: {total_score}")
    print(f"   周期: {level}")
    print(f"   CPI分: {cpi_score}")
    print(f"   PPI分: {ppi_score}")
    print(f"   PMI分: {pmi_score}")
    print(f"   流动性分: {liquidity_score}")
    print(f"   融资情绪分: {margin_score}")
    print(f"   推荐仓位: {position_pct}%")
    print(f"   风格偏好: {style}")
    print(f"   概述: {summary}")

    # 打印详细分解（仅终端展示，不入JSON）
    weighted_contrib = {
        "CPI": round(cpi_score * SCORE_WEIGHTS["cpi"], 1),
        "PPI": round(ppi_score * SCORE_WEIGHTS["ppi"], 1),
        "PMI": round(pmi_score * SCORE_WEIGHTS["pmi"], 1),
        "流动性": round(liquidity_score * SCORE_WEIGHTS["liquidity"], 1),
        "融资情绪": round(margin_score * SCORE_WEIGHTS["margin"], 1),
    }
    print(f"   加权分解（权重: CPI={SCORE_WEIGHTS['cpi']}, "
          f"PPI={SCORE_WEIGHTS['ppi']}, "
          f"PMI={SCORE_WEIGHTS['pmi']}, "
          f"流动性={SCORE_WEIGHTS['liquidity']}, "
          f"融资={SCORE_WEIGHTS['margin']}）:")
    for k, v in weighted_contrib.items():
        print(f"     {k}: {v}")

    # 融资变化
    margin_change_pct = ((margin - margin_prev) / margin_prev * 100) if margin_prev > 0 else 0
    print(f"   融资变化: {margin_change_pct:+.2f}%")

    # 风险提示
    print(f"   风险提示 ({len(risks)}条):")
    for r in risks:
        print(f"     • {r}")

    # 构建输出字典（务必与已验证JSON格式一致）
    result = {
        "total": total_score,
        "level": level,
        "cpi_score": cpi_score,
        "ppi_score": ppi_score,
        "pmi_score": pmi_score,
        "liquidity_score": liquidity_score,
        "margin_score": margin_score,
        "recommended_position": position,
        "style_preference": style,
        "risks": risks,
        "summary": summary,
        "date": TODAY,
        "generated_at": NOW.strftime("%Y-%m-%d %H:%M:%S"),
    }

    return result


def save_result(result: dict, output_path: str = OUTPUT_FILE) -> str:
    """
    保存评分结果到 JSON 文件。

    使用 ensure_ascii=False 保证中文正常显示，
    indent=2 保持人类可读格式。

    Args:
        result: 评分结果字典
        output_path: 输出文件路径（默认: ~/wiki/finance/raw/macro/macro_score.json）

    Returns:
        str: 实际写入的文件路径
    """
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print(f"\n✅ 已保存: {output_path}")
    return output_path


# ── 独立运行 ────────────────────────────────────────


def main():
    """
    主入口函数。

    执行流程：
      1. 打印标题和运行时间
      2. 调用 fetch_and_score() 获取数据并评分
      3. 调用 save_result() 保存 JSON
      4. 异常时打印错误信息并退出

    不接收命令行参数，所有配置通过模块级常量控制。
    """
    print("=" * 50)
    print(f"  宏观评分引擎 | {NOW.strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 50)

    try:
        result = fetch_and_score()
        save_result(result)

        # 持久化到 Dashboard DB
        try:
            from report_store import store_macro_score
            store_macro_score(
                date=result["date"],
                score=result["total"],
                position=result["recommended_position"],
                indicators={
                    "cpi": result.get("cpi_score", 0),
                    "ppi": result.get("ppi_score", 0),
                    "pmi": result.get("pmi_score", 0),
                    "liquidity": result.get("liquidity_score", 0),
                    "margin": result.get("margin_score", 0),
                },
            )
        except Exception as e:
            print(f"[report_store] persist failed (non-fatal): {e}")

    except Exception as e:
        print(f"\n  ❌ 运行失败: {e}")
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
