# 港交所公告 PDF 抽取可行性 —— 分部数据 spike

> 调研日期：2026-09-16 ｜ 目标：让 `nous pv` 自动填充季度级的「分部收入/分部毛利/毛利率/归母」
> 结论：**可行。pdftotext -layout 中文完全可读，两家都能一次抽到分产品/分部毛利表。**

---

## 0. 结论速览

| 公司 | 文件 | 页数 | 后端 | 目标表 | 抽取结果 | 校验 |
| --- | --- | --- | --- | --- | --- | --- |
| 信义光能 00968 | 2026 中期业绩公告 `2026073100853_c.pdf` | 61 | **pdftotext -layout ✅** | **P14 分部表** | 玻璃收入 7,162,693 / 毛利 237,832 → **3.32%**；可再生能源 1,210,427 / 673,833 → **55.67%** | ✅ 与已知 3.3% / 55.7% 一致 |
| 福莱特玻璃 06865 | 2026 中期報告 `2026082500671_c.pdf` | 221 | **pdftotext -layout ✅** | **P16 主要产品毛利表** | 光伏玻璃 毛利 394,432.53 / **毛利率 6.73%**；合計 545,173.55 / **8.18%** | ✅ 与已知 6.73% / 8.18% 完全一致 |
| 同上 | 同上 | — | pypdf | — | **❌ 中文全乱码**（数字可读） | 见 §1 |

**关键结论：pdftotext 是唯一可用后端；pypdf 只能拿数字、拿不到中文标签，不足以定位表格。**

---

## 1. (a) 后端对比与命令

### 环境（实测版本）

```bash
# 隔离环境（勿动项目 .venv）
python3 -m venv /tmp/pdfvenv && /tmp/pdfvenv/bin/pip install pypdf
# pypdf 6.18.1 ；Python 3.14.4

brew install poppler          # 约 1-2 分钟，装 nspr/nss/poppler 三个 bottle
# poppler 26.09.0 → /opt/homebrew/bin/pdftotext
pdftotext -v                  # pdftotext version 26.09.0
```

### 下载（无需 cookie，直连即可）

```bash
UA='Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0 Safari/537.36'
curl -sSL -A "$UA" -o xinyi_1h2026.pdf https://www1.hkexnews.hk/listedco/listconews/sehk/2026/0731/2026073100853_c.pdf   # 954,758 B
curl -sSL -A "$UA" -o flat_1h2026.pdf  https://www1.hkexnews.hk/listedco/listconews/sehk/2026/0825/2026082500671_c.pdf   # 3,067,644 B
```

### 抽取

```bash
pdftotext -layout xinyi_1h2026.pdf xinyi_1h2026.txt   # 118,823 B / 2,374 行 / 61 页
pdftotext -layout flat_1h2026.pdf  flat_1h2026.txt    # 398,114 B / 6,597 行 / 221 页
```

### pypdf 的失败形态（务必记录，避免以后有人走回头路）

`PdfReader(...).pages[i].extract_text()` 对中文**只出乱码**：

```text

'– 1 –\nҁ\n፠༈ഃʫ\nዄ\u0382Оப\u0382f\nXINYI SOLAR HOLDINGS LIMITED\nʮ̡\nʮ̡\x81\n΅˾\u0eeej 00968 \x81\nٙ\nʕಂุᐶʮѓ ...'
ϗू 8,430.1 10,931.8 -22.9%          ← 数字/英文/百分号正常
ϞɛᏐЦ๐л 39.0 745.8 -94.8%

```

即：**HKEX 的中文子集字体的 glyph→Unicode 映射缺失**，pypdf 退回 Latin-1 编码，汉字全部变 `ʮ̡`/`ϗू` 之类；
数字与英文可读，但**无法用于按关键词定位表格**。另：pypdf 还会向 stderr 刷数百行
`fontTools is required to fully parse the encoding of a CFF Type1 font ...`（无 fontTools 时的告警），
若一定要用 pypdf，必须 `pip install fonttools` 并抑制 stderr，但**仍解决不了中文**。

> 结论：**不要引入 pypdf 作为主路径**。若目标环境没有 poppler，宁可报「需要 poppler」也不要静默降级。

---

## 2. (b) 逐字抽取片段（最关键交付物）

