# Quant Meta-Team Multica Configuration v0.3

This directory is the source of truth for the Multica quant team instructions.

## Platform Objects

| Object | Multica ID | Source file |
|---|---|---|
| quant-orchestrator | 50cc665a-5d3b-471a-a7c2-6285f1440594 | `agents/quant-orchestrator.md` |
| quant-data-agent | 93bd9feb-c40b-42dd-93d7-08f0c58e00c3 | `agents/quant-data-agent.md` |
| quant-portfolio-agent | d3891021-eaeb-4ac7-b817-6b29423f6476 | `agents/quant-portfolio-agent.md` |
| quant-risk-agent | 926b5b84-250f-4dfc-9407-5fceb479fe49 | `agents/quant-risk-agent.md` |
| 量化小队 | c6e6fc58-83dd-4a77-8e5a-5e28a490b415 | `squads/quant-squad.md` |

## Autopilots (v0.3 新增)

| Name | Agent | Trigger | Source file |
|---|---|---|---|
| quant-data-daily-factor-refresh | quant-data-agent | 17 15 * * 1-5 | `autopilots/daily-factor-refresh.md` |
| quant-risk-weekly-backtest-audit | quant-risk-agent | 0 10 * * 6 | `autopilots/weekly-backtest-audit.md` |
| quant-orchestrator-weekly-experience-synthesis | quant-orchestrator | 0 10 * * 0 | `autopilots/weekly-experience-synthesis.md` |

## Agent Skills (v0.3 新增)

| Agent | Skills |
|---|---|
| quant-orchestrator | minimax-search, multica-memory, rss-news |
| quant-data-agent | minimax-search, multica-memory, rss-news |
| quant-portfolio-agent | minimax-search, mmx-cli, multica-memory |
| quant-risk-agent | minimax-search, multica-memory |

> 注意：tushare/akshare/pandas-pro/python-pro 当前不在 workspace skill 库中，暂用 minimax-search + rss-news 覆盖信息获取。后续如需实际数据访问，请通过 multica-skill-dev 安装对应 skill。

## Archived (v0.3 旧体系下线)

以下 [Q]* agents 已归档（archive），不再接收任务：

- [Q]chief-quant-agent (e5cc6615-8c2a-42c7-a057-8b91a2abe6db)
- [Q]data-agent (5a9e8ef9-8d24-404f-9b32-9f92111850db)
- [Q]strategy-agent (7dc21a3b-78aa-49db-9f2b-5ded916975c2)
- [Q]trade-agent (fbfe49a9-fac3-4be7-9a6c-a7d4fabc36eb)
- [Q]risk-agent (54a3c5ef-e522-471f-a23d-5dbdda410821)
- [Q]devops-agent (094eb292-b81f-4c8d-94d1-b97461b8ee68)

## v0.3 Changes

- 旧 [Q]* 体系 6 个 agent 全部归档
- P1 接线：4 个 quant agent 接入 minimax-search / rss-news / mmx-cli / multica-memory
- P2 增加 3 个 autopilots：盘后因子复算、周度回测体检、周度经验汇总
- P3 L1 instructions 增补「可观测性 / 失败模式 / 退出码」章节（每个 agent）
- v0.2 → v0.3 平台与仓库同步

## v0.2 Changes

- Added executable Multica workflow rules: issue status transitions, comment handoff format, blocking behavior, and review boundaries.
- Added data environment and data quality acceptance criteria for tushare + akshare workflows.
- Added stricter A-share backtest requirements: universe, adjustment method, suspension/limit-up handling, survivorship bias, look-ahead checks, costs, slippage, benchmark, and walk-forward validation.
- Added explicit execution boundary: the team produces research recommendations only and must not place real trades.
- Added Meta-Team L1/L2/L3 evolution schema, trigger metrics, approval rules, and before/after comparison requirements.
- Set `quant-orchestrator` expectation to workspace visibility and squad leader role.

## Operating Boundary

This team is a research and analysis team. It may produce data reports, candidate portfolios, risk reports, backtest evidence, and paper-trading recommendations. It must not directly place real-money orders or present research output as guaranteed investment advice.
