# Core Real-World Pilot v1 执行登记

> Pilot ID：`CRWP-V1`
>
> 状态：`registration-review-blocked / preflight-blocked / worker-not-started`
>
> 登记日期：2026-07-28
>
> 预注册提交：`6f6878a244d7deaf495d656f1371cce06f770b2a`
>
> Amendment 提交：`d372e88`、`30c89e5`
>
> Vega Runtime 代码基线：`eb18d6ffd96436a0ce5f29d43fbda3c105c76464`
>
> 控制输入登记提交：以首次同时包含本文和 `scripts/pilot/crwp-v1/` 四个控制文件的提交为准

## 一、当前结论

三个目标副本、最终 `.vega.yaml`、控制 oracle 和依赖环境已经 materialize。控制端已经完成：

- 三个目标各自只用一个本地准备提交加入 `.vega.yaml`；
- 准备提交的 parent 分别等于预注册的冻结上游修订；
- 三个目标均已移除 Git remote，tracked、index 和未跟踪状态为空；
- 三次 `config check --json` 均为 `passed`，命令数分别为 `6 / 5 / 5`；
- 最终配置中的全部非 oracle 命令均通过；
- 三个 oracle 都在独立进程中连续两次以 `1` 退出，决定性输出字节完全一致；
- 最终命令均通过当前 Runtime 的 Windows 原生命令行构造执行，`shell_kind=cmd`，
  每条命令的 owned execution 均进入终态，`termination_unconfirmed=false`。

登记审查随后发现三个控制 oracle 仍有合同覆盖缺口：

- Dormice 默认 timeout 分支未校验 Authorization、name 和 command；
- Sequelize 未建立真实 `-- AUTOINCREMENT` / `/* AUTOINCREMENT */` SQL 注释负对照；
- OpenStates 幂等场景未断言首次运行真实生成预期类型和 `ocdid` 的 YAML。

因此当前 oracle 双次结果只能证明现有检查稳定复现目标行为，不能视为最终控制合同已经冻结。
修订三个 oracle 后必须更新哈希并重新执行各自双次基线。

但正式 worker **没有启动**。当前 Runtime 无法为三个已安装依赖的普通目标仓库建立可信的
ignored 工作区基线：

- Dormice 的 ignored 枚举在 Runtime 固定的 30 秒 Git 读取上限内超时；
- Sequelize 与 OpenStates 的 ignored 文件数超过 Runtime 的 4096 个元数据条目预算，
  `snapshot_workspace().capture_complete=false`。

这已经命中预注册中的运行前停止条件。控制端因此在创建正式 run 前停止，没有让 Dormice 的
`TimeoutExpired` 进入 auto loop。`config check` 和目标命令通过不能覆盖工作区基线不可信，
因此本登记不包含 run ID、worker 输出、reviewer verdict 或 Finish 结论，也不得表述为
“Pilot 已执行失败”或“模型没有修好”。当前准确状态是：**配置解析与非 oracle 命令已验证；
控制 oracle 仍待补齐合同，可信 workspace baseline 未建立，正式执行未启动。**

## 二、执行环境与角色参数

### 2.1 主机与工具

| 项目 | 登记值 |
|---|---|
| 操作系统 | Windows 10 Enterprise G 21H2，build `19044.1415` |
| 架构 | `AMD64` |
| Git | `2.33.0.windows.2` |
| Codex CLI | `0.144.6` |
| Vega 源码版本 | `0.1.3` |
| Vega 控制端 Python | `3.14.3` |
| Node.js | `22.22.0` |
| Dormice pnpm | `10.30.2` |
| Sequelize Yarn | `4.13.0` |
| OpenStates Python | `3.12.13` |
| uv | `0.10.10` |

全局 `vega.exe` 指向旧版 `0.1.0`，不属于本实验 Runtime。所有 Vega 只读配置检查和控制端
调用均通过当前仓库 `src/` 执行。

### 2.2 Worker 与 Reviewer

三个 Case 使用相同角色配置：