### 2.1 信义光能 P14 —— 分部表（收入/销售成本/毛利，一表全给）

```text

截至二零二六年及二零二五年六月三十日止六個月的分部資料如下：

                     截至二零二六年六月三十日止六個月（未經審核）
                  太陽能          可再生
                玻璃銷售         能源業務        其他分部     未分配           總計
               人民幣千元 人民幣千元 人民幣千元 人民幣千元 人民幣千元

分部收益
 於某個時間點確認      7,162,693     1,210,427      —          —    8,373,120
 隨着時間確認                —           —        —     56,986       56,986

來自外部客戶的收益      7,162,693     1,210,427      —     56,986    8,430,106
銷售成本           (6,924,861)   (536,594)      —     (48,333) (7,509,788)

毛利               237,832      673,833       —       8,653    920,318

```

**由该表可直接算出（无需文字口径）：**

| 分部 | 收入(千元) | 毛利(千元) | 毛利率 |
| --- | --- | --- | --- |
| 太陽能玻璃銷售 | 7,162,693 | 237,832 | **3.32%**（已知 3.3%） |
| 可再生能源業務 | 1,210,427 | 673,833 | **55.67%**（已知 55.7%） |
| 未分配/其他 | 56,986 | 8,653 | — |
| 總計 | 8,430,106 | 920,318 | 10.92% |

同页还有**按地区划分的收入**、**折旧费用**（玻璃 552,454 / 可再生能源 403,657），可一并入库。

### 2.2 信义光能 P17 —— 分部毛利对账（交叉校验用）

```text

分部毛利與除所得稅前溢利的對賬載列如下：

                              截至六月三十日止六個月
                              二零二六年          二零二五年
                              人民幣千元          人民幣千元
                             （未經審核） （未經審核）

分部毛利                             911,665      1,992,835
未分配毛利                               8,653         5,701

總毛利                              920,318      1,998,536

```

（237,832 + 673,833 = 911,665 ✅；+ 8,653 = 920,318 ✅ 表内部自洽）

### 2.3 信义光能 P1 财务摘要 —— 归母/EPS/股息

```text

                      截至六月三十日止六個月
                      二零二六年        二零二五年          變動
                    人民幣百萬元        人民幣百萬元

收益                      8,430.1      10,931.8   -22.9%

本公司權益持有人應佔溢利               39.0         745.8   -94.8%

每股盈利－基本             人民幣 0.43 分    人民幣 8.21 分    -94.8%

每股中期股息                 0.23 港仙        4.2 港仙

```

P22 另给更精确的千元口径：`本公司權益持有人應佔溢利（人民幣千元） 39,016 745,755`；
P23：`擬派中期股息每股 0.23 港仙 …… 18,234`，股数 `9,147,043,615 股`。

### 2.4 信义光能 P48 —— MD&A 文字口径（可选交叉校验）

```text

毛利率減少 8.1 個百分點至 3.3%（二零二五
毛利貢獻於二零二六年上半年同比減少 26.2% 至人民幣 673.8

```

### 2.5 福莱特 P16 —— 主要产品毛利表（一行命中 6.73%）

```text

下 表 載 列 本 集 團 主 要 產 品 毛 利 情 況：

                     2026 年 1–6 月            2025 年 1–6 月
                      毛利          毛利率         毛利          毛利率
產品種類           人 民 幣（千 元）          (%) 人 民 幣（千 元）           (%)

光伏玻璃              394,432.53        6.73    854,843.90       12.31
家居玻璃               15,991.36       13.35     20,885.95       17.12
工程玻璃               35,312.79       16.21     83,725.56       34.49
浮法玻璃              -12,233.59      -12.01     -1,948.36       -6.96
發電收入               78,172.28       35.00     75,298.68       30.76
採礦產品               18,390.45       16.43       -798.02      -68.87
其他業務               15,107.73       47.56     55,099.28       35.92
合計                545,173.55       8.18    1,087,106.99     14.05

```

> ⚠️ 福莱特该表是**中文排版带空格**（A 股风格「光 伏 玻 璃」式），但本表恰好未插空格；
> 同文档其它段落大量使用**字间空格**（如「光 伏 玻 璃 銷 售 收 入 5,861.6」），正则必须容忍 `\s*` 或无空白归一化。

### 2.6 福莱特 P15 —— 收入文字段（分部收入的口径来源）

