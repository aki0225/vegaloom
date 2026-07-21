# Assurance 逐项验证记录

> 建立日期：2026-07-21。
> 合同来源：[`docs/ASSURANCE-CONTRACT-CANDIDATE.md`](../docs/ASSURANCE-CONTRACT-CANDIDATE.md)。
> 本文件属于 `eval/` 证据记录，**只允许追加，不允许改写历史条目**。发现记录错误时，追加
> `correction` 条目说明，不删除、不润色原始结果。

## 记录协议

后续每项验证使用以下字段：

- `record_type`：`preregistration`、`result` 或 `correction`。
- `case_id`：稳定 ID。
- `baseline`：commit、tag、平台和关键工具版本。
- `question`：本次只验证什么。
- `non_goals`：本次明确不验证什么。
- `procedure`：可重放步骤。
- `expected`：候选合同要求的行为。
- `observed`：真实观察结果。
- `decision`：`confirmed`、`refuted` 或 `inconclusive`。
- `evidence`：artifact 路径、SHA-256 和限制。
- `next_step`：下一项最小动作。

以下 2026-07-21 的现状结果发生在本文件建立前，属于**回溯固化**，不得宣称为预注册实验。
它们只证明当前 Runtime 行为或本地性能特征，不证明真实模型质量和生产安全。

---

## 2026-07-21 · AV-BASE-001 · result

### 问题

当 `verify=True`，但 project profile 没有识别出任何验证命令时，隔离 reviewer 的 `approve`
是否仍能让 Loop 成功，并让 Finish 给出 `ready_to_commit`？

### Baseline

- Git：`main@176ac381`
- Tag：`v0.1.1`
- 平台：Windows
- worker/reviewer：本地确定性 fake runner

### 非目标

- 不验证真实 Codex reviewer 的缺陷发现能力。
- 不验证任意真实项目测试质量。
- 不验证生产环境。

### 过程

1. 创建只包含 tracked `AGENTS.md` 和 `README.md` 的最小 Git 仓库。
2. worker 修改 tracked `README.md`。
3. 使用 `verify=True` 启动单轮 auto loop。
4. project profile 不返回 test/lint 命令。
5. reviewer 返回结构化 `approve`。
6. 执行 Finish 并读取结构化结果。

### 候选合同期望

- `verification_conclusion=unknown`
- Loop 不得进入 `success`
- Finish 不得进入 `ready_to_commit`

### 实际观察

- `selected_command_count=0`
- `command_count=0`
- `failed_count=0`
- iteration `verification_status=skipped`
- Risk Gate：`low/self-check`
- Loop：`success`
- Finish：`ready_to_commit`
- artifact integrity：`valid`
- evidence freshness：`fresh`

### 裁决

`confirmed`

当前实现把“没有观察到失败”提升成“可自动成功”。这违反候选合同，也暴露 Risk Gate
通过摘要字符串判断缺失测试的脆弱性。

### 证据

- 本地 artifact：`.local-validation/assurance-p0-zero-verification-result.json`
- SHA-256：`974E625854CD12F4FFC71815F8ABBCC1D33B6C0EB529AB95CEF2E6E2D2AB2475`
- 限制：artifact 默认不提交；fake runner 只证明状态机和门禁语义。

### 下一步

先新增失败回归测试，固定“零命令不能自动成功”；测试失败后再修改 Runtime。

---

## 2026-07-21 · AV-BASE-002 · result

### 问题

assist continue 提供一个明确包含失败结果的非结构化 `--test-log` 时，reviewer `approve`
是否仍能让 Loop 和 Finish 自动成功？

### Baseline

- Git：`main@176ac381`
- Tag：`v0.1.1`
- 平台：Windows
- reviewer：本地确定性 fake runner

### 非目标

- 不尝试用关键词完整解析任意测试框架输出。
- 不验证受信 CI Evidence 导入。

### 过程

1. 创建最小 Git 仓库并启动 assist loop。
2. 人工修改 tracked `README.md`。
3. 提供位于 Vega workspace 内的测试日志：

   ```text
   FAILED tests/test_demo.py::test_broken
   1 failed
   ```

4. 使用 `verify=True` 执行 continue，但因为提供了 `test_log`，Runtime 不运行结构化 verification。
5. reviewer 返回 `approve`。
6. 执行 Finish。

### 候选合同期望

- 非结构化日志只能作为补充材料。
- 缺少受信 Verification Record 时应为 `unknown`。
- reviewer 不得把它提升为自动成功。

### 实际观察