| 角色 | Runner | Model | Reasoning | Session | Sandbox |
|---|---|---|---|---|---|
| Worker | `codex-exec` | `gpt-5.4` | `medium` | `ephemeral` | `workspace-write` |
| Reviewer | `codex-exec` | `gpt-5.4` | `high` | `ephemeral` | `read-only` |

控制端对 `gpt-5.4` 的短探测曾返回 `READY`，Codex CLI 同时提示本地缺少该模型 metadata，
将使用 fallback metadata。另一次 `gpt-5.3-codex` 探测约 185 秒超时，未被静默替换为正式
模型，也未留下已知残余进程。上述探测没有独立原始日志文件，因此只作为模型选择说明，
不作为 Provider 可用性的持续证明；正式运行前仍需重新探测。

## 三、控制输入与合同哈希

### 3.1 控制目录

- `CRWP_CONTROL_ROOT` 仓库相对路径：`scripts/pilot/crwp-v1`

解析后绝对路径及其哈希只保存在 ignored 本地证据中，公开登记不保存本机路径指纹。当前
Runtime 没有为 worker、reviewer 和 verification 分别过滤环境变量，因此该目录只属于输入
分离和事后完整性边界，不构成操作系统级机密目录。

### 3.2 文件 SHA-256

| 文件 | SHA-256 |
|---|---|
| `scripts/pilot/crwp-v1/crwp-v1-01-timeout-oracle.py` | `75b7c31523a881cff66622d9a51cef99a32a916a3a6b8d44f42de941a80e4beb` |
| `scripts/pilot/crwp-v1/crwp-v1-02-sqlite-autoincrement-oracle.cjs` | `67817feb79dc91ad700195c299c7351e4ec18723e4b4d4c5b4431799146d3225` |
| `scripts/pilot/crwp-v1/crwp-v1-03-stub-filename-oracle.py` | `31044c384b39d386c72be78f8505b86049dfb46c2733a1921028f45ab0baf3cb` |
| `scripts/pilot/crwp-v1/ignore-native-drivers.cjs` | `d168cf32755ccda649a13bf245873a0d7cad153c74d4792f3f7d963e62c28b65` |
| `docs/CORE-REAL-WORLD-PILOT-V1-PREREGISTRATION.md` | `cdbb2d48df5959630b786e6cb4d2c6e1e6cd2a22d6a4623f5128df55ca841a89` |
| `eval/real-world-runs.md` | `ceeba74220545a2b11fe0543f803c2e6aa2f2e93c9c3f1d3183480d97b6b51ec` |

OpenStates oracle 已在导入目标模块前把
`src.init_migration.generate_recursive` logger 的级别设为 `WARNING`。这只过滤目标模块的
正常 `DEBUG/INFO`，不吞掉 warning、error 或 Python 异常；两次最终 oracle 输出都是单一 JSON。

本表记录的是当前待修订控制文件哈希。第十二节列出的三个合同缺口修复后，必须更新本表和
本地机器登记，不能沿用当前哈希启动正式 Worker。

## 四、目标准备提交

| Case | 本地分支 | Upstream base | Prepared HEAD | Prepared tree | `.vega.yaml` SHA-256 |
|---|---|---|---|---|---|
| `CRWP-V1-01` | `pilot/crwp-v1-01-dormice-33` | `f26ba3748e79c7225f4aafb757c6f9f1f6b2d733` | `9bf20d0c955aab205278e00e01293ea7e12abea7` | `7735e8269afab4a26b2b7c8cf66e074961f8ce28` | `d4322d5ce2c9e86dad259bfcf4795dc70d548a81eb01d115d5f84cc40c2711a7` |
| `CRWP-V1-02` | `pilot/crwp-v1-02-sequelize-18265` | `f0cea95e38b4f2c9096267371ab305d08f7b8497` | `6a7bf0c5457914fd034fbf6dd0ec8f46f82e6da6` | `67f271bb1fbd2506fc556ecab4ea319b827b234f` | `844800d61f6dbd016357e796f3db5bb7f371b22c0cdc1e50ccf25d47e92b2024` |
| `CRWP-V1-03` | `issue-122-ancestor-stub-filename` | `6fbe7d6aed32c3b781490c8e4c5a737bdd6e4705` | `d5f89f526924a08c6f0c6b882a59cdde07763247` | `d85dd61273c754204a9e32a345874b8938d5845c` | `9ae1f19d5417928656a114ca66ed23a4613ef374d7a3f6a978bb75ab54634b8e` |

