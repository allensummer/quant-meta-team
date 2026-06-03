# quant-risk-weekly-backtest-audit

- **Autopilot ID**: `24088cdc-5ab5-40cb-b8e4-fbc5a2410883`
- **Mode**: `create_issue`
- **Agent**: `quant-risk-agent` (926b5b84-250f-4dfc-9407-5fceb479fe49)
- **Trigger**: `0 10 * * 6` Asia/Shanghai (Saturday 10:00)
- **Issue Title Template**: 【周度回测体检】{{date}}

## 任务目标

每周对量化组合在样本外数据上执行严谨回测体检，验证 L1/L2/L3 变更后的实操表现，输出 walk-forward 指标供 Meta-Team 自进化决策。

## 执行流程

1. 选取 1-2 个标准股票池（沪深 300 + 中证 500）作为回测基准
2. 对当前 squad 配置生成的组合执行 walk-forward 验证：训练 12 个月 / 测试 1 个月 / 滚动 / 后复权 / 调仓 5 或 10 日
3. 报告指标：年化收益、夏普、最大回撤、胜率、换手率、行业集中度
4. 偏差检查：未来函数、幸存者偏差、停牌/涨跌停处理、交易成本与滑点
5. 与上一周期对比：metrics delta、显著退化项
6. 贴出可复现的 notebook / script 路径（`~/Code/quant-meta-team/backtest/`）

## 验收门禁

- 至少 4 个 walk-forward 窗口
- 必须报告基准对比（沪深 300、中证 500）
- 必须报告最大回撤、波动率、夏普
- 交易成本不低于单边 0.1%，滑点不低于 0.1%

## 失败升级

- 出现未来函数/幸存者偏差：立即 issue blocked，@mention @quant-orchestrator
- 连续 2 周最大回撤 >20%：触发 L3 章程审查
- walk-forward 结果与样本内偏差 > 30%：触发 L2 协议审查