- iteration `verification_status=skipped`
- `verification_failed_count=0`
- 不存在 `verification-result.json`
- `test-summary.md` 明确包含 `1 failed`
- Loop：`success`
- Finish：`ready_to_commit`
- artifact integrity：`valid`
- evidence freshness：`fresh`

### 裁决

`confirmed`

当前实现没有把外部测试日志绑定成机器可校验的验证结论，也没有阻止 `skipped` 进入成功链。

### 证据

- 本地 artifact：`.local-validation/assurance-p0-external-failed-log-result.json`
- SHA-256：`482E1AD52062570F670DB560EA84BB436D8402165E3DC05C1233F756179357B1`
- 限制：fake reviewer；只证明当前状态机和 artifact 完整性规则。

### 下一步

新增失败回归测试：任意非结构化外部日志在没有受信 Verification Record 时不得产生
`verified/ready_to_commit`。

---

## 2026-07-21 · AV-BASE-003 · result

### 问题

Codex adapter 初始化目标仓库时，如果仓库内 `.codex` 是指向仓库外目录的 Windows junction，
是否会把 `SKILL.md` 写出仓库？

### Baseline

- Git：`main@176ac381`
- Tag：`v0.1.1`
- 平台：Windows NTFS

### 候选合同期望

所有 adapter 写入的真实解析路径必须保留在目标仓库内；路径边界不确定时停止。

### 实际观察

两个报告路径表面位于：

```text
repo/.codex/skills/vega-loop/SKILL.md
repo/.codex/skills/vega-review/SKILL.md
```

解析后的真实路径均位于仓库外 junction 目标中。

### 裁决

`confirmed`

### 证据

- 本地 artifact：`.local-validation/branch-review-adapter-junction.json`
- SHA-256：`239397DB8D7F5B64CD56EEA02C2B71C8732AC9B837CC8BAC78DB467B209D0483`
- 限制：只验证 Windows junction；后续实现还需覆盖 symlink/reparse point 和 TOCTOU 边界。

### 下一步

在基础成功语义修复之后，用独立小改动修复 adapter 真实路径边界，并添加 Windows 专项回归。

---

## 2026-07-21 · AV-BASE-004 · result

### 问题

project profile 是否会根据 Node lockfile 选择唯一正确的包管理器命令？

### Baseline

- Git：`main@176ac381`
- Tag：`v0.1.1`
- 平台：Windows

### 候选合同期望

- npm 项目只给出 npm 命令。
- pnpm 项目只给出 pnpm 命令。
- yarn 项目只给出 yarn 命令。
- 不得因为生成错误命令制造假失败或遗漏真实验证。

### 实际观察

- npm：`npm test`、`npm run lint`
- pnpm：同时生成 `npm test` 和 `pnpm test`，lint 仍为 npm
- yarn：仍生成 `npm test` 和 `npm run lint`

### 裁决

`confirmed`

### 证据

- 本地 artifact：`.local-validation/branch-review-project-profile.json`
- SHA-256：`C587C3BE6A44D5E68BCEAEE27CE1F0A9F7660B63E581DAA1B569FC11AAA6468C`

### 下一步

在验证成功语义稳定后，单独修复 Node 包管理器选择，避免与 Evidence Adequacy 模型混在一个改动中。

---

## 2026-07-21 · AV-BASE-005 · result

### 问题

Finish 是否重复执行昂贵的完整性、新鲜度和风险重算，导致验证测试无法维持轻量回归？

### Baseline

- Git：`main@176ac381`
- Tag：`v0.1.1`
- 平台：Windows，Python 3.14.3

### 实际观察

单个 Finish artifact integrity 测试的 profile 样本：

- 总耗时约 `28.4s`
- `275` 次 subprocess
- 其中 `177` 次 Git 调用
- `validate_loop_artifact_integrity` 和 `validate_loop_evidence_freshness` 路径重复捕获工作区和风险证据

更大测试文件在同一环境中明显超过仓库约定的单测 60 秒上限。

### 裁决

`confirmed`

这是维护性和验证可执行性风险，不是证据充分性本身。修复应复用一次可信 Evidence Validation
Snapshot，而不是删除终态重算。

### 证据

- 本地 profile：`.local-validation/branch-review-finish-profile.txt`
- SHA-256：`650A774C5426ED64D9D7FEA74F536C40C467885E45838624EDF3EF17B08F49A5`
- 限制：Python 3.14.3 不属于当前 CI 的 3.11/3.12 支持矩阵；绝对耗时不可直接外推到 CI。

### 下一步

