#!/usr/bin/env python3
"""
Bridge: Quant System → Dashboard SQLite
Reads model outputs from JSON/Parquet files, encrypts sensitive values,
writes to screener.db quant_signals / ic_history / factor_importance tables.

Run: python bridge_to_dashboard.py
Cron: 09:05 daily (after predict cron)
"""

import os, json, sqlite3, base64, sys
from datetime import date
from pathlib import Path
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

HOME = Path.home()
DATA = HOME / "code/stock-screener/data"
DB_PATH = str(DATA / "screener.db")

# ── Encryption ──────────────────────────────────────

KEY_B64 = os.environ.get("TIANGONG_QUANT_ENC_KEY", "")
if not KEY_B64:
    # Try loading from dashboard .env.local
    env_file = HOME / "code/dashboard/.env.local"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            if line.startswith("TIANGONG_QUANT_ENC_KEY="):
                KEY_B64 = line.split("=", 1)[1].strip()
                break
if not KEY_B64:
    print("ERROR: TIANGONG_QUANT_ENC_KEY not found", file=sys.stderr)
    sys.exit(1)

KEY = base64.b64decode(KEY_B64)

def encrypt(val) -> str:
    """AES-256-GCM encrypt any value → base64(nonce + ciphertext)"""
    nonce = os.urandom(12)
    cipher = AESGCM(KEY)
    ct = cipher.encrypt(nonce, str(val).encode(), None)
    return base64.b64encode(nonce + ct).decode()

# ── Data Readers ────────────────────────────────────

def read_json(path):
    with open(path) as f:
        return json.load(f)

def read_ensemble_ic():
    """Read ensemble IC JSON → dict of model_name->{ic, rank_ic}"""
    files = sorted(DATA.glob("ic_analysis/ensemble_2026-*.json"))
    if not files:
        return None, {}
    latest = files[-1]
    data = read_json(latest)
    # data structure: {"date":"...", "model_counts":6, "models":{"lightgbm":{"ic":0.07,...}, ...}}
    models = {}
    raw_models = data.get("models", {})
    for name, vals in raw_models.items():
        models[name] = {"ic": vals.get("ic", 0), "rank_ic": vals.get("rank_ic", 0)}
    return latest, models

def read_regime():
    """Read current market regime"""
    path = DATA / "market_regime/current_regime.json"
    if not path.exists():
        return None
    data = read_json(path)
    return data  # {"regime": "SIDEWAYS", "confidence": 0.9995}

def read_shap_importance():
    """Read factor importance CSV → [{factor_name, importance}]"""
    files = sorted(DATA.glob("factor_importance/importance_*.csv"))
    if not files:
        return []
    path = files[-1]
    import csv
    factors = []
    with open(path) as f:
        reader = csv.DictReader(f)
        for row in reader:
            factors.append({
                "factor_name": row.get("factor", ""),
                "importance": float(row.get("shap_importance", 0)),
                "direction": "positive" if float(row.get("shap_importance", 0)) > 0 else "negative",
            })
    return factors

def read_factor_signals():
    """Count buy/sell signals from screen_results table"""
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    rows = db.execute(
        "SELECT symbol, score, ma_cross, macd_signal FROM screen_results WHERE screen_date = (SELECT MAX(screen_date) FROM screen_results)"
    ).fetchall()
    db.close()
    if not rows:
        return 0, 0, 0
    total = len(rows)
    # Consider score>5 as buy, score<2 as sell
    buy = sum(1 for r in rows if r["score"] > 5)
    sell = sum(1 for r in rows if r["score"] < 2)
    return total, buy, sell

# ── Main ────────────────────────────────────────────

def main():
    today = date.today().isoformat()
    db = sqlite3.connect(DB_PATH)
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA busy_timeout=5000")

    print(f"[bridge] {today} — reading quant outputs...")

    # 1. Ensemble IC
    ic_file, models = read_ensemble_ic()
    if models:
        print(f"  IC from {ic_file.name}: {len(models)} models")
        db.execute("""
            INSERT OR REPLACE INTO quant_signals
            (trade_date, ensemble_ic_enc, catboost_ic_enc, xgboost_ic_enc,
             lightgbm_ic_enc, mlp_ic_enc, ridge_ic_enc)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, [
            today,
            encrypt(models.get("voting", {}).get("ic", 0)),
            encrypt(models.get("catboost", {}).get("ic", 0)),
            encrypt(models.get("xgboost", {}).get("ic", 0)),
            encrypt(models.get("lightgbm", {}).get("ic", 0)),
            encrypt(models.get("mlp", {}).get("ic", 0)),
            encrypt(models.get("ridge", {}).get("ic", 0)),
        ])

        # IC history (all models)
        for name, vals in models.items():
            db.execute("""
                INSERT OR REPLACE INTO quant_ic_history (trade_date, model, ic_enc, rank_ic_enc)
                VALUES (?, ?, ?, ?)
            """, [today, name, encrypt(vals["ic"]), encrypt(vals.get("rank_ic", 0))])

    # 2. Market regime
    regime = read_regime()
    if regime:
        print(f"  Regime: {regime.get('regime', '?')}")
        db.execute("""
            UPDATE quant_signals
            SET regime_enc=?, regime_confidence_enc=?
            WHERE trade_date=?
        """, [
            encrypt(regime.get("regime", "")),
            encrypt(regime.get("confidence", 0)),
            today,
        ])

    # 3. Factor importance
    factors = read_shap_importance()
    if factors:
        print(f"  Factors: {len(factors)}")
        for f in factors:
            db.execute("""
                INSERT OR REPLACE INTO quant_factor_importance
                (trade_date, factor_name, importance_enc, direction_enc)
                VALUES (?, ?, ?, ?)
            """, [today, f["factor_name"], encrypt(f["importance"]), encrypt(f.get("direction", "positive"))])
        # Update top factor in signals
        top = factors[0]
        db.execute("""
            UPDATE quant_signals
            SET top_factor_enc=?, top_factor_ic_enc=?
            WHERE trade_date=?
        """, [encrypt(top["factor_name"]), encrypt(top["importance"]), today])

    # 4. Buy/sell signals
    total, buy, sell = read_factor_signals()
    if total > 0:
        print(f"  Signals: {total} stocks, {buy} buy, {sell} sell")
        bias = "bullish" if buy > sell else "bearish" if sell > buy else "neutral"
        strength = abs(buy - sell) / total if total > 0 else 0
        db.execute("""
            UPDATE quant_signals
            SET prediction_bias_enc=?, bias_strength_enc=?,
                total_stocks_enc=?, buy_signals_enc=?, sell_signals_enc=?
            WHERE trade_date=?
        """, [
            encrypt(bias), encrypt(strength),
            encrypt(total), encrypt(buy), encrypt(sell),
            today,
        ])

    db.commit()
    db.close()
    print(f"[bridge] Done.")

if __name__ == "__main__":
    main()
