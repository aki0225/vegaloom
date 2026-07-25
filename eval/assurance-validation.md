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

---

## 2026-07-21 · AV-STAGE0B-001 · result

### Baseline

- Git 基线：`main@176ac381`
- 修复分支：`fix/verification-success-semantics`
- 最终验证提交：`313503e87323077bbf82dbc63bd5b908ae403dd5`
- PR：`#1`
- GitHub Actions：`29837944440`
- Runtime 方案：复用现有结构化 verification artifact，不新增数据库、schema migration、
  数据修改、并发 detector 或 Agent 角色。

### 实现结果

- Loop 只有在最新 iteration 存在完整、非空、未中断且全部通过的结构化验证时，才可能
  进入自动成功链路。
- `--no-verify`、零条选中命令、缺失或损坏 artifact、部分执行、跳过命令和验证中断均
  fail-closed。
- reviewer `approve` 不能覆盖验证缺失或失败。
- success eval 会在写入最终终态前重新校验 verification artifact 与 workspace freshness；
  降级失败时 `state.status`、`run_finished.status` 和 completion step 保持一致。
- Finish 与 Goal 只使用最新 iteration 的可信验证裁决；历史失败仍保留在报告中，但不会
  覆盖后续已修复且受信的通过结果。
- worker/reviewer 会话边界、read-only reviewer 约束和既有 artifact integrity 门禁未放松。

### 本地验证

- `tests/test_p0_regressions.py::test_legacy_loop_without_scope_evidence_cannot_be_ready_to_commit`：
  `1 passed in 12.57s`。
- dogfood eval 两次都生成机器可读 `summary.json`，8 个 case 全部通过；本机完整脚本在当前
  资源负载下超过 60 秒外层限制，因此没有把对应 pytest timeout 冒充本地通过。
- `python -m compileall -q src`：通过。
- `ruff check src tests scripts`：通过。
- `git diff --check`：通过。
- collect-only：`516 tests collected`。

### PR CI

首次 PR CI `29835869528` 暴露两个旧 fixture 与新成功语义不一致：

1. legacy scope 用例没有先生成真实结构化验证成功证据。
2. dogfood 的 core loop 与 Goal child loop 使用 `verify=False`，却仍期待自动成功。

修复只为这些正向 fixture 增加已提交的固定 verification 命令并启用 `verify=True`，没有修改
或放松 Runtime。提交 `313503e` 后重新运行：

- Workflow `29837944440`：`completed / success`。
- Checks：`10/10 success`。
- Python 3.11 全量测试：`515 passed, 1 skipped in 115.39s`。
- Python 3.12：全部 5 个分片通过，并保持 516 节点收集合同。
- Windows 专项与 wheel smoke：通过。
- POSIX 临时目录专项：通过。
- wheel 构建、安装与隔离 smoke：通过。

### 裁决

`passed`

阶段 0-B 的注册成功条件全部满足。现有 v0.1 自动成功语义已经从“相信顶层状态或 reviewer
approve”收紧为“最新轮次必须具备可复核的结构化验证成功证据”，且正常结构化成功路径没有
被无差别阻塞。

### 限制

- 本结果证明的是本地代码工作流的成功裁决与证据一致性，不证明数据库 migration、
  backfill、分布式并发或生产事务安全。
- reviewer 与 worker 是会话上下文隔离；reviewer 仍使用共享仓库的只读视图，不是容器或
  操作系统级文件系统隔离。
- 分支尚未合并 `main`，也未打 `v0.1.2` 标签或发布。

### 下一步

人工复核 PR diff 后决定是否合并 `main`。合并、标签与发布必须作为独立决策执行，不由本次
验证自动触发。

---

## 2026-07-21 · AV-STAGE0B-002 · post-merge result

### Baseline

- 合并提交：`main@38bf1a1`
- 来源 PR：`#1`
- 合并后 GitHub Actions：`29845250723`
- 版本收口前包版本：`0.1.1`

### 实际观察

- 阶段 0-B 的 Runtime、测试和文档提交已经进入 `main`。
- 合并后 Workflow `29845250723` 完成，Checks 为 `10/10 success`。
- Python 3.11 全量测试为 `515 passed, 1 skipped`，并保持 `516` 节点收集合同。
- Python 3.12 全部分片、Windows wheel smoke、POSIX 专项和 package 安装验证均通过。
- 发布收口只需要更新版本、制品路径和当前事实文档，不需要再次修改成功语义 Runtime。

### 裁决

`passed`

阶段 0-B 已完成合并后复核，可以进入独立的 `v0.1.2` 版本与标签收口。该结论不改写
`AV-STAGE0B-001` 的历史基线和当时限制。

### 限制

- 本结果不单独证明 `v0.1.2` 标签已经创建；标签仍受 release 分支 CI 和合并后 main CI
  双重门禁约束。
- GitHub Release 和 PyPI 发布不在本次验证范围内。
- LangGraph、adapter、数据库、Web UI、多 Agent 和长期 Memory 写入仍不属于 v0.1.2。

---

## 2026-07-22 · AV-M001-001 · preregistration

### 目标

修复 `M-001`：Codex adapter 在目标仓库内生成 skill 文件前，必须确认每个写入目标的真实
解析路径仍位于目标仓库内。路径越界、无法解析或边界不确定时 fail-closed，不允许通过
`--force` 绕过。

### Baseline

- Git 基线：`main@1e9bb52`
- 工作分支：`fix/adapter-realpath-boundary`
- 已确认问题：`AV-BASE-003`
- 影响入口：`vega adapters init codex --repo <repo> [--force]`
- 范围：只处理 adapter 文件写入边界，不混入 Node 包管理器、Finish 性能、Threat/Evidence
  数据模型或新的 Agent 能力。

### 威胁模型

目标仓库内容不可信。攻击者或错误配置可能预先把 `.codex`、`skills` 或具体 skill 目录设置为
symlink、Windows junction 或其他可被路径解析跟随的 reparse point，使表面位于仓库内的
`SKILL.md` 实际写入仓库外。

本轮防守边界：

1. 写入集合必须在任何修改发生前完成全量真实路径预检，避免后一个危险目标导致前一个目标
   已经部分落盘。
2. 创建父目录后、写文件前再次解析目标，降低检查与写入之间路径状态变化造成的风险。
3. 已存在且本应跳过的文件也先验证边界；“不覆盖”不能掩盖危险路径。
4. 仓库内普通目录和真实解析后仍位于仓库内的目录链接继续可用。

### 方案取舍

- 采用：`Path.resolve(strict=False)` 解析现有链接和不存在的末端路径，再用规范化仓库根执行
  包含关系校验；先全量预检，写前复核。
- 不采用：拒绝路径链上的所有 junction/reparse point。该方案简单但会无差别阻塞真实目标仍在
  仓库内的安全双生案例。
- 暂不采用：基于目录句柄、`openat`/Windows handle 的逐级 no-follow 原子写入。它能进一步
  收紧并发替换窗口，但跨平台复杂度明显超过本次已确认维护缺口。

### 预注册案例

#### 危险案例 `AV-M001-DANGER-001`

- 在仓库外创建空目录。
- 让仓库内 `.codex` 成为指向该目录的 Windows junction；POSIX 对应使用目录 symlink。
- 执行 adapter 初始化，且外部目录中预先不存在两个目标 skill。

期望：

- CLI 非零退出并报告 adapter 写入路径越过目标仓库边界。
- 外部目录没有新增 `skills/vega-loop/SKILL.md` 或
  `skills/vega-review/SKILL.md`。
- `--force` 不改变该裁决。

#### 安全双生案例 `AV-M001-SAFE-001`

- 在目标仓库内部创建真实目录。
- 让仓库内 `.codex` 成为指向该仓库内目录的 Windows junction；POSIX 对应使用目录 symlink。
- 执行 adapter 初始化。

期望：

- CLI 成功。
- 两个 skill 文件均生成，且其真实解析路径仍位于目标仓库内。
- 既有普通目录 smoke 继续通过。

### 成功条件

- 危险案例与安全双生案例均按预期裁决。
- `force=False`、`force=True` 都不能把文件写出仓库。
- 受影响定向测试、Windows 专项回归、全量 pytest、`compileall`、`ruff` 和
  `git diff --check` 通过。
- 错误路径不包含仓库外目标的绝对路径，避免在 CLI 错误中额外泄露本地目录信息。

### 失败条件

- 任一 adapter 文件可经 junction、symlink 或可解析 reparse point 写到仓库外。
- 发现危险目标前已经写入同批次的其他 adapter 文件。
- 为阻止越界而无差别拒绝真实目标仍位于仓库内的安全链接。
- 边界解析失败后继续写入，或 `--force` 绕过边界检查。

### 已知限制

- `Path.resolve` 加写前复核不能消除拥有并发本地写权限的攻击者在最终检查后替换目录的
  TOCTOU 窗口。
- 本轮不处理 hardlink 别名、操作系统级恶意竞争或跨进程事务写入；如这些能力进入威胁模型，
  需要单独预注册句柄级实现与故障注入。

### 下一步

先新增危险案例和安全双生案例并确认危险案例在旧实现上可复现，再实现最小修复。

---

## 2026-07-22 · AV-M001-001 · local result

### Baseline

- Git 基线：`main@1e9bb52`
- 候选提交：`c1a9271328335be534fdc72e110d7f99fb36c5bb`
- 工作分支：`fix/adapter-realpath-boundary`
- 平台：Windows 10 / Python 3.14.3 / NTFS junction

### 旧实现复现

新增测试但尚未修改 Runtime 时：

```text
3 failed, 1 passed in 2.87s
```

三个危险案例都观察到 CLI 返回成功：外部 `.codex` junction 在 `force=False`、`force=True`
下均未被拒绝，后置 `vega-review` junction 还会让同批次的 `vega-loop` 文件先落盘。仓库内
安全 junction 控制案例通过。

### 实现结果

- 目标仓库根使用 `resolve(strict=True)` 固化；无法确认仓库路径时停止。
- 写入前先构造并预检整批 adapter 目标，任一真实路径越界时不产生本批次文件。
- 创建父目录使用已经解析且位于仓库内的路径，随后在写文件前再次解析逻辑目标。
- `force=False` 的既有文件跳过语义和 `force=True` 的覆盖语义都在边界检查之后执行。
- 错误只报告仓库相对目标，不包含仓库外真实路径。
- 真实目标仍位于仓库内的 junction/symlink 控制案例继续成功。

### 预注册案例结果

- `AV-M001-DANGER-001 / skip-existing`：通过。外部既有 sentinel 内容未变化，CLI 非零退出。
- `AV-M001-DANGER-001 / force`：通过。外部空目录没有新增 skill，CLI 非零退出。
- 后置危险目标批次预检：通过。`vega-loop` 未部分落盘，外部 `vega-review/SKILL.md`
  未生成。
- `AV-M001-SAFE-001`：通过。仓库内 junction 生成两个 skill，真实路径仍位于仓库内。
- 既有普通目录 smoke：通过。

### 本地验证

- 当前候选定向回归：`5 passed in 1.00s`。
- 收集合同：`520` 个节点，`520` 个唯一 nodeid。
- Windows 本地完整节点覆盖：`519 passed, 1 skipped`，无失败、无 error。
  - smoke：`102 passed`。
  - p0/CLI/lock：`109 passed`。
  - artifacts/runtime/security：当前 adapter 4 个节点通过；其余 `54 passed, 1 skipped`。
  - semantics/evidence/review：`80 passed`。
  - remaining：`170 passed`。
- 唯一跳过：
  `tests/test_runtime_safety_integration.py::test_posix_verification_temp_env_does_not_re_evaluate_path`，
  原因是本地 Windows 不执行 POSIX shell 变量展开语义；远端 POSIX job 必须真实通过。
- `ruff check src tests --no-cache`：通过。
- `python -m compileall -q src`：通过。
- `git diff --check`：通过。
- CI Python 3.12 分片文件合同：20 个测试文件，无遗漏、重复或意外文件。
- CI 收集阈值已从历史 v0.1.2 的 516 更新为当前 520；历史证据未改写。

### 本地证据

