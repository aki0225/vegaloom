# Core Real-World Pilot v1 接力说明

> 日期：2026-07-30
>
> 分支：`main`
>
> 远端续做入口：`origin/main`

## 2026-07-30 接力

### 本轮完成

本轮只整理主线既有可信性补丁，没有开始 Plan-first、Important Diff、Claude Code Runner，
也没有启动 CRWP-V1 Worker。

- Brief 与 Reflect 在同一次读取事务中复用固定 Git revision 和 ProjectKnowledge，避免
  多次 `rev-parse HEAD` 之间发生上下文漂移。
- 已解析 revision 绑定仓库根和进程内 proof，拒绝跨仓库复用及手工伪造。
- tracked project context 只接受由受信 loader 生成、且绑定相同 revision 的预加载知识；
  来源证明使用 Pydantic private attributes，不进入公开 JSON 或 Markdown。
- Gate 复用经过 freshness 校验的 Reflect `changed_files`，不再回退到新的
  `git diff --name-only` 事实源。
- Gate 与 Reflect 仍分别执行 staged/unstaged `git diff --check`，避免 `MM` 文件的净差异
  抵消 index 中问题。
- staged rename 的源路径继续参与 `risk.required_reviews`，不能通过移出高风险目录绕过披露。
- Git 读取使用空 `core.fsmonitor=`，兼容会把字符串 `false` 解释成 hook 路径的旧版 Git。
- 删除 Reflect 重复的 status/stat/name-only 读取；完整 diff、变更文件和指纹继续以 review
  workspace snapshot 为事实源。

架构增长门禁已恢复：

```text
架构增长门禁通过：C901 38->38，Python 模块 76->76
```

### 已取得终态的验证

```text
python -m compileall -q src scripts/check_repository_hygiene.py scripts/check_architecture_growth.py
python scripts/check_repository_hygiene.py --base-ref origin/main
python scripts/check_architecture_growth.py --base-ref origin/main
ruff check src tests scripts/check_repository_hygiene.py scripts/check_architecture_growth.py
git diff --check
```

以上命令均通过。

受影响测试共取得 `171 passed` 的明确终态：

```text
tests/test_security_evidence.py + tests/test_context_boundaries.py：80 passed
tests/test_evidence_freshness.py：27 passed
tests/test_required_risk_reviews.py：10 passed
tests/test_p0_regressions.py：54 passed
```

其中 `tests/test_evidence_freshness.py` 用时约 12 分钟，
`tests/test_p0_regressions.py` 用时约 17 分钟。测试较慢来自既有 Goal、Scope、Finish
重算链路，不是本轮命令失联。

### 未完成验证

完整 `python -m pytest -q --durations=20` 已启动，但在取得最终汇总前因会话中断停止。
残留 pytest 进程已经明确终止，因此：

- 本轮不能记录 full suite 通过；
- 不能沿用此前 `888 passed / 8 skipped` 作为当前补丁的新证据；
- 晚间应重新从头执行完整 pytest，并等待正常退出码和最终计数。

### 晚间固定顺序

```powershell
git fetch origin
git switch main
git pull --ff-only origin main
git status -sb
Get-Content docs/CORE-REAL-WORLD-PILOT-V1-HANDOFF.md -TotalCount 120
python -m pytest -q --durations=20
```

完整 pytest 取得可信终态后：

1. 重跑本文上方的静态检查和架构门禁；
2. 按现有预注册只执行 CRWP-V1 Case 01/02；
3. Case 03 继续记录为 `eligibility-changed-before-run`，不得启动 Worker；
4. 不为 Pilot 新增 Runtime、状态或 Artifact；
5. Pilot 有一次合同允许的真实终态后，再实现 Loop 内部 Plan-first 与 Important Diff。

## 2026-07-29 接力

### 当前状态

- 本轮 oracle 修订基于主线可信执行提交：
  `8f91844`。
- `52a8b3d test: finalize CRWP-V1 oracle contracts` 已在本地提交三份 oracle、Control
  Amendment 与证据口径修正。本次接力提交推送后，远端 `main` 将包含该提交和本文件的更新。
- PR `#22` 已于 2026-07-28 合并，标题为
  `fix: bound ignored inventory and fail closed on timeout`。
- 主线随后新增可配置的 `risk.required_reviews` 必审高风险披露，并完成 Goal/Gate 防篡改、
  人工接管新鲜度和关键行号修复。当前快照已取得 `896 collected / 888 passed /
  8 skipped` 的全量 pytest 终态证据。
