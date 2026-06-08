# Week4 Strategy Sweep Report (ADM-619)

Window: 2024-01-01 -> 2024-12-31
Strategies:12 (3 lookbacks x4 filter settings)

## Decision

## Honest Conclusion:12 strategies all failed the risk gates.

**Honest Conclusion:** No strategy in the12-strategy grid simultaneously satisfied
max_dd <= 15% AND VaR(95%) <= 2.5% AND Sharpe>0 over the2024-2025 window.
Signal layer needs further iteration. See the top5 next-best below.

## Metrics Table (12 rows x11 metrics)

| strategy | lookback | rebalance | filter | ann_ret | sharpe | max_dd | VaR95 | CVaR95 | turnover | n_rebal | n_trades |
|---|---|---|---|---|---|---|---|---|---|---|---|
| L5_weekly_none | 5 | weekly | none | -49.24% | -1.86 | 57.16% | 3.73% | 5.68% | 1.74x | 52 | 51 |
| L5_weekly_amount_percentile | 5 | weekly | amount_percentile | -49.89% | -1.85 | 57.23% | 4.33% | 6.13% | 1.74x | 52 | 51 |
| L5_weekly_liquidity_floor | 5 | weekly | liquidity_floor | -49.48% | -1.87 | 56.80% | 3.77% | 5.73% | 1.74x | 52 | 51 |
| L5_weekly_amount_percentile_and_liquidity_floor | 5 | weekly | amount_percentile_and_liquidity_floor | -49.89% | -1.85 | 57.23% | 4.33% | 6.13% | 1.74x | 52 | 51 |
| L20_monthly_none | 20 | monthly | none | -38.75% | -1.45 | 47.60% | 3.61% | 4.98% | 0.41x | 12 | 12 |
| L20_monthly_amount_percentile | 20 | monthly | amount_percentile | -40.81% | -1.51 | 48.95% | 3.82% | 5.02% | 0.41x | 12 | 12 |
| L20_monthly_liquidity_floor | 20 | monthly | liquidity_floor | -40.17% | -1.47 | 50.07% | 3.85% | 5.08% | 0.41x | 12 | 12 |
| L20_monthly_amount_percentile_and_liquidity_floor | 20 | monthly | amount_percentile_and_liquidity_floor | -40.81% | -1.51 | 48.95% | 3.82% | 5.02% | 0.41x | 12 | 12 |
| L60_quarterly_none | 60 | quarterly | none | -7.33% | -0.13 | 31.39% | 3.41% | 4.65% | 0.13x | 4 | 4 |
| L60_quarterly_amount_percentile | 60 | quarterly | amount_percentile | -9.52% | -0.20 | 32.35% | 3.41% | 4.85% | 0.13x | 4 | 4 |
| L60_quarterly_liquidity_floor | 60 | quarterly | liquidity_floor | -9.55% | -0.20 | 32.04% | 3.41% | 4.82% | 0.13x | 4 | 4 |
| L60_quarterly_amount_percentile_and_liquidity_floor | 60 | quarterly | amount_percentile_and_liquidity_floor | -9.52% | -0.20 | 32.35% | 3.41% | 4.85% | 0.13x | 4 | 4 |

## Next-best strategies (top5 by max_dd, then VaR95)

1. L60_quarterly_none | ann_ret=-7.33% sharpe=-0.13 max_dd=31.39% VaR95=3.41%
2. L60_quarterly_liquidity_floor | ann_ret=-9.55% sharpe=-0.20 max_dd=32.04% VaR95=3.41%
3. L60_quarterly_amount_percentile | ann_ret=-9.52% sharpe=-0.20 max_dd=32.35% VaR95=3.41%
4. L60_quarterly_amount_percentile_and_liquidity_floor | ann_ret=-9.52% sharpe=-0.20 max_dd=32.35% VaR95=3.41%
5. L20_monthly_none | ann_ret=-38.75% sharpe=-1.45 max_dd=47.60% VaR95=3.61%

## Bias checks

- Look-ahead: momentum uses close[-1] / close[-L-1] (no future columns).
- Survivorship: universe built at each as_of via get_index_member (historical time-point).
- Data leakage: walk-forward split by calendar; in-sample and out-of-sample separated.
- Reproducibility: identical inputs -> identical NAV (no random state, no clock).
- No tushare/akshare imports (verified by CI gate grep).