```text

截 至 二 零 二 六 年 六 月 三 十 日 止 六 個 月，本 集 團 銷 售 收 入 人 民 幣6,668.2百 萬 元，
較 二 零 二 五 年 同 期 7,737.0 百 萬 元 降 低 13.81%。其 中，光 伏 玻 璃 銷 售 收 入 5,861.6
百 萬 元，較 二 零 二 五 年 同 期 6,944.9 百 萬 元 減 少 15.60%，主 要 受 行 業 內 卷 及 貿
易 壁 壘 及 等 多 重 因 素 影 響，市 場 供 需 格 局 持 續 承 壓。

```

### 2.7 福莱特 P6 —— 主要会计数据（净利润/归母）

```text

淨利潤                               -362,771.12     265,957.38

```

- 单位：千元 → **-362.77 百万元 = -3.63 亿元**，与已知归母一致 ✅
- P42 另有元口径：`五、 淨 利 潤（淨 虧 損 以「–」號 填 列）  -362,771,115.32  265,957,379.79`
- P14/P18 MD&A：`本 集 團 淨 利 潤 為 人 民 幣 –362.8 百 萬 元`

---

## 3. (c) 正则建议

先做**空白归一化**（对福莱特尤其必要，可同时兼容两家）：

```python
import re
def norm(line: str) -> str:
    """全角空格/制表/多空格 → 单空格，并去首尾。"""
    return re.sub(r"[ \t\u3000]+", " ", line).strip()
```

### 3.1 信义：分部表（按「表头行 + 数字行」结构解析，不用死记列宽）

```python
RE_XINYI_SEG_HEADER = re.compile(r"來自外部客戶的收益\s+([\d,]+)\s+([\d,]+)\s+[—\-]\s+([\d,]+)\s+([\d,]+)")
RE_XINYI_SEG_COST   = re.compile(r"銷售成本\s+\(([\d,]+)\)\s+\(([\d,]+)\)\s+[—\-]\s+\(([\d,]+)\)\s+\(([\d,]+)\)")
RE_XINYI_SEG_GP     = re.compile(r"^毛利\s+([\d,]+)\s+([\d,]+)\s+[—\-]\s+([\d,]+)\s+([\d,]+)$")

# 归属：group1=太陽能玻璃, group2=可再生能源, group3=未分配, group4=總計
def xinyi_segment(text: str) -> dict:
    seg = {}
    for line in (norm(l) for l in text.splitlines()):
        if (m := RE_XINYI_SEG_HEADER.match(line)):
            seg["glass_revenue"], seg["renewable_revenue"], seg["other_revenue"], seg["total_revenue"] = \
                (float(x.replace(",", "")) for x in m.groups())
        elif (m := RE_XINYI_SEG_COST.match(line)):
            seg["glass_cost"], seg["renewable_cost"] = float(m.group(1).replace(",", "")), float(m.group(2).replace(",", ""))
        elif (m := RE_XINYI_SEG_GP.match(line)):
            seg["glass_gp"], seg["renewable_gp"], seg["other_gp"], seg["total_gp"] = \
                (float(x.replace(",", "")) for x in m.groups())
    if {"glass_revenue", "glass_cost"} <= seg.keys():
        seg["glass_gm_pct"] = round((seg["glass_revenue"] - seg["glass_cost"]) / seg["glass_revenue"] * 100, 2)
        seg["renewable_gm_pct"] = round((seg["renewable_revenue"] - seg["renewable_cost"]) / seg["renewable_revenue"] * 100, 2)
    return seg
```

> 注意 `毛利` 行**必须以行首锚定**（`^`）：P17 的对账表也含「分部毛利」「總毛利」行，不锚定会串页。

### 3.2 福莱特：主要产品毛利表（单行即含收入口径外的全部毛利信息）