在成功语义修复后再做性能改动；保留完整性重算，只消除同一 Finish 调用内的重复快照和 Git 读取。

---

## 2026-07-21 · AV-BASE-006 · preregistration

### 问题

显式 `--no-verify` 且 reviewer 返回 `approve` 时，当前 Loop 和 Finish 是否会进入自动成功？

### Baseline

执行时固定为当时的 `main` HEAD，并记录 Python、平台和 artifact schema。

### 非目标

- 不修改 Runtime。
- 不同时验证零命令和外部日志路径。

### 过程

1. 创建具有至少一条可识别验证命令的最小仓库，避免与 AV-BASE-001 混淆。
2. worker 产生一个 tracked diff。
3. 显式 `verify=False`。
4. reviewer 返回 `approve`。
5. 读取 Loop、Finish 和 artifact integrity 结果。

### 候选合同期望

- `verification_conclusion=unknown`
- Loop 和 Finish 不得自动成功
- CLI/报告明确说明本轮只完成审查，不具有自动交付资格

### 成功条件

本条只裁决当前行为，不修改实现。结果必须追加为独立 `result` 条目。

### 下一步

执行该预注册案例；完成前不开始数据库 migration detector。

---

## 2026-07-21 · AV-BASE-006 · result

### Baseline

- Git：`main@176ac381`
- Tag：`v0.1.1`
- 平台：Windows，Python 3.14.3
- worker/reviewer：本地确定性 fake runner
- project profile 可识别验证命令：`python -m pytest -q`

### 实际观察

- 调用参数：`verify=False`
- worker 产生 tracked `README.md` diff
- iteration `verification_status=skipped`
- `verification_failed_count=0`
- 不存在 `verification-result.json`
- Risk Gate：`medium/isolated-review`
- Risk 原因：`missing_tests`
- reviewer：`approve`
- Loop：`success`
- Finish：`ready_to_commit`
- artifact integrity：`valid`
- evidence freshness：`fresh`

### 裁决

`confirmed`

即使项目画像能够识别真实测试命令，显式跳过验证后，当前实现仍允许 reviewer `approve`
把 run 提升为自动成功。Risk Gate 的 `missing_tests` 只是 medium 提示，不是终态阻塞条件。

### 证据

- 本地 artifact：`.local-validation/assurance-p0-no-verify-result.json`
- SHA-256：`B155D2B6DF958D0AF09E95ACD89CCCC917B029E0A93E2C78C582DF41AB37A718`
- Runtime run：`.local-validation/assurance-p0-no-verify-20260721-120312-610635/`
- 限制：fake runner 只证明控制逻辑。
- 采集说明：Runtime 和 Finish 已成功完成后，首个结果整理脚本因错误读取 run 根目录下不存在的
  `project-profile.json` 而退出；随后从同一 repo 重新调用只读 `build_project_profile`，并使用
  已存在的 state、risk 和 Finish artifact 整理结果，没有重跑或改写该次 Runtime run。

### 下一步

阶段 0 的第一个代码改动只处理验证成功语义：

1. 先为 AV-BASE-001、AV-BASE-002、AV-BASE-006 增加失败回归测试。
2. 证明当前测试按预期失败。
3. 再引入最小 `verified/failed/unknown/interrupted` 结论。
4. 在 Loop、Finish 和 Goal 中统一阻止 `unknown/interrupted` 自动成功。
5. 该改动通过后再处理 adapter、包管理器和 Finish 性能，不混入数据库或并发 detector。

---

## 2026-07-21 · AV-STAGE0A-001 · preregistration

### 问题

能否把 AV-BASE-001、AV-BASE-002 和 AV-BASE-006 固化为三条自动化失败回归，使当前
`v0.1.1` 实现在不修改 Runtime 的前提下稳定暴露成功语义缺口？

### Baseline

- Git：执行时记录当前 `main` HEAD。
- 测试文件：`tests/test_assurance_verification_semantics.py`
- worker/reviewer：本地确定性 fake runner。

### 非目标

- 本项不修改 `src/vega/`。
- 本项不引入新状态字段或 artifact schema。
- 本项不验证数据库、数据修改或并发 detector。
- 本项不要求全量 pytest 通过；新增测试预期在当前实现上失败。

### 测试案例

1. `test_zero_selected_verification_commands_cannot_auto_succeed`
   - 仓库无法识别任何验证命令。
   - `verify=True`，reviewer 返回 `approve`。
   - 期望 Loop=`needs_human`，Finish 不为 `ready_to_commit`。
