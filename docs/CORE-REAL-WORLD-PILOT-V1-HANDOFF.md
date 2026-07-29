# Core Real-World Pilot v1 接力说明

> 日期：2026-07-29
>
> 分支：`main`
>
> 远端续做入口：`origin/main`

## 2026-07-29 接力

### 当前状态

- 必审高风险功能基线提交：
  `cab48777bd64017b373e5a35ec53fa49ff271288`。
- PR `#22` 已于 2026-07-28 合并，标题为
  `fix: bound ignored inventory and fail closed on timeout`。
- 主线随后新增可配置的 `risk.required_reviews` 必审高风险披露，并完成 Goal/Gate 防篡改、
  人工接管新鲜度和关键行号修复。当前快照已取得 `896 collected / 888 passed /
  8 skipped` 的全量 pytest 终态证据。
- 正式 CRWP-V1 Worker、Reviewer 和 Finish 仍然都没有启动。
- 三份 oracle 仍保留本地未提交修改：
  - `scripts/pilot/crwp-v1/crwp-v1-01-timeout-oracle.py`
  - `scripts/pilot/crwp-v1/crwp-v1-02-sqlite-autoincrement-oracle.cjs`
  - `scripts/pilot/crwp-v1/crwp-v1-03-stub-filename-oracle.py`
- 三个项目内目标副本位于 `.tmp/crwp-v1/targets/`，都停在预注册的 upstream SHA，
  工作区干净且当前没有 `.vega.yaml`。
- `.local-validation/crwp-v1/control-evidence/` 保留本机基线和 oracle 原始输出，继续保持
  ignored，不进入 Git。

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

当前工作树文件 SHA-256：

```text
5af44f7ec73328d373c791b4b042c5465ceeb754e2d2bf4f1aef45d17328cf76
```

该结果可以进入后续 Control Amendment，但在提交与登记更新前仍不是冻结控制哈希。

#### `CRWP-V1-02` Sequelize

块注释和行注释负对照已经写入 oracle，但当前版本仍被一个独立的上游解析限制阻断：

```text
Could not parse constraints from SQL: CREATE TABLE ...
```

原因是冻结版本的 `showConstraints()` 使用不支持换行的正则解析 `CREATE TABLE`。真实 SQL
行注释必须换行才能结束，因此当前 synthetic table 在 `describeTable()` 的 constraint
阶段失败，连续两次均以 `2` 退出，输出 SHA-256 均为：

```text
7cc34a40688d5095d540ccf95e246d7dd7c47c314240a637d2abe762a1c532c1
```

SQLite 还会从 `sqlite_master.sql` 中移除位于右括号后的尾随行注释和块注释，所以把注释放在
整个 `CREATE TABLE (...)` 之后也不能形成有效负对照。

下一会话应先隔离 `describeTable()` 的 auto-increment metadata 读取与
`showConstraints()` 的旧解析缺陷。推荐最小方案是在 oracle 内仅对该 synthetic 表临时替换
`queryInterface.showConstraints()` 为空结果，然后：

1. 确认 `sqlite_master.sql` 真实保留 `-- AUTOINCREMENT`；
2. 调用 `describeTable()` 获取列 metadata；
3. 断言普通整数主键没有被注释文本误标为 auto-increment；
4. 恢复原 `showConstraints()`；
5. 块注释用例继续走未替换的正常路径。

该替换只隔离与本题无关的旧 constraint parser，不得屏蔽待测 auto-increment metadata
逻辑。修正后必须重新执行 Node syntax check 和两次独立 oracle。

当前工作树文件 SHA-256：

```text
da9a6733d1a5c5743be2413191cc5e90a9ccef0e86473175fb596ef8e596a0cf
```

该哈希对应当前无效的 `exit=2` 版本，不得登记为最终控制哈希。

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

当前工作树文件 SHA-256：

```text
596145bfa64a84ae98bf63f6b96401e027d145a50aafc89410f0be68954f7e02
```

该结果可以进入 Control Amendment，但 Case 本身出现了新的资格变化。

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

1. 修正 Sequelize 行注释负对照，执行 syntax check 和双次 oracle。
2. 对三份最终 oracle 执行静态检查，计算最终 LF/index 版本 SHA-256。
3. 在预注册、执行登记和 `eval/real-world-runs.md` 中只追加 Control Amendment，不改写
   历史基线。
4. 记录 OpenStates `eligibility-changed-before-run`。未新建预注册前，不运行 Case 03。
5. 从冻结 SHA 重新准备目标副本，只提交 `.vega.yaml`，重新登记 prepared HEAD、tree 和
   config hash。
6. 重跑 config check、非 oracle 基线、双次 oracle、Issue/PR 资格和 Provider 探测。
7. 只有控制哈希、资格、workspace baseline 和 Provider 全部满足合同，才按固定顺序启动
   Dormice 和 Sequelize。每项最多两轮、总墙钟 30 分钟，不挑结果重跑。
8. 将真实结果只追加到 `eval/real-world-runs.md`，最后再执行 Vega 主仓库验证。

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
git -c "safe.directory=$repoRootPosix" status --short --branch
Get-Content docs/CORE-REAL-WORLD-PILOT-V1-HANDOFF.md
```

当前三份 oracle 修改尚未提交，也没有 push。不要丢弃或覆盖工作树；先从 Sequelize
阻断继续。

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