三个 prepared commit 的 parent 均与对应 Upstream base 完全相同，commit diff 都只包含
`.vega.yaml`。三个目标的 remote 数量均为 `0`，`git status -sb` 只有分支标题。

控制端另记录了目标绝对路径哈希、porcelain v2 状态哈希和 index flag 哈希。这些值保存在
ignored 本地证据中，不在公开文档暴露路径；它们是控制端清单，不替代 Vega 未能生成的可信
workspace fingerprint。

## 五、最终机器策略

### 5.1 `CRWP-V1-01`

```yaml
version: 1
verification:
  commands:
    - pnpm --filter @dormice/cli... build
    - pnpm --filter @dormice/cli test
    - pnpm --filter @dormice/cli typecheck
    - pnpm exec biome check packages/cli/src
    - python "%CRWP_CONTROL_ROOT%\crwp-v1-01-timeout-oracle.py" --repo .
    - git diff --check
  max_commands: 6
  timeout_seconds: 300
budget:
  max_changed_files: 2
  max_diff_lines: 350
  max_new_files: 0
  forbid_new_dependencies: true
  forbid_large_generated_files: true
scope:
  allowed_paths:
    - packages/cli/src/main.ts
    - packages/cli/src/commands.test.ts
runner:
  worker: codex-exec
  reviewer: codex-exec
  codex_exec:
    worker:
      model: gpt-5.4
      reasoning_effort: medium
      ephemeral: true
    reviewer:
      model: gpt-5.4
      reasoning_effort: high
      ephemeral: true
```

### 5.2 `CRWP-V1-02`

```yaml
version: 1
verification:
  commands:
    - set "NX_DAEMON=false" && corepack yarn build
    - set "NODE_OPTIONS=--require=%CRWP_CONTROL_ROOT%\ignore-native-drivers.cjs" && set "DIALECT=sqlite3" && corepack yarn workspace @sequelize/core mocha test/integration/query-interface/createTable.test.js test/integration/sequelize.test.js
    - corepack yarn eslint packages/sqlite3/src/query-interface.ts packages/core/test/integration/query-interface/createTable.test.js packages/core/test/integration/sequelize.test.js --report-unused-disable-directives
    - node "%CRWP_CONTROL_ROOT%\crwp-v1-02-sqlite-autoincrement-oracle.cjs" --repo .
    - git diff --check
  max_commands: 5
  timeout_seconds: 300
budget:
  max_changed_files: 3
  max_diff_lines: 600
  max_new_files: 0
  forbid_new_dependencies: true
  forbid_large_generated_files: true
scope:
  allowed_paths:
    - packages/sqlite3/src/query-interface.ts
    - packages/core/test/integration/query-interface/createTable.test.js
    - packages/core/test/integration/sequelize.test.js
runner:
  worker: codex-exec
  reviewer: codex-exec
  codex_exec:
    worker:
      model: gpt-5.4
      reasoning_effort: medium
      ephemeral: true
    reviewer:
      model: gpt-5.4
      reasoning_effort: high
      ephemeral: true
```

### 5.3 `CRWP-V1-03`

