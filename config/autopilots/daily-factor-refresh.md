# quant-data-daily-factor-refresh

- **Autopilot ID**: `2b45bf14-a6b2-44e6-bb33-04f9a3fc31da`
- **Mode**: `create_issue`
- **Agent**: `quant-data-agent` (93bd9feb-c40b-42dd-93d7-08f0c58e00c3)
- **Trigger**: `17 15 * * 1-5` Asia/Shanghai (weekday 15:17, after A-share close)
- **Issue Title Template**: 【盘后因子复算】{{date}}

## 任务目标

盘后/次日开盘前，对 A 股核心股票池执行数据更新与基础因子复算，输出当日数据质量报告，供 quant-portfolio-agent 与 quant-risk-agent 使用。

## 执行流程

1. 拉取上一交易日行情数据：使用 tushare/akshare 优先获取日线行情、复权因子、涨跌停状态、停牌标记
2. 计算基础因子：价值(PE/PB)、成长、动量(20/60日)、质量(ROE)、换手率
3. 输出文件到 `~/Code/quant-meta-team/data/daily/{{date}}/`
4. 在 issue comment 中报告：股票数、字段数、缺失率、限流次数、异常值数量
5. 数据质量失败率 >5% 或 关键字段缺失率 >0 时，issue 设为 `blocked` 并 @mention @quant-orchestrator

## 验收门禁

- 核心价格字段缺失率为 0
- 因子矩阵覆盖至少 95% 股票池
- 缓存文件可追溯（记录数据源/拉取时间/参数）

## 失败升级

- 限流连续 3 天：升级 L1 经验记录
- 数据源全面失败：issue 设为 blocked 并 @mention @quant-orchestrator 决策
