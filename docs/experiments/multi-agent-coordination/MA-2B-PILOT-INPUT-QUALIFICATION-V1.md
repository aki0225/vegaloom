# MA-2B Pilot 输入资格协议 v1

> 冻结日期：2026-07-26
> 主线基线：`origin/main@7805bba18f7d91594cba0c6cb95251493503362c`
> 分支：`experiment/ma2b-pilot-next`
> 完整输入提交：`6bc443ef3faf68adc5e2574cad6415de06f52e30`
> 状态：`candidate_inputs_complete / readiness_blocked / provider_not_authorized`

## 一、当前结论

Canary 的能力结论保持为：

```text
capability_signal_positive
```

`compiled-context-v2` 与 `plan-contract-v2` 已在单个 synthetic 小修复 `MA2B-F01`
上产生 A/B/C 三路有效 Attempt。三路补丁一致，Scope Gate、控制面 verification 和 Attempt
校验均通过，因此 budget Worker 的受控执行能力出现正向信号。

Canary 的经济性结论保持为：

```text
economic_signal_not_observed
```

在 `MA2B-F01` 上，C 相比 A 的 Worker Token 减少 `19.3%`、Worker 时间减少 `51.9%`，
但 treatment 总 Token 增加 `56.5%`、总墙钟增加 `17.5%`。premium Planner 的固定开销
超过了 budget Worker 的子阶段节省，不能宣称 C 已比 A 更经济。

本轮新增的 12 个正式候选只证明输入、scope、固定 verifier、ground truth 和哈希可复核。
它们没有调用 Provider，也不能证明 Worker 已经解决 8 个代码 case。能力结论不能外推到正式
Pilot、Reviewer、MA-3 或 multi-worker。

## 二、协议 supersession

协议优先级固定如下：

1. `eval/experiments/multi-agent-coordination/MA-2B-pre-registration.md` 继续控制正式
   `12 case × 3 treatment` 的研究设计、评分、随机顺序和停止线。
2. `MA-2B-TASK-PACK-CONTRACT.md`、`MA-2B-READINESS-GATE-CONTRACT.md`、
   `MA-2B-PRICING-MANIFEST-CONTRACT.md` 和 `MA-2B-EXECUTION-BINDING-CONTRACT.md`
   继续控制各 artifact 的结构与 fail-closed 语义。
3. `MA-2B-WORKER-INPUT-V2-CANARY.md` 只 supersede 更早 Canary 的 Worker 输入路径，
   不取代正式 Pilot 预注册。
4. `MA-2B-WORKER-INPUT-V2-CANARY-ADAPTER-REPAIR.md` 只 supersede Canary Planner
   的输出适配方式，不放宽 Plan、Scope、verification 或 Attempt 门禁。
5. 本协议 supersede Canary 对“下一步输入材料”和 `worker_token_limit` 的解释；本次完整
   12-case 冻结同时 supersede 本协议旧版“四个候选、八个缺失”的 readiness 描述。
6. Canary 的历史运行目录、失败事实和结果文档继续保留，不重写、不合并计分。

## 三、Worker Token 语义

MA-2B Pilot 中的 `worker_token_limit` 与 task-pack schema v1 中的
`budget_limits.max_worker_tokens` 统一解释为：

```text
worker_token_observation_budget
```

边界如下：

- 它是成本与容量分析的观测预算，不是 Provider 执行期硬终止门禁。
- 实际 usage 超过该值时必须如实记录超额量，但不能声称 Runtime 已强制停止。
- Token 超额本身不覆盖 Scope Gate、固定 verifier 或人工裁决，也不自动改写成功语义。
- 现有 schema 字段名暂时保留，避免为了改名侵入通用 Runtime；语义以本协议为准。
- 若未来需要硬门禁，必须单独修改 Runtime，并增加终止、usage 缺失和结果失效测试；该动作
  不属于本轮输入资格工作。

## 四、正式候选集合

候选根目录为：

```text
eval/experiments/multi-agent-coordination/fixtures/ma2b/pilot-candidates/v1/
```

该目录仍是隔离候选区，不是 readiness 默认读取的正式 task-pack 根目录。全部 12 个候选都
使用 `package_role=pilot_case`、`source_kind=git_snapshot`，且不包含 reference patch、
Provider prompt、凭据或运行结果。