```yaml
version: 1
verification:
  commands:
    - uv run pytest tests/src/init_migration/test_generate_recursive.py -q
    - uv run ruff check src/init_migration/generate_recursive.py tests/src/init_migration/test_generate_recursive.py
    - uv run python "%CRWP_CONTROL_ROOT%\crwp-v1-03-stub-filename-oracle.py" --repo .
    - uv run pytest -m "not integration and not slow" -q
    - git diff --check
  max_commands: 5
  timeout_seconds: 300
budget:
  max_changed_files: 2
  max_diff_lines: 400
  max_new_files: 0
  forbid_new_dependencies: true
  forbid_large_generated_files: true
scope:
  allowed_paths:
    - src/init_migration/generate_recursive.py
    - tests/src/init_migration/test_generate_recursive.py
runner:
  worker: codex-exec
  reviewer: codex-exec
  codex_exec:
    worker:
      model: gpt-5.4
      reasoning_effort: medium
      ephemeral: true
    reviewer:
      model: gpt-5.4
      reasoning_effort: high
      ephemeral: true
```

三次 `config check` 均返回 `status=passed`、`issues=[]`。每项都满足：

```text
configured_command_count
== distinct_selected_command_count
== max_commands
```

`config check` 不执行命令，也不验证 oracle 哈希、`CRWP_CONTROL_ROOT`、模型可用性或命令结束后
是否残留后台进程。本节只能证明 Runtime 能解析策略，不能证明目标可运行。

## 六、依赖与项目规则哈希

### 6.1 依赖清单

| Case | 文件 | SHA-256 |
|---|---|---|
| `CRWP-V1-01` | `pnpm-lock.yaml` | `f4c3208f72bc388b14e766883a050ffe51bf4cd6a385473fdeedab38cba481a6` |
| `CRWP-V1-01` | `package.json` | `0d7a5f38ea761e394b43cd58f253a04bb325c990dcb484758a36b3dfad5e7cfe` |
| `CRWP-V1-02` | `yarn.lock` | `521c128c9ba08b91ff40d12ed6443f742c43784b05fe1fd347c233c6bd45926c` |
| `CRWP-V1-02` | `package.json` | `76771e9678f240f67cbfe5e81b6189e2f19e3d684af651bc0150708d080cd6cd` |
| `CRWP-V1-02` | `.yarnrc.yml` | `013aff27ac2a697fb9b89079fea97266d53742471f16c0ad50001c37d97197f2` |
| `CRWP-V1-03` | `uv.lock` | `8a5732317a4928b90f446a47caba5859ed92afd894cc14876f64244878b14811` |
| `CRWP-V1-03` | `pyproject.toml` | `6a47f93dff659f742187aced57dfe46f61d03e9dfc6839f6df55fad893988c44` |

Dormice 与 Sequelize 冻结修订的仓库根没有受跟踪的 `AGENTS.md`。OpenStates 已读取
`AGENTS.md`、catalog 以及本任务相关的 active 指令：

| 文件 | SHA-256 |
|---|---|
| `AGENTS.md` | `ed45348c0eccf0eddc49eda2d9e156f6ed54d4ebe4abd370ca1d36385cccc459` |
| `ai_tools/catalog.yaml` | `1cc017f3ac67b981924f65a72c626e4f42a62203d85fc8b8e143243c1079fbca` |
| `ai_tools/system/repo-agent-system.instruction.md` | `a2b73c9dd7d29c6b74736336e7ba57fa60aabc32a0822eae4e536df4021fa9d2` |
| `ai_tools/system/pre-commit-checks.instruction.md` | `182c887dc27bf6b03bbe1d52d1b9005f5bfcc4ff482d8885cc1c4818d08f3a6d` |
| `ai_tools/system/contributor-workflows.instruction.md` | `ba03e5a068846df24c64bd022454f3a79736ab60e4c38b82e3c70ce76701a233` |
| `ai_tools/tasks/feature-delivery.instruction.md` | `e67cc1eca3b571f2f49ea6c31dcd8e9e62a5d37bdd384b99a9a342b827a03585` |

## 七、依赖准备中的失败与更正

### 7.1 Dormice

执行：

```text
corepack pnpm install --frozen-lockfile
corepack pnpm --filter @dormice/cli... build
```

依赖安装和目标包构建完成。项目根 `pnpm` 在该仓库内根据 `packageManager` 解析为 `10.30.2`。

### 7.2 Sequelize

执行：

```text
corepack yarn install --immutable --mode=skip-build
```