- 摘要：`.tmp/pytest/logs/m001-full-summary.json`
- SHA-256：`0EAF5B129353D3BCD1E5C2CADB3F94305137B58986D3AFF12C5B1100D247EB99`

以下尝试不计入通过结论：

1. 单进程全量 pytest 超过 304 秒外层限制且没有保留最终汇总。
2. 重定向 smoke 运行被外层终止后触发 pytest terminal writer
   `OSError: [Errno 22] Invalid argument`，JUnit 明确记录为 internal error。
3. 四分片并行时 semantics 分片达到 1200 秒组级上限；随后改用 80 个精确 nodeid 串行分片，
   全部返回 0。

### 裁决

`passed-local / requires-ci`

预注册的 Windows junction 危险案例与安全双生案例均满足退出条件，当前 520 个测试节点已在
本地完整覆盖。由于本机不能替代 Linux/POSIX、Python 3.11/3.12 和发布包安装验证，合并前仍
必须等待 PR CI 全部通过。

### 剩余风险

- 最终解析与文件写入之间仍存在已预注册的 TOCTOU 窗口。
- hardlink 别名、句柄级 no-follow、防恶意并发替换和跨进程事务不在本轮范围内。
- 本地 Python 3.14.3 的高负载耗时不能替代 CI 的 58 秒单节点预算；远端超时必须如实视为失败。

### 下一步

推送独立分支并创建 PR。只有静态检查、Python 3.11 全量、Python 3.12 五个分片、Windows
专项、POSIX 专项和 wheel/sdist 安装验证全部通过后，才追加 post-CI result 并提出合并建议。

---

## 2026-07-22 · AV-M001-001 · transport correction

首次推送提交 `c1a9271328335be534fdc72e110d7f99fb36c5bb` 被 GitHub `GH007` 邮箱隐私保护
拒绝，远端没有创建该分支。随后只把作者和提交者邮箱改为公开的 GitHub noreply 地址：

- 更正前提交：`c1a9271328335be534fdc72e110d7f99fb36c5bb`
- 更正后提交：`f44dca1772517bfe1f531a0be28f6c073472c527`
- 更正前 tree：`d6a709bb8f83261f3a4864466b6a9f1e9657c62b`
- 更正后 tree：`d6a709bb8f83261f3a4864466b6a9f1e9657c62b`

两个 tree 完全一致，代码、测试、文档和 CI 配置内容没有变化；本地验证结论继续绑定该相同
文件树。更正后的本地摘要为：

- 摘要：`.tmp/pytest/logs/m001-full-summary-v2.json`
- SHA-256：`F726417D3DB498970CF70E64F758852AC1DEA0E3B266A10CF0CAA3E85CC69345`

---

## 2026-07-22 · AV-M001-001 · post-CI result

### Baseline

- PR：`#2`
- PR head：`ffad154a1ab4a2b6e14a6779c8d114661905fba2`
- Workflow：`29896556016`
- 触发：`pull_request`

### GitHub Actions

Workflow 最终状态为 `completed / success`，10 个 job 全部通过：

1. 静态检查与节点收集。
2. Python 3.11 全量测试。
3. Python 3.12 分片 smoke。
4. Python 3.12 分片 p0-cli-lock。
5. Python 3.12 分片 artifacts-runtime-security。
6. Python 3.12 分片 semantics-evidence-review。
7. Python 3.12 分片 remaining。
8. Windows 专项与 wheel smoke。
9. POSIX 临时目录专项。
10. 构建并安装 wheel。

静态 job 通过意味着当前 workflow 中的 `520` 节点收集阈值和 20 个测试文件分片合同均满足。
Python 3.11 的“运行全量测试”步骤、五个 Python 3.12 分片、Windows、POSIX 和发布包安装
步骤分别返回 `success`。公开未登录 API 不提供原始 job 日志，因此本记录不臆造日志中未直接
读取到的逐项 pytest 汇总数字。

### 裁决

`passed-pr-ci / ready-for-human-merge-review`

预注册危险案例、安全双生案例、本地 520 节点覆盖和 PR 跨平台 CI 均满足 M-001 的退出条件。
该结论只支持进入人工 diff 与合并复核，不自动触发合并。

### 下一步

本条证据进入分支后会触发新的纯文档 CI。只有该最新 PR head 的 workflow 同样全部通过，且
人工复核完整 diff 后，才建议合并 `main`。合并后仍需核对 main CI，再更新路线图并开始
M-002。

---

## 2026-07-22 · AV-REPO-HYGIENE-001 · pre-CI

### Threat

公开仓库可能因文档、证据、示例、配置、注释或 Git 提交信息误带本机绝对路径而泄露开发
环境结构；后续提交删除该路径只能清理最终文件，不能自动清理已公开的提交历史。被强制
跟踪的环境文件、凭据、私钥、数据库或本地 Office 文件也可能绕过普通忽略规则。

### Contract

1. 当前跟踪文件和未忽略的新文件不得包含 Windows 盘符绝对路径、UNC 路径或真实 POSIX
   用户主目录。
2. 指定基线时，必须逐提交检查变化后的文件和提交信息，不能只检查基线到 `HEAD` 的净 diff。
3. 测试确需使用虚构绝对路径时，豁免必须与路径位于同一行、只允许出现在 `tests/`，失效
   豁免也必须 fail-closed。
4. 失败输出只报告仓库相对文件、行号、提交短哈希和规则，不回显命中的本机路径。
5. `.env.example` 以外的环境文件，以及常见凭据、私钥、数据库和 Office 本地文件名不得
   进入提交候选。
6. CI 必须使用精确 base SHA 执行历史扫描，并继续核对完整节点数和测试文件分片合同。

### Implementation

- 新增 `scripts/check_repository_hygiene.py`。
- `AGENTS.md` 增加相对路径、测试夹具豁免、敏感文件和 squash/history 规则。
- CI 静态 job 在测试收集前执行仓库卫生检查。
- 既有绝对路径测试夹具增加同一行显式豁免，普通文档不能使用该豁免。
- 新增 `tests/test_repository_hygiene.py`，覆盖盘符、UNC、POSIX 用户目录、非法或失效豁免、
  敏感文件名，以及“先提交、后删除”仍被历史扫描拒绝。

### Local validation

- 新门禁回归：`9 passed`。
- 受影响精确节点：`34 passed`。
- 收集合同：`529` 个节点，`529` 个唯一 nodeid。
- Python 3.12 分片合同：`21` 个测试文件，无遗漏、重复或意外文件。
- `python scripts/check_repository_hygiene.py --base-ref origin/main`：通过。
- `ruff check src tests scripts/check_repository_hygiene.py --no-cache`：通过。
- `python -m compileall -q src scripts/check_repository_hygiene.py`：通过。
- `git diff --check`：通过。

以下聚合尝试不计入通过结论：

1. 单进程全量 pytest 达到 15 分钟外层限制，没有产生最终汇总。
2. 五个受影响测试文件聚合运行达到 20 分钟外层限制，没有产生最终汇总。
3. 文件级诊断在 `tests/test_assurance_verification_semantics.py` 达到 240 秒文件级预算；
   终止后确认没有残留进程。随后将该文件的 14 个节点分别运行，结果为 `14 passed`，
   单节点耗时约 28 至 82 秒，未发现失败或单节点超时。

本地诊断证据：

- 节点收集：`.tmp/pytest/repo-hygiene-collected.txt`
- 节点收集 SHA-256：`FE09BDD0DBA2D410EBF528BF0E2D8B18B158F9E8D7A59761DA555E813A4F7C88`
- assurance 逐节点摘要：`.tmp/pytest/logs/assurance-nodes/summary.json`
- assurance 摘要 SHA-256：`28FF7126B7CF2FA29C4EF058E3083E1DA1D5361CD798D07AFD2ACDC683B53405`

### Verdict

`passed-targeted / requires-pr-ci`

新门禁的危险案例、安全案例、历史案例、日志不回显和文件名规则均通过本地定向验证。Windows
本机聚合测试没有形成完整通过证据，因此不得据此宣称全量绿；最终合并裁决必须等待 PR 的
Python 3.11 全量、Python 3.12 五分片、Windows 专项、POSIX 专项和发布包安装 job 全部成功。

---

## 2026-07-22 · AV-M002-001 · preregistration

### 目标

修复 `M-002`：Project Profile 必须为 Node 项目选择唯一可信的 npm、pnpm 或 yarn 命令。
仓库信号冲突且无法消歧时不得猜测执行，避免错误命令制造假失败、遗漏真实验证，或让
verification 依据错误工具链形成不可信结论。

### Baseline

- Git 基线：`main@6b74c5b`
- 工作分支：`fix/node-package-manager-selection`
- 已确认问题：`AV-BASE-004`
- 修改前收集合同：`529` 个节点、`21` 个测试文件。
- 影响入口：Project Profile、worker 项目上下文和自动 verification 命令选择。
- 范围：只修复根目录 Node 包管理器选择，不混入 M-003、Threat/Evidence 数据模型、依赖安装
  或新的 Agent 能力。

### 威胁模型

目标仓库的 `package.json` 和 lockfile 可能过期、冲突或来自错误的分支合并。当前实现分别
计算包管理器、test 命令和 lint 命令，使 pnpm 项目混入 npm，yarn 项目继续使用 npm。自动
verification 若执行这些命令，可能把工具链选择错误误报为代码失败，也可能错过项目真实的
测试与静态检查。

本轮防守边界：

1. `.vega.yaml` 中显式 verification 命令继续保持最高优先级；存在时不使用自动 Node
   test/lint 命令。
2. 顶层 `package.json.packageManager` 若明确声明 npm、pnpm 或 yarn，则优先于 lockfile。
3. 没有可用显式声明时，单一 `package-lock.json`、`pnpm-lock.yaml` 或 `yarn.lock` 决定
   对应包管理器。
4. 只有 `package.json` 且没有 lockfile 时保留 npm 默认值。
5. 多个受支持 lockfile 同时存在且没有显式声明时 fail-closed：不选择 Node 包管理器，
   不生成 Node test/lint 命令。
6. `tracked_only=True` 时，`packageManager` 必须来自固定 Git revision，不能读取工作区中
   未提交的修改。

### 方案取舍

- 采用：先得到一次 Node 包管理器选择结果，再让 package manager、test 和 lint 三处消费
  同一结果，避免独立条件继续漂移。
- 不采用：分别给 `_detect_package_managers`、`_detect_test_commands` 和
  `_detect_lint_commands` 增加局部判断。该方案改动小，但无法从结构上保证三个输出一致。
- 不采用：同时生成 npm、pnpm、yarn 命令并依赖“哪个能运行”。这会把环境偶然性当作证据，
  违反 fail-closed。
- 暂不新增 `ProjectProfile` 字段；冲突案例首先通过“不选择、不执行”收紧行为。若后续需要
  面向用户解释歧义，再单独设计版本化诊断字段。

### 预注册案例

#### npm 安全案例 `AV-M002-NPM-001`

- 只有 `package.json` 时选择 npm。
- `package.json` 与单一 `package-lock.json` 并存时选择 npm。
- 精确命令为 `npm test` 与 `npm run lint`。

#### pnpm 安全案例 `AV-M002-PNPM-001`

- `package.json` 与单一 `pnpm-lock.yaml` 并存时只选择 pnpm。
- `packageManager` 单独声明 pnpm 时也选择 pnpm。
- 精确命令为 `pnpm test` 与 `pnpm run lint`，不得混入 npm/yarn。

#### yarn 安全案例 `AV-M002-YARN-001`

- `package.json` 与单一 `yarn.lock` 并存时只选择 yarn。
- 精确命令为 `yarn test` 与 `yarn lint`，不得混入 npm/pnpm。

#### 配置消歧案例 `AV-M002-CONFIG-001`

- `packageManager` 声明 yarn，同时存在陈旧的 npm/pnpm lockfile。
- 期望：显式声明胜出，只生成 yarn 命令。

#### 冲突危险案例 `AV-M002-DANGER-001`

- `package.json` 与 npm、pnpm、yarn 三个 lockfile 同时存在，且没有 `packageManager`。
- 期望：不选择 npm/pnpm/yarn，不生成任何 Node test/lint 命令。

