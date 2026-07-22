# Gate 5.1 Hardening 结果

> 状态：`pass`
>
> 日期：2026-07-18（星期六）
>
> 时区：`Asia/Shanghai`
>
> 分支：`experiment/langgraph-comparison`
>
> 最终独立复审：`Blocker=0 / High=0 / Medium=0`
>
> 真实 provider 调用：`0`

---

## 1. 结论

Gate 5.1 已关闭 Gate 5 最终复审期间发现的恢复、原子发布、事件语义和 identity 绑定风险。
默认产品行为没有改变：

```text
engine = linear
review topology = single reviewer
```

Gate 5.5 仍为 `not started / not authorized`。本结果只证明确定性安全和恢复合同，不证明真实
模型质量、成本收益或多 Reviewer 应成为默认产品行为。

## 2. 已关闭风险

### 2.1 Seal 后 checkpoint 替换

- terminal recovery 第二次打开绑定刚 seal 的 `TrustedCheckpointState`；
- 新增完整 runtime 攻击 fixture；
- 攻击会在 seal 后替换为另一份相同 `run_id`、可独立通过 manifest 校验且数据库哈希不同的
  checkpoint；
- 第二次 checkpointer 在交给 Graph 前拒绝现场，不写 `graph_terminal_recovered`，不重调
  worker/provider。

### 2.2 Artifact 原子发布

- redacted text/json 使用同目录唯一临时文件、LF、flush、fsync 和 `os.replace()`；
- Windows `PermissionError` 使用有限重试，失败后清理临时文件且不截断旧文件；
- archive 使用 create-once hard-link 发布，既有不同内容不能被覆盖；
- checkpoint 撤销路径的 report、archive、eval 和 finish summary 均使用原子写；
- `finish-summary.json` 最后发布，作为机器提交标记；
- summary 绑定 `finish-report.md` 的 SHA-256，Goal 消费者拒绝旧 summary 与新 report 的
  半发布组合。

### 2.3 撤销事件语义

```text
run_terminal_state_revoked
  -> 权威 state.json 已原子转为 needs_human

run_terminal_revoked
  -> eval、state 和交付报告补偿阶段全部完成
```

完成事件之后不再有本次补偿事务的必需写入。validator 同时接受历史单事件序列，并拒绝新旧
事件 reason/status 不一致的伪造组合。

### 2.4 Owner PID 与 HITL

- active execution 的 live owner/child 一律拒绝 Graph recovery；
- terminal child 仍存活时一律拒绝；
- terminal live owner 只有在 run 级 Graph operation lock 已释放，且可信 Step Result 完整
  绑定 execution 与 attempt 时才允许继续 HITL；
- terminal execution 缺少提交证据或 identity 错绑时仍拒绝；
- `termination_unconfirmed=true` 始终 fail-closed。

### 2.5 Step Result Identity

Step Result 读取和复用现在同时绑定：

```text
文件名 step_id
manifest.step_id
attempt.json
execution.json
run / engine / graph schema
step / iteration / attempt
idempotency / replay class
runner identity
base head / workspace fingerprint
policy snapshot / input fingerprint
command hash / execution hash
```

新增文件名别名和“execution + Step Result 彼此自洽、但与 attempt 错绑”的攻击测试；两者均
fail-closed。

### 2.6 依赖与 Stop 合同

- `pytest --require-langgraph` 会实际导入 `langgraph` 与
  `langgraph.checkpoint.sqlite`，模块可发现但导入失败时会话直接失败；
- `vega stop` 建立永久 run 级 latch，向全部 active execution 广播 stop request，并阻止
  同一 run 后续 execution 启动。

## 3. 验证证据

超过 60 秒的执行全部不计通过；最终数字来自重新拆分后的 node 级结果：

```text
Parallel Review runner adapter：36 passed
Parallel Review resume：6 passed
Checkpoint resume：20 passed
Crash windows：31 passed
HITL interrupt / resume：5 passed
Decision binding：15 passed
Step Result identity binding：7 passed
Finish artifact integrity：22 passed
Redaction：32 passed
Goal finish binding：3 passed
LangGraph dependency gate：4 passed
```

静态检查：

```text
python -m compileall -q src：通过
ruff check src tests：通过
git diff --check：通过，仅有既有 LF/CRLF 提示
```

## 4. 最终决定

```text
Gate 5 = pass
Gate 5.1 = pass
Gate 5.5 = not started, not authorized
real provider calls = 0
```

下一步只能冻结 Gate 5.5 预注册合同，包括 provider/model、数据集、ground truth、数据出站、
token/延迟预算、stop 条件和失败口径。预注册完成并再次确认授权前，不调用真实 provider。
