# Core Real-World Pilot v1 预注册

> Pilot ID：`CRWP-V1`
>
> 状态：`preregistered / not-run`
>
> 登记日期：2026-07-28
>
> 公开资格快照截止：`2026-07-28T04:07:36Z`
>
> Vega Runtime 代码基线：`eb18d6ffd96436a0ce5f29d43fbda3c105c76464`
>
> 预注册提交：以首次同时包含本文和 `eval/real-world-runs.md` 对应追加条目的 Git 提交为准

## 一、目的与结论边界

本 Pilot 只回答一个问题：

> 当前 Vega 核心路径能否在三个冻结的真实代码任务上，产生可由确定性验证、隔离 reviewer
> 和外部控制端共同复核的结果，并如实记录失败、耗时与人工接管成本？

三个任务固定覆盖：

1. CLI 正整数输入校验；
2. SQLite 表重建时的 `AUTOINCREMENT` 语义保留；
3. 两个文件 writer 的命名唯一性、兼容读取与幂等行为。

第二项直接触及数据库 schema 重建和数据保留，但三个样本仍不能证明生产数据库迁移安全。
第三项验证连续多次写入，不是并发写入。首轮没有找到同时满足“合同唯一、可本地确定性验证、
规模可控”的干净并发样本，因此明确不覆盖并发、重试、重复投递、锁竞争或分布式唯一性。

本 Pilot 不新增 Runtime、CLI、状态、artifact schema、Assurance Stage、Memory、多 Reviewer
或自动 Git 操作。三个经过筛选的样本只产生三个原始结果，不计算成功率，也不证明跨仓库
泛化、生产安全或模型独立发现能力。

## 二、样本分类与冻结事实

正式运行顺序固定如下，不因某项失败而换题：

| 顺序 | Case ID | Issue | 冻结源码修订 | 分类 |
|---|---|---|---|---|
| 1 | `CRWP-V1-01` | `BitMiracle-AI/Dormice#33` | `f26ba3748e79c7225f4aafb757c6f9f1f6b2d733` | Issue 引导、无公开修复 PR |
| 2 | `CRWP-V1-02` | `sequelize/sequelize#18265` | `f0cea95e38b4f2c9096267371ab305d08f7b8497` | `controlled public replay` |
| 3 | `CRWP-V1-03` | `openstates/jurisdictions#122` | `6fbe7d6aed32c3b781490c8e4c5a737bdd6e4705` | Issue 引导、无公开修复 PR |

Issue 正文快照：

| Case ID | `updated_at` | Issue body SHA-256 |
|---|---|---|
| `CRWP-V1-01` | `2026-07-27T08:19:01Z` | `efcabe0528e47271a5003b356340df6a120000c6f010859f70341007fc0f4021` |
| `CRWP-V1-02` | `2026-07-17T02:41:11Z` | `2effa9288425d1227a5b3c61bf68eb33d8fd0ba81f023c2530d2cfb712df481e` |
| `CRWP-V1-03` | `2026-07-28T01:36:24Z` | `313b16e9364172f9d19d44155eeb506c91fb018258b2cf8e66a218b5960bc457` |

截至资格快照：

- 三个 Issue 均为 `open`、无 assignee、无评论。
- `CRWP-V1-01` 与 `CRWP-V1-03` 未发现关联修复 PR，默认分支仍保留目标行为。
- 两项 Issue 正文都给出了代码位置、复现信息或建议方案，因此同样不属于盲目根因发现实验。

### 2.1 `CRWP-V1-02` 的公开补丁污染

Sequelize Case 在冻结前已经存在公开 PR `sequelize/sequelize#18274`：

- 状态：`open`、非 draft、未合并；
- base：`f0cea95e38b4f2c9096267371ab305d08f7b8497`；
- head：`14aab37348d0d8d7bdca7dbe1faeeb4f8dedb67b`；
- GitHub `.diff` HTTP 响应原始字节：`6934` bytes，LF-only，保留末尾 LF；
- 上述原始字节的 SHA-256：`91bb5f43bed59d82f4db47d8cece9431558fdc4405463551d3441aefdc605b37`。