```python
RE_FLAT_PRODUCT = re.compile(
    r"^(\S+?)\s+(-?[\d,]+\.\d{2})\s+(-?[\d.]+)\s+(-?[\d,]+\.\d{2})\s+(-?[\d.]+)$"
)
# group1=产品名, g2=本期毛利(千元), g3=本期毛利率(%), g4=上期毛利, g5=上期毛利率

def flat_products(text: str) -> dict:
    out = {}
    for line in (norm(l) for l in text.splitlines()):
        if (m := RE_FLAT_PRODUCT.match(line)):
            name = m.group(1)
            if name in {"光伏玻璃", "家居玻璃", "工程玻璃", "浮法玻璃", "發電收入", "採礦產品", "其他業務", "合計"}:
                out[name] = {"gp": float(m.group(2).replace(",", "")),
                             "gm_pct": float(m.group(3)),
                             "gp_prev": float(m.group(4).replace(",", "")),
                             "gm_prev_pct": float(m.group(5))}
    return out
```

### 3.3 通用：归母 / EPS / 股息（信义）

```python
RE_HK_NP     = re.compile(r"本公司權益持有人應佔溢利\s+([\d,]+\.?\d*)\s+([\d,]+\.?\d*)")   # P22 千元，或 P1 百万元
RE_HK_EPS    = re.compile(r"每股盈利[－\-]?基本\s+人民幣\s*([\d.]+)\s*分")
RE_HK_DIV    = re.compile(r"每股中期股息\s+([\d.]+)\s*港仙")
```

### 3.4 福莱特：净利润（元/千元两种口径都见）

```python
RE_FLAT_NP_K = re.compile(r"^淨利潤\s+(-?[\d,]+\.\d{2})\s+([\d,]+\.\d{2})")                  # P6 千元
RE_FLAT_NP_Y = re.compile(r"淨 利 潤（淨 虧 損 以「–」號 填 列）\s+(-?[\d,]{1,}\.\d{2})")     # P42 元（注意字间空格）
```

---

## 4. (d) 页码定位策略（不硬编码页码）

**统一做法：按页切分 → 全文档找关键词锚点 → 在锚点页内解析表。**

页面切分：pdftotext 输出**用 form feed `\f` 分页**，直接 `text.split("\f")`，
下标 +1 即「PDF 页序」（注意**印刷页码 ≠ PDF 页序**：福莱特 P16 的页脚写「14」）。

| 文档 | 一级锚点（定位页） | 二级锚点（确认表） | 实测命中 |
| --- | --- | --- | --- |
| 信义 业绩公告 | `收益及分部資料` 或 `分部資料` | `來自外部客戶的收益` + `銷售成本` + `^毛利` | 分部資料 → P13,14,15,16,17,26,45,47,56 |
| 信义（备选） | `分部毛利與除所得稅前溢利的對賬` | `分部毛利` / `總毛利` | P17（对账，仅总量） |
| 福莱特 中期報告 | `主要產品毛利情況` 或 `下表載列本集團主要產品毛利` | `^光伏玻璃` 行 | 表在 P16 |
| 福莱特（分部叙述） | `分部信息` / `分部報告的確定依據` | 五个分部名单 | P206（**只有叙述，无数字**） |

```python
def locate_pages(pages: list[str], anchors: list[str], must_have: list[str] | None = None) -> list[int]:
    hits = []
    for i, p in enumerate(pages):
        t = norm_all(p)
        if any(a in t for a in anchors) and (not must_have or all(k in t for k in must_have)):
            hits.append(i)
    return hits
```

**实践建议（按稳定性排序）**

1. **优先用「行正则全文档扫描」，不要依赖页码**：`RE_XINYI_SEG_HEADER` / `RE_FLAT_PRODUCT`
   这类强特征行本身就能唯一定位（实测各只命中 1 次）。页码只用于日志与人工复核。
2. 文档类型要选对：
   - 信义：**中期業績公告**（60 页）就够，无需 220 页的中期报告；
   - 福莱特：**中期報告**（221 页）才有「主要产品毛利表」，其**中期業績公告**（P… 公告版）没有该表 —— 本次验证的是 221 页的中期報告。
3. 页面顺序可能变：所有正则都应能在**任意页**命中，解析后再做**恒等式校验**（见下）。

**恒等式校验（强烈建议落地，防错版/错页）**

```python
assert abs(glass_gp + renewable_gp - segment_gp_total) <= 2      # 信义 P14：237,832+673,833=911,665
assert abs(seg_gp_total + unalloc_gp - total_gp) <= 2            # 911,665+8,653=920,318
assert abs(sum(p["gp"] for p in products.values() if p is not products["合計"]) - products["合計"]["gp"]) <= 2
```

---

## 5. (e) 失败 / 不可抽取（不猜数字）

