# Gate 0 基线冻结

> 状态：`frozen-gate-0-baseline`
>
> 冻结日期：2026-07-15
>
> 实验分支：`experiment/langgraph-comparison`
>
> Gate 0 开始前 HEAD：`private-gate-0-contract-redacted`
>
> 代码实验基线：`private-experiment-base-redacted`

---

## 1. 基线身份

本实验区分两个不同身份：

- Gate 0 开始前 HEAD 是 `private-gate-0-contract-redacted`，它只在 `private-experiment-base-redacted` 上增加两份 LangGraph 实验文档。
- 代码实验基线是 `private-experiment-base-redacted`，第一轮不得把其他实验分支的实现静默纳入 A/B 条件。

Gate 0 之后新增的契约、测试或实验代码会继续推进当前分支 HEAD，因此任何文档不得再把
`private-experiment-base-redacted` 写成“当前 HEAD”。它只能被称为代码实验基线或 merge base。

当前分支在 Gate 0 开始前相对 `main@private-experiment-base-redacted` 的差异为：

```text
docs/experiments/langgraph-orchestration/DEMO.md
docs/experiments/langgraph-orchestration/INDEPENDENT-REVIEW-AND-EXECUTION-PLAN.md
```

当时没有 LangGraph 依赖、实现代码或实验测试。

## 2. 环境冻结

```text
OS: Windows 10.0.19045
Python: 3.12.10
Python executable: <python-executable>
Git: 2.45.1.windows.1
Shell: PowerShell
```

当前 Python 环境未安装 `langgraph`。Gate 1 不依赖 LangGraph；Gate 2 如需安装，只能使用
项目内隔离环境或明确的可选依赖，不得修改用户全局环境。

## 3. 依赖与默认行为

代码基线的核心依赖只有：

```text
pydantic
pyyaml
typer
```

开发依赖只有：

```text
pytest
ruff
```

Gate 0 固化以下默认行为：

- 默认编排引擎是 `linear`。
- `assist` 不启动外部 worker。
- `auto` 的 worker 使用 `workspace-write`。
- reviewer 使用独立 `read-only` runner。
- verification failure、evidence stale、workspace drift 或 `human-review` 不得被模型
  `approve` 覆盖。
- 不自动 commit、push、release 或接受长期 Memory。
- 第一轮 deterministic Gate 不调用真实 provider。
- 第一轮目标 fixture 不创建长期 memory ledger，accepted memory hit 必须为 0。

当前 `.vega.yaml` 没有显式模型名：

```text
worker reasoning_effort: medium
reviewer reasoning_effort: high
worker ephemeral: true
reviewer ephemeral: true
```

因此真实 runner 若进入后续 dogfood，实际模型身份必须在运行前重新预注册，不能用继承配置
推断模型。

## 4. 历史主线质量基线

`experiment/daily-loop-dogfood-mainline` 已对纯主线 `main@private-experiment-base-redacted` 的 Git 已跟踪测试记录过：

```text
332 collected
331 passed
0 failed
1 skipped
0 timeout
```

该结果使用 73 个独立 pytest 分片，每个子进程限制 58 秒。唯一 skipped 是当前 Windows
账户无法创建目录 symlink。

这是已有历史证据，不是本次 Gate 0 重新执行的全量测试。后续文档引用时必须保留来源分支、
基线 SHA 和执行口径。

## 5. Gate 0 当前验证

本次 Gate 0 开始前完成：

```text
python -m compileall -q src
ruff check src tests --cache-dir .tmp/ruff/cache/inspection
python -m pytest --collect-only -q
git diff --check
```

结果：

```text
compileall: passed
ruff: passed
pytest collection: 332 tests
git diff --check: passed
```

针对后续编排最相关的不变量，完成以下分片验证：

```text
tests/test_p0_regressions.py: 12 passed
tests/test_execution_control_safety.py: 12 passed
tests/test_path_and_eval_contract.py
  + tests/test_project_config_hardening.py: 25 passed
tests/test_review_artifact_integrity.py: 18 passed
```

合计：

```text
67 passed
0 failed
```

`test_p0_regressions.py` 和 `test_review_artifact_integrity.py` 的首次整文件命令超过 60 秒，
按项目规则不计通过或失败；随后按完整函数集合拆分，所有 node 得到明确通过终态。

本轮没有重新运行 332 个测试的完整分片集合，因此不能把 `67 passed` 写成新的全量质量基线。

## 6. 已知性能与实验风险

- Git workspace snapshot、Reflect、Risk Gate 和 Review 会重复读取状态与 diff，部分测试文件
  单进程超过 60 秒。
- `LoopAutomationRuntime._run_auto_iterations()` 是长过程控制器，状态保存、trace 和 artifact
  写入分散在多个提前返回路径中。
- LangGraph 不能通过把整个线性方法包装成一个黑盒节点来满足 Gate 1/2。
- checkpoint、`state.json`、`execution.json` 和 workspace 不能形成跨介质原子事务。
- 其他分支中的 Scope Gate、Selective Memory 和 dogfood 结果不得作为当前实现事实。

## 7. Gate 0 基线结论

Gate 0 的代码比较起点固定为 `private-experiment-base-redacted`，当前分支从 `private-gate-0-contract-redacted` 继续追加实验契约和实现。

进入 Gate 1 前必须满足：

- 契约文档对上述身份和测试口径表述一致；
- 独立 reviewer 没有未关闭 Blocker / High；
- 第一轮实现不依赖 LangGraph；
- linear 默认语义和现有 artifact contract 保持不变。