#### 固定 revision 案例 `AV-M002-TRACKED-001`

- Git `HEAD` 声明 pnpm，工作区未提交内容改为 yarn。
- 以 `tracked_only=True` 构建画像。
- 期望：仍只得到 pnpm 命令，证明隔离审查没有读取工作区污染。

#### 显式验证控制案例 `AV-M002-VEGA-001`

- pnpm 项目同时配置 `.vega.yaml` verification 命令。
- 期望：包管理器仍可识别为 pnpm，但 test 命令只保留显式配置，lint 为空。

### 成功条件

- 所有预注册案例按期望裁决，npm 安全控制不回归。
- pnpm/yarn 项目不出现 npm 命令，冲突 lockfile 不产生猜测命令。
- 固定 revision 与工作区内容不一致时只信任指定 revision。
- 受影响定向测试、完整 pytest、`compileall`、`ruff`、仓库卫生门禁和
  `git diff --check` 通过。
- Python 3.11、Python 3.12 分片、Windows、POSIX 和发布包安装 CI 全部通过后，才可建议
  合并。

### 失败条件

- 任一 pnpm/yarn 案例仍混入 npm，或 npm 案例被错误切换。
- 多 lockfile 无显式声明时仍猜测某个包管理器。
- `package_managers` 与 test/lint 命令来自不同选择结果。
- tracked profile 读取未提交的 `packageManager`。
- 为修复选择问题而降低 `.vega.yaml` 显式 verification 的优先级。

### 已知限制

- 本轮不判断 `scripts.test` / `scripts.lint` 是否存在，只验证包管理器选择。
- 不处理嵌套 workspace 的多包管理器、bun/deno、Corepack 自动安装或依赖完整性。
- 未知包管理器、损坏的 `package.json` 和面向用户的歧义诊断需另行预注册。

### 外部规则依据

- Node.js Corepack 文档：`packageManager` 用于声明项目期望的包管理器及版本。
- npm CLI 文档：测试脚本使用 `npm test`，其他脚本可使用 `npm run <script>`。
- pnpm CLI 文档：测试脚本可使用 `pnpm test`，其他脚本使用 `pnpm run <script>`。
- Yarn CLI 文档：项目脚本可通过 `yarn <script>` 执行。

### 下一步

先只新增上述回归并在旧实现上记录红灯；确认失败原因与 `AV-BASE-004` 一致后，再修改
`src/vega/project_profile.py`。本条预注册阶段不得把失败测试描述为可合并结果。

---

## 2026-07-22 · AV-M002-001 · red test result

### Baseline

- Git 基线：`main@6b74c5b`
- 工作分支：`fix/node-package-manager-selection`
- 平台：Windows / Python 3.14.3
- 本阶段修改边界：测试、路线图、验证记录与 CI 节点合同；`src/vega/` 没有修改。

### 旧实现复现

只运行预注册的 9 个 M-002 节点：

```text
6 failed, 3 passed in 3.53s
```

三个通过控制为：

1. 仅 `package.json` 的 npm 默认值。
2. 单一 `package-lock.json` 的 npm 项目。
3. `.vega.yaml` 显式 verification 继续覆盖自动命令。

六个失败与 `AV-BASE-004` 一致：

1. pnpm lockfile 项目同时生成 `npm test` 与 `pnpm test`，lint 仍为 npm。
2. yarn lockfile 项目仍生成 npm test/lint。
3. 仅由 `packageManager` 声明的 pnpm 项目被识别为 npm。
4. `packageManager` 声明 yarn 时，陈旧 lockfile 仍让实现选择 pnpm。
5. 三个 lockfile 冲突且无显式声明时，实现猜测选择 pnpm，而不是 fail-closed。
6. tracked profile 虽识别 pnpm，但命令集合仍混入 npm。

### 收集合同

- 完整收集：`538` 个节点。
- 唯一 nodeid：`538` 个。
- 新增节点：`9` 个。
- 测试文件数量保持 `21` 个，因此 Python 3.12 分片文件集合不变。

### 本地证据

- 红灯日志：`.tmp/pytest/logs/m002-red-before-fix.txt`
- SHA-256：`E19FE8C85D0F77C013D827331F39A0D8D9CB29AA4D57E2343404AB861158AA35`
- 收集清单：`.tmp/pytest/m002-collected.txt`

### 裁决

`confirmed-red / not-mergeable`

危险案例稳定复现，npm 与显式 verification 控制案例保持通过，说明红灯针对的是 Node
包管理器选择缺口而不是 fixture 基础设施故障。本结果只证明旧实现存在问题，不代表候选
分支可合并。

### 下一步

在同一分支实现一次性 Node 包管理器选择结果，并让 package manager、test 与 lint 输出共同
消费；转绿后再运行受影响回归、完整门禁与跨平台 CI。

---

## 2026-07-22 · AV-M002-001 · local result

### Baseline

- Git 基线：`main@6b74c5b`
- 红灯提交：`2fad84cdc16a67eacdc9579dfb229e726c002cf1`
- 实现提交：`c6b5325d025c64270c2bd1fa98fb3b7ae9dc8e2f`
- 实现 tree：`2fbbda1907631a16394e786efe70e47f84dda084`
- 工作分支：`fix/node-package-manager-selection`
- 平台：Windows / Python 3.14.3

### 实现结果

- 新增一次性 Node 包管理器选择结果，项目画像、test 和 lint 不再分别猜测。
- 顶层 `packageManager` 的受支持声明优先于 lockfile。
- 无显式声明时，单一 lockfile 决定 npm、pnpm 或 yarn。
- 只有 `package.json` 时保留 npm 默认值。
- 多 lockfile 冲突时不选择 Node 包管理器，也不生成 Node test/lint 命令。
- 显式声明存在但无法识别时停止猜测，不回退到可能陈旧的 lockfile。
- tracked profile 使用固定 revision 中的 `package.json` 内容。
- `.vega.yaml` 显式 verification 覆盖自动 test/lint 的既有优先级保持不变。

### 预注册案例结果

旧实现的 `6 failed, 3 passed` 已转为：

```text
9 passed in 3.18s
```

- `AV-M002-NPM-001`：通过。
- `AV-M002-PNPM-001`：通过，未混入 npm/yarn。
- `AV-M002-YARN-001`：通过，未混入 npm/pnpm。
- `AV-M002-CONFIG-001`：通过，显式 yarn 声明消歧陈旧 lockfile。
- `AV-M002-DANGER-001`：通过，多 lockfile 无声明时 fail-closed。
- `AV-M002-TRACKED-001`：通过，只读取固定 revision 的 pnpm 声明。
- `AV-M002-VEGA-001`：通过，显式 verification 继续优先。

### 受影响回归

- `tests/test_context_boundaries.py`：`34 passed`。
- `tests/test_security_evidence.py`：`15 passed`。
- `tests/test_project_config_hardening.py`：`25 passed`。
- Project Profile 与 verification 关键调用节点：`5 passed`。

### 完整本地覆盖

- 完整收集：`538` 个节点。
- 唯一 nodeid：`538` 个。
- 完整分片结果：`537 passed, 1 skipped`，无失败、无 error。
  - smoke：`102 passed`。
  - p0-cli-lock：`109 passed`。
  - artifacts-runtime-security：`58 passed, 1 skipped`。
  - remaining：`188 passed`。
  - success-semantics：`29 passed`。
  - evidence-freshness：`19 passed`。
  - review-integrity：`18 passed`。
  - assurance-semantics：`14 passed`。
- 唯一跳过：
  `tests/test_runtime_safety_integration.py::test_posix_verification_temp_env_does_not_re_evaluate_path`；
  当前平台为 Windows，该节点只覆盖 POSIX shell 变量展开语义。

首次显式指定分片 basetemp 时，其父目录尚不存在，pytest 在 fixture setup 阶段产生
`FileNotFoundError`。该尝试没有形成产品失败证据，不计入裁决；创建受控父目录后，全部
538 个节点已重新执行并得到上述结果。

### 静态门禁

- `python -m compileall -q src scripts/check_repository_hygiene.py`：通过。
- `ruff check src tests scripts/check_repository_hygiene.py --no-cache`：通过。
- `python scripts/check_repository_hygiene.py --base-ref origin/main`：通过。
- `git diff --check`：通过。

### 本地证据

- 结构化摘要：
  `examples/evidence/m002-node-package-manager-local-summary.json`
- 摘要 SHA-256：
  `959730D1F60CE83604AB01640977AE5D42A6A6BE6C8FBF71DE1BB5AB11B02752`
- 接力文档：`docs/M002-NODE-PACKAGE-MANAGER-HANDOFF.md`
- 分片原始日志位于未跟踪的 `.tmp/pytest/logs/`，其 SHA-256 已写入结构化摘要。

### 裁决

`passed-local / requires-ci / do-not-merge`

预注册危险案例、安全控制、固定 revision 边界和本地 538 个节点均满足 M-002 的本地退出
条件。该结果不替代 Python 3.11/3.12、Linux/POSIX、Windows CI 和发布包安装验证，也不授权
自动合并或发版。

### 剩余风险

- 本轮不判断 `scripts.test` / `scripts.lint` 是否存在。
- 不处理嵌套 workspace、bun/deno 或 Corepack 自动安装。
- 冲突与未知声明目前静默 fail-closed，尚未提供版本化的用户诊断字段。
- Python 3.14.3 的本地高负载耗时不能替代 CI 的 58 秒单节点预算。

### 下一步

推送当前隔离分支与接力文档，供另一台机器继续。后续最多先创建 Draft PR 并等待全部 CI；
在 post-CI 证据和人工 diff 复核完成前，不合并 `main`。

---

## 2026-07-22 · AV-M002-001 · first PR CI and review correction

### 首轮 PR CI

- PR：`#5`
- PR head：`1b8d2cc2b88907d487f33bbf597bcb899811b8ef`
- Workflow：`29923884827`
- 状态：`completed / success`
- Jobs：`10/10 success`

通过范围包括静态检查与 538 节点收集合同、Python 3.11 全量、Python 3.12 分片、
Windows 专项与 wheel smoke、POSIX 临时目录专项，以及 wheel 构建安装验证。

### 独立审阅发现

实现审阅未发现阻塞性代码问题，但测试与证据审阅发现：代码和接力文档都承诺“显式
`packageManager` 声明损坏或不受支持时 fail-closed”，原有 9 个预注册节点没有直接覆盖
该声明。首轮 CI 通过只能证明当时的 538 节点全绿，不能替代缺失的合同回归。

通用的“证据文件自动绑定当前 Git head”门禁属于后续 Assurance 数据合同，不在 M-002
内扩建；本轮继续通过明确记录 head、workflow 和人工复核保持证据边界。

### 审阅修正

- 新增非字符串 `packageManager` 声明的 fail-closed 回归。
- 新增不受支持的 `bun` 声明不能回退到陈旧 pnpm lockfile 的回归。
- 相关选择链路：`11 passed in 1.68s`。
- 完整收集：`540` 个节点。
- CI 节点合同同步更新为 `540`。
- compileall、Ruff、仓库卫生检查和 `git diff --check` 通过。

### 裁决

`first-pr-ci-passed / review-correction-passed-local / latest-head-requires-ci / do-not-merge`

首轮 workflow `29923884827` 已证明 `1b8d2cc` 的跨平台结果，但新增回归会形成新的 Git
head。只有该最新 head 的 540 节点 CI 同样全部通过后，才能追加最终 post-CI 结果、更新
Roadmap 并提出合并建议。

---

## 2026-07-22 · AV-M002-001 · corrected head post-CI result

### PR 与快照

- PR：`#5`
- 代码 head：`9e649ded05ebfa8f272f7e2bd1b134ac9207170f`
- Workflow：`29924503421`
- 状态：`completed / success`
- Jobs：`10/10 success`
- 收集合同：`540` 个节点

### 跨平台结果

以下 job 全部成功：

1. 静态检查与节点收集。
2. Python 3.11 全量测试。
3. Python 3.12 的 smoke、p0-cli-lock、artifacts-runtime-security、
   semantics-evidence-review 和 remaining 分片。
