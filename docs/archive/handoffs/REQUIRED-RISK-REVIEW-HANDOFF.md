# 必审高风险变更接力说明

## 当前结论

本轮实现与可信性修复已经完成，并在同一代码快照下取得全量 pytest 通过证据。

功能边界仍然不变：命中 `risk.required_reviews` 后只读 Reviewer 负责生成结构化人工检查
材料，最终固定为 `needs_human`，不能自动升级为 `ready_to_commit`。

## 本轮实现

### 可配置的必审风险

目标仓库可以在 `.vega.yaml` 中配置：

```yaml
risk:
  required_reviews:
    - id: payment
      label: 支付与资金
      paths:
        - src/payments/**
```

命中后：

- Gate 固定输出 `high / human-review`；
- Reviewer 必须逐类覆盖全部命中文件，说明变更、证据和剩余风险；
- 持久化 verdict 固定为 `needs_human`；
- `issue_found` 必须关联同文件且标题、证据、建议均非空的 finding；
- 缺失、非法、截断或不可信的 Reviewer 结果会生成逐类
  `insufficient_evidence`。

### 未放松的早停边界

命名风险不能掩盖其他人工早停条件。预算超限、删除文件、新依赖、未覆盖的高风险路径或
项目显式人工规则仍会在 Reviewer 前停止。

只有已被 `required_reviews` 覆盖的高风险文件才允许启动只读 Reviewer 生成披露。
Loop、Finish 和风险门禁完整性复核使用同一判断，不能通过伪造 `needs_human` Reviewer
证据绕过早停。

### Reviewer Runner 可信条件

只有同时满足以下条件时才解析 Reviewer 输出：

```text
status == success
error is None
termination_unconfirmed is False
```

`skipped`、`error`、`timed_out`、`stopped`，以及畸形的终止确认值，即使携带合法
`approve` JSON，也不能进入正式 verdict。原始输出只保留为人工接管材料。

### 最终只读审查修复

全量验证前的两轮只读审查发现并修复：

- Goal 不再只比较可同步修改的 Gate state/result，而是使用 Goal 自身绑定的 scope profile
  对当前仓库重新计算 Gate；
- `None`、空字符串和 `default` 统一表示默认 scope，命名 scope 去除首尾空白；
- standalone review 即使被篡改为 `status=success`，只要重算 Gate 仍要求人工，Goal
  checkpoint 就不能完成；
- `needs_human` 与证据新鲜度分离，合法人工接管不再被误报成工作区变化；
- 正常风险披露必须为每个命中文件提供正行号；`insufficient_evidence` 可使用 `line=0`
  表示只能定位到文件级；
- Windows venv 测试使用真实基础解释器启动 crash-recovery owner，避免把 venv launcher
  PID 误当成实际锁持有者 PID。

### Review Pack

`review-pack` 现在也会绑定确定性 Risk Gate。命中必审风险时，Prompt 包含全部风险 ID、
命中文件和 `risk_disclosures` 合同；Gate 失败时状态为 `needs_human`。

## 已完成验证

2026-07-29 的最终代码快照已通过：

```text
896 tests collected
888 passed, 8 skipped

python -m compileall src scripts/check_repository_hygiene.py scripts/check_architecture_growth.py
python scripts/check_repository_hygiene.py --base-ref origin/main
ruff check src tests scripts/check_repository_hygiene.py scripts/check_architecture_growth.py
python scripts/check_architecture_growth.py --base-ref origin/main
git diff --check
```

架构增长门禁结果：

```text
C901 39 -> 38
Python 模块 69 -> 76
```

关键定向验证包括：

- 必审高风险完整分片：`60 passed`；
- P0/CLI/锁分片：`137 passed, 2 skipped`；
- Goal Gate scope 同步篡改、standalone review status 篡改和默认 scope 别名：通过；
- 命名风险与预算超限组合：`2 passed`；
- Review Pack 命名风险绑定与 Gate 失败路径：分别通过；
- 非成功 Runner、空错误、畸形终止确认和空壳 finding 回归：分别通过；
- 风险配置与合同测试：通过。

8 个 skipped 均为当前 Windows 环境缺少符号链接权限或仅适用于 POSIX 的专项。pytest
唯一 warning 来自本地验证关闭 cache provider 后，配置中的 `cache_dir` 不再由插件识别；
它不属于产品代码 warning。

## 下一步

必审高风险功能不再继续扩张。主线下一项工作回到已预注册的 CRWP-V1：先解决 Sequelize
oracle 的旧 constraint parser 隔离，再重新冻结三份 oracle、资格和目标仓库快照。

## 范围约束

- 不新增产品能力；
- 不继续拆分 helper 模块；
- 不修改或重写 `eval/` 历史证据；
- 不把 `.local-validation/`、`.tmp/` 或其他本机产物提交；
- 不将本机绝对路径、环境文件、凭据或私密文件写入公开内容；
- 后续改动仍必须重新完成同等验证，不能复用本次快照的结论。
