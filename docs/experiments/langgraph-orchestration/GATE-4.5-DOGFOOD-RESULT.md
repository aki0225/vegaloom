# Gate 4.5 Core Dogfood 结果

> 最终分类：`blocked`
>
> 日期：2026-07-16
>
> 执行基线：`private-gate-4-5-core-dogfood-baseline-redacted`
>
> 真实 session：`real-core-20260716-private-gate-4-5-core-dogfood-baseline-redacted`
>
> 原始 harness 输出：`fail`，经预注册合同复核后更正为 `blocked`

---

## 1. 结论

Gate 4.5 **没有证明 LangGraph Core Dogfood 失败，也没有形成可接受的真实模型通过证据**。

三个真实 Case 都在第一次 worker 会话中被同一个 provider/model 可用性问题阻断：

```text
model = gpt-5.6
实际 provider = sandboxproxy
upstream = Sandbox Proxy / 合成 provider group
result = HTTP 404
cause = Model "gpt-5.6" is not supported by any configured account in this group
```

worker 没有修改 fixture，verification、HITL 和 reviewer 因此前置条件不成立而未执行。按照
预注册合同中“provider unavailable、model metadata/fallback 导致协议异常”和“真实 runner
环境或身份无法确定”的规则，本轮最终分类必须是：

```text
blocked
```

不允许把模型改成另一个可用名称、切换 provider/profile 或重试一个新 session 后，再把结果
合并进本轮。

## 2. Case 结果

| Case | 业务终态 | worker start / execution | 工作区 | Graph 控制面 | 合同分类 |
| --- | --- | ---: | --- | --- | --- |
| `linear-low` | `needs_human / worker_error` | `1 / 1` | clean，0 个变更文件 | 不适用 | `blocked` |
| `graph-low` | `needs_human / worker_error` | `1 / 1` | clean，0 个变更文件 | Graph State、checkpoint manifest、`run-status` 可校验 | `blocked` |
| `graph-crash-hitl` | `needs_human / worker_error` | `1 / 1` | clean，0 个变更文件 | fault 命中；recovery 复用失败 Step Result；无第二次 worker | `blocked` |

共同事实：

- 每个 Case 只有 1 次 worker start 和 1 份 worker execution；
- 每份 execution 都以 `returncode=1` 终止；
- 没有未知工作区副作用、越界文件、commit 或 push；
- 没有启动 reviewer；
- 没有把 verification failure 或 provider failure 升级为 success；
- Codex CLI 在同一 worker 进程内执行了 transport reconnect，但 Vega 没有创建第二个 worker
  session 或第二个 external attempt。

`graph-crash-hitl` 额外证明：在 worker execution 已形成失败终态、Step Result 已写入而业务
状态尚未推进的窗口，recovery 选择了 `safe_reuse_step_result`，没有重复启动 worker。
但该 Case 未满足“worker 已真实修改 workspace”的预注册前提，也没有到达 HITL，因此不能把
它当作完整 crash + HITL dogfood 通过证据。

## 3. Provider 身份漂移

预注册前只读 probe 记录的是：

```text
provider label = sandbox-provider
probe result = MODEL_PROBE_OK
```

真实 worker 输出记录的却是：

```text
provider = sandboxproxy
```

fixture 的 `.vega.yaml` 没有显式绑定 runner profile，而是继承执行机当前 Codex 配置。因此
probe 与真实 worker 并没有使用可证明相同的 provider identity 和完整命令形态。这是本轮
最重要的实验设计缺口：

> model 名称一致，不等于真实 runner 身份一致。

后续真实实验必须在创建任何 run 前完成同命令形态 preflight，至少绑定并校验：

- Codex CLI 版本；
- runner profile；
- provider identity；
- model；
- reasoning effort；
- ephemeral 配置；
- sandbox；
- endpoint 对该 model 的真实可用性。

## 4. 为什么原始 `fail` 不是最终分类

原始 harness 报告把三个 Case 记成 `safety_failed`，主要原因是它先检查：

```text
changed_files 必须等于 [src/slugify.py]
```

再判断 provider 是否阻断；同时它没有把 `process-output.txt` 中的 provider/model 错误提取到
Case 诊断字段。因此“provider 在推理前失败、工作区保持 clean”被误报成了 scope safety
failure。

这不符合预注册合同：

- `changed_files=[]` 不是越界修改；
- 没有 duplicate worker、unsafe resume、silent drift 或错误 success；
- 失败原因明确属于 provider/model unavailable。

结果复核后修正了 harness 的分类顺序和诊断提取，但**没有修改原始真实 session、没有重跑
provider、没有放宽通过标准**。原始 `summary.json` 和 `REPORT.md` 保持不变，作为分类缺陷的
审计证据。

## 5. 已获得与未获得的证据

### 已获得

1. Fake harness 三个 Case 全部通过：
   - Linear success + Finish；
   - LangGraph success + Graph terminal evidence；
   - crash recovery + consumed approval + single reviewer；
   - artifact integrity 与 evidence freshness 均可信。
2. 真实 runner 被阻断时：
   - Linear 与 LangGraph 都安全停在 `needs_human / worker_error`；
   - 没有重复 external attempt；
   - LangGraph checkpoint、Graph State 和 `run-status` 仍可消费；
   - crash recovery 没有重放失败 worker。
3. 执行基线完整测试覆盖：
   - `447 tests collected`；
   - 所有 node 最终通过；
   - Ruff、compileall、`git diff --check` 通过。
4. 分类器修正后的 postmortem fake session 再次得到三个 Case 全部 `passed`。

完整测试曾有一个并行分片因 Git 子进程争用触发 `git diff --stat` 30 秒环境超时；同一 node
独立复跑为 `1 passed in 5.72s`，不构成产品逻辑失败。

### 未获得

- 真实 worker 的代码生成质量；
- 真实 verification 结果；
- 真实 reviewer 质量；
- 真实高风险 HITL consumption；
- “worker 已修改 workspace 后 crash”的真实模型恢复证据；
- Linear 与 LangGraph 在真实成功任务上的延迟和成本对比。

## 6. Gate 决策

```text
Gate 4.5 = blocked
Gate 5 = 暂不进入
Gate 3/4 deterministic 结论 = 保留
```

解除阻塞的最小下一步：

1. 显式绑定 runner profile/provider identity；
2. 用与真实 worker 完全相同的 command shape 做一次模型可用性 preflight；
3. preflight 失败时在创建第一个业务 run 前停止；
4. owner 确认新的 provider/model 合同后，使用全新 session 重跑 Gate 4.5；
5. 不复用本轮三个 fixture 或把新结果写回本轮 raw summary。

## 7. 证据索引

```text
.local-validation/gate-4.5/real-core-20260716-private-gate-4-5-core-dogfood-baseline-redacted/summary.json
.local-validation/gate-4.5/real-core-20260716-private-gate-4-5-core-dogfood-baseline-redacted/REPORT.md
runs/20260716-163929-627704-bug-loop/
runs/20260716-163949-523394-bug-loop/
runs/20260716-164014-994887-bug-loop/
.local-validation/gate-4.5/fake-contract-20260716-v2/
.local-validation/gate-4.5/fake-postmortem-20260716/
.local-validation/gate-4.5/baseline-validation/
```

以上 `runs/`、`.tmp/` 和 `.local-validation/` 证据保持本地忽略，不进入 Git；Git 只提交本结果
文档、harness 修正和回归测试。