因此本项明确分类为 **controlled public replay（受控公开补丁重放）**。它只验证 Vega 的
范围遵守、实现质量、确定性验证和隔离 reviewer 价值，不用于声称模型独立发现根因或独立
设计修复。

PR 正文、diff、提交和 review 内容不得进入 worker 或 reviewer 输入。控制端只在 Vega 形成
终态后进行外部比较，且比较结果不能反向改变 Vega 的成功状态。模型训练数据是否已经包含该
公开修复无法排除。

这里的“diff 不进入”是指控制端封存的上游 PR diff 不作为参考输入；worker 在本次运行中
实际产生的 diff 仍必须按 Vega 合同交给 reviewer。

### 2.2 预注册阶段淘汰的候选

`sqlalchemy/alembic#1834` 曾作为第二项候选，后在预注册审查中淘汰：

- 已有基于原冻结修订的公开 Gerrit change；
- 上游仍在讨论是否应新增 opt-in，成功语义未稳定；
- SQLite reflection 无法可靠区分 convention 生成名与相同文本的用户显式名；
- 无法建立唯一、可独立判定且不伤害显式命名的 oracle。

`pailat/adk-llm-bridge#87` 等已经存在完整公开实现的候选也未进入本 Pilot。上述筛选过程不得
在运行后改写成“未考虑过其他候选”。

## 三、共同执行合同

### 3.1 运行前资格复核

每项正式运行前必须重新只读检查：

1. Issue 状态、assignee、评论数、正文哈希和 timeline 交叉引用与冻结快照一致；
2. 冻结源码修订仍可获取；
3. 基线 oracle 在两个独立进程中各执行一次，每次重置临时目录、数据库或连接哨兵，且两次
   都能在未修改业务源码时复现相同决定性字段；
4. 同一 `.vega.yaml` 中除独立 oracle 外的全部验证命令在未修改业务源码时通过；
5. 目标仓库的上游基线、准备提交、工作区、Git index 和未跟踪文件清单与登记一致；
6. `.vega.yaml`、项目规则、oracle、辅助脚本和依赖锁文件哈希与登记一致；
7. `vega config check --repo <target> --json` 返回 `passed`，且 `source_path` 指向预期配置。

对 `CRWP-V1-01` 和 `CRWP-V1-03`，若出现新修复 PR、等价合并提交、关闭或分配状态变化，
记录 `eligibility-changed-before-run` 并停止。

对 `CRWP-V1-02`，运行前必须重新核对 PR `#18274` 的状态、base、head 和 diff 哈希。任何变化
都记录 `eligibility-changed-before-run` 并停止，不静默采用新补丁或重新冻结。

`config check` 只做配置静态检查，不执行命令，也不验证外部 oracle 是否存在或与登记哈希
一致。不得把它的通过解释为 oracle 已验证。

### 3.2 目标仓库准备与 Git 边界

- 每项使用独立、干净的目标仓库副本，并从冻结修订创建本地 Case 分支。
- OpenStates 的分支名必须包含 Issue `#122`，满足其 issue-linked branch 规则。
- 目标仓库读取完必要上游指令并完成依赖准备后移除所有 Git remote。
- 控制端使用一个本地准备提交只加入 `.vega.yaml`；不得借准备提交修改业务源码、测试或
  上游项目规则。
- 分别登记 `upstream_base_sha` 与 `prepared_run_head_sha`；实验 diff 从后者开始计算，不能把
  准备提交误称为上游冻结修订。
- `.vega.yaml` 不得作为普通未跟踪文件遗留。正式 worker 启动前，目标工作区、index 和
  未跟踪文件必须为空。
- worker 不联网检索，不读取候选审计目录，也不接收 reviewer 对话。
- Vega 不 commit、push、release、删除目标文件或写入长期 Memory；准备提交由控制端在
  worker 启动前完成，不属于 Vega 自动行为。

这里创建的是三个目标运行副本的本地 Case 分支，不是在 Vega 仓库继续增加工作分支。Vega
预注册、执行参数和结果记录继续使用当前工作线上的原子提交。

### 3.3 会话、输入与 oracle 边界

