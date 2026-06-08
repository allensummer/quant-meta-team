"""Momentum (20D) + Reversal (5D) demo on the most recent trade day.

Runnable as:
    python -m quant_portfolio.examples.factor_momentum_reversal

Outputs
-------
- stdout: markdown table of the most recent trade day, the universe size
  considered, and Top/Bottom 10 by composite score.
- ``quant_portfolio/factor_report.md`` (sibling file): the same report
  written to disk for posting in the issue comment.

Factor definitions
------------------
- **Momentum(20D)** = close_qfq[t] / close_qfq[t-20] - 1
  Look 20 trade days back, take the return; higher = stronger trend.
- **Reversal(5D)**  = -1 * (close_qfq[t] / close_qfq[t-5] - 1)
  Short-term mean-reversion signal; positive score = oversold.

Composite score (illustrative — not a portfolio weight):
  score = 0.5 * zscore(mom_20d) + 0.5 * zscore(rev_5d)
  (zscore is cross-section; NaN is filled with 0.)

This is **example output, not an executable portfolio**. Per the
portfolio constraints (v0.4 §7.2), real weight assignment would apply:
  - single stock cap 10% / max 20% on request
  - single industry cap 30%
  - liquidity / suspension filters (already applied in data_layer)
  - estimated turnover vs. previous hold
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import date
from pathlib import Path

import pandas as pd

from quant_portfolio.data_layer import (
    FactorSpec,
    PortfolioDataLayer,
    amount_to_yuan,
    vol_to_shares,
)

log = logging.getLogger("quant_portfolio.examples.factor_momentum_reversal")

REPORT_PATH = Path(__file__).resolve().parents[1] / "factor_report.md"
MOMENTUM_FACTOR = FactorSpec(
    "momentum_20d", 20, +1, "20D momentum = close_qfq[t] / close_qfq[t-20] - 1"
)
REVERSAL_FACTOR = FactorSpec(
    "reversal_5d", 5, -1, "5D reversal = -1 * (close_qfq[t] / close_qfq[t-5] - 1)"
)


def _zscore(s: pd.Series) -> pd.Series:
    """Cross-section z-score, NaN-safe; returns 0 for missing values."""
    mu = s.mean(skipna=True)
    sd = s.std(skipna=True)
    if not sd or pd.isna(sd) or sd == 0:
        return pd.Series(0.0, index=s.index)
    return ((s - mu) / sd).fillna(0.0)


def build_report(
    *,
    on_date: str | date | None = None,
    min_amount_yuan: float = 1e8,           # 1 亿元日成交额门槛
    top_n: int = 10,
    n_samples: int = 5,
    store=None,
) -> tuple[date, pd.DataFrame, pd.DataFrame, pd.DataFrame, dict]:
    """Build the factor report and return its core data + the metadata.

    Returns
    -------
    as_of : date
        The trade day used.
    universe : DataFrame
        The full per-stock factor table (sorted by composite score desc).
    top : DataFrame
        Top ``top_n`` rows from ``universe``.
    bot : DataFrame
        Bottom ``top_n`` rows from ``universe``.
    meta : dict
        Diagnostics: number of candidates, factor compute calls, etc.
    """
    dl = PortfolioDataLayer(store=store)
    as_of = dl.latest_trade_day(on_date) if on_date else dl.latest_trade_day()
    if as_of is None:
        raise SystemExit("no trade calendar row found — is the calendar synced?")
    log.info("as-of trade day: %s", as_of.isoformat())

    res = dl.get_factor_universe(
        as_of,
        lookback_days=30,
        min_amount_yuan=min_amount_yuan,
        factors=(MOMENTUM_FACTOR, REVERSAL_FACTOR),
    )
    if res.df.empty:
        raise SystemExit(f"universe is empty on {as_of} after filters: {res.notes}")

    df = res.df.copy()
    df["momentum_20d_z"] = _zscore(df["momentum_20d"])
    df["reversal_5d_z"] = _zscore(df["reversal_5d"])
    df["composite_score"] = 0.5 * df["momentum_20d_z"] + 0.5 * df["reversal_5d_z"]
    df = df.sort_values("composite_score", ascending=False).reset_index(drop=True)
    df["amount_yuan"] = df["amount_yuan"].round(0)
    df["vol_shares"] = vol_to_shares(df["vol_lots"]).round(0)
    df["turnover_pct"] = (df["vol_shares"] * df["close"] / (df["amount_yuan"] + 1e-9) * 100).round(2)

    top = df.head(top_n)
    bot = df.tail(top_n).iloc[::-1]  # show worst-first

    meta = {
        "as_of": as_of.isoformat(),
        "candidate_universe_size": int(len(df)),
        "factor_compute_calls": {
            "momentum_20d": 1,
            "reversal_5d": 1,
        },
        "min_amount_yuan": min_amount_yuan,
        "suspension_filter": "high=low AND vol=0  (v0.4 §5)",
        "list_status_filter": "list_status='L' (上市)",
        "factor_directions": {
            "momentum_20d": +1,
            "reversal_5d": -1,
        },
        "composite_formula": "0.5 * z(mom_20d) + 0.5 * z(rev_5d)",
        "notes": res.notes,
    }
    return as_of, df, top, bot, meta


def to_markdown(
    as_of: date,
    universe: pd.DataFrame,
    top: pd.DataFrame,
    bot: pd.DataFrame,
    meta: dict,
    *,
    n_samples: int = 5,
) -> str:
    """Render the report as markdown. ``universe`` is the full table; we
    pick ``n_samples`` from the very top + 1 mid-cap + 1 bottom for the
    inline sample (the rest goes to Top/Bottom 10)."""
    lines: list[str] = []
    lines.append(f"# 动量(20D) + 反转(5D) 因子样例 — {as_of.isoformat()}")
    lines.append("")
    lines.append("> **示例输出，非可执行组合**。本报告展示 quant-portfolio-agent "
                 "切到 DuckDB/Parquet 消费层后的端到端流水线；不构成实盘建议，"
                 "权重未做风控约束。")
    lines.append("")
    lines.append("## 1. 因子定义")
    lines.append("")
    lines.append("| 因子 | 方向 | 公式 | 含义 |")
    lines.append("|------|------|------|------|")
    lines.append("| momentum_20d | +1 | `close_qfq[t] / close_qfq[t-20] - 1` | 20 日动量，趋势跟踪 |")
    lines.append("| reversal_5d  | -1 | `-1 * (close_qfq[t] / close_qfq[t-5] - 1)` | 5 日反转，短期均值回复 |")
    lines.append("")
    lines.append("合成得分：`composite = 0.5 * z(mom_20d) + 0.5 * z(rev_5d)` （横截面 z-score，NaN 填 0）")
    lines.append("")
    lines.append("## 2. 数据与筛选")
    lines.append("")
    lines.append(f"- as-of 交易日：**{as_of.isoformat()}**")
    lines.append(f"- 候选股票数（流动性+停牌+上市状态过滤后）：**{meta['candidate_universe_size']}**")
    lines.append(f"- 流动性门槛：`amount_yuan ≥ {meta['min_amount_yuan']:.0e}` 元（≈ 1 亿）")
    lines.append(f"- 停牌判定：`{meta['suspension_filter']}`")
    lines.append(f"- 上市状态过滤：`{meta['list_status_filter']}`")
    lines.append(f"- 因子计算调用次数：momentum_20d×1、reversal_5d×1")
    lines.append("")
    lines.append("## 3. Top 10（合成得分高 → 强趋势 + 短期超卖）")
    lines.append("")
    lines.append("| ts_code | 名称 | 行业 | close | 成交额(元) | mom_20d | rev_5d | 合成 | 入选理由 |")
    lines.append("|---------|------|------|-------|-----------|---------|--------|------|---------|")
    for _, r in top.iterrows():
        reason = _rationale_top(r)
        lines.append(
            f"| {r['ts_code']} | {r['name']} | {r['industry']} | {r['close']:.2f} | "
            f"{r['amount_yuan']:.0f} | {r['momentum_20d']:+.2%} | {r['reversal_5d']:+.2%} | "
            f"{r['composite_score']:+.2f} | {reason} |"
        )
    lines.append("")
    lines.append("## 4. Bottom 10（合成得分低 → 弱趋势 + 短期超买）")
    lines.append("")
    lines.append("| ts_code | 名称 | 行业 | close | 成交额(元) | mom_20d | rev_5d | 合成 | 出榜理由 |")
    lines.append("|---------|------|------|-------|-----------|---------|--------|------|---------|")
    for _, r in bot.iterrows():
        reason = _rationale_bot(r)
        lines.append(
            f"| {r['ts_code']} | {r['name']} | {r['industry']} | {r['close']:.2f} | "
            f"{r['amount_yuan']:.0f} | {r['momentum_20d']:+.2%} | {r['reversal_5d']:+.2%} | "
            f"{r['composite_score']:+.2f} | {reason} |"
        )
    lines.append("")
    lines.append("## 5. 5 只样例股票（入选/出榜理由）")
    lines.append("")
    # 3 from top + 1 mid + 1 bottom
    samples = pd.concat([top.head(3), universe.iloc[len(universe) // 2 : len(universe) // 2 + 1], bot.head(1)])
    samples = samples.drop_duplicates(subset=["ts_code"]).head(n_samples)
    lines.append("| ts_code | 名称 | 行业 | mom_20d | rev_5d | 合成 | 入选/出榜理由 |")
    lines.append("|---------|------|------|---------|--------|------|------------|")
    for _, r in samples.iterrows():
        if r["composite_score"] >= top["composite_score"].min():
            reason = _rationale_top(r)
        else:
            reason = _rationale_bot(r)
        lines.append(
            f"| {r['ts_code']} | {r['name']} | {r['industry']} | "
            f"{r['momentum_20d']:+.2%} | {r['reversal_5d']:+.2%} | "
            f"{r['composite_score']:+.2f} | {reason} |"
        )
    lines.append("")
    lines.append("## 6. 交接说明")
    lines.append("")
    lines.append("- 输出文件：`quant_portfolio/factor_report.md`")
    lines.append("- 复现命令：`python -m quant_portfolio.examples.factor_momentum_reversal`")
    lines.append("- 数据来源：仅消费 `quant_data` DuckDB 视图 (`mv_daily_qfq` / `mv_trade_cal` / `stock_basic` Parquet)")
    lines.append("- 退出码：`pass` — 候选 + 因子 + 文件齐备，可交 Risk agent 复现")
    lines.append("")
    lines.append("```json")
    lines.append(json.dumps({k: v for k, v in meta.items() if k != "notes"}, ensure_ascii=False, indent=2))
    lines.append("```")
    return "\n".join(lines) + "\n"


def _rationale_top(r: pd.Series) -> str:
    parts: list[str] = []
    if r["momentum_20d"] > 0.05:
        parts.append(f"20 日累计 {r['momentum_20d']:+.1%} 强趋势")
    elif r["momentum_20d"] > 0:
        parts.append(f"20 日累计 {r['momentum_20d']:+.1%} 温和上行")
    else:
        parts.append(f"20 日累计 {r['momentum_20d']:+.1%} 偏弱")
    if r["reversal_5d"] > 0.01:
        parts.append(f"近 5 日回撤 {r['reversal_5d']:+.1%} 形成短期超卖")
    elif r["reversal_5d"] < -0.01:
        parts.append(f"近 5 日加速 {r['reversal_5d']:+.1%} 趋势延续")
    if r["amount_yuan"] > 5e8:
        parts.append("流动性充裕")
    return "；".join(parts) or "中性"


def _rationale_bot(r: pd.Series) -> str:
    parts: list[str] = []
    if r["momentum_20d"] < -0.05:
        parts.append(f"20 日累计 {r['momentum_20d']:+.1%} 明显走弱")
    elif r["momentum_20d"] < 0:
        parts.append(f"20 日累计 {r['momentum_20d']:+.1%} 偏弱")
    else:
        parts.append(f"20 日累计 {r['momentum_20d']:+.1%} 趋势减弱")
    if r["reversal_5d"] < -0.01:
        parts.append(f"近 5 日加速 {r['reversal_5d']:+.1%} 短期超买")
    elif r["reversal_5d"] > 0.01:
        parts.append(f"近 5 日回撤 {r['reversal_5d']:+.1%} 反弹进行中")
    if r["amount_yuan"] < 2e8:
        parts.append("流动性偏低需注意")
    return "；".join(parts) or "中性"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else "")
    p.add_argument("--on-date", default=None, help="as-of trade day (default: latest)")
    p.add_argument("--min-amount-yuan", type=float, default=1e8, help="min daily amount (yuan)")
    p.add_argument("--top-n", type=int, default=10, help="top/bottom N rows")
    p.add_argument("--report-path", default=str(REPORT_PATH), help="markdown report path")
    p.add_argument("--verbose", "-v", action="store_true")
    args = p.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    as_of, universe, top, bot, meta = build_report(
        on_date=args.on_date,
        min_amount_yuan=args.min_amount_yuan,
        top_n=args.top_n,
    )
    md = to_markdown(as_of, universe, top, bot, meta)
    out_path = Path(args.report_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(md, encoding="utf-8")
    log.info("wrote report -> %s", out_path)
    # Echo a short preview to stdout (issue comment visibility).
    print(md)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