4. Windows 专项与 wheel smoke。
5. POSIX 临时目录专项。
6. wheel 构建与安装。

首轮 `1b8d2cc` 的 538 节点 CI 已通过；独立审阅补出的两个合同节点进入 `9e649de` 后，
第二轮 540 节点 CI 仍为 10/10 success。实现审阅未发现阻塞性代码问题，测试与证据审阅
提出的缺口已经关闭。

### 裁决

`passed-pr-ci / code-ready / final-docs-ci-required`

M-002 的实现、危险案例、安全控制、固定 revision 读取、失效声明 fail-closed 和跨平台
验证均满足退出条件。本条 post-CI 证据、接力文档和 Roadmap 更新会形成新的纯文档 head；
只有该最新 head 的 PR CI 同样全部通过后，才允许把 PR 转为 Ready 并合入 `main`。

### 后续边界

- `scripts.test` / `scripts.lint` 存在性诊断不在本轮。
- 嵌套 workspace、bun/deno 和 Corepack 自动安装不在本轮。
- 通用 Evidence 文件与 Git head 的机器校验属于后续 Assurance 数据合同。
- 合并后先核对 `main` CI，再把唯一 `Now` 转交给独立的 M-003。

---

## 2026-07-22 · AV-M003-001 · preregistration

### 目标

修复 `M-003`：同一次 Finish 调用只能执行一次终态 artifact integrity 验证，并把该结果与
evidence freshness 组合成同一个可信 Evidence Validation Snapshot。不能删除完整性、新鲜度、
risk gate 或 scope gate 的终态重算，也不能改变任何 fail-closed 结果。

### Baseline

- Git 基线：`main@da1ac290addd0042f8782476cdb5ece4e53f2aa8`
- 工作分支：`perf/finish-evidence-snapshot`
- 已确认问题：`AV-BASE-005`
- 影响入口：`vega finish <run>`
- 范围：只消除同一次 Finish 内重复的完整性整链计算，不混入 Stage 1 Threat/Evidence
  数据合同、Goal 优化、runner 调参或新的 Agent 能力。

### 当前重复链路

旧实现按以下顺序执行：

```text
FinishRuntime
  -> validate_loop_artifact_integrity
  -> validate_loop_evidence_freshness
       -> current workspace freshness
       -> review / reflect freshness
       -> validate_loop_artifact_integrity
```

第二次 integrity 位于 freshness 链路末端，是更接近最终写入 `finish-summary.json` 的终态
重算。候选实现必须保留该安全位置，只删除前面的重复 integrity 调用。

### 预注册回归

新增一个成功控制节点：

1. 先构造 verification passed、review approve 且证据完整的新 loop。
2. 在构造完成后开始统计 integrity 调用，避免把 Loop 自身校验计入 Finish。
3. 同时拦截旧 Finish 别名和 `goal_evidence` 中的真实函数。
4. 执行一次 Finish。
5. 要求 integrity 调用次数为 `1`。
6. 同时要求：
   - `finish_status=ready_to_commit`
   - `artifact_integrity.valid=true`
   - `evidence_freshness.fresh=true`

旧实现预期调用次数为 `2`，该断言应先稳定红灯；候选实现转绿后，现有篡改、缺失、错绑、
风险降级和 workspace 变化测试必须继续 fail-closed。

### 收集合同

- 预期完整节点：`541`
- 新增节点：`1`
- 测试文件数量不变，因此 Python 3.12 分片文件集合不变。

### 退出条件

- 预注册节点从 `2 != 1` 转为通过。
- Finish 只消费一个 Evidence Validation Snapshot。
- 现有 Finish artifact integrity 与 evidence freshness 回归全部通过。
- 完整节点、静态检查、Windows、POSIX 和构建安装 CI 全绿。
- 绝对耗时只作为观察数据，不作为跨机器硬阈值。

### 明确不做

- 不缓存跨 Finish 调用的结果。
- 不跳过终态 integrity、freshness、scope 或 risk 重算。
- 不把可变 evidence 写入长期 memory。
- 不开始 Assurance Stage 1。

---

## 2026-07-22 · AV-M003-001 · red test result

### Baseline

- Git 基线：`main@da1ac290addd0042f8782476cdb5ece4e53f2aa8`
- 工作分支：`perf/finish-evidence-snapshot`
- 平台：Windows / Python 3.12
- 本阶段修改边界：测试、预注册证据和 CI 节点合同；`src/vega/` 没有修改。

### 旧实现复现

只运行新增的 M-003 节点：

```text
1 failed in 18.75s
E assert 2 == 1
```

loop fixture、verification、review 和 Finish 均正常执行并写出 `finish-summary.json`。失败发生在
首个预注册断言：同一次 Finish 实际调用 artifact integrity `2` 次，而合同要求 `1` 次。
因此该红灯直接对应 `AV-BASE-005` 的重复整链计算，不是测试基础设施或业务证据损坏。

### 收集与静态门禁

- 完整收集：`541` 个节点。
- compileall：通过。
- Ruff：通过。
- 仓库路径与私密文件卫生检查：通过。
- `git diff --check`：通过。

### 本地证据

- 红灯日志：`.tmp/pytest/logs/m003-red-20260722.txt`
- SHA-256：`8159EA07E93A85DA7102503F6A33C33AD8B1293A57F46B04673C73F9A4DDEE01`

`.tmp` 不提交；哈希只用于本机复核，跨机器结论仍须由提交后的测试和 PR CI 证明。

### 裁决

`confirmed-red / not-mergeable`

旧实现稳定复现两次 artifact integrity。下一步只能把 Finish 改为消费一个在 freshness 链路
末端生成的 Evidence Validation Snapshot；不得通过删除末端 integrity 或弱化 fail-closed
断言来让测试转绿。

---

## 2026-07-22 · AV-M003-001 · local candidate result

### Candidate

- 候选提交：`15924027000f78fd61139ce8a952aa32ccb23188`
- 候选 tree：`ac15f524475bfb2a303865dadd475aac38707fde`
- 平台：Windows / Python 3.12.10
- 修改边界：`goal_evidence`、`finish_runtime` 和同一预注册测试节点。

### 实现结果

Finish 现在调用 `validate_loop_evidence_snapshot()`，由 freshness 链路末端产生一次
artifact integrity，并将两者作为同一个快照返回。正常成功路径不再先独立执行一次
integrity，因此预注册调用次数从 `2` 降为 `1`。

公开 `validate_loop_evidence_freshness()` 保持原有早退合同：没有 `review_run` 或引用的 review
run 不存在时，不执行完整 integrity。Finish snapshot 在这些早退路径才补一次 integrity，
从而既保持公开 API 语义，又保证 Finish 始终获得完整快照。

### 预注册节点

```text
1 passed in 14.81s
```

节点同时确认：

- `finish_status=ready_to_commit`
- `artifact_integrity.valid=true`
- `evidence_freshness.fresh=true`
- `verification_passed=true`
- `risk_gate_result_count=1`
- 两种 freshness 早退路径的 integrity 调用次数均为 `0`

红灯的 18.75 秒与候选的 14.81 秒只作为同机观察，不作为跨平台性能阈值。

### 独立审阅

独立审阅发现初版让公开 freshness API 的两个早退路径新增完整 integrity 扫描，扩大了 Goal
等调用方的耗时和异常面。该问题已通过私有协调函数和同一预注册节点的两种早退断言修正。
审阅未发现更高严重级别问题，也未发现 fail-closed、scope、risk、project policy 或
verification 结论被放宽。

### 完整本地覆盖

- 收集：`541` 个节点。
- 唯一 nodeid：`541` 个。
- 最终结果：`540 passed, 1 skipped, 0 failed, 0 errors`。
- 唯一跳过：POSIX shell 变量展开专项；Windows 本地按合同跳过，Linux CI 必须真实通过。
- 最终有效分片：`71` 个，每个都有明确 passed/skipped 计数。

本机没有安装 CI 额外依赖 `pytest-timeout`，带对应参数的首次命令在收集前退出；部分大分片
也在并发负载下超过 60 秒。两类尝试均未计入产品裁决，最终使用完整 nodeid 集合细分并覆盖
全部 541 个节点。

### 静态门禁

- `python -m compileall -q src scripts/check_repository_hygiene.py`：通过。
- `ruff check src tests scripts/check_repository_hygiene.py --no-cache`：通过。
- `python scripts/check_repository_hygiene.py --base-ref main`：通过。
- `git diff --check`：通过。

### 本地证据

- 结构化摘要：`examples/evidence/m003-finish-snapshot-local-summary.json`
- 接力文档：`docs/M003-FINISH-EVIDENCE-SNAPSHOT-HANDOFF.md`
- 最终节点日志 SHA-256：
  `4F08608B48F7AF4275922DE663A1FE270259FBFBEC9D7B1B9CACE07930830B38`
- 完整分片汇总 SHA-256：
  `3E3C92E9FCC72BE9DC3C490A6F96C2BCD87CF157D4F3C07535EA8D6EE97DAF5B`

### 裁决

`passed-local / requires-ci / do-not-merge`

M-003 已满足本地退出条件，但 Python 3.11/3.12、Linux/POSIX、Windows wheel 和构建安装
仍必须由 PR CI 证明。在代码 head CI 和独立 PR 审阅通过前，不更新 Roadmap 为 completed，
不开始 Assurance Stage 1。

---

## 2026-07-22 · AV-M003-001 · PR CI and final review correction

### PR

- PR：`#6`
- base：`main`
- head：`perf/finish-evidence-snapshot`
- 状态：Draft

### 首轮代码与证据 head

```text
927f46eff7231ded01e91a0d5d87312f5624ed0f
workflow 29931641373
10/10 success
```

通过范围包括静态检查与 541 节点收集合同、Python 3.11 全量、Python 3.12 五个分片、
Windows 专项与 wheel smoke、POSIX 临时目录专项，以及 wheel 构建安装。

### 最终独立审阅

最终只读审阅未发现阻塞问题，也未发现 fail-closed、state/workspace/review、scope、risk、
project policy 或 verification 语义被削弱。

审阅指出一个低严重级别测试缺口：同一预注册节点已证明公开 freshness 的两个早退路径不会
执行 integrity，但没有直接证明 Finish snapshot 在这些路径恰好补一次 integrity。修正提交
`efc09c69491f0eb79293e1f8e3a94e2228cabf98` 增加两组 `integrity_calls == 1` 断言，
节点结果为：

```text
1 passed in 14.73s
```

该修正不改变实现、不增加测试节点。

### 审阅修正 head CI

```text
efc09c69491f0eb79293e1f8e3a94e2228cabf98
workflow 29932356389
10/10 success
```

最新代码 head 已再次通过相同的跨平台 10 项门禁，证明新增断言与 M-003 实现共同满足
541 节点合同。

### Roadmap 裁决

代码 head 已满足 M-003 退出条件，因此统一 Roadmap 更新为：

- `M-003=completed`
- `Stage 0=completed`
- `Stage 1=next`

### 当前裁决

`passed-pr-ci / final-docs-ci-required / do-not-merge`

本次 post-CI 证据和 Roadmap 会形成新的纯文档 head。该最新 head 仍须通过全部 CI 后，才能
把 PR 从 Draft 转为 Ready；不自动合并，不开始 Stage 1 实现。

---

## 2026-07-22 · AV-STAGE1-001 · preregistration

### 目标

冻结并验证 Assurance Stage 1 的最小数据合同：版本化 Claim、Threat、EvidenceRecord、
AssuranceBundle 和确定性 AdequacyResult。该阶段只生成独立 Assurance artifact，不修改
Finish、Goal 或 Runtime 的成功语义。

### Baseline

- Git 基线：`main@775e1b9fb20f6c842ca70b7766abd76bab9707e3`。
- 工作分支：`feat/assurance-stage1-contract`。
- 稳定版本：`v0.1.2`。
- 测试文件：`tests/test_assurance_stage1_contract.py`。

### 预注册问题

1. 缺少必填字段或出现未知结构时，是否只能得到 fail-closed 结果。
2. 伪造、重复或悬空的 Claim/Threat/Evidence 引用是否被拒绝。
3. run、iteration、HEAD、staged/unstaged diff、review snapshot、项目策略和 scope policy
   错绑时，是否不能给出充分结论。
