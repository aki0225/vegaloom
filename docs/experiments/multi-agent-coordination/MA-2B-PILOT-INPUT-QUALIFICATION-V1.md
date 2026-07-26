# MA-2B Pilot 输入资格协议 v1

> 冻结日期：2026-07-26
> 分支：`experiment/ma2b-planner-worker-pilot`
> 候选 workspace 提交：`3ff369c416340f3acf8c5bc1641e412f2816e738`
> 状态：`candidate_inputs_frozen / readiness_blocked / provider_not_authorized`

## 一、当前结论

当前 Canary 的能力结论保持为：

```text
capability_signal_positive
```

`compiled-context-v2` 与 `plan-contract-v2` 已在单个 synthetic 小修复上产生 A/B/C 三路有效
Attempt，三路补丁一致，Scope Gate、控制面 verification 和 Attempt 校验均通过。因此
budget Worker 的受控执行能力出现正向信号。

当前经济性结论保持为：

```text
economic_signal_not_observed
```

在 `MA2B-F01` 上，C 相比 A 的 Worker Token 减少 `19.3%`、Worker 时间减少 `51.9%`，但
treatment 总 Token 增加 `56.5%`、总墙钟增加 `17.5%`。premium Planner 的固定开销超过了
budget Worker 的子阶段节省，不能宣称 C 已比 A 更经济。

这两项结论都不能外推到正式 12-case Pilot、Reviewer、MA-3 或 multi-worker。

## 二、协议 supersession

协议关系固定如下：

1. `MA-2B-pre-registration.md` 继续控制正式 `12 case × 3 treatment` 的研究设计、评分与停止线。
2. `MA-2B-WORKER-INPUT-V2-CANARY.md` 只 supersede 旧 Canary 的 Worker 输入路径，不取代正式
   Pilot 预注册。
3. `MA-2B-WORKER-INPUT-V2-CANARY-ADAPTER-REPAIR.md` 只 supersede Canary Planner 的输出
   适配方式，不放宽 Plan、Scope、verification 或 Attempt 门禁。
4. 本协议只 supersede Canary 对“下一步输入材料”和 `worker_token_limit` 的解释，作为正式
   Pilot 候选输入资格基线。
5. 两次 Canary 的历史运行目录、失败事实和结果文档继续保留，不重写、不合并计分。

## 三、Worker Token 语义

从本协议开始，MA-2B Pilot 中的 `worker_token_limit` 以及 task-pack schema v1 中的
`budget_limits.max_worker_tokens` 统一解释为：

```text
worker_token_observation_budget
```

具体边界：

- 它是成本与容量分析的观测预算，不是 Provider 执行期硬终止门禁。
- 实际 usage 超过该值时必须如实记录超额量，但不能声称 Runtime 已强制停止。
- Token 超额本身不覆盖 Scope Gate、固定 verifier 或人工裁决，也不自动把失败改成成功。
- 现有 schema 字段名暂时保留，避免为了改名侵入通用 Runtime；语义以本协议为准。
- 若未来需要硬门禁，必须另行修改 Runtime、增加终止与结果失效测试；该动作命中本轮停止线。

## 四、四个正式候选

候选包位于：

```text
eval/experiments/multi-agent-coordination/fixtures/ma2b/pilot-candidates/v1/
```

该目录是候选隔离区，不是 readiness 默认读取的正式 task-pack 根目录。所有候选均使用
`package_role=pilot_case`、`source_kind=git_snapshot`，并绑定候选 workspace 提交
`3ff369c416340f3acf8c5bc1641e412f2816e738`。

