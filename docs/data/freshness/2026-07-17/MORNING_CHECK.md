# 历史日线验收速查（2026-07-23 更新）

## 状态实话

| 项 | 状态 |
|----|------|
| 读路径（回测/因子/日历） | ✅ 已打通分区 `daily_bars` / `stock_daily_all` |
| 2026 分表 | ✅ max≈热表同步窗 |
| 2014 全市场 | ❌ 未达标（checkpoint 中断，需续跑） |
| 2015–2019 | ❌ **标的级空洞**（例：`000001` 缺 2015–2019；`600519` 仅 2020+） |
| 通宵链 | 已中断；需用改进后的 `--hole-fill`（看**任意更晚年份**）续跑 |

**不要**在洞补完成前，把 2015–2019 当全市场正式回测窗。

## 1. 覆盖报告
`docs/data/freshness/2026-07-17/HISTORY_COVERAGE.md`（续跑后会刷新）

## 2. 进程 / 进度
```bash
cat ~/nous-data/logs/overnight_backfill_status.txt
tail -30 ~/nous-data/logs/backfill_2014.log
python3 -c "import json; d=json.load(open('$HOME/nous-data/backfill_checkpoints/stock_daily_2014.json')); print(len(d['done']), 'done', len(d.get('failed',[])),'failed', d.get('updated_at'))"
# 标的级烟测
sqlite3 ~/nous-data/screener.db "
SELECT '000001' s, y, cnt FROM (
  SELECT '2015' y, COUNT(*) cnt FROM stock_daily_2015 WHERE symbol='000001'
  UNION ALL SELECT '2018', COUNT(*) FROM stock_daily_2018 WHERE symbol='000001'
  UNION ALL SELECT '2020', COUNT(*) FROM stock_daily_2020 WHERE symbol='000001'
);
SELECT '600519' s, y, cnt FROM (
  SELECT '2018' y, COUNT(*) cnt FROM stock_daily_2018 WHERE symbol='600519'
  UNION ALL SELECT '2020', COUNT(*) FROM stock_daily_2020 WHERE symbol='600519'
);"
```

## 3. 读路径自测
```bash
cd ~/code/nous
PYTHONPATH=src .venv/bin/python - <<'PY'
from nous.engine.backtest.data_handler import PointInTimeDataHandler
for d in ['2014-06-30','2018-06-29','2026-06-30']:
    dh=PointInTimeDataHandler(d)
    print(d, 'close000001', dh.get_close('000001'), 'univ', dh.get_universe_count('a'))
    dh.close()
PY
```

## 4. 安全续跑（workers=1，防封）
```bash
cd ~/code/nous
# 推荐：整链（2014 → hole-fill 2015-19 → 2025 thin → 报告）
nohup bash scripts/overnight_chain.sh >> ~/nous-data/logs/overnight_chain.log 2>&1 &

# 或单步
PYTHONPATH=src .venv/bin/python -u scripts/backfill_year_partition.py --year 2014 --workers 1
PYTHONPATH=src .venv/bin/python -u scripts/backfill_year_partition.py --year 2018 --hole-fill --workers 1
```

## 5. 因子重算（日线洞补达标后再跑）
```bash
bash ~/nous-data/logs/run_factor_recompute_2015plus.sh
```