2. `test_no_verify_cannot_auto_succeed_when_project_has_detectable_tests`
   - 仓库可识别 `python -m pytest -q`。
   - 显式 `verify=False`，reviewer 返回 `approve`。
   - 期望 Loop=`needs_human`，Finish 不为 `ready_to_commit`。
3. `test_unstructured_external_test_log_cannot_auto_succeed`
   - assist continue 提供包含 `1 failed` 的非结构化测试日志。
   - 不存在受信 `verification-result.json`，reviewer 返回 `approve`。
   - 期望 Loop=`needs_human`，Finish 不为 `ready_to_commit`。

### 成功条件

- 三条测试均被 pytest 收集。
- 在当前实现上三条均因观察到 `success/ready_to_commit` 而失败。
- 失败不是 fixture、路径、超时或 reviewer 无法执行造成的。
- 原有一条结构化验证成功测试继续通过，证明测试环境没有整体损坏。
- 将真实 pytest 摘要和日志哈希追加为独立 `result` 条目。

### 下一步

完成红灯证据后，阶段 0-B 才允许修改最小 Runtime 成功语义。

---

## 2026-07-21 · AV-STAGE0A-001 · result

### Baseline

- Git：`main@176ac381`
- Tag：`v0.1.1`
- 平台：Windows，Python 3.14.3
- worker/reviewer：本地确定性 fake runner
- Runtime：`src/vega/` 未修改
- 测试文件：`tests/test_assurance_verification_semantics.py`
- 测试文件 SHA-256：
  `97F3F402A66FBB9BB9297ABE94ECC343A55A5AFD379BB4BEE93C67A5D1B069BA`
- collect-only：3 条测试全部被收集

### 回归结果

| 基线案例 | 场景 | pytest | 实际 Loop / Finish | 总耗时 | 日志 SHA-256 |
|---|---|---|---|---:|---|
| `AV-BASE-001` | 零条选中验证命令 | 预期红灯，`1 failed` | `success` / `ready_to_commit` | 91.01s | `62D755E0DF82D64ECA0707D8ADE5A0CB5EFFC90392A1D0850F80BE2DA157B575` |
| `AV-BASE-006` | 可识别测试但显式 `--no-verify` | 预期红灯，`1 failed` | `success` / `ready_to_commit` | 90.60s | `76B97ED021ACAD0161F8C6E5BCB656A20D51B03F670FD7D9692F432A6936A226` |
| `AV-BASE-002` | 只有包含 `1 failed` 的非结构化外部日志 | 预期红灯，`1 failed` | `success` / `ready_to_commit` | 91.01s | `009268A23C9FC320398B48E538AFB80E7EF8581B9D5F858C8DB42F6E5C3BF5C9` |

三条测试都先在 `state.status == "needs_human"` 断言处失败，实际值均为 `success`。
pytest 因首个失败断言不会继续执行同一测试中的 Finish 断言，因此另行只读检查本次测试已经生成的
`finish-summary.json`；三条均为 `ready_to_commit`，对应 artifact SHA-256：

- 零命令：
  `A9B7546496F2E1E6E51C5AB9CB03428448C1C78F17EA1A4DE60557D96F2A95F1`
- `--no-verify`：
  `EB9E9B41F8E7F962C6240CC36764323A35F924CB2C46A090C273665EF406A75B`
- 外部日志：
  `62CCB85F1B97BAAA7E242BE3B6C525F1EFC12A2FE3B639048E10ABD8AECC9FA4`

### 控制案例

原有结构化验证成功案例
`tests/test_smoke.py::test_loop_auto_runs_detected_verification_commands` 继续通过：

- pytest：`1 passed in 40.50s`
- call duration：32.71s
- 日志：`.local-validation/stage0a-control-structured-verification-pytest.txt`
- 日志 SHA-256：
  `BD93DF0BF60DA9D6A205891841008E8EE52B37544B5AA1DB4CD0C4665007D769`

这说明三条红灯不是 pytest、Git fixture 或结构化验证链路整体损坏造成的。

### 未纳入裁决的尝试

曾误选 `tests/test_finish_artifact_integrity.py` 的参数化重控制案例；执行 240 秒仍未结束，
因此按 timeout 处理，不计为通过或失败证据。检查进程归属后，只终止该次尝试拥有的 PID
`4568` 和 `4472`。结果整理时未发现阶段 0-A 对应 pytest 命令残留。

### 裁决

`confirmed`

阶段 0-A 已把三个已知成功语义缺口固化为可执行回归：

1. 零验证命令不能等价于验证通过。
2. 显式跳过验证不能由 reviewer `approve` 提升为自动成功。
3. 非结构化外部日志不能形成受信验证结论。