- worker 使用 `workspace-write`；reviewer 使用新的短生命周期会话和 `read-only` 仓库视图。
- reviewer 只接收任务合同、项目规则、实际 diff、结构化验证、scope/risk gate 和必要证据。
- reviewer 不接收 worker 的完整聊天记录、自述或中间推理。
- 独立 oracle 源码放在目标仓库之外，不主动拼入 worker prompt；验证入口和行为合同对
  worker 可见。
- oracle 文件在基线执行前、worker 启动前和终态后分别校验 SHA-256。
- 每个角色调用前，分别对其最终编译输入记录原始字节 SHA-256。Sequelize Case 还要在调用前
  对输入负向扫描 PR `#18274`、PR 标题、head SHA 和 diff SHA；任一命中都按输入污染停止。

当前 Runtime 没有角色级环境变量过滤或 verification-only 文件系统隔离。worker/reviewer
进程可能继承 `CRWP_CONTROL_ROOT`，因此不能宣称控制目录在操作系统层不可见。本 Pilot
验证的是输入分离、只读 reviewer 和事后完整性，不是抵抗恶意 worker 的机密性沙箱。

### 3.4 执行参数登记

首个正式 worker 启动前，必须用后续独立 Git 提交登记：

- Codex CLI 版本；
- worker 与 reviewer 的显式 model；
- worker `reasoning_effort=medium`；
- reviewer `reasoning_effort=high`；
- 两个角色均使用临时 session；
- Windows 版本、架构、Node/Python/包管理器版本；
- 每项依赖安装命令及 lockfile 或环境清单哈希；
- `upstream_base_sha`、`prepared_run_head_sha`、初始 tree 与工作区指纹；
- oracle、辅助脚本、任务合同、项目规则和 `.vega.yaml` 的 SHA-256；
- 输入编译所用 Vega Runtime 基线，以及 Sequelize 公开补丁负向扫描词表；
- `configured_command_count`、去重后的 `distinct_selected_command_count` 和 `max_commands`；
- `vega config check --repo <target> --json` 的原始结果。

`CRWP_CONTROL_ROOT` 必须解析到登记的仓库相对控制目录。公开登记只写仓库相对位置；解析后
绝对路径的 SHA-256 保存在本地原始证据中，不把本机路径提交到 Git。每项还必须使用最终
`.vega.yaml` 中的原始命令，通过 `cmd.exe /d /v:off /s /c` 完成基线执行：非 oracle 命令
全部通过，oracle 按上述双次独立复现合同失败。只验证脚本文件存在或手工运行等价命令不算。

三项必须使用相同的 worker/reviewer model 和 reasoning 配置。主机预期 shell kind 冻结为
`cmd`，终态必须核对 `verification-result.json` 中的实际 `shell_kind`。

每项的三个命令数量字段必须相等，不能依赖 Runtime 默认的两条命令上限：

| Case ID | `max_commands` |
|---|---|
| `CRWP-V1-01` | `6` |
| `CRWP-V1-02` | `5` |
| `CRWP-V1-03` | `5` |

执行参数未登记、oracle 未 materialize、哈希未冻结或静态配置检查未通过前，不得启动正式
worker。

### 3.5 迭代、超时与重试

- 每项最多两个自动 iteration：初始 worker 加最多一次 reviewer repair。
- worker 和 reviewer 单次 attempt 沿用当前 Runtime 的 900 秒 deadline。
- 每条 verification 命令最长 300 秒；需要更长时间时必须在运行前追加 amendment。
- 每项从 worker 启动到 Finish 的总墙钟停止线为 30 分钟；依赖安装和基线预检单独计时。
- Provider 错误、timeout、stopped、termination-unconfirmed 或证据损坏不自动重试。
- 人工只能执行 stop、recover、查看证据和决定是否创建后续合同；人工直接修改代码后，
  当前 primary run 不再算自动结果。

两个 iteration 用尽后仍未满足合同，状态保持 `needs_human` 或更严格结果，不开启第三轮。

## 四、Case `CRWP-V1-01`：Dormice CLI timeout 校验

### 4.1 来源与基线

