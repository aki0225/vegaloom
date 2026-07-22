# Gate 4.5 R2 Readiness

> 状态：`local-hardening-pass`
>
> R2 状态：`not started`
>
> 日期：2026-07-16
>
> 父基线：`private-gate-4-5-r1-blocked-baseline-redacted`

---

## 1. 结论

R1 后的本地工程加固和完整分片验证已经完成，但这不解除 Gate 4.5 的真实 Runner 阻塞。

本轮结论：

```text
Gate 3 / Gate 4 deterministic 结论 = 保留
Post-R1 local hardening = pass
Gate 4.5 R2 = not started
Gate 5 = 暂不进入
```

本轮没有调用真实模型，没有读取 Codex credential store，没有修改全局 provider、profile 或
Python 环境，也没有创建新的 Gate 4.5 业务 Case。

## 2. 本轮修复

### 2.1 Optional dependency 测试合同

Gate 4.5 dogfood harness 会导入 LangGraph Runtime。此前基础环境未安装
`vegaloom[langgraph]` 时，14 个 harness 测试会在导入阶段失败，而不是明确跳过。

修复后：

- 文件标记为 `requires_langgraph`；
- 缺少 `langgraph` 或 `langgraph.checkpoint.sqlite` 时，14 个测试明确 skipped；
- module spec 存在但真实 import 失败时，测试明确 failed，不会把损坏安装误报为 skipped；
- 安装 optional extra 后，14 个测试正常执行并全部 passed；
- 默认 Linear Runtime 仍不要求安装 LangGraph。

### 2.2 Decision consumption 独占发布

此前 consumption 写入流程为：

```text
path.exists()
  -> write temp
  -> os.replace(final)
```

两个进程可能同时通过 `exists()` 检查，并由最后一个 `replace` 覆盖前一个 decision
identity。

修复后：

```text
final 已存在
  -> 只读校验既有 consumption
  -> 同一 identity 返回既有记录
  -> 不同 identity fail-closed

final 不存在
  -> serialize
  -> write and fsync unique temp
  -> os.link(temp, final)
  -> unlink temp
  -> 竞争失败者读取并校验 final
```

同一文件系统内的 hard-link publish 具备 create-once 语义：

- 已存在的合法 consumption 不再进入临时写入路径；
- final 不存在时，只有一个 writer 能创建；
- final 已存在时，不覆盖；
- 同一 decision identity 并发或重试时返回既有 consumption；
- 不同 decision identity 并发时，一个成功，另一个 fail-closed；
- 正式 artifact 不会暴露半写入 JSON。
- 临时文件写入、`fsync`、独占发布和清理失败统一归类为
  `GraphDecisionValidationError`。

当前项目所在 Windows 文件系统已完成 hard-link capability probe。

独立 reviewer 未发现 Blocker / High；提出的两个 Medium 已关闭：

- 既有 consumption 的幂等重试恢复为只读快路径；
- optional extra 已安装但损坏时不再被 `importorskip` 隐藏。

## 3. 隔离验证环境

验证环境只位于被忽略的：

```text
.tmp/langgraph-validation-venv/
```

实际版本：

```text
Python = 3.12.10
pytest = 9.1.1
ruff = 0.15.21
langgraph = 1.2.9
langgraph-checkpoint-sqlite = 3.1.0
pydantic = 2.13.4
```

环境占用约 `96 MiB`，`pip check` 结果为：

```text
No broken requirements found.
```

该环境不进入 Git，不影响全局 Python。

## 4. 验证结果

### 4.1 基础环境

基础 Python 未安装 LangGraph optional extra。

```text
collected = 332
passed = 331
skipped = 1
failed = 0
```

唯一 skip 是当前 Windows 权限不允许创建目录 symlink。

Gate 4.5 harness 在该环境中额外验证为：

```text
14 skipped
```

这 14 个 node 属于 experimental 收集计数，不属于上述 332 个基础 node。

### 4.2 LangGraph 环境

```text
collected = 131
passed = 130
skipped = 1
failed = 0
```

唯一 skip 是当前环境不允许创建 checkpoint 目录链接。

覆盖范围包括：

- engine selection；
- Graph State、Step Result 与 checkpoint contract；
- handler boundary 与 legacy compatibility；
- 8 个 crash window node；
- 15 个 Linear / LangGraph semantic parity node；
- structured HITL、decision binding 与 CLI resume；
- 新增并发 consumption；
- 14 个 fake dogfood harness node。

审查后受影响测试的明确结果：

```text
test_decision_binding.py = 15 passed
test_core_dogfood_harness.py = 14 passed
```

### 4.3 合计

```text
collected = 463
passed = 461
skipped = 2
failed = 0
```

其中上一轮完整分片基线为 `459 collected / 457 passed / 2 skipped`；独立复审后新增
4 个 hardening node，LangGraph 实验目录重新 collect 为 131 个 node，且受影响文件完整
15 个 node 已全部重跑通过。

静态验证：

```text
python -m compileall -q src tests scripts
ruff check src tests scripts
git diff --check
pip check
```

结果全部通过。

## 5. Timeout 处理

以下组合分片超过外层 60 秒限制，均未计入通过：

- 四个并行 semantic parity 组合；
- 六个 Graph success consumer 参数组合；
- `test_evidence_freshness.py` 整文件；
- `test_finish_artifact_integrity.py` 整文件及一个较大组合；
- `test_success_semantics.py` 整文件及一个较大组合。
- `test_decision_binding.py` 整文件，以及一次并行执行的三个五节点分片。

所有内容随后按完整 node id 或更小函数集合拆分，并取得明确 passed 结果。超时后残留的本轮
pytest 进程均已检查和终止。Decision binding 最终使用 `1 + 4 + 5 + 5` 个 node 的串行分片
覆盖全部 15 个 collected tests。

五轮 Linear / LangGraph 等价 node 在无资源争用时为：

```text
1 passed in 50.74s
```

## 6. R2 仍未满足的条件

R1 的冻结合同是：

```text
profile = sandbox-provider
expected provider = sandbox-provider
model = gpt-5.6
Codex CLI = 0.144.5
```

R1 live header 实际观察到 `provider=sandboxproxy`，模型调用返回 HTTP 404。

当前本机默认 `codex` 又是：

```text
codex-cli 0.144.4
```

因此不能直接复用 R1 命令、结果目录或环境声明，也不能把本轮 deterministic 验证解释为
真实 Runner 已恢复。

## 7. R2 准入条件

创建 R2 预注册合同前，项目 owner 必须明确：

1. 真实使用的 Codex executable 路径和版本；
2. 明确的 profile；
3. live header 应观察到的 provider；
4. 该 provider 实际支持的 model；
5. worker 与 reviewer reasoning effort；
6. 是否接受新的 provider / model 合同替代 R1；
7. 外部调用预算。

冻结并提交新的 R2 合同后，仍必须先运行一次 fail-fast preflight。只有 preflight 全部通过，
才允许创建 `linear-low`、`graph-low` 和 `graph-crash-hitl` 业务 Case。

在此之前：

```text
不得启动 Gate 5
不得静默切换 provider 或 model
不得把 fake harness 解释为 real dogfood
```
