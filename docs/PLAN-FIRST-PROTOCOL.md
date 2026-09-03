# 修改前调查与计划

## 默认规则

用户通常只知道现象，不知道根因和文件位置。除非范围、验收和验证已经明确，Vega 任务都先做
只读调查，再让人工批准 Change Contract。

```text
用户描述
  -> 只读调查
  -> Change Contract + Execution Plan
  -> 人工批准
  -> Worker
```

“任务很小”不是跳过调查的理由。只有调用者已经给出精确范围、成功条件和验证方式，并明确要求
直接执行时，主会话才可以把调查缩成快速核实。

## 调查内容

主会话至少检查：

1. 仓库根和相关目录的 `AGENTS.md`；
2. `.vega.yaml`；
3. Git 状态和当前 revision；
4. 入口、调用链和失败路径；
5. 相关测试、复现命令和既有行为；
6. 数据库、支付、并发、权限、数据删除、部署和外部写入风险。

调查阶段只读。不要先改代码，再用修改后的结果倒推计划。

## 事实与假设

计划必须把以下内容分开：

### Observed Facts

已经从代码、命令、测试或运行证据确认的内容。每条写清来源。

### Hypotheses

仍需 Worker 验证的根因或实现判断。假设不能写成合同事实。

### Unresolved Decisions

需要用户决定的问题，例如是否允许 Schema 变化、公共 API 变化或新增依赖。

权威顺序：

```text
用户要求 / 项目规则 / .vega.yaml
  > Change Contract
  > 已观察事实
  > 假设和实现建议
```

## Change Contract

Contract 回答“Agent 被授权做什么”：

```json
{
  "task_id": "export-button-fix",
  "contract_revision": 1,
  "goal": "修复导出按钮点击后无响应",
  "acceptance": [
    "合法筛选条件下会启动一次导出",
    "接口失败时页面显示错误并允许重试"
  ],
  "invariants": [
    "同一次点击不能创建重复导出任务"
  ],
  "non_goals": [
    "不重写整个导出模块"
  ],
  "authorized_risk_reviews": [],
  "required_verification": [
    "导出按钮定向测试",
    "导出 API 回归测试"
  ],
  "authority_envelope": {
    "allowed_paths": [
      "src/export/**",
      "tests/export/**"
    ],
    "forbidden_paths": [],
    "max_changed_files": 8,
    "max_repair_rounds": 3,
    "max_auto_replans": 1,
    "max_review_rounds": 4,
    "max_verification_retries": 1
  }
}
```

`authorized_risk_reviews` 只使用 `.vega.yaml` 中登记的风险 ID。自然语言 Planning
阶段保持该字段为空；Contract Compiler 根据候选文件确定命中的 ID，再写入待批准合同。
Planner 对风险的解释放在 Work Item 的 `risk_notes`，不能自行发明机器风险标识。Provider
仍输出旧式自由文本时，计划卡只把它显示为非授权提示，不会拿它放宽或替代项目风险规则。

未列出的副作用默认不授权。需要数据库、公共 API、依赖、部署、支付、权限、数据删除或验证期间
外部写入时，必须显式写入 `side_effect_policy`。

## Execution Plan

Plan 回答“当前准备怎么做”：

```json
{
  "task_id": "export-button-fix",
  "contract_revision": 1,
  "plan_revision": 1,
  "observed_facts": [
    "按钮事件已绑定，失败发生在请求状态没有恢复"
  ],
  "hypotheses": [
    "异常分支没有清理 pending 状态"
  ],
  "work_items": [
    {
      "work_item_id": "WI-01",
      "objective": "修复导出请求状态并补回归测试",
      "depends_on": [],
      "likely_files": [
        "src/export/**",
        "tests/export/**"
      ],
      "verification": [
        "python -m pytest tests/export -q"
      ],
      "risk_notes": [
        "检查重复点击和失败重试"
      ]
    }
  ],
  "implementation_strategy": [
    "先复现，再做最小修复"
  ],
  "additional_checks": [
    "git diff --check"
  ],
  "unresolved_decisions": []
}
```

Work Item 使用粗粒度边界。通常 1～4 项，最多 8 项。不要把每个函数或测试命令拆成一个
Work Item。每项的 `verification` 必须在该项完成时即可运行，不能引用后续 Work Item 才会
出现的文件或命令。Contract 的 `required_verification` 在最后一项执行，作为整次变更的最终
验收；`additional_checks` 也在此时补跑。中间项没有声明局部验证时，Vega 会保守回退到合同
验证，无法运行就停止，不会无验证推进。

## 人工批准

主会话展示：

- 目标和验收；
- 事实与假设；
- 允许和禁止范围；
- 高风险与副作用；
- Work Item；
- 必跑验证；
- 未决问题。

用户可以批准、修改、缩小或拒绝。批准后 Contract revision 冻结。范围、验收、风险或副作用
变化时，旧批准失效。

## 执行中的变化

### 合同内变化

以下变化可以由 Agent 通过 Execution Plan revision 处理：

- 调整实现算法；
- 调整 Work Item 顺序；
- 在已批准范围内更换候选文件；
- 增加测试或静态检查；
- 缩小范围；
- 修正已证伪的根因假设。

### 需要重新批准

- 用户可见行为或验收变化；
- 必跑验证被删除或弱化；
- 越出允许路径；
- 公共 API、数据库 Schema、新依赖或部署动作；
- 新出现的支付、权限、数据删除或外部写入；
- 自动预算需要扩大；
- fail-closed 边界需要改变。

Vega 按结构化字段和真实 Diff 判断，不让另一个模型决定“这次变化算不算重大”。

## Reviewer 的职责

Reviewer 判断 Candidate 是否满足当前 Contract 和 Work Item。它可以返回：

- `approve`；
- `repair`；
- `replan`；
- `needs_human`。

Reviewer 不直接改 Contract，也不批准自己的新方案。普通问题转成 Fix Packet 自动回给 Worker；
合同变化才回到人工。