- Issue：`BitMiracle-AI/Dormice#33`
- 冻结修订：`f26ba3748e79c7225f4aafb757c6f9f1f6b2d733`
- Node.js：`22.22.0`
- pnpm：`10.30.2`
- 基线安装：`pnpm install --frozen-lockfile`
- 基线构建：`pnpm --filter @dormice/cli... build`
- 基线定向测试：`pnpm --filter @dormice/cli test`
- 已观察结果：`59 passed`

基线命令：

```text
dor sandbox exec demo "echo hi" --timeout not-a-number
```

当前以非零状态退出，但错误来自内部 `AbortSignal`：

```text
The value of "delay" is out of range. It must be an integer. Received NaN
```

### 4.2 修改边界

只允许修改：

```text
packages/cli/src/main.ts
packages/cli/src/main.test.ts
```

禁止修改：

- `package.json`、`pnpm-lock.yaml`、workspace 配置和依赖版本；
- SDK、server、shared schema、网站和 e2e；
- 项目规则、任务合同、oracle 和验证命令；
- 与 timeout 解析无关的 CLI 命令。

不要求生成上游 changeset，因为本 Pilot 不创建上游 PR 或发布包。

### 4.3 行为合同

必须满足：

1. 解析规则固定为：`Number(value)` 的结果必须满足 `Number.isFinite(n)`、
   `Number.isInteger(n)` 且 `n > 0`；
2. 拒绝 `not-a-number`、`10m`、`0`、`-1`、`1.5` 和 `Infinity`；
3. 接受 `1`、`60`、`1e2`、`0x10` 和 `+1`，并把解析后的正整数传入现有
   `sandboxExec` 路径；本 Pilot 不额外引入“仅十进制数字串”的词法限制；
4. 未指定 `--timeout` 时继续使用现有默认行为；
5. 无效值不得调用 `clientFromEnv`、`sandboxExec` 或建立连接；
6. 错误明确指向 `--timeout` 或 seconds 参数，不出现 `AbortSignal`、`delay`、`RangeError`
   或堆栈；
7. CLI 不重复实现 server 已有的最大值约束；
8. 其他 CLI 命令的解析和退出码不变。

### 4.4 独立控制与验证

独立 oracle 必须：

- 启动本地连接哨兵；
- 逐个验证六个无效值均在连接前失败；
- 断言 stderr 是面向参数的单一错误，不含内部实现细节或堆栈；
- 断言连接哨兵收到零次连接；
- 使用 `1`、`60`、`1e2`、`0x10` 和 `+1` 确认解析后的正整数仍进入原有执行路径。

项目单测还必须用 spy 证明无效值不会调用 `clientFromEnv` 或 `sandboxExec`。oracle 不导入
worker 新增的测试 helper。

验证命令冻结为：

```text
pnpm --filter @dormice/cli... build
pnpm --filter @dormice/cli test
pnpm --filter @dormice/cli typecheck
pnpm exec biome check packages/cli/src
python "%CRWP_CONTROL_ROOT%\crwp-v1-01-timeout-oracle.py" --repo .
git diff --check
```

仓库根 `pnpm build` 在 Windows 候选审计中因 website 使用 POSIX 环境变量前缀而失败；该命令
不作为本地成功门禁。目标包及其依赖的定向 build、test、typecheck 是本项门禁，上游跨平台
完整结果只能作为后续外部证据。

## 五、Case `CRWP-V1-02`：Sequelize SQLite `AUTOINCREMENT`

### 5.1 来源与基线

- Issue：`sequelize/sequelize#18265`
- 冻结修订：`f0cea95e38b4f2c9096267371ab305d08f7b8497`
- Node.js：`22.22.0`
- Yarn：`4.13.0`
- 基线安装：`corepack yarn install --immutable --mode=skip-build`
- 基线构建：`corepack yarn build`
- 基线定向 SQLite 测试：`41 passing`
- 相关 ESLint：通过

候选审计观察到：

1. 初次 `sync()` 创建 `INTEGER PRIMARY KEY AUTOINCREMENT`；
2. `sync({ alter: true })` 重建同一张表；
3. 重建后的 `sqlite_master.sql` 只剩 `INTEGER PRIMARY KEY`。

