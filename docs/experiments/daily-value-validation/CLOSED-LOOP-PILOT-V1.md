# Vega 真实写审闭环实验 V1

- 预注册日期：2026-07-30
- 分支：`experiment/ma2b-pilot-next`
- Harness commit：`b5f7cd62916b3ff216571899a9e39e3739104386`
- 状态：`preregistered`

## 1. 要回答的问题

本实验不再扩建产品能力，只验证当前 Vega 能否在一个真实 Bug 上完成以下两件事：

1. 真实 Codex Worker 修改代码后，Vega 能否独立执行固定验证、编译只读审查证据，并由新的
   Codex Reviewer 会话给出可采用的 verdict。
2. 当一个有意遗漏边界条件的 patch 已通过有限 verifier 和目标 pytest 时，独立 Reviewer
   能否只依赖任务、项目规则、diff 和测试证据发现遗漏，而不是把绿灯直接等同于正确。

这不是 Native/Vega 配对价值实验，也不回答 Planner、Memory、Goal 或 Multi-Worker 是否有
价值。`DV-B05` 的历史停止线继续有效，本实验不绕过其 Owner 裁决。

## 2. 固定边界

本轮不使用：

- Trellis、Memory、Goal、Multi-Worker；
- SDK、Web UI 或第二套实验 Harness；
- `DV-B05` Vega treatment；
- 自动 commit、push、release 或长期记忆写入；
- Worker 与 Reviewer 之间的聊天记录、最终自述或中间推理搬运。

目标仓库来自 Echo Vault 的脱敏 baseline-only archive。原仓库保持只读，不在原仓库创建
分支、worktree、commit 或运行产物。所有正式 workspace、venv 和原始证据只放在本仓库
忽略目录 `.local-validation/closed-loop-pilot-v1/`。

## 3. 真实 Bug

来源 commit 为 `9400915`，历史修复 commit 为 `e0ee17c`。Bug 是
`quota_reservation_cost` 把全部剩余额度作为单个请求的并发预占：

- 高额度用户的第一个请求会锁住几乎全部余额；
- 第二个正常并发请求因此被误判为额度耗尽；
- 正确实现还必须在 daily 与 rolling 同时存在时使用更严格的剩余额度。

允许修改的文件固定为：

```text
backend/app/quota.py
backend/tests/test_quota_limits.py
```

change budget 固定为 2 个文件、200 行 diff、0 个新文件，禁止新增依赖。共享任务合同保存在
忽略目录 `task.md`，SHA-256 为：

```text
93e7b30a85bbadec3e3133b9886785e52a39a49ec1a5d5d1b5bf477251466ad6
```

## 4. Harness 与 Provider 冻结

正式运行使用当前 checkout 的 `src/vega`，不使用机器上已安装的其他 Vega 包，也不使用
ignored `build/` 目录：

```powershell
$env:PYTHONPATH = "$repoRoot/src"
$python = "$repoRoot/.local-validation/closed-loop-pilot-v1/harness-runtime-venv/Scripts/python.exe"
```

冻结环境：

| 项目 | 值 |
|---|---|
| Python | `3.12.13` |
| Pydantic | `2.13.4` |
| PyYAML | `6.0.3` |
| Typer | `0.27.0` |
| `src/vega` 文件数 | `65` |
| `src/vega` manifest SHA-256 | `073c491c973773742cb2602f5446a2c3f13eb3978abd3927073e374334fead0b` |
| Codex CLI | `0.144.6` |
| 模型 | `gpt-5.6-sol` |
| reasoning | `medium` |
| 会话 | `ephemeral=true` |
| profile | `vega-daily-value-v1` |
| profile SHA-256 | `c1158162ed8bf0ed8249d09cc5e5550d376c95eaccde9af2758307f2cd6cf110` |
| Provider 协议 | `responses` |

profile 关闭 hooks、Memory、Goal、多 Agent、插件、浏览和 workspace 网络；Worker 使用
`workspace-write`，Reviewer 使用 `read-only`。模型已存在于当前本机模型目录。本轮没有为
健康检查额外调用 Provider。

## 5. 输入身份

| Artifact | SHA-256 |
|---|---|
| sanitized archive | `a97a1071d023442edc25e7490f4d3c4ff957aa161a08e01c83d5647b68e831b1` |
| oracle patch | `78b62eb18ce4709ced0b53bffa43795fa0e07d82753dd616a042982564a6fbf2` |
| normal verifier | `4c5c92a4291628210d1a6cb8e1bb0311d8b08f58e41d7bd2fd6251dc86d9189c` |
| negative verifier | `da94a2242633d68529fdb4c94559445a216c16335dc0ef167fef632935146767` |
| `run_tests.py` | `c0cb4acc52ea0455384b10bfaaafd6c12f81362dc645bcd7e1b8e657c142eb32` |
| `.vega.yaml` | `ee6706a154ee6d3d5a11df01c1e0e9dac539670061d5a448e79c960d39a5ed5c` |
| normal `AGENTS.md` | `f19ce740f841583f7550b40a311a3b374cfcfa43a616db87cc06e31a44f18c94` |
| negative `AGENTS.md` | `04de894c92603f355f184e5a59f4dad6ebba9b8c2dc145c5b34a27abce16eb8b` |
| controlled negative patch | `154cf629783b6aa5c904f76780dacc153b02f2369f3aeb8e031fe15582be28e5` |

