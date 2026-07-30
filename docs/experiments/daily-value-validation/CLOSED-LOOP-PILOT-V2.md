# Vega 真实写审闭环实验 V2

- 预注册日期：2026-07-30
- 分支：`experiment/ma2b-pilot-next`
- Harness code baseline：`dfeb479117d01cff75c069c24714404d5f1afca7`
- 状态：`preregistered`

## 1. V1 边界

V1 结果继续保留：

- normal Worker 达到 360 秒观察上限后被安全停止，未执行 verification 或 Reviewer；
- negative 的 POSIX 风格验证命令无法由 Windows `cmd.exe` 执行，Reviewer 被停止且不计分。

V2 不重跑 normal，不延长 Worker 时间预算，不改写 V1。V2 只重建 negative treatment，
修复一个实验夹具变量：验证命令使用 Windows `cmd.exe` 可执行的反斜杠路径。

## 2. 唯一变化

V1：

```text
../venv/Scripts/python.exe ../verifier.py .
../venv/Scripts/python.exe ../run_tests.py . {{vega_verification_temp}}
```

V2：

```text
..\venv\Scripts\python.exe ..\verifier.py .
..\venv\Scripts\python.exe ..\run_tests.py . {{vega_verification_temp}}
```

以下输入保持不变：

- Echo Vault source commit `9400915`；
- 共享 Bug 任务；
- controlled negative patch；
- 弱 verifier 与目标 pytest；
- `AGENTS.md` 中 daily/rolling 组合规则；
- scope、change budget、模型、reasoning、profile、ephemeral 和 read-only Reviewer；
- Reviewer prompt、Runtime 代码与 360 秒单次 owned process 上限。

## 3. 冻结身份

| 项目 | 值 |
|---|---|
| Formal HEAD | `305d05ed68a6f6626ba602df5c91e60cec27544c` |
| Formal Tree | `ce5fad6a2a8788564d29ec645df3138bb9161d68` |
| tracked 文件数 | `167` |
| remote | `0` |
| Python | `3.12.13` |
| 模型 | `gpt-5.6-sol` |
| reasoning | `medium` |
| profile | `vega-daily-value-v1` |
| profile SHA-256 | `c1158162ed8bf0ed8249d09cc5e5550d376c95eaccde9af2758307f2cd6cf110` |
| `src/vega` manifest SHA-256 | `073c491c973773742cb2602f5446a2c3f13eb3978abd3927073e374334fead0b` |

| Artifact | SHA-256 |
|---|---|
| sanitized archive | `a97a1071d023442edc25e7490f4d3c4ff957aa161a08e01c83d5647b68e831b1` |
| task | `93e7b30a85bbadec3e3133b9886785e52a39a49ec1a5d5d1b5bf477251466ad6` |
| controlled negative patch | `154cf629783b6aa5c904f76780dacc153b02f2369f3aeb8e031fe15582be28e5` |
| negative verifier | `da94a2242633d68529fdb4c94559445a216c16335dc0ef167fef632935146767` |
| `run_tests.py` | `c0cb4acc52ea0455384b10bfaaafd6c12f81362dc645bcd7e1b8e657c142eb32` |
| `.vega.yaml` | `7717ac7ab2485b3d08aa76de9005c34903541fd20eaa6ace653189c7f1175f8f` |
| `AGENTS.md` | `04de894c92603f355f184e5a59f4dad6ebba9b8c2dc145c5b34a27abce16eb8b` |

Formal workspace 为全新独立单提交 Git 仓库，当前 clean、无 remote，也不包含 V1 diff、
Reviewer 输出或运行结论。

## 4. 资格门

资格门使用 Vega 自己的 `run_project_verification` 和实际 `cmd` shell，不再使用 PowerShell
手工等价命令。

| 输入 | 弱 verifier | 目标 pytest | `failed_count` |
|---|---:|---:|---:|
| baseline | `1` | `0` | `1` |
| controlled negative patch | `0` | `0` | `0` |

controlled negative patch 通过 V2 两条正式验证，但 V1 的完整 verifier 仍因
`dual_bucket_uses_tighter_remaining=false` 退出 `1`。资格证据：

| Artifact | SHA-256 |
|---|---|
| baseline `verification-result.json` | `05af023776b4e26fdfd99e94ec38d535f9cfacc6986cbcb016db437c61a0323f` |
| controlled patch `verification-result.json` | `ec8d4386d39d9b18c29855345e38bc6b52ec708d5bd660c8d2a291b435c0ff65` |

## 5. 正式流程

1. 使用同一任务合同以 `assist` 启动全新 loop，封存 V2 formal baseline。
2. 操作者应用 SHA-256 已冻结的 controlled negative patch。
3. 执行 `loop continue`；只有两条正式 verification 全绿，才采用后续 Reviewer 结果。
4. Reviewer 必须使用新的 read-only ephemeral Codex 会话，不接收 V1、Worker 聊天、patch
   来源、oracle 或其他会话内容。
5. 首次 Reviewer 若 `approve`，`reviewer_detection=failed`，停止且不人工补 finding。
6. 首次 Reviewer 若 `request_changes`，finding 必须明确指向 rolling bucket、
   daily/rolling 组合语义或等价根因，`reviewer_detection=passed`。
7. 只有 detection 通过时，允许一次新的 workspace-write ephemeral Codex Worker。输入只由
   共享任务、Vega 生成的 `fix-prompt.md` 和目标仓库规则组成。
8. 修复后再次执行同一 run 的 `loop continue`；两条 verification 全绿且新的 read-only
   Reviewer `approve` 时，`repair_recovery=passed`。

## 6. 停止线

- 首次 detection Reviewer、repair Worker、最终 Reviewer 各只允许一次正式调用；
- 单个 owned process 观察上限为 360 秒；
- verification 单命令上限为 60 秒；
- timeout、stop、Provider error、终止未确认、verification 失败、workspace 污染、HEAD
  变化、策略变化或证据不一致均 fail-closed；
- 不修改 Runtime，不放宽 scope，不替换 verifier，不把 stopped/needs_human 改写为通过；
- V2 失败后如需继续，必须再建版本并保留本次记录。

## 7. 结论边界

V2 只回答当前 Reviewer 能否发现一个“有限验证全绿但违反明确项目规则”的受控遗漏，以及
发现后能否由新 Worker 修复。它不改变 V1 normal 的失败结论，也不证明普遍成功率、经济性、
Planner、Memory 或 Multi-Worker 价值。

本文提交并推送后输入冻结，随后才允许 V2 正式 Provider 调用。