4. artifact 相对路径逃逸、文件缺失或 SHA-256 不一致时，是否被拒绝。
5. 旧 artifact 是否仍可读取，但不能升级为 `sufficient_for_merge`。
6. LLM 来源是否只能保留为候选，不能激活 Threat 或宣布证据充分。
7. 危险案例缺少最低证据时是否为 `insufficient`，对应安全双生案例是否能得到
   `sufficient_for_merge`。
8. 残余风险和人工决策是否分别收敛到 `requires_staged_rollout` 与 `human_required`。
9. 损坏输入是否仍能生成独立、脱敏的 fail-closed `assurance-result.json`。

### 非目标

- 不接 Runtime detector。
- 不改变 `ready_to_commit`、Finish 或 Goal 成功规则。
- 不调用 LLM。
- 不实现数据库 migration、数据修改或并发 Threat Family。
- 不引入数据库、Web UI、LangGraph、Memory 或多 Agent 产品能力。

### 预期

- 新增 26 个纯合同节点，完整收集从 541 增至 567。
- 旧实现因缺少 `vega.assurance` 而稳定红灯。
- 实现后 26 个节点全部通过，且现有 541 个节点结果不被放宽。
- CI 节点合同更新为 567，并把新文件加入 Python 3.12
  `semantics-evidence-review` 分片。

### 停止条件

只有以下条件同时满足，Stage 1 才可提出合并建议：

1. 26 个预注册节点转绿。
2. 完整 567 节点得到明确 passed/skipped/failed 计数。
3. compileall、Ruff、仓库卫生和 `git diff --check` 通过。
4. 独立审阅未发现 fail-open、引用逃逸或 LLM 越权。
5. 最新 PR head 的跨平台 CI 全绿。

---

## 2026-07-22 · AV-STAGE1-001 · red test result

### 旧实现复现

只运行预注册文件：

```text
26 failed in 0.92s
ModuleNotFoundError: No module named 'vega.assurance'
```

完整收集结果：

```text
567 tests collected in 10.87s
```

所有失败都发生在测试按需导入 Stage 1 合同模块时。测试文件本身已通过 Ruff；完整节点收集
成功，因此红灯不是 pytest fixture、临时目录或收集基础设施故障，而是当前主线确实没有该
合同能力。

### 本地证据

- 红灯日志：`.tmp/pytest/logs/stage1-red.txt`
- 红灯日志 SHA-256：
  `9B841AB9CE2045384F0BA98EE71E637CE69569661BB5613E607A54A7468426F7`
- 收集日志：`.tmp/pytest/logs/stage1-collect.txt`
- 收集日志 SHA-256：
  `DA87D6C07638D1E199BA1F475B995A0B46E5C1A250AF1A6BD3B28D2B700430AC`

### 裁决

`confirmed-red / not-mergeable`

下一步只能实现严格版本化模型、run-local artifact 引用解析和确定性充分性校验器；不得通过
放宽 snapshot、引用或 LLM 来源约束让测试转绿，也不得在本阶段接入 Finish 成功条件。

---

## 2026-07-22 · AV-STAGE1-001 · independent review correction

### 首轮实现结果

预注册的 26 个节点一度全部通过，但三路独立只读审阅发现该结果不足以满足合同：

1. 其他 run 或旧 iteration 中哈希相同的 artifact 可被当前 EvidenceRecord 复用。
2. 任意文件只要哈希匹配，就可能配合 Bundle 自报的 `result.status=passed` 冒充证据。
3. Bundle 自报的 `verification_conclusion` 未由结构化 artifact 重算。
4. LLM 可把来源标签改写为 `deterministic_detector`，原实现没有独立来源集合绑定。
5. candidate Threat 的悬空 Claim/Evidence 引用未被检查。
6. Pydantic 默认类型转换接受字符串 iteration 或布尔 schema version。
7. scope policy hash 可为 `null`。
8. Evidence 读取没有单文件、总字节和输入大小预算。
9. Windows 专项和 wheel smoke 没有直接覆盖新模块。

该首轮 `26 passed` 不能作为可合并证据，也没有提交。

### 修正

- Artifact 强制绑定当前 run/current iteration 的固定 verification-result 路径。
- 解析 verification artifact v2，重算命令、结果、失败数、中断和总体验证结论。
- 可信上下文独立冻结 accepted Claim、active Threat 和允许的 Evidence 合同 hash。
- 所有 Threat（包括 LLM candidate）都检查双向引用。
- 全部模型启用严格类型，scope policy hash 改为必需。
- 路径拒绝盘符、URI scheme、NTFS ADS、`..` 和静态 symlink/junction 逃逸。
- 输入、单 Evidence 文件和总读取量增加硬预算，并按真实路径去重。
- 独立 AdequacyResult 写入 snapshot、来源集合 hash 和输入 hash。
- Windows CI 直接运行 Stage 1 文件，Windows/Linux wheel smoke 都导入新模块。

### 审阅修正回归

测试文件从 26 个节点扩展到 54 个节点，新增覆盖：

- 重复 Claim/Threat/Evidence ID 与完整双向引用。
- 未知字段、不支持版本、布尔版本号和错误类型。
- Bundle、Evidence、Artifact 的 run/iteration/snapshot 错绑。
- 文件缺失、目录替代、真实链接逃逸、NTFS ADS 和跨 run/iteration 复用。
- 结构化 verification 与 Evidence 自报结果矛盾。
- LLM 来源伪装和 candidate 悬空引用。
- 损坏/深层 JSON、输入过大、Evidence 文件过大和持久化脱敏。
- 结果 snapshot 与可信来源 hash 绑定。

当前定向结果：

```text
54 passed in 2.64s
595 tests collected
```

compileall、Ruff、仓库卫生和 `git diff --check` 通过。

### 裁决

`review-correction-passed-targeted / full-suite-required / do-not-merge`

下一步必须用修正后的 595 节点合同重新跑完整本地分片。此前因 basetemp 父目录未预创建而产生
的 fixture setup errors 属于无效验证尝试，不计入产品裁决；重跑必须预先创建每个分片父目录。

---

## 2026-07-22 · AV-STAGE1-001 · candidate checkpoint

### 最新合同

独立审阅继续发现并修正以下边界：

1. Assurance input 在 `stat` 后增长时仍可能被完整读入。
2. verification artifact 未绑定声明命令、实际命令、命令序号和 verification 临时目录。
3. 同一个 oversized artifact 失败后会被多个 Evidence 重复读取。
4. 合法的多命令中断 artifact 会因存在 `skipped_commands` 被误判为结构损坏。
5. 双斜杠会被 `PurePosixPath` 规范化，导致原始空路径段未被拒绝。

新增红灯均在旧实现上稳定失败，修正后 Stage 1 文件结果为：

```text
59 passed in 1.24s
600 tests collected
```

候选实现提交：`9a67692`。

### 全量分片状态

最后三项审阅修正前，597 节点四分片得到：

```text
595 passed
1 skipped
2 failed
0 timed out
```

两条失败分别为 owner crash recovery 在长 Windows basetemp 下进入 `needs_human`，以及
dogfood eval 在四路并发下只完成 `7/8`。两条随后使用短路径、无并发条件串行复跑：

```text
owner crash recovery: 1 passed in 13.73s
dogfood eval: 1 passed in 37.14s
```

该串行结果关闭了两条既有测试的回归疑点，但最新 600 节点尚未重新完成全量分片，不能把
597 节点结果外推为最终通过。

### 静态门禁

以下命令通过：

```text
python -m compileall src scripts/check_repository_hygiene.py
ruff check src tests scripts/check_repository_hygiene.py --no-cache
python scripts/check_repository_hygiene.py --base-ref main
git diff --check
```

### 裁决

`candidate-committed / full-600-and-pr-ci-required / do-not-merge`

下一步只等待并核对最新分支 head 的跨平台 PR CI；若失败，必须修真实问题，不得放宽
Assurance 合同或把独立 `sufficient_for_merge` 接入当前 Runtime/Finish 成功语义。

---

## 2026-07-23 · AV-STAGE1-001 · draft PR CI 与 post-CI 只读复核

### CI 身份

- Draft PR：`#7`，`Assurance Stage 1 威胁与证据数据合同`。
- CI head：`b263d3020442ba07f2676282766b7cc14f7f9310`。
- GitHub Actions run：`29973446567`，事件：`pull_request`。
- 运行结论：`success`。

### 已通过的 CI 覆盖

- 静态检查与 600 节点收集合同。
- Python 3.11 全量测试。
- Python 3.12 的 `smoke`、`p0-cli-lock`、`artifacts-runtime-security`、
  `semantics-evidence-review` 与 `remaining` 五个分片。
- Windows 专项与 wheel smoke、POSIX 临时目录专项、构建并安装 wheel。

### 本地复核边界

- 当前候选 head 的 Stage 1 定向测试为 `59 passed`，本地收集为 `600 tests collected`；
  compileall、Ruff、相对 `origin/main` 的仓库卫生检查和 diff 检查均通过。
- 本机 Python 3.14 上直接执行全量 pytest 在外层 20 分钟执行上限前没有形成最终汇总；
  该尝试既不是全量绿灯，也不作为产品失败结论。跨平台 CI 是本条目的全量测试证据。

### post-CI 只读复核

- 变更面仅包含 Stage 1 合同模块、合同测试、CI 收集/专项覆盖与对应文档；没有把
  `AdequacyResult` 接入 Finish、Goal 或 Loop 的现有成功语义。
- 复核了严格 schema、可信上下文 hash、当前 run/current iteration artifact 绑定、
  有界读取与缓存、verification artifact v2 的命令/结果绑定，以及 LLM candidate
  不能升级为 active Threat 或充分证据的 fail-closed 路径。
- 本轮未发现新的阻断项；仍不把 Stage 1 的独立 `sufficient_for_merge` 解释为生产安全或
  Runtime 成功。

### 裁决

`pr-ci-passed / post-ci-evidence-appended / final-head-ci-required / do-not-merge`

本条目改变了 PR head 的文档证据，因此必须等待该最新文档 head 再次完成跨平台 PR CI；
在该 CI 全绿且人工确认合并前，不得将 PR 标为已合并或删除 head 分支。

---

## 2026-07-23 · AV-STAGE1-001 · 最终 head、合并与主线 CI 结果

### 身份

- PR：`#7`。
- 最终 PR head：`428573efc5e962757b146995fba1f0f47261f0c0`。
- 最终 PR workflow：`29973922619`，事件：`pull_request`。
- 合并提交：`521f9b924241ec258c75b2ecc893bdaa3be91abd`。
- 合并后主线 workflow：`29977016358`，事件：`push`。

### 最终 PR head

最终文档 head 的 10 项检查全部成功：

1. 静态检查与 600 节点收集。
2. Python 3.11 全量测试。
3. Python 3.12 的 `smoke`、`p0-cli-lock`、`artifacts-runtime-security`、
   `semantics-evidence-review` 和 `remaining` 五个分片。
4. Windows 专项与 wheel smoke。
5. POSIX 临时目录专项。
6. 构建并安装 wheel。

这关闭了上一条记录中的 `final-head-ci-required`，没有通过覆盖、删除或改写旧记录伪造连续
成功。

### 合并后主线

PR `#7` 已合并到 `main@521f9b9`。合并后的 push workflow `29977016358` 状态为
`success`，总耗时 `2m 29s`，同一 10 项任务全部成功。

主线本地复核结果：

```text
Stage 1 targeted: 59 passed
full collection: 600 tests collected
repository hygiene: passed
git diff --check: passed
```

### 裁决

`completed / merged-to-main / main-ci-passed`

Assurance Stage 1 已满足预注册合同、独立审阅、最终 PR head CI 和合并后主线 CI 的退出条件。
Stage 1 到此完成。

该结果仍只适用于独立数据合同与确定性充分性校验器。它不生成真实 Runtime Threat，不改变
Finish、Goal、`ready_to_commit` 或 Loop 的现有成功语义，也不构成生产数据库、操作系统
隔离或远程证明级安全声明。