初次 `corepack yarn build` 因 SQLite native binary 尚不存在而失败。随后一次
`corepack yarn rebuild sqlite3` 超过十分钟，并继续尝试本 Case 不使用的 `ibm_db`；控制端只
终止该次 owned 进程树。该过程已经产生可用的 SQLite binary：

```text
length = 1980416
sha256 = 5e1d1275e126c3fc584bcf5752fbf747bff89454bfcf8bc76c982b24e7815057
```

之后完整 workspace build 通过。上述 rebuild timeout 来自控制端交互记录，未保留独立原始
日志文件，因此只作为必须披露的 setup 事件，不把它包装成结构化验证证据。

第一次使用原命令 `corepack yarn build` 做最终基线诊断时，命令以 `0` 退出但留下 Nx daemon。
控制端执行 `corepack yarn nx reset` 后完成清理，并通过 Amendment 2 把最终命令改为：

```text
set "NX_DAEMON=false" && corepack yarn build
```

最终基线重新执行后，控制端交互扫描未发现目标相关残余进程；该历史扫描没有独立 artifact，
不能反推为结构化的“残余进程数为 `0`”证据。登记整理阶段另生成带时间和匹配规则的当前
进程审计，见第十一节。

### 7.3 OpenStates

执行：

```text
uv sync --frozen --all-extras
```

依赖准备完成；准备阶段定向 pytest 为 `21 passed`，定向 Ruff 通过。

## 八、最终基线执行

所有最终命令都由当前 Runtime 的 `build_verification_shell_command(..., "cmd")` 生成
Windows 原生命令行，再交给 `run_owned_process`。这避免了用普通 argv 列表调用 `cmd.exe`
时对嵌套双引号的错误转义。

### 8.1 `CRWP-V1-01`

| 命令序号 | 运行 | Exit | 秒 | 输出 SHA-256 |
|---:|---:|---:|---:|---|
| 1 | 1 | 0 | 66.895 | `e0631a26cee533eb9b98aeb4b7363f7be4d70f596df7d3de5d67fb890ade53b6` |
| 2 | 1 | 0 | 15.890 | `b0d5abf3103d7cd295b58313997cdff9b0a6d3b889c5bd2c43374af066778961` |
| 3 | 1 | 0 | 11.179 | `beb26139f96bcd659b4364837d4dcc198662e3a605ad66ec3a1fd828209bd36d` |
| 4 | 1 | 0 | 14.275 | `237a0e8c2cab7117db7ff68897f50d9ac235530ece0d6b1068d288602ce4858f` |
| 5 oracle | 1 | 1 | 34.387 | `6929af7b286572abbb975d6b4040ee7315d21b135ec8b6bfa0e43f9a67a344cb` |
| 5 oracle | 2 | 1 | 31.111 | `6929af7b286572abbb975d6b4040ee7315d21b135ec8b6bfa0e43f9a67a344cb` |
| 6 | 1 | 0 | 0.738 | `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` |

决定性基线：

- `not-a-number`、`10m`、`Infinity` 在建立连接前失败，但错误暴露内部 `delay`，没有指向
  timeout 参数；
- `0`、`-1`、`1.5` 被错误接受，并各建立一次连接；
- `1`、`60`、`1e2`、`0x10`、`+1`、`86401` 均按预期进入原路径；
- 未提供 timeout 时 SDK 默认值仍为 `300`。

两次 oracle 的完整输出字节相同。

### 8.2 `CRWP-V1-02`

| 命令序号 | 运行 | Exit | 秒 | 输出 SHA-256 |
|---:|---:|---:|---:|---|
| 1 | 1 | 0 | 170.399 | `cd2abe8dc0a16eeeb4f6043398da99edacbe336947364f3ae3ffd715b025c9e0` |
| 2 | 1 | 0 | 19.686 | `c098a63a3dbc5b71bd9c163c6672f22e1518f5acc73fcbeb19565463ed9ff3fb` |
| 3 | 1 | 0 | 19.543 | `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` |
| 4 oracle | 1 | 1 | 4.705 | `438c8cc053fd2834403f73f5ee703de5fb1ab0ab8069202c03ae092cf3681cca` |
| 4 oracle | 2 | 1 | 3.847 | `438c8cc053fd2834403f73f5ee703de5fb1ab0ab8069202c03ae092cf3681cca` |
| 5 | 1 | 0 | 1.049 | `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` |