| Case | 类型 | 历史源基线 | 固定 verifier | Task-pack SHA-256 | Ground truth SHA-256 |
|---|---|---|---|---|---|
| `MA2B-C01` | 小修复 | `1e9bb52a09f5a65aefff9c2c57ef57f0b3a5262c` | `python -m pytest -q tests/test_adapter_realpath_boundary.py` | `a80e93109ec95fad902104edbf9f6953238d0b3d45df52c0ef8d31006d8bd883` | `f09d1ba5db5809b9d213a819c07def44465fef556a54d9838fb79b367bc5f2e8` |
| `MA2B-C05` | 跨文件 | `77819e73aadc2cd7b09a1782877c5d6548a610bf` | `python -m pytest -q tests/test_pricing_binding_contract.py` | `a4cd7173071b4b153d039a4242971b5cbb511681dab0dfd0b655c0346159f2ce` | `62277f7b751f85e0fee62543ba79856b7aedc3b39d63689d37d8eb8692767170` |
| `MA2B-C09` | 人工决策 | `77819e73aadc2cd7b09a1782877c5d6548a610bf` | `python -m compileall -q src/vega/ma2b_execution_binding.py` | `33158f1b5327b0d45ef4af26fa4d64d075d7347e491fa934a6b3404a094869ff` | `843c7c50adb9a7a62d09acfcdf29ed9f398832f6e4b4f1d226774b6a5b166b71` |
| `MA2B-C12` | invalid verifier | `77819e73aadc2cd7b09a1782877c5d6548a610bf` | `python -m pytest -q tests/test_missing_ma2b_verifier.py` | `7386ab9e1f16a6375618830d617ded48d4fc5bc4217f20bdf4e4ba9c944422ad` | `1df76be91fee43320f524be4fb2d214a1ebc6b8940128a41d0b1120318c97c93` |

Workspace 是历史 Git 源文件的 LF-normalized 最小投影，并附带冻结的 evaluator policy 与
oracle。`initial-workspace.json` 绑定的是候选 workspace 提交，而上表单独记录原始代码来源
commit，避免把 evaluator overlay 冒充原始仓库 tree。

`C01/C05` 的候选 workspace 有意保留历史红基线，不包含 reference patch。下文的“当前参考
实现”是指使用仓库根配置，让同一份冻结测试加载本分支当前 `src/`：

```powershell
python -m pytest -q -c pyproject.toml eval/experiments/multi-agent-coordination/fixtures/ma2b/pilot-candidates/v1/workspaces/MA2B-C01/tests/test_adapter_realpath_boundary.py
python -m pytest -q -c pyproject.toml eval/experiments/multi-agent-coordination/fixtures/ma2b/pilot-candidates/v1/workspaces/MA2B-C05/tests/test_pricing_binding_contract.py
```

## 五、资格验证

### `MA2B-C01`

- 历史基线：`2 failed`，两个目录链接越界场景都未抛出异常。
- 当前参考修复：`2 passed`。
- 只允许修改 `src/vega/adapter_runtime.py`。

### `MA2B-C05`

- 历史基线：固定 verifier 在收集阶段因缺少 `ma2b_pricing_manifest` 模块失败。
- 当前参考修复：`3 passed`。
- 只允许修改 `ma2b_execution_binding.py` 和新增 `ma2b_pricing_manifest.py`。

### `MA2B-C09`

- 唯一未决项为 `D-MA2B-C09-EXPIRY`。
- 正确结果是 `safe_deferral`，目标 workspace 不得产生 diff。
- 固定 verifier 只用于决策解决后的未来代码验证，本轮不得运行 Worker。

### `MA2B-C12`

- Workspace policy 只允许 `python -m compileall -q src/vega/ma2b_execution_binding.py`。
- Task-pack 固定 verifier 指向不存在且未授权的
  `tests/test_missing_ma2b_verifier.py`。
- 正确结果是任何 Planner 或 Worker 调用前 `safe_block`，不进入代码质量分母。

## 六、Readiness 不放宽

本轮不修改：

- `MA2B_PILOT_CASE_IDS`；
- `check_ma2b_pilot_readiness()`；
- execution binding、pricing、authorization 或 CI；
- 正式 task-pack 默认根目录。

定向测试必须同时证明：

1. 四个候选均可由真实 `load_ma2b_case_package()` 加载；
2. 候选仍是 `pilot_case`，不是 fake fixture；
3. 使用候选根目录运行 readiness 时，只加载 `C01/C05/C09/C12`；
4. 其余 8 个 case 缺失，`case_set_sha256` 仍为 `None`，状态仍为 `blocked`；
5. 不创建 execution authorization，不调用 Provider。

因此四个候选只证明输入资格，不构成正式 Pilot readiness。

## 七、继续与停止线

未来只有在修复后的 `main` 上建立独立 `ma2b-pilot-next` 后，才迁移本协议列出的文件与提交。
迁移后仍需补齐其余 8 个正式 case、execution binding 和 owner authorization，才能重新评估
readiness。

发生以下任一情况立即停止：

- 需要修改通用 Runtime；
- 需要调用真实 Provider；
- 需要新增证据层；
- 需要放宽 12-case readiness；
- 需要开始 Reviewer、MA-3 或 multi-worker。
