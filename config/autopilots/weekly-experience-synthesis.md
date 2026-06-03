# quant-orchestrator-weekly-experience-synthesis

- **Autopilot ID**: `c04b1b6a-816a-4388-beea-2bddae9c3564`
- **Mode**: `create_issue`
- **Agent**: `quant-orchestrator` (50cc665a-5d3b-471a-a7c2-6285f1440594)
- **Trigger**: `0 10 * * 0` Asia/Shanghai (Sunday 10:00)
- **Issue Title Template**: 【周度经验汇总】{{date}}

## 任务目标

每周扫量化小队过去一周的所有子 issue，提取 L1/L2/L3 经验教训，更新 squad instructions 与 agent instructions 草案，推动 Meta-Team 持续自进化。

## 执行流程

1. 扫描本周量化小队所有 issue（status: in_review / done / blocked）
2. 提取经验，按 Level 分类：
   - L1（agent-level）：因子优化、字段口径、数据质量、组合约束、回测检查
   - L2（interaction-level）：交接失败、返工、阻塞、信息缺口、通信协议
   - L3（team-level）：章程、风控标准、角色分工、交易边界
3. 写入 `~/Code/quant-meta-team/experience/weekly-{{date}}.md`，遵循 squad instructions 中的 experience schema
4. 对于高频失败（同一症状 ≥3 次），生成 L2/L3 修改草案写入 squad instructions 草稿区
5. 在 issue comment 中汇总：扫描 issue 数、L1/L2/L3 经验数量、提议的指令变更、待批准的高风险变更
6. 提交 PR 到 https://github.com/allensummer/quant-meta-team

## 验收门禁

- 每条经验必须有 issue/evidence 引用
- 提议的 L1/L2/L3 变更必须满足 experience schema
- 高风险变更（涉及风控放宽、真实交易、密钥、章程）必须标 `approval_required: true`，不得自动应用

## 失败升级

- 关键 L3 变更需 @mention 用户批准后才可应用
- 数据/回测发现严重偏差：立即 issue blocked