### 下一步

下一阶段只选择数据库 Migration 做独立纵向实验。任何候选必须继续默认关闭，先关闭危险与
安全双生案例、输出路径和确定性 oracle 的审查问题，再讨论是否形成 Stage 2 的第一份公开
证据；不得直接把 SQLite 个案扩展为通用数据库安全结论。

---

## 2026-07-23 · AV-STAGE2-001 · SQLite migration 双生实验本地结果

### 验证问题

在不把数据库能力接入 Vega Runtime 的前提下，一个受控 SQLite 实验能否同时证明：

1. 已有行的表新增无默认值 `NOT NULL` 列会被注册的危险 detector 拒绝；
2. 绕过 detector 后，SQLite 实际执行失败，且 schema/data 保持基线；
3. expand-only 的可空列安全双生可通过最小 OldApp/NewApp × OldSchema/NewSchema 兼容矩阵；
4. 安全 wrapper 的第二次执行不会重复执行 DDL。

详细范围、SQL、重放命令和结论上限见
[`docs/ASSURANCE-STAGE2-SQLITE-EXPERIMENT.md`](../docs/ASSURANCE-STAGE2-SQLITE-EXPERIMENT.md)。

### Baseline 与预注册边界

- Git 基线：`origin/main@521f9b924241ec258c75b2ecc893bdaa3be91abd`。
- 候选分支：`refactor/lean-core`；该次实验与核心隔离改动同一候选工作树进行，尚未形成可合并
  结论。
- 平台：Windows / Python `3.14.3` / SQLite `3.50.4`。
- 实验脚本和双生测试在实际 artifact 运行前已固定为
  `scripts/run_assurance_stage2_sqlite_experiment.py` 与
  `tests/test_assurance_stage2_sqlite_experiment.py`。
- 详细文字预注册文档在同一候选工作树中补充，因此本条只能作为 `local candidate`，不能替代
  事前冻结、跨引擎或生产规模实验。

### 非目标

- 不新增 `vega` CLI、默认状态、默认 `runs/` artifact 或成功条件。
- 不接入 Finish、Goal、Loop 或 `AdequacyResult`。
- 不验证 PostgreSQL/MySQL 锁语义、在线索引、真实发布编排、真实应用代码或生产数据规模。
- 不验证 DML/backfill、恢复、并发写入、复制延迟、监控或 staged rollout。

### 过程

1. 在 `.local-validation/assurance-stage2-sqlite-20260723-142449/` 创建两个 SQLite 文件。
2. 对危险 SQL `ALTER TABLE customer ADD COLUMN external_id TEXT NOT NULL`：
   - 先运行有限语法 detector；
   - 再在含两行数据的表中实际执行；
   - 比较失败前后的 schema 与旧应用可读数据。
3. 对安全 SQL `ALTER TABLE customer ADD COLUMN external_id TEXT`：
   - 运行 detector；
   - 顺序执行四格兼容矩阵；
   - 通过 schema 检查包装 migration，第二次执行必须为 `already_present`。
4. 运行同一脚本的两条 pytest 回归，并确认输出目录越界时 fail-closed。

### 实际观察

- 危险双生：
  - detector 输出：
    `T-DB-MIG-COMPAT:add_column_not_null_without_default:existing_rows_would_reject_migration`；
  - SQLite 返回 `OperationalError: Cannot add a NOT NULL column with default value NULL`；
  - 失败后 schema 与两条基线数据保持一致；
  - 个案判定：`reject`。
- 安全双生：
  - detector 没有输出危险项；
  - `OldApp/OldSchema`、`NewApp/OldSchema`、`OldApp/NewSchema`、
    `NewApp/NewSchema` 均为通过；
  - 首次 wrapper 结果为 `applied`，第二次为 `already_present`；
  - 新列保持可空，`id` 与 `display_name` 基线不变；
  - 个案判定：`passed-local`。
- 总体实验结论：`continue-experiment`。
- 定向回归：`2 passed in 13.89s`。

### 证据

- 机器事实：`.local-validation/assurance-stage2-sqlite-20260723-142449/result.json`
  - SHA-256：`D6CE0892952DEC6C77855CCEFFD16BFBDE5D59A72F0FDCEA7B84406A39B62067`
- 人类报告：`.local-validation/assurance-stage2-sqlite-20260723-142449/report.md`
  - SHA-256：`5ED4631A0B67AB083CB033E93F2D44A0E3DED3D3AB08134DC82492C211882D3B`
- `.local-validation/` 已被 `.gitignore` 排除；本条只公开可重放步骤、摘要和哈希，不提交
  本机 SQLite 文件。

### 裁决与下一步

`confirmed-local / continue-experiment / do-not-integrate`

本实验确认了一个真实执行的 SQLite migration 兼容性危险控制和对应安全双生的最低敏感性，
但没有获得任何生产数据库、锁影响、恢复、数据规模或实际发布顺序的证据。不得把
`passed-local` 或 `continue-experiment` 解释为 `sufficient_for_merge`、生产安全或
Runtime 成功。

下一步只能在独立实验中增加一个明确、可重放的数据库引擎/发布兼容场景；在该场景有危险控制、
负向敏感性、限制说明和跨平台复核前，不扩大 Threat Family，也不接入默认产品路径。

---

## 2026-07-23 · AV-STAGE2-001 · review correction candidate

### 原始审查问题

Draft PR `#8` 的只读审查确认三个阻塞问题：

1. 架构增长门禁可被 package shim、`from . import experimental` 和
   `from vega import experimental` 绕过。
2. SQLite 安全双生只比较行 ID，错误 `external_id`、`schema_mode` 或实际行内容破坏仍可
   得到 `passed-local`。
3. `.local-validation/` 本身为 symlink 或 Windows junction 时，输出可被重定向到当前工作
   目录外。

### 红灯

```text
architecture targeted: 15 failed, 24 passed
stage2 targeted: 5 failed, 1 passed
```

失败均位于上述新负向断言，不是 fixture、SQLite 初始化或目录链接创建失败。

### 修复候选

- 已移除内部模块改按模块名检查，同名 `.py`、package 目录和链接路径均被拒绝。
- Core → Experimental 检查解析 `ImportFrom.names`，补齐两种 package 导入变体。
- SQLite artifact schema 升为 `2`；四格矩阵按完整有序行内容校验，最终
  `data_invariant` 绑定 `display_name`、`external_id` 和 `schema_mode` 并参与结论。
- 输出目录使用词法 `.local-validation/` 根，在目录创建前后拒绝 symlink、junction 和
  reparse point。
- wheel/sdist smoke 扩展为检查全部 13 个已移除旧模块路径。

### 定向结果

```text
architecture targeted: 39 passed
stage2 targeted: 6 passed
full collection: 646 tests collected
targeted compileall: passed
targeted Ruff: passed
```

### 裁决

`review-findings-closed-targeted / full-suite-and-pr-ci-required / do-not-merge`

这只证明三个已知复现已由确定性回归关闭。完整本地分片、仓库卫生、架构增量门禁、Windows、
POSIX、Python 3.11/3.12、wheel/sdist 和最新 PR head CI 尚未完成；也不证明恶意并发路径替换、
其他数据库引擎或生产 migration 安全。

---

## 2026-07-23 · AV-STAGE2-001 · review correction local final result

### 追加红灯

首次定向修复后，独立只读复核又注册并复现：

1. `import vega.experimental_tools` 与 `import vega.experimentalish` 被命名空间前缀误报：
   `2 failed`。
2. 数据库 `external_id` 被破坏、NewApp 同时掩盖该值时，安全双生仍可能误判：
   `1 failed`。

两个红灯均在对应最小修复后转绿。SQLite 最终 oracle 现在通过独立 SQL 快照校验持久化
`external_id`，不再只信任被测 NewApp 读取层。

### 完整本地结果

```text
full collection: 651 tests collected
full sharded result: 650 passed, 1 skipped, 0 failed, 0 errors
architecture targeted: 42 passed
stage2 targeted: 8 passed
compileall: passed
Ruff: passed
architecture growth: passed, C901 48->46, Python modules 47->54
repository hygiene: passed
CI YAML parse: passed
git diff --check: passed
```

唯一跳过为 Windows 本地不执行 POSIX shell 变量展开专项；Linux PR CI 必须真实通过。由于本机
未安装 `pytest-timeout`，测试按文件或完整 node id 集合分片，外层 60 秒超时；所有超时尝试
均未计入最终汇总。

### 裁决

`passed-local / pr-ci-required / do-not-merge`

三个原始 finding 和后续同根组合问题已由负向回归关闭，完整本地节点与静态门禁通过。该结果
仍不证明恶意并发路径替换、其他数据库引擎、生产 migration 或操作系统级隔离安全；在最新
PR head 的 Python 3.11/3.12、Windows、POSIX、wheel/sdist 检查全部成功并完成 post-CI
只读复核前，不得转 Ready 或合并。

---

## 2026-07-23 · AV-STAGE2-001 · final PR/main closure

### PR head 门禁

- PR：`#8`。
- 最终 head：`f59a71d7dd6898bc6fb240bdec3a19d0cb8727df`。
- PR workflow：`30014062338`。
- 结果：`success`，总耗时 `2m 50s`。
- 同一 run 完成：
  - 静态检查与节点收集；
  - Python 3.11 全量测试；
  - Python 3.12 五个分片；
  - Windows 专项与 wheel smoke；
  - POSIX 临时目录专项；
  - wheel 构建与安装。

post-CI 定向复核结果：

```text
architecture + Stage 2 targeted: 50 passed
compileall: passed
Ruff: passed
architecture growth: passed, C901 48->46, Python modules 47->54
repository hygiene: passed
git diff --check: passed
```

三路独立只读复核没有发现新的阻塞问题。架构门禁、SQLite 独立 SQL oracle 和
`.local-validation/` 链接/reparse 防护的已知 findings 均已关闭。

### 合并后主线

- PR `#8` 已 squash 合并。
- 主线提交：`main@0280b9f6df0205261a489e1fd67c6b574684cb64`。
- 合并后 push workflow：`30016175900`。
- 结果：`success`，总耗时 `2m 39s`。
- 同一 10 项任务全部成功。

### 裁决

`completed-first-public-evidence / continue-experiment / do-not-integrate`

`AV-STAGE2-001` 已完成预注册问题、真实 SQLite 执行、负向敏感性修复、完整本地测试、
最终 PR head CI、post-CI 只读复核和合并后主线 CI。它现在可以作为 Stage 2 的第一份公开
实验代码与证据。

该裁决只关闭 `AV-STAGE2-001` 的工程与证据门禁，不把 SQLite 个案提升为生产数据库安全、
跨数据库引擎安全或 Runtime 成功，也不改变 `continue-experiment` 的结论上限。

---

## 2026-07-23 · AV-STAGE2-002 · expand/backfill/contract preregistration

### 基线

- Git 基线：`main@0280b9f6df0205261a489e1fd67c6b574684cb64`。
- 工作分支：`experiment/assurance-stage2-backfill`。
- Threat：`T-DB-MIG-COMPAT`。
- 引擎：Python 标准库 SQLite。
- 详细预注册合同：
  [`docs/ASSURANCE-STAGE2-EXPAND-CONTRACT-EXPERIMENT.md`](../docs/ASSURANCE-STAGE2-EXPAND-CONTRACT-EXPERIMENT.md)。

### 冻结问题

1. 固定两行已有数据时，`expand -> contract -> backfill` 的危险顺序能否在执行前被 detector
   标记，并在绕过 detector 后由 SQLite 实际拒绝？
2. contract 失败后，transaction rollback 能否保持 expanded schema 和完整数据，同时不残留
   临时表？
3. `expand -> bounded fixture backfill -> contract` 能否通过 OldApp/NewApp 与
   Old/Expanded/Backfilled/Contract schema 的兼容矩阵？
4. 数据准备和 contract wrapper 能否在本实验固定 fixture 上重复执行而不产生额外修改？
5. 关闭原连接后重新打开数据库的独立 SQL oracle，能否精确验证 `NOT NULL UNIQUE`、完整行
   映射和零个 `NULL`？