两次 oracle 均稳定得到：

```json
{
  "status": "failed",
  "before_has_autoincrement": true,
  "after_has_autoincrement": false,
  "second_alter_has_autoincrement": false,
  "describe_reports_autoincrement": false,
  "data_preserved_after_first_alter": true,
  "inserted_id_after_deleting_max": 2,
  "deleted_id_not_reused": false,
  "data_preserved_after_second_alter": true,
  "plain_pk_not_autoincrement": true,
  "literal_text_not_autoincrement": true,
  "dash_literal_preserves_later_autoincrement": false
}
```

### 8.3 `CRWP-V1-03`

| 命令序号 | 运行 | Exit | 秒 | 输出 SHA-256 |
|---:|---:|---:|---:|---|
| 1 | 1 | 0 | 8.739 | `6f1b64fb1d62ce9cb53de027dd44078c6632b1f49d0cf1916c8a8f359f570f63` |
| 2 | 1 | 0 | 4.719 | `82b3e6a6c090a57601d22943bd23fca9218d1031dbe5a7b754092f9a156b4f18` |
| 3 oracle | 1 | 1 | 4.841 | `34a9633e54a1a57827773487846fb508f46f641ea5478b62e6da56f7f62b34bf` |
| 3 oracle | 2 | 1 | 5.254 | `34a9633e54a1a57827773487846fb508f46f641ea5478b62e6da56f7f62b34bf` |
| 4 | 1 | 0 | 23.498 | `1d7de9b4cddcef5bc784bc9ca736e55857b15c239390b6ed2baa9b1137a5425a` |
| 5 | 1 | 0 | 1.284 | `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` |

决定性基线：

- Division 与 Jurisdiction writer 都连续返回
  `washington_county_stub.yaml`；
- 每个同目录双写场景最终都只有一个文件，第一份被覆盖；
- legacy 与 UUID 命名文件均能由 `stub_exists()` 按 YAML 内 `ocdid` 找到；
- `ensure_ancestor_stubs()` 第二次运行全部 skip，writer 调用数为 `0`，manifest 不变；
- 两次 oracle 输出字节完全相同。

### 8.4 控制端更正记录

第一次 Dormice 基线 runner 把 `cmd.exe` 当普通 argv 列表启动，嵌套双引号被错误保留，
oracle 路径因而以 `2` 退出。这是控制 harness 调用错误，不是 oracle 内部错误或目标行为。
该次输出保留在本地证据中，随后使用 Runtime 的原生命令行构造把全部六条命令重新执行；
本节只采用重新执行的最终结果。

第一次 Sequelize 最终基线使用原 `corepack yarn build` 命令，发现命令结束后仍有 Nx daemon。
该次结果同样只作为诊断，最终结果来自 Amendment 2 后重新执行的五条命令。

上述两次作废尝试已写入本地 `registration-data.json` 的 `control_corrections`。Dormice
作废尝试有完整 summary 和 execution artifact；Sequelize 的基线 summary 可复核，但当时
Nx daemon 的进程清单、`nx reset` 和 rebuild timeout 没有独立原始 artifact，登记中明确以
`null` 表示缺失，不把交互记录包装成结构化证据。

## 九、正式 Worker 的前置阻断

当前 Runtime 的关键边界：

- ignored 枚举使用 Git 读取固定 30 秒 timeout；
- ignored manifest 最多完整登记 4096 个元数据条目；
- auto loop 在 worker 前要求 `snapshot_workspace().capture_complete=true`；
- 该条件不满足时不得把后续 diff 归因给 worker。

最终目标现状：