| Case | 类别 | 历史红基线 | 参考修复或预期行为 | 固定 verifier |
|---|---|---|---|---|
| `MA2B-C01` | 小修复 | `1e9bb52` | `5277205` | `python -m pytest -q tests/test_adapter_realpath_boundary.py` |
| `MA2B-C02` | 小修复 | `deaa2f7` | `3b47f45` | `python -m pytest -q tests/test_cli_repo_directory_guard.py` |
| `MA2B-C03` | 小修复 | `de53d24` | `8a4b802` | `python -m pytest -q tests/test_windows_taskkill_decoding.py` |
| `MA2B-C04` | 小修复 | `6b74c5b` | `da1ac29` | `python -m pytest -q tests/test_node_package_manager_selection.py` |
| `MA2B-C05` | 跨文件 | `77819e7` | `d2bfbe6` | `python -m pytest -q tests/test_pricing_binding_contract.py` |
| `MA2B-C06` | 跨文件 | `58268fa` | `29706a9` | `python -m pytest -q tests/test_bounded_execution_progress_contract.py` |
| `MA2B-C07` | 跨文件 | `3b47f45` | `9449deb` | `python -m pytest -q tests/test_cli_validation_architecture.py` |
| `MA2B-C08` | 跨文件 | `516e98b` | `662efee` | `python -m pytest -q tests/test_tracked_profile_identity.py` |
| `MA2B-C09` | 人工决策 | 冻结 workspace | `D-MA2B-C09-EXPIRY` → `safe_deferral` | `python -m compileall -q src/vega/ma2b_execution_binding.py` |
| `MA2B-C10` | 人工决策 | 冻结 workspace | `D-MA2B-C10-CLIENT-DRIFT` → `safe_deferral` | `python -m compileall -q src/vega/ma2b_execution_binding.py` |
| `MA2B-C11` | stale evidence | 干净可加载包 | 未来编译上下文后注入 `task_artifact_mismatch` | `python -m pytest -q tests/test_adapter_realpath_boundary.py` |
| `MA2B-C12` | invalid verifier | 固定无效命令 | Planner/Worker 前 `safe_block` | `python -m pytest -q tests/test_missing_ma2b_verifier.py` |

Workspace 是历史 Git 源文件的 LF-normalized 最小投影，并附带 evaluator policy 与 oracle。
`initial-workspace.json` 绑定候选 workspace 提交，表中的历史红基线单独记录原始代码来源，
避免把 evaluator overlay 冒充原始仓库 tree。

## 五、固定哈希

| Case | Workspace 提交 | Task-pack SHA-256 | Ground truth SHA-256 |
|---|---|---|---|
| `MA2B-C01` | `3ff369c` | `a80e93109ec95fad902104edbf9f6953238d0b3d45df52c0ef8d31006d8bd883` | `f09d1ba5db5809b9d213a819c07def44465fef556a54d9838fb79b367bc5f2e8` |
| `MA2B-C02` | `e569d3e` | `169f38c2caddcdc59be9f750b2b62612434a3b59f57bcb070a6fff27070e68eb` | `f0e3131e4bf4213efee9ad201416b5b471a0232fdea164bbe395ef7389bd95d0` |
| `MA2B-C03` | `e569d3e` | `7b183ceddd5e7e5fd42127e20849886141187e80b2645a8039d37c1b708ad051` | `77b86452d74956809788fb733027a6e8d4f89237cffa20659581435ab83aca7b` |
| `MA2B-C04` | `e569d3e` | `545e1e72a80efd6baba9934c602d6262e693c73ff8584df995eb8d5127d16c6e` | `8225e7f61ee5be1367287d5e8c9f7819ce63b26d636a2d094828e04c4568f815` |
| `MA2B-C05` | `3ff369c` | `a4cd7173071b4b153d039a4242971b5cbb511681dab0dfd0b655c0346159f2ce` | `62277f7b751f85e0fee62543ba79856b7aedc3b39d63689d37d8eb8692767170` |
| `MA2B-C06` | `e569d3e` | `933b239bbf37b9f7b3042ef32be3a48b1da05eb16d4c3ed7f530f41b36eee2c6` | `dc36ede79c231c448c8e93db351e6117be5a68c6e59731455700eb62d51259b6` |
| `MA2B-C07` | `e569d3e` | `79555bd5b1a98e948197444483857f3c4a0de2dd4b86459eef96807c5d3e62e5` | `70577ee79d255216e36b3853c592763a3fec4539c8d8dcf84b5fb7ecaf1ad059` |
| `MA2B-C08` | `e569d3e` | `ed2cf54bbc594c6d2bfda7a84bf1c0ab077ee5c6e52f05b430a21af73b6e50f0` | `24368b6bdfe0fc662c4a8569670043695702ab2c7f03b8f94842c2fecc2d9357` |
| `MA2B-C09` | `3ff369c` | `33158f1b5327b0d45ef4af26fa4d64d075d7347e491fa934a6b3404a094869ff` | `843c7c50adb9a7a62d09acfcdf29ed9f398832f6e4b4f1d226774b6a5b166b71` |
| `MA2B-C10` | `3ff369c` | `e01d2bed9b376950b9d87eb1def2ea9f87e14cf69e9393a7fd38b6b1b78819bd` | `304cb845fd913dbf4aa0d607a8fb87b3a84b38c42ded8d992fd344c0be70bfc4` |
| `MA2B-C11` | `3ff369c` | `d4141f8b295a669574107c9d3c567d749ebd5b9e9ed54a4d61177fa23accfc70` | `28017ec58ec05ff9125b3923890194b407b26345ef3387d3c93fc28d0c4c16d2` |
| `MA2B-C12` | `3ff369c` | `7386ab9e1f16a6375618830d617ded48d4fc5bc4217f20bdf4e4ba9c944422ad` | `1df76be91fee43320f524be4fb2d214a1ebc6b8940128a41d0b1120318c97c93` |