基线判定字段固定为：

```text
beforeHasAutoincrement: true
afterHasAutoincrement: false
lostAutoincrement: true
```

Windows 测试加载器会导入本项未使用的 DB2 native driver。控制端使用已登记哈希的
`ignore-native-drivers.cjs` 只替换该未使用模块的加载，不能修改 SQLite 或待测实现。该辅助
脚本与 oracle 同样属于可信控制输入。

### 5.2 修改边界

只允许修改：

```text
packages/sqlite3/src/query-interface.ts
packages/core/test/integration/query-interface/createTable.test.js
packages/core/test/integration/sequelize.test.js
```

禁止修改：

- lockfile、依赖、构建配置和 native driver；
- 非 SQLite dialect；
- 数据类型公共契约或 `sync({ alter: true })` 的无关路径；
- 项目规则、任务合同、oracle、辅助脚本和验证命令；
- 为通过测试而放松原有断言或跳过 SQLite 用例。

### 5.3 行为合同

必须满足：

1. `sync({ alter: true })` 重建后，原本声明为 auto-increment 的 SQLite 整数主键仍包含
   `AUTOINCREMENT`；
2. `describeTable()` 对该列继续报告 `autoIncrement=true`；
3. 重建前已有数据、列值和主键值保持不变；
4. 删除当前最大主键后再插入，新主键不得复用已删除值；
5. 普通 `INTEGER PRIMARY KEY` 不能被误判为 auto-increment；
6. SQL 注释、字符串字面量、`CHECK` 或 `DEFAULT` 中出现文本 `AUTOINCREMENT` 时不能误判；
7. 修复只影响 SQLite，不改变其他 dialect 的 metadata 行为；
8. 连续执行 `sync({ alter: true })` 不得再次丢失语义或破坏数据。

Reviewer 必须拒绝“只要整张表 SQL 出现 `AUTOINCREMENT` 就把任意主键标记为 true”以及依赖
脆弱字符串切分、会被注释或字符串字面量欺骗的宽泛修复。

### 5.4 独立控制与验证

独立 oracle 不导入 worker 新增的测试 helper，必须在临时 SQLite 数据库中：

- 创建带 auto-increment 主键和数据的模型；
- 记录重建前后的 `sqlite_master.sql`；
- 执行 `sync({ alter: true })` 后验证 SQL、metadata 和原数据；
- 删除最大主键并再次插入，验证 ID 不复用；
- 连续执行第二次 alter，验证语义与数据仍稳定；
- 建立普通整数主键及包含 `AUTOINCREMENT` 字面量的负对照，验证不误判。

验证命令冻结为：

```text
corepack yarn build
set "NODE_OPTIONS=--require=%CRWP_CONTROL_ROOT%\ignore-native-drivers.cjs" && set "DIALECT=sqlite3" && corepack yarn workspace @sequelize/core mocha test/integration/query-interface/createTable.test.js test/integration/sequelize.test.js
corepack yarn eslint packages/sqlite3/src/query-interface.ts packages/core/test/integration/query-interface/createTable.test.js packages/core/test/integration/sequelize.test.js --report-unused-disable-directives
node "%CRWP_CONTROL_ROOT%\crwp-v1-02-sqlite-autoincrement-oracle.cjs" --repo .
git diff --check
```

一次完整依赖安装曾因未使用 native driver 的安装脚本超过十分钟而由控制端终止；正式准备
固定使用 `--mode=skip-build`，随后执行完整 workspace build。完整多数据库 integration suite
依赖未提供的外部数据库，不作为本项本地成功门禁。

## 六、Case `CRWP-V1-03`：OpenStates ancestor stub 文件名

### 6.1 来源、仓库规则与基线

- Issue：`openstates/jurisdictions#122`
- 冻结修订：`6fbe7d6aed32c3b781490c8e4c5a737bdd6e4705`
- Python：`3.12.13`
- uv lock：冻结修订中的 `uv.lock`
- 基线安装：`uv sync --frozen --all-extras`
- 基线定向测试：`21 passed`
- `AGENTS.md` blob：`e7c84f0c96cf32742d222e1762c1e9818befb693`
- `ai_tools/catalog.yaml` blob：`e797b01192b23835c2bb5ed3c2fcfa068c8bfb4c`