| Case | Ignored 文件数 | Git 输出字节 | 控制端枚举秒 | Vega snapshot |
|---|---:|---:|---:|---|
| `CRWP-V1-01` | 407934 | 43086563 | 38.847 | `TimeoutExpired`，未生成 baseline |
| `CRWP-V1-02` | 65438 | 4130499 | 4.831 | `capture_complete=false` |
| `CRWP-V1-03` | 7152 | 433568 | 1.078 | `capture_complete=false` |

Dormice 的 Runtime 读取在 30 秒处超时；控制端放宽到 180 秒后，实际枚举耗时仍为
38.847 秒。Sequelize 与 OpenStates 虽能完成枚举，但都超过 4096 个条目的 manifest 上限，
因此 `ignored_manifest_complete=false`。

Dormice 还暴露出一个更严格的错误边界：`snapshot_workspace()` 只捕获 `RuntimeError`，
没有捕获 `subprocess.TimeoutExpired`。若直接启动当前 auto loop，状态会先写成
`running / current_step=worker`，随后在调用 worker runner 前抛出该异常；worker 不会启动，
但 run 可能停留在非终态并需要 recovery。此次是控制端在创建正式 run 前主动停止，不能把它
描述成 Runtime 已经生成了干净的 `needs_human` 终态。

这不是简单把 timeout 或条目上限调大就能可信解决的问题。无界读取 `node_modules`、`.venv`
和构建缓存会放大启动耗时、内存与证据体积；直接忽略所有 ignored 路径又会失去对 worker
修改构建产物、缓存、凭据文件或其他本地状态的检测。下一步应先在 Vega 主线为“大型依赖目录
与高价值 ignored 状态并存”定义有界、可解释的策略，并补回归测试，再创建新的运行登记提交。

在此之前不得：

- 启动任一正式 worker；
- 删除依赖目录后假装目标仍具备相同可执行环境；
- 只依据 `git status` 干净或 `config check` 通过宣称 workspace 可信；
- 把当前阻断记为模型失败、oracle 失败或 reviewer 拒绝。

### 9.1 控制 oracle 登记审查阻断

除 workspace baseline 外，当前控制端还有三个独立阻断：

1. Dormice 默认 timeout 场景需要同时校验请求路径、Authorization、name、command 和
   `timeoutSeconds=300`；
2. Sequelize 负对照需要真实 SQL 行注释和块注释，不能只用字符串字面量近似；
3. OpenStates 幂等场景需要证明首次运行真实写出预期 division/jurisdiction YAML，并校验
   对应 `ocdid`，再验证第二次不新增、不改写且 writer 调用为 `0`。

这些修复会改变控制文件哈希。完成后必须重新执行各自 oracle 两次并追加 Amendment；当前
双次输出不得直接升级为最终冻结证据。

### 9.2 OpenStates 的后续风险门禁

即使 ignored 基线问题修复，当前 `HIGH_RISK_PATH_KEYWORDS` 也会让
`src/init_migration/generate_recursive.py` 命中 `migration`。若 worker 修改该生产文件，
当前 risk gate 将给出 `human-review`，auto loop 会在隔离 reviewer 前停止。

这符合 fail-closed，但意味着 `CRWP-V1-03` 预期不能作为“自动 reviewer 完整通过”的样本；
它只能成为高风险路径转人工的真实结果。若未来目标改为“三项都必须进入隔离 reviewer”，
必须在运行前另行 amendment，不能在看到结果后放松风险门禁。

## 十、Sequelize 输入污染负向词表

正式调用 worker 和 reviewer 前必须扫描其最终编译输入，至少拒绝以下精确值：

```text
sequelize/sequelize#18274
fix(sqlite3): preserve autoincrement on alter sync
14aab37348d0d8d7bdca7dbe1faeeb4f8dedb67b
91bb5f43bed59d82f4db47d8cece9431558fdc4405463551d3441aefdc605b37
```

截至 2026-07-28 本次登记检查时，PR `#18274` 仍为 open、非 draft，base 与 head 仍分别为
预注册值。由于正式 worker 没有启动，最终角色输入尚未编译，负向扫描也尚未执行；不能把
“词表已登记”写成“输入已证明无污染”。

