# Gate 7 R4 测试闭环结果

> 状态：`ready-for-baseline-freeze-not-frozen`
>
> 日期：`2026-07-20`
>
> 时区：`Asia/Shanghai`
>
> 已验证实现提交：`private-gate-7-r4-crash-marker-fix-redacted`

## 1. 结论

Gate 7 R4 的全量测试门槛已经闭合：

```text
collected = 838
covered = 838
passed = 838
failed = 0
skipped = 0
timeout unresolved = 0
```

因此，原 `761/838` 的 baseline freeze 阻塞已经解除。

本轮只完成确定性测试和恢复语义修复，没有：

- 调用真实 Provider；
- 创建 R4 baseline tag；
- 创建 R4 consumed tag；
- 改动 provider、模型、reasoning、CLI 或 retry 策略；
- 删除或改写 R1～R3 的失败证据。

当前状态是“具备 baseline freeze 条件”，不是“已经冻结 baseline”，更不是“真实 Gate 7A
已经启动或成功”。

## 2. 短路径复验

原交接记录中的 77 个未闭合记录来自 Windows 深路径、文件系统原子操作和分片时限混合
现象。2026-07-20 在短路径 checkout `<short-checkout>` 中重新收集：

```text
838 tests collected
```

先对原 77 条使用五个完整文件的保守超集，共 `122 passed`。随后对全量测试重新分片：

| 证据来源 | 通过数 |
| --- | ---: |
| 27 个完整文件分片 | 473 |
| 12 个超时文件拆成完整 node id | 348 |
| 系统 Python 下 execution control 完整文件 | 17 |
| 合计 | 838 |

12 个文件级分片在冻结的 58 秒窗口内没有完成，因此不计通过；其 `348` 个完整 node id
随后全部得到明确终态。没有把超时当成通过。

## 3. 发现并修复的真实回归

短路径复验唯一稳定失败为：

```text
tests/experimental/langgraph_engine/test_crash_windows.py::
test_abrupt_process_exit_with_unsealed_checkpoint_stops_without_replay
```

现场满足：

```text
child returncode = 86
worker start count = 1
terminal execution exists = true
Step Result exists = true
state.json current_step = worker
graph-state.json exists = false
```

根因不是 LangGraph `1.2.9` 或 SQLite 数据漂移。旧 manifest 仍然正确绑定“进入 worker
之前”的上一条 checkpoint，所以文件哈希、大小、游标和 writes 数量都能自洽；但
`os._exit(86)` 不执行 Python `finally`，Runtime 缺少一条持久化证据来说明：

```text
Step Result 已写，
但包含该结果的下一条 Graph checkpoint 尚未提交。
```

仅校验 SQLite 自洽性会误把“上一条可信 checkpoint”解释成“本次 Graph 提交已经完成”。

修复新增 `graph/checkpoint-pending.json`：

1. terminal execution、workspace evidence 和 Step Result 完整落盘后写 marker；
2. 只有新的 checkpoint 行已经提交且 manifest seal 成功后才清除；
3. `put_writes` 仍可能只是中间态，不允许提前清除；
4. 正常异常路径可在最终 seal 后清除；
5. `os._exit` 不执行清理，marker 被保留；
6. 恢复校验发现 marker 后 fail-closed，不打开可写 SQLite checkpointer，也不重启 worker。

该 marker 不是业务状态、execution 终态或第二套事实源，只表示“Graph 提交边界尚未闭合”。

## 4. 验证证据

代码身份：

```text
verified commit =
private-gate-7-r4-crash-marker-fix-redacted

source bundle SHA-256 =
b00770154073e8942653c657bb42ce790dfb4c546fe1b8440b23396cf78c6c46
```

本机忽略证据：

```text
.local-validation/gate-7-r4-shortpath-fix-20260720/
```

核心文件：

```text
aggregate-summary.json
coverage-manifest.json
file-shards-summary.json
unresolved-nodes-summary.json
execution-control-system-python.txt
```

关键 SHA-256：

```text
collect-all.txt =
577edcabfb27df581b48ecac2699c455709473f97320479ccedf719324c478aa

file-shards-summary.json =
bff6441736b35e1d3c4e5d878b54788db55ef06789fb9d11e3c2521cc6008fcc

unresolved-nodes-summary.json =
71edc904e6c7a59222e4f89dc1a6ec8294027f550aa31f9661195f63905fc65b

execution-control-system-python.txt =
b84bc2ddf2da468ec1614821f3f21ee959c3465c279bf00d403ed12815f9cc06
```

静态验证：

```text
python -m compileall src
ruff check src tests
git diff --check
```

均明确通过。

## 5. 下一步与停止线

下一步只能是：

1. 由项目 owner 明确授权 baseline freeze；
2. 创建并推送两个 R4 annotated baseline tag；
3. 复核分支与两个 tag peel 到同一已验证提交；
4. 再按预注册合同只执行一次真实 Gate 7A；
5. Gate 7A 成功且 transcript 全链复验通过后，才允许条件触发 Gate 7C。

在 owner 再次授权前，继续保持：

```text
real provider calls = 0
R4 baseline tags = 0
R4 consumed tags = 0
```