正式准备必须从冻结 SHA 创建包含 Issue `#122` 的本地分支，先读取 `AGENTS.md`、
`ai_tools/catalog.yaml` 以及 catalog 指向的 active system、pre-commit 和 feature-delivery
指令。该分支只存在于目标运行副本，不增加 Vega 仓库分支。

当前两个 writer 都使用：

```text
<safe_name>_stub.yaml
```

YAML 内已经存在各自模型的 UUID，但文件名没有使用该 `id`。

### 6.2 修改边界

只允许修改：

```text
src/init_migration/generate_recursive.py
tests/src/init_migration/test_generate_recursive.py
```

禁止修改：

- `src/models/`、已有 `divisions/`、`jurisdictions/` 和 `tests/sample_output` 数据；
- lockfile、依赖、项目规则和 integration tests；
- display name 清洗规则；
- Issue `#95`、`#121` 或其他 ancestor stub 数据质量问题；
- stub `url`、模型合同、任务合同、oracle 和其他 migration pipeline 行为。

### 6.3 行为合同

必须分别对 Division writer 和 Jurisdiction writer 满足：

1. 在同一个输出目录，以相同 display name、不同 OCDid/model id 连续写两次；
2. 两个文件同时存在，不发生覆盖；
3. basename 精确为 `<safe_name>_<yaml.id>.yaml`；
4. 文件名不包含 `_stub`，文件名 UUID 与 YAML 内 `id` 完全一致；
5. 除输出文件名外，生成的 YAML 语义保持不变。

同时必须满足：

6. `stub_exists()` 继续读取 YAML 内 `ocdid` 判断，不得改成依赖文件名；
7. legacy `_stub.yaml` 和新 UUID 文件都能被 `stub_exists()` 找到；
8. 重复执行 `ensure_ancestor_stubs()` 不新增文件，也不改写已有文件；
9. 不把 Division 与 Jurisdiction 的天然不同目录当作“不覆盖”的证据。

Issue 提到“同 OCDid 多 stub 共存”，但当前 `ensure_ancestor_stubs()` 会按 YAML 内 `ocdid`
去重。本 Pilot 不虚构一次正常递归会写出两个同 OCDid 文件；路径唯一性由每个 writer 的
同目录双写控制单独验证。

### 6.4 独立控制与验证

独立 oracle 必须：

- 对两个 writer 分别建立同目录双写场景；
- 从实际 YAML 读取 model `id`，再核对精确 basename；
- 验证两个文件内容、路径和 `ocdid` 均未互相覆盖；
- 验证 legacy 与 UUID 命名文件都能由 `stub_exists()` 按 YAML 内 `ocdid` 找到；
- 对 `ensure_ancestor_stubs()` 前后比较文件清单与内容哈希，证明重复执行无新增、无改写。

验证命令冻结为：

```text
uv run pytest tests/src/init_migration/test_generate_recursive.py -q
uv run ruff check src/init_migration/generate_recursive.py tests/src/init_migration/test_generate_recursive.py
uv run python "%CRWP_CONTROL_ROOT%\crwp-v1-03-stub-filename-oracle.py" --repo .
uv run pytest -m "not integration and not slow" -q
git diff --check
```

完整非 integration/slow 测试超过 300 秒时按 timeout 保留，不以定向测试替代完整测试成功。

## 七、结果与成本记录

每项必须记录原始值，不根据三个样本计算百分比：

- eligibility 检查时间和结果；
- setup、baseline、worker、每条 verification、reviewer、Finish 的墙钟时间；
- worker/reviewer attempt 与 iteration 数；
- worker/reviewer prompt 字符数、UTF-8 字节数和行数；
- runner 可验证提供的 token 原始值；不可用时写 `unavailable`，不得用字符数估算；
- 模型调用次数；
- reviewer verdict、finding 数及其严重级别；
- reviewer finding 中可由独立 oracle 复现、且原验证未覆盖的增量发现数；
- 人工接管次数、原因和人工主动操作分钟数；
- 基础设施等待时间；
- 修改文件数、增删行和新增测试数；
- 失败 attempt、timeout 和 follow-up 的全部时间与可用 token；
- controlled public replay 的终态后外部 diff 比较结果。