6. 部分数据准备、错误映射、读取层掩盖、错误约束和临时表残留是否都会使结论降为
   `inconclusive`？

### 冻结顺序

危险控制：

```text
old -> expand -> contract -> backfill
```

安全双生：

```text
old -> expand -> bounded fixture backfill -> contract
```

bounded fixture data preparation 只更新：

```text
id=1 -> external_id=cust-0001
id=2 -> external_id=cust-0002
```

### 非目标

- 不实现通用 backfill runner、租户 scope、row budget、分批 checkpoint 或恢复框架。
- 不覆盖并发写入、复制延迟、真实应用部署、生产数据规模或在线 DDL。
- 不注册默认 CLI，不写默认 `runs/`，不接 Finish、Goal、Loop 或 `AdequacyResult`。
- 不把实验结论解释为 `sufficient_for_merge`、`ready_to_commit` 或生产 migration 安全。

### Artifact 与停止条件

- artifact schema：`2`。
- 输出只能位于 `.local-validation/`。
- 机器事实源：`result.json`。
- 人类报告：`report.md`。
- 数据库：`dangerous.sqlite`、`safe.sqlite`。
- 所有注册负向控制、定向测试、完整测试、静态检查和跨平台 CI 必须明确通过。

即使全部通过，结论仍不得超过：

```text
overall_decision = continue-experiment
evidence_adequacy = requires_staged_rollout
runtime_integration = disabled
```

### 当前裁决

`preregistered / not-run / do-not-integrate`

本条在实现脚本、测试和本地 artifact 运行前追加。后续失败、修正和结果只能继续追加，不能
改写本预注册合同。

---

## 2026-07-23 · AV-STAGE2-002 · local candidate result

### 基线与范围

- Git 基线：`main@0280b9f6df0205261a489e1fd67c6b574684cb64`。
- 工作分支：`experiment/assurance-stage2-backfill`。
- 预注册提交：`0eb8073`。
- 实现仍是 Python 标准库 SQLite 独立脚本；没有注册 `vega` CLI，没有接入 Runtime、
  Finish、Goal、Loop 或 `AdequacyResult`。

### 红灯、无效尝试与审查修正

预注册后先执行了目标测试红灯，再实现双生实验。后续两次本地 artifact 暴露结论层级问题：

1. `.local-validation/assurance-stage2-expand-contract-20260723-232008/` 在外部测试和 CI
   未关闭时，错误地把总体结论提升为 `continue-experiment / requires_staged_rollout`，
   且没有显式记录决策范围和外部门禁状态。
2. `.local-validation/assurance-stage2-expand-contract-20260723-234037/` 补充了
   `decision_scope` 和 `external_quality_gates`，但仍由单次脚本自行提升总体结论。

这两个目录均早于最终修正，不具备证据资格；不记录其 SHA-256，也不用于本条裁决。最终实现
把“实验执行候选”和“组合门禁裁决”分开：

- artifact 固定为 `overall_decision=inconclusive`；
- 内部候选单独记录为 `candidate_decision=continue-experiment`；
- `evidence_adequacy=insufficient`；
- `external_quality_gates.status=not_evaluated`；
- 独立脚本即使完整写出 artifact，也返回退出码 `1`，直到外部门禁由追加式证据关闭。

只读审查还发现 `main()` 入口的退出码、控制台脱敏和相对 artifact 路径没有被直接测试。
该合同已并入现有首个 Stage 2 测试，测试节点总数不变；目标文件最终为 `13 passed`。

第一次全量分片使用过长的 Windows `basetemp`，触发 `WinError 206` 和衍生的
`RunMutationLockError`。该轮属于测试环境路径失效，全部作废。第二轮改用数字短路径重新
执行全部收集节点，不把首轮结果计为产品失败或通过。

### 最终实验事实

最终本地 artifact：

- `.local-validation/assurance-stage2-expand-contract-20260723-final/result.json`
  - SHA-256：
    `6AA739107A360035AD34DB412D9FEC0B3CEFFFF842F0AE96063296F660DBB35E`
- `.local-validation/assurance-stage2-expand-contract-20260723-final/report.md`
  - SHA-256：
    `42843CF1CD39A58A28C82157BC888F750621FA405EDC4CCDC3992A94795F2DDD`

结构化结果：

```text
overall_decision: inconclusive
candidate_decision: continue-experiment
evidence_adequacy: insufficient
runtime_integration: disabled
external_quality_gates.status: not_evaluated
dangerous_twin.decision: reject
safe_twin.decision: candidate-passed-local
script exit code: 1
```

危险顺序 `expand -> contract -> backfill` 先被 detector 标记，绕过 detector 后 SQLite
真实拒绝包含 `NULL` 的 contract，transaction rollback 后 expanded schema 和两行数据保持
不变，且没有残留临时表。

安全顺序 `expand -> bounded fixture backfill -> contract` 只更新冻结的 `id=1/2`。首次
数据准备更新两行，第二次更新零行；contract 第二次返回 `already_contracted`；完整兼容矩阵
和独立 SQL oracle 均通过。

`.local-validation/` 不提交。其他机器重放时允许时间戳和哈希变化，但必须重新核对结构化字段，
不能复制本机结论冒充 live evidence。

### 完整本地测试

机器可读汇总：

- `.tmp/validation/stage2-adaptive-v2/summary.json`
- SHA-256：
  `50EAFEFCEB3CB38DEA17440C434B8D21E983AC33C7180FC2729150A0DB3D00E3`

```text
collected: 664
passed: 663
skipped: 1
failed: 0
timed_out: 0
```

唯一跳过是 Windows 本地不执行的 POSIX shell 变量展开专项，Linux PR CI 必须真实执行。
本地分片使用互不相同的短 `basetemp`，每个分片由 58 秒外层预算控制；超时的大分片只用于
递归拆分，不进入最终 leaves 或通过计数。

最终收尾门禁：

```text
compileall src scripts: passed
Ruff src tests scripts: passed
architecture growth: passed, C901 46->46, Python modules 54->54
repository hygiene: passed
final collection: 664 tests collected
git diff check: passed
```

### 当前裁决

`candidate-passed-local / pr-ci-required / do-not-integrate`

本地危险/安全双生、负向敏感性、入口合同和完整 664 节点已经关闭。当前仍缺最新 PR head
上的 Python 3.11/3.12、Windows、POSIX、wheel/sdist 和 package CI，以及 CI 后只读复核。
这些门禁全部成功前，不得把本实验接入 Runtime，不得把裁决提升为
`continue-experiment / requires_staged_rollout`，也不得自动合并。

---

## 2026-07-24 · AV-STAGE2-002 · independent review correction local result

### 审查红灯

对 `44ab56f` 做独立审查时新增三个负向回归，修正前结果为：

```text
3 failed, 13 passed
```

三个红灯分别证明：

1. 错误 backfill 与共享 `_stored_rows` 掩盖同时存在时，NewApp、detector 和旧 oracle 会读取
   同一份伪装结果；实际 SQLite 行为 `wrong-0001/wrong-0002` 时仍可能得到
   `candidate-passed-local`。
2. contract 表删除 `id PRIMARY KEY` 与 `display_name NOT NULL` 后，旧完整性判定仍会返回
   `already_contracted`，旧 oracle 也会通过。
3. `external_id_not_unique` 检查位于精确映射比较之后，重复值只能得到
   `external_id_mapping_mismatch`，专用 detector 分支不可达。

### 最小修复

- oracle 在关闭原连接后重新打开 SQLite，并独立执行列、行、计数、表和索引 SQL；不再复用
  NewApp、detector 或 contract 的读取 helper。
- contract 完整性与 oracle 都核对冻结的完整列定义，保留 `id PRIMARY KEY`、
  `display_name TEXT NOT NULL` 和 `external_id TEXT NOT NULL UNIQUE`。
- detector 先检查 `NULL` 与唯一性，再检查精确行映射。
- 新增三个负向回归；完整收集合同由 `664` 更新为 `667`。

### 当前本地结果

```text
Stage 2 targeted: 16 passed
compileall src scripts: passed
Ruff src tests scripts: passed
architecture growth: passed, C901 46->46, Python modules 54->54
repository hygiene: passed
full collection: 667 tests collected
git diff check: passed
```

本轮没有得到可采信的当前 head 完整 pytest 汇总：

- 单进程全量运行耗尽 20 分钟外层预算；
- 直接 smoke 和后续自适应分片运行时，同机另有 pytest 进程并行执行；
- 第一版自适应脚本未预建短 `basetemp` 父目录，产生环境性 setup error，整轮作废；
- 第二版只完成部分节点并记录多个 60 秒 timeout，随后主动终止。

上述尝试均不计为通过，也不直接计为产品失败。2026-07-23 的
`663 passed, 1 skipped` 只适用于修正前旧 head，不能复用来关闭当前 head。

### 当前裁决

`review-findings-fixed-targeted / pr-ci-required / do-not-integrate`

三个已知 evidence false-positive 已由定向回归关闭，静态门禁与 667 节点收集合同通过。完整
Python 3.11/3.12、Windows、POSIX、wheel/sdist 和 package smoke 必须由同一最新 Draft PR
head 的隔离 CI 关闭；CI 全绿并完成 post-CI 只读复核前，不得合并或接入 Runtime。

### 修正后 artifact 重放

当前工作区重新执行：

```text
.local-validation/assurance-stage2-expand-contract-20260724-review-final/
```

结果保持 fail-closed：

```text
overall_decision: inconclusive
candidate_decision: continue-experiment
evidence_adequacy: insufficient
runtime_integration: disabled
external_quality_gates.status: not_evaluated
dangerous_twin.decision: reject
safe_twin.decision: candidate-passed-local
safe_twin.oracle.passed: true
safe_twin.oracle.schema_columns_passed: true
script exit code: 1
```

SHA-256：

```text
result.json:
29BC11A9E060ED547665050E0F6B3123134A0A25F96C88D3EE51F4AD1BA20442

report.md:
90D09243024EA7BE00AD86C4FFB640274838FED12E4F92C92B3B3E0FA16A8B05
```

该目录仍是本地重放 artifact，不提交；哈希只绑定本次输出，不能替代 PR CI。

---

## 2026-07-24 · AV-STAGE2-002 · PR #10 first-head static failure and correction

### 失败事实

- Draft PR：`#10`。
- 失败 head：`237cb2967b73743194fa338775bd37b3945a7f0f`。
- workflow：`30066400503`。
- 结果：`failure`。
- 失败任务：`静态检查与节点收集`。
- 失败步骤：`编译与静态检查`。
- 其余依赖该任务的测试与打包任务全部被跳过。

该 run 必须保留为失败，不能用后续重跑覆盖描述。

### 根因复现

项目开发依赖只声明 `ruff>=0.5`，`pyproject.toml` 没有显式冻结 lint 规则集合。PR runner 安装
Ruff `0.16.0` 后，当前配置实际启用了比本机 Ruff `0.15.20` 更广的规则集合，立即报告大量
历史代码问题；这不是 Stage 2 新脚本单独引入的 193 个回归，也不能通过一次性批量格式化扩大
本 PR 范围。

本机使用 Python 3.12 与 Ruff `0.16.0` 复现同一行为后，采用最小根因修复：

```toml
[tool.ruff.lint]
select = ["E4", "E7", "E9", "F"]
```

该配置把仓库原先依赖的 Ruff 默认规则转成显式合同；未来增加规则必须单独评估。新增
`test_ruff_lint_selection_is_explicit_and_stable`，防止再次退回隐式版本默认值。

### 修正后本地结果

```text
Python 3.12 + Ruff 0.16.0 exact CI command: passed
local Ruff 0.15.20: passed
repository hygiene + Stage 2 targeted: 26 passed
full collection: 668 tests collected
compileall src scripts: passed
git diff check: passed
```

### 当前裁决

`first-pr-head-failed / ruff-contract-fixed-local / latest-head-ci-required / do-not-integrate`

下一次推送必须使用同一 Draft PR `#10`。只有新 head 的静态任务和全部下游跨平台任务都成功，
才可继续 post-CI 复核；不得新建微型修复分支，也不得自动合并。

---