完整 case-set 哈希为：

```text
33b2caa335b417b47ee45bb5de7051aef20682bbf938eddf5d2e4ad5d3d4f137
```

## 六、资格验证边界

8 个代码 case 的同一固定 verifier 已分别在历史父提交与参考修复提交上验证：

| Case | 历史结果 | 参考修复结果 |
|---|---|---|
| `MA2B-C01` | `2 failed` | `2 passed` |
| `MA2B-C02` | `1 failed`，缺少仓库目录 guard | `1 passed` |
| `MA2B-C03` | `1 failed`，抛出 `UnicodeDecodeError` | `1 passed` |
| `MA2B-C04` | `1 failed`，缺少统一选择合同 | `1 passed` |
| `MA2B-C05` | 收集失败，缺少 pricing 模块 | `3 passed` |
| `MA2B-C06` | `1 failed`，缺少 progress 模块 | `1 passed` |
| `MA2B-C07` | `1 failed`，校验未抽离 | `1 passed` |
| `MA2B-C08` | `1 failed`，缺少固定 revision 与 identity | `1 passed` |

这些红绿结果证明 verifier 对历史缺陷有区分力，并不等同于 Provider treatment 结果。

其余 4 个 case 的输入边界为：

- `C09/C10` 必须在唯一 decision id 上产生 `safe_deferral`，目标 workspace 不得产生 diff。
- `C11` 的 task-pack 本身必须正常加载；stale 只能在未来 driver 编译上下文后注入，不能永久
  写进包内。正确停止码为 `task_artifact_mismatch`，Planner/Worker 调用应为零。
- `C12` 的 verifier 固定为 workspace 未授权且不存在的命令，正确结果是任何模型调用前
  `safe_block`。

本轮没有执行 C11/C12 的真实 driver 故障注入，因此不能把“零 Provider 调用”写成已完成
运行证据；这里只冻结其输入与 ground truth。

## 七、Readiness 不放宽

本轮没有修改：

- `MA2B_PILOT_CASE_IDS`；
- `check_ma2b_pilot_readiness()`；
- execution binding、pricing、authorization 或 CI；
- 正式 task-pack 默认根目录。

使用候选根目录运行真实 readiness 的结果为：

```text
loaded_case_ids = MA2B-C01 ... MA2B-C12
case_set_sha256 = 33b2caa335b417b47ee45bb5de7051aef20682bbf938eddf5d2e4ad5d3d4f137
status = blocked
issue_codes = execution_binding_path_invalid, execution_authorization_path_invalid
```

因此 12 个输入已经齐全，但仍未获得真实执行资格。定向测试为 `24 passed, 1 skipped`，项目
测试收集数仍为 `820`，没有通过增加测试节点或缩减 case 集合放宽 readiness。

## 八、从更新后 main 重建的迁移清单

当前分支已经从 `origin/main@7805bba18f7d91594cba0c6cb95251493503362c`
建立。若后续需要从更新后的 `main` 再建 `ma2b-pilot-next`，按以下顺序迁移；目标主线若已包含
等价能力，应按文件比较而不是覆盖新 Runtime。

1. `13b62c2`：MA-2B 最小合同、task-pack/readiness/pricing/execution-binding 模块、fake
   fixture、预注册和定向测试。
2. `68c3806`：`C01/C05/C09-C12` 候选 workspace。
3. `9f749b9`：四个锚点 task-pack、ground truth、观测预算语义和初版资格协议。
4. `e569d3e`：`C02-C04/C06-C08` 候选 workspace。
5. `6bc443e`：其余 8 个 task-pack、ground truth、完整 12-case 哈希与现有测试节点扩展。
6. 迁移本文件以及
   `docs/experiments/multi-agent-coordination/RESEARCH-AND-EXPERIMENT-PLAN.md` 中对应的
   supersession 说明。

文件范围分三组：

- 能力与合同：`src/vega/ma2b_*.py`、`src/vega/delegation.py`、对应合同文档和 MA 专用测试。
- 固定输入：`eval/experiments/multi-agent-coordination/fixtures/ma2b/**` 与
  `eval/experiments/multi-agent-coordination/MA-2B-pre-registration.md`。
- 解释文档：`docs/experiments/multi-agent-coordination/**` 中的 MA-2B 协议与 Canary 结果。

如果迁移需要覆盖更新后 main 的通用 Runtime、修改 CI、调用真实 Provider、增加证据层、
放宽 readiness，或开始 Reviewer、MA-3、multi-worker，应立即停止并重新评审范围。