| 项目 | 结论 | 原因 |
| --- | --- | --- |
| pypdf 抽中文 | ❌ 不可用 | HKEX 中文子集字体缺 glyph→Unicode 映射，汉字全乱码；数字可读但无法定位 |
| 信义 P14 的「分部毛利率」 | ⚠️ **不直接给** | 表里只给「收入/销售成本/毛利」；3.3% / 55.7% 需**自行相除**（已验证 3.32%/55.67%）。文字口径的 3.3% 在 P48 MD&A（繁体「減少 8.1 個百分點至 3.3%」） |
| 福莱特「光伏玻璃分部收入 58.62 亿」 | ⚠️ 只在**文字段**（P15），不在表格里 | 表格只有毛利/毛利率；收入需从 P15 那句 `光 伏 玻 璃 銷 售 收 入 5,861.6 百 萬 元` 取（注意字间空格） |
| 福莱特「归母」字样 | ❌ 搜不到 `股東應佔` / `归属于母公司股东` / `净利润` | 该报告用**繁体 + 港式表述**；实测可行替代：P6 `淨利潤 -362,771.12`（千元）、P42 `淨 利 潤（淨 虧 損 以「–」號 填 列）`。数值与已知归母一致，但**语义上是合并净利润行**，若公司存在显著少数股东权益需另找「歸屬於母公司股東的淨利潤」行 |
| 福莱特在产日熔量（吨/天） | ❌ 本次未在 PDF 文本中定位 | 与上一次调研一致（该数字在管理层讨论的「產能佈局」段落，本次未逐页检索；不猜） |
| poppler 缺失环境 | ❌ 无替代 | 建议启动时探测 `pdftotext -v`，缺失则**跳过 PDF 抽取并明确提示**（`brew install poppler`），不要静默降级到 pypdf |

---

## 6. 落地建议（给 nous pv）

1. 新增 `sources/pdf_reports.py`（或 `sources/hkex_pdf.py`）：
   - 发现层复用现有 `hkex` 采集器（已能按 `category=interim_results / annual_results` 拿到 PDF 链接）；
   - 取 **最新一份** `interim_results` / `annual_results` PDF → 下载到 `~/nous-data/pdfs/`（带缓存，按 URL 文件名）；
   - `pdftotext -layout` 抽文本 → 缓存 `.txt`（下次免重抽）；
   - 跑本文件的四个正则 → 写入 `obs`（source=`pdf`，obs_date=报告期期末如 `2026-06-30`，`source_url`=PDF 链接，`note` 保留命中原文片段）。
2. 缺口探测：启动时 `shutil.which("pdftotext")`；缺失 → `FetchResult(status="partial", message="需要 poppler: brew install poppler")`。
3. 依赖声明：**poppler 属系统依赖**，建议写进 `docs/pvglass-tracking.md` 前置条件；不要加 pypdf 依赖。
4. 指标映射（现成 id）：`xinyi_glass_gm`、`xinyi_renewable_gm`、`xinyi_attributable_profit`、`xinyi_revenue`、`flat_glass_glass_gm`、`flat_attributable_profit`、`flat_revenue`。
5. 测试：把 §2 的片断（去掉数字校验值）做成 fixture，断言抽取结果等于已知真值（3.32/55.67、6.73/8.18、39,016、-362,771.12）。

---

## 7. 本次使用的完整命令（可复现）

```bash
export PATH=/opt/homebrew/bin:$PATH
cd /tmp/pdfspike
UA='Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0 Safari/537.36'
curl -sSL -A "$UA" -o xinyi_1h2026.pdf https://www1.hkexnews.hk/listedco/listconews/sehk/2026/0731/2026073100853_c.pdf
curl -sSL -A "$UA" -o flat_1h2026.pdf  https://www1.hkexnews.hk/listedco/listconews/sehk/2026/0825/2026082500671_c.pdf
pdftotext -layout xinyi_1h2026.pdf xinyi_1h2026.txt
pdftotext -layout flat_1h2026.pdf  flat_1h2026.txt
python3 - <<'PY'
pages = open("xinyi_1h2026.txt", encoding="utf-8").read().split("\f")   # \f = 分页
print([i+1 for i, p in enumerate(pages) if "分部資料" in p])            # → [13,14,15,16,17,26,45,47,56]
PY
```
