# 必审高风险变更接力说明

## 当前结论

本轮实现已经完成代码与定向回归，但**尚未取得冻结快照下的全量 pytest 通过证据**。
当前提交适合作为跨机器接力点，不应据此宣称功能已经完成最终验收。

继续工作时保持当前分支，不要再拆分模块或增加新的产品能力。下一步只完成全量验证、
处理真实失败并做最终只读审查。

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

### Review Pack

`review-pack` 现在也会绑定确定性 Risk Gate。命中必审风险时，Prompt 包含全部风险 ID、
命中文件和 `risk_disclosures` 合同；Gate 失败时状态为 `needs_human`。

## 已完成验证

当前代码快照已通过：

```text
python -m compileall src scripts/check_repository_hygiene.py
python scripts/check_repository_hygiene.py --base-ref origin/main
ruff check src tests scripts/check_repository_hygiene.py
python scripts/check_architecture_growth.py --base-ref origin/main
git diff --check
```

架构增长门禁结果：

```text
C901 39 -> 38
Python 模块 69 -> 76
```

关键定向验证包括：

- 自动模式命名风险正常路径：`1 passed`；
- 命名风险与预算超限组合：`2 passed`；
- Review Pack 命名风险绑定与 Gate 失败路径：分别通过；
- 非成功 Runner、空错误、畸形终止确认和空壳 finding 回归：分别通过；
- 风险配置与合同测试：通过。

## 未完成验证

当前共收集：

```text
893 tests collected
```

标准全量 `python -m pytest` 尚未完成：

1. 一次后台运行执行到约 17% 后被外层进程机制无摘要终止，该结果无效；
2. 随后的前台运行因本次跨机器接力被中断，相关 pytest 进程已明确停止；
3. 两次运行都不能计入全量通过证据。

本机 Git 操作存在明显延迟，部分临时仓库的 `git status` 接近或超过内部 30 秒超时。
如果晚间运行出现 `subprocess.TimeoutExpired`，先确认是 Git 环境延迟还是代码断言失败，
不要为了追求全绿现场修改业务语义。

## 晚间继续步骤

```powershell
git pull --ff-only
git status -sb
git rev-parse HEAD

python -m pytest

python -m compileall src scripts/check_repository_hygiene.py
python scripts/check_repository_hygiene.py --base-ref origin/main
ruff check src tests scripts/check_repository_hygiene.py
python scripts/check_architecture_growth.py --base-ref origin/main
git diff --check
```

如果全量 pytest 失败：

1. 保留完整输出；
2. 区分断言失败、Git 30 秒超时和进程终止；
3. 只修复可稳定复现的真实问题；
4. 修复后重新从完整冻结快照执行全量测试，不能把不同代码快照的局部结果拼成全绿。

## 范围约束

- 不新增产品能力；
- 不继续拆分 helper 模块；
- 不修改或重写 `eval/` 历史证据；
- 不把 `.local-validation/`、`.tmp/` 或其他本机产物提交；
- 不将本机绝对路径、环境文件、凭据或私密文件写入公开内容；
- 全量验证和最终只读审查通过前，不宣称完成最终验收。