## 2026-07-24 · AV-STAGE2-002 · PR #10 code-head CI and post-CI review

### PR head 门禁

- PR：`#10`。
- head：`6e809caa5511385c6852da2d404aa70d8bcc6f17`。
- workflow：`30066936448`。
- event：`pull_request`。
- 结果：`success`。
- 运行时间：2026-07-24 12:29 至 12:32（Asia/Shanghai）。

同一 run 的 10 项任务全部成功：

1. 静态检查与 `668` 节点收集。
2. Python 3.11 全量测试。
3. Python 3.12 `smoke` 分片。
4. Python 3.12 `p0-cli-lock` 分片。
5. Python 3.12 `artifacts-runtime-security` 分片。
6. Python 3.12 `semantics-evidence-review` 分片。
7. Python 3.12 `remaining` 分片。
8. Windows 专项与 wheel smoke。
9. POSIX 临时目录专项。
10. wheel/sdist 构建、安装和 package smoke。

POSIX 专项的 job conclusion 为 `success`，不是 skip。首个失败 workflow `30066400503`
仍保留为失败记录，不能被本次成功覆盖。

### post-CI 只读复核

复核绑定同一 `6e809ca`：

- PR head、远端分支和本地 `HEAD` 一致；
- 新实验没有进入 `src/`、默认 Loop 或 `vega` CLI；
- artifact 仍固定为
  `inconclusive / insufficient / runtime_integration=disabled / external gates not_evaluated`，
  脚本退出码仍为 `1`；
- `eval/assurance-validation.md` 相对主线只有新增行，没有删除历史记录；
- PR 文件集合不含 `.tmp/`、`.local-validation/`、SQLite、`runs/`、`.env`、docx 或本机路径；
- 仓库卫生、架构增量和 diff check 再次通过；
- 没有发现新的阻塞 finding。

### 代码 head 裁决

`pr-ci-passed / post-ci-review-clean / continue-experiment / requires_staged_rollout / do-not-integrate`

该裁决只关闭 `AV-STAGE2-002` 代码 head 的实验与工程门禁，不证明 PostgreSQL/MySQL、生产
规模、在线 DDL、并发写入、可恢复 backfill 或 Runtime 成功语义。本文与接力文档的收口提交
只修改文档；推送后仍必须确认新的最新 PR head CI 全绿，PR 保持 Draft 且不得自动合并。

---

## 2026-07-24 · AV-STAGE2-002 · PR #10 merge and main CI verification

### 合并事实

- PR：`#10`。
- 最终 head：`625acf6fdce718fa2741f9a45ecfc9798e72d858`。
- 合并方式：`squash`。
- 合并提交：`main@1922e5f0e821a22b2ea6a3c0c9f15686e5374dc7`。
- 实验分支保留，未删除。

合并前重新确认 PR 为非 Draft、`MERGEABLE`，base 仍为 `main@0280b9f`，最终 head 的
10 项 PR CI 全部成功，且 `git diff --check` 无问题。

### 合并后主线 CI

- workflow：`30072320027`。
- event：`push`。
- commit：`1922e5f0e821a22b2ea6a3c0c9f15686e5374dc7`。
- 结果：`success`。

同一 run 的 10 项任务全部成功：

1. 静态检查与 `668` 节点收集。
2. Python 3.11 全量测试。
3. Python 3.12 `smoke` 分片。
4. Python 3.12 `p0-cli-lock` 分片。
5. Python 3.12 `artifacts-runtime-security` 分片。
6. Python 3.12 `semantics-evidence-review` 分片。
7. Python 3.12 `remaining` 分片。
8. Windows 专项与 wheel smoke。
9. POSIX 临时目录专项。
10. wheel/sdist 构建、安装和 package smoke。

没有 pending、skip 或失败任务。该事实只证明同一合并提交通过仓库现有门禁，不替代生产环境
数据库演练。

### 合并后裁决

```text
pr-merged
main-ci-passed
continue-experiment
requires_staged_rollout
do-not-integrate
```

`do-not-integrate` 继续表示不得接入默认 Runtime、Finish、Goal 或成功语义；实验代码与证据
进入主线不等于生产 migration、通用 backfill、并发安全或自动部署能力成立。

---

## 2026-07-24 · AV-STAGE3-001 · test-first red and local implementation candidate

### 测试先行失败事实

在实现脚本不存在时，先新增 Stage 3 测试并运行：

```text
14 failed
exit code: 1
共同根因：scripts/run_assurance_stage3_dml_backfill_experiment.py 不存在
```

该红灯证明测试不是在实现完成后补写。后续又增加了 batch/checkpoint transaction 原子性、
checkpoint metadata mismatch 和 reconciliation tampering 负向控制；当前 Stage 3 文件共有
`20` 个测试节点。

### 实现边界

- 分支：`experiment/assurance-stage3-dml-backfill`。
- 实现提交：`c7122d3ce41407beeb59b2285b07ee910b6ea52e`。
- 新增独立脚本与测试，没有修改 `src/vega/`。
- 没有新增 `vega` CLI、默认 Loop、Runtime 状态、Finish、Goal 或成功条件。
- batch 行更新与 checkpoint 更新在同一 SQLite transaction。
- 首批提交后由独立子进程以退出码 `97` 硬退出，父进程重新打开数据库恢复。
- checkpoint 只用于诊断；恢复以数据库中的 `exact / pending / conflict` 事实为准。
- 独立 SQL oracle 不复用 detector、checkpoint 或应用读取 helper。

### 当前本地结果

```text
Stage 1 + Stage 2 + Stage 3 + repository/architecture targeted: 155 passed
Stage 3 targeted: 20 passed
full collection: 688 tests collected
compileall src + registered experiment scripts: passed
Ruff src + tests + registered experiment scripts: passed
Ruff C901 on Stage 3 script: passed
architecture growth: passed, C901 46->46, Python modules 54->54
repository hygiene: passed
git diff check: passed
```

clean-head artifact 绑定 `c7122d3`：

```text
overall_decision: inconclusive
candidate_decision: continue-experiment
evidence_adequacy: insufficient
runtime_integration: disabled
external_quality_gates.status: not_evaluated
safe_twin.decision: candidate-passed-local
safe_twin.evidence_bindings_valid: true
interruption.process_exit_code: 97
recovery.updated_ids: [102]
repeat.updated_ids: []
safe_twin.oracle.passed: true
script exit code: 1
```

本地目录：

```text
.local-validation/assurance-stage3-dml-backfill-20260724-handoff/
```

SHA-256：

```text
result.json:
2BB892547773B14D0D4917C55EF8DF0EC2F988BAAA9824FC3220D5A883BE34CA

report.md:
D871929E6E5F615627C9A48F322055C3645A66C1E9389440BAB82A94B3B6A053
```

该目录不提交；哈希只绑定本次 clean-head 重放。

### 未完成与失败尝试

- 单进程全量 pytest 超过 30 分钟外层预算，只到约 `31%`，不计为通过；
- Windows 本地完整 semantics shard 在既有
  `test_loop_eval_rejects_superseded_terminal_without_state_binding` 上触发 `58s` timeout；
- 同一节点独立重跑为 `1 passed in 48.65s`，只能说明该失败可能受本地共享环境时序影响，
  不能把完整 shard 改记为通过；
- Python 3.11/3.12、Windows、POSIX、wheel/sdist 与 package smoke 仍需最新 Draft PR head
  的隔离 CI。

### 当前裁决

```text
local-candidate
full-local-suite-not-established
draft-pr-ci-required
continue-experiment
requires_staged_rollout
do-not-integrate
do-not-merge-yet
```

该结果只证明固定 SQLite 个案的本地候选成立，不证明生产数据库、并发写入、真实流量、
PostgreSQL/MySQL、在线 DML 或通用 backfill runner 已成立。

---

## 2026-07-24 · AV-STAGE3-001 · independent review correction and PR CI

### 独立审查发现

三个只读审查视角分别检查实现语义、安全边界和测试/CI，确认 Stage 3 仍未进入
`src/vega/`、CLI、Runtime、Finish、Goal 或成功语义，同时发现三个需要在合并前修正的问题：

1. SQL scope detector 使用子字符串判断 `id = ?`，会把 `tenant_id = ?` 的后缀误判为
   独立目标 ID 约束；
2. evidence 虽声明 `dangerous-oracle.json`、`safe-oracle.json` 等 artifact，但绑定检查没有
   重新校验全部声明文件的 SHA-256；
3. policy 文件缺失时使用 `unavailable` sentinel，snapshot 可能与同一 sentinel 自洽。

### 测试先行红灯与修复

先新增三个回归测试，得到：

```text
3 failed
missing target scope: detector 未在写入前给出 target_scope_missing
oracle artifact tampering: evidence binding 仍返回 true
policy unavailable sentinel: snapshot 仍返回 trusted
```

随后做最小修复：

- 使用带列名边界的正则分别识别 `tenant_id = ?` 与独立 `id = ?`；
- 每条 evidence 新增 `artifact_hashes`，要求它与 `artifacts` 完全同集合且每个文件的
  64 位 SHA-256 与磁盘内容一致；
- policy 缺失或不可读时返回 `None`，trusted snapshot 要求加载时和运行时 policy 哈希均为
  相同的真实 64 位 SHA-256。

同三个节点修复后：

```text
3 passed
```

### 本地验证

Stage 3 并行分片首次尝试造成共享 Windows 资源争用：一个分片 `5 passed`，其余三个超过
60 秒，超时分片不计为通过或失败。随后使用独立 basetemp 串行执行全部节点：

```text
6 passed
6 passed
6 passed
5 passed
Stage 3 total: 23 passed
```

跨阶段与仓库门禁分片：

```text
Stage 1 contract: 59 passed
Stage 2 experiments: 24 passed
Stage 3 experiment: 23 passed
architecture + repository hygiene tests: 52 passed
targeted total: 158 passed
full collection: 691 tests collected
```

其他门禁：

```text
compileall src + registered experiment scripts: passed
Ruff src + tests + registered experiment scripts: passed
Ruff C901 on Stage 3 script: passed
architecture growth: passed, C901 46->46, Python modules 54->54
repository hygiene: passed
git diff check: passed
path/secret scan: passed
eval relative to origin/main remains append-only
src/vega diff remains empty
```

### clean-head artifact

artifact 绑定审查修复提交：

```text
snapshot.head: 25a7efc0058de62d2fc665c99b501f890ff5d3e9
script exit code: 1
overall_decision: inconclusive
candidate_decision: continue-experiment
evidence_adequacy: insufficient
runtime_integration: disabled
dangerous_twin.decision: reject
safe_twin.decision: candidate-passed-local
safe_twin.evidence_bindings_valid: true
interruption.process_exit_code: 97
recovery.updated_ids: [102]
repeat.updated_ids: []
safe_twin.oracle.passed: true
verified declared artifact bindings: 10
```

SHA-256：

```text
result.json:
883C133F0790AD7F3793F6F774A796D95CF4077CF5A463F5FAFE9A0E733D0EDA

report.md:
67BABCFE2836F3E3B900E4E480F899852ABA90047C0AA56C1149642DEA55B630
```

artifact 文本扫描未发现本机绝对路径、API key 或 Authorization header；目录继续由
`.gitignore` 排除。

### PR CI

修复提交 `25a7efc` 对应 Draft PR `#13` workflow `30099248716`：

```text
10/10 jobs success
static checks and 691-node collection: success
Python 3.11 full suite: success
Python 3.12 five shards: success
Windows + wheel smoke: success
POSIX temp-dir checks: success
wheel/sdist build and package smoke: success
```

### 当前裁决

```text
review-findings-fixed
implementation-head-pr-ci-passed
latest-head-ci-must-be-green-at-merge
continue-experiment
requires_staged_rollout
do-not-integrate
manual-merge-candidate-only
```

该裁决只允许继续把 Stage 3 作为冻结实验候选审查。人工合并时必须读取 PR 最新 head 的
实时 10 项 CI；通过后只在 PR 描述或评论记录最终 SHA，不再为追逐新 SHA 追加仓库文档提交。
它不自动接入 Runtime、不启动 Stage 4，也不扩大为通用数据库安全声明。
