import { getScreenerDb } from "./db";
import * as crypto from "crypto";

// ── Encryption ────────────────────────────────────────

const KEY_B64 = process.env.TIANGONG_QUANT_ENC_KEY || "";
const ENC_KEY = KEY_B64 ? Buffer.from(KEY_B64, "base64") : null;

function decrypt(enc: string | null): string {
  if (!enc || !ENC_KEY) return "";
  try {
    const raw = Buffer.from(enc, "base64");
    const nonce = raw.subarray(0, 12);
    const ct = raw.subarray(12);
    const decipher = crypto.createDecipheriv("aes-256-gcm", ENC_KEY, nonce);
    decipher.setAuthTag(ct.subarray(ct.length - 16));
    const pt = Buffer.concat([decipher.update(ct.subarray(0, ct.length - 16)), decipher.final()]);
    return pt.toString("utf8");
  } catch {
    return "";
  }
}

function decryptFloat(enc: string | null): number {
  const s = decrypt(enc);
  return s ? parseFloat(s) : 0;
}

// ─── Types ─────────────────────────────────────────────

export interface QuantSignal {
  trade_date: string;
  regime: string;
  regime_confidence: number;
  ensemble_ic: number;
  catboost_ic: number;
  xgboost_ic: number;
  lightgbm_ic: number;
  mlp_ic: number;
  ridge_ic: number;
  prediction_bias: string;
  bias_strength: number;
  top_factor: string;
  top_factor_ic: number;
  total_stocks: number;
  buy_signals: number;
  sell_signals: number;
}

export interface ICHistoryPoint {
  trade_date: string;
  model: string;
  ic: number;
  rank_ic: number;
}

export interface FactorImportance {
  trade_date: string;
  factor_name: string;
  importance: number;
  direction: string;
}

// ─── Queries ───────────────────────────────────────────

function decryptRow(row: any): any {
  if (!row) return row;
  const r = { ...row };
  for (const k of Object.keys(r)) {
    if (k.endsWith("_enc") && typeof r[k] === "string") {
      const plainKey = k.replace("_enc", "");
      if (["ic", "confidence", "strength", "importance"].some(t => plainKey.endsWith(t)) ||
          ["total_stocks", "buy_signals", "sell_signals"].includes(plainKey)) {
        r[plainKey] = decryptFloat(r[k]);
      } else {
        r[plainKey] = decrypt(r[k]);
      }
      delete r[k];
    }
  }
  return r;
}

export function getLatestQuantSignal(): QuantSignal | null {
  const row = getScreenerDb().prepare(
    "SELECT * FROM quant_signals ORDER BY trade_date DESC LIMIT 1"
  ).get() as any;
  return row ? decryptRow(row) as QuantSignal : null;
}

export function getQuantICHistory(limit = 30): ICHistoryPoint[] {
  const rows = getScreenerDb().prepare(
    "SELECT trade_date, model, ic_enc, rank_ic_enc FROM quant_ic_history ORDER BY trade_date DESC, model LIMIT ?"
  ).all(limit) as any[];
  return rows.map(decryptRow) as ICHistoryPoint[];
}

export function getLatestICByModel(): Record<string, number> {
  const rows = getScreenerDb().prepare(
    "SELECT model, ic_enc FROM quant_ic_history WHERE trade_date = (SELECT MAX(trade_date) FROM quant_ic_history)"
  ).all() as any[];
  const result: Record<string, number> = {};
  for (const r of rows) {
    result[r.model] = decryptFloat(r.ic_enc);
  }
  return result;
}

export function getTopFactors(limit = 10): FactorImportance[] {
  const rows = getScreenerDb().prepare(
    "SELECT trade_date, factor_name, importance_enc, direction_enc FROM quant_factor_importance ORDER BY importance_enc DESC LIMIT ?"
  ).all(limit) as any[];
  // decrypt then re-sort since encrypted sort is meaningless
  const decrypted = rows.map(decryptRow) as FactorImportance[];
  return decrypted.sort((a, b) => b.importance - a.importance);
}

export function getPortfolioNav(limit = 60) {
  return getScreenerDb().prepare(
    "SELECT trade_date, nav, daily_return FROM portfolio_nav ORDER BY trade_date DESC LIMIT ?"
  ).all(limit) as { trade_date: string; nav: number; daily_return: number }[];
}