- 正式 CRWP-V1 Worker、Reviewer 和 Finish 仍然都没有启动。
- 三份 oracle 合同已经补齐，并由本文件所在提交共同冻结：
  - `scripts/pilot/crwp-v1/crwp-v1-01-timeout-oracle.py`
  - `scripts/pilot/crwp-v1/crwp-v1-02-sqlite-autoincrement-oracle.cjs`
  - `scripts/pilot/crwp-v1/crwp-v1-03-stub-filename-oracle.py`
- 三个项目内目标副本位于 `.tmp/crwp-v1/targets/`，都停在预注册的 upstream SHA，
  工作区干净且当前没有 `.vega.yaml`。
- `.local-validation/crwp-v1/control-evidence/` 保留本机基线和 oracle 原始输出，继续保持
  ignored，不进入 Git。
- 三份 oracle 已取得双次稳定 `exit=1` 证据；OpenStates 因 PR `#125` 固定停止为
  `eligibility-changed-before-run`。
- 本轮重新收集到 `896` 个 pytest node，但完整 pytest 在 60 秒外层上限内没有正常退出，
  因此不能记为新的全量通过。临时安全目录配置后，焦点集 JUnit 记录 `67 passed / 0 failed`，
  但 runner 同样未退出；该结果只说明测试主体已执行，不构成通过证据。
- 继续 CRWP 前必须先定位 pytest runner 未正常终止的原因，并取得带明确
  `passed / failed / skipped` 计数的正常退出终态。该问题不通过放宽超时、强制成功退出或忽略
  残留进程解决。

### Runtime 阻断已经解除

PR `#22` 已解决此前的两个 P0：

1. 大型 ignored 目录改为有界、可解释的 inventory，不再逐文件无界枚举依赖目录。
2. Git 或工作区读取 timeout 会进入结构化 fail-closed 终态，不再留下
   `running / current_step=worker` 的半完成现场。

合并后使用当前 `main` 对三个现有目标副本重新执行 `snapshot_workspace()`：

| Case | `capture_complete` | `ignored_manifest_complete` | `ignored_content_complete` | 秒 |
|---|---:|---:|---:|---:|
| Dormice | `true` | `true` | `false` | `0.265` |
| Sequelize | `true` | `true` | `false` | `0.242` |
| OpenStates | `true` | `true` | `false` | `0.209` |

`ignored_content_complete=false` 是当前 `metadata_bounded` 语义的一部分：依赖目录根和高价值
ignored 文件仍受控，但不能把结果描述成“完整扫描了依赖目录内的每个文件”。

### Oracle 修订进度

#### `CRWP-V1-01` Dormice

默认 timeout 分支已经补充以下断言：

- 请求路径为 `/execCommand`；
- Authorization 为 `Bearer oracle-token`；
- body 的 `name=oracle-timeout`；
- body 的 `command=echo oracle`；
- `timeoutSeconds=300`。

连续两次基线均稳定以 `1` 退出，输出 SHA-256 均为：

```text
4308870971d831b3f42883a42ea06057da9267782574b278a527f614172095e1
```

Git 暂存区中的 LF 字节 SHA-256（提交后的权威控制哈希）：

```text
a1a9152a9d96f0ac935f6c26baccba7ce4632453b388ba570ceb62731ae65b5f
```

当前 Windows 工作树因 CRLF checkout 得到
`5af44f7ec73328d373c791b4b042c5465ceeb754e2d2bf4f1aef45d17328cf76`；该值只用于
本机行尾诊断，不替代 Git index 控制哈希。

#### `CRWP-V1-02` Sequelize

行注释负对照已经只在 `describeTable('line_comment_pk')` 期间临时替换
`queryInterface.showConstraints()` 为空结果，并在 `finally` 恢复。真实
`PRAGMA TABLE_INFO`、AUTOINCREMENT metadata 路径和块注释正常路径均未替换。

两次独立基线均以 `1` 退出，输出 SHA-256 均为：

```text
0ee06abd2c7451e416e7514f49ada0f7ff1017a14c6a94dde27d6db35464626b
```

两次均确认行注释与块注释真实保留，且没有把普通主键误标为 auto-increment。当前失败只剩
目标 Issue 的正向语义。

Git 暂存区中的 LF 字节 SHA-256（提交后的权威控制哈希）：

```text
f784abc3518e12991f3f0b93628773adda1d68c9add4fe2a75d9e93b318e93d0
```

当前 Windows 工作树因 CRLF checkout 得到
`61c85e423715ad748b3adabef6b9cd9718b51fb1a4aa31b416b981885479c294`；该值只用于
本机行尾诊断。