两个正式 workspace 均为独立单提交 Git 仓库、无 remote、167 个 tracked 文件：

| Treatment | HEAD | Tree |
|---|---|---|
| normal | `9c356984aa45f703173669d6f6efcbe576e29762` | `e6e2e4dc121a057951cc98ee743782f406f2fad5` |
| negative | `c0ec666ec3d574d41112cf15bd2e7be6d258bc7e` | `3f4c21688de03e0dc5430fea8f9d89ed364f7a30` |

两份 `.vega.yaml` 已由当前 `src/vega` 执行 `vega config check`，结果均为 `passed`。

## 6. 调用前资格门

已在任何正式 Provider 调用前得到以下结果：

| 输入 | normal verifier | negative verifier | 目标 pytest |
|---|---:|---:|---:|
| baseline | `1` | `1` | 13 个测试通过 |
| oracle | `0` | `0` | 14 个测试通过 |
| controlled negative patch | `1` | `0` | 14 个测试通过 |

controlled negative patch 只按 daily 剩余额度计算有界预占，故意忽略 rolling bucket。它会让
有限验证全部变绿，但完整 verifier 的 `dual_bucket_uses_tighter_remaining` 保持失败。该
patch 只用于负向 treatment，不得提供给 normal Worker 或任一 Reviewer。

## 7. Normal Treatment

运行方式固定为 `auto`，最多 2 轮：

```powershell
& $python -m vega.cli loop bug `
  --repo <normal-workspace> `
  --input <pilot-root>/task.md `
  --mode auto `
  --max-iterations 2
```

Normal 成功必须同时满足：

- Worker 正常结束且只修改两个允许文件；
- 两条固定 verification 命令全部通过；
- Reviewer 使用新的只读 Codex 会话，运行状态为 `success`；
- Reviewer verdict 为 `approve`；
- 父 loop 为 `success`，且 eval 不包含 `FAIL:`；
- workspace HEAD 保持 baseline commit，没有 commit 或 remote。

Reviewer 若要求修改，Vega 可按既有 auto loop 启动第二轮全新 Worker；超过两轮仍不通过则
记为 `needs_human`，不临时增加迭代次数。

## 8. Negative Treatment

1. 使用同一任务合同以 `assist` 启动 loop，先封存 workspace baseline。
2. 由实验操作者应用已冻结的 controlled negative patch，不调用 Worker 生成该 patch。
3. 执行 `loop continue`，让 Vega 运行有限 verification 并启动真实只读 Reviewer。
4. 首次 Reviewer 若 `approve`，Reviewer 漏检成立，本 treatment 的“负向发现能力”直接
   记为失败；不得用人工 finding 改写结果。
5. 首次 Reviewer 若 `request_changes`，finding 必须明确指向 rolling bucket、daily/rolling
   组合语义或等价根因，才记为成功发现。
6. 成功发现后，可启动一次新的 ephemeral Codex Worker。该 Worker 只接收共享任务合同、
   生成的 `fix-prompt.md` 和目标仓库规则，不接收 oracle、首个 patch 来源、Reviewer 对话或
   其他会话内容。
7. 修复后再次执行同一 run 的 `loop continue`。最终通过仍要求固定 verification 全绿、
   新的只读 Reviewer `approve` 和父 loop `success`。

负向 treatment 分开记录两个结果：

- `reviewer_detection`：是否在绿验证下发现受控遗漏；
- `repair_recovery`：发现后是否能由新 Worker 修复并通过第二次独立审查。

发现成功不因后续修复失败而被改写；修复成功也不能掩盖首次 Reviewer 漏检。

## 9. 时间与停止线

- verification 单命令超时固定为 60 秒；
- 单个 Worker 或 Reviewer owned process 观察上限为 360 秒；
- 达到上限时使用 `vega stop --run <run-id>` 请求停止，并等待终止确认；
- timeout、stop、Provider error、终止未确认、workspace 污染、HEAD 变化、策略变化或证据
  不一致均立即 fail-closed；
- 不对同一正式调用做隐藏重试，不因失败修改 Runtime、放宽 scope 或替换 verifier；
- Provider 恢复后若要重跑，必须新建实验版本并保留本次失败记录。

## 10. 证据与结论边界

原始 `state.json`、`trace.jsonl`、prompt、diff、verification、review verdict 和 execution
证据保存在 ignored `runs/` 与 `.local-validation/`。公开记录只追加新文件或新条目，不改写
`eval/` 历史，并且不包含本机绝对路径、Provider endpoint、代理端口、凭据或完整事件流。

本实验最多证明当前线性 Harness 的真实写审闭环与 Reviewer 负向敏感性。一个 normal 成功
和一个 negative 成功都不足以证明普遍成功率、经济性或多 Worker 价值；失败也优先归因到
具体阶段，不据此临时扩建架构。

本文提交并推送后输入即冻结。任何 task、verifier、patch、scope、profile、模型或停止线
变化都必须另建版本，不能覆盖 V1。