当前 `v0.1.1` 对三条均错误地产生 `success/ready_to_commit`，而正常结构化验证控制案例仍可
完成，因此阶段 0-B 可以只针对成功语义做最小修复。

### 证据

- 结构化摘要：`.local-validation/stage0a-regression-before-fix.json`
- 摘要 SHA-256：
  `905D5F7DEC3A8B622285089EDCDBB420E49E36A5B8644DCF9D621B748862AACA`
- 红灯日志：
  - `.local-validation/stage0a-zero-commands-pytest.txt`
  - `.local-validation/stage0a-no-verify-pytest.txt`
  - `.local-validation/stage0a-external-log-pytest.txt`

### 限制

- fake runner 只证明控制逻辑，不代表真实模型、数据库或生产环境。
- 本阶段没有执行或宣称全量 pytest 通过；新增测试在修复前按设计保持红灯。
- 本阶段没有验证数据库迁移、数据修改或并发 detector。

### 下一步

进入阶段 0-B，只修改最小验证成功语义，使三条回归转绿，并复核结构化验证控制案例。
在该退出条件完成前，不开始数据库 migration、数据修改或并发 detector。

---

## 2026-07-21 · AV-STAGE0B-001 · preregistration

### 问题

能否在不引入数据库、数据修改或并发 detector，也不改变 reviewer 会话边界的前提下，
统一收紧 Loop、Finish 和 Goal 的自动成功语义：

> 只有绑定当前 iteration 的结构化验证实际执行了至少一条命令且全部通过，reviewer
> `approve` 才可能进入自动成功链路？

### Baseline

- Git：`main@176ac381`
- Tag：`v0.1.1`
- Python：3.14.3
- 已确认红灯：`AV-STAGE0A-001`
- Runtime 起始状态：`src/vega/` 无本轮变更

### 主方案与备选方案

主方案：

- 复用当前 `verification_status=passed/failed/skipped` 和受信
  `verification-result.json`。
- 将 `passed` 严格限定为“至少一条命令执行且无失败”。
- 在 Loop、Finish、Goal 各自的成功裁决边界独立要求可信 `passed`，避免只依赖上游
  `state.status=success`。
- `skipped` 在自动成功语义中等价于证据未知，必须交还人工。

备选方案：

- 立即新增持久化 `verified/failed/unknown/interrupted` 字段并升级全部 artifact schema。

本阶段选择主方案。原因是它能用最小改动修复已确认根因，并保持旧 artifact 可读；新结论字段、
schema 版本迁移和 `interrupted` 的完整建模留给后续独立阶段，避免一次改变过多变量。

### 预期修改边界

- 允许修改与成功裁决直接相关的 Runtime、共享证据判定和测试。
- 不改变 reviewer prompt 输入，不传递 worker 完整对话。
- 不放松任何 artifact integrity、freshness、scope 或 risk gate。
- 不修改已有 `eval/` 条目；结果只能追加。
- 不处理 adapter junction、Node 包管理器或 Finish 性能。

### 注册案例

必须转绿：

1. 零条选中验证命令 + reviewer approve。
2. 显式 `--no-verify` + reviewer approve。
3. 只有非结构化外部测试日志 + reviewer approve。

必须保持绿色：

1. 至少一条结构化验证命令真实执行且全部通过。
2. 结构化验证失败时 reviewer approve 不能覆盖失败。
3. Finish 对缺失、损坏、错绑或状态矛盾的验证 artifact 继续 fail-closed。
4. Goal 不能通过挂载一个“Loop success + approve，但验证为 skipped/缺失”的链路获得自动完成证据。

### 成功条件

- 三条阶段 0-A 回归全部通过。
- 结构化验证成功控制案例继续通过。
- 新增或复用的 Finish、Goal 定向测试证明它们不只信任 Loop 顶层状态。
- 受影响测试、`compileall`、`ruff` 和 `git diff --check` 通过。
- 如全量测试受运行时间限制未完成，必须如实记录执行范围，不得宣称全绿。

### 失败条件

- 任一 `skipped`、零命令、缺失结构化 artifact 或非结构化日志路径仍可自动成功。
- reviewer `approve` 能覆盖验证缺失或失败。
- 正常结构化验证成功路径被无差别阻塞。
- 为修复成功语义而打通 worker/reviewer 会话边界，或放松其他既有 fail-closed 门禁。

### 下一步

先追踪 Loop、Finish 和 Goal 当前实际重算链路；确认最小共同判定点后再修改代码。