#### `CRWP-V1-03` OpenStates

幂等用例已经补充：

- 首次运行真实生成一份 Division YAML 和一份 Jurisdiction YAML；
- 两份文件都位于预期输出目录并进入 manifest；
- Division `ocdid=ocd-division/country:us/state:wa`；
- Jurisdiction `ocdid=ocd-jurisdiction/country:us/state:wa/government`；
- 第二次运行全部 skip、writer 调用为 `0`、manifest 不变。

连续两次基线均稳定以 `1` 退出，输出 SHA-256 均为：

```text
e3cf488350744510cbe084c452e4dc4ce4a314724ea0d02910837084c6675f21
```

Git 暂存区中的 LF 字节 SHA-256（提交后的权威控制哈希）：

```text
79fd7227f44e1cf1aaff40d9f02b9d19a96834b14aa858de6c507503208ace0f
```

当前 Windows 工作树因 CRLF checkout 得到
`596145bfa64a84ae98bf63f6b96401e027d145a50aafc89410f0be68954f7e02`；该值只用于
本机行尾诊断。Case 本身另有资格变化。

### 新发现的资格变化

`openstates/jurisdictions` PR `#125`：

- 创建时间：`2026-07-28T07:59:23Z`；
- 状态：open、非 draft；
- 标题：`Fix ancestor stub filenames`；
- 明确 `Closes #122`；
- base：预注册冻结 SHA
  `6fbe7d6aed32c3b781490c8e4c5a737bdd6e4705`；
- head：`89e39fe09d952e92708a3a10f898a79650fd6a22`。

它晚于资格快照截止时间 `2026-07-28T04:07:36Z`，因此
`CRWP-V1-03` 已触发 `eligibility-changed-before-run`。下一会话不得按原 Issue 引导样本
直接启动 Worker。可选动作只有：

1. 保留原预注册并把该 Case 记录为运行前资格变化后停止；
2. 另建新的预注册版本，把它明确改为 controlled public replay。

不能在同一登记中静默改分类或把新 PR 内容加入模型输入。

### 下一会话固定顺序

1. 先定位 pytest runner 在测试主体完成后未正常终止的原因；保持当前 60 秒上限，不把
   JUnit 单独生成当作测试通过。
2. 取得一次完整 pytest 的正常退出和明确计数后，复核本文件中的 `896 collected / 888 passed /
   8 skipped` 历史基线是否仍适用。
3. OpenStates 固定记录为 `eligibility-changed-before-run`，不运行 Case 03。
4. 从冻结 SHA 重新准备 Case 01/02 目标副本，只提交 `.vega.yaml`，重新登记 prepared HEAD、tree 和
   config hash。
5. 重跑 config check、非 oracle 基线、双次 oracle、Issue/PR 资格和 Provider 探测。
6. 只有控制哈希、资格、workspace baseline、Provider 和可信 pytest 终态全部满足合同，才按固定顺序启动
   Dormice 和 Sequelize。每项最多两轮、总墙钟 30 分钟，不挑结果重跑。
7. 将真实结果只追加到 `eval/real-world-runs.md`，最后再执行 Vega 主仓库验证。

### 续做命令

如果 Git 报告目录所有者与当前用户不一致，不要改全局 `safe.directory`。先设置当前仓库
变量，再只对本次命令传入安全目录：

```powershell
$repoRoot = (Get-Location).Path
$repoRootPosix = $repoRoot.Replace("\", "/")
git -c "safe.directory=$repoRootPosix" status --short --branch
```

进入现场：

```powershell
$repoRoot = "<vegaloom-repository>"
Set-Location $repoRoot
$repoRootPosix = (Get-Location).Path.Replace("\", "/")
git -c "safe.directory=$repoRootPosix" fetch origin --prune
git -c "safe.directory=$repoRootPosix" pull --ff-only origin main
git -c "safe.directory=$repoRootPosix" status --short --branch
Get-Content docs/CORE-REAL-WORLD-PILOT-V1-HANDOFF.md
```

先检查 pytest runner 是否有遗留进程，再从测试终态诊断继续。正式 Worker、Reviewer 和 Finish
仍未启动；在可信 pytest 终态与 Case 01/02 全量 preflight 同时满足前，不得启动它们。

## 上一阶段结论（保留作历史）

CRWP-V1 的预注册、两次 Amendment、四个控制脚本和执行登记草案已经整理并可从远端接续。
正式 Worker **没有启动**，三个 Case 也没有产生 Reviewer 或 Finish 结果。