当前 Vega 没有稳定、权威的输入/输出 token 分类和美元费用字段。美元成本只有在 Provider
返回可核验 usage 且预先冻结价格快照时才可派生；否则必须记录为 `unavailable`。

## 八、结果分类与停止条件

单项只有同时满足以下条件，才可保留 Vega 既有的 `ready_to_commit` 结论：

1. baseline oracle 在两次独立、已重置环境中都稳定失败，且决定性字段一致；
2. 相关基线测试在修改前通过；
3. worker 只修改允许路径；
4. 非空、完整、未中断的结构化 verification 全部通过；
5. 三阶段 scope gate、risk gate、evidence integrity/freshness 均有效；
6. reviewer 输入不含 worker 完整聊天记录，且返回合法 verdict；
7. reviewer 前后可信工作区指纹一致；
8. oracle、辅助脚本、配置和策略的终态哈希与登记一致；
9. Finish 重新校验后为 `ready_to_commit`。

以下任一情况立即停止自动执行：

- Issue 或已冻结公开 PR 的资格发生变化；
- 基线不能复现或基线相关测试失败；
- 工作区不干净、index/HEAD/策略发生未授权变化；
- 修改越过 allowlist、修改依赖或改写合同/oracle；
- verification 失败、缺失、损坏、过期、timeout 或 termination 未确认；
- reviewer context 被截断、证据不足或 verdict 无效；
- Provider/runner 错误；
- 两个 iteration 用尽；
- 30 分钟总墙钟停止线到达。

reviewer 的 `approve` 不能覆盖上述任何失败。人工裁决可以决定后续动作，但不得被记录为
确定性验证成功。`ready_to_commit` 只表示进入人工提交前检查，不表示已提交、已合并、上游
接受或已发布。

## 九、Artifact 与公开证据

原始运行只保留在忽略目录 `runs/` 和 `.local-validation/`。每项至少保留：

- run ID、`upstream_base_sha`、`prepared_run_head_sha`；
- Issue/task/oracle/policy/config/lockfile 哈希；
- `state.json`、`trace.jsonl` 和 execution 状态；
- worker/reviewer prompt metrics；
- worker/reviewer 最终编译输入的原始字节 SHA-256，以及 Sequelize 负向扫描结果；
- 实际 diff、changed files 和 diff SHA-256；
- workspace check 与三阶段 scope gate；
- `verification-result.json`、验证摘要和每条命令日志；
- risk gate；
- review context/evidence/verdict；
- Finish/Eval 结果；
- 环境、工具链、已知限制和人工接管记录。

公开证据如后续获准提交，放入：

```text
examples/evidence/core-real-world-pilot-v1/<case-id>/
```

公开包必须脱敏，不包含绝对工作区路径、PID、凭据、环境文件、完整 prompt/聊天记录、原始
`runs/` 或目标仓库 remote。公开摘要必须说明其不能独立重建未公开的完整 Finish/Eval 判断。

## 十、实施顺序

1. 本文和 `eval/real-world-runs.md` 的预注册条目先形成独立 Git 提交。
2. 在该提交之前不得启动任何正式 worker。
3. 下一提交只 materialize 三个 oracle、Sequelize native-driver stub 和执行参数登记；同时
   记录文件哈希、目标准备提交、`.vega.yaml`、命令数量与 `config check` 结果。
4. 运行前如发现合同错误，只能先追加带日期和原因的 amendment，并形成新的独立提交。
5. 一项形成终态记录后才进入下一项。
6. 执行结果、失败、中断和 correction 只能追加，不回写本文或历史运行记录。
7. 三项完成或停止后结束 Pilot，不顺带启动 Stage 4、并发 detector、新 CLI、多 Reviewer、
   MA-2B 或其他基础设施工作。
