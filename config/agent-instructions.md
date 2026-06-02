# Quant Meta-Team Multica Configuration v0.2

This directory is the source of truth for the Multica quant team instructions.

## Platform Objects

| Object | Multica ID | Source file |
|---|---|---|
| quant-orchestrator | 50cc665a-5d3b-471a-a7c2-6285f1440594 | `agents/quant-orchestrator.md` |
| quant-data-agent | 93bd9feb-c40b-42dd-93d7-08f0c58e00c3 | `agents/quant-data-agent.md` |
| quant-portfolio-agent | d3891021-eaeb-4ac7-b817-6b29423f6476 | `agents/quant-portfolio-agent.md` |
| quant-risk-agent | 926b5b84-250f-4dfc-9407-5fceb479fe49 | `agents/quant-risk-agent.md` |
| 量化小队 | c6e6fc58-83dd-4a77-8e5a-5e28a490b415 | `squads/quant-squad.md` |

## v0.2 Changes

- Added executable Multica workflow rules: issue status transitions, comment handoff format, blocking behavior, and review boundaries.
- Added data environment and data quality acceptance criteria for tushare + akshare workflows.
- Added stricter A-share backtest requirements: universe, adjustment method, suspension/limit-up handling, survivorship bias, look-ahead checks, costs, slippage, benchmark, and walk-forward validation.
- Added explicit execution boundary: the team produces research recommendations only and must not place real trades.
- Added Meta-Team L1/L2/L3 evolution schema, trigger metrics, approval rules, and before/after comparison requirements.
- Set `quant-orchestrator` expectation to workspace visibility and squad leader role.

## Operating Boundary

This team is a research and analysis team. It may produce data reports, candidate portfolios, risk reports, backtest evidence, and paper-trading recommendations. It must not directly place real-money orders or present research output as guaranteed investment advice.