当前有两个独立阻断，不能只修其中一个：

1. 控制 oracle 登记审查发现三个合同覆盖缺口；
2. Vega Runtime 无法为带完整依赖目录的目标仓库建立可信 workspace baseline。

Runtime 阻断：

- Dormice 的 ignored 枚举超过 Runtime 固定的 30 秒 Git 读取上限；
- Sequelize 与 OpenStates 的 ignored 文件数超过 4096 个元数据条目预算；
- Dormice 的 `subprocess.TimeoutExpired` 还会直接向上传播，可能留下
  `running / current_step=worker` 的半完成 run。

控制 oracle 阻断：

- Dormice 默认 timeout 场景还要校验 Authorization、name 和 command；
- Sequelize 还要加入真实 SQL 行注释与块注释负对照；
- OpenStates 还要证明首次幂等运行真实生成预期 division/jurisdiction YAML 和 `ocdid`。

因此不能靠删除依赖、提高条目上限、沿用当前 oracle 哈希或放宽 fail-closed 语义继续实验。

## 本次接力提交包含

- `docs/CORE-REAL-WORLD-PILOT-V1-PREREGISTRATION.md`
- `docs/CORE-REAL-WORLD-PILOT-V1-RUN-REGISTRATION.md`
- `eval/real-world-runs.md` 中 2026-07-28 的追加记录
- `scripts/pilot/crwp-v1/` 下四个控制文件

`.local-validation/` 和 `.tmp/` 仍是本机 ignored 证据，不会推送到公开仓库。它们包含目标
副本、原始命令输出和本机路径信息，不能直接加入 Git。后续正式运行本来就必须从冻结 SHA
重新创建三个目标副本，因此晚间续做不依赖复制这些本机目录。

## 验证现状

已通过：

```powershell
python -m compileall src scripts/check_repository_hygiene.py
python scripts/check_repository_hygiene.py --base-ref origin/main
ruff check src tests scripts/check_repository_hygiene.py
ruff check scripts/pilot/crwp-v1
node --check scripts/pilot/crwp-v1/crwp-v1-02-sqlite-autoincrement-oracle.cjs
node --check scripts/pilot/crwp-v1/ignore-native-drivers.cjs
git diff --check
```

三个当前版本 oracle 都连续复现两次，非 oracle 命令全部通过；但 oracle 代码审查发现上述
三个覆盖缺口，因此双次结果只属于待修订控制基线，不属于最终冻结证据。

`python -m pytest` 尚无可信全量终态。本机为 Python 3.14.3 + pytest 9.0.2；仓库 CI 使用
Python 3.11/3.12。定向诊断确认 `tests/test_assurance_verification_semantics.py` 是重型集成
测试集合，共 18 个 node，单文件预计需要 40 至 50 分钟；此前 900 秒停止不是
cacheprovider 挂死。不要把部分节点结果写成全量通过。

## 晚间续做

```powershell
git fetch origin
git switch main
git pull --ff-only origin main
git status -sb
Get-Content docs/CORE-REAL-WORLD-PILOT-V1-HANDOFF.md
```

先补控制合同，不启动正式 Worker：

1. 修复三个 oracle 覆盖缺口；
2. 对每个变更后的 oracle 运行语法/静态检查和双次缺陷基线；
3. 更新控制文件哈希、执行登记、本地机器证据和 Control Amendment。

然后做 Vega 主线修复：

1. 为 `node_modules`、`.venv` 等大型依赖目录定义有界、可解释的 ignored inventory。
2. 保留对高价值 ignored 文件新增、修改和删除的检测能力。
3. 将 ignored 枚举 timeout 转成结构化、可恢复的 `needs_human` 终态。
4. 补 Node/Yarn/Python 大依赖目录回归测试，证明 worker 前后使用同一套 workspace 语义。
5. 修复通过后追加 Runtime Amendment，再从三个冻结 SHA 重建目标副本。

OpenStates 的生产文件路径会命中 `migration` 高风险门禁，后续即使 workspace 阻断解除，也应
按当前 fail-closed 语义转人工，不能为了让三个样本都进入 Reviewer 而放宽门禁。

## 不要做

- 不复用当前办公室机器上的目标副本作为正式运行输入；
- 不把 `config check` 或控制基线写成 Pilot 已执行；
- 不提交 `.local-validation/`、`.tmp/`、`.env`、数据库、Office 文件或本机绝对路径；
- 不自动 commit、push、release 或启动正式 Worker。