本地 Git 输入面审计未发现冻结修订之后的 heads、reflog 或已审计候选提交包含三个允许路径的
变更，公开 PR `#18274` 的 head 对象也不在目标副本中。但当前副本仍保留额外 `main`、reflog、
677 个 tag 和 promisor 元数据，不属于最小冻结副本。因此它只作为准备阶段证据；解除 Runtime
阻断后必须从冻结 SHA 重建只保留 Case ref 的干净副本并重新登记。

## 十一、本地原始证据

原始控制证据保存在 ignored 目录：

```text
.local-validation/crwp-v1/control-evidence/
```

关键入口：

```text
.local-validation/crwp-v1/control-evidence/registration-data.json
.local-validation/crwp-v1/control-evidence/process-audit.json
.local-validation/crwp-v1/control-evidence/sequelize-ref-surface-audit.json
.local-validation/crwp-v1/control-evidence/baseline-dormice-final/
.local-validation/crwp-v1/control-evidence/baseline-sequelize-final-v2/
.local-validation/crwp-v1/control-evidence/baseline-openstates-final/
```

`registration-data.json` SHA-256：

```text
92d2f06bcb05b628fac892c70e8367bcf3cc11868a8c584894482267a939d163
```

附加本地审计 artifact：

| Artifact | SHA-256 |
|---|---|
| `process-audit.json` | `6959ae44a38298908b6eda6b93dbfefc61986c4df54dc4a06516679ffd7e4318` |
| `sequelize-ref-surface-audit.json` | `10d0d8f00392098dd3840bfd7b6285d3cc7ef258553ac32271275e7acc5d91f9` |

该本地 JSON 包含绝对路径哈希、配置检查原始结果、完整命令、准备提交、baseline summary、
oracle JSON、ignored inventory、snapshot 结果、作废尝试和 setup 证据缺口。它不进入 Git，
也不是公开证据包。

当前控制证据下共有 30 个 owned execution 记录：`21 completed / 9 failed`，其中 8 个
`returncode=1` 是符合预期的缺陷 oracle，唯一非预期项是作废 Dormice runner 的
`returncode=2`。30 个记录均已进入终态，`termination_unconfirmed=0`。

登记整理阶段的 `process-audit.json` 使用“三个目标副本绝对路径是否出现在进程命令行中”
作为限定匹配规则，扫描结果为目标相关进程 `0`、已登记 execution PID 存活数 `0`。该结论
只绑定 artifact 中的观察时间，不证明历史任意时刻都没有残余进程。

## 十二、解除阻断后的固定顺序

1. 先补齐第 9.1 节的三个 oracle 合同，重新执行双次控制基线并追加 Control Amendment。
2. 再修复并验证 Vega ignored workspace inventory，不改本 Pilot 的三个业务题目。
3. 用覆盖 `node_modules`、Yarn workspace 和 `.venv` 的自动化测试证明：
   - 大型依赖目录不会造成无界枚举；
   - 高价值 ignored 文件被新增、修改或删除时仍会 fail-closed；
   - ignored 枚举 timeout 会被结构化捕获并形成可恢复的终态，不留下 `running` 半现场；
   - timeout、清单截断和内容预算都有结构化证据；
   - worker 前 baseline 和 worker 后比较使用同一语义。
4. 形成独立主线提交后，追加新的 CRWP-V1 Runtime amendment，登记新 Runtime SHA。
5. 从本登记的 Upstream base 重新创建三个干净目标副本，不复用可能被后续操作改变的现场。
6. 重新加入修订后的 `.vega.yaml`，移除 remote，重跑 config check、全部非 oracle 命令和 oracle
   双次基线。
7. 重新执行 Issue/PR 资格检查、模型探测和 Sequelize 输入负向扫描。
8. 只有新的 workspace baseline 完整且工作区 clean，才按固定顺序启动
   `CRWP-V1-01`、`CRWP-V1-02`、`CRWP-V1-03`。

本登记提交完成后仍不 push、不启动正式 worker，也不创建新的 Vega 工作分支。
