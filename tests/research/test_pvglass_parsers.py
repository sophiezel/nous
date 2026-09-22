"""解析器离线测试 — 用真实抓到的文本片段，不联网。"""

from __future__ import annotations

from nous.research.pvglass.sources import consensus, energytrend, mysteel

# ── 光伏玻璃价格（Mysteel/隆众正文实测片段）──────────────────────────
MYSTEEL_2026_09 = """
综述：近期光伏玻璃行业迎来重要变化，在二轮联合减产持续推进背景下，9月行业2.0mm单镀面板
统一报价上调至10.5元/平米，较8月末9.5/平米的结算价格实现1元/平米的上涨，市场挺价氛围浓厚。
"""

MYSTEEL_2026_08 = """
隆众资讯8月26日报道：八月目前行业统一报价为2.0mm（单镀）面板9.5元/平米；
2.0mm双镀（面板）10.50；2.0mm（背板）8.50；3.2mm（单镀）面板价格从16-18元/平米不等，一单一议。
"""

MYSTEEL_INVENTORY = """
5月下旬行业库存上升至53.4天创历史新高；7月30日库存天数降至45.13天。
年初至今行业合计冷修产线1.96万吨，截至9月10日全球/国内在产产线名义产能合计8.5/7.2万吨。
"""


def test_mysteel_extract_price_and_settlement():
    found = mysteel.extract(MYSTEEL_2026_09)
    assert found["glass_price_2_0_single"]["value"] == 10.5
    assert found["glass_settlement_price"]["value"] == 9.5
    # 原文留证，便于人工复核
    assert "10.5" in found["glass_price_2_0_single"]["raw"]


def test_mysteel_extract_range_uses_midpoint_and_skips_double_coating():
    found = mysteel.extract(MYSTEEL_2026_08)
    assert found["glass_price_2_0_single"]["value"] == 9.5
    assert found["glass_price_2_0_double"]["value"] == 10.5
    assert found["glass_price_2_0_back"]["value"] == 8.5
    # 3.2mm 区间 16-18 → 中点 17
    assert found["glass_price_3_2_single"]["value"] == 17.0


def test_mysteel_extract_inventory_and_capacity_with_unit_conversion():
    found = mysteel.extract(MYSTEEL_INVENTORY)
    assert found["glass_inventory_days"]["value"] == 53.4  # 取首次出现（区间语义需人工确认）
    assert found["glass_capacity_cold_repair_ytd"]["value"] == 19600  # 1.96万吨 → 吨/日
    assert found["glass_capacity_operating"]["value"] == 72000  # 8.5/7.2 取国内


def test_mysteel_article_date_from_url():
    assert mysteel._article_date("https://www.mysteel.com/oilchem/a/26090409/ABC.html") == "2026-09-04"
    assert mysteel._article_date("https://example.com/x.html") is None


# ── 集邦产业链价格 ───────────────────────────────────────────────────
TRENDFORCE_2026_09 = """
当前硅料库存维持54万吨以上，生产端仍在提产，供需过剩压力进一步加剧。
本周硅片低价货源持续释放，当前库存约25GW，库存小幅上升。
目前183、210R、210成交均价约1.02、1.02、1.12元/片。
当前电池片价格继续下跌，183、210R成交价格已接近0.30元/W。
头部企业TOPCon报价约0.70-0.72元/W，二线企业报价多在0.68元/W以下，但实际成交有限。
"""


def test_trendforce_extract_chain_prices():
    found = energytrend.extract(TRENDFORCE_2026_09)
    assert found["polysilicon_inventory"]["value"] == 54.0
    assert found["wafer_inventory"]["value"] == 25.0
    assert found["wafer_price_182"]["value"] == 1.02
    assert found["cell_price_182"]["value"] == 0.30
    assert found["module_price_topcon"]["value"] == 0.71  # 0.70-0.72 中点


def test_trendforce_event_rules_match_only_relevant_titles():
    titles = [
        "信义、福莱特牵头，光伏玻璃龙头企业联合减产",
        "多晶硅收储平台公司正式注册成立",
        "某公司发布年度分红方案",
    ]
    matched = {}
    for title in titles:
        for indicator_id, keywords in energytrend.EVENT_RULES:
            if any(k in title for k in keywords):
                matched[title] = indicator_id
                break
    assert matched["信义、福莱特牵头，光伏玻璃龙头企业联合减产"] == "glass_capacity_cold_repair_ytd"
    assert matched["多晶硅收储平台公司正式注册成立"] == "polysilicon_platform"
    assert "某公司发布年度分红方案" not in matched


# ── etnet 一致预期 ───────────────────────────────────────────────────
ETNET_HTML = """
<table>
<tr><td>財政年度</td><td>純利/(虧損) (百萬元人民幣)</td><td>每股盈利/ (虧損)(分)</td>
    <td>每股派息 (分)</td><td>每股資產淨值 (人民幣元)</td><td>最高 (百萬元人民幣)</td>
    <td>最低 (百萬元人民幣)</td></tr>
<tr><td>2026</td><td>371.30</td><td>3.73</td><td>2.00</td><td>--</td><td>2,830.60</td><td>-927.00</td></tr>
<tr><td>財政年度</td><td>純利/(虧損) (百萬元人民幣)</td><td>每股盈利*/ (虧損) (分)</td>
    <td>每股派息* (分)</td><td>證券商</td><td>評級</td><td>目標價* (港元)</td><td>更新日期</td></tr>
<tr><td>2026</td><td>105.00</td><td>0.87</td><td>--</td><td>瑞銀</td><td>買入</td><td>3.60</td><td>31/07/2026</td></tr>
<tr><td>2026</td><td>2,830.60</td><td>30.00</td><td>15.62</td><td>摩根士丹利</td><td>增持</td><td>3.50</td><td>31/07/2026</td></tr>
<tr><td>2026</td><td>-927.00</td><td>-10.00</td><td>--</td><td>高盛</td><td>買入</td><td>2.80</td><td>27/07/2026</td></tr>
</table>
"""


def test_consensus_parse_sections():
    parsed = consensus.parse(ETNET_HTML)
    assert parsed["consensus"][0]["fiscal_year"] == "2026"
    assert parsed["consensus"][0]["net_profit"] == 371.30
    assert parsed["consensus"][0]["max"] == 2830.60
    assert parsed["consensus"][0]["min"] == -927.00

    brokers = parsed["brokers"]
    assert [b["broker"] for b in brokers] == ["瑞銀", "摩根士丹利", "高盛"]
    assert brokers[0]["target_price"] == 3.60
    assert brokers[2]["net_profit"] == -927.0


def test_consensus_as_of_picks_latest_update_date():
    parsed = consensus.parse(ETNET_HTML)
    assert consensus._as_of(parsed["brokers"]) == "2026-07-31"


def test_consensus_median_is_robust_to_outlier():
    """均值被极值污染 —— 中位数才是可用的一致预期。"""
    import statistics

    values = [105.0, 2830.6, -927.0]
    assert statistics.median(values) == 105.0
    assert statistics.fmean(values) > 600
